#!/usr/bin/env python3
"""
MiraiMind Local Control & Automation Tool (control.main).

Full Python Source Entry Point for:
1. Direct UI automation on the local application window (MiraiMind / TTM-Tool by Hermit Coder).
2. Live real-time progress tracking and log streaming.
3. Desktop GUI Control Panel or Command-Line Interface.

Usage:
  python main.py                                      # Launch Desktop Control GUI
  python main.py --code npxMgy --points 2000          # Automate local UI to enter job & track
  python main.py --code npxMgy --api-only             # Inject via backend API & track
  python main.py --track                              # Live track whatever job is running
  python main.py --status                             # Check window and backend connectivity
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import warnings

# Suppress harmless 3rd-party warnings on Python 3.15 & Qt DPI messages
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="uiautomation")
warnings.filterwarnings("ignore", module="comtypes")
os.environ["QT_LOGGING_RULES"] = "qt.qpa.*=false;qt.qpa.window=false"

# Ensure control folder is in sys.path and remove workspace root & empty strings
THIS_DIR = Path(__file__).resolve().parent
parent_dir = THIS_DIR.parent
cleaned_path = [str(THIS_DIR)]
for p in sys.path:
    if not p or p == ".":
        continue
    try:
        if Path(p).resolve() == parent_dir:
            continue
    except Exception:
        pass
    if p != str(THIS_DIR) and p not in cleaned_path:
        cleaned_path.append(p)
sys.path = cleaned_path

from api_bridge import LocalBackendBridge
from gui import launch_gui
from tracker import JobProgressTracker
from ui_automator import LocalAppAutomator


def print_status(automator: LocalAppAutomator, bridge: LocalBackendBridge) -> None:
    print("=" * 70)
    print("          MIRAIMIND LOCAL CONTROLLER - KIỂM TRA KẾT NỐI")
    print("=" * 70)

    # 1. Check local tool window
    hwnd = automator.find_local_window()
    if hwnd:
        print(f"[*] Cửa sổ bản local: 🟢 ĐANG MỞ -> '{automator.window_title}' (HWND: {hwnd})")
    else:
        print("[!] Cửa sổ bản local: 🔴 CHƯA MỞ (Hãy chạy 'MO_MIRAIMIND_LOCAL.cmd')")

    # 2. Check backend
    is_port = bridge.is_port_listening()
    print(f"[*] Cổng Backend (127.0.0.1:18888): {'🟢 ĐANG LẮNG NGHE' if is_port else '🔴 CHƯA MỞ'}")

    if is_port:
        health = bridge.check_health()
        print(f"[+] Ứng dụng: {health.get('application')} (v{health.get('version')}) | DB: {health.get('database')}")
        jobs_res = bridge.get_jobs(limit=5)
        total_jobs = jobs_res.get("pagination", {}).get("total", 0)
        print(f"[+] Tổng số jobs trong hệ thống: {total_jobs}")

    # 3. Read active jobs from UI table
    if hwnd:
        try:
            ui_jobs = automator.read_active_jobs_from_ui()
            print(f"[+] Đọc từ bảng 'Job đang chạy' trên giao diện: {len(ui_jobs)} job")
            for i, j in enumerate(ui_jobs[:3]):
                print(f"   [{i+1}] {j.get('time')} | Mã: {j.get('code')} | Tiến độ: {j.get('progress')} | Trạng thái: {j.get('status')}")
        except Exception as e:
            print(f"[-] Không thể đọc bảng UI: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MiraiMind Local Control & Progress Tracker")
    parser.add_argument("--code", type=str, default=None, help="Mã mời để nạp (ví dụ npxMgy)")
    parser.add_argument("--points", type=int, default=2000, help="Điểm mục tiêu (mặc định 2000 = 10 account)")
    parser.add_argument("--name", type=str, default=None, help="Tên job / khách hàng")
    parser.add_argument("--api-only", action="store_true", help="Bơm job qua backend API thay vì click giao diện")
    parser.add_argument("--track", action="store_true", help="Chỉ theo dõi tiến độ job đang chạy")
    parser.add_argument("--status", action="store_true", help="Kiểm tra trạng thái kết nối cửa sổ và backend")
    parser.add_argument("--cli", action="store_true", help="Chạy chế độ dòng lệnh thay vì mở giao diện GUI")

    args = parser.parse_args()

    automator = LocalAppAutomator()
    bridge = LocalBackendBridge()
    tracker = JobProgressTracker(automator=automator)

    if args.status:
        print_status(automator, bridge)
        return

    if args.track:
        tracker.track_job()
        return

    if args.code:
        code = args.code.strip()
        pts = args.points
        accs = max(1, pts // 200)
        name = args.name or f"Nạp mã {code}"

        print("=" * 70)
        print(f"[*] THỰC HIỆN JOB: Mã '{code}' | Mục tiêu: {pts} điểm ({accs} account)")
        print("=" * 70)

        if not args.api_only:
            print("[*] Chế độ: Thao tác trực tiếp trên giao diện của bản local...")
            res = automator.enter_job_and_run(
                invitation_code=code,
                target_points=pts,
                job_name=name,
            )
            print(f"[✔] Đã kích hoạt job trên giao diện bản local!")
        else:
            print("[*] Chế độ: Nạp job trực tiếp qua Backend Local (127.0.0.1:18888)...")
            res = bridge.create_job(
                invitation_code=code,
                target_count=accs,
                job_name=name,
            )
            print(f"[✔] Đã đăng ký job vào backend: {res.get('job', {}).get('id') or res.get('id')}")

        # Start live tracker
        tracker.track_job()
        return

    if args.cli:
        print("[*] Chế độ CLI yêu cầu tham số --code. Dùng '--help' để xem hướng dẫn.")
        return

    # Default action: Launch Desktop Control GUI
    launch_gui()


if __name__ == "__main__":
    main()
