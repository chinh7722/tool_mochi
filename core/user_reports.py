"""Credential-free, exact-money CSV reports from one owner ledger snapshot."""
import csv
from datetime import datetime, timezone
import io
import json
from core.pricing import earned_units,price_snapshot

from core.job_schedule import VIETNAM_TIME

MAX_REPORT_BYTES = 6 * 1024 * 1024
_TOO_LARGE = 'Báo cáo vượt giới hạn xuất CSV trong một lượt. Liên hệ quản trị viên để xuất dữ liệu đầy đủ; chưa xuất báo cáo bị cắt bớt.'

JOB_COLUMNS = (
    ('Chat ID', 'chat_id'), ('Tên người dùng', 'name'), ('Vai trò', 'role'), ('Quyền truy cập', 'access'),
    ('Mã job', 'job_id'), ('Mã đơn khách', 'customer_order_id'), ('Mã mời', 'code'),
    ('Tên khách / Ghi chú', 'customer_name'), ('Combo', 'combo'), ('Bean yêu cầu', 'requested_beans'),
    ('Mục tiêu lượt thành công', 'target_count'), ('Lượt đã thử', 'attempted_count'),
    ('Lượt thành công', 'success_count'), ('Lượt lỗi', 'failure_count'), ('Bean thực tế', 'achieved_beans'),
    ('Trạng thái job', 'status'), ('Trạng thái điều phối', 'dispatch_state'),
    ('Đơn giá VND / 100 bean', 'sale_rate'), ('Doanh thu đã ghi nhận (VND)', 'revenue'),
    ('Giá chốt theo bảng giá (VND)', 'quoted_total'),
    ('Chi phí mail đã xác định (VND)', 'known_cost'), ('Chi phí mail tổng (VND)', 'total_cost'),
    ('Giao dịch mail chờ đối soát', 'pending_costs'),
    ('Lợi nhuận đã xác định (VND)', 'profit'), ('Tình trạng tài chính', 'finance_state'),
    ('Tiền giữ đơn khách (VND)', 'reserved'), ('Tiền đã thu đơn khách (VND)', 'charged'),
    ('Tiền hoàn đơn khách (VND)', 'refunded'), ('Tạo lúc (UTC+7)', 'created_at'),
    ('Cập nhật lúc (UTC+7)', 'updated_at'), ('Kết thúc lúc (UTC+7)', 'finalized_at'),
    ('Hẹn chạy lúc (UTC+7)', 'scheduled_at'), ('Chốt đơn khách lúc (UTC+7)', 'settled_at'),
)

PURCHASE_COLUMNS = (
    ('Chat ID', 'chat_id'), ('Tên người dùng', 'name'), ('Vai trò', 'role'), ('Quyền truy cập', 'access'),
    ('Mã giao dịch mua mail', 'purchase_id'), ('Mã job', 'job_id'), ('Mã mời', 'code'),
    ('Nhà cung cấp', 'provider'), ('Product ID', 'product_id'), ('Số mail', 'quantity'),
    ('Chi phí (VND)', 'cost'), ('Số dư shop trước mua (VND)', 'balance_before'),
    ('Số dư shop sau mua (VND)', 'balance_after'), ('Mã đơn shop', 'order_id'),
    ('Nguồn xác định chi phí', 'source'), ('Trạng thái giao dịch', 'state'),
    ('Tình trạng chi phí', 'cost_state'), ('Tạo lúc (UTC+7)', 'created_at'),
    ('Cập nhật lúc (UTC+7)', 'updated_at'),
)


class _Numeric(str):
    """Only program-generated numeric cells bypass untrusted text sanitization."""


def _money(units):
    if units is None:
        return ''
    units = int(units)
    amount = abs(units)
    return _Numeric(('-' if units < 0 else '') + str(amount // 100) + '.' + f'{amount % 100:02d}')


def _time(value):
    if not value:
        return ''
    try:
        timestamp = datetime.fromisoformat(str(value))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(VIETNAM_TIME).isoformat(sep=' ', timespec='seconds')
    except (TypeError, ValueError, OverflowError):
        return ''


def _cell(value):
    if value is None:
        return ''
    if isinstance(value, _Numeric):
        return str(value)
    text = str(value)
    # CSV has no typed cell metadata: protect long numeric identifiers from
    # Excel rounding, and untrusted text from formula/control-prefix execution.
    if text.isdecimal() and len(text) > 15:
        return "'" + text
    if isinstance(value, str) and (text.startswith(('\t', '\r', '\n')) or
            text.lstrip(' \t\r\n').startswith(('=', '+', '-', '@'))):
        return "'" + text
    return text


def _owner_columns(owner):
    return dict(chat_id=owner['chat_id'], name=owner['name'],
        role='Local' if owner['chat_id'] == -1 else owner['role'],
        access='Được phép' if owner['active'] else 'Đã thu hồi')


def _job(owner, row):
    row = dict(row)
    try:
        data = json.loads(row.get('schedule_data') or '{}')
        if not isinstance(data, dict):
            data = {}
    except (TypeError, ValueError):
        data = {}
    combo = data.get('combo') is True
    customer = data.get('customer_name', '')
    customer = customer if isinstance(customer, str) else ''
    success = row['success_count']
    rate = row['sale_rate_units']
    has_order = bool(row['customer_order_id'])
    unknown_rate = not has_order and row['status'] == 'completed' and rate is None
    revenue = (row['charged_units'] or 0) if has_order else (
        None if unknown_rate else earned_units(success,row['target_count'],rate,row.get('schedule_data')) if row['status'] == 'completed' and rate is not None else 0)
    pending = row['pending_costs']
    incomplete_order = has_order and row['settled_at'] is None
    notes = []
    if pending:
        notes.append('Chờ đối soát chi phí mail')
    if unknown_rate:
        notes.append('Chưa có đơn giá')
    if incomplete_order:
        notes.append('Đơn khách chưa chốt')
    if row['status'] == 'running' and not has_order:
        notes.append('Job chưa kết thúc; doanh thu chưa ghi nhận')
    status = {'completed':'Hoàn thành', 'failed':'Thất bại', 'cancelled':'Đã hủy', 'running':'Đang xử lý'}.get(row['status'],row['status'])
    if combo and row['status'] == 'running' and row['dispatch_state'] == 'scheduled':
        status = 'Chờ lượt combo'
    cost = row['known_cost_units']
    profit = None if pending or unknown_rate or incomplete_order else revenue - cost
    refunded = None if not has_order or row['charged_units'] is None else row['reserved_units'] - row['charged_units']
    return {**_owner_columns(owner),
        'job_id':row['job_id'], 'customer_order_id':row['customer_order_id'] or '', 'code':row['code'],
        'customer_name':customer, 'combo':'Có' if combo else 'Không', 'requested_beans':row['requested_beans'],
        'target_count':row['target_count'], 'attempted_count':row['attempted_count'],
        'success_count':success, 'failure_count':row['failure_count'], 'achieved_beans':success*100,
        'status':status, 'dispatch_state':{'running':'Đang xử lý', 'queued':'Đang chờ',
            'scheduled':'Hẹn giờ', 'dispatching':'Đang cấp chạy'}.get(row['dispatch_state'],row['dispatch_state']),
        'sale_rate':_money(rate), 'quoted_total':_money(price_snapshot(row.get('schedule_data'))), 'revenue':_money(revenue), 'known_cost':_money(cost),
        'total_cost':_money(None if pending else cost),
        'pending_costs':pending, 'profit':_money(profit), 'finance_state':'; '.join(notes) or 'Đã xác định',
        'reserved':_money(row['reserved_units']), 'charged':_money(row['charged_units']),
        'refunded':_money(refunded), **{key:_time(row[key]) for key in
            ('created_at','updated_at','finalized_at','scheduled_at','settled_at')}}


def _purchase(owner, row):
    row = dict(row)
    return {**_owner_columns(owner), 'purchase_id':row['purchase_id'], 'job_id':row['job_id'],
        'code':row['code'], 'provider':row['provider'], 'product_id':row['product_id'],
        'quantity':row['quantity'], 'cost':_money(row['cost_units']),
        'balance_before':_money(row['balance_before_units']), 'balance_after':_money(row['balance_after_units']),
        'order_id':row['order_id'], 'source':row['source'], 'state':row['state'],
        'cost_state':'Chờ đối soát' if row['cost_units'] is None else
            'Biên nhận trùng; không cộng chi phí' if row['state']=='duplicate' else 'Đã xác định',
        'created_at':_time(row['created_at']), 'updated_at':_time(row['updated_at'])}


def build_user_report(owner, jobs, purchases, *, kind='all', generated_at=None):
    """Render every selected row or reject the whole report; never truncate it."""
    if kind not in ('all','jobs','purchases'):
        raise ValueError('Loại báo cáo phải là all, jobs hoặc purchases.')
    generated_at = generated_at or datetime.now(VIETNAM_TIME)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=VIETNAM_TIME)
    generated_at = generated_at.astimezone(VIETNAM_TIME)
    owner = {key:owner[key] for key in ('chat_id','name','role','active')}
    owner['active'] = bool(owner['active'])
    report = dict(owner=owner, files=[], generated_at=generated_at.isoformat(timespec='seconds'))
    used_bytes = 0
    for report_kind, columns, rows, transform in (('jobs',JOB_COLUMNS,jobs,_job),
            ('purchases',PURCHASE_COLUMNS,purchases,_purchase)):
        if kind not in ('all',report_kind):
            continue
        out = io.StringIO(newline='')
        out.write('\ufeff')
        writer = csv.writer(out,lineterminator='\r\n')
        writer.writerow([header for header,_ in columns])
        count = 0
        # Measure each serialized row, bounding memory while consuming cursors.
        line = io.StringIO(newline='')
        line_writer = csv.writer(line,lineterminator='\r\n')
        for row in rows:
            data = transform(owner,row)
            line.seek(0)
            line.truncate(0)
            line_writer.writerow([_cell(data[key]) for _,key in columns])
            text = line.getvalue()
            used_bytes += len(text.encode('utf-8'))
            if used_bytes > MAX_REPORT_BYTES:
                raise ValueError(_TOO_LARGE)
            out.write(text)
            count += 1
        identifier = 'local' if owner['chat_id'] == -1 else str(owner['chat_id'])
        report['files'].append(dict(kind=report_kind,
            filename=f'hoshino_{identifier}_{report_kind}_{generated_at:%Y%m%d_%H%M%S}.csv',
            text=out.getvalue(),row_count=count))
    # JSON quoting adds bytes; enforce the cap on the full API representation.
    if len(json.dumps(report,ensure_ascii=False).encode('utf-8')) > MAX_REPORT_BYTES:
        raise ValueError(_TOO_LARGE)
    return report
