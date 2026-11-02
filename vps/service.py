"""Durable single-job scheduler and Telegram bot; no desktop event loop."""
from datetime import datetime, timezone
import threading
import time

from job_history import export_history
from job_queue import QueuedJob, parse_codes
from telegram_protocol import TelegramClient, TelegramError, parse_job_command, notification_text

PUBLIC_CONFIG = ("rate", "auto_start", "default_points", "default_money", "default_name", "job_draft",
                 "queue_paused", "mail_provider")


class Service:
    def __init__(self, store, executor=None):
        self.store = store
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.bot_stop = threading.Event()
        self.bot_thread = None
        self.bot_status = "Bot chưa bật trên VPS."
        self.bot_accepting = False
        self.username = ""
        if executor is None:
            from executor import execute_account, readiness
            self.executor_ready, self.executor_status = readiness()
            executor = execute_account
        else:
            self.executor_ready, self.executor_status = True, "Bộ thực thi kiểm thử được cung cấp riêng."
        self.executor = executor
        # External side effects may have completed just before a crash. Never replay them.
        for row in store.jobs():
            if row["status"] in ("submitting", "running"):
                store.mark(row["id"], "interrupted", error="VPS đã khởi động lại khi đang xử lý. Kiểm tra trước khi tạo lại.")
        self.scheduler = threading.Thread(target=self._schedule, daemon=True, name="JobScheduler")

    def start(self):
        self.scheduler.start()
        if self.store.config("auto_start", False) and self.store.token():
            self.start_bot()

    def snapshot(self):
        with self.lock, self.store.lock:
            return {"config": {key: self.store.config(key) for key in PUBLIC_CONFIG if self.store.config(key) is not None},
                    "users": self.store.users(), "jobs": self.store.jobs(), "logs": self.store.logs(limit=2000),
                    "executor": {"ready": self.executor_ready, "status": self.executor_status},
                    "bot": {"accepting": self.bot_accepting, "running": bool(self.bot_thread and self.bot_thread.is_alive()),
                            "status": self.bot_status, "username": self.username, "has_token": bool(self.store.config("token"))}}

    def enqueue(self, args, request_id, chat_id="LOCAL", bot_id="", rate=None):
        codes = parse_codes(str(args.get("codes", "")))
        points, money = int(args.get("points", 2000)), int(args.get("money", 0))
        name = str(args.get("name", "")).strip()
        if not 200 <= points <= 1_000_000_000 or points % 200 or money < 0 or len(name) > 100 or len(codes) > 1000:
            raise ValueError("Nhập tối đa 1000 mã, konpeito chia hết cho 200 và tổng tiền không âm.")
        scheduled = args.get("scheduled_at")
        if scheduled:
            value = datetime.fromisoformat(scheduled.replace("Z", "+00:00"))
            if value.tzinfo is None:
                raise ValueError("Giờ hẹn phải có múi giờ.")
            scheduled = value.astimezone(timezone.utc).isoformat()
        with self.lock:
            active = {row["code"] for row in self.store.jobs() if row["status"] in ("queued", "submitting", "running")}
            added, skipped = [], []
            entries = []
            for index, code in enumerate(codes):
                key = f"{request_id}:{index}"
                existing = self.store.find_external(key)
                if existing:
                    added.append(existing["id"])
                    continue
                if code in active:
                    skipped.append(code)
                    continue
                active.add(code)
                item = QueuedJob(0, code, points, name or f"Nạp mã {code}", money, False,
                                 chat_id=chat_id, bot_id=bot_id, scheduled_at=scheduled)
                entries.append((item, key, rate))
            added.extend(self.store.add_jobs(entries))
            return {"added": added, "skipped": skipped}

    def command(self, operation, args, request_id):
        with self.lock:
            if operation == "enqueue":
                return self.enqueue(args, "remote:" + request_id)
            if operation == "logs":
                return self.store.logs(args.get("id"), limit=2000)
            if operation == "set_config":
                if set(args) - set(PUBLIC_CONFIG):
                    raise ValueError("Cài đặt không được hỗ trợ.")
                for key in ("rate", "default_money"):
                    if key in args and (type(args[key]) is not int or not 0 <= args[key] <= 10**12):
                        raise ValueError("Giá trị tiền không hợp lệ.")
                if "default_points" in args and (type(args["default_points"]) is not int or not 200 <= args["default_points"] <= 10**9 or args["default_points"] % 200):
                    raise ValueError("Konpeito phải chia hết cho 200.")
                if "mail_provider" in args and args["mail_provider"] not in ("mail_tm", "tempmail_lol"):
                    raise ValueError("Bộ thực thi Linux hỗ trợ mail_tm và tempmail_lol.")
                if "queue_paused" in args and type(args["queue_paused"]) is not bool:
                    raise ValueError("Trạng thái hàng chờ không hợp lệ.")
                if args.get("queue_paused") is False and not self.executor_ready:
                    raise ValueError(self.executor_status)
                self.store.set_config(**args)
            elif operation == "save_user":
                existing = self.store.user(args["chat_id"])
                if not (args.get("create") and existing and existing["name"] == args["name"] and existing["role"] == args["role"]):
                    self.store.save_user(**args)
            elif operation == "set_access":
                self.store.set_access(args["chat_id"], bool(args["active"]))
                if not args["active"]:
                    for row in self.store.jobs(args["chat_id"]):
                        if row["status"] == "queued":
                            self.store.mark(row["id"], "cancelled", error="Người quản lý thu hồi quyền.")
                            self._notify(row, "Đã hủy job chờ vì quyền truy cập bị thu hồi.")
            elif operation == "save_bot":
                if self.bot_thread and self.bot_thread.is_alive():
                    raise ValueError("Tắt bot trước khi đổi token.")
                self.store.save_bot(str(args.get("token", "")), bool(args.get("auto_start")))
            elif operation == "start_bot":
                self.start_bot()
            elif operation == "stop_bot":
                self.bot_stop.set()
                self.bot_accepting = False
            elif operation == "remove":
                row = self.store.job(args["id"])
                if not row or row["status"] in ("submitting", "running"):
                    raise ValueError("Không thể xóa job đang chạy.")
                if row["status"] == "queued":
                    self.store.mark(row["id"], "cancelled", error="Người quản lý xóa mã chờ.")
                    self._notify(row, "Đã hủy trước khi chạy.")
                # History is permanent. Session hiding belongs to each client.
            else:
                raise ValueError("Lệnh quản lý không được hỗ trợ.")
        return {"ok": True}

    def _notify(self, row, text):
        self.store.notify(row["bot_id"], row["chat_id"], f"Job {row['code']}: {text}")

    def run_one(self):
        with self.lock:
            if not self.executor_ready or self.store.config("queue_paused", True) or self.stop_event.is_set():
                return False
            rows = list(reversed(self.store.jobs()))
            if any(row["status"] in ("running", "submitting") for row in rows):
                return False
            row = next((row for row in rows if row["status"] == "queued" and
                        (not row["scheduled_at"] or datetime.fromisoformat(row["scheduled_at"]) <= datetime.now(timezone.utc))), None)
            if not row:
                return False
            if row["chat_id"] != "LOCAL":
                user = self.store.user(row["chat_id"])
                if not user or not user["active"]:
                    self.store.mark(row["id"], "cancelled", error="Quyền truy cập đã thu hồi.")
                    return True
            self.store.mark(row["id"], "running", backend_id=row["id"])
            provider = self.store.config("mail_provider", "mail_tm")
            self._notify(row, "Đang chạy trên VPS.")
        current, points, status, error = 0, 0, "completed", ""
        self.store.append_log(row["id"], "VPS bắt đầu thực thi job.")
        try:
            for _ in range(row["target_count"]):
                if self.stop_event.is_set():
                    status, error = "interrupted", "Dịch vụ dừng khi đang thực thi; không tự chạy lại."
                    break
                reward = self.executor(row["code"], provider)
                if type(reward) is not int or reward != 200:
                    raise RuntimeError("Không xác nhận được số konpeito từ bộ thực thi.")
                current += 1
                points += reward
                self.store.update_progress(row["id"], current, row["target_count"], points)
                self.store.append_log(row["id"], f"Đã xác nhận {current}/{row['target_count']} account, {points} konpeito.")
        except Exception as exc:
            from executor import InviteLimitError, InvalidInvitationCodeError
            if isinstance(exc, InviteLimitError):
                status, error = "invite_limit_reached", "Đạt giới hạn mã mời 24h."
            elif isinstance(exc, InvalidInvitationCodeError):
                status, error = "invalid_code", "Mã mời không hợp lệ."
            else:
                # Do not save raw transport exceptions: they may include credentials or OTP.
                status, error = "error", f"Bộ thực thi chưa xác nhận kết quả ({type(exc).__name__}). Kiểm tra dịch vụ trước khi tiếp tục."
                self.store.set_config(queue_paused=True)
        result = {"success_count": current, "target_count": row["target_count"], "achieved_points": points}
        self.store.mark(row["id"], status, result=result, error=error)
        self.store.append_log(row["id"], error or f"Kết thúc: {current}/{row['target_count']} account.", "error" if error else "success")
        self._notify(row, f"{status}\nThành công: {current}/{row['target_count']} account\nKonpeito đạt: {points:,}")
        try:
            export_history(self.store)
        except OSError:
            pass
        return True

    def _schedule(self):
        while not self.stop_event.is_set():
            try:
                self.run_one()
            except Exception:
                self.store.set_config(queue_paused=True)
                self.store.append_log(None, "Lỗi scheduler; hàng chờ đã tạm dừng.", "error")
            self.stop_event.wait(0.5)

    def start_bot(self):
        if self.bot_thread and self.bot_thread.is_alive():
            return
        token = self.store.token()
        if not token:
            raise ValueError("Nhập và lưu Bot Token trước.")
        self.bot_stop.clear()
        self.bot_thread = threading.Thread(target=self._bot, args=(token,), daemon=True, name="Telegram")
        self.bot_thread.start()

    def handle_update(self, update, bot_id, username):
        message = update.get("message") or {}
        chat, sender = message.get("chat") or {}, message.get("from") or {}
        if chat.get("type") != "private" or chat.get("id") != sender.get("id") or sender.get("is_bot"):
            return
        chat_id = str(chat["id"])
        for index, line in enumerate(str(message.get("text", "")).strip().splitlines()):
            if not line.strip():
                continue
            command = line.split()[0].lower()
            if "@" in command:
                command, target = command.split("@", 1)
                if target != username.lower():
                    continue
            if command == "/id":
                self.store.notify(bot_id, chat_id, f"Chat ID của bạn: {chat_id}")
                continue
            user = self.store.user(chat_id)
            if not user or not user["active"]:
                self.store.notify(bot_id, chat_id, "Chưa được cấp quyền. Liên hệ người quản lý.")
                continue
            if command == "/job":
                try:
                    code, points, name = parse_job_command(line)
                    rate = int(self.store.config("rate", 1000))
                    result = self.enqueue({"codes": code, "points": points, "name": name,
                                           "money": (points * rate + 50) // 100},
                                          f"tg:{bot_id}:{update['update_id']}:{index}", chat_id, bot_id, rate)
                    text = (f"Đã nhận job {code}: {points:,} konpeito; giá {(points * rate + 50) // 100:,} VND."
                            if result["added"] else "Mã đã có trong hàng chờ/đang chạy.")
                    if self.store.config("queue_paused", True):
                        text += " Hàng chờ đang tạm dừng."
                    self.store.notify(bot_id, chat_id, text)
                except ValueError as exc:
                    self.store.notify(bot_id, chat_id, str(exc))
            elif command == "/stats":
                stats = self.store.stats(chat_id)
                self.store.notify(bot_id, chat_id, f"{user['name']}\nHoàn thành: {stats['completed']}\nThất bại/hủy: {stats['failed']}\nKonpeito: {stats['konpeito']:,}\nDoanh thu: {stats['revenue']:,} VND")
            else:
                self.store.notify(bot_id, chat_id, "/id · /stats · /job MÃ_MỜI KONPEITO [TÊN_KHÁCH]\nMỗi dòng một /job, mã thêm sau vào hàng chờ.")

    def _bot(self, token):
        client = TelegramClient(token)
        last_sent = {}
        try:
            me = client.call("getMe")
            bot_id, self.username = str(me["id"]), me.get("username", "")
            if client.call("getWebhookInfo").get("url"):
                raise TelegramError("Bot đang dùng webhook. Tắt kết nối cũ trước.", 409)
            backlog = client.call("getUpdates", offset=-1, limit=1, timeout=0, allowed_updates=["message"])
            offset = max((item["update_id"] + 1 for item in backlog), default=0)
            while not self.bot_stop.is_set() and not self.stop_event.is_set():
                try:
                    self.bot_accepting = True
                    self.bot_status = f"Bot @{self.username} đang chạy trên VPS."
                    for item in self.store.pending_messages(bot_id, time.time()):
                        if time.monotonic() - last_sent.get(item["chat_id"], -100) < 1.1:
                            continue
                        try:
                            client.call("sendMessage", chat_id=item["chat_id"], text=notification_text(item["text"]))
                            self.store.message_done(item["id"])
                            last_sent[item["chat_id"]] = time.monotonic()
                        except TelegramError as exc:
                            if exc.code in (400, 403):
                                self.store.message_done(item["id"])
                            elif exc.code in (401, 404, 409):
                                raise
                            else:
                                self.store.message_retry(item["id"], time.time() + max(5, exc.retry_after))
                    updates = client.call("getUpdates", offset=offset, limit=100, timeout=2, allowed_updates=["message"])
                    for update in updates:
                        if self.bot_stop.is_set() or self.stop_event.is_set():
                            break
                        if update["update_id"] >= offset:
                            self.handle_update(update, bot_id, self.username)
                            offset = update["update_id"] + 1
                except TelegramError as exc:
                    if exc.code in (401, 404, 409):
                        raise
                    self.bot_accepting = False
                    self.bot_status = str(exc)
                    self.bot_stop.wait(max(2, exc.retry_after))
        except (TelegramError, ValueError) as exc:
            self.bot_status = str(exc)
        except Exception:
            self.bot_status = "Bot gặp lỗi; kiểm tra cấu hình và kết nối VPS."
        finally:
            self.bot_accepting = False
            if self.bot_stop.is_set():
                self.bot_status = "Bot đã tắt trên VPS. Job đã nhận vẫn được xử lý."

    def stop(self):
        self.stop_event.set()
        self.bot_stop.set()
        if self.scheduler.is_alive():
            self.scheduler.join(timeout=110)
        if self.bot_thread and self.bot_thread.is_alive():
            self.bot_thread.join(timeout=10)
