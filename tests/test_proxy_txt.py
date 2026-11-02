"""Offline file import/save/export regressions for the Proxy Pool GUI.

Run: py -3.14 -I tests/test_proxy_txt.py
"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from core.proxy.models import ProxyModel
from gui.pages.proxy import ProxyPage


MODULE = 'gui.pages.proxy'


class ProxyTxtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        cls.app.setFont(QFont('Segoe UI', 10))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.settings = QSettings(str(self.directory / 'settings.ini'), QSettings.Format.IniFormat)
        self.pages = []
        self.page = self.make_page(self.settings)
        self.addCleanup(self.close_pages)
        self.information = self.start_patch('QMessageBox.information')
        self.warning = self.start_patch('QMessageBox.warning')
        self.check = self.start_patch('ProxyPage._check_proxy_batch')
        self.rotate = self.start_patch('_ProxyRotateThread.start')

    def start_patch(self, suffix):
        patcher = patch(f'{MODULE}.{suffix}')
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def tearDown(self):
        self.check.assert_not_called()
        self.rotate.assert_not_called()

    def make_page(self, settings):
        page = ProxyPage(settings=settings)
        self.pages.append(page)
        return page

    def close_pages(self):
        for page in self.pages:
            page.shutdown_workers()
            page.close()
            page.deleteLater()
        self.app.processEvents()

    def add(self, line, *, failed=False):
        item = self.page._parse_proxy_line(line)
        self.assertIsNotNone(item, line)
        item['is_failed'] = failed
        if failed:
            item['status'] = 'ERROR'
            item['error_reason'] = 'offline test failure'
        self.page._proxies[item['id']] = item
        self.page._save_to_settings()
        return item

    def import_file(self, path, *, page=None):
        target = self.page if page is None else page
        with patch(f'{MODULE}.QFileDialog.getOpenFileName', return_value=(str(path), 'Text (*.txt)')):
            target.btn_import_file.click()

    def choose_export(self, path):
        with patch(f'{MODULE}.QFileDialog.getSaveFileName', return_value=(str(path), 'Text (*.txt)')):
            self.page.btn_export.click()

    def lines(self, path):
        return Path(path).read_text(encoding='utf-8-sig').splitlines()

    def semantics(self, page):
        return {(ProxyModel.from_string(item['endpoint']).to_url(), item['key_xoay'])
            for item in page._proxies.values()}

    def test_import_utf8_bom_crlf_blank_comments_and_each_proxy_line(self):
        source = self.directory / 'danh sách proxy.txt'
        source.write_bytes(('\ufeff# local proxies\r\n\r\n127.0.0.1:21001\r\n'
            'socks5://127.0.0.1:21002|rotate-one\r\n').encode('utf-8'))
        self.import_file(source)
        self.assertEqual(len(self.page._proxies), 2)
        self.assertEqual(Path(self.page._proxy_file_path), source)
        self.assertEqual(Path(self.settings.value('proxy/txt_path')), source)
        self.assertEqual(source.read_bytes()[:3], b'\xef\xbb\xbf')

    def test_import_utf16_bom_text_file(self):
        source = self.directory / 'utf16.txt'
        source.write_text('127.0.0.1:21003\r\n127.0.0.1:21004|rotate-two\r\n', encoding='utf-16')
        self.import_file(source)
        self.assertEqual(len(self.page._proxies), 2)

    def test_import_merges_keeps_existing_and_skips_key_and_url_duplicates(self):
        existing = self.add('127.0.0.1:21005|same-key', failed=True)
        self.add('127.0.0.1:21006')
        source = self.directory / 'merge.txt'
        source.write_text('127.0.0.2:21007|https://proxyxoay.shop/api/get.php?key=same-key\n'
            'http://127.0.0.1:21006\n127.0.0.1:21008\n127.0.0.1:21008\n', encoding='utf-8')
        self.import_file(source)
        self.assertEqual(len(self.page._proxies), 3)
        self.assertIs(self.page._proxies[existing['id']], existing)
        self.assertTrue(existing['is_failed'])
        self.assertEqual(existing['endpoint'], '127.0.0.1:21005')

    def test_import_valid_entries_reports_invalid_line_numbers_without_credentials(self):
        source = self.directory / 'partly valid.txt'
        source.write_text('127.0.0.1:21009\nnot-a-proxy\n127.0.0.1:99999:user:private-password\n', encoding='utf-8')
        self.import_file(source)
        self.assertEqual(len(self.page._proxies), 1)
        messages = ' '.join(str(call) for call in self.information.call_args_list + self.warning.call_args_list)
        self.assertIn('2', messages)
        self.assertIn('3', messages)
        self.assertNotIn('private-password', messages)

    def test_invalid_only_import_does_not_replace_binding_or_membership(self):
        self.add('127.0.0.1:21010')
        original = self.directory / 'original.txt'
        self.page._proxy_file_path = str(original)
        self.settings.setValue('proxy/txt_path', str(original))
        before = dict(self.page._proxies)
        source = self.directory / 'invalid.txt'
        source.write_text('invalid\n127.0.0.1:0\n', encoding='utf-8')
        self.import_file(source)
        self.assertEqual(self.page._proxies, before)
        self.assertEqual(self.page._proxy_file_path, str(original))
        self.assertEqual(self.settings.value('proxy/txt_path'), str(original))

    def test_import_cancel_leaves_list_binding_and_settings_unchanged(self):
        self.add('127.0.0.1:21011')
        before = dict(self.page._proxies)
        before_path = self.page._proxy_file_path
        with patch(f'{MODULE}.QFileDialog.getOpenFileName', return_value=('', '')):
            self.page.btn_import_file.click()
        self.assertEqual(self.page._proxies, before)
        self.assertEqual(self.page._proxy_file_path, before_path)
        self.assertIsNone(self.settings.value('proxy/txt_path'))

    def test_import_missing_file_reports_failure_and_keeps_existing_list(self):
        self.add('127.0.0.1:21012|keep-key')
        before = dict(self.page._proxies)
        self.import_file(self.directory / 'missing.txt')
        self.assertEqual(self.page._proxies, before)
        self.warning.assert_called()
        self.assertIsNone(self.settings.value('proxy/txt_path'))

    def test_export_roundtrip_preserves_static_auth_socks_ipv6_and_rotation_keys(self):
        for line in ('127.0.0.1:21013', '127.0.0.1:21014:offline-user:p@ss:word',
                'socks5://user:p%40ss%3Aword@[2001:db8::1]:21015',
                'https://127.0.0.1:21016|https://proxyxoay.shop/api/get.php?key=rotate-three'):
            self.add(line)
        exported = self.directory / 'roundtrip.txt'
        self.choose_export(exported)
        lines = self.lines(exported)
        self.assertEqual(len(lines), 4)
        other_settings = QSettings(str(self.directory / 'other.ini'), QSettings.Format.IniFormat)
        other = self.make_page(other_settings)
        self.import_file(exported, page=other)
        self.assertEqual(self.semantics(other), self.semantics(self.page))

    def test_export_includes_failed_and_hidden_by_search_without_changing_binding(self):
        self.add('127.0.0.1:21017|active-key')
        self.add('127.0.0.1:21018|failed-key', failed=True)
        original = self.directory / 'source.txt'
        original.write_text('unchanged source\n', encoding='utf-8')
        self.page._proxy_file_path = str(original)
        self.settings.setValue('proxy/txt_path', str(original))
        self.page.input_search.setText('nothing matches')
        exported = self.directory / 'copy.txt'
        self.choose_export(exported)
        self.assertEqual(len(self.lines(exported)), 2)
        self.assertTrue(any('failed-key' in line for line in self.lines(exported)))
        self.assertEqual(self.page._proxy_file_path, str(original))
        self.assertEqual(self.settings.value('proxy/txt_path'), str(original))
        self.assertEqual(original.read_text(encoding='utf-8'), 'unchanged source\n')

    def test_first_save_asks_filename_adds_txt_and_restores_binding_on_reopen(self):
        self.add('127.0.0.1:21019|saved-key')
        chosen = self.directory / 'my proxies'
        with patch(f'{MODULE}.QFileDialog.getSaveFileName', return_value=(str(chosen), 'Text (*.txt)')) as dialog:
            self.page.btn_save_file.click()
        dialog.assert_called_once()
        written = chosen.with_name(chosen.name + '.txt')
        self.assertEqual(self.lines(written), ['127.0.0.1:21019|saved-key'])
        self.assertEqual(Path(self.page._proxy_file_path), written)
        self.settings.sync()
        reopened = self.make_page(QSettings(self.settings.fileName(), QSettings.Format.IniFormat))
        self.assertEqual(Path(reopened._proxy_file_path), written)
        self.assertEqual(len(reopened._proxies), 1)

    def test_save_existing_binding_updates_same_file_without_picker(self):
        source = self.directory / 'bound.txt'
        source.write_text('127.0.0.1:21020\n', encoding='utf-8')
        self.import_file(source)
        self.add('127.0.0.1:21021|new-key', failed=True)
        with patch(f'{MODULE}.QFileDialog.getSaveFileName') as dialog:
            self.page.btn_save_file.click()
        dialog.assert_not_called()
        self.assertEqual(len(self.lines(source)), 2)
        self.assertIn('127.0.0.1:21021|new-key', self.lines(source))

    def test_save_cancel_does_not_create_file_or_bind_path(self):
        self.add('127.0.0.1:21022')
        with patch(f'{MODULE}.QFileDialog.getSaveFileName', return_value=('', '')):
            self.page.btn_save_file.click()
        self.assertFalse(self.page._proxy_file_path)
        self.assertIsNone(self.settings.value('proxy/txt_path'))
        self.assertEqual(list(self.directory.glob('*.txt')), [])

    def test_export_cancel_does_not_create_file_or_change_binding(self):
        self.add('127.0.0.1:21023')
        before_path = self.page._proxy_file_path
        with patch(f'{MODULE}.QFileDialog.getSaveFileName', return_value=('', '')):
            self.page.btn_export.click()
        self.assertEqual(self.page._proxy_file_path, before_path)
        self.assertEqual(list(self.directory.glob('*.txt')), [])

    def test_failed_first_save_does_not_claim_success_or_bind_file(self):
        self.add('127.0.0.1:21024|keep-me')
        target = self.directory / 'cannot write.txt'
        self.information.reset_mock()
        with patch(f'{MODULE}.QFileDialog.getSaveFileName', return_value=(str(target), 'Text (*.txt)')), \
                patch.object(self.page, '_write_proxy_file', side_effect=OSError('offline access denied')):
            self.page.btn_save_file.click()
        self.assertFalse(target.exists())
        self.assertFalse(self.page._proxy_file_path)
        self.assertIsNone(self.settings.value('proxy/txt_path'))
        self.information.assert_not_called()
        self.warning.assert_called()

    def test_failed_export_preserves_existing_file_and_bound_source(self):
        self.add('127.0.0.1:21025')
        target = self.directory / 'existing.txt'
        target.write_text('original bytes\n', encoding='utf-8')
        before_path = self.page._proxy_file_path
        with patch.object(self.page, '_write_proxy_file', side_effect=OSError('offline disk full')):
            self.choose_export(target)
        self.assertEqual(target.read_text(encoding='utf-8'), 'original bytes\n')
        self.assertEqual(self.page._proxy_file_path, before_path)
        self.warning.assert_called()


if __name__ == '__main__':
    unittest.main(verbosity=2)
