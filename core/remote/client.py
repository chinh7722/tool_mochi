"""Desktop transport. No automation, bot polling or database runs on the client."""
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
import uuid

from core.remote.protocol import MAX_BODY, RemoteError, encode


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RemoteClient:
    def __init__(self, url, token, *, timeout=30):
        parsed = urlsplit(url)
        if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise RemoteError('Dùng địa chỉ localhost qua SSH tunnel, ví dụ http://127.0.0.1:8765.')
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
            raise RemoteError('Địa chỉ kết nối không hợp lệ.')
        if not isinstance(token, str) or len(token) < 32 or any(c.isspace() for c in token):
            raise RemoteError('Nhập token quản lý VPS (tối thiểu 32 ký tự).')
        self.url, self.token, self.timeout = url.rstrip('/'), token, timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, data=None):
        body = None if data is None else encode(data)
        if body is not None and len(body) > MAX_BODY:
            raise RemoteError('Nội dung gửi quá lớn.')
        request = Request(self.url + path, data=body, headers={
            'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_BODY * 4 + 1)
                if len(raw) > MAX_BODY * 4:
                    raise RemoteError('Phản hồi quá lớn; giảm dữ liệu yêu cầu.')
                result = json.loads(raw)
        except HTTPError as exc:
            if exc.code == 401:
                exc.close()
                raise RemoteError('Token quản lý không đúng.') from None
            try:
                message = json.loads(exc.read(MAX_BODY)).get('error', 'VPS từ chối yêu cầu.')
            except Exception:
                message = 'VPS từ chối yêu cầu.'
            finally:
                exc.close()
            raise RemoteError(str(message)) from None
        except (URLError, OSError, ValueError):
            raise RemoteError('Mất kết nối VPS. Lệnh có thể đã được nhận; kiểm tra trạng thái sau khi nối lại trước khi gửi lệnh mới.') from None
        return result

    def state(self):
        return self.request('/v1/state')

    def command(self, action, payload=None, *, request_id=None):
        # Never automatically retry a POST with a fresh identity.
        return self.request('/v1/command', {'request_id': request_id or str(uuid.uuid4()),
            'action': action, 'payload': payload or {}})
