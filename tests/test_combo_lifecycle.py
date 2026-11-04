"""Offline combo rounds, quota waiting and durable recovery through actual Qt signals."""
from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from core.job_events import JobProgressEvent
from core.job_schedule import VIETNAM_TIME, schedule_time
from core.platforms.mochichat.runners.invite_referral import MochiInviteJobSummary
from core.telegram_bot import TelegramAccess, TelegramCustomerRequest
from core.telegram_users import TelegramUserStore
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread
from gui.pages.telegram import TelegramPage


class Clock(datetime):
    current = None

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz is not None else cls.current.replace(tzinfo=None)


class ComboLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / 'settings.ini'), QSettings.IniFormat)
        self.settings.setValue('finance/sale_rate_per_100', '1000')
        Clock.current = datetime.now(VIETNAM_TIME).replace(microsecond=0)
        self.clock_patch = patch('gui.pages.mochi_chat.datetime', Clock)
        self.clock_patch.start()
        self.started = []
        self.start_patch = patch.object(MochiWorkerThread, 'start', lambda worker: self.started.append(worker.job_id))
        self.start_patch.start()
        self.pages = []
        self.mochi, self.page = self.open_pages()
        self.store = self.page.store
        self.bot = SimpleNamespace(fingerprint='offline-combo', access=TelegramAccess(()),
            reply=Mock(), complete_request=Mock())
        self.page.worker = self.bot
        self.page._accepting_requests = True

    def tearDown(self):
        for mochi, page in reversed(self.pages):
            page.worker = None
            mochi.shutdown_workers()
            page.shutdown_workers()
            page.deleteLater()
            mochi.deleteLater()
        self.app.processEvents()
        self.start_patch.stop()
        self.clock_patch.stop()
        self.temp.cleanup()

    def open_pages(self):
        mochi = MochiChatPage(settings=self.settings)
        mochi.input_api_key.setText('offline-shop-key')
        mochi.spin_job_concurrency.setValue(1)
        mochi._schedule_timer.stop()
        mochi._invite_quota_snapshot = lambda code: mochi.invite_quota_store.snapshot(code, now=Clock.current)
        page = TelegramPage(self.settings, mochi)
        self.pages.append((mochi, page))
        return mochi, page

    def create(self, points=5500, *, combo=True):
        job = self.mochi._on_start_clicked(code='TEST', points=points, combo=combo)
        self.assertIsNotNone(job, self.mochi.validation_label.text())
        return job

    def customer(self):
        self.store.ensure_customer(456, 'Khách A')
        self.store.set_customer_balance(456, 10000000, 'offline funding', expected_units=0)
        request = TelegramCustomerRequest(10, 456, 'job', 'Khách A', 'TEST', 5500, combo=True)
        self.page._on_customer_requested((self.bot.fingerprint, request))
        row = self.store.lookup_request(self.bot.fingerprint, 10)
        self.assertIsNotNone(row)
        return row.job_id

    def dispatch(self, job):
        Clock.current = max(Clock.current, self.mochi._scheduled_jobs[job].scheduled_at) + timedelta(seconds=1)
        self.mochi._check_scheduled_jobs(Clock.current)
        self.app.processEvents()

    def record_confirmed(self, job, first, count):
        for number in range(first, first + count):
            self.mochi.invite_quota_store.record_success('TEST', job, f'offline-account-{number}',
                occurred_at=Clock.current)

    def finish_round(self, job, successes, failures=0, status='invite_limit_reached'):
        worker = self.mochi._workers[job]
        initial = worker.initial_success_count
        self.record_confirmed(job, initial, successes)
        worker.progress_signal.emit(JobProgressEvent(job, 5500, successes + failures,
            initial + successes, failures, 'running'))
        summary = MochiInviteJobSummary(job, 'TEST', worker.target_count, successes + failures,
            initial + successes, failures, 0, status, ())
        worker.finished_signal.emit(summary)
        return summary

    def release(self, job):
        self.mochi._on_worker_stopped(job)

    def test_5500_bean_runs_six_rounds_with_one_job_and_only_one_final_result(self):
        finished, waiting = [], []
        self.mochi.job_finished.connect(finished.append)
        self.mochi.job_combo_waiting.connect(lambda *args: waiting.append(args))
        job = self.create()
        self.assertEqual(self.started, [])
        self.assertEqual(self.store.get_job(job).dispatch_state, 'scheduled')
        for index, successes in enumerate((10, 10, 10, 10, 10, 5)):
            self.dispatch(job)
            self.assertEqual(self.mochi._workers[job].initial_success_count, index * 10)
            self.finish_round(job, successes, 1 if index<4 else 0, 'completed' if index == 5 else 'invite_limit_reached')
            row = self.store.get_job(job)
            self.assertEqual((row.success_count, row.attempted_count, row.failure_count),
                (min(55, (index + 1) * 10), min(55, (index + 1) * 10) + min(index+1,4), min(index+1,4)))
            if index < 5:
                self.assertIsNone(row.finalized_at)
                self.assertEqual(row.dispatch_state, 'scheduled')
                self.assertEqual(schedule_time(row.scheduled_at) - Clock.current, timedelta(hours=24, minutes=1))
                self.assertEqual(self.mochi.job_history_store.read(), ())
                self.assertEqual(finished, [])
            self.release(job)
        self.assertEqual(self.started, [job] * 6)
        self.assertEqual(len(waiting), 5)
        self.assertEqual(len(finished), 1)
        self.assertEqual((finished[0].success_count, finished[0].attempted_count, finished[0].failure_count), (55, 59, 4))
        self.assertEqual(self.store.get_job(job).status, 'completed')
        self.assertEqual(len(self.store.list_jobs(-1)), 1)
        self.assertEqual(len(self.mochi.job_history_store.read()), 1)
        self.assertNotIn(job, self.mochi._scheduled_jobs)

    def test_full_quota_waits_without_worker_resources_or_a_final_result(self):
        self.record_confirmed('other-job', 0, 10)
        self.mochi.input_api_key.clear()
        finished = []
        self.mochi.job_finished.connect(finished.append)
        with patch('gui.pages.mochi_chat.MochiInviteRunner') as runner:
            job = self.create()
            self.dispatch(job)
            runner.assert_not_called()
        self.assertEqual(self.started, [])
        self.assertEqual(finished, [])
        self.assertEqual(self.store.get_job(job).success_count, 0)
        self.assertIsNone(self.store.get_job(job).finalized_at)
        self.assertEqual(self.mochi._session_jobs[job].status, 'Chờ lượt combo')
        self.assertEqual(self.store.list_purchases(), ())

    def test_next_round_waits_for_previous_worker_to_exit_even_when_clock_is_due(self):
        job = self.create()
        self.dispatch(job)
        self.finish_round(job, 10)
        Clock.current = self.mochi._scheduled_jobs[job].scheduled_at + timedelta(seconds=1)
        self.mochi._check_scheduled_jobs(Clock.current)
        self.assertEqual(self.started, [job])
        self.release(job)
        self.mochi._check_scheduled_jobs(Clock.current)
        self.assertEqual(self.started, [job, job])
        self.assertEqual(self.mochi._workers[job].initial_success_count, 10)

    def test_customer_cancel_after_two_rounds_charges_cumulative_success_once(self):
        job = self.customer()
        for _ in range(2):
            self.dispatch(job)
            self.finish_round(job, 10, 1)
            self.release(job)
            self.assertEqual(self.store.get_customer(456)['balance_units'], 4500000)
            self.assertIsNone(self.store.get_customer_order_for_job(job)['charged_units'])
        self.mochi._stop_job(job)
        self.mochi._stop_job(job)
        row = self.store.get_job(job)
        self.assertEqual((row.status, row.success_count, row.attempted_count, row.failure_count), ('cancelled', 20, 22, 2))
        self.assertEqual(self.store.get_customer(456)['balance_units'], 8000000)
        self.assertEqual(self.store.get_customer_order_for_job(job)['charged_units'], 2000000)
        self.assertEqual(self.mochi.job_history_store.read()[0].achieved_beans, 2000)

    def test_explicit_stop_wins_over_queued_quota_result(self):
        job = self.create()
        self.dispatch(job)
        worker = self.mochi._workers[job]
        with patch.object(worker, 'request_cancel') as cancel:
            self.mochi._stop_job(job)
            cancel.assert_called_once()
            self.finish_round(job, 10)
        self.assertEqual(self.store.get_job(job).status, 'cancelled')
        self.assertNotIn(job, self.mochi._scheduled_jobs)

    def test_ordinary_failure_stops_combo_and_preserves_confirmed_success(self):
        job = self.create()
        self.dispatch(job)
        self.finish_round(job, 3, 1, 'failed')
        self.assertEqual((self.store.get_job(job).status, self.store.get_job(job).success_count), ('failed', 3))
        self.assertNotIn(job, self.mochi._scheduled_jobs)
        self.assertEqual(self.mochi.job_history_store.read()[0].achieved_beans, 300)

    def test_restart_recovers_redeem_confirmed_before_qt_progress_and_cancel_charges_it(self):
        job = self.customer()
        self.dispatch(job)
        self.record_confirmed(job, 0, 3)
        self.assertEqual(self.store.get_job(job).success_count, 0)
        # Simulate a process exit without its pending Qt progress/final signals.
        self.release(job)
        self.mochi._closing_jobs = True
        self.page.worker = None
        recovered, page = self.open_pages()
        self.assertIn(job, recovered._scheduled_jobs)
        self.assertEqual(recovered._session_jobs[job].success_count, 3)
        self.assertEqual(page.store.get_job(job).success_count, 3)
        recovered._stop_job(job)
        self.assertEqual(page.store.get_customer_order_for_job(job)['charged_units'], 300000)
        self.assertEqual(page.store.get_customer(456)['balance_units'], 9700000)
        self.assertEqual(page.store.get_job(job).status, 'cancelled')

    def test_restart_preserves_wait_deadline_and_cumulative_progress(self):
        job = self.create()
        self.dispatch(job)
        self.finish_round(job, 10, 2)
        self.release(job)
        deadline = self.store.get_job(job).scheduled_at
        self.mochi._closing_jobs = True
        self.page.worker = None
        recovered, page = self.open_pages()
        row = page.store.get_job(job)
        self.assertEqual(row.scheduled_at, deadline)
        self.assertEqual((row.success_count, row.attempted_count, row.failure_count), (10, 12, 2))
        recovered._check_scheduled_jobs(Clock.current)
        self.assertEqual(self.started, [job])
        Clock.current = schedule_time(deadline) + timedelta(seconds=1)
        recovered._check_scheduled_jobs(Clock.current)
        self.assertEqual(self.started, [job, job])
        self.assertEqual(recovered._workers[job].initial_success_count, 10)
        self.assertEqual(recovered._workers[job].runner.failure_budget_used,2)

    def test_graceful_shutdown_saves_interrupted_round_without_settling_hold(self):
        job = self.customer()
        self.dispatch(job)
        worker = self.mochi._workers[job]
        with patch.object(worker, 'isRunning', return_value=True), patch.object(worker, 'request_cancel') as cancel:
            self.assertTrue(self.mochi.shutdown_workers())
            cancel.assert_called_once()
        self.finish_round(job, 3, 1, 'cancelled')
        row = self.store.get_job(job)
        self.assertEqual((row.status, row.dispatch_state, row.success_count), ('running', 'scheduled', 3))
        self.assertIsNone(row.finalized_at)
        self.assertEqual(schedule_time(row.scheduled_at) - Clock.current, timedelta(minutes=1))
        self.assertEqual(self.store.get_customer(456)['balance_units'], 4500000)
        self.assertIsNone(self.store.get_customer_order_for_job(job)['charged_units'])

    def test_failed_persistence_of_next_round_finishes_safely_with_partial_customer_charge(self):
        job = self.customer()
        self.dispatch(job)
        with patch.object(self.mochi, 'combo_job_reschedule', side_effect=OSError('offline disk failure')):
            self.finish_round(job, 10)
        self.assertEqual(self.store.get_job(job).status, 'failed')
        self.assertEqual(self.store.get_customer_order_for_job(job)['charged_units'], 1000000)
        self.assertEqual(self.store.get_customer(456)['balance_units'], 9000000)
        self.assertNotIn(job, self.mochi._scheduled_jobs)
        self.release(job)
        Clock.current += timedelta(days=2)
        self.mochi._check_scheduled_jobs(Clock.current)
        self.assertEqual(self.started, [job])

    def test_duplicate_quota_callback_keeps_existing_deadline_and_notifies_only_once(self):
        job = self.create()
        notifications = []
        self.mochi.job_combo_waiting.connect(lambda *args: notifications.append(args))
        self.dispatch(job)
        summary = self.finish_round(job, 10, 1)
        deadline = self.store.get_job(job).scheduled_at
        Clock.current += timedelta(minutes=5)
        self.mochi._on_job_finished(summary)
        self.assertEqual(self.store.get_job(job).scheduled_at, deadline)
        self.assertEqual(len(notifications), 1)
        self.assertEqual(self.store.get_job(job).attempted_count, 11)

    def test_stop_between_full_quota_detection_and_deferred_summary_cancels_combo(self):
        for index in range(10):
            self.mochi.invite_quota_store.record_success('TEST','other-job',f'quota-{index}',
                occurred_at=Clock.current)
        job = self.create()
        self.mochi._check_scheduled_jobs(Clock.current + timedelta(seconds=1))
        self.assertFalse(self.mochi._session_jobs[job].is_terminal)
        self.mochi._stop_job(job)
        self.app.processEvents()
        self.assertEqual(self.store.get_job(job).status,'cancelled')
        self.assertNotIn(job,self.mochi._scheduled_jobs)
        self.assertEqual(self.started,[])


    def test_statistics_counts_each_finished_combo_round_once_and_keeps_whole_job_open(self):
        jid=self.customer()
        self.dispatch(jid)
        self.finish_round(jid,10)
        month=Clock.current.strftime('%Y-%m')
        report=self.store.statistics(month)
        self.assertEqual(report['customer_total'],1)
        self.assertEqual(report['total'],0)
        self.assertIsNone(self.store.get_job(jid).finalized_at)
        self.store=TelegramUserStore(self.store.path)
        self.assertEqual(self.store.statistics(month)['customer_total'],1)

    def test_statistics_distinguishes_repeated_quota_only_combo_rounds(self):
        jid=self.create()
        quota=SimpleNamespace(limit_reached=True,count=10,next_available_at=None)
        with patch.object(self.mochi,'_invite_quota_snapshot',return_value=quota):
            for _ in range(2):
                self.dispatch(jid)
                self.assertIn(jid,self.mochi._scheduled_jobs)
        with self.store._connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM job_runs WHERE job_id=?',(jid,)).fetchone()[0],2)

    def test_next_combo_round_and_restart_keep_total_failure_budget(self):
        jid=self.create();self.dispatch(jid);self.finish_round(jid,10,2);self.release(jid)
        self.dispatch(jid)
        self.assertEqual(self.mochi._workers[jid].runner.failure_budget_used,2)
        self.finish_round(jid,10,2);self.release(jid)
        self.mochi._closing_jobs=True;self.page.worker=None
        recovered,page=self.open_pages()
        Clock.current=schedule_time(page.store.get_job(jid).scheduled_at)+timedelta(seconds=1)
        recovered._check_scheduled_jobs(Clock.current)
        self.assertEqual(recovered._workers[jid].runner.failure_budget_used,4)


if __name__ == '__main__':
    unittest.main()
