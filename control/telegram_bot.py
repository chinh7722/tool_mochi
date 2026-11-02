"""Telegram Bot API transport and long polling, entirely off the GUI thread."""
import json
import re
import threading
import time
import urllib.error
import urllib.request

from PyQt6.QtCore import QThread, pyqtSignal


class TelegramError(RuntimeError):
    def __init__(self, message, code=0, retry_after=0):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


def notification_text(text):
    """Normalize currency in notifications queued by older controller versions."""
    text = text.replace("Bean đã bán:", "Konpeito đã bán:")
    return re.sub(r"(\d[ \t]+)beans?\b", r"\1konpeito", text, flags=re.IGNORECASE)


class TelegramClient:
    def __init__(self, token):
        token = token.strip()
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", token):
            raise ValueError("Bot Token chưa đúng định dạng. Sao chép token từ @BotFather.")
        self._token = token

    def call(self, method, **payload):
        if method not in ("getMe", "getUpdates", "getWebhookInfo", "sendMessage"):
            raise ValueError("Phương thức Telegram không được hỗ trợ.")
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                data = json.loads(error.read().decode("utf-8"))
            except Exception:
                raise TelegramError("Telegram từ chối yêu cầu.", error.code) from None
            finally:
                error.close()
        except Exception:
            # urllib exceptions can contain the complete URL, including the token.
            raise TelegramError("Không kết nối được Telegram. Đang chờ kết nối lại.") from None
        if not isinstance(data, dict) or not data.get("ok"):
            description = str(data.get("description", "Phản hồi Telegram không hợp lệ.")) if isinstance(data, dict) else "Phản hồi Telegram không hợp lệ."
            description = description.replace(self._token, "[TOKEN ĐÃ ẨN]")
            code = data.get("error_code", 0) if isinstance(data, dict) else 0
            retry = (data.get("parameters") or {}).get("retry_after", 0) if isinstance(data, dict) else 0
            raise TelegramError(description, code, retry)
        return data.get("result")


class TelegramPoller(QThread):
    update_received = pyqtSignal(dict)
    connected = pyqtSignal(dict)
    status_changed = pyqtSignal(str)
    log_signal = pyqtSignal(str)

    def __init__(self, token, store, client=None):
        super().__init__()
        self.client = client or TelegramClient(token)
        self.store = store
        self._stop_event = threading.Event()
        self.bot_id = ""
        self._last_sent = {}

    def stop(self):
        self._stop_event.set()

    def _flush_messages(self):
        for message in self.store.pending_messages(self.bot_id, time.time()):
            if self._stop_event.is_set():
                return
            chat_id = message["chat_id"]
            if time.monotonic() - self._last_sent.get(chat_id, -100) < 1.1:
                continue
            try:
                self.client.call("sendMessage", chat_id=chat_id, text=notification_text(message["text"]))
                self.store.message_done(message["id"])
                self._last_sent[chat_id] = time.monotonic()
            except TelegramError as error:
                if error.code in (401, 404, 409):
                    raise
                if error.code in (400, 403):
                    self.store.message_done(message["id"])
                    self.log_signal.emit(f"[Telegram] Không gửi được thông báo tới Chat ID {chat_id}: {error}")
                else:
                    self.store.message_retry(message["id"], time.time() + max(5, error.retry_after))

    def run(self):
        try:
            me = self.client.call("getMe")
            self.bot_id = str(me["id"])
            if self.client.call("getWebhookInfo").get("url"):
                raise TelegramError("Bot đang dùng webhook ở nơi khác. Tắt kết nối webhook đó trước khi bật bot trong Controller.", 409)
            # Drop commands accumulated while this bot connection was off.
            backlog = self.client.call("getUpdates", offset=-1, limit=1, timeout=0, allowed_updates=["message"])
            offset = max((update["update_id"] + 1 for update in backlog), default=0)
            if self._stop_event.is_set():
                return
            self.connected.emit(me)
            self.status_changed.emit(f"Bot @{me.get('username', self.bot_id)} đang nhận lệnh mới.")
            disconnected = False
            while not self._stop_event.is_set():
                try:
                    self._flush_messages()
                    updates = self.client.call("getUpdates", offset=offset, limit=100, timeout=2, allowed_updates=["message"])
                    if disconnected:
                        self.status_changed.emit(f"Bot @{me.get('username', self.bot_id)} đã kết nối lại, đang nhận lệnh mới.")
                    disconnected = False
                    for update in updates:
                        if self._stop_event.is_set():
                            break
                        if update.get("update_id", -1) < offset:
                            continue
                        self.update_received.emit({**update, "bot_id": self.bot_id})
                        offset = update["update_id"] + 1
                except TelegramError as error:
                    if error.code in (401, 404, 409):
                        raise
                    self.status_changed.emit(str(error))
                    disconnected = True
                    if self._stop_event.wait(max(2, error.retry_after)):
                        break
        except Exception as error:
            self.status_changed.emit(f"Bot đã dừng: {error}")
        finally:
            self.log_signal.emit("[Telegram] Đã dừng nhận lệnh. Job đã vào hàng chờ vẫn được giữ lại.")


def parse_job_command(text):
    parts = text.strip().split(maxsplit=3)
    if len(parts) < 3:
        raise ValueError("Cú pháp: /job MÃ_MỜI KONPEITO [TÊN_KHÁCH]. Ví dụ: /job npxMgy 2000 Khách A")
    code = parts[1]
    if not code.isascii() or not code.isalnum():
        raise ValueError("Mã mời chỉ gồm chữ và số.")
    try:
        points = int(parts[2])
    except ValueError:
        raise ValueError("Konpeito phải là số nguyên dương, chia hết cho 200.") from None
    if points <= 0 or points > 1_000_000_000 or points % 200:
        raise ValueError("Konpeito phải từ 200 đến 1.000.000.000 và chia hết cho 200 (mỗi account 200 konpeito).")
    name = parts[3].strip() if len(parts) == 4 else f"Nạp mã {code}"
    if len(name) > 100:
        raise ValueError("Tên khách hàng tối đa 100 ký tự.")
    return code, points, name
