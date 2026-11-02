from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bootstrap
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import uuid

from executor import ProtocolError, StrictFetcher, require_success
from run import ManagementServer
from service import Service
from store import ServerStore


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = ServerStore(Path(self.folder.name) / "state.sqlite3")
        self.calls = []
        self.service = Service(self.store, lambda code, provider: self.calls.append(code) or 200)

    def tearDown(self):
        self.service.stop()
        self.store.close()
        self.folder.cleanup()

    def add(self, codes="A1", **kwargs):
        return self.service.enqueue({"codes": codes, "points": 200, "money": 1000, **kwargs}, "test:" + str(uuid.uuid4()))

    def test_fifo_and_idempotent_submission(self):
        args = {"codes": "A1 B2 A1", "points": 200, "money": 1000}
        first = self.service.enqueue(args, "one")
        again = self.service.enqueue(args, "one")
        self.assertEqual(first["added"], again["added"])
        self.assertEqual(2, len(self.store.jobs()))
        self.store.set_config(queue_paused=False)
        self.service.run_one()
        self.add("C3")
        self.service.run_one()
        self.service.run_one()
        self.assertEqual(["A1", "B2", "C3"], self.calls)
        self.assertEqual(3000, self.store.stats()["revenue"])

    def test_schedule_does_not_block_ready_jobs(self):
        self.add("A1", scheduled_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
        self.add("B2")
        self.store.set_config(queue_paused=False)
        self.service.run_one()
        self.assertEqual(["B2"], self.calls)

    def test_error_preserves_partial_progress_and_pauses(self):
        def account(*_):
            self.calls.append("x")
            if len(self.calls) == 2:
                raise RuntimeError("private-token-never-log")
            return 200
        self.service.executor = account
        ident = self.add(points=400)["added"][0]
        self.store.set_config(queue_paused=False)
        self.service.run_one()
        row = self.store.job(ident)
        self.assertEqual(("error", 200, 0), (row["status"], row["achieved"], row["revenue"]))
        self.assertTrue(self.store.config("queue_paused"))
        self.assertNotIn("private-token", json.dumps(self.service.snapshot()))

    def test_reboot_marks_running_interrupted_without_replay(self):
        ident = self.add()["added"][0]
        self.store.mark(ident, "running", backend_id=ident)
        Service(self.store, self.service.executor)
        self.assertEqual("interrupted", self.store.job(ident)["status"])
        self.assertFalse(self.service.run_one())
        self.assertEqual([], self.calls)

    def test_allowlist_and_revoke_cancel_waiting(self):
        self.store.save_user("11", "Partner", "partner", create=True)
        message = {"update_id": 1, "message": {"chat": {"type": "private", "id": 11}, "from": {"id": 11}, "text": "/job A1 200 Khách"}}
        self.service.handle_update(message, "42", "test_bot")
        self.service.handle_update(message, "42", "test_bot")
        self.assertEqual(1, len(self.store.jobs()))
        self.service.command("set_access", {"chat_id": "11", "active": False}, "test")
        self.assertEqual("cancelled", self.store.jobs()[0]["status"])
        self.service.handle_update({**message, "update_id": 2}, "42", "test_bot")
        self.assertEqual(1, len(self.store.jobs()))

    def test_token_encrypted_and_absent_from_snapshot(self):
        token = "123:" + "example_only_not_a_real_token_12345"
        self.store.save_bot(token, True)
        self.assertNotEqual(token, self.store.config("token"))
        self.assertEqual(token, self.store.token())
        self.assertNotIn(token, json.dumps(self.service.snapshot()))
        self.assertNotIn(self.store.config("token"), json.dumps(self.service.snapshot()))

    def test_invalid_targets_and_unsupported_provider_rejected(self):
        for points in (0, -200, 201, 10**10):
            with self.assertRaises(ValueError):
                self.add(points=points)
        with self.assertRaises(ValueError):
            self.service.command("set_config", {"mail_provider": "unknown"}, "id")

    def test_no_success_without_explicit_response(self):
        for data in ({}, {"raw_text": "not json"}, {"code": 500}, None):
            with self.assertRaises(ProtocolError):
                require_success(data)
        self.assertEqual({"ec": 0}, require_success({"ec": 0}))
        with patch("executor.MiraimindFetcher.login_guest", return_value={}):
            fetcher = StrictFetcher()
            try:
                with self.assertRaises(ProtocolError):
                    fetcher.login_guest()
            finally:
                fetcher.session.close()

    def test_default_executor_is_blocked_before_external_calls(self):
        service = Service(self.store)
        self.assertFalse(service.executor_ready)
        self.add()
        self.store.set_config(queue_paused=False)
        self.assertFalse(service.run_one())
        with self.assertRaises(ValueError):
            service.command("set_config", {"queue_paused": False}, "id")

    def test_http_requires_token_origin_and_valid_operation(self):
        token = "x" * 40
        server = ManagementServer(("127.0.0.1", 0), self.service, token)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = "http://127.0.0.1:" + str(server.server_port)
        try:
            for headers in ({}, {"Authorization": "Bearer " + token, "Origin": "https://example.com"}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(urllib.request.Request(url + "/snapshot", headers=headers))
                self.assertEqual(401, error.exception.code)
                error.exception.close()
            headers = {"Authorization": "Bearer " + token}
            with urllib.request.urlopen(urllib.request.Request(url + "/snapshot", headers=headers)) as response:
                self.assertIn("jobs", json.load(response))
            data = json.dumps({"operation": "execute_shell", "arguments": {}, "request_id": str(uuid.uuid4())}).encode()
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(urllib.request.Request(url + "/command", data=data, headers=headers))
            self.assertEqual(400, error.exception.code)
            error.exception.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
