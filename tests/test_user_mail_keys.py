"""Private operator mail keys, shared source controls and strict credential isolation."""
import asyncio
from dataclasses import asdict
from pathlib import Path
import os
import sys
import tempfile
from datetime import timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication,QLineEdit
from core.finance import MailPurchaseReceipt
from core.mail.providers.priority import GMAILVIP,ONE_TRIEU,MULTI_SHOP,buy_priority_batch,MailProductUnavailable
from core.telegram_bot import (TelegramAccess,TelegramBotService,TelegramJobRequest,
    TelegramControlRequest,parse_control_command)
from core.telegram_users import TelegramUserStore,LOCAL_USER_ID
import test_job_queue as queue_fixtures
from test_job_scheduling import future
from test_telegram_bot import update


def pair(prefix):
    return {GMAILVIP:prefix+'-gmailvip-key',ONE_TRIEU:prefix+'-1trieumail-key'}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=TelegramUserStore(Path(self.temp.name)/'users.sqlite3')
        self.store.upsert_user(123,'Admin','admin')
        self.store.upsert_user(234,'Partner','partner')

    def save(self,chat_id=123,prefix='admin'):
        keys=pair(prefix)
        self.store.set_user_mail_keys(chat_id,keys[GMAILVIP],keys[ONE_TRIEU])
        return keys

    def test_two_roles_have_independent_pairs_that_survive_reopening(self):
        a,b=self.save(),self.save(234,'partner')
        reopened=TelegramUserStore(self.store.path)
        self.assertEqual(reopened.get_user_mail_keys(123,require_complete=True),a)
        self.assertEqual(reopened.get_user_mail_keys(234,require_complete=True),b)

    def test_partial_empty_invalid_or_overlong_save_keeps_old_pair(self):
        old=self.save()
        for a,b in (('new',''),('','new'),('bad key','new'),('https://shop.test/?api_key=secret','new'),('x'*2049,'new')):
            with self.subTest(length=len(a)),self.assertRaises(ValueError):
                self.store.set_user_mail_keys(123,a,b)
            self.assertEqual(self.store.get_user_mail_keys(123),old)

    def test_local_customer_unknown_and_revoked_ids_cannot_save_or_get_private_keys(self):
        self.store.ensure_customer(456,'Customer')
        self.store.set_active(234,False)
        for chat_id in (LOCAL_USER_ID,456,789,234):
            with self.subTest(chat_id=chat_id):
                with self.assertRaises((ValueError,PermissionError)):
                    self.store.set_user_mail_keys(chat_id,'a','b')
                with self.assertRaises((ValueError,PermissionError)):
                    self.store.get_user_mail_keys(chat_id)

    def test_missing_pair_requires_explicit_entry_not_shared_or_migrated_keys(self):
        self.assertEqual(self.store.get_user_mail_keys(123),{})
        with self.assertRaisesRegex(ValueError,'2 API key'):
            self.store.get_user_mail_keys(123,require_complete=True)

    def test_name_role_edit_and_revoke_restore_preserve_credentials(self):
        keys=self.save()
        self.store.upsert_user(123,'Renamed','partner')
        self.store.set_active(123,False)
        with self.assertRaises(PermissionError):
            self.store.get_user_mail_keys(123,require_complete=True)
        self.store.set_active(123,True)
        self.assertEqual(self.store.get_user_mail_keys(123),keys)

    def test_stats_and_job_records_do_not_contain_private_api_values(self):
        keys=self.save()
        self.store.record_job('job1',123,'TEST',100,1)
        exported=repr((asdict(self.store.get_user(123)),self.store.list_jobs(123),self.store.finished_job_history()))
        self.assertFalse(any(key in exported for key in keys.values()))


class CommandTests(unittest.TestCase):
    def test_status_command_has_no_secret_and_set_request_repr_hides_both_keys(self):
        self.assertEqual(parse_control_command('/mailapi',123,'partner',1).action,'mail_keys_status')
        request=parse_control_command('/mailapi my-private-a my-private-b',123,'partner',1)
        self.assertEqual(request.action,'mail_keys_save')
        self.assertEqual(request.gmailvip_api_key,'my-private-a')
        self.assertEqual(request.one_trieu_api_key,'my-private-b')
        self.assertNotIn('my-private',repr(request))

    def test_malformed_commands_never_echo_keys(self):
        for text in ('/mailapi my-private-a','/mailapi my-private-a my-private-b extra',
                '/mailapi https://shop.test/?api_key=my-private-a other'):
            with self.subTest(text_length=len(text)),self.assertRaises(ValueError) as caught:
                parse_control_command(text,123,'admin',1)
            self.assertNotIn('my-private',str(caught.exception))

    def test_customer_and_group_cannot_reach_operator_key_management(self):
        controls,customers=[],[]
        bot=TelegramBotService(None,TelegramAccess((123,)),Mock(),on_control=controls.append,on_customer=customers.append)
        bot.handle_update(update(1,456,'/mailapi my-private-a my-private-b'))
        bot.handle_update(update(2,123,'/mailapi a b',chat={'id':123,'type':'group'}))
        bot.handle_update(update(3,123,'/mailapi a b',**{'from':{'id':234,'is_bot':False}}))
        self.assertEqual(controls,[])
        self.assertEqual([x.action for x in customers],['help'])


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    setUp=queue_fixtures.QueueTests.setUp
    operator=queue_fixtures.QueueTests.operator
    customer=queue_fixtures.QueueTests.customer
    local=queue_fixtures.QueueTests.local
    finish=queue_fixtures.QueueTests.finish

    def save(self,chat_id=123,prefix='personal'):
        keys=pair(prefix)
        self.store.set_user_mail_keys(chat_id,keys[GMAILVIP],keys[ONE_TRIEU])
        return keys

    def send_keys(self,chat_id=123,prefix='bot',update_id=50):
        keys=pair(prefix)
        request=parse_control_command(f'/mailapi {keys[GMAILVIP]} {keys[ONE_TRIEU]}',chat_id,'partner',update_id)
        self.page._on_control_requested((self.bot.fingerprint,request))
        return keys

    def select(self,chat_id):
        self.page._render_users(select_chat_id=chat_id)

    def multi(self):
        self.mochi.combo_provider.setCurrentIndex(self.mochi.combo_provider.findData(MULTI_SHOP))
        self.mochi.input_pids.setText('gmailvip:3445, 1trieumail:3423')
        self.mochi.input_gmailvip_key.setText('shared-gmailvip-key')
        self.mochi.input_1trieumail_key.setText('shared-1trieumail-key')

    def missing_keys(self):
        with self.store._connection(write=True) as db:
            db.execute('DELETE FROM user_mail_keys WHERE chat_id=123')

    def test_bot_pair_updates_only_its_own_profile_and_never_echoes_secrets(self):
        other=self.store.get_user_mail_keys(234)
        for chat_id,role in ((123,'admin'),(234,'partner')):
            keys=self.send_keys(chat_id,prefix=role,update_id=chat_id)
            self.assertEqual(self.store.get_user_mail_keys(chat_id),keys)
            text=self.bot.reply.call_args.args[1]
            self.assertEqual(self.bot.reply.call_args.args[0],chat_id)
            self.assertFalse(any(value in text for value in keys.values()))
        self.assertNotEqual(self.store.get_user_mail_keys(234),other)
        self.assertEqual(self.store.get_user_mail_keys(123),pair('admin'))

    def test_revoked_control_request_does_not_save_after_polling_dispatch(self):
        original=self.store.get_user_mail_keys(123)
        request=parse_control_command('/mailapi new-a new-b',123,'admin',50)
        self.store.set_active(123,False)
        self.page._on_control_requested((self.bot.fingerprint,request))
        self.store.set_active(123,True)
        self.assertEqual(self.store.get_user_mail_keys(123),original)
        self.bot.complete_request.assert_called_with(50)

    def test_status_reply_contains_readiness_only(self):
        keys=self.save()
        self.page._on_control_requested((self.bot.fingerprint,parse_control_command('/mailapi',123,'admin',50)))
        text=self.bot.reply.call_args.args[1]
        self.assertIn('GmailVip.vn: Đã nhập',text)
        self.assertIn('1TrieuMail.com: Đã nhập',text)
        self.assertFalse(any(value in text for value in keys.values()))

    def test_desktop_masked_pair_save_requires_matching_selected_user_and_typed_id(self):
        self.select(123)
        self.assertEqual(self.page.input_user_gmailvip_key.echoMode(),QLineEdit.EchoMode.Password)
        self.assertEqual(self.page.input_user_one_trieu_key.echoMode(),QLineEdit.EchoMode.Password)
        original=self.store.get_user_mail_keys(123)
        other=self.store.get_user_mail_keys(234)
        self.page.input_chat_id.setText('234')
        self.page.input_user_gmailvip_key.setText('desktop-a')
        self.page.input_user_one_trieu_key.setText('desktop-b')
        self.page._save_user_mail_keys()
        self.assertEqual(self.store.get_user_mail_keys(123),original)
        self.assertEqual(self.store.get_user_mail_keys(234),other)
        self.assertIn('khác dòng',self.page.lbl_user_mail_keys.text())
        self.page.input_chat_id.setText('123')
        self.page._save_user_mail_keys()
        self.assertEqual(self.store.get_user_mail_keys(123),{GMAILVIP:'desktop-a',ONE_TRIEU:'desktop-b'})
        self.assertEqual(self.store.get_user_mail_keys(234),other)
        self.assertNotIn('desktop-a',self.page.lbl_status.text())

    def test_desktop_partial_edit_preserves_both_saved_keys_and_local_is_disabled(self):
        old=self.save()
        self.select(123)
        self.page.input_user_one_trieu_key.clear()
        self.page._save_user_mail_keys()
        self.assertEqual(self.store.get_user_mail_keys(123),old)
        self.assertIn('2 API key',self.page.lbl_user_mail_keys.text())
        self.select(LOCAL_USER_ID)
        self.assertFalse(self.page.btn_save_user_mail_keys.isEnabled())
        self.assertEqual(self.page.input_user_gmailvip_key.text(),'')

    def test_profile_switch_loads_the_correct_masked_pair_without_shared_settings_mutation(self):
        a,b=self.save(123,'a'),self.save(234,'b')
        self.mochi._save_workspace_settings()
        settings_before={key:self.settings.value(key) for key in self.settings.allKeys()}
        self.select(123)
        self.assertEqual(self.page.input_user_gmailvip_key.text(),a[GMAILVIP])
        self.select(234)
        self.assertEqual(self.page.input_user_gmailvip_key.text(),b[GMAILVIP])
        self.assertEqual({key:self.settings.value(key) for key in self.settings.allKeys()},settings_before)

    def test_single_gmailvip_job_uses_owners_key_with_global_key_empty(self):
        keys=self.save()
        self.mochi.input_api_key.clear()
        job=self.operator(1)
        self.assertEqual(self.mochi._workers[job].runner.mail_provider_api_key,keys[GMAILVIP])
        self.assertEqual(self.mochi.input_api_key.text(),'')

    def test_single_1trieumail_job_uses_correct_owners_other_shop_key(self):
        keys=self.save()
        self.mochi.combo_provider.setCurrentIndex(self.mochi.combo_provider.findData(ONE_TRIEU))
        self.mochi.input_api_key.setText('shared-wrong-key')
        job=self.operator(1)
        self.assertEqual(self.mochi._workers[job].runner.mail_provider_api_key,keys[ONE_TRIEU])

    def test_two_operators_keep_different_pairs_under_shared_multi_shop_and_thread_settings(self):
        self.multi()
        self.mochi.spin_job_concurrency.setValue(2)
        self.mochi.spin_concurrency.setValue(7)
        a,b=self.save(123,'a'),self.save(234,'b')
        first,second=self.operator(1),self.operator(2,234)
        self.assertEqual(self.started,[first,second])
        for job,expected in ((first,a),(second,b)):
            runner=self.mochi._workers[job].runner
            self.assertEqual(runner.mail_api_keys,expected)
            self.assertEqual(runner.mail_product_ids,'gmailvip:3445, 1trieumail:3423')
            self.assertEqual(runner.concurrency,7)
        self.assertEqual(self.mochi.input_gmailvip_key.text(),'shared-gmailvip-key')
        self.assertEqual(self.settings.value('runtime/mochi_chat/mail_provider_api_key_gmailvip'),'shared-gmailvip-key')

    def test_missing_personal_pair_blocks_even_single_shop_batch_and_scheduled_requests(self):
        self.missing_keys()
        for index,when in ((1,None),(2,future().isoformat())):
            self.page._on_job_requested((self.bot.fingerprint,TelegramJobRequest(index,123,'TEST',100,'',when)))
        self.mochi.chk_use_mail_batch.setChecked(True)
        self.page._on_job_requested((self.bot.fingerprint,TelegramJobRequest(3,123,'TEST',100)))
        self.assertEqual(self.started,[])
        self.assertEqual(self.store.list_jobs(123),())
        self.assertFalse(self.mochi._scheduled_jobs)
        self.assertIn('/mailapi',self.bot.reply.call_args.args[1])

    def test_incomplete_legacy_pair_does_not_fall_back_to_global_key(self):
        with self.store._connection(write=True) as db:
            db.execute("UPDATE user_mail_keys SET one_trieu_key='' WHERE chat_id=123")
        self.page._on_job_requested((self.bot.fingerprint,TelegramJobRequest(1,123,'TEST',100)))
        self.assertFalse(self.mochi._workers)
        self.assertIsNone(self.store.lookup_request(self.bot.fingerprint,1))

    def test_local_and_customer_jobs_continue_using_machine_keys(self):
        self.mochi.spin_job_concurrency.setValue(2)
        self.mochi.input_api_key.setText('machine-global-key')
        local,customer=self.local(),self.customer()
        for job in (local,customer):
            self.assertEqual(self.mochi._workers[job].runner.mail_provider_api_key,'machine-global-key')

    def test_new_credentials_do_not_mutate_already_created_runner_snapshot(self):
        original=self.save()
        first=self.operator(1)
        new=self.send_keys(prefix='replacement')
        second=self.operator(2)
        self.assertEqual(self.mochi._workers[first].runner.mail_provider_api_key,original[GMAILVIP])
        self.assertEqual(self.mochi._workers[second].runner.mail_provider_api_key,new[GMAILVIP])

    def test_scheduled_job_reloads_latest_owners_pair_after_reopening_store(self):
        self.multi()
        when=future()
        self.page._on_job_requested((self.bot.fingerprint,TelegramJobRequest(1,123,'TEST',100,'',when.isoformat())))
        job=self.store.lookup_request(self.bot.fingerprint,1).job_id
        new=pair('latest')
        TelegramUserStore(self.store.path).set_user_mail_keys(123,new[GMAILVIP],new[ONE_TRIEU])
        self.mochi._check_scheduled_jobs(when+timedelta(seconds=1))
        self.assertEqual(self.mochi._workers[job].runner.mail_api_keys,new)
        self.assertFalse(any(value in self.store.get_job(job).schedule_data for value in new.values()))

    def test_scheduled_job_with_missing_pair_at_due_time_fails_without_global_fallback(self):
        when=future()
        self.page._on_job_requested((self.bot.fingerprint,TelegramJobRequest(1,123,'TEST',100,'',when.isoformat())))
        job=self.store.lookup_request(self.bot.fingerprint,1).job_id
        self.missing_keys()
        self.mochi._check_scheduled_jobs(when+timedelta(seconds=1))
        self.assertEqual(self.started,[])
        self.assertEqual(self.store.get_job(job).status,'failed')
        self.assertIn('API mua mail riêng',self.bot.reply.call_args.args[1])

    def test_actual_priority_fallback_receives_two_personal_keys(self):
        self.multi()
        keys=self.save()
        job=self.operator(1)
        runner=self.mochi._workers[job].runner
        seen=[]
        class Shop:
            def __init__(self,key,**kwargs):
                self.key=key
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
            async def buy_gmail_domain_info(self,products,amount):
                seen.append((self.key,products,amount))
                if self.key==keys[GMAILVIP]:raise MailProductUnavailable('sold out')
                return ['offline-delivery']
        result=asyncio.run(buy_priority_batch(runner.mail_product_ids,runner.mail_api_keys,1,
            provider_types={GMAILVIP:Shop,ONE_TRIEU:Shop}))
        self.assertEqual(result,['offline-delivery'])
        self.assertEqual(seen,[(keys[GMAILVIP],[3445],1),(keys[ONE_TRIEU],[3423],1)])

    def test_cost_receipts_are_still_attributed_to_the_correct_operator(self):
        self.mochi.spin_job_concurrency.setValue(2)
        a,b=self.operator(1),self.operator(2,234)
        for job,cost in ((a,10000),(b,23000)):
            self.mochi._workers[job].runner.mail_purchase_sink(MailPurchaseReceipt('p-'+job,job,'shop.test','safe-hash',1,1,cost))
        self.assertEqual(self.store.get_user(123).cost_units,10000)
        self.assertEqual(self.store.get_user(234).cost_units,23000)


if __name__=='__main__':
    unittest.main(verbosity=2)
