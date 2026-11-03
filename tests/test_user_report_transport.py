"""Desktop report export through actual HTTP, Qt and the durable mutation journal."""
from contextlib import closing
from copy import deepcopy
import csv
import io
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
from core.finance import MailPurchaseReceipt,money_units
import test_remote_job_sync as fixtures


class UserReportTransportTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.JobSyncTests.setUpClass.__func__)
    tearDown=fixtures.JobSyncTests.tearDown
    until=fixtures.JobSyncTests.until

    def setUp(self):
        fixtures.JobSyncTests.setUp(self)
        self.window.timer.stop()
        self.store=self.runtime.telegram.store
        self.store.upsert_user(300,'Report target','partner')
        self.store.upsert_user(400,'Other admin private','admin')
        self.store.set_user_mail_keys(300,'private-gmail-do-not-export','private-trieu-do-not-export')
        self.store.record_job('target-job',300,'TARGET',300,3,sale_rate_units=money_units('1500.25'),
            schedule_data={'customer_name':'Khách báo cáo'})
        self.store.finish_job('target-job','completed',3,attempted_count=4,failure_count=1)
        self.store.record_purchase(MailPurchaseReceipt('target-purchase','target-job','gmailvip_vn',
            'private-account-hash-do-not-export',3445,3,12345,order_id='SHOP-TARGET',
            balance_before_units=200000,balance_after_units=187655))
        self.store.record_job('other-job-private',400,'OTHERPRIVATE',100,1,sale_rate_units=100000)
        self.store.finish_job('other-job-private','completed',1,attempted_count=1)
        self.store.record_purchase(MailPurchaseReceipt('other-purchase-private','other-job-private',
            'gmailvip_vn','other-account-hash',3445,1,99999,order_id='SHOP-PRIVATE'))
        self.responses=[]
        self.bridge.after_command=lambda action,result:self.responses.append(deepcopy(result)) if action=='user.report' else None
        self.window.poll()
        self.until(lambda:any(row['chat_id']==300 for row in self.window.users._rows) and not self.window._workers,
            'report owners did not reach the desktop')
        self.window.users.selectRow(next(index for index,row in enumerate(self.window.users._rows)
            if row['chat_id']==300))
        # A typed ID must never replace the actual selected owner of this export.
        self.window.user_id.setText('400')
        self.exports=self.directory/'report_exports'
        self.exports.mkdir()

    def export(self):
        with patch('gui.remote_window.QFileDialog.getExistingDirectory',return_value=str(self.exports)) as choose:
            self.window.export_user_report_button.click()
            self.until(lambda:len(list(self.exports.glob('*.csv')))==2 and not self.window._workers,
                'HTTP report did not save both CSV files')
        choose.assert_called_once()
        return {path.name:path.read_bytes() for path in self.exports.glob('*.csv')}

    def rows(self,raw):
        return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'),newline='')))

    def test_desktop_exports_selected_owner_csvs_through_real_api_with_exact_amounts(self):
        documents=self.export()
        self.assertEqual(self.bridge.commands,['user.report'])
        report=self.responses[0]
        self.assertEqual(report['owner']['chat_id'],300)
        self.assertEqual({item['kind'] for item in report['files']},{'jobs','purchases'})
        self.assertEqual([item['row_count'] for item in report['files']],[1,1])
        for item in report['files']:
            raw=documents[item['filename']]
            self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
            self.assertFalse(raw.startswith(b'\xef\xbb\xbf\xef\xbb\xbf'))
            self.assertEqual(raw,item['text'].encode('utf-8'))
            text=raw.decode('utf-8-sig')
            for secret in ('Other admin private','OTHERPRIVATE','other-purchase-private',
                    'private-gmail-do-not-export','private-trieu-do-not-export',
                    'private-account-hash-do-not-export',self.token):
                self.assertNotIn(secret,text)
        jobs=self.rows(documents[next(item['filename'] for item in report['files'] if item['kind']=='jobs')])
        purchases=self.rows(documents[next(item['filename'] for item in report['files'] if item['kind']=='purchases')])
        self.assertEqual(jobs[0]['Mã job'],'target-job')
        self.assertEqual(jobs[0]['Doanh thu đã ghi nhận (VND)'],'4500.75')
        self.assertEqual(jobs[0]['Chi phí mail đã xác định (VND)'],'123.45')
        self.assertEqual(jobs[0]['Lợi nhuận đã xác định (VND)'],'4377.30')
        self.assertEqual(purchases[0]['Mã giao dịch mua mail'],'target-purchase')
        self.assertEqual(purchases[0]['Số dư shop trước mua (VND)'],'2000.00')
        self.assertEqual(purchases[0]['Số dư shop sau mua (VND)'],'1876.55')

    def test_same_http_request_replays_original_snapshot_without_generating_new_export(self):
        original_files=self.export()
        original=deepcopy(self.responses[0])
        with closing(sqlite3.connect(self.directory/'operations.sqlite3')) as db:
            rows=db.execute('SELECT id FROM operations').fetchall()
        self.assertEqual(len(rows),1)
        request_id=rows[0][0]
        self.store.record_job('new-job-after-export',300,'LATER',100,1,sale_rate_units=100000)
        self.store.finish_job('new-job-after-export','completed',1,attempted_count=1)
        responses=[]
        self.window.task(lambda:self.window.client.command('user.report',{'chat_id':300,'kind':'all'},
            request_id=request_id),responses.append)
        self.until(lambda:bool(responses) and not self.window._workers,'identical HTTP replay did not complete')
        self.assertEqual(responses[0]['result'],original)
        self.assertEqual({path.name:path.read_bytes() for path in self.exports.glob('*.csv')},original_files)
        self.assertTrue(all('new-job-after-export' not in document['text'] for document in responses[0]['result']['files']))
        with closing(sqlite3.connect(self.directory/'operations.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM operations').fetchone()[0],1)


if __name__=='__main__': unittest.main()
