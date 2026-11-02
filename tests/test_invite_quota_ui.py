"""Offline UI/startup integration for rolling invite limits before mail purchase."""
from datetime import datetime,timedelta,timezone
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from core.finance import money_units
from core.telegram_bot import TelegramAccess,TelegramJobRequest,TelegramCustomerRequest
from core.telegram_users import TelegramUserStore
from gui.pages.mochi_chat import MochiChatPage,MochiWorkerThread
from gui.pages.telegram import TelegramPage


class QuotaUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.directory=Path(self.temp.name)
        self.settings=QSettings(str(self.directory/'settings.ini'),QSettings.Format.IniFormat)
        self.mochi=MochiChatPage(settings=self.settings)
        self.store=TelegramUserStore(self.directory/'telegram.sqlite3')
        self.page=TelegramPage(self.settings,self.mochi,user_store=self.store)
        self.bot=SimpleNamespace(fingerprint='offline',access=TelegramAccess((123,)),
            reply=Mock(),complete_request=Mock(),request_stop=Mock(),isRunning=Mock(return_value=False))
        self.store.upsert_user(123,'Partner')
        self.store.set_user_mail_keys(123,'fake-gmail-api','fake-trieu-api')
        self.page.worker=self.bot
        self.page._accepting_requests=True
        self.settings.setValue('finance/sale_rate_per_100','1000')

    def tearDown(self):
        self.page.worker=None
        self.page.shutdown_workers()
        self.mochi.shutdown_workers()
        self.page.deleteLater()
        self.mochi.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def fill_limit(self,code='TEST'):
        for index in range(10):
            self.mochi.invite_quota_store.record_success(code,'source-job',f'offline-account-{index}')

    def test_full_code_finishes_without_mail_key_proxy_or_worker(self):
        self.fill_limit()
        self.mochi.chk_use_proxy.setChecked(True)
        self.mochi.input_api_key.clear()
        summaries=[]
        self.mochi.job_finished.connect(summaries.append)
        with patch.object(MochiWorkerThread,'start') as start, \
             patch('gui.pages.mochi_chat.MochiInviteRunner') as runner:
            jid=self.mochi._on_start_clicked(code='test',points=1000)
            self.assertIsNotNone(jid)
            self.assertEqual(summaries,[])
            self.app.processEvents()
        start.assert_not_called()
        runner.assert_not_called()
        self.assertEqual(len(summaries),1)
        self.assertEqual((summaries[0].status,summaries[0].success_count),('invite_limit_reached',0))
        self.assertEqual(self.mochi._session_jobs[jid].status,'Đạt giới hạn 24h')
        self.assertIsNotNone(self.store.get_job(jid).finalized_at)

    def test_operator_limit_reply_keeps_owner_mapping_and_does_not_create_worker(self):
        self.fill_limit()
        with patch.object(MochiWorkerThread,'start') as start:
            self.page._on_job_requested((self.bot.fingerprint,TelegramJobRequest(42,123,'TEST',1000)))
            stored=self.store.lookup_request(self.bot.fingerprint,42)
            self.assertIsNotNone(stored)
            self.assertIn(stored.job_id,self.page._job_chats)
            self.app.processEvents()
        start.assert_not_called()
        replies=[call.args[1] for call in self.bot.reply.call_args_list]
        self.assertTrue(any('24 giờ' in reply for reply in replies))
        self.assertTrue(all(call.args[0]==123 for call in self.bot.reply.call_args_list))
        self.assertEqual(self.store.get_job(stored.job_id).success_count,0)

    def test_customer_limit_refunds_entire_hold_once(self):
        self.fill_limit()
        self.store.ensure_customer(456,'Customer')
        self.store.set_customer_balance(456,money_units('10000'),'offline funding',expected_units=0)
        request=TelegramCustomerRequest(51,456,'job','Customer','TEST',1000,'')
        with patch.object(MochiWorkerThread,'start') as start:
            self.page._on_customer_requested((self.bot.fingerprint,request))
            job=self.store.lookup_request(self.bot.fingerprint,51)
            self.assertIsNotNone(job)
            self.assertEqual(self.store.get_customer(456)['balance_units'],0)
            self.app.processEvents()
        start.assert_not_called()
        order=self.store.get_customer_order_for_job(job.job_id)
        self.assertEqual(order['charged_units'],0)
        self.assertEqual(self.store.get_customer(456)['balance_units'],money_units('10000'))
        self.store.finish_job(job.job_id,'failed',0)
        self.assertEqual(self.store.get_customer(456)['balance_units'],money_units('10000'))

    def test_future_schedule_is_saved_even_while_current_quota_is_full(self):
        self.fill_limit()
        when=datetime.now(timezone.utc)+timedelta(days=2)
        with patch.object(MochiWorkerThread,'start') as start:
            jid=self.mochi._on_start_clicked(code='TEST',points=100,scheduled_at=when)
            self.app.processEvents()
        self.assertIsNotNone(jid)
        self.assertIn(jid,self.mochi._scheduled_jobs)
        self.assertEqual(self.store.get_job(jid).dispatch_state,'scheduled')
        start.assert_not_called()

    def test_due_schedule_rechecks_limit_before_resources_and_finishes_normally(self):
        self.fill_limit()
        when=datetime.now(timezone.utc)+timedelta(minutes=5)
        jid=self.mochi._on_start_clicked(code='TEST',points=100,scheduled_at=when)
        with patch.object(MochiWorkerThread,'start') as start:
            self.mochi._check_scheduled_jobs(now=when+timedelta(seconds=1))
            self.app.processEvents()
        start.assert_not_called()
        self.assertNotIn(jid,self.mochi._scheduled_jobs)
        self.assertEqual(self.mochi._session_jobs[jid].status,'Đạt giới hạn 24h')
        self.assertEqual(self.store.get_job(jid).status,'failed')

    def test_store_read_error_stops_before_job_registration_or_purchase(self):
        self.mochi.input_api_key.setText('fake-shop-key')
        with patch.object(self.mochi.invite_quota_store,'snapshot',side_effect=OSError('private path')), \
             patch.object(MochiWorkerThread,'start') as start:
            jid=self.mochi._on_start_clicked(code='TEST',points=100)
        self.assertIsNone(jid)
        start.assert_not_called()
        self.assertIn('bộ đếm',self.mochi.validation_label.text())
        self.assertNotIn('private path',self.mochi.validation_label.text())
        self.assertEqual(self.store.list_jobs(-1),())

    def test_constructor_failure_is_fail_closed_and_does_not_use_untracked_runner(self):
        settings=QSettings(str(self.directory/'broken'/'settings.ini'),QSettings.Format.IniFormat)
        with patch('gui.pages.mochi_chat.InviteQuotaStore',side_effect=OSError('private failure')):
            page=MochiChatPage(settings=settings)
        try:
            page.input_api_key.setText('fake-key')
            with patch.object(MochiWorkerThread,'start') as start:
                self.assertIsNone(page._on_start_clicked(code='TEST',points=100))
            start.assert_not_called()
            self.assertTrue(page._invite_quota_error)
        finally:
            page.shutdown_workers();page.deleteLater();self.app.processEvents()

    def test_runner_receives_shared_quota_store(self):
        self.mochi.input_api_key.setText('fake-shop-key')
        with patch.object(MochiWorkerThread,'start'):
            jid=self.mochi._on_start_clicked(code='TEST',points=100)
        self.assertIsNotNone(jid)
        self.assertIs(self.mochi._workers[jid].runner.invite_quota_store,self.mochi.invite_quota_store)

    def test_history_query_and_startup_count_successes_in_all_final_states_once(self):
        for index,(status,count) in enumerate((('completed',4),('failed',3),('cancelled',3))):
            jid=f'legacy-{index}'
            self.store.record_local_job(jid,'OLDER',1000,10)
            self.store.finish_job(jid,status,count)
        history=self.store.invite_quota_history()
        self.assertEqual(sum(row['success_count'] for row in history),10)
        another=MochiChatPage(settings=self.settings)
        page=TelegramPage(self.settings,another,user_store=self.store)
        try:
            self.assertEqual(another.invite_quota_store.snapshot('older').count,10)
            self.assertTrue(another.invite_quota_store.snapshot('OLDER').limit_reached)
            another.invite_quota_store.seed_legacy_jobs(history)
            self.assertEqual(another.invite_quota_store.snapshot('OLDER').count,10)
        finally:
            page.shutdown_workers();another.shutdown_workers()
            page.deleteLater();another.deleteLater();self.app.processEvents()

    def test_startup_seed_failure_blocks_new_jobs(self):
        another=MochiChatPage(settings=self.settings)
        with patch.object(self.store,'invite_quota_history',side_effect=OSError('private db error')):
            page=TelegramPage(self.settings,another,user_store=self.store)
        try:
            with patch.object(MochiWorkerThread,'start') as start:
                self.assertIsNone(another._on_start_clicked(code='TEST',points=100))
            start.assert_not_called()
            self.assertIn('khôi phục',another._invite_quota_error)
        finally:
            page.shutdown_workers();another.shutdown_workers()
            page.deleteLater();another.deleteLater();self.app.processEvents()


if __name__=='__main__':
    unittest.main()
