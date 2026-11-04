"""Mochi API TLS/proxy recovery regressions; all HTTP and Google calls are mocked.

Run: py -3.14 -I tests/test_mochi_tls.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from curl_cffi.const import CurlHttpVersion, CurlOpt
from curl_cffi.requests.errors import RequestsError

from core.platforms.mochichat.fetcher import MochiChatFetcher
from core.platforms.mochichat.runners.invite_referral import MochiAccountItem, MochiInviteRunner
from core.proxy import broker as broker_module
from core.proxy.broker import ProxyBroker
from core.proxy.client import ProxyPoolClient
from core.proxy.models import ProxyModel
from core.proxy.health import ProxyHealthResult
from core.proxy.rotators import RotationResult
from core.proxy.dns_routes import (ProxyDnsRoute, remember_proxy_dns_route,
    get_proxy_dns_route, clear_proxy_dns_route)
from core.platforms.mochichat.constants import MOCHI_API_BASE
from urllib.parse import urlsplit


MODULE = 'core.platforms.mochichat.fetcher'
ID_TOKEN = 'eyJoffline-google-token'


class Response:
    def __init__(self, data=None, status=200):
        self.status_code = status
        self.data = data if data is not None else {
            'ok': True, 'data': {'token': 'offline-api-token', 'user': {'id': 'offline-user'}},
        }
        self.text = json.dumps(self.data)

    def json(self):
        return self.data


def session(*results):
    return SimpleNamespace(post=AsyncMock(side_effect=list(results)), close=AsyncMock())


def request_error(code):
    message = {
        16: 'HTTP2 protocol error',
        28: 'Operation timed out',
        35: 'BoringSSL SSL_connect: Connection closed abruptly (SSL_ERROR_SYSCALL)',
        60: 'SSL certificate verification failed',
        92: 'HTTP2 stream was not closed cleanly',
    }.get(code, 'Connection failed')
    return RequestsError(message, code=code)


class MochiTlsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        diagnostic = patch(f'{MODULE}.probe_proxy_target', AsyncMock(return_value=ProxyHealthResult(
            False, 'offline', 'tls', 'offline diagnostic failure')))
        self.dns_probe = diagnostic.start()
        self.addCleanup(diagnostic.stop)
        for proxy in self.proxies():
            clear_proxy_dns_route(proxy, MOCHI_API_BASE)
            self.addCleanup(clear_proxy_dns_route, proxy, MOCHI_API_BASE)

    def proxies(self):
        return (
            ProxyModel.from_string('http://127.0.0.1:8701'),
            ProxyModel.from_string('http://127.0.0.1:8702'),
        )

    def assert_same_login_payload(self, sessions):
        calls = [call for item in sessions for call in item.post.call_args_list]
        for call in calls:
            self.assertTrue(call.args[0].endswith('/auth/google'))
            self.assertEqual(call.kwargs['json'], {'id_token': ID_TOKEN, 'region': 'vn'})

    async def test_owned_session_keeps_explicit_proxy_and_tls_verification(self):
        proxy, _ = self.proxies()
        owned = session(Response())
        with patch(f'{MODULE}.AsyncSession', return_value=owned) as factory:
            fetcher = MochiChatFetcher(proxy=proxy)
            self.addAsyncCleanup(fetcher.close)
        options = factory.call_args.kwargs
        self.assertIs(options.get('trust_env'), False)
        self.assertIs(options.get('verify'), True)
        self.assertEqual(options['proxies'], proxy.to_curl_cffi_dict())

    async def test_ssl35_retries_same_token_with_fresh_original_transport(self):
        proxy, _ = self.proxies()
        first, compatible = session(request_error(35)), session(Response())
        with patch(f'{MODULE}.AsyncSession', side_effect=[first, compatible]) as factory:
            fetcher = MochiChatFetcher(proxy=proxy)
            self.addAsyncCleanup(fetcher.close)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN)
        self.assertTrue(result['ok'])
        self.assertEqual(factory.call_count, 2)
        first.close.assert_awaited_once()
        self.assertEqual(factory.call_args.kwargs['impersonate'], 'chrome_android')
        self.assertNotIn(CurlOpt.HTTP_VERSION, factory.call_args.kwargs.get('curl_options', {}))
        self.assertEqual(factory.call_args.kwargs['curl_options'][CurlOpt.CONNECTTIMEOUT_MS], 10000)
        self.assertEqual(factory.call_args.kwargs['proxies'], proxy.to_curl_cffi_dict())
        self.assert_same_login_payload([first, compatible])

    async def test_cold_cache_tls_failure_resolves_and_validates_dns_before_retry(self):
        proxy, _ = self.proxies()
        route = ProxyDnsRoute(urlsplit(MOCHI_API_BASE).hostname, 443, '203.0.113.40')

        async def validate(*args, **kwargs):
            self.assertIsNone(get_proxy_dns_route(proxy, MOCHI_API_BASE))
            remember_proxy_dns_route(proxy, MOCHI_API_BASE, route)
            return ProxyHealthResult(True, proxy.server, 'https', dns_fallback=True,
                target_address=route.address)

        self.dns_probe.side_effect = validate
        first, compatible = session(request_error(35)), session(Response())
        with patch(f'{MODULE}.AsyncSession', side_effect=[first, compatible]) as factory:
            fetcher = MochiChatFetcher(proxy=proxy)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN)
            await fetcher.close()
        self.assertTrue(result['ok'])
        self.dns_probe.assert_awaited_once_with(proxy, target_url=MOCHI_API_BASE, timeout=8.0, recover_dns=True)
        self.assertNotIn('curl_options', factory.call_args_list[0].kwargs)
        options = factory.call_args_list[1].kwargs
        self.assertIs(options['curl_options'][CurlOpt.CONNECT_TO], route.curl_options()[CurlOpt.CONNECT_TO])
        self.assertEqual(options['curl_options'][CurlOpt.CONNECTTIMEOUT_MS], 10000)
        self.assertTrue(options['verify'])
        self.assert_same_login_payload([first, compatible])

    async def test_failed_dns_probe_does_not_apply_unverified_route(self):
        proxy, _ = self.proxies()
        first, compatible = session(request_error(35)), session(Response())
        with patch(f'{MODULE}.AsyncSession', side_effect=[first, compatible]) as factory:
            fetcher = MochiChatFetcher(proxy=proxy)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN)
            await fetcher.close()
        self.assertTrue(result['ok'])
        self.assertNotIn(CurlOpt.CONNECT_TO, factory.call_args.kwargs.get('curl_options', {}))
        self.assertIsNone(get_proxy_dns_route(proxy, MOCHI_API_BASE))
        self.dns_probe.assert_awaited_once()

    async def test_same_gateway_rotation_discards_overrides_and_tries_original_transport(self):
        proxy, _ = self.proxies()
        route_a = ProxyDnsRoute(urlsplit(MOCHI_API_BASE).hostname, 443, '203.0.113.41')
        route_b = ProxyDnsRoute(urlsplit(MOCHI_API_BASE).hostname, 443, '203.0.113.42')
        checked = []

        async def validate(*args, **kwargs):
            self.assertIsNone(get_proxy_dns_route(proxy, MOCHI_API_BASE))
            route = route_a if not checked else route_b
            checked.append(route)
            remember_proxy_dns_route(proxy, MOCHI_API_BASE, route)
            return ProxyHealthResult(True, proxy.server, 'https', dns_fallback=True,
                target_address=route.address)

        self.dns_probe.side_effect = validate
        first, compatible, recovered = session(request_error(35)), session(request_error(35)), session(Response())
        recover = AsyncMock(return_value=proxy)
        with patch(f'{MODULE}.AsyncSession', side_effect=[first, compatible, recovered]) as factory:
            fetcher = MochiChatFetcher(proxy=proxy)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN, recover_proxy=recover)
            await fetcher.close()
        self.assertTrue(result['ok'])
        self.assertEqual(factory.call_count, 3)
        self.assertEqual(self.dns_probe.await_count, 1)
        recover.assert_awaited_once()
        self.assertIs(factory.call_args_list[1].kwargs['curl_options'][CurlOpt.CONNECT_TO], route_a.curl_options()[CurlOpt.CONNECT_TO])
        self.assertNotIn(CurlOpt.HTTP_VERSION, factory.call_args_list[2].kwargs.get('curl_options', {}))
        self.assertNotIn(CurlOpt.CONNECT_TO, factory.call_args_list[2].kwargs.get('curl_options', {}))
        self.assertEqual(factory.call_args_list[2].kwargs['curl_options'][CurlOpt.CONNECTTIMEOUT_MS], 10000)
        self.assertNotIn('http_version', recovered.post.call_args.kwargs)
        self.assertIsNone(get_proxy_dns_route(proxy, MOCHI_API_BASE))
        self.assert_same_login_payload([first, compatible, recovered])

    async def test_direct_certificate_and_business_failures_never_probe_dns(self):
        for proxy, error in ((None, request_error(35)), (self.proxies()[0], request_error(60)),
                (self.proxies()[0], Response({'ok': False, 'error': 'offline rejection'}, 403))):
            self.dns_probe.reset_mock()
            active = session(error, error)
            with patch(f'{MODULE}.AsyncSession', return_value=active):
                fetcher = MochiChatFetcher(proxy=proxy)
                await fetcher._login_mochi_with_id_token(ID_TOKEN)
                await fetcher.close()
            self.dns_probe.assert_not_awaited()

    async def test_cancellation_during_dns_recovery_does_not_retry_or_rotate(self):
        self.dns_probe.side_effect = asyncio.CancelledError()
        active = session(request_error(35))
        recover = AsyncMock()
        with patch(f'{MODULE}.AsyncSession', return_value=active):
            fetcher = MochiChatFetcher(proxy=self.proxies()[0])
            with self.assertRaises(asyncio.CancelledError):
                await fetcher._login_mochi_with_id_token(ID_TOKEN, recover_proxy=recover)
            await fetcher.close()
        active.post.assert_awaited_once()
        active.close.assert_awaited_once()
        recover.assert_not_awaited()

    async def test_http2_errors_get_same_bounded_compatibility_retry(self):
        for code in (16, 92):
            with self.subTest(code=code):
                first, compatible = session(request_error(code)), session(Response())
                with patch(f'{MODULE}.AsyncSession', side_effect=[first, compatible]) as factory:
                    fetcher = MochiChatFetcher(proxy=self.proxies()[0])
                    result = await fetcher._login_mochi_with_id_token(ID_TOKEN)
                    await fetcher.close()
                self.assertTrue(result['ok'])
                self.assertEqual(factory.call_count, 2)
                self.assertEqual(factory.call_args.kwargs['curl_options'][CurlOpt.HTTP_VERSION], CurlHttpVersion.V1_1)
                self.assert_same_login_payload([first, compatible])

    async def test_persistent_tls_failure_recovers_proxy_once_and_reuses_token(self):
        old_proxy, new_proxy = self.proxies()
        first = session(request_error(35))
        compatible = session(request_error(35))
        recovered = session(Response())
        recover = AsyncMock(return_value=new_proxy)
        with patch(f'{MODULE}.AsyncSession', side_effect=[first, compatible, recovered]) as factory:
            fetcher = MochiChatFetcher(proxy=old_proxy)
            self.addAsyncCleanup(fetcher.close)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN, recover_proxy=recover)
        self.assertTrue(result['ok'])
        recover.assert_awaited_once()
        self.assertEqual(factory.call_count, 3)
        self.assertEqual(fetcher.proxy.to_url(), new_proxy.to_url())
        self.assertEqual(factory.call_args.kwargs['proxies'], new_proxy.to_curl_cffi_dict())
        self.assertNotIn(CurlOpt.HTTP_VERSION, factory.call_args.kwargs.get('curl_options', {}))
        self.assertNotIn(CurlOpt.CONNECT_TO, factory.call_args.kwargs.get('curl_options', {}))
        first.close.assert_awaited_once()
        compatible.close.assert_awaited_once()
        self.assert_same_login_payload([first, compatible, recovered])

    async def test_connection_timeout_recovers_proxy_without_repeating_google(self):
        old_proxy, new_proxy = self.proxies()
        first, recovered = session(request_error(28)), session(Response())
        recover = AsyncMock(return_value=new_proxy)
        google = AsyncMock(return_value=ID_TOKEN)
        with patch(f'{MODULE}.AsyncSession', side_effect=[first, recovered]) as factory, \
             patch(f'{MODULE}.get_google_id_token_for_app', google):
            fetcher = MochiChatFetcher(proxy=old_proxy)
            self.addAsyncCleanup(fetcher.close)
            result = await fetcher.login('owner', 'offline@example.com', 'offline-password',
                google_login_via_proxy=False, recover_proxy=recover)
        self.assertTrue(result['ok'])
        self.assertEqual(factory.call_count, 2)
        google.assert_awaited_once()
        self.assertIs(google.call_args.kwargs['google_login_via_proxy'], False)
        recover.assert_awaited_once()
        self.assert_same_login_payload([first, recovered])

    async def test_google_direct_called_once_across_compatibility_and_proxy_recovery(self):
        old_proxy, new_proxy = self.proxies()
        first, compatible, recovered = session(request_error(35)), session(request_error(35)), session(Response())
        recover = AsyncMock(return_value=new_proxy)
        google = AsyncMock(return_value=ID_TOKEN)
        with patch(f'{MODULE}.AsyncSession', side_effect=[first, compatible, recovered]) as factory, \
             patch(f'{MODULE}.get_google_id_token_for_app', google):
            fetcher = MochiChatFetcher(proxy=old_proxy)
            self.addAsyncCleanup(fetcher.close)
            result = await fetcher.login('same-owner', 'offline@example.com', 'offline-password',
                google_login_via_proxy=False, recover_proxy=recover)
        self.assertTrue(result['ok'])
        self.assertEqual(factory.call_count, 3)
        google.assert_awaited_once()
        self.assertIs(google.call_args.kwargs['google_login_via_proxy'], False)
        recover.assert_awaited_once()
        self.assert_same_login_payload([first, compatible, recovered])

    async def run_mixed_transport_failure(self, final_response):
        old_proxy, new_proxy = self.proxies()
        events = []

        def make_session(label, outcome):
            owned = session()

            async def post(url, **kwargs):
                if url.endswith('/invites/redeem'):
                    events.append('redeem')
                    return Response({'ok': True})
                events.append(label)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

            owned.post.side_effect = post
            return owned

        attempts = [make_session('tls35', request_error(35)),
            make_session('http2-16', request_error(16)),
            make_session('timeout28', request_error(28)),
            make_session('new-proxy', final_response)]

        async def rotate():
            events.append('rotate')
            return new_proxy

        recover = AsyncMock(side_effect=rotate)
        google = AsyncMock(return_value=ID_TOKEN)
        with patch(f'{MODULE}.AsyncSession', side_effect=attempts) as factory, \
             patch(f'{MODULE}.get_google_id_token_for_app', google):
            fetcher = MochiChatFetcher(proxy=old_proxy)
            result = await fetcher.login('mixed-owner', 'offline@example.com', 'offline-password',
                google_login_via_proxy=False, recover_proxy=recover)
            if result['ok']:
                self.assertTrue((await fetcher.redeem_invite_code('TEST')).success)
            await fetcher.close()
        google.assert_awaited_once()
        self.assertFalse(google.await_args.kwargs['google_login_via_proxy'])
        recover.assert_awaited_once()
        self.assertEqual(factory.call_count, 4)
        self.assertEqual(events[:5], ['tls35', 'http2-16', 'timeout28', 'rotate', 'new-proxy'])
        self.assertEqual(events.count('redeem'), 1 if result['ok'] else 0)
        login_calls = [call for owned in attempts for call in owned.post.await_args_list
            if call.args[0].endswith('/auth/google')]
        self.assertEqual(len(login_calls), 4)
        self.assertTrue(all(call.kwargs['json'] == {'id_token': ID_TOKEN, 'region': 'vn'} for call in login_calls))
        self.assertEqual(factory.call_args_list[2].kwargs['curl_options'][CurlOpt.HTTP_VERSION], CurlHttpVersion.V1_1)
        final_settings = factory.call_args_list[3].kwargs
        self.assertEqual(final_settings['proxies'], new_proxy.to_curl_cffi_dict())
        self.assertEqual(final_settings['impersonate'], 'chrome_android')
        self.assertIs(final_settings['verify'], True)
        self.assertIs(final_settings['trust_env'], False)
        self.assertNotIn(CurlOpt.HTTP_VERSION, final_settings.get('curl_options', {}))
        self.assertNotIn(CurlOpt.CONNECT_TO, final_settings.get('curl_options', {}))
        self.assertNotIn('http_version', attempts[3].post.await_args_list[0].kwargs)
        for owned in attempts:
            owned.close.assert_awaited_once()
        return result

    async def test_mixed_tls_http2_timeout_reserves_one_post_for_new_proxy(self):
        result = await self.run_mixed_transport_failure(Response())
        self.assertTrue(result['ok'])

    async def test_mixed_transport_failure_stops_after_four_posts_and_one_rotation(self):
        result = await self.run_mixed_transport_failure(request_error(28))
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'NETWORK_ERROR')
        self.assertEqual(result['curl_code'], 28)
        self.assertTrue(result['stop_job'])
        self.assertEqual(result['attempts'], 4)
        self.assertTrue(result['proxy_recovery_attempted'])

    async def test_direct_api_mixed_failures_keep_three_post_cap_without_rotation(self):
        recover = AsyncMock(side_effect=AssertionError('direct API must not rotate a mail proxy'))
        attempts = [session(request_error(code)) for code in (35, 16, 28)]
        with patch(f'{MODULE}.AsyncSession', side_effect=attempts) as factory:
            fetcher = MochiChatFetcher(proxy=self.proxies()[0], app_login_via_proxy=False)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN, recover_proxy=recover)
            await fetcher.close()
        self.assertFalse(result['ok'])
        self.assertFalse(result['proxy_used'])
        self.assertEqual(result['attempts'], 3)
        self.assertFalse(result['proxy_recovery_attempted'])
        self.assertEqual(factory.call_count, 3)
        self.assertEqual(sum(owned.post.await_count for owned in attempts), 3)
        self.assert_same_login_payload(attempts)
        recover.assert_not_awaited()

    async def test_exhausted_tls_recovery_is_capped_at_three_posts(self):
        old_proxy, new_proxy = self.proxies()
        attempts = [session(request_error(35)) for _ in range(3)]
        recover = AsyncMock(return_value=new_proxy)
        with patch(f'{MODULE}.AsyncSession', side_effect=attempts) as factory:
            fetcher = MochiChatFetcher(proxy=old_proxy)
            self.addAsyncCleanup(fetcher.close)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN, recover_proxy=recover)
        self.assertFalse(result['ok'])
        self.assertEqual(result.get('code'), 'NETWORK_ERROR')
        self.assertEqual(result.get('curl_code'), 35)
        self.assertEqual(factory.call_count, 3)
        self.assertEqual(sum(item.post.await_count for item in attempts), 3)
        recover.assert_awaited_once()
        self.assert_same_login_payload(attempts)

    async def test_ssl_without_proxy_recovery_stops_after_compatibility_attempt(self):
        first, compatible = session(request_error(35)), session(request_error(35))
        with patch(f'{MODULE}.AsyncSession', side_effect=[first, compatible]) as factory:
            fetcher = MochiChatFetcher(proxy=self.proxies()[0])
            self.addAsyncCleanup(fetcher.close)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN)
        self.assertFalse(result['ok'])
        self.assertEqual(result.get('code'), 'NETWORK_ERROR')
        self.assertEqual(result.get('curl_code'), 35)
        self.assertEqual(factory.call_count, 2)

    async def test_certificate_error_does_not_retry_or_rotate(self):
        first = session(request_error(60))
        recover = AsyncMock(return_value=self.proxies()[1])
        with patch(f'{MODULE}.AsyncSession', return_value=first) as factory:
            fetcher = MochiChatFetcher(proxy=self.proxies()[0])
            self.addAsyncCleanup(fetcher.close)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN, recover_proxy=recover)
        self.assertFalse(result['ok'])
        self.assertEqual(first.post.await_count, 1)
        self.assertEqual(factory.call_count, 1)
        recover.assert_not_awaited()

    async def test_http_auth_rejection_does_not_retry_or_rotate(self):
        first = session(Response({'ok': False, 'error': 'invalid Google token'}, status=401))
        recover = AsyncMock(return_value=self.proxies()[1])
        with patch(f'{MODULE}.AsyncSession', return_value=first) as factory:
            fetcher = MochiChatFetcher(proxy=self.proxies()[0])
            self.addAsyncCleanup(fetcher.close)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN, recover_proxy=recover)
        self.assertFalse(result['ok'])
        self.assertEqual(first.post.await_count, 1)
        self.assertEqual(factory.call_count, 1)
        recover.assert_not_awaited()

    async def test_business_rejection_does_not_retry_or_rotate(self):
        first = session(Response({'ok': False, 'error': 'account rejected'}))
        recover = AsyncMock(return_value=self.proxies()[1])
        with patch(f'{MODULE}.AsyncSession', return_value=first) as factory:
            fetcher = MochiChatFetcher(proxy=self.proxies()[0])
            self.addAsyncCleanup(fetcher.close)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN, recover_proxy=recover)
        self.assertFalse(result['ok'])
        self.assertEqual(first.post.await_count, 1)
        self.assertEqual(factory.call_count, 1)
        recover.assert_not_awaited()

    async def test_redeem_post_is_not_automatically_repeated(self):
        first = session(request_error(35))
        with patch(f'{MODULE}.AsyncSession', return_value=first) as factory:
            fetcher = MochiChatFetcher(proxy=self.proxies()[0], access_token='offline-api-token')
            self.addAsyncCleanup(fetcher.close)
            result = await fetcher.redeem_invite_code('TEST')
        self.assertFalse(result.success)
        self.assertEqual(first.post.await_count, 1)
        self.assertEqual(factory.call_count, 1)

    async def test_external_session_is_not_closed_or_replaced(self):
        external = session(request_error(35), request_error(35), request_error(35))
        with patch(f'{MODULE}.AsyncSession') as factory:
            fetcher = MochiChatFetcher(proxy=self.proxies()[0], session=external)
            result = await fetcher._login_mochi_with_id_token(ID_TOKEN)
            await fetcher.close()
        self.assertFalse(result['ok'])
        self.assertGreaterEqual(external.post.await_count, 1)
        self.assertLessEqual(external.post.await_count, 3)
        external.close.assert_not_awaited()
        factory.assert_not_called()

    async def test_cancellation_does_not_retry_or_rotate(self):
        first = session(asyncio.CancelledError())
        recover = AsyncMock(return_value=self.proxies()[1])
        with patch(f'{MODULE}.AsyncSession', return_value=first) as factory:
            fetcher = MochiChatFetcher(proxy=self.proxies()[0])
            self.addAsyncCleanup(fetcher.close)
            with self.assertRaises(asyncio.CancelledError):
                await fetcher._login_mochi_with_id_token(ID_TOKEN, recover_proxy=recover)
        self.assertEqual(first.post.await_count, 1)
        self.assertEqual(factory.call_count, 1)
        recover.assert_not_awaited()


class RunnerTlsTests(unittest.IsolatedAsyncioTestCase):
    def make_runner(self):
        diagnostic = patch(f'{MODULE}.probe_proxy_target', AsyncMock(return_value=ProxyHealthResult(
            False, 'offline', 'tls', 'offline diagnostic failure')))
        diagnostic.start()
        self.addCleanup(diagnostic.stop)
        import tempfile
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        original = ProxyModel.from_string('http://127.0.0.1:8801', rotation_url='offline-' + self.id())
        replacement = ProxyModel.from_string('http://127.0.0.1:8802')
        broker = ProxyBroker([original], account_concurrency=1, allow_direct_without_proxies=False)
        client = ProxyPoolClient(broker=broker)
        runner = MochiInviteRunner(proxy_client=client, concurrency=1, google_login_via_proxy=False, mail_batch_dir=directory.name,
            proxy_probe=AsyncMock(return_value=ProxyHealthResult(True, original.server, 'https', http_status=200)))
        return runner, broker, broker._proxies[original.raw], replacement

    def assert_pool_drained(self, broker, state):
        self.assertFalse(broker._leases)
        self.assertFalse(broker._job_leases)
        self.assertEqual(state.in_use, 0)
        self.assertFalse(state.rotating)
        with broker_module._SHARED_LOCK:
            self.assertEqual(broker_module._SHARED_USAGE.get(state.shared_key, 0), 0)
            self.assertNotIn(state.shared_key, broker_module._SHARED_ROTATING)

    async def test_runner_recovers_same_input_account_without_second_google_or_redeem(self):
        runner, broker, state, replacement = self.make_runner()
        order = []

        async def initial_post(*args, **kwargs):
            state.provider.get_current_or_rotate.assert_not_awaited()
            order.append('initial-post')
            raise request_error(35)

        async def compatibility_post(*args, **kwargs):
            state.provider.get_current_or_rotate.assert_not_awaited()
            order.append('compatibility-post')
            raise request_error(35)

        async def rotate(**kwargs):
            self.assertTrue(kwargs['force_rotate'])
            self.assertEqual(order, ['initial-post', 'compatibility-post'])
            order.append('rotate')
            return RotationResult(True, replacement)

        async def recovered_post(url, **kwargs):
            if url.endswith('/auth/google'):
                order.append('recovered-login')
                return Response()
            self.assertTrue(url.endswith('/invites/redeem'))
            order.append('redeem')
            return Response({'ok': True})

        initial, compatible, recovered = session(), session(), session()
        initial.post.side_effect = initial_post
        compatible.post.side_effect = compatibility_post
        recovered.post.side_effect = recovered_post
        state.provider.get_current_or_rotate = AsyncMock(side_effect=rotate)
        item = MochiAccountItem('input-account', 'same-input@example.com', 'offline-password')
        google = AsyncMock(return_value=ID_TOKEN)
        with patch(f'{MODULE}.AsyncSession', side_effect=[initial, compatible, recovered]) as factory, \
             patch(f'{MODULE}.get_google_id_token_for_app', google), \
             patch('core.platforms.mochichat.runners.invite_referral.GmailVipVn') as mail:
            result = await runner.run('tls-runner-success', 'TEST', 1, accounts=[item])
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.attempted_count, 1)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.results[0].account_identifier, item.identifier)
        self.assertEqual(order, ['initial-post', 'compatibility-post', 'rotate', 'recovered-login', 'redeem'])
        self.assertEqual(factory.call_count, 3)
        mail.assert_not_called()
        google.assert_awaited_once()
        self.assertEqual(google.call_args.kwargs['email'], item.email)
        self.assertIs(google.call_args.kwargs['google_login_via_proxy'], False)
        state.provider.get_current_or_rotate.assert_awaited_once()
        login_calls = [call for owned in (initial, compatible, recovered) for call in owned.post.call_args_list
                       if call.args[0].endswith('/auth/google')]
        self.assertEqual(len(login_calls), 3)
        self.assertTrue(all(call.kwargs['json']['id_token'] == ID_TOKEN for call in login_calls))
        self.assertEqual(recovered.post.call_args_list[-1].kwargs['json'], {'code': 'TEST'})
        self.assertEqual(factory.call_args.kwargs['proxies'], replacement.to_curl_cffi_dict())
        self.assert_pool_drained(broker, state)

    async def test_persistent_tls_failure_stops_after_three_failed_account_slots(self):
        runner, broker, state, replacement = self.make_runner()
        state.provider.get_current_or_rotate = AsyncMock(return_value=RotationResult(True, replacement))
        attempts = [session(request_error(35)) for _ in range(9)]
        mail_client = SimpleNamespace(buy_gmail_domain_info=AsyncMock(return_value=[
            SimpleNamespace(username='purchased-once@example.com', password='offline-password')]))
        mail_context = AsyncMock()
        mail_context.__aenter__.return_value = mail_client
        google = AsyncMock(return_value=ID_TOKEN)
        with patch(f'{MODULE}.AsyncSession', side_effect=attempts), \
             patch(f'{MODULE}.get_google_id_token_for_app', google), \
             patch('core.platforms.mochichat.runners.invite_referral.GmailVipVn', return_value=mail_context) as mail:
            result = await runner.run('tls-runner-exhausted', 'TEST', 2)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.attempted_count, 3)
        self.assertEqual(result.failure_count, 3)
        self.assertEqual(result.success_count, 0)
        self.assertEqual(result.results[0].account_identifier, 'purchased-once@example.com')
        self.assertTrue(result.results[0].data['stop_job'])
        self.assertEqual(mail.call_count,3)
        self.assertEqual(mail_client.buy_gmail_domain_info.await_count,3)
        self.assertEqual(google.await_count,3)
        self.assertEqual(state.provider.get_current_or_rotate.await_count,5)  # 3 recoveries + 2 between failed accounts
        posts = [call for owned in attempts for call in owned.post.call_args_list]
        self.assertEqual(len(posts), 9)
        self.assertTrue(all(call.args[0].endswith('/auth/google') for call in posts))
        self.assert_pool_drained(broker, state)

    async def test_cancellation_while_waiting_for_new_ip_releases_every_reservation(self):
        runner, broker, state, _ = self.make_runner()
        rotating, cancelled = asyncio.Event(), asyncio.Event()

        async def rotate(**kwargs):
            rotating.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        state.provider.get_current_or_rotate = AsyncMock(side_effect=rotate)
        initial, compatible = session(request_error(35)), session(request_error(35))
        google = AsyncMock(return_value=ID_TOKEN)
        item = MochiAccountItem('cancelled-account', 'cancel@example.com', 'offline-password')
        with patch(f'{MODULE}.AsyncSession', side_effect=[initial, compatible]), \
             patch(f'{MODULE}.get_google_id_token_for_app', google):
            task = asyncio.create_task(runner.run('tls-runner-cancelled', 'TEST', 1, accounts=[item]))
            await asyncio.wait_for(rotating.wait(), 1)
            self.assertTrue(state.rotating)
            task.cancel()
            result = await asyncio.wait_for(task, 1)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(result.attempted_count, 1)
        self.assertTrue(cancelled.is_set())
        google.assert_awaited_once()
        state.provider.get_current_or_rotate.assert_awaited_once()
        self.assertEqual(initial.post.await_count + compatible.post.await_count, 2)
        initial.close.assert_awaited_once()
        compatible.close.assert_awaited_once()
        self.assert_pool_drained(broker, state)


if __name__ == '__main__':
    unittest.main(verbosity=2)
