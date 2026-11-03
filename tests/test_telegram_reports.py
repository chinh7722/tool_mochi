"""Private CSV uploads and send-time report authorization without real Telegram."""
import asyncio
from copy import deepcopy
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from core.telegram_bot import (TelegramAccess,TelegramApi,TelegramApiError,TelegramBotService,
    TelegramConfig,TelegramControlRequest,parse_control_command,MAX_REPORT_BYTES)
from core.telegram_users import TelegramUserStore
from gui.telegram_bot import TelegramBotThread
from gui.pages.mochi_chat import MochiChatPage
from gui.pages.telegram import TelegramPage

TOKEN='123456789:'+'T'*35


def report(owner=202):
    return {'owner':{'chat_id':owner,'name':'Partner report','role':'partner','active':True},
        'generated_at':'2026-11-03T10:30:00+07:00','files':[
            {'filename':f'{owner}_jobs_20261103.csv','text':'\ufeffMã job,Bean\r\njob,300\r\n','row_count':1,'kind':'jobs'},
            {'filename':f'{owner}_purchases_20261103.csv','text':'\ufeffShop,Chi phí\r\ngmailvip,1000\r\n','row_count':1,'kind':'purchases'}]}


def update(index,actor,text,chat_type='private'):
    return {'update_id':index,'message':{'chat':{'id':actor,'type':chat_type},
        'from':{'id':actor,'is_bot':False},'text':text}}


class ReportCommandTests(unittest.TestCase):
    def test_own_report_and_alias_for_both_roles(self):
        for role in ('admin','partner'):
            for text in ('/baocao','/report','/BAOCAO@ours 101'):
                with self.subTest(role=role,text=text):
                    self.assertEqual(parse_control_command(text,101,role,8),
                        TelegramControlRequest(8,101,'user_report',101))

    def test_only_admin_can_parse_another_owner(self):
        self.assertEqual(parse_control_command('/baocao 202',101,'admin',9).target_chat_id,202)
        with self.assertRaises(ValueError): parse_control_command('/baocao 202',101,'partner',9)
        with self.assertRaises(ValueError): parse_control_command('/baocao',101,None,9)

    def test_invalid_target_and_extra_arguments_are_rejected(self):
        for text in ('/baocao 0','/baocao -1','/baocao True','/baocao 202 extra',
                '/baocao 9223372036854775808','/report 101,202','/baocao 1e3'):
            with self.subTest(text=text),self.assertRaises(ValueError):
                parse_control_command(text,101,'admin',1)

    def test_private_dispatch_mentions_dedup_and_partner_denial(self):
        requests=[]
        service=TelegramBotService(None,TelegramAccess((101,202),roles={101:'admin'}),Mock(),on_control=requests.append)
        service.username='ours'
        first=update(1,101,'/baocao@ours 202')
        service.handle_update(first);service.handle_update(first)
        service.handle_update(update(2,101,'/baocao 202','group'))
        service.handle_update(update(3,202,'/baocao 101'))
        service.handle_update(update(4,101,'/baocao@other 202'))
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0].target_chat_id,202)
        self.assertEqual(service.pending,{1})


class ReportApiTests(unittest.IsolatedAsyncioTestCase):
    def api(self,response=None):
        response=response or SimpleNamespace(status_code=200,json=Mock(return_value={'ok':True,'result':{'document':{'file_id':'offline'}}}))
        session=SimpleNamespace(post=AsyncMock(return_value=response))
        return TelegramApi(TOKEN,session),session

    async def test_csv_upload_is_multipart_bytes_with_no_local_file(self):
        api,session=self.api()
        content='\ufeffTên,Bean\r\nPartner,100\r\n'.encode()
        with patch('core.telegram_bot.CurlMime') as mime:
            await api.send_document(101,content,'101_jobs.csv','Lịch sử job')
            mime.return_value.addpart.assert_called_once_with(name='document',filename='101_jobs.csv',
                content_type='text/csv; charset=utf-8',data=content)
            mime.return_value.close.assert_called_once()
            self.assertIs(session.post.call_args.kwargs['multipart'],mime.return_value)
        self.assertTrue(session.post.call_args.args[0].endswith('/sendDocument'))
        self.assertEqual(session.post.call_args.kwargs['data'],{'chat_id':'101','caption':'Lịch sử job'})
        self.assertTrue(session.post.call_args.kwargs['verify'])

    async def test_invalid_document_never_starts_upload(self):
        api,session=self.api()
        for chat,data,name in ((True,b'csv','jobs.csv'),(101,'not-bytes','jobs.csv'),
                (101,b'csv','../secret.csv'),(101,b'csv','jobs.txt'),(101,b'x'*(MAX_REPORT_BYTES+1),'jobs.csv')):
            with self.subTest(name=name),self.assertRaises(ValueError):
                await api.send_document(chat,data,name)
        session.post.assert_not_awaited()

    async def test_upload_network_error_redacts_token_and_releases_mime(self):
        api,session=self.api()
        session.post.side_effect=RuntimeError('secret URL '+TOKEN)
        with patch('core.telegram_bot.CurlMime') as mime,self.assertRaises(TelegramApiError) as caught:
            await api.send_document(101,b'csv','jobs.csv')
        self.assertNotIn(TOKEN,str(caught.exception))
        mime.return_value.close.assert_called_once()
        self.assertEqual(session.post.await_count,1)

    async def test_document_rejection_keeps_explicit_retry_delay_and_hides_token(self):
        response=SimpleNamespace(status_code=429,json=lambda:{'ok':False,'error_code':429,
            'description':'token '+TOKEN,'parameters':{'retry_after':1}})
        api,_=self.api(response)
        with self.assertRaises(TelegramApiError) as caught:
            await api.send_document(101,b'csv','jobs.csv')
        self.assertEqual((caught.exception.code,caught.exception.retry_after),(429,1))
        self.assertNotIn(TOKEN,str(caught.exception))


class ReportQueueTests(unittest.IsolatedAsyncioTestCase):
    def bot(self,role='admin'):
        api=SimpleNamespace(send_document=AsyncMock(),call=AsyncMock())
        access=TelegramAccess((101,202),roles={101:role})
        return TelegramBotService(api,access,Mock(),on_status=Mock())

    async def drain(self,bot):
        sender=asyncio.create_task(bot._send_reports())
        try: await asyncio.wait_for(bot.report_outbox.join(),2)
        finally:
            sender.cancel();await asyncio.gather(sender,return_exceptions=True)

    async def test_two_csv_documents_are_frozen_and_authorized_before_each_send(self):
        bot=self.bot();authority=Mock(return_value=True);value=report()
        self.assertTrue(bot.send_report(101,value,target_id=202,admin_only=True,authorize=authority))
        value['files'][0]['text']='changed later'
        await self.drain(bot)
        self.assertEqual(bot.api.send_document.await_count,2)
        first=bot.api.send_document.call_args_list[0].args
        self.assertEqual(first[:3],(101,report()['files'][0]['text'].encode(),'202_jobs_20261103.csv'))
        self.assertEqual(authority.call_args_list,[((101,202),),((101,202),)])

    async def test_partner_cannot_enqueue_other_owner_even_with_forged_admin_only_flag(self):
        bot=self.bot('partner')
        self.assertFalse(bot.send_report(101,report(),target_id=202,authorize=lambda *_:True))
        self.assertTrue(bot.report_outbox.empty())
        self.assertTrue(bot.send_report(101,report(101),target_id=101,authorize=lambda *_:True))
        await self.drain(bot)
        self.assertEqual(bot.api.send_document.await_count,2)

    async def test_demoted_admin_loses_queued_other_user_report_but_keeps_own_report(self):
        bot=self.bot()
        bot.send_report(101,report(),target_id=202,authorize=lambda *_:True)
        bot.access.replace((101,202),roles={101:'partner'})
        await self.drain(bot)
        bot.api.send_document.assert_not_awaited()

    async def test_sql_authorization_can_block_stale_role_cache_before_first_file(self):
        bot=self.bot()
        def deny(*_): raise PermissionError('private SQL authorization detail')
        bot.send_report(101,report(),target_id=202,authorize=deny)
        await self.drain(bot)
        bot.api.send_document.assert_not_awaited()
        self.assertNotIn('private SQL',str(bot.on_status.call_args_list))

    async def test_revocation_after_first_file_prevents_second_file(self):
        bot=self.bot();authority=Mock(side_effect=[True,False])
        bot.send_report(101,report(),target_id=202,authorize=authority)
        await self.drain(bot)
        self.assertEqual(bot.api.send_document.await_count,1)

    async def test_rate_limited_upload_rechecks_sql_before_retry(self):
        bot=self.bot();authority=Mock(side_effect=[True,False])
        bot.api.send_document.side_effect=TelegramApiError(429,'offline rate limit',.01)
        bot.send_report(101,report(),target_id=202,authorize=authority)
        await self.drain(bot)
        self.assertEqual(bot.api.send_document.await_count,1)

    async def test_ambiguous_network_response_is_not_retried_or_followed_by_second_file(self):
        bot=self.bot();bot.api.send_document.side_effect=TelegramApiError(0,'offline uncertain response')
        bot.send_report(101,report(),target_id=202,authorize=lambda *_:True)
        await self.drain(bot)
        self.assertEqual(bot.api.send_document.await_count,1)

    async def test_queue_and_report_sizes_are_bounded_without_disk_writes(self):
        bot=self.bot()
        for _ in range(4): self.assertTrue(bot.send_report(101,report(),target_id=202,authorize=lambda *_:True))
        self.assertFalse(bot.send_report(101,report(),target_id=202,authorize=lambda *_:True))
        invalid=report();invalid['files'][0]['text']='x'*(MAX_REPORT_BYTES+1)
        self.assertFalse(self.bot().send_report(101,invalid,target_id=202,authorize=lambda *_:True))
        invalid=report();invalid['owner']['chat_id']=303
        self.assertFalse(self.bot().send_report(101,invalid,target_id=202,authorize=lambda *_:True))

    async def test_slow_document_does_not_block_ordinary_reply_sender(self):
        bot=self.bot();started=asyncio.Event();release=asyncio.Event()
        async def wait(*_): started.set();await release.wait()
        bot.api.send_document.side_effect=wait
        bot.send_report(101,report(),target_id=202,authorize=lambda *_:True)
        sender=asyncio.create_task(bot._send_reports());replies=asyncio.create_task(bot._send_replies())
        try:
            await asyncio.wait_for(started.wait(),1)
            bot.reply(101,'ordinary own stats')
            await asyncio.wait_for(bot.outbox.join(),1)
            bot.api.call.assert_awaited_once_with('sendMessage',{'chat_id':101,'text':'ordinary own stats'})
        finally:
            release.set();sender.cancel();replies.cancel();await asyncio.gather(sender,replies,return_exceptions=True)

    async def test_qt_bridge_completes_request_and_authority_expires_when_worker_stops(self):
        worker=TelegramBotThread(TelegramConfig(TOKEN,(101,),user_roles=((101,'admin'),)))
        captured={}
        def send(*args,**kwargs): captured.update(kwargs);return True
        service=SimpleNamespace(complete=Mock(),send_report=Mock(side_effect=send),reply=Mock())
        worker._loop=asyncio.get_running_loop();worker._service=service
        authority=Mock(return_value=True)
        self.assertTrue(worker.send_report(101,report(),7,target_id=202,admin_only=True,authorize=authority))
        await asyncio.sleep(0)
        service.complete.assert_called_once_with(7)
        self.assertTrue(captured['authorize'](101,202))
        worker.request_stop()
        self.assertFalse(captured['authorize'](101,202))
        self.assertFalse(worker.send_report(101,report(),8,target_id=202,authorize=authority))
        worker._service=worker._loop=None


class ReportPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        directory=Path(self.temp.name)
        self.settings=QSettings(str(directory/'settings.ini'),QSettings.IniFormat)
        self.store=TelegramUserStore(directory/'users.sqlite3')
        self.store.upsert_user(101,'Admin','admin');self.store.upsert_user(202,'Partner')
        self.store.upsert_user(303,'Other admin','admin')
        self.mochi=MochiChatPage(settings=self.settings);self.mochi._schedule_timer.stop()
        self.page=TelegramPage(self.settings,self.mochi,user_store=self.store)
        self.worker=SimpleNamespace(fingerprint='offline-report-bot',access=TelegramAccess(self.store.active_ids(),roles=self.store.roles()),
            reply=Mock(),send_report=Mock(return_value=True),complete_request=Mock())
        self.page.worker=self.worker;self.page._accepting_requests=True

    def tearDown(self):
        self.page.worker=None;self.page.shutdown_workers();self.mochi.shutdown_workers()
        self.page.deleteLater();self.mochi.deleteLater();self.app.processEvents()

    def control(self,actor,target,index=1):
        self.page._on_control_requested((self.worker.fingerprint,TelegramControlRequest(index,actor,'user_report',target)))

    def test_admin_can_export_other_admin_and_partner_but_partner_only_self(self):
        for actor,target in ((101,303),(101,202),(202,202)):
            with self.subTest(actor=actor,target=target):
                self.worker.send_report.reset_mock();self.control(actor,target)
                call=self.worker.send_report.call_args
                self.assertEqual(call.args[0],actor)
                self.assertEqual(call.args[1]['owner']['chat_id'],target)
                self.assertEqual(len(call.args[1]['files']),2)
                self.assertEqual(call.kwargs['admin_only'],actor!=target)
                self.assertTrue(call.kwargs['authorize'](actor,target))

    def test_forged_partner_request_for_admin_does_not_generate_or_queue_report(self):
        self.control(202,101)
        self.worker.send_report.assert_not_called()
        self.assertNotIn('Other admin',self.worker.reply.call_args.args[1])

    def test_admin_demoted_in_sql_cannot_use_old_cached_admin_role(self):
        self.store.upsert_user(101,'Admin now partner','partner')
        self.control(101,303)
        self.worker.send_report.assert_not_called()

    def test_revoked_actor_and_old_bot_do_not_receive_report(self):
        self.store.set_active(101,False)
        self.control(101,202,8)
        self.worker.complete_request.assert_called_with(8)
        self.worker.send_report.assert_not_called()
        self.page._on_control_requested(('old-bot',TelegramControlRequest(9,202,'user_report',202)))
        self.worker.send_report.assert_not_called()

    def test_generation_failure_is_generic_and_does_not_leak_sql_or_credentials(self):
        with patch.object(self.store,'user_report',side_effect=RuntimeError('SQL secret credential')):
            self.control(101,202)
        self.worker.send_report.assert_not_called()
        self.assertNotIn('SQL secret',self.worker.reply.call_args.args[1])


if __name__=='__main__': unittest.main()
