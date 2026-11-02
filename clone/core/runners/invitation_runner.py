"""
MiraiMind Invitation Job Runner (core.runners.invitation_runner).

Reconstructed from core.runners.invitation_job_runner in MiraimindTool.exe.
Executes the full automated lifecycle:
1. Android device stealth initialization.
2. Visitor bootstrap loginGuest (P-384 mdata).
3. Hardware MMUID v1 registration (Command 101/107) & v3 supplement.
4. HData hardware report upload.
5. Disposable email generation & OTP reception.
6. Email account verification (loginWeb).
7. Referral invitation submission (+200 reward points).
8. Balance inspection and account persistence.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Dict, List, Optional

from ..api.fetcher import (
    MiraimindFetcher,
    MiraimindResponseError,
    InvalidInvitationCodeError,
)
from ..crypto.device_profile import DeviceProfile
from ..mail.mail_providers import BaseMailProvider, get_mail_provider
from ..storage import StorageManager

logger = logging.getLogger("InvitationRunner")


@dataclass
class AccountResult:
    email: str
    password: str
    invitation_code: str
    points: int
    status: str
    session_id: str
    mmuid: str
    created_at: str
    note: str = ""


class InvitationTaskWorker:
    """Handles execution for a single invitation account registration."""

    def __init__(
        self,
        invitation_code: str,
        mail_provider_name: str = "tempmail_lol",
        proxy: Optional[str] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.invitation_code = invitation_code.strip()
        self.mail_provider = get_mail_provider(mail_provider_name, proxy=proxy)
        self.proxy = proxy
        self.log_callback = log_callback or (lambda msg: None)

    def _log(self, msg: str) -> None:
        logger.info(msg)
        self.log_callback(msg)

    def run(self) -> AccountResult:
        """Executes the complete end-to-end task."""
        self._log(f"[*] Khởi tạo thiết bị Android ảo & cấu hình bảo mật...")
        profile = DeviceProfile()
        fetcher = MiraimindFetcher(device_profile=profile, proxy=self.proxy)

        # 1. Cold-start pre-guest telemetry
        self._log(f"[*] Chạy các gói tin cold-start telemetry & appConfig...")
        fetcher.run_captured_pre_guest_requests()

        # 2. Guest Login
        self._log(f"[*] Thực hiện loginGuest với mã hóa P-384 mdata...")
        guest_info = fetcher.login_guest()
        self._log(f"[+] loginGuest thành công! SESSIONID: {fetcher.session_id[:16]}...")

        # 3. MMUID Registration
        self._log(f"[*] Ký & đăng ký phần cứng MMUID v1 (Command 101/107)...")
        mmuid_v1 = fetcher.fetch_mmuid_supplement()
        self._log(f"[+] MMUID v1 xác thực: {mmuid_v1}")

        self._log(f"[*] Đăng ký MMUID v3 supplement...")
        fetcher.register_mmuid_v3_supplement()

        self._log(f"[*] Gửi báo cáo định danh HData hardware...")
        fetcher.upload_hdata_identity()

        # 4. Email creation
        self._log(f"[*] Tạo hộp thư tạm thời qua {type(self.mail_provider).__name__}...")
        email, mail_token = self.mail_provider.create_mailbox()
        self._log(f"[+] Đã tạo email: {email}")

        # 5. Request verification code
        self._log(f"[*] Gửi yêu cầu mã xác nhận (sendEmailVerifyCode) tới {email}...")
        fetcher.send_email_verify_code(email)

        # 6. Wait for OTP
        self._log(f"[*] Đang lắng nghe OTP trong hộp thư đến...")
        otp = self.mail_provider.wait_for_otp(email, mail_token, timeout_seconds=60)
        if not otp:
            raise RuntimeError(f"Hết thời gian chờ mã OTP gửi tới {email}")
        self._log(f"[+] Nhận mã OTP thành công: {otp}")

        # 7. Confirm Email Code
        self._log(f"[*] Đăng nhập & liên kết email qua loginWeb...")
        login_res = fetcher.confirm_email_code(email, otp)
        self._log(f"[+] Đăng nhập tài khoản hoàn tất!")

        # 8. Submit Invitation Code
        self._log(f"[*] Nhập mã mời {self.invitation_code}...")
        invite_res = fetcher.submit_invitation_code(self.invitation_code)
        self._log(f"[+] Nhập mã mời thành công! Nhận được +200 điểm thưởng.")

        # 9. Query Balance
        balance_info = fetcher.get_user_balance()
        points = balance_info.get("data", {}).get("points", 200)

        result = AccountResult(
            email=email,
            password="Generated_Pass123!",
            invitation_code=self.invitation_code,
            points=points,
            status="Sẵn sàng",
            session_id=fetcher.session_id or "",
            mmuid=fetcher.mmuid or "",
            created_at=datetime.now(timezone.utc).isoformat(),
            note="Đã nạp mã mời thành công",
        )
        return result


class InvitationJobManager:
    """Manages batches of invitation tasks with concurrency and persistence."""

    def __init__(
        self,
        invitation_code: str,
        target_count: int = 1,
        mail_provider: str = "tempmail_lol",
        proxy: Optional[str] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.invitation_code = invitation_code
        self.target_count = target_count
        self.mail_provider = mail_provider
        self.proxy = proxy
        self.log_callback = log_callback or (lambda msg: None)

        self.success_count = 0
        self.failed_count = 0
        self.accounts: List[AccountResult] = []
        self.is_running = False
        self.storage = StorageManager()

    def start(self) -> List[AccountResult]:
        """Runs the job until target_count is reached."""
        self.is_running = True
        self.log_callback(f"[JOB] Bắt đầu job nạp khách: Mã={self.invitation_code}, Mục tiêu={self.target_count}")

        for i in range(self.target_count):
            if not self.is_running:
                self.log_callback("[JOB] Job đã bị dừng bởi người dùng.")
                break

            self.log_callback(f"\n--- Tiến độ: {i + 1}/{self.target_count} ---")
            try:
                worker = InvitationTaskWorker(
                    invitation_code=self.invitation_code,
                    mail_provider_name=self.mail_provider,
                    proxy=self.proxy,
                    log_callback=self.log_callback,
                )
                acc = worker.run()
                self.accounts.append(acc)
                self.storage.add_account(asdict(acc))
                self.success_count += 1
                self.log_callback(f"[JOB] Hoàn thành tài khoản {acc.email}! (+{acc.points} điểm)")
            except InvalidInvitationCodeError as e:
                self.log_callback(f"[ERROR] Mã mời không hợp lệ hoặc đã hết hạn: {e}")
                self.failed_count += 1
                break
            except Exception as e:
                self.log_callback(f"[ERROR] Lỗi xử lý tài khoản: {e}")
                self.failed_count += 1

        self.is_running = False
        self.log_callback(
            f"[JOB] Kết thúc job! Thành công: {self.success_count}/{self.target_count}, Thất bại: {self.failed_count}"
        )
        return self.accounts

    def stop(self) -> None:
        self.is_running = False
