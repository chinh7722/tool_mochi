"""Offline named-user management and durable Telegram job accounting in Qt."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import hashlib
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from core.job_events import JobProgressEvent
from core.platforms.mochichat.runners.invite_referral import MochiInviteJobSummary
from core.telegram_bot import TelegramAccess, TelegramJobRequest
from core.telegram_users import LOCAL_USER_ID, TelegramUserStore
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread
from gui.pages.telegram import TelegramPage


TOKEN = '123456789:' + 'A' * 35
FINGERPRINT = hashlib.sha256(TOKEN.encode()).hexdigest()


class TelegramUserUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.settings = QSettings(str(self.path / 'settings.ini'), QSettings.IniFormat)
        self.store = TelegramUserStore(self.path / 'users.sqlite3')
        self.store.upsert_user(123, 'Đối tác A')
        self.store.set_user_mail_keys(123,'offline-gmailvip-123','offline-1trieumail-123')
        self.mochi = MochiChatPage(settings=self.settings)
        self.mochi.input_api_key.setText('offline-shop-key')
        self.page = TelegramPage(self.settings, self.mochi, user_store=self.store)
        self.addCleanup(self.mochi.close)
        self.addCleanup(self.page.close)

    def select_user(self, chat_id):
        for row in range(self.page.table_users.rowCount()):
            if self.page.table_users.item(row, 0).text() == str(chat_id):
                self.page.table_users.selectRow(row)
                self.app.processEvents()
                return row
        self.fail(f'User {chat_id} missing from the management table')

    def set_role(self, role):
        index = self.page.combo_role.findData(role)
        self.assertGreaterEqual(index, 0, f'Missing role {role}')
        self.page.combo_role.setCurrentIndex(index)

    def fake_worker(self, ids=None):
        worker = SimpleNamespace(fingerprint=FINGERPRINT,
            access=TelegramAccess(self.store.active_ids() if ids is None else ids),
            reply=Mock(), complete_request=Mock(), request_stop=Mock())
        self.page.input_token.setText(TOKEN)
        self.page.worker = worker
        self.page._accepting_requests = True
        return worker

    def remote_job(self, update_id=1, *, points=1000):
        if self.page.worker is None:
            self.fake_worker()
        request = TelegramJobRequest(update_id, 123, 'REMOTE', points, 'Khách A')
        with patch.object(MochiWorkerThread, 'start') as start:
            self.page._on_job_requested((FINGERPRINT, request))
        self.assertEqual(start.call_count, 1)
        stored = self.store.lookup_request(FINGERPRINT, update_id)
        self.assertIsNotNone(stored)
        return stored.job_id

    def finish(self, job_id, status, success_count, failure_count=0):
        self.mochi._on_job_finished(MochiInviteJobSummary(job_id, 'REMOTE', 10,
            success_count + failure_count, success_count, failure_count, 0, status, ()))

    def progress(self, job_id, success_count, attempted_count=0, failure_count=0):
        self.mochi._handle_runner_progress(JobProgressEvent(job_id=job_id,
            target_points=10, attempted_count=attempted_count,
            success_count=success_count, failure_count=failure_count, status='running'))

    def assert_table_counts(self, chat_id, completed, failed, beans):
        self.page._render_users()
        row = self.select_user(chat_id)
        values = tuple(int(''.join(character for character in self.page.table_users.item(row, column).text()
            if character.isdigit()) or '0') for column in (3, 4, 5))
        self.assertEqual(values, (completed, failed, beans))

    def test_named_roles_save_reload_and_edit_existing_user(self):
        self.page.input_chat_id.setText('456')
        self.page.input_name.setText('Quản trị viên B')
        self.set_role('admin')
        self.page._add_id()
        added = self.store.get_user(456)
        self.assertEqual((added.name, added.role, added.active), ('Quản trị viên B', 'admin', True))
        self.select_user(456)
        self.page.input_name.setText('Partner B')
        self.set_role('partner')
        self.page._save_user()
        reopened = TelegramUserStore(self.path / 'users.sqlite3').get_user(456)
        self.assertEqual((reopened.name, reopened.role), ('Partner B', 'partner'))
        self.page._render_users()
        row = self.select_user(456)
        self.assertEqual(self.page.table_users.item(row, 1).text(), 'Partner B')
        self.assertEqual(self.page.table_users.item(row, 2).text().lower(), 'partner')

    def test_revoke_edit_and_restore_preserve_history_and_permissions(self):
        job_id = self.remote_job()
        self.finish(job_id, 'completed', 10)
        worker = self.page.worker
        self.select_user(123)
        self.page._remove_ids()
        self.assertFalse(self.store.get_user(123).active)
        self.assertFalse(worker.access.allowed(123))
        self.select_user(123)
        self.page.input_name.setText('Tên sau thu hồi')
        self.set_role('admin')
        self.page._save_user()
        user = self.store.get_user(123)
        self.assertEqual((user.name, user.role, user.active, user.sold_beans),
            ('Tên sau thu hồi', 'admin', False, 1000))
        self.assertFalse(worker.access.allowed(123))
        self.select_user(123)
        self.page._restore_ids()
        self.assertTrue(self.store.get_user(123).active)
        self.assertTrue(worker.access.allowed(123))
        self.assert_table_counts(123, 1, 0, 1000)

    def test_completed_and_partial_failed_jobs_count_jobs_and_actual_beans(self):
        first = self.remote_job(1)
        second = self.remote_job(2)
        self.finish(second, 'failed', 3, 20)
        self.finish(first, 'completed', 10, 5)
        user = self.store.get_user(123)
        self.assertEqual((user.completed_jobs, user.failed_jobs, user.sold_beans), (1, 1, 1300))
        self.assert_table_counts(123, 1, 1, 1300)

    def test_cancelled_job_preserves_partial_beans_without_failed_job(self):
        job_id = self.remote_job()
        self.finish(job_id, 'cancelled', 4)
        self.assert_table_counts(123, 0, 0, 400)

    def test_duplicate_or_conflicting_finalization_does_not_change_totals(self):
        job_id = self.remote_job()
        self.finish(job_id, 'failed', 3, 2)
        self.finish(job_id, 'failed', 3, 2)
        self.finish(job_id, 'completed', 10)
        self.mochi._on_job_error('late execution error', job_id)
        self.assert_table_counts(123, 0, 1, 300)

    def test_progress_persists_and_exception_keeps_confirmed_beans(self):
        job_id = self.remote_job()
        self.progress(job_id, 5, attempted_count=8, failure_count=3)
        reopened = TelegramUserStore(self.path / 'users.sqlite3')
        self.assertEqual(reopened.get_user(123).sold_beans, 500)
        self.assertEqual(reopened.get_job(job_id).attempted_count, 8)
        self.mochi._on_job_error('offline exception detail', job_id)
        self.assert_table_counts(123, 0, 1, 500)

    def test_results_still_count_while_bot_is_stopped(self):
        job_id = self.remote_job()
        worker = self.page.worker
        worker.reply.reset_mock()
        self.page.worker = None
        self.page._accepting_requests = False
        self.finish(job_id, 'completed', 10)
        self.assert_table_counts(123, 1, 0, 1000)
        worker.reply.assert_not_called()

    def test_revoked_user_still_gets_accounted_but_no_result_message(self):
        job_id = self.remote_job()
        worker = self.page.worker
        self.select_user(123)
        self.page._remove_ids()
        worker.reply.reset_mock()
        self.progress(job_id, 2, attempted_count=4, failure_count=2)
        self.finish(job_id, 'failed', 2, 2)
        self.assert_table_counts(123, 0, 1, 200)
        worker.reply.assert_not_called()

    def test_store_checks_access_independently_of_stale_worker_permissions(self):
        self.fake_worker()
        self.store.set_active(123, False)
        self.assertTrue(self.page.worker.access.allowed(123))
        with patch.object(MochiWorkerThread, 'start') as start:
            self.page._on_job_requested((FINGERPRINT, TelegramJobRequest(1, 123, 'REMOTE', 1000)))
        start.assert_not_called()
        self.assertFalse(self.mochi._workers)
        self.assertIsNone(self.store.lookup_request(FINGERPRINT, 1))

    def test_job_is_persisted_before_the_paid_worker_can_start(self):
        self.fake_worker()
        observed = []

        def observe_start(worker):
            job = TelegramUserStore(self.path / 'users.sqlite3').get_job(worker.job_id)
            self.assertIsNotNone(job)
            self.assertEqual((job.chat_id, job.requested_beans, job.target_count), (123, 700, 7))
            self.assertEqual((job.bot_fingerprint, job.update_id), (FINGERPRINT, 17))
            observed.append(worker.job_id)

        with patch.object(MochiWorkerThread, 'start', observe_start):
            self.page._on_job_requested((FINGERPRINT, TelegramJobRequest(17, 123, 'REMOTE', 700)))
        self.assertEqual(len(observed), 1)

    def test_store_failure_prevents_starting_paid_work(self):
        worker = self.fake_worker()
        with patch.object(self.store, 'record_job', side_effect=sqlite3.OperationalError('disk unavailable')), \
                patch.object(MochiWorkerThread, 'start') as start:
            self.page._on_job_requested((FINGERPRINT, TelegramJobRequest(1, 123, 'REMOTE', 1000)))
        start.assert_not_called()
        self.assertFalse(self.mochi._workers)
        self.assertIsNone(self.store.lookup_request(FINGERPRINT, 1))
        worker.reply.assert_called()
        self.assertNotIn('disk unavailable', worker.reply.call_args.args[1])

    def test_user_read_failure_rejects_job_before_registration_or_start(self):
        worker = self.fake_worker()
        detail = 'injected SQLite user SELECT detail'
        with patch.object(self.store, 'get_user', side_effect=sqlite3.OperationalError(detail)), \
                patch.object(self.store, 'record_job', wraps=self.store.record_job) as record, \
                patch.object(MochiWorkerThread, 'start') as start:
            self.page._on_job_requested((FINGERPRINT, TelegramJobRequest(31, 123, 'REMOTE', 1000)))
        start.assert_not_called()
        record.assert_not_called()
        self.assertFalse(self.mochi._workers)
        self.assertIsNone(self.store.lookup_request(FINGERPRINT, 31))
        completed = any(call.args == (31,) for call in worker.complete_request.call_args_list)
        replied = any(len(call.args) >= 3 and call.args[2] == 31 for call in worker.reply.call_args_list)
        self.assertTrue(completed or replied, 'Rejected request must leave the pending queue')
        for call in worker.reply.call_args_list:
            self.assertNotIn(detail, call.args[1])

    def test_request_lookup_failure_rejects_job_before_registration_or_start(self):
        worker = self.fake_worker()
        detail = 'injected SQLite request SELECT detail'
        with patch.object(self.store, 'lookup_request', side_effect=sqlite3.OperationalError(detail)), \
                patch.object(self.store, 'record_job', wraps=self.store.record_job) as record, \
                patch.object(MochiWorkerThread, 'start') as start:
            self.page._on_job_requested((FINGERPRINT, TelegramJobRequest(32, 123, 'REMOTE', 1000)))
        start.assert_not_called()
        record.assert_not_called()
        self.assertFalse(self.mochi._workers)
        self.assertIsNone(self.store.lookup_request(FINGERPRINT, 32))
        completed = any(call.args == (32,) for call in worker.complete_request.call_args_list)
        replied = any(len(call.args) >= 3 and call.args[2] == 32 for call in worker.reply.call_args_list)
        self.assertTrue(completed or replied, 'Rejected request must leave the pending queue')
        for call in worker.reply.call_args_list:
            self.assertNotIn(detail, call.args[1])

    def test_replayed_request_after_reopening_store_does_not_start_another_job(self):
        self.fake_worker()
        request = TelegramJobRequest(27, 123, 'REMOTE', 1000)
        with patch.object(MochiWorkerThread, 'start') as start:
            self.page._on_job_requested((FINGERPRINT, request))
            self.page.store = TelegramUserStore(self.path / 'users.sqlite3')
            self.page._on_job_requested((FINGERPRINT, request))
        self.assertEqual(start.call_count, 1)
        self.assertEqual(self.store.get_user(123).requested_beans, 1000)
        self.assertEqual(len(self.mochi._workers), 1)

    def test_local_job_does_not_contribute_to_a_telegram_users_statistics(self):
        self.mochi.input_code.setText('LOCAL')
        self.mochi.input_target_points.setText('1000')
        with patch.object(MochiWorkerThread, 'start'):
            job_id = self.mochi._on_start_clicked()
        self.assertIsNotNone(job_id)
        self.finish(job_id, 'completed', 10)
        self.assertEqual(self.store.get_job(job_id).chat_id, LOCAL_USER_ID)
        self.assertEqual(self.store.get_user(LOCAL_USER_ID).sold_beans, 1000)
        self.assert_table_counts(123, 0, 0, 0)


if __name__ == '__main__':
    unittest.main()
