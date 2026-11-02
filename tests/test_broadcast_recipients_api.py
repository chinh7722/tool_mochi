"""Offline explicit announcement audiences through the actual VPS Qt controller."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from PySide6.QtWidgets import QApplication
from core.remote.protocol import RemoteError
from core.remote.runtime import Runtime
from core.telegram_bot import TelegramAccess
from core.telegram_broadcast import BroadcastProgress


class BroadcastRecipientApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.environment=patch.dict(os.environ,{'HOSHINO_DATA_DIR':self.temp.name})
        self.environment.start()
        self.runtime=Runtime(self.temp.name)
        self.page=self.runtime.telegram
        self.store=self.page.store
        self.store.upsert_user(123,'Admin','admin')
        self.store.upsert_user(234,'Partner','partner')
        self.store.upsert_user(345,'Revoked','partner',active=False)
        self.store.ensure_customer(456,'Customer')
        self.bot=SimpleNamespace(fingerprint='offline-selected-audience',
            access=TelegramAccess(self.store.active_ids(),roles=self.store.roles()),
            broadcast=Mock(return_value=True),cancel_broadcast=Mock(),request_stop=Mock(),
            isRunning=Mock(return_value=False))
        self.page.worker=self.bot
        self.page._accepting_requests=True
        self.page.input_broadcast.setPlainText('Previous editor')
        self.page.lbl_broadcast_status.setText('Previous status')

    def tearDown(self):
        self.page.worker=None
        self.page._broadcast_id=None
        self.runtime.shutdown()
        self.app.processEvents()
        for page in (self.runtime.telegram,self.runtime.mochi,self.runtime.proxy,self.runtime.mail_proxy):
            page.deleteLater()
        self.app.processEvents()
        self.environment.stop()
        self.temp.cleanup()

    def send(self,recipients):
        return self.runtime.command('telegram.broadcast',{'text':'Selected notice\nOriginal text.',
            'recipients':recipients})

    def assert_unchanged(self):
        self.bot.broadcast.assert_not_called()
        self.assertIsNone(self.page._broadcast_id)
        self.assertEqual(self.page.input_broadcast.toPlainText(),'Previous editor')
        self.assertEqual(self.page.lbl_broadcast_status.text(),'Previous status')

    def test_one_selected_partner_is_the_only_recipient_and_total_is_one(self):
        self.send([234])
        campaign,recipients,text=self.bot.broadcast.call_args.args
        self.assertEqual(recipients,(234,))
        self.assertEqual(text,'Selected notice\nOriginal text.')
        self.assertEqual(self.page._broadcast_last.total,1)
        self.assertEqual(self.page._broadcast_last.broadcast_id,campaign)
        telegram=self.runtime.state()['telegram']
        self.assertTrue(telegram['broadcast_running'])
        self.assertTrue(telegram['broadcast_recipient_selection'])

    def test_duplicates_are_removed_and_snapshot_keeps_the_requested_order(self):
        selected=[234,123,234]
        self.send(selected)
        selected[:]=[345]
        self.assertEqual(self.bot.broadcast.call_args.args[1],(234,123))
        self.assertEqual(self.page._broadcast_last.total,2)

    def test_omitted_recipients_keeps_legacy_all_active_operators(self):
        self.runtime.command('telegram.broadcast',{'text':'Legacy notice'})
        self.assertEqual(set(self.bot.broadcast.call_args.args[1]),{123,234})
        self.assertEqual(self.page._broadcast_last.total,2)

    def test_empty_null_and_malformed_audiences_never_fall_back_to_everyone(self):
        for selected in ([],None,{},'123',123,True,[True],['123'],[0],[-1],
                [2**63],(123,),[123]*2001):
            with self.subTest(selected=repr(selected)[:80]),self.assertRaises(RemoteError):
                self.send(selected)
            self.assert_unchanged()

    def test_revoked_customer_unknown_and_local_targets_reject_the_entire_audience(self):
        for invalid in (345,456,999,-1):
            with self.subTest(invalid=invalid),self.assertRaises(RemoteError):
                self.send([123,invalid])
            self.assert_unchanged()

    def test_selection_is_checked_against_current_permissions_at_send_time(self):
        self.store.set_active(234,False)
        self.assertTrue(self.bot.access.allowed(234))
        with self.assertRaises(RemoteError):
            self.send([234])
        self.assert_unchanged()

    def test_in_progress_campaign_cannot_be_overwritten_by_a_second_command(self):
        self.send([123])
        campaign=self.page._broadcast_id
        original=self.page.input_broadcast.toPlainText()
        with self.assertRaises(RemoteError):
            self.runtime.command('telegram.broadcast',{'text':'Replace in flight','recipients':[234]})
        self.bot.broadcast.assert_called_once()
        self.assertEqual(self.page._broadcast_id,campaign)
        self.assertEqual(self.page.input_broadcast.toPlainText(),original)
        self.assertEqual(self.page._broadcast_last.total,1)

    def test_offline_and_stopping_bot_do_not_change_the_editor_or_begin_a_campaign(self):
        for attr,value in (('worker',None),('_accepting_requests',False),('_closing',True)):
            previous=getattr(self.page,attr)
            setattr(self.page,attr,value)
            with self.subTest(attr=attr),self.assertRaises(RemoteError):
                self.send([123])
            setattr(self.page,attr,previous)
            self.assert_unchanged()

    def test_invalid_text_is_rejected_before_the_editor_changes(self):
        for text in (' ','x'*4001,'😀'*2001):
            with self.subTest(length=len(text)),self.assertRaises(RemoteError):
                self.runtime.command('telegram.broadcast',{'text':text,'recipients':[123]})
            self.assert_unchanged()

    def test_list_larger_than_the_worker_outbox_can_be_selected_explicitly(self):
        selected=list(range(1000,1257))
        for chat_id in selected:
            self.store.upsert_user(chat_id,f'Partner {chat_id}')
        self.send(selected)
        self.assertEqual(self.bot.broadcast.call_args.args[1],tuple(selected))
        self.assertEqual(self.page._broadcast_last.total,257)

    def test_failed_worker_handoff_is_reported_without_claiming_active_send(self):
        self.bot.broadcast.return_value=False
        with self.assertRaises(RemoteError):
            self.send([234])
        self.assertIsNone(self.page._broadcast_id)
        self.assertFalse(self.runtime.state()['telegram']['broadcast_running'])
        self.assertEqual(self.page._broadcast_last.sent,0)
        self.assertEqual(self.page._broadcast_last.total,1)

    def test_finishing_selected_campaign_clears_authoritative_running_flag(self):
        self.send([234])
        campaign=self.page._broadcast_id
        self.page._on_broadcast_progress(self.bot.fingerprint,
            BroadcastProgress(campaign,1,sent=1,finished=True))
        self.assertFalse(self.runtime.state()['telegram']['broadcast_running'])
        self.assertEqual(self.page._broadcast_last.sent,1)

    def test_controller_direct_empty_selection_does_not_start_a_legacy_broadcast(self):
        self.assertFalse(self.page._send_broadcast(recipients=[]))
        self.bot.broadcast.assert_not_called()
        self.assertIsNone(self.page._broadcast_id)


if __name__=='__main__':
    unittest.main()
