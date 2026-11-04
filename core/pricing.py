"""Exact integer quotes from configurable bean milestones and immutable job snapshots."""
import json
from core.finance import MAX_MONEY_UNITS

SETTING = 'finance/bean_prices'


def validate_prices(rows):
    if not isinstance(rows,list) or len(rows)>100:
        raise ValueError('Bảng giá tối đa 100 mốc.')
    result=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'beans','price_units'}:
            raise ValueError('Mỗi mốc cần số bean và giá tiền.')
        beans,price=row['beans'],row['price_units']
        if type(beans) is not int or not 100<=beans<=1_000_000 or beans%100:
            raise ValueError('Mốc bean phải là bội số 100, từ 100 đến 1.000.000.')
        if type(price) is not int or not 0<price<=MAX_MONEY_UNITS:
            raise ValueError('Giá phải lớn hơn 0 và trong giới hạn lưu trữ.')
        if beans in seen:raise ValueError('Mỗi mốc bean chỉ được có một giá.')
        seen.add(beans);result.append(dict(beans=beans,price_units=price))
    return sorted(result,key=lambda r:r['beans'])


def read_prices(settings):
    raw=settings.value(SETTING,'[]') or '[]'
    try:return validate_prices(json.loads(str(raw)))
    except (TypeError,ValueError):raise ValueError('Bảng giá đã lưu không hợp lệ; kiểm tra tab Bảng giá.') from None


def quote_prices(rows, points, fallback_rate=None):
    target=(points+99)//100
    if rows:
        tier=next((r for r in reversed(rows) if r['beans']<=target*100),rows[0])
        total=max(1,(tier['price_units']*target*100+tier['beans']//2)//tier['beans'])
        if total>MAX_MONEY_UNITS:raise ValueError('Giá trị đơn hàng vượt giới hạn lưu trữ.')
        return dict(total_units=total,rate_units=(total+target-1)//target,
            source_beans=tier['beans'],source_price_units=tier['price_units'])
    return dict(total_units=None if fallback_rate is None else target*fallback_rate,
        rate_units=fallback_rate,source_beans=None,source_price_units=None)


def priced_metadata(data, quote):
    result=dict(data or {})
    if quote['source_beans'] is not None:
        result.update(price_total_units=quote['total_units'],price_source_beans=quote['source_beans'],
            price_source_units=quote['source_price_units'])
    return result


def price_snapshot(data):
    try:config=json.loads(data) if isinstance(data,str) else data
    except (TypeError,ValueError):return None
    value=config.get('price_total_units') if isinstance(config,dict) else None
    return value if type(value) is int and 0<=value<=MAX_MONEY_UNITS else None


def earned_units(success, target, rate, data='{}'):
    total=price_snapshot(data)
    if total is not None:
        return total*min(max(0,success),target)//max(1,target)
    return success*(rate or 0)
