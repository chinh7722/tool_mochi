"""Offline persistence and concurrent accounting checks for Telegram users."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest

from core.telegram_users import TelegramUserStore


class UserStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'telegram' / 'users.sqlite3'
        self.store = TelegramUserStore(self.path)
        self.store.upsert_user(123, 'Đối tác A')

    def job(self, jobid='job1', chatid=123, **kwargs):
        return self.store.record_job(jobid, chatid, 'ABC123', 1000, 10, **kwargs)

    def test_users_and_actual_sales_persist_after_reopen(self):
        self.job()
        self.store.finish_job('job1', 'completed', 10, attempted_count=12, failure_count=2)
        user = TelegramUserStore(self.path).get_user(123)
        self.assertEqual((user.name, user.role, user.active), ('Đối tác A', 'partner', True))
        self.assertEqual((user.completed_jobs, user.failed_jobs, user.requested_beans, user.sold_beans),
            (1, 0, 1000, 1000))
        self.assertIsNotNone(self.store.get_job('job1').finalized_at)

    def test_account_failures_do_not_count_as_failed_jobs(self):
        self.job()
        self.store.finish_job('job1', 'completed', 10, failure_count=15)
        self.job('job2')
        self.store.finish_job('job2', 'failed', 3, failure_count=20)
        user = self.store.get_user(123)
        self.assertEqual((user.completed_jobs, user.failed_jobs, user.sold_beans), (1, 1, 1300))

    def test_cancelled_partial_sales_remain_and_are_not_failed_jobs(self):
        self.job()
        self.store.finish_job('job1', 'cancelled', 4)
        user = self.store.get_user(123)
        self.assertEqual((user.completed_jobs, user.failed_jobs, user.sold_beans), (0, 0, 400))

    def test_code_or_invite_limit_failure_is_one_failed_job(self):
        for index, status in enumerate(('invalid_code', 'invite_limit_reached', 'network_error')):
            self.job(f'job{index}')
            self.store.finish_job(f'job{index}', status, index)
        user = self.store.get_user(123)
        self.assertEqual((user.failed_jobs, user.sold_beans), (3, 300))

    def test_replayed_or_late_results_never_change_a_finalized_job(self):
        self.job()
        self.assertTrue(self.store.finish_job('job1', 'failed', 3, failure_count=2))
        self.assertFalse(self.store.finish_job('job1', 'completed', 10))
        self.assertFalse(self.store.update_progress('job1', 20))
        user = self.store.get_user(123)
        self.assertEqual((user.completed_jobs, user.failed_jobs, user.sold_beans), (0, 1, 300))

    def test_progress_and_final_error_preserve_confirmed_successes(self):
        self.job()
        self.store.update_progress('job1', 5, attempted_count=8, failure_count=3)
        self.store.update_progress('job1', 2, attempted_count=4, failure_count=1)
        self.assertEqual(self.store.get_user(123).sold_beans, 500)
        self.store.finish_job('job1', 'failed', 0)
        job = self.store.get_job('job1')
        self.assertEqual((job.success_count, job.attempted_count, job.failure_count), (5, 8, 3))
        self.assertEqual(self.store.get_user(123).sold_beans, 500)

    def test_reopening_keeps_running_jobs_for_other_instances(self):
        self.job()
        self.store.update_progress('job1', 2)
        reopened = TelegramUserStore(self.path)
        job = reopened.get_job('job1')
        self.assertEqual((job.status, job.success_count, job.finalized_at), ('running', 2, None))
        self.assertTrue(reopened.finish_job('job1', 'completed', 10))

    def test_soft_revoke_still_accounts_results_and_restores_history(self):
        self.job()
        self.assertTrue(self.store.set_active(123, False))
        self.assertEqual(self.store.active_ids(), ())
        self.assertEqual(self.store.roles(), {})
        with self.assertRaises(PermissionError):
            self.job('blocked')
        self.store.finish_job('job1', 'completed', 10)
        self.assertEqual(self.store.get_user(123).sold_beans, 1000)
        self.assertFalse(self.store.list_users(include_inactive=False))
        self.assertEqual(len(self.store.list_users()), 1)
        user = self.store.upsert_user(123, 'Tên mới', 'admin')
        self.assertEqual((user.active, user.role, user.completed_jobs, user.sold_beans), (True, 'admin', 1, 1000))
        self.assertEqual(self.store.roles(), {123: 'admin'})

    def test_one_time_legacy_migration_never_changes_existing_users(self):
        self.store.set_active(123, False)
        self.assertEqual(self.store.migrate_legacy((123, 456, 456)), 1)
        self.assertFalse(self.store.get_user(123).active)
        migrated = self.store.get_user(456)
        self.assertEqual((migrated.name, migrated.role, migrated.sold_beans), ('Telegram 456', 'partner', 0))
        self.assertEqual(TelegramUserStore(self.path).migrate_legacy((789,)), 0)
        self.assertIsNone(self.store.get_user(789))

    def test_editing_revoked_user_preserves_access_and_statistics_atomically(self):
        self.job()
        self.store.finish_job('job1', 'completed', 10)
        self.store.set_active(123, False)
        user = self.store.upsert_user(123, 'Tên mới', 'admin', active=False)
        self.assertEqual((user.name, user.role, user.active), ('Tên mới', 'admin', False))
        self.assertEqual((user.completed_jobs, user.sold_beans), (1, 1000))
        self.assertFalse(self.store.active_ids())
        self.assertFalse(self.store.roles())
        with self.assertRaises(PermissionError):
            self.job('still-blocked')
        with self.assertRaises(ValueError):
            self.store.upsert_user(456, 'Other', active=1)

    def test_duplicate_request_or_job_cannot_change_owner_or_start_twice(self):
        self.store.upsert_user(456, 'Other')
        self.assertTrue(self.job(bot_fingerprint='bot', update_id=7))
        other = TelegramUserStore(self.path)
        self.assertFalse(other.record_job('another', 456, 'NEW', 700, 7, 'bot', 7))
        self.assertFalse(other.record_job('job1', 456, 'NEW', 700))
        self.assertEqual(other.lookup_request('bot', 7).job_id, 'job1')
        self.assertEqual(other.get_job('job1').chat_id, 123)
        self.assertIsNone(other.lookup_request('different-bot', 7))
        self.assertTrue(other.record_job('newbot', 456, 'NEW', 700, 7, 'different-bot', 7))

    def test_jobs_without_update_ids_are_independent(self):
        self.assertTrue(self.job('job1'))
        self.assertTrue(self.job('job2'))
        self.assertEqual(self.store.get_user(123).requested_beans, 2000)

    def test_two_instances_compete_for_one_request_atomically(self):
        other = TelegramUserStore(self.path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.store.record_job, 'one', 123, 'ABC', 100, 1, 'bot', 99)
            second = pool.submit(other.record_job, 'two', 123, 'ABC', 100, 1, 'bot', 99)
            self.assertEqual(sorted((first.result(), second.result())), [False, True])
        self.assertEqual(self.store.get_user(123).requested_beans, 100)

    def test_two_instances_finalize_once_and_preserve_other_users(self):
        self.store.upsert_user(456, 'Other', 'admin')
        self.job('mine')
        self.job('theirs', 456)
        other = TelegramUserStore(self.path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.store.finish_job, 'mine', 'completed', 10)
            second = pool.submit(other.finish_job, 'mine', 'completed', 10)
            self.assertEqual(sorted((first.result(), second.result())), [False, True])
        self.assertEqual(self.store.get_user(123).completed_jobs, 1)
        self.assertEqual(self.store.get_user(456).sold_beans, 0)

    def test_invalid_inputs_or_unauthorized_users_never_create_records(self):
        for chat_id in (0, -1, True, '123', 2**63):
            with self.subTest(chat_id=chat_id), self.assertRaises(ValueError):
                self.store.upsert_user(chat_id, 'Name')
        for name, role in (('', 'admin'), ('x' * 121, 'partner'), ('Name', 'owner')):
            with self.subTest(name=name, role=role), self.assertRaises(ValueError):
                self.store.upsert_user(456, name, role)
        with self.assertRaises(PermissionError):
            self.job('unknown', 999)
        with self.assertRaises(ValueError):
            self.store.record_job('invalid', 123, 'ABC', True)
        self.assertIsNone(self.store.get_job('invalid'))
        self.assertFalse(self.store.finish_job('unknown', 'failed', 0))
        self.assertFalse(self.store.update_progress('unknown', 0))


if __name__ == '__main__':
    unittest.main()
