"""Offline static/rotating proxy compatibility in the existing proxy page.

Run: py -3.14 -I tests/test_proxy_static_ui.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import asyncio
from contextlib import nullcontext
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QPushButton

from gui.pages import proxy as proxy_module


class StaticProxyUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        cls.app.setFont(QFont('Segoe UI', 10))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = QSettings(str(Path(self.directory.name) / 'proxy.ini'), QSettings.Format.IniFormat)
        self.page = proxy_module.ProxyPage(settings=self.settings)
        self.addCleanup(self._close_page)

    def _close_page(self):
        self.page.shutdown_workers()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def add_proxy(self, text, *, failed=False):
        item = self.page._parse_proxy_line(text)
        self.assertIsNotNone(item, text)
        item['is_failed'] = failed
        self.page._proxies[item['id']] = item
        return item

    def run_check(self, ids):
        health = SimpleNamespace(success=True, stage='ip', error=None)
        with patch.object(proxy_module, 'start_background') as start, \
                patch.object(proxy_module, 'probe_proxy_connection', AsyncMock(return_value=health)) as probe, \
                patch.object(proxy_module, 'proxy_maintenance', side_effect=lambda _key: nullcontext()) as maintenance, \
                patch.object(proxy_module, 'update_proxy_endpoint') as update:
            self.page._check_proxy_batch(ids)
            start.assert_called_once()
            factory, finished, restore = (start.call_args.args[1], start.call_args.args[2], start.call_args.args[4])
            finished(asyncio.run(factory()))
            restore()
        return probe, maintenance, update

    def test_plain_authenticated_and_socks_static_inputs_preserve_endpoint_format(self):
        endpoints = ('127.0.0.1:8080', '127.0.0.1:8080:alice:p@ss:word',
            'http://alice:p%40ss%3Aword@127.0.0.1:8080',
            'socks5://alice:secret@127.0.0.1:1080', 'socks5h://127.0.0.1:1080',
            'http://[::1]:8080')
        for endpoint in endpoints:
            with self.subTest(endpoint=endpoint):
                parsed = self.page._parse_proxy_line(endpoint)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed['endpoint'], endpoint)
                self.assertEqual(parsed['key_xoay'], '')
                self.assertEqual(parsed['provider'], 'Normal - Proxy thường')

    def test_rotation_inputs_still_preserve_key_and_protocol(self):
        parsed = self.page._parse_proxy_line('socks5://127.0.0.1:1080|offline-key')
        self.assertEqual(parsed['endpoint'], 'socks5://127.0.0.1:1080')
        self.assertEqual(parsed['key_xoay'], 'offline-key')
        self.assertEqual(parsed['provider'], 'Xoay - proxy.vn')

    def test_malformed_endpoints_and_missing_explicit_rotation_keys_are_rejected(self):
        for text in ('', 'key-only', '127.0.0.1:abc', '127.0.0.1:0',
                '127.0.0.1:65536', 'ftp://127.0.0.1:8080', '|offline-key',
                '127.0.0.1:8080|', '127.0.0.1:8080|   '):
            with self.subTest(text=text):
                self.assertIsNone(self.page._parse_proxy_line(text))

    def test_bom_and_surrounding_whitespace_do_not_become_part_of_the_host(self):
        static = self.page._parse_proxy_line('\ufeff 127.0.0.1:8080\r\n')
        rotating = self.page._parse_proxy_line('\ufeff127.0.0.1:8081 | offline-key ')
        self.assertEqual(static['endpoint'], '127.0.0.1:8080')
        self.assertEqual(rotating['endpoint'], '127.0.0.1:8081')
        self.assertEqual(rotating['key_xoay'], 'offline-key')

    def test_static_identity_normalizes_url_equivalents_but_preserves_credentials_and_scheme(self):
        def identity(text):
            return self.page._proxy_identity(self.page._parse_proxy_line(text))

        self.assertEqual(identity('127.0.0.1:8080'), identity('http://127.0.0.1:8080'))
        self.assertEqual(identity('127.0.0.1:8080:alice:p@ss:word'),
            identity('http://alice:p%40ss%3Aword@127.0.0.1:8080'))
        self.assertNotEqual(identity('127.0.0.1:8080:alice:one'),
            identity('127.0.0.1:8080:alice:two'))
        self.assertNotEqual(identity('http://127.0.0.1:8080'), identity('socks5://127.0.0.1:8080'))
        self.assertNotEqual(identity('127.0.0.1:8080'), identity('127.0.0.1:8081'))

    def test_rotation_identity_uses_the_provider_key_instead_of_the_current_endpoint(self):
        first = self.page._parse_proxy_line('127.0.0.1:8080|offline-key')
        second = self.page._parse_proxy_line(
            '127.0.0.1:8081|https://proxy.vn/api/get-ip?key=offline-key&nhamang=fpt')
        other = self.page._parse_proxy_line('127.0.0.1:8080|other-key')
        self.assertEqual(self.page._proxy_identity(first), self.page._proxy_identity(second))
        self.assertNotEqual(self.page._proxy_identity(first), self.page._proxy_identity(other))

    def test_settings_reload_keeps_static_auth_and_rotating_entries(self):
        first = self.add_proxy('socks5://alice:secret@127.0.0.1:1080')
        second = self.add_proxy('127.0.0.1:8080|offline-key')
        expected = [(first['endpoint'], first['key_xoay']), (second['endpoint'], second['key_xoay'])]
        self.page._save_to_settings()
        self.settings.sync()
        reloaded = proxy_module.ProxyPage(settings=self.settings)
        try:
            self.assertEqual(reloaded.get_active_proxies(), expected)
        finally:
            reloaded.shutdown_workers()
            reloaded.close()
            reloaded.deleteLater()

    def test_settings_invalid_null_endpoint_does_not_drop_following_valid_static_proxy(self):
        valid = self.page._parse_proxy_line('127.0.0.1:8080')
        self.settings.setValue('proxy/managed_list', json.dumps([{'endpoint': None}, valid]))
        self.page._load_from_settings()
        self.assertEqual(self.page.get_active_proxies(), [('127.0.0.1:8080', '')])

    def test_static_runtime_updates_never_consult_the_rotation_registry(self):
        first = self.add_proxy('127.0.0.1:8080')
        second = self.add_proxy('127.0.0.1:8081')
        with patch.object(proxy_module, 'proxy_activity', side_effect=AssertionError('empty rotation key')) as activity:
            self.page._refresh_runtime_status()
        activity.assert_not_called()
        self.assertEqual((first['endpoint'], second['endpoint']), ('127.0.0.1:8080', '127.0.0.1:8081'))

    def test_static_rotate_is_disabled_in_both_tables_and_direct_call_does_nothing(self):
        active = self.add_proxy('127.0.0.1:8080')
        self.add_proxy('127.0.0.1:8081', failed=True)
        self.page._update_metrics_and_render()
        active_actions = self.page.tbl_active.cellWidget(0, 4).findChildren(QPushButton)
        failed_actions = self.page.tbl_failed.cellWidget(0, 3).findChildren(QPushButton)
        self.assertFalse(active_actions[0].isEnabled())
        self.assertFalse(failed_actions[0].isEnabled())
        self.assertTrue(active_actions[1].isEnabled())
        with patch.object(proxy_module, '_ProxyRotateThread') as worker:
            self.page._rotate_single_proxy(active['id'])
        worker.assert_not_called()
        self.assertEqual(active['status'], 'READY')

    def test_static_check_keeps_endpoints_separate_and_never_updates_rotation_state(self):
        first = self.add_proxy('127.0.0.1:8080')
        second = self.add_proxy('socks5://alice:secret@127.0.0.1:1080')
        probe, maintenance, update = self.run_check([first['id'], second['id']])
        self.assertEqual(probe.await_count, 2)
        maintenance.assert_not_called()
        update.assert_not_called()
        self.assertEqual(first['endpoint'], 'http://127.0.0.1:8080')
        self.assertEqual(second['endpoint'], 'socks5://alice:secret@127.0.0.1:1080')
        self.assertEqual((first['status'], second['status']), ('SUCCESS', 'SUCCESS'))

    def test_mixed_check_only_protects_and_updates_the_rotating_key(self):
        static = self.add_proxy('127.0.0.1:8080')
        rotating = self.add_proxy('127.0.0.1:8081|offline-key')
        probe, maintenance, update = self.run_check([static['id'], rotating['id']])
        self.assertEqual(probe.await_count, 2)
        maintenance.assert_called_once_with('offline-key')
        self.assertEqual(update.call_count, 1)
        self.assertEqual(update.call_args.args[0], 'offline-key')


if __name__ == '__main__':
    unittest.main(verbosity=2)
