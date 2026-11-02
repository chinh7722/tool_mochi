"""Offline regressions for the original release's IP-check provider chain."""

import sys
from pathlib import Path
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from curl_cffi import CurlOpt
from curl_cffi.requests.errors import RequestsError
from curl_cffi.requests.exceptions import ProxyError

ROOT = Path(__file__).resolve().parents[1]
# Keep installed extensions ahead of bundled Python 3.11 release DLLs.
sys.path.append(str(ROOT))

from core.proxy.checker import CHECK_APIS, probe_proxy_connection
from core.proxy.models import ProxyModel
from core.proxy.health import ProxyHealthResult
from core.proxy.protocol import ProxyLease, ProxyTransportError
from core.platforms.mochichat.fetcher import MochiChatFetcher
from core.platforms.mochichat.runners.invite_referral import MochiInviteRunner, MochiAccountResult


CHECKER = 'core.proxy.checker'


def response(payload=None, status=200, error=None):
    return SimpleNamespace(status_code=status, json=Mock(return_value=payload, side_effect=error))


class OriginalProxyTests(unittest.IsolatedAsyncioTestCase):
    def proxy(self):
        return ProxyModel('127.0.0.1', 8901, username='offline@user', password='offline:password')

    async def asyncSetUp(self):
        import tempfile
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    async def probe(self, replies, **kwargs):
        client = SimpleNamespace(get=AsyncMock(side_effect=replies))
        context = AsyncMock()
        context.__aenter__.return_value = client
        with patch(f'{CHECKER}.AsyncSession', return_value=context) as factory:
            result = await probe_proxy_connection(self.proxy(), **kwargs)
        return result, client, factory

    async def test_valid_first_api_preserves_credentials_and_original_session_options(self):
        proxy = self.proxy()
        result, client, factory = await self.probe([response({'status': 'success', 'query': '203.0.113.12'})])
        self.assertTrue(result.success)
        self.assertEqual(result.stage, 'ip')
        self.assertEqual(result.exit_ip, '203.0.113.12')
        self.assertEqual(result.source_api, 'ip-api.com')
        self.assertFalse(result.transport_error)
        self.assertNotIn('offline', result.endpoint)
        factory.assert_called_once_with(trust_env=False)
        client.get.assert_awaited_once_with(CHECK_APIS[0][0], proxy=proxy.to_url(), timeout=10.0)
        self.assertIn('offline%40user:offline%3Apassword@', client.get.call_args.kwargs['proxy'])

    async def test_provider_fallback_keeps_exact_original_order(self):
        replies = [response(status=502), response(status=503), response(status=504),
                   response(error=ValueError('invalid JSON')), response({'error': True}),
                   response({'ip': 'invalid'}), response({'ip': '2001:db8::abcd'})]
        result, client, _ = await self.probe(replies)
        self.assertTrue(result.success)
        self.assertEqual(result.exit_ip, '2001:db8::abcd')
        self.assertEqual(result.source_api, 'ipify64.org')
        self.assertEqual([call.args[0] for call in client.get.call_args_list], [url for url, _ in CHECK_APIS])

    async def test_invalid_ip_and_rejected_provider_payloads_fall_back(self):
        replies = [response({'status': 'fail', 'query': '203.0.113.1'}),
                   response({'success': False, 'ip': '203.0.113.1'}),
                   response({'ip': 12345}), response({'ip': '999.1.1.1'}),
                   response({'ip': '203.0.113.5'})]
        result, client, _ = await self.probe(replies)
        self.assertTrue(result.success)
        self.assertEqual(result.exit_ip, '203.0.113.5')
        self.assertEqual(client.get.await_count, 5)

    async def test_two_timeouts_total_stop_across_provider_fallback(self):
        result, client, _ = await self.probe([RequestsError('curl: (28) timeout', code=28),
            response(status=503), asyncio.TimeoutError(), response({'ip': '203.0.113.1'})])
        self.assertFalse(result.success)
        self.assertTrue(result.transport_error)
        self.assertEqual(client.get.await_count, 3)

    async def test_http_407_stops_without_trying_next_provider(self):
        result, client, _ = await self.probe([response(status=407), response({'ip': '203.0.113.1'})])
        self.assertFalse(result.success)
        self.assertEqual(result.http_status, 407)
        self.assertTrue(result.transport_error)
        self.assertEqual(client.get.await_count, 1)

    async def test_proxy_connection_error_stops_without_trying_next_provider(self):
        result, client, _ = await self.probe([ProxyError('offline proxy failed', code=7),
                                            response({'ip': '203.0.113.1'})])
        self.assertFalse(result.success)
        self.assertEqual(result.curl_code, 7)
        self.assertEqual(client.get.await_count, 1)

    async def test_all_invalid_payloads_do_not_claim_proxy_transport_failure(self):
        result, client, _ = await self.probe([response({'ip': 'not-an-ip'})] * len(CHECK_APIS))
        self.assertFalse(result.success)
        self.assertFalse(result.transport_error)
        self.assertIsNone(result.exit_ip)
        self.assertEqual(client.get.await_count, len(CHECK_APIS))

    async def test_http_407_exception_also_stops_immediately(self):
        error = RequestsError('HTTP 407', response=response(status=407))
        result, client, _ = await self.probe([error, response({'ip': '203.0.113.1'})])
        self.assertFalse(result.success)
        self.assertEqual(result.http_status, 407)
        self.assertEqual(client.get.await_count, 1)

    async def test_total_deadline_cancels_a_stalled_request(self):
        original_timeout = asyncio.timeout

        async def stalled(*args, **kwargs):
            await asyncio.Event().wait()

        client = SimpleNamespace(get=AsyncMock(side_effect=stalled))
        context = AsyncMock()
        context.__aenter__.return_value = client
        with patch(f'{CHECKER}.AsyncSession', return_value=context), \
             patch(f'{CHECKER}.asyncio.timeout', side_effect=lambda seconds: original_timeout(0.01)) as guard:
            result = await probe_proxy_connection(self.proxy())
        guard.assert_called_once_with(30.0)
        self.assertFalse(result.success)
        self.assertTrue(result.transport_error)
        self.assertEqual(result.curl_code, 28)
        self.assertEqual(client.get.await_count, 1)
        context.__aexit__.assert_awaited_once()

    async def test_cancellation_propagates(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.probe([asyncio.CancelledError()])

    async def test_model_check_info_converts_milliseconds_and_raises_on_failed_check(self):
        proxy = self.proxy()
        good = ProxyHealthResult(True, proxy.server, 'ip', exit_ip='203.0.113.1')
        bad = ProxyHealthResult(False, proxy.server, 'ip', error='offline proxy failed')
        checker = AsyncMock(side_effect=[good, bad])
        with patch(f'{CHECKER}.probe_proxy_connection', checker):
            self.assertIs(await proxy.check_info(timeout=10000, max_timeout_retries=2), good)
            with self.assertRaises(ProxyTransportError) as caught:
                await proxy.check_info(timeout=10000, max_timeout_retries=2, raise_for_status=True)
        self.assertEqual(caught.exception.kind, 'proxy_check_failed')
        self.assertEqual(checker.await_args_list, [
            call(proxy, timeout=10.0, max_timeout_retries=2),
            call(proxy, timeout=10.0, max_timeout_retries=2)])

    async def test_default_fetcher_uses_original_transport_without_cached_dns_override(self):
        module = 'core.platforms.mochichat.fetcher'
        proxy = self.proxy()
        route = SimpleNamespace(curl_options=Mock(return_value={CurlOpt.CONNECT_TO: 'offline-rule'}))
        owned_session = SimpleNamespace(close=AsyncMock())
        with patch(f'{module}.get_proxy_dns_route', return_value=route) as cached_dns, \
             patch(f'{module}.AsyncSession', return_value=owned_session) as factory:
            fetcher = MochiChatFetcher(proxy=proxy)
            await fetcher.close()
        options = factory.call_args.kwargs
        self.assertEqual(options['timeout'], 45.0)
        self.assertEqual(options['impersonate'], 'chrome_android')
        self.assertEqual(options['proxies'], proxy.proxies)
        self.assertTrue(options['verify'])
        self.assertFalse(options['trust_env'])
        self.assertNotIn('curl_options', options)
        self.assertNotIn('http_version', options)
        cached_dns.assert_not_called()
        route.curl_options.assert_not_called()

    async def test_runner_default_ip_check_precedes_purchase_without_mochi_head_gate(self):
        module = 'core.platforms.mochichat.runners.invite_referral'
        proxy = self.proxy()
        lease = ProxyLease('offline-lease', 'offline-task', 'mochichat', proxy=proxy)
        proxy_client = SimpleNamespace(lease=AsyncMock(return_value=lease), release=AsyncMock(),
                                       release_job=AsyncMock())
        order = []

        async def check(assigned):
            self.assertIs(assigned, proxy)
            order.append('check')
            return ProxyHealthResult(True, proxy.server, 'ip', exit_ip='203.0.113.1', source_api='ip-api.com')

        async def buy(*args, **kwargs):
            order.append('buy')
            return [SimpleNamespace(username='offline@example.com', password='offline-password')]

        async def process(*args, **kwargs):
            order.append('process')
            return MochiAccountResult('offline@example.com', True, 'success')

        checker = AsyncMock(side_effect=check)
        mail_client = SimpleNamespace(buy_gmail_domain_info=AsyncMock(side_effect=buy))
        mail_context = AsyncMock()
        mail_context.__aenter__.return_value = mail_client
        with patch(f'{module}.probe_proxy_connection', checker), \
             patch(f'{module}.GmailVipVn', return_value=mail_context), \
             patch(f'{module}.browser_manager.release_by_owner', AsyncMock()), \
             patch('core.proxy.health.probe_proxy_target', AsyncMock(side_effect=AssertionError('Mochi HEAD gate used'))) as mochi_head:
            runner = MochiInviteRunner(proxy_client=proxy_client, concurrency=1, google_login_via_proxy=False,
                mail_batch_dir=self.directory.name)
            runner._process_account = AsyncMock(side_effect=process)
            summary = await runner.run('offline-original-check', 'TEST', 1)
        self.assertEqual(summary.status, 'completed')
        self.assertEqual(summary.attempted_count, 1)
        self.assertEqual(order, ['check', 'buy', 'process'])
        checker.assert_awaited_once_with(proxy)
        mochi_head.assert_not_awaited()
        proxy_client.release.assert_awaited_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
