"""Offline purchase gating and confirmed-success accounting across invite jobs."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from core.platforms.mochichat.invite_quota import InviteQuotaStore
from core.platforms.mochichat.runners.invite_referral import (
    MochiAccountItem, MochiAccountResult, MochiInviteRunner,
)

RUNNER='core.platforms.mochichat.runners.invite_referral'


def item(index):
    return MochiAccountItem(f'account-{index}',f'account-{index}@example.test','offline-password')


class InviteQuotaRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.directory=Path(self.temp.name)
        self.store=InviteQuotaStore(self.directory/'quota.sqlite3')
        self.requests=[]
        self.purchase_index=0

        async def buy(products,amount):
            self.requests.append(amount)
            result=[SimpleNamespace(username=f'bought-{self.purchase_index+i}@example.test',
                                    password='offline-password') for i in range(amount)]
            self.purchase_index+=amount
            return result
        self.buy=AsyncMock(side_effect=buy)
        context=AsyncMock()
        context.__aenter__.return_value=SimpleNamespace(buy_gmail_domain_info=self.buy)
        self.provider_patch=patch(f'{RUNNER}.GmailVipVn',return_value=context)
        self.provider=self.provider_patch.start()
        self.addCleanup(self.provider_patch.stop)
        self.cleanup_patch=patch(f'{RUNNER}.browser_manager.release_by_owner',AsyncMock())
        self.cleanup_patch.start()
        self.addCleanup(self.cleanup_patch.stop)
        self.addCleanup(self.temp.cleanup)

    def seed(self,count,*,code='TEST',when=None):
        timestamp=when or datetime.now(timezone.utc)-timedelta(minutes=1)
        for index in range(count):
            self.store.record_success(code,'old-job',f'old-{index}@example.test',occurred_at=timestamp)

    def runner(self,*,store=None,process=None,cancel_event=None):
        proxy=SimpleNamespace(lease=AsyncMock(),release=AsyncMock(),release_job=AsyncMock())
        runner=MochiInviteRunner(concurrency=1,proxy_client=proxy,
            invite_quota_store=self.store if store is None else store,
            mail_batch_dir=self.directory/'mail_batches',mail_via_proxy=False,
            google_login_via_proxy=False,app_login_via_proxy=False,redeem_via_proxy=False,
            cancel_event=cancel_event)
        if process is not False:
            runner._process_account=AsyncMock(side_effect=process or (
                lambda _job,_code,account,*args,**kwargs:
                    MochiAccountResult(account.identifier,True,'success')))
        return runner

    async def test_ten_existing_successes_stop_before_probe_mail_or_account_proxy(self):
        self.seed(10)
        runner=self.runner()
        result=await runner.run('blocked',' test ',10)
        self.assertEqual((result.status,result.success_count,result.attempted_count),('invite_limit_reached',0,0))
        self.provider.assert_not_called()
        runner.proxy_client.lease.assert_not_called()
        runner._process_account.assert_not_called()
        self.assertEqual(self.store.snapshot('TEST').count,10)

    async def test_combo_55_successes_over_six_windows_only_buys_remaining_quota(self):
        clock = [datetime.now(timezone.utc)]
        snapshot,record = self.store.snapshot,self.store.record_success
        def confirm(code,job_id,account_identifier,**kwargs):
            return record(code,job_id,account_identifier,occurred_at=clock[0])
        with patch.object(self.store,'snapshot',side_effect=lambda code:snapshot(code,now=clock[0])), \
             patch.object(self.store,'record_success',side_effect=confirm):
            completed = 0
            for index in range(6):
                result = await self.runner().run('durable-combo','TEST',55,initial_success_count=completed)
                completed = result.success_count
                self.assertEqual(completed,min(55,(index+1)*10))
                self.assertEqual(result.status,'completed' if index==5 else 'invite_limit_reached')
                self.assertEqual(result.attempted_count,5 if index==5 else 10)
                if index < 5:
                    purchases = list(self.requests)
                    blocked = await self.runner().run('durable-combo','TEST',55,initial_success_count=completed)
                    self.assertEqual((blocked.status,blocked.success_count,blocked.attempted_count),
                        ('invite_limit_reached',completed,0))
                    self.assertEqual(self.requests,purchases)
                clock[0] += timedelta(hours=24,minutes=1)
        self.assertEqual(self.requests,[1,9]*5+[1,4])
        self.assertEqual(self.store.job_success_count('test','durable-combo'),55)

    async def test_nine_existing_successes_buy_only_one_and_next_job_buys_none(self):
        self.seed(9)
        result=await self.runner().run('one-slot','TEST',10)
        self.assertEqual(self.requests,[1])
        self.assertEqual((result.status,result.success_count),('invite_limit_reached',1))
        self.assertEqual(self.store.snapshot('TEST').count,10)
        next_result=await self.runner().run('no-slots','TEST',10)
        self.assertEqual((next_result.status,next_result.success_count),('invite_limit_reached',0))
        self.assertEqual(self.requests,[1])

    async def test_eight_existing_successes_cap_probe_and_remainder_at_one_each(self):
        self.seed(8)
        result=await self.runner().run('two-slots','TEST',10)
        self.assertEqual(self.requests,[1,1])
        self.assertEqual((result.status,result.success_count),('invite_limit_reached',2))
        self.assertEqual(self.store.snapshot('TEST').count,10)

    async def test_successes_in_failed_job_stay_counted_for_next_job(self):
        def process(_job,_code,account,*args,**kwargs):
            ok=account.identifier=='account-1'
            return MochiAccountResult(account.identifier,ok,'success' if ok else 'auth_failed')
        result=await self.runner(process=process).run('partial-failed','TEST',2,accounts=(item(1),item(2)))
        self.assertEqual((result.status,result.success_count),('failed',1))
        self.assertEqual(self.store.snapshot('TEST').count,1)
        self.seed(9)
        blocked=await self.runner().run('blocked-after-failure','TEST',2)
        self.assertEqual(blocked.status,'invite_limit_reached')
        self.assertEqual(self.requests,[])

    async def test_successes_in_cancelled_job_stay_counted(self):
        cancel=threading.Event()
        runner=self.runner(cancel_event=cancel)
        runner.progress_sink=lambda event:cancel.set() if event.success_count else None
        result=await runner.run('partial-cancelled','TEST',3,accounts=(item(1),item(2),item(3)))
        self.assertEqual((result.status,result.success_count),('cancelled',1))
        self.assertEqual(self.store.snapshot('TEST').count,1)

    async def test_expired_successes_do_not_prevent_the_normal_one_plus_nine_flow(self):
        self.seed(10,when=datetime.now(timezone.utc)-timedelta(hours=24,minutes=1))
        result=await self.runner().run('expired-window','TEST',10)
        self.assertEqual((result.status,result.success_count),('completed',10))
        self.assertEqual(self.requests,[1,9])
        self.assertEqual(self.store.snapshot('TEST').count,10)

    async def test_exact_expiry_unblocks_purchase_without_waiting_for_next_day(self):
        event_time=datetime.now(timezone.utc)-timedelta(hours=24)+timedelta(minutes=1)
        self.seed(10,when=event_time)
        expiry=event_time+timedelta(hours=24)
        snapshot=self.store.snapshot
        with patch.object(self.store,'snapshot',side_effect=lambda code:snapshot(code,now=expiry-timedelta(microseconds=1))):
            blocked=await self.runner().run('before-exact-expiry','TEST',1)
        self.assertEqual(blocked.status,'invite_limit_reached')
        self.assertEqual(self.requests,[])
        with patch.object(self.store,'snapshot',side_effect=lambda code:snapshot(code,now=expiry)):
            result=await self.runner().run('at-exact-expiry','TEST',1)
        self.assertEqual((result.status,result.success_count),('completed',1))
        self.assertEqual(self.requests,[1])
        self.assertEqual(snapshot('TEST',now=expiry).count,1)

    async def test_same_code_concurrent_jobs_wait_and_second_never_buys_after_tenth(self):
        self.seed(9)
        entered,finish=asyncio.Event(),asyncio.Event()
        async def process(_job,_code,account,*args,**kwargs):
            entered.set()
            await finish.wait()
            return MochiAccountResult(account.identifier,True,'success')
        first=asyncio.create_task(self.runner(process=process).run('concurrent-first','TEST',10))
        await asyncio.wait_for(entered.wait(),2)
        second=asyncio.create_task(self.runner().run('concurrent-second',' test ',10))
        try:
            await asyncio.sleep(0.1)
            self.assertEqual(self.requests,[1])
            self.assertFalse(second.done())
            finish.set()
            result1,result2=await asyncio.wait_for(asyncio.gather(first,second),3)
        finally:
            finish.set()
            for task in (first,second):
                if not task.done():
                    task.cancel()
            await asyncio.gather(first,second,return_exceptions=True)
        self.assertEqual((result1.status,result2.status),('invite_limit_reached','invite_limit_reached'))
        self.assertEqual((result1.success_count,result2.success_count),(1,0))
        self.assertEqual(self.requests,[1])
        self.assertEqual(self.store.snapshot('TEST').count,10)

    async def test_different_codes_can_run_concurrently(self):
        entered=set()
        both_entered,finish=asyncio.Event(),asyncio.Event()
        async def process(_job,code,account,*args,**kwargs):
            entered.add(code)
            if len(entered)==2:
                both_entered.set()
            await finish.wait()
            return MochiAccountResult(account.identifier,True,'success')
        tasks=[asyncio.create_task(self.runner(process=process).run('different-'+code,code,1))
               for code in ('TEST','OTHER')]
        try:
            await asyncio.wait_for(both_entered.wait(),2)
            self.assertEqual(len(self.requests),2)
            finish.set()
            results=await asyncio.wait_for(asyncio.gather(*tasks),3)
        finally:
            finish.set()
            for task in tasks:
                if not task.done():task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
        self.assertTrue(all(result.status=='completed' for result in results))
        self.assertEqual(self.store.snapshot('TEST').count,1)
        self.assertEqual(self.store.snapshot('OTHER').count,1)

    async def test_same_code_guard_serializes_jobs_on_independent_thread_event_loops(self):
        self.seed(9)
        entered,finish=threading.Event(),threading.Event()
        async def process(_job,_code,account,*args,**kwargs):
            entered.set()
            while not finish.is_set():
                await asyncio.sleep(0.01)
            return MochiAccountResult(account.identifier,True,'success')
        first_runner=self.runner(process=process)
        second_runner=self.runner()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(lambda:asyncio.run(first_runner.run('thread-first','TEST',10)))
            second=None
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                second=pool.submit(lambda:asyncio.run(second_runner.run('thread-second','TEST',10)))
                await asyncio.sleep(0.15)
                self.assertFalse(second.done())
                self.assertEqual(self.requests,[1])
                finish.set()
                result1=await asyncio.wait_for(asyncio.wrap_future(first),3)
                result2=await asyncio.wait_for(asyncio.wrap_future(second),3)
            finally:
                finish.set()
                await asyncio.to_thread(first.result,3)
                if second is not None:
                    await asyncio.to_thread(second.result,3)
        self.assertEqual((result1.success_count,result2.success_count),(1,0))
        self.assertEqual((result1.status,result2.status),('invite_limit_reached','invite_limit_reached'))
        self.assertEqual(self.requests,[1])
        self.assertEqual(self.store.snapshot('TEST').count,10)

    async def test_cancel_waiting_for_same_code_does_not_purchase_or_steal_guard(self):
        entered,finish=asyncio.Event(),asyncio.Event()
        async def process(_job,_code,account,*args,**kwargs):
            entered.set()
            await finish.wait()
            return MochiAccountResult(account.identifier,True,'success')
        first=asyncio.create_task(self.runner(process=process).run('guard-holder','TEST',1))
        await asyncio.wait_for(entered.wait(),2)
        waiting=asyncio.create_task(self.runner().run('guard-waiter','TEST',1))
        try:
            await asyncio.sleep(0.05)
            waiting.cancel()
            result=await asyncio.wait_for(waiting,2)
            self.assertEqual(result.status,'cancelled')
            self.assertEqual(self.requests,[1])
            finish.set()
            self.assertEqual((await asyncio.wait_for(first,2)).status,'completed')
        finally:
            finish.set()
            for task in (first,waiting):
                if not task.done():task.cancel()
            await asyncio.gather(first,waiting,return_exceptions=True)

    async def test_store_read_error_fails_before_buying(self):
        runner=self.runner()
        with patch.object(self.store,'snapshot',side_effect=OSError('offline database unavailable')):
            result=await runner.run('read-failed','TEST',10)
        self.assertEqual((result.status,result.success_count),('failed',0))
        self.provider.assert_not_called()
        runner._process_account.assert_not_called()

    async def test_store_write_error_retains_confirmed_success_and_stops_remainder(self):
        runner=self.runner()
        with patch.object(self.store,'record_success',side_effect=OSError('offline storage failure')):
            result=await runner.run('write-failed','TEST',10)
        self.assertEqual((result.status,result.success_count),('failed',1))
        self.assertEqual(self.requests,[1])
        self.assertTrue(result.results[0].success)

    async def test_true_redeem_path_records_before_fetcher_close_failure(self):
        entered_close=asyncio.Event()
        async def close():
            self.assertEqual(self.store.snapshot('TEST').count,1)
            entered_close.set()
            raise OSError('offline close failed')
        fetcher=SimpleNamespace(proxy=None,login=AsyncMock(return_value={'ok':True}),
            redeem_invite_code=AsyncMock(return_value=SimpleNamespace(success=True,message='Accepted',data={})),
            close=close)
        with patch(f'{RUNNER}.MochiChatFetcher',return_value=fetcher):
            result=await self.runner(process=False).run('cleanup-failed','TEST',1)
        self.assertTrue(entered_close.is_set())
        self.assertEqual(self.store.snapshot('TEST').count,1)
        self.assertEqual(result.success_count,1)
        self.assertEqual(self.requests,[1])

    async def test_true_redeem_and_run_loop_fallback_do_not_count_same_success_twice(self):
        fetcher=SimpleNamespace(proxy=None,login=AsyncMock(return_value={'ok':True}),
            redeem_invite_code=AsyncMock(return_value=SimpleNamespace(success=True,message='Accepted',data={})),
            close=AsyncMock())
        with patch(f'{RUNNER}.MochiChatFetcher',return_value=fetcher):
            result=await self.runner(process=False).run('one-confirmation','TEST',1)
        self.assertEqual((result.status,result.success_count),('completed',1))
        self.assertEqual(self.store.snapshot('TEST').count,1)
        self.assertEqual(self.requests,[1])


if __name__=='__main__':
    unittest.main()
