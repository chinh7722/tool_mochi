"""Mochi Chat Async Fetcher and Google Sign-In Client.

Quản lý phiên làm việc, trạng thái token, giả lập phần cứng Android và giao tiếp
với cả Google Play Services và Mochi Chat Backend bằng curl_cffi AsyncSession.
Chạy hoàn toàn cục bộ (Local), cấu hình sẵn mọi credentials không phụ thuộc Worker.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
import logging
import re
from typing import Any, Dict, Optional

try:
    from curl_cffi import CurlHttpVersion, CurlOpt
    from curl_cffi.requests import AsyncSession
except ImportError:
    AsyncSession = Any

from core.auth.google import get_google_id_token_for_app
from core.platforms.mochichat.constants import (
    MOCHI_API_BASE,
    MOCHI_APP_BUILD,
    MOCHI_APP_PKG,
    MOCHI_APP_VERSION,
    MOCHI_CERT_SHA1,
    MOCHI_PLATFORM,
    MOCHI_WEB_CLIENT_ID,
)
from core.platforms.mochichat.device import DeviceInfo
from core.proxy.models import curl_proxy_options
from core.proxy.dns_routes import get_proxy_dns_route, clear_proxy_dns_route
from core.proxy.health import probe_proxy_target
from core.proxy.protocol import is_request_transport_error

logger = logging.getLogger("core.platforms.mochichat.fetcher")


class MochiAccountReadError(RuntimeError):
    """Safe error for authenticated read-only own-account endpoints."""
    def __init__(self, message, *, transport_error=False):
        self.transport_error = bool(transport_error)
        super().__init__(message)


@dataclass
class MochiRedeemResult:
    """Kết quả thực hiện nhập mã mời Mochi Chat."""
    success: bool
    message: str
    error_code: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)


class MochiChatFetcher:
    """Lớp xử lý toàn bộ các HTTP requests bất đồng bộ bằng curl_cffi.AsyncSession."""

    _CACHED_BOOTSTRAP_CONFIG: Optional[Dict[str, Any]] = None

    def __init__(
        self,
        device: Optional[DeviceInfo] = None,
        access_token: Optional[str] = None,
        master_token: Optional[str] = None,
        email: Optional[str] = None,
        proxy: Optional[Any] = None,
        session: Optional[Any] = None,
        timeout: float = 45.0,
        cloud_client: Optional[Any] = None,
        use_remote_config: bool = False,
        allow_local_fallback: bool = True,
        app_login_via_proxy: bool = True,
        redeem_via_proxy: bool = True,
        auth_cert_sha1: Optional[str] = None,
    ):
        self.device = device or DeviceInfo()
        self.access_token = access_token
        self.master_token = master_token
        self.email = email
        self.proxy = proxy
        self.app_login_via_proxy = app_login_via_proxy
        self.redeem_via_proxy = redeem_via_proxy
        if auth_cert_sha1 is not None and (not isinstance(auth_cert_sha1,str)
                or re.fullmatch(r'[0-9a-fA-F]{40}',auth_cert_sha1) is None):
            raise ValueError('Chứng chỉ ứng dụng không hợp lệ.')
        self.auth_cert_sha1 = auth_cert_sha1
        self._request_proxy_enabled = app_login_via_proxy
        self.timeout = timeout
        self.user: Optional[Dict[str, Any]] = None
        self._external_session = session is not None
        self.cloud_client = cloud_client
        self.use_remote_config = use_remote_config
        self.allow_local_fallback = allow_local_fallback
        self.api_base = MOCHI_API_BASE
        self._http1_compatibility = False
        self._transport_recovery = False

        if session is not None:
            self.session = session
        else:
            self.session = self._new_session()

    def _new_session(self):
        request_proxy = self.proxy if self._request_proxy_enabled else None
        options = {}
        if self._transport_recovery or self._http1_compatibility:
            options[CurlOpt.CONNECTTIMEOUT_MS] = 10000
        if self._http1_compatibility:
            # curl_options is applied after impersonation; the profile must not
            # silently restore HTTP/2 while recovering a failed TLS connection.
            options[CurlOpt.HTTP_VERSION] = CurlHttpVersion.V1_1
            route = get_proxy_dns_route(request_proxy, self.api_base)
            if route is not None:
                options.update(route.curl_options())
        # Original Mochi transport: chrome_android + proxy mapping + 45s timeout.
        # Compatibility overrides are used only after a real request failure.
        settings = dict(impersonate="chrome_android", proxies=curl_proxy_options(request_proxy),
            timeout=self.timeout, verify=True, trust_env=False)
        if options:
            settings['curl_options'] = options
        return AsyncSession(**settings)

    async def _reset_session(self):
        if self._external_session:
            return
        previous, self.session = self.session, None
        if previous is not None:
            try:
                await previous.close()
            except Exception:
                pass
        self.session = self._new_session()

    async def set_proxy(self, proxy, *, reset_session=True):
        # A provider can change exit IP while returning the same gateway URL.
        # Revalidate any target route rather than carrying the old exit's cache.
        clear_proxy_dns_route(self.proxy, self.api_base)
        clear_proxy_dns_route(proxy, self.api_base)
        self.proxy = proxy
        self._http1_compatibility = False
        self._transport_recovery = False
        if reset_session:
            await self._reset_session()

    async def _select_request_route(self, enabled):
        if self._request_proxy_enabled != enabled:
            self._request_proxy_enabled = enabled
            self._http1_compatibility = False
            self._transport_recovery = False
            await self._reset_session()

    def _request_proxies(self):
        proxy = self.proxy if self._request_proxy_enabled else None
        # Empty values explicitly disable a caller-owned session's proxy too.
        return curl_proxy_options(proxy) or {'http': '', 'https': ''}

    @classmethod
    def set_cached_bootstrap_config(cls, config: Optional[Dict[str, Any]]) -> None:
        """Thiết lập cấu hình cache tĩnh cho Mochi Chat."""
        cls._CACHED_BOOTSTRAP_CONFIG = config

    @classmethod
    def get_cached_bootstrap_config(cls) -> Optional[Dict[str, Any]]:
        """Lấy cấu hình cache tĩnh của Mochi Chat nếu có."""
        return cls._CACHED_BOOTSTRAP_CONFIG

    async def get_bootstrap_config(self) -> Dict[str, Any]:
        """Lấy cấu hình Mochi Chat - chạy hoàn toàn ở local."""
        return {
            "api_base": self.api_base,
            "web_client_id": MOCHI_WEB_CLIENT_ID,
            "app_pkg": MOCHI_APP_PKG,
            "cert_sha1": self.auth_cert_sha1 or MOCHI_CERT_SHA1,
            "app_version": self.device.app_version or MOCHI_APP_VERSION,
            "app_build": self.device.app_build or MOCHI_APP_BUILD,
        }

    async def ensure_ready(self) -> Dict[str, Any]:
        """Đảm bảo fetcher đã nạp bootstrap config trước khi gọi API."""
        cfg = await self.get_bootstrap_config()
        self.api_base = cfg.get("api_base", self.api_base)
        return cfg

    async def __aenter__(self) -> "MochiChatFetcher":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self) -> None:
        """Đóng kết nối session an toàn nếu được tạo nội bộ."""
        if not self._external_session and self.session is not None:
            try:
                await self.session.close()
            except Exception:
                pass
            self.session = None

    @property
    def android_id(self) -> str:
        return self.device.android_id

    @property
    def device_id(self) -> str:
        return self.device.device_id

    def _get_mochi_headers(self, access_token: Optional[str] = None) -> Dict[str, str]:
        """Bộ Headers chuẩn của ứng dụng Mochi Chat Android (React Native/OkHttp)."""
        app_version = self.device.app_version or MOCHI_APP_VERSION
        app_build = self.device.app_build or MOCHI_APP_BUILD

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "X-Platform": "android",
            "X-Device-Id": self.device.device_id,
            "X-App-Version": app_version,
            "X-App-Build": app_build,
            "X-Country": self.device.device_country,
            "X-Timezone": "Asia/Bangkok" if self.device.device_country == "vn" else "America/New_York",
            "User-Agent": f"Mochichat/{app_version} (Android; OkHttp/4.9.2)",
        }
        token = access_token or self.access_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    async def login(
        self,
        owner_id: str,
        email: Optional[str] = None,
        password: Optional[str] = None,
        proxy: Optional[Any] = None,
        on_step: Optional[Callable[[int, str], None]] = None,
        headless: Optional[bool] = None,
        google_login_via_proxy: bool = True,
        recover_proxy: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Đăng nhập Mochi Chat qua Google Sign-In hoàn toàn ở local."""
        owner_str = str(owner_id).strip()
        if not owner_str:
            raise ValueError("owner_id là bắt buộc để quản lý browser session.")

        login_email = email or self.email
        if not login_email or not password:
            raise ValueError("Cần cung cấp cả email và password để đăng nhập.")

        cfg = await self.ensure_ready()
        eff_proxy = proxy or self.proxy
        if proxy is not None and proxy is not self.proxy:
            await self.set_proxy(proxy)

        web_client_id = cfg.get("web_client_id", MOCHI_WEB_CLIENT_ID)
        app_pkg = cfg.get("app_pkg", MOCHI_APP_PKG)
        cert_sha1 = cfg.get("cert_sha1", MOCHI_CERT_SHA1)

        def _progress_bridge(msg: str) -> None:
            if on_step:
                on_step(1, msg)

        id_token = await get_google_id_token_for_app(
            owner_id=owner_str,
            email=login_email,
            password=password,
            client_id=web_client_id,
            app_pkg=app_pkg,
            client_sig=cert_sha1,
            android_id=self.device.android_id,
            proxy=eff_proxy,
            headless=headless,
            on_progress=_progress_bridge,
            google_login_via_proxy=google_login_via_proxy,
        )

        if not id_token:
            return {
                "ok": False,
                "error": "Google từ chối xác thực hoặc không cấp ID Token cho Mochi Chat.",
            }

        if on_step:
            route = 'qua proxy' if self.proxy and self.app_login_via_proxy else 'bằng mạng trực tiếp'
            on_step(2, f"Đang đăng nhập Mochi Chat API {route}...")

        res = await self._login_mochi_with_id_token(id_token=id_token, region=self.device.device_country,
            on_retry=(lambda message: on_step(2, message)) if on_step else None,
            recover_proxy=recover_proxy)
        if res.get("ok"):
            uid = self.user.get("id", "") if self.user else ""
            uid_short = str(uid)[:16]
            logger.info(f"Đăng nhập Mochi Chat thành công (UID: {uid_short}...).")

        return res

    async def _login_mochi_with_id_token(
        self, id_token: str, region: str = "vn", *,
        on_retry: Optional[Callable[[str], None]] = None,
        recover_proxy: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """[Internal] Gửi ID Token Google sang Mochi Chat Backend để lấy JWT Bearer Token."""
        headers = self._get_mochi_headers()
        payload = {"id_token": id_token, "region": region}
        url = f"{self.api_base}/auth/google"
        await self._select_request_route(self.app_login_via_proxy)

        recovered = False
        compatibility_attempted = self._http1_compatibility
        fresh_tls_attempted = False
        dns_attempted = False
        last_diagnostic = None
        can_recover_proxy = bool(self.proxy and self._request_proxy_enabled and recover_proxy is not None)
        # Two distinct transport adjustments must not consume the final attempt
        # reserved for trying a replacement proxy with the same Google token.
        max_posts = 4 if can_recover_proxy else 3

        async def prepare_dns_route():
            nonlocal last_diagnostic
            if self._external_session or not self.proxy or not self._request_proxy_enabled:
                return False
            if on_retry:
                on_retry('Kiểm tra phục hồi HTTPS bằng DNS từ máy qua chính proxy hiện tại...')
            health = await probe_proxy_target(self.proxy, target_url=self.api_base,
                timeout=8.0, recover_dns=True)
            last_diagnostic = health
            if on_retry:
                message = (f'Đã xác minh HTTPS qua proxy tới {health.target_address}; giữ nguyên tên miền và xác minh chứng chỉ.'
                    if health.success and health.dns_fallback else
                    f'Chưa phục hồi được HTTPS qua proxy: {health.error}')
                on_retry(message)
            return health.success and health.dns_fallback

        for attempt in range(1, max_posts + 1):
            try:
                if on_retry:
                    on_retry(f'Thử kết nối API Mochi lần {attempt}/{max_posts}.')
                request_options = {'proxies': self._request_proxies(), 'verify': True}
                if self._http1_compatibility:
                    request_options['http_version'] = CurlHttpVersion.V1_1
                resp = await self.session.post(url, headers=headers, json=payload, **request_options)
            except Exception as exc:
                code = self._curl_error_code(exc)
                certificate_error = code in (51, 60, 77, 83, 90, 91)
                transient = (code in (5, 6, 7, 16, 18, 28, 35, 52, 55, 56, 92, 97)
                             if code is not None else is_request_transport_error(exc))
                if certificate_error or not transient:
                    return {'ok': False, 'error': str(exc), 'transport_error': False,
                        'code': 'TLS_CERTIFICATE_ERROR' if certificate_error else 'REQUEST_FAILED',
                        'stop_job': certificate_error, 'attempts': attempt,
                        'proxy_recovery_attempted': recovered}
                if attempt < max_posts and code in (28, 35) and not dns_attempted:
                    dns_attempted = True
                    if await prepare_dns_route():
                        fresh_tls_attempted = True
                        self._http1_compatibility = True
                        compatibility_attempted = True
                        await self._reset_session()
                        continue
                if attempt < max_posts and code == 35 and not fresh_tls_attempted:
                    fresh_tls_attempted = True
                    self._http1_compatibility = False
                    self._transport_recovery = True
                    if on_retry:
                        on_retry('Lỗi bắt tay TLS (curl 35); tạo kết nối mới với cấu hình HTTP/TLS mặc định, giữ nguyên tài khoản và token Google.')
                    await self._reset_session()
                    continue
                if attempt < max_posts and code in (16, 92) and not compatibility_attempted:
                    compatibility_attempted = True
                    self._http1_compatibility = True
                    if on_retry:
                        on_retry(f'Lỗi kết nối TLS/HTTP (curl {code}); thử kết nối mới bằng HTTP/1.1, giữ nguyên tài khoản và token Google.')
                    await self._reset_session()
                    continue
                if attempt < max_posts and can_recover_proxy and not recovered:
                    recovered = True
                    if on_retry:
                        on_retry('Kết nối API qua proxy vẫn lỗi; đang chờ đổi IP rồi thử lại cùng token Google.')
                    try:
                        new_proxy = await recover_proxy()
                        if not new_proxy:
                            raise RuntimeError('Không nhận được proxy mới để tiếp tục đăng nhập API.')
                        await self.set_proxy(new_proxy, reset_session=False)
                        self._transport_recovery = True
                        # The new exit may support the original transport even
                        # when recovery overrides failed on the previous exit.
                        # Do not carry forced HTTP/1.1 or a stale DNS route over.
                        last_diagnostic = None
                        if on_retry:
                            on_retry('Proxy đã được cấp lại; thử cấu hình HTTP/TLS mặc định qua proxy mới, giữ nguyên token Google.')
                        await self._reset_session()
                    except Exception as recovery_error:
                        return {'ok': False, 'error': f'Không khôi phục được kết nối proxy: {recovery_error}',
                            'code': 'NETWORK_ERROR', 'transport_error': True, 'curl_code': code,
                            'stop_job': True, 'proxy_used': True, 'attempts': attempt,
                            'proxy_recovery_attempted': recovered}
                    continue
                return {'ok': False, 'error': str(exc), 'code': 'NETWORK_ERROR',
                    'transport_error': True, 'curl_code': code, 'stop_job': True,
                    'diagnostic': last_diagnostic.error if last_diagnostic is not None and not last_diagnostic.success else None,
                    'attempts': attempt, 'proxy_recovery_attempted': recovered,
                    'proxy_used': bool(self.proxy and self._request_proxy_enabled)}
            break

        try:
            data = resp.json()

            if not isinstance(data, dict):
                return {"ok": False, "error": "Mochi API trả về dữ liệu đăng nhập không hợp lệ."}
            if 200 <= resp.status_code < 300 and self._response_succeeded(data):
                auth_info = data.get("data", data)
                if not isinstance(auth_info, dict) or not isinstance(auth_info.get("token"), str) or not auth_info["token"].strip():
                    return {"ok": False, "error": "Mochi API không trả về access token."}
                self.access_token = auth_info["token"]
                self.user = auth_info.get("user")
                return {"ok": True, "token": self.access_token, "user": self.user, "data": auth_info}
            else:
                err_msg = data.get("error") or data.get("errmsg") or f"HTTP {resp.status_code}"
                return {"ok": False, "error": err_msg, "code": data.get("code")}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _curl_error_code(error) -> Optional[int]:
        try:
            code = getattr(error, 'code', None)
            if code is not None:
                return int(code)
        except (TypeError, ValueError):
            pass
        match = re.search(r'curl:\s*\((\d+)\)', str(error), re.I)
        return int(match.group(1)) if match else None

    @staticmethod
    def _response_succeeded(data: Dict[str, Any]) -> bool:
        if not data or data.get('error') or data.get('error_code'):
            return False
        for flag in ('ok', 'success'):
            if flag in data and str(data[flag]).strip().lower() not in ('true', '1'):
                return False
        return True

    async def _get_own_account_data(self, path: str) -> Dict[str, Any]:
        """Strict XAPK getData contract; no arbitrary URLs or mutable endpoints."""
        if path not in ('/user/me','/invites/my-code'):
            raise MochiAccountReadError('Đường dẫn đọc thông tin tài khoản không hợp lệ.')
        if not isinstance(self.access_token,str) or not self.access_token.strip():
            raise MochiAccountReadError('Chưa có phiên đăng nhập Mochi để đọc thông tin tài khoản.')
        await self._select_request_route(self.app_login_via_proxy)
        try:
            response = await self.session.get(self.api_base+path,headers=self._get_mochi_headers(),
                proxies=self._request_proxies(),verify=True,allow_redirects=False)
        except Exception as exc:
            code=self._curl_error_code(exc)
            transport=is_request_transport_error(exc) or code in (5,6,7,16,18,28,35,52,55,56,92,97)
            if code in (51,60,77,83,90,91):
                raise MochiAccountReadError('Không xác minh được chứng chỉ HTTPS của Mochi.') from None
            raise MochiAccountReadError('Không kết nối được Mochi để đọc thông tin tài khoản.',
                transport_error=transport) from None
        status=response.status_code
        if status in (401,403):
            raise MochiAccountReadError('Phiên đăng nhập Mochi không hợp lệ hoặc không có quyền đọc tài khoản.')
        if not 200<=status<300:
            raise MochiAccountReadError(f'Mochi từ chối đọc thông tin tài khoản (HTTP {status}).')
        try:
            payload=response.json(parse_float=Decimal)
        except Exception:
            raise MochiAccountReadError('Mochi trả về dữ liệu đọc tài khoản không phải JSON hợp lệ.') from None
        if (not isinstance(payload,dict) or payload.get('ok') is not True
                or not self._response_succeeded(payload) or not isinstance(payload.get('data'),dict)):
            raise MochiAccountReadError('Mochi chưa xác nhận dữ liệu tài khoản hoặc cấu trúc phản hồi đã thay đổi.')
        return payload['data']

    async def get_own_user_me(self) -> Dict[str, Any]:
        return await self._get_own_account_data('/user/me')

    async def get_own_invite_code(self) -> Dict[str, Any]:
        return await self._get_own_account_data('/invites/my-code')

    async def get_user_me(self, access_token: Optional[str] = None) -> Dict[str, Any]:
        """Lấy thông tin tài khoản hiện tại (GET /api/user/me)."""
        headers = self._get_mochi_headers(access_token)
        resp = await self.session.get(f"{self.api_base}/user/me", headers=headers)
        return resp.json()

    async def get_user_account(self, access_token: Optional[str] = None) -> Dict[str, Any]:
        """Lấy thông tin tài khoản chi tiết (GET /api/user/account)."""
        headers = self._get_mochi_headers(access_token)
        resp = await self.session.get(f"{self.api_base}/user/account", headers=headers)
        return resp.json()

    async def update_profile(
        self,
        nickname: Optional[str] = None,
        gender: Optional[str] = None,
        timezone: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cập nhật thông tin tài khoản / đổi biệt danh (PATCH /api/user/me)."""
        headers = self._get_mochi_headers(access_token)
        payload = {}
        if nickname is not None:
            payload["nickname"] = nickname
        if gender is not None:
            payload["gender"] = gender
        if timezone is not None:
            payload["timezone"] = timezone

        resp = await self.session.patch(f"{self.api_base}/user/me", headers=headers, json=payload)
        return resp.json()

    async def set_nickname(
        self, nickname: str, gender: str = "female", access_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """Đặt biệt danh cho tài khoản (gọi update_profile)."""
        return await self.update_profile(nickname=nickname, gender=gender, access_token=access_token)

    async def get_my_invite_code(self, access_token: Optional[str] = None) -> Dict[str, Any]:
        """Lấy mã mời của tài khoản (GET /api/invites/my-code)."""
        headers = self._get_mochi_headers(access_token)
        resp = await self.session.get(f"{self.api_base}/invites/my-code", headers=headers)
        return resp.json()

    async def redeem_invite_code(
        self, code_or_token: str, optional_code: Optional[str] = None
    ) -> MochiRedeemResult:
        """Nhập mã mời nhận thưởng (POST /api/invites/redeem).

        Hỗ trợ cả 2 cách gọi:
        - fetcher.redeem_invite_code("A75FNX")                 (tự dùng self.access_token)
        - fetcher.redeem_invite_code(access_token, "A75FNX")  (truyền token thủ công)
        """
        if optional_code is not None:
            token = code_or_token
            code = optional_code
        else:
            token = self.access_token
            code = code_or_token

        clean_code = str(code).strip().upper() if code else ""
        if not clean_code:
            return MochiRedeemResult(
                success=False,
                message="Mã mời không được để trống.",
                error_code="INVALID_CODE",
            )

        headers = self._get_mochi_headers(token)
        payload = {"code": clean_code}
        url = f"{self.api_base}/invites/redeem"
        await self._select_request_route(self.redeem_via_proxy)

        try:
            resp = await self.session.post(url, headers=headers, json=payload,
                proxies=self._request_proxies(), verify=True)
            raw_text = resp.text
            status_code = resp.status_code

            try:
                data = resp.json()
            except Exception:
                return MochiRedeemResult(success=False, message=f"Mochi API trả về phản hồi không phải JSON (HTTP {status_code}).", error_code="INVALID_RESPONSE")

            if not isinstance(data, dict):
                return MochiRedeemResult(success=False, message="Mochi API trả về dữ liệu không hợp lệ.", error_code="INVALID_RESPONSE")

            if status_code in (200, 201) and self._response_succeeded(data):
                return MochiRedeemResult(
                    success=True,
                    message="Nhập mã mời thành công! Đã nhận thưởng đậu.",
                    data=data,
                )

            err_code = data.get("code") or data.get("error_code") or "REDEEM_FAILED"
            err_msg = data.get("message") or data.get("error") or data.get("errmsg") or f"HTTP {status_code}"
            err_lower = str(err_msg).lower()

            if "already" in err_lower or "đã từng" in err_lower:
                return MochiRedeemResult(
                    success=False,
                    message="Tài khoản này đã từng sử dụng mã mời rồi.",
                    error_code="ALREADY_REDEEMED",
                    data=data,
                )

            if "limit" in err_lower or "rolling 24-hour" in err_lower or "giới hạn" in err_lower:
                return MochiRedeemResult(
                    success=False,
                    message="inviter user has reached rolling 24-hour invite count limit",
                    error_code="INVITE_LIMIT_REACHED",
                    data=data,
                )

            if "invalid" in err_lower or "không hợp lệ" in err_lower or "not found" in err_lower:
                return MochiRedeemResult(
                    success=False,
                    message="Mã mời không hợp lệ.",
                    error_code="INVALID_CODE",
                    data=data,
                )

            return MochiRedeemResult(
                success=False,
                message=str(err_msg),
                error_code=str(err_code),
                data=data,
            )

        except Exception as e:
            return MochiRedeemResult(
                success=False,
                message=str(e),
                error_code="NETWORK_ERROR",
                data={'proxy_used': bool(self.proxy and self._request_proxy_enabled)},
            )

    async def claim_daily_checkin(self, access_token: Optional[str] = None) -> Dict[str, Any]:
        """Điểm danh hàng ngày nhận 20 đậu (POST /api/checkin/claim)."""
        headers = self._get_mochi_headers(access_token)
        resp = await self.session.post(f"{self.api_base}/checkin/claim", headers=headers)
        return resp.json()

    async def get_daily_status(self, access_token: Optional[str] = None) -> Dict[str, Any]:
        """Xem trạng thái điểm danh hôm nay (GET /api/tasks/daily/status)."""
        headers = self._get_mochi_headers(access_token)
        resp = await self.session.get(f"{self.api_base}/tasks/daily/status", headers=headers)
        return resp.json()

