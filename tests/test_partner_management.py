"""Admin partner privileges are checked atomically against durable account state."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from core.telegram_users import LOCAL_USER_ID,TelegramUserStore


class PartnerManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'users.sqlite3'
        self.store = TelegramUserStore(self.path)
        self.store.upsert_user(123,'Admin','admin')
        self.store.upsert_user(456,'Partner','partner')
        self.store.upsert_user(789,'Another admin','admin')

    def calls(self, actor):
        return (
            lambda:self.store.admin_upsert_partner(actor,999,'New partner'),
            lambda:self.store.admin_revoke_partner(actor,456),
            lambda:self.store.admin_list_partners(actor),
            lambda:self.store.admin_get_partner(actor,456),
        )

    def customer_order(self, target=888):
        self.store.ensure_customer(target,'Customer')
        self.store.set_customer_balance(target,500000,'offline funding',expected_units=0)
        self.store.record_customer_order('customer-job',target,'TEST',100,1,'offline',99,100000)

    def test_active_admin_enrolls_partner_without_granting_admin(self):
        result = self.store.admin_upsert_partner(123,999,'  Partner mới  ')
        self.assertEqual((result.chat_id,result.name,result.role,result.active),
            (999,'Partner mới','partner',True))
        self.assertEqual(self.store.roles()[999],'partner')
        self.assertEqual(TelegramUserStore(self.path).admin_get_partner(123,999),result)

    def test_all_management_actions_reject_partner_missing_and_revoked_admin(self):
        for actor in (456,999):
            for call in self.calls(actor):
                with self.subTest(actor=actor),self.assertRaises(PermissionError):
                    call()
        self.store.set_active(123,False)
        for call in self.calls(123):
            with self.assertRaises(PermissionError):
                call()
        self.assertIsNone(self.store.get_user(999))
        self.assertTrue(self.store.get_user(456).active)

    def test_current_database_role_overrides_previously_observed_admin_permission(self):
        stale_actor = self.store.get_user(123)
        self.assertEqual(stale_actor.role,'admin')
        other_connection = TelegramUserStore(self.path)
        other_connection.upsert_user(123,'Admin demoted','partner')
        for call in self.calls(stale_actor.chat_id):
            with self.assertRaises(PermissionError):
                call()
        self.assertIsNone(self.store.get_user(999))

    def test_waiting_mutation_rechecks_admin_after_concurrent_revocation_commits(self):
        attempted = threading.Event()
        def manage():
            attempted.set()
            return self.store.admin_upsert_partner(123,999,'Must stay absent')
        with ThreadPoolExecutor(max_workers=1) as pool,closing(sqlite3.connect(self.path,timeout=2)) as other:
            other.execute('BEGIN IMMEDIATE')
            other.execute('UPDATE users SET active=0 WHERE chat_id=123')
            future = pool.submit(manage)
            self.assertTrue(attempted.wait(2))
            other.commit()
            with self.assertRaises(PermissionError):
                future.result(timeout=3)
        self.assertIsNone(self.store.get_user(999))

    def test_admin_and_local_targets_cannot_be_modified_or_viewed_as_partners(self):
        for target in (123,789):
            before = self.store.get_user(target)
            with self.assertRaises(PermissionError):
                self.store.admin_upsert_partner(123,target,'Try demotion')
            with self.assertRaises(ValueError):
                self.store.admin_revoke_partner(123,target)
            with self.assertRaises(ValueError):
                self.store.admin_get_partner(123,target)
            self.assertEqual(self.store.get_user(target),before)
        for method in (self.store.admin_upsert_partner,self.store.admin_revoke_partner,self.store.admin_get_partner):
            with self.assertRaises(ValueError):
                method(123,LOCAL_USER_ID,*(['Local change'] if method==self.store.admin_upsert_partner else []))
        self.assertTrue(self.store.get_user(LOCAL_USER_ID).active)

    def test_invalid_ids_and_names_do_not_write_membership_or_user(self):
        for target in (0,-2,True,'999',2**63):
            with self.subTest(target=target),self.assertRaises(ValueError):
                self.store.admin_upsert_partner(123,target,'Invalid')
        for name in ('','  ','x'*121,None):
            with self.subTest(name=name),self.assertRaises(ValueError):
                self.store.admin_upsert_partner(123,999,name)
        for call in self.calls(True):
            with self.assertRaises(ValueError):
                call()
        self.assertIsNone(self.store.get_user(999))

    def test_revoke_keeps_stats_jobs_and_keys_then_add_restores_same_partner(self):
        self.store.set_user_mail_keys(456,'offline-gmail','offline-trieu')
        self.store.record_job('completed',456,'TEST',1000,10,sale_rate_units=150000)
        self.store.finish_job('completed','completed',10,attempted_count=12,failure_count=2)
        self.store.record_job('partial',456,'TEST',1000,10,sale_rate_units=150000)
        self.store.finish_job('partial','failed',3,attempted_count=4,failure_count=1)
        before = self.store.get_user(456)
        self.assertTrue(self.store.admin_revoke_partner(123,456))
        self.assertFalse(self.store.admin_revoke_partner(123,456))
        self.assertNotIn(456,self.store.active_ids())
        revoked = self.store.admin_get_partner(123,456)
        self.assertEqual((revoked.sold_beans,revoked.completed_jobs,revoked.failed_jobs,revoked.revenue_units),
            (1300,1,1,before.revenue_units))
        restored = self.store.admin_upsert_partner(123,456,'Partner restored')
        self.assertTrue(restored.active)
        self.assertEqual(restored.sold_beans,1300)
        self.assertEqual(len(self.store.list_jobs(456)),2)
        self.assertEqual(self.store.get_user_mail_keys(456),
            {'gmailvip_vn':'offline-gmail','1trieumail_com':'offline-trieu'})
        reopened = TelegramUserStore(self.path)
        self.assertEqual(reopened.admin_get_partner(123,456),restored)

    def test_partner_list_includes_inactive_operators_and_excludes_admins_local_and_customers(self):
        self.store.upsert_user(654,'Revoked without jobs','partner',active=False)
        self.customer_order()
        self.assertEqual({user.chat_id for user in self.store.admin_list_partners(123)},{456,654})
        with self.assertRaises(ValueError):
            self.store.admin_get_partner(123,888)
        with self.assertRaises(ValueError):
            self.store.admin_revoke_partner(123,888)
        self.assertIsNotNone(self.store.get_customer_order_for_job('customer-job'))

    def test_enrolling_existing_customer_preserves_wallet_orders_and_explicit_membership_on_revoke(self):
        self.customer_order()
        customer_before = self.store.get_customer(888)
        order_before = self.store.get_customer_order_for_job('customer-job')
        self.assertTrue(self.store.admin_upsert_partner(123,888,'Now partner').active)
        self.assertTrue(self.store.admin_revoke_partner(123,888))
        self.assertEqual(self.store.get_customer(888),customer_before)
        self.assertEqual(self.store.get_customer_order_for_job('customer-job'),order_before)
        self.assertIn(888,{user.chat_id for user in self.store.admin_list_partners(123)})
        self.assertFalse(self.store.admin_get_partner(123,888).active)

    def test_desktop_enrollment_marker_keeps_partner_visible_after_customer_history_and_revoke(self):
        self.customer_order()
        self.store.upsert_user(888,'Desktop partner','partner')
        self.store.set_active(888,False)
        self.assertFalse(self.store.admin_get_partner(123,888).active)

    def test_failed_transaction_rolls_back_enrollment_and_membership_together(self):
        with patch.object(self.store,'_admin_partner',side_effect=OSError('offline rollback')):
            with self.assertRaises(OSError):
                self.store.admin_upsert_partner(123,999,'Rollback')
        self.assertIsNone(self.store.get_user(999))
        with self.store._connection() as db:
            self.assertIsNone(db.execute("SELECT value FROM metadata WHERE key='bot_partner:999'").fetchone())

    def test_legacy_partner_with_operator_history_remains_visible_after_customer_order(self):
        self.store.record_job('operator-job',456,'TEST',100,1)
        self.store.finish_job('operator-job','completed',1)
        self.store.set_active(456,False)
        self.customer_order(456)
        with self.store._connection(write=True) as db:
            db.execute("DELETE FROM metadata WHERE key='bot_partner:456'")
        self.assertFalse(self.store.admin_get_partner(123,456).active)

    def test_missing_partner_read_and_revoke_do_not_create_any_rows(self):
        for method in (self.store.admin_get_partner,self.store.admin_revoke_partner):
            with self.assertRaises(ValueError):
                method(123,999)
        self.assertIsNone(self.store.get_user(999))


if __name__=='__main__':
    unittest.main()
