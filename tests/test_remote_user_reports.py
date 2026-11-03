"""Remote desktop exports the selected owner's CSVs with safe atomic local writes."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
import test_remote_classic_ui as fixtures


class RemoteUserReportTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.ClassicRemoteUiTests.setUpClass.__func__)
    new_window=fixtures.ClassicRemoteUiTests.new_window
    send=fixtures.ClassicRemoteUiTests.send
    tearDown=fixtures.ClassicRemoteUiTests.tearDown

    def setUp(self):
        fixtures.ClassicRemoteUiTests.setUp(self)
        self.window.send=Mock()
        self.report=dict(owner=dict(chat_id=101,name='Partner một',role='partner',active=True),
            files=[dict(filename='jobs_101_20261103.csv',kind='jobs',text='\ufeffMã job,Tên\r\njob1,Khách A\r\n',row_count=1),
                dict(filename='mail_101_20261103.csv',kind='purchases',text='\ufeffMã mua,Chi phí\r\np1,1000.25\r\n',row_count=1)],
            generated_at='2026-11-03T09:30:00+07:00')

    def select(self,owner):
        self.window.users.selectRow(next(index for index,row in enumerate(self.window.users._rows)
            if row['chat_id']==owner))

    def save(self,report=None,directory=None,expected_owner=101):
        folder=Path(directory) if directory else Path(self.temp.name)/'exports'
        folder.mkdir(exist_ok=True)
        with patch('gui.remote_window.QFileDialog.getExistingDirectory',return_value=str(folder)) as choose:
            self.window.save_user_report(self.report if report is None else report,expected_owner=expected_owner)
        return folder,choose

    def test_export_requests_actual_selected_user_not_typed_form_id(self):
        self.select(101)
        self.window.user_id.setText('202')
        self.window.export_user_report_button.click()
        self.window.send.assert_called_once()
        args=self.window.send.call_args.args
        self.assertEqual(args[:2],('user.report',{'chat_id':101,'kind':'all'}))
        self.assertTrue(callable(args[2]))

    def test_filtered_selection_cannot_export_someone_hidden(self):
        self.select(101)
        self.window.user_search.setText('Partner two')
        self.window.export_user_report()
        self.window.send.assert_not_called()
        self.assertIn('Chọn một dòng',self.window.status.text())

    def test_local_and_inactive_users_are_supported_from_desktop(self):
        self.select(-1)
        self.window.export_user_report()
        self.assertEqual(self.window.send.call_args.args[1],{'chat_id':-1,'kind':'all'})
        self.window.send.reset_mock()
        self.state['users'][1]['active']=False
        self.window.render({'state':self.state})
        self.select(101)
        self.window.export_user_report()
        self.assertEqual(self.window.send.call_args.args[1]['chat_id'],101)

    def test_export_button_obeys_offline_and_busy_gating(self):
        self.select(101)
        self.window.connection_lost('offline')
        self.assertFalse(self.window.export_user_report_button.isEnabled())
        self.window.export_user_report_button.click()
        self.window.send.assert_not_called()
        self.window.render({'state':self.state})
        self.window._mutating=True
        self.window.enable_actions(True)
        self.assertFalse(self.window.export_user_report_button.isEnabled())

    def test_saves_two_utf8_csv_files_and_chooses_directory_once(self):
        folder,choose=self.save()
        choose.assert_called_once()
        self.assertIn('Partner một',choose.call_args.args[1])
        self.assertIn('101',choose.call_args.args[1])
        for item in self.report['files']:
            raw=(folder/item['filename']).read_bytes()
            self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
            self.assertFalse(raw.startswith(b'\xef\xbb\xbf\xef\xbb\xbf'))
            self.assertEqual(raw.decode('utf-8'),item['text'])
        self.assertIn('Đã lưu 2 báo cáo',self.window.status.text())

    def test_duplicate_bom_is_normalized_without_changing_csv_content(self):
        report=deepcopy(self.report)
        report['files'][0]['text']='\ufeff'+report['files'][0]['text']
        folder,_=self.save(report)
        self.assertEqual((folder/report['files'][0]['filename']).read_bytes(),self.report['files'][0]['text'].encode('utf-8'))

    def test_existing_exports_are_preserved_and_new_files_get_suffix(self):
        folder=Path(self.temp.name)/'exports'
        folder.mkdir()
        for item in self.report['files']:
            (folder/item['filename']).write_text('previous export',encoding='utf-8')
        self.save(directory=folder)
        for item in self.report['files']:
            self.assertEqual((folder/item['filename']).read_text(),'previous export')
            self.assertTrue((folder/(Path(item['filename']).stem+'_2.csv')).is_file())

    def test_cancel_directory_chooser_writes_nothing(self):
        with patch('gui.remote_window.QFileDialog.getExistingDirectory',return_value=''):
            self.window.save_user_report(self.report,expected_owner=101)
        self.assertIn('hủy lưu',self.window.status.text())
        self.assertFalse((Path(self.temp.name)/'exports').exists())

    def test_malformed_names_and_wrong_owner_are_rejected_before_file_dialog(self):
        cases=[]
        for name in ('../escape.csv','..\\escape.csv','D:\\escape.csv','report.txt','/escape.csv'):
            report=deepcopy(self.report)
            report['files'][0]['filename']=name
            cases.append(report)
        report=deepcopy(self.report)
        report['owner']['chat_id']=202
        cases.append(report)
        for report in cases:
            with self.subTest(report=report),patch('gui.remote_window.QFileDialog.getExistingDirectory') as choose:
                self.window.save_user_report(report,expected_owner=101)
                choose.assert_not_called()
                self.assertIn('không hợp lệ',self.window.status.text())

    def test_preparing_write_failure_leaves_no_success_message(self):
        target=Mock()
        target.open.return_value=False
        with patch('PySide6.QtCore.QSaveFile',return_value=target):
            folder,_=self.save()
        self.assertIn('Chưa lưu được',self.window.status.text())
        self.assertEqual(list(folder.iterdir()),[])
        target.cancelWriting.assert_called()

    def test_partial_commit_is_reported_as_partial_not_complete(self):
        targets=[Mock(),Mock()]
        for target in targets:
            target.open.return_value=True
            target.write.side_effect=lambda raw:len(raw)
        targets[0].commit.return_value=True
        targets[1].commit.return_value=False
        with patch('PySide6.QtCore.QSaveFile',side_effect=targets):
            self.save()
        text=self.window.status.text()
        self.assertIn('Đã lưu 1/2',text)
        self.assertIn(self.report['files'][0]['filename'],text)
        self.assertNotIn('Đã lưu 2 báo cáo',text)
        targets[1].cancelWriting.assert_called()


if __name__=='__main__': unittest.main()
