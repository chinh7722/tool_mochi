"""Authenticated, read-only SePay v2 polling for a desktop wallet."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import uuid


@dataclass(frozen=True)
class SePayConfig:
    token: str = ''
    bank_id: str = ''
    account_number: str = ''
    bank_name: str = ''
    account_name: str = ''
    start_at: str = ''
    enabled: bool = False

    @classmethod
    def from_settings(cls, settings):
        values = [str(settings.value('sepay/'+key,'') or '').strip() for key in
            ('token','bank_id','account_number','bank_name','account_name','start_at')]
        return cls(*values, str(settings.value('sepay/enabled',False)).lower() in ('true','1'))

    def validate(self):
        if not self.token or any(c.isspace() for c in self.token):
            raise ValueError('Nhập API Token SePay hợp lệ.')
        try:
            if str(uuid.UUID(self.bank_id)) != self.bank_id:
                raise ValueError()
        except ValueError:
            raise ValueError('Bank Account ID SePay v2 phải là UUID tài khoản ngân hàng.') from None
        if not self.account_number or not self.account_number.isascii() or not self.account_number.isdigit():
            raise ValueError('Nhập số tài khoản nhận tiền chỉ gồm chữ số.')
        if not self.bank_name or not self.account_name:
            raise ValueError('Nhập tên ngân hàng và tên chủ tài khoản nhận tiền.')
        date = datetime.fromisoformat(self.start_at)
        if date.tzinfo is None:
            raise ValueError('Mốc nhận tiền phải có múi giờ.')

    def save(self, settings):
        for key in ('token','bank_id','account_number','bank_name','account_name','start_at','enabled'):
            settings.setValue('sepay/'+key,getattr(self,key))


class SePayError(Exception):
    def __init__(self, message, retry_after=30):
        self.retry_after = retry_after
        super().__init__(message)


class SePayClient:
    def __init__(self, config, session):
        config.validate()
        self.config, self.session = config, session

    async def sync(self, store):
        config = self.config
        cursor = store.sepay_cursor(config.bank_id)
        base = {'bank_account_id':config.bank_id,'per_page':100,'timestamp_format':'iso8601'}
        if cursor:
            base['since_id'] = cursor
        else:
            date = datetime.fromisoformat(config.start_at).astimezone(timezone(timedelta(hours=7)))
            base.update(transaction_date_from=date.strftime('%Y-%m-%d %H:%M:%S'),transaction_date_sort='asc')
        credited, last_id, seen = 0, '', set()
        for page in range(1,1001):
            try:
                response = await self.session.get('https://userapi.sepay.vn/v2/transactions',
                    params={**base,'page':page},headers={'Authorization':'Bearer '+config.token},
                    timeout=20,verify=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                raise SePayError('Không kết nối được SePay; số dư đang giữ nguyên.') from None
            if response.status_code == 429:
                try:
                    retry = max(1,min(3600,float(response.headers.get('x-sepay-userapi-retry-after',30))))
                except (ValueError,TypeError):
                    retry = 30
                raise SePayError('SePay yêu cầu giảm tốc độ; sẽ đồng bộ lại.',retry)
            if response.status_code in (401,403):
                raise SePayError('SePay từ chối API Token. Kiểm tra cấu hình.',300)
            if response.status_code != 200:
                raise SePayError('SePay trả lỗi HTTP; chưa hoàn tất đồng bộ.')
            try:
                body = response.json()
                rows = body['data']
                pagination = body['meta']['pagination']
                if (body['status']!='success' or not isinstance(rows,list)
                        or type(pagination['has_more']) is not bool):
                    raise ValueError()
                if pagination['has_more'] and not rows:
                    raise ValueError()
                for row in rows:
                    value = str(uuid.UUID(str(row['id'])))
                    if value in seen:
                        raise ValueError()
                    seen.add(value)
                    credited += int(store.process_sepay_transaction(row,config.bank_id,
                        config.account_number,config.start_at))
                    last_id = value
            except Exception:
                raise SePayError('Dữ liệu SePay chưa hợp lệ; các giao dịch đã ghi vẫn được chống trùng, chưa tiến con trỏ.') from None
            if not pagination['has_more']:
                if last_id:
                    store.set_sepay_cursor(config.bank_id,last_id)
                return credited
            await asyncio.sleep(0.4)
        raise SePayError('Chưa đọc hết trang SePay; giữ con trỏ để tiếp tục đối soát.')
