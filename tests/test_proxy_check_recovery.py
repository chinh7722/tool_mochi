"""IP-check diagnostics, bounded recovery and explicit proxy routing; offline."""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from curl_cffi.requests.errors import RequestsError
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from core.proxy.checker import CHECK_APIS, probe_proxy_connection
from core.proxy.rotators import RotationResult, is_cooldown_or_wait_message
from core.proxy.models import ProxyModel
from core.job_failure import job_failure_reason
from gui.pages.proxy import ProxyPage

MODULE = 'core.proxy.checker'

def response(payload=None, status=200):
    return SimpleNamespace(status_code=status, json=Mock(return_value=payload))

def context(client):
    manager = AsyncMock()
    manager.__aenter__.return_value = client
    return manager

def reset():
    return RequestsError('curl: (56) Recv failure: Connection was reset api_key=secret-value', code=56)


class CheckerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def run_probe(self, replies, fresh_replies=(), **kwargs):
        normal = SimpleNamespace(get=AsyncMock(side_effect=replies))
        fresh = SimpleNamespace(get=AsyncMock(side_effect=fresh_replies))
        normal_context, fresh_context = context(normal), context(fresh)
        with patch(f'{MODULE}.AsyncSession', side_effect=[normal_context, fresh_context]) as factory:
            result = await probe_proxy_connection('127.0.0.1:8100', **kwargs)
        return result, normal, fresh, normal_context, fresh_context, factory

    async def test_connection_reset_retries_same_endpoint_once_using_fresh_session(self):
        result, normal, fresh, first, second, factory = await self.run_probe([reset()],
            [response({'ip': '203.0.113.9'})])
        self.assertTrue(result.success)
        self.assertEqual(result.exit_ip, '203.0.113.9')
        self.assertEqual(result.error, '')
        self.assertEqual(normal.get.await_args, fresh.get.await_args)
        self.assertEqual(factory.call_count, 2)
        self.assertTrue(all(c.kwargs == {'trust_env': False} for c in factory.call_args_list))
        first.__aexit__.assert_awaited_once()
        second.__aexit__.assert_awaited_once()

    async def test_screenshot_reset_reports_all_sources_and_curl56_without_secrets(self):
        result, normal, fresh, *_ = await self.run_probe([reset() for _ in CHECK_APIS], [reset()])
        self.assertFalse(result.success)
        self.assertTrue(result.transport_error)
        self.assertEqual(result.curl_code, 56)
        self.assertIn('ngắt/reset', result.error)
        self.assertIn('7 nguồn', result.error)
        self.assertIn('curl 56', result.error)
        self.assertIn('ip-api.com', result.error)
        self.assertIn('ipify64.org', result.error)
        self.assertNotIn('secret-value', result.error)
        self.assertIn('kết nối bị ngắt', job_failure_reason(message=result.error))
        self.assertEqual(normal.get.await_count + fresh.get.await_count, len(CHECK_APIS) + 1)

    async def test_reset_at_one_provider_falls_back_without_renewing_retry_budget(self):
        result, normal, fresh, *_ = await self.run_probe([reset(), reset(), response({'ip': '203.0.113.8'})], [reset()])
        self.assertTrue(result.success)
        self.assertEqual(result.source_api, 'ipinfo.io')
        self.assertEqual(normal.get.await_count, 3)
        self.assertEqual(fresh.get.await_count, 1)

    async def test_http_429_cause_is_kept_when_later_providers_reset(self):
        result, *_ = await self.run_probe([response(status=429)] + [reset() for _ in CHECK_APIS[1:]], [reset()])
        self.assertIn('ip-api.com trả HTTP 429', result.error)
        self.assertIn('curl 56', result.error)
        self.assertNotIn('sai key', result.error)

    async def test_407_after_several_failures_has_priority_and_stops(self):
        result, normal, fresh, *_ = await self.run_probe([response(status=429), response(status=503), response(status=407)])
        self.assertEqual(result.http_status, 407)
        self.assertIn('xác thực', result.error)
        self.assertNotIn('HTTP 429', result.error)
        self.assertEqual(normal.get.await_count, 3)
        fresh.get.assert_not_awaited()

    async def test_fresh_retry_timeout_keeps_existing_timeout_budget(self):
        result, normal, fresh, *_ = await self.run_probe([reset(), asyncio.TimeoutError()], [asyncio.TimeoutError()])
        self.assertFalse(result.success)
        self.assertIn('hết thời gian chờ', result.error)
        self.assertEqual(normal.get.await_count + fresh.get.await_count, 3)
        self.assertFalse(is_cooldown_or_wait_message(result.error))

    async def test_cancellation_in_fresh_retry_propagates_and_closes_both_sessions(self):
        normal = SimpleNamespace(get=AsyncMock(side_effect=[reset()]))
        fresh = SimpleNamespace(get=AsyncMock(side_effect=asyncio.CancelledError()))
        first, second = context(normal), context(fresh)
        with patch(f'{MODULE}.AsyncSession', side_effect=[first, second]):
            with self.assertRaises(asyncio.CancelledError):
                await probe_proxy_connection('127.0.0.1:8100')
        first.__aexit__.assert_awaited_once()
        second.__aexit__.assert_awaited_once()

    async def test_windows_reset_without_curl_code_also_uses_fresh_connection(self):
        result, *_ = await self.run_probe([ConnectionResetError(10054, 'reset')],
            [response({'ip': '203.0.113.7'})])
        self.assertTrue(result.success)

    async def test_explicit_proxy_is_used_even_when_no_proxy_environment_is_star(self):
        seen = []
        async def handle(reader, writer):
            seen.append((await reader.readuntil(b'\r\n\r\n')).decode('ascii'))
            payload = b'{"query":"203.0.113.99","status":"success"}'
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n'
                + f'Content-Length: {len(payload)}\r\n\r\n'.encode() + payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(handle, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        try:
            with patch.dict(os.environ, {'NO_PROXY': '*', 'no_proxy': '*', 'HTTP_PROXY': 'http://127.0.0.1:1'}):
                result = await probe_proxy_connection(f'127.0.0.1:{port}', timeout=2)
        finally:
            server.close()
            await server.wait_closed()
        self.assertTrue(result.success)
        self.assertEqual(result.exit_ip, '203.0.113.99')
        self.assertEqual(len(seen), 1)
        self.assertTrue(seen[0].startswith('GET http://ip-api.com/'))


class ProxyCheckUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(str(Path(self.temp.name) / 'proxy.ini'), QSettings.IniFormat)
        self.page = ProxyPage(settings=self.settings)
        self.addCleanup(self.page.close)
        self.item = self.page._parse_proxy_line('192.0.2.10:10870|offline-key')
        self.page._proxies[self.item['id']] = self.item
        self.model = ProxyModel.from_string(self.item['endpoint'])

    def result(self, **kwargs):
        self.page._on_rotate_finished(self.item['id'], RotationResult(proxy=self.model, **kwargs), 123)

    def test_repeatable_reset_stays_failed_until_real_ip_check_succeeds(self):
        self.result(success=False, error='ip: Kết nối proxy bị ngắt/reset (curl 56).')
        self.assertTrue(self.item['is_failed'])
        self.assertEqual(self.page.get_active_proxies(), [])
        self.assertIn('curl 56', self.item['error_reason'])
        with patch('gui.pages.proxy.update_proxy_endpoint'):
            self.result(success=True)
        self.assertFalse(self.item['is_failed'])
        self.assertEqual(self.item['error_reason'], '')
        self.assertEqual(len(self.page.get_active_proxies()), 1)

    def test_ip_check_timeout_is_not_misclassified_as_rotation_cooldown(self):
        self.result(success=False, error='ip: Kiểm tra IP hết thời gian chờ (curl 28).')
        self.assertTrue(self.item['is_failed'])
        self.assertNotEqual(self.item['status'], 'COOLING')

    def test_actual_rotation_cooldown_remains_reusable(self):
        self.result(success=False, is_cooling=True, wait_seconds=24, error='Còn 24s mới có thể đổi proxy')
        self.assertFalse(self.item['is_failed'])
        self.assertEqual(self.item['status'], 'COOLING')
        self.assertEqual(len(self.page.get_active_proxies()), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
