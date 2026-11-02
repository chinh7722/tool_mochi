#!/usr/bin/env python3
"""Authenticated loopback management API, intended for an SSH tunnel."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import threading
import uuid

from service import Service
from store import ServerStore


class ManagementServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, service, token):
        if address[0] != "127.0.0.1" or len(token) < 32:
            raise ValueError("API phải bind loopback và dùng khóa ít nhất 32 ký tự.")
        self.service, self.token = service, token
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_args):
        pass

    def reply(self, code, value):
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def authorized(self):
        return hmac.compare_digest(self.headers.get("Authorization", "").encode(),
                                   ("Bearer " + self.server.token).encode()) and not self.headers.get("Origin")

    def do_GET(self):
        if not self.authorized():
            return self.reply(401, {"error": "Không có quyền truy cập."})
        if self.path == "/health":
            return self.reply(200, {"application": "hoshino-vps", "version": 1})
        if self.path == "/snapshot":
            return self.reply(200, self.server.service.snapshot())
        self.reply(404, {"error": "Không tìm thấy endpoint."})

    def do_POST(self):
        if not self.authorized():
            return self.reply(401, {"error": "Không có quyền truy cập."})
        if self.path != "/command":
            return self.reply(404, {"error": "Không tìm thấy endpoint."})
        try:
            size = int(self.headers.get("Content-Length", "-1"))
            if self.headers.get("Transfer-Encoding") or not 0 <= size <= 128 * 1024:
                return self.reply(413, {"error": "Yêu cầu quá lớn hoặc không hợp lệ."})
            value = json.loads(self.rfile.read(size))
            args = value.get("arguments", {})
            request_id = str(uuid.UUID(value["request_id"]))
            if not isinstance(args, dict):
                raise ValueError()
            result = self.server.service.command(value["operation"], args, request_id)
            self.reply(200, result)
        except (KeyError, ValueError, TypeError) as exc:
            self.reply(400, {"error": str(exc) if isinstance(exc, ValueError) else "Yêu cầu không hợp lệ."})
        except Exception:
            self.reply(500, {"error": "Lỗi dịch vụ; chưa tự gửi lại job."})


def main():
    import fcntl
    os.umask(0o077)
    data = Path(os.environ.get("HOSHINO_DATA_DIR", bootstrap.ROOT / "vps-data"))
    data.mkdir(parents=True, exist_ok=True)
    lock = (data / "server.lock").open("w")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    store = ServerStore(data / "telegram.sqlite3")
    service = Service(store)
    server = ManagementServer(("127.0.0.1", int(os.environ.get("HOSHINO_PORT", "18900"))), service,
                              os.environ.get("HOSHINO_ADMIN_TOKEN", ""))
    def shutdown(*_):
        service.stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    # Keep legacy protocol loggers from writing OTP, mailbox and session data to journald.
    import logging
    logging.disable(logging.CRITICAL)
    service.start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
        service.stop()
        if not service.scheduler.is_alive():
            store.close()
        lock.close()


if __name__ == "__main__":
    main()
