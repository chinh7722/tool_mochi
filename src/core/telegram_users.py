"""Durable Telegram users and job accounting, independent of bot availability."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import threading
import uuid

from core.config.constants import MOCHI_INVITATION_REWARD_POINTS
from core.finance import MailPurchaseReceipt, MAX_MONEY_UNITS
from core.customer_accounts import CustomerAccountsMixin
from core.job_history import JOB_TIMEZONE, JobHistoryEntry
from core.job_schedule import schedule_storage, schedule_time
from core.mail.user_keys import validated_user_mail_keys
from core.mail.providers.priority import GMAILVIP, ONE_TRIEU

_RECEIPT_LOCK_GUARD = threading.Lock()
_RECEIPT_LOCKS = {}
LOCAL_USER_ID = -1

_USERS_SCHEMA = '''CREATE TABLE {table} (
    chat_id INTEGER PRIMARY KEY CHECK(chat_id > 0 OR chat_id = -1),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
    role TEXT NOT NULL CHECK(role IN ('admin', 'partner')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0, 1)),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL)'''


@dataclass(frozen=True)
class TelegramUser:
    chat_id: int
    name: str
    role: str
    active: bool
    completed_jobs: int = 0
    failed_jobs: int = 0
    requested_beans: int = 0
    sold_beans: int = 0
    revenue_units: int = 0
    cost_units: int = 0
    pending_costs: int = 0
    unpriced_jobs: int = 0

    @property
    def is_local(self):
        return self.chat_id == LOCAL_USER_ID

    @property
    def revenue_vnd(self):
        return Decimal(self.revenue_units) / 100

    @property
    def cost_vnd(self):
        return Decimal(self.cost_units) / 100

    @property
    def profit_vnd(self):
        return self.revenue_vnd - self.cost_vnd


@dataclass(frozen=True)
class TelegramStoredJob:
    job_id: str
    chat_id: int
    code: str
    requested_beans: int
    target_count: int
    bot_fingerprint: str
    update_id: int | None
    status: str
    success_count: int
    attempted_count: int
    failure_count: int
    created_at: str
    updated_at: str
    finalized_at: str | None
    sale_rate_units: int | None = None
    finance_tracked: int = 0
    dispatch_state: str = 'running'
    scheduled_at: str | None = None
    schedule_data: str = '{}'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _chat_id(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ValueError('Chat ID phải là số nguyên dương của cuộc trò chuyện riêng.')
    return value


def _owner_id(value):
    if type(value) is int and value == LOCAL_USER_ID:
        return value
    return _chat_id(value)


def _count(value, label, *, positive=False):
    if type(value) is not int or not int(positive) <= value <= 2**63 - 1:
        raise ValueError(f'{label} phải là số nguyên {"dương" if positive else "không âm"}.')
    return value


def _text(value, label, limit, *, empty=False):
    if not isinstance(value, str):
        raise ValueError(f'{label} không hợp lệ.')
    clean = value.strip()
    if (not clean and not empty) or len(clean) > limit:
        raise ValueError(f'{label} phải có {"tối đa" if empty else "từ 1 đến"} {limit} ký tự.')
    return clean


class TelegramUserStore(CustomerAccountsMixin):
    """Use short, independent SQLite transactions so GUI and bot threads can share it.

    Statistics are derived from job rows, never incremented counters. A result can
    therefore be delivered twice without selling the same confirmed beans twice.
    """

    _USER_SELECT = '''SELECT u.chat_id, u.name, u.role, u.active,
        COALESCE(SUM(CASE WHEN j.status = 'completed' THEN 1 ELSE 0 END), 0) AS completed_jobs,
        COALESCE(SUM(CASE WHEN j.status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_jobs,
        COALESCE(SUM(j.requested_beans), 0) AS requested_beans,
        COALESCE(SUM(j.success_count), 0) AS success_count,
        COALESCE(SUM(CASE WHEN co.order_id IS NOT NULL THEN COALESCE(co.charged_units,0)
            WHEN j.status='completed' THEN j.success_count * j.sale_rate_units ELSE 0 END), 0) AS revenue_units,
        COALESCE(MAX(e.cost_units), 0) AS cost_units,
        COALESCE(MAX(e.pending_costs), 0) AS pending_costs,
        COALESCE(SUM(CASE WHEN j.status='completed' AND j.finance_tracked=1 AND j.sale_rate_units IS NULL THEN 1 ELSE 0 END), 0) AS unpriced_jobs
        FROM users u LEFT JOIN jobs j ON j.chat_id = u.chat_id
        LEFT JOIN customer_orders co ON co.job_id=j.job_id
        LEFT JOIN (SELECT owner.chat_id, SUM(p.cost_units) AS cost_units,
            SUM(CASE WHEN p.cost_units IS NULL THEN 1 ELSE 0 END) AS pending_costs
            FROM mail_purchases p JOIN jobs owner ON owner.job_id=p.job_id
            GROUP BY owner.chat_id) e ON e.chat_id=u.chat_id'''

    def __init__(self, path):
        if str(path) == ':memory:':
            raise ValueError('Cần đường dẫn file để lưu thống kê Telegram bền vững.')
        self.path = Path(path).expanduser().resolve()
        with _RECEIPT_LOCK_GUARD:
            self._receipt_lock = _RECEIPT_LOCKS.setdefault(str(self.path).casefold(), threading.RLock())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_local_user_schema()
        with self._connection(write=True) as connection:
            connection.execute(_USERS_SCHEMA.format(table='IF NOT EXISTS users'))
            connection.execute('''CREATE TABLE IF NOT EXISTS user_mail_keys (
                chat_id INTEGER PRIMARY KEY REFERENCES users(chat_id) CHECK(chat_id > 0),
                gmailvip_key TEXT NOT NULL, one_trieu_key TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            connection.execute('''CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                chat_id INTEGER NOT NULL REFERENCES users(chat_id),
                code TEXT NOT NULL,
                requested_beans INTEGER NOT NULL CHECK(requested_beans > 0),
                target_count INTEGER NOT NULL DEFAULT 0 CHECK(target_count >= 0),
                bot_fingerprint TEXT NOT NULL DEFAULT '', update_id INTEGER,
                status TEXT NOT NULL DEFAULT 'running'
                    CHECK(status IN ('running', 'completed', 'failed', 'cancelled')),
                success_count INTEGER NOT NULL DEFAULT 0 CHECK(success_count >= 0),
                attempted_count INTEGER NOT NULL DEFAULT 0 CHECK(attempted_count >= 0),
                failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, finalized_at TEXT,
                UNIQUE(bot_fingerprint, update_id))''')
            connection.execute('CREATE INDEX IF NOT EXISTS jobs_chat_id ON jobs(chat_id)')
            connection.execute('CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(jobs)')}
            if 'sale_rate_units' not in columns:
                connection.execute('ALTER TABLE jobs ADD COLUMN sale_rate_units INTEGER CHECK(sale_rate_units >= 0)')
            if 'finance_tracked' not in columns:
                connection.execute('ALTER TABLE jobs ADD COLUMN finance_tracked INTEGER NOT NULL DEFAULT 0')
            if 'dispatch_state' not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN dispatch_state TEXT NOT NULL DEFAULT 'running'")
            if 'scheduled_at' not in columns:
                connection.execute('ALTER TABLE jobs ADD COLUMN scheduled_at TEXT')
            if 'schedule_data' not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN schedule_data TEXT NOT NULL DEFAULT '{}'")
            connection.execute('''CREATE TABLE IF NOT EXISTS mail_purchases (
                purchase_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
                provider TEXT NOT NULL, account_hash TEXT NOT NULL, product_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity > 0), cost_units INTEGER CHECK(cost_units >= 0),
                order_id TEXT NOT NULL DEFAULT '', source TEXT NOT NULL, state TEXT NOT NULL,
                balance_before_units INTEGER, balance_after_units INTEGER,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            purchase_columns = {row['name'] for row in connection.execute('PRAGMA table_info(mail_purchases)')}
            for column in ('balance_before_units', 'balance_after_units'):
                if column not in purchase_columns:
                    connection.execute(f'ALTER TABLE mail_purchases ADD COLUMN {column} INTEGER')
            connection.execute('CREATE INDEX IF NOT EXISTS mail_purchases_job ON mail_purchases(job_id)')
            connection.execute('''CREATE UNIQUE INDEX IF NOT EXISTS mail_purchase_order
                ON mail_purchases(provider, account_hash, order_id) WHERE order_id != '' ''')
            stamp = _now()
            connection.execute('''INSERT INTO users(chat_id, name, role, active, created_at, updated_at)
                VALUES (?, 'Local', 'partner', 1, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET name='Local', role='partner', active=1''',
                (LOCAL_USER_ID, stamp, stamp))
        self.receipt_directory = self.path.parent / 'mail_purchase_receipts' / self.path.stem
        self._init_customer_accounts()
        self.recovery_errors = 0
        self._recover_purchase_receipts()

    def _migrate_local_user_schema(self):
        """Extend the old parent constraint atomically, preserving every job FK."""
        if not self.path.is_file():
            return
        with self._receipt_lock:
            connection = sqlite3.connect(str(self.path), timeout=15)
            try:
                row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
                if row is None or 'chat_id = -1' in row[0]:
                    return
                columns = {item[1] for item in connection.execute('PRAGMA table_info(users)')}
                if columns != {'chat_id', 'name', 'role', 'active', 'created_at', 'updated_at'}:
                    raise ValueError('Cấu trúc bảng người dùng khác dự kiến; chưa chuyển dữ liệu Local.')
                backup = self.path.with_name(self.path.stem + '.before_local_' + uuid.uuid4().hex[:8] + self.path.suffix)
                destination = sqlite3.connect(str(backup))
                try:
                    connection.backup(destination)
                finally:
                    destination.close()
                connection.execute('PRAGMA foreign_keys = OFF')
                connection.execute('BEGIN IMMEDIATE')
                # Another tool instance may already have migrated while we backed up.
                sql = connection.execute("SELECT sql FROM sqlite_master WHERE name='users'").fetchone()[0]
                if 'chat_id = -1' in sql:
                    connection.rollback()
                    return
                connection.execute(_USERS_SCHEMA.format(table='__users_with_local'))
                connection.execute('''INSERT INTO __users_with_local
                    SELECT chat_id, name, role, active, created_at, updated_at FROM users''')
                connection.execute('DROP TABLE users')
                connection.execute('ALTER TABLE __users_with_local RENAME TO users')
                if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
                    raise ValueError('Không chuyển dữ liệu Local vì phát hiện liên kết lịch sử không hợp lệ.')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    @contextmanager
    def _connection(self, *, write=False):
        connection = sqlite3.connect(str(self.path), timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA foreign_keys = ON')
            if write:
                connection.execute('BEGIN IMMEDIATE')
            yield connection
            if write:
                connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _user(row):
        if row is None:
            return None
        return TelegramUser(row['chat_id'], row['name'], row['role'], bool(row['active']),
            row['completed_jobs'], row['failed_jobs'], row['requested_beans'],
            row['success_count'] * MOCHI_INVITATION_REWARD_POINTS,
            row['revenue_units'], row['cost_units'], row['pending_costs'], row['unpriced_jobs'])

    def upsert_user(self, chat_id, name, role='partner', *, active=True):
        chat_id = _chat_id(chat_id)
        name = _text(name, 'Tên', 120)
        if role not in ('admin', 'partner'):
            raise ValueError('Vai trò phải là admin hoặc partner.')
        if type(active) is not bool:
            raise ValueError('Trạng thái người dùng phải là true hoặc false.')
        stamp = _now()
        with self._connection(write=True) as connection:
            connection.execute('''INSERT INTO users(chat_id, name, role, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET name=excluded.name, role=excluded.role,
                    active=excluded.active, updated_at=excluded.updated_at''',
                (chat_id, name, role, int(active), stamp, stamp))
        return self.get_user(chat_id)

    def set_active(self, chat_id, active):
        chat_id = _chat_id(chat_id)
        if type(active) is not bool:
            raise ValueError('Trạng thái người dùng phải là true hoặc false.')
        with self._connection(write=True) as connection:
            return bool(connection.execute('UPDATE users SET active=?, updated_at=? WHERE chat_id=?',
                (int(active), _now(), chat_id)).rowcount)

    def migrate_legacy(self, ids):
        """Import the old allowlist once, without changing existing users or history."""
        ids = tuple(sorted({_chat_id(value) for value in ids}))
        with self._connection(write=True) as connection:
            if connection.execute("SELECT 1 FROM metadata WHERE key='legacy_allowlist_migrated'").fetchone():
                return 0
            inserted = 0
            stamp = _now()
            for chat_id in ids:
                inserted += connection.execute('''INSERT OR IGNORE INTO users
                    (chat_id, name, role, active, created_at, updated_at)
                    VALUES (?, ?, 'partner', 1, ?, ?)''',
                    (chat_id, f'Telegram {chat_id}', stamp, stamp)).rowcount
            connection.execute("INSERT INTO metadata(key, value) VALUES ('legacy_allowlist_migrated', ?)", (stamp,))
            return inserted

    def get_user(self, chat_id):
        chat_id = _owner_id(chat_id)
        with self._connection() as connection:
            return self._user(connection.execute(self._USER_SELECT +
                ' WHERE u.chat_id=? GROUP BY u.chat_id', (chat_id,)).fetchone())

    def list_users(self, include_inactive=True, *, include_local=False):
        """Bot-facing lists exclude Local; desktop statistics opt in explicitly."""
        if type(include_inactive) is not bool:
            raise ValueError('include_inactive phải là true hoặc false.')
        if type(include_local) is not bool:
            raise ValueError('include_local phải là true hoặc false.')
        clauses = [] if include_inactive else ['u.active=1']
        if not include_local:
            clauses.append('u.chat_id > 0')
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self._connection() as connection:
            return tuple(self._user(row) for row in connection.execute(self._USER_SELECT +
                where + ' GROUP BY u.chat_id ORDER BY (u.chat_id=-1) DESC, u.name COLLATE NOCASE, u.chat_id'))

    def active_ids(self):
        with self._connection() as connection:
            return tuple(row[0] for row in connection.execute('SELECT chat_id FROM users WHERE active=1 AND chat_id>0 ORDER BY chat_id'))

    def roles(self):
        with self._connection() as connection:
            return dict(connection.execute('SELECT chat_id, role FROM users WHERE active=1 AND chat_id>0 ORDER BY chat_id'))

    def set_user_mail_keys(self, chat_id, gmailvip_key, one_trieu_key):
        chat_id = _chat_id(chat_id)
        keys = validated_user_mail_keys({GMAILVIP: gmailvip_key, ONE_TRIEU: one_trieu_key})
        with self._connection(write=True) as connection:
            actor = connection.execute('SELECT active FROM users WHERE chat_id=?', (chat_id,)).fetchone()
            if actor is None or not actor['active']:
                raise PermissionError('Chỉ lưu API riêng cho admin/partner đang được cấp quyền.')
            connection.execute('''INSERT INTO user_mail_keys VALUES (?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET gmailvip_key=excluded.gmailvip_key,
                    one_trieu_key=excluded.one_trieu_key,updated_at=excluded.updated_at''',
                (chat_id, keys[GMAILVIP], keys[ONE_TRIEU], _now()))

    def get_user_mail_keys(self, chat_id, *, require_complete=False):
        chat_id = _chat_id(chat_id)
        with self._connection() as connection:
            actor = connection.execute('SELECT active FROM users WHERE chat_id=?', (chat_id,)).fetchone()
            if actor is None or not actor['active']:
                raise PermissionError('Chat ID chưa được cấp quyền hoặc đã bị thu hồi quyền.')
            row = connection.execute('SELECT gmailvip_key,one_trieu_key FROM user_mail_keys WHERE chat_id=?',
                (chat_id,)).fetchone()
        keys = {GMAILVIP: row['gmailvip_key'], ONE_TRIEU: row['one_trieu_key']} if row else {}
        return validated_user_mail_keys(keys) if require_complete else keys

    def record_local_job(self, jobid, code, requested_beans, target_count=0, *, sale_rate_units=None,
            scheduled_at=None, schedule_data=None):
        return self.record_job(jobid, LOCAL_USER_ID, code, requested_beans, target_count,
            sale_rate_units=sale_rate_units, scheduled_at=scheduled_at, schedule_data=schedule_data)

    def record_job(self, jobid, chatid, code, requested_beans, target_count=0,
            bot_fingerprint='', update_id=None, *, sale_rate_units=None, scheduled_at=None, schedule_data=None):
        scheduled_at, schedule_data = schedule_storage(scheduled_at, schedule_data)
        jobid = _text(jobid, 'Mã job', 120)
        chatid = _owner_id(chatid)
        code = _text(code, 'Mã mời', 64)
        requested_beans = _count(requested_beans, 'Bean yêu cầu', positive=True)
        target_count = _count(target_count, 'Số tài khoản')
        bot_fingerprint = _text(bot_fingerprint, 'Định danh bot', 128, empty=True)
        if chatid == LOCAL_USER_ID and (bot_fingerprint or update_id is not None):
            raise ValueError('Local chỉ nhận job từ máy tính, không nhận yêu cầu bot.')
        if update_id is not None:
            update_id = _count(update_id, 'Update ID')
        if sale_rate_units is not None:
            sale_rate_units = _count(sale_rate_units, 'Đơn giá bán')
            expected = max(1, target_count, (requested_beans + 99) // 100)
            if sale_rate_units > MAX_MONEY_UNITS // expected:
                raise ValueError('Giá trị đơn hàng vượt giới hạn lưu trữ.')
        stamp = _now()
        with self._connection(write=True) as connection:
            # A duplicate must not be assigned to a new owner or started again.
            if connection.execute('SELECT 1 FROM jobs WHERE job_id=?', (jobid,)).fetchone():
                return False
            if update_id is not None and connection.execute('''SELECT 1 FROM jobs
                    WHERE bot_fingerprint=? AND update_id=?''', (bot_fingerprint, update_id)).fetchone():
                return False
            user = connection.execute('SELECT active FROM users WHERE chat_id=?', (chatid,)).fetchone()
            if not user or not user['active']:
                raise PermissionError('Chat ID chưa được cấp quyền hoặc đã bị thu hồi quyền.')
            connection.execute('''INSERT INTO jobs(job_id, chat_id, code, requested_beans,
                target_count, bot_fingerprint, update_id, created_at, updated_at, sale_rate_units, finance_tracked,
                scheduled_at, schedule_data, dispatch_state)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)''',
                (jobid, chatid, code, requested_beans, target_count, bot_fingerprint, update_id, stamp, stamp, sale_rate_units,
                 scheduled_at, schedule_data, 'scheduled' if scheduled_at else 'running'))
            return True

    def record_purchase(self, receipt):
        """Journal paid receipts before SQLite, including purchases never consumed."""
        with self._receipt_lock:
            return self._record_purchase_locked(receipt)

    @staticmethod
    def _validate_receipt(receipt):
        if not isinstance(receipt, MailPurchaseReceipt):
            raise ValueError('Biên nhận mua mail không hợp lệ.')
        _text(receipt.purchase_id, 'Mã giao dịch', 120)
        _text(receipt.job_id, 'Mã job', 120)
        _text(receipt.provider, 'Shop', 120)
        _text(receipt.account_hash, 'Định danh tài khoản shop', 128)
        _text(receipt.order_id, 'Mã đơn shop', 200, empty=True)
        _count(receipt.quantity, 'Số mail mua', positive=True)
        _count(receipt.product_id, 'ID sản phẩm', positive=True)
        for value in (receipt.balance_before_units, receipt.balance_after_units):
            if value is not None:
                _count(value, 'Số dư shop')
        if receipt.cost_units is not None:
            _count(receipt.cost_units, 'Chi phí mail')

    def _record_purchase_locked(self, receipt):
        self._validate_receipt(receipt)
        self.receipt_directory.mkdir(parents=True, exist_ok=True)
        path = self.receipt_directory / (hashlib.sha256(receipt.purchase_id.encode()).hexdigest() + '.json')
        # A replay without a balance must never replace an already known amount.
        payload = asdict(receipt)
        if path.is_file():
            previous = json.loads(path.read_text(encoding='utf-8'))
            if any(previous.get(key) != payload[key] for key in
                    ('purchase_id', 'job_id', 'provider', 'account_hash', 'product_id', 'quantity')):
                raise ValueError('Biên nhận mua mail bị trùng định danh nhưng khác nội dung.')
            if (previous.get('cost_units') is not None and receipt.cost_units is not None
                    and previous['cost_units'] != receipt.cost_units):
                raise ValueError('Biên nhận mua mail có số tiền mâu thuẫn; cần đối soát.')
            if previous.get('cost_units') is not None and receipt.cost_units is None:
                payload = previous
                receipt = MailPurchaseReceipt(**payload)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return self._store_purchase(receipt)

    def _store_purchase(self, receipt):
        self._validate_receipt(receipt)
        with self._connection(write=True) as connection:
            if not connection.execute('SELECT 1 FROM jobs WHERE job_id=?', (receipt.job_id,)).fetchone():
                return False  # Local job has no Telegram owner; never invent an owner.
            existing = connection.execute('SELECT * FROM mail_purchases WHERE purchase_id=?',
                (receipt.purchase_id,)).fetchone()
            order = None
            if receipt.order_id:
                order = connection.execute('''SELECT * FROM mail_purchases
                    WHERE provider=? AND account_hash=? AND order_id=?''',
                    (receipt.provider, receipt.account_hash, receipt.order_id)).fetchone()
            if order is not None and order['purchase_id'] != receipt.purchase_id:
                if (order['job_id'] != receipt.job_id or order['product_id'] != receipt.product_id
                        or order['quantity'] != receipt.quantity):
                    raise ValueError('Mã đơn shop đã được ghi nhận cho giao dịch khác; cần đối soát.')
                if (order['cost_units'] is not None and receipt.cost_units is not None
                        and order['cost_units'] != receipt.cost_units):
                    raise ValueError('Mã đơn shop có số tiền mâu thuẫn; cần đối soát.')
                if order['cost_units'] is None and receipt.cost_units is not None:
                    connection.execute('''UPDATE mail_purchases SET cost_units=?, source=?,
                        state=?, updated_at=? WHERE purchase_id=?''',
                        (receipt.cost_units, receipt.source, receipt.state, _now(), order['purchase_id']))
                if existing is not None:
                    connection.execute('''UPDATE mail_purchases SET cost_units=0,
                        source='duplicate_receipt', state='duplicate', updated_at=? WHERE purchase_id=?''',
                        (_now(), receipt.purchase_id))
                return False
            if existing:
                if (existing['job_id'] != receipt.job_id or existing['provider'] != receipt.provider
                        or existing['account_hash'] != receipt.account_hash):
                    raise ValueError('Giao dịch mua mail đã thuộc job khác; cần đối soát.')
                if existing['cost_units'] is not None:
                    if receipt.cost_units is not None and existing['cost_units'] != receipt.cost_units:
                        raise ValueError('Biên nhận lặp có chi phí khác; cần đối soát.')
                    return False
                connection.execute('''UPDATE mail_purchases SET cost_units=?, order_id=?, source=?,
                    state=?, balance_before_units=?, balance_after_units=?, updated_at=? WHERE purchase_id=?''',
                    (receipt.cost_units, receipt.order_id or existing['order_id'], receipt.source,
                     receipt.state, receipt.balance_before_units, receipt.balance_after_units,
                     _now(), existing['purchase_id']))
                return True
            stamp = _now()
            connection.execute('''INSERT INTO mail_purchases
                (purchase_id, job_id, provider, account_hash, product_id, quantity,
                 cost_units, order_id, source, state, created_at, updated_at)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (receipt.purchase_id, receipt.job_id, receipt.provider, receipt.account_hash,
                 receipt.product_id, receipt.quantity, receipt.cost_units, receipt.order_id,
                 receipt.source, receipt.state, stamp, stamp))
            connection.execute('''UPDATE mail_purchases SET balance_before_units=?, balance_after_units=?
                WHERE purchase_id=?''', (receipt.balance_before_units, receipt.balance_after_units, receipt.purchase_id))
            return True

    def list_purchases(self, chat_id=None):
        parameters = () if chat_id is None else (_owner_id(chat_id),)
        where = '' if chat_id is None else ' WHERE j.chat_id=?'
        with self._connection() as connection:
            return tuple(dict(row) for row in connection.execute('''SELECT p.*, j.chat_id,
                u.name FROM mail_purchases p JOIN jobs j ON j.job_id=p.job_id
                JOIN users u ON u.chat_id=j.chat_id''' + where +
                ' ORDER BY p.created_at DESC, p.purchase_id', parameters))

    def reconcile_purchase(self, purchase_id, cost_units):
        cost_units = _count(cost_units, 'Chi phí mail')
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM mail_purchases WHERE purchase_id=?',
                (_text(purchase_id, 'Mã giao dịch', 120),)).fetchone()
        if row is None or row['cost_units'] is not None:
            raise ValueError('Chỉ đối soát giao dịch đang thiếu chi phí.')
        self.record_purchase(MailPurchaseReceipt(row['purchase_id'], row['job_id'], row['provider'],
            row['account_hash'], row['product_id'], row['quantity'], cost_units,
            row['order_id'], 'manual_verified', row['state'],
            row['balance_before_units'], row['balance_after_units']))

    def price_job(self, job_id, sale_rate_units):
        sale_rate_units = _count(sale_rate_units, 'Đơn giá bán')
        with self._connection(write=True) as connection:
            job = connection.execute('SELECT * FROM jobs WHERE job_id=?',
                (_text(job_id, 'Mã job', 120),)).fetchone()
            if job is None or job['sale_rate_units'] is not None:
                raise ValueError('Chỉ bổ sung giá cho job chưa chốt đơn giá.')
            if sale_rate_units > MAX_MONEY_UNITS // max(1, job['target_count'], job['success_count']):
                raise ValueError('Giá trị đơn hàng vượt giới hạn lưu trữ.')
            connection.execute('UPDATE jobs SET sale_rate_units=?, finance_tracked=1 WHERE job_id=?',
                (sale_rate_units, job_id))

    def list_unpriced_jobs(self):
        with self._connection() as connection:
            return tuple(dict(row) for row in connection.execute('''SELECT j.job_id, j.chat_id,
                u.name, j.requested_beans, j.status FROM jobs j JOIN users u ON u.chat_id=j.chat_id
                WHERE j.finance_tracked=1 AND j.sale_rate_units IS NULL
                ORDER BY j.created_at DESC'''))

    def list_jobs(self, owner_id):
        owner_id = _owner_id(owner_id)
        with self._connection() as connection:
            return tuple(dict(row) for row in connection.execute('''SELECT j.*,
                COALESCE(p.cost_units, 0) AS cost_units, COALESCE(p.pending_costs, 0) AS pending_costs
                FROM jobs j LEFT JOIN (SELECT job_id, SUM(cost_units) AS cost_units,
                    SUM(CASE WHEN cost_units IS NULL THEN 1 ELSE 0 END) AS pending_costs
                    FROM mail_purchases GROUP BY job_id) p ON p.job_id=j.job_id
                WHERE j.chat_id=? ORDER BY j.created_at DESC, j.job_id DESC''', (owner_id,)))

    def invite_quota_history(self):
        """All confirmed successes, independent of final status or job owner."""
        with self._connection() as connection:
            return tuple(dict(row) for row in connection.execute('''SELECT job_id,code,
                success_count,created_at,updated_at,finalized_at FROM jobs
                WHERE success_count>0 ORDER BY created_at,job_id'''))

    def finished_job_history(self):
        """Export finalized legacy jobs without inferring missing job/customer names."""
        with self._connection() as connection:
            rows=connection.execute('''SELECT j.*,u.name,co.order_id FROM jobs j JOIN users u ON u.chat_id=j.chat_id
                LEFT JOIN customer_orders co ON co.job_id=j.job_id
                WHERE j.finalized_at IS NOT NULL AND j.status!='running' ORDER BY j.finalized_at,j.job_id''').fetchall()
        result=[]
        for row in rows:
            target=row['target_count'] or (row['requested_beans']+99)//100
            result.append(JobHistoryEntry(row['job_id'],row['order_id'] or '',
                'Local' if row['chat_id']==LOCAL_USER_ID else row['name'],
                None if row['chat_id']==LOCAL_USER_ID else row['chat_id'],'',row['code'],
                datetime.fromisoformat(row['created_at']).astimezone(JOB_TIMEZONE).isoformat(timespec='seconds'),
                datetime.fromisoformat(row['finalized_at']).astimezone(JOB_TIMEZONE).isoformat(timespec='seconds'),
                row['status'],target,row['attempted_count'],row['success_count'],row['failure_count'],
                target*100,row['success_count']*100))
        return tuple(result)

    def _recover_purchase_receipts(self):
        if not self.receipt_directory.is_dir():
            return
        for path in self.receipt_directory.glob('*.json'):
            try:
                self._store_purchase(MailPurchaseReceipt(**json.loads(path.read_text(encoding='utf-8'))))
            except Exception:
                self.recovery_errors += 1
                logging.getLogger(__name__).warning('Không phục hồi được biên nhận %s', path.name)

    def lookup_request(self, fp, update_id):
        fp = _text(fp, 'Định danh bot', 128, empty=True)
        update_id = _count(update_id, 'Update ID')
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM jobs WHERE bot_fingerprint=? AND update_id=?',
                (fp, update_id)).fetchone()
            return TelegramStoredJob(**dict(row)) if row else None

    def get_job(self, jobid):
        jobid = _text(jobid, 'Mã job', 120)
        with self._connection() as connection:
            row = connection.execute('SELECT * FROM jobs WHERE job_id=?', (jobid,)).fetchone()
            return TelegramStoredJob(**dict(row)) if row else None

    def scheduled_jobs(self):
        with self._connection() as connection:
            return tuple(dict(row) for row in connection.execute('''SELECT j.*,u.name AS owner_name
                FROM jobs j JOIN users u ON u.chat_id=j.chat_id
                WHERE j.dispatch_state='scheduled' AND j.finalized_at IS NULL ORDER BY j.scheduled_at,j.created_at'''))

    def reschedule_combo_job(self, jobid, when, success_count, attempted_count=0, failure_count=0):
        """Save the next round without closing the job or settling its customer hold."""
        jobid = _text(jobid, 'Mã job', 120)
        when = schedule_time(when).isoformat()
        success_count = _count(success_count, 'Số thành công')
        attempted_count = _count(attempted_count, 'Số đã thử')
        failure_count = _count(failure_count, 'Số lỗi')
        with self._connection(write=True) as connection:
            row = connection.execute('''SELECT * FROM jobs
                WHERE job_id=? AND finalized_at IS NULL''', (jobid,)).fetchone()
            if row is None or json.loads(row['schedule_data']).get('combo') is not True:
                return False
            # A duplicate completion callback must not postpone a saved round.
            if (row['dispatch_state'] == 'scheduled' and row['scheduled_at'] is not None
                    and success_count <= row['success_count']
                    and attempted_count <= row['attempted_count']
                    and failure_count <= row['failure_count']):
                return True
            connection.execute('''UPDATE jobs SET dispatch_state='scheduled', scheduled_at=?,
                success_count=MAX(success_count, ?), attempted_count=MAX(attempted_count, ?),
                failure_count=MAX(failure_count, ?), updated_at=?
                WHERE job_id=? AND finalized_at IS NULL''',
                (when, success_count, attempted_count, failure_count, _now(), jobid))
            return True

    def recover_combo_jobs(self, now=None):
        """Requeue unfinished combo rounds after an exclusive runtime restart.

        Call only before workers are started. Already saved future rounds retain
        their deadline; interrupted rounds become due now and recheck quota.
        """
        when = schedule_time(now if now is not None else datetime.now(timezone.utc))
        recovered = 0
        with self._connection(write=True) as connection:
            rows = connection.execute('''SELECT job_id,schedule_data,scheduled_at FROM jobs
                WHERE finalized_at IS NULL AND dispatch_state IN ('queued','running','dispatching')''').fetchall()
            for row in rows:
                data = json.loads(row['schedule_data'])
                if not isinstance(data, dict) or data.get('combo') is not True:
                    continue
                saved = schedule_time(row['scheduled_at']) if row['scheduled_at'] else when
                connection.execute('''UPDATE jobs SET dispatch_state='scheduled',scheduled_at=?,updated_at=?
                    WHERE job_id=? AND finalized_at IS NULL''',
                    (max(saved, when).isoformat(), _now(), row['job_id']))
                recovered += 1
        return recovered

    def defer_queued_schedule(self, jobid):
        jobid = _text(jobid, 'Mã job', 120)
        with self._connection(write=True) as connection:
            return bool(connection.execute('''UPDATE jobs SET dispatch_state='scheduled',updated_at=?
                WHERE job_id=? AND dispatch_state='queued' AND scheduled_at IS NOT NULL
                AND finalized_at IS NULL''', (_now(),jobid)).rowcount)

    def cancel_scheduled_job(self, jobid):
        jobid = _text(jobid, 'Mã job', 120)
        stamp = _now()
        with self._connection(write=True) as connection:
            changed = bool(connection.execute('''UPDATE jobs SET status='cancelled',updated_at=?,finalized_at=?
                WHERE job_id=? AND dispatch_state='scheduled' AND finalized_at IS NULL''', (stamp,stamp,jobid)).rowcount)
            if changed:
                self._settle_customer_order(connection,jobid)
            return changed

    def claim_scheduled_job(self, jobid):
        """Claim once before creating any worker, even if two tool processes open the same ledger."""
        jobid = _text(jobid, 'Mã job', 120)
        with self._connection(write=True) as connection:
            row = connection.execute('''SELECT j.*,u.active,co.order_id FROM jobs j
                JOIN users u ON u.chat_id=j.chat_id LEFT JOIN customer_orders co ON co.job_id=j.job_id
                WHERE j.job_id=? AND j.finalized_at IS NULL AND j.dispatch_state='scheduled' ''', (jobid,)).fetchone()
            if row is None:
                return False
            if not row['active'] and not row['order_id']:
                raise PermissionError('Người tạo job đã bị thu hồi quyền truy cập.')
            connection.execute("UPDATE jobs SET dispatch_state='dispatching',updated_at=? WHERE job_id=?", (_now(),jobid))
            return True

    def update_dispatch(self, jobid, state):
        jobid = _text(jobid, 'Mã job', 120)
        if state not in ('scheduled', 'queued', 'running'):
            raise ValueError('Trạng thái hàng đợi không hợp lệ.')
        with self._connection(write=True) as connection:
            return bool(connection.execute('''UPDATE jobs SET dispatch_state=?, updated_at=?
                WHERE job_id=? AND finalized_at IS NULL AND dispatch_state!=?''',
                (state, _now(), jobid, state)).rowcount)

    def update_progress(self, jobid, success_count, attempted_count=0, failure_count=0):
        jobid = _text(jobid, 'Mã job', 120)
        success_count = _count(success_count, 'Số thành công')
        attempted_count = _count(attempted_count, 'Số đã thử')
        failure_count = _count(failure_count, 'Số lỗi')
        with self._connection(write=True) as connection:
            return bool(connection.execute('''UPDATE jobs SET
                success_count=MAX(success_count, ?), attempted_count=MAX(attempted_count, ?),
                failure_count=MAX(failure_count, ?), updated_at=?
                WHERE job_id=? AND finalized_at IS NULL''',
                (success_count, attempted_count, failure_count, _now(), jobid)).rowcount)

    def finish_job(self, jobid, status, success_count, failure_count=0, attempted_count=0):
        jobid = _text(jobid, 'Mã job', 120)
        status = _text(status, 'Trạng thái job', 64)
        if status in ('running', 'pending'):
            raise ValueError('Job chưa kết thúc.')
        status = status if status in ('completed', 'cancelled') else 'failed'
        success_count = _count(success_count, 'Số thành công')
        attempted_count = _count(attempted_count, 'Số đã thử')
        failure_count = _count(failure_count, 'Số lỗi')
        stamp = _now()
        with self._connection(write=True) as connection:
            changed = bool(connection.execute('''UPDATE jobs SET status=?,
                success_count=MAX(success_count, ?), attempted_count=MAX(attempted_count, ?),
                failure_count=MAX(failure_count, ?), updated_at=?, finalized_at=?
                WHERE job_id=? AND finalized_at IS NULL''',
                (status, success_count, attempted_count, failure_count, stamp, stamp, jobid)).rowcount)
            if changed:
                self._settle_customer_order(connection, jobid)
            return changed
