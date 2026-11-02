"""
Real-time Job Progress & Log Tracker (control.tracker).

Monitors job execution across both the Native Desktop GUI and the Local Backend API:
- Live polling of active table rows from the local application window.
- Real-time SSE streaming from http://127.0.0.1:18888/api/logs/stream.
- Visual CLI progress bar with colored status indicators and terminal updates.
"""
from __future__ import annotations

import json
import logging
import math
import sys
import time
from typing import Any, Callable, Dict, List, Optional
import urllib.error
import urllib.request
from backend_config import local_backend_key

logger = logging.getLogger("ProgressTracker")


class JobProgressTracker:
    """Monitors live job progress and log stream."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:18888",
        api_key: Optional[str] = None,
        automator: Optional[Any] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = local_backend_key() if api_key is None else api_key
        self.automator = automator
        self.log_callback = log_callback or (lambda msg: print(msg))
        self.is_tracking = False

    def _get_json(self, path: str) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    def get_latest_job(self) -> Optional[Dict[str, Any]]:
        """Retrieves the most recent running or created job from backend or UI."""
        data = self._get_json("/api/jobs?limit=5")
        if data and data.get("items"):
            # Return first running or most recent job
            items = data["items"]
            running = [j for j in items if j.get("status") == "running"]
            return running[0] if running else items[0]

        # Fallback to UI table
        if self.automator:
            try:
                ui_jobs = self.automator.read_active_jobs_from_ui()
                if ui_jobs:
                    first = ui_jobs[0]
                    # Parse progress "4 / 15"
                    prog_parts = first.get("progress", "0/1").split("/")
                    cur = int(prog_parts[0].strip()) if len(prog_parts) > 0 and prog_parts[0].strip().isdigit() else 0
                    tot = int(prog_parts[1].strip()) if len(prog_parts) > 1 and prog_parts[1].strip().isdigit() else 1
                    return {
                        "id": f"ui_{first.get('code')}",
                        "status": first.get("status", "running"),
                        "job_name": first.get("job_name", ""),
                        "invitation_code": first.get("code", ""),
                        "success_count": cur,
                        "target_count": tot,
                        "achieved_points": cur * 200,
                        "from_ui": True,
                    }
            except Exception:
                pass
        return None

    @staticmethod
    def render_progress_bar(current: int, total: int, width: int = 30) -> str:
        """Renders an ASCII progress bar."""
        if total <= 0:
            total = 1
        pct = min(1.0, max(0.0, current / total))
        filled = int(width * pct)
        bar = "█" * filled + "░" * (width - filled)
        return f"[{bar}] {int(pct * 100)}%"

    def track_job(
        self,
        job_id: Optional[str] = None,
        poll_interval: float = 1.0,
        timeout_seconds: int = 600,
        stop_on_terminal: bool = True,
    ) -> None:
        """
        Continuously monitors progress in the terminal with live visual updates.
        """
        self.is_tracking = True
        start_time = time.time()
        last_success = -1
        last_status = ""

        print("\n" + "=" * 70)
        print("          MIRAIMIND - THEO DÕI TIẾN ĐỘ JOB LOCAL THEO THỜI GIAN THỰC")
        print("=" * 70)

        try:
            while self.is_tracking:
                elapsed = int(time.time() - start_time)
                if elapsed > timeout_seconds:
                    print(f"\n[!] Hết thời gian theo dõi ({timeout_seconds}s).")
                    break

                job = None
                if job_id:
                    res = self._get_json(f"/api/jobs/{job_id}")
                    if res:
                        job = res.get("job")
                if not job:
                    job = self.get_latest_job()

                if not job:
                    print(f"\r[*] ({elapsed}s) Đang chờ phát hiện job trên bản local...", end="", flush=True)
                    time.sleep(poll_interval)
                    continue

                jid = job.get("id", "N/A")
                status = job.get("status", "running")
                success = job.get("success_count", 0)
                target = job.get("target_count", 1)
                points = job.get("achieved_points", success * 200)
                code = (job.get("customer") or {}).get("invitation_code") or job.get("invitation_code", "")

                bar = self.render_progress_bar(success, target, width=24)
                status_icon = "🟢" if status == "completed" else ("🟡" if status == "running" else "🔴")

                # Print update line
                sys.stdout.write(
                    f"\r{status_icon} [{status.upper()}] Mã: {code} | {bar} | {success}/{target} acc (+{points} pts) | {elapsed}s  "
                )
                sys.stdout.flush()

                # Notify on milestone changes
                if success != last_success or status != last_status:
                    if success > (last_success if last_success >= 0 else 0):
                        print(f"\n  [✔] Thành công thêm 1 account (+200 điểm) | Tổng điểm: {points}")
                    last_success = success
                    last_status = status

                if stop_on_terminal and status in ("completed", "failed", "cancelled", "invalid_code"):
                    print(f"\n\n[★] JOB KẾT THÚC: Trạng thái: {status.upper()} | Đạt: {success}/{target} account (+{points} điểm)")
                    break

                time.sleep(poll_interval)

        except KeyboardInterrupt:
            print("\n[!] Đã dừng theo dõi theo yêu cầu người dùng.")
        finally:
            self.is_tracking = False
