"""Offline stock lifecycle, paid-batch recovery, exports and bot permissions."""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from core.stock_accounts import StockAccountStore, creation_options
from core.job_schedule import VIETNAM_TIME
from core.platforms.mochichat.stock_creation import purchase_stock_batch
from core.platforms.mochichat.account_lookup import AccountProfile
from core.telegram_bot import parse_control_command, TelegramBotService, TelegramAccess


class StockStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'stock.sqlite3';self.store=StockAccountStore(self.path)
        self.bid=self.store.create_batch(2,2000,False,'123')
        self.batch=self.store.batches()[0]

    def delivery(self):
        self.store.save_delivery(self.batch,[SimpleNamespace(username=f'fake{i}@example.com',password=f'fake|notused'.replace('|','-')+str(i)) for i in (1,2)])
        for row in self.store.all(internal=True):self.store.update(row['id'],invite_code='CODE'+str(row['id']),beans='1000',status='paused')
        self.store.batch_update(self.bid,'completed')
        return [r['id'] for r in self.store.all()]

    def test_input_limits_and_exact_hundred_beans(self):
        for args in [(True,2000,False,'1'),(501,2000,False,'1'),(1,10,True,'1'),(1,150,False,'1'),(1,100,1,'1'),(1,100,False,'')]:
            with self.subTest(args=args),self.assertRaises(ValueError):creation_options(*args)

    def test_pending_mail_does_not_appear_as_created_accounts(self):
        self.store.save_delivery(self.batch,[SimpleNamespace(username='fake@example.com',password='fake')])
        self.assertEqual(self.store.all(),[])
        self.assertEqual(len(self.store.all(internal=True)),1)
        self.assertEqual(self.store.summary()['total'],0)

    def test_summary_and_restart_preserve_created_credentials_and_beans(self):
        self.delivery();store=StockAccountStore(self.path)
        self.assertEqual(store.summary(),{'total':2,'beans':'2000'})
        self.assertEqual(store.all()[0]['password'],'fake-notused1')

    def test_export_reserves_then_close_removes_and_history_keeps_exact_lines(self):
        ids=self.delivery();r=self.store.prepare_export(ids,'admin')
        self.assertEqual(r['content'],'fake1@example.com|fake-notused1\nfake2@example.com|fake-notused2')
        self.assertEqual(self.store.summary()['total'],2)
        self.assertEqual(self.store.all()[0]['status'],'exporting')
        self.store.finish_export(r['id']);self.store.finish_export(r['id'])
        self.assertEqual(self.store.summary(),{'total':0,'beans':'0'})
        self.assertEqual(self.store.export(r['id'])['content'],r['content'])
        self.assertEqual(self.store.exports()[0]['count'],2)
        self.assertNotIn('content',self.store.exports()[0])

    def test_export_reservation_rejects_duplicate_selection_and_survives_restart(self):
        ids=self.delivery();r=self.store.prepare_export(ids,'admin')
        with self.assertRaises(ValueError):self.store.prepare_export(ids,'another-admin')
        restarted=StockAccountStore(self.path);restarted.recover()
        self.assertEqual(restarted.export(r['id'])['state'],'pending')
        restarted.finish_export(r['id']);self.assertFalse(restarted.all())

    def test_delete_and_export_reject_any_active_job_even_if_row_is_failed(self):
        ids=self.delivery();self.store.update(ids[0],status='failed',job_id='still-running')
        with self.assertRaises(ValueError):self.store.delete(ids)
        with self.assertRaises(ValueError):self.store.prepare_export(ids,'admin')
        self.assertEqual(self.store.summary()['total'],2)

    def test_delete_only_chosen_accounts_preserves_export_history(self):
        ids=self.delivery();r=self.store.prepare_export([ids[0]],'admin');self.store.finish_export(r['id'])
        self.assertEqual(self.store.delete([ids[1]]),1)
        self.assertEqual(self.store.exports()[0]['id'],r['id'])

    def test_buying_crash_never_queues_another_purchase(self):
        self.store.batch_update(self.bid,'buying');self.store.recover()
        self.assertEqual(self.store.batches()[0]['state'],'uncertain')
        self.assertFalse(self.store.all(internal=True))

    def test_created_batch_resumes_login_without_repurchase(self):
        self.store.save_delivery(self.batch,[SimpleNamespace(username='fake@example.com',password='fake')])
        self.store.update(self.store.all(internal=True)[0]['id'],status='logging_in')
        self.store.recover()
        self.assertEqual(self.store.batches()[0]['state'],'creating')
        self.assertEqual(self.store.all(internal=True)[0]['status'],'creating')

    def test_bot_update_is_idempotent_and_new_batch_is_blocked_during_creation(self):
        with self.assertRaises(ValueError):self.store.create_batch(1,1000,True,'123')
        self.store.batch_update(self.bid,'completed')
        bid=self.store.create_batch(1,1000,True,'123',request_key='bot:1')
        self.assertEqual(self.store.create_batch(1,1000,True,'123',request_key='bot:1'),bid)

    def test_incomplete_or_duplicate_mail_delivery_is_not_silently_discarded(self):
        with self.assertRaises(ValueError):self.store.save_delivery(self.batch,[SimpleNamespace(username='bad',password='pw')])
        with self.assertRaises(ValueError):self.store.save_delivery(self.batch,[SimpleNamespace(username='a@example.com',password='pw')]*2)
        self.assertEqual(self.store.all(internal=True),[])

    def test_creation_keys_are_owner_scoped_and_never_part_of_batch_lists(self):
        self.store.set_creation_keys(123,{'gmailvip_vn':'private-creation-one'})
        self.store.set_creation_keys(456,{'gmailvip_vn':'private-creation-two'})
        restarted=StockAccountStore(self.path)
        self.assertEqual(restarted.get_creation_keys(123)['gmailvip_vn'],'private-creation-one')
        self.assertEqual(restarted.get_creation_keys(456)['gmailvip_vn'],'private-creation-two')
        self.assertNotIn('private-creation',json.dumps(restarted.batches()))
        self.assertEqual(restarted.get_creation_keys(999)['gmailvip_vn'],'')


class PurchaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=StockAccountStore(Path(self.temp.name)/'stock.sqlite3')
        self.store.create_batch(3,2000,True,'123');self.batch=self.store.batches()[0]

    async def test_one_purchase_for_requested_number_and_delivery_is_saved_before_login(self):
        delivered=[SimpleNamespace(username=f'a{i}@example.com',password='fake') for i in range(3)]
        client=SimpleNamespace(buy_gmail_domain_info=AsyncMock(return_value=delivered))
        class Provider:
            async def __aenter__(self):return client
            async def __aexit__(self,*a):pass
        sink=Mock()
        with patch('core.platforms.mochichat.stock_creation.GmailVipVn',return_value=Provider()) as provider:
            await purchase_stock_batch(self.store,self.batch,provider='gmailvip_vn',keys={'gmailvip_vn':'offline'},purchase_sink=sink,directory=self.temp.name)
        client.buy_gmail_domain_info.assert_awaited_once_with('123',amount=3)
        self.assertIs(provider.call_args.kwargs['on_purchase'],sink)
        self.assertEqual(len(self.store.all(internal=True)),3)
        self.assertEqual(len(list(Path(self.temp.name).glob('stock_*.json'))),1)

    async def test_lost_purchase_response_stays_uncertain_without_second_purchase(self):
        client=SimpleNamespace(buy_gmail_domain_info=AsyncMock(side_effect=RuntimeError('offline')))
        class Provider:
            async def __aenter__(self):return client
            async def __aexit__(self,*a):pass
        with patch('core.platforms.mochichat.stock_creation.GmailVipVn',return_value=Provider()):
            with self.assertRaises(RuntimeError):
                await purchase_stock_batch(self.store,self.batch,provider='gmailvip_vn',keys={'gmailvip_vn':'offline'},purchase_sink=Mock(),directory=self.temp.name)
        self.assertEqual(client.buy_gmail_domain_info.await_count,1)
        self.store.recover();self.assertEqual(self.store.batches()[0]['state'],'uncertain')


class StockBotTests(unittest.TestCase):
    def test_partner_and_customer_cannot_parse_stock_commands(self):
        for role in ('partner',None):
            for text in ('/acc_create 1 infinite 123','/accs','/acc_export 1','/acc_done abc'):
                with self.subTest(role=role,text=text),self.assertRaises(ValueError):parse_control_command(text,123,role,1)

    def test_admin_create_and_selection_parse_without_credentials_in_request_repr(self):
        r=parse_control_command('/acc_create 5 infinite 123',123,'admin',1)
        self.assertEqual(r.stock_data,dict(count=5,beans=1000,infinite=True,product_ids='123'))
        r=parse_control_command('/acc_export 1,2,3',123,'admin',2)
        self.assertEqual(r.stock_data,{'ids':[1,2,3]})
        self.assertNotIn('stock_data',repr(r))

    def test_invalid_selection_and_wrong_bean_unit_rejected(self):
        for text in ('/acc_create 1 10 123','/acc_stop 0','/acc_delete 1,1','/acc_export 1;2','/acc_create 5000 1000 123'):
            with self.subTest(text=text),self.assertRaises(ValueError):parse_control_command(text,123,'admin',2)

    def test_admin_account_details_and_batch_progress_are_separate_from_export(self):
        self.assertEqual(parse_control_command('/acc_info 7',123,'admin',1).stock_data,{'ids':[7]})
        self.assertEqual(parse_control_command('/acc_batches 2',123,'admin',2).stock_data,{'page':2})
        with self.assertRaises(ValueError):parse_control_command('/acc_info 7',123,'partner',1)

    def test_creation_key_command_is_admin_only_and_masks_keys_in_request_repr(self):
        r=parse_control_command('/acc_mailapi private-create -',123,'admin',1)
        self.assertEqual(r.stock_data['mail_keys'],{'gmailvip_vn':'private-create','1trieumail_com':''})
        self.assertNotIn('private-create',repr(r))
        with self.assertRaises(ValueError):parse_control_command('/acc_mailapi a b',123,'partner',1)

    def test_export_txt_requires_admin_and_preserves_mail_password_lines(self):
        service=TelegramBotService(None,TelegramAccess((123,),roles={123:'admin'}),lambda _:None)
        report={'owner':{'chat_id':123},'files':[dict(kind='stock_accounts',filename='accounts_fake.txt',text='fake@example.com|pw',row_count=1)]}
        self.assertTrue(service.send_report(123,report,target_id=123,authorize=lambda *_:True,admin_only=True))
        self.assertFalse(service.send_report(123,report,target_id=123,authorize=lambda *_:True,admin_only=False))


from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread
from gui.pages.telegram import TelegramPage


class StockControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.settings=QSettings(str(Path(self.temp.name)/'settings.ini'),QSettings.IniFormat)
        self.m=MochiChatPage(settings=self.settings)
        self.t=TelegramPage(self.settings,self.m)
        self.m.chk_use_proxy.setChecked(False);self.m.input_gmailvip_key.setText('offline-key')
        self.m.input_api_key.setText('offline-key');self.m.input_pids.setText('999')
        self.controller=self.m.stock_controller;self.controller.timer.stop();self.m._schedule_timer.stop()
        self.store=self.controller.store

    def tearDown(self):
        self.t.shutdown_workers();self.m.shutdown_workers();self.app.processEvents()
        self.t.deleteLater();self.m.deleteLater();self.app.processEvents();self.temp.cleanup()

    def account(self,infinite=False,target=2500):
        self.store.create_batch(1,target,infinite,'123')
        batch=self.store.batches()[0]
        self.store.save_delivery(batch,[SimpleNamespace(username='fake@example.com',password='fake')])
        row=self.store.all(internal=True)[0]
        self.store.update(row['id'],status='waiting',beans='1000',invite_code='STOCKCODE')
        self.store.batch_update(batch['id'],'completed')
        return self.store.all()[0]

    def test_dispatch_uses_missing_beans_combo_saved_product_and_zero_sale_revenue(self):
        row=self.account();self.controller._dispatch(row)
        row=self.store.all()[0];job=self.t.store.get_job(row['job_id'])
        self.assertEqual(job.target_count,15)
        self.assertEqual(job.sale_rate_units,0)
        self.assertEqual(json.loads(job.schedule_data)['mail_product_ids'],'123')
        self.assertTrue(json.loads(job.schedule_data)['combo'])
        self.assertEqual(self.m._session_jobs[job.job_id].mail_product_ids,'123')

    def test_finite_completion_updates_beans_without_scheduling_new_cycle(self):
        row=self.account();self.controller._dispatch(row);job=self.store.all()[0]['job_id']
        self.t.store.finish_job(job,'completed',15)
        self.controller._sync_jobs();r=self.store.all()[0]
        self.assertEqual((r['beans'],r['status'],r['job_id']),('2500','ready',None))
        self.controller._sync_jobs();self.assertEqual(self.store.all()[0]['beans'],'2500')

    def test_infinite_completion_waits_24h_one_minute_then_keeps_accumulating(self):
        row=self.account(True);self.controller._dispatch(row);jid=self.store.all()[0]['job_id']
        self.t.store.finish_job(jid,'completed',10)
        self.controller._sync_jobs();r=self.store.all()[0]
        self.assertEqual((r['beans'],r['status']),('2000','waiting'))
        when=datetime.fromisoformat(r['next_at'])
        self.assertGreaterEqual(when,datetime.now(VIETNAM_TIME)+timedelta(hours=24,seconds=55))
        self.controller.tick();self.assertIsNone(self.store.all()[0]['job_id'])

    def test_quota_confirmation_is_counted_even_before_progress_reaches_job_ledger(self):
        row=self.account();self.controller._dispatch(row);jid=self.store.all()[0]['job_id']
        self.m.invite_quota_store.record_success('STOCKCODE',jid,'fake-referral')
        self.controller._sync_jobs();self.assertEqual(self.store.all()[0]['beans'],'1100')

    def test_stop_cancels_scheduled_combo_and_export_then_has_no_running_job(self):
        row=self.account(True);self.controller._dispatch(row)
        self.controller.stop([row['id']]);r=self.store.all()[0]
        self.assertEqual((r['status'],r['job_id']),('paused',None))
        self.store.prepare_export([r['id']],'Local')

    def test_delete_does_not_remove_permanent_job_or_finance(self):
        row=self.account();self.controller._dispatch(row);jid=self.store.all()[0]['job_id']
        self.t.store.finish_job(jid,'failed',3);self.controller._sync_jobs()
        self.store.delete([row['id']])
        self.assertEqual(self.t.store.get_job(jid).success_count,3)

    def test_failed_acc_can_resume_only_missing_beans(self):
        row=self.account();self.controller._dispatch(row);jid=self.store.all()[0]['job_id']
        self.t.store.finish_job(jid,'failed',3);self.controller._sync_jobs()
        self.controller.resume([row['id']]);self.controller._dispatch(self.store.all()[0])
        self.assertEqual(self.t.store.get_job(self.store.all()[0]['job_id']).target_count,12)

    def test_terminal_job_keeps_log_link_and_legacy_missing_link_is_recovered(self):
        row=self.account();self.controller._dispatch(row);jid=self.store.all()[0]['job_id']
        self.t.store.finish_job(jid,'failed',2);self.controller._sync_jobs()
        result=self.controller.list()['rows'][0]
        self.assertIsNone(result['job_id']);self.assertEqual(result['last_job_id'],jid)
        with self.store.connection() as db:db.execute('UPDATE stock_accounts SET last_job_id=NULL')
        self.assertEqual(self.controller.list()['rows'][0]['last_job_id'],jid)

    def test_login_failure_records_safe_progress_reason_and_never_password(self):
        from core.platforms.mochichat.account_lookup import AccountLookupError
        bid=self.store.create_batch(1,2500,False,'123');batch=self.store.batches()[0]
        self.store.save_delivery(batch,[SimpleNamespace(username='fake@example.com',password='private-test-password')])
        self.t.store.record_local_job(bid,'STOCK_CREATE',1,0,sale_rate_units=0)
        row=self.store.all(internal=True)[0]
        async def lookup(*_args,**kw):
            kw['on_progress']('Đang đăng nhập Google…')
            raise AccountLookupError('Không kết nối được Mochi.',transport_error=True)
        with patch('gui.stock_controller.lookup_account',new=lookup):
            self.controller._login(row);deadline=time.monotonic()+5
            while self.controller.worker is not None and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        self.assertIsNone(self.controller.worker)
        logs='\n'.join(self.m._job_logs[bid])
        self.assertIn('Đang đăng nhập Google',logs);self.assertIn('Không kết nối được Mochi',logs)
        self.assertIn('Lỗi kết nối/proxy',logs);self.assertNotIn('private-test-password',logs)
        self.assertEqual(self.store.all(internal=True)[0]['status'],'creation_failed')

    def test_local_batch_log_can_open_even_before_an_invite_job_exists(self):
        self.m._append_log_line('Test creation log',job_id='stock_batch_test')
        self.m.show_job_logs('stock_batch_test')
        self.assertEqual(self.m._selected_job_id,'stock_batch_test')
        self.assertIn('Test creation log',self.m.txt_log.toPlainText())

    def test_generic_continue_cannot_restart_a_stock_account_after_its_job_failed(self):
        row=self.account();self.controller._dispatch(row);jid=self.store.all()[0]['job_id']
        self.t.store.finish_job(jid,'failed',3);self.controller._sync_jobs()
        with self.assertRaisesRegex(ValueError,'Kho tài khoản'):
            self.t.store.create_continuation(jid,'unsafe-child',scheduled_at=datetime.now(VIETNAM_TIME).isoformat(),schedule_data={})
        temporary=self.t.store.temporary_history()
        self.assertFalse(next(r for r in temporary['rows'] if r['job_id']==jid)['can_continue'])

    def test_local_stock_type_activates_form_and_warehouse(self):
        self.m._select_job_type('STOCK_ACCOUNT')
        self.assertFalse(self.m.stock_create_panel.isHidden())
        self.assertTrue(self.m.input_code.isEnabled() is False)
        self.assertIsNotNone(self.m.stock_inventory_panel)

    def test_login_creates_account_profile_and_enqueues_only_missing_beans_without_buying(self):
        bid=self.store.create_batch(1,2500,False,'123')
        batch=self.store.batches()[0]
        self.store.save_delivery(batch,[SimpleNamespace(username='fake@example.com',password='fake')])
        self.t.store.record_local_job(bid,'STOCK_CREATE',1,0,sale_rate_units=0)
        row=self.store.all(internal=True)[0]
        profile=AccountProfile('fake@example.com','offline-user','Fake','CREATED1','1000',datetime.now(VIETNAM_TIME).isoformat())
        with patch('gui.stock_controller.lookup_account',new=AsyncMock(return_value=profile)) as lookup:
            self.controller._login(row)
            deadline=time.monotonic()+5
            while self.controller.worker is not None and time.monotonic()<deadline:
                self.app.processEvents();time.sleep(.01)
            self.assertIsNone(self.controller.worker)
            lookup.assert_awaited_once()
        self.assertEqual(self.store.batches()[0]['state'],'completed')
        row=self.store.all()[0]
        self.assertEqual((row['invite_code'],row['beans'],row['status']),('CREATED1','1000','waiting'))
        self.controller._dispatch(row)
        self.assertEqual(self.t.store.get_job(self.store.all()[0]['job_id']).target_count,15)

    def test_invalid_product_never_queues_a_purchase(self):
        with self.assertRaises(ValueError):self.controller.create(1,2000,False,'not-a-product',mail_keys={'gmailvip_vn':'creation-key'})
        self.assertEqual(self.store.batches(),[])

    def test_initial_mail_purchase_does_not_invent_referral_beans_or_customers(self):
        self.controller.create(1,2000,False,'123',mail_keys={'gmailvip_vn':'creation-key'});batch=self.store.batches()[0]
        with patch.object(self.controller,'_start'):self.controller._buy(batch)
        job=self.t.store.get_job(batch['id'])
        self.assertEqual((job.target_count,job.sale_rate_units),(0,0))
        self.assertEqual(self.t.store.get_user(-1).requested_beans,0)
        self.t.store.finish_job(job.job_id,'completed',0)
        self.t.store._init_statistics()
        report=self.t.store.statistics(datetime.now(VIETNAM_TIME).strftime('%Y-%m'))
        self.assertEqual(report['customer_counts']['month'],0)
        self.assertEqual(sum(d['unique_customers'] for d in report['days']),0)

    def test_general_key_removed_while_queued_does_not_change_creation_purchase_key(self):
        self.controller.create(1,2000,False,'123',mail_keys={'gmailvip_vn':'creation-key'})
        self.m.input_gmailvip_key.clear()
        captured=[]
        with patch.object(self.controller,'_start',side_effect=lambda fn,*args:captured.append(fn)):
            self.controller.tick()
        with patch('gui.stock_controller.purchase_stock_batch',new=AsyncMock()) as purchase:asyncio.run(captured[0]())
        self.assertEqual(purchase.call_args.kwargs['keys']['gmailvip_vn'],'creation-key')
        self.assertEqual(self.m.input_gmailvip_key.text(),'')

    def test_missing_creation_key_cannot_fall_back_to_general_api(self):
        with self.assertRaisesRegex(ValueError,'Nhập API'):self.controller.create(1,2000,False,'123')
        self.assertEqual(self.store.batches(),[])

    def test_creation_purchase_snapshot_survives_restart_and_source_changes(self):
        self.controller.create(1,2000,False,'123',mail_keys={'gmailvip_vn':'private-create'})
        batch=self.store.batches()[0]
        self.controller.store=StockAccountStore(self.store.path)
        self.m.combo_provider.setCurrentIndex(self.m.combo_provider.findData('1trieumail_com'))
        captured=[]
        with patch.object(self.controller,'_start',side_effect=lambda fn,*args:captured.append(fn)):self.controller._buy(batch)
        with patch('gui.stock_controller.purchase_stock_batch',new=AsyncMock()) as purchase:asyncio.run(captured[0]())
        self.assertEqual(purchase.call_args.kwargs['provider'],'gmailvip_vn')
        self.assertEqual(purchase.call_args.kwargs['keys']['gmailvip_vn'],'private-create')
        self.assertNotIn('private-create',json.dumps(self.controller.list()))

    def test_local_referral_mail_still_uses_general_api(self):
        self.store.set_creation_keys(-1,{'gmailvip_vn':'private-create'})
        row=self.account();self.controller._dispatch(row);jid=self.store.all()[0]['job_id']
        with patch.object(MochiWorkerThread,'start'):
            self.m._check_scheduled_jobs(now=datetime.now(VIETNAM_TIME)+timedelta(seconds=1))
        self.assertEqual(self.m._workers[jid].runner.mail_provider_api_key,'offline-key')
        self.assertNotIn('private-create',self.t.store.get_job(jid).schedule_data)

    def test_same_api_can_be_used_for_creation_and_referral_purchases(self):
        result=self.controller.create(1,2000,False,'123',mail_keys={'gmailvip_vn':'offline-key'})
        self.assertEqual(self.store.batch_mail_settings(result['batch_id'])[1]['gmailvip_vn'],self.m.input_gmailvip_key.text())

    def test_bot_referral_mail_still_uses_owner_mailapi_not_creation_keys(self):
        self.t.store.upsert_user(123,'Admin','admin')
        self.t.store.set_user_mail_keys(123,'owner-invite-key','owner-other-key')
        self.store.set_creation_keys(123,{'gmailvip_vn':'private-create'})
        row=self.account()
        with self.store.connection() as db:db.execute('UPDATE stock_batches SET owner=123 WHERE id=?',(row['batch_id'],))
        self.controller._dispatch(self.store.all()[0]);jid=self.store.all()[0]['job_id']
        with patch.object(MochiWorkerThread,'start'):
            self.m._check_scheduled_jobs(now=datetime.now(VIETNAM_TIME)+timedelta(seconds=1))
        self.assertEqual(self.m._workers[jid].runner.mail_provider_api_key,'owner-invite-key')
        self.assertEqual(self.t.store.get_user_mail_keys(123)['gmailvip_vn'],'owner-invite-key')

    def test_bot_creation_command_snapshots_separate_keys_without_changing_mailapi(self):
        from gui.stock_telegram import stock_control
        self.t.store.upsert_user(123,'Admin','admin')
        self.t.store.set_user_mail_keys(123,'owner-invite-key','owner-other-key')
        actor=self.t.store.get_user(123);worker=SimpleNamespace(fingerprint='offline',reply=Mock())
        stock_control(self.t,worker,parse_control_command('/acc_mailapi private-create -',123,'admin',1),actor)
        self.assertNotIn('private-create',worker.reply.call_args.args[1])
        stock_control(self.t,worker,parse_control_command('/acc_create 1 2000 123',123,'admin',2),actor)
        batch=self.store.batches()[0]
        self.assertEqual(self.store.batch_mail_settings(batch['id'])[1]['gmailvip_vn'],'private-create')
        self.assertEqual(self.t.store.get_user_mail_keys(123)['gmailvip_vn'],'owner-invite-key')


if __name__=='__main__':unittest.main()
