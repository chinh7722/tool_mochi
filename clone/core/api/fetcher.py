"""
MiraiMind Official API Fetcher (core.api.fetcher).

Full Python implementation of the native MiraimindFetcher disassembled from MiraimindTool.exe.
Handles session cookies, P-384 mdata encryption, MMUID registration, email authentication,
invitation submission, points inspection, and bot interactions.
"""
from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any, Dict, Optional, Tuple

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    from curl_cffi import requests as cffi_requests
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False

from ..crypto.device_profile import DeviceProfile
from ..crypto.mdata_emulator import (
    build_mdata,
    derive_mdata_key,
    generate_p384_keypair,
    native_h,
    native_m,
)
from ..crypto.mmuid_codec import (
    decode_mmuid_107,
    decode_mmuid_v3_103,
    sign_mmuid_101,
    sign_mmuid_v3_102,
)
from .endpoints import (
    AGE_RANGE_TIPS_URL,
    APP_CONFIG_URL,
    BIND_PUSH_TOKEN_URL,
    CHAT_PAGE_EXPOSED_URL,
    CUSTOMER_INVITATION_REWARD_POINTS,
    FEED_LIKE_URL,
    FOLLOW_ONE_URL,
    GUEST_REGISTER_AWARD_URL,
    HDATA_UPLOAD_URL,
    INVITATION_SUBMIT_URL,
    LOGIN_GUEST_URL,
    LOGIN_WEB_URL,
    MMUID_SUPPLEMENT_V1_URL,
    MMUID_SUPPLEMENT_V3_URL,
    PUBLISHED_FEED_LIST_URL,
    QUICK_REPLY_URL,
    SEND_EMAIL_VERIFY_CODE_URL,
    SESSION_GET_BOT_INFO_URL,
    SESSION_LIST_URL,
    SETTING_CONFIG_URL,
    TELEMETRY_ELEMENTS_URL,
    TELEMETRY_POINT_REPORT_URL,
    USER_BALANCE_URL,
    USER_INFO_URL,
)

logger = logging.getLogger("MiraimindFetcher")


class MiraimindResponseError(Exception):
    """Raised when the MiraiMind API responds with an error code."""
    def __init__(self, ec: int, em: str, raw_response: Any = None):
        self.ec = ec
        self.em = em
        self.raw_response = raw_response
        super().__init__(f"MiraiMind Error {ec}: {em}")


class InvalidInvitationCodeError(MiraimindResponseError):
    """Raised when an invitation code is rejected by the server."""


class MiraimindFetcher:
    """
    High-fidelity API client replicating the internal MiraimindFetcher pipeline.
    """

    def __init__(
        self,
        device_profile: Optional[DeviceProfile] = None,
        proxy: Optional[str] = None,
        timeout: int = 15,
        use_curl_cffi: bool = False,
    ):
        self.profile = device_profile or DeviceProfile()
        self.proxy = proxy
        self.timeout = timeout
        self.use_curl_cffi = use_curl_cffi and _HAS_CURL_CFFI

        if self.use_curl_cffi:
            self.session = cffi_requests.Session(impersonate="chrome120")
        elif _HAS_REQUESTS:
            self.session = requests.Session()
        else:
            raise RuntimeError("Either requests or curl_cffi must be installed.")

        if self.proxy:
            self.set_proxy(self.proxy)

        # Authentication state
        self.session_id: Optional[str] = None
        self.chat_user_id: Optional[str] = None
        self.guest_token: Optional[str] = None
        self.access_token: Optional[str] = None
        self.mmuid: Optional[str] = None
        self.session_crypto_key: Optional[str] = None

        self._mmuid_v1_registered = False
        self._mmuid_v3_registered = False

    def set_proxy(self, proxy: str) -> None:
        """Configures HTTP/HTTPS/SOCKS5 proxy."""
        self.proxy = proxy
        proxies = {"http": proxy, "https": proxy}
        self.session.proxies.update(proxies)

    def _build_headers(self, custom: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "User-Agent": self.profile.get_user_agent(),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "X-App-Version": self.profile.app_version,
            "X-Version-Code": self.profile.app_version_code,
            "X-Platform": "android",
        }
        if self.session_id:
            headers["SESSIONID"] = self.session_id
            headers["Cookie"] = f"SESSIONID={self.session_id}"
        if self.mmuid:
            headers["X-Mmuid"] = self.mmuid
        if self.chat_user_id:
            headers["X-Chat-User-Id"] = self.chat_user_id
        if custom:
            headers.update(custom)
        return headers

    def _post(self, url: str, data: Any = None, json_data: Any = None, headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        req_headers = self._build_headers(headers)
        resp = self.session.post(url, data=data, json=json_data, headers=req_headers, params=params, timeout=self.timeout)
        try:
            return resp.json()
        except Exception:
            return {"raw_text": resp.text, "status_code": resp.status_code}

    def _get(self, url: str, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        req_headers = self._build_headers(headers)
        resp = self.session.get(url, params=params, headers=req_headers, timeout=self.timeout)
        try:
            return resp.json()
        except Exception:
            return {"raw_text": resp.text, "status_code": resp.status_code}

    # ─── 1. PRE-GUEST TELEMETRY & STEALTH BOOTSTRAP ──────────────────────────────
    def run_captured_pre_guest_requests(self) -> None:
        """Replays cold-start pre-guest API requests observed in native capture."""
        try:
            self._post(TELEMETRY_ELEMENTS_URL, data={"mmuid": ""})
            self._get(APP_CONFIG_URL)
            self._get(SETTING_CONFIG_URL)
            self._get(AGE_RANGE_TIPS_URL)
        except Exception as e:
            logger.debug(f"Pre-guest request ignored exception: {e}")

    # ─── 2. GUEST LOGIN (loginGuest) ─────────────────────────────────────────────
    def login_guest(self) -> Dict[str, Any]:
        """
        Executes visitor bootstrap loginGuest using P-384 mdata encryption.
        Sets SESSIONID and chatUserId.
        """
        mdata_res = build_mdata(
            region_code="VND",
            chat_user_id="",
            identifier="",
            mdp_login_type=13,
            init_encrypt=1,
            rom=self.profile.rom,
            common_params=self.profile.build_common_params(),
        )
        post_data = {
            "mdata": mdata_res["mdata"],
            "initEncrypt": 1,
            "regionCode": "VND",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = self._post(LOGIN_GUEST_URL, data=post_data, headers=headers)

        # Handle decrypted or encrypted response
        if "data" in resp and isinstance(resp["data"], str):
            try:
                decrypted_json = native_h(resp["data"], mdata_res["response_decryption_key"])
                data_obj = json.loads(decrypted_json)
            except Exception:
                data_obj = resp.get("data", {})
        else:
            data_obj = resp.get("data", resp)

        token = data_obj.get("token") or data_obj.get("guest_token") or ""
        session_id = data_obj.get("session_id") or data_obj.get("chat_user_id") or f"G_{secrets.token_hex(16)}"
        chat_user_id = data_obj.get("chat_user_id") or f"G_{secrets.token_hex(10)}"

        self.guest_token = token
        self.session_id = session_id
        self.chat_user_id = chat_user_id
        self.profile.session_id = session_id
        self.profile.chat_user_id = chat_user_id
        self.profile.guest_token = token
        self.session_crypto_key = mdata_res["response_decryption_key"]

        logger.info(f"loginGuest success: session_id={session_id[:12]}... chatUserId={chat_user_id}")
        return data_obj

    # ─── 3. HARDWARE MMUID REGISTRATION (Command 101 / 107) ──────────────────────
    def fetch_mmuid_supplement(self) -> str:
        """
        Registers hardware MMUID token via native Command 101 / 107.
        POSTs mzip=<value> to MMUID_SUPPLEMENT_V1_URL.
        """
        req_session_id = self.profile.generate_request_session_id()
        fp = self.profile.build_mmuid_fingerprint(req_session_id)
        signed = sign_mmuid_101(fp)

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": f"SESSIONID={req_session_id}",
        }
        body = f"mzip={signed['mzip']}"
        resp = self._post(MMUID_SUPPLEMENT_V1_URL, data=body, headers=headers)

        decoded = decode_mmuid_107(resp.get("raw_text") or json.dumps(resp))
        self.mmuid = decoded["mmuid"]
        self.profile.mmuid = self.mmuid
        self._mmuid_v1_registered = True
        logger.info(f"MMUID v1 registered: {self.mmuid}")
        return self.mmuid

    # ─── 4. MMUID V3 SUPPLEMENT (/v3/log/upload_info/supplement) ─────────────────
    def register_mmuid_v3_supplement(self) -> str:
        """Registers device supplement with v3 log upload."""
        fp_v3 = self.profile.build_mmuid_v3_fingerprint(self.guest_token)
        signed_v3 = sign_mmuid_v3_102(fp_v3)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        body = f"mzip={signed_v3['mzip']}"
        resp = self._post(MMUID_SUPPLEMENT_V3_URL, data=body, headers=headers)
        decoded_v3 = decode_mmuid_v3_103(resp.get("raw_text") or json.dumps(resp))
        token_v3 = decoded_v3.get("data", {}).get("token") or signed_v3["mmuidv3"]
        self.profile.mmuid_v3 = token_v3
        self._mmuid_v3_registered = True
        logger.info(f"MMUID v3 registered: {token_v3[:12]}...")
        return token_v3

    # ─── 5. HARDWARE ATTESTATION UPLOAD (HData) ──────────────────────────────────
    def upload_hdata_identity(self, source: str = "guest") -> Dict[str, Any]:
        """Uploads hardware fingerprint report to /v1/log/upload_info."""
        payload = {
            "source": source,
            "android_id": self.profile.android_id,
            "imei": self.profile.imei,
            "mac": self.profile.mac,
            "mmuid": self.mmuid or "",
            "mmuid_v3": self.profile.mmuid_v3 or "",
            "timestamp": int(time.time()),
        }
        return self._post(HDATA_UPLOAD_URL, json_data=payload)

    # ─── 6. EMAIL VERIFICATION & OTP (sendEmailVerifyCode) ────────────────────────
    def send_email_verify_code(
        self,
        email: str,
        pass_token: Optional[str] = None,
        captcha_id: Optional[str] = None,
        lot_number: Optional[str] = None,
        captcha_output: Optional[str] = None,
        gen_time: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Sends email verification code request to server.
        Includes solved Geetest captcha if required.
        """
        payload: Dict[str, Any] = {
            "email": email,
            "identifier": email,
            "regionCode": "VND",
            "chatUserId": self.chat_user_id or "",
        }
        if pass_token:
            payload["passToken"] = pass_token
        if captcha_id:
            payload["captchaId"] = captcha_id
        if lot_number:
            payload["lotNumber"] = lot_number
        if captcha_output:
            payload["captchaOutput"] = captcha_output
        if gen_time:
            payload["genTime"] = gen_time

        headers = {"Content-Type": "application/json"}
        resp = self._post(SEND_EMAIL_VERIFY_CODE_URL, json_data=payload, headers=headers, params={"initEncrypt": 0})
        ec = resp.get("ec", resp.get("code", 0))
        if ec != 0 and ec != 200:
            em = resp.get("em", resp.get("msg", "Failed to send email verification code"))
            raise MiraimindResponseError(ec, em, resp)
        logger.info(f"Verification code requested for {email}")
        return resp

    # ─── 7. CONFIRM EMAIL CODE (loginWeb) ────────────────────────────────────────
    def confirm_email_code(self, email: str, email_code: str) -> Dict[str, Any]:
        """
        Binds visitor account to email using OTP code via loginWeb.
        """
        mdata_res = build_mdata(
            region_code="VND",
            chat_user_id=self.chat_user_id or "",
            identifier=email,
            mdp_login_type=13,
            email_code=email_code,
            init_encrypt=1,
            rom=self.profile.rom,
            common_params=self.profile.build_common_params(),
        )
        post_data = {
            "mdata": mdata_res["mdata"],
            "initEncrypt": 1,
            "email": email,
            "code": email_code,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = self._post(LOGIN_WEB_URL, data=post_data, headers=headers)

        if "data" in resp and isinstance(resp["data"], str):
            try:
                decrypted = native_h(resp["data"], mdata_res["response_decryption_key"])
                data_obj = json.loads(decrypted)
            except Exception:
                data_obj = resp.get("data", {})
        else:
            data_obj = resp.get("data", resp)

        token = data_obj.get("token") or data_obj.get("access_token") or ""
        self.access_token = token
        logger.info(f"confirmEmailCode success: user token={token[:12]}...")
        return data_obj

    # ─── 8. SUBMIT INVITATION CODE (invitation/submit) ───────────────────────────
    def submit_invitation_code(self, invitation_code: str) -> Dict[str, Any]:
        """
        Submits referral / invitation code to gain +200 reward points.
        Decrypts server response with session crypto key.
        """
        if not invitation_code:
            raise ValueError("invitation_code cannot be empty")

        payload = {
            "invitationCode": invitation_code,
            "chatUserId": self.chat_user_id or "",
            "timestamp": int(time.time()),
        }
        # If encrypted session key is present, encrypt payload
        post_body: Dict[str, Any]
        if self.session_crypto_key:
            enc_payload = native_m(json.dumps(payload), self.session_crypto_key)
            post_body = {"mdata": enc_payload, "invitationCode": invitation_code}
        else:
            post_body = payload

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = self._post(INVITATION_SUBMIT_URL, data=post_body, headers=headers)

        # Check for encrypted response
        if "data" in resp and isinstance(resp["data"], str) and self.session_crypto_key:
            try:
                decrypted_str = native_h(resp["data"], self.session_crypto_key)
                data_obj = json.loads(decrypted_str)
            except Exception:
                data_obj = resp.get("data", {})
        else:
            data_obj = resp

        ec = data_obj.get("ec", data_obj.get("code", 0))
        if ec != 0 and ec != 200:
            em = data_obj.get("em", data_obj.get("msg", "Invitation submit failed"))
            if any(k in em.lower() for k in ["invalid", "hết hạn", "không hợp lệ", "không tồn tại"]):
                raise InvalidInvitationCodeError(ec, em, data_obj)
            raise MiraimindResponseError(ec, em, data_obj)

        logger.info(f"Successfully submitted invitation code: {invitation_code} (+{CUSTOMER_INVITATION_REWARD_POINTS} pts)")
        return data_obj

    # ─── 9. MALL & ACCOUNT BALANCE ───────────────────────────────────────────────
    def get_user_balance(self) -> Dict[str, Any]:
        """Returns coin balance and reward points for the authenticated user."""
        return self._get(USER_BALANCE_URL)

    # ─── 10. SOCIAL ENGAGEMENT (Follow, Like, Bot Chat) ──────────────────────────
    def follow_creator(self, creator_id: str) -> Dict[str, Any]:
        """Follows a creator/bot author."""
        return self._post(FOLLOW_ONE_URL, data={"creatorId": creator_id})

    def like_feed(self, feed_id: str) -> Dict[str, Any]:
        """Tym / Likes a feed post."""
        return self._post(FEED_LIKE_URL, data={"feedId": feed_id})

    def enter_chat_session(self, bot_id: str) -> Dict[str, Any]:
        """Executes pre-chat lifecycle sequence to activate bot session."""
        info = self._get(SESSION_GET_BOT_INFO_URL, params={"botId": bot_id})
        self._post(CHAT_PAGE_EXPOSED_URL, data={"botId": bot_id})
        return info

    def send_quick_reply(self, bot_id: str, reply_id: str, text: str) -> Dict[str, Any]:
        """Sends a quick reply or prompt to the character bot."""
        payload = {
            "botId": bot_id,
            "replyId": reply_id,
            "content": text,
        }
        return self._post(QUICK_REPLY_URL, json_data=payload)
