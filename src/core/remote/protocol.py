"""Bounded HTTP protocol and durable mutation deduplication (no secret bodies)."""
from dataclasses import asdict, is_dataclass
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

VERSION = 1
MAX_BODY = 2 * 1024 * 1024


class RemoteError(ValueError):
    pass


def encode(value):
    def fallback(item):
        if is_dataclass(item):
            return asdict(item)
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        if isinstance(item, Decimal):
            return str(item)
        raise TypeError(type(item).__name__)
    return json.dumps(value, ensure_ascii=False, default=fallback, allow_nan=False).encode('utf-8')


def text_field(data, key, *, maximum=4000, default=''):
    value = data.get(key, default)
    if not isinstance(value, str) or len(value) > maximum or '\0' in value:
        raise RemoteError(f'Trường {key} không hợp lệ.')
    return value


def integer(data, key, *, minimum=1, maximum=2**63-1):
    value = data.get(key)
    if type(value) is not int or not minimum <= value <= maximum:
        raise RemoteError(f'Trường {key} phải là số nguyên trong khoảng {minimum}–{maximum}.')
    return value


class MutationJournal:
    """A lost reply or service restart must never create a second paid job."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS operations ('
                       'id TEXT PRIMARY KEY, digest TEXT NOT NULL, result BLOB)')

    def execute(self, request_id, action, payload, callback):
        try:
            if str(uuid.UUID(request_id)) != request_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise RemoteError('request_id không hợp lệ.') from None
        digest = hashlib.sha256(encode([request_id, action, payload])).hexdigest()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT digest,result FROM operations WHERE id=?', (request_id,)).fetchone()
            if row:
                if row[0] != digest:
                    raise RemoteError('request_id đã được dùng cho nội dung khác.')
                if row[1] is None:
                    raise RemoteError('Lệnh đã được nhận nhưng chưa xác nhận kết quả. Kiểm tra trạng thái trước khi tạo lệnh mới.')
                return json.loads(row[1])
            db.execute('INSERT INTO operations VALUES (?,?,NULL)', (request_id, digest))
        # Commit the intention before the side effect. Uncertain operations stay blocked.
        result = callback()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('UPDATE operations SET result=? WHERE id=?', (encode(result), request_id))
        return result
