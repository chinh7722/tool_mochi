"""
Persistent SQLite Storage for MiraiMind Tool Clone (core.storage).

Stores created accounts, job histories, proxies, and configuration settings locally.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional


class StorageManager:
    """Thread-safe SQLite manager for local persistence."""

    def __init__(self, db_path: Optional[Path | str] = None):
        if db_path is None:
            data_dir = Path(__file__).resolve().parents[1] / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "miraimind_clone.db"

        self.db_path = Path(db_path)
        self.lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self.lock:
            conn = self._get_connection()
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS accounts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        email TEXT UNIQUE NOT NULL,
                        password TEXT NOT NULL,
                        invitation_code TEXT,
                        points INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'Sẵn sàng',
                        session_id TEXT,
                        chat_user_id TEXT,
                        mmuid TEXT,
                        created_at TEXT,
                        note TEXT
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS jobs (
                        id TEXT PRIMARY KEY,
                        job_type TEXT NOT NULL,
                        target_count INTEGER,
                        success_count INTEGER DEFAULT 0,
                        points_earned INTEGER DEFAULT 0,
                        status TEXT,
                        created_at TEXT
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    );
                """)
            conn.close()

    def add_account(self, acc: Dict[str, Any]) -> bool:
        with self.lock:
            conn = self._get_connection()
            try:
                with conn:
                    conn.execute("""
                        INSERT INTO accounts (email, password, invitation_code, points, status, session_id, chat_user_id, mmuid, created_at, note)
                        VALUES (:email, :password, :invitation_code, :points, :status, :session_id, :chat_user_id, :mmuid, :created_at, :note)
                        ON CONFLICT(email) DO UPDATE SET
                            points = excluded.points,
                            status = excluded.status,
                            note = excluded.note
                    """, {
                        "email": acc.get("email", ""),
                        "password": acc.get("password", ""),
                        "invitation_code": acc.get("invitation_code", ""),
                        "points": acc.get("points", 0),
                        "status": acc.get("status", "Sẵn sàng"),
                        "session_id": acc.get("session_id", ""),
                        "chat_user_id": acc.get("chat_user_id", ""),
                        "mmuid": acc.get("mmuid", ""),
                        "created_at": acc.get("created_at", ""),
                        "note": acc.get("note", ""),
                    })
                return True
            except Exception:
                return False
            finally:
                conn.close()

    def get_all_accounts(self) -> List[Dict[str, Any]]:
        with self.lock:
            conn = self._get_connection()
            rows = conn.execute("SELECT * FROM accounts ORDER BY id DESC").fetchall()
            result = [dict(r) for r in rows]
            conn.close()
            return result

    def delete_account(self, email: str) -> None:
        with self.lock:
            conn = self._get_connection()
            with conn:
                conn.execute("DELETE FROM accounts WHERE email = ?", (email,))
            conn.close()

    def update_account_status(self, email: str, status: str) -> None:
        with self.lock:
            conn = self._get_connection()
            with conn:
                conn.execute("UPDATE accounts SET status = ? WHERE email = ?", (status, email))
            conn.close()

    def save_setting(self, key: str, value: Any) -> None:
        with self.lock:
            conn = self._get_connection()
            val_str = json.dumps(value) if not isinstance(value, str) else value
            with conn:
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, val_str))
            conn.close()

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.lock:
            conn = self._get_connection()
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            conn.close()
            if not row:
                return default
            try:
                return json.loads(row[0])
            except Exception:
                return row[0]
