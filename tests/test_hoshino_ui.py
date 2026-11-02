"""Offline regressions for the hoshino sidebar, shared settings, and artwork.

Run with: py -3.14 -I tests/test_hoshino_ui.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import importlib.util
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPixmap
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton

from core.auth.google import resolve_google_login_via_proxy
from core.browser.manager import resolve_browser_headless
from core.config.runtime_config import MochiChatConfig
from gui import branding
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread, _MochiJobSessionRecord
from gui.pages.proxy import ProxyPage
from gui.telegram_bot import TelegramBotThread


class HoshinoUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        cls.app.setFont(QFont('Segoe UI', 10))
        spec = importlib.util.spec_from_file_location('hoshino_main_offline', ROOT / 'main_mochi_gui.py')
        cls.main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.main)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.settings_path = self.directory / 'settings.ini'
        self.settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        self.windows = []
        self.extra_pages = []
        self.job_start_patch = patch.object(MochiWorkerThread, 'start')
        self.job_start = self.job_start_patch.start()
        self.addCleanup(self.job_start_patch.stop)
        self.bot_start_patch = patch.object(TelegramBotThread, 'start')
        self.bot_start = self.bot_start_patch.start()
        self.addCleanup(self.bot_start_patch.stop)
        self.addCleanup(self._close_windows)
        self.window = self.new_window()
        self.mochi = self.window.mochi_page

    def _close_windows(self):
        for page in self.extra_pages:
            page.close()
            page.deleteLater()
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.app.processEvents()

    def tearDown(self):
        self.job_start.assert_not_called()
        self.bot_start.assert_not_called()

    def new_window(self, settings=None):
        settings = self.settings if settings is None else settings
        with patch.object(self.main, 'QSettings', return_value=settings):
            window = self.main.MochiMainWindow()
        self.windows.append(window)
        return window

    def new_mochi_page(self):
        self.settings.sync()
        settings = QSettings(str(self.settings_path), QSettings.Format.IniFormat)
        page = MochiChatPage(settings=settings)
        self.extra_pages.append(page)
        return page

    def add_proxy_entries(self, count):
        page = self.window.proxy_page
        for index in range(count):
            item = page._parse_proxy_line(f'127.0.0.1:{29000 + index}|offline-rotation-key-{index}')
            self.assertIsNotNone(item)
            page._proxies[item['id']] = item
        page._save_to_settings()

    def add_session_job(self, job_id, *, status='Đang chạy', successes=0):
        record = _MochiJobSessionRecord(job_id, 'ABC123', 10,
            customer_name=f'Khách {job_id}', status=status)
        record.success_count = successes
        self.mochi._session_jobs[job_id] = record
        return record

    def test_sidebar_has_six_requested_pages_and_hoshino_brand(self):
        self.assertEqual(self.window.windowTitle(), 'hoshino-tool')
        self.assertEqual(self.window.sidebar.width(), 220)
        self.assertEqual(self.window.stacked.count(), 6)
        buttons = [button for button in self.window.sidebar.findChildren(QPushButton)
            if button.property('nav')]
        self.assertEqual([button.text() for button in buttons],
            ['Dashboard', 'Mochi Chat', 'Proxy Pool','Proxy mua mail', 'Telegram', 'Cài đặt chung'])
        self.assertFalse(any('miraimind' in button.text().casefold()
            or 'license' in button.text().casefold() for button in buttons))

    def test_navigation_selects_matching_page_and_only_one_sidebar_button(self):
        pages = ((self.window.btn_tab_dashboard, self.window.dashboard_page),
            (self.window.btn_tab_mochi, self.mochi),
            (self.window.btn_tab_proxy, self.window.proxy_page),
            (self.window.btn_tab_mail_proxy,self.window.mail_proxy_page),
            (self.window.btn_tab_telegram, self.window.telegram_page),
            (self.window.btn_tab_settings, self.mochi.settings_page))
        for button, page in pages:
            with self.subTest(page=button.text()):
                button.click()
                self.assertIs(self.window.stacked.currentWidget(), page)
                self.assertTrue(button.isChecked())
                self.assertEqual(sum(nav.isChecked() for nav in self.window._nav_buttons.values()), 1)

    def test_existing_proxy_page_instance_and_public_navigation_are_retained(self):
        proxy = self.window.proxy_page
        self.assertIsInstance(proxy, ProxyPage)
        self.assertIs(self.window.stacked.widget(1), proxy)
        self.assertIs(self.mochi.proxy_page, proxy)
        self.window.switch_to_settings_tab()
        self.mochi._switch_to_proxy_tab()
        self.assertIs(self.window.stacked.currentWidget(), proxy)
        self.assertEqual(proxy.get_active_proxies(), [])

    def test_settings_page_contains_the_original_shared_controls(self):
        page = self.mochi.settings_page
        self.assertIs(self.window.stacked.widget(4), page)
        for widget in (self.mochi.input_api_key, self.mochi.combo_provider,
                self.mochi.input_pids, self.mochi.spin_concurrency,
                self.mochi.chk_use_proxy, self.mochi.chk_google_via_proxy,
                self.mochi.spin_proxy_account_concurrency):
            with self.subTest(widget=type(widget).__name__):
                self.assertTrue(page.isAncestorOf(widget))
                self.assertFalse(self.mochi.isAncestorOf(widget))
        self.assertEqual(sum(widget is self.mochi.input_api_key
            for widget in page.findChildren(QLineEdit)), 1)
        self.assertEqual([self.mochi.settings_tabs.tabText(index)
            for index in range(self.mochi.settings_tabs.count())], ['Hạ tầng chung', 'Mochi Chat'])
        self.assertEqual(self.mochi.input_api_key.echoMode(), QLineEdit.EchoMode.Password)

    def test_saved_workspace_settings_survive_reload_without_starting_a_job(self):
        page = self.mochi
        page.spin_concurrency.setValue(3)
        page.combo_provider.setCurrentIndex(page.combo_provider.findData('1trieumail_com'))
        page.input_api_key.setText('offline-1trieu-key')
        page.input_pids.setText('101, 202')
        page.chk_headless.setChecked(False)
        page.chk_use_proxy.setChecked(True)
        page.radio_proxy_key.setChecked(True)
        page.input_proxy_vn_key.setText('offline-proxy-key')
        page.spin_proxy_account_concurrency.setValue(2)
        page.combo_proxy_isp.setCurrentIndex(page.combo_proxy_isp.findData('fpt'))
        page.chk_mail_via_proxy.setChecked(False)
        page.chk_google_via_proxy.setChecked(False)
        page.chk_app_login_via_proxy.setChecked(True)
        page.chk_redeem_via_proxy.setChecked(False)
        page.btn_settings_save.click()

        reloaded = self.new_mochi_page()
        config = MochiChatConfig.from_settings(reloaded.settings)
        self.assertEqual((config.mail_provider, config.mail_provider_api_key,
            config.mail_product_ids), ('1trieumail_com', 'offline-1trieu-key', '101, 202'))
        self.assertEqual(config.account_concurrency, 3)
        self.assertEqual(config.proxy_account_concurrency, 2)
        self.assertEqual((config.mail_via_proxy, config.app_login_via_proxy,
            config.redeem_via_proxy), (False, True, False))
        self.assertFalse(resolve_browser_headless(settings=reloaded.settings))
        self.assertFalse(resolve_google_login_via_proxy(reloaded.settings))
        self.assertTrue(reloaded.chk_use_proxy.isChecked())
        self.assertTrue(reloaded.radio_proxy_key.isChecked())
        self.assertEqual(reloaded.input_proxy_vn_key.text(), 'offline-proxy-key')
        self.assertEqual(reloaded.combo_proxy_isp.currentData(), 'fpt')
        self.assertFalse(page._workers)

    def test_provider_switch_preserves_each_provider_key_and_product_ids(self):
        page = self.mochi
        page.input_api_key.setText('offline-gmailvip-key')
        page.input_pids.setText('111')
        page.combo_provider.setCurrentIndex(page.combo_provider.findData('1trieumail_com'))
        page.input_api_key.setText('offline-1trieu-key')
        page.input_pids.setText('222')
        page.btn_settings_save.click()
        page.combo_provider.setCurrentIndex(page.combo_provider.findData('gmailvip_vn'))
        self.assertEqual((page.input_api_key.text(), page.input_pids.text()),
            ('offline-gmailvip-key', '111'))
        page.combo_provider.setCurrentIndex(page.combo_provider.findData('1trieumail_com'))
        self.assertEqual((page.input_api_key.text(), page.input_pids.text()),
            ('offline-1trieu-key', '222'))

    def test_job_draft_restores_all_fields_without_starting_or_buying(self):
        page = self.mochi
        page.input_code.setText('ABC123')
        page.input_target_points.setText('700')
        page.input_customer.setText('Khách thử nghiệm')
        page.input_total_vnd.setText('250000')
        page.btn_save_draft.click()
        reloaded = self.new_mochi_page()
        self.assertEqual((reloaded.input_code.text(), reloaded.input_target_points.text(),
            reloaded.input_customer.text(), reloaded.input_total_vnd.text()),
            ('ABC123', '700', 'Khách thử nghiệm', '250000'))
        self.assertFalse(reloaded._workers)
        self.assertFalse(reloaded._session_jobs)

    def test_job_and_inventory_tabs_switch_to_their_existing_widgets(self):
        for index in range(3):
            self.mochi.job_tabs.setCurrentIndex(index)
            self.assertEqual(self.mochi.job_stack.currentIndex(), index)
        self.mochi.workspace_tabs.setCurrentIndex(1)
        self.assertIs(self.mochi.workspace_stack.currentWidget(), self.mochi.inventory_page)
        self.assertTrue(self.mochi.inventory_page.isAncestorOf(self.mochi.chk_use_mail_batch))
        self.assertTrue(self.mochi.inventory_page.isAncestorOf(self.mochi.input_mail_batch))

    def test_open_job_logs_selects_the_requested_job_and_log_tab(self):
        self.add_session_job('job_first')
        self.add_session_job('job_second')
        self.mochi._append_log_line('Only the first job', job_id='job_first')
        self.mochi._append_log_line('Only the second job', job_id='job_second')
        self.mochi.workspace_tabs.setCurrentIndex(1)
        self.mochi.show_job_logs('job_first')
        self.assertEqual(self.mochi.workspace_tabs.currentIndex(), 0)
        self.assertEqual(self.mochi.job_tabs.currentIndex(), 2)
        self.assertEqual(self.mochi.job_stack.currentIndex(), 2)
        self.assertEqual(self.mochi._selected_job_id, 'job_first')
        self.assertIn('Only the first job', self.mochi.txt_log.toPlainText())
        self.assertNotIn('Only the second job', self.mochi.txt_log.toPlainText())

    def test_running_table_log_action_opens_the_log_tab(self):
        self.add_session_job('job_first')
        self.mochi._append_log_line('Requested job log', job_id='job_first')
        self.mochi._render_jobs_table()
        self.mochi.job_tabs.setCurrentIndex(1)
        actions = self.mochi.table_jobs.cellWidget(0, 7).findChildren(QPushButton)
        next(button for button in actions if button.text() == 'Log').click()
        self.assertEqual(self.mochi._selected_job_id, 'job_first')
        self.assertEqual(self.mochi.job_tabs.currentIndex(), 2)
        self.assertIn('Requested job log', self.mochi.txt_log.toPlainText())

    def test_five_proxies_keep_five_slots_and_manual_concurrency_after_settings_move(self):
        self.add_proxy_entries(5)
        page = self.mochi
        page.chk_use_proxy.setChecked(False)
        page.spin_concurrency.setValue(1)
        page.radio_proxy_tab.setChecked(True)
        page.spin_proxy_account_concurrency.setValue(1)
        page.chk_app_login_via_proxy.setChecked(True)
        page.chk_use_proxy.setChecked(True)
        self.assertEqual(page.spin_concurrency.value(), 5)
        self.assertFalse(page.spin_concurrency.isEnabled())
        page.btn_settings_save.click()
        self.assertEqual(MochiChatConfig.from_settings(self.settings).account_concurrency, 1)
        page.spin_proxy_account_concurrency.setValue(2)
        self.assertEqual(page.spin_concurrency.value(), 10)
        page.chk_use_proxy.setChecked(False)
        self.assertTrue(page.spin_concurrency.isEnabled())
        self.assertEqual(page.spin_concurrency.value(), 1)

    def test_dashboard_reports_actual_partial_success_and_authorized_user_counts(self):
        self.add_session_job('completed', status='Hoàn thành', successes=10)
        self.add_session_job('failed', status='Thất bại', successes=2)
        self.add_session_job('cancelled', status='Đã hủy', successes=1)
        self.add_session_job('running', successes=3)
        self.add_proxy_entries(5)
        store = self.window.telegram_page.store
        store.upsert_user(123, 'Đối tác A')
        store.upsert_user(456, 'Đối tác B', active=False)
        dashboard = self.window.dashboard_page
        dashboard.refresh()
        self.assertEqual((dashboard.lbl_total_jobs.text(), dashboard.lbl_running_jobs.text(),
            dashboard.lbl_completed_jobs.text(), dashboard.lbl_failed_jobs.text()), ('4', '1', '1', '1'))
        self.assertEqual(dashboard.lbl_successful_beans.text(), '1,600')
        self.assertEqual(dashboard.lbl_active_proxies.text(), '5')
        self.assertTrue(dashboard.lbl_telegram_users.text().startswith('1 '))
        self.assertEqual(dashboard.table_recent_jobs.rowCount(), 4)

    def test_dashboard_shortcuts_and_recent_job_open_real_pages(self):
        self.add_session_job('job_dashboard')
        self.mochi._append_log_line('Dashboard target log', job_id='job_dashboard')
        dashboard = self.window.dashboard_page
        dashboard.refresh()
        for button, expected in ((dashboard.btn_proxy, self.window.proxy_page),
                (dashboard.btn_telegram, self.window.telegram_page),
                (dashboard.btn_settings, self.mochi.settings_page),
                (dashboard.btn_new_job, self.mochi)):
            button.click()
            self.assertIs(self.window.stacked.currentWidget(), expected)
        dashboard._open_job(0, 0)
        self.assertIs(self.window.stacked.currentWidget(), self.mochi)
        self.assertEqual(self.mochi.job_tabs.currentIndex(), 2)
        self.assertIn('Dashboard target log', self.mochi.txt_log.toPlainText())

    def test_dashboard_new_job_opens_setup_without_clearing_the_draft(self):
        page = self.mochi
        page.input_code.setText('DRAFT1')
        page.input_target_points.setText('700')
        page.input_customer.setText('Khách đang soạn')
        page.input_total_vnd.setText('123000')
        page.workspace_tabs.setCurrentIndex(1)
        page.job_tabs.setCurrentIndex(2)
        self.window.switch_to_dashboard_tab()
        self.window.dashboard_page.btn_new_job.click()
        self.assertIs(self.window.stacked.currentWidget(), page)
        self.assertEqual(page.workspace_tabs.currentIndex(), 0)
        self.assertEqual(page.job_tabs.currentIndex(), 0)
        self.assertEqual(page.job_stack.currentIndex(), 0)
        self.assertEqual((page.input_code.text(), page.input_target_points.text(),
            page.input_customer.text(), page.input_total_vnd.text()),
            ('DRAFT1', '700', 'Khách đang soạn', '123000'))
        self.assertFalse(page._workers)

    def test_branding_missing_artwork_uses_fallbacks_from_one_canonical_folder(self):
        self.assertEqual(branding.ASSET_DIR, ROOT / 'gui' / 'assets' / 'branding')
        with patch.object(branding, 'ASSET_DIR', self.directory / 'missing'):
            self.assertEqual(branding.asset_path('logo'), '')
            self.assertEqual(branding.asset_path('dashboard_banner'), '')
            self.assertTrue(branding.icon('logo').isNull())
            self.assertFalse(branding.icon('nav_dashboard', 'nav_dashboard.svg').isNull())

    def test_custom_artwork_is_resolved_and_loaded_without_required_assets(self):
        custom = self.directory / 'custom-artwork'
        custom.mkdir()
        artwork = QPixmap(16, 16)
        artwork.fill(QColor('#88aaff'))
        for filename in ('logo.png', 'dashboard-banner.png', 'sidebar-background.png'):
            self.assertTrue(artwork.save(str(custom / filename)))
        (custom / 'nav-telegram.svg').write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'
            '<circle cx="8" cy="8" r="6" fill="blue"/></svg>', encoding='utf-8')
        with patch.object(branding, 'ASSET_DIR', custom):
            self.assertEqual(branding.asset_path('logo'), str(custom / 'logo.png'))
            self.assertEqual(branding.asset_path('app_icon'), str(custom / 'logo.png'))
            self.assertEqual(branding.asset_path('dashboard_banner'), str(custom / 'dashboard-banner.png'))
            self.assertFalse(branding.icon('nav_telegram').isNull())
            sidebar = branding.BrandSidebar()
            self.extra_pages.append(sidebar)
            self.assertFalse(sidebar._artwork.isNull())
            self.window.dashboard_page._load_banner_image()
            banner = self.window.dashboard_page.banner_frame
            self.assertFalse(banner._artwork.isNull())
            self.assertEqual(banner.minimumHeight(), 120)
            banner.resize(900, 120)
            rendered = banner.grab().toImage()
            self.assertFalse(rendered.isNull())
            self.assertLess(rendered.pixelColor(880, 105).lightness(), QColor('#88aaff').lightness())

    def test_image_folder_button_opens_the_documented_folder_with_os_call_mocked(self):
        custom = self.directory / 'new-artwork-directory'
        with patch.object(self.main, 'ASSET_DIR', custom), \
                patch.object(self.main.QDesktopServices, 'openUrl', return_value=True) as open_url:
            self.window.btn_assets.click()
        self.assertTrue(custom.is_dir())
        open_url.assert_called_once()
        url = open_url.call_args.args[0]
        self.assertTrue(url.isLocalFile())
        self.assertEqual(Path(url.toLocalFile()), custom)


if __name__ == '__main__':
    unittest.main(verbosity=2)
