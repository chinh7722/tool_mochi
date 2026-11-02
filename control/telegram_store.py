"""Persistent bot configuration, users, job ledger and notification outbox."""
import base64
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from job_history import HISTORY_STATUSES


def protect_token(text: str, decrypt=False) -> str:
    """Use Windows DPAPI for tokens saved under the current Windows account."""
    if not text:
        return ""

    class Blob(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    raw = base64.b64decode(text) if decrypt else text.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    if decrypt:
        method = crypt.CryptUnprotectData
        method.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    else:
        method = crypt.CryptProtectData
        method.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    method.restype = wintypes.BOOL
    if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise RuntimeError("Không đọc/lưu được Bot Token bằng tài khoản Windows hiện tại.")
    try:
        result = ctypes.string_at(target.data, target.length)
        return result.decode("utf-8") if decrypt else base64.b64encode(result).decode("ascii")
    finally:
        kernel.LocalFree(ctypes.cast(target.data, wintypes.HLOCAL))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TelegramStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("CASEFOLD", 1, lambda value: str(value or "").casefold(), deterministic=True)
        with self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users(
                    chat_id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, external_key TEXT UNIQUE NOT NULL,
                    chat_id TEXT NOT NULL, bot_id TEXT NOT NULL DEFAULT '',
                    code TEXT NOT NULL, points INTEGER NOT NULL, name TEXT NOT NULL,
                    money INTEGER NOT NULL, rate INTEGER, use_ui INTEGER NOT NULL,
                    status TEXT NOT NULL, backend_id TEXT, achieved INTEGER NOT NULL DEFAULT 0,
                    revenue INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS jobs_chat ON jobs(chat_id, created_at);
                CREATE TABLE IF NOT EXISTS outbox(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL,
                    chat_id TEXT NOT NULL, text TEXT NOT NULL, due REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS handled_commands(key TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS job_logs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT,
                    created_at TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL,
                    source_key TEXT UNIQUE);
                CREATE INDEX IF NOT EXISTS logs_job ON job_logs(record_id,id);
            """)
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(jobs)")}
            additions = {"completed_at": "TEXT", "started_at": "TEXT", "scheduled_at": "TEXT",
                         "success_count": "INTEGER NOT NULL DEFAULT 0", "target_count": "INTEGER NOT NULL DEFAULT 1",
                         "creator_name": "TEXT NOT NULL DEFAULT ''"}
            for name, definition in additions.items():
                if name not in columns:
                    self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            if "success_count" not in columns:
                self.db.execute("UPDATE jobs SET success_count=achieved/200,target_count=MAX(1,points/200)")
            if "completed_at" not in columns:
                marks = ",".join("?" for _ in HISTORY_STATUSES)
                self.db.execute(f"UPDATE jobs SET completed_at=updated_at WHERE status IN ({marks})", HISTORY_STATUSES)
            if "creator_name" not in columns:
                self.db.execute("UPDATE jobs SET creator_name=COALESCE((SELECT name FROM users WHERE users.chat_id=jobs.chat_id),CASE WHEN chat_id='LOCAL' THEN 'Local' ELSE chat_id END)")

    def close(self):
        with self.lock:
            self.db.close()

    def config(self, key, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_config(self, **values):
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO config(key,value) VALUES(?,?)",
                                [(key, json.dumps(value, ensure_ascii=False)) for key, value in values.items()])

    def save_bot(self, token, auto_start):
        self.set_config(token=protect_token(token), auto_start=bool(auto_start))

    def token(self):
        return protect_token(self.config("token", ""), decrypt=True)

    def user(self, chat_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM users WHERE chat_id=?", (str(chat_id),)).fetchone()
        return dict(row) if row else None

    def users(self):
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT * FROM users ORDER BY name,chat_id")]

    def save_user(self, chat_id, name, role, create=False):
        chat_id, name = str(chat_id).strip(), name.strip()
        if not chat_id.isascii() or not chat_id.isdigit() or int(chat_id) <= 0:
            raise ValueError("Chat ID phải là số dương của cuộc trò chuyện riêng. Dùng /id để lấy ID.")
        if not name or len(name) > 100 or role not in ("admin", "partner"):
            raise ValueError("Nhập tên (tối đa 100 ký tự) và chọn vai trò admin/partner.")
        with self.lock, self.db:
            existing = self.user(chat_id)
            if create and existing:
                raise ValueError("Chat ID đã có trong danh sách. Chọn người dùng để sửa hoặc khôi phục quyền.")
            if not create and not existing:
                raise ValueError("Chọn người dùng đã tồn tại trước khi lưu thay đổi.")
            self.db.execute("INSERT INTO users(chat_id,name,role,active) VALUES(?,?,?,1) "
                            "ON CONFLICT(chat_id) DO UPDATE SET name=excluded.name,role=excluded.role",
                            (chat_id, name, role))

    def set_access(self, chat_id, active):
        with self.lock, self.db:
            self.db.execute("UPDATE users SET active=? WHERE chat_id=?", (int(active), str(chat_id)))

    def add_job(self, item, external_key=None, rate=None):
        return self.add_jobs([(item, external_key, rate)])[0]

    def add_jobs(self, entries):
        record_ids = []
        with self.lock, self.db:
            for item, external_key, rate in entries:
                record_id, timestamp = uuid.uuid4().hex, now()
                user = self.user(item.chat_id)
                creator = user["name"] if user else ("Local" if item.chat_id == "LOCAL" else item.chat_id)
                result = self.db.execute("""INSERT OR IGNORE INTO jobs
                    (id,external_key,chat_id,bot_id,code,points,name,money,rate,use_ui,status,created_at,updated_at,scheduled_at,target_count,creator_name)
                    VALUES(?,?,?,?,?,?,?,?,?,?,'queued',?,?,?,?,?)""",
                    (record_id, external_key or "local:" + record_id, item.chat_id, item.bot_id,
                     item.invitation_code, item.target_points, item.job_name, item.unit_price,
                     rate, int(item.use_ui_automation), timestamp, timestamp, item.scheduled_at,
                     max(1, item.target_points // 200), creator))
                record_ids.append(record_id if result.rowcount else None)
        return record_ids

    def find_external(self, key):
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE external_key=?", (key,)).fetchone()
        return dict(row) if row else None

    def command_seen(self, key):
        with self.lock:
            return bool(self.db.execute("SELECT 1 FROM handled_commands WHERE key=?", (key,)).fetchone())

    def remember_command(self, key):
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO handled_commands(key) VALUES(?)", (key,))

    def mark(self, record_id, status=None, backend_id=None, result=None, error=""):
        if not record_id:
            return
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM jobs WHERE id=?", (record_id,)).fetchone()
            if not row:
                return
            status = status or row["status"]
            achieved, revenue = row["achieved"], row["revenue"]
            success, total = row["success_count"], row["target_count"]
            if result is not None:
                achieved = max(0, int(result.get("achieved_points") or int(result.get("success_count") or 0) * 200))
                revenue = ((row["money"] * min(row["points"], achieved) + row["points"] // 2) // row["points"]
                           if status == "completed" else 0)
                success = max(0, int(result.get("success_count", achieved // 200)))
                total = max(1, int(result.get("target_count") or total))
                error = error or result.get("error_message") or result.get("error") or ""
                if not isinstance(error, str):
                    error = str(error)
            stamp = now()
            completed = (row["completed_at"] or stamp) if status in HISTORY_STATUSES else None
            if completed and result and result.get("completed_at"):
                try:
                    backend_time = datetime.fromisoformat(result["completed_at"].replace("Z", "+00:00"))
                    if backend_time.tzinfo is None:
                        backend_time = backend_time.replace(tzinfo=timezone.utc)
                    completed = backend_time.astimezone(timezone.utc).isoformat(timespec="seconds")
                except (ValueError, TypeError, AttributeError):
                    pass
            started = row["started_at"] or (stamp if status in ("submitting", "running") else None)
            self.db.execute("""UPDATE jobs SET status=?,backend_id=?,achieved=?,revenue=?,error=?,updated_at=?,
                            success_count=?,target_count=?,completed_at=?,started_at=? WHERE id=?""",
                            (status, backend_id or row["backend_id"], achieved, revenue, error, stamp,
                             success, total, completed, started, record_id))

    def update_progress(self, record_id, current, total, points):
        if not record_id:
            return
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET success_count=MAX(success_count,?),target_count=?,achieved=MAX(achieved,?),updated_at=? WHERE id=?",
                            (max(0, current), max(1, total), max(0, points), now(), record_id))

    @staticmethod
    def _history_where(query="", status="all"):
        where = "status IN (" + ",".join("?" for _ in HISTORY_STATUSES) + ")"
        args = list(HISTORY_STATUSES)
        if status == "invite_limit_reached":
            where += " AND (status='invite_limit_reached' OR LOWER(error) LIKE '%invite_limit_reached%')"
        elif status != "all":
            where += " AND status=?"
            args.append(status)
        if query.strip():
            needle = query.strip().casefold().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            fields = ("id", "backend_id", "external_key", "chat_id", "creator_name", "name", "code")
            where += " AND (" + " OR ".join(f"CASEFOLD({field}) LIKE ? ESCAPE '\\'" for field in fields) + ")"
            args.extend(["%" + needle + "%"] * len(fields))
        return where, args

    def history(self, query="", status="all", limit=500):
        where, args = self._history_where(query, status)
        sql = "SELECT * FROM jobs WHERE " + where + " ORDER BY COALESCE(completed_at,updated_at) DESC,rowid DESC"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args)]

    def history_summary(self, query="", status="all"):
        where, args = self._history_where(query, status)
        with self.lock:
            row = self.db.execute("SELECT COUNT(*) AS total,COALESCE(SUM(status='completed'),0) AS completed,"
                                  "COALESCE(SUM(achieved),0) AS konpeito FROM jobs WHERE " + where, args).fetchone()
        return dict(row)

    def append_log(self, record_id, message, level="info", source_key=None, created_at=None):
        with self.lock, self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO job_logs(record_id,created_at,level,message,source_key) VALUES(?,?,?,?,?)",
                                     (record_id, created_at or now(), level, message, source_key))
            if not cursor.rowcount:
                return None
            return dict(self.db.execute("SELECT * FROM job_logs WHERE id=?", (cursor.lastrowid,)).fetchone())

    def logs(self, record_id=None, limit=2000):
        clause, args = ("", []) if record_id is None else (" WHERE record_id=?", [record_id])
        with self.lock:
            rows = self.db.execute("SELECT * FROM job_logs" + clause + " ORDER BY id DESC LIMIT ?", [*args, limit]).fetchall()
        return [dict(row) for row in reversed(rows)]

    def jobs(self, chat_id=None):
        with self.lock:
            if chat_id is None:
                rows = self.db.execute("SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC")
            else:
                rows = self.db.execute("SELECT * FROM jobs WHERE chat_id=? ORDER BY created_at DESC,rowid DESC", (str(chat_id),))
            return [dict(row) for row in rows]

    def recent_jobs(self, limit=10):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC,rowid DESC LIMIT ?", (limit,))]

    def job(self, record_id):
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE id=?", (record_id,)).fetchone()
        return dict(row) if row else None

    def dashboard_stats(self):
        with self.lock:
            row = self.db.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(status IN ('submitting','running')),0) AS running,
                COALESCE(SUM(status='completed'),0) AS completed,
                COALESCE(SUM(status IN ('failed','invalid_code','invite_limit_reached','error','interrupted')),0) AS failed,
                COALESCE(SUM(status='queued'),0) AS queued,
                COALESCE(SUM(achieved),0) AS konpeito FROM jobs""").fetchone()
            users = self.db.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]
        return {**dict(row), "users": users}

    def recover_jobs(self):
        """Known IDs resume monitoring; uncertain submissions are never resubmitted."""
        recovered = []
        with self.lock, self.db:
            rows = self.db.execute("SELECT * FROM jobs WHERE status IN ('queued','submitting','running','error') ORDER BY rowid").fetchall()
            for row in rows:
                if row["status"] == "queued" or row["backend_id"]:
                    recovered.append(dict(row))
                elif row["status"] in ("submitting", "running"):
                    self.db.execute("UPDATE jobs SET status='interrupted',error=?,updated_at=? WHERE id=?",
                                    ("Lần chạy trước bị gián đoạn, chưa biết ID. Kiểm tra bản local trước khi tạo lại.", now(), row["id"]))
        return recovered

    def stats(self, chat_id=None):
        where, args = ("", ()) if chat_id is None else (" WHERE chat_id=?", (str(chat_id),))
        with self.lock:
            row = self.db.execute("""SELECT
                COALESCE(SUM(status='completed'),0) AS completed,
                COALESCE(SUM(status IN ('failed','invalid_code','invite_limit_reached','cancelled','error','interrupted')),0) AS failed,
                COALESCE(SUM(CASE WHEN status='completed' THEN achieved ELSE 0 END),0) AS konpeito,
                COALESCE(SUM(CASE WHEN status='completed' THEN revenue ELSE 0 END),0) AS revenue FROM jobs""" + where, args).fetchone()
            users = self.db.execute("SELECT COUNT(*) FROM users WHERE active=1").fetchone()[0]
        return {**dict(row), "users": users}

    def notify(self, bot_id, chat_id, text):
        if not bot_id or str(chat_id) == "LOCAL":
            return
        # Plain text only. No untrusted Markdown/HTML entities are interpreted.
        with self.lock, self.db:
            self.db.execute("INSERT INTO outbox(bot_id,chat_id,text) VALUES(?,?,?)",
                            (str(bot_id), str(chat_id), text[:3500]))

    def pending_messages(self, bot_id, timestamp):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT o.* FROM outbox o WHERE bot_id=? AND due<=? AND NOT EXISTS "
                "(SELECT 1 FROM outbox older WHERE older.bot_id=o.bot_id AND older.chat_id=o.chat_id AND older.id<o.id) "
                "ORDER BY id LIMIT 20", (str(bot_id), timestamp))]

    def message_done(self, message_id):
        with self.lock, self.db:
            self.db.execute("DELETE FROM outbox WHERE id=?", (message_id,))

    def message_retry(self, message_id, due):
        with self.lock, self.db:
            self.db.execute("UPDATE outbox SET due=? WHERE id=?", (due, message_id))
