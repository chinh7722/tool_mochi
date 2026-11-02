"""Operator announcements: real acknowledgements, access checks and nonblocking Qt bridge."""
import asyncio
from dataclasses import replace
import os
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock,Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication,QPlainTextEdit
from core.telegram_bot import TelegramApiError,TelegramAccess,TelegramConfig
from core.telegram_broadcast import BroadcastProgress,send_announcement,validate_announcement,announcement_length
from gui.telegram_bot import TelegramBotThread
import test_job_queue as fixtures
from test_telegram_bot import TOKEN


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def send(self,ids=(123,234),effects=None,allowed=lambda _:True,text='Thông báo\n<b>Nguyên văn</b>',progress=None,interval=0):
        api=SimpleNamespace(call=AsyncMock(side_effect=effects))
        events=[]
        result=await send_announcement(api,ids,text,broadcast_id='notice',allowed=allowed,
            on_progress=progress or events.append,interval=interval)
        return result,api,events

    async def test_each_unique_recipient_receives_exact_plain_text_and_confirmed_counts(self):
        state,api,events=await self.send(ids=(123,234,123))
        self.assertEqual((state.total,state.sent,state.failed,state.remaining),(2,2,0,0))
        self.assertTrue(state.finished)
        self.assertEqual([x.args for x in api.call.await_args_list],[
            ('sendMessage',{'chat_id':123,'text':'Thông báo\n<b>Nguyên văn</b>'}),
            ('sendMessage',{'chat_id':234,'text':'Thông báo\n<b>Nguyên văn</b>'})])
        self.assertEqual(events[0].sent,0)

    async def test_permissions_are_rechecked_after_previous_delivery(self):
        permitted={123,234}
        def progress(state):
            if state.sent==1:permitted.discard(234)
        state,api,_=await self.send(allowed=lambda cid:cid in permitted,progress=progress)
        self.assertEqual((state.sent,state.skipped),(1,1))
        api.call.assert_awaited_once()

    async def test_permission_check_fails_closed_without_sending(self):
        state,api,_=await self.send(allowed=Mock(side_effect=OSError('private internal detail')))
        api.call.assert_not_awaited()
        self.assertTrue(state.cancelled)
        self.assertEqual(state.remaining,2)
        self.assertNotIn('private internal',state.error)

    async def test_blocked_recipient_fails_once_and_next_recipient_still_receives(self):
        state,api,_=await self.send(effects=[TelegramApiError(403,'private blocked detail'),{}])
        self.assertEqual((state.sent,state.failed),(1,1))
        self.assertEqual(api.call.await_count,2)
        self.assertIn('403',state.error)
        self.assertNotIn('private blocked',state.error)

    async def test_lost_response_is_not_retried_and_error_does_not_echo_message_or_secrets(self):
        for error in (TelegramApiError(0,'https://token-secret/private'),ConnectionError('token-secret')):
            state,api,_=await self.send(effects=[error,{}])
            self.assertEqual(api.call.await_count,2)
            self.assertEqual((state.failed,state.sent),(1,1))
            self.assertNotIn('token-secret',state.error)

    async def test_explicit_429_waits_then_retries_only_rejected_request(self):
        started=time.monotonic()
        state,api,_=await self.send(ids=(123,),effects=[TelegramApiError(429,'wait',.02),{}])
        self.assertGreaterEqual(time.monotonic()-started,.02)
        self.assertEqual((state.sent,state.failed),(1,0))
        self.assertEqual(api.call.await_count,2)

    async def test_429_retry_count_is_bounded_and_continues_to_next_recipient(self):
        state,api,_=await self.send(effects=[TelegramApiError(429,'wait',.001) for _ in range(3)]+[{}])
        self.assertEqual((state.failed,state.sent),(1,1))
        self.assertEqual(api.call.await_count,4)

    async def test_revoke_during_retry_wait_prevents_the_retry(self):
        access=TelegramAccess((123,))
        async def call(*args):
            access.replace(())
            raise TelegramApiError(429,'wait',.001)
        state,api,_=await self.send(ids=(123,),effects=call,allowed=access.allowed)
        self.assertEqual((state.sent,state.failed,state.skipped),(0,0,1))
        api.call.assert_awaited_once()

    async def test_cancel_inflight_keeps_confirmed_counts_and_unknown_remainder(self):
        waiting=asyncio.Event()
        async def call(_,payload):
            if payload['chat_id']==123:return {}
            waiting.set()
            await asyncio.Event().wait()
        api=SimpleNamespace(call=AsyncMock(side_effect=call))
        task=asyncio.create_task(send_announcement(api,(123,234,345),'test',broadcast_id='id',
            allowed=lambda _:True,on_progress=lambda _:None,interval=0))
        await asyncio.wait_for(waiting.wait(),1)
        task.cancel()
        state=await asyncio.wait_for(task,1)
        self.assertEqual((state.sent,state.failed,state.remaining),(1,0,2))
        self.assertTrue(state.cancelled)
        self.assertTrue(state.finished)

    async def test_cancel_during_long_retry_after_is_prompt(self):
        waiting=asyncio.Event()
        async def call(*args):
            waiting.set()
            raise TelegramApiError(429,'wait',100)
        api=SimpleNamespace(call=AsyncMock(side_effect=call))
        task=asyncio.create_task(send_announcement(api,(123,),'test',broadcast_id='id',
            allowed=lambda _:True,on_progress=lambda _:None,interval=0))
        await asyncio.wait_for(waiting.wait(),1)
        task.cancel()
        state=await asyncio.wait_for(task,1)
        self.assertEqual(state.remaining,1)
        self.assertTrue(state.cancelled)
        api.call.assert_awaited_once()

    async def test_large_audience_is_not_dropped_at_the_normal_outbox_capacity(self):
        state,api,_=await self.send(ids=(index for index in range(1,301)))
        self.assertEqual(state.sent,300)
        self.assertEqual(api.call.await_count,300)

    async def test_ui_callback_exception_does_not_retry_already_delivered_messages(self):
        state,api,_=await self.send(progress=Mock(side_effect=RuntimeError('UI unavailable')))
        self.assertEqual(state.sent,2)
        self.assertEqual(api.call.await_count,2)

    async def test_invalid_content_or_recipients_never_reaches_api(self):
        api=SimpleNamespace(call=AsyncMock())
        for ids,text in (((123,),''),((123,),'x'*4001),((123,),'😀'*2001),((),'ok'),((True,),'ok'),((-1,),'ok')):
            with self.subTest(length=len(text)),self.assertRaises(ValueError):
                await send_announcement(api,ids,text,broadcast_id='id',allowed=lambda _:True,on_progress=lambda _:None)
        api.call.assert_not_awaited()
        self.assertEqual(announcement_length('😀'),2)
        self.assertEqual(validate_announcement('😀'*2000),'😀'*2000)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.worker=TelegramBotThread(TelegramConfig(TOKEN,(123,234)))
        self.worker._loop=asyncio.get_running_loop()
        self.api=SimpleNamespace(call=AsyncMock())
        self.worker._service=SimpleNamespace(api=self.api)
        self.events=[]
        self.errors=[]
        self.worker.broadcast_progress.connect(lambda fp,state:self.events.append((fp,state)))
        self.worker.broadcast_failed.connect(lambda *args:self.errors.append(args))

    async def asyncTearDown(self):
        self.worker.cancel_broadcast()
        await asyncio.sleep(0)
        task=self.worker._broadcast_task
        if task:await asyncio.gather(task,return_exceptions=True)
        self.worker._loop=None

    async def complete(self):
        for _ in range(100):
            if self.events and self.events[-1][1].finished:return
            await asyncio.sleep(.01)
        self.fail('Broadcast did not finish')

    async def test_bridge_reports_actual_counts_and_rejects_an_overlapping_campaign(self):
        self.assertTrue(self.worker.broadcast('first',(123,234),'text'))
        self.assertTrue(self.worker.broadcast('second',(123,),'other'))
        await self.complete()
        self.assertEqual(self.events[-1][1].sent,2)
        self.assertTrue(any(x[1]=='second' for x in self.errors))
        self.assertEqual(self.api.call.await_count,2)

    async def test_cancel_before_coroutine_start_emits_final_cancelled_state(self):
        self.worker.broadcast('first',(123,234),'text')
        self.worker.cancel_broadcast()
        await self.complete()
        self.assertTrue(self.events[-1][1].cancelled)
        self.assertEqual(self.events[-1][1].remaining,2)
        self.api.call.assert_not_awaited()

    async def test_not_ready_or_stopped_bot_refuses_to_enqueue(self):
        self.worker._service=None
        self.assertFalse(self.worker.broadcast('first',(123,),'text'))
        self.worker._service=SimpleNamespace(api=self.api)
        self.worker.request_stop()
        self.assertFalse(self.worker.broadcast('first',(123,),'text'))
        self.api.call.assert_not_awaited()

    async def test_bridge_never_sends_to_ids_outside_current_operator_access(self):
        self.worker.broadcast('first',(123,234,456),'text')
        self.worker.access.replace((123,))
        await self.complete()
        state=self.events[-1][1]
        self.assertEqual((state.sent,state.skipped),(1,2))
        self.api.call.assert_awaited_once()


class UiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        fixtures.QueueTests.setUp(self)
        self.bot.broadcast=Mock(return_value=True)
        self.bot.cancel_broadcast=Mock()
        self.page.input_broadcast.setPlainText('Nội dung thông báo\nNguyên văn.')

    def submit(self):
        self.page._send_broadcast()
        return self.page._broadcast_id

    def test_widget_broadcasts_to_all_active_operators_independent_of_table_filter(self):
        self.store.upsert_user(345,'Revoked','partner',active=False)
        self.store.upsert_user(789,'New operator no API','admin')
        self.store.ensure_customer(456,'Customer')
        self.page.input_search.setText('Admin')
        self.assertIsInstance(self.page.input_broadcast,QPlainTextEdit)
        self.submit()
        _,ids,text=self.bot.broadcast.call_args.args
        self.assertEqual(set(ids),{123,234,789})
        self.assertEqual(text,'Nội dung thông báo\nNguyên văn.')
        self.assertEqual(self.page._broadcast_last.sent,0)

    def test_bot_offline_empty_and_oversized_content_do_not_send(self):
        self.page.worker=None
        self.submit()
        self.assertIn('Bật bot',self.page.lbl_broadcast_status.text())
        self.page.worker=self.bot
        for text in (' ','x'*4001,'😀'*2001):
            self.page.input_broadcast.setPlainText(text)
            self.submit()
        self.bot.broadcast.assert_not_called()
        self.assertIsNone(self.page._broadcast_id)

    def test_no_eligible_users_does_not_send(self):
        for cid in (123,234):self.store.set_active(cid,False)
        self.submit()
        self.bot.broadcast.assert_not_called()
        self.assertIn('Chưa có admin/partner',self.page.lbl_broadcast_status.text())

    def test_busy_click_is_ignored_and_content_is_snapshotted(self):
        first=self.submit()
        self.page.input_broadcast.setPlainText('Edited later')
        self.submit()
        self.bot.broadcast.assert_called_once()
        self.assertEqual(self.page._broadcast_id,first)
        self.assertIn('Nguyên văn.',self.bot.broadcast.call_args.args[2])
        self.assertFalse(self.page.btn_send_broadcast.isEnabled())

    def test_progress_counts_only_confirmed_deliveries_and_preserves_editor(self):
        bid=self.submit()
        self.page._on_broadcast_progress(self.bot.fingerprint,BroadcastProgress(bid,2,sent=1))
        self.assertIn('Thành công: 1/2',self.page.lbl_broadcast_status.text())
        self.page._on_broadcast_progress(self.bot.fingerprint,BroadcastProgress(bid,2,sent=1,failed=1,finished=True))
        self.assertIsNone(self.page._broadcast_id)
        self.assertTrue(self.page.btn_send_broadcast.isEnabled())
        self.assertEqual(self.page.input_broadcast.toPlainText(),'Nội dung thông báo\nNguyên văn.')

    def test_old_bot_or_campaign_signals_do_not_change_current_progress(self):
        bid=self.submit()
        before=self.page.lbl_broadcast_status.text()
        self.page._on_broadcast_progress('other-bot',BroadcastProgress(bid,2,sent=2,finished=True))
        self.page._on_broadcast_progress(self.bot.fingerprint,BroadcastProgress('old-id',2,sent=2,finished=True))
        self.assertEqual(self.page.lbl_broadcast_status.text(),before)
        self.assertEqual(self.page._broadcast_id,bid)

    def test_cancel_stops_only_announcement_and_shows_unfinished_remainder(self):
        bid=self.submit()
        self.page._cancel_broadcast()
        self.bot.cancel_broadcast.assert_called_once()
        self.assertTrue(self.page._accepting_requests)
        self.assertIs(self.page.worker,self.bot)
        self.page._on_broadcast_progress(self.bot.fingerprint,
            BroadcastProgress(bid,2,sent=1,finished=True,cancelled=True))
        self.assertIn('Chưa hoàn tất: 1',self.page.lbl_broadcast_status.text())

    def test_worker_not_ready_keeps_content_and_does_not_claim_success(self):
        self.bot.broadcast.return_value=False
        self.submit()
        self.assertIsNone(self.page._broadcast_id)
        self.assertIn('Thành công: 0/2',self.page.lbl_broadcast_status.text())
        self.assertIn('chưa sẵn sàng',self.page.lbl_broadcast_status.text())

    def test_bot_stopping_keeps_confirmed_counts_and_releases_send_button(self):
        bid=self.submit()
        self.page._on_broadcast_progress(self.bot.fingerprint,BroadcastProgress(bid,2,sent=1))
        self.bot.deleteLater=Mock()
        self.page._worker_stopped()
        self.assertIsNone(self.page.worker)
        self.assertIsNone(self.page._broadcast_id)
        self.assertIn('Thành công: 1/2',self.page.lbl_broadcast_status.text())
        self.assertIn('Chưa hoàn tất: 1',self.page.lbl_broadcast_status.text())

    def test_real_thread_bridge_updates_gui_and_shuts_down_without_network(self):
        ready=threading.Event()
        delivered=[]
        async def call(method,payload=None):
            if method=='getMe':return {'username':'offline_bot'}
            if method=='getWebhookInfo':return {'url':''}
            if method=='getUpdates':
                if payload['offset']==-1:return []
                ready.set()
                await asyncio.Event().wait()
            if method=='sendMessage':
                delivered.append(payload)
                return {'message_id':len(delivered)}
        session=AsyncMock()
        api=SimpleNamespace(call=AsyncMock(side_effect=call))
        self.page.worker=None
        self.page.input_token.setText(TOKEN)
        with patch('gui.telegram_bot.AsyncSession',return_value=session),patch('gui.telegram_bot.TelegramApi',return_value=api):
            self.page._start_bot()
            worker=self.page.worker
            try:
                self.assertTrue(ready.wait(2))
                self.submit()
                deadline=time.monotonic()+3
                while self.page._broadcast_id and time.monotonic()<deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                self.assertIsNone(self.page._broadcast_id)
                self.assertIn('Thành công: 2/2',self.page.lbl_broadcast_status.text())
                self.assertEqual({x['chat_id'] for x in delivered},{123,234})
            finally:
                self.page._stop_bot()
                self.assertTrue(worker.wait(2000))
                self.app.processEvents()


if __name__=='__main__':
    unittest.main(verbosity=2)
