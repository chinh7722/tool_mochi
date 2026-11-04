"""Job schedules in Vietnam time, without credentials in saved metadata."""
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
    data = {} if data is None else data
    allowed = {'customer_name', 'creator_name', 'total_vnd', 'source_mail_batch', 'combo',
        'price_total_units','price_source_beans','price_source_units','mail_product_ids','stock_account_id'}
    if 'stock_account_id' in data and (type(data['stock_account_id']) is not int or not 1<=data['stock_account_id']<=2**63-1):
        raise ValueError('ID tài khoản kho của lịch job không hợp lệ.')
    if 'mail_product_ids' in data and (not isinstance(data['mail_product_ids'],str) or len(data['mail_product_ids'])>1000):
        raise ValueError('Product ID của lịch job không hợp lệ.')
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError('Thông tin lịch job không hợp lệ.')
    if 'combo' in data and type(data['combo']) is not bool:
        raise ValueError('Chế độ combo phải là giá trị bật hoặc tắt.')
    for key in ('price_total_units','price_source_beans','price_source_units'):
        if key in data and (type(data[key]) is not int or not 0<=data[key]<=2**63-1):
            raise ValueError('Giá đã chốt của job không hợp lệ.')
    payload = json.dumps(data, ensure_ascii=False)
    if len(payload) > 16000:
        raise ValueError('Thông tin lịch job quá dài.')
    return schedule_time(value).isoformat() if value is not None else None, payload
