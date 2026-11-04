"""Daily ledger views and durable completed execution rounds, in Vietnam time."""
import calendar
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import re
from core.pricing import earned_units,price_snapshot
from core.job_schedule import VIETNAM_TIME


def day_of(value):
    if not value:
        return None
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(VIETNAM_TIME).date().isoformat()


def month_days(month):
    if not isinstance(month, str) or not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('Tháng phải có dạng YYYY-MM.')
    year, number = map(int, month.split('-'))
    return [date(year, number, day).isoformat() for day in range(1, calendar.monthrange(year, number)[1]+1)]


class JobStatisticsMixin:
    def _init_statistics(self):
        with self._connection(write=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS job_runs (
                job_id TEXT NOT NULL REFERENCES jobs(job_id), round_key TEXT NOT NULL,
                finished_at TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                success_count INTEGER NOT NULL, attempted_count INTEGER NOT NULL,
                PRIMARY KEY(job_id,round_key))''')
            db.execute('''CREATE TABLE IF NOT EXISTS job_continuations (
                source_id TEXT PRIMARY KEY REFERENCES jobs(job_id),
                child_id TEXT UNIQUE NOT NULL REFERENCES jobs(job_id))''')
            db.execute('CREATE TABLE IF NOT EXISTS temporary_hidden_jobs (job_id TEXT PRIMARY KEY REFERENCES jobs(job_id))')
            # Old final outcomes are known; old intermediate combo rounds are not.
            db.execute('''INSERT INTO job_runs
                SELECT job_id,'legacy',finalized_at,status,'Chưa ghi nhận lý do ở phiên cũ',success_count,attempted_count
                FROM jobs j WHERE finalized_at IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM job_runs r WHERE r.job_id=j.job_id)''')

    def record_job_run(self, job_id, round_key, status, success_count, attempted_count, reason='', *, finished_at=None):
        if not isinstance(round_key, str) or not 1 <= len(round_key) <= 160:
            raise ValueError('Định danh đợt chạy không hợp lệ.')
        if status in ('running', 'pending'):
            raise ValueError('Đợt chạy chưa kết thúc.')
        if any(type(v) is not int or v < 0 for v in (success_count, attempted_count)):
            raise ValueError('Tiến độ đợt chạy không hợp lệ.')
        finished_at = finished_at or datetime.now(timezone.utc).isoformat()
        day_of(finished_at)
        with self._connection(write=True) as db:
            return bool(db.execute('''INSERT OR IGNORE INTO job_runs
                VALUES (?,?,?,?,?,?,?)''', (job_id, round_key, finished_at, status,
                    reason[:1000], success_count, attempted_count)).rowcount)

    def continuation(self, source_id):
        with self._connection() as db:
            row = db.execute('SELECT child_id FROM job_continuations WHERE source_id=?', (source_id,)).fetchone()
            return row[0] if row else None

    def create_continuation(self, source_id, job_id, *, scheduled_at, schedule_data):
        """Keep the old outcome; reserve only the unfulfilled target in a child job."""
        from core.customer_accounts import stamp
        with self._connection(write=True) as db:
            source = db.execute('''SELECT j.*,u.active,co.order_id FROM jobs j JOIN users u USING(chat_id)
                LEFT JOIN customer_orders co USING(job_id) WHERE job_id=?''', (source_id,)).fetchone()
            if source is None or source['finalized_at'] is None or source['status']=='completed':
                raise ValueError('Chỉ tiếp tục job lỗi hoặc đã dừng còn thiếu bean.')
            if json.loads(source['schedule_data'] or '{}').get('stock_account_id'):
                raise ValueError('Job thuộc kho acc. Dùng Tiếp tục trong Kho tài khoản để giữ đúng tiến độ và trạng thái xuất.')
            if db.execute('SELECT 1 FROM job_continuations WHERE source_id=?', (source_id,)).fetchone():
                raise ValueError('Job đã có lần tiếp tục. Chọn job tiếp tục mới nhất.')
            remaining = source['target_count']-source['success_count']
            if remaining <= 0:
                raise ValueError('Job đã đủ bean.')
            if not source['active'] and not source['order_id']:
                raise ValueError('Người tạo job đã bị thu hồi quyền.')
            rate = source['sale_rate_units']
            source_total=price_snapshot(source['schedule_data'])
            reserved=rate*remaining if rate is not None else 0
            if source_total is not None:
                reserved=source_total-earned_units(source['success_count'],source['target_count'],rate,source['schedule_data'])
                original=json.loads(source['schedule_data'])
                schedule_data={**schedule_data,'price_total_units':reserved,
                    'price_source_beans':original.get('price_source_beans',source['target_count']*100),
                    'price_source_units':original.get('price_source_units',source_total)}
            now = stamp()
            if source['order_id']:
                customer = db.execute('SELECT deleted_at FROM customers WHERE chat_id=?', (source['chat_id'],)).fetchone()
                if customer is None or customer[0] or not rate or self._customer_balance(db, source['chat_id']) < reserved:
                    raise ValueError('Khách đã xóa hoặc số dư không đủ để tiếp tục phần bean còn thiếu.')
            db.execute('''INSERT INTO jobs(job_id,chat_id,code,requested_beans,target_count,
                bot_fingerprint,created_at,updated_at,sale_rate_units,finance_tracked,
                dispatch_state,scheduled_at,schedule_data) VALUES (?,?,?,?,?,?,?,?,?,1,'scheduled',?,?)''',
                (job_id, source['chat_id'], source['code'], remaining*100, remaining,
                    source['bot_fingerprint'], now, now, rate, scheduled_at, json.dumps(schedule_data,ensure_ascii=False)))
            if source['order_id']:
                import uuid
                order_id = 'DH'+uuid.uuid4().hex.upper()
                db.execute('INSERT INTO customer_orders VALUES (?,?,?,?,NULL,?,NULL)',
                    (order_id,job_id,source['chat_id'],reserved,now))
                self._wallet_entry(db,source['chat_id'],'reserve',-reserved,'reserve:'+order_id,
                    'Giữ tiền phần còn thiếu của '+source_id)
            db.execute('INSERT INTO job_continuations VALUES (?,?)', (source_id,job_id))
        return True

    def _statistics_rows(self, db):
        rows = db.execute('''SELECT j.job_id,j.chat_id,u.name,j.code,j.status,j.target_count,
            j.success_count,j.failure_count,j.created_at,j.finalized_at,j.sale_rate_units,j.schedule_data,
            co.order_id,co.charged_units,co.settled_at,
            COALESCE(p.cost,0) AS cost_units,COALESCE(p.pending,0) AS pending_costs
            FROM jobs j JOIN users u USING(chat_id)
            LEFT JOIN customer_orders co USING(job_id)
            LEFT JOIN (SELECT job_id,SUM(cost_units) AS cost,
                SUM(CASE WHEN cost_units IS NULL THEN 1 ELSE 0 END) AS pending
                FROM mail_purchases GROUP BY job_id) p USING(job_id)
            WHERE j.finalized_at IS NOT NULL ORDER BY j.finalized_at DESC,j.job_id''')
        result = []
        for row in rows:
            r = dict(row)
            unpriced = not r['order_id'] and r['status']=='completed' and r['sale_rate_units'] is None
            revenue = (r['charged_units'] or 0) if r['order_id'] else (
                earned_units(r['success_count'],r['target_count'],r['sale_rate_units'],r['schedule_data']) if r['status']=='completed' else 0)
            r.update(day=day_of(r['finalized_at']), revenue_units=revenue,
                profit_units=None if unpriced or r['pending_costs'] or (r['order_id'] and not r['settled_at'])
                    else revenue-r['cost_units'], unpriced=bool(unpriced))
            del r['sale_rate_units'],r['charged_units'],r['settled_at'],r['schedule_data']
            result.append(r)
        return result

    def statistics(self, month, *, day=None, owner_id=None, offset=0, limit=100):
        days = month_days(month)
        if day is not None and day not in days:
            raise ValueError('Ngày phải nằm trong tháng đã chọn.')
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=500:
            raise ValueError('Phân trang không hợp lệ.')
        if owner_id is not None and (type(owner_id) is not int or owner_id==0 or owner_id < -1):
            raise ValueError('Người dùng không hợp lệ.')
        with self._connection() as db:
            db.execute('BEGIN')
            jobs = self._statistics_rows(db)
            runs = [dict(r) for r in db.execute('''SELECT r.job_id,j.chat_id,u.name,j.code,
                r.finished_at,r.status,r.reason,r.success_count,j.schedule_data AS _configuration FROM job_runs r
                JOIN jobs j USING(job_id) JOIN users u USING(chat_id)
                WHERE j.job_id NOT GLOB 'stock_*' ORDER BY r.finished_at DESC''')]
            owners = [dict(r) for r in db.execute('SELECT chat_id,name FROM users ORDER BY name,chat_id')]
        monthly = [r for r in jobs if r['day'] in days]
        daily = {d:dict(day=d, jobs=0, customers=0, unique_customers=0, revenue_units=0,cost_units=0,profit_units=0,pending_jobs=0) for d in days}
        for r in monthly:
            d = daily[r['day']]
            d['jobs'] += 1
            for field in ('revenue_units','cost_units'):
                d[field] += r[field]
            if r['profit_units'] is None:
                d['pending_jobs'] += 1
            else:
                d['profit_units'] += r['profit_units']
        reasons = Counter()
        today = datetime.now(VIETNAM_TIME).date()
        week_start = today-timedelta(days=today.weekday())
        counts = dict(today=0,week=0,month=0)
        selected_runs = []
        daily_codes={d:set() for d in days}
        code_groups={}
        for r in runs:
            try:
                config=json.loads(r.pop('_configuration'))
                combo=isinstance(config,dict) and config.get('combo') is True
            except (TypeError,ValueError):
                combo=False
            r['day'] = day_of(r['finished_at'])
            if r['day']==today.isoformat(): counts['today'] += 1
            if week_start.isoformat() <= r['day'] <= today.isoformat(): counts['week'] += 1
            if r['day'] in days:
                counts['month'] += 1
                daily[r['day']]['customers'] += 1
                code=r['code'].strip().upper()
                daily_codes[r['day']].add(code)
                if r['status']=='invite_limit_reached' and not combo:
                    reasons['Mã mời đã đạt giới hạn 24 giờ.'] += 1
                elif r['status'] not in ('completed','cancelled','invite_limit_reached'):
                    reasons[r['reason'] or 'Chưa ghi nhận lý do'] += 1
                if (day is None or r['day']==day) and (owner_id is None or r['chat_id']==owner_id):
                    selected_runs.append(r)
                    key=(r['day'],code)
                    group=code_groups.setdefault(key,dict(day=r['day'],code=code,runs=0,owners={}))
                    group['runs']+=1;group['owners'][r['chat_id']]=r['name']
        for d in days:daily[d]['unique_customers']=len(daily_codes[d])
        code_history=[dict(day=g['day'],code=g['code'],runs=g['runs'],
            owners=[dict(chat_id=cid,name=name) for cid,name in g['owners'].items()]) for g in code_groups.values()]
        selected = [r for r in monthly if (day is None or r['day']==day) and (owner_id is None or r['chat_id']==owner_id)]
        groups = {}
        for r in selected:
            g = groups.setdefault(r['chat_id'],dict(chat_id=r['chat_id'],name=r['name'],jobs=0,
                revenue_units=0,cost_units=0,profit_units=0,pending_jobs=0))
            g['jobs'] += 1
            for field in ('revenue_units','cost_units'): g[field] += r[field]
            if r['profit_units'] is None: g['pending_jobs'] += 1
            else: g['profit_units'] += r['profit_units']
        return dict(month=month,day=day,days=list(daily.values()),owners=owners,
            reasons=[dict(reason=k,count=v) for k,v in reasons.items()],customer_counts=counts,
            users=list(groups.values()),history=selected[offset:offset+limit],total=len(selected),
            customer_history=selected_runs[offset:offset+limit],customer_total=len(selected_runs),offset=offset,limit=limit,
            code_history=code_history[offset:offset+limit],code_total=len(code_history),
            note='Tài chính tính theo ngày kết thúc job; lợi nhuận là phần đã đối soát. Mỗi đợt combo kết thúc tính 1 lượt khách. Không suy đoán các đợt combo cũ chưa được ghi.')

    def temporary_history(self, *, offset=0,limit=100):
        if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=500:
            raise ValueError('Phân trang không hợp lệ.')
        with self._connection() as db:
            db.execute('BEGIN')
            clause='FROM jobs j JOIN users u USING(chat_id) WHERE j.finalized_at IS NOT NULL AND NOT EXISTS(SELECT 1 FROM temporary_hidden_jobs h WHERE h.job_id=j.job_id)'
            total=db.execute('SELECT COUNT(*) '+clause).fetchone()[0]
            rows=[dict(r) for r in db.execute('''SELECT j.job_id,j.code,j.chat_id,u.name,j.status,j.target_count,
                j.success_count,j.failure_count,j.created_at,j.finalized_at,j.schedule_data,
                (j.status='failed' AND j.success_count<j.target_count AND NOT EXISTS(
                    SELECT 1 FROM job_continuations c WHERE c.source_id=j.job_id)) AS can_continue '''+
                clause+' ORDER BY j.finalized_at DESC,j.job_id LIMIT ? OFFSET ?',(limit,offset))]
        for row in rows:
            if json.loads(row.pop('schedule_data') or '{}').get('stock_account_id'):row['can_continue']=False
        return dict(rows=rows,total=total,offset=offset,limit=limit)

    def clear_temporary_history(self):
        """Hide only outcomes already finished; never alter jobs, rounds, money or logs."""
        with self._connection(write=True) as db:
            count=db.execute('''INSERT OR IGNORE INTO temporary_hidden_jobs
                SELECT job_id FROM jobs WHERE finalized_at IS NOT NULL''').rowcount
        return dict(hidden=count,message=f'Đã xóa {count} job khỏi lịch sử tạm. Lịch sử Thống kê và log vẫn được giữ.')
