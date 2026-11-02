"""Telegram integration checks. All HTTP and job execution are simulated."""
import io
import os
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtWidgets import QApplication, QLabel
from job_queue import QueuedJob
from job_worker import AutomationWorkerThread
from telegram_bot import TelegramClient, TelegramError, TelegramPoller, parse_job_command
from telegram_store import TelegramStore
from test_job_queue import FakeWorker
import gui
import telegram_page

TOKEN = "123456:" + "example_token_for_tests_only_123456"


def item(code="A12345", chat_id="11", money=20000):
    return QueuedJob(1, code, 2000, "Khách A", money, True, chat_id=chat_id, bot_id="42")


def update(text, update_id=100, chat_id=11, chat_type="private"):
    return {"update_id": update_id, "bot_id": "42", "message": {
        "chat": {"id": chat_id, "type": chat_type}, "from": {"id": chat_id, "is_bot": False}, "text": text}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "state.sqlite3"
        self.store = TelegramStore(self.path)

    def tearDown(self):
        self.store.close()
        self.folder.cleanup()

    def test_token_encrypted_at_rest_and_restored_by_same_windows_account(self):
        self.store.save_bot(TOKEN, True)
        self.assertEqual(self.store.token(), TOKEN)
        self.assertNotIn(TOKEN, self.store.config("token"))
        self.assertTrue(self.store.config("auto_start"))

    def test_editing_name_or_role_does_not_restore_revoked_access(self):
        self.store.save_user("11", "Người A", "admin", create=True)
        self.store.set_access("11", False)
        self.store.save_user("11", "Tên mới", "partner")
        self.assertFalse(self.store.user("11")["active"])
        self.store.set_access("11", True)
        self.assertTrue(self.store.user("11")["active"])

    def test_completed_job_revenue_uses_locked_price_and_counts_once(self):
        record_id = self.store.add_job(item(), "tg:42:100:0", 1000)
        self.store.set_config(rate=9999)
        result = {"achieved_points": 2000, "success_count": 10}
        self.store.mark(record_id, "completed", result=result)
        self.store.mark(record_id, "completed", result=result)
        stats = self.store.stats("11")
        self.assertEqual((stats["completed"], stats["konpeito"], stats["revenue"]), (1, 2000, 20000))
        self.assertEqual(self.store.jobs()[0]["rate"], 1000)
        self.assertIsNone(self.store.add_job(item(), "tg:42:100:0", 9999))

    def test_failed_partial_job_does_not_add_sales_revenue(self):
        record_id = self.store.add_job(item())
        self.store.mark(record_id, "failed", result={"achieved_points": 600})
        stats = self.store.stats("11")
        self.assertEqual((stats["failed"], stats["konpeito"], stats["revenue"]), (1, 0, 0))
        self.assertEqual(self.store.jobs()[0]["achieved"], 600)

    def test_uncertain_submission_not_replayed_but_known_id_can_be_monitored(self):
        queued = self.store.add_job(item("A"))
        known = self.store.add_job(item("B"))
        unknown = self.store.add_job(item("C"))
        self.store.mark(known, "running", backend_id="backend-known")
        self.store.mark(unknown, "submitting")
        recovered = self.store.recover_jobs()
        self.assertEqual([entry["id"] for entry in recovered], [queued, known])
        self.assertEqual(next(entry for entry in self.store.jobs() if entry["id"] == unknown)["status"], "interrupted")

    def test_outbox_preserves_per_chat_order_when_retrying(self):
        self.store.notify("42", "11", "First")
        self.store.notify("42", "11", "Second")
        self.store.notify("42", "22", "Other user")
        pending = self.store.pending_messages("42", time.time())
        self.assertEqual([entry["text"] for entry in pending], ["First", "Other user"])
        self.store.message_retry(pending[0]["id"], time.time() + 100)
        self.assertEqual([entry["text"] for entry in self.store.pending_messages("42", time.time())], ["Other user"])


class TelegramGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / "state.sqlite3"
        self.poll_patch = patch.object(gui, "StatusPollingThread")
        self.poll_patch.start().return_value.isRunning.return_value = False
        self.worker_patch = patch.object(gui, "AutomationWorkerThread", FakeWorker)
        self.worker_patch.start()
        self.window = gui.ControlMainWindow(state_path=self.path, auto_start_telegram=False)
        self.page, self.store = self.window.telegram_page, self.window.telegram_store
        self.page.accepting = True
        self.page.bot_id, self.page.username = "42", "test_bot"
        self.store.save_user("11", "Khách A", "admin", create=True)
        self.store.save_user("22", "Khách B", "partner", create=True)
        FakeWorker.instances = []

    def tearDown(self):
        self.window._closing = True
        if self.window.worker:
            self.window.worker.stop()
        self.window.worker = None
        self.window.close()
        self.app.processEvents()
        self.window.telegram_store.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.worker_patch.stop()
        self.poll_patch.stop()
        self.folder.cleanup()

    def messages(self, chat="11"):
        with self.store.lock:
            return [row[0] for row in self.store.db.execute("SELECT text FROM outbox WHERE chat_id=? ORDER BY id", (chat,))]

    def test_tab_exists_and_has_no_mail_cost_or_profit_fields(self):
        self.window._navigate("telegram")
        self.assertIs(self.window.pages.currentWidget(), self.page)
        texts = [label.text() for label in self.page.findChildren(QLabel)]
        self.assertFalse(any("chi phí mail" in text.lower() or "lợi nhuận" in text.lower() for text in texts))
        self.page.refresh(force=True)
        self.assertEqual(self.page.stat_labels["users"].text(), "2")

    def test_unauthorized_user_can_get_id_but_cannot_create_a_job(self):
        self.page.handle_update(update("/id\n/job A12345 2000", chat_id=33))
        self.assertIn("33", self.messages("33")[0])
        self.assertEqual(self.window.job_queue.items, [])
        self.assertEqual(self.store.jobs(), [])

    def test_group_messages_and_spoofed_sender_are_ignored(self):
        self.page.handle_update(update("/job A12345 2000", chat_type="group"))
        forged = update("/job A12345 2000")
        forged["message"]["from"]["id"] = 22
        self.page.handle_update(forged)
        self.assertEqual(self.store.jobs(), [])

    def test_telegram_jobs_append_to_existing_fifo_without_stealing_mouse(self):
        self.window.edit_code.setPlainText("LOCAL1 LOCAL2")
        self.window._start_job(True)
        local_worker = self.window.worker
        self.page.handle_update(update("/job REMOTE1 2000 Khách A\n/job REMOTE2 1000 Khách B"))
        self.assertIs(self.window.worker, local_worker)
        self.assertEqual([entry.invitation_code for entry in self.window.job_queue.items], ["LOCAL1", "LOCAL2", "REMOTE1", "REMOTE2"])
        self.assertTrue(all(entry.use_ui_automation for entry in self.window.job_queue.items))
        self.assertEqual([entry.unit_price for entry in self.window.job_queue.items], [0, 0, 20000, 10000])
        self.assertEqual(len(FakeWorker.instances), 1)

    def test_repeated_update_cannot_create_duplicate_even_after_completion(self):
        message = update("/job A12345 2000 Khách A")
        self.page.handle_update(message)
        worker = self.window.worker
        worker.complete()
        worker.exit()
        self.page.handle_update(message)
        self.assertEqual(len(FakeWorker.instances), 1)
        self.assertEqual(len(self.store.jobs("11")), 1)

    def test_rejected_duplicate_update_is_not_reaccepted_later(self):
        self.page.handle_update(update("/job A12345 2000", update_id=100))
        rejected = update("/job A12345 2000", update_id=101, chat_id=22)
        self.page.handle_update(rejected)
        self.window.worker.complete()
        self.window.worker.exit()
        self.page.handle_update(rejected)
        self.assertEqual(len(FakeWorker.instances), 1)
        self.assertEqual(self.store.jobs("22"), [])

    def test_revocation_cancels_waiting_codes_but_keeps_current_job(self):
        self.page.handle_update(update("/job A12345 2000\n/job B12345 2000"))
        active = self.window.worker
        self.page.edit_chat.setText("11")
        self.page.change_access(False)
        self.assertIs(self.window.worker, active)
        self.assertEqual([entry.invitation_code for entry in self.window.job_queue.items], ["A12345"])
        self.assertEqual(next(entry for entry in self.store.jobs() if entry["code"] == "B12345")["status"], "cancelled")
        self.page.handle_update(update("/job C12345 2000", update_id=101))
        self.assertEqual(len(self.window.job_queue.items), 1)

    def test_stats_are_personal_even_for_admin(self):
        own = self.store.add_job(item())
        other = self.store.add_job(item("B12345", "22", 90000))
        self.store.mark(own, "completed", result={"achieved_points": 2000})
        self.store.mark(other, "completed", result={"achieved_points": 2000})
        self.page.handle_update(update("/stats"))
        message = self.messages()[-1]
        self.assertIn("20,000", message)
        self.assertNotIn("90,000", message)
        self.assertNotIn("110,000", message)

    def test_price_change_does_not_change_queued_job_and_notifications_are_saved(self):
        self.window.queue_paused = True
        self.page.handle_update(update("/job A12345 2000"))
        self.page.edit_rate.setText("5000")
        self.page.save_rate()
        self.page.handle_update(update("/job B12345 2000", update_id=101))
        self.assertEqual([entry.unit_price for entry in self.window.job_queue.items], [20000, 100000])
        self.assertIn("20,000 VND", self.messages()[0])
        self.assertIn("100,000 VND", self.messages()[1])

    def test_reopen_restores_queue_paused_and_known_id_is_never_submitted_again(self):
        first = self.store.add_job(item("QUEUED"))
        known = self.store.add_job(item("KNOWN"))
        unknown = self.store.add_job(item("UNKNOWN"))
        self.store.mark(known, "running", backend_id="known-backend-id")
        self.store.mark(unknown, "submitting")
        self.window.close()
        self.store.close()
        self.window = gui.ControlMainWindow(state_path=self.path, auto_start_telegram=False)
        self.page, self.store = self.window.telegram_page, self.window.telegram_store
        self.assertTrue(self.window.queue_paused)
        self.assertEqual([entry.record_id for entry in self.window.job_queue.items], [known, first])
        self.assertEqual(self.window.job_queue.items[0].resume_job_id, "known-backend-id")
        self.assertIsNone(self.window.worker)

    def test_bot_start_receive_reply_and_stop_with_real_qthread(self):
        client = Mock()
        incoming, sent = [], []
        lock = threading.Lock()

        def call(method, **payload):
            if method == "getMe":
                return {"id": 42, "username": "test_bot"}
            if method == "getWebhookInfo":
                return {"url": ""}
            if method == "sendMessage":
                sent.append(payload)
                return {"message_id": len(sent)}
            if payload["offset"] == -1:
                return []
            time.sleep(0.01)
            with lock:
                messages = list(incoming)
                incoming.clear()
                return messages

        client.call.side_effect = call
        poller = TelegramPoller(TOKEN, self.store, client=client)

        def await_state(condition):
            deadline = time.monotonic() + 5
            while not condition() and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.005)
            self.assertTrue(condition(), self.page.status.text())

        self.page.accepting = False
        self.page.edit_token.setText(TOKEN)
        with patch.object(telegram_page, "TelegramPoller", return_value=poller):
            try:
                self.page.start_bot()
                await_state(lambda: self.page.accepting)
                with lock:
                    incoming.append(update("/job REMOTE1 2000 Khách A"))
                await_state(lambda: len(self.window.job_queue.items) == 1)
                await_state(lambda: bool(sent))
                self.assertEqual(self.window.worker.settings["invitation_code"], "REMOTE1")
                self.assertIn("Đã nhận job", sent[0]["text"])
                self.assertEqual(sent[0]["chat_id"], "11")
                self.page.stop_bot()
                await_state(lambda: self.page.poller is None)
                self.assertFalse(self.page.accepting)
                self.assertTrue(self.page.btn_start.isEnabled())
            finally:
                try:
                    poller.stop()
                    poller.wait(1000)
                except RuntimeError:
                    pass


class TransportTests(unittest.TestCase):
    def test_successful_request_uses_json_plain_text_without_parse_mode(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"ok":true,"result":{"message_id":1}}'
        with patch("telegram_bot.urllib.request.urlopen", return_value=response) as request:
            result = TelegramClient(TOKEN).call("sendMessage", chat_id="11", text="Khách {A} <b>B</b>")
        self.assertEqual(result["message_id"], 1)
        payload = json.loads(request.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload, {"chat_id": "11", "text": "Khách {A} <b>B</b>"})

    def test_notification_delivery_and_rate_limit_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            store = TelegramStore(Path(folder) / "state.sqlite3")
            client = Mock()
            poller = TelegramPoller(TOKEN, store, client=client)
            poller.bot_id = "42"
            try:
                store.notify("42", "11", "Queued")
                store.notify("42", "11", "Started")
                client.call.side_effect = TelegramError("Too many requests", 429, 12)
                poller._flush_messages()
                self.assertEqual(store.pending_messages("42", time.time()), [])
                future = store.pending_messages("42", time.time() + 13)
                self.assertEqual([entry["text"] for entry in future], ["Queued"])
                store.message_retry(future[0]["id"], 0)
                client.call.side_effect = None
                poller._flush_messages()
                self.assertEqual([entry["text"] for entry in store.pending_messages("42", time.time())], ["Started"])
            finally:
                store.close()

    def test_network_errors_never_expose_token(self):
        with patch("telegram_bot.urllib.request.urlopen", side_effect=urllib.error.URLError("secret URL " + TOKEN)):
            with self.assertRaises(TelegramError) as context:
                TelegramClient(TOKEN).call("getMe")
        self.assertNotIn(TOKEN, str(context.exception))

    def test_http_error_response_redacts_token_and_preserves_retry_after(self):
        body = ('{"ok":false,"error_code":429,"description":"' + TOKEN + '","parameters":{"retry_after":9}}').encode()
        error = urllib.error.HTTPError("https://test", 429, "Too many", {}, io.BytesIO(body))
        with patch("telegram_bot.urllib.request.urlopen", side_effect=error):
            with self.assertRaises(TelegramError) as context:
                TelegramClient(TOKEN).call("getMe")
        self.assertEqual(context.exception.retry_after, 9)
        self.assertNotIn(TOKEN, str(context.exception))

    def test_polling_drops_offline_backlog_and_advances_offset(self):
        with tempfile.TemporaryDirectory() as folder:
            store = TelegramStore(Path(folder) / "state.sqlite3")
            client = Mock()
            poller = TelegramPoller(TOKEN, store, client=client)
            received = []
            poller.update_received.connect(received.append)
            calls = []

            def call(method, **payload):
                calls.append((method, payload))
                if method == "getMe":
                    return {"id": 42, "username": "test_bot"}
                if method == "getWebhookInfo":
                    return {"url": ""}
                if payload["offset"] == -1:
                    return [update("/job OLD 2000", 7)]
                if payload["offset"] == 8:
                    return [update("/id", 8), update("/stats", 9)]
                poller.stop()
                return []

            client.call.side_effect = call
            try:
                poller.run()
                self.assertEqual([entry["update_id"] for entry in received], [8, 9])
                self.assertEqual([payload["offset"] for method, payload in calls if method == "getUpdates"], [-1, 8, 10])
            finally:
                store.close()

    def test_existing_webhook_is_not_deleted_automatically(self):
        store = Mock()
        client = Mock()
        client.call.side_effect = [{"id": 42}, {"url": "https://existing.example/hook"}]
        poller = TelegramPoller(TOKEN, store, client=client)
        statuses = []
        poller.status_changed.connect(statuses.append)
        poller.run()
        self.assertIn("webhook", statuses[-1])
        self.assertEqual([call.args[0] for call in client.call.call_args_list], ["getMe", "getWebhookInfo"])

    def test_recovering_known_backend_id_uses_only_read_requests(self):
        worker = AutomationWorkerThread("A12345", 2000, "Khách A", 20000, existing_job_id="known")
        worker.automator, worker.bridge = Mock(), Mock()
        worker._wait = Mock(return_value=False)
        worker.bridge.get_job.return_value = {"job": {"id": "known", "status": "completed", "achieved_points": 2000}}
        result = []
        worker.finished_signal.connect(result.append)
        worker.run()
        self.assertEqual(result[0]["id"], "known")
        worker.automator.enter_job_and_run.assert_not_called()
        worker.bridge.create_job.assert_not_called()

    def test_job_command_rejects_non_multiple_of_200(self):
        with self.assertRaises(ValueError):
            parse_job_command("/job A12345 250")
        self.assertEqual(parse_job_command("/job A12345 2000 Khách A"), ("A12345", 2000, "Khách A"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
