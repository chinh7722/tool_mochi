"""Durable combo rounds retain one job, owner, price and customer hold."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from core.job_schedule import VIETNAM_TIME, schedule_storage
from core.telegram_users import TelegramUserStore


class ComboLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'users.sqlite3'
        self.store = TelegramUserStore(self.path)
        self.now = datetime(2026, 11, 3, 10, tzinfo=VIETNAM_TIME)
        self.next_round = self.now + timedelta(hours=24, minutes=1)
        self.metadata = dict(combo=True, customer_name='Khách A', creator_name='Local',
            total_vnd=55000, source_mail_batch=None)

    def local(self, job='combo', *, scheduled_at=None, combo=True):
        return self.store.record_local_job(job, 'TEST', 5500, 55, sale_rate_units=100000,
            scheduled_at=scheduled_at, schedule_data=self.metadata if combo else None)

    def customer(self):
        self.store.ensure_customer(456, 'Khách A')
        self.store.set_customer_balance(456, 10000000, 'funding', expected_units=0)
        self.store.record_customer_order('customer', 456, 'TEST', 5500, 55, 'bot', 10,
            100000, schedule_data=self.metadata)

    def test_combo_metadata_is_saved_for_immediate_jobs_and_validated_without_a_date(self):
        self.assertEqual(schedule_storage(), (None, '{}'))
        self.local()
        row = self.store.get_job('combo')
        self.assertEqual(json.loads(row.schedule_data), self.metadata)
        self.assertIsNone(row.scheduled_at)
        for invalid in ({'combo': 1}, {'combo': 'true'}, {'api_key': 'secret'}, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                schedule_storage(None, invalid)

    def test_multiple_rounds_keep_one_job_and_restore_progress_after_reopen(self):
        self.local()
        initial = self.store.get_job('combo')
        self.assertTrue(self.store.reschedule_combo_job('combo', self.next_round, 10, 12, 2))
        reopened = TelegramUserStore(self.path)
        row = reopened.get_job('combo')
        self.assertEqual((row.success_count, row.attempted_count, row.failure_count), (10, 12, 2))
        self.assertEqual((row.status, row.dispatch_state, row.finalized_at), ('running', 'scheduled', None))
        for name in ('job_id', 'chat_id', 'target_count', 'requested_beans', 'created_at',
                'sale_rate_units', 'schedule_data'):
            self.assertEqual(getattr(row, name), getattr(initial, name), name)
        self.assertEqual(reopened.scheduled_jobs()[0]['success_count'], 10)
        self.assertTrue(reopened.claim_scheduled_job('combo'))
        self.assertFalse(self.store.claim_scheduled_job('combo'))
        reopened.update_dispatch('combo', 'running')
        second = self.next_round + timedelta(hours=24, minutes=1)
        self.assertTrue(reopened.reschedule_combo_job('combo', second, 20, 23, 3))
        self.assertEqual(len(reopened.list_jobs(-1)), 1)
        self.assertEqual(reopened.get_job('combo').scheduled_at, second.isoformat())
        self.assertEqual(reopened.get_job('combo').success_count, 20)

    def test_duplicate_wait_callback_does_not_postpone_or_reduce_saved_progress(self):
        self.local()
        self.store.reschedule_combo_job('combo', self.next_round, 10, 11, 1)
        self.assertTrue(self.store.reschedule_combo_job('combo', self.next_round + timedelta(hours=1), 10, 11, 1))
        self.assertTrue(self.store.reschedule_combo_job('combo', self.next_round + timedelta(hours=2), 9, 10, 0))
        row = self.store.get_job('combo')
        self.assertEqual(row.scheduled_at, self.next_round.isoformat())
        self.assertEqual((row.success_count, row.attempted_count, row.failure_count), (10, 11, 1))

    def test_rescheduling_requires_combo_and_cannot_reopen_a_finalized_job(self):
        self.local('ordinary', combo=False)
        initial = self.store.get_job('ordinary')
        self.assertFalse(self.store.reschedule_combo_job('ordinary', self.next_round, 10))
        self.assertEqual(self.store.get_job('ordinary'), initial)
        self.local()
        self.store.finish_job('combo', 'completed', 55, attempted_count=55)
        final = self.store.get_job('combo')
        self.assertFalse(self.store.reschedule_combo_job('combo', self.next_round, 10))
        self.assertEqual(self.store.get_job('combo'), final)
        self.assertFalse(self.store.reschedule_combo_job('missing', self.next_round, 0))

    def test_rescheduling_invalid_counts_rolls_back_everything(self):
        self.local()
        initial = self.store.get_job('combo')
        for kwargs in ({'success_count': -1}, {'success_count': True},
                {'success_count': 10, 'attempted_count': -2}, {'success_count': 10, 'failure_count': '1'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.store.reschedule_combo_job('combo', self.next_round, **kwargs)
            self.assertEqual(self.store.get_job('combo'), initial)

    def test_recovery_requeues_only_interrupted_combos_and_preserves_future_deadlines(self):
        self.local('running')
        self.local('queued', scheduled_at=self.next_round)
        self.store.update_dispatch('queued', 'queued')
        self.local('dispatching', scheduled_at=self.now - timedelta(hours=2))
        self.store.claim_scheduled_job('dispatching')
        self.store.update_progress('dispatching', 10, 12, 2)
        self.local('waiting', scheduled_at=self.next_round)
        self.local('ordinary', combo=False)
        self.local('done')
        self.store.finish_job('done', 'completed', 55)
        self.assertEqual(self.store.recover_combo_jobs(self.now), 3)
        self.assertEqual(self.store.recover_combo_jobs(self.now), 0)
        rows = {x['job_id']: x for x in self.store.scheduled_jobs()}
        self.assertEqual(set(rows), {'running', 'queued', 'dispatching', 'waiting'})
        self.assertEqual(rows['running']['scheduled_at'], self.now.isoformat())
        self.assertEqual(rows['dispatching']['scheduled_at'], self.now.isoformat())
        self.assertEqual(rows['dispatching']['success_count'], 10)
        self.assertEqual(rows['queued']['scheduled_at'], self.next_round.isoformat())
        self.assertEqual(rows['waiting']['scheduled_at'], self.next_round.isoformat())
        self.assertEqual(self.store.get_job('ordinary').dispatch_state, 'running')

    def test_customer_keeps_single_hold_across_rounds_and_settles_once_at_completion(self):
        self.customer()
        for index in range(1, 6):
            if index > 1:
                self.assertTrue(self.store.claim_scheduled_job('customer'))
                self.store.update_dispatch('customer', 'running')
            self.store.reschedule_combo_job('customer', self.next_round + timedelta(days=index),
                index * 10, index * 10)
            reopened = TelegramUserStore(self.path)
            self.assertEqual(reopened.get_customer(456)['balance_units'], 4500000)
            self.assertIsNone(reopened.get_customer_order_for_job('customer')['charged_units'])
        self.assertTrue(self.store.claim_scheduled_job('customer'))
        self.assertTrue(self.store.finish_job('customer', 'completed', 55, attempted_count=55))
        self.assertFalse(self.store.finish_job('customer', 'completed', 55, attempted_count=55))
        self.assertEqual(self.store.get_customer_order_for_job('customer')['charged_units'], 5500000)
        self.assertEqual(self.store.get_customer(456)['balance_units'], 4500000)
        with self.store._connection() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM customer_wallet WHERE kind='reserve'").fetchone()[0], 1)
        self.assertEqual(len(self.store.customer_orders(456)), 1)

    def test_cancel_waiting_combo_charges_confirmed_beans_and_refunds_remainder_once(self):
        self.customer()
        self.store.reschedule_combo_job('customer', self.next_round, 20, 23, 3)
        self.assertTrue(self.store.cancel_scheduled_job('customer'))
        self.assertFalse(self.store.cancel_scheduled_job('customer'))
        self.assertFalse(self.store.claim_scheduled_job('customer'))
        self.assertFalse(self.store.reschedule_combo_job('customer', self.next_round, 20))
        self.assertEqual(self.store.get_customer(456)['balance_units'], 8000000)
        self.assertEqual(self.store.get_customer_order_for_job('customer')['charged_units'], 2000000)
        self.assertEqual(self.store.get_job('customer').success_count, 20)
        self.assertEqual(self.store.recover_combo_jobs(self.now), 0)


if __name__ == '__main__':
    unittest.main()
