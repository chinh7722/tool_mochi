"""Management API CSV snapshots for actual ledgers, with no purchases or bot sends."""
import csv
import io
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from core.finance import MailPurchaseReceipt
from core.remote.protocol import RemoteError
import test_remote_management as fixtures


class UserReportApiTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.RuntimeTests.setUpClass.__func__)
    setUp=fixtures.RuntimeTests.setUp
    tearDown=fixtures.RuntimeTests.tearDown

    def seed(self):
        store=self.runtime.telegram.store
        store.upsert_user(123,'Partner A')
        store.upsert_user(234,'Admin B','admin')
        for owner in (123,234):
            job='job-'+str(owner)
            store.record_job(job,owner,'CODE'+str(owner),1000,10,sale_rate_units=150000)
            store.record_purchase(MailPurchaseReceipt('purchase-'+str(owner),job,'gmailvip_vn',
                'private-shop-hash',3445,2,12525,order_id='shop-'+str(owner),
                balance_before_units=100000,balance_after_units=87475))
            store.finish_job(job,'failed',2,failure_count=1,attempted_count=3)
        return store

    def test_selected_owner_csv_reports_do_not_include_other_owners_or_private_fields(self):
        self.seed()
        report=self.runtime.command('user.report',{'chat_id':123})
        self.assertEqual(report['owner']['chat_id'],123)
        self.assertEqual({f['kind'] for f in report['files']},{'jobs','purchases'})
        for item in report['files']:
            self.assertTrue(item['text'].startswith('\ufeff'))
            self.assertEqual(item['row_count'],1)
            self.assertIn('job-123',item['text'])
            self.assertNotIn('job-234',item['text'])
            self.assertNotIn('private-shop-hash',item['text'])
            self.assertNotIn('account_hash',item['text'])
        purchases=next(f for f in report['files'] if f['kind']=='purchases')
        row=list(csv.DictReader(io.StringIO(purchases['text'].lstrip('\ufeff'))))[0]
        self.assertEqual(row['Chi phí (VND)'],'125.25')

    def test_privileged_management_can_export_local_admin_and_revoked_users(self):
        store=self.seed()
        store.set_active(123,False)
        for owner in (-1,123,234):
            with self.subTest(owner=owner):
                report=self.runtime.command('user.report',{'chat_id':owner,'kind':'all'})
                self.assertEqual(report['owner']['chat_id'],owner)

    def test_report_kind_and_owner_validation_are_explicit(self):
        self.seed()
        for payload in ({'chat_id':True},{'chat_id':0},{'chat_id':-2},{'chat_id':'123'},
                {'chat_id':999},{'chat_id':123,'kind':'invalid'},{'chat_id':123,'kind':None}):
            with self.subTest(payload=payload),self.assertRaises(RemoteError):
                self.runtime.command('user.report',payload)
        report=self.runtime.command('user.report',{'chat_id':123,'kind':'jobs'})
        self.assertEqual([f['kind'] for f in report['files']],['jobs'])

    def test_export_does_not_finish_jobs_change_balances_or_start_workers(self):
        store=self.seed()
        store.record_job('still-running',123,'RUNNING',5500,55,sale_rate_units=150000)
        before=store.get_job('still-running')
        self.runtime.command('user.report',{'chat_id':123,'kind':'all'})
        self.assertEqual(store.get_job('still-running'),before)
        self.assertFalse(self.runtime.mochi._workers)
        self.assertIsNone(self.runtime.telegram.worker)


if __name__=='__main__': unittest.main()
