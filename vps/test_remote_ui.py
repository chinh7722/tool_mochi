"""Real HTTP between Qt remote UI and a temporary server; no external services."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import tempfile
import threading
import time
import unittest

from PyQt6.QtWidgets import QApplication
from remote_client import RemoteConnection
from remote_gui import RemoteWindow
from run import ManagementServer
from service import Service
from store import ServerStore


APP = QApplication.instance() or QApplication([])


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = ServerStore(Path(self.folder.name) / "server.sqlite3")
        self.service = Service(self.store, lambda *_: 200)
        self.server = ManagementServer(("127.0.0.1", 0), self.service, "x" * 40)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = RemoteConnection("http://127.0.0.1:" + str(self.server.server_port), "x" * 40)
        self.window = RemoteWindow(self.connection)
        self.wait(lambda: self.window._initialized)

    def wait(self, condition):
        deadline = time.monotonic() + 7
        while time.monotonic() < deadline:
            APP.processEvents()
            if condition():
                return
            time.sleep(0.01)
        self.fail("Remote UI did not synchronize")

    def tearDown(self):
        self.connection.stop()
        self.connection.wait(7000)
        self.window.close()
        APP.processEvents()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.service.stop()
        self.store.close()
        self.folder.cleanup()

    def test_remote_enqueue_history_and_logs(self):
        self.window.jobs.edit_code.setPlainText("A1\nB2")
        self.window.jobs.edit_points.setText("200")
        self.window.enqueue(False)
        self.wait(lambda: len(self.window.store.jobs()) == 2)
        self.assertEqual(2, len(self.store.jobs()))
        self.store.set_config(queue_paused=False)
        self.service.run_one()
        self.connection.wakeup.set()
        self.wait(lambda: self.window.store.stats()["completed"] == 1)
        self.assertEqual(200, self.window.store.stats()["konpeito"])
        row = self.window.store.history()[0]
        self.window.jobs.show_log(row["id"])
        self.wait(lambda: "Kết thúc" in self.window.jobs.logs.box.toPlainText())

    def test_user_configuration_and_no_local_bot(self):
        self.window.store.save_user("11", "Partner", "partner", create=True)
        self.wait(lambda: self.window.store.user("11") is not None)
        self.window.store.set_access("11", False)
        self.wait(lambda: not self.window.store.user("11")["active"])
        self.assertIsNone(self.window.telegram.poller)
        self.assertNotIn("ui_automator", sys.modules)

    def test_server_token_is_not_available_to_client(self):
        self.store.save_bot("123:" + "example_fake_token_abcdefghijklmnop", False)
        self.connection.wakeup.set()
        self.wait(lambda: self.service.snapshot()["bot"]["has_token"])
        self.assertEqual("", self.window.store.token())
        self.assertIsNone(self.window.store.config("token"))


if __name__ == "__main__":
    unittest.main()
