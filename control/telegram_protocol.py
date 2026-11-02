"""Telegram HTTP protocol and command parsing, usable without Qt or Windows."""
import json
import re
import urllib.error
import urllib.request


class TelegramError(RuntimeError):
    def __init__(self, message, code=0, retry_after=0):
        super().__init__(message)
        self.code, self.retry_after = code, retry_after


def notification_text(text):
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
            raise TelegramError("Không kết nối được Telegram. Đang chờ kết nối lại.") from None
        if not isinstance(data, dict) or not data.get("ok"):
            description = str(data.get("description", "Phản hồi Telegram không hợp lệ.")) if isinstance(data, dict) else "Phản hồi Telegram không hợp lệ."
            description = description.replace(self._token, "[TOKEN ĐÃ ẨN]")
            code = data.get("error_code", 0) if isinstance(data, dict) else 0
            retry = (data.get("parameters") or {}).get("retry_after", 0) if isinstance(data, dict) else 0
            raise TelegramError(description, code, retry)
        return data.get("result")


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
