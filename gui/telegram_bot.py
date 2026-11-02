"""Qt bridge: poll Telegram on its own event loop, dispatch jobs on the GUI."""

import asyncio
import hashlib
import threading
from dataclasses import replace

try:
    from PySide6.QtCore import QThread, Signal
except ImportError:
    from PyQt6.QtCore import QThread, pyqtSignal as Signal
from curl_cffi.requests import AsyncSession

from core.telegram_bot import TelegramAccess, TelegramApi, TelegramApiError, TelegramBotService
from core.telegram_broadcast import BroadcastProgress, send_announcement, validate_announcement


class TelegramBotThread(QThread):
    job_requested = Signal(object)
    control_requested = Signal(object)
    customer_requested = Signal(object)
    status_changed = Signal(str)
    broadcast_progress = Signal(str, object)
    broadcast_failed = Signal(str, str, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._token = config.token
        self.fingerprint = hashlib.sha256(config.token.encode()).hexdigest()
        self.access = TelegramAccess(config.chat_ids, roles=dict(config.user_roles))
        self._stop = threading.Event()
        self._loop = self._task = self._service = None
        self._broadcast_task = None
        self._broadcast_state = None

    def request_stop(self):
        self._stop.set()
        if self._loop and self._task:
            try:
                self._loop.call_soon_threadsafe(self._task.cancel)
            except RuntimeError:
                pass

    def reply(self, chat_id, text, request_id=None, *, public=False, admin_only=False):
        if not self._loop or not self._service or self._stop.is_set():
            return False

        service = self._service

        def deliver():
            if self._service is not service or self._stop.is_set():
                return
            if request_id is not None:
                service.complete(request_id)
            service.reply(chat_id, text, public=public, admin_only=admin_only)

        try:
            self._loop.call_soon_threadsafe(deliver)
            return True
        except RuntimeError:
            return False

    def complete_request(self, request_id):
        service = self._service
        if self._loop and service:
            try:
                self._loop.call_soon_threadsafe(service.complete, request_id)
            except RuntimeError:
                pass

    def broadcast(self, broadcast_id, chat_ids, text):
        if not self._loop or not self._service or self._stop.is_set():
            return False
        service=self._service
        recipients=tuple(chat_ids)
        def start():
            if self._service is not service or self._stop.is_set():
                self.broadcast_failed.emit(self.fingerprint,broadcast_id,'Bot chưa sẵn sàng gửi thông báo.')
                return
            if self._broadcast_task and not self._broadcast_task.done():
                self.broadcast_failed.emit(self.fingerprint,broadcast_id,'Đang gửi một thông báo khác; chờ lượt gửi kết thúc.')
                return
            try:
                validate_announcement(text)
            except ValueError as exc:
                self.broadcast_failed.emit(self.fingerprint,broadcast_id,str(exc))
                return
            self._broadcast_state=BroadcastProgress(broadcast_id,len(set(recipients)))
            def progress(state):
                self._broadcast_state=state
                self.broadcast_progress.emit(self.fingerprint,state)
            task=asyncio.create_task(send_announcement(service.api,recipients,text,broadcast_id=broadcast_id,
                allowed=lambda chat_id:self.access.allowed(chat_id) and self.access.role(chat_id) in ('admin','partner'),
                on_progress=progress))
            self._broadcast_task=task
            def stopped(completed):
                if completed.cancelled():
                    progress(replace(self._broadcast_state,finished=True,cancelled=True))
                elif completed.exception() is not None:
                    self.broadcast_failed.emit(self.fingerprint,broadcast_id,'Không bắt đầu được lượt gửi thông báo.')
                if self._broadcast_task is completed:
                    self._broadcast_task=None
            task.add_done_callback(stopped)
        try:
            self._loop.call_soon_threadsafe(start)
            return True
        except RuntimeError:
            return False

    def cancel_broadcast(self):
        if self._loop:
            def cancel():
                if self._broadcast_task:
                    self._broadcast_task.cancel()
            try:
                self._loop.call_soon_threadsafe(cancel)
            except RuntimeError:
                pass

    def run(self):
        async def execute():
            self._loop = asyncio.get_running_loop()
            if self._stop.is_set():
                return
            # Bot traffic uses the machine's network, independent of task proxies.
            async with AsyncSession(trust_env=False, proxies={'http': '', 'https': ''},
                    verify=True, timeout=30) as session:
                api = TelegramApi(self._token, session)
                self._service = TelegramBotService(api, self.access,
                    lambda request: self.job_requested.emit((self.fingerprint, request)), self.status_changed.emit,
                    on_control=lambda request: self.control_requested.emit((self.fingerprint, request)),
                    on_customer=lambda request: self.customer_requested.emit((self.fingerprint, request)))
                self._task = asyncio.create_task(self._service.run())
                if self._stop.is_set():
                    self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
                finally:
                    if self._broadcast_task:
                        self._broadcast_task.cancel()
                        await asyncio.gather(self._broadcast_task,return_exceptions=True)
                    self._service = None

        try:
            asyncio.run(execute())
        except TelegramApiError as exc:
            self.status_changed.emit(f'Bot đã dừng: {exc}')
        except Exception:
            self.status_changed.emit('Bot đã dừng do lỗi kết nối hoặc cấu hình. Kiểm tra lại Bot Token và mạng.')
        finally:
            self._loop = self._task = None
