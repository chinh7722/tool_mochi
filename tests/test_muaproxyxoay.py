"""Offline provider, broker and Qt integration for muaproxyxoay.net."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from core.proxy import rotators
from core.proxy.broker import ProxyBroker, proxy_activity, proxy_maintenance, update_proxy_endpoint
from core.proxy.models import ProxyModel
from core.proxy.protocol import ProxyReleaseOutcome
from core.proxy.rotators import (MuaProxyXoayProvider, ProxyVnProvider, RotationResult,
    make_rotating_provider, provider_shared_identity, rotation_key_reference)
from gui.pages.proxy import ProxyPage, _ProxyRotateThread, _ProxyImportDialog
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread


def reply(data, status=200, headers=None):
    return SimpleNamespace(status_code=status, json=lambda: data, headers=headers or {})


def success(host='192.0.2.20', http=32001, **extra):
    row = {'public_ip': host, 'public_origin_ip': '192.0.2.10',
        'http_port': http, 'https_port': 32002, 'socks_port': 32003,
        'proxy_auth_type': 'IP_ADDRESS', 'proxy_auth_ip': '127.0.0.1'}
    row.update(extra)
    return {'Status': 'Success', 'Message': 'Thành công', 'Data': [row]}


@contextmanager
def responses(*items):
    get = AsyncMock(side_effect=items)
    session = SimpleNamespace(get=get)
    context = AsyncMock()
    context.__aenter__.return_value = session
    with patch.object(rotators, 'AsyncSession', return_value=context) as factory:
        yield get, factory


class MuaProviderTests(unittest.IsolatedAsyncioTestCase):
    def provider(self, endpoint='192.0.2.10:31000:alice:p@ss:word', **kw):
        return MuaProxyXoayProvider('offline-' + self._testMethodName, current_endpoint=endpoint, **kw)

    async def test_current_authenticated_endpoint_is_reused_without_calling_rotation_api(self):
        provider = self.provider()
        with responses() as (get, factory):
            result = await provider.get_current_or_rotate()
        factory.assert_not_called()
        get.assert_not_awaited()
        self.assertTrue(result.success)
        self.assertEqual((result.proxy.host, result.proxy.port, result.proxy.username, result.proxy.password),
            ('192.0.2.10', 31000, 'alice', 'p@ss:word'))

    async def test_success_uses_documented_method_parameters_and_keeps_password(self):
        provider = self.provider()
        with responses(reply(success())) as (get, factory):
            result = await provider.get_current_or_rotate(force_rotate=True)
        self.assertTrue(result.success)
        self.assertEqual(get.call_args.args[0], MuaProxyXoayProvider.API_ENDPOINT)
        self.assertEqual(get.call_args.kwargs['params'], {'package_api_key': provider.api_key})
        self.assertEqual(factory.call_args.kwargs['trust_env'], False)
        self.assertEqual((result.proxy.host, result.proxy.port, result.proxy.username, result.proxy.password),
            ('192.0.2.20', 32001, 'alice', 'p@ss:word'))
        self.assertIn('alice:p%40ss%3Aword@', result.proxy.to_url())

    async def test_optional_app_id_and_url_normalize_without_changing_shared_package_identity(self):
        raw = 'https://api.muaproxyxoay.net/user/package/changeip?app_id=1000&package_api_key=offline-package'
        provider = MuaProxyXoayProvider(raw, current_endpoint='192.0.2.1:8080:u:p')
        other = MuaProxyXoayProvider('offline-package', app_id=42)
        self.assertEqual(provider_shared_identity(provider), provider_shared_identity(other))
        with responses(reply(success())) as (get, _):
            result = await provider.get_current_or_rotate(force_rotate=True)
        self.assertTrue(result.success)
        self.assertEqual(get.call_args.kwargs['params'], {'package_api_key': 'offline-package', 'app_id': 1000})
        self.assertEqual(make_rotating_provider(provider.key_reference()).provider_id, 'muaproxyxoay')

    async def test_https_and_socks_keep_the_selected_protocol_and_authentication(self):
        for scheme, expected_port in (('https', 32002), ('socks5', 32003), ('socks5h', 32003)):
            provider = self.provider(f'{scheme}://alice:p%40ss@192.0.2.1:31000')
            with responses(reply(success())):
                result = await provider.get_current_or_rotate(force_rotate=True)
            self.assertEqual((result.proxy.protocol, result.proxy.port, result.proxy.password),
                (scheme, expected_port, 'p@ss'))

    async def test_wait_180_is_cooldown_not_a_broken_key_and_current_proxy_remains_usable(self):
        provider = self.provider()
        with responses(reply({'Status': 'Error', 'Message': 'Vui lòng chờ sau 180 giây'})) as (get, _):
            result = await provider.get_current_or_rotate(force_rotate=True)
            current = await provider.get_current_or_rotate()
        self.assertFalse(result.success)
        self.assertTrue(result.is_cooling)
        self.assertEqual(result.wait_seconds, 180)
        self.assertTrue(current.success)
        self.assertEqual(get.await_count, 1)

    async def test_waiting_honors_full_180_seconds_before_next_api_request_without_real_sleep(self):
        clock = [100.0]
        ticks, progress = [], []
        async def sleep(seconds):
            ticks.append(seconds)
            clock[0] += seconds
            await asyncio.sleep(0)
        fake_time = SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 10000 + clock[0])
        fake_asyncio = SimpleNamespace(timeout=asyncio.timeout, sleep=sleep)
        provider = self.provider()
        with responses(reply({'Status': 'Error', 'Message': 'Vui lòng chờ sau 180 giây'}),
                reply(success())) as (get, _), patch.object(rotators, 'time', fake_time), \
                patch.object(rotators, 'asyncio', fake_asyncio):
            result = await provider.rotate_and_wait(timeout=300, on_progress=lambda *args: progress.append(args))
        self.assertTrue(result.success)
        self.assertEqual(sum(ticks), 180)
        self.assertEqual(get.await_count, 2)
        self.assertTrue(progress)

    async def test_deadline_does_not_lease_the_failed_current_ip_or_retry_early(self):
        provider = self.provider()
        with responses(reply({'Status': 'Error', 'Message': 'Vui lòng chờ sau 180 giây'})) as (get, _):
            result = await provider.get_current_or_rotate(force_rotate=True, wait_until_rotated=True, max_wait_seconds=.025)
        self.assertFalse(result.success)
        self.assertTrue(result.is_cooling)
        self.assertGreater(result.wait_seconds, 179)
        self.assertEqual(get.await_count, 1)

    async def test_invalid_key_takes_precedence_over_wait_text(self):
        provider = self.provider()
        with responses(reply({'Status': 'Error', 'Message': 'Key không tồn tại, chờ hỗ trợ'})) as (get, _):
            result = await provider.get_current_or_rotate(force_rotate=True, wait_until_rotated=True)
        self.assertFalse(result.success)
        self.assertFalse(result.is_cooling)
        get.assert_awaited_once()

    async def test_auth_http_errors_and_transient_errors_do_not_leak_query_key(self):
        for code in (401, 403, 500):
            provider = self.provider()
            with responses(reply({}, status=code)) as (get, _):
                result = await provider.get_current_or_rotate(force_rotate=True)
            self.assertFalse(result.success)
            self.assertNotIn(provider.api_key, result.error)
            get.assert_awaited_once()

    async def test_http_429_honors_retry_after(self):
        provider = self.provider()
        with responses(reply({}, status=429, headers={'Retry-After': '180'})):
            result = await provider.get_current_or_rotate(force_rotate=True)
        self.assertTrue(result.is_cooling)
        self.assertEqual(result.wait_seconds, 180)

    async def test_malformed_success_never_silently_loses_credentials_or_retries_rotation(self):
        invalid_rows = [success(http=0), success(host='not-an-ip'), {'Status': 'Success', 'Data': 'bad'}]
        for data in invalid_rows:
            provider = self.provider()
            with responses(reply(data)) as (get, _):
                result = await provider.get_current_or_rotate(force_rotate=True, wait_until_rotated=True)
            self.assertFalse(result.success)
            self.assertEqual(provider._current_proxy.password, 'p@ss:word')
            get.assert_awaited_once()

    async def test_multiple_proxy_rows_select_current_port_and_do_not_pick_an_arbitrary_endpoint(self):
        data = success(http=31000)
        data['Data'].append(dict(data['Data'][0], public_ip='192.0.2.99', http_port=31999))
        provider = self.provider()
        with responses(reply(data)):
            result = await provider.get_current_or_rotate(force_rotate=True)
        self.assertEqual(result.proxy.host, '192.0.2.20')
        provider = self.provider('192.0.2.200:9999:u:p')
        with responses(reply(data)):
            result = await provider.get_current_or_rotate(force_rotate=True)
        self.assertFalse(result.success)

    async def test_key_only_password_package_requires_explicit_authenticated_endpoint(self):
        provider = self.provider(endpoint=None)
        with responses(reply(success(proxy_auth_type='USERNAME_PASSWORD'))):
            result = await provider.get_current_or_rotate()
        self.assertFalse(result.success)
        self.assertIn('ip:port:user:pass', result.error)

    async def test_invalid_provider_urls_and_app_ids_are_rejected_without_network(self):
        values = ('https://other.test/user/package/changeip?package_api_key=offline',
            'https://api.muaproxyxoay.net/wrong?package_api_key=offline',
            'https://api.muaproxyxoay.net/user/package/changeip',
            'https://api.muaproxyxoay.net/user/package/changeip?package_api_key=offline&app_id=-1')
        with patch.object(rotators, 'AsyncSession') as network:
            for value in values:
                with self.subTest(value=value), self.assertRaises(ValueError):
                    MuaProxyXoayProvider(value)
        network.assert_not_called()

    async def test_cancelled_request_propagates_without_retries(self):
        provider = self.provider()
        with responses(asyncio.CancelledError()) as (get, _):
            with self.assertRaises(asyncio.CancelledError):
                await provider.rotate_and_wait()
        get.assert_awaited_once()

    async def test_broker_reuses_then_rotates_after_network_failure_and_preserves_auth(self):
        key = 'offline-' + self._testMethodName
        reference = rotation_key_reference(key, 'muaproxyxoay')
        proxy = ProxyModel.from_string('192.0.2.10:31000:alice:p@ss', rotation_url=reference)
        broker = ProxyBroker(static_proxies=[proxy], allow_direct_without_proxies=False)
        with responses(reply(success())) as (get, _):
            first = await broker.lease('first')
            get.assert_not_awaited()
            await broker.release(first, outcome=ProxyReleaseOutcome.NETWORK_ERROR, success=False)
            second = await broker.lease('second', timeout=1)
            self.assertEqual((second.proxy.host, second.proxy.username, second.proxy.password),
                ('192.0.2.20', 'alice', 'p@ss'))
            self.assertEqual(second.metadata['provider_status'], 'Success')
            get.assert_awaited_once()
            await broker.release(second)

    async def test_same_key_two_providers_have_independent_slots_and_rotation_locks(self):
        key = 'offline-' + self._testMethodName
        reference = rotation_key_reference(key, 'muaproxyxoay')
        self.assertNotEqual(provider_shared_identity(make_rotating_provider(key)),
            provider_shared_identity(make_rotating_provider(reference)))
        endpoint = '192.0.2.10:31000:u:p'
        broker = ProxyBroker(static_proxies=[ProxyModel.from_string(endpoint, rotation_url=key),
            ProxyModel.from_string(endpoint, rotation_url=reference)], allow_direct_without_proxies=False)
        self.assertEqual(broker.account_capacity, 2)
        with proxy_maintenance(reference):
            self.assertTrue(proxy_activity(reference)[1])
            self.assertFalse(proxy_activity(key)[1])
            lease = await broker.lease('other-provider', timeout=.1)
            self.assertEqual(lease.proxy.provider, 'static')
            await broker.release(lease)


class MuaGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(str(Path(self.temp.name) / 'settings.ini'), QSettings.IniFormat)
        self.page = ProxyPage(settings=self.settings)
        self.addCleanup(self.page.close)
        self.addCleanup(self.page.shutdown_workers)

    def source(self, value):
        self.page.combo_import_provider.setCurrentIndex(self.page.combo_import_provider.findData(value))

    def test_selected_provider_keeps_input_format_password_and_rotator_metadata(self):
        self.source('muaproxyxoay')
        with patch.object(rotators, 'AsyncSession') as network:
            ids, duplicates, invalid = self.page._import_proxy_lines(['192.0.2.10:31000:u:p@ss|offline-package'])
        network.assert_not_called()
        self.assertEqual((len(ids), duplicates, invalid), (1, 0, []))
        item = self.page._proxies[ids[0]]
        self.assertEqual((item['endpoint'], item['key_xoay'], item['provider_id']),
            ('192.0.2.10:31000:u:p@ss', 'offline-package', 'muaproxyxoay'))
        self.assertIn('muaproxyxoay.net', item['provider'])
        endpoint, keyref = self.page.get_active_proxies()[0]
        self.assertEqual(make_rotating_provider(keyref, current_endpoint=endpoint).api_key, 'offline-package')

    def test_paste_dialog_provider_choice_is_applied_without_automatic_rotation(self):
        self.source('muaproxyxoay')
        dialog = _ProxyImportDialog(self.page)
        self.assertEqual(dialog.combo_provider.currentData(), 'muaproxyxoay')
        dialog.txt_input.setPlainText('192.0.2.10:31000:u:p|offline-paste-key')
        dialog.chk_auto_check.setChecked(False)
        with patch('gui.pages.proxy._ProxyImportDialog', return_value=dialog), \
                patch.object(dialog, 'exec', return_value=QDialog.DialogCode.Accepted), \
                patch.object(QMessageBox, 'information'), patch.object(rotators, 'AsyncSession') as network:
            self.page.btn_import.click()
        network.assert_not_called()
        self.assertEqual(next(iter(self.page._proxies.values()))['provider_id'], 'muaproxyxoay')
        dialog.close()

    def test_txt_roundtrip_mixed_providers_is_unambiguous_even_when_selector_is_mua(self):
        text = '192.0.2.10:31000:u:p|same-offline-key'
        self.source('proxyvn')
        self.page._import_proxy_lines([text])
        self.source('muaproxyxoay')
        self.page._import_proxy_lines([text])
        self.assertEqual(len(self.page._proxies), 2)
        lines = self.page._serialize_proxy_lines()
        self.assertTrue(any('proxyxoay.shop' in line for line in lines))
        self.assertTrue(any('api.muaproxyxoay.net' in line for line in lines))
        fresh_settings = QSettings(str(Path(self.temp.name) / 'fresh.ini'), QSettings.IniFormat)
        fresh = ProxyPage(settings=fresh_settings)
        try:
            fresh.combo_import_provider.setCurrentIndex(1)
            ids, duplicates, invalid = fresh._import_proxy_lines(lines)
            self.assertEqual((len(ids), duplicates, invalid), (2, 0, []))
            self.assertEqual({item['provider_id'] for item in fresh._proxies.values()}, {'proxyvn', 'muaproxyxoay'})
        finally:
            fresh.shutdown_workers()
            fresh.close()

    def test_saved_mua_provider_is_kept_and_legacy_proxyvn_not_reclassified_by_import_preference(self):
        self.settings.setValue('proxy/import_provider', 'muaproxyxoay')
        legacy = self.page._parse_proxy_line('192.0.2.1:30001|old-offline-key', provider_id='proxyvn')
        legacy.pop('provider_id')
        mua = self.page._parse_proxy_line('192.0.2.2:30002:u:p|new-offline-key', provider_id='muaproxyxoay')
        self.settings.setValue('proxy/managed_list', json.dumps([legacy, mua]))
        fresh = ProxyPage(settings=self.settings)
        try:
            sources = {item['key_xoay']: item['provider_id'] for item in fresh._proxies.values()}
            self.assertEqual(sources, {'old-offline-key': 'proxyvn', 'new-offline-key': 'muaproxyxoay'})
        finally:
            fresh.shutdown_workers()
            fresh.close()

    def test_mochi_job_and_settings_fallback_construct_mua_broker_with_five_slots(self):
        self.source('muaproxyxoay')
        self.page._import_proxy_lines([f'192.0.2.{i + 1}:{31000 + i}:u:p|offline-pkg-{i}' for i in range(5)])
        self.settings.setValue('proxy/proxyvn_enabled', True)
        mochi = MochiChatPage(settings=self.settings, proxy_page=self.page)
        fallback = MochiChatPage(settings=self.settings)
        try:
            self.assertEqual(fallback._configured_proxy_count(), 5)
            self.assertEqual(mochi.spin_concurrency.value(), 5)
            mochi.input_code.setText('TEST')
            mochi.input_target_points.setText('1000')
            mochi.input_api_key.setText('offline-shop')
            mochi.chk_mail_via_proxy.setChecked(False)
            with patch.object(MochiWorkerThread, 'start'), patch.object(rotators, 'AsyncSession') as network:
                job = mochi._on_start_clicked()
            self.assertIsNotNone(job)
            network.assert_not_called()
            runner = mochi._workers[job].runner
            self.assertEqual(runner.current_concurrency(), 5)
            states = list(runner.proxy_client.broker._proxies.values())
            self.assertTrue(all(isinstance(state.provider, MuaProxyXoayProvider) for state in states))
            self.assertTrue(all(state.model.username == 'u' and state.model.password == 'p' for state in states))
        finally:
            fallback.close()
            mochi.close()

    def test_rotation_worker_uses_mua_api_and_keeps_health_check_inside_the_provider_lock(self):
        key = rotation_key_reference('offline-worker-package', 'muaproxyxoay')
        model = ProxyModel.from_string('192.0.2.20:32001:u:p')
        provider = SimpleNamespace(provider_id='muaproxyxoay', rotate_and_wait=AsyncMock(return_value=RotationResult(True, model)))
        with patch('gui.pages.proxy.make_rotating_provider', return_value=provider) as factory, \
                patch('gui.pages.proxy.probe_proxy_connection', AsyncMock(return_value=SimpleNamespace(success=True))):
            worker = _ProxyRotateThread('test', key, '192.0.2.10:31000:u:p')
            result, _latency = asyncio.run(worker.factory())
        self.assertTrue(result.success)
        factory.assert_called_once_with(key, current_endpoint='192.0.2.10:31000:u:p')
        self.assertEqual(provider.rotate_and_wait.call_args.kwargs['timeout'], 300)

    def test_runtime_updates_do_not_mix_same_literal_key_between_providers(self):
        bare = 'offline-' + self._testMethodName
        self.source('proxyvn')
        old = self.page._parse_proxy_line('192.0.2.10:31000:u:old|' + bare)
        self.source('muaproxyxoay')
        mua = self.page._parse_proxy_line('192.0.2.11:31001:u:new|' + bare)
        self.page._proxies = {item['id']: item for item in (old, mua)}
        reference = self.page._item_key_reference(mua)
        update_proxy_endpoint(reference, ProxyModel.from_string('192.0.2.99:32001:u:new'))
        self.page._refresh_runtime_status()
        self.assertIn('192.0.2.99', mua['endpoint'])
        self.assertEqual(old['endpoint'], '192.0.2.10:31000:u:old')

    def test_gui_cannot_rotate_a_mua_key_while_a_job_holds_its_proxy(self):
        reference = rotation_key_reference('offline-' + self._testMethodName, 'muaproxyxoay')
        endpoint = '192.0.2.10:31000:u:p'
        broker = ProxyBroker(static_proxies=[ProxyModel.from_string(endpoint, rotation_url=reference)],
            allow_direct_without_proxies=False)
        async def scenario():
            lease = await broker.lease('active-job')
            try:
                with patch.object(rotators, 'AsyncSession') as network:
                    worker = _ProxyRotateThread('test', reference, endpoint)
                    result, _ = await worker.factory()
                network.assert_not_called()
                self.assertTrue(result.is_cooling)
                self.assertFalse(result.success)
            finally:
                await broker.release(lease)
        asyncio.run(scenario())


if __name__ == '__main__':
    unittest.main(verbosity=2)
