"""Offline checks: Telegram roles classify users and never grant bot privileges."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import asyncio
import hashlib
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from core.telegram_bot import (TelegramAccess, TelegramBotService, TelegramControlRequest,
    TelegramJobRequest, parse_control_command)
from core.telegram_users import TelegramUserStore
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread
from gui.pages.telegram import TelegramPage


TOKEN = '123456789:' + 'A' * 35
FINGERPRINT = hashlib.sha256(TOKEN.encode()).hexdigest()


def update(index, chat_id, text):
    return {'update_id': index, 'message': {
        'chat': {'id': chat_id, 'type': 'private'},
        'from': {'id': chat_id, 'is_bot': False}, 'text': text}}


class RolePolicyTests(unittest.TestCase):
    def test_roles_remain_metadata_and_removing_access_removes_role(self):
        access = TelegramAccess((123, 456), roles={123: 'admin'})
        self.assertEqual((access.role(123), access.role(456), access.role(999)), ('admin', 'partner', None))
        self.assertTrue(access.allowed(123))
        self.assertTrue(access.allowed(456))
        self.assertIsNone(access.role(True))
        access.replace((123, 456), roles={123: 'partner', 456: 'admin'})
        self.assertTrue(access.allowed(123))
        self.assertTrue(access.allowed(456))
        access.replace((456,))
        self.assertIsNone(access.role(123))
        self.assertFalse(access.allowed(123))
        with self.assertRaises(ValueError):
            access.replace((123,), roles={123: 'owner'})

    def test_both_roles_can_parse_only_their_own_statistics(self):
        for role in ('admin', 'partner'):
            for command in ('/stats', '/stats 123'):
                with self.subTest(role=role, command=command):
                    self.assertEqual(parse_control_command(command, 123, role, 7),
                        TelegramControlRequest(7, 123, 'stats', 123))

    def test_no_role_can_parse_management_or_someone_elses_statistics(self):
        commands = ('/stats 456', '/users', '/user_add 123 admin Self', '/user_revoke 456',
            '/stats 123 extra', '/users extra')
        for role in ('admin', 'partner'):
            for command in commands:
                with self.subTest(role=role, command=command), self.assertRaises(ValueError):
                    parse_control_command(command, 123, role, 7)

    def test_both_roles_can_create_jobs_and_request_own_statistics(self):
        for role in ('admin', 'partner'):
            with self.subTest(role=role):
                on_job, on_control = Mock(), Mock()
                bot = TelegramBotService(None, TelegramAccess((123,), roles={123: role}),
                    on_job, on_control=on_control)
                bot.handle_update(update(1, 123, '/job ABC123 1000'))
                bot.handle_update(update(2, 123, '/stats'))
                self.assertEqual(on_job.call_args.args[0], TelegramJobRequest(1, 123, 'ABC123', 1000))
                self.assertEqual(on_control.call_args.args[0], TelegramControlRequest(2, 123, 'stats', 123))

    def test_service_never_dispatches_management_for_either_role(self):
        commands = ('/users', '/user_add 123 admin Self', '/stats 456', '/user_revoke 456')
        for role in ('admin', 'partner'):
            with self.subTest(role=role):
                on_control = Mock()
                bot = TelegramBotService(None, TelegramAccess((123,), roles={123: role}),
                    Mock(), on_control=on_control)
                for index, command in enumerate(commands, 1):
                    bot.handle_update(update(index, 123, command))
                on_control.assert_not_called()
                self.assertFalse(bot.pending)

    def test_help_never_advertises_bot_management_even_for_admin(self):
        for role in ('admin', 'partner'):
            with self.subTest(role=role):
                bot = TelegramBotService(None, TelegramAccess((123,), roles={123: role}), Mock())
                bot.handle_update(update(1, 123, '/help'))
                text = bot.outbox.get_nowait()[1]
                self.assertIn('/stats', text)
                for command in ('/users', '/user_add', '/user_revoke'):
                    self.assertNotIn(command, text)


class ReplyRoleTests(unittest.IsolatedAsyncioTestCase):
    async def drain(self, bot):
        task = asyncio.create_task(bot._send_replies())
        try:
            await asyncio.wait_for(bot.outbox.join(), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_changing_classification_keeps_a_queued_ordinary_reply(self):
        for old_role, new_role in (('admin', 'partner'), ('partner', 'admin')):
            with self.subTest(old_role=old_role, new_role=new_role):
                api = SimpleNamespace(call=AsyncMock())
                access = TelegramAccess((123,), roles={123: old_role})
                bot = TelegramBotService(api, access, Mock())
                self.assertTrue(bot.reply(123, 'own statistics'))
                access.replace((123,), roles={123: new_role})
                await self.drain(bot)
                api.call.assert_awaited_once_with('sendMessage', {'chat_id': 123, 'text': 'own statistics'})

    async def test_active_permission_still_blocks_queued_replies_after_revocation(self):
        for role in ('admin', 'partner'):
            with self.subTest(role=role):
                api = SimpleNamespace(call=AsyncMock())
                access = TelegramAccess((123,), roles={123: role})
                bot = TelegramBotService(api, access, Mock())
                self.assertTrue(bot.reply(123, 'private job result'))
                access.replace(())
                await self.drain(bot)
                api.call.assert_not_awaited()


class RoleGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name)
        self.settings = QSettings(str(path / 'telegram.ini'), QSettings.IniFormat)
        self.store = TelegramUserStore(path / 'users.sqlite3')
        self.store.upsert_user(123, 'Partner Own')
        self.store.upsert_user(456, 'Admin', 'admin')
        self.store.upsert_user(789, 'Other Private Name')
        for chat_id in (123,456,789):
            self.store.set_user_mail_keys(chat_id,f'offline-gmailvip-{chat_id}',f'offline-1trieumail-{chat_id}')
        self.store.record_job('own-job', 123, 'OWN', 1000, 10)
        self.store.finish_job('own-job', 'failed', 3)
        self.store.record_job('other-job', 789, 'OTHER', 1000, 10)
        self.store.finish_job('other-job', 'completed', 10)
        self.mochi = MochiChatPage(settings=self.settings)
        self.mochi.input_api_key.setText('offline-shop-key')
        self.page = TelegramPage(self.settings, self.mochi, user_store=self.store)
        self.page.input_token.setText(TOKEN)
        self.worker = SimpleNamespace(fingerprint=FINGERPRINT,
            access=TelegramAccess(self.store.active_ids(), roles=self.store.roles()),
            reply=Mock(), complete_request=Mock(), request_stop=Mock())
        self.page.worker = self.worker
        self.page._accepting_requests = True
        self.addCleanup(self.mochi.close)
        self.addCleanup(self.page.close)

    def control(self, actor, action, target=None, *, name='', role='', update_id=1):
        request = TelegramControlRequest(update_id, actor, action, target, name, role)
        self.page._on_control_requested((FINGERPRINT, request))
        return request

    def test_neither_role_can_forge_management_or_other_stats_requests(self):
        actions = (('users', None, '', ''), ('stats', 789, '', ''),
            ('upsert', 999, 'New', 'admin'), ('revoke', 789, '', ''))
        for actor in (123, 456):
            for index, (action, target, name, role) in enumerate(actions, 1):
                with self.subTest(actor=actor, action=action):
                    self.worker.reply.reset_mock()
                    self.control(actor, action, target, name=name, role=role, update_id=index)
                    self.assertIsNone(self.store.get_user(999))
                    self.assertTrue(self.store.get_user(789).active)
                    for call in self.worker.reply.call_args_list:
                        self.assertNotIn('Other Private Name', call.args[1])
        self.assertEqual(self.store.get_user(456).role, 'admin')

    def test_both_roles_see_only_their_own_durable_statistics(self):
        for actor, own_name in ((123, 'Partner Own'), (456, 'Admin')):
            with self.subTest(actor=actor):
                self.control(actor, 'stats', actor)
                text = self.worker.reply.call_args.args[1]
                self.assertIn(own_name, text)
                self.assertNotIn('Other Private Name', text)
                if actor == 123:
                    self.assertIn('Job thất bại: 1', text)
                    self.assertIn('Bean đã bán: 300', text)

    def test_classification_does_not_change_job_creation_permissions(self):
        with patch.object(MochiWorkerThread, 'start') as start:
            for index, actor in enumerate((123, 456), 101):
                self.page._on_job_requested((FINGERPRINT, TelegramJobRequest(index, actor, 'REMOTE', 1000)))
                row = self.store.lookup_request(FINGERPRINT, index)
                self.assertIsNotNone(row)
                self.assertEqual(row.chat_id, actor)
        self.assertEqual(start.call_count, 2)

    def test_role_change_after_dispatch_preserves_own_stats_access(self):
        for actor, new_role in ((123, 'admin'), (456, 'partner')):
            with self.subTest(actor=actor):
                requests = []
                bot = TelegramBotService(None, self.worker.access, Mock(), on_control=requests.append)
                bot.handle_update(update(actor, actor, '/stats'))
                self.assertEqual(len(requests), 1)
                user = self.store.get_user(actor)
                self.store.upsert_user(actor, user.name, new_role)
                self.page._sync_access()
                self.page._on_control_requested((FINGERPRINT, requests[0]))
                self.assertIn(user.name, self.worker.reply.call_args.args[1])

    def test_local_ui_edits_classification_without_losing_history_or_access(self):
        for row in range(self.page.table_users.rowCount()):
            if self.page.table_users.item(row, 0).text() == '123':
                self.page.table_users.selectRow(row)
                break
        self.app.processEvents()
        self.page.input_name.setText('Renamed Partner')
        self.page.combo_role.setCurrentIndex(self.page.combo_role.findData('admin'))
        self.page._save_user()
        user = self.store.get_user(123)
        self.assertEqual((user.name, user.role, user.active, user.failed_jobs, user.sold_beans),
            ('Renamed Partner', 'admin', True, 1, 300))
        self.assertTrue(self.worker.access.allowed(123))

    def test_revocation_after_dispatch_discards_own_statistics_for_either_role(self):
        for index, actor in enumerate((123, 456), 11):
            with self.subTest(actor=actor):
                self.worker.reply.reset_mock()
                self.store.set_active(actor, False)
                self.page._on_control_requested((FINGERPRINT, TelegramControlRequest(index, actor, 'stats', actor)))
                self.worker.reply.assert_not_called()
                self.worker.complete_request.assert_called_with(index)

    def test_filtering_by_classification_does_not_change_access(self):
        original_ids = self.store.active_ids()
        for query, expected in (('admin', {456}), ('partner', {123, 789})):
            with self.subTest(query=query):
                self.page.input_search.setText(query)
                self.app.processEvents()
                visible = {int(self.page.table_users.item(row, 0).text())
                    for row in range(self.page.table_users.rowCount())}
                self.assertEqual(visible, expected)
                self.assertEqual(self.store.active_ids(), original_ids)
                self.assertTrue(all(self.worker.access.allowed(chat_id) for chat_id in original_ids))

    def test_actor_read_failure_rejects_own_stats_without_sql_detail(self):
        detail = 'injected SQLite actor SELECT detail'
        with patch.object(self.store, 'get_user', side_effect=sqlite3.OperationalError(detail)), \
                patch.object(self.store, 'upsert_user', wraps=self.store.upsert_user) as upsert:
            self.control(456, 'stats', 456, update_id=33)
        upsert.assert_not_called()
        completed = any(call.args == (33,) for call in self.worker.complete_request.call_args_list)
        replied = any(len(call.args) >= 3 and call.args[2] == 33 for call in self.worker.reply.call_args_list)
        self.assertTrue(completed or replied, 'Rejected request must leave the pending queue')
        for call in self.worker.reply.call_args_list:
            self.assertNotIn(detail, call.args[1])


if __name__ == '__main__':
    unittest.main()

