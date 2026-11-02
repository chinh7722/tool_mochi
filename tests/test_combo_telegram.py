"""Offline combo command routes and nonterminal Telegram notifications."""
import os
from pathlib import Path
import sys
from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tests'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PySide6.QtWidgets import QApplication
from core.job_schedule import VIETNAM_TIME
from core.telegram_bot import (TelegramAccess, TelegramBotService, TelegramCustomerRequest,
    TelegramJobRequest)
from core.telegram_users import TelegramUserStore
import test_job_queue as queue_fixtures
from test_telegram_bot import update


class ComboCommandTests(unittest.TestCase):
    def test_combo_routes_operator_and_customer_requests_with_full_target(self):
        operators, customers = [], []
        bot = TelegramBotService(None, TelegramAccess((123,)), operators.append,
            on_customer=customers.append)
        bot.handle_update(update(1, 123, '/combo test 5500 Khách A'))
        bot.handle_update(update(2, 456, '/combo test 5500 Khách B'))
        self.assertEqual(operators, [TelegramJobRequest(1, 123, 'TEST', 5500, 'Khách A', combo=True)])
        self.assertEqual(customers, [TelegramCustomerRequest(2, 456, 'job', '', 'TEST', 5500,
            'Khách B', combo=True)])
        self.assertIsNone(operators[0].scheduled_at)
        self.assertFalse(bot.access.allowed(456))

    def test_normal_job_defaults_remain_single_round(self):
        requests = []
        bot = TelegramBotService(None, TelegramAccess((123,)), requests.append)
        bot.handle_update(update(1, 123, '/job TEST 5500'))
        self.assertFalse(requests[0].combo)
        self.assertFalse(TelegramCustomerRequest(1, 456, 'job').combo)

    def test_combo_replay_sender_and_bot_mention_checks_apply(self):
        requests = []
        bot = TelegramBotService(None, TelegramAccess((123,)), requests.append)
        bot.username = 'ours'
        command = update(1, 123, '/combo TEST 5500')
        bot.handle_update(command)
        bot.handle_update(command)
        bot.handle_update(update(2, 123, '/combo TEST 5500', chat={'id': 123, 'type': 'group'}))
        bot.handle_update(update(3, 123, '/combo TEST 5500', **{'from': {'id': 456, 'is_bot': False}}))
        bot.handle_update(update(4, 123, '/combo@other TEST 5500'))
        self.assertEqual(len(requests), 1)

    def test_bad_combo_never_dispatches_and_usage_names_combo(self):
        requests = []
        bot = TelegramBotService(None, TelegramAccess((123,)), requests.append)
        for index, text in enumerate(('/combo', '/combo BAD;CODE 5500', '/combo TEST 0',
                '/combo TEST 1000001', '/combo TEST 5.5'), 1):
            bot.handle_update(update(index, 123, text))
        self.assertFalse(requests)
        self.assertIn('/combo', bot.outbox.get_nowait()[1])
        self.assertIn('/combo', bot.HELP)


class ComboNotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    setUp = queue_fixtures.QueueTests.setUp

    def prepare(self, customer=False):
        when = datetime.now(VIETNAM_TIME) + timedelta(hours=24, minutes=1)
        metadata = dict(combo=True, customer_name='Khách A', creator_name='Admin', total_vnd=0,
            source_mail_batch=None)
        if customer:
            self.store.ensure_customer(456, 'Khách A')
            self.store.set_customer_balance(456, 10_000_000, 'offline funding', expected_units=0)
            self.store.record_customer_order('combo_test', 456, 'TEST', 5500, 55,
                self.bot.fingerprint, 1, 100000, scheduled_at=when, schedule_data=metadata)
            owner = 456
        else:
            self.store.record_job('combo_test', 123, 'TEST', 5500, 55,
                self.bot.fingerprint, 1, scheduled_at=when, schedule_data=metadata)
            owner = 123
        self.store.reschedule_combo_job('combo_test', when, 10, 12, 2)
        self.page._job_chats['combo_test'] = (self.bot.fingerprint, owner)
        self.bot.reply.reset_mock()
        return when, owner

    def test_waiting_operator_notification_keeps_origin_and_job_unfinalized(self):
        when, owner = self.prepare()
        self.page._on_combo_waiting('combo_test', when.isoformat())
        job = self.store.get_job('combo_test')
        self.assertEqual((job.status, job.success_count, job.failure_count), ('running', 10, 2))
        self.assertIsNone(job.finalized_at)
        self.assertIn('combo_test', self.page._job_chats)
        self.assertEqual(self.bot.reply.call_args.args[0], owner)
        text = self.bot.reply.call_args.args[1]
        self.assertIn('1,000/5,500 bean', text)
        self.assertIn('4,500 bean', text)
        self.assertIn(when.strftime('%d/%m/%Y %H:%M'), text)
        self.assertFalse(self.bot.reply.call_args.kwargs.get('public', False))

    def test_customer_wait_keeps_original_hold_and_sends_public_reply(self):
        when, owner = self.prepare(customer=True)
        balance = self.store.get_customer(owner)['balance_units']
        with patch.object(self.store, 'finish_job', side_effect=AssertionError('early settlement')):
            self.page._on_combo_waiting('combo_test', when.isoformat())
        self.assertEqual(self.store.get_customer(owner)['balance_units'], balance)
        self.assertIsNone(self.store.get_customer_order_for_job('combo_test')['charged_units'])
        self.assertTrue(self.bot.reply.call_args.kwargs['public'])
        self.assertIn('combo_test', self.page._job_chats)

    def test_waiting_notification_requires_original_bot_and_current_permissions(self):
        when, _ = self.prepare()
        self.bot.access.replace(())
        self.page._on_combo_waiting('combo_test', when.isoformat())
        self.bot.reply.assert_not_called()
        self.bot.access.replace((123,))
        self.bot.fingerprint = 'another-bot'
        self.page._on_combo_waiting('combo_test', when.isoformat())
        self.bot.reply.assert_not_called()
        self.assertIn('combo_test', self.page._job_chats)

    def test_operator_and_customer_creation_pass_combo_without_dropping_target(self):
        for request, handler in (
            (TelegramJobRequest(10, 123, 'TEST', 5500, combo=True), self.page._on_job_requested),
            (TelegramCustomerRequest(20, 456, 'job', 'Khách', 'TEST', 5500, combo=True),
                self.page._on_customer_requested)):
            if request.chat_id == 456:
                self.store.ensure_customer(456, 'Khách')
                self.store.set_customer_balance(456, 10_000_000, 'offline funding', expected_units=0)
            with self.subTest(owner=request.chat_id), patch.object(self.mochi, '_on_start_clicked',
                    return_value=None) as create:
                handler((self.bot.fingerprint, request))
                self.assertEqual(create.call_args.kwargs['points'], 5500)
                self.assertTrue(create.call_args.kwargs['combo'])

    def test_startup_recovers_before_restoring_saved_combo(self):
        self.prepare()
        self.store.update_dispatch('combo_test', 'running')
        from gui.pages.mochi_chat import MochiChatPage
        from gui.pages.telegram import TelegramPage
        reopened = MochiChatPage(settings=self.settings)
        self.addCleanup(reopened.close)
        page = TelegramPage(self.settings, reopened, user_store=TelegramUserStore(self.store.path))
        self.addCleanup(page.close)
        self.assertIn('combo_test', reopened._scheduled_jobs)
        self.assertEqual(page.store.get_job('combo_test').dispatch_state, 'scheduled')
        self.assertIn('combo_test', page._job_chats)


if __name__ == '__main__':
    unittest.main()
