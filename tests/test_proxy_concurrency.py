"""Offline scheduling regressions for filling configured proxy account slots."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import asyncio
from collections import Counter
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.platforms.mochichat.runners.invite_referral import (
    MochiAccountItem, MochiAccountResult, MochiInviteRunner,
)
from core.proxy.broker import ProxyBroker
from core.proxy.client import ProxyPoolClient
from core.proxy.health import ProxyHealthResult
from core.proxy.models import ProxyModel

RUNNER = 'core.platforms.mochichat.runners.invite_referral'


def accounts(count):
    return tuple(MochiAccountItem(str(i), f'offline{i}@example.test', 'offline-password') for i in range(count))


class ProxyConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def runner(self, proxy_count, slots=1, manual=1, **kwargs):
        models = [ProxyModel.from_string(f'127.0.0.1:{28000 + i}') for i in range(proxy_count)]
        broker = ProxyBroker(models, account_concurrency=slots)
        client = ProxyPoolClient(broker=broker)
        probe = AsyncMock(return_value=ProxyHealthResult(True, 'offline', 'ip'))
        logs = []
        runner = MochiInviteRunner(proxy_client=client, concurrency=manual,
            mail_batch_dir=self.directory.name, proxy_probe=probe,
            log_sink=lambda event: logs.append(event.message), **kwargs)
        return runner, broker, logs

    def start(self, runner, target, given):
        task = asyncio.create_task(runner.run(self.id(), 'TEST', target, accounts=given))

        async def cancel_unfinished():
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        self.addAsyncCleanup(cancel_unfinished)
        return task

    async def assert_capacity_filled(self, proxy_count, slots, manual, target, expected):
        runner, broker, logs = self.runner(proxy_count, slots, manual)
        active = Counter()
        peak_by_proxy = Counter()
        reached = asyncio.Event()
        finish = asyncio.Event()
        peak = 0
        processed = []

        async def process(job, code, item, proxy, owner, **kwargs):
            nonlocal peak
            key = proxy.server if proxy else 'direct'
            active[key] += 1
            peak_by_proxy[key] = max(peak_by_proxy[key], active[key])
            peak = max(peak, sum(active.values()))
            processed.append(item.identifier)
            if sum(active.values()) == expected:
                reached.set()
            try:
                await finish.wait()
                return MochiAccountResult(item.identifier, True, 'success')
            finally:
                active[key] -= 1

        runner._process_account = process
        task = self.start(runner, target, accounts(target))
        await asyncio.wait_for(reached.wait(), 1)
        self.assertEqual(sum(active.values()), expected)
        self.assertEqual(runner.current_concurrency(), proxy_count * slots if proxy_count else manual)
        if proxy_count:
            self.assertEqual(len([n for n in active.values() if n]), min(proxy_count, expected))
            self.assertTrue(all(n <= slots for n in peak_by_proxy.values()))
        finish.set()
        summary = await asyncio.wait_for(task, 2)
        self.assertEqual(summary.status, 'completed')
        self.assertEqual(summary.attempted_count, target)
        self.assertEqual(len(set(processed)), target)
        self.assertEqual(peak, expected)
        self.assertTrue(any(f'Số luồng tối đa: {runner.current_concurrency()}' in line for line in logs))
        self.assertFalse(broker._leases)
        self.assertTrue(all(state.in_use == 0 for state in broker._proxies.values()))

    async def test_five_proxies_one_slot_fill_five_despite_manual_one(self):
        await self.assert_capacity_filled(5, 1, 1, 10, 5)

    async def test_five_proxies_two_slots_fill_ten_despite_manual_one(self):
        await self.assert_capacity_filled(5, 2, 1, 10, 10)

    async def test_manual_fifty_does_not_overschedule_five_proxy_slots(self):
        await self.assert_capacity_filled(5, 1, 50, 10, 5)

    async def test_remaining_target_caps_workers_below_proxy_capacity(self):
        await self.assert_capacity_filled(5, 1, 1, 3, 3)

    async def test_no_proxies_keeps_manual_direct_limit(self):
        await self.assert_capacity_filled(0, 1, 3, 6, 3)

    def test_duplicate_static_and_rotating_identities_do_not_inflate_capacity(self):
        static = ProxyBroker()
        static.add_proxy(ProxyModel.from_string('127.0.0.1:28901'), max_slots=3)
        static.add_proxy(ProxyModel.from_string('http://127.0.0.1:28901'), max_slots=1)
        self.assertEqual(static.account_capacity, 1)

        rotating = ProxyBroker()
        rotating.add_proxy(ProxyModel.from_string('127.0.0.1:28902', rotation_url='same-offline-key'), max_slots=2)
        rotating.add_proxy(ProxyModel.from_string('127.0.0.1:28903', rotation_url='same-offline-key'), max_slots=1)
        rotating.add_proxy(ProxyModel.from_string('127.0.0.1:28904'), max_slots=2)
        self.assertEqual(rotating.account_capacity, 3)

    async def test_a_freed_slot_refills_while_other_accounts_are_still_running(self):
        runner, broker, _ = self.runner(5)
        all_started, replacement_started = asyncio.Event(), asyncio.Event()
        finish_first, finish_rest = asyncio.Event(), asyncio.Event()
        active = Counter()
        proxies = {}
        started = []

        async def process(job, code, item, proxy, owner, **kwargs):
            active[proxy.server] += 1
            self.assertLessEqual(active[proxy.server], 1)
            proxies[item.identifier] = proxy.server
            started.append(item.identifier)
            if len(started) == 5:
                all_started.set()
            if item.identifier == '5':
                replacement_started.set()
            try:
                await (finish_first if item.identifier == '0' else finish_rest).wait()
                return MochiAccountResult(item.identifier, True, 'success')
            finally:
                active[proxy.server] -= 1

        runner._process_account = process
        task = self.start(runner, 6, accounts(6))
        await asyncio.wait_for(all_started.wait(), 1)
        finish_first.set()
        await asyncio.wait_for(replacement_started.wait(), 1)
        self.assertFalse(finish_rest.is_set())
        self.assertEqual(sum(active.values()), 5)
        self.assertEqual(proxies['5'], proxies['0'])
        finish_rest.set()
        self.assertEqual((await asyncio.wait_for(task, 2)).success_count, 6)
        self.assertFalse(broker._leases)

    async def test_one_batch_purchase_fills_five_before_first_account_finishes(self):
        runner, broker, _ = self.runner(5,probe_invite_first=False)
        shop = SimpleNamespace(buy_gmail_domain_info=AsyncMock(return_value=[
            SimpleNamespace(username=f'batch{i}@example.test', password='offline-password') for i in range(10)]))
        context = AsyncMock()
        context.__aenter__.return_value = shop
        reached, finish = asyncio.Event(), asyncio.Event()
        active, peak, started = 0, 0, 0

        async def process(job, code, item, proxy, owner, **kwargs):
            nonlocal active, peak, started
            active += 1
            started += 1
            peak = max(peak, active)
            if active == 5:
                reached.set()
            try:
                await finish.wait()
                return MochiAccountResult(item.identifier, True, 'success')
            finally:
                active -= 1

        runner._process_account = process
        with patch(f'{RUNNER}.GmailVipVn', return_value=context):
            task = self.start(runner, 10, None)
            await asyncio.wait_for(reached.wait(), 2)
            self.assertEqual(active, 5)
            self.assertFalse(finish.is_set())
            shop.buy_gmail_domain_info.assert_awaited_once_with(None, amount=10)
            finish.set()
            self.assertEqual((await asyncio.wait_for(task, 2)).success_count, 10)
        self.assertEqual((peak, started), (5, 10))
        self.assertFalse(broker._leases)

    async def test_mail_only_proxy_preserves_manual_account_worker_limit(self):
        runner, broker, _ = self.runner(5, manual=2, mail_via_proxy=True,
            google_login_via_proxy=False, app_login_via_proxy=False, redeem_via_proxy=False)
        self.assertEqual(runner.current_concurrency(), 2)
        reached, finish = asyncio.Event(), asyncio.Event()
        active, peak = 0, 0

        async def process(job, code, item, proxy, owner, **kwargs):
            nonlocal active, peak
            self.assertIsNone(proxy)
            active += 1
            peak = max(peak, active)
            if active == 2:
                reached.set()
            try:
                await finish.wait()
                return MochiAccountResult(item.identifier, True, 'success')
            finally:
                active -= 1

        runner._process_account = process
        task = self.start(runner, 6, accounts(6))
        await asyncio.wait_for(reached.wait(), 1)
        finish.set()
        self.assertEqual((await asyncio.wait_for(task, 2)).success_count, 6)
        self.assertEqual(peak, 2)
        runner.proxy_probe.assert_not_awaited()
        self.assertFalse(broker._leases)

    async def test_busy_slot_waits_and_does_not_reduce_capacity_to_zero(self):
        runner, broker, _ = self.runner(1, manual=50)
        external = await broker.lease('offline-external-holder')
        self.addAsyncCleanup(broker.release, external)
        requested, started, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_lease = runner.proxy_client.lease

        async def lease(*args, **kwargs):
            requested.set()
            return await original_lease(*args, **kwargs)

        async def process(job, code, item, proxy, owner, **kwargs):
            started.set()
            await finish.wait()
            return MochiAccountResult(item.identifier, True, 'success')

        runner.proxy_client.lease = lease
        runner._process_account = process
        task = self.start(runner, 1, accounts(1))
        await asyncio.wait_for(requested.wait(), 1)
        self.assertEqual(runner.current_concurrency(), 1)
        self.assertFalse(task.done())
        await broker.release(external)
        await asyncio.wait_for(started.wait(), 1)
        finish.set()
        self.assertEqual((await asyncio.wait_for(task, 2)).status, 'completed')


if __name__ == '__main__':
    unittest.main(verbosity=2)
