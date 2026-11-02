"""Asynchronous management transport and a read-only SQLite view of VPS state."""
import json
from pathlib import Path
import queue
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PyQt6.QtCore import QThread, pyqtSignal
from telegram_store import TelegramStore


class RemoteConnection(QThread):
    snapshot_received = pyqtSignal(dict)
    completed = pyqtSignal(str, object)
    failed = pyqtSignal(str)

    def __init__(self, url, token):
        super().__init__()
        parsed = urllib.parse.urlsplit(url)
        if parsed.hostname not in ("localhost", "127.0.0.1") or parsed.scheme != "http" or parsed.username or parsed.password or parsed.path not in ("", "/"):
            raise ValueError("Dùng URL HTTP loopback qua SSH tunnel, ví dụ http://127.0.0.1:18900.")
        if len(token) < 32:
            raise ValueError("Khóa quản lý VPS phải có ít nhất 32 ký tự.")
        self.url, self.token = url.rstrip("/"), token
        self.commands = queue.Queue()
        self.stopped = threading.Event()
        self.wakeup = threading.Event()
        self.online = False
        self.pending = None

    def submit(self, operation, arguments=None):
        if not self.online:
            raise ValueError("Chưa kết nối VPS. Chờ kết nối lại trước khi gửi thay đổi.")
        self.commands.put({"operation": operation, "arguments": arguments or {}, "request_id": str(uuid.uuid4())})
        self.wakeup.set()

    def request(self, path, value=None):
        request = urllib.request.Request(self.url + path, data=json.dumps(value, ensure_ascii=False).encode() if value is not None else None,
                                         headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                message = json.load(exc).get("error", "VPS từ chối yêu cầu.")
            except Exception:
                message = "VPS từ chối yêu cầu. Kiểm tra khóa kết nối."
            finally:
                exc.close()
            raise ValueError(message) from None
        except Exception:
            raise ConnectionError("Mất kết nối VPS. Kiểm tra SSH tunnel; job trên VPS vẫn tiếp tục.") from None
        return result

    def run(self):
        while not self.stopped.is_set():
            self.wakeup.clear()
            try:
                while (self.pending or not self.commands.empty()) and not self.stopped.is_set():
                    command = self.pending or self.commands.get_nowait()
                    self.pending = command
                    try:
                        self.completed.emit(command["operation"], self.request("/command", command))
                        self.pending = None
                    except ConnectionError:
                        # Retry the exact request ID after reconnecting; enqueue is idempotent.
                        raise
                    except ValueError as exc:
                        self.pending = None
                        self.failed.emit(str(exc))
                snapshot = self.request("/snapshot")
                self.online = True
                self.snapshot_received.emit(snapshot)
            except (ValueError, ConnectionError) as exc:
                self.online = False
                self.failed.emit(str(exc))
            self.wakeup.wait(2)

    def stop(self):
        self.online = False
        self.stopped.set()
        self.wakeup.set()


class RemoteStore(TelegramStore):
    def __init__(self, connection):
        self.connection = connection
        self.folder = tempfile.TemporaryDirectory(prefix="hoshino-vps-view-")
        self._loading = False
        self._requested_logs = set()
        super().__init__(Path(self.folder.name) / "view.sqlite3")

    def apply_logs(self, rows):
        with self.lock, self.db:
            for row in rows:
                columns = ("id", "record_id", "created_at", "level", "message", "source_key")
                self.db.execute("INSERT OR REPLACE INTO job_logs(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")", [row.get(column) for column in columns])

    def apply_snapshot(self, state):
        # Values are data. Column names come from the local schema, never the response.
        with self.lock, self.db:
            for table in ("users", "jobs"):
                columns = [row["name"] for row in self.db.execute("PRAGMA table_info(" + table + ")")]
                self.db.execute("DELETE FROM " + table)
                for row in state[table]:
                    selected = [column for column in columns if column in row]
                    self.db.execute("INSERT INTO " + table + "(" + ",".join(selected) + ") VALUES(" + ",".join("?" for _ in selected) + ")", [row[column] for column in selected])
            self.db.execute("DELETE FROM config")
            self.db.executemany("INSERT INTO config(key,value) VALUES(?,?)", [(key, json.dumps(value)) for key, value in state["config"].items()])
        self.apply_logs(state["logs"])

    def token(self):
        return ""  # Server tokens are write-only from the desktop.

    def save_bot(self, token, auto_start):
        self.connection.submit("save_bot", {"token": token, "auto_start": auto_start})

    def set_config(self, **values):
        self.connection.submit("set_config", values)

    def save_user(self, chat_id, name, role, create=False):
        self.connection.submit("save_user", {"chat_id": chat_id.strip(), "name": name.strip(), "role": role, "create": create})

    def set_access(self, chat_id, active):
        self.connection.submit("set_access", {"chat_id": chat_id, "active": active})

    def logs(self, record_id=None, limit=2000):
        if record_id and record_id not in self._requested_logs and self.connection.online:
            self._requested_logs.add(record_id)
            self.connection.submit("logs", {"id": record_id})
        return super().logs(record_id, limit)
