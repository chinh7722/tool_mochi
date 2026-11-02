"""Offline terminal history durability, CSV recovery, migration and Qt integration."""
import csv
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor,ProcessPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from core.job_history import JobHistoryEntry,JobHistoryStore
from core.telegram_users import TelegramUserStore
from core.platforms.mochichat.runners.invite_referral import MochiInviteJobSummary
from gui.pages.mochi_chat import MochiChatPage,_MochiJobSessionRecord,MochiWorkerThread
from gui.pages.telegram import TelegramPage


def entry(job='job1',**values):
    return replace(JobHistoryEntry(job,'','Local',None,'Khách, tiếng Việt','TEST',
        '2026-11-01T18:00:00+07:00','2026-11-01T18:05:00+07:00','completed',10,10,10,0,1000,1000),**values)


def process_write(path,prefix):
    JobHistoryStore(path).record_many(entry(f'{prefix}_{index}') for index in range(5))
    return True


class CsvHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'job_history.csv'
        self.store=JobHistoryStore(self.path)

    def test_csv_roundtrip_has_vietnamese_headers_bom_and_exact_metadata(self):
        value=entry(creator_name='Đối tác A',creator_chat_id=5837675348,order_id='DH001')
        self.store.record(value)
        self.assertTrue(self.path.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertIn('Người tạo job',self.path.read_text(encoding='utf-8-sig'))
        self.assertEqual(JobHistoryStore(self.path).read(),(value,))

    def test_same_terminal_job_is_written_once_and_original_snapshot_is_kept(self):
        self.store.record(entry())
        self.store.record(entry(status='failed',creator_name='Changed',finished_at='2026-11-02T10:00:00+07:00'))
        self.assertEqual(self.store.read(),(entry(),))

    def test_untrusted_excel_cells_are_escaped_and_roundtrip_without_loss(self):
        for index,name in enumerate(('=1+1',' +SUM(A1:A2)','@IMPORT','-Test',"'=Name",'\t=1','Tên, có\nxuống dòng')):
            self.store.record(entry(f'job{index}',customer_name=name,creator_name=name))
        rows=self.store.read()
        self.assertEqual({r.customer_name for r in rows},{'=1+1',' +SUM(A1:A2)','@IMPORT','-Test',"'=Name",'\t=1','Tên, có\nxuống dòng'})
        with self.path.open(encoding='utf-8-sig',newline='') as stream:
            raw=list(csv.DictReader(stream))
        self.assertTrue(raw[0]['Khách hàng / Job'].startswith("'="))

    def test_locked_csv_keeps_existing_file_and_recovers_pending_record_after_restart(self):
        self.store.record(entry())
        before=self.path.read_bytes()
        from core import job_history
        atomic=job_history._atomic_bytes
        def fail_csv(path,data):
            if path==self.path:
                raise PermissionError('Excel holds the CSV')
            return atomic(path,data)
        with patch.object(job_history,'_atomic_bytes',side_effect=fail_csv):
            with self.assertRaises(PermissionError):
                self.store.record(entry('job2',status='failed'))
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(len(self.store.read()),2)
        restored=JobHistoryStore(self.path)
        restored.sync()
        self.assertEqual(len(restored.read()),2)
        self.assertEqual(list(restored.pending_directory.glob('*.json')),[])

    def test_waiting_for_csv_writer_still_saves_a_durable_pending_receipt(self):
        with patch.object(self.store,'_locked',side_effect=OSError('Writer busy')):
            with self.assertRaises(OSError):
                self.store.record(entry())
        self.assertEqual(len(list(self.store.pending_directory.glob('*.json'))),1)
        JobHistoryStore(self.path).sync()
        self.assertEqual(self.store.read(),(entry(),))

    def test_bad_existing_csv_is_never_overwritten_and_new_job_remains_pending(self):
        self.path.write_text('unknown,columns\nold,data\n',encoding='utf-8')
        before=self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.record(entry())
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(len(list(self.store.pending_directory.glob('*.json'))),1)

    def test_parallel_threads_do_not_lose_or_duplicate_job_rows(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda index:JobHistoryStore(self.path).record(entry(f'job{index}')),range(20)))
        self.assertEqual(len(self.store.read()),20)

    def test_parallel_processes_use_the_same_csv_lock(self):
        with ProcessPoolExecutor(max_workers=2) as pool:
            results=[pool.submit(process_write,str(self.path),prefix) for prefix in ('first','second')]
            self.assertTrue(all(result.result(timeout=20) for result in results))
        self.assertEqual(len(self.store.read()),10)

    def test_running_job_is_not_a_terminal_history_record(self):
        with self.assertRaises(ValueError):
            entry(status='running')


class JobHistoryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings=QSettings(str(Path(self.temp.name)/'settings.ini'),QSettings.IniFormat)
        self.page=MochiChatPage(settings=self.settings)
        self.addCleanup(self.page.close)

    def add_job(self,job='job1',**values):
        record=_MochiJobSessionRecord(job,'TEST',10,**values)
        self.page._session_jobs[job]=record
        return record

    def finish(self,job='job1',status='completed',success=10):
        self.page._on_job_finished(MochiInviteJobSummary(job,'TEST',10,success,success,
            0 if status=='completed' else 1,0,status,()))

    def test_new_tab_selects_persistent_history_and_preserves_log_tab_index(self):
        self.assertEqual(self.page.job_tabs.tabText(3),'Lịch sử job')
        self.page.job_tabs.setCurrentIndex(3)
        self.assertIs(self.page.job_stack.currentWidget(),self.page.history_page)
        self.page.show_job_logs('missing')
        self.assertEqual(self.page.job_tabs.currentIndex(),2)

    def test_success_and_failure_persist_after_reopening_without_running_jobs(self):
        self.add_job()
        self.add_job('job2',creator_name='Người gửi',creator_chat_id=123)
        self.finish()
        self.finish('job2','failed',3)
        reopened=MochiChatPage(settings=self.settings)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.history_page.table.rowCount(),2)
        self.assertEqual(reopened._workers,{})
        self.assertEqual(reopened._session_jobs,{})
        saved={r.job_id:r for r in reopened.job_history_store.read()}
        self.assertEqual(saved['job2'].achieved_beans,300)
        self.assertEqual(saved['job2'].creator_chat_id,123)

    def test_cancelled_summary_keeps_confirmed_progress_in_history(self):
        record=self.add_job()
        record.success_count=4
        record.attempted_count=6
        self.finish(status='cancelled',success=0)
        saved=self.page.job_history_store.read()[0]
        self.assertEqual((saved.status,saved.success_count,saved.attempted_count,saved.achieved_beans),('cancelled',4,6,400))

    def test_unhandled_error_persists_partial_progress_but_no_sensitive_error_text(self):
        record=self.add_job()
        record.success_count=2
        self.page._on_job_error('token=private-secret password=hidden','job1')
        text=self.page.job_history_store.path.read_text(encoding='utf-8-sig')
        self.assertNotIn('private-secret',text)
        self.assertNotIn('password',text)
        self.assertEqual(self.page.job_history_store.read()[0].achieved_beans,200)

    def test_removing_a_finished_session_job_keeps_its_history(self):
        self.add_job()
        self.finish()
        self.page._delete_job('job1')
        self.assertFalse(self.page._session_jobs)
        self.assertEqual(len(self.page.job_history_store.read()),1)

    def test_search_and_status_filter_match_creator_chatid_and_job_code(self):
        self.add_job(creator_name='Local')
        self.add_job('job2',creator_name='Khách Linh',creator_chat_id=123)
        self.finish()
        self.finish('job2','failed',2)
        view=self.page.history_page
        view.search.setText('123')
        self.assertEqual(view.table.rowCount(),1)
        view.filter_status.setCurrentIndex(view.filter_status.findData('completed'))
        self.assertEqual(view.table.rowCount(),0)
        view.search.clear()
        self.assertEqual(view.table.rowCount(),1)

    def test_history_write_failure_does_not_block_job_result_signals(self):
        self.add_job()
        receiver=Mock()
        self.page.job_finished.connect(receiver)
        with patch.object(self.page.job_history_store,'record',side_effect=OSError('disk error')):
            self.finish()
        receiver.assert_called_once()
        self.assertIn('Chưa cập nhật',self.page.history_page.status.text())

    def test_old_sqlite_history_backfill_preserves_owner_and_customer_order_id_once(self):
        store=TelegramUserStore(Path(self.temp.name)/'users.sqlite3')
        store.record_local_job('old_local','LOCAL',100,1)
        store.finish_job('old_local','completed',1)
        store.upsert_user(123,'Partner','partner')
        store.record_job('old_remote',123,'REMOTE',100,1)
        store.finish_job('old_remote','failed',0,1,1)
        store.record_job('still_running',123,'WAIT',100,1)
        store.ensure_customer(456,'Khách cũ')
        store.set_customer_balance(456,100000,'Fixture',expected_units=0)
        store.record_customer_order('old_customer',456,'PAID',100,1,'fp',1,100000)
        store.finish_job('old_customer','completed',1)
        order_id=store.get_customer_order_for_job('old_customer')['order_id']
        telegram=TelegramPage(self.settings,self.page,user_store=store)
        self.addCleanup(telegram.close)
        rows={r.job_id:r for r in self.page.job_history_store.read()}
        self.assertEqual(set(rows),{'old_local','old_remote','old_customer'})
        self.assertEqual(rows['old_local'].creator_name,'Local')
        self.assertEqual(rows['old_remote'].creator_chat_id,123)
        self.assertEqual(rows['old_remote'].customer_name,'')
        self.assertEqual(rows['old_customer'].order_id,order_id)
        self.page.job_history_store.record_many(store.finished_job_history())
        self.assertEqual(len(self.page.job_history_store.read()),3)

    def test_telegram_customer_order_id_is_saved_with_the_terminal_snapshot(self):
        self.page.job_order_lookup=lambda _job:{'order_id':'DH_TEST'}
        self.add_job(creator_name='Khách A',creator_chat_id=123)
        self.finish()
        self.assertEqual(self.page.job_history_store.read()[0].order_id,'DH_TEST')


if __name__=='__main__':
    unittest.main(verbosity=2)
