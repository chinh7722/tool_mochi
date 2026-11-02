"""Offline scheduling, wallet settlement and private Telegram state notifications."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox
from core.platforms.mochichat.runners.invite_referral import MochiInviteJobSummary
from core.telegram_bot import TelegramAccess, TelegramJobRequest, TelegramCustomerRequest
from core.telegram_users import LOCAL_USER_ID, TelegramUserStore
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread
from gui.pages.telegram import TelegramPage
from gui.pages.proxy import ProxyPage, _ProxyImportDialog
from gui.pages.mail_proxy import MailProxyPage


class QueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(str(Path(self.temp.name) / 'settings.ini'), QSettings.IniFormat)
        self.settings.setValue('finance/sale_rate_per_100', '1000')
        self.mochi = MochiChatPage(settings=self.settings)
        self.mochi.input_api_key.setText('offline-shop-key')
        self.mochi.spin_job_concurrency.setValue(1)
        self.page = TelegramPage(self.settings, self.mochi)
        self.store = self.page.store
        self.store.upsert_user(123, 'Admin', 'admin')
        self.store.upsert_user(234, 'Partner', 'partner')
        for chat_id in (123,234):
            self.store.set_user_mail_keys(chat_id,f'offline-gmailvip-{chat_id}',f'offline-1trieumail-{chat_id}')
        self.bot = SimpleNamespace(fingerprint='offline-fingerprint', access=TelegramAccess((123, 234)),
            reply=Mock(), complete_request=Mock())
        self.page.worker = self.bot
        self.page._accepting_requests = True
        self.addCleanup(self.mochi.close)
        self.addCleanup(self.page.close)
        self.started = []
        owner = self
        def start(worker):
            owner.started.append(worker.job_id)
        self.start_patch = patch.object(MochiWorkerThread, 'start', start)
        self.start_patch.start()
        self.addCleanup(self.start_patch.stop)

    def local(self):
        self.mochi.input_code.setText('LOCAL')
        self.mochi.input_target_points.setText('100')
        return self.mochi._on_start_clicked()

    def operator(self, update, chat_id=123):
        request = TelegramJobRequest(update, chat_id, 'TEST', 100, '')
        self.page._on_job_requested((self.bot.fingerprint, request))
        return self.store.lookup_request(self.bot.fingerprint, update).job_id

    def customer(self, update=30):
        self.store.ensure_customer(456, 'Khách')
        self.store.set_customer_balance(456, 1000000, 'offline credit', expected_units=0)
        request = TelegramCustomerRequest(update, 456, 'job', 'Khách', 'TEST', 100)
        self.page._on_customer_requested((self.bot.fingerprint, request))
        return self.store.lookup_request(self.bot.fingerprint, update).job_id

    def finish(self, job, status='completed'):
        self.mochi._on_job_finished(MochiInviteJobSummary(job, 'TEST', 1, 1, 1, 0, 0, status, ()))

    def release(self, job):
        self.mochi._on_worker_stopped(job)

    def test_one_shared_limit_fifo_for_local_admin_partner_and_customer(self):
        first = self.local()
        admin, partner, customer = self.operator(10), self.operator(20, 234), self.customer()
        self.assertEqual(self.started, [first])
        self.assertEqual(list(self.mochi._pending_jobs), [admin, partner, customer])
        for previous, following in ((first, admin), (admin, partner), (partner, customer)):
            self.finish(previous)
            self.assertNotIn(following, self.started, 'A terminal result must not free a still-alive QThread slot')
            self.release(previous)
            self.assertEqual(self.started[-1], following)
        self.assertEqual(self.store.get_job(first).chat_id, LOCAL_USER_ID)
        self.assertEqual(self.store.get_job(customer).dispatch_state, 'running')

    def test_increasing_limit_dispatches_pending_jobs_and_persists_setting(self):
        jobs = [self.local() for _ in range(4)]
        self.mochi.spin_job_concurrency.setValue(3)
        self.assertEqual(self.started, jobs[:3])
        self.assertEqual(list(self.mochi._pending_jobs), jobs[3:])
        self.assertEqual(self.settings.value('runtime/mochi_chat/job_concurrency', type=int), 3)
        reloaded = MochiChatPage(settings=self.settings)
        self.addCleanup(reloaded.close)
        self.assertEqual(reloaded.spin_job_concurrency.value(), 3)

    def test_decreasing_limit_does_not_cancel_running_or_overfill_replacement(self):
        self.mochi.spin_job_concurrency.setValue(3)
        jobs = [self.local() for _ in range(4)]
        self.mochi.spin_job_concurrency.setValue(1)
        for job in jobs[:2]:
            self.finish(job)
            self.release(job)
        self.assertEqual(self.started, jobs[:3])
        self.finish(jobs[2])
        self.release(jobs[2])
        self.assertEqual(self.started, jobs)

    def test_queued_job_never_runs_before_slot_and_can_be_cancelled_and_archived(self):
        first, waiting = self.local(), self.local()
        worker = self.mochi._workers[waiting]
        with patch.object(worker.runner, 'run') as run:
            self.mochi._stop_job(waiting)
            run.assert_not_called()
        self.assertEqual(self.started, [first])
        self.assertEqual(self.store.get_job(waiting).status, 'cancelled')
        self.assertNotIn(waiting, self.mochi._workers)
        entries = {x.job_id: x for x in self.mochi.job_history_store.read()}
        self.assertEqual((entries[waiting].status, entries[waiting].achieved_beans), ('cancelled', 0))
        self.mochi._delete_job(waiting)
        self.assertNotIn(waiting, self.mochi._session_jobs)

    def test_queued_customer_holds_then_refunds_without_mail_cost_or_execution(self):
        self.local()
        job = self.customer()
        self.assertEqual(self.store.get_customer(456)['balance_units'], 900000)
        self.bot.reply.reset_mock()
        self.mochi._stop_job(job)
        self.assertEqual(self.store.get_customer(456)['balance_units'], 1000000)
        self.assertEqual(self.store.get_customer_order_for_job(job)['charged_units'], 0)
        self.assertEqual(self.store.list_purchases(), ())
        self.assertEqual(self.bot.reply.call_args.args[0], 456)
        self.assertIn('Đã hủy', self.bot.reply.call_args.args[1])
        self.assertTrue(self.bot.reply.call_args.kwargs['public'])
        self.assertEqual(len(self.started), 1)

    def test_delete_cannot_orphan_queued_job_and_cancel_removes_it(self):
        self.local()
        job = self.local()
        with patch.object(QMessageBox, 'warning') as warning:
            self.mochi._delete_job(job)
        warning.assert_called_once()
        self.assertIn(job, self.mochi._pending_jobs)
        self.mochi._stop_job(job)
        self.assertNotIn(job, self.mochi._pending_jobs)

    def test_shutdown_cancels_queue_and_does_not_start_it_after_active_exit(self):
        first, waiting = self.local(), self.local()
        self.mochi.shutdown_workers()
        self.release(first)
        self.assertEqual(self.started, [first])
        self.assertFalse(self.mochi._pending_jobs)
        self.assertEqual(self.store.get_job(waiting).status, 'cancelled')
        self.assertIsNone(self.local())

    def test_delayed_start_failure_notifies_owner_and_continues_next_job(self):
        first = self.local()
        failed, following = self.operator(10), self.operator(20, 234)
        self.bot.reply.reset_mock()
        def start(worker):
            if worker.job_id == failed:
                raise RuntimeError('browser resource unavailable api_key=private')
            self.started.append(worker.job_id)
        with patch.object(MochiWorkerThread, 'start', start):
            self.finish(first)
            self.release(first)
        self.assertEqual(self.store.get_job(failed).status, 'failed')
        self.assertEqual(self.started, [first, following])
        replies = [(c.args[0], c.args[1]) for c in self.bot.reply.call_args_list]
        self.assertTrue(any(chat == 123 and 'Nguyên nhân:' in text for chat, text in replies))
        self.assertTrue(any(chat == 234 and 'Đang xử lý' in text for chat, text in replies))
        self.assertFalse(any('private' in text for _, text in replies))

    def test_admin_and_partner_receive_wait_processing_and_completed_once(self):
        for chat_id in (123, 234):
            with self.subTest(chat_id=chat_id):
                first = self.local()
                job = self.operator(chat_id, chat_id)
                self.assertIn('Đang chờ (vị trí 1)', self.bot.reply.call_args.args[1])
                self.assertEqual(self.store.get_job(job).dispatch_state, 'queued')
                self.bot.reply.reset_mock()
                self.finish(first)
                self.release(first)
                self.bot.reply.assert_called_once()
                self.assertEqual(self.bot.reply.call_args.args[0], chat_id)
                self.assertIn('Đang xử lý', self.bot.reply.call_args.args[1])
                self.page._on_job_dispatch_changed(job, 'running')
                self.bot.reply.assert_called_once()
                self.finish(job)
                self.finish(job)
                self.assertEqual(self.bot.reply.call_count, 2)
                self.assertIn('Đã hoàn thành', self.bot.reply.call_args.args[1])
                self.release(job)

    def test_customer_receives_wait_processing_completed_and_status_query(self):
        first = self.local()
        job = self.customer()
        self.assertIn('Đang chờ', self.bot.reply.call_args.args[1])
        request = TelegramCustomerRequest(31, 456, 'orders', 'Khách')
        self.page._on_customer_requested((self.bot.fingerprint, request))
        self.assertIn('Đang chờ', self.bot.reply.call_args.args[1])
        self.bot.reply.reset_mock()
        self.finish(first)
        self.release(first)
        self.assertIn('Đang xử lý', self.bot.reply.call_args.args[1])
        self.assertTrue(self.bot.reply.call_args.kwargs['public'])
        self.finish(job)
        self.assertIn('Đã hoàn thành', self.bot.reply.call_args.args[1])
        self.assertIn(self.store.get_customer_order_for_job(job)['order_id'], self.bot.reply.call_args.args[1])
        self.assertEqual(self.store.get_customer(456)['balance_units'], 900000)

    def test_immediate_job_creation_reply_reports_processing_without_duplicate_start_notice(self):
        self.operator(10)
        self.bot.reply.assert_called_once()
        self.assertIn('Đang xử lý', self.bot.reply.call_args.args[1])

    def test_revoked_operator_and_replaced_bot_do_not_receive_queue_notices(self):
        first = self.local()
        revoked, old_bot = self.operator(10), self.operator(20, 234)
        self.store.set_active(123, False)
        self.bot.access.replace((234,))
        self.bot.reply.reset_mock()
        self.finish(first)
        self.release(first)
        self.bot.reply.assert_not_called()
        self.finish(revoked)
        self.bot.fingerprint = 'different-bot'
        self.release(revoked)
        self.bot.reply.assert_not_called()
        self.assertEqual(self.store.get_job(old_bot).dispatch_state, 'running')

    def test_reopening_store_keeps_queue_status_and_terminal_update_is_ignored(self):
        self.local()
        job = self.operator(10)
        reopened = TelegramUserStore(self.store.path)
        self.assertEqual(reopened.get_job(job).dispatch_state, 'queued')
        self.mochi._stop_job(job)
        self.assertFalse(reopened.update_dispatch(job, 'running'))
        self.assertEqual(reopened.get_job(job).status, 'cancelled')

    def test_stop_during_thread_cleanup_preserves_terminal_status_and_history(self):
        job = self.local()
        self.finish(job)
        self.mochi._stop_job(job)
        self.assertEqual(self.mochi._session_jobs[job].status, 'Hoàn thành')
        self.assertEqual(self.mochi.job_history_store.read()[0].status, 'completed')

    def test_thread_must_fully_exit_before_next_job_starts(self):
        self.start_patch.stop()
        release = threading.Event()
        entered = threading.Event()
        def run(worker):
            entered.set()
            release.wait(5)
        try:
            with patch.object(MochiWorkerThread, 'run', run):
                first = self.local()
                self.assertTrue(entered.wait(2))
                second = self.local()
                self.finish(first)
                self.app.processEvents()
                self.assertIn(second, self.mochi._pending_jobs)
                release.set()
                deadline = time.monotonic() + 3
                while second in self.mochi._pending_jobs and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                self.assertNotIn(second, self.mochi._pending_jobs)
                worker = self.mochi._workers.get(second)
                if worker:
                    self.assertTrue(worker.wait(2000))
                self.app.processEvents()
        finally:
            release.set()
            for worker in list(self.mochi._workers.values()):
                worker.wait(2000)


class ProxyFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_proxyvn_placeholder_and_txt_round_trip_for_both_proxy_tabs(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / 'settings.ini'), QSettings.IniFormat)
            for page_type in (ProxyPage, MailProxyPage):
                page = page_type(settings=settings)
                try:
                    dialog = _ProxyImportDialog(page)
                    self.assertIn('32001|KEY_XOAY_1', dialog.txt_input.placeholderText())
                    self.assertNotIn('USER', dialog.txt_input.placeholderText())
                    ids, duplicates, invalid = page._import_proxy_lines(['192.0.2.10:32001|offline-key'], 'proxyvn')
                    self.assertEqual((len(ids), duplicates, invalid), (1, 0, []))
                    self.assertEqual(page._serialize_proxy_lines(), ['192.0.2.10:32001|offline-key'])
                    dialog.combo_provider.setCurrentIndex(1)
                    self.assertIn('32001:USER:PASS|KEY_XOAY_1', dialog.txt_input.placeholderText())
                    dialog.close()
                finally:
                    page.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
