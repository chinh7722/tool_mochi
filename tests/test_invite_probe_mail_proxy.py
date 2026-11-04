"""Offline one-account invite probe and independent rotating purchase proxies."""
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from core.platforms.mochichat.runners.invite_referral import MochiInviteRunner,MochiAccountResult
from core.mail.proxy_pool import MailProxyPool
from core.mail import proxy_pool as pools
from core.mail.providers import purchase_policy as policy
from core.proxy.models import ProxyModel
from core.proxy.broker import ProxyBroker
from core.proxy.rotators import RotationResult
from core.proxy.rotators import provider_shared_identity
from core.telegram_users import TelegramUserStore
from decimal import Decimal
from gui.pages.proxy import ProxyPage
from gui.pages.mail_proxy import MailProxyPage
from gui.pages.mochi_chat import MochiChatPage,MochiWorkerThread

RUNNER='core.platforms.mochichat.runners.invite_referral'


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.requests=[]
        self.next_id=0
        async def buy(_products,amount):
            self.requests.append(amount)
            result=[SimpleNamespace(username=f'probe-{self.next_id+i}@example.test',password='pw') for i in range(amount)]
            self.next_id+=amount
            return result
        self.buy=AsyncMock(side_effect=buy)
        context=AsyncMock()
        context.__aenter__.return_value=SimpleNamespace(buy_gmail_domain_info=self.buy)
        self.patch=patch(f'{RUNNER}.GmailVipVn',return_value=context)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.runner=MochiInviteRunner(concurrency=5,mail_batch_dir=self.temp.name,
            google_login_via_proxy=False,app_login_via_proxy=False,redeem_via_proxy=False,mail_via_proxy=False)
        self.runner._process_account=AsyncMock(side_effect=lambda _j,_c,item,*_a,**_kw:
            MochiAccountResult(item.identifier,True,'success'))

    async def test_default_flow_buys_one_then_nine_and_counts_the_probe(self):
        result=await self.runner.run('probe-ok','TEST',10)
        self.assertEqual(self.requests,[1,9])
        self.assertEqual((result.status,result.success_count,result.attempted_count),('completed',10,10))
        data=json.loads(self.runner.mail_batch_path.read_text())['accounts']
        self.assertEqual(len(data),10)
        self.assertTrue(all(row['status']=='success' for row in data))

    async def test_no_second_purchase_until_the_invite_was_redeemed(self):
        entered,finish=asyncio.Event(),asyncio.Event()
        async def process(_j,_c,item,*args,**kwargs):
            entered.set()
            await finish.wait()
            return MochiAccountResult(item.identifier,True,'success')
        self.runner._process_account=process
        task=asyncio.create_task(self.runner.run('probe-gate','TEST',10))
        await entered.wait()
        self.assertEqual(self.requests,[1])
        finish.set()
        self.assertEqual((await task).success_count,10)

    async def test_invalid_probe_stops_and_account_errors_retry_up_to_three_probes(self):
        for status in ('invite_limit_reached','invalid_code','auth_failed','already_redeemed','network_error'):
            self.requests.clear()
            self.runner=MochiInviteRunner(mail_batch_dir=self.temp.name,mail_via_proxy=False,
                google_login_via_proxy=False,app_login_via_proxy=False,redeem_via_proxy=False)
            self.runner._process_account=AsyncMock(side_effect=lambda _j,_c,item,*a,**kw:
                MochiAccountResult(item.identifier,False,status,message='Probe rejected'))
            result=await self.runner.run('probe-'+status,'TEST',10)
            expected=1 if status in ('invite_limit_reached','invalid_code') else 3
            self.assertEqual(self.requests,[1]*expected)
            self.assertEqual(result.success_count,0)
            self.assertEqual(len(self.runner.purchased_accounts),expected)

    async def test_target_one_never_buys_a_second_batch(self):
        result=await self.runner.run('one','TEST',1)
        self.assertEqual(self.requests,[1])
        self.assertEqual(result.status,'completed')

    async def test_remaining_accounts_fill_all_five_slots_after_probe_success(self):
        reached,finish=asyncio.Event(),asyncio.Event()
        active=0
        async def process(_j,_c,item,*a,**kw):
            nonlocal active
            if item.email=='probe-0@example.test':
                return MochiAccountResult(item.identifier,True,'success')
            active+=1
            if active==5:
                reached.set()
            try:
                await finish.wait()
                return MochiAccountResult(item.identifier,True,'success')
            finally:
                active-=1
        self.runner._process_account=process
        task=asyncio.create_task(self.runner.run('max-after-probe','TEST',10))
        await asyncio.wait_for(reached.wait(),2)
        self.assertEqual(self.requests,[1,9])
        self.assertEqual(active,5)
        finish.set()
        self.assertEqual((await task).success_count,10)

    async def test_continued_job_subtracts_existing_successes_and_the_probe(self):
        result=await self.runner.run('continued','TEST',10,initial_success_count=3)
        self.assertEqual(self.requests,[1,6])
        self.assertEqual(result.success_count,10)

    async def test_failure_in_remaining_batch_preserves_probe_and_does_not_buy_again(self):
        async def process(_j,_c,item,*a,**kw):
            ok=item.email=='probe-0@example.test'
            return MochiAccountResult(item.identifier,ok,'success' if ok else 'auth_failed')
        self.runner._process_account=process
        result=await self.runner.run('remaining-fail','TEST',10)
        self.assertEqual(self.requests,[1,9])
        self.assertEqual(result.success_count,1)
        self.assertEqual(len(self.runner.purchased_accounts),10)

    async def test_extra_mail_delivered_with_probe_reduces_second_purchase(self):
        original=self.buy.side_effect
        async def oversell(products,amount):
            if not self.requests:
                result=await original(products,4)
                self.requests[0]=1
                return result
            return await original(products,amount)
        self.buy.side_effect=oversell
        result=await self.runner.run('extra','TEST',10)
        self.assertEqual(self.requests,[1,6])
        self.assertEqual(result.success_count,10)

    async def test_cancellation_during_probe_never_buys_remaining_mail(self):
        entered=asyncio.Event()
        async def process(*a,**kw):
            entered.set()
            await asyncio.Event().wait()
        self.runner._process_account=process
        task=asyncio.create_task(self.runner.run('cancel','TEST',10))
        await entered.wait()
        task.cancel()
        result=await task
        self.assertEqual(self.requests,[1])
        self.assertEqual(result.status,'cancelled')

    async def test_waiting_for_mail_does_not_hold_any_account_proxy_slot(self):
        waiting,finish=asyncio.Event(),asyncio.Event()
        original=self.buy.side_effect
        async def buy(products,amount):
            waiting.set()
            await finish.wait()
            return await original(products,amount)
        self.buy.side_effect=buy
        client=SimpleNamespace(lease=AsyncMock(return_value=SimpleNamespace(proxy=None,is_direct=True,metadata={})),
            release=AsyncMock(),release_job=AsyncMock())
        self.runner.proxy_client=client
        self.runner.google_login_via_proxy=True
        self.runner.app_login_via_proxy=True
        self.runner.mail_via_proxy=True
        self.runner.mail_proxy_pool=SimpleNamespace(session=lambda:None)
        task=asyncio.create_task(self.runner.run('no-idle-account-lease','TEST',10))
        await waiting.wait()
        client.lease.assert_not_called()
        finish.set()
        self.assertEqual((await task).success_count,10)


class MailPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.gates={}
        self.trace=[]
        owner=self
        class Provider:
            def __init__(self,reference,current_endpoint=None):
                self.api_key=reference
                self.model=ProxyModel.from_string(current_endpoint)
                owner.gates.setdefault(reference,threading.Event())
            async def get_current_or_rotate(self,**options):
                owner.trace.append(('rotate',self.api_key,options))
                while not owner.gates[self.api_key].is_set():
                    await asyncio.sleep(0.01)
                return RotationResult(True,replace(self.model,port=self.model.port+100))
        self.factory=Provider
        self.entries=[(f'http://u:p@127.0.0.1:{31000+i}',uuid.uuid4().hex) for i in range(2)]
        self.pool=MailProxyPool(self.entries,state_directory=self.temp.name,provider_factory=self.factory,timeout=1)

    async def asyncTearDown(self):
        for gate in self.gates.values():
            gate.set()
        for _ in range(200):
            if all(not state.rotating for state in self.pool.states):
                break
            await asyncio.sleep(0.01)

    async def test_rotating_first_proxy_is_skipped_and_second_is_used_immediately(self):
        first=await self.pool.acquire()
        pools._marker(first.state)
        first.consumed=True
        self.pool.release(first)
        second=await asyncio.wait_for(self.pool.acquire(),0.3)
        self.assertIsNot(first.state,second.state)
        self.assertTrue(first.state.rotating)
        self.pool.release(second)

    async def test_waits_only_when_all_proxies_are_rotating_then_uses_first_ready(self):
        for _ in range(2):
            lease=await self.pool.acquire()
            lease.consumed=True
            self.pool.release(lease)
        task=asyncio.create_task(self.pool.acquire())
        await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        self.gates[self.entries[1][1]].set()
        lease=await asyncio.wait_for(task,0.5)
        self.assertIs(lease.state,self.pool.states[1])
        self.assertEqual((lease.state.proxy.username,lease.state.proxy.password),('u','p'))
        self.pool.release(lease)

    async def test_two_jobs_share_exclusive_proxy_state(self):
        another=MailProxyPool(self.entries,state_directory=self.temp.name,provider_factory=self.factory)
        first=await self.pool.acquire()
        second=await another.acquire()
        self.assertIsNot(first.state,second.state)
        self.pool.release(first)
        another.release(second)

    async def test_successful_manual_check_can_recover_failed_key_without_reusing_dirty_ip(self):
        state=self.pool.states[0]
        state.failed=True
        state.required=True
        pools._marker(state)
        pools.acknowledge_mail_proxy_check(state.reference,state.proxy,rotated=False)
        self.assertFalse(state.failed)
        self.assertTrue(state.required)
        pools.acknowledge_mail_proxy_check(state.reference,replace(state.proxy,port=state.proxy.port+100),rotated=True)
        self.assertFalse(state.required)
        self.assertFalse(json.loads(state.marker.read_text())['requires_rotation'])

    async def test_same_key_cannot_be_leased_by_account_broker_during_purchase(self):
        lease=await self.pool.acquire()
        proxy=ProxyModel.from_string(self.entries[0][0],rotation_url=self.entries[0][1])
        broker=ProxyBroker([proxy],allow_direct_without_proxies=False)
        with self.assertRaises(asyncio.TimeoutError):
            await broker.lease('account',timeout=0.05)
        self.pool.release(lease)
        account=await broker.lease('account',timeout=0.2)
        await broker.release(account)

    async def test_each_post_uses_one_proxy_for_both_balance_checks_and_then_rotates(self):
        trace=self.trace
        connections=[]
        def connection_factory(**options):
            index=len(connections)
            async def get(*a,**kw):
                trace.append(('get',index))
                return 100 if sum(x==('get',index) for x in trace)==1 else 90
            async def post(*a,**kw):
                trace.append(('post',index))
                return SimpleNamespace(json=lambda:{'status':'success','data':['a|b']})
            raw=SimpleNamespace(get=get,post=post)
            context=AsyncMock()
            context.__aenter__.return_value=raw
            connections.append((options,raw))
            return context
        def receipt(data,before,after):
            if after is not None:
                trace.append(('receipt',before,after))
        with patch('core.mail.proxy_pool.AsyncSession',side_effect=connection_factory), \
                patch.object(policy._PurchaseSlot,'before_request',new=AsyncMock()):
            await policy.purchase_json(None,'https://offline.test',{'id':1,'amount':1},'offline.test','key',
                balance_probe=lambda session:session.get('profile'),on_transaction=receipt,purchase_route=self.pool.session)
        self.assertEqual([x[0] for x in trace[:4]],['get','post','get','receipt'])
        self.assertEqual(connections[0][0]['proxies']['https'],self.entries[0][0])
        self.assertTrue(self.pool.states[0].required or self.pool.states[0].rotating)
        self.assertTrue(self.pool.states[0].marker.exists())

    async def test_confirmed_rate_rejection_switches_proxy_before_next_post(self):
        contexts=[]
        def factory(**options):
            data=({'status':'error','msg':'Thao tác quá nhanh, chờ 1s'} if not contexts
                else {'status':'success','data':['a|b']})
            raw=SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(json=lambda:data,headers={},status_code=200)))
            context=AsyncMock()
            context.__aenter__.return_value=raw
            contexts.append((options,raw))
            return context
        with patch('core.mail.proxy_pool.AsyncSession',side_effect=factory), \
                patch.object(policy._PurchaseSlot,'before_request',new=AsyncMock()):
            await policy.purchase_json(None,'https://offline.test',{'id':1,'amount':9},'offline.test','key-rate',
                purchase_route=self.pool.session)
        self.assertEqual(len(contexts),2)
        self.assertNotEqual(contexts[0][0]['proxies'],contexts[1][0]['proxies'])

    async def test_uncertain_post_is_not_retried_and_proxy_is_rotated(self):
        raw=SimpleNamespace(post=AsyncMock(side_effect=TimeoutError('Lost response')))
        context=AsyncMock()
        context.__aenter__.return_value=raw
        with patch('core.mail.proxy_pool.AsyncSession',return_value=context), \
                patch.object(policy._PurchaseSlot,'before_request',new=AsyncMock()):
            with self.assertRaises(ValueError):
                await policy.purchase_json(None,'offline',{'id':1,'amount':1},'offline','uncertain',purchase_route=self.pool.session)
        raw.post.assert_awaited_once()
        self.assertTrue(self.pool.states[0].marker.exists())

    async def test_completed_rotation_endpoint_survives_restart_without_storing_credentials(self):
        lease=await self.pool.acquire()
        pools._marker(lease.state)
        lease.consumed=True
        self.pool.release(lease)
        self.gates[self.entries[0][1]].set()
        for _ in range(200):
            if not lease.state.rotating:
                break
            await asyncio.sleep(0.01)
        self.assertFalse(lease.state.required)
        saved=lease.state.marker.read_text()
        self.assertNotIn('u:p',saved)
        self.assertNotIn(self.entries[0][1],saved)
        identity=provider_shared_identity(self.factory(self.entries[0][1],current_endpoint=self.entries[0][0]))
        with pools._LOCK:
            pools._STATES.pop(identity)
        reopened=MailProxyPool([self.entries[0]],state_directory=self.temp.name,provider_factory=self.factory)
        resumed=await reopened.acquire()
        self.assertEqual(resumed.state.proxy.port,31100)
        reopened.release(resumed)

    async def test_unfinished_rotation_is_required_after_restart_before_another_purchase(self):
        lease=await self.pool.acquire()
        pools._marker(lease.state)
        lease.consumed=True
        self.pool.release(lease)
        for _ in range(200):
            if lease.state.loop is not None:
                break
            await asyncio.sleep(0.01)
        lease.state.stop.set()
        lease.state.loop.call_soon_threadsafe(lease.state.task.cancel)
        for _ in range(200):
            if not lease.state.rotating:
                break
            await asyncio.sleep(0.01)
        identity=provider_shared_identity(self.factory(self.entries[0][1],current_endpoint=self.entries[0][0]))
        with pools._LOCK:
            pools._STATES.pop(identity)
        reopened=MailProxyPool([self.entries[0]],state_directory=self.temp.name,provider_factory=self.factory)
        task=asyncio.create_task(reopened.acquire())
        await asyncio.sleep(0.03)
        self.assertFalse(task.done())
        self.gates[self.entries[0][1]].set()
        resumed=await asyncio.wait_for(task,0.5)
        self.assertEqual(resumed.state.proxy.port,31100)
        reopened.release(resumed)

    async def test_http_close_failure_does_not_discard_a_confirmed_purchase(self):
        raw=SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(json=lambda:{'status':'success','data':['a|pw']})))
        context=AsyncMock()
        context.__aenter__.return_value=raw
        context.__aexit__.side_effect=OSError('close failed')
        with patch('core.mail.proxy_pool.AsyncSession',return_value=context), \
                patch.object(policy._PurchaseSlot,'before_request',new=AsyncMock()):
            result=await policy.purchase_json(None,'offline',{'id':1,'amount':1},'offline','close-failure',purchase_route=self.pool.session)
        self.assertEqual(result['data'],['a|pw'])
        raw.post.assert_awaited_once()

    async def test_cancel_during_http_close_still_releases_the_purchase_lease(self):
        raw=SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(json=lambda:{'status':'success','data':['a|pw']})))
        context=AsyncMock()
        context.__aenter__.return_value=raw
        context.__aexit__.side_effect=asyncio.CancelledError()
        with patch('core.mail.proxy_pool.AsyncSession',return_value=context), \
                patch.object(policy._PurchaseSlot,'before_request',new=AsyncMock()):
            with self.assertRaises(asyncio.CancelledError):
                await policy.purchase_json(None,'offline',{'id':1,'amount':1},'offline','cancel-close',purchase_route=self.pool.session)
        self.assertFalse(self.pool.states[0].in_use)
        self.assertTrue(self.pool.states[0].required)

    async def test_two_stage_purchase_uses_mail_proxies_and_records_both_actual_shop_debits(self):
        store=TelegramUserStore(Path(self.temp.name)/'ledger.sqlite3')
        store.upsert_user(123,'Operator','partner')
        store.record_job('accounting-probe',123,'TEST',1000,10,sale_rate_units=100000)
        runner=MochiInviteRunner(concurrency=5,mail_batch_dir=self.temp.name,mail_provider_api_key='offline-accounting',
            mail_proxy_pool=self.pool,mail_via_proxy=True,google_login_via_proxy=False,
            app_login_via_proxy=False,redeem_via_proxy=False,mail_purchase_sink=store.record_purchase)
        runner._process_account=AsyncMock(side_effect=lambda _j,_c,item,*a,**kw:MochiAccountResult(item.identifier,True,'success'))
        balance=Decimal('10000')
        purchased=[]
        count=0
        def connection_factory(**options):
            async def get(*a,**kw):
                return SimpleNamespace(json=lambda:{'status':'success','data':{'money':str(balance)}})
            async def post(_url,*,data):
                nonlocal balance,count
                amount=data['amount']
                purchased.append(amount)
                balance-=Decimal('2.5')*amount
                accounts=[f'accounting-mail-{count+i}@example.test|pw' for i in range(amount)]
                count+=amount
                return SimpleNamespace(json=lambda:{'status':'success','data':accounts},status_code=200,headers={})
            context=AsyncMock()
            context.__aenter__.return_value=SimpleNamespace(get=get,post=post)
            return context
        unused=SimpleNamespace(close=AsyncMock())
        with patch('core.mail.providers.gmailvip_vn.AsyncSession',return_value=unused), \
                patch('core.mail.proxy_pool.AsyncSession',side_effect=connection_factory), \
                patch.object(policy._PurchaseSlot,'before_request',new=AsyncMock()):
            result=await runner.run('accounting-probe','TEST',10)
        self.assertEqual(result.status,'completed')
        self.assertEqual(purchased,[1,9])
        self.assertEqual(store.get_user(123).cost_units,2500)
        self.assertEqual(len(store.list_purchases(123)),2)
        self.assertEqual(len(runner.purchased_accounts),10)

    async def test_both_shop_priority_uses_new_routes_for_stock_fallback_in_both_purchase_stages(self):
        for gate in self.gates.values():
            gate.set()
        store=TelegramUserStore(Path(self.temp.name)/'priority.sqlite3')
        store.upsert_user(123,'Operator','partner')
        store.record_job('priority-probe',123,'TEST',1000,10)
        runner=MochiInviteRunner(mail_provider='multi_shop',mail_api_keys={'gmailvip_vn':'g-key','1trieumail_com':'one-key'},
            mail_product_ids='gmailvip:1,1trieumail:2',mail_proxy_pool=self.pool,mail_via_proxy=True,
            google_login_via_proxy=False,app_login_via_proxy=False,redeem_via_proxy=False,
            mail_batch_dir=self.temp.name,mail_purchase_sink=store.record_purchase)
        runner._process_account=AsyncMock(side_effect=lambda _j,_c,item,*a,**kw:MochiAccountResult(item.identifier,True,'success'))
        balances={'gmailvip.vn':Decimal('10000'),'1trieumail.com':Decimal('10000')}
        calls=[]
        count=0
        def connection_factory(**options):
            async def get(url,**kw):
                domain=url.split('/')[2]
                return SimpleNamespace(json=lambda:{'status':'success','data':{'money':str(balances[domain])}})
            async def post(url,*,data):
                nonlocal count
                domain=url.split('/')[2]
                calls.append((domain,data['amount']))
                if domain=='gmailvip.vn':
                    return SimpleNamespace(json=lambda:{'status':'error','msg':'Hết hàng'},status_code=200,headers={})
                balances[domain]-=Decimal('2.5')*data['amount']
                emails=[f'priority-mail-{count+i}@example.test|pw' for i in range(data['amount'])]
                count+=data['amount']
                return SimpleNamespace(json=lambda:{'status':'success','data':emails},status_code=200,headers={})
            context=AsyncMock()
            context.__aenter__.return_value=SimpleNamespace(get=get,post=post)
            return context
        unused=SimpleNamespace(close=AsyncMock())
        with patch('core.mail.providers.gmailvip_vn.AsyncSession',return_value=unused), \
                patch('core.mail.providers.one_trieu_mail.AsyncSession',return_value=unused), \
                patch('core.mail.proxy_pool.AsyncSession',side_effect=connection_factory), \
                patch.object(policy._PurchaseSlot,'before_request',new=AsyncMock()), \
                patch.object(policy,'wait_for_purchase_retry',new=AsyncMock()):
            result=await runner.run('priority-probe','TEST',10)
        self.assertEqual(result.status,'completed')
        self.assertEqual(calls,[('gmailvip.vn',1),('1trieumail.com',1),('gmailvip.vn',9),('1trieumail.com',9)])
        self.assertEqual(store.get_user(123).cost_units,2500)


class MailProxyUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings=QSettings(str(Path(self.temp.name)/'settings.ini'),QSettings.IniFormat)
        self.account=ProxyPage(settings=self.settings)
        self.mail=MailProxyPage(settings=self.settings)
        self.page=MochiChatPage(settings=self.settings,proxy_page=self.account)
        self.page.mail_proxy_page=self.mail
        self.addCleanup(self.page.close)
        self.addCleanup(self.mail.close)
        self.addCleanup(self.account.close)

    def test_separate_namespace_and_rotating_only_mail_list(self):
        self.assertIsNone(self.mail._parse_proxy_line('127.0.0.1:31001'))
        self.assertIsNotNone(self.account._parse_proxy_line('127.0.0.1:31001'))
        item=self.mail._parse_proxy_line('127.0.0.1:31001:u:p|offline-key')
        self.mail._proxies[item['id']]=item
        self.mail._save_to_settings()
        self.assertFalse(self.settings.value('proxy/managed_list',''))
        self.assertIn('31001',self.settings.value('mail_proxy/managed_list',''))
        self.assertEqual(len(self.mail.get_active_proxies()),1)

    def test_missing_mail_pool_stops_before_registration_or_purchase(self):
        self.page.input_api_key.setText('offline')
        self.page.input_code.setText('TEST')
        self.page.chk_use_proxy.setChecked(True)
        registrar=AsyncMock()
        self.page.local_job_registrar=registrar
        with patch.object(MochiWorkerThread,'start') as start:
            self.page._on_start_clicked()
        self.assertIn('Proxy mua mail',self.page.validation_label.text())
        start.assert_not_called()
        registrar.assert_not_called()

    def test_mail_only_proxy_does_not_require_an_account_proxy_pool(self):
        item=self.mail._parse_proxy_line('127.0.0.1:31001:u:p|offline-key-only')
        self.mail._proxies[item['id']]=item
        self.page.input_api_key.setText('offline')
        self.page.input_code.setText('TEST')
        self.page.chk_use_proxy.setChecked(True)
        self.page.chk_google_via_proxy.setChecked(False)
        self.page.chk_app_login_via_proxy.setChecked(False)
        self.page.chk_redeem_via_proxy.setChecked(False)
        with patch.object(MochiWorkerThread,'start'):
            job=self.page._on_start_clicked()
        self.assertIsNotNone(job)
        runner=self.page._workers[job].runner
        self.assertIsNotNone(runner.mail_proxy_pool)
        self.assertFalse(runner.proxy_client.broker.has_proxies)
        self.assertTrue(runner.probe_invite_first)


if __name__=='__main__':
    unittest.main(verbosity=2)
