"""Run read-only network checks away from the Qt GUI thread."""

import asyncio
import threading

from PySide6.QtCore import QThread, Signal


class AsyncTaskThread(QThread):
    result = Signal(object)
    failed = Signal(str)

    def __init__(self, factory, parent=None):
        super().__init__(parent)
        self.factory = factory
        self._loop = None
        self._task = None
        self._cancelled = threading.Event()

    def request_cancel(self):
        if self._cancelled.is_set():
            return
        self._cancelled.set()
        if self._loop and self._task:
            try:
                self._loop.call_soon_threadsafe(self._task.cancel)
            except RuntimeError:
                pass

    def run(self):
        async def execute():
            self._loop = asyncio.get_running_loop()
            if self._cancelled.is_set():
                return
            self._task = asyncio.create_task(self.factory())
            try:
                self.result.emit(await self._task)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.failed.emit(str(exc))

        try:
            asyncio.run(execute())
        except Exception as exc:
            self.failed.emit(str(exc))


def start_background(parent, factory, on_result, on_error, on_finished=None):
    """Keep threads alive until their actual QThread.finished signal."""
    workers = getattr(parent, '_background_workers', None)
    if workers is None:
        workers = parent._background_workers = set()
    worker = AsyncTaskThread(factory, parent)
    workers.add(worker)
    worker.result.connect(on_result)
    worker.failed.connect(on_error)

    def finished():
        workers.discard(worker)
        if on_finished:
            on_finished()
        worker.deleteLater()

    worker.finished.connect(finished)
    worker.start()
    return worker
