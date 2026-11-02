"""Persistent job history, CSV, job logs, drafts and scheduling; isolated data."""
import csv
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PyQt6.QtWidgets import QApplication
import gui
from job_history import export_history, vn_time
from job_queue import QueuedJob
from job_worker import AutomationWorkerThread
from telegram_store import TelegramStore
from test_job_queue import FakeWorker


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "state.sqlite3"
        self.store = TelegramStore(self.path)

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def add(self, code, status="completed", name="Khách A", chat="LOCAL", error=""):
        record = self.store.add_job(QueuedJob(1, code, 2000, name, 20000, True, chat_id=chat), "order:" + code)
        self.store.mark(record, status, backend_id="backend-" + code,
                        result={"success_count": 7, "target_count": 10, "achieved_points": 1400, "error_message": error})
        return record

    def test_search_matches_creator_unicode_ids_code_and_order_with_literal_wildcards(self):
        self.store.save_user("11", "Đức Chính", "partner", create=True)
        # IDs are searchable too; random UUIDs can coincidentally contain "11".
        with patch("telegram_store.uuid.uuid4", side_effect=[Mock(hex="first-record"), Mock(hex="second-record")]):
            first = self.add("A12345", chat="11", name="Khách % A")
            self.add("B12345", "failed", name="Khách B")
        for query in ("đức chính", "11", "backend-A12345", "order:A12345", "A12345", "%"):
            self.assertEqual([row["id"] for row in self.store.history(query)], [first])
        self.assertEqual(self.store.history("' OR 1=1 --"), [])
        self.assertEqual(len(self.store.history(status="failed")), 1)

    def test_partial_failure_and_limit_message_preserve_actual_counts(self):
        record = self.add("LIMIT", "failed", error="invite_limit_reached: rolling 24-hour limit")
        row = self.store.history(status="invite_limit_reached")[0]
        self.assertEqual((row["id"], row["success_count"], row["target_count"], row["achieved"]), (record, 7, 10, 1400))
        self.assertTrue(row["completed_at"])
        self.assertEqual(row["revenue"], 0)

    def test_csv_is_full_history_utf8_and_protects_formula_cells(self):
        self.add("A", name="=HYPERLINK(\"bad\")")
        self.add("B", "failed", name="Khách Việt")
        self.assertEqual(len(self.store.history(limit=1)), 1)
        path = export_history(self.store)
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)
        self.assertIn("Konpeito thực tế", rows[0])
        self.assertTrue(next(row for row in rows if row["Mã mời"] == "A")["Khách hàng / Job"].startswith("'="))
        self.assertEqual(vn_time("2026-11-01T17:36:14+00:00"), "02/11/2026 00:36:14")

    def test_locked_csv_does_not_lose_history_or_previous_file(self):
        self.add("A")
        path = export_history(self.store)
        before = path.read_bytes()
        self.add("B")
        with patch("job_history.os.replace", side_effect=PermissionError("locked")):
            with self.assertRaises(PermissionError):
                export_history(self.store)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.store.history()), 2)
        self.assertEqual(list(path.parent.glob(".job_history_*.tmp")), [])

    def test_actual_overdelivery_is_visible_without_exceeding_quoted_revenue(self):
        record = self.store.add_job(QueuedJob(1, "OVER", 1000, "Khách", 10000, True))
        self.store.mark(record, "completed", result={"success_count": 6, "target_count": 5,
            "achieved_points": 1200, "completed_at": "2026-11-01T17:36:14+00:00"})
        row = self.store.history()[0]
        self.assertEqual((row["achieved"], row["success_count"], row["revenue"]), (1200, 6, 10000))
        self.assertEqual(vn_time(row["completed_at"]), "02/11/2026 00:36:14")

    def test_log_deduplication_and_scope_survive_reopening(self):
        first, second = self.add("A"), self.add("B")
        self.store.append_log(first, "First", source_key="backend:a:1")
        self.store.append_log(first, "First", source_key="backend:a:1")
        self.store.append_log(second, "Second")
        self.store.close()
        self.store = TelegramStore(self.path)
        self.assertEqual([row["message"] for row in self.store.logs(first)], ["First"])
        self.assertEqual([row["message"] for row in self.store.logs(second)], ["Second"])

    def test_migration_from_previous_schema_keeps_old_jobs_and_prices(self):
        self.store.close()
        legacy = Path(self.folder.name) / "legacy.sqlite3"
        db = sqlite3.connect(legacy)
        db.executescript("""
            CREATE TABLE users(chat_id TEXT PRIMARY KEY,name TEXT,role TEXT,active INTEGER);
            INSERT INTO users VALUES('11','Đức','partner',1);
            CREATE TABLE jobs(id TEXT PRIMARY KEY,external_key TEXT UNIQUE NOT NULL,chat_id TEXT NOT NULL,
                bot_id TEXT NOT NULL DEFAULT '',code TEXT NOT NULL,points INTEGER NOT NULL,name TEXT NOT NULL,
                money INTEGER NOT NULL,rate INTEGER,use_ui INTEGER NOT NULL,status TEXT NOT NULL,backend_id TEXT,
                achieved INTEGER NOT NULL DEFAULT 0,revenue INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            INSERT INTO jobs VALUES('old','order:old','11','42','OLD',2000,'Khách cũ',20000,1000,1,'completed','backend-old',
                2000,20000,'','2026-11-01T16:00:00+00:00','2026-11-01T17:00:00+00:00');
        """)
        db.close()
        self.store = TelegramStore(legacy)
        row = self.store.history()[0]
        self.assertEqual((row["id"], row["creator_name"], row["success_count"], row["target_count"], row["money"], row["revenue"]),
                         ("old", "Đức", 10, 10, 20000, 20000))
        self.assertEqual(row["completed_at"], "2026-11-01T17:00:00+00:00")


class JobsWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "state.sqlite3"
        self.poller = patch.object(gui, "StatusPollingThread")
        self.poller.start().return_value.isRunning.return_value = False
        self.worker_patch = patch.object(gui, "AutomationWorkerThread", FakeWorker)
        self.worker_patch.start()
        self.window = gui.ControlMainWindow(self.path, auto_start_telegram=False)

    def tearDown(self):
        if self.window.worker:
            self.window.worker.stop()
            self.window.worker = None
        self.window.close()
        self.app.processEvents()
        self.window.telegram_store.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.worker_patch.stop()
        self.poller.stop()
        self.folder.cleanup()

    def add(self, code):
        self.window.edit_code.setPlainText(code)
        self.window._start_job(True)

    def test_completion_exports_history_and_removal_only_hides_session_row(self):
        self.add("A12345")
        active = self.window.active_item
        self.window.worker.log_signal.emit("Literal <b>job log</b>")
        self.window.worker.complete()
        self.window.worker.exit()
        self.window._remove_job(active.number)
        self.assertEqual(self.window.job_queue.items, [])
        self.assertEqual(len(self.window.telegram_store.history()), 1)
        self.assertTrue(self.window.jobs_page.history.csv_path.exists())
        self.window.jobs_page.show_log(active.record_id)
        self.assertEqual(self.window.jobs_page.tabs.currentIndex(), 2)
        self.assertIn("Literal <b>job log</b>", self.window.log_box.toPlainText())
        self.window.jobs_page.logs.clear_screen()
        self.window.jobs_page.logs.load()
        self.assertIn("Literal <b>job log</b>", self.window.log_box.toPlainText())

    def test_draft_survives_reopen_without_starting_a_job(self):
        page = self.window.jobs_page
        page.edit_code.setPlainText("A12345\nB12345")
        page.edit_points.setText("4000")
        page.edit_money.setText("70000")
        page.edit_name.setText("Khách A")
        page.save_draft()
        self.window.close()
        self.window.telegram_store.close()
        self.window = gui.ControlMainWindow(self.path, auto_start_telegram=False)
        self.assertEqual(self.window.edit_code.toPlainText(), "A12345\nB12345")
        self.assertEqual(self.window.edit_points.text(), "4000")
        self.assertIsNone(self.window.worker)

    def test_scheduled_job_waits_until_due_and_does_not_block_ready_jobs(self):
        page = self.window.jobs_page
        page.schedule_check.setChecked(True)
        page._set_schedule_date(datetime.now(timezone.utc) + timedelta(hours=1))
        self.add("LATER")
        scheduled = self.window.job_queue.items[0]
        self.assertIsNone(self.window.worker)
        self.assertTrue(self.window.telegram_store.job(scheduled.record_id)["scheduled_at"])
        page.schedule_check.setChecked(False)
        self.add("NOW")
        self.assertEqual(self.window.active_item.invitation_code, "NOW")
        self.window.worker.complete()
        self.window.worker.exit()
        scheduled.scheduled_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.window._pump_queue()
        self.assertEqual(self.window.active_item.invitation_code, "LATER")

    def test_backend_logs_are_filtered_by_job_and_not_replayed_twice(self):
        worker = AutomationWorkerThread("A", 2000, "Khách", 0)
        worker.bridge = Mock()
        worker.bridge.get_recent_logs.return_value = [
            {"id": "one", "job_id": "mine", "message": "Mine"},
            {"id": "two", "job_id": "other", "message": "Other"},
        ]
        events = []
        worker.backend_log_signal.connect(events.append)
        worker._read_backend_logs("mine", force=True)
        worker._read_backend_logs("mine", force=True)
        self.assertEqual([event["message"] for event in events], ["Mine"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
