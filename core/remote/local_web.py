"""Loopback-only browser manager. VPS credentials stay in this local process."""
import argparse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import unquote, urlsplit
import uuid
import webbrowser

from core.remote.client import RemoteClient
from core.remote.protocol import MAX_BODY, RemoteError, encode

READ_COMMANDS = frozenset(('job.logs','job.search','statistics.get','history.temporary','stock.list','stock.export.get','stock.exports',
    'user.jobs','customer.history','finance.ledger','batch.list','history.export','proxy.export'))

COMMANDS = frozenset(('job.create', 'job.stop', 'job.continue', 'job.logs', 'job.search', 'statistics.get', 'proxy.import', 'proxy.check',
    'pricing.save','history.temporary','history.temporary.clear',
    'stock.create','stock.list','stock.stop','stock.resume','stock.delete','stock.retry',
    'stock.export.prepare','stock.export.get','stock.export.finish','stock.exports',
    'proxy.rotate', 'proxy.delete', 'proxy.export', 'settings.save', 'telegram.save',
    'telegram.start', 'telegram.stop', 'telegram.broadcast', 'telegram.cancel_broadcast',
    'user.save', 'user.active', 'user.mail_keys', 'user.jobs', 'user.report', 'customer.save',
    'customer.delete', 'customer.balance', 'customer.history', 'customer.settle',
    'finance.ledger', 'finance.reconcile', 'finance.price', 'sepay.save', 'sepay.start',
    'sepay.stop', 'sepay.reconcile', 'lookup.start', 'lookup.cancel', 'batch.upload',
    'batch.list', 'history.export'))


def browser_numbers(value):
    """Keep large Chat IDs and exact wallet balances lossless in JavaScript."""
    if type(value) is int and abs(value) > 2**53-1:
        return str(value)
    if isinstance(value, dict):
        return {k: browser_numbers(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [browser_numbers(v) for v in value]
    return value


def restore_numbers(payload):
    payload = dict(payload)
    for key in ('chat_id', 'owner_id', 'expected_units'):
        value = payload.get(key)
        if isinstance(value, str) and re.fullmatch(r'-?[0-9]{1,19}', value):
            payload[key] = int(value)
    if isinstance(payload.get('recipients'), list):
        payload['recipients'] = [int(v) if isinstance(v, str) and re.fullmatch(r'[0-9]{1,19}', v)
            else v for v in payload['recipients']]
    if isinstance(payload.get('prices'),list):
        payload['prices']=[{**row,'price_units':int(row['price_units'])}
            if isinstance(row,dict) and isinstance(row.get('price_units'),str) and re.fullmatch(r'[0-9]{1,19}',row['price_units']) else row
            for row in payload['prices']]
    return payload


class LocalWebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, root, *, port=8787, client_factory=RemoteClient, connection_path=None):
        self.root = Path(root).resolve()
        self.connection_path = Path(connection_path) if connection_path else self.root/'data'/'remote_connection.json'
        self.client_factory = client_factory
        self.client = None
        self.url = 'http://127.0.0.1:8765'
        self._token = ''
        self.session = secrets.token_urlsafe(32)
        self.bootstrap = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)
        self.mutation = threading.Lock()
        self.capacity = threading.BoundedSemaphore(12)
        try:
            saved = json.loads(self.connection_path.read_text(encoding='utf-8'))
            candidate = self.client_factory(saved['url'], saved['token'])
            self.url, self._token, self.client = saved['url'], saved['token'], candidate
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass
        super().__init__(('127.0.0.1', port), LocalWebHandler)
        self.cookie_name = 'hoshino_web_' + str(self.server_port)

    @property
    def browser_url(self):
        return f'http://127.0.0.1:{self.server_port}/#session={self.bootstrap}'

    def connect(self, payload):
        url, token = payload.get('url', self.url), payload.get('token', '') or self._token
        if not isinstance(url, str) or not isinstance(token, str) or len(url)>2048 or len(token)>2048:
            raise RemoteError('Địa chỉ hoặc token kết nối không hợp lệ.')
        client = self.client_factory(url, token)
        state = client.state()
        self.connection_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.connection_path.with_name('.remote_connection_' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_text(json.dumps({'url': url, 'token': token}, ensure_ascii=False), encoding='utf-8')
            temporary.chmod(0o600)
            temporary.replace(self.connection_path)
        finally:
            temporary.unlink(missing_ok=True)
        self.url, self._token, self.client = url, token, client
        return state


class LocalWebHandler(BaseHTTPRequestHandler):
    server_version = 'HoshinoLocalWeb/1'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(35)

    def log_message(self, *_):
        pass

    def respond(self, status, value, *, content_type='application/json; charset=utf-8', headers=None):
        body = value if isinstance(value, bytes) else encode(browser_numbers(value))
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def trusted_request(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in allowed or self.headers.get('Transfer-Encoding'):
            return False
        origin = self.headers.get('Origin')
        return (not origin or origin in {'http://' + host for host in allowed}) and \
            self.headers.get('Sec-Fetch-Site') not in ('cross-site',)

    def authorized(self):
        try:
            cookies = SimpleCookie()
            cookies.load(self.headers.get('Cookie', ''))
            item = cookies.get(self.server.cookie_name)
            return item is not None and secrets.compare_digest(item.value, self.server.session)
        except Exception:
            return False

    def read_json(self):
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            raise RemoteError('Yêu cầu phải dùng JSON.')
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= MAX_BODY:
                raise ValueError()
            value = json.loads(self.rfile.read(size))
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, UnicodeError):
            raise RemoteError('Nội dung yêu cầu không hợp lệ hoặc quá lớn.') from None

    def do_OPTIONS(self):
        self.respond(405, {'error': 'Không hỗ trợ yêu cầu này.'})

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)

    def dispatch(self, write):
        if not self.trusted_request():
            self.respond(403, {'error': 'Chỉ cho phép truy cập từ giao diện web local.'})
            return
        if not self.server.capacity.acquire(blocking=False):
            self.respond(503, {'error': 'Đang có nhiều yêu cầu; thử lại sau.'})
            return
        try:
            self.handle_request(write)
        except RemoteError as exc:
            self.respond(400, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            self.respond(500, {'error': 'Chưa xử lý được yêu cầu local. Kiểm tra kết nối rồi thử lại.'})
        finally:
            self.server.capacity.release()

    def handle_request(self, write):
        parsed = urlsplit(self.path)
        path = unquote(parsed.path)
        if parsed.query or parsed.fragment:
            self.respond(404, {'error': 'Không tìm thấy trang.'})
            return
        if path == '/api/session' and write:
            supplied = self.headers.get('X-Web-Bootstrap', '')
            if not secrets.compare_digest(supplied, self.server.bootstrap):
                self.respond(403, {'error': 'Mở web bằng Launch_Web_VPS_Manager.bat để tạo phiên local.'})
                return
            self.read_json()
            self.respond(200, {'ok': True}, headers={'Set-Cookie':
                f'{self.server.cookie_name}={self.server.session}; HttpOnly; SameSite=Strict; Path=/'})
            return
        if path.startswith('/api/'):
            if not self.authorized():
                self.respond(401, {'error': 'Mở web bằng Launch_Web_VPS_Manager.bat để tạo phiên local.'})
                return
            if write and not secrets.compare_digest(self.headers.get('X-Web-CSRF', ''), self.server.csrf):
                self.respond(403, {'error': 'Phiên local không hợp lệ; tải lại trang.'})
                return
            if path == '/api/config' and not write:
                self.respond(200, {'url': self.server.url, 'has_token': bool(self.server._token), 'csrf': self.server.csrf})
            elif path == '/api/state' and not write:
                if self.server.client is None:
                    raise RemoteError('Mở SSH tunnel và nhập kết nối tại Cài đặt chung.')
                self.respond(200, self.server.client.state())
            elif path in ('/api/connect', '/api/command') and write:
                payload = self.read_json()
                locked = not (path=='/api/command' and isinstance(payload.get('action'),str)
                    and payload['action'] in READ_COMMANDS)
                if locked and not self.server.mutation.acquire(blocking=False):
                    self.respond(409, {'error': 'Đang thực hiện một lệnh khác; chờ kết quả trước.'})
                    return
                try:
                    if path == '/api/connect':
                        result = self.server.connect(payload)
                    else:
                        action = payload.get('action')
                        data = payload.get('payload', {})
                        request_id = payload.get('request_id')
                        if not isinstance(action, str) or action not in COMMANDS or not isinstance(data, dict):
                            raise RemoteError('Lệnh quản lý không được hỗ trợ.')
                        try:
                            if str(uuid.UUID(request_id)) != request_id:
                                raise ValueError()
                        except (ValueError, TypeError, AttributeError):
                            raise RemoteError('Mã yêu cầu không hợp lệ.') from None
                        if self.server.client is None:
                            raise RemoteError('Kết nối VPS trước khi thực hiện lệnh.')
                        result = self.server.client.command(action, restore_numbers(data), request_id=request_id)
                    self.respond(200, result)
                finally:
                    if locked:self.server.mutation.release()
            else:
                self.respond(404, {'error': 'Không tìm thấy chức năng.'})
            return
        if write:
            self.respond(405, {'error': 'Không hỗ trợ yêu cầu này.'})
            return
        roots = {'/web/': self.server.root/'web', '/branding/': self.server.root/'gui'/'assets'/'branding',
                 '/icons/': self.server.root/'gui'/'assets'}
        if path == '/':
            target = self.server.root/'web'/'index.html'
        else:
            prefix = next((prefix for prefix in roots if path.startswith(prefix)), None)
            if prefix is None:
                self.respond(404, {'error': 'Không tìm thấy trang.'})
                return
            base = roots[prefix].resolve()
            target = (base/path[len(prefix):]).resolve()
            if not target.is_relative_to(base):
                self.respond(404, {'error': 'Không tìm thấy tài nguyên.'})
                return
        if target.suffix.lower() not in ('.html', '.css', '.js', '.svg', '.png', '.jpg', '.ico') or not target.is_file():
            self.respond(404, {'error': 'Không tìm thấy tài nguyên.'})
            return
        content_type = {'.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
            '.html': 'text/html; charset=utf-8'}.get(target.suffix, mimetypes.guess_type(target.name)[0] or 'application/octet-stream')
        self.respond(200, target.read_bytes(), content_type=content_type)


def main():
    parser = argparse.ArgumentParser(description='Hoshino local web VPS manager')
    parser.add_argument('--port', type=int, default=8787)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if root.name == 'src':
        root = root.parent
    if not 1 <= args.port <= 65535:
        parser.error('Port phải từ 1 đến 65535.')
    try:
        server = LocalWebServer(root, port=args.port)
    except OSError:
        parser.exit(1, f'Port {args.port} đang được dùng. Đóng cửa sổ web local cũ hoặc dùng --port khác.\n')
    print(f'Hoshino web local: http://127.0.0.1:{server.server_port}/', flush=True)
    print('Giữ cửa sổ này mở. Ctrl+C để dừng web local; job trên VPS vẫn chạy.', flush=True)
    if not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(server.browser_url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
