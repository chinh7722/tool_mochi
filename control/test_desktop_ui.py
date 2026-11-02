"""Dashboard data, navigation and defaults. Uses isolated SQLite, no network."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton
import gui
from job_queue import QueuedJob
from telegram_bot import notification_text


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.poller = patch.object(gui, "StatusPollingThread")
        self.poller.start().return_value.isRunning.return_value = False
        self.window = gui.ControlMainWindow(Path(self.folder.name) / "state.sqlite3", auto_start_telegram=False)

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.window.telegram_store.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.poller.stop()
        self.folder.cleanup()

    def test_sidebar_navigation_and_create_job_button(self):
        self.assertIs(self.window.pages.currentWidget(), self.window.dashboard)
        for route, button in self.window.sidebar.buttons.items():
            button.click()
            self.assertIs(self.window.pages.currentWidget(), self.window.routes[route])
            self.assertTrue(button.isChecked())
        self.window.dashboard.btn_create.click()
        self.assertIs(self.window.pages.currentWidget(), self.window.routes["jobs"])
        labels = [widget.text().casefold() for widget in self.window.findChildren(QLabel)]
        labels += [widget.text().casefold() for widget in self.window.findChildren(QPushButton)]
        self.assertFalse(any("proxy" in text for text in labels))

    def test_dashboard_totals_do_not_count_cancellation_as_failure(self):
        store = self.window.telegram_store
        for index, (status, achieved) in enumerate((("completed", 1000), ("failed", 400), ("cancelled", 200), ("queued", 0))):
            item = QueuedJob(index, str(index), 2000, "Khách", 0, True)
            record = store.add_job(item)
            store.mark(record, status, result={"achieved_points": achieved})
        self.window._refresh_dashboard()
        labels = self.window.dashboard.metrics
        self.assertEqual({key: labels[key].text() for key in ("total", "completed", "failed", "queued", "konpeito")},
                         {"total": "4", "completed": "1", "failed": "1", "queued": "1", "konpeito": "1,600"})

    def test_running_progress_is_not_double_counted_when_job_is_older_than_recent_table(self):
        store = self.window.telegram_store
        active = QueuedJob(1, "ACTIVE", 2000, "Khách", 0, True, status="running")
        active.record_id = store.add_job(active)
        store.mark(active.record_id, "running", result={"achieved_points": 200})
        for index in range(11):
            store.add_job(QueuedJob(index + 2, str(index), 2000, "Chờ", 0, True))
        self.window.dashboard.refresh(store, active, (3, 10, 600, "running"))
        self.assertEqual(self.window.dashboard.metrics["konpeito"].text(), "600")
        self.assertEqual(self.window.dashboard.table.rowCount(), 10)

    def test_saving_defaults_preserves_already_queued_settings(self):
        self.window.queue_paused = True
        self.window.edit_code.setPlainText("FIRST")
        self.window._start_job(True)
        settings = self.window.settings_page
        settings.points.setText("4000")
        settings.money.setText("50000")
        settings.name.setText("Khách mới")
        settings.save()
        self.window.edit_code.setPlainText("SECOND")
        self.window._start_job(True)
        items = self.window.job_queue.items
        self.assertEqual((items[0].target_points, items[0].unit_price), (2000, 0))
        self.assertEqual((items[1].target_points, items[1].unit_price, items[1].job_name), (4000, 50000, "Khách mới"))
        self.assertEqual(self.window.telegram_store.config("default_points"), 4000)

    def test_old_pending_currency_messages_are_renamed_without_changing_customer_name(self):
        old = "Khách hàng: Bean\nMục tiêu: 2,000 bean\nBean đã bán: 1,000"
        self.assertEqual(notification_text(old), "Khách hàng: Bean\nMục tiêu: 2,000 konpeito\nKonpeito đã bán: 1,000")


if __name__ == "__main__":
    unittest.main(verbosity=2)
