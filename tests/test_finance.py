"""Offline exact-money ledger and real purchase callback integration."""
import asyncio
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from core.finance import MailAccountingError, MailPurchaseReceipt, money_units, profile_balance_units
from core.mail.providers.gmailvip_vn import GmailVipVn
from core.mail.providers.one_trieu_mail import OneTrieuMail
from core.mail.providers import purchase_policy as policy
from core.telegram_users import TelegramUserStore
from core.platforms.mochichat.runners.invite_referral import MochiAccountResult, MochiInviteRunner


def response(data):
    return SimpleNamespace(json=lambda: data, status_code=200, headers={})


def balance(value):
    return response({'status': 'success', 'data': {'money': str(value)}})


def receipt(pid='purchase1', job='job1', cost=12345, **kw):
    return MailPurchaseReceipt(pid, job, 'shop.test', 'hash-not-a-key', 1, 10, cost, **kw)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'ledger.sqlite3'
        self.store = TelegramUserStore(self.path)
        self.store.upsert_user(123, 'Partner A')
        self.store.record_job('job1', 123, 'TEST', 1000, 10, sale_rate_units=money_units('1000'))

    def test_cost_is_booked_before_any_mail_is_used_and_revenue_only_on_completion(self):
        self.store.record_purchase(receipt(cost=money_units('530.27')))
        user = self.store.get_user(123)
        self.assertEqual((user.revenue_vnd, user.cost_vnd, user.profit_vnd),
            (Decimal('0'), Decimal('530.27'), Decimal('-530.27')))
        self.store.update_progress('job1', 3)
        self.assertEqual(self.store.get_user(123).revenue_vnd, 0)
        self.store.finish_job('job1', 'completed', 10)
        user = self.store.get_user(123)
        self.assertEqual((user.revenue_vnd, user.cost_vnd, user.profit_vnd),
            (Decimal('10000'), Decimal('530.27'), Decimal('9469.73')))

    def test_failed_or_cancelled_job_keeps_full_cost_including_unused_mail(self):
        self.store.record_purchase(receipt(cost=money_units('700.65')))
        self.store.finish_job('job1', 'failed', 2)
        self.store.record_job('job2', 123, 'TEST', 1000, 10, sale_rate_units=50000)
        self.store.record_purchase(receipt('purchase2', 'job2', money_units('300.10')))
        self.store.finish_job('job2', 'cancelled', 1)
        user = self.store.get_user(123)
        self.assertEqual((user.revenue_vnd, user.cost_vnd, user.profit_vnd, user.sold_beans),
            (0, Decimal('1000.75'), Decimal('-1000.75'), 300))

    def test_multiple_receipts_do_not_multiply_job_counts_or_revenue(self):
        self.store.record_purchase(receipt(cost=100))
        self.store.record_purchase(receipt('purchase2', cost=200))
        self.store.finish_job('job1', 'completed', 10)
        user = self.store.get_user(123)
        self.assertEqual((user.completed_jobs, user.sold_beans, user.revenue_vnd, user.cost_vnd),
            (1, 1000, 10000, Decimal('3')))

    def test_replayed_progress_receipts_and_finish_never_double_count(self):
        pending = receipt(cost=None, state='intent')
        self.store.record_purchase(pending)
        self.store.record_purchase(pending)
        self.assertEqual(self.store.get_user(123).pending_costs, 1)
        self.store.record_purchase(replace(pending, cost_units=65, state='confirmed'))
        self.store.record_purchase(replace(pending, cost_units=65, state='confirmed'))
        self.store.record_purchase(pending)
        self.store.finish_job('job1', 'completed', 10)
        self.store.finish_job('job1', 'completed', 10)
        user = TelegramUserStore(self.path).get_user(123)
        self.assertEqual((user.pending_costs, user.cost_vnd, user.revenue_vnd), (0, Decimal('.65'), 10000))

    def test_same_shop_order_with_new_event_id_does_not_add_cost_or_pending(self):
        first = receipt(cost=123, order_id='ORDER-1')
        self.store.record_purchase(first)
        second = replace(first, purchase_id='second', order_id='', cost_units=None, state='intent')
        self.store.record_purchase(second)
        self.store.record_purchase(replace(second, order_id='ORDER-1', cost_units=123, state='confirmed'))
        user = self.store.get_user(123)
        self.assertEqual((user.cost_units, user.pending_costs), (123, 0))
        self.assertEqual(TelegramUserStore(self.path).get_user(123).cost_units, 123)

    def test_price_snapshots_and_users_are_independent_and_rename_keeps_history(self):
        self.store.upsert_user(456, 'Partner A')
        self.store.record_job('job2', 456, 'TEST', 1000, 10, sale_rate_units=200000)
        self.store.record_purchase(receipt('p2', 'job2', 350))
        self.store.finish_job('job1', 'completed', 10)
        self.store.finish_job('job2', 'completed', 10)
        self.store.upsert_user(123, 'New name', 'admin', active=False)
        self.assertEqual(self.store.get_user(123).revenue_vnd, 10000)
        self.assertEqual((self.store.get_user(456).revenue_vnd, self.store.get_user(456).cost_vnd),
            (20000, Decimal('3.50')))

    def test_reconciliation_resolves_missing_cost_once(self):
        self.store.record_purchase(receipt(cost=None))
        self.assertEqual(self.store.get_user(123).pending_costs, 1)
        self.store.reconcile_purchase('purchase1', 65)
        with self.assertRaises(ValueError):
            self.store.reconcile_purchase('purchase1', 100)
        self.assertEqual((self.store.get_user(123).cost_vnd, self.store.get_user(123).pending_costs),
            (Decimal('.65'), 0))

    def test_journal_recovers_a_purchase_when_sqlite_write_failed_after_charge(self):
        with patch.object(self.store, '_store_purchase', side_effect=OSError('temporary DB failure')):
            with self.assertRaises(OSError):
                self.store.record_purchase(receipt(cost=3055))
        self.assertEqual(self.store.get_user(123).cost_units, 0)
        recovered = TelegramUserStore(self.path)
        self.assertEqual((recovered.get_user(123).cost_units, recovered.recovery_errors), (3055, 0))
        journal = ''.join(p.read_text(encoding='utf-8') for p in recovered.receipt_directory.glob('*.json'))
        self.assertNotIn('api_key', journal)
        self.assertNotIn('password', journal)

    def test_missing_rate_is_explicit_and_can_be_priced_without_repricing_other_jobs(self):
        self.store.record_job('unpriced', 123, 'TEST', 100, 1)
        self.store.finish_job('unpriced', 'completed', 1)
        self.assertEqual(self.store.get_user(123).unpriced_jobs, 1)
        self.store.price_job('unpriced', 12345)
        self.assertEqual((self.store.get_user(123).revenue_vnd, self.store.get_user(123).unpriced_jobs),
            (Decimal('123.45'), 0))
        with self.assertRaises(ValueError):
            self.store.price_job('unpriced', 0)

    def test_cross_owner_and_conflicting_amount_are_rejected_without_corrupting_receipt(self):
        self.store.record_purchase(receipt(cost=100))
        self.store.upsert_user(456, 'Other')
        self.store.record_job('other', 456, 'TEST', 100)
        for conflicting in (receipt(cost=200), receipt(job='other', cost=100)):
            with self.assertRaises(ValueError):
                self.store.record_purchase(conflicting)
        reopened = TelegramUserStore(self.path)
        self.assertEqual((reopened.get_user(123).cost_units, reopened.get_user(456).cost_units,
            reopened.recovery_errors), (100, 0, 0))

    def test_local_unowned_job_does_not_get_assigned_to_a_telegram_user(self):
        self.assertFalse(self.store.record_purchase(receipt(job='local-no-owner', cost=500)))
        self.assertEqual(self.store.get_user(123).cost_units, 0)

    def test_pre_finance_schema_migrates_without_inventing_historical_prices(self):
        legacy = Path(self.temp.name) / 'legacy.sqlite3'
        # Remove only the newly added columns from a representative old-schema DB.
        with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(legacy)) as target:
            source.backup(target)
            target.execute('DROP TABLE mail_purchases')
            target.execute('ALTER TABLE jobs DROP COLUMN sale_rate_units')
            target.execute('ALTER TABLE jobs DROP COLUMN finance_tracked')
            target.execute("UPDATE jobs SET status='completed', success_count=10, finalized_at='old'")
            target.commit()
        restored = TelegramUserStore(legacy).get_user(123)
        self.assertEqual((restored.sold_beans, restored.completed_jobs, restored.revenue_vnd,
            restored.unpriced_jobs), (1000, 1, 0, 0))

    def test_exact_fractional_money_and_invalid_inputs(self):
        self.assertEqual(money_units('5607.73') - money_units('5607.08'), 65)
        self.assertEqual(profile_balance_units({'status': 'success', 'data': {'money': '0.65'}}), 65)
        for value in (True, None, 'NaN', 'Infinity', '-1', '0.001', '1,000', '999999999999999999999999999'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                money_units(value)


class PurchaseAccountingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TelegramUserStore(Path(self.temp.name) / 'ledger.sqlite3')
        self.store.upsert_user(123, 'A')
        self.store.record_job('job1', 123, 'TEST', 1000, 10, sale_rate_units=10000)
        self.key = 'offline-key-' + self._testMethodName
        self.interval = patch.object(policy, 'MIN_PURCHASE_INTERVAL', 0)
        self.interval.start()
        self.addCleanup(self.interval.stop)

    def provider(self, kind=GmailVipVn, *, job='job1', callback=None):
        provider = kind(self.key, on_purchase=callback or self.store.record_purchase, job_id=job)
        provider._session = SimpleNamespace(get=AsyncMock(), post=AsyncMock())
        return provider

    async def test_both_shops_book_debit_before_mail_parsing_and_all_mail_remains_unused(self):
        for index, kind in enumerate((GmailVipVn, OneTrieuMail)):
            provider = self.provider(kind)
            provider._session.get.side_effect = [balance('5607.73'), balance('5507.08')]
            provider._session.post.return_value = response({'status': 'success', 'trans_id': f'O-{index}',
                'data': ['unused@example.test|pw'] * 10})
            result = await provider.buy_product(1, 10)
            self.assertEqual(len(result), 10)
            provider._session.post.assert_awaited_once()
        self.assertEqual((self.store.get_user(123).cost_vnd, self.store.get_user(123).sold_beans),
            (Decimal('201.30'), 0))

    async def test_balance_read_failure_before_purchase_does_not_spend_money(self):
        provider = self.provider()
        provider._session.get.side_effect = TimeoutError('profile unavailable')
        with self.assertRaisesRegex(ValueError, 'chưa gửi'):
            await provider.buy_product(1, 10)
        provider._session.post.assert_not_awaited()
        self.assertFalse(self.store.list_purchases())

    async def test_failed_after_balance_keeps_unknown_receipt_instead_of_zero_cost(self):
        provider = self.provider()
        provider._session.get.side_effect = [balance('100'), TimeoutError('profile unavailable')]
        provider._session.post.return_value = response({'status': 'success', 'data': ['u@example.test|pw']})
        self.assertEqual(await provider.buy_product(1), ['u@example.test|pw'])
        user = self.store.get_user(123)
        self.assertEqual((user.cost_units, user.pending_costs), (0, 1))
        self.assertIsNone(self.store.list_purchases()[0]['cost_units'])

    async def test_lost_purchase_response_with_confirmed_debit_is_booked_without_retry(self):
        provider = self.provider()
        provider._session.get.side_effect = [balance('100'), balance('95.35')]
        provider._session.post.side_effect = TimeoutError('reply lost')
        with self.assertRaises(ValueError):
            await provider.buy_product(1, 10)
        provider._session.post.assert_awaited_once()
        self.assertEqual(self.store.get_user(123).cost_vnd, Decimal('4.65'))

    async def test_malformed_paid_accounts_still_leave_full_cost(self):
        provider = self.provider()
        provider._session.get.side_effect = [balance('100'), balance('91.25')]
        provider._session.post.return_value = response({'status': 'success', 'data': None})
        with self.assertRaises(ValueError):
            await provider.buy_product(1, 10)
        self.assertEqual(self.store.get_user(123).cost_vnd, Decimal('8.75'))

    async def test_cancelled_post_keeps_durable_unresolved_intent(self):
        provider = self.provider()
        provider._session.get.return_value = balance('100')
        provider._session.post.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await provider.buy_product(1)
        self.assertEqual(self.store.get_user(123).pending_costs, 1)
        self.assertTrue(list(self.store.receipt_directory.glob('*.json')))

    async def test_rate_limit_rejections_do_not_charge_or_leave_false_pending_cost(self):
        provider = self.provider()
        provider._session.get.side_effect = [balance('100'), balance('100'), balance('89.45')]
        provider._session.post.side_effect = [response({'status': 'error', 'msg': 'Thao tác quá nhanh'}),
            response({'status': 'success', 'data': ['u@example.test|pw']})]
        with patch.object(policy, 'retry_wait', return_value=0):
            await provider.buy_product(1)
        self.assertEqual((self.store.get_user(123).cost_vnd, self.store.get_user(123).pending_costs),
            (Decimal('10.55'), 0))
        self.assertEqual(provider._session.post.await_count, 2)

    async def test_recording_failure_before_post_stops_without_purchasing(self):
        provider = self.provider(callback=lambda _: (_ for _ in ()).throw(OSError('cannot persist')))
        provider._session.get.return_value = balance('100')
        with self.assertRaises(MailAccountingError):
            await provider.buy_gmail_domain_info([1, 2], 10)
        provider._session.post.assert_not_awaited()

    async def test_shared_key_jobs_do_not_mix_before_and_after_balances(self):
        self.store.record_job('job2', 123, 'TEST', 1000, 10, sale_rate_units=10000)
        wallet = Decimal('100')
        async def get(*_args, **_kwargs):
            await asyncio.sleep(0)
            return balance(wallet)
        async def post(_url, data):
            nonlocal wallet
            await asyncio.sleep(0)
            wallet -= Decimal('25.35') if data['id'] == 1 else Decimal('17.25')
            return response({'status': 'success', 'data': ['u@example.test|pw']})
        first, second = self.provider(), self.provider(job='job2')
        for provider in (first, second):
            provider._session.get.side_effect = get
            provider._session.post.side_effect = post
        await asyncio.gather(first.buy_product(1), second.buy_product(2))
        rows = self.store.list_purchases()
        self.assertEqual({row['job_id']: row['cost_units'] for row in rows}, {'job1': 2535, 'job2': 1725})
        self.assertEqual(self.store.get_user(123).cost_vnd, Decimal('42.60'))

    async def test_runner_books_a_whole_paid_batch_and_reuse_does_not_charge_second_user(self):
        session = SimpleNamespace(get=AsyncMock(side_effect=[balance('5607.73'), balance('5500.25')]),
            post=AsyncMock(return_value=response({'status': 'success', 'data': [
                'integration-finance-a@example.test|pw', 'integration-finance-b@example.test|pw']})),
            close=AsyncMock())
        runner = MochiInviteRunner(concurrency=1, mail_provider_api_key=self.key,
            mail_product_ids=[1], mail_batch_dir=self.temp.name, mail_purchase_sink=self.store.record_purchase)
        runner._process_account = AsyncMock(side_effect=lambda _job, _code, item, *_args, **_kwargs:
            MochiAccountResult(item.identifier, False, 'invalid_code'))
        with patch('core.mail.providers.gmailvip_vn.AsyncSession', return_value=session):
            result = await runner.run('job1', 'TEST', 2)
        self.assertEqual((result.attempted_count, result.success_count), (1, 0))
        rows = json.loads(runner.mail_batch_path.read_text(encoding='utf-8'))['accounts']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['status'], 'pending')
        self.assertEqual(self.store.get_user(123).cost_vnd, Decimal('107.48'))
        self.assertEqual(self.store.list_purchases()[0]['balance_before_units'], 560773)
        self.assertEqual(self.store.list_purchases()[0]['balance_after_units'], 550025)
        self.store.upsert_user(456, 'Reusing buyer')
        self.store.record_job('job2', 456, 'TEST2', 200, 2, sale_rate_units=10000)
        reuse = MochiInviteRunner(concurrency=1, mail_purchase_sink=self.store.record_purchase)
        reuse._process_account = AsyncMock(side_effect=lambda _job, _code, item, *_args, **_kwargs:
            MochiAccountResult(item.identifier, True, 'success'))
        with patch('core.platforms.mochichat.runners.invite_referral.GmailVipVn') as shop:
            reused = await reuse.run('job2', 'TEST2', 2, source_mail_batch=runner.mail_batch_path)
        shop.assert_not_called()
        self.assertEqual(reused.success_count, 2)
        self.assertEqual(self.store.get_user(456).cost_vnd, 0)
        self.assertEqual(self.store.get_user(123).cost_vnd, Decimal('107.48'))

    async def test_receipt_write_failure_after_charge_never_falls_through_to_second_product(self):
        provider = self.provider()
        provider._session.get.side_effect = [balance('100'), balance('91.25')]
        provider._session.post.return_value = response({'status': 'success', 'data': ['paid@example.test|pw']})
        original = self.store._store_purchase
        def fail_after_charge(receipt):
            if receipt.cost_units is not None:
                raise OSError('DB temporarily unavailable after purchase')
            return original(receipt)
        with patch.object(self.store, '_store_purchase', side_effect=fail_after_charge):
            with self.assertRaises(MailAccountingError):
                await provider.buy_gmail_domain_info([1, 2], 10)
        provider._session.post.assert_awaited_once()
        self.assertEqual(TelegramUserStore(self.store.path).get_user(123).cost_vnd, Decimal('8.75'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
