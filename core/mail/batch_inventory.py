"""Keep purchased mail available when a job stops before using the whole batch."""

import json
from pathlib import Path
import re
import threading
import uuid
from core.config.paths import data_directory


_BATCH_LOCKS = {}
_BATCH_LOCKS_GUARD = threading.Lock()


def reserve_batch(path):
    """Do not let two worker threads consume the same paid batch together."""
    key = str(Path(path).resolve()).casefold()
    with _BATCH_LOCKS_GUARD:
        lock = _BATCH_LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise ValueError('Lô mail này đang được job khác sử dụng; chờ job đó kết thúc.')
    return lock


def _new_batch_path(job_id, directory=None):
    directory = Path(directory) if directory is not None else data_directory() / 'mail_batches'
    directory.mkdir(parents=True, exist_ok=True)
    safe_id = re.sub(r'[^a-zA-Z0-9_-]', '_', str(job_id))[:80] or 'job'
    return directory / f'{safe_id}_{uuid.uuid4().hex[:12]}.json'


def save_batch(job_id, accounts, directory=None):
    path = _new_batch_path(job_id, directory)
    write_batch(path, job_id, accounts)
    return path


def save_reserved_batch(job_id, accounts, directory=None):
    """Reserve a fresh purchase before its JSON becomes visible to other jobs."""
    path = _new_batch_path(job_id, directory)
    lock = reserve_batch(path)
    try:
        write_batch(path, job_id, accounts)
    except BaseException:
        lock.release()
        raise
    return path, lock


def write_batch(path, job_id, accounts, results=()):
    statuses = {item.account_identifier: item.status for item in results}
    data = {'job_id': job_id, 'accounts': [
        {'email': item.email, 'password': item.password,
         'status': statuses.get(item.identifier, 'pending')}
        for item in accounts
    ]}
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def load_batch(path):
    """Load unfinished mail without ever switching to another shop purchase."""
    from core.platforms.mochichat.runners.invite_referral import MochiAccountItem
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    rows = data.get('accounts') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError('File lô mail phải chứa danh sách accounts.')
    accounts, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('File lô mail có bản ghi không hợp lệ.')
        email, password = row.get('email'), row.get('password')
        if not isinstance(email, str) or not email.strip() or not isinstance(password, str) or not password:
            raise ValueError('File lô mail có tài khoản thiếu email hoặc mật khẩu.')
        email = email.strip()
        if email.lower() in seen:
            raise ValueError('File lô mail có tài khoản trùng; kiểm tra lại trước khi chạy.')
        seen.add(email.lower())
        if row.get('status', 'pending') not in ('success', 'already_redeemed'):
            accounts.append(MochiAccountItem(email, email, password))
    if not accounts:
        raise ValueError('Lô mail không còn tài khoản chưa hoàn tất.')
    return tuple(accounts)


def update_batch(path, job_id, results):
    """Merge only this run's results, preserving all paid and unused records."""
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    rows = data.get('accounts') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError('Không cập nhật được file lô mail: thiếu accounts.')
    statuses = {item.account_identifier.lower(): item.status for item in results}
    for row in rows:
        key = str(row.get('email', '')).strip().lower()
        if key in statuses:
            row['status'] = statuses[key]
    data['last_job_id'] = job_id
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
