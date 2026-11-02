"""Linux executor. Accept only explicit service responses; never manufacture sessions."""
from core.api.fetcher import MiraimindFetcher, InvalidInvitationCodeError, MiraimindResponseError
from core.crypto.device_profile import DeviceProfile
from core.mail.mail_providers import get_mail_provider


class ProtocolError(RuntimeError):
    pass


def readiness():
    from core.crypto.mmuid_codec import MMUID_V3_AES_KEY, MMUID_V3_AES_IV
    if len(MMUID_V3_AES_KEY) not in (16, 24, 32) or len(MMUID_V3_AES_IV) != 16:
        return False, "Mã clone có khóa/IV MMUID v3 không hợp lệ. Cần bộ thực thi Linux hoàn chỉnh trước khi chạy job thật."
    return False, "Bộ thực thi clone chưa được xác minh tương đương bản Windows bằng job thật."


def require_success(response):
    if not isinstance(response, dict):
        raise ProtocolError("API không trả JSON object.")
    code = response.get("ec", response.get("code"))
    if code is None or str(code) not in ("0", "200"):
        # Do not expose response bodies, session IDs, email or tokens in management logs.
        raise ProtocolError("API chưa xác nhận thành công; không ghi nhận konpeito.")
    return response


class StrictFetcher(MiraimindFetcher):
    def _post(self, url, **kwargs):
        response = super()._post(url, **kwargs)
        if not isinstance(response, dict) or "raw_text" in response:
            raise ProtocolError("API trả nội dung không hợp lệ.")
        if ("ec" in response or "code" in response) and str(response.get("ec", response.get("code"))) not in ("0", "200"):
            # Classification is handled by the caller; do not keep private server messages.
            message = str(response.get("em", response.get("msg", ""))).lower()
            if "invite" in message and any(word in message for word in ("24", "limit")):
                raise InviteLimitError("Đạt giới hạn mã mời; chưa gửi thêm tài khoản.")
            if "invalid" in message and "invit" in message:
                raise InvalidInvitationCodeError(400, "Mã mời không hợp lệ.")
            raise ProtocolError("Dịch vụ từ chối yêu cầu.")
        return response

    def login_guest(self):
        data = super().login_guest()
        # The legacy clone invents G_ identifiers on missing fields. Reject that result.
        if not isinstance(data, dict) or not (data.get("session_id") and data.get("chat_user_id")):
            raise ProtocolError("Chưa lấy được phiên thật từ dịch vụ; dừng trước khi tạo mail.")
        return data

    def submit_invitation_code(self, code):
        return require_success(super().submit_invitation_code(code))


class InviteLimitError(RuntimeError):
    pass


def execute_account(code, provider):
    ready, reason = readiness()
    if not ready:
        raise ProtocolError(reason)
    if provider not in ("mail_tm", "tempmail_lol"):
        raise ValueError("Bộ thực thi Linux hiện hỗ trợ mail_tm và tempmail_lol.")
    fetcher = StrictFetcher(device_profile=DeviceProfile())
    mail = get_mail_provider(provider)
    try:
        fetcher.run_captured_pre_guest_requests()
        fetcher.login_guest()
        fetcher.fetch_mmuid_supplement()
        fetcher.register_mmuid_v3_supplement()
        fetcher.upload_hdata_identity()
        email, token = mail.create_mailbox()
        if not email or not token:
            raise ProtocolError("Không tạo được hộp thư.")
        require_success(fetcher.send_email_verify_code(email))
        otp = mail.wait_for_otp(email, token, timeout_seconds=60)
        if not otp:
            raise ProtocolError("Hết thời gian nhận OTP.")
        require_success(fetcher.confirm_email_code(email, otp))
        fetcher.submit_invitation_code(code)
        # Confirmed invitation reward from this protocol, not the account's total balance.
        return 200
    finally:
        fetcher.session.close()
        if mail.session:
            mail.session.close()
