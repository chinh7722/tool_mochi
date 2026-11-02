"""Durable successful redeems grouped by invite code over a rolling 24 hours.

Only confirmed successful redeems are recorded. A job's final status never
changes the events already accepted here. Account identifiers are hashed before
writing; this database contains no mail/password/token records.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import sqlite3

INVITE_LIMIT = 10
WINDOW = timedelta(hours=24)
_WINDOW_US = 24 * 60 * 60 * 1_000_000
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def normalize_invite_code(value):
    if not isinstance(value, str):
        raise ValueError('Mã mời không hợp lệ.')
    code = value.strip().upper()
    if re.fullmatch(r'[A-Z0-9_-]{1,128}', code) is None:
        raise ValueError('Mã mời không hợp lệ.')
    return code


def _identifier(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(label + ' không hợp lệ.')
    value = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(label + ' không hợp lệ.')
    return value


def _time_us(value=None):
    try:
        when = datetime.now(timezone.utc) if value is None else value
        if isinstance(when, str):
            when = datetime.fromisoformat(when)
        if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
            raise ValueError()
        when = when.astimezone(timezone.utc)
        # Keep arithmetic and stored SQLite integer ranges bounded even for bad clocks.
        if when < _EPOCH or when.year > 9998:
            raise ValueError()
        delta = when - _EPOCH
        return ((delta.days * 86400 + delta.seconds) * 1_000_000) + delta.microseconds
    except (TypeError, ValueError, OverflowError):
        raise ValueError('Thời gian đếm lượt nhập mã phải hợp lệ và có múi giờ.') from None


def _datetime(value):
    return _EPOCH + timedelta(microseconds=value)


@dataclass(frozen=True)
class InviteQuotaSnapshot:
    code: str
    count: int
    remaining: int
    next_available_at: datetime | None
    limit_reached: bool


class InviteQuotaStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('''CREATE TABLE IF NOT EXISTS invite_successes (
                code TEXT NOT NULL,
                job_id TEXT NOT NULL,
                account_hash TEXT NOT NULL,
                occurred_us INTEGER NOT NULL CHECK(occurred_us >= 0),
                PRIMARY KEY(code, job_id, account_hash)
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS invite_successes_window '
                       'ON invite_successes(code, occurred_us)')
            db.execute('''CREATE TABLE IF NOT EXISTS invite_legacy_jobs (
                job_id TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                success_count INTEGER NOT NULL CHECK(success_count >= 0),
                occurred_us INTEGER NOT NULL CHECK(occurred_us >= 0)
            )''')
            db.execute('CREATE INDEX IF NOT EXISTS invite_legacy_window '
                       'ON invite_legacy_jobs(code,occurred_us)')

    def record_success(self, code, job_id, account_identifier, *, occurred_at=None):
        """Return True once per confirmed job/account redeem, False on replay."""
        code = normalize_invite_code(code)
        job_id = _identifier(job_id, 'Mã job', 256)
        account = _identifier(account_identifier, 'ID tài khoản', 2048).casefold()
        account_hash = hashlib.sha256(account.encode('utf-8')).hexdigest()
        timestamp = _time_us(occurred_at)
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            cursor = db.execute('''INSERT OR IGNORE INTO invite_successes
                (code,job_id,account_hash,occurred_us) VALUES(?,?,?,?)''',
                (code, job_id, account_hash, timestamp))
            return cursor.rowcount == 1

    def snapshot(self, code, *, now=None):
        code = normalize_invite_code(code)
        timestamp = _time_us(now)
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            # One SELECT sees one consistent SQLite snapshot across both tables.
            timestamps = list(db.execute('''SELECT occurred_us,1
                FROM invite_successes WHERE code=? AND occurred_us>? AND occurred_us<=?
                UNION ALL SELECT occurred_us,success_count
                FROM invite_legacy_jobs WHERE code=? AND success_count>0
                AND occurred_us>? AND occurred_us<=? ORDER BY 1''',
                (code, timestamp - _WINDOW_US, timestamp, code, timestamp - _WINDOW_US, timestamp)))
        count = sum(weight for _, weight in timestamps)
        blocked = count >= INVITE_LIMIT
        # If historical/concurrent activity exceeded 10, enough events must expire
        # before the total falls below 10; the first expiry alone is insufficient.
        next_time = None
        if blocked:
            threshold = count - INVITE_LIMIT
            passed = 0
            for stamp, weight in timestamps:
                passed += weight
                if passed > threshold:
                    next_time = _datetime(stamp + _WINDOW_US)
                    break
        return InviteQuotaSnapshot(code, count, max(0, INVITE_LIMIT - count), next_time, blocked)

    def seed_legacy_jobs(self, rows):
        """Import aggregate successes once, before enabling newly tracked workers.

        Older stores have only a job-level total, not each successful timestamp.
        Place that total at finalized_at/updated_at/created_at (the first present
        value) so it cannot expire prematurely. A marker makes this immutable.
        A job already having detailed events is marked but never imported again;
        a legacy active job's baseline is added to future detailed confirmations.
        The returned integer counts positive baseline jobs actually imported.
        """
        prepared = []
        for row in rows:
            try:
                row = dict(row)
                job_id = _identifier(row['job_id'], 'Mã job', 256)
                code = normalize_invite_code(row['code'])
                count = row['success_count']
                if type(count) is not int or not 0 <= count <= 1_000_000:
                    raise ValueError()
                when = row.get('finalized_at') or row.get('updated_at') or row.get('created_at')
                if when is None:
                    raise ValueError()
                timestamp = _time_us(when)
            except (KeyError, TypeError, AttributeError, ValueError):
                raise ValueError('Dữ liệu lịch sử lượt nhập mã không hợp lệ.') from None
            prepared.append((job_id, code, count, timestamp))
        imported = 0
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            for job_id, code, count, timestamp in prepared:
                if db.execute('SELECT 1 FROM invite_legacy_jobs WHERE job_id=?', (job_id,)).fetchone():
                    continue
                if db.execute('SELECT 1 FROM invite_successes WHERE job_id=? LIMIT 1', (job_id,)).fetchone():
                    count = 0
                db.execute('INSERT INTO invite_legacy_jobs VALUES(?,?,?,?)', (job_id, code, count, timestamp))
                imported += bool(count)
        return imported
