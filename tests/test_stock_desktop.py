"""Desktop selection, creation payloads, confirmations and export completion."""
from pathlib import Path
import sys
from unittest.mock import Mock,patch
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
import test_remote_classic_ui as fixtures
from PySide6.QtWidgets import QDialog,QMessageBox,QPlainTextEdit
from PySide6.QtCore import Qt


class StockDesktopTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.ClassicRemoteUiTests.setUpClass.__func__)
    new_window=fixtures.ClassicRemoteUiTests.new_window
    send=fixtures.ClassicRemoteUiTests.send
    tearDown=fixtures.ClassicRemoteUiTests.tearDown

    def setUp(self):
        fixtures.ClassicRemoteUiTests.setUp(self)
        self.window.send=Mock()
        self.panel=self.window.stock_inventory_panel
        self.result=dict(total=1,beans='2200',busy=False,error='',batches=[],exports=[],rows=[
            dict(id=7,email='fake@example.com',password='fake-password',beans='2200',target=2500,
                infinite=True,invite_code='FAKE7',status='paused',job_id=None)])
        self.panel.render(self.result)

    def test_stock_mode_is_selectable_and_sends_own_form_payload(self):
        self.window.select_stock_mode(True)
        form=self.window.stock_create_panel
        form.count.setValue(5);form.beans.setValue(5500);form.product.setText('123');form.infinite.setChecked(True)
        form.gmailvip_key.setText('creation-gmail');form.one_trieu_key.setText('creation-trieu')
        self.window.create_job()
        args=self.window.send.call_args.args
        self.assertEqual(args[:2],('stock.create',dict(count=5,beans=5500,infinite=True,product_ids='123',
            gmailvip_key='creation-gmail',one_trieu_key='creation-trieu')))
        args[2]({'message':'Accepted'})
        self.assertEqual((form.gmailvip_key.text(),form.one_trieu_key.text()),('',''))
        self.assertTrue(self.window.invite_form.isHidden())
        self.assertFalse(form.beans.isEnabled())

    def test_checked_account_survives_new_live_values(self):
        self.panel.table.item(0,0).setCheckState(Qt.CheckState.Checked)
        self.result['rows'][0]['beans']='2300';self.panel.render(self.result)
        self.assertEqual(self.panel.selected,{7})
        self.assertEqual(self.panel.table.item(0,0).checkState(),Qt.CheckState.Checked)

    def test_cancel_delete_sends_nothing_and_confirm_sends_exact_ids(self):
        self.panel.selected={7}
        with patch('gui.stock_widgets.QMessageBox.question',return_value=QMessageBox.StandardButton.No):self.panel.delete()
        self.window.send.assert_not_called()
        with patch('gui.stock_widgets.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):self.panel.delete()
        self.assertEqual(self.window.send.call_args.args[:2],('stock.delete',{'ids':[7]}))

    def test_export_keeps_plain_credentials_and_only_finishes_when_popup_closes(self):
        self.panel.show_export(dict(id='export-test',content='fake@example.com|fake-password',state='pending'))
        dialog=self.panel.findChild(QDialog)
        self.assertEqual(dialog.findChild(QPlainTextEdit).toPlainText(),'fake@example.com|fake-password')
        self.window.send.assert_not_called()
        dialog.reject();self.app.processEvents()
        self.assertEqual(self.window.send.call_args.args[:2],('stock.export.finish',{'export_id':'export-test'}))

    def test_reopening_completed_export_does_not_complete_it_again(self):
        self.panel.show_export(dict(id='export-test',content='fake@example.com|fake-password',state='completed'))
        self.panel.findChild(QDialog).accept();self.app.processEvents()
        self.window.send.assert_not_called()

    def test_row_stop_button_targets_only_its_own_account(self):
        self.result['rows'][0]['status']='accumulating';self.panel.render(self.result)
        with patch('gui.stock_widgets.QMessageBox.question',return_value=QMessageBox.StandardButton.Yes):
            self.panel.table.cellWidget(0,8).click()
        self.assertEqual(self.window.send.call_args.args[:2],('stock.stop',{'ids':[7]}))

    def test_inventory_opens_terminal_bean_job_and_creation_log(self):
        self.result['rows'][0].update(last_job_id='stock-failed-job',batch_id='stock-batch')
        self.panel.render(self.result)
        from PySide6.QtWidgets import QPushButton
        actions=self.panel.table.cellWidget(0,9).findChildren(QPushButton)
        actions[0].click();self.assertEqual(self.window._log_job_id,'stock-failed-job')
        actions[1].click();self.assertEqual(self.window._log_job_id,'stock-batch')

    def test_running_search_filters_by_invite_job_and_owner_without_losing_records(self):
        from copy import deepcopy
        rows=[fixtures.job('first'),fixtures.job('second')]
        rows[0].update(invite_code='INV42',creator_name='Đặng',creator_chat_id=555)
        state=deepcopy(self.state);state['jobs']=rows;self.window.render({'state':state})
        for query in ('inv42','FIRST','dang','555'):
            self.window.job_search.setText(query)
            self.assertEqual([r['job_id'] for r in self.window.jobs._rows],['first'])
        self.window.job_search.clear();self.assertEqual(self.window.jobs.rowCount(),2)

    def test_reopening_same_job_ignores_old_pending_log_response(self):
        from types import SimpleNamespace
        from gui.remote_window import RemoteMainWindow
        w=self.window;w.client=Mock();w._connected=True;w._log_polling=False
        w.poll_logs=RemoteMainWindow.poll_logs.__get__(w);w.log_picker.load=Mock()
        callbacks=[]
        w.task=Mock(side_effect=lambda factory,callback:callbacks.append(callback) or SimpleNamespace(finished=Mock()))
        w.open_job_logs('same-job');old=callbacks[-1];w.open_job_logs('same-job')
        old(dict(result=dict(lines=['stale delta'],cursor=50,epoch='old',reset=False)))
        self.assertEqual(w.logs.toPlainText(),'');self.assertIsNone(w._log_cursor)
        w._log_polling=False;w.poll_logs()
        callbacks[-1](dict(result=dict(lines=['full saved log'],cursor=60,epoch='new',reset=True)))
        self.assertEqual(w.logs.toPlainText(),'full saved log')
        w.client=None


if __name__=='__main__':unittest.main()
