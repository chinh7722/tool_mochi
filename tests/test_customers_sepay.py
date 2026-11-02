"""Offline wallet, paid orders, SePay reconciliation and customer bot checks."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime,timezone,timedelta
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication,QLineEdit
from core.telegram_users import TelegramUserStore
from core.telegram_bot import TelegramBotService,TelegramAccess,TelegramCustomerRequest
from core.sepay import SePayConfig,SePayClient,SePayError
from core.finance import MailPurchaseReceipt
from gui.pages.mochi_chat import MochiChatPage,MochiWorkerThread
from gui.pages.telegram import TelegramPage
from gui.sepay import SePayThread

BANK = '12345678-1234-4321-8321-123456789abc'
START = (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
CONFIG = SePayConfig('offline-sepay-token',BANK,'1234567890','Ngân hàng thử','KHACH HANG',START)


def transaction(amount=10000,content='456',**changes):
    result={'id':str(uuid.uuid4()),'bank_account_id':BANK,'account_number':CONFIG.account_number,
        'amount_in':amount,'amount_out':0,'transfer_type':'in','transaction_content':content,
        'reference_number':'REF'+uuid.uuid4().hex,'transaction_date':datetime.now(timezone.utc).isoformat()}
    return {**result,**changes}


def page(rows,more=False):
    return SimpleNamespace(status_code=200,headers={},json=lambda:{'status':'success','data':rows,
        'meta':{'pagination':{'has_more':more}}})


class WalletTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'wallet.sqlite3'
        self.store=TelegramUserStore(self.path)
        self.store.ensure_customer(456,'Khách thử')

    def deposit(self,amount=10000):
        data=transaction(amount)
        self.assertTrue(self.store.process_sepay_transaction(data,BANK,CONFIG.account_number,START))
        return data

    def order(self,job='job1',update=1,count=10,rate=100000):
        return self.store.record_customer_order(job,456,'TEST',count*100,count,'fp',update,rate)

    def test_registration_keeps_customers_out_of_allowlist_and_is_durable(self):
        self.assertEqual(self.store.active_ids(),())
        self.assertIsNone(self.store.get_user(456))
        self.assertEqual(TelegramUserStore(self.path).get_customer(456)['name'],'Khách thử')

    def test_deposit_boundaries_credit_exact_amount_and_survive_restart(self):
        self.deposit(10000)
        self.deposit(100000)
        self.assertEqual(TelegramUserStore(self.path).get_customer(456)['balance_units'],11000000)

    def test_outside_limits_wrong_bank_outgoing_old_and_ambiguous_content_are_reviewed(self):
        cases=[transaction(9999),transaction(100001),transaction(content='nap 456'),transaction(content='456 789'),
            transaction(content='0456'),transaction(content='999'),transaction(bank_account_id=str(uuid.uuid4())),
            transaction(account_number='999'),transaction(transfer_type='out'),transaction(amount_out=100),
            transaction(transaction_date='2020-01-01T00:00:00+00:00')]
        for data in cases:
            self.assertFalse(self.store.process_sepay_transaction(data,BANK,CONFIG.account_number,START))
        self.assertEqual(self.store.get_customer(456)['balance_units'],0)
        self.assertEqual(len(self.store.list_sepay_transactions()),len(cases))
        self.assertTrue(all(r['state']=='review' for r in self.store.list_sepay_transactions()))

    def test_duplicate_id_and_reference_do_not_credit_twice(self):
        data=self.deposit()
        for duplicate in (data,{**data,'id':str(uuid.uuid4())}):
            self.assertFalse(self.store.process_sepay_transaction(duplicate,BANK,CONFIG.account_number,START))
        self.assertEqual(len(self.store.customer_history(456)),1)

    def test_manual_matching_of_verified_payment_is_audited_and_cannot_repeat(self):
        data=transaction(content='Nội dung chưa khớp')
        self.assertFalse(self.store.process_sepay_transaction(data,BANK,CONFIG.account_number,START))
        self.store.reconcile_sepay_credit(data['id'],456)
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)
        self.assertIn('thủ công',self.store.customer_history(456)[0]['description'])
        with self.assertRaises(ValueError):
            self.store.reconcile_sepay_credit(data['id'],456)

    def test_manual_matching_cannot_bypass_bank_amount_or_direction_checks(self):
        for data in (transaction(9999),transaction(100001),transaction(account_number='999'),
                transaction(transfer_type='out'),transaction(transaction_date='2020-01-01T00:00:00+00:00')):
            self.store.process_sepay_transaction(data,BANK,CONFIG.account_number,START)
            with self.assertRaises(ValueError):
                self.store.reconcile_sepay_credit(data['id'],456)
        self.assertEqual(self.store.get_customer(456)['balance_units'],0)

    def test_deposit_and_wallet_credit_rollback_together_on_disk_error(self):
        data=transaction()
        with patch.object(self.store,'_wallet_entry',side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                self.store.process_sepay_transaction(data,BANK,CONFIG.account_number,START)
        self.assertEqual(self.store.list_sepay_transactions(),())
        self.assertTrue(self.store.process_sepay_transaction(data,BANK,CONFIG.account_number,START))

    def test_invalid_api_amount_cannot_credit(self):
        for value in (True,10000.0,'10000',-10,None):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.store.process_sepay_transaction(transaction(value),BANK,CONFIG.account_number,START)
        self.assertEqual(self.store.get_customer(456)['balance_units'],0)

    def test_reservation_and_partial_failed_settlement_charge_successful_beans_once(self):
        self.deposit()
        self.assertTrue(self.order())
        order=self.store.get_customer_order_for_job('job1')
        self.assertTrue(order['order_id'].startswith('DH'))
        self.assertEqual(self.store.get_customer(456)['balance_units'],0)
        self.store.update_progress('job1',3)
        self.assertTrue(self.store.finish_job('job1','failed',2,1,4))
        self.assertFalse(self.store.finish_job('job1','completed',10))
        self.assertEqual(self.store.get_customer(456)['balance_units'],700000)
        self.assertEqual(self.store.get_customer_order_for_job('job1')['charged_units'],300000)
        self.assertEqual(self.store.get_user(456).revenue_units,300000)
        self.assertEqual(self.store.get_customer(456)['failed_orders'],1)
        self.assertEqual(len(self.store.customer_history(456)),3)
        self.assertNotIn(456,self.store.active_ids())

    def test_completed_order_consumes_hold_and_no_refund(self):
        self.deposit()
        self.order()
        self.store.finish_job('job1','completed',10)
        self.assertEqual(self.store.get_customer(456)['completed_orders'],1)
        self.assertEqual(self.store.get_customer(456)['balance_units'],0)
        self.assertEqual(len(self.store.customer_history(456)),2)

    def test_customer_revenue_and_whole_mail_batch_cost_are_separate_from_deposits(self):
        self.deposit()
        self.order()
        receipt=MailPurchaseReceipt('customer-purchase','job1','shop.test','offline-account-hash',1,10,53027)
        self.store.record_purchase(receipt)
        self.store.record_purchase(receipt)
        self.assertEqual(self.store.get_user(456).revenue_units,0)
        self.store.finish_job('job1','failed',3)
        owner=self.store.get_user(456)
        self.assertEqual((owner.revenue_units,owner.cost_units),(300000,53027))
        self.assertEqual(self.store.get_customer(456)['balance_units'],700000)

    def test_cancelled_zero_success_order_returns_all_money(self):
        self.deposit()
        self.order()
        self.store.finish_job('job1','cancelled',0)
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)

    def test_duplicate_request_never_creates_or_charges_a_second_order(self):
        self.deposit()
        self.order()
        self.assertFalse(self.order(job='different',update=1))
        self.assertEqual(len(self.store.customer_orders(456)),1)

    def test_insufficient_balance_creates_no_job_and_no_ledger_entry(self):
        with self.assertRaisesRegex(ValueError,'không đủ'):
            self.order()
        self.assertIsNone(self.store.get_job('job1'))
        self.assertEqual(self.store.customer_history(456),())

    def test_invalid_paid_order_inputs_never_charge_a_wallet(self):
        self.deposit()
        for points,code,rate in ((True,'TEST',100000),(100,'',100000),(100,'TEST',0),(-100,'TEST',100000)):
            with self.subTest(points=points,code=code,rate=rate),self.assertRaises(ValueError):
                self.store.record_customer_order('bad',456,code,points,1,'fp',1,rate)
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)
        self.assertEqual(self.store.customer_orders(456),())

    def test_simultaneous_orders_cannot_spend_the_same_balance(self):
        self.deposit()
        def buy(index):
            try:
                return self.order(job=f'job{index}',update=index)
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(buy,[1,2]))
        self.assertEqual(sum(results),1)
        self.assertEqual(self.store.get_customer(456)['balance_units'],0)

    def test_failed_settlement_rolls_back_both_job_and_wallet(self):
        self.deposit()
        self.order()
        with patch.object(self.store,'_wallet_entry',side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.store.finish_job('job1','failed',3)
        self.assertEqual(self.store.get_job('job1').status,'running')
        self.assertIsNone(self.store.get_customer_order_for_job('job1')['charged_units'])
        self.assertEqual(self.store.get_customer(456)['balance_units'],0)

    def test_promotion_preserves_wallet_and_excludes_id_from_customer_list(self):
        self.deposit()
        self.store.upsert_user(456,'Đối tác','partner')
        self.assertEqual(self.store.list_customers(),())
        with self.assertRaises(ValueError):
            self.store.ensure_customer(456,restore=True)
        with self.assertRaises(ValueError):
            self.order()
        self.store.set_active(456,False)
        self.assertEqual(self.store.list_customers()[0]['balance_units'],1000000)

    def test_sale_rate_and_order_id_are_snapshotted_per_order(self):
        self.deposit(100000)
        self.order(rate=120025)
        self.order(job='job2',update=2,count=1,rate=150000)
        self.store.finish_job('job1','failed',2)
        self.assertEqual(self.store.get_customer_order_for_job('job1')['charged_units'],240050)
        self.assertNotEqual(*(r['order_id'] for r in self.store.customer_orders(456)))


class SePayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=TelegramUserStore(Path(self.temp.name)/'sepay.sqlite3')
        self.store.ensure_customer(456)

    async def test_authenticated_paging_commits_cursor_after_all_pages(self):
        first,second=transaction(),transaction()
        session=SimpleNamespace(get=AsyncMock(side_effect=[page([first],True),page([second])]))
        with patch('core.sepay.asyncio.sleep',new=AsyncMock()):
            self.assertEqual(await SePayClient(CONFIG,session).sync(self.store),2)
        self.assertEqual(self.store.sepay_cursor(BANK),second['id'])
        self.assertEqual(session.get.call_args_list[0].kwargs['headers'],{'Authorization':'Bearer '+CONFIG.token})
        self.assertEqual(session.get.call_args_list[1].kwargs['params']['page'],2)
        session.get=AsyncMock(return_value=page([]))
        await SePayClient(CONFIG,session).sync(self.store)
        self.assertEqual(session.get.call_args.kwargs['params']['since_id'],second['id'])

    async def test_failure_after_first_page_retries_without_crediting_twice(self):
        data=transaction()
        session=SimpleNamespace(get=AsyncMock(side_effect=[page([data],True),TimeoutError(CONFIG.token)]))
        with patch('core.sepay.asyncio.sleep',new=AsyncMock()),self.assertRaises(SePayError) as error:
            await SePayClient(CONFIG,session).sync(self.store)
        self.assertNotIn(CONFIG.token,str(error.exception))
        self.assertEqual(self.store.sepay_cursor(BANK),'')
        session.get=AsyncMock(return_value=page([data]))
        self.assertEqual(await SePayClient(CONFIG,session).sync(self.store),0)
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)

    async def test_http_errors_do_not_change_cursor_or_wallet(self):
        for code in (401,403,429,500):
            response=SimpleNamespace(status_code=code,headers={'x-sepay-userapi-retry-after':'60'})
            session=SimpleNamespace(get=AsyncMock(return_value=response))
            with self.subTest(code=code),self.assertRaises(SePayError):
                await SePayClient(CONFIG,session).sync(self.store)
        self.assertEqual(self.store.sepay_cursor(BANK),'')
        self.assertEqual(self.store.get_customer(456)['balance_units'],0)

    async def test_invalid_status_and_pagination_never_advance_cursor(self):
        for data in ({'status':'error','data':[],'meta':{'pagination':{'has_more':False}}},
                {'status':'success','data':[]}, {'status':'success','data':[],'meta':{'pagination':{'has_more':True}}}):
            session=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(status_code=200,json=lambda:data)))
            with self.assertRaises(SePayError):
                await SePayClient(CONFIG,session).sync(self.store)
        self.assertEqual(self.store.sepay_cursor(BANK),'')


class CustomerBotTests(unittest.TestCase):
    def update(self,index=1,customer=456,text='/job TEST 1000',**changes):
        message={'chat':{'id':customer,'type':'private'},'from':{'id':customer,'is_bot':False,'first_name':'Khách'},'text':text}
        return {'update_id':index,'message':{**message,**changes}}

    def test_unapproved_customer_never_uses_operator_callback_and_replay_is_ignored(self):
        jobs,customers=[],[]
        bot=TelegramBotService(None,TelegramAccess([123]),jobs.append,on_customer=customers.append)
        data=self.update()
        bot.handle_update(data)
        bot.handle_update(data)
        self.assertFalse(jobs)
        self.assertEqual(customers,[TelegramCustomerRequest(1,456,'job','Khách','TEST',1000,'')])

    def test_private_sender_identity_is_required_for_customer_wallet_actions(self):
        customers=[]
        bot=TelegramBotService(None,TelegramAccess(),Mock(),on_customer=customers.append)
        bot.handle_update(self.update(1,**{'from':{'id':123}}))
        bot.handle_update(self.update(2,chat={'id':456,'type':'group'}))
        bot.handle_update(self.update(3,**{'from':{'id':456,'is_bot':True}}))
        self.assertFalse(customers)

    def test_customer_commands_cannot_select_another_wallet_or_grant_access(self):
        customers=[]
        bot=TelegramBotService(None,TelegramAccess(),Mock(),on_customer=customers.append)
        for index,text in enumerate(('/sodu 123','/nap','/giaodich 123','/donhang 123','/user_add 456 admin'),1):
            bot.handle_update(self.update(index,text=text))
        self.assertEqual([x.chat_id for x in customers],[456]*5)
        self.assertEqual([x.action for x in customers],['balance','deposit','history','orders','help'])
        self.assertFalse(bot.access.allowed(456))


class CustomerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings=QSettings(str(Path(self.temp.name)/'settings.ini'),QSettings.IniFormat)
        self.settings.setValue('finance/sale_rate_per_100','1000')
        self.mochi=MochiChatPage(settings=self.settings)
        self.mochi.input_api_key.setText('offline-mail-key')
        self.page=TelegramPage(self.settings,self.mochi)
        self.addCleanup(self.page.close)
        self.addCleanup(self.mochi.close)
        self.worker=SimpleNamespace(fingerprint='fp',access=TelegramAccess(),reply=Mock(),complete_request=Mock())
        self.page.worker=self.worker
        self.page._accepting_requests=True
        self.store=self.page.store

    def send(self,action,update=1):
        request=TelegramCustomerRequest(update,456,action,'Khách thử','TEST',1000)
        with patch.object(MochiWorkerThread,'start') as start:
            self.page._on_customer_requested(('fp',request))
        return start

    def test_tabs_customer_columns_and_sepay_token_masking(self):
        self.assertEqual([self.page.tabs.tabText(i) for i in range(3)],['Người dùng và bot','Quản lý khách hàng','Thanh toán SePay'])
        self.assertEqual(self.page.customer_page.table.columnCount(),6)
        self.assertEqual(self.page.sepay_page.fields['token'].echoMode(),QLineEdit.EchoMode.Password)

    def test_new_customer_is_stored_from_bot_without_being_granted_access(self):
        self.send('help')
        self.assertEqual(self.store.get_customer(456)['balance_units'],0)
        self.assertEqual(self.store.active_ids(),())
        self.assertEqual(self.page.customer_page.table.rowCount(),1)
        self.assertTrue(self.worker.reply.call_args.kwargs['public'])

    def test_underfunded_customer_never_starts_a_job(self):
        start=self.send('job')
        start.assert_not_called()
        self.assertIn('Số dư không đủ',self.worker.reply.call_args.args[1])

    def test_funded_order_has_own_id_and_partial_failure_refunds(self):
        self.send('help')
        self.store.process_sepay_transaction(transaction(),BANK,CONFIG.account_number,START)
        self.send('job',2).assert_called_once()
        job=self.store.lookup_request('fp',2)
        self.assertIsNotNone(job)
        order=self.store.get_customer_order_for_job(job.job_id)
        creator=self.mochi.table_jobs.item(0,1).text()
        self.assertEqual(creator,'Khách thử (456)')
        self.assertIn(order['order_id'],self.worker.reply.call_args.args[1])
        summary=SimpleNamespace(job_id=job.job_id,status='failed',success_count=4,failure_count=1,
            attempted_count=5,target_count=10)
        self.page._on_job_finished(summary)
        self.assertEqual(self.store.get_customer(456)['balance_units'],600000)
        self.assertIn('4,000 VND',self.worker.reply.call_args.args[1])
        self.assertTrue(self.worker.reply.call_args.kwargs['public'])

    def test_duplicate_order_request_is_not_charged_again(self):
        self.send('help')
        self.store.process_sepay_transaction(transaction(),BANK,CONFIG.account_number,START)
        self.send('job',2)
        self.send('job',2).assert_not_called()
        self.assertEqual(len(self.store.customer_orders(456)),1)

    def test_start_failure_refunds_hold_without_losing_order_history(self):
        self.send('help')
        self.store.process_sepay_transaction(transaction(),BANK,CONFIG.account_number,START)
        request=TelegramCustomerRequest(2,456,'job','Khách','TEST',1000)
        with patch.object(MochiWorkerThread,'start',side_effect=RuntimeError('offline failure')):
            self.page._on_customer_requested(('fp',request))
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)
        self.assertEqual(self.store.customer_orders(456)[0]['status'],'failed')

    def test_sepay_settings_save_keeps_initial_cutoff_and_uses_no_network(self):
        panel=self.page.sepay_page
        for key,field in panel.fields.items():
            field.setText(getattr(CONFIG,key))
        self.assertTrue(panel.save())
        cutoff=panel.config.start_at
        panel.fields['token'].setText('changed-offline-token')
        self.assertTrue(panel.save())
        self.assertEqual(panel.config.start_at,cutoff)
        self.assertEqual(SePayConfig.from_settings(self.settings).token,'changed-offline-token')
        self.assertIsNone(panel.worker)

    def test_deposit_instructions_include_exact_id_limits_and_no_api_token(self):
        self.page.sepay_page.config=CONFIG
        self.send('deposit')
        text=self.worker.reply.call_args.args[1]
        self.assertIn('Nội dung chuyển khoản: 456',text)
        self.assertIn('100.000',text)
        self.assertNotIn(CONFIG.token,text)

    def test_sepay_manual_matching_updates_customer_balance_and_visible_ledger(self):
        self.send('help')
        data=transaction(content='Khách ghi sai nội dung')
        self.store.process_sepay_transaction(data,BANK,CONFIG.account_number,START)
        panel=self.page.sepay_page
        panel.refresh()
        panel.table.selectRow(0)
        panel.review_chat_id.setText('456')
        panel.reconcile()
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)
        self.assertEqual(panel.table.item(0,4).text(),'Đã cộng tiền')
        self.assertIn('10,000',self.page.customer_page.table.item(0,1).text())

    def test_financial_write_failure_keeps_hold_and_reports_pending_settlement(self):
        self.send('help')
        self.store.process_sepay_transaction(transaction(),BANK,CONFIG.account_number,START)
        self.send('job',2)
        job=self.store.lookup_request('fp',2)
        summary=SimpleNamespace(job_id=job.job_id,status='failed',success_count=3,failure_count=1,
            attempted_count=4,target_count=10)
        with patch.object(self.store,'_wallet_entry',side_effect=OSError('disk error')):
            self.page._on_job_finished(summary)
        self.assertEqual(self.store.get_customer(456)['balance_units'],0)
        self.assertIn('chờ chốt tiền',self.worker.reply.call_args.args[1])
        self.assertIsNone(self.store.get_customer_order_for_job(job.job_id)['charged_units'])

    def test_sepay_polling_thread_cancels_pending_network_wait(self):
        entered=threading.Event()
        async def blocked(_self,_store):
            entered.set()
            await asyncio.Event().wait()
        thread=SePayThread(CONFIG,self.store)
        with patch.object(SePayClient,'sync',blocked):
            try:
                thread.start()
                self.assertTrue(entered.wait(2))
                thread.request_stop()
                self.assertTrue(thread.wait(2000))
            finally:
                thread.request_stop()
                thread.wait(2000)


if __name__=='__main__':
    unittest.main(verbosity=2)
