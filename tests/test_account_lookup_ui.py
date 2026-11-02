"""Offline Qt regressions for the read-only Mochi account workspace.

Run with: py -3.14 -I tests/test_account_lookup_ui.py
Every lookup is mocked; the real Qt worker and event loop are exercised.
"""
import asyncio
import csv
from dataclasses import replace
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QLineEdit

from core.platforms.mochichat.account_lookup import (
    AccountLookupError, AccountProfile, AccountProfileStore,
)
from core.proxy.models import ProxyModel
from core.proxy.protocol import ProxyReleaseOutcome
from core.telegram_users import LOCAL_USER_ID, TelegramUserStore
from gui.pages import account_lookup as lookup_ui
from gui.pages import mochi_chat as mochi_ui
from gui.pages.telegram import TelegramPage
from gui.telegram_bot import TelegramBotThread

STAMP = '2026-11-02T12:00:00+07:00'
PASSWORD = 'offline-password-do-not-persist'
RAW_SECRET = 'http://proxy-user:private-pass@127.0.0.1:3128/?api_key=private-token'


def profile(**values):
    return replace(AccountProfile('User@example.test', 'uid123', 'Tên hiển thị',
        'ABC123', '123.45', STAMP), **values)


def proxy_client():
    lease = SimpleNamespace(proxy=ProxyModel.from_string('127.0.0.1:29001'))
    return SimpleNamespace(lease=AsyncMock(return_value=lease),
        release=AsyncMock(), release_job=AsyncMock()), lease


class _QtFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.settings = QSettings(str(self.directory / 'settings.ini'), QSettings.IniFormat)
        self.widgets = []
        self.gates = []
        self.lookup = AsyncMock(return_value=profile())
        self.enterContext(patch.object(lookup_ui, 'lookup_account', self.lookup))
        self.job_start = self.enterContext(patch.object(mochi_ui.MochiWorkerThread, 'start'))
        self.bot_start = self.enterContext(patch.object(TelegramBotThread, 'start'))

    def tearDown(self):
        # Release any blocking fake coroutine/direct signal before waiting for QThread.
        for gate in self.gates:
            gate.set()
        for widget in reversed(self.widgets):
            if hasattr(widget, 'shutdown_workers'):
                widget.shutdown_workers()
        self.wait_for(lambda: all(getattr(widget, '_worker', None) is None
            for widget in self.widgets if isinstance(widget, lookup_ui.AccountLookupPage)))
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        self.app.processEvents()
        self.job_start.assert_not_called()
        self.bot_start.assert_not_called()

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail('Qt worker did not reach the expected state before timeout')

    def gate(self):
        value = threading.Event()
        self.gates.append(value)
        return value

    def new_lookup(self, route=None):
        route = Mock(return_value={}) if route is None else route
        page = lookup_ui.AccountLookupPage(self.directory / f'accounts{len(self.widgets)}.sqlite3', route)
        self.widgets.append(page)
        return page

    def new_mochi(self):
        page = mochi_ui.MochiChatPage(settings=self.settings)
        self.widgets.extend((page, page.account_lookup_page))
        return page

    def start(self, page):
        page.input_email.setText('  User@example.test  ')
        page.input_password.setText(PASSWORD)
        page._start_lookup()

    def finish(self, page):
        self.wait_for(lambda: page._worker is None)

    def assert_private_status(self, page):
        text = page.lbl_status.text()
        for secret in (PASSWORD, RAW_SECRET, 'proxy-user', 'private-pass', 'private-token'):
            self.assertNotIn(secret, text)


class AccountLookupUiTests(_QtFixture):
    def test_password_is_masked_and_invalid_input_never_starts_or_allocates_proxy(self):
        route = Mock(return_value={})
        page = self.new_lookup(route)
        self.assertEqual(page.input_password.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(page.lbl_status.textFormat(), Qt.TextFormat.PlainText)
        for email, password in (('', ''), ('user', PASSWORD),
                ('a b@example.test', PASSWORD), ('a@example.test', '')):
            with self.subTest(email=email, has_password=bool(password)):
                page.input_email.setText(email)
                page.input_password.setText(password)
                page._start_lookup()
                self.assertIsNone(page._worker)
                self.assertTrue(page.btn_lookup.isEnabled())
        route.assert_not_called()
        self.lookup.assert_not_awaited()

    def test_success_uses_real_worker_clears_password_and_persists_only_profile(self):
        route = Mock(return_value={'google_via_proxy': False, 'app_via_proxy': False, 'headless': True})
        page = self.new_lookup(route)
        self.start(page)
        self.assertEqual(page.input_password.text(), '')
        self.finish(page)
        self.assertEqual(page._profiles, (profile(),))
        self.assertEqual(AccountProfileStore(page.database_path).list_profiles(), (profile(),))
        self.lookup.assert_awaited_once()
        args, options = self.lookup.await_args
        self.assertEqual(args, ('User@example.test', PASSWORD))
        self.assertTrue(options['owner_id'].startswith('account_lookup_'))
        self.assertEqual(route.call_args.args, (options['owner_id'],))
        self.assertIsNone(options['proxy'])
        self.assertEqual(page.database_path.read_bytes().find(PASSWORD.encode()), -1)
        self.assertNotIn('password', ' '.join(self.settings.allKeys()).lower())
        self.assert_private_status(page)

    def test_route_failure_is_sanitized_and_does_not_start_worker(self):
        page = self.new_lookup(Mock(side_effect=RuntimeError(RAW_SECRET)))
        self.start(page)
        self.assertIsNone(page._worker)
        self.assertEqual(page.table.rowCount(), 0)
        self.lookup.assert_not_awaited()
        self.assert_private_status(page)

    def test_thread_start_failure_restores_controls_without_exposing_exception(self):
        page = self.new_lookup()
        with patch.object(lookup_ui.AsyncTaskThread, 'start', side_effect=RuntimeError(RAW_SECRET)):
            self.start(page)
        self.assertIsNone(page._worker)
        self.assertTrue(page.btn_lookup.isEnabled())
        self.assertFalse(page.btn_cancel.isEnabled())
        self.assert_private_status(page)

    def test_busy_lookup_cannot_start_a_second_worker_and_cancel_releases_lease(self):
        client, lease = proxy_client()
        entered = self.gate()
        async def waiting_lookup(*args, **options):
            entered.set()
            await asyncio.Event().wait()
        self.lookup.side_effect = waiting_lookup
        route = Mock(return_value={'proxy_client': client, 'app_via_proxy': True})
        page = self.new_lookup(route)
        self.start(page)
        self.wait_for(entered.is_set)
        worker = page._worker
        self.assertFalse(page.btn_lookup.isEnabled())
        self.assertFalse(page.input_email.isEnabled())
        self.assertFalse(page.input_password.isEnabled())
        self.assertTrue(page.btn_cancel.isEnabled())
        page._start_lookup()
        self.assertIs(page._worker, worker)
        route.assert_called_once()
        self.lookup.assert_awaited_once()
        page._cancel_lookup()
        self.assertFalse(page.btn_cancel.isEnabled())
        self.finish(page)
        client.release.assert_awaited_once_with(lease, outcome=ProxyReleaseOutcome.ABORTED, success=False)
        self.assertTrue(page.btn_lookup.isEnabled())
        self.assertEqual(page.table.rowCount(), 0)
        self.assertIn('Đã hủy', page.lbl_status.text())

    def test_result_does_not_clear_busy_until_actual_qthread_finished(self):
        allow_lookup, inside_result, allow_finish = self.gate(), self.gate(), self.gate()
        async def controlled_lookup(*args, **options):
            while not allow_lookup.is_set():
                await asyncio.sleep(0.005)
            return profile()
        self.lookup.side_effect = controlled_lookup
        page = self.new_lookup()
        self.start(page)
        worker = page._worker
        def hold_result(_result):
            inside_result.set()
            allow_finish.wait(3)
        worker.result.connect(hold_result, Qt.ConnectionType.DirectConnection)
        allow_lookup.set()
        self.wait_for(lambda: inside_result.is_set() and page.table.rowCount() == 1)
        self.assertTrue(worker.isRunning())
        self.assertIs(page._worker, worker)
        self.assertFalse(page.btn_lookup.isEnabled())
        allow_finish.set()
        self.finish(page)
        self.assertTrue(page.btn_lookup.isEnabled())

    def test_success_releases_proxy_with_success_outcome(self):
        client, lease = proxy_client()
        page = self.new_lookup(Mock(return_value={'proxy_client': client, 'google_via_proxy': True}))
        self.start(page)
        self.finish(page)
        client.release.assert_awaited_once_with(lease, outcome=ProxyReleaseOutcome.SUCCESS, success=True)
        client.release_job.assert_not_awaited()
        self.assertIs(self.lookup.await_args.kwargs['proxy'], lease.proxy)
        self.assertEqual(page._profiles, (profile(),))

    def test_business_error_retains_good_rows_and_releases_without_network_failure(self):
        client, lease = proxy_client()
        page = self.new_lookup(Mock(return_value={'proxy_client': client}))
        previous = profile(email='old@example.test', invite_code='OLD123')
        page._on_result(previous)
        self.lookup.side_effect = AccountLookupError('Tài khoản không hợp lệ.')
        self.start(page)
        self.finish(page)
        client.release.assert_awaited_once_with(lease,
            outcome=ProxyReleaseOutcome.BUSINESS_FAILURE, success=False)
        self.assertEqual(page._profiles, (previous,))
        self.assertEqual(page.table.item(0, 2).text(), 'OLD123')
        self.assertEqual(page.lbl_status.text(), 'Tài khoản không hợp lệ.')

    def test_transport_error_marks_network_release(self):
        client, lease = proxy_client()
        self.lookup.side_effect = AccountLookupError('Không kết nối được Mochi.', transport_error=True)
        page = self.new_lookup(Mock(return_value={'proxy_client': client}))
        self.start(page)
        self.finish(page)
        client.release.assert_awaited_once_with(lease,
            outcome=ProxyReleaseOutcome.NETWORK_ERROR, success=False)
        self.assertEqual(page._profiles, ())

    def test_unexpected_lookup_error_is_sanitized(self):
        self.lookup.side_effect = RuntimeError(RAW_SECRET)
        page = self.new_lookup()
        self.start(page)
        self.finish(page)
        self.assertEqual(page._profiles, ())
        self.assert_private_status(page)

    def test_lease_error_is_sanitized_and_does_not_release_nonexistent_lease(self):
        client, _lease = proxy_client()
        client.lease.side_effect = RuntimeError(RAW_SECRET)
        page = self.new_lookup(Mock(return_value={'proxy_client': client}))
        self.start(page)
        self.finish(page)
        self.lookup.assert_not_awaited()
        client.release.assert_not_awaited()
        self.assert_private_status(page)

    def test_release_failure_uses_job_fallback_and_keeps_success(self):
        client, _lease = proxy_client()
        client.release.side_effect = RuntimeError(RAW_SECRET)
        page = self.new_lookup(Mock(return_value={'proxy_client': client}))
        self.start(page)
        self.finish(page)
        client.release_job.assert_awaited_once_with()
        self.assertEqual(page.store.list_profiles(), (profile(),))
        self.assertEqual(page._profiles, (profile(),))
        self.assert_private_status(page)

    def test_both_release_failures_show_safe_warning_without_losing_success(self):
        client, _lease = proxy_client()
        client.release.side_effect = RuntimeError(RAW_SECRET)
        client.release_job.side_effect = RuntimeError(RAW_SECRET)
        page = self.new_lookup(Mock(return_value={'proxy_client': client}))
        self.start(page)
        self.finish(page)
        self.assertEqual(page._profiles, (profile(),))
        self.assertEqual(page.store.list_profiles(), (profile(),))
        self.assertIn('Chưa xác nhận trả được proxy', page.lbl_status.text())
        self.assert_private_status(page)

    def test_cancellation_also_uses_sanitized_release_fallback(self):
        client, lease = proxy_client()
        client.release.side_effect = RuntimeError(RAW_SECRET)
        client.release_job.side_effect = RuntimeError(RAW_SECRET)
        entered = self.gate()
        async def waiting_lookup(*args, **options):
            entered.set()
            await asyncio.Event().wait()
        self.lookup.side_effect = waiting_lookup
        page = self.new_lookup(Mock(return_value={'proxy_client': client}))
        self.start(page)
        self.wait_for(entered.is_set)
        page._cancel_lookup()
        self.finish(page)
        client.release.assert_awaited_once_with(lease, outcome=ProxyReleaseOutcome.ABORTED, success=False)
        client.release_job.assert_awaited_once_with()
        self.assertIn('Đã hủy', page.lbl_status.text())
        self.assertIn('Chưa xác nhận trả được proxy', page.lbl_status.text())
        self.assert_private_status(page)

    def test_table_copy_export_and_reopen_keep_exact_profile_values(self):
        page = self.new_lookup()
        page._on_result(profile())
        self.assertEqual([page.table.item(0, column).text() for column in range(6)],
            ['User@example.test', 'Tên hiển thị', 'ABC123', '123.45', 'uid123', '02/11/2026 12:00'])
        self.assertTrue(page.btn_copy.isEnabled())
        page.btn_copy.click()
        self.assertEqual(self.app.clipboard().text(), 'ABC123')
        output = self.directory / 'accounts.csv'
        with patch.object(lookup_ui.QFileDialog, 'getSaveFileName', return_value=(str(output), 'CSV (*.csv)')):
            page.btn_export.click()
        with output.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows, [dict(email='User@example.test', user_id='uid123',
            display_name='Tên hiển thị', invite_code='ABC123', beans='123.45', updated_at=STAMP)])
        reloaded = lookup_ui.AccountLookupPage(page.database_path, Mock(return_value={}))
        self.widgets.append(reloaded)
        self.assertEqual(reloaded._profiles, (profile(),))
        self.assertEqual(reloaded.input_password.text(), '')

    def test_refresh_preserves_failed_save_overlay_and_retries_it(self):
        page = self.new_lookup()
        old = profile(beans='10', updated_at='2026-11-02T11:00:00+07:00')
        page._on_result(old)
        latest = profile(email='user@EXAMPLE.test', beans='0', invite_code='NEW123')
        with patch.object(page.store, 'upsert', side_effect=OSError(RAW_SECRET)):
            page._on_result(latest)
            page._refresh_profiles()
            self.assertEqual(page._profiles, (latest,))
            self.assertEqual(page.table.item(0, 3).text(), '0')
            self.assertTrue(page.btn_export.isEnabled())
            self.assertTrue(page._pending_profiles)
            self.assert_private_status(page)
        page._refresh_profiles()
        self.assertEqual(page._profiles, (latest,))
        self.assertEqual(page.store.list_profiles(), (latest,))
        self.assertEqual(page._pending_profiles, {})

    def test_failed_database_initialization_still_allows_standalone_export(self):
        with patch.object(lookup_ui, 'AccountProfileStore', side_effect=OSError(RAW_SECRET)):
            page = self.new_lookup()
        self.assertIsNone(page.store)
        page._on_result(profile())
        self.assertTrue(page.btn_export.isEnabled())
        output = self.directory / 'memory-only.csv'
        with patch.object(lookup_ui.QFileDialog, 'getSaveFileName', return_value=(str(output), 'CSV (*.csv)')):
            page._export_csv()
        self.assertIn('ABC123', output.read_text(encoding='utf-8-sig'))
        self.assert_private_status(page)
        page._refresh_profiles()
        self.assertEqual(page.store.list_profiles(), (profile(),))
        self.assertFalse(page._pending_profiles)

    def test_read_failure_retains_visible_profile_and_export_failure_is_sanitized(self):
        page = self.new_lookup()
        page._on_result(profile())
        with patch.object(page.store, 'list_profiles', side_effect=OSError(RAW_SECRET)):
            page._refresh_profiles()
        self.assertEqual(page._profiles, (profile(),))
        self.assertEqual(page.table.rowCount(), 1)
        output = self.directory / 'blocked.csv'
        with patch.object(lookup_ui.QFileDialog, 'getSaveFileName', return_value=(str(output), 'CSV (*.csv)')), \
                patch.object(lookup_ui, 'export_profiles_csv', side_effect=OSError(RAW_SECRET)):
            page._export_csv()
        self.assert_private_status(page)
        self.assertEqual(page._profiles, (profile(),))

    def test_invalid_worker_result_keeps_previous_profile(self):
        page = self.new_lookup()
        page._on_result(profile())
        self.lookup.return_value = {'token': 'private-token'}
        self.start(page)
        self.finish(page)
        self.assertEqual(page._profiles, (profile(),))
        self.assert_private_status(page)


class AccountLookupWorkspaceTests(_QtFixture):
    def test_new_workspace_tab_keeps_existing_job_tabs_and_inventory(self):
        page = self.new_mochi()
        self.assertEqual([page.workspace_tabs.tabText(index) for index in range(3)],
            ['Nhiệm vụ', 'Kho tài khoản', 'Lấy mã mời'])
        self.assertEqual(page.workspace_stack.count(), 3)
        self.assertIs(page.workspace_stack.widget(1), page.inventory_page)
        page.workspace_tabs.setCurrentIndex(2)
        self.assertIs(page.workspace_stack.currentWidget(), page.account_lookup_page)
        self.assertEqual(page.job_tabs.count(), 4)
        self.assertEqual(page.job_tabs.tabText(2), 'Log job')
        self.assertEqual(page.job_tabs.tabText(3), 'Lịch sử job')

    def test_master_proxy_off_overrides_google_app_mail_and_redeem_flags(self):
        page = self.new_mochi()
        page.chk_use_proxy.setChecked(False)
        for entry in (page.chk_google_via_proxy, page.chk_app_login_via_proxy,
                page.chk_mail_via_proxy, page.chk_redeem_via_proxy):
            entry.setChecked(True)
        with patch.object(mochi_ui, 'ProxyBroker') as broker, \
                patch.object(mochi_ui, 'make_rotating_provider') as provider:
            route = page._account_lookup_route('lookup-direct')
        self.assertIsNone(route['proxy_client'])
        self.assertFalse(route['google_via_proxy'])
        self.assertFalse(route['app_via_proxy'])
        broker.assert_not_called()
        provider.assert_not_called()

    def test_mail_and_redeem_only_do_not_allocate_lookup_proxy(self):
        page = self.new_mochi()
        page.chk_use_proxy.setChecked(True)
        page.chk_google_via_proxy.setChecked(False)
        page.chk_app_login_via_proxy.setChecked(False)
        page.chk_mail_via_proxy.setChecked(True)
        page.chk_redeem_via_proxy.setChecked(True)
        with patch.object(mochi_ui, 'ProxyBroker') as broker:
            route = page._account_lookup_route('lookup-direct')
        self.assertIsNone(route['proxy_client'])
        broker.assert_not_called()

    def test_google_and_app_routes_use_shared_pool_controls_independently(self):
        page = self.new_mochi()
        page.chk_use_proxy.setChecked(True)
        page.radio_proxy_tab.setChecked(True)
        page.spin_proxy_account_concurrency.setValue(3)
        page.chk_headless.setChecked(False)
        endpoints = [('127.0.0.1:29001', 'offline-rotation-key')]
        for google, app in ((True, False), (False, True), (True, True)):
            with self.subTest(google=google, app=app):
                page.chk_google_via_proxy.setChecked(google)
                page.chk_app_login_via_proxy.setChecked(app)
                with patch.object(page, '_active_proxy_entries', return_value=endpoints), \
                        patch.object(mochi_ui, 'ProxyBroker') as broker, \
                        patch.object(mochi_ui, 'ProxyPoolClient') as client:
                    route = page._account_lookup_route('lookup-pool')
                self.assertIs(route['proxy_client'], client.return_value)
                self.assertEqual((route['google_via_proxy'], route['app_via_proxy'], route['headless']),
                    (google, app, False))
                options = broker.call_args.kwargs
                self.assertEqual(options['account_concurrency'], 3)
                self.assertFalse(options['allow_direct_without_proxies'])
                self.assertEqual(len(options['static_proxies']), 1)
                self.assertEqual(options['static_proxies'][0].rotation_url, 'offline-rotation-key')
                client.assert_called_once_with(job_id='lookup-pool', platform_id='mochichat',
                    broker=broker.return_value)

    def test_key_provider_route_uses_shared_key_without_calling_provider_network(self):
        page = self.new_mochi()
        page.chk_use_proxy.setChecked(True)
        page.chk_google_via_proxy.setChecked(False)
        page.chk_app_login_via_proxy.setChecked(True)
        page.radio_proxy_key.setChecked(True)
        page.input_proxy_vn_key.setText('  offline-key  ')
        with patch.object(mochi_ui, 'make_rotating_provider') as provider, \
                patch.object(mochi_ui, 'ProxyBroker') as broker, \
                patch.object(mochi_ui, 'ProxyPoolClient'):
            route = page._account_lookup_route('lookup-key')
        provider.assert_called_once_with('offline-key', nhamang=page.combo_proxy_isp.currentData())
        self.assertIs(broker.call_args.kwargs['rotating_provider'], provider.return_value)
        self.assertFalse(route['google_via_proxy'])
        self.assertTrue(route['app_via_proxy'])

    def test_selected_proxy_route_without_pool_or_key_blocks_before_lookup(self):
        page = self.new_mochi()
        page.chk_use_proxy.setChecked(True)
        page.chk_google_via_proxy.setChecked(True)
        page.radio_proxy_tab.setChecked(True)
        with patch.object(page, '_active_proxy_entries', return_value=[]):
            self.start(page.account_lookup_page)
        self.assertIsNone(page.account_lookup_page._worker)
        page.radio_proxy_key.setChecked(True)
        page.input_proxy_vn_key.clear()
        self.start(page.account_lookup_page)
        self.assertIsNone(page.account_lookup_page._worker)
        self.lookup.assert_not_awaited()

    def test_lookup_needs_no_mail_api_and_never_creates_job_finance_or_bot_records(self):
        page = self.new_mochi()
        page.chk_use_proxy.setChecked(False)
        page.input_api_key.clear()
        store = TelegramUserStore(self.directory / 'telegram.sqlite3')
        telegram = TelegramPage(self.settings, page, user_store=store)
        self.widgets.append(telegram)
        with patch.object(store, 'record_local_job') as record, \
                patch.object(store, 'record_purchase') as purchase:
            self.start(page.account_lookup_page)
            self.finish(page.account_lookup_page)
        record.assert_not_called()
        purchase.assert_not_called()
        self.assertEqual(store.list_jobs(LOCAL_USER_ID), ())
        self.assertEqual(page._workers, {})
        self.assertEqual(page._session_jobs, {})
        self.assertEqual(page.job_history_store.read(), ())
        local = store.get_user(LOCAL_USER_ID)
        self.assertEqual((local.sold_beans, local.revenue_units, local.cost_units), (0, 0, 0))
        page.btn_settings_save.click()
        self.settings.sync()
        self.assertNotIn(PASSWORD, (self.directory / 'settings.ini').read_text(encoding='utf-8'))
        self.assertEqual(page.account_lookup_page._profiles, (profile(),))

    def test_mochi_shutdown_cancels_lookup_and_waits_for_its_actual_finish(self):
        page = self.new_mochi()
        page.chk_use_proxy.setChecked(False)
        entered, cleaned = self.gate(), self.gate()
        async def waiting_lookup(*args, **options):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.03)
                cleaned.set()
        self.lookup.side_effect = waiting_lookup
        self.start(page.account_lookup_page)
        self.wait_for(entered.is_set)
        self.assertTrue(page.shutdown_workers())
        self.assertTrue(page.account_lookup_page._closing)
        self.assertFalse(page.account_lookup_page.btn_lookup.isEnabled())
        self.finish(page.account_lookup_page)
        self.assertTrue(cleaned.is_set())
        self.assertFalse(page.shutdown_workers())
        self.assertEqual(page.account_lookup_page._profiles, ())


if __name__ == '__main__':
    unittest.main()
