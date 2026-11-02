"""Offline announcement drafts preserve selected recipients through VPS polling."""
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtCore import Qt,QSettings,QEvent
from PySide6.QtWidgets import QApplication
from gui.broadcast_recipients import BroadcastRecipients
from gui.remote_window import RemoteMainWindow
from test_remote_classic_ui import snapshot,user


def person(chat_id,name='Partner',role='partner',active=True):
    return dict(chat_id=chat_id,name=name,role=role,active=active)


class RecipientPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.picker=BroadcastRecipients()
        self.people=[person(123,'Admin','admin'),person(234,'Partner A'),person(345,'Partner B')]
        self.picker.set_users(self.people)

    def tearDown(self):
        self.picker.close();self.picker.deleteLater()
        self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def custom(self):
        self.picker.mode.setCurrentIndex(self.picker.mode.findData('custom'))

    def check(self,chat_id):
        for row in range(self.picker.people.count()):
            item=self.picker.people.item(row)
            if item.data(Qt.ItemDataRole.UserRole)==chat_id:
                item.setCheckState(Qt.CheckState.Checked)
                return
        self.fail('Missing recipient')

    def test_modes_choose_all_admin_or_partner_and_custom_starts_empty(self):
        self.assertEqual(self.picker.recipient_ids(),(123,234,345))
        for mode,expected in (('admin',(123,)),('partner',(234,345)),('custom',())):
            self.picker.mode.setCurrentIndex(self.picker.mode.findData(mode))
            self.assertEqual(self.picker.recipient_ids(),expected)

    def test_custom_selection_survives_reorder_rename_and_role_change(self):
        self.custom();self.check(234)
        self.picker.set_users([person(345),person(234,'Renamed','admin'),person(123,'Admin','admin')])
        self.assertEqual(self.picker.recipient_ids(),(234,))
        self.assertIn('1 / 3',self.picker.count.text())

    def test_revoked_removed_or_readded_people_are_not_silently_selected(self):
        self.custom();self.check(234);self.check(345)
        self.picker.set_users([person(123,'Admin','admin'),person(234,active=False)])
        self.assertEqual(self.picker.recipient_ids(),())
        self.picker.set_users(self.people)
        self.assertEqual(self.picker.recipient_ids(),())

    def test_custom_does_not_include_new_people_after_poll(self):
        self.custom();self.check(234)
        self.picker.set_users(self.people+[person(456,'New')])
        self.assertEqual(self.picker.recipient_ids(),(234,))

    def test_switching_modes_keeps_custom_choices_without_adding_all(self):
        self.custom();self.check(234)
        self.picker.mode.setCurrentIndex(self.picker.mode.findData('all'))
        self.assertEqual(self.picker.recipient_ids(),(123,234,345))
        self.custom()
        self.assertEqual(self.picker.recipient_ids(),(234,))

    def test_select_all_and_clear_apply_to_only_eligible_people(self):
        self.custom()
        self.picker.select_all_button.click()
        self.assertEqual(self.picker.recipient_ids(),(123,234,345))
        self.picker.clear_button.click()
        self.assertEqual(self.picker.recipient_ids(),())

    def test_customer_local_inactive_and_invalid_rows_are_excluded(self):
        self.picker.set_users(self.people+[person(-1,'Local'),person(456,role='customer'),
            person(567,active=False),person(True),person('678'),person(789,active='false'),{},None])
        self.assertEqual(self.picker.recipient_ids(),(123,234,345))
        self.assertEqual(self.picker.people.count(),3)

    def test_identical_poll_does_not_reset_items_or_emit_new_selection(self):
        changes=Mock()
        self.picker.selection_changed.connect(changes)
        item=self.picker.people.item(0)
        self.picker.set_users(list(reversed(self.people)))
        self.assertIs(self.picker.people.item(0),item)
        changes.assert_not_called()


class RemoteRecipientsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        prefs=QSettings(str(Path(self.temp.name)/'preferences.ini'),QSettings.Format.IniFormat)
        with patch('gui.remote_shell.QSettings',return_value=prefs), \
             patch('gui.remote_shell.Path.read_text',return_value='{}'):
            self.window=RemoteMainWindow()
        self.window.timer.stop()
        self.window.send=Mock()
        self.state=snapshot()
        self.state['users']=[user(-1,'Local'),{**user(101,'Admin'),'role':'admin'},user(202,'Partner')]
        self.state['telegram'].update(broadcast_recipient_selection=True,broadcast_running=False,running=True,ready=True)
        self.window.render({'state':self.state})
        self.window.broadcast.setPlainText('Thông báo\nNguyên văn.')

    def tearDown(self):
        self.window.close();self.window.deleteLater();self.app.processEvents()
        self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def custom(self,chat_id=202):
        picker=self.window.broadcast_recipients
        picker.mode.setCurrentIndex(picker.mode.findData('custom'))
        if chat_id is not None:
            for row in range(picker.people.count()):
                item=picker.people.item(row)
                if item.data(Qt.ItemDataRole.UserRole)==chat_id:
                    item.setCheckState(Qt.CheckState.Checked)

    def test_send_posts_only_selected_ids_and_unchanged_content(self):
        self.custom()
        self.window.broadcast_send_button.click()
        self.window.send.assert_called_once_with('telegram.broadcast',
            {'text':'Thông báo\nNguyên văn.','recipients':[202]})

    def test_general_user_filter_and_selection_do_not_change_audience(self):
        self.window.user_search.setText('Admin')
        self.window.users.selectRow(1)
        self.window.broadcast_send_button.click()
        self.assertEqual(self.window.send.call_args.args[1]['recipients'],[101,202])

    def test_empty_selection_stays_disabled_after_global_button_refresh(self):
        self.custom(None)
        self.window.enable_actions(True)
        self.assertFalse(self.window.broadcast_send_button.isEnabled())
        self.window.telegram_presentation.send_broadcast()
        self.window.send.assert_not_called()
        self.assertIn('Chọn ít nhất',self.window.broadcast_status.text())

    def test_state_poll_preserves_custom_selection_and_draft(self):
        self.custom()
        changed=deepcopy(self.state)
        changed['users']=[user(303,'New'),user(202,'Renamed'),user(101,'Admin')]
        self.window.render({'state':changed})
        self.assertEqual(self.window.broadcast_recipients.recipient_ids(),(202,))
        self.assertEqual(self.window.broadcast.toPlainText(),'Thông báo\nNguyên văn.')

    def test_old_server_capability_blocks_send_instead_of_broadcasting_to_all(self):
        del self.state['telegram']['broadcast_recipient_selection']
        self.window.render({'state':self.state})
        self.assertFalse(self.window.broadcast_send_button.isEnabled())
        self.window.telegram_presentation.send_broadcast()
        self.window.send.assert_not_called()
        self.assertIn('cần cập nhật',self.window.broadcast_status.text())

    def test_pending_post_and_server_broadcast_keep_send_locked(self):
        self.window.broadcast_send_button.click()
        self.window.enable_actions(True)
        self.window.telegram_presentation.send_broadcast()
        self.assertEqual(self.window.send.call_count,1)
        self.assertFalse(self.window.broadcast_send_button.isEnabled())
        self.state['telegram']['broadcast_running']=True
        self.window.render({'state':self.state})
        self.assertFalse(self.window.broadcast_recipients.isEnabled())
        self.assertTrue(self.window.broadcast_cancel_button.isEnabled())
        self.state['telegram']['broadcast_running']=False
        self.window.render({'state':self.state})
        self.assertTrue(self.window.broadcast_send_button.isEnabled())

    def test_offline_and_closing_block_direct_sending(self):
        self.window.connection_lost('offline')
        self.window.telegram_presentation.send_broadcast()
        self.assertFalse(self.window.broadcast_send_button.isEnabled())
        self.window.send.assert_not_called()
        self.window.render({'state':{**self.state,'closing':True}})
        self.window.telegram_presentation.send_broadcast()
        self.window.send.assert_not_called()

    def test_stopped_or_connecting_bot_cannot_send(self):
        for running,ready in ((False,False),(True,False)):
            self.state['telegram'].update(running=running,ready=ready)
            self.window.render({'state':self.state})
            self.assertFalse(self.window.broadcast_send_button.isEnabled())
            self.window.telegram_presentation.send_broadcast()
            self.window.send.assert_not_called()
            self.assertIn('Bật bot',self.window.broadcast_status.text())

    def test_old_poll_during_mutation_does_not_release_pending_send(self):
        self.window.broadcast_send_button.click()
        self.window._mutating=True
        self.window.render({'state':self.state})
        self.window._mutating=False
        self.window._refresh_requested=True
        self.window.render({'state':self.state})
        self.window.enable_actions(True)
        self.assertFalse(self.window.broadcast_send_button.isEnabled())
        self.window.telegram_presentation.send_broadcast()
        self.assertEqual(self.window.send.call_count,1)

    def test_oversize_selected_audience_is_blocked_before_post(self):
        self.state['users']=[user(index,'Partner') for index in range(1,2002)]
        self.window.render({'state':self.state})
        self.assertFalse(self.window.broadcast_send_button.isEnabled())
        self.window.telegram_presentation.send_broadcast()
        self.window.send.assert_not_called()
        self.assertIn('2000',self.window.broadcast_status.text())


if __name__=='__main__':
    unittest.main()
