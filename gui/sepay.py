"""SePay polling outside the Qt UI thread; no inbound server is required."""
import asyncio
import threading
from PySide6.QtCore import QThread, Signal
from curl_cffi.requests import AsyncSession
from core.sepay import SePayClient, SePayError


class SePayThread(QThread):
    status_changed = Signal(str)
    synced = Signal(int)

    def __init__(self, config, store, parent=None):
        super().__init__(parent)
        self.config, self.store = config, store
        self._stop = threading.Event()
        self._loop = self._task = None

    def request_stop(self):
        self._stop.set()
        if self._loop and self._task:
            try:
                self._loop.call_soon_threadsafe(self._task.cancel)
            except RuntimeError:
                pass

    def run(self):
        async def execute():
            self._loop = asyncio.get_running_loop()
            self._task = asyncio.current_task()
            async with AsyncSession(trust_env=False,proxies={'http':'','https':''},verify=True,timeout=20) as session:
                client = SePayClient(self.config,session)
                while not self._stop.is_set():
                    delay = 30
                    try:
                        count = await client.sync(self.store)
                        self.synced.emit(count)
                        self.status_changed.emit(f'Đã đồng bộ SePay. Cộng tiền cho {count} giao dịch mới; kiểm tra lại sau 30 giây.')
                    except SePayError as exc:
                        self.status_changed.emit(str(exc))
                        delay = exc.retry_after
                    await asyncio.sleep(delay)
        try:
            asyncio.run(execute())
        except asyncio.CancelledError:
            pass
        except Exception:
            self.status_changed.emit('Đồng bộ SePay đã dừng do lỗi dữ liệu hoặc cấu hình. Kiểm tra lại trong tool.')
        finally:
            self._loop = self._task = None
