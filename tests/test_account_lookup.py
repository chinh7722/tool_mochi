"""Offline profile schema, safe storage, strict own-account reads and Google lifecycle."""
import asyncio
import csv
from dataclasses import asdict,replace
from decimal import Decimal
import io
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from curl_cffi.requests.errors import RequestsError
from core.platforms.mochichat.account_lookup import (AccountProfile,AccountProfileStore,AccountLookupError,
    parse_account_profile,lookup_account,export_profiles_csv,LOOKUP_CERT_SHA1,PROFILE_FIELDS)
from core.platforms.mochichat.constants import MOCHI_CERT_SHA1
from core.platforms.mochichat.fetcher import MochiChatFetcher,MochiAccountReadError
from core.proxy.models import ProxyModel

MODULE='core.platforms.mochichat.account_lookup'
STAMP='2026-11-02T12:00:00+07:00'


def profile(**values):
    base=parse_account_profile('User@example.test',{'id':'uid123','nickname':'Tên hiển thị','remainingQuota':'123.4500'},
        {'code':'ABC123'},updated_at=STAMP)
    return replace(base,**values)


def response(payload=None,status=200,exception=None):
    return SimpleNamespace(status_code=status,json=Mock(return_value=payload,side_effect=exception))


class ParserTests(unittest.TestCase):
    def test_exact_apk_fields_return_named_whitelisted_profile_only(self):
        result=profile()
        self.assertEqual(tuple(asdict(result)),PROFILE_FIELDS)
        self.assertEqual((result.user_id,result.display_name,result.invite_code,result.beans),
            ('uid123','Tên hiển thị','ABC123','123.45'))

    def test_real_zero_is_kept_and_no_unit_scaling_is_applied(self):
        for value,expected in ((0,'0'),('0.000','0'),(100,'100'),(Decimal('0.1234567890123456789'),'0.1234567890123456789'),
                ('1e3','1000'),('-12.500','-12.5'),('-0','0')):
            result=parse_account_profile('a@example.test',{'nickname':'A','remainingQuota':value},{'code':'CODE'})
            self.assertEqual(result.beans,expected)

    def test_missing_quota_is_not_invented_from_wallet_or_generic_balance(self):
        for payload in ({'nickname':'A'},{'nickname':'A','balance':999},{'nickname':'A','beans':999}):
            with self.subTest(payload_keys=tuple(payload)),self.assertRaises(AccountLookupError):
                parse_account_profile('a@example.test',payload,{'code':'CODE'})

    def test_null_boolean_nonfinite_or_malformed_quota_is_rejected(self):
        for value in (None,True,False,'',{},[],'NaN','Infinity','-Infinity','1,000','1_000','secret-value','1e1000000'):
            with self.subTest(value_type=type(value).__name__),self.assertRaises(AccountLookupError) as raised:
                parse_account_profile('a@example.test',{'nickname':'A','remainingQuota':value},{'code':'CODE'})
            self.assertNotIn('secret-value',str(raised.exception))

    def test_app_display_name_fallback_for_blank_null_or_missing_nickname(self):
        for payload in ({'remainingQuota':1},{'nickname':None,'remainingQuota':1},{'nickname':'   ','remainingQuota':1}):
            self.assertEqual(parse_account_profile('a@example.test',payload,{'code':'CODE'}).display_name,'Mochi User')

    def test_no_alternative_name_or_invite_field_guessing(self):
        result=parse_account_profile('a@example.test',{'displayName':'Incorrect source','remainingQuota':1},{'code':'CODE'})
        self.assertEqual(result.display_name,'Mochi User')
        for invite in ({'invite_code':'ABC123'},{'data':{'code':'ABC123'}},{'code':None},{'code':''},{'code':'not a code'}):
            with self.assertRaises(AccountLookupError):
                parse_account_profile('a@example.test',{'nickname':'A','remainingQuota':1},invite)

    def test_optional_user_id_stays_empty_if_not_available(self):
        self.assertEqual(parse_account_profile('a@example.test',{'nickname':'A','remainingQuota':1},{'code':'CODE'}).user_id,'')
        self.assertEqual(parse_account_profile('a@example.test',{'id':123,'nickname':'A','remainingQuota':1},{'code':'CODE'}).user_id,'123')

    def test_invalid_identity_and_structures_have_safe_errors(self):
        cases=(('',{},{}),('no-email',{},{}),('a@example.test',[],{}),('a@example.test',{'nickname':{},'remainingQuota':1},{'code':'CODE'}),
            ('a@example.test',{'id':True,'nickname':'A','remainingQuota':1},{'code':'CODE'}))
        for email,me,invite in cases:
            with self.assertRaises(AccountLookupError):parse_account_profile(email,me,invite)

    def test_timestamp_is_canonical_vietnam_time(self):
        value=parse_account_profile('a@example.test',{'nickname':'A','remainingQuota':1},{'code':'CODE'},
            updated_at='2026-11-02T05:00:00+00:00')
        self.assertEqual(value.updated_at,STAMP)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)
        self.store=AccountProfileStore(self.directory/'mochi_account_profiles.sqlite3')

    def test_upsert_is_casefolded_by_email_and_survives_reopening(self):
        self.store.upsert(profile())
        self.store.upsert(profile(email=' user@EXAMPLE.test ',beans='2.000'))
        rows=AccountProfileStore(self.store.path).list_profiles()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].beans,'2')

    def test_latest_updated_profiles_are_first(self):
        self.store.upsert(profile(email='old@example.test'))
        self.store.upsert(profile(email='new@example.test',updated_at='2026-11-02T12:05:00+07:00'))
        self.assertEqual(self.store.list_profiles()[0].email,'new@example.test')

    def test_invalid_new_snapshot_never_overwrites_good_profile(self):
        old=profile()
        self.store.upsert(old)
        with self.assertRaises(AccountLookupError):self.store.upsert(replace(old,beans='NaN'))
        self.assertEqual(self.store.list_profiles(),(old,))

    def test_storage_failure_keeps_previous_row_and_does_not_echo_raw_error(self):
        old=profile()
        self.store.upsert(old)
        with patch.object(self.store,'_connection',side_effect=OSError('token-secret')):
            with self.assertRaises(AccountLookupError) as raised:self.store.upsert(replace(old,beans='9'))
        self.assertNotIn('token-secret',str(raised.exception))
        self.assertEqual(self.store.list_profiles(),(old,))

    def test_csv_contains_only_profile_columns_and_prevents_formulas(self):
        dangerous=profile(email='=email@example.test',user_id='+123',display_name='=SUM(1,2)',beans='-12.5')
        self.store.upsert(dangerous)
        path=self.directory/'result.csv'
        self.store.export_csv(path)
        raw=path.read_bytes()
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'))
        rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
        self.assertEqual(rows[0],list(PROFILE_FIELDS))
        self.assertEqual(rows[1][:3],["'=email@example.test","'+123","'=SUM(1,2)"])
        self.assertEqual(rows[1][4],'-12.5')
        self.assertEqual(len(rows[1]),6)

    def test_export_can_include_explicit_in_memory_success_even_if_save_failed(self):
        memory=profile()
        path=self.directory/'memory.csv'
        self.store.export_csv(path,profiles=(memory,))
        self.assertIn(memory.invite_code,path.read_text(encoding='utf-8-sig'))
        self.assertEqual(self.store.list_profiles(),())

    def test_standalone_export_does_not_open_or_construct_a_profile_store(self):
        path=self.directory/'standalone.csv'
        with patch(f'{MODULE}.sqlite3.connect',side_effect=OSError('database unavailable')):
            export_profiles_csv(path,(profile(),))
        self.assertIn('ABC123',path.read_text(encoding='utf-8-sig'))

    def test_failed_atomic_export_preserves_original_file_and_removes_own_temp(self):
        path=self.directory/'result.csv'
        path.write_text('previous-data',encoding='utf-8')
        with patch(f'{MODULE}.os.replace',side_effect=OSError('password-secret')):
            with self.assertRaises(AccountLookupError) as raised:self.store.export_csv(path,profiles=(profile(),))
        self.assertNotIn('password-secret',str(raised.exception))
        self.assertEqual(path.read_text(encoding='utf-8'),'previous-data')
        self.assertEqual(list(self.directory.glob('*.tmp')),[])

    def test_store_open_read_and_export_errors_are_safe(self):
        with self.assertRaises(AccountLookupError):AccountProfileStore(None)
        with patch.object(self.store,'_connection',side_effect=OSError('token-secret')):
            with self.assertRaises(AccountLookupError) as raised:self.store.list_profiles()
            self.assertNotIn('token-secret',str(raised.exception))
        with self.assertRaises(AccountLookupError):self.store.export_csv(None,profiles=())


class FetcherReadTests(unittest.IsolatedAsyncioTestCase):
    def fetcher(self,replies,**kwargs):
        self.session=SimpleNamespace(get=AsyncMock(side_effect=replies))
        return MochiChatFetcher(access_token='token-secret',session=self.session,**kwargs)

    async def test_only_evidenced_gets_return_unwrapped_payloads_and_keep_decimal_decoder(self):
        me={'id':'uid','nickname':'A','remainingQuota':Decimal('123.4567890123456789')}
        first=response({'ok':True,'data':me})
        fetcher=self.fetcher([first,response({'ok':True,'data':{'code':'ABC123'}})],app_login_via_proxy=False)
        self.assertEqual(await fetcher.get_own_user_me(),me)
        self.assertEqual(await fetcher.get_own_invite_code(),{'code':'ABC123'})
        self.assertEqual([x.args[0] for x in self.session.get.await_args_list],
            ['https://mochi.lightchaser.xyz/api/user/me','https://mochi.lightchaser.xyz/api/invites/my-code'])
        first.json.assert_called_once_with(parse_float=Decimal)
        for call in self.session.get.await_args_list:
            self.assertEqual(call.kwargs['proxies'],{'http':'','https':''})
            self.assertTrue(call.kwargs['verify'])
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertEqual(call.kwargs['headers']['Authorization'],'Bearer token-secret')

    async def test_proxied_reads_use_selected_proxy_including_external_session_override(self):
        proxy=ProxyModel.from_string('127.0.0.1:8100:user:password')
        fetcher=self.fetcher([response({'ok':True,'data':{'code':'CODE'}})],proxy=proxy)
        await fetcher.get_own_invite_code()
        self.assertEqual(self.session.get.await_args.kwargs['proxies'],proxy.to_curl_cffi_dict())

    async def test_http_rejections_and_redirects_do_not_become_profile_success(self):
        for status in (301,401,403,429,500):
            reply=response({'ok':True,'data':{'nickname':'Fake','remainingQuota':99}},status=status)
            fetcher=self.fetcher([reply])
            with self.assertRaises(MochiAccountReadError) as raised:await fetcher.get_own_user_me()
            self.assertFalse(raised.exception.transport_error)
            reply.json.assert_not_called()

    async def test_business_rejections_missing_envelopes_and_invalid_json_are_rejected(self):
        for payload in ([],{}, {'ok':False,'error':'token-secret'}, {'ok':True,'data':None},
                {'ok':True,'success':False,'data':{}},{'ok':'true','data':{}},{'ok':True,'data':[]}):
            fetcher=self.fetcher([response(payload)])
            with self.assertRaises(MochiAccountReadError) as raised:await fetcher.get_own_user_me()
            self.assertNotIn('token-secret',str(raised.exception))
        fetcher=self.fetcher([response(exception=ValueError('password-secret'))])
        with self.assertRaises(MochiAccountReadError) as raised:await fetcher.get_own_user_me()
        self.assertNotIn('password-secret',str(raised.exception))

    async def test_missing_access_token_and_unknown_path_never_send_request(self):
        fetcher=self.fetcher([])
        fetcher.access_token=None
        with self.assertRaises(MochiAccountReadError):await fetcher.get_own_user_me()
        with self.assertRaises(MochiAccountReadError):await fetcher._get_own_account_data('/user/account')
        self.session.get.assert_not_awaited()

    async def test_network_and_certificate_errors_are_safe_and_distinguished(self):
        for code,transport in ((35,True),(56,True),(60,False)):
            fetcher=self.fetcher([RequestsError('token-secret password-secret',code=code)])
            with self.assertRaises(MochiAccountReadError) as raised:await fetcher.get_own_user_me()
            self.assertEqual(raised.exception.transport_error,transport)
            self.assertNotIn('secret',str(raised.exception))

    async def test_get_cancellation_is_not_converted_to_business_failure(self):
        fetcher=self.fetcher([asyncio.CancelledError()])
        with self.assertRaises(asyncio.CancelledError):await fetcher.get_own_user_me()

    async def test_cert_override_is_scoped_to_this_fetcher_and_global_defaults_are_unchanged(self):
        fetcher=self.fetcher([],auth_cert_sha1=LOOKUP_CERT_SHA1)
        self.assertEqual((await fetcher.get_bootstrap_config())['cert_sha1'],LOOKUP_CERT_SHA1)
        default=self.fetcher([])
        self.assertEqual((await default.get_bootstrap_config())['cert_sha1'],MOCHI_CERT_SHA1)


class LookupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fake=SimpleNamespace(login=AsyncMock(return_value={'ok':True,'token':'token-secret',
            'data':{'private':'password-secret'}}),get_own_user_me=AsyncMock(return_value={
                'id':'uid','nickname':'A','remainingQuota':25}),get_own_invite_code=AsyncMock(return_value={'code':'ABC123'}),
            close=AsyncMock(),redeem_invite_code=AsyncMock(),update_profile=AsyncMock())
        self.factory_patch=patch(f'{MODULE}.MochiChatFetcher',return_value=self.fake)
        self.factory=self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)
        self.release_patch=patch(f'{MODULE}.browser_manager.release_by_owner',new_callable=AsyncMock)
        self.release=self.release_patch.start()
        self.addCleanup(self.release_patch.stop)
        self.shutdown_patch=patch(f'{MODULE}.browser_manager.shutdown_for_loop',new_callable=AsyncMock)
        self.shutdown=self.shutdown_patch.start()
        self.addCleanup(self.shutdown_patch.stop)

    async def lookup(self,**kwargs):
        return await lookup_account('a@example.test','password-secret',owner_id='lookup-owner',**kwargs)

    async def test_success_has_only_six_public_fields_and_no_mutations(self):
        result=await self.lookup()
        self.assertIsInstance(result,AccountProfile)
        self.assertEqual(result.beans,'25')
        self.assertEqual(tuple(asdict(result)),PROFILE_FIELDS)
        self.assertNotIn('secret',repr(result))
        self.fake.redeem_invite_code.assert_not_awaited()
        self.fake.update_profile.assert_not_awaited()
        self.fake.close.assert_awaited_once()
        self.release.assert_awaited_once_with('lookup-owner')
        self.shutdown.assert_awaited_once()

    async def test_google_progress_raw_exception_urls_and_tokens_never_reach_callback(self):
        events=[]
        async def login(**kwargs):
            kwargs['on_step'](1,'password-secret https://user:pass@proxy?token=token-secret')
            kwargs['on_step'](2,'token-secret')
            return {'ok':True}
        self.fake.login.side_effect=login
        await self.lookup(on_progress=events.append)
        self.assertTrue(any('Google' in x for x in events))
        self.assertTrue(any('Mochi Chat' in x for x in events))
        self.assertFalse(any('secret' in x or 'https://' in x for x in events))

    async def test_verified_metadata_and_routes_are_per_lookup(self):
        proxy=ProxyModel.from_string('127.0.0.1:8100')
        await self.lookup(proxy=proxy,google_via_proxy=True,app_via_proxy=False,headless=False)
        settings=self.factory.call_args.kwargs
        self.assertEqual((settings['device'].app_version,settings['device'].app_build),('1.3.2','38'))
        self.assertEqual(settings['auth_cert_sha1'],LOOKUP_CERT_SHA1)
        self.assertIs(settings['proxy'],proxy)
        self.assertFalse(settings['app_login_via_proxy'])
        self.assertTrue(self.fake.login.await_args.kwargs['google_login_via_proxy'])
        self.assertFalse(self.fake.login.await_args.kwargs['headless'])

    async def test_invalid_inputs_and_missing_requested_proxy_do_not_allocate_fetcher(self):
        for email,password,owner,kwargs in (('bad','pw','owner',{}),('a@example.test','','owner',{}),
                ('a@example.test','pw','',{}),('a@example.test','pw','owner',{'google_via_proxy':True})):
            with self.assertRaises(AccountLookupError):
                await lookup_account(email,password,owner_id=owner,**kwargs)
        self.factory.assert_not_called()

    async def test_login_rejection_does_not_read_or_echo_raw_login_body(self):
        self.fake.login.return_value={'ok':False,'error':'password-secret','transport_error':True}
        with self.assertRaises(AccountLookupError) as raised:await self.lookup()
        self.assertTrue(raised.exception.transport_error)
        self.assertNotIn('secret',str(raised.exception))
        self.fake.get_own_user_me.assert_not_awaited()
        self.fake.close.assert_awaited_once()
        self.shutdown.assert_awaited_once()

    async def test_read_schema_and_unknown_login_failures_are_safe(self):
        self.fake.get_own_user_me.return_value={'nickname':'A','token':'token-secret'}
        with self.assertRaises(AccountLookupError) as raised:await self.lookup()
        self.assertNotIn('token-secret',str(raised.exception))
        self.fake.login.side_effect=RuntimeError('password-secret')
        with self.assertRaises(AccountLookupError) as raised:await self.lookup()
        self.assertNotIn('password-secret',str(raised.exception))

    async def test_strict_read_error_preserves_safe_transport_classification(self):
        self.fake.get_own_user_me.side_effect=MochiAccountReadError('Không kết nối được Mochi.',transport_error=True)
        with self.assertRaises(AccountLookupError) as raised:await self.lookup()
        self.assertTrue(raised.exception.transport_error)
        self.fake.get_own_invite_code.assert_not_awaited()

    async def test_cancelled_login_propagates_and_still_releases_browser_resources(self):
        self.fake.login.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await self.lookup()
        self.fake.close.assert_awaited_once()
        self.release.assert_awaited_once_with('lookup-owner')
        self.shutdown.assert_awaited_once()

    async def test_cancellation_during_http_close_still_releases_owner_and_loop(self):
        self.fake.close.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await self.lookup()
        self.release.assert_awaited_once_with('lookup-owner')
        self.shutdown.assert_awaited_once()

    async def test_cleanup_failures_do_not_expose_raw_internal_errors(self):
        self.fake.close.side_effect=RuntimeError('password-secret')
        self.release.side_effect=RuntimeError('token-secret')
        self.shutdown.side_effect=RuntimeError('proxy-secret')
        result=await self.lookup()
        self.assertEqual(result.invite_code,'ABC123')
        self.shutdown.assert_awaited_once()


if __name__=='__main__':
    unittest.main(verbosity=2)
