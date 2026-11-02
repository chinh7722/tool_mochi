"""Offline Telegram authorization, transport, replay and GUI job checks."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import asyncio
import hashlib
import importlib.util
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PySide6.QtCore import QSettings, QThread, Signal
from PySide6.QtWidgets import QApplication, QLineEdit
from core.telegram_bot import (TelegramAccess, TelegramApi, TelegramApiError,
    TelegramBotService, TelegramConfig, TelegramJobRequest, parse_chat_ids, parse_job_command)
from core.platforms.mochichat.runners.invite_referral import MochiInviteJobSummary
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread
from gui.pages.telegram import TelegramPage
from gui.telegram_bot import TelegramBotThread

TOKEN = '123456789:' + 'A' * 35
FINGERPRINT = hashlib.sha256(TOKEN.encode()).hexdigest()


def update(index=1, chat_id=123, text='/job ABC123 1000', **changes):
    message = {'chat': {'id': chat_id, 'type': 'private'},
        'from': {'id': chat_id, 'is_bot': False}, 'text': text}
    message.update(changes)
    return {'update_id': index, 'message': message}


class PolicyTests(unittest.TestCase):
    def test_chat_ids_validate_and_deduplicate(self):
        self.assertEqual(parse_chat_ids('456,123;456\n789'), (123, 456, 789))
        self.assertEqual(parse_chat_ids(''), ())
        for raw in ('0', '-123', '@name', '123abc', '9223372036854775808', 'True'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_chat_ids(raw)

    def test_job_command_and_customer_name(self):
        self.assertEqual(parse_job_command('/job abc123 700 Khách A'), ('ABC123', 700, 'Khách A'))
        self.assertEqual(parse_job_command('/job@ttm_bot ABC_1 1000'), ('ABC_1', 1000, ''))
        for text in ('/job', '/job CODE', '/job CODE 0', '/job CODE -100',
                '/job CODE 1000001', '/job CODE 1.5', '/job CODE;bad 100'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_job_command(text)

    def service(self, ids=(123,)):
        requests = []
        service = TelegramBotService(SimpleNamespace(call=AsyncMock()), TelegramAccess(ids), requests.append)
        service.username = 'ttm_bot'
        return service, requests

    def test_allowed_private_chat_dispatches_one_request(self):
        bot, requests = self.service()
        bot.handle_update(update(text='/job abc123 700 Khách A'))
        self.assertEqual(requests, [TelegramJobRequest(1, 123, 'ABC123', 700, 'Khách A')])

    def test_unauthorized_chat_cannot_create_job_by_typing_allowed_id(self):
        bot, requests = self.service()
        bot.handle_update(update(chat_id=456, text='/job 123 1000'))
        self.assertFalse(requests)
        chat, text, public = bot.outbox.get_nowait()
        self.assertEqual(chat, 456)
        self.assertIn('chưa được cấp quyền', text)
        self.assertTrue(public)

    def test_empty_allowlist_denies_jobs_but_id_returns_own_id(self):
        bot, requests = self.service(())
        bot.handle_update(update(text='/id'))
        self.assertEqual(bot.outbox.get_nowait()[:2], (123, 'Chat ID của bạn: 123'))
        bot.handle_update(update(2))
        self.assertFalse(requests)

    def test_groups_mismatched_sender_and_bots_are_ignored(self):
        bot, requests = self.service()
        invalid = [update(1, chat={'id': 123, 'type': 'group'}),
            update(2, **{'from': {'id': 456, 'is_bot': False}}),
            update(3, **{'from': {'id': 123, 'is_bot': True}}),
            update(4, chat={'id': True, 'type': 'private'}),
            {'update_id': 5, 'edited_message': update()['message']}]
        for item in invalid:
            bot.handle_update(item)
        self.assertFalse(requests)
        self.assertTrue(bot.outbox.empty())

    def test_replayed_and_edited_updates_do_not_create_duplicate_jobs(self):
        bot, requests = self.service()
        bot.handle_update(update(10))
        bot.handle_update(update(10))
        bot.handle_update(update(9))
        bot.handle_update({'update_id': 11, 'edited_message': update()['message']})
        self.assertEqual(len(requests), 1)
        self.assertEqual(bot.offset, 12)

    def test_bot_mentions_and_revocation(self):
        bot, requests = self.service()
        bot.handle_update(update(1, text='/job@other_bot CODE 1000'))
        self.assertFalse(requests)
        bot.handle_update(update(2, text='/job@TTM_BOT CODE 1000'))
        self.assertEqual(len(requests), 1)
        bot.access.replace(())
        bot.handle_update(update(3))
        self.assertEqual(len(requests), 1)

    def test_invalid_commands_and_data_never_dispatch(self):
        bot, requests = self.service()
        for item in (None, {}, {'update_id': True}, {'update_id': 1, 'message': None},
                update(2, text='/job CODE 0'), update(3, text='hello'), update(4, text='/job CODE 1000001')):
            bot.handle_update(item)
        self.assertFalse(requests)


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_error_cannot_be_treated_as_a_successful_update(self):
        session = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=500,
            json=lambda: {'ok': True, 'result': [update()]})))
        with self.assertRaises(TelegramApiError):
            await TelegramApi(TOKEN, session).call('getUpdates')
    async def test_network_exceptions_do_not_expose_token(self):
        session = SimpleNamespace(post=AsyncMock(side_effect=RuntimeError(f'https://api.telegram.org/bot{TOKEN}/getMe')))
        api = TelegramApi(TOKEN, session)
        with self.assertRaises(TelegramApiError) as raised:
            await api.call('getMe')
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    async def test_token_validation_and_fixed_api_methods(self):
        session = SimpleNamespace(post=AsyncMock())
        for token in ('', 'https://api.telegram.org/bot12345:token', '123456:bad/path'):
            with self.assertRaises(ValueError):
                TelegramApi(token, session)
        with self.assertRaises(ValueError):
            await TelegramApi(TOKEN, session).call('deleteWebhook')
        session.post.assert_not_awaited()

    async def test_api_error_redaction_and_retry_after(self):
        session = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=429, json=lambda: {
            'ok': False, 'error_code': 429, 'description': f'error {TOKEN}', 'parameters': {'retry_after': 7}})))
        with self.assertRaises(TelegramApiError) as raised:
            await TelegramApi(TOKEN, session).call('getUpdates')
        self.assertNotIn(TOKEN, str(raised.exception))
        self.assertEqual(raised.exception.retry_after, 7)

    async def test_success_uses_https_and_verified_tls(self):
        session = SimpleNamespace(post=AsyncMock(return_value=SimpleNamespace(status_code=200,
            json=lambda: {'ok': True, 'result': {'username': 'ttm_bot'}})))
        result = await TelegramApi(TOKEN, session).call('getMe')
        self.assertEqual(result['username'], 'ttm_bot')
        self.assertTrue(session.post.await_args.args[0].startswith('https://api.telegram.org/bot'))
        self.assertTrue(session.post.await_args.kwargs['verify'])


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_skips_old_commands_then_handles_each_new_update_once(self):
        arrived = asyncio.Event()
        requests = []
        polls = []

        def on_job(request):
            requests.append(request)
            arrived.set()

        async def call(method, payload=None):
            if method == 'getMe':
                return {'username': 'ttm_bot'}
            if method == 'getWebhookInfo':
                return {'url': ''}
            if method == 'getUpdates':
                polls.append(payload)
                if payload['offset'] == -1:
                    return [update(41)]
                if len(polls) == 2:
                    return [update(41), update(42), update(42)]
                await asyncio.Event().wait()

        api = SimpleNamespace(call=AsyncMock(side_effect=call))
        bot = TelegramBotService(api, TelegramAccess((123,)), on_job)
        task = asyncio.create_task(bot.run())
        try:
            await asyncio.wait_for(arrived.wait(), 1)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual([request.update_id for request in requests], [42])
        self.assertEqual(polls[1]['offset'], 42)
        self.assertEqual(bot.offset, 43)

    async def test_existing_webhook_is_reported_without_deleting_it(self):
        api = SimpleNamespace(call=AsyncMock(side_effect=[{'username': 'ttm_bot'}, {'url': 'https://existing.example.test'}]))
        bot = TelegramBotService(api, TelegramAccess((123,)), Mock())
        with self.assertRaises(TelegramApiError) as raised:
            await bot.run()
        self.assertEqual(raised.exception.code, 409)
        self.assertEqual([call.args[0] for call in api.call.await_args_list], ['getMe', 'getWebhookInfo'])

    async def test_revoked_chat_does_not_receive_queued_job_results(self):
        api = SimpleNamespace(call=AsyncMock())
        access = TelegramAccess((123,))
        bot = TelegramBotService(api, access, Mock())
        bot.reply(123, 'private job result')
        access.replace(())
        sender = asyncio.create_task(bot._send_replies())
        try:
            await asyncio.wait_for(bot.outbox.join(), 1)
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        api.call.assert_not_awaited()

    async def test_send_rate_limit_waits_before_retry_and_lost_reply_is_not_repeated(self):
        times = []

        async def call(method, payload):
            times.append(time.monotonic())
            if len(times) == 1:
                raise TelegramApiError(429, 'wait', 0.02)
            raise TelegramApiError(0, 'lost response')

        api = SimpleNamespace(call=AsyncMock(side_effect=call))
        bot = TelegramBotService(api, TelegramAccess((123,)), Mock())
        bot.reply(123, 'accepted')
        sender = asyncio.create_task(bot._send_replies())
        try:
            await asyncio.wait_for(bot.outbox.join(), 1)
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
        self.assertEqual(len(times), 2)
        self.assertGreaterEqual(times[1] - times[0], 0.018)


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(str(Path(self.temp.name) / 'telegram.ini'), QSettings.IniFormat)
        self.mochi = MochiChatPage(settings=self.settings)
        self.page = TelegramPage(self.settings, self.mochi)
        self.addCleanup(self.mochi.close)
        self.addCleanup(self.page.close)

    def fake_worker(self, ids=(123,)):
        for chat_id in ids:
            if self.page.store.get_user(chat_id) is None:
                self.page.store.upsert_user(chat_id, f'Telegram {chat_id}')
            self.page.store.set_user_mail_keys(chat_id,f'offline-gmailvip-{chat_id}',f'offline-1trieumail-{chat_id}')
        self.page._render_users()
        worker = SimpleNamespace(fingerprint=FINGERPRINT, access=TelegramAccess(ids),
            reply=Mock(), complete_request=Mock(), request_stop=Mock())
        self.page.worker = worker
        self.page._accepting_requests = True
        return worker

    def select_chat_row(self, chat_id):
        row = next(row for row in range(self.page.table_users.rowCount())
            if self.page.table_users.item(row, 0).text() == str(chat_id))
        self.page.table_users.selectRow(row)

    def test_chat_ids_save_reload_and_token_is_masked(self):
        self.page.input_token.setText(TOKEN)
        self.page.input_chat_id.setText('456,123,123')
        self.page._add_id()
        self.assertEqual(self.page._ids(), (123, 456))
        self.assertEqual(TelegramConfig.from_settings(self.settings).chat_ids, (123, 456))
        self.assertEqual(self.page.input_token.echoMode(), QLineEdit.EchoMode.Password)
        self.select_chat_row(123)
        self.page._remove_ids()
        self.assertEqual(TelegramConfig.from_settings(self.settings).chat_ids, (456,))

    def test_remote_job_uses_current_settings_without_overwriting_form_and_notifies_owner(self):
        worker = self.fake_worker()
        self.mochi.input_api_key.setText('offline-shop-key')
        self.mochi.input_code.setText('LOCAL')
        self.mochi.input_target_points.setText('100')
        request = TelegramJobRequest(1, 123, 'REMOTE', 700, 'Khách A')
        with patch.object(MochiWorkerThread, 'start'):
            self.page._on_job_requested((FINGERPRINT, request))
        self.assertEqual(len(self.mochi._workers), 1)
        job = next(iter(self.mochi._workers.values()))
        self.assertEqual((job.invite_code, job.target_count, job.customer_name), ('REMOTE', 7, 'Khách A'))
        creator = self.mochi.table_jobs.item(0, 1).text()
        self.assertIn(self.page.store.get_user(123).name, creator)
        self.assertIn('(123)', creator)
        self.assertNotEqual(creator, job.customer_name)
        self.assertEqual(self.mochi.input_code.text(), 'LOCAL')
        self.assertEqual(self.mochi.input_target_points.text(), '100')
        self.assertEqual(worker.reply.call_args.args[0], 123)
        self.assertIn(job.job_id, worker.reply.call_args.args[1])
        summary = MochiInviteJobSummary(job.job_id, 'REMOTE', 7, 7, 7, 0, 0, 'completed', ())
        self.mochi._on_job_finished(summary)
        self.assertEqual(worker.reply.call_count, 2)
        self.assertIn('7/7', worker.reply.call_args.args[1])
        self.assertNotIn('offline-shop-key', worker.reply.call_args.args[1])

    def test_manual_start_button_still_creates_a_local_job(self):
        self.mochi.input_api_key.setText('offline-key')
        self.mochi.input_code.setText('LOCAL')
        self.mochi.input_target_points.setText('700')
        with patch.object(MochiWorkerThread, 'start'):
            self.mochi.btn_start.click()
        job = next(iter(self.mochi._workers.values()))
        self.assertEqual((job.invite_code, job.target_count), ('LOCAL', 7))
        self.assertEqual(self.mochi.table_jobs.item(0, 1).text(), 'Local')
        self.assertFalse(self.page._job_chats)

    def test_revoked_or_stopped_requests_never_create_jobs(self):
        worker = self.fake_worker(ids=())
        request = TelegramJobRequest(1, 123, 'REMOTE', 1000)
        with patch.object(self.mochi, '_on_start_clicked') as start:
            self.page._on_job_requested((FINGERPRINT, request))
            worker.access.replace((123,))
            self.page._accepting_requests = False
            self.page._on_job_requested((FINGERPRINT, request))
            self.page._accepting_requests = True
            self.page._on_job_requested(('old-bot-fingerprint', request))
        start.assert_not_called()
        self.assertEqual(worker.complete_request.call_count, 2)

    def test_configuration_failure_creates_no_job_and_sends_no_credentials(self):
        worker = self.fake_worker()
        self.mochi.chk_use_proxy.setChecked(True)
        self.page._on_job_requested((FINGERPRINT, TelegramJobRequest(1, 123, 'REMOTE', 1000)))
        self.assertFalse(self.mochi._workers)
        self.assertFalse(self.mochi._session_jobs)
        self.assertIn('Không tạo được job', worker.reply.call_args.args[1])

    def test_local_jobs_and_results_from_another_bot_are_not_sent(self):
        worker = self.fake_worker()
        summary = MochiInviteJobSummary('local', 'LOCAL', 1, 1, 1, 0, 0, 'completed', ())
        self.mochi._on_job_finished(summary)
        self.page._job_chats['local'] = ('old-bot', 123)
        self.mochi._on_job_finished(summary)
        worker.reply.assert_not_called()

    def test_permission_changes_apply_to_running_bot(self):
        worker = self.fake_worker()
        self.page.input_token.setText(TOKEN)
        self.page.input_chat_id.setText('123')
        self.page._add_id()
        self.assertTrue(worker.access.allowed(123))
        self.select_chat_row(123)
        self.page._remove_ids()
        self.assertFalse(worker.access.allowed(123))

    def spin_until(self, condition, timeout=2):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(condition())

    def test_qt_delivers_bot_request_on_gui_thread_and_stop_drains_worker(self):
        finish = threading.Event()
        replies, gui_threads = [], []

        class FakeBot(QThread):
            job_requested = Signal(object)
            control_requested = Signal(object)
            customer_requested = Signal(object)
            status_changed = Signal(str)

            def __init__(self, config, parent):
                super().__init__(parent)
                self.fingerprint = FINGERPRINT
                self.access = TelegramAccess(config.chat_ids)

            def run(self):
                self.job_requested.emit((FINGERPRINT, TelegramJobRequest(1, 123, 'REMOTE', 1000)))
                finish.wait(2)

            def reply(self, *args):
                gui_threads.append(threading.get_ident())
                replies.append(args)

            def request_stop(self):
                finish.set()

        self.page.input_token.setText(TOKEN)
        self.page.input_chat_id.setText('123')
        self.page._add_id()
        self.page.store.set_user_mail_keys(123,'offline-gmailvip-123','offline-1trieumail-123')
        self.mochi.input_api_key.setText('offline-key')
        try:
            with patch('gui.pages.telegram.TelegramBotThread', FakeBot), patch.object(MochiWorkerThread, 'start'):
                self.page._start_bot()
                self.spin_until(lambda: len(self.mochi._workers) == 1)
                self.assertEqual(gui_threads, [threading.get_ident()])
                self.page._stop_bot()
                self.spin_until(lambda: self.page.worker is None)
        finally:
            finish.set()
            if self.page.worker is not None:
                self.page.worker.wait(2500)
                self.app.processEvents()
        self.assertEqual(len(replies), 1)

    def test_real_bot_thread_cancels_pending_long_poll_without_network(self):
        entered = threading.Event()

        async def call(method, payload=None):
            if method == 'getMe':
                return {'username': 'ttm_bot'}
            if method == 'getWebhookInfo':
                return {'url': ''}
            if method == 'getUpdates' and payload['offset'] == -1:
                return []
            entered.set()
            await asyncio.Event().wait()

        session = AsyncMock()
        session.__aenter__.return_value = session
        api = SimpleNamespace(call=AsyncMock(side_effect=call))
        self.page.input_token.setText(TOKEN)
        try:
            with patch('gui.telegram_bot.AsyncSession', return_value=session), \
                 patch('gui.telegram_bot.TelegramApi', return_value=api):
                self.page._start_bot()
                self.spin_until(entered.is_set)
                self.page._stop_bot()
                self.spin_until(lambda: self.page.worker is None)
        finally:
            if self.page.worker is not None:
                self.page.worker.request_stop()
                self.page.worker.wait(2500)
                self.app.processEvents()
        session.__aexit__.assert_awaited_once()

    def test_main_window_has_telegram_navigation_and_does_not_start_bot_by_default(self):
        spec = importlib.util.spec_from_file_location('telegram_main_smoke', ROOT / 'main_mochi_gui.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with patch.object(module, 'QSettings', return_value=self.settings), \
             patch.object(TelegramBotThread, 'start') as start:
            window = module.MochiMainWindow()
            window.btn_tab_telegram.click()
            self.assertEqual(window.stacked.currentIndex(), 2)
            self.assertIs(window.stacked.currentWidget(), window.telegram_page)
            self.app.processEvents()
            start.assert_not_called()
            window.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
