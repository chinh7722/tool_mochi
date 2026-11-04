"""Loopback HTTP API, Qt dispatch, process ownership and graceful shutdown."""
import argparse
from dataclasses import dataclass, field
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import signal
import threading
import time

from core.remote.protocol import MAX_BODY, MutationJournal, RemoteError, encode

log = logging.getLogger(__name__)


@dataclass(repr=False)
class Command:
    action: str
    payload: dict = field(repr=False)
    request_id: str = ''
    done: threading.Event = field(default_factory=threading.Event)
    result: dict = field(default_factory=dict)


class ManagementServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, token, submit):
        self.token, self.submit = token, submit
        self.capacity = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = 'HoshinoManagement/1'
    sys_version = ''

    def setup(self):
        super().setup()
        self.connection.settimeout(35)

    def log_message(self, *_):
        # Never log Authorization, bodies, passwords or query strings.
        pass

    def respond(self, status, value):
        body = encode(value)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def dispatch(self, *, write=False):
        authorization = self.headers.get('Authorization', '')
        if not hmac.compare_digest(authorization.encode(), ('Bearer '+self.server.token).encode()):
            self.respond(401, {'error': 'Không có quyền truy cập.'})
            return
        if self.headers.get('Origin') or self.headers.get('Transfer-Encoding'):
            self.respond(400, {'error': 'Yêu cầu không hợp lệ.'})
            return
        expected = '/v1/command' if write else '/v1/state'
        if self.path != expected:
            self.respond(404, {'error': 'Không tìm thấy API.'})
            return
        if not self.server.capacity.acquire(blocking=False):
            self.respond(503, {'error': 'VPS đang bận; kiểm tra trạng thái sau.'})
            return
        try:
            if write:
                try:
                    size = int(self.headers.get('Content-Length', '-1'))
                    if not 0<size<=MAX_BODY:
                        raise ValueError()
                    raw = self.rfile.read(size)
                    if len(raw)!=size:
                        raise ValueError()
                    body = json.loads(raw)
                    if not isinstance(body,dict) or not isinstance(body.get('action'),str) or not isinstance(body.get('payload'),dict):
                        raise ValueError()
                    command = Command(body['action'],body['payload'],body.get('request_id',''))
                except (ValueError, TypeError, OSError):
                    self.respond(400,{'error':'Nội dung lệnh không hợp lệ hoặc quá lớn.'})
                    return
            else:
                command = Command('state',{})
            self.server.submit(command)
            if not command.done.wait(25):
                self.respond(504,{'error':'Chưa xác nhận kết quả lệnh. Kết nối lại và kiểm tra trạng thái trước khi gửi lệnh mới.'})
                return
            result = command.result
            self.respond(200 if result.get('ok') else 400,result)
        finally:
            self.server.capacity.release()

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(write=True)


def main():
    parser = argparse.ArgumentParser(description='hoshino-tool Linux runtime')
    parser.add_argument('--data-dir', default=os.environ.get('HOSHINO_DATA_DIR', 'data'))
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    token = os.environ.get('HOSHINO_API_TOKEN', '')
    if len(token)<32 or any(c.isspace() for c in token):
        parser.error('Set HOSHINO_API_TOKEN to a random token of at least 32 characters.')
    if not 1<=args.port<=65535:
        parser.error('Invalid port.')
    directory = Path(args.data_dir).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    os.environ['HOSHINO_DATA_DIR'] = str(directory)
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    if os.name!='nt':
        os.umask(0o077)
    # One service process per data directory, including separate terminal launches.
    lock = (directory/'runtime.lock').open('a+b')
    try:
        if os.name=='nt':
            import msvcrt
            lock.write(b'0'); lock.flush(); lock.seek(0)
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        parser.error('Another runtime already owns this data directory.')
    from PySide6.QtCore import QObject, Signal, Slot, QTimer
    from PySide6.QtWidgets import QApplication
    from core.remote.runtime import Runtime
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    runtime = Runtime(directory)
    journal = MutationJournal(directory/'management_operations.sqlite3')

    class Bridge(QObject):
        received = Signal(object)

        def __init__(self):
            super().__init__()
            self.received.connect(self.handle)

        @Slot(object)
        def handle(self, command):
            try:
                if command.action=='state' and not command.request_id:
                    command.result = {'ok':True,'state':runtime.state()}
                else:
                    def invoke():
                        try:
                            return {'ok':True,'result':runtime.command(command.action,command.payload)}
                        except (RemoteError,ValueError) as exc:
                            # Controllers use safe validation strings. No raw HTTP exceptions.
                            return {'ok':False,'error':str(exc)}
                    readonly = ('job.logs','statistics.get','history.temporary','user.jobs','customer.history','finance.ledger','batch.list',
                                'history.export','proxy.export')
                    command.result = (invoke() if command.action in readonly else
                        journal.execute(command.request_id,command.action,command.payload,invoke))
            except RemoteError as exc:
                command.result = {'ok':False,'error':str(exc)}
            except Exception as exc:
                log.error('Management operation failed: %s',type(exc).__name__)
                command.result = {'ok':False,'error':'Không hoàn tất được lệnh trên VPS. Kiểm tra trạng thái và nhật ký dịch vụ.'}
            finally:
                command.payload.clear()
                command.done.set()

    bridge = Bridge()
    server = ManagementServer(('127.0.0.1',args.port),token,bridge.received.emit)
    thread = threading.Thread(target=server.serve_forever,name='management-api',daemon=True)
    thread.start()
    stopping = threading.Event()
    def tick():
        if stopping.is_set() and not runtime.shutdown():
            app.quit()
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    for name in ('SIGINT','SIGTERM'):
        if hasattr(signal,name):
            signal.signal(getattr(signal,name),lambda *_:stopping.set())
    print(f'Hoshino runtime ready on 127.0.0.1:{args.port}',flush=True)
    try:
        app.exec()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        lock.close()


if __name__=='__main__':
    main()
