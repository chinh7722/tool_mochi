"""Local ownership, SQLite migration and desktop-to-accounting integration."""
from contextlib import closing
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import QItemSelectionModel, QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QTableWidget
from core.finance import MailPurchaseReceipt, money_units
from core.job_events import JobProgressEvent
from core.platforms.mochichat.runners.invite_referral import MochiInviteJobSummary
from core.telegram_bot import TelegramAccess, TelegramJobRequest
from core.telegram_users import LOCAL_USER_ID, TelegramUserStore
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread
from gui.pages.telegram import TelegramPage


class LocalStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'users.sqlite3'

    def legacy(self, orphan=False):
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript('''
                CREATE TABLE users(chat_id INTEGER PRIMARY KEY CHECK(chat_id > 0),
                    name TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE jobs(job_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES users(chat_id),
                    code TEXT NOT NULL, requested_beans INTEGER NOT NULL, target_count INTEGER NOT NULL,
                    bot_fingerprint TEXT NOT NULL, update_id INTEGER, status TEXT NOT NULL DEFAULT 'running',
                    success_count INTEGER NOT NULL DEFAULT 0, attempted_count INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finalized_at TEXT,
                    sale_rate_units INTEGER, finance_tracked INTEGER NOT NULL,
                    UNIQUE(bot_fingerprint, update_id));
                CREATE TABLE mail_purchases(purchase_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    provider TEXT NOT NULL, account_hash TEXT NOT NULL, product_id INTEGER NOT NULL,
                    quantity INTEGER NOT NULL, cost_units INTEGER, order_id TEXT NOT NULL, source TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                INSERT INTO users VALUES (123, 'Existing partner', 'partner', 1, '2026-11-01T00:00:00+00:00', 'old');
                INSERT INTO jobs VALUES ('remote-old', 123, 'OLD', 1000, 10, 'bot-old', 8, 'completed',
                    10, 12, 2, '2026-11-01T00:00:00+00:00', 'old', 'old', 100000, 1);
                INSERT INTO mail_purchases VALUES ('paid-old', 'remote-old', 'shop.test', 'safe-hash',
                    1, 10, 53027, 'ORDER-OLD', 'balance_difference', 'confirmed', 'old', 'old');
            ''')
            if orphan:
                db.execute('UPDATE jobs SET chat_id=999')
            db.commit()

    def test_local_is_created_once_but_never_enters_telegram_allowlist(self):
        store = TelegramUserStore(self.path)
        store.upsert_user(123, 'Partner')
        local = store.get_user(LOCAL_USER_ID)
        self.assertTrue(local.is_local)
        self.assertEqual(local.name, 'Local')
        self.assertEqual(store.active_ids(), (123,))
        self.assertEqual(store.roles(), {123: 'partner'})
        self.assertEqual(len(store.list_users()), 1)
        self.assertEqual(store.list_users(include_local=True)[0].chat_id, LOCAL_USER_ID)
        reopened = TelegramUserStore(self.path)
        self.assertEqual(sum(u.is_local for u in reopened.list_users(include_local=True)), 1)
        for action in (lambda: store.upsert_user(LOCAL_USER_ID, 'Changed'),
                lambda: store.set_active(LOCAL_USER_ID, False)):
            with self.assertRaises(ValueError):
                action()

    def test_local_jobs_require_desktop_origin_and_are_idempotent(self):
        store = TelegramUserStore(self.path)
        self.assertTrue(store.record_local_job('local1', 'TEST', 1000, 10, sale_rate_units=100000))
        self.assertFalse(store.record_local_job('local1', 'OTHER', 1000, 10))
        for kwargs in ({'bot_fingerprint': 'bot'}, {'update_id': 1}):
            with self.assertRaises(ValueError):
                store.record_job('forged', LOCAL_USER_ID, 'TEST', 1000, 10, **kwargs)
        self.assertEqual(len(store.list_jobs(LOCAL_USER_ID)), 1)

    def test_migration_preserves_owners_prices_costs_foreign_keys_and_a_backup(self):
        self.legacy()
        store = TelegramUserStore(self.path)
        user = store.get_user(123)
        self.assertEqual((user.completed_jobs, user.sold_beans, user.revenue_vnd, user.cost_vnd),
            (1, 1000, 10000, Decimal('530.27')))
        self.assertEqual(store.get_job('remote-old').chat_id, 123)
        self.assertEqual(store.lookup_request('bot-old', 8).job_id, 'remote-old')
        self.assertEqual(store.list_jobs(LOCAL_USER_ID), ())
        store.record_local_job('local-new', 'NEW', 100, 1, sale_rate_units=100000)
        store.record_purchase(MailPurchaseReceipt('p-new', 'local-new', 'shop.test', 'safe-hash', 1, 1, 65))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(db.execute('PRAGMA foreign_key_list(jobs)').fetchone()[2], 'users')
        backups = list(self.path.parent.glob('users.before_local_*.sqlite3'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as db:
            self.assertEqual(db.execute('SELECT cost_units FROM mail_purchases').fetchone()[0], 53027)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
        self.assertEqual(TelegramUserStore(self.path).get_user(LOCAL_USER_ID).cost_units, 65)
        self.assertEqual(len(list(self.path.parent.glob('users.before_local_*.sqlite3'))), 1)

    def test_failed_migration_rolls_back_without_losing_original_rows(self):
        self.legacy(orphan=True)
        with self.assertRaises(ValueError):
            TelegramUserStore(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            sql = db.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
            self.assertNotIn('chat_id = -1', sql)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT chat_id FROM jobs').fetchone()[0], 999)
            self.assertEqual(db.execute('SELECT cost_units FROM mail_purchases').fetchone()[0], 53027)
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='__users_with_local'").fetchone())

    def test_local_money_and_job_history_stay_separate_from_remote_users(self):
        store = TelegramUserStore(self.path)
        store.upsert_user(123, 'Partner')
        store.record_local_job('local1', 'LOCAL', 1000, 10, sale_rate_units=100000)
        store.record_job('remote1', 123, 'REMOTE', 1000, 10, sale_rate_units=200000)
        store.record_purchase(MailPurchaseReceipt('p-local', 'local1', 'shop.test', 'safe-hash', 1, 10, 53027))
        store.finish_job('local1', 'completed', 10)
        store.finish_job('remote1', 'completed', 10)
        local = TelegramUserStore(self.path).get_user(LOCAL_USER_ID)
        self.assertEqual((local.revenue_vnd, local.cost_vnd, local.profit_vnd),
            (10000, Decimal('530.27'), Decimal('9469.73')))
        self.assertEqual(store.get_user(123).revenue_vnd, 20000)
        self.assertEqual(store.get_user(123).cost_vnd, 0)
        self.assertEqual([j['job_id'] for j in store.list_jobs(LOCAL_USER_ID)], ['local1'])
        self.assertEqual(len(store.list_purchases(LOCAL_USER_ID)), 1)


class LocalUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(str(Path(self.temp.name) / 'settings.ini'), QSettings.IniFormat)
        self.settings.setValue('finance/sale_rate_per_100', '1000')
        self.mochi = MochiChatPage(settings=self.settings)
        self.mochi.input_code.setText('LOCAL1')
        self.mochi.input_target_points.setText('1000')
        self.mochi.input_api_key.setText('offline-shop-key')
        self.page = TelegramPage(self.settings, self.mochi)
        self.page.store.upsert_user(123, 'Partner')
        self.page.store.set_user_mail_keys(123,'offline-gmailvip-123','offline-1trieumail-123')
        self.page._render_users()
        self.addCleanup(self.mochi.close)
        self.addCleanup(self.page.close)

    def local_row(self):
        return next(row for row in range(self.page.table_users.rowCount())
            if self.page.table_users.item(row, 0).data(Qt.ItemDataRole.UserRole) == LOCAL_USER_ID)

    def start_local(self):
        def before_worker():
            self.assertEqual(len(self.page.store.list_jobs(LOCAL_USER_ID)), len(self.mochi._session_jobs))
        with patch.object(MochiWorkerThread, 'start', side_effect=before_worker):
            job_id = self.mochi._on_start_clicked()
        self.assertIsNotNone(job_id)
        self.assertEqual(self.page.store.get_job(job_id).chat_id, LOCAL_USER_ID)
        return job_id

    def finish(self, job_id, status='completed', success=10):
        self.mochi._on_job_finished(MochiInviteJobSummary(job_id, 'LOCAL1', 10, success,
            success, 0, 0, status, ()))

    def bot(self):
        worker = SimpleNamespace(fingerprint=hashlib.sha256(b'offline-bot').hexdigest(),
            access=TelegramAccess(self.page.store.active_ids()), reply=Mock(), complete_request=Mock())
        self.page.worker, self.page._accepting_requests = worker, True
        return worker

    def test_local_is_visible_without_chat_id_and_is_not_a_bot_user(self):
        row = self.local_row()
        self.assertEqual(row, 0)
        self.assertEqual([self.page.table_users.item(row, col).text() for col in (0, 1, 2, 6)],
            ['LOCAL', 'Local', 'local', 'Máy tính'])
        self.assertEqual(self.page.lbl_user_count.text(), '1')
        self.assertEqual(self.page._ids(), (123,))

    def test_manual_job_is_registered_before_worker_with_bot_stopped(self):
        self.assertIsNone(self.page.worker)
        job = self.start_local()
        stored = self.page.store.get_job(job)
        self.assertEqual((stored.chat_id, stored.sale_rate_units, stored.code, stored.target_count),
            (LOCAL_USER_ID, 100000, 'LOCAL1', 10))

    def test_full_local_cost_progress_revenue_and_profit_are_persistent(self):
        job = self.start_local()
        sink = self.mochi._workers[job].runner.mail_purchase_sink
        sink(MailPurchaseReceipt('p-local-ui', job, 'shop.test', 'safe-hash', 1, 10, 53027))
        self.assertEqual(self.page.store.get_user(LOCAL_USER_ID).cost_units, 53027)
        self.mochi._handle_runner_progress(JobProgressEvent(job, 1000, 3, 3, 0, 'running'))
        self.assertEqual(self.page.store.get_user(LOCAL_USER_ID).sold_beans, 300)
        self.finish(job)
        self.finish(job)
        user = self.page.store.get_user(LOCAL_USER_ID)
        self.assertEqual((user.completed_jobs, user.sold_beans, user.profit_vnd),
            (1, 1000, Decimal('9469.73')))
        self.assertEqual(self.page.table_users.item(self.local_row(), 9).text(), '9,469.73')
        self.assertEqual(self.page.store.get_user(123).sold_beans, 0)
        self.assertEqual(TelegramUserStore(self.page.store.path).get_user(LOCAL_USER_ID).completed_jobs, 1)

    def test_local_uses_price_snapshot_and_does_not_send_telegram_notifications(self):
        worker = self.bot()
        first = self.start_local()
        self.settings.setValue('finance/sale_rate_per_100', '2000')
        second = self.start_local()
        self.finish(first)
        self.finish(second)
        worker.reply.assert_not_called()
        self.assertEqual(self.page.store.get_user(LOCAL_USER_ID).revenue_vnd, 30000)

    def test_remote_job_ownership_is_not_overwritten_with_local(self):
        worker = self.bot()
        with patch.object(MochiWorkerThread, 'start'), \
                patch.object(self.mochi, 'local_job_registrar', side_effect=AssertionError('wrong owner')):
            self.page._on_job_requested((worker.fingerprint, TelegramJobRequest(1, 123, 'REMOTE', 1000, '')))
        stored = self.page.store.lookup_request(worker.fingerprint, 1)
        self.assertEqual(stored.chat_id, 123)
        self.assertEqual(self.page.store.list_jobs(LOCAL_USER_ID), ())

    def test_failed_and_cancelled_local_jobs_keep_partial_beans_and_full_cost(self):
        first = self.start_local()
        self.mochi._workers[first].runner.mail_purchase_sink(
            MailPurchaseReceipt('fail-local', first, 'shop.test', 'safe-hash', 1, 10, 30065))
        second = self.start_local()
        self.finish(first, 'failed', 2)
        self.finish(second, 'cancelled', 1)
        user = self.page.store.get_user(LOCAL_USER_ID)
        self.assertEqual((user.failed_jobs, user.sold_beans, user.revenue_units, user.cost_units),
            (1, 300, 0, 30065))

    def test_failed_local_owner_persistence_does_not_start_or_buy(self):
        with patch.object(self.page.store, 'record_local_job', side_effect=sqlite3.OperationalError('cannot save')), \
                patch.object(MochiWorkerThread, 'start') as start:
            result = self.mochi._on_start_clicked()
        self.assertIsNone(result)
        start.assert_not_called()
        self.assertEqual(self.mochi._workers, {})
        self.assertFalse(self.page.store.list_jobs(LOCAL_USER_ID))

    def test_worker_start_failure_is_recorded_as_a_failed_local_job(self):
        with patch.object(MochiWorkerThread, 'start', side_effect=RuntimeError('cannot create thread')):
            result = self.mochi._on_start_clicked()
        self.assertIsNone(result)
        self.assertEqual(self.page.store.get_user(LOCAL_USER_ID).failed_jobs, 1)
        self.assertEqual(self.mochi._workers, {})
        self.assertFalse(self.page.store.list_purchases(LOCAL_USER_ID))

    def test_local_row_is_protected_from_revoke_and_edit_but_can_open_its_job_history(self):
        job = self.start_local()
        self.page.table_users.selectRow(self.local_row())
        self.assertFalse(self.page.btn_update_user.isEnabled())
        self.assertFalse(self.page.btn_remove_id.isEnabled())
        self.assertFalse(self.page.btn_restore_id.isEnabled())
        self.assertTrue(self.page.btn_view_jobs.isEnabled())
        self.assertEqual(self.page.input_chat_id.text(), '')
        self.page._remove_ids()
        self.page.table_users.selectRow(self.local_row())
        with patch('gui.pages.telegram.QDialog.exec', return_value=0):
            self.page.btn_view_jobs.click()
        dialog = self.page.findChildren(QDialog)[-1]
        table = dialog.findChild(QTableWidget)
        self.assertEqual(dialog.windowTitle(), 'Job của Local')
        self.assertEqual(table.rowCount(), 1)
        self.assertEqual(table.item(0, 0).text(), job)
        self.assertTrue(self.page.store.get_user(LOCAL_USER_ID).active)
        self.assertEqual(self.page.store.active_ids(), (123,))

    def test_mixed_selection_revokes_only_real_telegram_users(self):
        selection = self.page.table_users.selectionModel()
        flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
        for row in range(self.page.table_users.rowCount()):
            selection.select(self.page.table_users.model().index(row, 0), flags)
        self.assertTrue(self.page.btn_remove_id.isEnabled())
        self.page.btn_remove_id.click()
        self.assertTrue(self.page.store.get_user(LOCAL_USER_ID).active)
        self.assertFalse(self.page.store.get_user(123).active)
        self.assertEqual(self.page._ids(), ())

    def test_local_cannot_receive_forged_bot_jobs_or_bot_statistics(self):
        worker = self.bot()
        worker.access = SimpleNamespace(allowed=lambda _: True)
        request = TelegramJobRequest(1, LOCAL_USER_ID, 'FORGED', 1000, '')
        with patch.object(MochiWorkerThread, 'start') as start:
            self.page._on_job_requested((worker.fingerprint, request))
        self.page._on_control_requested((worker.fingerprint, SimpleNamespace(
            chat_id=LOCAL_USER_ID, target_chat_id=LOCAL_USER_ID, update_id=2, action='stats')))
        start.assert_not_called()
        worker.reply.assert_not_called()
        self.assertEqual(worker.complete_request.call_count, 2)
        self.assertFalse(self.page.store.list_jobs(LOCAL_USER_ID))


if __name__ == '__main__':
    unittest.main(verbosity=2)
