"""Detailed per-owner CSV, exact amounts, privacy and fresh bot permissions."""
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import csv
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from core.finance import MailPurchaseReceipt, money_units
from core.telegram_users import TelegramUserStore
from core import user_reports


def rows(report,kind):
    document=next(f for f in report['files'] if f['kind']==kind)
    return list(csv.DictReader(io.StringIO(document['text'].removeprefix('\ufeff'),newline='')))


class UserReportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'users.sqlite3'
        self.store=TelegramUserStore(self.path)
        for identifier,name,role in ((100,'Admin A','admin'),(200,'Admin B','admin'),
                (300,'Partner A','partner'),(400,'Partner revoked','partner')):
            self.store.upsert_user(identifier,name,role)
        self.store.set_active(400,False)
        self.store.set_user_mail_keys(300,'private-gmail-key-DO-NOT-EXPORT','private-trieu-key-DO-NOT-EXPORT')
        self.store.record_job('completed-job',300,'GOOD',1000,10,'private-bot-fingerprint',1,
            sale_rate_units=money_units('1500.25'),schedule_data={'customer_name':'Khách A',
                'source_mail_batch':'C:/private/mail/password-file.json'})
        self.store.finish_job('completed-job','completed',10,attempted_count=11,failure_count=1)
        self.store.record_purchase(self.receipt('purchase-1','completed-job',12345,
            balance_before_units=200000,balance_after_units=187655,order_id='SHOP-ORDER-1'))
        self.store.record_purchase(self.receipt('purchase-2','completed-job',55))
        self.store.record_job('combo-job',300,'COMBO',5500,55,sale_rate_units=100000,
            schedule_data={'combo':True,'customer_name':'Combo customer'})
        self.store.reschedule_combo_job('combo-job',datetime.now(timezone.utc)+timedelta(days=1,minutes=1),10,12,2)
        self.store.record_purchase(self.receipt('pending','combo-job',None,state='intent'))

    def receipt(self,identifier,job,cost,**kwargs):
        return MailPurchaseReceipt(identifier,job,'gmailvip_vn','private-account-hash-DO-NOT-EXPORT',3445,10,cost,**kwargs)

    def customer(self,identifier=500):
        self.store.ensure_customer(identifier,'Customer only')
        self.store.set_customer_balance(identifier,2000000,'Offline balance',expected_units=0)
        self.store.record_customer_order('customer-job',identifier,'CUSTOMER',1000,10,'bot-customer',50,100000)

    def test_two_csv_files_have_bom_crlf_vietnamese_headers_and_safe_filenames(self):
        report=self.store.user_report(300)
        self.assertEqual(report['owner'],dict(chat_id=300,name='Partner A',role='partner',active=True))
        self.assertEqual({f['kind'] for f in report['files']},{'jobs','purchases'})
        self.assertTrue(report['generated_at'].endswith('+07:00'))
        for document in report['files']:
            with self.subTest(kind=document['kind']):
                self.assertTrue(document['text'].startswith('\ufeff'))
                self.assertIn('\r\n',document['text'])
                self.assertIn('Tên người dùng',document['text'])
                self.assertTrue(document['filename'].isascii())
                self.assertRegex(document['filename'],r'^hoshino_300_(jobs|purchases)_\d{8}_\d{6}\.csv$')
                self.assertEqual(document['row_count'],len(rows(report,document['kind'])))

    def test_admin_can_report_partner_other_admin_and_revoked_operator(self):
        for target in (100,200,300,400):
            with self.subTest(target=target):
                self.assertTrue(self.store.authorize_user_report(100,target))
                self.assertEqual(self.store.user_report(target,actor_id=100)['owner']['chat_id'],target)
        self.store.set_active(200,False)
        self.assertEqual(self.store.user_report(200,actor_id=100)['owner']['role'],'admin')

    def test_partner_only_own_reports_and_privileged_ui_can_report_local(self):
        self.assertTrue(self.store.authorize_user_report(300,300))
        self.assertEqual(self.store.user_report(300,actor_id=300)['owner']['chat_id'],300)
        for target in (100,200,400,-1):
            for call in (lambda:self.store.user_report(target,actor_id=300),
                    lambda:self.store.authorize_user_report(300,target)):
                with self.subTest(target=target),self.assertRaises(PermissionError): call()
        local=self.store.user_report(-1)
        self.assertEqual(local['owner']['chat_id'],-1)
        self.assertEqual([f['row_count'] for f in local['files']],[0,0])
        with self.assertRaises(PermissionError): self.store.user_report(-1,actor_id=100)

    def test_revoked_unknown_or_newly_demoted_actor_cannot_export_other_users(self):
        for actor in (400,999):
            with self.subTest(actor=actor),self.assertRaises(PermissionError):
                self.store.user_report(300,actor_id=actor)
        self.assertTrue(self.store.authorize_user_report(100,200))
        self.store.upsert_user(100,'Demoted admin','partner')
        with self.assertRaises(PermissionError): self.store.authorize_user_report(100,200)
        with self.assertRaises(PermissionError): self.store.user_report(200,actor_id=100)
        self.store.set_active(300,False)
        with self.assertRaises(PermissionError): self.store.authorize_user_report(300,300)

    def test_customer_placeholders_are_not_bot_export_targets_and_unknown_is_rejected(self):
        self.customer()
        with self.assertRaises(ValueError): self.store.user_report(500,actor_id=100)
        with self.assertRaises(ValueError): self.store.authorize_user_report(100,500)
        with self.assertRaises(ValueError): self.store.user_report(999)
        self.assertEqual(self.store.user_report(500)['owner']['chat_id'],500)
        for target in (True,'300',0,-2,2**63):
            with self.subTest(target=target),self.assertRaises(ValueError): self.store.user_report(target)

    def test_export_is_scoped_to_owner_and_preserves_exact_purchases_and_balances(self):
        self.store.record_job('other-job',200,'OTHER',100,1,sale_rate_units=100)
        self.store.record_purchase(self.receipt('other-purchase','other-job',999))
        report=self.store.user_report(300)
        purchases={r['Mã giao dịch mua mail']:r for r in rows(report,'purchases')}
        self.assertEqual(set(purchases),{'purchase-1','purchase-2','pending'})
        row=purchases['purchase-1']
        self.assertEqual((row['Mã job'],row['Mã mời'],row['Nhà cung cấp'],row['Product ID']),
            ('completed-job','GOOD','gmailvip_vn','3445'))
        self.assertEqual((row['Chi phí (VND)'],row['Số dư shop trước mua (VND)'],row['Số dư shop sau mua (VND)']),
            ('123.45','2000.00','1876.55'))
        self.assertEqual(row['Mã đơn shop'],'SHOP-ORDER-1')
        jobs={r['Mã job']:r for r in rows(report,'jobs')}
        self.assertEqual(jobs['completed-job']['Doanh thu đã ghi nhận (VND)'],'15002.50')
        self.assertEqual(jobs['completed-job']['Chi phí mail đã xác định (VND)'],'124.00')
        self.assertEqual(jobs['completed-job']['Lợi nhuận đã xác định (VND)'],'14878.50')

    def test_pending_cost_and_missing_price_are_blank_and_not_false_zero_profit(self):
        self.store.record_job('unpriced',300,'NOPRICE',100,1)
        self.store.finish_job('unpriced','completed',1)
        report=self.store.user_report(300)
        jobs={r['Mã job']:r for r in rows(report,'jobs')}
        self.assertEqual(jobs['combo-job']['Giao dịch mail chờ đối soát'],'1')
        self.assertEqual(jobs['combo-job']['Chi phí mail tổng (VND)'],'')
        self.assertEqual(jobs['combo-job']['Lợi nhuận đã xác định (VND)'],'')
        self.assertIn('Chờ đối soát',jobs['combo-job']['Tình trạng tài chính'])
        self.assertEqual(jobs['unpriced']['Đơn giá VND / 100 bean'],'')
        self.assertEqual(jobs['unpriced']['Doanh thu đã ghi nhận (VND)'],'')
        self.assertEqual(jobs['unpriced']['Lợi nhuận đã xác định (VND)'],'')
        pending=next(r for r in rows(report,'purchases') if r['Mã giao dịch mua mail']=='pending')
        self.assertEqual(pending['Chi phí (VND)'],'')
        self.assertEqual(pending['Tình trạng chi phí'],'Chờ đối soát')

    def test_failed_partial_success_and_combo_totals_survive_and_negative_profit_is_numeric(self):
        self.store.record_job('failed-partial',300,'FAILED',1000,10,sale_rate_units=100000)
        self.store.record_purchase(self.receipt('failed-cost','failed-partial',350))
        self.store.finish_job('failed-partial','failed',3,attempted_count=10,failure_count=7)
        records={r['Mã job']:r for r in rows(self.store.user_report(300),'jobs')}
        row=records['failed-partial']
        self.assertEqual((row['Trạng thái job'],row['Bean thực tế'],row['Lượt thành công'],row['Lượt lỗi']),
            ('Thất bại','300','3','7'))
        self.assertEqual(row['Doanh thu đã ghi nhận (VND)'],'0.00')
        self.assertEqual(row['Lợi nhuận đã xác định (VND)'],'-3.50')
        combo=records['combo-job']
        self.assertEqual((combo['Combo'],combo['Trạng thái job'],combo['Bean yêu cầu'],combo['Bean thực tế']),
            ('Có','Chờ lượt combo','5500','1000'))

    def test_customer_settlement_records_actual_charge_reserve_and_refund(self):
        self.customer()
        self.store.record_purchase(self.receipt('customer-mail','customer-job',600))
        self.store.finish_job('customer-job','failed',2)
        row=rows(self.store.user_report(500,kind='jobs'),'jobs')[0]
        self.assertTrue(row['Mã đơn khách'])
        self.assertEqual((row['Tiền giữ đơn khách (VND)'],row['Tiền đã thu đơn khách (VND)'],
            row['Tiền hoàn đơn khách (VND)'],row['Doanh thu đã ghi nhận (VND)']),
            ('10000.00','2000.00','8000.00','2000.00'))

    def test_private_keys_identifiers_fingerprints_paths_and_raw_metadata_are_excluded(self):
        report=self.store.user_report(300)
        text='\n'.join(f['text'] for f in report['files'])
        for secret in ('private-gmail-key','private-trieu-key','private-account-hash','private-bot-fingerprint',
                'password-file.json','source_mail_batch','account_hash','bot_fingerprint','schedule_data'):
            with self.subTest(secret=secret): self.assertNotIn(secret,text)
        self.assertIn('Khách A',text)

    def test_formula_like_text_is_escaped_but_large_id_is_not_rounded(self):
        identifier=2**63-2
        self.store.upsert_user(identifier,'  =HYPERLINK("test")')
        self.store.record_job('safe-job',identifier,'=FORMULA',100,1,
            schedule_data={'customer_name':'\t=SUM(1,1)'})
        row=rows(self.store.user_report(identifier,kind='jobs'),'jobs')[0]
        self.assertEqual(row['Chat ID'],"'"+str(identifier))
        self.assertTrue(row['Tên người dùng'].startswith("'"))
        self.assertEqual(row['Mã mời'],"'=FORMULA")
        self.assertTrue(row['Tên khách / Ghi chú'].startswith("'\t="))

    def test_timestamps_are_always_utc_plus_seven_and_seconds_are_retained(self):
        with self.store._connection(write=True) as db:
            db.execute("UPDATE jobs SET created_at='2026-11-03T01:02:03+00:00' WHERE job_id='completed-job'")
        records={r['Mã job']:r for r in rows(self.store.user_report(300),'jobs')}
        self.assertEqual(records['completed-job']['Tạo lúc (UTC+7)'],'2026-11-03 08:02:03+07:00')

    def test_replayed_purchase_does_not_create_duplicate_report_row_or_job_revenue(self):
        self.store.record_purchase(self.receipt('purchase-1','completed-job',12345,
            balance_before_units=200000,balance_after_units=187655,order_id='SHOP-ORDER-1'))
        report=self.store.user_report(300)
        self.assertEqual(len(rows(report,'purchases')),3)
        self.assertEqual(len(rows(report,'jobs')),2)
        row=next(r for r in rows(report,'jobs') if r['Mã job']=='completed-job')
        self.assertEqual(row['Doanh thu đã ghi nhận (VND)'],'15002.50')

    def test_more_than_500_jobs_and_purchases_are_all_exported(self):
        with self.store._connection(write=True) as db:
            db.executemany('''INSERT INTO jobs(job_id,chat_id,code,requested_beans,target_count,created_at,updated_at)
                VALUES (?,300,'HISTORY',100,1,'2026-11-01T00:00:00+00:00','2026-11-01T00:00:00+00:00')''',
                [(f'history-{i}',) for i in range(550)])
            db.executemany('''INSERT INTO mail_purchases(purchase_id,job_id,provider,account_hash,product_id,
                quantity,cost_units,source,state,created_at,updated_at)
                VALUES (?,'completed-job','shop','private-profile',1,1,1,'manual_verified','confirmed',
                    '2026-11-01T00:00:00+00:00','2026-11-01T00:00:00+00:00')''',
                [(f'history-purchase-{i}',) for i in range(550)])
        report=self.store.user_report(300)
        self.assertEqual({f['kind']:f['row_count'] for f in report['files']},{'jobs':552,'purchases':553})

    def test_one_kind_empty_history_and_size_failure_are_explicit(self):
        for kind in ('jobs','purchases'):
            report=self.store.user_report(200,kind=kind)
            self.assertEqual(len(report['files']),1)
            self.assertEqual(report['files'][0]['row_count'],0)
        with self.assertRaises(ValueError): self.store.user_report(300,kind='unknown')
        report=self.store.user_report(300)
        raw_size=sum(len(f['text'].encode('utf-8')) for f in report['files'])
        with patch('core.user_reports.MAX_REPORT_BYTES',raw_size+1),self.assertRaisesRegex(ValueError,'vượt giới hạn'):
            self.store.user_report(300)

    def test_both_files_use_same_sql_snapshot_while_another_connection_writes(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute('PRAGMA journal_mode=WAL')
        original=user_reports._job
        called=False
        def race(owner,row):
            nonlocal called
            if not called:
                called=True
                self.store.record_purchase(self.receipt('later-purchase','completed-job',1))
            return original(owner,row)
        with patch('core.user_reports._job',side_effect=race):
            report=self.store.user_report(300)
        self.assertTrue(called)
        self.assertEqual(len(rows(report,'purchases')),3)
        self.assertEqual(len(rows(self.store.user_report(300),'purchases')),4)


if __name__=='__main__': unittest.main()
