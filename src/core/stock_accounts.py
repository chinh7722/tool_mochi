"""Durable inventory of created Mochi accounts and two-phase exports."""
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from core.job_schedule import VIETNAM_TIME
from core.mail.providers.priority import GMAILVIP, ONE_TRIEU


def creation_mail_keys(values):
    if not isinstance(values,dict) or set(values)-{GMAILVIP,ONE_TRIEU}:
        raise ValueError('API mua mail tạo acc không hợp lệ.')
    result={}
    for provider in (GMAILVIP,ONE_TRIEU):
        value=values.get(provider,'')
        if not isinstance(value,str) or len(value)>1000 or '://' in value or any(c.isspace() or ord(c)<32 or ord(c)==127 for c in value.strip()):
            raise ValueError('API mua mail tạo acc phải là chuỗi không có khoảng trắng, tối đa 1.000 ký tự.')
        result[provider]=value.strip()
    return result


def now():
    return datetime.now(VIETNAM_TIME).isoformat()


def selected_ids(values):
    if (not isinstance(values, list) or not 1 <= len(values) <= 500
            or any(type(v) is not int or v < 1 for v in values) or len(set(values)) != len(values)):
        raise ValueError('Chọn từ 1 đến 500 tài khoản khác nhau.')
    return values


def creation_options(count, beans, infinite, product_ids):
    if type(count) is not int or not 1 <= count <= 500:
        raise ValueError('Số acc phải từ 1 đến 500.')
    if type(infinite) is not bool:
        raise ValueError('Chế độ vô hạn phải là bật hoặc tắt.')
    if type(beans) is not int or not 100 <= beans <= 1_000_000 or beans % 100:
        raise ValueError('Bean mỗi acc phải là bội số 100, từ 100 đến 1.000.000.')
    if not isinstance(product_ids, str) or not product_ids.strip() or len(product_ids) > 1000:
        raise ValueError('Nhập product ID mua mail.')
    return count, beans, infinite, product_ids.strip()


class StockAccountStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS stock_batches (
                    id TEXT PRIMARY KEY, count INTEGER NOT NULL, target INTEGER NOT NULL,
                    infinite INTEGER NOT NULL, product_ids TEXT NOT NULL, owner INTEGER NOT NULL,
                    state TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    request_key TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS stock_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL,
                    email TEXT NOT NULL COLLATE NOCASE UNIQUE, password TEXT NOT NULL,
                    invite_code TEXT NOT NULL DEFAULT '', beans TEXT NOT NULL DEFAULT '0',
                    target INTEGER NOT NULL, infinite INTEGER NOT NULL, status TEXT NOT NULL,
                    job_id TEXT, job_base TEXT NOT NULL DEFAULT '0', next_at TEXT,
                    message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stock_exports (
                    id TEXT PRIMARY KEY, actor TEXT NOT NULL, state TEXT NOT NULL,
                    account_ids TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
                    completed_at TEXT);
                CREATE TABLE IF NOT EXISTS stock_creation_keys (
                    owner INTEGER PRIMARY KEY, keys_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS stock_batch_keys (
                    batch_id TEXT PRIMARY KEY, provider TEXT NOT NULL, keys_json TEXT NOT NULL);
            ''')
        if os.name != 'nt':
            self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_batch(self, count, beans, infinite, product_ids, owner=-1, request_key=None, *, mail_keys=None,provider=None):
        count, beans, infinite, product_ids = creation_options(count, beans, infinite, product_ids)
        keys=creation_mail_keys(mail_keys) if mail_keys is not None else None
        with self.connection() as db:
            if request_key:
                existing = db.execute('SELECT id FROM stock_batches WHERE request_key=?', (request_key,)).fetchone()
                if existing:
                    return existing['id']
            if db.execute("SELECT 1 FROM stock_batches WHERE state IN ('queued','buying','creating')").fetchone():
                raise ValueError('Đang có một lô tạo acc. Chờ lô hiện tại kết thúc trước khi tạo lô mới.')
            bid = 'stock_' + uuid.uuid4().hex[:20]
            db.execute('INSERT INTO stock_batches VALUES (?,?,?,?,?,?,?, ?,?,?)',
                (bid, count, beans, int(infinite), product_ids, owner, 'queued', '', now(), request_key))
            if keys is not None:
                db.execute('INSERT INTO stock_batch_keys VALUES (?,?,?)',(bid,provider,json.dumps(keys)))
            return bid

    def set_creation_keys(self,owner,keys):
        keys=creation_mail_keys(keys)
        with self.connection() as db:
            db.execute('INSERT INTO stock_creation_keys VALUES (?,?) ON CONFLICT(owner) DO UPDATE SET keys_json=excluded.keys_json',
                (owner,json.dumps(keys)))

    def get_creation_keys(self,owner):
        with self.connection() as db:
            row=db.execute('SELECT keys_json FROM stock_creation_keys WHERE owner=?',(owner,)).fetchone()
        return creation_mail_keys(json.loads(row[0]) if row else {})

    def batch_mail_settings(self,bid):
        with self.connection() as db:
            row=db.execute('SELECT provider,keys_json FROM stock_batch_keys WHERE batch_id=?',(bid,)).fetchone()
        if row is None:
            raise ValueError('Lô cũ chưa có API tạo acc riêng. Chưa mua mail; tạo lại lô với API riêng.')
        return row['provider'],creation_mail_keys(json.loads(row['keys_json']))

    def batches(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM stock_batches ORDER BY created_at DESC LIMIT 100')]

    def batch_update(self, bid, state, message=''):
        with self.connection() as db:
            db.execute('UPDATE stock_batches SET state=?,message=? WHERE id=?', (state, message, bid))

    def recover(self):
        # Never resubmit a purchase whose result was lost. Already delivered mail is recoverable.
        with self.connection() as db:
            db.execute("UPDATE stock_batches SET state='uncertain',message='Mua mail bị gián đoạn; đối soát shop trước khi tạo lô mới.' WHERE state='buying'")
            db.execute("UPDATE stock_accounts SET status='creating' WHERE status='logging_in'")

    def save_delivery(self, batch, accounts):
        rows, seen = [], set()
        for item in accounts:
            email, password = item.username.strip(), item.password
            if (not re.fullmatch(r'[^@\s|]+@[^@\s|]+\.[^@\s|]+', email) or not isinstance(password, str)
                    or not password or len(password) > 1000 or any(c in password for c in '\r\n\0|')):
                raise ValueError('Shop giao thông tin tài khoản không hợp lệ; đối soát lô đã mua.')
            if email.casefold() in seen:
                raise ValueError('Shop giao email trùng; đối soát lô đã mua.')
            seen.add(email.casefold())
            rows.append((batch['id'], email, password, batch['target'], batch['infinite'], 'creating', now(), now()))
        if not rows:
            raise ValueError('Shop không giao mail; kiểm tra giao dịch trước khi mua lại.')
        with self.connection() as db:
            db.executemany('''INSERT INTO stock_accounts(batch_id,email,password,target,infinite,status,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)''', rows)
            db.execute("UPDATE stock_batches SET state='creating',message=? WHERE id=?",
                (f'Đã nhận {len(rows)}/{batch["count"]} mail. Không tự mua bù phần shop giao thiếu.', batch['id']))

    def update(self, aid, **values):
        allowed = {'invite_code','beans','status','job_id','job_base','next_at','message'}
        if not values or set(values) - allowed:
            raise ValueError('Thông tin kho acc không hợp lệ.')
        values['updated_at'] = now()
        with self.connection() as db:
            db.execute('UPDATE stock_accounts SET ' + ','.join(k+'=?' for k in values) + ' WHERE id=?', (*values.values(), aid))

    def all(self, *, internal=False):
        with self.connection() as db:
            rows = [dict(r) for r in db.execute("SELECT a.*,b.owner,b.product_ids FROM stock_accounts a JOIN stock_batches b ON a.batch_id=b.id WHERE a.status NOT IN ('exported','deleted') ORDER BY a.id")]
        return rows if internal else [r for r in rows if r['invite_code']]

    def summary(self):
        rows = self.all()
        return {'total': len(rows), 'beans': str(sum((Decimal(r['beans']) for r in rows), Decimal(0)))}

    def _chosen(self, db, ids):
        selected_ids(ids)
        rows = [dict(r) for r in db.execute('SELECT * FROM stock_accounts WHERE id IN ('+','.join('?' for _ in ids)+') ORDER BY id', ids)]
        if len(rows) != len(ids) or any(r['status'] in ('exported','deleted','exporting') or not r['invite_code'] for r in rows):
            raise ValueError('Có acc không còn trong kho hoặc đang nằm trong phiếu xuất khác. Làm mới danh sách.')
        return rows

    def delete(self, ids):
        with self.connection() as db:
            rows = self._chosen(db, ids)
            if any(r['status'] not in ('paused','ready','failed') or r['job_id'] for r in rows):
                raise ValueError('Dừng nhập mã cho acc và chờ job kết thúc trước khi xóa.')
            db.executemany("UPDATE stock_accounts SET status='deleted',updated_at=? WHERE id=?", [(now(), r['id']) for r in rows])
        return len(rows)

    def prepare_export(self, ids, actor):
        with self.connection() as db:
            rows = self._chosen(db, ids)
            if any(r['status'] not in ('paused','ready','failed') or r['job_id'] for r in rows):
                raise ValueError('Dừng nhập mã cho acc và chờ job kết thúc trước khi xuất.')
            eid = uuid.uuid4().hex
            content = '\n'.join(r['email']+'|'+r['password'] for r in rows)
            db.execute('INSERT INTO stock_exports VALUES (?,?,?,?,?,?,NULL)',
                (eid, str(actor), 'pending', json.dumps([r['id'] for r in rows]), content, now()))
            db.executemany("UPDATE stock_accounts SET status='exporting',updated_at=? WHERE id=?", [(now(), r['id']) for r in rows])
        return self.export(eid)

    def export(self, eid):
        with self.connection() as db:
            row = db.execute('SELECT * FROM stock_exports WHERE id=?', (eid,)).fetchone()
        if row is None:
            raise ValueError('Không tìm thấy phiếu xuất.')
        return dict(row)

    def finish_export(self, eid):
        with self.connection() as db:
            row = db.execute('SELECT * FROM stock_exports WHERE id=?', (eid,)).fetchone()
            if row is None:
                raise ValueError('Không tìm thấy phiếu xuất.')
            if row['state'] != 'completed':
                ids = json.loads(row['account_ids'])
                db.executemany("UPDATE stock_accounts SET status='exported',updated_at=? WHERE id=? AND status='exporting'", [(now(), i) for i in ids])
                db.execute("UPDATE stock_exports SET state='completed',completed_at=? WHERE id=?", (now(), eid))
        return {'message': 'Đã hoàn tất xuất và đưa acc ra khỏi kho.'}

    def exports(self, offset=0, limit=50):
        with self.connection() as db:
            rows = [dict(r) for r in db.execute('SELECT id,actor,state,account_ids,created_at,completed_at FROM stock_exports ORDER BY created_at DESC LIMIT ? OFFSET ?', (limit, offset))]
        for r in rows:
            r['count'] = len(json.loads(r.pop('account_ids')))
        return rows
