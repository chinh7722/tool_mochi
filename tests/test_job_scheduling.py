"""One-time scheduling, durable ownership/holds, Telegram commands and due-time queueing."""
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QApplication
from core.job_schedule import VIETNAM_TIME, schedule_time, schedule_storage
from core.telegram_bot import (TelegramAccess, TelegramBotService, TelegramJobRequest,
    TelegramCustomerRequest, TelegramControlRequest, parse_scheduled_job_command)
from core.telegram_users import TelegramUserStore, LOCAL_USER_ID
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread
from gui.pages.telegram import TelegramPage
import test_job_queue as queue_fixtures
from test_telegram_bot import update


def future():
    return (datetime.now(VIETNAM_TIME) + timedelta(days=1)).replace(second=0, microsecond=0)


def command(when=None):
    return '/hen TEST 100 ' + (when or future()).strftime('%d/%m/%Y %H:%M') + ' Khách A'


class CommandTests(unittest.TestCase):
    def test_command_parses_code_points_customer_and_explicit_vietnam_time(self):
        when = future()
        self.assertEqual(parse_scheduled_job_command(command(when)), ('TEST', 100, 'Khách A', when.isoformat()))
        self.assertEqual(schedule_time(datetime(2026, 1, 2, 1, tzinfo=timezone.utc)).hour, 8)

    def test_invalid_past_dates_and_amounts_are_rejected(self):
        for text in ('/hen', '/hen TEST 100 31/02/2099 12:30', '/hen TEST 100 01/01/2020 12:30',
            '/hen TEST 100 31/12/2099 25:00', '/hen TEST 0 31/12/2099 12:00', '/hen BAD;CODE 100 31/12/2099 12:00'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_scheduled_job_command(text)

    def test_operator_and_customer_schedule_commands_use_existing_separate_routes(self):
        operators, customers = [], []
        bot = TelegramBotService(None, TelegramAccess((123,)), operators.append, on_customer=customers.append)
        bot.handle_update(update(1,123,command()))
        bot.handle_update(update(2,456,command()))
        self.assertEqual((len(operators),len(customers)),(1,1))
        self.assertIsNotNone(operators[0].scheduled_at)
        self.assertEqual(customers[0].action,'job')
        self.assertIsNotNone(customers[0].scheduled_at)
        self.assertFalse(bot.access.allowed(456))

    def test_schedule_command_replay_group_sender_and_other_bot_mention_are_rejected(self):
        requests=[]
        bot=TelegramBotService(None,TelegramAccess((123,)),requests.append)
        bot.username='ours'
        first=update(1,123,command())
        bot.handle_update(first)
        bot.handle_update(first)
        bot.handle_update(update(2,123,command(),chat={'id':123,'type':'group'}))
        bot.handle_update(update(3,123,command(),**{'from':{'id':234,'is_bot':False}}))
        bot.handle_update(update(4,123,command().replace('/hen','/hen@other')))
        self.assertEqual(len(requests),1)

    def test_list_and_cancel_are_owner_control_commands_for_both_routes(self):
        controls,customers=[],[]
        bot=TelegramBotService(None,TelegramAccess((123,)),Mock(),on_control=controls.append,on_customer=customers.append)
        for index,(chat,text) in enumerate(((123,'/lich'),(123,'/huy mochi_test'),(456,'/lich'),(456,'/huy mochi_test')),1):
            bot.handle_update(update(index,chat,text))
        self.assertEqual([x.action for x in controls],['schedules','cancel_schedule'])
        self.assertEqual([x.action for x in customers],['schedules','cancel_schedule'])
        self.assertEqual(customers[-1].job_id,'mochi_test')

    def test_schedule_metadata_rejects_credentials_and_invalid_timestamp(self):
        with self.assertRaises(ValueError):
            schedule_storage(future(),{'api_key':'secret'})
        with self.assertRaises(ValueError):
            schedule_storage('not-a-time',{})


class SchedulingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    setUp=queue_fixtures.QueueTests.setUp
    local=queue_fixtures.QueueTests.local
    finish=queue_fixtures.QueueTests.finish
    release=queue_fixtures.QueueTests.release

    def scheduled(self, update_id=10, chat_id=123, when=None):
        when=when or future()
        self.page._on_job_requested((self.bot.fingerprint,
            TelegramJobRequest(update_id,chat_id,'TEST',100,'Khách A',when.isoformat())))
        row=self.store.lookup_request(self.bot.fingerprint,update_id)
        return row.job_id if row else None

    def customer_schedule(self, update_id=30):
        self.store.ensure_customer(456,'Khách')
        self.store.set_customer_balance(456,1000000,'offline funding',expected_units=0)
        request=TelegramCustomerRequest(update_id,456,'job','Khách','TEST',100,'',future().isoformat())
        self.page._on_customer_requested((self.bot.fingerprint,request))
        return self.store.lookup_request(self.bot.fingerprint,update_id).job_id

    def tick(self):
        self.mochi._check_scheduled_jobs(future()+timedelta(minutes=2))

    def reopen(self):
        self.mochi.shutdown_workers()
        mochi=MochiChatPage(settings=self.settings)
        mochi.input_api_key.setText('current-offline-key')
        page=TelegramPage(self.settings,mochi,user_store=TelegramUserStore(self.store.path))
        page.worker=self.bot
        page._accepting_requests=True
        self.addCleanup(mochi.close)
        self.addCleanup(page.close)
        return mochi,page

    def test_future_schedule_creates_no_worker_and_does_not_consume_job_slots(self):
        job=self.scheduled()
        self.assertIsNotNone(job)
        self.assertEqual(self.started,[])
        self.assertNotIn(job,self.mochi._workers)
        self.assertFalse(self.mochi._active_job_ids)
        self.assertFalse(self.mochi._pending_jobs)
        self.assertIn('Hẹn giờ:',self.bot.reply.call_args.args[1])
        self.assertEqual(self.store.get_job(job).dispatch_state,'scheduled')
        self.assertIsNotNone(self.local())
        self.assertEqual(len(self.started),1)
        self.mochi._check_scheduled_jobs(datetime.now(VIETNAM_TIME))
        self.assertEqual(len(self.started),1)

    def test_due_job_enters_shared_queue_and_starts_only_when_slot_released(self):
        active=self.local()
        job=self.scheduled()
        self.bot.reply.reset_mock()
        self.tick()
        self.assertEqual(list(self.mochi._pending_jobs),[job])
        self.assertEqual(self.store.get_job(job).dispatch_state,'queued')
        self.assertIn('Đang chờ',self.bot.reply.call_args.args[1])
        self.finish(active)
        self.release(active)
        self.assertEqual(self.started,[active,job])
        self.assertIn('Đang xử lý',self.bot.reply.call_args.args[1])
        self.tick()
        self.assertEqual(self.started,[active,job])

    def test_due_job_uses_current_mail_configuration_without_new_owner_registration(self):
        job=self.scheduled()
        self.store.set_user_mail_keys(123,'new-mail-key-at-due-time','new-1trieumail-at-due-time')
        self.mochi.input_api_key.setText('shared-key-must-not-be-used')
        with patch.object(self.store,'record_job',side_effect=AssertionError('duplicate registration')):
            self.tick()
        self.assertEqual(self.started,[job])
        self.assertEqual(self.mochi._workers[job].runner.mail_provider_api_key,'new-mail-key-at-due-time')
        self.assertEqual(len(self.store.list_jobs(123)),1)

    def test_schedules_survive_close_reopen_without_reholding_customer_balance(self):
        job=self.customer_schedule()
        hold=self.store.get_customer(456)['balance_units']
        mochi,page=self.reopen()
        self.assertIn(job,mochi._scheduled_jobs)
        self.assertEqual(page.store.get_customer(456)['balance_units'],hold)
        self.assertEqual(mochi._session_jobs[job].creator_chat_id,456)
        mochi._check_scheduled_jobs(future()+timedelta(minutes=1))
        self.assertEqual(self.started,[job])
        self.assertEqual(page.store.get_customer(456)['balance_units'],hold)
        self.assertTrue(self.bot.reply.call_args.kwargs['public'])

    def test_overdue_schedule_from_previous_session_runs_when_tool_reopens(self):
        job=self.scheduled()
        with self.store._connection(write=True) as db:
            db.execute('UPDATE jobs SET scheduled_at=? WHERE job_id=?',
                ((datetime.now(VIETNAM_TIME)-timedelta(minutes=2)).isoformat(),job))
        mochi,page=self.reopen()
        mochi._check_scheduled_jobs()
        self.assertEqual(self.started,[job])
        self.assertEqual(page.store.get_job(job).dispatch_state,'running')

    def test_cancel_future_customer_refunds_once_and_writes_cancelled_csv(self):
        job=self.customer_schedule()
        self.assertEqual(self.store.get_customer(456)['balance_units'],900000)
        self.mochi._stop_job(job)
        self.mochi._stop_job(job)
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)
        self.assertEqual(self.store.get_customer_order_for_job(job)['charged_units'],0)
        self.assertEqual(self.store.get_job(job).status,'cancelled')
        self.assertEqual(self.mochi.job_history_store.read()[0].status,'cancelled')
        self.assertEqual(self.started,[])
        mochi,_=self.reopen()
        self.assertNotIn(job,mochi._scheduled_jobs)

    def test_due_validation_failure_refunds_customer_and_reports_failure(self):
        job=self.customer_schedule()
        self.mochi.input_api_key.clear()
        self.tick()
        self.assertEqual(self.started,[])
        self.assertEqual(self.store.get_job(job).status,'failed')
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)
        self.assertIn('lỗi thực thi',self.bot.reply.call_args.args[1])

    def test_revoked_operator_schedule_fails_before_execution(self):
        job=self.scheduled()
        self.store.set_active(123,False)
        self.bot.access.replace((234,))
        self.bot.reply.reset_mock()
        self.tick()
        self.assertEqual(self.started,[])
        self.assertEqual(self.store.get_job(job).status,'failed')
        self.bot.reply.assert_not_called()

    def test_storage_failure_keeps_schedule_and_hold_for_retry(self):
        job=self.customer_schedule()
        with patch.object(self.mochi,'scheduled_job_claim',side_effect=OSError('locked')):
            self.tick()
        self.assertIn(job,self.mochi._scheduled_jobs)
        self.assertIsNone(self.store.get_job(job).finalized_at)
        self.assertEqual(self.store.get_customer(456)['balance_units'],900000)
        self.tick()
        self.assertEqual(self.started,[job])

    def test_cancel_storage_failure_does_not_acknowledge_or_remove_schedule(self):
        job=self.customer_schedule()
        with patch.object(self.mochi,'scheduled_job_cancel',side_effect=OSError('locked')):
            text=self.page._cancel_schedule_text(456,self.bot.fingerprint,job)
        self.assertIn('Chưa hủy',text)
        self.assertIn(job,self.mochi._scheduled_jobs)
        self.assertEqual(self.store.get_customer(456)['balance_units'],900000)

    def test_multiple_sessions_cannot_claim_or_cancel_a_claimed_schedule_twice(self):
        job=self.customer_schedule()
        second=TelegramUserStore(self.store.path)
        self.assertTrue(second.claim_scheduled_job(job))
        self.assertFalse(self.store.claim_scheduled_job(job))
        self.assertFalse(self.store.cancel_scheduled_job(job))
        self.mochi._stop_job(job)
        self.assertEqual(self.store.get_customer(456)['balance_units'],900000)
        self.assertIsNone(self.store.get_job(job).finalized_at)
        self.assertEqual(self.started,[])

    def test_bot_lists_and_cancels_only_jobs_owned_by_sender_and_same_bot(self):
        mine=self.scheduled()
        other=self.scheduled(20,234)
        text=self.page._schedule_list_text(123,self.bot.fingerprint)
        self.assertIn(mine,text)
        self.assertNotIn(other,text)
        self.assertIn('Không tìm thấy',self.page._cancel_schedule_text(123,self.bot.fingerprint,other))
        self.assertIn('Không tìm thấy',self.page._cancel_schedule_text(123,'other-bot',mine))
        self.page._on_control_requested((self.bot.fingerprint,
            TelegramControlRequest(31,123,'cancel_schedule',job_id=mine)))
        self.assertEqual(self.store.get_job(mine).status,'cancelled')
        self.assertEqual(self.store.get_job(other).dispatch_state,'scheduled')

    def test_customer_bot_list_and_cancel_work_with_refund(self):
        job=self.customer_schedule()
        request=TelegramCustomerRequest(31,456,'schedules','Khách')
        self.page._on_customer_requested((self.bot.fingerprint,request))
        self.assertIn(job,self.bot.reply.call_args.args[1])
        self.page._on_customer_requested((self.bot.fingerprint,
            TelegramCustomerRequest(32,456,'cancel_schedule','Khách',job_id=job)))
        self.assertIn('Đã hủy',self.bot.reply.call_args.args[1])
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)

    def test_local_ui_uses_picker_and_remote_immediate_job_ignores_it(self):
        self.mochi.chk_schedule_job.setChecked(True)
        self.mochi.input_schedule_time.setDateTime(QDateTime.fromString(future().strftime('%Y-%m-%d %H:%M'),'yyyy-MM-dd HH:mm'))
        job=self.local()
        self.assertEqual(self.store.get_job(job).chat_id,LOCAL_USER_ID)
        self.assertIn(job,self.mochi._scheduled_jobs)
        self.assertEqual(self.mochi.btn_start.text(),'Tạo lịch job')
        self.page._on_job_requested((self.bot.fingerprint,TelegramJobRequest(60,123,'NOW',100)))
        remote=self.store.lookup_request(self.bot.fingerprint,60)
        self.assertIn(remote.job_id,self.started)
        self.assertNotIn(remote.job_id,self.mochi._scheduled_jobs)

    def test_past_schedule_is_rejected_before_registration_and_reservation(self):
        job=self.scheduled(when=datetime.now(VIETNAM_TIME)-timedelta(minutes=1))
        self.assertIsNone(job)
        self.assertEqual(self.store.list_jobs(123),())
        self.assertEqual(self.started,[])

    def test_saved_schedule_contains_no_mail_keys_or_proxy_credentials(self):
        job=self.scheduled()
        stored=self.store.get_job(job)
        self.assertNotIn('offline-shop-key',stored.schedule_data)
        self.assertNotIn('api_key',stored.schedule_data)
        self.assertEqual(stored.sale_rate_units,100000)

    def test_changing_rate_after_scheduling_does_not_change_customer_hold_or_final_price(self):
        job=self.customer_schedule()
        self.settings.setValue('finance/sale_rate_per_100','9999')
        self.tick()
        self.finish(job)
        order=self.store.get_customer_order_for_job(job)
        self.assertEqual(order['charged_units'],100000)
        self.assertEqual(self.store.get_customer(456)['balance_units'],900000)

    def test_durable_schedule_creation_failure_never_reserves_customer_money(self):
        self.store.ensure_customer(456,'Khách')
        self.store.set_customer_balance(456,1000000,'funding',expected_units=0)
        with patch('core.customer_accounts.schedule_storage',side_effect=ValueError('invalid schedule')):
            self.page._on_customer_requested((self.bot.fingerprint,
                TelegramCustomerRequest(30,456,'job','Khách','TEST',100,'',future().isoformat())))
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)
        self.assertEqual(self.store.customer_orders(456),())
        self.assertEqual(self.mochi._scheduled_jobs,{})

    def test_scheduled_saved_mail_batch_is_loaded_only_at_due_time_and_kept_after_ui_changes(self):
        source=str(Path(self.temp.name)/'saved-batch.json')
        self.mochi.chk_use_mail_batch.setChecked(True)
        self.mochi.input_mail_batch.setText(source)
        with patch('gui.pages.mochi_chat.load_batch',return_value=(SimpleNamespace(identifier='offline'),)) as load:
            job=self.scheduled()
            load.assert_not_called()
            self.mochi.chk_use_mail_batch.setChecked(False)
            self.mochi.input_mail_batch.setText('different-batch.json')
            self.tick()
            load.assert_called_once_with(source)
        self.assertEqual(self.started,[job])

    def test_multiple_due_schedules_follow_due_time_without_idle_timer_table_resets(self):
        later=self.scheduled(10,when=future()+timedelta(minutes=10))
        earlier=self.scheduled(11,when=future())
        with patch.object(self.mochi,'_render_jobs_table') as render:
            self.mochi._check_scheduled_jobs(datetime.now(VIETNAM_TIME))
            render.assert_not_called()
        self.mochi._check_scheduled_jobs(future()+timedelta(minutes=11))
        self.assertEqual(self.started,[earlier])
        self.assertEqual(list(self.mochi._pending_jobs),[later])

    def test_concurrent_store_claims_allow_only_one_executor(self):
        job=self.scheduled()
        barrier=threading.Barrier(2)
        def claim():
            store=TelegramUserStore(self.store.path)
            barrier.wait(timeout=3)
            return store.claim_scheduled_job(job)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:claim(),range(2)))
        self.assertEqual(sorted(results),[False,True])

    def test_real_qt_timer_dispatches_due_schedule_without_user_click(self):
        job=self.scheduled(when=datetime.now(VIETNAM_TIME)+timedelta(milliseconds=50))
        self.mochi._schedule_timer.setInterval(10)
        deadline=time.monotonic()+2
        while not self.started and time.monotonic()<deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertEqual(self.started,[job])

    def test_due_schedule_queued_behind_running_job_survives_graceful_close(self):
        self.local()
        job=self.customer_schedule()
        self.tick()
        self.assertIn(job,self.mochi._pending_jobs)
        mochi,page=self.reopen()
        self.assertIn(job,mochi._scheduled_jobs)
        self.assertEqual(page.store.get_job(job).dispatch_state,'scheduled')
        self.assertEqual(page.store.get_customer(456)['balance_units'],900000)
        mochi._check_scheduled_jobs(future()+timedelta(minutes=3))
        self.assertEqual(self.started[-1],job)

    def test_atomic_customer_cancellation_refreshes_visible_balance_with_bot_offline(self):
        job=self.customer_schedule()
        self.page.worker=None
        self.mochi._stop_job(job)
        table=self.page.customer_page.table
        row=next(i for i in range(table.rowCount()) if table.item(i,0).text()=='456')
        self.assertIn('10,000',table.item(row,1).text())


if __name__=='__main__':
    unittest.main(verbosity=2)
