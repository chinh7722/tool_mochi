"""Submit one job and monitor that exact backend ID until it terminates."""
import threading
import time

from PyQt6.QtCore import QThread, pyqtSignal

from api_bridge import LocalBackendBridge
from ui_automator import LocalAppAutomator


TERMINAL_STATUSES = frozenset(("completed", "failed", "cancelled", "invalid_code", "invite_limit_reached"))


class AutomationWorkerThread(QThread):
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int, int, int, str)
    finished_signal = pyqtSignal(dict)
    identified_signal = pyqtSignal(str)
    error_signal = pyqtSignal(str)
    backend_log_signal = pyqtSignal(dict)

    def __init__(self, invitation_code, target_points, job_name, unit_price, use_ui_automation=True, existing_job_id=None):
        super().__init__()
        self.invitation_code = invitation_code.strip()
        self.target_points = target_points
        self.target_accounts = max(1, target_points // 200)
        self.job_name = job_name.strip()
        self.unit_price = unit_price
        self.use_ui_automation = use_ui_automation
        self.existing_job_id = existing_job_id
        self.automator = LocalAppAutomator(log_callback=self.log_signal.emit)
        self.bridge = LocalBackendBridge()
        self.is_running = True
        self._stop_event = threading.Event()
        self._seen_events = set()
        self._last_log_read = -100.0

    def _read_backend_logs(self, job_id, force=False):
        if not force and time.monotonic() - self._last_log_read < 5:
            return
        self._last_log_read = time.monotonic()
        try:
            events = self.bridge.get_recent_logs()
            if not isinstance(events, list):
                return
            for event in sorted(events, key=lambda event: str(event.get("created_at", ""))):
                if event.get("job_id") != job_id:
                    continue
                key = str(event.get("id") or (str(event.get("created_at")) + str(event.get("message"))))
                if key not in self._seen_events:
                    self._seen_events.add(key)
                    self.backend_log_signal.emit(event)
        except Exception:
            # Logs are optional; a log endpoint outage must not submit/finish a job.
            pass

    def stop(self):
        self.is_running = False
        self._stop_event.set()

    def _stopped(self):
        return not self.is_running or self._stop_event.is_set()

    def _wait(self, seconds=1.0):
        return self._stop_event.wait(seconds) or self._stopped()

    @staticmethod
    def _check(response):
        if not isinstance(response, dict) or response.get("error"):
            error = response.get("error") if isinstance(response, dict) else "Phản hồi không hợp lệ"
            if isinstance(error, dict):
                error = error.get("message") or str(error)
            raise RuntimeError(f"Backend local: {error}")
        return response

    def _list_jobs(self):
        jobs, page = [], 1
        while not self._stopped():
            data = self._check(self.bridge.get_jobs(limit=100, page=page))
            items = data.get("items")
            if not isinstance(items, list):
                raise RuntimeError("Backend không trả về danh sách job hợp lệ.")
            jobs.extend(items)
            if not data.get("pagination", {}).get("has_more"):
                return jobs
            page += 1
        return jobs

    def _matches(self, job):
        code = (job.get("customer") or {}).get("invitation_code") or job.get("invitation_code") or (job.get("job_payload") or {}).get("invitation_code")
        return (code == self.invitation_code and job.get("job_name") == self.job_name
                and job.get("target_count") == self.target_accounts)

    def _identify_created_job(self, previous_ids):
        deadline = time.monotonic() + 30
        while not self._stopped():
            try:
                candidates = [job for job in self._list_jobs()
                              if job.get("id") and job["id"] not in previous_ids and self._matches(job)]
            except RuntimeError:
                candidates = []
            if len(candidates) > 1:
                raise RuntimeError("Có nhiều job mới trùng mã/thông số. Đã tạm dừng hàng chờ; hãy kiểm tra bản local.")
            if candidates:
                return candidates[0]
            if time.monotonic() >= deadline:
                raise RuntimeError("Đã gửi lệnh nhưng chưa xác định được ID job. Đã tạm dừng hàng chờ; hãy kiểm tra bản local trước khi tiếp tục, tránh tạo trùng.")
            if self._wait():
                return None
        return None

    def run(self):
        try:
            self._run_job()
        except Exception as error:
            self.error_signal.emit(str(error))

    def _run_job(self):
        if self._stopped():
            return
        self.log_signal.emit(f"[*] Chạy mã '{self.invitation_code}' | Mục tiêu {self.target_points} konpeito...")
        if self.existing_job_id:
            # Recovery only monitors the known ID; it never submits the job again.
            job = {"id": self.existing_job_id, "status": "running", "target_count": self.target_accounts}
        elif self.use_ui_automation:
            # A snapshot excludes old completed jobs, including earlier runs of this code.
            previous_ids = {job["id"] for job in self._list_jobs() if job.get("id")}
            if self._stopped():
                return
            self.automator.enter_job_and_run(self.invitation_code, self.target_points, self.job_name, self.unit_price)
            job = self._identify_created_job(previous_ids)
        else:
            response = self._check(self.bridge.create_job(
                invitation_code=self.invitation_code, target_count=self.target_accounts,
                job_name=self.job_name, unit_price=self.unit_price,
            ))
            job = response.get("job")
            if not isinstance(job, dict) or not job.get("id"):
                raise RuntimeError("Đã gửi yêu cầu nhưng backend chưa trả ID job. Hãy kiểm tra bản local trước khi tiếp tục.")
        if self._stopped() or not job:
            return
        job_id = job["id"]
        self.identified_signal.emit(str(job_id))
        self.log_signal.emit(f"[✔] Theo dõi job {job_id} — mã '{self.invitation_code}'.")
        last_progress = None
        connection_lost = False
        while not self._stopped():
            current = int(job.get("success_count") or 0)
            total = int(job.get("target_count") or self.target_accounts)
            points = int(job.get("achieved_points") or current * 200)
            status = job.get("status", "running")
            self.progress_signal.emit(current, total, points, status)
            progress = (current, total, status)
            if progress != last_progress:
                self.log_signal.emit(f"[+] {self.invitation_code}: {current}/{total} account (+{points} konpeito) — {status}")
                last_progress = progress
            if status in TERMINAL_STATUSES:
                self._read_backend_logs(job_id, force=True)
                self.finished_signal.emit(job)
                return
            self._read_backend_logs(job_id)
            if self._wait():
                return
            while not self._stopped():
                try:
                    response = self._check(self.bridge.get_job(job_id))
                    updated = response.get("job")
                    if not isinstance(updated, dict) or updated.get("id") != job_id:
                        raise RuntimeError("Chưa đọc được đúng ID job.")
                    job = updated
                    if connection_lost:
                        self.log_signal.emit("[✔] Đã kết nối lại; tiếp tục theo dõi đúng job đang chạy.")
                    connection_lost = False
                    break
                except Exception as error:
                    if not connection_lost:
                        self.log_signal.emit(f"[!] {error}. Giữ hàng chờ, chờ kết nối lại; không gửi mã tiếp theo.")
                    connection_lost = True
                    if self._wait(2.0):
                        return
