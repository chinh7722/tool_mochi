"""Real Chrome and Qt smoke tests using only intercepted/offline requests.

Run: py -3.14 -I tests/test_browser_gui.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import asyncio
import json
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication

from core.auth import google
from core.browser.manager import CentralBrowserManager
from core.platforms.mochichat.runners.invite_referral import MochiAccountResult, MochiInviteJobSummary
from gui.background import start_background
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread
from gui.pages.proxy import ProxyPage
from core.proxy.broker import ProxyBroker, proxy_maintenance
from core.proxy.models import ProxyModel
from core.proxy.protocol import ProxyTransportError
from core.proxy.rotators import RotationResult
from core.proxy.health import ProxyHealthResult


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_request_error_is_returned_for_proxy_rotation(self):
        context = await self.manager.acquire_context('request-error', headless=True)
        await context.route('**/*', lambda route: route.abort('connectionrefused'))
        with patch.object(google.browser_manager, 'acquire_context', AsyncMock(return_value=context)) as acquire, \
             patch.object(google.browser_manager, 'release_context', self.manager.release_context):
            with self.assertRaises(ProxyTransportError) as raised:
                await google._extract_id_token_via_browser('request-error', 'test@example.com', 'pw', 'client',
                    proxy='http://127.0.0.1:8500', timeout=5, max_retries=2)
        self.assertEqual(raised.exception.proxy, 'http://127.0.0.1:8500')
        self.assertEqual(acquire.await_count, 1)

    async def asyncSetUp(self):
        self.manager = CentralBrowserManager()
        self.addAsyncCleanup(self.manager.shutdown_for_loop)

    async def test_contexts_reuse_connected_browser_and_remain_isolated(self):
        one = await self.manager.acquire_context('first', headless=True)
        two = await self.manager.acquire_context('second', headless=True)
        self.assertIs(one.browser, two.browser)
        await one.add_cookies([dict(name='isolated', value='yes', url='https://example.test')])
        self.assertFalse(await two.cookies('https://example.test'))
        await self.manager.release_by_owner('first')
        page = await two.new_page()
        await page.set_content('<p>second context still works</p>')
        self.assertIn('still works', await page.inner_text('body'))

    async def test_complete_oauth_flow_chooses_exact_account_without_callback_download(self):
        context = await self.manager.acquire_context('flow', headless=True)
        wrong_clicked = []
        email = 'correct@example.com'
        html = '''<html><body>
          <input name="identifier"><button id="identifierNext">Next</button>
          <script>
            document.querySelector('#identifierNext').onclick = () => {
              document.body.innerHTML = '<input name="Passwd" type="password"><button id="passwordNext">Next</button>';
              document.querySelector('#passwordNext').onclick = () => {
                document.body.innerHTML = '<h1>Choose an account</h1><button data-email="wrong@example.com" id="wrong">wrong@example.com</button><button data-email="correct@example.com" id="correct">correct@example.com</button>';
                document.querySelector('#wrong').onclick = () => fetch('/wrong-account-clicked');
                document.querySelector('#correct').onclick = () => {
                  document.body.innerHTML = '<button id="consent">Continue</button>';
                  document.querySelector('#consent').onclick = () => location.href = 'https://mochi.lightchaser.xyz/#id_token=eyJoffline-test';
                };
              };
            };
          </script></body></html>'''

        async def serve(route):
            if 'wrong-account-clicked' in route.request.url:
                wrong_clicked.append(True)
            await route.fulfill(status=200, content_type='text/html', body=html)

        await context.route('**/*', serve)
        with patch.object(google.browser_manager, 'acquire_context', AsyncMock(return_value=context)), \
             patch.object(google.browser_manager, 'release_context', self.manager.release_context):
            started = time.monotonic()
            token = await google._extract_id_token_via_browser('flow', email, 'offline-password', 'offline-client',
                headless=True, timeout=8.0, max_retries=1)
        self.assertEqual(token, 'eyJoffline-test')
        self.assertFalse(wrong_clicked)
        self.assertLess(time.monotonic() - started, 8.0)

    async def test_slow_document_controls_are_waited_for(self):
        context = await self.manager.acquire_context('delayed', headless=True)
        html = '''<html><body><script>setTimeout(() => {
          document.body.innerHTML = '<input name="identifier"><button id="identifierNext">Next</button>';
          document.querySelector('#identifierNext').onclick = () => location.href='https://mochi.lightchaser.xyz/#id_token=eyJdelayed';
        }, 1200);</script></body></html>'''
        await context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
        with patch.object(google.browser_manager, 'acquire_context', AsyncMock(return_value=context)), \
             patch.object(google.browser_manager, 'release_context', self.manager.release_context):
            token = await google._extract_id_token_via_browser('delayed', 'test@example.com', 'pw', 'client',
                timeout=5.0, max_retries=1)
        self.assertEqual(token, 'eyJdelayed')

    async def test_wrong_password_stops_without_retry(self):
        context = await self.manager.acquire_context('bad-password', headless=True)
        await context.route('**/*', lambda route: route.fulfill(content_type='text/html',
            body='<body><input name="Passwd" aria-invalid="true"><div role="alert">Wrong password</div></body>'))
        with patch.object(google.browser_manager, 'acquire_context', AsyncMock(return_value=context)) as acquire, \
             patch.object(google.browser_manager, 'release_context', self.manager.release_context):
            with self.assertRaises(google.GoogleLoginError):
                await google._extract_id_token_via_browser('bad-password', 'test@example.com', 'pw', 'client',
                    timeout=3.0, max_retries=2)
        self.assertEqual(acquire.await_count, 1)

    async def test_manual_rotation_cannot_interrupt_active_job(self):
        proxy = ProxyModel.from_string('127.0.0.1:8301', rotation_url='gui-busy-key')
        broker = ProxyBroker([proxy])
        state = broker._proxies[proxy.raw]
        state.provider.get_current_or_rotate = AsyncMock(return_value=RotationResult(True, proxy))
        lease = await broker.lease('active-job')
        with self.assertRaises(ProxyTransportError):
            with proxy_maintenance('gui-busy-key'):
                self.fail('must not rotate')
        await broker.release(lease)
        with proxy_maintenance('gui-busy-key'):
            with self.assertRaises(TimeoutError):
                await broker.lease('wait-for-gui', timeout=0.02)


class GuiTests(unittest.TestCase):
    def proxy_capacity_page(self, count=5):
        entries = [(f'127.0.0.1:{31200 + i}', '') for i in range(count)]
        source = SimpleNamespace(get_active_proxies=lambda: entries)
        self.settings.setValue('runtime/mochi_chat/account_concurrency', 1)
        page = MochiChatPage(settings=self.settings, proxy_page=source)
        from core.mail.proxy_pool import MailProxyPool
        mail_entries=[('127.0.0.1:31999','offline-mail-'+self.id())]
        page.mail_proxy_page=SimpleNamespace(get_active_proxies=lambda:mail_entries,
            create_pool=lambda:MailProxyPool(mail_entries))
        page.input_code.setText('TEST')
        page.input_target_points.setText('1000')
        page.input_api_key.setText('offline-key')
        page.radio_proxy_tab.setChecked(True)
        page.chk_use_proxy.setChecked(True)
        page.spin_proxy_account_concurrency.setValue(1)
        return page, entries

    def test_five_proxies_start_five_workers_despite_old_manual_limit_one(self):
        page, _ = self.proxy_capacity_page()
        self.assertEqual(page.spin_concurrency.value(), 5)
        self.assertFalse(page.spin_concurrency.isEnabled())
        self.assertIn('5 proxy × 1', page.lbl_concurrency_summary.text())
        with patch.object(MochiWorkerThread, 'start'):
            page._on_start_clicked()
        runner = next(iter(page._workers.values())).runner
        self.assertEqual(runner.concurrency, 5)
        self.assertEqual(runner.current_concurrency(), 5)
        self.assertEqual(int(self.settings.value('runtime/mochi_chat/account_concurrency')), 1)
        page.close()

    def test_proxy_slots_and_list_updates_raise_auto_capacity_above_fifty(self):
        page, entries = self.proxy_capacity_page()
        page.spin_proxy_account_concurrency.setValue(2)
        self.assertEqual(page.spin_concurrency.value(), 10)
        entries.append(('127.0.0.1:31205', ''))
        page._update_proxy_tab_status()
        page.spin_proxy_account_concurrency.setValue(10)
        self.assertEqual(page.spin_concurrency.value(), 60)
        self.assertIn('6 proxy × 10', page.lbl_concurrency_summary.text())
        with patch.object(MochiWorkerThread, 'start'):
            page._on_start_clicked()
        self.assertEqual(next(iter(page._workers.values())).runner.current_concurrency(), 60)
        page.close()

    def test_direct_account_routes_restore_manual_limit_even_for_proxied_mail(self):
        page, _ = self.proxy_capacity_page()
        page.chk_use_proxy.setChecked(False)
        page.spin_concurrency.setValue(3)
        page.chk_use_proxy.setChecked(True)
        self.assertEqual(page.spin_concurrency.value(), 5)
        for box in (page.chk_google_via_proxy, page.chk_app_login_via_proxy, page.chk_redeem_via_proxy):
            box.setChecked(False)
        self.assertTrue(page.chk_mail_via_proxy.isChecked())
        self.assertTrue(page.spin_concurrency.isEnabled())
        self.assertEqual(page.spin_concurrency.value(), 3)
        with patch.object(MochiWorkerThread, 'start'):
            page._on_start_clicked()
        self.assertEqual(next(iter(page._workers.values())).runner.current_concurrency(), 3)
        page.close()

    def test_duplicate_endpoints_do_not_inflate_display_or_runner_capacity(self):
        page, entries = self.proxy_capacity_page(count=1)
        entries.append(('http://127.0.0.1:31200', ''))
        page._update_proxy_tab_status()
        self.assertEqual(page.spin_concurrency.value(), 1)
        with patch.object(MochiWorkerThread, 'start'):
            page._on_start_clicked()
        self.assertEqual(next(iter(page._workers.values())).runner.current_concurrency(), 1)
        page.close()

    def test_single_rotation_key_uses_slots_as_total_capacity(self):
        page, _ = self.proxy_capacity_page()
        page.radio_proxy_key.setChecked(True)
        page.input_proxy_vn_key.setText('offline-capacity-key')
        page.spin_proxy_account_concurrency.setValue(4)
        self.assertEqual(page.spin_concurrency.value(), 4)
        with patch.object(MochiWorkerThread, 'start'):
            page._on_start_clicked()
        self.assertEqual(next(iter(page._workers.values())).runner.current_concurrency(), 4)
        page.close()
    def test_check_proxy_queries_ip_instead_of_trusting_cached_endpoint(self):
        page = ProxyPage(settings=self.settings)
        item = page._parse_proxy_line('127.0.0.1:8910|gui-real-probe-key')
        page._proxies[item['id']] = item
        for success in (False, True):
            probe = AsyncMock(return_value=ProxyHealthResult(success, 'http://127.0.0.1:8910',
                'https', error='' if success else 'TLS timeout', http_status=200 if success else None))
            with patch('gui.pages.proxy.probe_proxy_connection', probe), \
                 patch('gui.pages.proxy.ProxyVnProvider.get_current_or_rotate', AsyncMock()) as provider:
                page._check_single_proxy(item['id'])
                self.spin_until(lambda: not page._background_workers)
            self.assertEqual(item['is_failed'], not success)
            probe.assert_awaited_once()
            provider.assert_not_awaited()
        page.shutdown_workers()
        page.close()

    def test_cooldown_expiry_does_not_automatically_rotate_working_proxy(self):
        page = ProxyPage(settings=self.settings)
        item = page._parse_proxy_line('127.0.0.1:8508|gui-reuse-key')
        item.update(status='COOLING', cooling_until=time.time() - 1)
        page._proxies[item['id']] = item
        with patch.object(page, '_rotate_single_proxy') as rotate:
            page._refresh_runtime_status()
        rotate.assert_not_called()
        self.assertEqual(item['status'], 'READY')
        page.shutdown_workers()
        page.close()

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = QSettings(str(Path(self.temp.name) / 'settings.ini'), QSettings.Format.IniFormat)
        self.addCleanup(self.temp.cleanup)

    def spin_until(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(condition())

    def test_invalid_proxy_config_does_not_create_phantom_job(self):
        page = MochiChatPage(settings=self.settings)
        page.input_code.setText('TEST')
        page.input_target_points.setText('100')
        page.input_api_key.setText('offline-key')
        page.chk_use_proxy.setChecked(True)
        page.radio_proxy_tab.setChecked(True)
        page._on_start_clicked()
        self.assertFalse(page._session_jobs)
        self.assertFalse(page._workers)
        page.close()

    def test_background_result_delivered_on_gui_thread_while_timer_runs(self):
        page = ProxyPage(settings=self.settings)
        ticks, results, threads = [], [], []
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start()

        async def slow():
            await asyncio.sleep(0.15)
            return 42

        start_background(page, slow,
            lambda value: (results.append(value), threads.append(threading.get_ident())), self.fail)
        self.spin_until(lambda: not page._background_workers)
        timer.stop()
        self.assertEqual(results, [42])
        self.assertEqual(threads, [threading.get_ident()])
        self.assertGreater(len(ticks), 3)
        page.close()

    def test_proxy_import_preserves_protocol_and_rejects_bad_lines(self):
        page = ProxyPage(settings=self.settings)
        parsed = page._parse_proxy_line('socks5://127.0.0.1:1080|rotation-key')
        self.assertEqual(parsed['endpoint'], 'socks5://127.0.0.1:1080')
        self.assertIsNone(page._parse_proxy_line('127.0.0.1:abc|key'))
        self.assertIsNone(page._parse_proxy_line('key-only'))
        page.close()

    def test_saved_mail_provider_and_keys_are_kept_separate(self):
        self.settings.setValue('runtime/mochi_chat/mail_provider', '1trieumail_com')
        self.settings.setValue('runtime/mochi_chat/mail_provider_api_key', 'one-trieu-key')
        page = MochiChatPage(settings=self.settings)
        self.assertEqual(page.combo_provider.currentData(), '1trieumail_com')
        self.assertEqual(page.input_api_key.text(), 'one-trieu-key')
        page.combo_provider.setCurrentIndex(0)
        self.assertEqual(page.input_api_key.text(), '')
        page.input_api_key.setText('gmail-vip-key')
        page.combo_provider.setCurrentIndex(1)
        self.assertEqual(page.input_api_key.text(), 'one-trieu-key')
        page.combo_provider.setCurrentIndex(0)
        self.assertEqual(page.input_api_key.text(), 'gmail-vip-key')
        page.close()

    def test_four_proxy_options_persist_and_follow_master_switch(self):
        page = MochiChatPage(settings=self.settings)
        boxes = (page.chk_mail_via_proxy, page.chk_google_via_proxy,
                 page.chk_app_login_via_proxy, page.chk_redeem_via_proxy)
        page.chk_use_proxy.setChecked(True)
        for box, checked in zip(boxes, (True, False, False, True)):
            self.assertTrue(box.isEnabled())
            box.setChecked(checked)
        page.chk_use_proxy.setChecked(False)
        self.assertTrue(all(not box.isEnabled() for box in boxes))
        page.close()
        restored = MochiChatPage(settings=self.settings)
        self.assertEqual((restored.chk_mail_via_proxy.isChecked(),
            restored.chk_google_via_proxy.isChecked(), restored.chk_app_login_via_proxy.isChecked(),
            restored.chk_redeem_via_proxy.isChecked()), (True, False, False, True))
        restored.close()

    def test_proxy_options_reach_runner_and_target_is_ten_accounts(self):
        page = MochiChatPage(settings=self.settings)
        page.input_code.setText('TEST')
        page.input_target_points.setText('1000')
        page.input_api_key.setText('offline-key')
        page.chk_use_proxy.setChecked(False)
        page.chk_mail_via_proxy.setChecked(True)
        page.chk_google_via_proxy.setChecked(False)
        page.chk_app_login_via_proxy.setChecked(False)
        page.chk_redeem_via_proxy.setChecked(True)
        with patch('gui.pages.mochi_chat.MochiInviteRunner') as runner, \
             patch.object(MochiWorkerThread, 'start'):
            page._on_start_clicked()
        self.assertEqual({key: runner.call_args.kwargs[key] for key in
            ('mail_via_proxy', 'google_login_via_proxy', 'app_login_via_proxy', 'redeem_via_proxy')},
            dict(mail_via_proxy=False, google_login_via_proxy=False,
                 app_login_via_proxy=False, redeem_via_proxy=False))
        self.assertEqual(next(iter(page._session_jobs.values())).target_count, 10)
        page.close()

    def test_saved_mail_batch_runs_ten_accounts_without_api_key_or_purchase(self):
        path = Path(self.temp.name) / 'purchased-mail.json'
        path.write_text(json.dumps({'accounts': [
            {'email': f'paid{i}@example.test', 'password': 'offline-password', 'status': 'pending'}
            for i in range(10)]}), encoding='utf-8')
        page = MochiChatPage(settings=self.settings)
        page.input_code.setText('TEST')
        page.input_target_points.setText('1000')
        page.input_api_key.clear()
        page.chk_use_mail_batch.setChecked(True)
        page.input_mail_batch.setText(str(path))
        # A proxy configured only for buying mail must not be required here.
        page.chk_use_proxy.setChecked(True)
        page.chk_mail_via_proxy.setChecked(True)
        page.chk_google_via_proxy.setChecked(False)
        page.chk_app_login_via_proxy.setChecked(False)
        page.chk_redeem_via_proxy.setChecked(False)
        with patch.object(MochiWorkerThread, 'start'):
            page._on_start_clicked()
        self.assertEqual(len(page._workers), 1)
        worker = next(iter(page._workers.values()))
        self.assertEqual(worker.target_count, 10)
        self.assertEqual(len(worker.accounts), 10)
        self.assertEqual(worker.source_mail_batch, str(path.resolve()))
        self.assertFalse(page.input_api_key.isEnabled())

        async def process(job_id, code, item, *args, **kwargs):
            return MochiAccountResult(item.identifier, True, 'success')

        module = 'core.platforms.mochichat.runners.invite_referral'
        with patch.object(worker.runner, '_process_account', AsyncMock(side_effect=process)) as processed, \
             patch.object(worker.runner, 'run', wraps=worker.runner.run) as run, \
             patch(f'{module}.GmailVipVn') as gmail, patch(f'{module}.OneTrieuMail') as one_trieu:
            worker.run()
        self.assertEqual(run.await_args.kwargs['accounts'], worker.accounts)
        self.assertEqual(run.await_args.kwargs['source_mail_batch'], str(path.resolve()))
        self.assertEqual(processed.await_count, 10)
        gmail.assert_not_called()
        one_trieu.assert_not_called()
        self.assertEqual({row['status'] for row in json.loads(path.read_text())['accounts']}, {'success'})
        page.close()

    def test_invalid_saved_batch_does_not_create_job_or_worker(self):
        path = Path(self.temp.name) / 'invalid-batch.json'
        path.write_text('{invalid JSON', encoding='utf-8')
        page = MochiChatPage(settings=self.settings)
        page.input_code.setText('TEST')
        page.input_api_key.clear()
        page.chk_use_mail_batch.setChecked(True)
        page.input_mail_batch.setText(str(path))
        with patch.object(MochiWorkerThread, 'start') as start:
            page._on_start_clicked()
        self.assertIn('Không đọc được lô mail', page.validation_label.text())
        self.assertFalse(page._session_jobs)
        self.assertFalse(page._workers)
        start.assert_not_called()
        page.close()

    def test_insufficient_unfinished_mail_shows_count_and_never_purchases(self):
        path = Path(self.temp.name) / 'partially-used-batch.json'
        path.write_text(json.dumps({'accounts': [
            {'email': f'paid{i}@example.test', 'password': 'offline-password', 'status': status}
            for i, status in enumerate(('success', 'already_redeemed', 'pending'))]}), encoding='utf-8')
        page = MochiChatPage(settings=self.settings)
        page.input_code.setText('TEST')
        page.input_target_points.setText('1000')
        page.input_api_key.clear()
        page.chk_use_mail_batch.setChecked(True)
        page.input_mail_batch.setText(str(path))
        with patch('gui.pages.mochi_chat.MochiInviteRunner') as runner:
            page._on_start_clicked()
        self.assertIn('chỉ còn 1 mail', page.validation_label.text())
        self.assertIn('cần 10', page.validation_label.text())
        self.assertIn('không mua bù', page.validation_label.text())
        runner.assert_not_called()
        self.assertFalse(page._session_jobs)
        self.assertFalse(page._workers)
        page.close()

    def test_batch_file_chooser_and_mode_controls(self):
        path = str(Path(self.temp.name) / 'chosen-batch.json')
        page = MochiChatPage(settings=self.settings)
        self.assertFalse(page.input_mail_batch.isEnabled())
        page.chk_use_mail_batch.setChecked(True)
        self.assertTrue(page.input_mail_batch.isEnabled())
        self.assertFalse(page.combo_provider.isEnabled())
        self.assertFalse(page.btn_check_balance.isEnabled())
        with patch('gui.pages.mochi_chat.QFileDialog.getOpenFileName', return_value=(path, '')):
            page.btn_choose_mail_batch.click()
        self.assertEqual(page.input_mail_batch.text(), path)
        self.assertEqual(self.settings.value('runtime/mochi_chat/mail_batch_path'), path)
        page.chk_use_mail_batch.setChecked(False)
        self.assertTrue(page.input_api_key.isEnabled())
        self.assertTrue(page.combo_provider.isEnabled())
        self.assertFalse(page.btn_choose_mail_batch.isEnabled())
        page.close()

    def test_worker_can_stop_then_next_job_runs(self):
        page = MochiChatPage(settings=self.settings)

        class Runner:
            async def run(self, job_id, invite_code, target_count, **kwargs):
                if job_id == 'cancel':
                    await asyncio.Event().wait()
                return MochiInviteJobSummary(job_id, invite_code, target_count, 1, 1, 0, 0, 'completed', ())

        one = MochiWorkerThread(Runner(), 'cancel', 'TEST', 1, parent=page)
        results = []
        one.finished_signal.connect(results.append)
        one.start()
        self.spin_until(lambda: one._main_task is not None)
        one.request_cancel()
        self.spin_until(lambda: not one.isRunning())
        two = MochiWorkerThread(Runner(), 'next', 'TEST', 1, parent=page)
        two.finished_signal.connect(results.append)
        two.start()
        self.spin_until(lambda: not two.isRunning())
        self.app.processEvents()
        self.assertEqual([s.status for s in results], ['cancelled', 'completed'])
        one.deleteLater()
        two.deleteLater()
        page.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
