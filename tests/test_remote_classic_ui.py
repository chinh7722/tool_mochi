"""Offline regressions for the classic-looking, remote-only management UI."""
from copy import deepcopy
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from PySide6.QtCore import QDateTime,QSettings,QObject,Signal
from PySide6.QtWidgets import QApplication,QPushButton
from gui.remote_window import RemoteMainWindow


def user(chat_id,name,role='partner'):
    return dict(chat_id=chat_id,name=name,role=role,active=True,completed_jobs=1,
        failed_jobs=0,requested_beans=100,sold_beans=100,revenue_units=100000,
        cost_units=10000,pending_costs=0,unpriced_jobs=0)


def job(job_id,customer='Same customer',status='Đang chạy'):
    return dict(job_id=job_id,customer_name=customer,creator_name='Local',creator_chat_id=None,
        invite_code='TEST',status=status,target_count=2,success_count=1,failure_count=0,
        attempted_count=1,created_at='2026-11-03T12:00:00+07:00',scheduled_at=None)


def proxy(identifier,status='READY'):
    return dict(id=identifier,endpoint='http://127.0.0.1:'+('8001' if identifier=='one' else '8002'),
        status=status,provider='Xoay - proxy.vn',speed='-',error='reset' if status=='FAILED' else '',
        has_key=True)


def snapshot():
    return dict(version=1,closing=False,jobs=[],active_jobs=0,queued_jobs=0,scheduled_jobs=0,
        job_limit=2,proxies=[],mail_proxies=[],history=[],
        users=[user(-1,'Local'),user(101,'Partner one'),user(202,'Partner two')],customers=[],
        telegram=dict(running=False,ready=False,status='Bot chưa bật.',has_token=False,
            auto_start=False,broadcast='Chưa gửi.'),
        sepay=dict(running=False,status='SePay chưa bật.',has_token=False,enabled=False,
            fields=dict(bank_id='',account_number='',bank_name='',account_name=''),transactions=[]),
        lookup=dict(busy=False,status='Chưa tra cứu.',profiles=[]),
        settings=dict(provider='gmailvip_vn',product_ids='123',concurrency=3,proxy_slots=1,
            job_limit=2,use_proxy=False,google_proxy=True,mail_proxy=True,app_proxy=True,
            redeem_proxy=True,has_gmailvip_key=True,has_one_trieu_key=True,sale_rate='1500'))


class ClassicRemoteUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.preferences=QSettings(str(Path(self.temp.name)/'preferences.ini'),QSettings.Format.IniFormat)
        self.windows=[]
        self.window=self.new_window()
        self.state=snapshot()
        self.calls=[]
        self.window.send=self.send
        self.window.render({'state':self.state})

    def new_window(self):
        # Do not read the actual deployment token or the user's saved drafts.
        with patch('gui.remote_shell.QSettings',return_value=self.preferences), \
             patch('gui.remote_shell.Path.read_text',return_value='{}'):
            window=RemoteMainWindow()
        window.timer.stop()
        self.windows.append(window)
        return window

    def tearDown(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.app.processEvents()
        self.preferences.sync()
        self.temp.cleanup()

    def send(self,action,payload=None,callback=None):
        self.calls.append((action,deepcopy(payload or {})))
        if callback:
            callback({'lines':['Log for '+str((payload or {}).get('job_id',''))]}
                if action=='job.logs' else {'message':'Offline test result'})

    def render(self,state):
        self.state=state
        self.window.render({'state':state})

    def test_job_selection_survives_reorder_for_identical_customer_names(self):
        state=deepcopy(self.state)
        state['jobs']=[job('first'),job('second')]
        self.render(state)
        self.window.jobs.selectRow(next(i for i,r in enumerate(self.window.jobs._rows) if r['job_id']=='second'))
        state['jobs']=[job('second'),job('first')]
        self.render(state)
        self.assertEqual(self.window.selected(self.window.jobs,'job_id'),'second')
        self.window.selected_send(self.window.jobs,'job_id','job.stop')
        self.assertEqual(self.calls,[('job.stop',{'job_id':'second'})])

    def test_vanished_job_clears_selection_before_stop(self):
        state=deepcopy(self.state)
        state['jobs']=[job('first'),job('second')]
        self.render(state)
        self.window.jobs.selectRow(next(i for i,r in enumerate(self.window.jobs._rows) if r['job_id']=='second'))
        state['jobs']=[job('first')]
        self.render(state)
        self.window.selected_send(self.window.jobs,'job_id','job.stop')
        self.assertEqual(self.calls,[])
        self.assertEqual(self.window.jobs.currentRow(),-1)

    def test_filtered_user_cannot_be_revoked_by_stale_selection(self):
        self.window.users.selectRow(1)
        self.window.user_search.setText('Partner two')
        self.window.selected_send(self.window.users,'chat_id','user.active',{'active':False})
        self.assertEqual(self.calls,[])
        self.assertEqual(self.window.users.currentRow(),-1)

    def test_filtered_customer_cannot_open_balance_edit_for_hidden_row(self):
        state=deepcopy(self.state)
        state['customers']=[dict(chat_id=303,name='Customer one',balance_units=10000,
            total_orders=1,completed_orders=1,failed_orders=0),
            dict(chat_id=404,name='Customer two',balance_units=20000,
            total_orders=0,completed_orders=0,failed_orders=0)]
        self.render(state)
        self.window.customers.selectRow(0)
        self.window.customer_search.setText('Customer two')
        with patch('gui.remote_window.QDialog',side_effect=AssertionError('Hidden customer must not be edited')):
            self.window.edit_balance()
        self.assertEqual(self.calls,[])

    def test_proxy_selection_follows_identity_between_active_and_failed_lists(self):
        state=deepcopy(self.state)
        state['proxies']=[proxy('one'),proxy('two')]
        self.render(state)
        view=self.window.proxy_views['account']
        view.active_table.selectRow(0)
        state['proxies']=[proxy('two'),proxy('one','FAILED')]
        self.render(state)
        self.assertEqual(view.selected_identifier(),'one')
        view.selected_action('proxy.check')
        self.assertEqual(self.calls,[('proxy.check',{'pool':'account','id':'one'})])

    def test_proxy_filter_clears_selection_before_delete_and_keeps_pool_metrics(self):
        state=deepcopy(self.state)
        state['proxies']=[proxy('one'),proxy('two','FAILED')]
        self.render(state)
        view=self.window.proxy_views['account']
        view.active_table.selectRow(0)
        view.search.setText('8002')
        view.selected_action('proxy.delete')
        self.assertEqual(self.calls,[])
        self.assertIsNone(view.selected_identifier())
        self.assertEqual(view.metrics['active'].value.text(),'1')
        self.assertEqual(view.metrics['failed'].value.text(),'1')

    def test_polling_does_not_replace_settings_or_user_editor_drafts(self):
        w=self.window
        w.users.selectRow(1)
        w.pick_user(1,0)
        w.user_name.setText('Unsaved partner name')
        w.user_role.setCurrentText('admin')
        w.user_gmail.setText('fake-unsaved-private-key')
        w.settings_fields['product_ids'].setText('unsaved-product-order')
        w.settings_fields['gmailvip_key'].setText('fake-unsaved-shop-key')
        w.bot_auto.setChecked(True)
        w.sepay_fields['bank_name'].setText('Unsaved bank')
        state=deepcopy(self.state)
        state['settings']['provider']='multi_shop'
        state['settings']['product_ids']='remote-new-products'
        state['users'][1]['name']='Remote new name'
        self.render(state)
        self.assertEqual(w.user_name.text(),'Unsaved partner name')
        self.assertEqual(w.user_role.currentText(),'admin')
        self.assertEqual(w.user_gmail.text(),'fake-unsaved-private-key')
        self.assertEqual(w.settings_fields['product_ids'].text(),'unsaved-product-order')
        self.assertEqual(w.settings_fields['gmailvip_key'].text(),'fake-unsaved-shop-key')
        self.assertTrue(w.bot_auto.isChecked())
        self.assertEqual(w.sepay_fields['bank_name'].text(),'Unsaved bank')

    def test_dashboard_double_click_opens_exact_job_in_inner_log_tab(self):
        state=deepcopy(self.state)
        state['jobs']=[job('first'),job('second')]
        self.render(state)
        # Dashboard displays newest first; jobs table remains creation order.
        self.window.jobs.selectRow(0)
        self.window.workspace_tabs.setCurrentIndex(2)
        self.window.dashboard_jobs.cellDoubleClicked.emit(0,0)
        self.assertEqual(self.calls,[('job.logs',{'job_id':'second'})])
        self.assertEqual(self.window.pages.currentIndex(),1)
        self.assertEqual(self.window.workspace_tabs.currentIndex(),0)
        self.assertEqual(self.window.mochi_tabs.currentIndex(),2)
        self.assertIn('second',self.window.logs.toPlainText())

    def test_row_log_button_does_not_use_another_jobs_current_selection(self):
        state=deepcopy(self.state)
        state['jobs']=[job('first'),job('second')]
        self.render(state)
        first=next(i for i,r in enumerate(self.window.jobs._rows) if r['job_id']=='first')
        second=next(i for i,r in enumerate(self.window.jobs._rows) if r['job_id']=='second')
        self.window.jobs.selectRow(first)
        buttons=self.window.jobs.cellWidget(second,7).findChildren(QPushButton)
        next(button for button in buttons if button.text()=='Log').click()
        self.assertEqual(self.calls,[('job.logs',{'job_id':'second'})])
        self.assertEqual(self.window.mochi_tabs.currentIndex(),2)

    def test_create_job_uses_selected_batch_and_explicit_vietnam_schedule(self):
        w=self.window
        w.job_code.setText('ABC123')
        w.job_customer.setText('Customer note')
        w.job_points.setValue(250)
        w.job_batch.addItem('Purchased batch','batch_fixture.json')
        w.job_batch.setCurrentIndex(1)
        w.schedule.setChecked(True)
        w.schedule_time.setDateTime(QDateTime.fromString('2030-04-05 09:35','yyyy-MM-dd HH:mm'))
        w.create_job()
        self.assertEqual(self.calls,[('job.create',dict(code='ABC123',points=250,
            customer='Customer note',batch='batch_fixture.json',scheduled_at='2030-04-05T09:35:00+07:00'))])
        self.assertEqual(w.job_account_count.text(),'(= 3 accounts)')
        self.assertEqual(w.job_quote.text(),'4,500')

    def test_closing_snapshot_locks_row_and_toolbar_mutations(self):
        state=deepcopy(self.state)
        state['closing']=True
        state['jobs']=[job('running')]
        state['proxies']=[proxy('one')]
        self.render(state)
        self.assertTrue(self.window._job_row_buttons)
        self.assertTrue(all(not button.isEnabled() for button,_ in self.window._job_row_buttons))
        self.assertTrue(all(not button.isEnabled() for button in self.window._mutation_buttons))
        self.assertTrue(all(not button.isEnabled() for button in self.window.proxy_views['account']._inline_buttons))

    def test_constructor_and_snapshot_never_start_operational_store_or_worker(self):
        with patch.object(sqlite3,'connect',side_effect=AssertionError('No local operational database')), \
             patch('gui.remote_window.RequestWorker.start',side_effect=AssertionError('No implicit remote request')):
            other=self.new_window()
            other.render({'state':deepcopy(self.state)})
            self.assertIsNone(other.client)
            self.assertFalse(other._workers)

    def test_command_error_keeps_actions_locked_until_state_confirms_connection(self):
        class Reply(QObject):
            error=Signal(str)
            finished=Signal()
        reply=Reply()
        w=self.window
        w.client=object()
        with patch.object(w,'task',return_value=reply),patch.object(w,'poll') as poll:
            RemoteMainWindow.send(w,'job.stop',{'job_id':'unknown'})
            reply.error.emit('Connection lost after request')
            reply.finished.emit()
            self.assertFalse(w._connected)
            self.assertTrue(all(not button.isEnabled() for button in w._mutation_buttons))
            poll.assert_called_once()
        w.render({'state':deepcopy(self.state)})
        self.assertTrue(w._connected)
        self.assertTrue(w.job_start_button.isEnabled())


if __name__=='__main__':
    unittest.main()
