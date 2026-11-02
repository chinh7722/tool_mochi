"""History formatting and atomic, Excel-compatible CSV snapshots."""
import csv
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile

VN_TIME = timezone(timedelta(hours=7))
HISTORY_STATUSES = ("completed", "failed", "cancelled", "invalid_code", "invite_limit_reached", "error", "interrupted")


def vn_time(value, short=False):
    if not value:
        return "—"
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp.astimezone(VN_TIME).strftime("%H:%M:%S" if short else "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return "—"


def status_text(status, error=""):
    if status == "invite_limit_reached" or "invite_limit_reached" in (error or "").lower():
        return "Đạt giới hạn 24h"
    return {"queued": "Đang chờ", "submitting": "Đang tạo job", "running": "Đang chạy", "completed": "Hoàn thành",
            "failed": "Thất bại", "cancelled": "Đã hủy", "invalid_code": "Mã không hợp lệ",
            "error": "Cần kiểm tra", "interrupted": "Bị gián đoạn"}.get(status, status)


def creator_text(row):
    if row["chat_id"] == "LOCAL":
        return "Local (máy tính)"
    return f"{row.get('creator_name') or row['chat_id']} ({row['chat_id']})"


def csv_cell(value):
    text = str(value if value is not None else "")
    # A customer name/code is text, never an executable spreadsheet formula.
    if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r")):
        return "'" + text
    return text


def export_history(store, destination=None):
    destination = Path(destination or store.path.parent / "job_history.csv")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=destination.parent,
                                         prefix=".job_history_", suffix=".tmp", delete=False) as stream:
            temp_path = Path(stream.name)
            writer = csv.writer(stream)
            writer.writerow(["Kết thúc (VN)", "Người tạo job", "Chat ID", "Khách hàng / Job", "Mã mời", "Kết quả",
                             "Thành công", "Tổng account", "Konpeito thực tế", "Konpeito mục tiêu", "Mã job",
                             "Mã đơn", "ID bản ghi", "Tổng tiền (VND)", "Doanh thu (VND)", "Lỗi"])
            for row in store.history(limit=None):
                writer.writerow([csv_cell(value) for value in (
                    vn_time(row["completed_at"] or row["updated_at"]), creator_text(row), row["chat_id"], row["name"], row["code"],
                    status_text(row["status"], row["error"]), row["success_count"], row["target_count"], row["achieved"], row["points"],
                    row["backend_id"] or "", row["external_key"], row["id"], row["money"], row["revenue"], row["error"],
                )])
        os.replace(temp_path, destination)
        return destination
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()
