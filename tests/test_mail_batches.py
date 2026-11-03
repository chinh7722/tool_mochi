"""Offline checks for batch size, purchase pacing, and independent proxy routes."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import asyncio
import itertools
import json
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.mail.providers.gmailvip_vn import GmailVipVn
from core.mail.providers.one_trieu_mail import OneTrieuMail
from core.mail.providers import purchase_policy as policy
from core.mail.batch_inventory import load_batch
from core.platforms.mochichat.fetcher import MochiChatFetcher
from core.platforms.mochichat.runners.invite_referral import (
    MochiAccountItem, MochiAccountResult, MochiInviteRunner,
)
from core.proxy.models import ProxyModel, curl_proxy_options
from core.proxy.health import ProxyHealthResult
from core.proxy.broker import ProxyBroker
from core.proxy.client import ProxyPoolClient
from core.proxy.protocol import ProxyReleaseOutcome
from core.proxy.rotators import RotationResult

RUNNER = 'core.platforms.mochichat.runners.invite_referral'


def response(data, headers=None):
    return SimpleNamespace(json=lambda: data, status_code=200, text='offline', headers=headers or {})


def mail(index):
    return SimpleNamespace(username=f'offline{index}@example.test', password='offline-password')


class BatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def setup_batch(self, count, **kwargs):
        runner = MochiInviteRunner(concurrency=5, mail_batch_dir=self.directory.name, probe_invite_first=False, **kwargs)
        client = SimpleNamespace(buy_gmail_domain_info=AsyncMock(return_value=[mail(i) for i in range(count)]))
        context = AsyncMock()
        context.__aenter__.return_value = client

        async def process(job, code, item, proxy, owner, **kwargs):
            return MochiAccountResult(item.identifier, True, 'success')

        runner._process_account = AsyncMock(side_effect=process)
        return runner, client, context

    async def test_ten_attempts_purchase_one_batch_of_ten(self):
        runner, client, context = self.setup_batch(10)
        purchase_started = asyncio.Event()
        release_purchase = asyncio.Event()

        async def buy(*args, **kwargs):
            purchase_started.set()
            await release_purchase.wait()
            return [mail(i) for i in range(10)]

        client.buy_gmail_domain_info.side_effect = buy
        with patch(f'{RUNNER}.GmailVipVn', return_value=context):
            task = asyncio.create_task(runner.run('ten', 'TEST', 10))
            await asyncio.wait_for(purchase_started.wait(), 1)
            await asyncio.sleep(0.25)  # Several scheduler ticks while purchase is pending.
            self.assertEqual(client.buy_gmail_domain_info.await_count, 1)
            runner._process_account.assert_not_awaited()
            release_purchase.set()
            result = await asyncio.wait_for(task, 3)
        client.buy_gmail_domain_info.assert_awaited_once_with(None, amount=10)
        self.assertEqual((result.success_count, result.attempted_count), (10, 10))
        identifiers = [call.args[2].identifier for call in runner._process_account.await_args_list]
        self.assertEqual(len(set(identifiers)), 10)
        saved = json.loads(runner.mail_batch_path.read_text(encoding='utf-8'))
        self.assertEqual(len(saved['accounts']), 10)
        self.assertEqual({a['status'] for a in saved['accounts']}, {'success'})

    async def test_resume_buys_only_remaining_seven(self):
        runner, client, context = self.setup_batch(7)
        with patch(f'{RUNNER}.GmailVipVn', return_value=context):
            result = await runner.run('resume', 'TEST', 10, initial_success_count=3)
        client.buy_gmail_domain_info.assert_awaited_once_with(None, amount=7)
        self.assertEqual((result.success_count, result.attempted_count), (10, 7))

    async def test_saved_batch_runs_without_purchasing_and_merges_existing_statuses(self):
        path = Path(self.directory.name) / 'paid.json'
        rows = [dict(email=f'paid{i}@example.test', password='pw', status=status)
            for i, status in enumerate(('success', 'already_redeemed', 'network_error', 'pending'))]
        path.write_text(json.dumps({'job_id': 'old', 'accounts': rows}), encoding='utf-8')
        self.assertEqual(len(load_batch(path)), 2)
        runner, _, _ = self.setup_batch(0)
        with patch(f'{RUNNER}.GmailVipVn') as provider:
            result = await runner.run('reuse-paid', 'TEST', 2, source_mail_batch=path)
        provider.assert_not_called()
        self.assertEqual(result.status, 'completed')
        saved = json.loads(path.read_text())
        self.assertEqual(len(saved['accounts']), 4)
        self.assertEqual([a['status'] for a in saved['accounts']], ['success', 'already_redeemed', 'success', 'success'])
        self.assertEqual(saved['last_job_id'], 'reuse-paid')

    async def test_invalid_saved_batch_never_purchases(self):
        for data in ({}, {'accounts': [{'email': 'missing-password@example.test'}]},
                {'accounts': [{'email': 'duplicate@example.test', 'password': 'pw'}] * 2}):
            path = Path(self.directory.name) / 'invalid.json'
            path.write_text(json.dumps(data), encoding='utf-8')
            runner, _, _ = self.setup_batch(0)
            with patch(f'{RUNNER}.GmailVipVn') as provider:
                result = await runner.run('invalid-paid', 'TEST', 2, source_mail_batch=path)
            provider.assert_not_called()
            self.assertEqual(result.status, 'failed')
            runner._process_account.assert_not_awaited()

    async def test_second_job_cannot_process_the_same_batch_concurrently(self):
        path = Path(self.directory.name) / 'shared-paid.json'
        path.write_text(json.dumps({'accounts': [dict(email='paid@example.test', password='pw', status='pending')]}))
        first, _, _ = self.setup_batch(0)
        second, _, _ = self.setup_batch(0)
        started, finish = asyncio.Event(), asyncio.Event()

        async def process(job, code, item, *args, **kwargs):
            started.set()
            await finish.wait()
            return MochiAccountResult(item.identifier, True, 'success')

        first._process_account.side_effect = process
        with patch(f'{RUNNER}.GmailVipVn') as provider:
            running = asyncio.create_task(first.run('first-paid', 'TEST', 1, source_mail_batch=path))
            await asyncio.wait_for(started.wait(), 1)
            rejected = await second.run('second-paid', 'TEST', 1, source_mail_batch=path)
            self.assertEqual(rejected.status, 'failed')
            self.assertIn('job khác', rejected.error_message)
            second._process_account.assert_not_awaited()
            finish.set()
            self.assertEqual((await running).status, 'completed')
        provider.assert_not_called()

    async def test_new_purchase_is_reserved_until_processing_and_status_save_finish(self):
        first, shop, context = self.setup_batch(1)
        second, _, _ = self.setup_batch(0)
        started, finish = asyncio.Event(), asyncio.Event()

        async def process(job, code, item, *args, **kwargs):
            started.set()
            await finish.wait()
            return MochiAccountResult(item.identifier, True, 'success')

        first._process_account.side_effect = process
        with patch(f'{RUNNER}.GmailVipVn', return_value=context) as provider:
            running = asyncio.create_task(first.run('new-paid', 'TEST', 1))
            await asyncio.wait_for(started.wait(), 1)
            rejected = await second.run('reuse-active', 'TEST', 1, source_mail_batch=first.mail_batch_path)
            self.assertEqual(rejected.status, 'failed')
            self.assertIn('job khác', rejected.error_message)
            second._process_account.assert_not_awaited()
            finish.set()
            self.assertEqual((await running).status, 'completed')
        self.assertEqual(provider.call_count, 1)
        self.assertIsNone(first._purchased_batch_lock)

    async def test_source_batch_is_reloaded_before_using_a_gui_snapshot(self):
        path = Path(self.directory.name) / 'stale-paid.json'
        path.write_text(json.dumps({'accounts': [
            dict(email=f'paid{i}@example.test', password='pw', status='pending') for i in range(2)]}))
        snapshot = load_batch(path)
        data = json.loads(path.read_text())
        data['accounts'][0]['status'] = 'success'
        path.write_text(json.dumps(data))
        runner, _, _ = self.setup_batch(0)
        with patch(f'{RUNNER}.GmailVipVn') as provider:
            result = await runner.run('reloaded-paid', 'TEST', 1, accounts=snapshot, source_mail_batch=path)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(runner._process_account.await_args.args[2].email, 'paid1@example.test')
        provider.assert_not_called()

    async def test_already_completed_target_never_purchases(self):
        runner, client, context = self.setup_batch(0)
        with patch(f'{RUNNER}.GmailVipVn', return_value=context) as provider:
            result = await runner.run('done', 'TEST', 10, initial_success_count=10)
        provider.assert_not_called()
        self.assertEqual(result.status, 'completed')

    async def test_short_delivery_is_finite_and_never_topped_up(self):
        runner, client, context = self.setup_batch(3)
        with patch(f'{RUNNER}.GmailVipVn', return_value=context):
            result = await runner.run('short', 'TEST', 10)
        client.buy_gmail_domain_info.assert_awaited_once_with(None, amount=10)
        self.assertEqual((result.success_count, result.attempted_count), (3, 3))
        self.assertEqual(result.status, 'failed')

    async def test_failed_accounts_do_not_trigger_more_purchases(self):
        runner, client, context = self.setup_batch(10)
        runner._process_account.side_effect = lambda job, code, item, *args, **kw: MochiAccountResult(item.identifier, False, 'auth_failed')
        with patch(f'{RUNNER}.GmailVipVn', return_value=context):
            result = await runner.run('failures', 'TEST', 10)
        self.assertEqual(client.buy_gmail_domain_info.await_count, 1)
        self.assertEqual((result.attempted_count, result.failure_count), (10, 10))

    async def test_stopped_job_keeps_unused_mail_on_disk(self):
        runner, client, context = self.setup_batch(10)
        runner._process_account.side_effect = lambda job, code, item, *args, **kw: MochiAccountResult(item.identifier, False, 'invalid_code')
        with patch(f'{RUNNER}.GmailVipVn', return_value=context):
            result = await runner.run('stopped', 'TEST', 10)
        self.assertEqual(result.status, 'invalid_code')
        saved = json.loads(runner.mail_batch_path.read_text(encoding='utf-8'))
        self.assertEqual(len(saved['accounts']), 10)
        self.assertEqual(sum(a['status'] == 'pending' for a in saved['accounts']), 9)

    async def test_cancel_after_purchase_preserves_complete_batch(self):
        runner, client, context = self.setup_batch(10)
        cancel = threading.Event()
        runner.cancel_event = cancel

        async def buy(*args, **kwargs):
            cancel.set()
            return [mail(i) for i in range(10)]

        client.buy_gmail_domain_info.side_effect = buy
        with patch(f'{RUNNER}.GmailVipVn', return_value=context):
            result = await runner.run('cancelled-batch', 'TEST', 10)
        self.assertEqual(result.status, 'cancelled')
        runner._process_account.assert_not_awaited()
        self.assertEqual(len(json.loads(runner.mail_batch_path.read_text())['accounts']), 10)

    async def test_overdelivery_is_saved_but_processing_stops_at_requested_amount(self):
        runner, client, context = self.setup_batch(12)
        with patch(f'{RUNNER}.GmailVipVn', return_value=context):
            result = await runner.run('extra', 'TEST', 10)
        self.assertEqual(result.attempted_count, 10)
        saved = json.loads(runner.mail_batch_path.read_text())['accounts']
        self.assertEqual(len(saved), 12)
        self.assertEqual(sum(a['status'] == 'pending' for a in saved), 2)

    async def test_mail_proxy_toggle_applies_to_whole_batch(self):
        proxy = ProxyModel.from_string('127.0.0.1:29991')
        for enabled in (True, False):
            broker = ProxyBroker([proxy])
            runner, client, context = self.setup_batch(2,
                proxy_client=ProxyPoolClient(broker=broker), mail_via_proxy=enabled,
                proxy_probe=AsyncMock(return_value=ProxyHealthResult(True, proxy.server, 'ip')))
            with patch(f'{RUNNER}.GmailVipVn', return_value=context) as provider:
                result = await runner.run(f'mail-proxy-{enabled}', 'TEST', 2)
            self.assertEqual(result.status, 'completed')
            self.assertIs(provider.call_args.kwargs['proxy'], proxy if enabled else None)
            client.buy_gmail_domain_info.assert_awaited_once_with(None, amount=2)

    async def test_all_routes_direct_never_leases_configured_proxy(self):
        client = SimpleNamespace(lease=AsyncMock(side_effect=AssertionError('must not lease')), release_job=AsyncMock())
        runner, shop, context = self.setup_batch(1, proxy_client=client,
            mail_via_proxy=False, google_login_via_proxy=False,
            app_login_via_proxy=False, redeem_via_proxy=False)
        with patch(f'{RUNNER}.GmailVipVn', return_value=context):
            result = await runner.run('all-direct', 'TEST', 1)
        self.assertEqual(result.status, 'completed')
        client.lease.assert_not_awaited()

    async def test_direct_api_error_does_not_mark_mail_proxy_faulty(self):
        proxy = ProxyModel.from_string('127.0.0.1:29992')
        broker = ProxyBroker([proxy])
        client = ProxyPoolClient(broker=broker)
        runner, shop, context = self.setup_batch(1, proxy_client=client, app_login_via_proxy=False,
            proxy_probe=AsyncMock(return_value=ProxyHealthResult(True, proxy.server, 'ip')))
        runner._process_account.side_effect = lambda job, code, item, *a, **k: MochiAccountResult(
            item.identifier, False, 'network_error', data={'stop_job': True, 'proxy_used': False})
        with patch.object(client, 'release', wraps=client.release) as release, patch(f'{RUNNER}.GmailVipVn', return_value=context):
            await runner.run('direct-api-error', 'TEST', 1)
        self.assertEqual(release.await_args.kwargs['outcome'], ProxyReleaseOutcome.BUSINESS_FAILURE)

    async def test_shop_rate_limit_rotates_broker_and_uses_new_proxy_for_same_batch(self):
        for provider in (GmailVipVn, OneTrieuMail):
            provider.clear_seen_cache()
            original = ProxyModel.from_string('127.0.0.1:29980', rotation_url=f'offline-rate-{provider.__name__}')
            replacement = ProxyModel.from_string('127.0.0.1:29981')
            broker = ProxyBroker([original])
            client = ProxyPoolClient(broker=broker)
            state = broker._proxies[original.raw]
            state.provider.get_current_or_rotate = AsyncMock(return_value=RotationResult(True, replacement))
            runner, _, _ = self.setup_batch(2, proxy_client=client,
                mail_provider='1trieumail_com' if provider is OneTrieuMail else 'gmailvip_vn',
                proxy_probe=AsyncMock(return_value=ProxyHealthResult(True, original.server, 'ip')))
            old = SimpleNamespace(post=AsyncMock(return_value=response({'status': 'error', 'msg': 'Thao tác quá nhanh'})), close=AsyncMock())
            new = SimpleNamespace(post=AsyncMock(return_value=response({'status': 'success',
                'data': ['rotated0@example.test|pw', 'rotated1@example.test|pw']})), close=AsyncMock())
            with patch(f'{provider.__module__}.AsyncSession', side_effect=[old, new]) as sessions, \
                 patch.object(policy._PurchaseSlot, 'before_request', AsyncMock()), \
                 patch.object(client, 'release', wraps=client.release) as release:
                result = await runner.run(f'rate-{provider.__name__}', 'TEST', 2)
            self.assertEqual(result.status, 'completed')
            self.assertEqual(old.post.await_args.kwargs['data'], new.post.await_args.kwargs['data'])
            self.assertEqual(new.post.await_args.kwargs['data']['amount'], 2)
            self.assertEqual(sessions.call_args_list[1].kwargs['proxies'], curl_proxy_options(replacement))
            self.assertTrue(state.provider.get_current_or_rotate.await_args.kwargs['force_rotate'])
            state.provider.get_current_or_rotate.assert_awaited_once()
            self.assertEqual(release.await_args_list[0].kwargs['outcome'], ProxyReleaseOutcome.QUARANTINED)
            self.assertIs(runner._process_account.await_args_list[0].args[3], replacement)
            old.close.assert_awaited_once()
            new.close.assert_awaited_once()
            self.assertFalse(broker._leases)

    async def test_cancel_during_shop_proxy_rotation_never_repeats_purchase(self):
        original = ProxyModel.from_string('127.0.0.1:29982', rotation_url='offline-rate-cancel')
        broker = ProxyBroker([original])
        client = ProxyPoolClient(broker=broker)
        state = broker._proxies[original.raw]
        started = asyncio.Event()

        async def rotate(**kwargs):
            started.set()
            await asyncio.Event().wait()

        state.provider.get_current_or_rotate = AsyncMock(side_effect=rotate)
        runner, _, _ = self.setup_batch(10, proxy_client=client,
            proxy_probe=AsyncMock(return_value=ProxyHealthResult(True, original.server, 'ip')))
        session = SimpleNamespace(post=AsyncMock(return_value=response({'status': 'error', 'msg': 'Thao tác quá nhanh'})), close=AsyncMock())
        with patch('core.mail.providers.gmailvip_vn.AsyncSession', return_value=session), \
             patch.object(policy._PurchaseSlot, 'before_request', AsyncMock()):
            task = asyncio.create_task(runner.run('rate-cancelled', 'TEST', 10))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            result = await asyncio.wait_for(task, 1)
        self.assertEqual(result.status, 'cancelled')
        session.post.assert_awaited_once()
        session.close.assert_awaited_once()
        runner._process_account.assert_not_awaited()
        self.assertFalse(broker._leases)
        self.assertFalse(state.rotating)


class PurchasePolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        policy._slots.clear()
        for provider in (GmailVipVn, OneTrieuMail):
            provider.clear_seen_cache()
        self.pace = patch.object(policy._PurchaseSlot, 'before_request', AsyncMock())
        self.pace.start()
        self.addCleanup(self.pace.stop)

    async def test_rate_rejection_waits_then_retries_same_batch_and_product(self):
        for provider in (GmailVipVn, OneTrieuMail):
            provider.clear_seen_cache()
            session = SimpleNamespace(post=AsyncMock(side_effect=[
                response({'status': 'error', 'msg': 'Thao tác quá nhanh, vui lòng chờ'}, {'Retry-After': '9'}),
                response({'status': 'success', 'data': [f'test{i}@example.test|pw' for i in range(10)]}),
            ]))
            client = provider('offline-key')
            client._session = session
            with patch.object(policy._PurchaseSlot, 'defer') as defer:
                result = await client.buy_gmail_domain_info([18551, 999], amount=10)
            self.assertEqual(len(result), 10)
            self.assertEqual(session.post.await_count, 2)
            self.assertTrue(all(c.kwargs['data']['id'] == 18551 and c.kwargs['data']['amount'] == 10 for c in session.post.await_args_list))
            defer.assert_called_once_with(10.0)

    async def test_rate_limit_exhaustion_stops_without_changing_products(self):
        for provider in (GmailVipVn, OneTrieuMail):
            session = SimpleNamespace(post=AsyncMock(return_value=response({'status': 'error', 'msg': 'Thao tác quá nhanh'})))
            client = provider('offline-key')
            client._session = session
            with self.assertRaises(policy.PurchaseRateLimitError):
                await client.buy_gmail_domain_info([1, 2], amount=10)
            self.assertEqual(session.post.await_count, 3)
            self.assertEqual({c.kwargs['data']['id'] for c in session.post.await_args_list}, {1})

    async def test_uncertain_or_contradictory_purchase_is_not_repeated(self):
        cases = [TimeoutError('lost response'), response([]), response({'msg': 'Thao tác quá nhanh'}),
            response({'status': 'error', 'msg': 'Thao tác quá nhanh', 'data': ['paid@example.test|pw']})]
        for provider, case in itertools.product((GmailVipVn, OneTrieuMail), cases):
            session = SimpleNamespace(post=AsyncMock(side_effect=case) if isinstance(case, Exception)
                else AsyncMock(return_value=case))
            client = provider('offline-key')
            client._session = session
            with self.assertRaises(ValueError):
                await client.buy_gmail_domain_info([1, 2], amount=10)
            self.assertEqual(session.post.await_count, 1)

    async def test_long_retry_after_stops_instead_of_retrying_early(self):
        session = SimpleNamespace(post=AsyncMock(return_value=response(
            {'status': 'error', 'msg': 'Thao tác quá nhanh'}, {'Retry-After': '120'})))
        with self.assertRaises(policy.PurchaseRateLimitError) as raised:
            await policy.purchase_json(session, 'offline', {'id': 1, 'amount': 10}, 'shop', 'key')
        self.assertEqual(raised.exception.retry_after, 120)
        self.assertEqual(session.post.await_count, 1)

    async def test_provider_sessions_use_selected_proxy_with_credentials(self):
        proxy = ProxyModel.from_string('127.0.0.1:29993:user:pw')
        for provider in (GmailVipVn, OneTrieuMail):
            for route in (proxy, None):
                with patch(f'{provider.__module__}.AsyncSession', return_value=SimpleNamespace(close=AsyncMock())) as session:
                    async with provider('key', proxy=route):
                        pass
                self.assertEqual(session.call_args.kwargs['proxies'], curl_proxy_options(route))
                self.assertFalse(session.call_args.kwargs['trust_env'])

    async def test_rate_limit_reopens_session_even_when_endpoint_stays_same(self):
        proxy = ProxyModel.from_string('127.0.0.1:29983')
        for provider, context_mode in itertools.product((GmailVipVn, OneTrieuMail), (True, False)):
            provider.clear_seen_cache()
            rotate = AsyncMock(return_value=proxy)
            old = SimpleNamespace(post=AsyncMock(return_value=response({'status': 'error', 'msg': 'Thao tác quá nhanh'})), close=AsyncMock())
            new = SimpleNamespace(post=AsyncMock(return_value=response({'status': 'success', 'data': ['new@example.test|pw']})), close=AsyncMock())
            client = provider('offline-key', proxy=proxy, on_rate_limit=rotate)
            with patch(f'{provider.__module__}.AsyncSession', side_effect=[old, new]) as factory:
                if context_mode:
                    async with client:
                        await client.buy_gmail_domain_info([1, 2], amount=1)
                        self.assertIs(client._session, new)
                else:
                    await client.buy_gmail_domain_info([1, 2], amount=1)
                    self.assertIsNone(client._session)
            rotate.assert_awaited_once()
            old.post.assert_awaited_once()
            new.post.assert_awaited_once()
            old.close.assert_awaited_once()
            new.close.assert_awaited_once()
            self.assertEqual(factory.call_count, 2)

    async def test_direct_purchase_never_calls_proxy_rotation(self):
        for provider in (GmailVipVn, OneTrieuMail):
            provider.clear_seen_cache()
            rotate = AsyncMock(side_effect=AssertionError('direct purchase cannot rotate'))
            client = provider('offline-key', on_rate_limit=rotate)
            client._session = SimpleNamespace(post=AsyncMock(side_effect=[
                response({'status': 'error', 'msg': 'Thao tác quá nhanh'}),
                response({'status': 'success', 'data': ['direct@example.test|pw']})]))
            await client.buy_gmail_domain_info([1], amount=1)
            rotate.assert_not_awaited()

    async def test_failed_rotation_stops_before_new_purchase_or_next_product(self):
        proxy = ProxyModel.from_string('127.0.0.1:29984')
        for provider in (GmailVipVn, OneTrieuMail):
            rotate = AsyncMock(side_effect=RuntimeError('offline rotation failed'))
            client = provider('offline-key', proxy=proxy, on_rate_limit=rotate)
            client._session = SimpleNamespace(post=AsyncMock(return_value=response({'status': 'error', 'msg': 'Thao tác quá nhanh'})))
            with self.assertRaisesRegex(ValueError, 'offline rotation failed'):
                await client.buy_gmail_domain_info([1, 2], amount=10)
            client._session.post.assert_awaited_once()
            rotate.assert_awaited_once()

    async def test_uncertain_response_and_business_rejection_never_rotate(self):
        proxy = ProxyModel.from_string('127.0.0.1:29985')
        for provider, payload in itertools.product((GmailVipVn, OneTrieuMail), (
                TimeoutError('lost response'),
                {'status': 'error', 'msg': 'Thao tác quá nhanh', 'data': ['paid@example.test|pw']},
                {'status': 'error', 'msg': 'Không đủ số dư'},
                {'status': 'success', 'data': ['success@example.test|pw']})):
            provider.clear_seen_cache()
            rotate = AsyncMock()
            client = provider('offline-key', proxy=proxy, on_rate_limit=rotate)
            post = AsyncMock(side_effect=payload) if isinstance(payload, Exception) else AsyncMock(return_value=response(payload))
            client._session = SimpleNamespace(post=post)
            try:
                await client.buy_gmail_domain_info([1], amount=1)
            except (ValueError, RuntimeError):
                pass
            post.assert_awaited_once()
            rotate.assert_not_awaited()


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_app_and_redeem_routes_are_independent(self):
        proxy = ProxyModel.from_string('127.0.0.1:29994:user:pw')
        for google, app, redeem in itertools.product((False, True), repeat=3):
            session = SimpleNamespace(post=AsyncMock(side_effect=[
                response({'ok': True, 'data': {'token': 'offline-token'}}), response({'ok': True}),
            ]))
            fetcher = MochiChatFetcher(proxy=proxy, session=session,
                app_login_via_proxy=app, redeem_via_proxy=redeem)
            with patch('core.platforms.mochichat.fetcher.get_google_id_token_for_app', AsyncMock(return_value='offline-id-token')) as auth:
                self.assertTrue((await fetcher.login('owner', 'offline@example.test', 'pw', google_login_via_proxy=google))['ok'])
            self.assertTrue((await fetcher.redeem_invite_code('TEST')).success)
            self.assertEqual(auth.await_args.kwargs['google_login_via_proxy'], google)
            calls = session.post.await_args_list
            for call, enabled in zip(calls, (app, redeem)):
                self.assertEqual(call.kwargs['proxies'], curl_proxy_options(proxy) if enabled else {'http': '', 'https': ''})

    async def test_direct_login_failure_never_recovers_unrelated_proxy(self):
        session = SimpleNamespace(post=AsyncMock(side_effect=TimeoutError('curl: (28) Connection timed out')))
        fetcher = MochiChatFetcher(proxy=ProxyModel.from_string('127.0.0.1:29995'),
            session=session, app_login_via_proxy=False)
        recover = AsyncMock()
        result = await fetcher._login_mochi_with_id_token('offline', recover_proxy=recover)
        self.assertFalse(result['proxy_used'])
        recover.assert_not_awaited()

    async def test_route_switch_recreates_owned_session(self):
        first = SimpleNamespace(post=AsyncMock(return_value=response({'data': {'token': 'offline'}})), close=AsyncMock())
        second = SimpleNamespace(post=AsyncMock(return_value=response({'ok': True})), close=AsyncMock())
        with patch('core.platforms.mochichat.fetcher.AsyncSession', side_effect=[first, second]) as factory:
            fetcher = MochiChatFetcher(proxy=ProxyModel.from_string('127.0.0.1:29996'),
                app_login_via_proxy=False, redeem_via_proxy=True)
            await fetcher._login_mochi_with_id_token('offline')
            await fetcher.redeem_invite_code('TEST')
            await fetcher.close()
        self.assertIsNone(factory.call_args_list[0].kwargs['proxies'])
        self.assertIsNotNone(factory.call_args_list[1].kwargs['proxies'])
        first.close.assert_awaited_once()
        second.close.assert_awaited_once()


class PurchaseGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_recovery_keeps_retry_after_wait_before_next_purchase(self):
        policy._slots.clear()
        times = []

        async def rejected(*args, **kwargs):
            times.append(time.monotonic())
            return response({'status': 'error', 'msg': 'Thao tác quá nhanh'}, {'Retry-After': '0.02'})

        async def accepted(*args, **kwargs):
            times.append(time.monotonic())
            return response({'status': 'success', 'data': []})

        old, new = SimpleNamespace(post=rejected), SimpleNamespace(post=accepted)
        recover = AsyncMock(return_value=new)
        with patch.object(policy, 'MIN_PURCHASE_INTERVAL', 0), patch.object(policy, 'MIN_FAILED_PURCHASE_INTERVAL', 0):
            await policy.purchase_json(old, 'offline', {'id': 1, 'amount': 10},
                'offline-shop', 'same-key', on_rate_limit=recover)
        recover.assert_awaited_once()
        self.assertGreaterEqual(times[1] - times[0], 0.018)

    async def test_same_key_requests_are_really_paced(self):
        policy._slots.clear()
        times = []

        async def post(*args, **kwargs):
            times.append(time.monotonic())
            return response({'status': 'success', 'data': []})

        session = SimpleNamespace(post=post)
        with patch.object(policy, 'MIN_PURCHASE_INTERVAL', 0.02):
            await asyncio.gather(*(policy.purchase_json(session, 'offline',
                {'id': 1, 'amount': 10}, 'offline-shop', 'same-key') for _ in range(2)))
        self.assertGreaterEqual(times[1] - times[0], 0.018)

    async def test_rejected_final_attempt_keeps_cooldown_for_next_job(self):
        policy._slots.clear()
        times = []

        async def post(*args, **kwargs):
            times.append(time.monotonic())
            return (response({'status': 'error', 'msg': 'Thao tác quá nhanh'}, {'Retry-After': '0.02'})
                if len(times) == 1 else response({'status': 'success', 'data': []}))

        session = SimpleNamespace(post=post)
        with patch.object(policy, 'MIN_PURCHASE_INTERVAL', 0), patch.object(policy, 'MIN_FAILED_PURCHASE_INTERVAL', 0), patch.object(policy, 'MAX_PURCHASE_ATTEMPTS', 1):
            with self.assertRaises(policy.PurchaseRateLimitError):
                await policy.purchase_json(session, 'offline', {'id': 1, 'amount': 10}, 'offline-shop', 'same-key')
            await policy.purchase_json(session, 'offline', {'id': 1, 'amount': 10}, 'offline-shop', 'same-key')
        self.assertGreaterEqual(times[1] - times[0], 0.018)


class PurchaseRetryTimingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        policy._slots.clear()
        self.clock = 1000.0
        self.waits = []
        self.posts = []
        for provider in (GmailVipVn, OneTrieuMail):
            provider.clear_seen_cache()
        timer = patch.object(policy, 'time', SimpleNamespace(monotonic=lambda: self.clock))
        timer.start()
        self.addCleanup(timer.stop)
        sleeper = patch.object(policy.asyncio, 'sleep', side_effect=self.sleep)
        sleeper.start()
        self.addCleanup(sleeper.stop)

    async def sleep(self, delay):
        self.waits.append(delay)
        self.clock += delay

    def connection(self, replies):
        async def post(*args, **kwargs):
            self.posts.append(self.clock)
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return response(reply)
        return SimpleNamespace(post=post, close=AsyncMock())

    async def test_retry_after_has_ten_second_floor_and_honors_longer_shop_wait(self):
        for raw, expected in ((None, 10), ('0', 10), ('9', 10), ('20', 20), ('invalid', 10)):
            headers = {} if raw is None else {'Retry-After': raw}
            self.assertEqual(policy.retry_wait(response({}, headers), {}, 1), expected)

    async def test_each_rejected_rate_attempt_waits_ten_seconds_before_next_post(self):
        connection = self.connection([
            {'status': 'error', 'msg': 'Thao tác quá nhanh'},
            {'status': 'error', 'msg': 'Thao tác quá nhanh'},
            {'status': 'success', 'data': []},
        ])
        await policy.purchase_json(connection, 'offline', {'id': 1, 'amount': 10}, 'shop', 'key')
        self.assertEqual(self.posts, [1000, 1010, 1020])
        self.assertEqual(self.waits, [10, 10])

    async def test_proxy_recovery_time_counts_toward_ten_second_wait(self):
        old = self.connection([{'status': 'error', 'msg': 'Thao tác quá nhanh'}])
        new = self.connection([{'status': 'success', 'data': []}])
        async def recover():
            self.clock += 3
            return new
        await policy.purchase_json(old, 'offline', {'id': 1, 'amount': 10}, 'shop', 'key', on_rate_limit=recover)
        self.assertEqual(self.posts, [1000, 1010])
        self.assertEqual(self.waits, [7])

    async def test_stock_fallback_within_each_shop_waits_ten_seconds(self):
        for provider in (GmailVipVn, OneTrieuMail):
            policy._slots.clear()
            self.clock = 1000
            self.posts.clear()
            client = provider('offline')
            client._session = self.connection([
                {'status': 'error', 'msg': 'Hết hàng'},
                {'status': 'success', 'data': ['retry-timing@example.test|pw']},
            ])
            result = await client.buy_gmail_domain_info([1, 2], 1)
            self.assertEqual(len(result), 1)
            self.assertEqual(self.posts, [1000, 1010])

    async def test_cross_shop_stock_fallback_waits_before_each_new_request(self):
        from core.mail.providers.priority import buy_priority_batch, GMAILVIP, ONE_TRIEU
        first = self.connection([{'status': 'error', 'msg': 'Hết hàng'}])
        second = self.connection([{'status': 'error', 'msg': 'Hết hàng'}])
        third = self.connection([{'status': 'success', 'data': ['cross-retry@example.test|pw']}])
        with patch('core.mail.providers.gmailvip_vn.AsyncSession', side_effect=[first, third]), \
                patch('core.mail.providers.one_trieu_mail.AsyncSession', return_value=second):
            result = await buy_priority_batch('gmailvip:1,1trieumail:2,gmailvip:3',
                {GMAILVIP: 'offline', ONE_TRIEU: 'offline'}, 1)
        self.assertEqual(len(result), 1)
        self.assertEqual(self.posts, [1000, 1010, 1020])
        self.assertEqual(self.waits, [10, 10])

    async def test_final_rate_rejection_preserves_ten_second_cooldown_for_next_job(self):
        connection = self.connection([
            {'status': 'error', 'msg': 'Thao tác quá nhanh'},
            {'status': 'success', 'data': []},
        ])
        with patch.object(policy, 'MAX_PURCHASE_ATTEMPTS', 1):
            with self.assertRaises(policy.PurchaseRateLimitError):
                await policy.purchase_json(connection, 'offline', {'id': 1, 'amount': 1}, 'shop', 'key')
        await policy.purchase_json(connection, 'offline', {'id': 1, 'amount': 1}, 'shop', 'key')
        self.assertEqual(self.posts, [1000, 1010])

    async def test_lost_response_stops_but_next_explicit_request_respects_cooldown(self):
        connection = self.connection([TimeoutError('lost response'), {'status': 'success', 'data': []}])
        with self.assertRaisesRegex(ValueError, 'Không xác định'):
            await policy.purchase_json(connection, 'offline', {'id': 1, 'amount': 1}, 'shop', 'key')
        self.assertEqual(len(self.posts), 1)
        await policy.purchase_json(connection, 'offline', {'id': 1, 'amount': 1}, 'shop', 'key')
        self.assertEqual(self.posts, [1000, 1010])

    async def test_cancel_during_stock_wait_never_buys_next_shop(self):
        from core.mail.providers.priority import buy_priority_batch, GMAILVIP, ONE_TRIEU
        waiting = asyncio.Event()
        async def pause(delay):
            self.assertEqual(delay, 10)
            waiting.set()
            await asyncio.Event().wait()
        first = self.connection([{'status': 'error', 'msg': 'Hết hàng'}])
        with patch.object(policy.asyncio, 'sleep', side_effect=pause), \
                patch('core.mail.providers.gmailvip_vn.AsyncSession', return_value=first), \
                patch('core.mail.providers.one_trieu_mail.AsyncSession') as other:
            task = asyncio.create_task(buy_priority_batch('gmailvip:1,1trieumail:2',
                {GMAILVIP: 'offline', ONE_TRIEU: 'offline'}, 1))
            await asyncio.wait_for(waiting.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            other.assert_not_called()
        self.assertEqual(self.posts, [1000])


if __name__ == '__main__':
    unittest.main(verbosity=2)
