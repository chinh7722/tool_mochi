"""Offline proxy preflight checks and purchase gating regressions.

Run: py -3.14 -I tests/test_proxy_health.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from curl_cffi import CurlHttpVersion, CurlOpt
from curl_cffi.requests.errors import RequestsError

from core.proxy.health import ProxyHealthResult, probe_proxy_target
from core.proxy.models import ProxyModel
from core.proxy.broker import ProxyBroker
from core.proxy.client import ProxyPoolClient
from core.proxy.rotators import RotationResult
import core.proxy.broker as broker_module
from core.platforms.mochichat.runners.invite_referral import MochiInviteRunner, MochiAccountResult


HEALTH = 'core.proxy.health'
RUNNER = 'core.platforms.mochichat.runners.invite_referral'
TARGET = 'https://mochi.lightchaser.xyz/api'


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        resolver = patch(f'{HEALTH}.resolve_proxy_dns_route', AsyncMock(return_value=None))
        resolver.start()
        self.addCleanup(resolver.stop)
    def proxy(self):
        return ProxyModel.from_string('http://127.0.0.1:8900:offline-user:offline-password')

    def network(self, *, status=200, error=None):
        writer = SimpleNamespace(close=Mock(), wait_closed=AsyncMock())
        connection = AsyncMock(return_value=(SimpleNamespace(), writer))
        client = SimpleNamespace(head=AsyncMock(return_value=SimpleNamespace(status_code=status)))
        if error is not None:
            client.head.side_effect = error
        context = AsyncMock()
        context.__aenter__.return_value = client
        return connection, writer, client, context

    async def test_tcp_failure_stops_before_sending_https_request(self):
        connection = AsyncMock(side_effect=ConnectionRefusedError('proxy endpoint refused connection'))
        with patch(f'{HEALTH}.asyncio.open_connection', connection), \
             patch(f'{HEALTH}.AsyncSession') as factory:
            result = await probe_proxy_target(self.proxy(), target_url=TARGET, timeout=1)
        self.assertFalse(result.success)
        self.assertIn('tcp', result.stage.lower())
        self.assertTrue(result.transport_error)
        factory.assert_not_called()

    async def test_tcp_success_then_tls_failure_is_not_reported_as_healthy(self):
        connection, writer, client, context = self.network(error=RequestsError('SSL handshake failed', code=35))
        with patch(f'{HEALTH}.asyncio.open_connection', connection), \
             patch(f'{HEALTH}.AsyncSession', return_value=context):
            result = await probe_proxy_target(self.proxy(), target_url=TARGET, timeout=1)
        self.assertFalse(result.success)
        self.assertNotIn(result.stage.lower(), ('tcp', 'tcp_connect'))
        self.assertEqual(result.curl_code, 35)
        self.assertTrue(result.transport_error)
        client.head.assert_awaited_once()
        writer.close.assert_called_once()

    async def test_head_405_proves_https_reachability_with_explicit_proxy_and_verified_tls(self):
        proxy = self.proxy()
        connection, writer, client, context = self.network(status=405)
        with patch(f'{HEALTH}.asyncio.open_connection', connection), \
             patch(f'{HEALTH}.AsyncSession', return_value=context) as factory:
            result = await probe_proxy_target(proxy, target_url=TARGET, timeout=1)
        self.assertTrue(result.success)
        self.assertEqual(result.http_status, 405)
        options = factory.call_args.kwargs
        self.assertIs(options.get('trust_env'), False)
        self.assertIs(options.get('verify'), True)
        explicit_proxy = options.get('proxy') or options.get('proxies')
        self.assertIn(explicit_proxy, (proxy.to_url(), proxy.to_curl_cffi_dict()))
        self.assertTrue(
            options.get('http_version') in ('v1', CurlHttpVersion.V1_1)
            or options.get('curl_options', {}).get(CurlOpt.HTTP_VERSION) == CurlHttpVersion.V1_1)
        self.assertEqual(client.head.call_args.args[0], 'https://mochi.lightchaser.xyz/')
        writer.close.assert_called_once()

    async def test_target_503_is_not_misclassified_as_a_proxy_transport_failure(self):
        connection, writer, client, context = self.network(status=503)
        with patch(f'{HEALTH}.asyncio.open_connection', connection), \
             patch(f'{HEALTH}.AsyncSession', return_value=context):
            result = await probe_proxy_target(self.proxy(), target_url=TARGET, timeout=1)
        self.assertFalse(result.success)
        self.assertEqual(result.http_status, 503)
        self.assertFalse(result.transport_error)

    async def test_proxy_407_is_rejected_before_any_authenticated_account_action(self):
        connection, writer, client, context = self.network(status=407)
        with patch(f'{HEALTH}.asyncio.open_connection', connection), \
             patch(f'{HEALTH}.AsyncSession', return_value=context):
            result = await probe_proxy_target(self.proxy(), target_url=TARGET, timeout=1)
        self.assertFalse(result.success)
        self.assertEqual(result.http_status, 407)


class RunnerPreflightTests(unittest.IsolatedAsyncioTestCase):
    def make_runner(self, probe):
        import tempfile
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        proxy = ProxyModel.from_string('http://127.0.0.1:8901', rotation_url='offline-health-' + self.id())
        broker = ProxyBroker([proxy], account_concurrency=1, allow_direct_without_proxies=False)
        client = ProxyPoolClient(broker=broker)
        logs = []
        runner = MochiInviteRunner(proxy_client=client, concurrency=1, proxy_probe=probe, mail_batch_dir=directory.name,
                                  google_login_via_proxy=False, log_sink=lambda event: logs.append(event.message))
        state = broker._proxies[proxy.raw]
        state.provider.get_current_or_rotate = AsyncMock(return_value=RotationResult(True, proxy))
        runner._process_account = AsyncMock(return_value=MochiAccountResult('offline-account', True, 'success'))
        return runner, broker, state, proxy, logs

    def mail(self):
        client = SimpleNamespace(buy_gmail_domain_info=AsyncMock(return_value=[
            SimpleNamespace(username='offline-account@example.com', password='offline-password')]))
        context = AsyncMock()
        context.__aenter__.return_value = client
        return client, context

    def assert_pool_drained(self, broker, state):
        self.assertFalse(broker._leases)
        self.assertFalse(broker._job_leases)
        self.assertEqual(state.in_use, 0)
        self.assertFalse(state.rotating)
        with broker_module._SHARED_LOCK:
            self.assertEqual(broker_module._SHARED_USAGE.get(state.shared_key, 0), 0)
            self.assertNotIn(state.shared_key, broker_module._SHARED_ROTATING)

    def healthy(self, proxy):
        return ProxyHealthResult(True, proxy.server, 'https', http_status=200)

    def failed(self, proxy):
        return ProxyHealthResult(False, proxy.server, 'tcp', error='endpoint timeout', curl_code=28)

    async def test_healthy_proxy_is_probed_before_buy_and_never_forced_to_rotate(self):
        order = []

        async def probe(proxy, *args, **kwargs):
            order.append('probe')
            return self.healthy(proxy)

        runner, broker, state, proxy, logs = self.make_runner(AsyncMock(side_effect=probe))
        mail_client, mail_context = self.mail()
        initial_buy = mail_client.buy_gmail_domain_info

        async def buy(*args, **kwargs):
            order.append('buy')
            return await initial_buy(*args, **kwargs)

        mail_client.buy_gmail_domain_info = AsyncMock(side_effect=buy)
        with patch(f'{RUNNER}.GmailVipVn', return_value=mail_context):
            result = await runner.run('healthy-preflight', 'TEST', 1)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.attempted_count, 1)
        self.assertEqual(order, ['probe', 'buy'])
        state.provider.get_current_or_rotate.assert_not_awaited()
        mail_client.buy_gmail_domain_info.assert_awaited_once()
        runner._process_account.assert_awaited_once()
        self.assert_pool_drained(broker, state)

    async def test_failed_probe_rotates_once_and_accepts_same_endpoint_if_probe_recovers(self):
        order = []
        count = 0

        async def probe(proxy, *args, **kwargs):
            nonlocal count
            count += 1
            order.append('probe')
            return self.failed(proxy) if count == 1 else self.healthy(proxy)

        runner, broker, state, original, logs = self.make_runner(AsyncMock(side_effect=probe))

        async def rotate(**kwargs):
            self.assertTrue(kwargs['force_rotate'])
            order.append('rotate')
            return RotationResult(True, original)

        state.provider.get_current_or_rotate.side_effect = rotate
        mail_client, mail_context = self.mail()
        with patch(f'{RUNNER}.GmailVipVn', return_value=mail_context):
            result = await runner.run('same-endpoint-preflight', 'TEST', 1)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(order, ['probe', 'rotate', 'probe'])
        self.assertEqual(result.attempted_count, 1)
        state.provider.get_current_or_rotate.assert_awaited_once()
        mail_client.buy_gmail_domain_info.assert_awaited_once()
        self.assert_pool_drained(broker, state)

    async def test_two_failed_probes_never_buy_mail_or_open_google(self):
        probe = AsyncMock(side_effect=lambda proxy, *args, **kwargs: self.failed(proxy))
        runner, broker, state, proxy, logs = self.make_runner(probe)
        with patch(f'{RUNNER}.GmailVipVn') as mail, \
             patch('core.platforms.mochichat.fetcher.get_google_id_token_for_app', AsyncMock()) as google:
            result = await runner.run('dead-endpoint-preflight', 'TEST', 10)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.attempted_count, 0)
        self.assertEqual(probe.await_count, 2)
        state.provider.get_current_or_rotate.assert_awaited_once()
        mail.assert_not_called()
        google.assert_not_awaited()
        runner._process_account.assert_not_awaited()
        self.assert_pool_drained(broker, state)

    async def test_replacement_is_probed_and_logged_with_actual_returned_endpoint(self):
        seen = []

        async def probe(proxy, *args, **kwargs):
            seen.append(proxy.server)
            return self.failed(proxy) if len(seen) == 1 else self.healthy(proxy)

        runner, broker, state, original, logs = self.make_runner(AsyncMock(side_effect=probe))
        replacement = ProxyModel.from_string('http://127.0.0.1:8911')
        state.provider.get_current_or_rotate.return_value = RotationResult(True, replacement)
        mail_client, mail_context = self.mail()
        with patch(f'{RUNNER}.GmailVipVn', return_value=mail_context):
            result = await runner.run('replacement-endpoint-preflight', 'TEST', 1)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(seen, [original.server, replacement.server])
        self.assertTrue(any(replacement.server in message for message in logs))
        self.assert_pool_drained(broker, state)

    async def test_failed_target_http_503_does_not_rotate_proxy_or_buy_mail(self):
        probe = AsyncMock(return_value=ProxyHealthResult(False, 'http://127.0.0.1:8901', 'https',
            error='Mochi HTTP 503', http_status=503, transport_error=False))
        runner, broker, state, proxy, logs = self.make_runner(probe)
        with patch(f'{RUNNER}.GmailVipVn') as mail:
            result = await runner.run('target-unavailable-preflight', 'TEST', 1)
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.attempted_count, 0)
        state.provider.get_current_or_rotate.assert_not_awaited()
        mail.assert_not_called()
        runner._process_account.assert_not_awaited()
        self.assert_pool_drained(broker, state)

    async def test_cancelled_probe_releases_lease_without_purchasing(self):
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def probe(proxy, *args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        runner, broker, state, proxy, logs = self.make_runner(AsyncMock(side_effect=probe))
        with patch(f'{RUNNER}.GmailVipVn') as mail:
            task = asyncio.create_task(runner.run('cancelled-preflight', 'TEST', 1))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            result = await asyncio.wait_for(task, 1)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(result.attempted_count, 0)
        self.assertTrue(cancelled.is_set())
        mail.assert_not_called()
        runner._process_account.assert_not_awaited()
        self.assert_pool_drained(broker, state)


if __name__ == '__main__':
    unittest.main(verbosity=2)
