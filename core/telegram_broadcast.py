"""Cancellable operator announcements with confirmed delivery counts."""
import asyncio
from dataclasses import dataclass, replace

from core.telegram_bot import TelegramApiError

MAX_ANNOUNCEMENT_LENGTH = 4000


def announcement_length(text):
    try:
        return len(text.encode('utf-16-le')) // 2
    except (AttributeError,UnicodeError):
        raise ValueError('Nội dung thông báo không hợp lệ.') from None


def validate_announcement(text):
    if not isinstance(text,str) or not text.strip():
        raise ValueError('Nhập nội dung thông báo trước khi gửi.')
    if announcement_length(text)>MAX_ANNOUNCEMENT_LENGTH:
        raise ValueError('Thông báo quá dài; tối đa 4000 ký tự theo bộ đếm.')
    return text


@dataclass(frozen=True)
class BroadcastProgress:
    broadcast_id: str
    total: int
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    finished: bool = False
    cancelled: bool = False
    error: str = ''

    @property
    def remaining(self):
        return self.total-self.sent-self.failed-self.skipped


async def send_announcement(api, chat_ids, text, *, broadcast_id, allowed, on_progress,
        interval=0.05):
    """Only retry explicit Telegram 429 rejections; uncertain sends are never repeated."""
    text=validate_announcement(text)
    chat_ids=tuple(chat_ids)
    if any(type(chat_id) is not int or not 1<=chat_id<=2**63-1 for chat_id in chat_ids):
        raise ValueError('Danh sách người nhận thông báo không hợp lệ.')
    recipients=tuple(dict.fromkeys(chat_ids))
    if not recipients:
        raise ValueError('Chưa có admin/partner đang được cấp quyền để nhận thông báo.')
    state=BroadcastProgress(broadcast_id,len(recipients))

    def publish():
        try:
            on_progress(state)
        except Exception:
            pass  # A UI callback must not cause duplicate delivery attempts.

    publish()
    try:
        for chat_id in recipients:
            if not allowed(chat_id):
                state=replace(state,skipped=state.skipped+1)
                publish()
                continue
            for attempt in range(3):
                # Leave the polling loop and job notifications responsive.
                await asyncio.sleep(max(0,interval))
                if not allowed(chat_id):
                    state=replace(state,skipped=state.skipped+1)
                    break
                try:
                    await api.call('sendMessage',{'chat_id':chat_id,'text':text})
                except TelegramApiError as exc:
                    if exc.code==429 and exc.retry_after>0:
                        await asyncio.sleep(exc.retry_after)
                        if attempt<2:
                            continue
                    state=replace(state,failed=state.failed+1,error=(
                        f'Telegram từ chối gửi (mã {exc.code}).' if exc.code
                        else 'Lỗi mạng; chưa xác nhận được tin đã gửi và không tự gửi lại.'))
                    break
                except Exception:
                    state=replace(state,failed=state.failed+1,
                        error='Lỗi mạng; chưa xác nhận được tin đã gửi và không tự gửi lại.')
                    break
                else:
                    state=replace(state,sent=state.sent+1)
                    break
            publish()
        state=replace(state,finished=True)
    except asyncio.CancelledError:
        state=replace(state,finished=True,cancelled=True)
    except Exception:
        state=replace(state,finished=True,cancelled=True,error='Đã dừng vì không kiểm tra được quyền người nhận.')
    publish()
    return state
