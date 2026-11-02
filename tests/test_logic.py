"""Offline regressions. Run: py -3.14 -I tests/test_logic.py"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import asyncio
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from concurrent.futures import ThreadPoolExecutor

from core.auth import google
from core.browser.manager import CentralBrowserManager
from core.platforms.mochichat.fetcher import MochiChatFetcher
from core.platforms.mochichat.runners.invite_referral import (
    MochiAccountItem, MochiAccountResult, MochiInviteRunner,
)
from core.proxy.broker import ProxyBroker
from core.proxy.client import ProxyPoolClient
from core.proxy.health import ProxyHealthResult
from core.proxy.models import ProxyModel, curl_proxy_options
from core.proxy.protocol import ProxyReleaseOutcome, ProxyTransportError, is_request_transport_error
from core.proxy.rotators import ProxyVnProvider, RotationResult, is_cooldown_or_wait_message, is_real_proxy_error
from core.mail.providers.gmailvip_vn import GmailVipVn
from core.mail.providers.one_trieu_mail import OneTrieuMail


def account(index):
    return MochiAccountItem(str(index), f'test{index}@example.com', 'test-password')


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.text = data, status, 'test response'

    def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data


class ProxyTests(unittest.IsolatedAsyncioTestCase):
    async def test_current_proxy_reused_without_rotation_api_call(self):
        proxy = ProxyModel.from_string('127.0.0.1:8501', rotation_url='reuse-current-key')
        broker = ProxyBroker([proxy], rate_limit_cooldown_seconds=0)
        state = broker._proxies[proxy.raw]
        state.provider.get_current_or_rotate = AsyncMock(side_effect=AssertionError('must reuse current IP'))
        for outcome in (ProxyReleaseOutcome.SUCCESS, ProxyReleaseOutcome.BUSINESS_FAILURE,
                        ProxyReleaseOutcome.ABORTED, ProxyReleaseOutcome.RATE_LIMITED):
            lease = await broker.lease('reuse')
            self.assertEqual(lease.proxy.port, 8501)
            await broker.release(lease, outcome=outcome, success=outcome == ProxyReleaseOutcome.SUCCESS)
        state.provider.get_current_or_rotate.assert_not_called()

    async def test_request_failure_rotates_only_after_other_holders_release(self):
        proxy = ProxyModel.from_string('127.0.0.1:8502', rotation_url='error-only-rotation-key')
        broker = ProxyBroker([proxy], account_concurrency=2)
        state = broker._proxies[proxy.raw]
        state.provider.get_current_or_rotate = AsyncMock(return_value=RotationResult(True,
            ProxyModel.from_string('127.0.0.1:8512')))
        first = await broker.lease('first')
        second = await broker.lease('second')
        await broker.release(first, outcome=ProxyReleaseOutcome.NETWORK_ERROR, success=False)
        pending = asyncio.create_task(broker.lease('after-error', timeout=1))
        await asyncio.sleep(0.02)
        state.provider.get_current_or_rotate.assert_not_called()
        await broker.release(second)
        lease = await pending
        self.assertEqual(lease.proxy.port, 8512)
        self.assertTrue(state.provider.get_current_or_rotate.call_args.kwargs['force_rotate'])
        await broker.release(lease)
        reused = await broker.lease('reuse-new-ip')
        self.assertEqual(reused.proxy.port, 8512)
        self.assertEqual(state.provider.get_current_or_rotate.await_count, 1)
        await broker.release(reused)

    async def test_failed_ip_is_not_reused_if_rotation_still_cooling(self):
        proxy = ProxyModel.from_string('127.0.0.1:8503', rotation_url='failed-ip-cooling-key')
        broker = ProxyBroker([proxy])
        state = broker._proxies[proxy.raw]
        lease = await broker.lease('first')
        await broker.release(lease, outcome=ProxyReleaseOutcome.NETWORK_ERROR, success=False)
        state.provider.get_current_or_rotate = AsyncMock(return_value=RotationResult(False, proxy,
            is_cooling=True, error='Con 24s moi co the doi proxy'))
        with self.assertRaises(ProxyTransportError):
            await broker.lease('needs-new-ip')
        self.assertEqual(state.in_use, 0)

    async def test_request_failure_in_previous_job_triggers_rotation_in_new_job(self):
        proxy = ProxyModel.from_string('127.0.0.1:8509', rotation_url='failure-between-jobs-key')
        first = ProxyBroker([proxy])
        lease = await first.lease('old-job')
        await first.release(lease, outcome=ProxyReleaseOutcome.NETWORK_ERROR, success=False)
        second = ProxyBroker([proxy])
        provider = second._proxies[proxy.raw].provider
        provider.get_current_or_rotate = AsyncMock(return_value=RotationResult(True,
            ProxyModel.from_string('127.0.0.1:8519')))
        lease = await second.lease('new-job')
        self.assertTrue(provider.get_current_or_rotate.call_args.kwargs['force_rotate'])
        self.assertEqual(lease.proxy.port, 8519)
        await second.release(lease)

    async def test_known_endpoint_bypasses_provider_cooldown_entirely(self):
        provider = ProxyVnProvider('existing-endpoint', current_endpoint='127.0.0.1:8504')
        with patch('core.proxy.rotators.AsyncSession') as session:
            result = await provider.get_current_or_rotate(wait_until_rotated=True)
        self.assertTrue(result.success)
        self.assertEqual(result.proxy.port, 8504)
        session.assert_not_called()

    async def test_exact_logged_cooldown_is_waited_on_even_with_status_101(self):
        provider = ProxyVnProvider('log-reproduction', current_endpoint='127.0.0.1:8505')
        session = SimpleNamespace(get=AsyncMock(return_value=FakeResponse({
            'status': 101, 'message': 'Con 24s moi co the doi proxy'})))
        cm = AsyncMock()
        cm.__aenter__.return_value = session
        with patch('core.proxy.rotators.AsyncSession', return_value=cm):
            result = await provider.get_current_or_rotate(force_rotate=True, wait_until_rotated=True, max_wait_seconds=0.03)
        self.assertTrue(result.is_cooling)
        self.assertFalse(is_real_proxy_error('Con 24s moi co the doi proxy', status=101))

    async def test_key_only_can_discover_endpoint_in_cooldown_response(self):
        provider = ProxyVnProvider('discover-cooling-endpoint')
        session = SimpleNamespace(get=AsyncMock(return_value=FakeResponse({
            'status': 102, 'message': 'Con 24s moi co the doi proxy',
            'data': {'proxyhttp': '127.0.0.1:8506'}})))
        cm = AsyncMock()
        cm.__aenter__.return_value = session
        with patch('core.proxy.rotators.AsyncSession', return_value=cm):
            result = await provider.get_current_or_rotate(wait_until_rotated=True)
        self.assertTrue(result.success)
        self.assertFalse(result.is_cooling)
        self.assertEqual(result.proxy.port, 8506)

    async def test_release_is_idempotent_and_job_index_clears(self):
        broker = ProxyBroker([ProxyModel.from_string('127.0.0.1:8101')], account_concurrency=2)
        one = await broker.lease('1', job_id='job')
        two = await broker.lease('2', job_id='job')
        await broker.report_failure(one, 'network')
        await broker.release(one)
        state = next(iter(broker._proxies.values()))
        self.assertEqual(state.in_use, 1)
        self.assertEqual(state.total_failures, 1)
        await broker.release(two, outcome=ProxyReleaseOutcome.RATE_LIMITED)
        self.assertGreater(state.cooling_until, time.time())
        self.assertFalse(broker._job_leases)

    async def test_configured_pool_never_falls_back_to_direct(self):
        broker = ProxyBroker([ProxyModel.from_string('127.0.0.1:8102')])
        lease = await broker.lease('first')
        with self.assertRaises(TimeoutError):
            await broker.lease('second', timeout=0.02)
        await broker.release(lease)

    async def test_independent_brokers_respect_same_proxy_slots(self):
        one = ProxyBroker([ProxyModel.from_string('127.0.0.1:8103')])
        two = ProxyBroker([ProxyModel.from_string('127.0.0.1:8103')], account_concurrency=10)
        lease = await one.lease('first')
        with self.assertRaises(TimeoutError):
            await two.lease('second', timeout=0.02)
        await one.release(lease)
        lease = await two.lease('second')
        await two.release(lease)

    async def test_rotation_does_not_block_other_release(self):
        p = ProxyModel.from_string('127.0.0.1:8104', rotation_url='key-test')
        broker = ProxyBroker([p, ProxyModel.from_string('127.0.0.1:8105')])
        state = broker._proxies[p.raw]
        first = await broker.lease('first-request')
        await broker.release(first, outcome=ProxyReleaseOutcome.NETWORK_ERROR, success=False)
        broker._round_robin_idx = 0
        started, finish = asyncio.Event(), asyncio.Event()

        async def rotate(**kwargs):
            started.set()
            await finish.wait()
            return RotationResult(True, ProxyModel.from_string('127.0.0.1:8114'))

        state.provider.get_current_or_rotate = rotate
        pending = asyncio.create_task(broker.lease('rotation'))
        await asyncio.wait_for(started.wait(), 1)
        other = await broker.lease('other')
        await asyncio.wait_for(broker.release(other), timeout=0.1)
        finish.set()
        rotated = await pending
        self.assertEqual(rotated.proxy.port, 8114)
        await broker.release(rotated)
        self.assertEqual(state.in_use, 0)

    async def test_cancelled_rotation_returns_reservation(self):
        p = ProxyModel.from_string('127.0.0.1:8106', rotation_url='cancel-key')
        broker = ProxyBroker([p])
        state = broker._proxies[p.raw]
        first = await broker.lease('first-request')
        await broker.release(first, outcome=ProxyReleaseOutcome.NETWORK_ERROR, success=False)
        started = asyncio.Event()

        async def rotate(**kwargs):
            started.set()
            await asyncio.Event().wait()

        state.provider.get_current_or_rotate = rotate
        task = asyncio.create_task(broker.lease('cancel'))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(state.in_use, 0)
        self.assertFalse(state.rotating)

    async def test_provider_cooldown_string_status_and_deadline(self):
        provider = ProxyVnProvider('offline-key', current_endpoint='127.0.0.1:8107')
        session = SimpleNamespace(get=AsyncMock(return_value=FakeResponse({'status': '102', 'message': 'cho 3s'})))
        cm = AsyncMock()
        cm.__aenter__.return_value = session
        with patch('core.proxy.rotators.AsyncSession', return_value=cm):
            started = time.monotonic()
            result = await provider.get_current_or_rotate(force_rotate=True, wait_until_rotated=True, max_wait_seconds=0.03)
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertFalse(result.success)
        self.assertTrue(result.is_cooling)

    async def test_provider_empty_response_has_deadline(self):
        provider = ProxyVnProvider('offline-key')
        session = SimpleNamespace(get=AsyncMock(return_value=FakeResponse(ValueError('bad JSON'))))
        cm = AsyncMock()
        cm.__aenter__.return_value = session
        with patch('core.proxy.rotators.AsyncSession', return_value=cm):
            result = await provider.get_current_or_rotate(wait_until_rotated=True, max_wait_seconds=0.02)
        self.assertFalse(result.success)
        self.assertFalse(result.is_cooling)

    async def test_broker_uses_discovered_cooling_endpoint(self):
        provider = ProxyVnProvider('offline-cooling')
        provider.get_current_or_rotate = AsyncMock(return_value=RotationResult(True,
            ProxyModel.from_string('127.0.0.1:8108'), is_cooling=True))
        broker = ProxyBroker(rotating_provider=provider, allow_direct_without_proxies=False)
        lease = await broker.lease('cooling')
        self.assertEqual(lease.proxy.port, 8108)
        await broker.release(lease)
        self.assertEqual(next(iter(broker._proxies.values())).in_use, 0)


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_exception_is_classified_for_rotation(self):
        runner = MochiInviteRunner()
        for error, expected in ((ConnectionError('connection reset'), 'network_error'),
                                (google.GoogleLoginError('Wrong password'), 'exception'),
                                (ProxyTransportError(proxy=None, cause='Google direct connection reset'), 'exception')):
            fetcher = SimpleNamespace(login=AsyncMock(side_effect=error), close=AsyncMock())
            with patch('core.platforms.mochichat.runners.invite_referral.MochiChatFetcher', return_value=fetcher):
                result = await runner._process_account('classification', 'TEST', account(0),
                    ProxyModel.from_string('127.0.0.1:8507'), 'classification-owner')
            self.assertEqual(result.status, expected)

    async def test_no_target_overshoot_with_staggered_accounts(self):
        runner = MochiInviteRunner(concurrency=5)
        processed = []

        async def process(job_id, code, item, proxy, owner, **kwargs):
            processed.append(item.identifier)
            await asyncio.sleep(0.002 * (int(item.identifier) % 3))
            return MochiAccountResult(item.identifier, True, 'success')

        runner._process_account = process
        result = await runner.run('target', 'TEST', 11, accounts=[account(i) for i in range(30)])
        self.assertEqual(result.success_count, 11)
        self.assertEqual(len(processed), 11)
        self.assertEqual(result.attempted_count, 11)

    async def test_empty_explicit_accounts_never_purchase(self):
        runner = MochiInviteRunner()
        with patch('core.platforms.mochichat.runners.invite_referral.GmailVipVn') as provider:
            result = await runner.run('empty', 'TEST', 1, accounts=[])
        provider.assert_not_called()
        self.assertEqual(result.status, 'failed')

    async def test_exhausted_accounts_not_completed_or_purchased(self):
        runner = MochiInviteRunner(concurrency=3)
        runner._process_account = AsyncMock(return_value=MochiAccountResult('1', False, 'auth_failed'))
        result = await runner.run('exhaust', 'TEST', 3, accounts=[account(1)])
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.attempted_count, 1)
        self.assertFalse(runner.proxy_client.broker._leases)

    async def test_purchase_error_returns_proxy_slot(self):
        broker = ProxyBroker([ProxyModel.from_string('127.0.0.1:8201')])
        runner = MochiInviteRunner(proxy_client=ProxyPoolClient(broker=broker),
            proxy_probe=AsyncMock(return_value=ProxyHealthResult(True, 'http://127.0.0.1:8201', 'https', http_status=200)))
        provider = AsyncMock()
        provider.__aenter__.return_value.buy_gmail_domain_info.side_effect = ValueError('offline purchase error')
        with patch('core.platforms.mochichat.runners.invite_referral.GmailVipVn', return_value=provider):
            result = await runner.run('purchase-error', 'TEST', 1)
        self.assertEqual(result.status, 'failed')
        self.assertFalse(broker._leases)
        self.assertEqual(next(iter(broker._proxies.values())).in_use, 0)

    async def test_lease_failure_stops_before_purchase_or_login(self):
        client = SimpleNamespace(lease=AsyncMock(side_effect=RuntimeError('proxy unavailable')),
            release_job=AsyncMock())
        runner = MochiInviteRunner(proxy_client=client)
        runner._process_account = AsyncMock()
        result = await runner.run('lease-error', 'TEST', 1)
        self.assertEqual(result.status, 'failed')
        runner._process_account.assert_not_called()
        self.assertEqual(result.attempted_count, 0)

    async def test_terminal_error_preserved_and_pending_accounts_cancelled(self):
        runner = MochiInviteRunner(concurrency=2)
        cancelled = asyncio.Event()

        async def process(job_id, code, item, proxy, owner, **kwargs):
            if item.identifier == '0':
                await asyncio.sleep(0.01)
                return MochiAccountResult('0', False, 'invalid_code', 'invalid')
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        runner._process_account = process
        result = await runner.run('invalid', 'TEST', 2, accounts=[account(0), account(1)])
        self.assertEqual(result.status, 'invalid_code')
        self.assertTrue(cancelled.is_set())
        self.assertFalse(runner.proxy_client.broker._leases)

    async def test_cancellation_preserves_counts_and_drains_children(self):
        runner = MochiInviteRunner(concurrency=2)
        waiting = asyncio.Event()

        async def process(job_id, code, item, proxy, owner, **kwargs):
            if item.identifier == '0':
                return MochiAccountResult('0', True, 'success')
            waiting.set()
            await asyncio.Event().wait()

        runner._process_account = process
        task = asyncio.create_task(runner.run('cancel', 'TEST', 3, accounts=[account(i) for i in range(3)]))
        await waiting.wait()
        await asyncio.sleep(0.01)
        task.cancel()
        result = await task
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(result.success_count, 1)
        self.assertGreater(result.attempted_count, 0)
        self.assertFalse(runner.proxy_client.broker._leases)


class FetcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_false_or_invalid_redeem_not_success(self):
        for data in ({'ok': False, 'message': 'rejected'}, {'success': False}, {},
                     {'error': 'failed'}, [], ValueError('HTML')):
            with self.subTest(data=data):
                session = SimpleNamespace(post=AsyncMock(return_value=FakeResponse(data)))
                fetcher = MochiChatFetcher(session=session)
                result = await fetcher.redeem_invite_code('TEST')
                self.assertFalse(result.success)

    async def test_login_requires_http_success_and_access_token(self):
        for data, status in (({'ok': True, 'data': {'token': 'token'}}, 500),
                             ({'ok': True}, 200), ({'ok': False}, 200), ([], 200)):
            session = SimpleNamespace(post=AsyncMock(return_value=FakeResponse(data, status)))
            fetcher = MochiChatFetcher(session=session)
            result = await fetcher._login_mochi_with_id_token('offline')
            self.assertFalse(result['ok'])

    async def test_google_proxy_option_preserves_api_proxy(self):
        proxy = ProxyModel.from_string('127.0.0.1:8202:user:pass')
        fetcher = MochiChatFetcher(session=SimpleNamespace(), proxy=proxy)
        fetcher._login_mochi_with_id_token = AsyncMock(return_value={'ok': True})
        with patch('core.platforms.mochichat.fetcher.get_google_id_token_for_app', AsyncMock(return_value='eyJtest')) as auth:
            await fetcher.login('owner', 'test@example.com', 'pw', google_login_via_proxy=False)
        self.assertFalse(auth.call_args.kwargs['google_login_via_proxy'])
        self.assertIs(fetcher.proxy, proxy)

    async def test_google_proxy_switch_reaches_browser(self):
        for enabled in (True, False):
            with patch.object(google, '_extract_id_token_via_browser', AsyncMock(return_value='eyJtest')) as flow:
                await google.get_google_id_token_for_app('owner', 'test@example.com', 'pw', 'client',
                    proxy='http://127.0.0.1:8202', google_login_via_proxy=enabled)
            self.assertEqual(flow.call_args.kwargs['proxy'], 'http://127.0.0.1:8202' if enabled else None)


class ModelTests(unittest.TestCase):
    def test_proxy_slots_shared_across_real_worker_threads(self):
        acquired, finish = threading.Event(), threading.Event()

        def first_thread():
            async def run():
                broker = ProxyBroker([ProxyModel.from_string('127.0.0.1:8401')])
                lease = await broker.lease('thread-one')
                acquired.set()
                await asyncio.to_thread(finish.wait, 2)
                await broker.release(lease)
            asyncio.run(run())

        def second_thread():
            async def run():
                broker = ProxyBroker([ProxyModel.from_string('127.0.0.1:8401')])
                try:
                    await broker.lease('thread-two', timeout=0.02)
                except TimeoutError:
                    return True
                return False
            return asyncio.run(run())

        with ThreadPoolExecutor(max_workers=2) as executor:
            first = executor.submit(first_thread)
            self.assertTrue(acquired.wait(2))
            try:
                self.assertTrue(executor.submit(second_thread).result(timeout=2))
            finally:
                finish.set()
            first.result(timeout=2)

    def test_auth_and_protocol_survive_proxy_conversion(self):
        model = ProxyModel.from_string('socks5://127.0.0.1:1080:user:pa:ss')
        self.assertEqual(model.password, 'pa:ss')
        self.assertEqual(curl_proxy_options(model)['https'], 'socks5://user:pa%3Ass@127.0.0.1:1080')
        self.assertEqual(curl_proxy_options(model.to_playwright_dict()), curl_proxy_options(model))

    def test_legacy_password_with_at_and_colon_survives_both_client_conversions(self):
        for raw, protocol, password in (
                ('127.0.0.1:1080:user:pa@ss', 'http', 'pa@ss'),
                ('http://proxy.example.test:1080:user:pa@ss:word', 'http', 'pa@ss:word'),
                ('socks5://127.0.0.1:1080:user:pa:ss@word', 'socks5', 'pa:ss@word')):
            with self.subTest(raw=raw):
                model = ProxyModel.from_string(raw)
                self.assertEqual((model.port, model.username, model.password, model.protocol),
                    (1080, 'user', password, protocol))
                self.assertEqual(ProxyModel.from_string(model.to_url()).password, password)
                self.assertEqual(curl_proxy_options(model.to_playwright_dict()), curl_proxy_options(model))
                self.assertIn('%40', model.to_url())

    def test_standard_url_credentials_and_ipv6_are_preserved(self):
        for raw, host, protocol, password in (
                ('user:pa:ss@proxy.example.test:1080', 'proxy.example.test', 'http', 'pa:ss'),
                ('http://user:123:abc@proxy.example.test:1080', 'proxy.example.test', 'http', '123:abc'),
                ('https://user:pa@ss@proxy.example.test:1080', 'proxy.example.test', 'https', 'pa@ss'),
                ('socks5h://user:pa%40ss%3Aword@[::1]:1080', '::1', 'socks5h', 'pa@ss:word')):
            with self.subTest(raw=raw):
                model = ProxyModel.from_string(raw)
                self.assertEqual((model.host, model.port, model.username, model.password, model.protocol),
                    (host, 1080, 'user', password, protocol))
                self.assertEqual(ProxyModel.from_string(model.to_url()).to_curl_cffi_dict(), model.to_curl_cffi_dict())

    def test_malformed_authority_never_becomes_a_different_proxy(self):
        for raw in ('user:pw@proxy.example.test', 'http://user:pw@proxy.example.test:bad',
                    'http://[::1]:bad', '127.0.0.1:invalid:user:pa@ss'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ProxyModel.from_string(raw)

    def test_invalid_proxy_not_silently_changed_to_port_8080(self):
        for value in ('127.0.0.1:abc', '127.0.0.1:0', '127.0.0.1:65536', 'missing-port'):
            with self.assertRaises(ValueError):
                ProxyModel.from_string(value)

    def test_cooldown_not_confused_with_unauthorized(self):
        self.assertTrue(is_cooldown_or_wait_message('cho 15s'))
        self.assertFalse(is_cooldown_or_wait_message('Khong cho phep truy cap: unauthorized'))
        self.assertTrue(is_cooldown_or_wait_message('Con 24s moi co the doi proxy'))
        self.assertTrue(is_cooldown_or_wait_message('Còn 24s mới có thể đổi proxy'))
        self.assertFalse(is_request_transport_error('Wrong password'))
        self.assertFalse(is_request_transport_error('INVALID_CODE'))
        self.assertTrue(is_request_transport_error('Page.goto: net::ERR_PROXY_CONNECTION_FAILED'))


class MailTests(unittest.IsolatedAsyncioTestCase):
    async def test_uncertain_purchase_is_never_automatically_repeated(self):
        for provider_type in (GmailVipVn, OneTrieuMail):
            client = provider_type('offline-key')
            session = SimpleNamespace(post=AsyncMock(side_effect=TimeoutError('lost response')))
            client._session = session
            with self.assertRaises(ValueError):
                await client.buy_gmail_domain_info([1, 2, 3])
            self.assertEqual(session.post.await_count, 1)

    async def test_duplicate_purchase_stops_before_another_charge(self):
        for provider_type in (GmailVipVn, OneTrieuMail):
            client = provider_type('offline-key')
            provider_type.clear_seen_cache()
            provider_type._seen_usernames.add('duplicate@example.com')
            client.buy_product = AsyncMock(return_value=['DUPLICATE@example.com|pw'])
            with self.assertRaises(ValueError):
                await client.buy_gmail_domain_info([1, 2])
            self.assertEqual(client.buy_product.await_count, 1)
            provider_type.clear_seen_cache()

    async def test_invalid_success_payload_stops_before_another_charge(self):
        for provider_type in (GmailVipVn, OneTrieuMail):
            client = provider_type('offline-key')
            client.buy_product = AsyncMock(return_value=['not-an-account'])
            with self.assertRaises(ValueError):
                await client.buy_gmail_domain_info([1, 2])
            self.assertEqual(client.buy_product.await_count, 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
