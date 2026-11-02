"""Command-line interface for Mochi Chat Local Tool.

Usage example:
    py -3.14 -I cli_mochi.py --code A75FNX --points 1000 --mail-provider gmailvip_vn --api-key YOUR_KEY --concurrency 5
"""

import argparse
import os
import sys
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
while PROJECT_ROOT in sys.path:
    sys.path.remove(PROJECT_ROOT)
while "" in sys.path:
    sys.path.remove("")
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import asyncio
from core.config.constants import required_mochi_invitation_accounts
from core.job_events import JobLogEvent, JobLogLevel, JobProgressEvent
from core.platforms.mochichat.runners.invite_referral import MochiInviteRunner
from core.browser import browser_manager
from core.config.paths import data_directory
from core.platforms.mochichat.invite_quota import InviteQuotaStore


def main():
    parser = argparse.ArgumentParser(description="Mochi Chat Automation Tool (Local Edition)")
    parser.add_argument("--code", "-c", required=True, help="Mã mời Mochi Chat (vd: A75FNX)")
    parser.add_argument("--points", "-p", type=int, default=1000, help="Điểm mục tiêu cần nạp (mỗi 100 điểm = 1 tài khoản, mặc định 1000)")
    parser.add_argument("--mail-provider", choices=["gmailvip_vn", "1trieumail_com"], default="gmailvip_vn", help="Nhà cung cấp tài khoản Gmail")
    parser.add_argument("--api-key", "-k", required=True, help="API Key shop mua mail")
    parser.add_argument("--product-ids", default=None, help="Danh sách Product IDs phân cách bởi dấu phẩy")
    parser.add_argument("--concurrency", type=int, default=5, help="Số luồng tài khoản đồng thời (mặc định 5)")
    parser.add_argument("--no-headless", action="store_true", help="Hiện cửa sổ browser khi đăng nhập Google (mặc định chạy ẩn)")

    args = parser.parse_args()
    if args.points <= 0 or args.concurrency <= 0:
        parser.error('Điểm mục tiêu và số luồng phải lớn hơn 0.')
    if not args.code.strip() or not args.api_key.strip():
        parser.error('Mã mời và API key không được để trống.')

    invite_code = args.code.strip().upper()
    target_count = required_mochi_invitation_accounts(args.points)
    print(f"[*] Mã mời: {invite_code}")
    print(f"[*] Mục tiêu: {args.points} điểm (= {target_count} tài khoản)")
    print(f"[*] Shop mail: {args.mail_provider}")
    print(f"[*] Concurrency: {args.concurrency}")
    print(f"[*] Headless: {not args.no_headless}")
    print("-" * 50)

    def log_callback(evt: JobLogEvent):
        prefix = f"[{evt.level.value}]"
        if evt.account:
            prefix += f" [{evt.account}]"
        print(f"{prefix} {evt.message}")

    def progress_callback(evt: JobProgressEvent):
        pct = (evt.success_count / target_count) * 100 if target_count else 0
        print(f"--> [Tiến độ: {evt.success_count}/{target_count} ({pct:.1f}%)] Đã thử: {evt.attempted_count}, Lỗi: {evt.failure_count}")

    runner = MochiInviteRunner(
        concurrency=args.concurrency,
        log_sink=log_callback,
        progress_sink=progress_callback,
        mail_provider=args.mail_provider,
        mail_provider_api_key=args.api_key,
        mail_product_ids=args.product_ids,
        browser_headless=not args.no_headless,
        invite_quota_store=InviteQuotaStore(data_directory()/'invite_quota.sqlite3'),
    )

    async def execute():
        try:
            return await runner.run(
            job_id='cli_'+uuid.uuid4().hex,
            invite_code=invite_code,
            target_count=target_count,
            )
        finally:
            await browser_manager.shutdown_for_loop()

    summary = asyncio.run(execute())

    print("=" * 50)
    print(f"[!] KẾT QUẢ CUỐI CÙNG: {summary.status.upper()}")
    print(f"    - Thành công: {summary.success_count}/{summary.target_count} (+{summary.success_count * 100} kẹo)")
    print(f"    - Trùng lặp: {summary.already_redeemed_count}")
    print(f"    - Thất bại: {summary.failure_count}")
    if summary.error_message:
        print(f"    - Chi tiết: {summary.error_message}")


if __name__ == "__main__":
    main()
