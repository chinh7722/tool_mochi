"""Ordered products across both mail shops; never repeat a paid transaction."""
from dataclasses import dataclass
import re
import unicodedata


MULTI_SHOP = 'multi_shop'
GMAILVIP = 'gmailvip_vn'
ONE_TRIEU = '1trieumail_com'
SHOP_NAMES = {GMAILVIP: 'GmailVip.vn', ONE_TRIEU: '1TrieuMail.com'}
ALIASES = {'gmailvip': GMAILVIP, 'gmailvip.vn': GMAILVIP, GMAILVIP: GMAILVIP,
    '1trieumail': ONE_TRIEU, '1trieumail.com': ONE_TRIEU, ONE_TRIEU: ONE_TRIEU}


@dataclass(frozen=True)
class MailProductChoice:
    provider: str
    product_id: int

    @property
    def label(self):
        return f'{SHOP_NAMES[self.provider]}:{self.product_id}'


def parse_mail_priority(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError('Nhập thứ tự mua, ví dụ gmailvip:3445, 1trieumail:3423.')
    result, seen = [], set()
    for token in re.split(r'[,;\s]+', raw.strip()):
        if not token:
            continue
        parts = token.split(':')
        if len(parts) != 2 or parts[0].lower() not in ALIASES or not re.fullmatch(r'[0-9]+', parts[1]):
            raise ValueError('Mỗi mục cần có dạng gmailvip:3445 hoặc 1trieumail:3423.')
        choice = MailProductChoice(ALIASES[parts[0].lower()], int(parts[1]))
        if not 1 <= choice.product_id <= 2**63 - 1:
            raise ValueError('Product ID phải là số nguyên dương hợp lệ.')
        if choice not in seen:
            seen.add(choice)
            result.append(choice)
    if not result:
        raise ValueError('Danh sách ưu tiên chưa có sản phẩm.')
    return tuple(result)


class MailProductUnavailable(RuntimeError):
    """The shop explicitly rejected a product/quantity without delivering mail."""


def is_product_unavailable(data):
    if data.get('status') != 'error':
        return False
    payload = data.get('data')
    # Any nonempty delivery makes a rejection ambiguous, irrespective of format.
    if payload not in (None, '', [], {}):
        return False
    text = str(data.get('msg') or data.get('message') or '').casefold()
    plain = ''.join(c for c in unicodedata.normalize('NFD', text) if not unicodedata.combining(c)).replace('đ', 'd')
    return any(term in plain for term in ('het hang', 'het so luong', 'khong du so luong',
        'khong du hang', 'khong con hang', 'out of stock', 'out-of-stock', 'sold out',
        'insufficient stock', 'not enough stock', 'san pham khong ton tai',
        'san pham khong hop le', 'product not found', 'invalid product'))


async def buy_priority_batch(priority, api_keys, amount, *, provider_types=None,
        proxy=None, on_wait=None, on_rate_limit=None, on_purchase=None, job_id='', on_choice=None, purchase_route=None):
    """Try the whole requested batch in priority order; stop after first paid success."""
    if type(amount) is not int or amount <= 0:
        raise ValueError('Số lượng mail phải là số nguyên dương.')
    from core.mail.providers.purchase_policy import wait_for_purchase_retry
    if provider_types is None:
        from core.mail.providers.gmailvip_vn import GmailVipVn
        from core.mail.providers.one_trieu_mail import OneTrieuMail
        provider_types = {GMAILVIP: GmailVipVn, ONE_TRIEU: OneTrieuMail}
    choices = parse_mail_priority(priority)
    for choice in choices:
        if not str(api_keys.get(choice.provider, '')).strip():
            raise ValueError(f'Chưa nhập API key {SHOP_NAMES[choice.provider]}. Chưa mua mail.')
    errors = []
    current_proxy = proxy
    for index, choice in enumerate(choices):
        if on_choice is not None:
            on_choice(f'Thử {choice.label}, số lượng {amount} mail...')
        async def rotate():
            nonlocal current_proxy
            current_proxy = await on_rate_limit()
            return current_proxy
        options = {'proxy': current_proxy, 'on_wait': on_wait,
            'on_rate_limit': rotate if current_proxy is not None and on_rate_limit is not None else None}
        if on_purchase is not None:
            options.update(on_purchase=on_purchase, job_id=job_id)
        if purchase_route is not None:
            options['purchase_route']=purchase_route
        try:
            async with provider_types[choice.provider](str(api_keys[choice.provider]).strip(), **options) as client:
                accounts = await client.buy_gmail_domain_info([choice.product_id], amount=amount)
            if not accounts:
                raise ValueError('Shop đã trả thành công nhưng không có mail; dừng để kiểm tra giao dịch.')
            return accounts
        except MailProductUnavailable:
            errors.append(choice.label)
            if on_choice is not None:
                on_choice(f'{choice.label} không đủ hàng/sản phẩm không còn; chuyển mục ưu tiên tiếp theo.')
            if index + 1 < len(choices):
                await wait_for_purchase_retry(on_wait or on_choice)
    raise MailProductUnavailable('Tất cả mục ưu tiên đều chưa mua được do hết hàng hoặc sản phẩm không còn: '
        + ', '.join(errors))
