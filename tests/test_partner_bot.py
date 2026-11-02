"""Offline admin partner commands, current SQL authorization and private replies."""
import asyncio
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from core.telegram_bot import (TelegramAccess, TelegramApiError, TelegramBotService, TelegramControlRequest,
    parse_control_command)
from core.telegram_users import TelegramUserStore
from gui.pages.mochi_chat import MochiChatPage
from gui.pages.telegram import TelegramPage


def update(index, actor, text, **changes):
    message = {'chat': {'id': actor, 'type': 'private'},
        'from': {'id': actor, 'is_bot': False}, 'text': text}
    message.update(changes)
    return {'update_id': index, 'message': message}


class PartnerCommandTests(unittest.TestCase):
    def test_admin_commands_parse_target_name_and_page_without_role_promotion(self):
        expected = (
            ('/partner_add 456 Partner Mới', 'partner_add', 456, 'Partner Mới', 1),
            ('/PARTNER_ADD@ours 456 Partner Mới', 'partner_add', 456, 'Partner Mới', 1),
            ('/partner_remove 456', 'partner_remove', 456, '', 1),
            ('/partners', 'partner_list', None, '', 1),
            ('/partners 2', 'partner_list', None, '', 2),
            ('/partner 456', 'partner_info', 456, '', 1),
        )
        for command, action, target, name, page in expected:
            with self.subTest(command=command):
                request = parse_control_command(command, 123, 'admin', 7)
                self.assertEqual((request.action, request.target_chat_id, request.name, request.page),
                    (action, target, name, page))

    def test_partner_and_unknown_role_cannot_parse_admin_commands(self):
        for role in ('partner', None, 'owner'):
            for command in ('/partner_add 456 New', '/partner_remove 456', '/partners', '/partner 456'):
                with self.subTest(role=role, command=command), self.assertRaises(ValueError):
                    parse_control_command(command, 123, role, 7)

    def test_malformed_commands_and_non_private_targets_are_rejected(self):
        for command in ('/partner_add', '/partner_add 456', '/partner_add 0 Name',
                '/partner_add -1 Name', '/partner_add True Name', '/partner_add 1e3 Name',
                '/partner_add 9223372036854775808 Name', '/partner_remove',
                '/partner_remove 456 extra', '/partner', '/partner 456 extra',
                '/partners 0', '/partners -1', '/partners two', '/partners 1 extra'):
            with self.subTest(command=command), self.assertRaises(ValueError):
                parse_control_command(command, 123, 'admin', 7)

    def test_service_dispatches_once_only_for_private_admin_and_correct_bot(self):
        requests = []
        bot = TelegramBotService(None, TelegramAccess((123, 234), roles={123: 'admin'}),
            Mock(), on_control=requests.append)
        bot.username = 'ours'
        first = update(1, 123, '/partner_add@ours 456 New Partner')
        bot.handle_update(first)
        bot.handle_update(first)
        bot.handle_update(update(2, 234, '/partners'))
        bot.handle_update(update(3, 123, '/partners', chat={'id': 123, 'type': 'group'}))
        bot.handle_update(update(4, 123, '/partners', **{'from': {'id': 999, 'is_bot': False}}))
        bot.handle_update(update(5, 123, '/partners@another'))
        self.assertEqual(len(requests), 1)
        self.assertEqual((requests[0].action, requests[0].target_chat_id), ('partner_add', 456))
        self.assertEqual(bot.pending, {1})

    def test_help_advertises_management_to_admin_only(self):
        for role in ('admin', 'partner'):
            with self.subTest(role=role):
                bot = TelegramBotService(None, TelegramAccess((123,), roles={123: role}), Mock())
                bot.handle_update(update(1, 123, '/help'))
                text = bot.outbox.get_nowait()[1]
                for command in ('/partner_add', '/partner_remove', '/partners', '/partner '):
                    if role == 'admin':
                        self.assertIn(command, text)
                    else:
                        self.assertNotIn(command, text)


class PartnerReplyTests(unittest.IsolatedAsyncioTestCase):
    async def drain(self, bot):
        sender = asyncio.create_task(bot._send_replies())
        try:
            await asyncio.wait_for(bot.outbox.join(), 1)
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    async def test_admin_reply_is_rejected_for_partner_before_enqueue(self):
        bot = TelegramBotService(SimpleNamespace(call=AsyncMock()), TelegramAccess((123,)), Mock())
        self.assertFalse(bot.reply(123, 'partner private details', admin_only=True))
        self.assertTrue(bot.outbox.empty())

    async def test_queued_partner_details_do_not_send_after_admin_is_demoted(self):
        api = SimpleNamespace(call=AsyncMock())
        access = TelegramAccess((123,), roles={123: 'admin'})
        bot = TelegramBotService(api, access, Mock())
        self.assertTrue(bot.reply(123, 'partner private details', admin_only=True))
        access.replace((123,), roles={123: 'partner'})
        await self.drain(bot)
        api.call.assert_not_awaited()

    async def test_ordinary_own_reply_survives_demotion_while_admin_reply_requires_access(self):
        api = SimpleNamespace(call=AsyncMock())
        access = TelegramAccess((123,), roles={123: 'admin'})
        bot = TelegramBotService(api, access, Mock())
        self.assertTrue(bot.reply(123, 'own statistics'))
        access.replace((123,), roles={123: 'partner'})
        await self.drain(bot)
        api.call.assert_awaited_once_with('sendMessage', {'chat_id': 123, 'text': 'own statistics'})

    async def test_admin_reply_retry_rechecks_role_after_rate_limit(self):
        access = TelegramAccess((123,), roles={123: 'admin'})
        async def rate_limit(*args):
            access.replace((123,), roles={123: 'partner'})
            raise TelegramApiError(429, 'offline rate limit', retry_after=0.01)
        api = SimpleNamespace(call=AsyncMock(side_effect=rate_limit))
        bot = TelegramBotService(api, access, Mock())
        self.assertTrue(bot.reply(123, 'partner private details', admin_only=True))
        await self.drain(bot)
        self.assertEqual(api.call.await_count, 1)


class PartnerControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        directory = Path(self.temp.name)
        self.settings = QSettings(str(directory / 'settings.ini'), QSettings.IniFormat)
        self.store = TelegramUserStore(directory / 'users.sqlite3')
        self.store.upsert_user(123, 'Admin Secret', 'admin')
        self.store.upsert_user(234, 'Partner Private', 'partner')
        self.store.upsert_user(345, 'Other Admin', 'admin')
        self.store.set_user_mail_keys(234, 'fake-private-gmail-key', 'fake-private-trieu-key')
        self.store.record_job('historical-partner-job', 234, 'TEST', 1000, 10)
        self.store.finish_job('historical-partner-job', 'failed', 3, attempted_count=4, failure_count=1)
        self.mochi = MochiChatPage(settings=self.settings)
        self.mochi._schedule_timer.stop()
        self.page = TelegramPage(self.settings, self.mochi, user_store=self.store)
        self.worker = SimpleNamespace(fingerprint='fake-partner-bot',
            access=TelegramAccess(self.store.active_ids(), roles=self.store.roles()),
            reply=Mock(), complete_request=Mock(), request_stop=Mock(), isRunning=Mock(return_value=False))
        self.page.worker = self.worker
        self.page._accepting_requests = True

    def tearDown(self):
        self.page.worker = None
        self.page.shutdown_workers()
        self.mochi.shutdown_workers()
        self.page.deleteLater()
        self.mochi.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def request(self, action, target=None, *, actor=123, name='', role='', page=1, index=1):
        self.worker.reply.reset_mock()
        request = TelegramControlRequest(index, actor, action, target, name=name, role=role, page=page)
        self.page._on_control_requested((self.worker.fingerprint, request))
        return request

    def test_admin_adds_partner_and_access_changes_immediately(self):
        self.request('partner_add', 456, name='New Partner')
        user = self.store.get_user(456)
        self.assertIsNotNone(user)
        self.assertEqual((user.name, user.role, user.active), ('New Partner', 'partner', True))
        self.assertTrue(self.worker.access.allowed(456))
        self.assertEqual(self.worker.access.role(456), 'partner')
        self.assertIn(456, tuple(int(value) for value in self.settings.value('telegram/allowed_chat_ids').split()))

    def test_revoke_and_restore_preserve_history_and_private_keys(self):
        self.request('partner_remove', 234)
        self.assertFalse(self.store.get_user(234).active)
        self.assertFalse(self.worker.access.allowed(234))
        self.assertEqual(self.store.get_user(234).sold_beans, 300)
        self.request('partner_add', 234, name='Restored Partner', index=2)
        user = self.store.get_user(234)
        self.assertEqual((user.active, user.failed_jobs, user.sold_beans), (True, 1, 300))
        self.assertTrue(self.worker.access.allowed(234))
        keys = self.store.get_user_mail_keys(234, require_complete=True)
        self.assertIn('fake-private-gmail-key', keys.values())
        self.assertIsNotNone(self.store.get_job('historical-partner-job'))

    def test_partner_information_includes_durable_statistics_without_secrets(self):
        self.request('partner_info', 234)
        call = self.worker.reply.call_args
        self.assertEqual(call.args[0], 123)
        text = call.args[1]
        self.assertIn('Partner Private', text)
        self.assertIn('234', text)
        self.assertIn('Job thất bại: 1', text)
        self.assertIn('Bean đã bán: 300', text)
        self.assertNotIn('fake-private', text)
        self.assertTrue(call.kwargs.get('admin_only'))

    def test_partner_list_excludes_other_admin_local_and_customers(self):
        self.store.ensure_customer(567, 'Customer Hidden')
        self.store.upsert_user(678, 'Revoked Partner', active=False)
        self.request('partner_list')
        text = self.worker.reply.call_args.args[1]
        self.assertIn('Partner Private', text)
        self.assertIn('Revoked Partner', text)
        for name in ('Admin Secret', 'Other Admin', 'Customer Hidden', 'Local'):
            self.assertNotIn(name, text)
        self.assertTrue(self.worker.reply.call_args.kwargs.get('admin_only'))

    def test_partner_list_paginates_and_never_exceeds_telegram_message_limit(self):
        for offset in range(32):
            self.store.upsert_user(1000 + offset, f'Long Partner {offset:02d} ' + 'a' * 80)
        self.request('partner_list', page=1)
        first = self.worker.reply.call_args.args[1]
        self.request('partner_list', page=2, index=2)
        second = self.worker.reply.call_args.args[1]
        self.assertNotEqual(first, second)
        self.assertLessEqual(len(first), 4000)
        self.assertLessEqual(len(second), 4000)
        self.assertNotIn('Partner Private', first)

    def test_partner_cannot_forge_management_requests(self):
        for action, target in (('partner_add', 456), ('partner_remove', 234),
                ('partner_info', 234), ('partner_list', None)):
            with self.subTest(action=action):
                self.request(action, target, actor=234, name='New Partner')
                self.assertIsNone(self.store.get_user(456))
                self.assertTrue(self.store.get_user(234).active)
                for call in self.worker.reply.call_args_list:
                    self.assertNotIn('Partner Private', call.args[1])

    def test_cached_admin_cannot_manage_after_sql_role_is_demoted(self):
        self.store.upsert_user(123, 'Former Admin', 'partner')
        self.assertEqual(self.worker.access.role(123), 'admin')
        for action, target in (('partner_add', 456), ('partner_remove', 234),
                ('partner_info', 234), ('partner_list', None)):
            with self.subTest(action=action):
                self.request(action, target, name='New Partner')
                self.assertIsNone(self.store.get_user(456))
                self.assertTrue(self.store.get_user(234).active)
                for call in self.worker.reply.call_args_list:
                    self.assertNotIn('Partner Private', call.args[1])

    def test_revoked_admin_does_not_dispatch_or_receive_sensitive_reply(self):
        self.store.set_active(123, False)
        self.request('partner_info', 234, index=91)
        self.worker.reply.assert_not_called()
        self.worker.complete_request.assert_called_with(91)

    def test_ledger_rechecks_admin_after_page_actor_read_races_with_demotion(self):
        original = self.store.get_user
        first = True
        def get_user(chat_id):
            nonlocal first
            actor = original(chat_id)
            if chat_id == 123 and first:
                first = False
                with closing(sqlite3.connect(self.store.path)) as db, db:
                    db.execute("UPDATE users SET role='partner' WHERE chat_id=123")
            return actor
        with patch.object(self.store, 'get_user', side_effect=get_user):
            self.request('partner_add', 456, name='Race Target')
        self.assertIsNone(original(456))
        self.assertEqual(original(123).role, 'partner')

    def test_other_admin_and_local_are_not_partner_targets(self):
        for action in ('partner_add', 'partner_remove', 'partner_info'):
            for target in (123, 345, -1):
                with self.subTest(action=action, target=target):
                    self.request(action, target, name='Forged Rename')
                    self.assertEqual(self.store.get_user(123).name, 'Admin Secret')
                    self.assertEqual(self.store.get_user(345).name, 'Other Admin')
                    self.assertTrue(self.store.get_user(345).active)
                    self.assertTrue(self.store.get_user(-1).is_local)

    def test_database_error_is_safe_and_request_pending_can_complete(self):
        detail = 'injected SQL private path /sensitive/database.sqlite3'
        with patch.object(self.store, 'admin_get_partner', side_effect=sqlite3.OperationalError(detail)):
            self.request('partner_info', 234, index=81)
        self.assertTrue(self.worker.reply.called)
        self.assertNotIn(detail, self.worker.reply.call_args.args[1])
        self.assertEqual(self.worker.reply.call_args.args[2], 81)


if __name__ == '__main__':
    unittest.main()
