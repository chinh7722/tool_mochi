"""Offline HTTP/Qt regressions for desktop and Telegram job synchronization."""
from datetime import datetime, timedelta, timezone
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
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, QSettings, Signal, Slot
from PySide6.QtWidgets import QApplication
from core.platforms.mochichat.runners.invite_referral import MochiInviteJobSummary
from core.remote.protocol import MutationJournal
from core.remote.runtime import Runtime
from core.remote.server import ManagementServer
from core.telegram_bot import TelegramAccess, TelegramJobRequest
from gui.pages.mochi_chat import MochiWorkerThread
from gui.remote_window import RemoteMainWindow


class TestBridge(QObject):
    received = Signal(object)

    def __init__(self, runtime, journal):
        super().__init__()
        self.runtime, self.journal = runtime, journal
        self.commands = []
        self.reads = 0
        self.after_command = None
        self.received.connect(self.handle)

    @Slot(object)
    def handle(self, command):
        try:
            if command.action == 'state':
                self.reads += 1
                command.result = {'ok': True, 'state': self.runtime.state()}
            else:
                self.commands.append(command.action)
                result = self.journal.execute(command.request_id, command.action, command.payload,
                    lambda: self.runtime.command(command.action, command.payload))
                if self.after_command:
                    self.after_command(command.action, result)
                command.result = {'ok': True, 'result': result}
        except Exception as exc:
            command.result = {'ok': False, 'error': type(exc).__name__}
        finally:
            command.done.set()


class JobSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {'HOSHINO_DATA_DIR': str(self.directory)})
        self.environment.start()
        self.runtime = Runtime(self.directory)
        self.runtime.mochi.input_api_key.setText('offline-shop-key')
        self.runtime.mochi.chk_use_proxy.setChecked(False)
        self.runtime.mochi.spin_job_concurrency.setValue(1)
        # No runner, shop request, browser or account login executes in this suite.
        self.start_patch = patch.object(MochiWorkerThread, 'start')
        self.start_patch.start()
        self.bridge = TestBridge(self.runtime, MutationJournal(self.directory / 'operations.sqlite3'))
        self.token = 'offline-management-token-' + 't' * 40
        self.server = ManagementServer(('127.0.0.1', 0), self.token, self.bridge.received.emit)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        preferences = QSettings(str(self.directory / 'desktop.ini'), QSettings.Format.IniFormat)
        with patch('gui.remote_shell.QSettings', return_value=preferences), \
             patch('gui.remote_shell.Path.read_text', return_value='{}'):
            self.window = RemoteMainWindow()
        self.window.url.setText(f'http://127.0.0.1:{self.server.server_port}')
        self.window.token.setText(self.token)
        self.window.timer.setInterval(40)
        self.window.connect_server()
        self.until(lambda: self.window._connected and not self.window._workers,
                   'initial authenticated state did not reach the manager')

    def tearDown(self):
        self.window.timer.stop()
        self.window.close()
        self.until(lambda: not self.window._workers, 'desktop request did not finish during cleanup')
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.runtime.telegram.worker = None
        self.runtime.telegram._accepting_requests = False
        for job_id in list(self.runtime.mochi._workers):
            self.runtime.mochi._on_worker_stopped(job_id)
        self.runtime.shutdown()
        self.start_patch.stop()
        for page in (self.window, self.runtime.telegram, self.runtime.mochi,
                     self.runtime.proxy, self.runtime.mail_proxy):
            page.deleteLater()
        self.app.processEvents()
        self.environment.stop()
        self.temp.cleanup()

    def until(self, predicate, message, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail(message + '; status=' + self.window.status.text())

    def desktop_create(self, code='DESKTOP',points=100):
        self.window.job_code.setText(code)
        self.window.job_points.setValue(points)
        self.window.job_customer.setText('Same customer')
        before = set(self.runtime.mochi._session_jobs)
        self.window.create_job()
        self.until(lambda: bool(set(self.runtime.mochi._session_jobs) - before),
                   'desktop POST did not create a server job')
        job_id = (set(self.runtime.mochi._session_jobs) - before).pop()
        self.until(lambda: any(row['job_id'] == job_id for row in self.window.jobs._rows),
                   'new desktop job was not synchronized into the jobs table')
        return job_id

    def bot_create(self, update_id=100, code='FROMBOT'):
        store = self.runtime.telegram.store
        store.upsert_user(123, 'Offline partner')
        store.set_user_mail_keys(123, 'offline-private-gmail', 'offline-private-trieu')
        self.bot = SimpleNamespace(fingerprint='offline-fingerprint', access=TelegramAccess((123,)),
            reply=Mock(), complete_request=Mock(), request_stop=Mock(), isRunning=Mock(return_value=False))
        self.runtime.telegram.worker = self.bot
        self.runtime.telegram._accepting_requests = True
        self.runtime.telegram._on_job_requested((self.bot.fingerprint,
            TelegramJobRequest(update_id, 123, code, 100, 'Same customer')))
        stored = store.lookup_request(self.bot.fingerprint, update_id)
        self.assertIsNotNone(stored)
        return stored.job_id

    def finish(self, job_id, status='completed'):
        record = self.runtime.mochi._session_jobs[job_id]
        successes = record.target_count if status == 'completed' else 0
        self.runtime.mochi._on_job_finished(MochiInviteJobSummary(job_id, record.invite_code,
            record.target_count, 1, successes, int(not successes), 0, status, ()))
        self.runtime.mochi._on_worker_stopped(job_id)

    def seed_finished(self, count=22):
        oldest = datetime.now(timezone.utc) - timedelta(days=1)
        for index in range(count):
            job_id = self.runtime.command('job.create', {'code': f'OLD{index}', 'points': 100})['job_id']
            self.runtime.mochi._session_jobs[job_id].created_at = oldest + timedelta(minutes=index)
            self.finish(job_id)

    def test_desktop_job_is_visible_selected_and_opens_running_jobs_without_duplicate_post(self):
        self.seed_finished()
        job_id = self.desktop_create()
        self.assertEqual(self.window.jobs._rows[0]['job_id'], job_id)
        self.assertEqual(self.window.selected(self.window.jobs, 'job_id'), job_id)
        self.assertEqual((self.window.pages.currentIndex(), self.window.workspace_tabs.currentIndex(),
                          self.window.mochi_tabs.currentIndex()), (1, 0, 1))
        reads = self.bridge.reads
        self.until(lambda: self.bridge.reads >= reads + 3, 'automatic polling stopped after desktop create')
        self.assertEqual(self.bridge.commands.count('job.create'), 1)
        self.assertEqual(len(self.runtime.mochi._session_jobs), 23)

    def test_bot_job_arrives_via_automatic_poll_above_old_finished_rows_without_changing_draft(self):
        self.seed_finished()
        self.window.job_customer.setText('Unsaved desktop note')
        self.window.settings_fields['product_ids'].setText('unsaved-product-order')
        job_id = self.bot_create()
        self.until(lambda: any(row['job_id'] == job_id for row in self.window.jobs._rows),
                   'Telegram job did not arrive through state polling')
        self.assertEqual(self.window.jobs._rows[0]['job_id'], job_id)
        self.assertEqual(self.window.jobs._rows[0]['creator_chat_id'], 123)
        self.assertEqual(self.window.job_customer.text(), 'Unsaved desktop note')
        self.assertEqual(self.window.settings_fields['product_ids'].text(), 'unsaved-product-order')
        self.assertNotIn('job.create', self.bridge.commands)

    def test_job_finishing_before_first_refresh_remains_visible_with_quota_result(self):
        self.seed_finished()
        self.bridge.after_command = lambda action, result: (
            self.finish(result['job_id'], 'invite_limit_reached') if action == 'job.create' else None)
        job_id = self.desktop_create('FASTFAIL')
        row = next(row for row in self.window.jobs._rows if row['job_id'] == job_id)
        self.assertEqual(row['status'], 'Đạt giới hạn 24h')
        self.assertEqual(self.window.jobs._rows[0]['job_id'], job_id)
        self.assertEqual(self.window.selected(self.window.jobs, 'job_id'), job_id)
        self.assertEqual(self.bridge.commands.count('job.create'), 1)

    def test_new_terminal_bot_job_stays_visible_while_older_active_job_keeps_priority(self):
        active = self.desktop_create('RUNNING')
        bot = self.bot_create(code='QUOTA')
        self.finish(bot, 'invite_limit_reached')
        self.until(lambda: any(row['job_id'] == bot for row in self.window.jobs._rows),
                   'fast-finished bot job was omitted from polling')
        self.assertEqual([row['job_id'] for row in self.window.jobs._rows[:2]], [active, bot])
        self.assertEqual(self.window.jobs._rows[1]['status'], 'Đạt giới hạn 24h')

    def test_selection_follows_job_id_when_new_bot_job_reorders_same_customer(self):
        first = self.desktop_create('FIRST')
        self.until(lambda: not self.window._mutating, 'desktop POST stayed busy')
        row = next(index for index, item in enumerate(self.window.jobs._rows) if item['job_id'] == first)
        self.window.jobs.selectRow(row)
        second = self.bot_create(code='SECOND')
        self.until(lambda: any(item['job_id'] == second for item in self.window.jobs._rows),
                   'second job did not arrive')
        self.assertEqual(self.window.jobs._rows[0]['job_id'], second)
        self.assertEqual(self.window.selected(self.window.jobs, 'job_id'), first)


    def test_log_cursor_appends_new_lines_live_through_actual_http_without_reopening(self):
        jid=self.desktop_create('LIVELOG')
        self.until(lambda:not self.window._mutating,'create busy')
        self.runtime.mochi._append_log_line('FIRST LIVE LINE',job_id=jid)
        self.window.open_job_logs(jid)
        self.until(lambda:'FIRST LIVE LINE' in self.window.logs.toPlainText(),'initial log missing')
        self.runtime.mochi._append_log_line('SECOND LIVE LINE',job_id=jid)
        self.until(lambda:'SECOND LIVE LINE' in self.window.logs.toPlainText(),'log did not auto-refresh')
        self.assertEqual(self.window.logs.toPlainText().count('FIRST LIVE LINE'),1)
        cursor=self.window._log_cursor
        result=self.runtime.command('job.logs',dict(job_id=jid,cursor=cursor,epoch=self.window._log_epoch))
        self.assertEqual(result['lines'],[])
        self.assertFalse(result['reset'])
        self.runtime.log_store.epoch='new-runtime'
        self.window.poll_logs()
        self.until(lambda:self.window._log_epoch=='new-runtime','log epoch not reset')
        self.assertEqual(self.window.logs.toPlainText().count('FIRST LIVE LINE'),1)

    def test_continue_failed_job_preserves_source_and_creates_only_remaining_bean_for_same_owner(self):
        jid=self.desktop_create('REMAIN',points=1000)
        self.until(lambda:not self.window._mutating,'create busy')
        m=self.runtime.mochi
        m._on_job_finished(MochiInviteJobSummary(jid,'REMAIN',10,5,2,3,0,'failed',()))
        m._on_worker_stopped(jid)
        result=self.runtime.command('job.continue',{'job_id':jid})
        child=self.runtime.telegram.store.get_job(result['job_id'])
        self.assertEqual((child.chat_id,child.requested_beans,child.target_count),(-1,800,8))
        self.assertEqual(self.runtime.telegram.store.get_job(jid).success_count,2)
        replay=self.runtime.command('job.continue',{'job_id':jid})
        self.assertEqual(replay['job_id'],child.job_id)
        row=next(r for r in self.runtime.state()['jobs'] if r['job_id']==jid)
        self.assertFalse(row['can_continue'])

    def test_stop_ui_no_and_yes_capture_exact_id_while_backend_never_prompts(self):
        from PySide6.QtWidgets import QMessageBox
        jid=self.desktop_create('CONFIRM')
        self.until(lambda:not self.window._mutating,'create busy')
        count=self.bridge.commands.count('job.stop')
        with patch('gui.remote_window.QMessageBox.question',return_value=QMessageBox.StandardButton.No):
            self.window.confirm_stop_job(jid)
        self.assertEqual(self.bridge.commands.count('job.stop'),count)
        with patch('gui.remote_window.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):
            self.window.confirm_stop_job(jid)
        self.until(lambda:self.bridge.commands.count('job.stop')==count+1,'confirmed stop missing')

    def test_statistics_records_one_completed_run_and_renders_person_totals(self):
        jid=self.desktop_create('STATS',points=1000)
        self.until(lambda:not self.window._mutating,'create busy')
        m=self.runtime.mochi
        summary=MochiInviteJobSummary(jid,'STATS',10,10,10,0,0,'completed',())
        m._on_job_finished(summary);m._on_job_finished(summary)
        m._on_worker_stopped(jid)
        self.window.activate(6)
        panel=self.window.statistics_panel
        self.until(lambda:getattr(panel,'total',0)==1,'statistics not displayed')
        self.assertEqual(panel.history.rowCount(),2)  # job and owner total
        report=self.runtime.command('statistics.get',dict(month=panel.month.date().toString('yyyy-MM')))
        self.assertEqual((report['total'],report['customer_total']),(1,1))
        self.assertEqual(len(panel.charts),4)
        self.assertIn('TỔNG',panel.history.item(1,0).text())


    def test_price_milestone_saved_over_http_is_snapshotted_for_local_job(self):
        self.window.send('pricing.save',dict(prices=[dict(beans=2000,price_units=1500000)]))
        self.until(lambda:not self.window._mutating,'price save stayed busy')
        jid=self.desktop_create('PRICE',points=1000)
        import json
        job=self.runtime.telegram.store.get_job(jid)
        self.assertEqual(json.loads(job.schedule_data)['price_total_units'],750000)
        self.assertEqual(job.sale_rate_units,75000)

    def test_temporary_clear_uses_confirmation_and_keeps_permanent_job_and_log(self):
        from PySide6.QtWidgets import QMessageBox
        jid=self.desktop_create('TEMP',points=1000)
        self.until(lambda:not self.window._mutating,'create busy')
        m=self.runtime.mochi;m._append_log_line('KEEP TEMP LOG',job_id=jid)
        m._on_job_finished(MochiInviteJobSummary(jid,'TEMP',10,3,0,3,0,'failed',()))
        m._on_worker_stopped(jid)
        self.window.mochi_tabs.setCurrentIndex(3)
        panel=self.window.temporary_panel
        self.until(lambda:panel.total==1,'temporary outcome missing')
        self.assertTrue(panel.table._rows[0]['can_continue'])
        with patch('gui.remote_management_pages.QMessageBox.question',return_value=QMessageBox.StandardButton.No):panel.clear()
        self.assertNotIn('history.temporary.clear',self.bridge.commands)
        with patch('gui.remote_management_pages.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):panel.clear()
        self.until(lambda:panel.total==0,'temporary outcome not hidden')
        self.assertIsNotNone(self.runtime.telegram.store.get_job(jid))
        self.assertIn('KEEP TEMP LOG',self.runtime.command('job.logs',dict(job_id=jid))['lines'])
        month=panel.window.statistics_panel.month.date().toString('yyyy-MM')
        self.assertEqual(self.runtime.command('statistics.get',dict(month=month))['total'],1)

    def test_log_survives_real_runtime_recreation_while_desktop_keeps_same_cursor(self):
        self.runtime.mochi._append_log_line('COMBO DAY ONE',job_id='combo-log')
        self.window.open_job_logs('combo-log')
        self.until(lambda:'COMBO DAY ONE' in self.window.logs.toPlainText(),'first log missing')
        epoch=self.window._log_epoch
        self.runtime.shutdown()
        old=self.runtime;self.runtime=Runtime(self.directory);self.bridge.runtime=self.runtime
        self.runtime.mochi._append_log_line('COMBO DAY TWO',job_id='combo-log')
        self.until(lambda:'COMBO DAY TWO' in self.window.logs.toPlainText(),'persisted combo log missing')
        self.assertEqual(self.window._log_epoch,epoch)
        self.assertEqual(self.window.logs.toPlainText().count('COMBO DAY ONE'),1)
        for page in (old.telegram,old.mochi,old.proxy,old.mail_proxy):page.deleteLater()


if __name__ == '__main__':
    unittest.main()
