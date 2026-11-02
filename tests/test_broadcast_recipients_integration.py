"""Actual desktop/HTTP/Qt selection transport with a fake Telegram sender."""
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt
from core.telegram_bot import TelegramAccess
import test_remote_job_sync as fixtures


class BroadcastTransportTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.JobSyncTests.setUpClass.__func__)
    tearDown=fixtures.JobSyncTests.tearDown
    until=fixtures.JobSyncTests.until

    def setUp(self):
        fixtures.JobSyncTests.setUp(self)
        self.window.timer.stop()
        page=self.runtime.telegram
        for chat_id,name,role in ((101,'Admin','admin'),(202,'Partner A','partner'),(303,'Partner B','partner')):
            page.store.upsert_user(chat_id,name,role)
        self.bot=SimpleNamespace(fingerprint='offline-selected-broadcast',
            access=TelegramAccess(page.store.active_ids(),roles=page.store.roles()),
            broadcast=Mock(return_value=True),cancel_broadcast=Mock())
        page.worker=self.bot
        page._accepting_requests=True
        self.window.poll()
        self.until(lambda:not self.window._workers and len(self.window.snapshot.get('users',[]))>=4,
            'recipient state did not reach desktop')
        self.window.broadcast.setPlainText('Selected recipients only.\nKeep this line.')
        picker=self.window.broadcast_recipients
        picker.mode.setCurrentIndex(picker.mode.findData('custom'))
        for index in range(picker.people.count()):
            item=picker.people.item(index)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole)==202 else Qt.Unchecked)

    def test_selected_person_reaches_sender_once_and_identical_http_replay_is_deduplicated(self):
        self.assertTrue(self.window.broadcast_send_button.isEnabled())
        self.window.broadcast_send_button.click()
        self.until(lambda:self.bot.broadcast.called and not self.window._workers,'announcement was not accepted')
        _,recipients,text=self.bot.broadcast.call_args.args
        self.assertEqual(recipients,(202,))
        self.assertEqual(text,'Selected recipients only.\nKeep this line.')
        self.assertEqual(self.runtime.telegram._broadcast_last.total,1)
        self.assertFalse(self.window.broadcast_send_button.isEnabled())
        with closing(sqlite3.connect(self.directory/'operations.sqlite3')) as db:
            request_id=db.execute('SELECT id FROM operations').fetchone()[0]
        response=[]
        self.window.task(lambda:self.window.client.command('telegram.broadcast',
            {'text':text,'recipients':[202]},request_id=request_id),response.append)
        self.until(lambda:bool(response) and not self.window._workers,'replay did not complete')
        self.bot.broadcast.assert_called_once()

    def test_stale_selection_is_rejected_without_delivering_to_other_people(self):
        self.runtime.telegram.store.set_active(202,False)
        self.runtime.telegram._sync_access()
        self.window.broadcast_send_button.click()
        self.until(lambda:'telegram.broadcast' in self.bridge.commands and not self.window._workers,
            'stale selection command did not complete')
        self.bot.broadcast.assert_not_called()
        self.assertIsNone(self.runtime.telegram._broadcast_id)
        self.assertEqual(self.runtime.telegram.input_broadcast.toPlainText(),'')

    def test_empty_selection_cannot_become_all_recipients_through_http(self):
        self.window.broadcast_recipients.clear_button.click()
        self.assertFalse(self.window.broadcast_send_button.isEnabled())
        self.window.send('telegram.broadcast',{'text':'Empty selection','recipients':[]})
        self.until(lambda:'telegram.broadcast' in self.bridge.commands and not self.window._workers,
            'empty selection command did not complete')
        self.bot.broadcast.assert_not_called()
        self.assertIsNone(self.runtime.telegram._broadcast_id)


if __name__=='__main__':
    unittest.main()
