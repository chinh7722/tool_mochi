"""Telegram job commands with a private-chat allowlist and bounded polling."""

import asyncio
from dataclasses import dataclass, field
import re
import threading
from typing import Callable
from datetime import datetime
from core.job_schedule import schedule_time
from core.mail.user_keys import validated_user_mail_keys
from core.mail.providers.priority import GMAILVIP, ONE_TRIEU

from curl_cffi.requests import AsyncSession


def validate_bot_token(token):
    if not re.fullmatch(r'[0-9]{5,}:[A-Za-z0-9_-]{20,}', token):
        raise ValueError('Bot Token không đúng định dạng. Dán token do BotFather cấp.')


def parse_chat_ids(raw):
    parts = re.split(r'[,;\s]+', str(raw).strip())
    result = set()
    for part in parts:
        if not part:
            continue
        if not re.fullmatch(r'[1-9][0-9]{0,18}', part) or int(part) > 2**63 - 1:
            raise ValueError('Chat ID phải là số nguyên dương của cuộc trò chuyện riêng.')
        result.add(int(part))
    return tuple(sorted(result))


@dataclass(frozen=True)
class TelegramConfig:
    token: str = ''
    chat_ids: tuple = ()
    auto_start: bool = False
    user_roles: tuple = ()

    @classmethod
    def from_settings(cls, settings):
        try:
            ids = parse_chat_ids(settings.value('telegram/allowed_chat_ids', ''))
        except ValueError:
            ids = ()  # Invalid saved data never grants access.
        auto = str(settings.value('telegram/auto_start', False)).lower() in ('true', '1')
        return cls(str(settings.value('telegram/bot_token', '') or '').strip(), ids, auto)

    def save(self, settings):
        settings.setValue('telegram/bot_token', self.token)
        settings.setValue('telegram/allowed_chat_ids', '\n'.join(map(str, self.chat_ids)))
        settings.setValue('telegram/auto_start', self.auto_start)


class TelegramAccess:
    def __init__(self, chat_ids=(), *, roles=None):
        self._lock = threading.Lock()
        self._roles = {}
        self.replace(chat_ids, roles=roles)

    def replace(self, chat_ids, *, roles=None):
        ids = frozenset(parse_chat_ids(' '.join(map(str, chat_ids))))
        if roles is not None and any(role not in ('admin', 'partner') for role in roles.values()):
            raise ValueError('Vai trò phải là admin hoặc partner.')
        with self._lock:
            self._ids = ids
            current = self._roles if roles is None else roles
            self._roles = {chat_id: current.get(chat_id, 'partner') for chat_id in ids}

    def allowed(self, chat_id):
        with self._lock:
            return type(chat_id) is int and chat_id in self._ids

    def role(self, chat_id):
        with self._lock:
            return self._roles.get(chat_id) if type(chat_id) is int else None


@dataclass(frozen=True)
class TelegramJobRequest:
    update_id: int
    chat_id: int
    code: str
    points: int
    customer: str = ''
    scheduled_at: str | None = None
    combo: bool = False


@dataclass(frozen=True)
class TelegramControlRequest:
    update_id: int
    chat_id: int
    action: str
    target_chat_id: int | None = None
    name: str = ''
    role: str = ''
    job_id: str = ''
    gmailvip_api_key: str = field(default='', repr=False)
    one_trieu_api_key: str = field(default='', repr=False)
    page: int = 1


@dataclass(frozen=True)
class TelegramCustomerRequest:
    update_id: int
    chat_id: int
    action: str
    name: str = ''
    code: str = ''
    points: int = 0
    customer: str = ''
    scheduled_at: str | None = None
    job_id: str = ''
    combo: bool = False


def parse_control_command(text, chat_id, role, update_id):
    """Parse private commands; current ledger permissions are checked on dispatch."""
    parts = text.strip().split(maxsplit=3)
    command = parts[0].lower().split('@', 1)[0]
    partner_commands = {'/partner_add':'partner_add','/partner_remove':'partner_remove',
        '/partner_delete':'partner_remove','/partners':'partner_list',
        '/partner':'partner_info','/partner_info':'partner_info'}
    if command in partner_commands:
        if role != 'admin':
            raise ValueError('Chỉ admin đang được cấp quyền mới được quản lý partner.')
        action = partner_commands[command]
        if action == 'partner_list':
            if len(parts)>2 or (len(parts)==2 and not re.fullmatch(r'[1-9][0-9]{0,5}',parts[1])):
                raise ValueError('Cú pháp: /partners [SỐ_TRANG]. Ví dụ: /partners 2')
            return TelegramControlRequest(update_id,chat_id,action,page=int(parts[1]) if len(parts)==2 else 1)
        arguments = text.strip().split(maxsplit=2)
        syntax = ('/partner_add CHAT_ID TÊN_PARTNER' if action=='partner_add' else
            '/partner_remove CHAT_ID' if action=='partner_remove' else '/partner CHAT_ID')
        if len(arguments)!=(3 if action=='partner_add' else 2) or not re.fullmatch(r'[1-9][0-9]{0,18}',arguments[1]):
            raise ValueError('Cú pháp: '+syntax)
        ids = parse_chat_ids(arguments[1])
        name = arguments[2].strip() if action=='partner_add' else ''
        if action=='partner_add' and (not name or len(name)>120):
            raise ValueError('Tên partner phải có từ 1 đến 120 ký tự.')
        return TelegramControlRequest(update_id,chat_id,action,ids[0],name)
    if command == '/mailapi':
        if len(parts) == 1:
            return TelegramControlRequest(update_id, chat_id, 'mail_keys_status')
        if len(parts) != 3:
            raise ValueError('Cú pháp: /mailapi KEY_GMAILVIP KEY_1TRIEUMAIL. Cần nhập đủ cả 2 key.')
        keys = validated_user_mail_keys({GMAILVIP: parts[1], ONE_TRIEU: parts[2]})
        return TelegramControlRequest(update_id, chat_id, 'mail_keys_save',
            gmailvip_api_key=keys[GMAILVIP], one_trieu_api_key=keys[ONE_TRIEU])
    if command in ('/lich','/schedules') and len(parts) == 1:
        return TelegramControlRequest(update_id, chat_id, 'schedules')
    if command in ('/huy','/cancel'):
        if len(parts) != 2 or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', parts[1]):
            raise ValueError('Cú pháp: /huy MÃ_JOB. Chỉ hủy job hẹn giờ/chờ của bạn.')
        return TelegramControlRequest(update_id, chat_id, 'cancel_schedule', job_id=parts[1])
    if command == '/stats':
        if len(parts) > 2:
            raise ValueError('Cú pháp: /stats để xem thống kê của bạn.')
        target = chat_id
        if len(parts) == 2:
            ids = parse_chat_ids(parts[1])
            if len(ids) != 1:
                raise ValueError('Nhập một Chat ID để xem thống kê.')
            target = ids[0]
        if target != chat_id:
            raise ValueError('Bạn chỉ được xem thống kê của chính mình qua bot.')
        return TelegramControlRequest(update_id, chat_id, 'stats', target)
    raise ValueError('Lệnh không hợp lệ. Admin dùng /partners, /partner, /partner_add hoặc /partner_remove.')


def parse_job_command(text):
    parts = text.strip().split(maxsplit=3)
    if len(parts) < 3 or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', parts[1]):
        command = '/combo' if parts and parts[0].lower().split('@', 1)[0] == '/combo' else '/job'
        raise ValueError(f'Cú pháp: {command} MÃ_MỜI SỐ_BEAN [TÊN_KHÁCH]. Ví dụ: {command} VBRN5H 1000')
    if not re.fullmatch(r'[0-9]{1,7}', parts[2]):
        raise ValueError('Số kẹo phải là số nguyên dương, tối đa 1.000.000.')
    points = int(parts[2])
    if not 1 <= points <= 1_000_000:
        raise ValueError('Số kẹo phải từ 1 đến 1.000.000.')
    return parts[1].upper(), points, parts[3].strip()[:120] if len(parts) > 3 else ''


def parse_scheduled_job_command(text):
    parts = text.strip().split(maxsplit=5)
    if len(parts) < 5:
        raise ValueError('Cú pháp: /hen MÃ_MỜI SỐ_BEAN DD/MM/YYYY HH:MM [TÊN_KHÁCH]. Giờ Việt Nam.')
    code, points, _ = parse_job_command('/job ' + ' '.join(parts[1:3]))
    try:
        when = datetime.strptime(parts[3] + ' ' + parts[4], '%d/%m/%Y %H:%M')
    except ValueError:
        raise ValueError('Ngày giờ không hợp lệ. Dùng DD/MM/YYYY HH:MM, giờ Việt Nam.') from None
    return code, points, parts[5].strip()[:120] if len(parts) > 5 else '', schedule_time(when, future=True).isoformat()


class TelegramApiError(Exception):
    def __init__(self, code, description, retry_after=0):
        self.code, self.retry_after = code, retry_after
        super().__init__(description)


class TelegramApi:
    def __init__(self, token, session):
        validate_bot_token(token)
        self._token, self._session = token, session

    async def call(self, method, payload=None):
        if method not in ('getMe', 'getWebhookInfo', 'getUpdates', 'sendMessage'):
            raise ValueError('Phương thức Telegram không được hỗ trợ.')
        try:
            response = await self._session.post(f'https://api.telegram.org/bot{self._token}/{method}',
                json=payload or {}, timeout=30, verify=True)
            data = response.json()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Network exceptions often contain the URL, which includes the token.
            raise TelegramApiError(0, 'Không kết nối được Telegram; kiểm tra mạng.') from None
        if not isinstance(data, dict):
            raise TelegramApiError(0, 'Telegram trả dữ liệu không hợp lệ.')
        if data.get('ok') is not True or not 200 <= response.status_code < 300:
            try:
                code = int(data.get('error_code') or response.status_code)
            except (TypeError, ValueError):
                code = 0
            description = str(data.get('description') or 'Telegram từ chối yêu cầu.').replace(self._token, '[ẩn token]')
            parameters = data.get('parameters')
            retry = parameters.get('retry_after', 0) if isinstance(parameters, dict) else 0
            try:
                retry = max(0, float(retry))
            except (TypeError, ValueError):
                retry = 0
            raise TelegramApiError(code, description, retry)
        return data.get('result')


class TelegramBotService:
    ADMIN_HELP = ('\nQuản lý partner (admin):\n'
        '/partner_add CHAT_ID TÊN: thêm hoặc khôi phục partner.\n'
        '/partner_remove CHAT_ID: xóa quyền partner, giữ lịch sử.\n'
        '/partners [TRANG]: danh sách partner.\n'
        '/partner CHAT_ID: thông tin và thống kê của partner.')
    HELP = ('Lệnh tạo job: /job MÃ_MỜI SỐ_KẸO [TÊN_KHÁCH]\n'
        '/mailapi KEY_GMAILVIP KEY_1TRIEUMAIL: lưu đủ 2 API mua mail riêng. /mailapi: xem trạng thái đã nhập.\n'
        'Ví dụ: /job VBRN5H 1000 Khách A\n'
        'Combo: /combo MÃ_MỜI SỐ_BEAN [TÊN_KHÁCH]; đạt giới hạn ngày sẽ tự tiếp tục sau 24 giờ 1 phút đến khi đủ bean.\n'
        'Hẹn giờ: /hen MÃ_MỜI SỐ_BEAN DD/MM/YYYY HH:MM [TÊN_KHÁCH] (giờ Việt Nam).\n'
        '/lich: xem lịch. /huy MÃ_JOB: hủy lịch/job chưa chạy của bạn.\n'
        '/id: xem chat ID. /stats: xem thống kê của bạn.\n'
        'Job dùng cấu hình đang chọn trong tool; tool cần đang mở.')

    def __init__(self, api, access, on_job: Callable, on_status: Callable = lambda text: None, *, on_control=None, on_customer=None):
        self.api, self.access, self.on_job, self.on_status = api, access, on_job, on_status
        self.offset = 0
        self.username = ''
        self.outbox = asyncio.Queue(maxsize=256)
        self.pending = set()
        self.on_control = on_control
        self.on_customer = on_customer

    def reply(self, chat_id, text, *, public=False, admin_only=False):
        if not public and not self.access.allowed(chat_id):
            return False
        if admin_only and (not self.access.allowed(chat_id) or self.access.role(chat_id)!='admin'):
            return False
        try:
            item=(chat_id, str(text)[:4000], public)
            self.outbox.put_nowait(item+(True,) if admin_only else item)
            return True
        except asyncio.QueueFull:
            self.on_status('Hàng đợi phản hồi Telegram đầy; không gửi thêm phản hồi.')
            return False

    def complete(self, request_id):
        self.pending.discard(request_id)

    def handle_update(self, update):
        if not isinstance(update, dict) or type(update.get('update_id')) is not int:
            return
        update_id = update['update_id']
        if update_id < self.offset:
            return
        # Advance before dispatch: repeated updates cannot create a second job.
        self.offset = update_id + 1
        message = update.get('message')
        if not isinstance(message, dict):
            return
        chat, sender = message.get('chat'), message.get('from')
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return
        chat_id = chat.get('id')
        if (chat.get('type') != 'private' or type(chat_id) is not int or chat_id <= 0
                or type(sender.get('id')) is not int or sender['id'] != chat_id or sender.get('is_bot')):
            return
        text = message.get('text')
        if not isinstance(text, str) or not text.strip():
            if not self.access.allowed(chat_id) and self.on_customer is not None and len(self.pending)<64:
                name=' '.join(str(sender.get(key) or '') for key in ('first_name','last_name')).strip()[:120]
                self.pending.add(update_id)
                try:
                    self.on_customer(TelegramCustomerRequest(update_id,chat_id,'register',name))
                except Exception:
                    self.pending.discard(update_id)
            return
        command = text.strip().split(maxsplit=1)[0].lower()
        base, _, mention = command.partition('@')
        if mention and mention != self.username.lower():
            return
        if not self.access.allowed(chat_id) and self.on_customer is not None:
            if len(self.pending) >= 64:
                self.reply(chat_id,'Tool đang bận; vui lòng thử lại sau.',public=True)
                return
            name = ' '.join(str(sender.get(key) or '') for key in ('first_name','last_name')).strip()[:120]
            actions = {'/id':'id','/start':'help','/help':'help','/balance':'balance','/sodu':'balance',
                '/nap':'deposit','/topup':'deposit','/orders':'orders','/donhang':'orders',
                '/history':'history','/giaodich':'history','/stats':'balance'}
            try:
                if base in ('/lich','/schedules','/huy','/cancel'):
                    control = parse_control_command(text, chat_id, None, update_id)
                    request = TelegramCustomerRequest(update_id,chat_id,control.action,name,job_id=control.job_id)
                elif base in ('/job','/addjob','/hen','/schedule','/combo'):
                    if base in ('/hen','/schedule'):
                        code,points,customer,when = parse_scheduled_job_command(text)
                    else:
                        code,points,customer = parse_job_command(text)
                        when = None
                    request = TelegramCustomerRequest(update_id,chat_id,'job',name,code,points,customer,when,
                        combo=base == '/combo')
                else:
                    request = TelegramCustomerRequest(update_id,chat_id,actions.get(base,'help'),name)
                self.pending.add(update_id)
                self.on_customer(request)
            except ValueError as exc:
                self.pending.discard(update_id)
                self.reply(chat_id,str(exc),public=True)
            except Exception:
                self.pending.discard(update_id)
                self.reply(chat_id,'Chưa xử lý được yêu cầu khách hàng.',public=True)
            return
        if base == '/id':
            self.reply(chat_id, f'Chat ID của bạn: {chat_id}', public=True)
            return
        if not self.access.allowed(chat_id):
            self.reply(chat_id, f'Chat ID {chat_id} chưa được cấp quyền. Gửi ID này cho quản trị viên.', public=True)
            return
        if base in ('/start', '/help'):
            self.reply(chat_id, self.HELP+(self.ADMIN_HELP if self.access.role(chat_id)=='admin' else ''))
        elif base in ('/stats', '/users', '/user_add', '/user_revoke','/lich','/schedules','/huy','/cancel','/mailapi',
                '/partner_add','/partner_remove','/partner_delete','/partners','/partner','/partner_info'):
            try:
                request = parse_control_command(text, chat_id, self.access.role(chat_id), update_id)
            except ValueError as exc:
                self.reply(chat_id, str(exc))
                return
            if self.on_control is None or len(self.pending) >= 64:
                self.reply(chat_id, 'Chức năng quản lý đang bận hoặc chưa sẵn sàng.')
                return
            self.pending.add(update_id)
            try:
                self.on_control(request)
            except Exception:
                self.pending.discard(update_id)
                self.reply(chat_id, 'Không chuyển được lệnh quản lý tới tool.')
        elif base in ('/job', '/addjob', '/hen', '/schedule', '/combo'):
            try:
                if base in ('/hen','/schedule'):
                    code, points, customer, when = parse_scheduled_job_command(text)
                else:
                    code, points, customer = parse_job_command(text)
                    when = None
            except ValueError as exc:
                self.reply(chat_id, str(exc))
                return
            if len(self.pending) >= 64:
                self.reply(chat_id, 'Tool đang bận nhận yêu cầu; vui lòng thử lại sau.')
                return
            self.pending.add(update_id)
            request = TelegramJobRequest(update_id, chat_id, code, points, customer, when, combo=base == '/combo')
            try:
                self.on_job(request)
            except Exception:
                self.pending.discard(update_id)
                self.reply(chat_id, 'Không chuyển được yêu cầu tạo job tới tool.')
        else:
            self.reply(chat_id, self.HELP+(self.ADMIN_HELP if self.access.role(chat_id)=='admin' else ''))

    async def _send_replies(self):
        while True:
            item = await self.outbox.get()
            chat_id, text, public = item[:3]
            admin_only = len(item)>3 and item[3]
            try:
                for _ in range(3):
                    if not public and not self.access.allowed(chat_id):
                        break
                    if admin_only and (not self.access.allowed(chat_id) or self.access.role(chat_id)!='admin'):
                        break
                    try:
                        await self.api.call('sendMessage', {'chat_id': chat_id, 'text': text})
                        break
                    except TelegramApiError as exc:
                        if exc.code == 429 and exc.retry_after:
                            await asyncio.sleep(exc.retry_after)
                            continue
                        self.on_status('Không gửi được phản hồi Telegram; job trong tool vẫn được giữ.')
                        break  # Lost response is not repeated automatically.
            finally:
                self.outbox.task_done()

    async def run(self):
        identity = await self.api.call('getMe')
        if not isinstance(identity, dict) or not isinstance(identity.get('username'), str):
            raise TelegramApiError(0, 'Không xác định được tên bot Telegram.')
        self.username = identity['username']
        webhook = await self.api.call('getWebhookInfo')
        if isinstance(webhook, dict) and webhook.get('url'):
            raise TelegramApiError(409, 'Bot đang có webhook. Hãy dùng bot riêng cho tool hoặc gỡ webhook trước.')
        # Do not execute old purchase commands queued while this tool was off.
        stale = await self.api.call('getUpdates', {'offset': -1, 'timeout': 0, 'allowed_updates': ['message']})
        if isinstance(stale, list):
            for update in stale:
                if isinstance(update, dict) and type(update.get('update_id')) is int:
                    self.offset = max(self.offset, update['update_id'] + 1)
        self.on_status(f'Bot @{self.username} đang nhận lệnh mới.')
        sender = asyncio.create_task(self._send_replies())
        try:
            while True:
                try:
                    updates = await self.api.call('getUpdates', {'offset': self.offset, 'timeout': 20,
                        'limit': 50, 'allowed_updates': ['message']})
                    if not isinstance(updates, list):
                        raise TelegramApiError(0, 'Danh sách cập nhật Telegram không hợp lệ.')
                    for update in updates:
                        self.handle_update(update)
                    if not updates:
                        await asyncio.sleep(0.1)
                except TelegramApiError as exc:
                    if exc.code in (401, 403, 409):
                        raise
                    self.on_status(str(exc))
                    await asyncio.sleep(max(3, exc.retry_after))
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
