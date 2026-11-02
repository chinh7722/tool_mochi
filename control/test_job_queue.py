"""Queue and job identity regression tests; no local app or backend writes.

Run: python -I test_job_queue.py
"""
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
from job_queue import JobQueue, parse_codes
from job_worker import AutomationWorkerThread
import gui


def job(job_id, status="running", code="A12345", name="Nạp mã A12345"):
    return {"id": job_id, "status": status, "customer": {"invitation_code": code},
            "job_name": name, "target_count": 10, "success_count": 0}


class QueueModelTests(unittest.TestCase):
    def test_parse_multiple_separators_in_order(self):
        self.assertEqual(parse_codes(" A12345\nB12345,C12345;D12345\tE12345 "),
                         ["A12345", "B12345", "C12345", "D12345", "E12345"])

    def test_parse_rejects_prose_or_invalid_codes(self):
        with self.assertRaises(ValueError):
            parse_codes("Mã mời: A12345")

    def test_fifo_late_additions_and_settings_snapshot(self):
        queue = JobQueue()
        queue.add(["A", "B"], 2000, "", 100, True)
        active = queue.take_next()
        queue.add(["C", "D"], 4000, "Khách mới", 200, False)
        self.assertIsNone(queue.take_next())
        self.assertEqual(active.invitation_code, "A")
        active.status = "completed"
        second = queue.take_next()
        self.assertEqual((second.invitation_code, second.target_points, second.unit_price, second.use_ui_automation),
                         ("B", 2000, 100, True))
        second.status = "completed"
        third = queue.take_next()
        self.assertEqual((third.invitation_code, third.target_points, third.job_name), ("C", 4000, "Khách mới"))

    def test_duplicates_are_skipped_in_batch_and_while_active(self):
        queue = JobQueue()
        added, skipped = queue.add(["A", "A", "B"], 2000, "", 0, True)
        queue.take_next()
        more, skipped_more = queue.add(["A", "B", "C"], 2000, "", 0, True)
        self.assertEqual([item.invitation_code for item in added + more], ["A", "B", "C"])
        self.assertEqual(skipped + skipped_more, ["A", "A", "B"])

    def test_remove_does_not_cancel_running_job(self):
        queue = JobQueue()
        queue.add(["A", "B", "C"], 2000, "", 0, True)
        queue.take_next()
        self.assertEqual(queue.remove_waiting({1, 2}), 1)
        self.assertEqual([item.invitation_code for item in queue.items], ["A", "C"])


class FakeWorker(QObject):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int, int, str)
    finished_signal = pyqtSignal(dict)
    identified_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    finished = pyqtSignal()
    instances = []

    def __init__(self, **kwargs):
        super().__init__()
        self.settings = kwargs
        self.running = False
        self.instances.append(self)

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def stop(self):
        self.running = False

    def complete(self, status="completed"):
        self.finished_signal.emit(job("backend-id", status, self.settings["invitation_code"], self.settings["job_name"]))

    def exit(self):
        self.running = False
        self.finished.emit()


class QueueGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        FakeWorker.instances = []
        self.worker_patch = patch.object(gui, "AutomationWorkerThread", FakeWorker)
        self.poller_patch = patch.object(gui, "StatusPollingThread")
        self.worker_patch.start()
        self.poller = self.poller_patch.start().return_value
        self.poller.isRunning.return_value = False
        self.temp_dir = tempfile.TemporaryDirectory()
        self.window = gui.ControlMainWindow(state_path=Path(self.temp_dir.name) / "state.sqlite3", auto_start_telegram=False)

    def tearDown(self):
        self.window._closing = True
        if self.window.worker:
            self.window.worker.stop()
        self.window.worker = None
        self.window.close()
        self.window.deleteLater()
        self.application.processEvents()
        self.window.telegram_store.close()
        self.temp_dir.cleanup()
        self.worker_patch.stop()
        self.poller_patch.stop()

    def add(self, text, use_ui=True):
        self.window.edit_code.setPlainText(text)
        self.window._start_job(use_ui)

    def test_add_while_running_appends_and_waits_for_thread_exit(self):
        self.add("A12345\nB12345")
        first = self.window.worker
        self.add("C12345\nD12345")
        self.assertIs(self.window.worker, first)
        self.assertEqual(len(FakeWorker.instances), 1)
        self.assertTrue(self.window.btn_ui_run.isEnabled())
        self.assertEqual([item.invitation_code for item in self.window.job_queue.items],
                         ["A12345", "B12345", "C12345", "D12345"])
        first.complete()
        self.assertEqual(len(FakeWorker.instances), 1)
        first.exit()
        self.assertEqual(self.window.worker.settings["invitation_code"], "B12345")

    def test_pause_allows_current_job_to_finish_then_resume_fifo(self):
        self.add("A12345 B12345")
        first = self.window.worker
        self.window._toggle_queue_pause()
        first.complete()
        first.exit()
        self.assertIsNone(self.window.worker)
        self.add("C12345")
        self.assertIsNone(self.window.worker)
        self.window._toggle_queue_pause()
        self.assertEqual(self.window.worker.settings["invitation_code"], "B12345")

    def test_later_form_edits_do_not_change_waiting_jobs(self):
        self.add("A12345 B12345")
        self.window.edit_points.setText("4000")
        self.window.edit_money.setText("50000")
        self.add("C12345", use_ui=False)
        self.window.worker.complete()
        self.window.worker.exit()
        self.assertEqual(self.window.worker.settings["target_points"], 2000)
        self.assertEqual(self.window.worker.settings["unit_price"], 0)
        self.window.worker.complete()
        self.window.worker.exit()
        self.assertEqual(self.window.worker.settings["target_points"], 4000)
        self.assertFalse(self.window.worker.settings["use_ui_automation"])

    def test_submission_error_pauses_queue_and_keeps_later_codes(self):
        self.add("A12345 B12345")
        first = self.window.worker
        first.error_signal.emit("Cannot confirm submission")
        first.exit()
        self.assertTrue(self.window.queue_paused)
        self.assertIsNone(self.window.worker)
        self.assertEqual([item.status for item in self.window.job_queue.items], ["error", "queued"])

    def test_terminal_invalid_code_advances_to_next(self):
        self.add("A12345 B12345")
        self.window.worker.complete("invalid_code")
        self.window.worker.exit()
        self.assertEqual(self.window.worker.settings["invitation_code"], "B12345")

    def test_closing_never_starts_the_next_code(self):
        self.add("A12345 B12345")
        self.window._closing = True
        self.window.worker.complete()
        self.window.worker.exit()
        self.assertIsNone(self.window.worker)
        self.assertEqual(len(FakeWorker.instances), 1)

    def test_real_worker_threads_advance_fifo_with_late_addition(self):
        store, submitted = {}, []
        lock = threading.Lock()
        workers = []

        def make_worker(**settings):
            worker = AutomationWorkerThread(**settings)
            workers.append(worker)

            def submit(code, points, name, money):
                with lock:
                    submitted.append(code)
                    job_id = f"test-{len(submitted)}"
                    store[job_id] = job(job_id, code=code, name=name)

            def list_jobs(**kwargs):
                with lock:
                    return {"items": [dict(item) for item in store.values()]}

            def get_job(job_id):
                with lock:
                    return {"job": dict(store[job_id])}

            worker.automator = Mock()
            worker.automator.enter_job_and_run.side_effect = submit
            worker.bridge = Mock()
            worker.bridge.get_jobs.side_effect = list_jobs
            worker.bridge.get_job.side_effect = get_job
            worker._wait = lambda seconds=1: worker._stop_event.wait(0.005)
            return worker

        def await_state(condition):
            deadline = time.monotonic() + 5
            while not condition() and time.monotonic() < deadline:
                self.application.processEvents()
                time.sleep(0.005)
            self.assertTrue(condition(), self.window.log_box.toPlainText())

        with patch.object(gui, "AutomationWorkerThread", side_effect=make_worker):
            try:
                self.add("A12345 B12345")
                await_state(lambda: self.window.active_item.backend_id is not None)
                self.add("C12345")
                self.assertEqual(submitted, ["A12345"])
                with lock:
                    store["test-1"]["status"] = "completed"
                await_state(lambda: len(submitted) == 2)
                self.assertEqual(submitted, ["A12345", "B12345"])
                with lock:
                    store["test-2"]["status"] = "invalid_code"
                await_state(lambda: len(submitted) == 3)
                self.assertEqual(submitted, ["A12345", "B12345", "C12345"])
                with lock:
                    store["test-3"]["status"] = "completed"
                await_state(lambda: self.window.worker is None)
                self.assertEqual([item.status for item in self.window.job_queue.items],
                                 ["completed", "invalid_code", "completed"])
            finally:
                for worker in workers:
                    # Qt may already have deleted completed workers via deleteLater.
                    try:
                        worker.stop()
                        worker.wait(1000)
                    except RuntimeError:
                        pass


class WorkerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.worker = AutomationWorkerThread("A12345", 2000, "Nạp mã A12345", 0)
        self.worker.bridge = Mock()
        self.worker.automator = Mock()
        self.worker._wait = Mock(return_value=False)
        self.completed, self.errors = [], []
        self.worker.finished_signal.connect(self.completed.append)
        self.worker.error_signal.connect(self.errors.append)

    def test_gui_job_excludes_previous_completed_same_code_and_unrelated_jobs(self):
        old = job("old", "completed")
        other = job("unrelated", "completed", "OTHER", "Other")
        fresh = job("new")
        self.worker.bridge.get_jobs.side_effect = [
            {"items": [old, other]}, {"items": [other, old]}, {"items": [other, fresh, old]},
        ]
        self.worker.bridge.get_job.return_value = {"job": job("new", "completed")}
        self.worker.run()
        self.assertEqual(self.errors, [])
        self.assertEqual([item["id"] for item in self.completed], ["new"])
        self.worker.bridge.get_job.assert_called_once_with("new")
        self.worker.automator.enter_job_and_run.assert_called_once()
        self.worker.bridge.create_job.assert_not_called()

    def test_backend_connection_failure_before_submit_does_not_touch_gui(self):
        self.worker.bridge.get_jobs.return_value = {"error": {"message": "offline"}}
        self.worker.run()
        self.assertEqual(len(self.errors), 1)
        self.worker.automator.enter_job_and_run.assert_not_called()

    def test_ambiguous_new_jobs_stop_without_retrying_submission(self):
        self.worker.bridge.get_jobs.side_effect = [{"items": []}, {"items": [job("one"), job("two")]}]
        self.worker.run()
        self.assertEqual(len(self.errors), 1)
        self.assertEqual(self.completed, [])
        self.worker.automator.enter_job_and_run.assert_called_once()

    def test_api_result_id_is_tracked_through_a_temporary_disconnect(self):
        self.worker.use_ui_automation = False
        self.worker.bridge.create_job.return_value = {"job": job("exact")}
        self.worker.bridge.get_job.side_effect = [
            {"error": {"message": "offline"}}, {"job": job("wrong", "completed")},
            {"job": job("exact", "completed")},
        ]
        self.worker.run()
        self.assertEqual([item["id"] for item in self.completed], ["exact"])
        self.assertEqual(self.errors, [])
        self.worker.bridge.create_job.assert_called_once()
        self.assertEqual([call.args for call in self.worker.bridge.get_job.call_args_list], [("exact",)] * 3)

    def test_paginated_snapshot_does_not_mistake_an_older_job_for_a_new_one(self):
        self.worker.bridge.get_jobs.side_effect = [
            {"items": [job("one")], "pagination": {"has_more": True}},
            {"items": [job("two")]},
        ]
        self.assertEqual([item["id"] for item in self.worker._list_jobs()], ["one", "two"])
        self.assertEqual(self.worker.bridge.get_jobs.call_args_list[1].kwargs["page"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
