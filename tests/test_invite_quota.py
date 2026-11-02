"""Offline rolling-window, idempotency and persistence checks for invite quota."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from core.platforms.mochichat.invite_quota import InviteQuotaStore, normalize_invite_code


class InviteQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'quota.sqlite3'
        self.store = InviteQuotaStore(self.path)
        self.now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, index, *, when=None, code='TEST', job='job'):
        return self.store.record_success(code, job, f'mail-{index}@example.test',
            occurred_at=self.now if when is None else when)

    def test_empty_count_and_frozen_snapshot(self):
        result = self.store.snapshot(' TEST ', now=self.now)
        self.assertEqual((result.code, result.count, result.remaining, result.limit_reached),
                         ('TEST', 0, 10, False))
        self.assertIsNone(result.next_available_at)
        with self.assertRaises(FrozenInstanceError):
            result.count = 4

    def test_ten_successes_trigger_limit_with_next_expiry(self):
        start = self.now - timedelta(hours=3)
        for index in range(10):
            self.record(index, when=start + timedelta(minutes=index))
        result = self.store.snapshot('test', now=self.now)
        self.assertEqual((result.count, result.remaining, result.limit_reached), (10, 0, True))
        self.assertEqual(result.next_available_at, start + timedelta(hours=24))
        self.assertIsNotNone(result.next_available_at.utcoffset())

    def test_fewer_than_ten_remain_available(self):
        for index in range(9):
            self.record(index)
        result = self.store.snapshot('TEST', now=self.now)
        self.assertEqual((result.count, result.remaining, result.limit_reached), (9, 1, False))

    def test_exact_window_boundary_excludes_old_events(self):
        self.record('old', when=self.now - timedelta(hours=24))
        self.record('inside', when=self.now - timedelta(hours=24) + timedelta(microseconds=1))
        self.record('now')
        self.record('future', when=self.now + timedelta(microseconds=1))
        self.assertEqual(self.store.snapshot('TEST', now=self.now).count, 2)

    def test_sliding_window_is_not_calendar_day(self):
        local = timezone(timedelta(hours=7))
        before_midnight = datetime(2026, 11, 2, 23, 59, tzinfo=local)
        self.record(1, when=before_midnight)
        after_midnight = datetime(2026, 11, 3, 0, 1, tzinfo=local)
        self.assertEqual(self.store.snapshot('TEST', now=after_midnight).count, 1)
        self.assertEqual(self.store.snapshot('TEST', now=before_midnight + timedelta(hours=24)).count, 0)

    def test_case_and_whitespace_normalize_code_and_account_for_replay(self):
        self.assertTrue(self.store.record_success(' test ', 'job', '  Account@Example.test ', occurred_at=self.now))
        self.assertFalse(self.store.record_success('TEST', 'job', 'account@example.test', occurred_at=self.now))
        self.assertEqual(self.store.snapshot('test', now=self.now).count, 1)

    def test_successes_are_independent_of_job_final_status_and_owner(self):
        # No final-status field exists: confirmations from failed/cancelled jobs
        # use the same API as confirmations in completed jobs.
        for index, job in enumerate(('completed-job', 'failed-job', 'cancelled-job', 'local-job', 'bot-job')):
            self.record(index, job=job)
        self.assertEqual(self.store.snapshot('TEST', now=self.now).count, 5)

    def test_distinct_codes_are_separate(self):
        for index in range(10):
            self.record(index)
        self.record(1, code='OTHER')
        self.assertTrue(self.store.snapshot('TEST', now=self.now).limit_reached)
        self.assertEqual(self.store.snapshot('OTHER', now=self.now).remaining, 9)

    def test_reopen_and_duplicate_event_do_not_change_first_confirmation_time(self):
        when = self.now - timedelta(hours=23)
        self.record(1, when=when)
        reopened = InviteQuotaStore(self.path)
        self.assertFalse(reopened.record_success('TEST', 'job', 'mail-1@example.test', occurred_at=self.now))
        self.assertEqual(reopened.snapshot('TEST', now=when + timedelta(hours=24)).count, 0)

    def test_multiple_jobs_count_all_confirmations(self):
        self.record(1, job='first-job')
        self.record(1, job='second-job')
        self.assertEqual(self.store.snapshot('TEST', now=self.now).count, 2)

    def test_more_than_ten_uses_expiry_needed_to_fall_below_ten(self):
        start = self.now - timedelta(hours=1)
        for index in range(12):
            self.record(index, when=start + timedelta(minutes=index))
        result = self.store.snapshot('TEST', now=self.now)
        self.assertEqual(result.count, 12)
        self.assertEqual(result.next_available_at, start + timedelta(minutes=2, hours=24))
        self.assertEqual(self.store.snapshot('TEST', now=result.next_available_at).count, 9)

    def test_concurrent_store_instances_record_once(self):
        def write(index):
            return InviteQuotaStore(self.path).record_success('TEST', 'shared-job',
                'same@example.test', occurred_at=self.now)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(write, range(32)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.store.snapshot('TEST', now=self.now).count, 1)

    def test_concurrent_distinct_events_preserve_count(self):
        def write(index):
            return InviteQuotaStore(self.path).record_success('TEST', f'job-{index}',
                f'distinct-{index}@example.test', occurred_at=self.now)
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertTrue(all(pool.map(write, range(24))))
        self.assertEqual(self.store.snapshot('TEST', now=self.now).count, 24)

    def test_mail_identity_not_written_in_plaintext(self):
        address = 'private-mail-account@example.test'
        self.store.record_success('TEST', 'safe-job', address, occurred_at=self.now)
        raw = self.path.read_bytes()
        self.assertNotIn(address.encode(), raw)
        self.assertIn(b'TEST', raw)
        with closing(sqlite3.connect(self.path)) as db:
            hashed = db.execute('SELECT account_hash FROM invite_successes').fetchone()[0]
        self.assertEqual(len(hashed), 64)

    def test_invalid_codes_rejected(self):
        for value in ('', 'a b', 'line\ncode', 'x' * 129, None, 4, 'mã'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_invite_code(value)

    def test_invalid_identity_rejected_without_event(self):
        for job, account in (('', 'a'), ('job', ''), ('job\nraw', 'a'), ('job', 'a\0b')):
            with self.subTest(job=job, account=account), self.assertRaises(ValueError):
                self.store.record_success('TEST', job, account, occurred_at=self.now)
        self.assertEqual(self.store.snapshot('TEST', now=self.now).count, 0)

    def test_invalid_or_naive_clocks_rejected(self):
        for value in (datetime(2026, 1, 1), '2026-01-01T00:00:00', 'bad', float('nan'), 42,
                      datetime(1969, 1, 1, tzinfo=timezone.utc)):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.snapshot('TEST', now=value)
            with self.subTest(record=value), self.assertRaises(ValueError):
                self.store.record_success('TEST', 'job', 'a', occurred_at=value)

    def test_equivalent_iso_timezone_instants_count_once_in_window(self):
        self.store.record_success('TEST', 'job', 'a', occurred_at='2026-11-03T19:00:00+07:00')
        self.assertEqual(self.store.snapshot('TEST', now='2026-11-03T12:00:00+00:00').count, 1)

    def legacy(self, job_id, count, **extra):
        return dict(job_id=job_id, code='TEST', success_count=count,
            created_at=(self.now-timedelta(hours=4)).isoformat(),
            updated_at=(self.now-timedelta(hours=2)).isoformat(), finalized_at=None, **extra)

    def test_legacy_job_status_does_not_filter_success_count(self):
        rows = [self.legacy('completed',4,status='completed'),self.legacy('failed',3,status='failed'),
                self.legacy('cancelled',3,status='cancelled')]
        self.assertEqual(self.store.seed_legacy_jobs(rows),3)
        result=self.store.snapshot('TEST',now=self.now)
        self.assertEqual(result.count,10)
        self.assertTrue(result.limit_reached)
        self.assertEqual(result.next_available_at,self.now+timedelta(hours=22))

    def test_legacy_seed_is_once_per_job_across_restart(self):
        self.store.seed_legacy_jobs([self.legacy('old',8)])
        self.record('new',job='old')
        reopened=InviteQuotaStore(self.path)
        self.assertEqual(reopened.seed_legacy_jobs([self.legacy('old',9)]),0)
        self.assertEqual(reopened.snapshot('TEST',now=self.now).count,9)

    def test_newly_tracked_job_not_double_counted_when_legacy_source_contains_it(self):
        self.record(1,job='new')
        self.record(2,job='new')
        self.assertEqual(self.store.seed_legacy_jobs([self.legacy('new',2)]),0)
        self.assertEqual(self.store.snapshot('TEST',now=self.now).count,2)
        self.record(3,job='new')
        self.assertEqual(InviteQuotaStore(self.path).seed_legacy_jobs([self.legacy('new',3)]),0)
        self.assertEqual(self.store.snapshot('TEST',now=self.now).count,3)

    def test_legacy_latest_timestamp_precedence_and_expiry(self):
        row=self.legacy('old',10)
        row['finalized_at']=(self.now-timedelta(hours=1)).isoformat()
        self.store.seed_legacy_jobs([row])
        result=self.store.snapshot('TEST',now=self.now)
        self.assertEqual(result.next_available_at,self.now+timedelta(hours=23))
        self.assertFalse(self.store.snapshot('TEST',now=result.next_available_at).limit_reached)

    def test_legacy_created_at_fallback(self):
        row=self.legacy('old',2)
        row['updated_at']=None
        self.store.seed_legacy_jobs([row])
        self.assertEqual(self.store.snapshot('TEST',now=self.now).count,2)
        self.assertEqual(self.store.snapshot('TEST',now=self.now+timedelta(hours=20)).count,0)

    def test_legacy_validation_is_atomic(self):
        rows=[self.legacy('first',8),self.legacy('bad',True)]
        with self.assertRaises(ValueError):
            self.store.seed_legacy_jobs(rows)
        self.assertEqual(self.store.snapshot('TEST',now=self.now).count,0)
        self.assertEqual(self.store.seed_legacy_jobs([self.legacy('first',8)]),1)

    def test_concurrent_legacy_seed_and_future_events_are_not_duplicated(self):
        def seed(_):
            return InviteQuotaStore(self.path).seed_legacy_jobs([self.legacy('old',6)])
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(seed,range(20))),1)
        self.record(1,job='old')
        self.record(2,job='old')
        self.assertEqual(self.store.snapshot('TEST',now=self.now).count,8)

    def test_zero_legacy_marker_allows_only_later_confirmed_events(self):
        self.assertEqual(self.store.seed_legacy_jobs([self.legacy('zero',0)]),0)
        self.record(1,job='zero')
        self.store.seed_legacy_jobs([self.legacy('zero',1)])
        self.assertEqual(self.store.snapshot('TEST',now=self.now).count,1)

    def test_sqlite_rows_can_seed_legacy_jobs(self):
        with closing(sqlite3.connect(':memory:')) as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT ? AS job_id,? AS code,? AS success_count,? AS updated_at',
                ('old','TEST',2,self.now.isoformat())).fetchone()
            self.assertEqual(self.store.seed_legacy_jobs([row]),1)
        self.assertEqual(self.store.snapshot('TEST',now=self.now).count,2)


if __name__ == '__main__':
    unittest.main()
