"""One-time job schedules in Vietnam time, without credentials in saved metadata."""
from datetime import datetime, timedelta, timezone
import json

VIETNAM_TIME = timezone(timedelta(hours=7))


def schedule_time(value, *, future=False, now=None):
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if result.tzinfo is None:
            result = result.replace(tzinfo=VIETNAM_TIME)
        result = result.astimezone(VIETNAM_TIME)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Ngày giờ hẹn không hợp lệ. Dùng giờ Việt Nam.') from None
    if future and result <= (now or datetime.now(VIETNAM_TIME)):
        raise ValueError('Giờ hẹn phải ở tương lai (giờ Việt Nam).')
    return result


def schedule_text(value):
    return schedule_time(value).strftime('%d/%m/%Y %H:%M') + ' (giờ Việt Nam)'


def schedule_storage(value=None, data=None):
    if value is None:
        return None, '{}'
    data = data or {}
    allowed = {'customer_name', 'creator_name', 'total_vnd', 'source_mail_batch'}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError('Thông tin lịch job không hợp lệ.')
    payload = json.dumps(data, ensure_ascii=False)
    if len(payload) > 16000:
        raise ValueError('Thông tin lịch job quá dài.')
    return schedule_time(value).isoformat(), payload
