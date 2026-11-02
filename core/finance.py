"""Exact VND amounts and credential-free mail purchase receipts."""
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from typing import Optional

MAX_MONEY_UNITS = 2**63 - 1


def money_units(value):
    """Store hundredths of VND as integers, never binary float sums."""
    if value is None or isinstance(value, bool):
        raise ValueError('Số tiền không hợp lệ.')
    try:
        amount = Decimal(str(value).strip())
        if not amount.is_finite() or amount < 0:
            raise ValueError('Số tiền cần không âm và có tối đa hai chữ số thập phân.')
        if amount > Decimal(MAX_MONEY_UNITS) / 100:
            raise ValueError('Số tiền vượt giới hạn lưu trữ.')
        rounded = amount.quantize(Decimal('0.01'))
        if amount != rounded:
            raise ValueError('Số tiền cần có tối đa hai chữ số thập phân.')
        return int(rounded * 100)
    except (DecimalException, TypeError, OverflowError) as exc:
        raise ValueError('Số tiền không hợp lệ.') from exc


def format_vnd(value):
    amount = Decimal(value)
    return format(amount, ',.0f') if amount == amount.to_integral_value() else format(amount, ',.2f')


def profile_balance_units(data):
    if not isinstance(data, dict) or data.get('status') != 'success':
        raise ValueError('Shop chưa xác nhận số dư.')
    payload = data.get('data')
    if isinstance(payload, dict) and 'money' in payload:
        return money_units(payload['money'])
    if 'money' in data:
        return money_units(data['money'])
    if isinstance(payload, (int, float, str)) and not isinstance(payload, bool):
        return money_units(payload)
    raise ValueError('Phản hồi shop thiếu số dư money.')


@dataclass(frozen=True)
class MailPurchaseReceipt:
    purchase_id: str
    job_id: str
    provider: str
    account_hash: str
    product_id: int
    quantity: int
    cost_units: Optional[int]
    order_id: str = ''
    source: str = 'balance_difference'
    state: str = 'confirmed'
    balance_before_units: Optional[int] = None
    balance_after_units: Optional[int] = None


class MailAccountingError(ValueError):
    """Stop purchasing if a paid receipt cannot be stored durably."""
