"""Preemption, persistent safe pause and paid-mail reuse without live purchases."""
import asyncio
from datetime import datetime,timedelta
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import AsyncMock,Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'tests'))
from core.job_schedule import VIETNAM_TIME
from core.platforms.mochichat.invite_quota import InviteQuotaStore
from core.platforms.mochichat.invite_quota_lock import invite_code_guard
from core.platforms.mochichat.runners.invite_referral import MochiInviteRunner,MochiAccountResult,MochiInviteJobSummary
from gui.pages.mochi_chat import MochiWorkerThread
import test_stock_accounts as stock_fixture
import test_replacement_mail as mail_fixture


class PriorityRunnerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=mail_fixture.ReplacementTests.asyncSetUp

    def runner(self,event,*,pause_after=0,outcome='success',resume=None,streak=0):
        r=MochiInviteRunner(concurrency=1,mail_batch_dir=self.temp.name,probe_invite_first=False,
            pause_event=event,resume_mail_batch=resume,failure_streak=streak)
        count=0
        async def process(_j,_c,item,*a,**k):
            nonlocal count
            count+=1
            if pause_after==count:event.set()
            return MochiAccountResult(item.identifier,outcome=='success',outcome)
        r._process_account=AsyncMock(side_effect=process)
        return r

    async def test_pause_finishes_current_account_and_preserves_unused_batch_for_resume(self):
        event=threading.Event();r=self.runner(event,pause_after=1)
        first=await r.run('background','CODE',4)
        self.assertEqual((first.status,first.success_count),('paused',1))
        self.assertEqual(self.purchases,[4]);self.assertEqual(r._process_account.await_count,1)
        saved=json.loads(r.mail_batch_path.read_text())
        self.assertEqual(sum(a['status']=='pending' for a in saved['accounts']),3)
        resumed=self.runner(threading.Event(),resume=r.mail_batch_path)
        second=await resumed.run('background','CODE',4,initial_success_count=1)
        self.assertEqual((second.status,second.success_count),('completed',4))
        self.assertEqual(self.purchases,[4])
        self.assertTrue(all(a['status']=='success' for a in json.loads(r.mail_batch_path.read_text())['accounts']))

    async def test_pause_before_start_never_purchases(self):
        event=threading.Event();event.set()
        result=await self.runner(event).run('paused','CODE',3)
        self.assertEqual(result.status,'paused');self.assertFalse(self.purchases)

    async def test_resume_exhausted_probe_batch_buys_only_remaining(self):
        event=threading.Event();r=self.runner(event,pause_after=1);r.probe_invite_first=True
        first=await r.run('probe','CODE',4)
        self.assertEqual(first.status,'paused');self.assertEqual(self.purchases,[1])
        resumed=self.runner(threading.Event(),resume=r.mail_batch_path)
        second=await resumed.run('probe','CODE',4,initial_success_count=1)
        self.assertEqual(second.status,'completed');self.assertEqual(self.purchases,[1,3])

    async def test_pause_retains_failure_streak_and_does_not_retry_failed_paid_accounts(self):
        r=self.runner(threading.Event(),pause_after=1,outcome='auth_failed')
        first=await r.run('fail','CODE',4)
        self.assertEqual((first.status,r.failure_streak),('paused',1))
        resumed=self.runner(threading.Event(),resume=r.mail_batch_path,streak=r.failure_streak,outcome='auth_failed')
        resumed.failure_budget_used=first.failure_count
        second=await resumed.run('fail','CODE',4)
        self.assertEqual((second.status,second.failure_count),('failed',2))
        self.assertEqual(self.purchases,[4])
        old=first.results[0].account_identifier
        self.assertNotIn(old,[c.args[2].identifier for c in resumed._process_account.call_args_list])

    async def test_manual_cancel_wins_over_priority_pause(self):
        event=threading.Event();event.set();cancel=threading.Event();cancel.set()
        r=self.runner(event);r.cancel_event=cancel
        self.assertEqual((await r.run('stop','CODE',4)).status,'cancelled')

    async def test_second_pause_uses_newest_paid_batch_and_never_rebuys_spares(self):
        first=self.runner(threading.Event(),pause_after=1);first.probe_invite_first=True
        await first.run('twice','CODE',4)
        second=self.runner(threading.Event(),pause_after=1,resume=first.mail_batch_path)
        await second.run('twice','CODE',4,initial_success_count=1)
        self.assertEqual(self.purchases,[1,3])
        third=self.runner(threading.Event(),resume=second.mail_batch_path)
        result=await third.run('twice','CODE',4,initial_success_count=2)
        self.assertEqual((result.status,result.success_count),('completed',4));self.assertEqual(self.purchases,[1,3])

    async def test_failed_seed_is_skipped_and_remaining_paid_seed_is_used_before_topup(self):
        first=self.runner(threading.Event(),pause_after=1,outcome='auth_failed')
        result=await first.run('seed','CODE',4)
        second=self.runner(threading.Event(),resume=first.mail_batch_path,streak=first.failure_streak)
        second.probe_invite_first=True;second.failure_budget_used=result.failure_count
        final=await second.run('seed','CODE',4)
        self.assertEqual((final.status,final.success_count),('completed',4))
        self.assertEqual(self.purchases,[4,1])

    async def test_waiting_for_same_code_can_pause_without_holding_customer_lock(self):
        store=InviteQuotaStore(Path(self.temp.name)/'quota.sqlite3')
        event=threading.Event();runner=self.runner(event);runner.invite_quota_store=store
        async with invite_code_guard(store,'CODE'):
            task=asyncio.create_task(runner.run('wait','CODE',4))
            await asyncio.sleep(.02);event.set()
            result=await asyncio.wait_for(task,2)
        self.assertEqual(result.status,'paused');self.assertFalse(self.purchases)

    async def test_paid_batch_write_failure_prevents_unsafe_auto_resume(self):
        runner=self.runner(threading.Event(),pause_after=1)
        with patch('core.platforms.mochichat.runners.invite_referral.write_batch',side_effect=OSError('offline write failure')):
            result=await runner.run('write-fail','CODE',4)
        self.assertEqual((result.status,result.success_count),('failed',1))
        self.assertIn('lô mail',result.error_message)

    async def test_pause_does_not_turn_short_shop_delivery_into_permission_to_buy_more(self):
        from types import SimpleNamespace
        self.client.buy_gmail_domain_info.side_effect=None
        self.client.buy_gmail_domain_info.return_value=[SimpleNamespace(username='short@example.test',password='pw')]
        first=self.runner(threading.Event(),pause_after=1)
        result=await first.run('short','CODE',4)
        self.assertEqual(result.status,'paused')
        resumed=self.runner(threading.Event(),resume=first.mail_batch_path)
        final=await resumed.run('short','CODE',4,initial_success_count=1)
        self.assertEqual((final.status,final.success_count),('failed',1))
        self.assertEqual(self.client.buy_gmail_domain_info.await_count,1)

    async def test_waiting_for_proxy_can_yield_to_customer_without_waiting_for_pool_timeout(self):
        from types import SimpleNamespace
        event=threading.Event();runner=self.runner(event);entered=asyncio.Event()
        async def lease(*a,**k):entered.set();await asyncio.Event().wait()
        runner.proxy_client=SimpleNamespace(lease=AsyncMock(side_effect=lease),release=AsyncMock(),release_job=AsyncMock())
        task=asyncio.create_task(runner.run('proxy-wait','CODE',4))
        await entered.wait();event.set()
        result=await asyncio.wait_for(task,2)
        self.assertEqual(result.status,'paused');self.assertFalse(self.purchases)


class CustomerPriorityTests(unittest.TestCase):
    setUpClass=classmethod(stock_fixture.StockControllerTests.setUpClass.__func__)
    account=stock_fixture.StockControllerTests.account

    def setUp(self):
        stock_fixture.StockControllerTests.setUp(self)
        self.started=[]
        self.patch=patch.object(MochiWorkerThread,'start',lambda w:self.started.append(w.job_id));self.patch.start()
        self.m.spin_job_concurrency.setValue(1)

    def tearDown(self):
        stock_fixture.StockControllerTests.tearDown(self);self.patch.stop()

    def stock(self):
        row=self.account();self.controller._dispatch(row)
        jid=self.store.all()[0]['job_id'];self.m._check_scheduled_jobs(datetime.now(VIETNAM_TIME)+timedelta(seconds=1))
        self.assertIn(jid,self.m._active_job_ids)
        return row,jid

    def pause_stock(self,jid):
        self.m._on_job_finished(MochiInviteJobSummary(jid,'STOCKCODE',15,1,1,0,0,'paused',()))
        self.m._on_worker_stopped(jid)

    def test_customer_preempts_stock_then_stock_resumes_same_job_with_progress(self):
        row,jid=self.stock()
        customer=self.m._on_start_clicked(code='CUSTOMER',points=100)
        self.assertTrue(self.m._workers[jid]._pause_event.is_set())
        self.assertFalse(self.m._workers[jid]._cancel_event.is_set())
        self.assertNotIn(customer,self.started)
        self.pause_stock(jid)
        self.assertEqual(self.started,[jid,customer])
        self.assertIsNone(self.t.store.get_job(jid).finalized_at)
        self.m._check_scheduled_jobs(datetime.now(VIETNAM_TIME)+timedelta(seconds=10))
        self.assertEqual(self.started,[jid,customer])
        self.m._on_job_finished(MochiInviteJobSummary(customer,'CUSTOMER',1,1,1,0,0,'completed',()))
        self.m._on_worker_stopped(customer)
        self.m._check_scheduled_jobs(datetime.now(VIETNAM_TIME)+timedelta(seconds=10))
        self.assertEqual(self.started,[jid,customer,jid])
        self.assertEqual(self.m._workers[jid].initial_success_count,1)
        self.assertEqual(self.t.store.get_job(jid).success_count,1)

    def test_due_customer_combo_preempts_but_future_schedule_does_not(self):
        row,jid=self.stock()
        when=datetime.now(VIETNAM_TIME)+timedelta(minutes=2)
        customer=self.m._on_start_clicked(code='DUECODE',points=5500,combo=True,scheduled_at=when)
        self.assertFalse(self.m._workers[jid]._pause_event.is_set())
        self.m._check_scheduled_jobs(when+timedelta(seconds=1))
        self.assertTrue(self.m._workers[jid]._pause_event.is_set())
        self.assertIn(customer,self.m._pending_jobs)

    def test_customer_waits_for_safe_pause_even_when_another_slot_is_free(self):
        row,jid=self.stock();self.m.spin_job_concurrency.setValue(2)
        customer=self.m._on_start_clicked(code='CUSTOMER',points=100)
        self.assertNotIn(customer,self.started)
        self.assertTrue(self.m._workers[jid]._pause_event.is_set())

    def test_stock_preparation_and_dispatch_do_not_start_during_customer_work(self):
        customer=self.m._on_start_clicked(code='CUSTOMER',points=100)
        with patch.object(self.controller,'_buy') as buy,patch.object(self.controller,'_login') as login,patch.object(self.controller,'_dispatch') as dispatch:
            self.controller.tick()
        buy.assert_not_called();login.assert_not_called();dispatch.assert_not_called()

    def test_in_flight_mail_purchase_is_not_cancelled_and_customer_waits_for_result(self):
        worker=Mock();self.controller.worker=worker;self.controller.phase='buy'
        customer=self.m._on_start_clicked(code='CUSTOMER',points=100)
        worker.request_cancel.assert_not_called();self.assertNotIn(customer,self.started)
        self.controller.worker=None;self.controller.phase=None;self.m._drain_job_queue()
        self.assertIn(customer,self.started)

    def test_explicit_stop_of_priority_paused_account_prevents_auto_resume(self):
        row,jid=self.stock();customer=self.m._on_start_clicked(code='CUSTOMER',points=100)
        self.pause_stock(jid);self.controller.stop([row['id']])
        self.assertIsNotNone(self.t.store.get_job(jid).finalized_at)
        self.assertNotIn(jid,self.m._scheduled_jobs)

    def test_checkpoint_keeps_paid_batch_and_failure_streak_without_finalizing_job(self):
        row,jid=self.stock();worker=self.m._workers[jid]
        worker.runner.mail_batch_path=Path(self.temp.name)/'paid.json';worker.runner.failure_streak=2
        self.m._on_job_finished(MochiInviteJobSummary(jid,'STOCKCODE',15,3,1,2,0,'paused',()))
        saved=self.t.store.get_job(jid);data=json.loads(saved.schedule_data)
        self.assertIsNone(saved.finalized_at)
        self.assertEqual(data['resume_mail_batch'],str(worker.runner.mail_batch_path))
        self.assertEqual(data['priority_failure_streak'],2)
        self.assertTrue(data['priority_paused'])
        self.assertEqual(self.t.store.statistics(datetime.now(VIETNAM_TIME).strftime('%Y-%m'))['customer_counts']['month'],0)

    def test_all_customer_jobs_finish_before_background_is_admitted_again(self):
        row,jid=self.stock()
        first=self.m._on_start_clicked(code='FIRST',points=100)
        second=self.m._on_start_clicked(code='SECOND',points=100)
        self.pause_stock(jid)
        self.m._on_job_finished(MochiInviteJobSummary(first,'FIRST',1,1,1,0,0,'completed',()))
        self.m._on_worker_stopped(first)
        self.m._check_scheduled_jobs(datetime.now(VIETNAM_TIME)+timedelta(seconds=10))
        self.assertEqual(self.started,[jid,first,second])
        self.assertNotIn(jid,self.m._workers)

    def test_profile_lookup_is_cancelled_safely_when_customer_arrives(self):
        worker=Mock();self.controller.worker=worker;self.controller.phase='login'
        self.m._on_start_clicked(code='CUSTOMER',points=100)
        worker.request_cancel.assert_called_once()
        self.controller.worker=None;self.controller.phase=None

    def test_lookup_cancelled_before_factory_starts_returns_mail_to_creation_queue(self):
        self.store.create_batch(1,2000,False,'123');batch=self.store.batches()[0]
        from types import SimpleNamespace
        self.store.save_delivery(batch,[SimpleNamespace(username='waiting@example.test',password='offline')])
        row=self.store.all(internal=True)[0];captured=[]
        def start(_factory,_error,_result,on_finished):captured.append(on_finished)
        with patch.object(self.controller,'_start',side_effect=start):self.controller._login(row)
        self.assertEqual(self.store.all(internal=True)[0]['status'],'logging_in')
        captured[0]()
        self.assertEqual(self.store.all(internal=True)[0]['status'],'creating')

    def test_stock_list_reports_priority_wait_instead_of_failure(self):
        row,jid=self.stock();self.m._on_start_clicked(code='CUSTOMER',points=100)
        self.pause_stock(jid);self.controller._sync_jobs()
        self.assertEqual(self.store.all()[0]['status'],'priority_wait')
        self.assertIn('ưu tiên khách',self.m.job_dispatch_text(jid))

    def test_restart_restores_paused_stock_and_due_customer_still_goes_first(self):
        from gui.pages.mochi_chat import MochiChatPage
        from gui.pages.telegram import TelegramPage
        row,jid=self.stock();customer=self.m._on_start_clicked(code='CUSTOMER',points=5500,combo=True)
        self.m._check_scheduled_jobs(datetime.now(VIETNAM_TIME)+timedelta(seconds=1))
        self.pause_stock(jid)
        other=MochiChatPage(settings=self.settings);other._schedule_timer.stop()
        page=TelegramPage(self.settings,other);other.stock_controller.timer.stop()
        try:
            self.assertTrue(other._session_jobs[jid].priority_paused)
            self.assertEqual(other._session_jobs[jid].success_count,1)
            other._check_scheduled_jobs(datetime.now(VIETNAM_TIME)+timedelta(seconds=1))
            self.assertIn(customer,other._active_job_ids)
            self.assertNotIn(jid,other._workers)
        finally:
            page.shutdown_workers();other.shutdown_workers();page.deleteLater();other.deleteLater();self.app.processEvents()

    def test_completed_stock_waiting_for_thread_cleanup_keeps_completed_status(self):
        row,jid=self.stock()
        self.m._on_job_finished(MochiInviteJobSummary(jid,'STOCKCODE',15,15,15,0,0,'completed',()))
        self.m._on_start_clicked(code='CUSTOMER',points=100)
        self.assertEqual(self.m._session_jobs[jid].status,'Hoàn thành')
        self.assertFalse(self.m._workers[jid]._pause_event.is_set())


if __name__=='__main__':unittest.main()
