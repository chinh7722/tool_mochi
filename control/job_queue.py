"""FIFO job settings, owned exclusively by the controller GUI thread."""
from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Optional


def parse_codes(text: str) -> list[str]:
    codes = [code for code in re.split(r"[\s,;]+", text.strip()) if code]
    if not codes:
        raise ValueError("Vui lòng nhập ít nhất một mã mời.")
    if any(not code.isascii() or not code.isalnum() for code in codes):
        raise ValueError("Mã mời chỉ gồm chữ và số. Dán mỗi mã một dòng hoặc cách nhau bằng dấu phẩy/chấm phẩy.")
    return codes


@dataclass
class QueuedJob:
    number: int
    invitation_code: str
    target_points: int
    job_name: str
    unit_price: int
    use_ui_automation: bool
    status: str = "queued"
    progress: str = ""
    backend_id: Optional[str] = None
    record_id: Optional[str] = None
    chat_id: str = "LOCAL"
    bot_id: str = ""
    resume_job_id: Optional[str] = None
    scheduled_at: Optional[str] = None

    def ready(self):
        if self.resume_job_id or not self.scheduled_at:
            return True
        return datetime.fromisoformat(self.scheduled_at.replace("Z", "+00:00")) <= datetime.now(timezone.utc)


class JobQueue:
    def __init__(self):
        self.items: list[QueuedJob] = []
        self._next_number = 1

    def add(self, codes: list[str], points: int, name: str, money: int, use_ui: bool, scheduled_at=None):
        if points <= 0 or money < 0:
            raise ValueError("Konpeito mục tiêu phải lớn hơn 0, tổng tiền không được âm.")
        existing = {item.invitation_code for item in self.items if item.status in ("queued", "running")}
        added, skipped = [], []
        for code in codes:
            if code in existing:
                skipped.append(code)
                continue
            item = QueuedJob(self._next_number, code, points, name.strip() or f"Nạp mã {code}", money, use_ui)
            item.scheduled_at = scheduled_at
            self._next_number += 1
            self.items.append(item)
            added.append(item)
            existing.add(code)
        return added, skipped

    def take_next(self) -> Optional[QueuedJob]:
        if any(item.status == "running" for item in self.items):
            return None
        for item in self.items:
            if item.status == "queued" and item.ready():
                item.status = "running"
                return item
        return None

    def restore(self, item: QueuedJob):
        item.number = self._next_number
        self._next_number += 1
        item.status = "queued"
        self.items.append(item)
        return item

    def remove_waiting(self, numbers: set[int]) -> int:
        before = len(self.items)
        self.items = [item for item in self.items if item.status != "queued" or item.number not in numbers]
        return before - len(self.items)
