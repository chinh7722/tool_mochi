"""Offline ordered two-shop buying, stock-only fallback, accounting and Qt config."""
import asyncio
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QLineEdit
from core.config.runtime_config import MochiChatConfig
from core.mail.providers.priority import (GMAILVIP, ONE_TRIEU, MULTI_SHOP,
    MailProductUnavailable, buy_priority_batch, parse_mail_priority)
from core.mail.providers.gmailvip_vn import GmailVipVn
from core.mail.providers.one_trieu_mail import OneTrieuMail
from core.mail.providers import purchase_policy as policy
from core.platforms.mochichat.runners.invite_referral import MochiInviteRunner, MochiAccountResult
from core.proxy.models import ProxyModel
from core.telegram_users import LOCAL_USER_ID, TelegramUserStore
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread


def response(data):
    return SimpleNamespace(json=lambda: data, status_code=200, headers={})


def balance(value):
    return response({'status': 'success', 'data': {'money': str(value)}})


def session(data, balances=()):
    return SimpleNamespace(post=AsyncMock(side_effect=data) if isinstance(data, Exception)
        else AsyncMock(return_value=response(data)), get=AsyncMock(side_effect=list(balances)), close=AsyncMock())


def account(index=0):
    return f'multi-offline-{index}@example.test|pw'


KEYS = {GMAILVIP: 'offline-gmail-key', ONE_TRIEU: 'offline-1trieu-key'}
PRIORITY = 'gmailvip:3445, 1trieumail:3423, gmailvip:2862'


class PriorityParserTests(unittest.TestCase):
    def test_order_aliases_and_duplicate_pairs_preserve_first_priority(self):
        items = parse_mail_priority('GMAILVIP.VN:3445; 1trieumail:3423\ngmailvip:3445 gmailvip_vn:2862 1trieumail:3445')
        self.assertEqual([(x.provider, x.product_id) for x in items],
            [(GMAILVIP, 3445), (ONE_TRIEU, 3423), (GMAILVIP, 2862), (ONE_TRIEU, 3445)])

    def test_bad_order_never_silently_uses_default_products(self):
        for value in ('', ', ;', '3445', 'other:3445', 'gmailvip:-1', 'gmailvip:0', 'gmailvip:3.5',
                'gmailvip:3445 bad', '1trieumail:999999999999999999999999'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_mail_priority(value)


class MultiPurchaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = TelegramUserStore(Path(self.temp.name) / 'ledger.sqlite3')
        self.store.record_local_job('local1', 'TEST', 1000, 10, sale_rate_units=100000)
        for provider in (GmailVipVn, OneTrieuMail):
            provider.clear_seen_cache()
        self.pace = patch.object(policy._PurchaseSlot, 'before_request', AsyncMock())
        self.pace.start()
        self.addCleanup(self.pace.stop)

    async def test_interleaved_priority_uses_correct_key_id_quantity_and_stops_at_first_success(self):
        first = session({'status': 'error', 'msg': 'Sản phẩm hết hàng'})
        second = session({'status': 'error', 'msg': 'Không đủ số lượng sản phẩm'})
        third = session({'status': 'success', 'data': [account(i) for i in range(10)]})
        log = []
        with patch('core.mail.providers.gmailvip_vn.AsyncSession', side_effect=[first, third]), \
                patch('core.mail.providers.one_trieu_mail.AsyncSession', return_value=second):
            result = await buy_priority_batch(PRIORITY, KEYS, 10, on_choice=log.append)
        self.assertEqual(len(result), 10)
        for connection, shop, pid in ((first, GMAILVIP, 3445), (second, ONE_TRIEU, 3423), (third, GMAILVIP, 2862)):
            connection.post.assert_awaited_once()
            payload = connection.post.call_args.kwargs['data']
            self.assertEqual((payload['api_key'], payload['id'], payload['amount']), (KEYS[shop], pid, 10))
            connection.close.assert_awaited_once()
        self.assertEqual([x for x in log if x.startswith('Thử')],
            ['Thử GmailVip.vn:3445, số lượng 10 mail...', 'Thử 1TrieuMail.com:3423, số lượng 10 mail...',
             'Thử GmailVip.vn:2862, số lượng 10 mail...'])
        self.assertFalse(any(key in str(log) for key in KEYS.values()))

    async def test_paid_short_delivery_never_buys_from_another_shop(self):
        first = session({'status': 'success', 'data': [account(i) for i in range(3)]})
        with patch('core.mail.providers.gmailvip_vn.AsyncSession', return_value=first), \
                patch('core.mail.providers.one_trieu_mail.AsyncSession') as other:
            result = await buy_priority_batch(PRIORITY, KEYS, 10)
        self.assertEqual(len(result), 3)
        other.assert_not_called()
        first.post.assert_awaited_once()

    async def test_uncertain_paid_or_invalid_responses_never_fall_through(self):
        cases = [TimeoutError('lost response'), {'status': 'success', 'data': []},
            {'status': 'success', 'data': ['bad-account']},
            {'status': 'error', 'msg': 'Hết hàng', 'data': ['paid@example.test|pw']},
            {'status': 'error', 'msg': 'API key không hợp lệ'},
            {'status': 'error', 'msg': 'Internal error'}]
        for case in cases:
            first = session(case)
            with patch('core.mail.providers.gmailvip_vn.AsyncSession', return_value=first), \
                    patch('core.mail.providers.one_trieu_mail.AsyncSession') as other:
                with self.subTest(case=type(case).__name__), self.assertRaises(ValueError):
                    await buy_priority_batch(PRIORITY, KEYS, 10)
            other.assert_not_called()
            first.post.assert_awaited_once()

    async def test_missing_referenced_key_stops_before_any_purchase(self):
        with patch('core.mail.providers.gmailvip_vn.AsyncSession') as first:
            with self.assertRaisesRegex(ValueError, '1TrieuMail'):
                await buy_priority_batch(PRIORITY, {GMAILVIP: 'offline'}, 10)
        first.assert_not_called()

    async def test_all_stock_rejections_end_without_a_paid_batch(self):
        with patch('core.mail.providers.gmailvip_vn.AsyncSession', return_value=session(
                {'status': 'error', 'msg': 'Sản phẩm không hợp lệ'})), \
                patch('core.mail.providers.one_trieu_mail.AsyncSession', return_value=session(
                {'status': 'error', 'msg': 'Sản phẩm không tồn tại'})):
            with self.assertRaises(MailProductUnavailable):
                await buy_priority_batch('gmailvip:3445, 1trieumail:3423', KEYS, 10)

    async def test_rate_limit_exhaustion_does_not_switch_shop(self):
        first = session({'status': 'error', 'msg': 'Thao tác quá nhanh'})
        with patch('core.mail.providers.gmailvip_vn.AsyncSession', return_value=first), \
                patch('core.mail.providers.one_trieu_mail.AsyncSession') as other:
            with self.assertRaises(policy.PurchaseRateLimitError):
                await buy_priority_batch(PRIORITY, KEYS, 10)
        self.assertEqual(first.post.await_count, 3)
        self.assertEqual({c.kwargs['data']['id'] for c in first.post.await_args_list}, {3445})
        other.assert_not_called()

    async def test_stock_guard_books_each_shop_and_final_paid_cost_for_owner(self):
        first = session({'status': 'error', 'msg': 'Hết hàng'}, [balance('100'), balance('100')])
        second = session({'status': 'error', 'msg': 'Không đủ số lượng'}, [balance('50'), balance('50')])
        third = session({'status': 'success', 'data': [account(i) for i in range(10)]},
            [balance('100'), balance('72.45')])
        with patch('core.mail.providers.gmailvip_vn.AsyncSession', side_effect=[first, third]), \
                patch('core.mail.providers.one_trieu_mail.AsyncSession', return_value=second):
            await buy_priority_batch(PRIORITY, KEYS, 10, on_purchase=self.store.record_purchase, job_id='local1')
        user = self.store.get_user(LOCAL_USER_ID)
        self.assertEqual((user.cost_vnd, user.pending_costs), (Decimal('27.55'), 0))
        purchases = self.store.list_purchases(LOCAL_USER_ID)
        self.assertEqual(len(purchases), 3)
        self.assertEqual(sum(row['cost_units'] for row in purchases), 2755)
        self.assertEqual({row['provider'] for row in purchases}, {'gmailvip.vn', '1trieumail.com'})

    async def test_stock_response_with_debit_or_unknown_balance_never_buys_next_source(self):
        for after, expected_cost, pending in ((balance('90'), 1000, 0), (TimeoutError('profile lost'), 0, 1)):
            # Separate ledger avoids mixing test receipts from the two scenarios.
            job = f'guard-{pending}'
            self.store.record_local_job(job, 'TEST', 1000, 10)
            first = session({'status': 'error', 'msg': 'Hết hàng'}, [balance('100'), after])
            with patch('core.mail.providers.gmailvip_vn.AsyncSession', return_value=first), \
                    patch('core.mail.providers.one_trieu_mail.AsyncSession') as other:
                with self.assertRaisesRegex(ValueError, 'đối soát'):
                    await buy_priority_batch(PRIORITY, KEYS, 10, on_purchase=self.store.record_purchase, job_id=job)
            other.assert_not_called()
            receipt = self.store.list_purchases(LOCAL_USER_ID)[0]
            self.assertEqual(receipt['cost_units'], expected_cost if not pending else None)

    async def test_rotated_mail_proxy_is_carried_to_the_next_shop(self):
        original, new = ProxyModel('127.0.0.1', 25001), ProxyModel('127.0.0.1', 25002)
        calls = []
        def first(key, **kwargs):
            calls.append(kwargs['proxy'])
            async def buy(*_args, **_kwargs):
                await kwargs['on_rate_limit']()
                raise MailProductUnavailable('hết hàng')
            context = AsyncMock()
            context.__aenter__.return_value = SimpleNamespace(buy_gmail_domain_info=buy)
            return context
        def second(key, **kwargs):
            calls.append(kwargs['proxy'])
            context = AsyncMock()
            context.__aenter__.return_value = SimpleNamespace(buy_gmail_domain_info=AsyncMock(return_value=[1]))
            return context
        rotate = AsyncMock(return_value=new)
        await buy_priority_batch('gmailvip:1, 1trieumail:2', KEYS, 1,
            provider_types={GMAILVIP: first, ONE_TRIEU: second}, proxy=original, on_rate_limit=rotate)
        self.assertEqual(calls, [original, new])
        rotate.assert_awaited_once()

    async def test_cancelled_purchase_never_switches_shop(self):
        context = AsyncMock()
        context.__aenter__.return_value = SimpleNamespace(buy_gmail_domain_info=AsyncMock(side_effect=asyncio.CancelledError()))
        with patch('core.mail.providers.gmailvip_vn.GmailVipVn', return_value=context), \
                patch('core.mail.providers.one_trieu_mail.OneTrieuMail') as other:
            with self.assertRaises(asyncio.CancelledError):
                await buy_priority_batch(PRIORITY, KEYS, 10)
        other.assert_not_called()

    async def test_runner_uses_priority_and_preserves_a_short_batch_without_topping_up(self):
        first = session({'status': 'error', 'msg': 'Hết hàng'})
        second = session({'status': 'success', 'data': [account(i) for i in range(3)]})
        runner = MochiInviteRunner(concurrency=2, mail_provider=MULTI_SHOP, mail_api_keys=KEYS,
            mail_product_ids=PRIORITY, mail_batch_dir=self.temp.name,probe_invite_first=False)
        runner._process_account = AsyncMock(side_effect=lambda _j, _c, item, *_args, **_kwargs:
            MochiAccountResult(item.identifier, True, 'success'))
        with patch('core.mail.providers.gmailvip_vn.AsyncSession', return_value=first), \
                patch('core.mail.providers.one_trieu_mail.AsyncSession', return_value=second):
            summary = await runner.run('runner-multi', 'TEST', 10)
        self.assertEqual((summary.status, summary.success_count), ('failed', 3))
        self.assertEqual(len(json.loads(runner.mail_batch_path.read_text(encoding='utf-8'))['accounts']), 3)
        first.post.assert_awaited_once()
        second.post.assert_awaited_once()

    async def test_saved_batch_never_requires_two_keys_or_attempts_new_purchase(self):
        path = Path(self.temp.name) / 'paid.json'
        path.write_text(json.dumps({'accounts': [{'email': 'paid-multi@example.test', 'password': 'pw'}]}))
        runner = MochiInviteRunner(mail_provider=MULTI_SHOP, mail_product_ids='', mail_api_keys={})
        runner._process_account = AsyncMock(side_effect=lambda _j, _c, item, *_args, **_kwargs:
            MochiAccountResult(item.identifier, True, 'success'))
        with patch('core.platforms.mochichat.runners.invite_referral.buy_priority_batch') as buy:
            summary = await runner.run('reuse', 'TEST', 1, source_mail_batch=path)
        self.assertEqual(summary.status, 'completed')
        buy.assert_not_called()


class MultiGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(str(Path(self.temp.name) / 'settings.ini'), QSettings.IniFormat)
        self.page = MochiChatPage(settings=self.settings)
        self.addCleanup(self.page.close)
        self.page.input_code.setText('TEST')
        self.page.input_target_points.setText('1000')

    def choose(self, shop):
        self.page.combo_provider.setCurrentIndex(self.page.combo_provider.findData(shop))

    def configure(self):
        self.choose(MULTI_SHOP)
        self.page.input_gmailvip_key.setText(KEYS[GMAILVIP])
        self.page.input_1trieumail_key.setText(KEYS[ONE_TRIEU])
        self.page.input_pids.setText(PRIORITY)

    def test_both_keys_and_priority_persist_and_are_masked(self):
        self.configure()
        self.page._save_workspace_settings()
        config = MochiChatConfig.from_settings(self.settings)
        self.assertEqual((config.mail_provider, config.mail_api_keys, config.mail_product_ids), (MULTI_SHOP, KEYS, PRIORITY))
        for field in (self.page.input_gmailvip_key, self.page.input_1trieumail_key):
            self.assertEqual(field.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(self.page.mail_key_stack.currentIndex(), 1)
        reopened = MochiChatPage(settings=self.settings)
        try:
            self.assertEqual(reopened.input_gmailvip_key.text(), KEYS[GMAILVIP])
            self.assertEqual(reopened.input_1trieumail_key.text(), KEYS[ONE_TRIEU])
            self.assertEqual(reopened.input_pids.text(), PRIORITY)
        finally:
            reopened.close()

    def test_switching_modes_preserves_two_keys_and_each_single_shop_product_list(self):
        self.page.input_api_key.setText(KEYS[GMAILVIP])
        self.page.input_pids.setText('3445, 2862')
        self.choose(ONE_TRIEU)
        self.page.input_api_key.setText(KEYS[ONE_TRIEU])
        self.page.input_pids.setText('3423')
        self.choose(MULTI_SHOP)
        self.assertEqual(self.page.input_gmailvip_key.text(), KEYS[GMAILVIP])
        self.assertEqual(self.page.input_1trieumail_key.text(), KEYS[ONE_TRIEU])
        self.page.input_pids.setText(PRIORITY)
        self.choose(GMAILVIP)
        self.assertEqual((self.page.input_api_key.text(), self.page.input_pids.text()), (KEYS[GMAILVIP], '3445, 2862'))
        self.choose(ONE_TRIEU)
        self.assertEqual(self.page.input_pids.text(), '3423')
        self.choose(MULTI_SHOP)
        self.assertEqual(self.page.input_pids.text(), PRIORITY)

    def test_bad_priority_or_missing_shop_key_never_registers_or_starts_a_paid_job(self):
        self.configure()
        for pids, second_key in (('3445, 3423', KEYS[ONE_TRIEU]), (PRIORITY, '')):
            self.page.input_pids.setText(pids)
            self.page.input_1trieumail_key.setText(second_key)
            self.page.local_job_registrar = Mock()
            with patch.object(MochiWorkerThread, 'start') as start:
                self.assertIsNone(self.page._on_start_clicked())
            self.page.local_job_registrar.assert_not_called()
            start.assert_not_called()

    def test_job_keeps_an_immutable_snapshot_of_two_keys_and_order(self):
        self.configure()
        with patch.object(MochiWorkerThread, 'start'):
            job = self.page._on_start_clicked()
        runner = self.page._workers[job].runner
        self.page.input_gmailvip_key.setText('changed-after-start')
        self.page.input_pids.setText('gmailvip:999')
        self.assertEqual(runner.mail_api_keys, KEYS)
        self.assertEqual(runner.mail_product_ids, PRIORITY)

    def test_legacy_generic_key_migrates_to_the_correct_shop_without_being_copied_to_both(self):
        settings = QSettings(str(Path(self.temp.name) / 'legacy.ini'), QSettings.IniFormat)
        settings.setValue('runtime/mochi_chat/mail_provider', ONE_TRIEU)
        settings.setValue('runtime/mochi_chat/mail_provider_api_key', 'old-1trieu-key')
        config = MochiChatConfig.from_settings(settings)
        self.assertEqual(config.mail_api_keys, {GMAILVIP: '', ONE_TRIEU: 'old-1trieu-key'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
