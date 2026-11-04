"""Offline paid-batch replacement and failure budgets with real runner scheduling."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from core.platforms.mochichat.runners.invite_referral import MochiInviteRunner,MochiAccountResult
RUNNER='core.platforms.mochichat.runners.invite_referral'


class ReplacementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.purchases=[];self.next_mail=0
        async def buy(_products,amount):
            self.purchases.append(amount)
            accounts=[SimpleNamespace(username=f'new-{i}@example.test',password='pw') for i in range(self.next_mail,self.next_mail+amount)]
            self.next_mail+=amount
            return accounts
        self.client=SimpleNamespace(buy_gmail_domain_info=AsyncMock(side_effect=buy))
        context=AsyncMock();context.__aenter__.return_value=self.client
        self.shop=patch(RUNNER+'.GmailVipVn',return_value=context);self.shop.start();self.addCleanup(self.shop.stop)

    def runner(self,outcomes,*,probe=False,concurrency=1):
        runner=MochiInviteRunner(concurrency=concurrency,mail_batch_dir=self.temp.name,probe_invite_first=probe)
        sequence=iter(outcomes)
        async def process(_job,_code,item,*args,**kwargs):
            value=next(sequence)
            if isinstance(value,Exception):raise value
            return MochiAccountResult(item.identifier,value=='success',value)
        runner._process_account=AsyncMock(side_effect=process)
        return runner

    async def test_replaces_only_failed_slots_then_completes_and_saves_every_paid_mail(self):
        r=self.runner(['success','auth_failed','success','success'])
        result=await r.run('replace','CODE',3)
        self.assertEqual((result.status,result.success_count,result.failure_count),('completed',3,1))
        self.assertEqual(self.purchases,[3,1])
        saved=json.loads(r.mail_batch_path.read_text())
        self.assertEqual(len(saved['accounts']),4)
        self.assertEqual(sum(a['status']=='auth_failed' for a in saved['accounts']),1)

    async def test_probe_auth_failure_buys_another_probe_before_remaining_batch(self):
        r=self.runner(['auth_failed','success','success','success'],probe=True)
        result=await r.run('probe-replace','CODE',3)
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.purchases,[1,1,2])

    async def test_three_consecutive_failures_stop_before_fourth_paid_mail(self):
        r=self.runner(['auth_failed']*3,probe=True)
        result=await r.run('three','CODE',10)
        self.assertEqual((result.status,result.failure_count),('failed',3))
        self.assertEqual(self.purchases,[1,1,1])
        self.assertIn('3 lỗi',result.error_message)

    async def test_failed_probe_uses_paid_overdelivery_before_buying_more_mail(self):
        self.client.buy_gmail_domain_info.side_effect=None
        self.client.buy_gmail_domain_info.return_value=[SimpleNamespace(username=f'extra-{i}@example.test',password='pw') for i in range(4)]
        r=self.runner(['auth_failed','success','success','success'],probe=True)
        result=await r.run('spares','CODE',3)
        self.assertEqual((result.status,result.success_count),('completed',3))
        self.client.buy_gmail_domain_info.assert_awaited_once()

    async def test_five_total_failures_stop_despite_success_resetting_streak(self):
        r=self.runner(['auth_failed','success']*4+['auth_failed'])
        result=await r.run('five','CODE',10)
        self.assertEqual((result.status,result.success_count,result.failure_count),('failed',4,5))
        self.assertIn('5 lỗi',result.error_message)
        self.assertEqual(r._process_account.await_count,9)

    async def test_already_redeemed_counts_toward_failure_budget(self):
        r=self.runner(['already_redeemed']*3,probe=True)
        result=await r.run('redeemed','CODE',5)
        self.assertEqual((result.status,result.already_redeemed_count),('failed',3))
        self.assertEqual(result.failure_count,3)
        self.assertEqual(self.purchases,[1,1,1])

    async def test_invalid_code_or_quota_does_not_buy_replacements(self):
        for outcome in ('invalid_code','invite_limit_reached'):
            self.purchases.clear()
            result=await self.runner([outcome],probe=True).run(outcome,'CODE',5)
            self.assertEqual(result.status,outcome)
            self.assertEqual(self.purchases,[1])

    async def test_concurrent_failures_never_exceed_five_attempt_budget(self):
        r=self.runner(['auth_failed']*20,concurrency=10)
        result=await r.run('concurrent','CODE',10)
        self.assertEqual(result.status,'failed')
        self.assertLessEqual(result.failure_count,5)
        self.assertLessEqual(r._process_account.await_count,5)

    async def test_short_delivery_without_processing_failures_never_topups(self):
        self.client.buy_gmail_domain_info.side_effect=None
        self.client.buy_gmail_domain_info.return_value=[SimpleNamespace(username='short@example.test',password='pw')]
        r=self.runner(['success'])
        result=await r.run('short','CODE',3)
        self.assertEqual((result.status,result.success_count),('failed',1))
        self.client.buy_gmail_domain_info.assert_awaited_once()

    async def test_exceptions_in_account_processing_use_same_failure_budget(self):
        r=self.runner([RuntimeError('account error')]*3,probe=True)
        result=await r.run('exceptions','CODE',10)
        self.assertEqual((result.status,result.failure_count,len(result.results)),('failed',3,3))

    async def test_recovered_proxy_exhaustion_flag_retries_account_slots_until_budget(self):
        r=self.runner([] ,probe=True)
        r._process_account=AsyncMock(side_effect=lambda _j,_c,item,*a,**k:
            MochiAccountResult(item.identifier,False,'network_error','failed recovery',data={'stop_job':True}))
        result=await r.run('network','CODE',10)
        self.assertEqual((result.status,result.failure_count),('failed',3))
        self.assertEqual(self.purchases,[1,1,1])

    async def test_combo_remaining_failure_budget_uses_prior_round_errors(self):
        r=self.runner(['auth_failed','success'],probe=True)
        r.failure_budget_used=4
        result=await r.run('last-budget','CODE',10,initial_success_count=3)
        self.assertEqual((result.status,result.failure_count,result.success_count),('failed',1,3))
        self.assertEqual(self.purchases,[1])

    async def test_exhausted_persisted_budget_never_buys_mail(self):
        r=self.runner([],probe=True);r.failure_budget_used=5
        result=await r.run('exhausted-budget','CODE',10,initial_success_count=3)
        self.assertEqual((result.status,result.success_count),('failed',3))
        self.assertEqual(self.purchases,[])


if __name__=='__main__':unittest.main(verbosity=2)
