"""Rotating Proxy Providers for Proxy.vn and muaproxyxoay.net.

Integrates with https://proxy.vn/ (API at https://proxyxoay.shop/api/get.php)
Provides automated IP rotation, proxy leasing, cooldown tracking, and status inspection.
100% local fidelity.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from ipaddress import ip_address
import logging
import re
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlparse

from curl_cffi.requests import AsyncSession

from core.proxy.models import ProxyModel

logger = logging.getLogger("core.proxy.rotators")


@dataclass
class RotationResult:
    success: bool
    proxy: Optional[ProxyModel] = None
    next_rotation_allowed_at: float = 0.0
    wait_seconds: float = 0.0
    error: Optional[str] = None
    info: Dict[str, Any] = None
    is_cooling: bool = False  # True nếu đang trong thời gian chờ (cooldown/chưa đến lúc xoay)


def is_cooldown_or_wait_message(msg: str) -> bool:
    """Kiểm tra xem thông báo có phải là đang chờ cooldown xoay hay không."""
    if not msg:
        return False
    m = str(msg).lower()
    if any(text in m for text in ('timeout', 'timed out', 'hết thời gian chờ', 'het thoi gian cho')):
        return False
    return bool(re.search(r"\bcho\s+\d+|chờ|\bwait(?:ing)?\b|cooling|die sau|chua den|s nua|s nữa|đang nghỉ|(?:con|còn)\s+\d+(?:\.\d+)?\s*(?:s\b|giây|giay)", m))


def is_real_proxy_error(err_msg: str, status: Optional[int] = None) -> bool:
    """Chỉ coi là lỗi thật nếu key hỏng, hết hạn, hoặc bị từ chối; KHÔNG coi chờ xoay là lỗi."""
    if status == 101 and not is_cooldown_or_wait_message(err_msg):
        return True
    if not err_msg:
        return False
    m = str(err_msg).lower()
    return any(w in m for w in ("khong ton tai", "không tồn tại", "does not exist", "het han", "hết hạn", "invalid", "sai key", "khoa", "khóa", "unauthorized"))


class BaseRotatingProvider:
    """Lớp cơ sở cho các nhà cung cấp Proxy xoay."""
    provider_id = 'proxyvn'

    def __init__(self, api_key: str, min_wait_seconds: float = 3.0, current_endpoint: Optional[str] = None):
        self.api_key = api_key.strip()
        self.min_wait_seconds = min_wait_seconds
        self._last_rotated_at: float = 0.0
        self._current_proxy: Optional[ProxyModel] = None
        if current_endpoint and current_endpoint.strip():
            try:
                self._current_proxy = ProxyModel.from_string(current_endpoint.strip(), provider="proxy.vn")
            except Exception:
                pass
        self._lock = asyncio.Lock()

    async def get_current_or_rotate(
        self,
        force_rotate: bool = False,
        wait_until_rotated: bool = False,
        max_wait_seconds: float = 300.0,
        on_wait_progress: Optional[Callable[[float, str], None]] = None,
    ) -> RotationResult:
        raise NotImplementedError()


class ProxyVnProvider(BaseRotatingProvider):
    """Tích hợp chính thức dịch vụ Proxy Xoay của Proxy.vn (https://proxy.vn/?home=apixoay).
    
    API endpoint: https://proxyxoay.shop/api/get.php
    """

    API_ENDPOINT = "https://proxyxoay.shop/api/get.php"

    def __init__(
        self,
        api_key_or_url: str,
        nhamang: str = "random",
        tinhthanh: str = "0",
        min_wait_seconds: float = 5.0,
        current_endpoint: Optional[str] = None,
    ):
        raw = api_key_or_url.strip()
        key = raw

        # Nếu người dùng dán nguyên link https://proxyxoay.shop/api/get.php?key=XYZ...
        if "key=" in raw:
            try:
                parsed = urlparse(raw)
                qs = parse_qs(parsed.query)
                if "key" in qs and qs["key"]:
                    key = qs["key"][0]
                if "nhamang" in qs and qs["nhamang"]:
                    nhamang = qs["nhamang"][0]
                if "tinhthanh" in qs and qs["tinhthanh"]:
                    tinhthanh = qs["tinhthanh"][0]
            except Exception:
                pass

        super().__init__(api_key=key, min_wait_seconds=min_wait_seconds, current_endpoint=current_endpoint)
        self.nhamang = nhamang or "random"
        self.tinhthanh = tinhthanh or "0"
        self._last_info: Dict[str, Any] = {}

    async def get_current_or_rotate(
        self, force_rotate: bool = False, wait_until_rotated: bool = False,
        max_wait_seconds: float = 300.0,
        on_wait_progress: Optional[Callable[[float, str], None]] = None,
    ) -> RotationResult:
        """Reuse the current endpoint; only force a change after transport failure."""
        deadline = time.monotonic() + max(0.0, max_wait_seconds)
        last_wait = 0.0
        try:
            async with asyncio.timeout(max(0.0, max_wait_seconds)):
                async with self._lock:
                    if not force_rotate and self._current_proxy:
                        return RotationResult(success=True, proxy=self._current_proxy, info=self._last_info)
                    # Original provider sends only key unless the user has
                    # explicitly selected a carrier/province override.
                    params = {'key': self.api_key}
                    if self.nhamang.lower() not in ('', 'random'):
                        params['nhamang'] = self.nhamang
                    if self.tinhthanh not in ('', '0'):
                        params['tinhthanh'] = self.tinhthanh
                    async with AsyncSession(timeout=30.0, trust_env=False) as session:
                        while True:
                            wait_sec = 2.0
                            try:
                                remaining = deadline - time.monotonic()
                                resp = await session.get(self.API_ENDPOINT, params=params, timeout=min(30.0, max(0.01, remaining)))
                                if resp.status_code != 200:
                                    raise RuntimeError(f'Proxy.vn HTTP {resp.status_code}')
                                data = resp.json()
                                if not isinstance(data, dict):
                                    raise ValueError('Proxy.vn trả về dữ liệu không hợp lệ.')
                                try:
                                    status = int(data.get('status', 0))
                                except (ValueError, TypeError):
                                    status = 0
                                msg = str(data.get('message') or data.get('comen') or '')
                                self._last_info = data
                                # Fatal authentication errors take precedence over cooldown text.
                                if is_real_proxy_error(msg, status):
                                    return RotationResult(success=False, error=msg or 'Key Proxy.vn không hợp lệ.', info=data)
                                nested = data.get('data') if isinstance(data.get('data'), dict) else {}
                                http_raw = data.get('proxyhttp') or nested.get('proxyhttp')
                                socks_raw = data.get('proxysocks5') or nested.get('proxysocks5')
                                proxy_raw = http_raw or socks_raw
                                if proxy_raw and not http_raw and '://' not in proxy_raw:
                                    proxy_raw = 'socks5://' + proxy_raw
                                if status == 100 and proxy_raw:
                                    self._current_proxy = ProxyModel.from_string(proxy_raw, provider='proxy.vn')
                                    self._last_rotated_at = time.time()
                                    return RotationResult(success=True, proxy=self._current_proxy,
                                        next_rotation_allowed_at=self._last_rotated_at + self.min_wait_seconds, info=data)
                                cooling = status == 102 or is_cooldown_or_wait_message(msg)
                                if not cooling:
                                    raise RuntimeError(msg or f'Proxy.vn không cấp endpoint (status={status}).')
                                # Cooldown only restricts changing IP; an endpoint
                                # supplied with the response can still be used.
                                if proxy_raw and not force_rotate:
                                    self._current_proxy = ProxyModel.from_string(proxy_raw, provider='proxy.vn')
                                    return RotationResult(success=True, proxy=self._current_proxy, info=data)
                                match = re.search(r'(\d+(?:\.\d+)?)\s*(?:s\b|giây|giay|seconds?)', msg, re.I)
                                wait_sec = max(0.2, float(match.group(1))) if match else 10.0
                                last_wait = wait_sec
                                if not wait_until_rotated:
                                    return RotationResult(success=False, proxy=self._current_proxy,
                                        wait_seconds=wait_sec, next_rotation_allowed_at=time.time() + wait_sec,
                                        is_cooling=True, info=data)
                            except Exception as exc:
                                if not wait_until_rotated:
                                    return RotationResult(success=False, error=str(exc), info=self._last_info)
                                logger.warning('Proxy.vn chưa sẵn sàng (%s), sẽ thử lại.', type(exc).__name__)
                            wait_until = min(deadline, time.monotonic() + wait_sec)
                            while time.monotonic() < wait_until:
                                rem = wait_until - time.monotonic()
                                if on_wait_progress:
                                    on_wait_progress(rem, f'Đang chờ xoay IP: còn {rem:.0f}s...')
                                await asyncio.sleep(min(1.0, rem))
        except TimeoutError:
            return RotationResult(success=False, proxy=self._current_proxy, is_cooling=last_wait > 0,
                wait_seconds=last_wait, error='Hết thời gian chờ Proxy.vn; chưa cấp proxy cho tác vụ.', info=self._last_info)

    async def rotate_and_wait(
        self,
        timeout: float = 300.0,
        on_progress: Optional[Callable[[float, str], None]] = None,
    ) -> RotationResult:
        """Bắt buộc xoay IP và đợi đến khi xoay thành công."""
        return await self.get_current_or_rotate(
            force_rotate=True,
            wait_until_rotated=True,
            max_wait_seconds=timeout,
            on_wait_progress=on_progress,
        )


class MuaProxyXoayProvider(BaseRotatingProvider):
    """muaproxyxoay.net change-IP API, using the supplied authenticated endpoint."""
    provider_id = 'muaproxyxoay'
    API_ENDPOINT = 'https://api.muaproxyxoay.net/user/package/changeip'

    def __init__(self, api_key_or_url, *, current_endpoint=None, app_id=None):
        raw = str(api_key_or_url).strip()
        if '://' in raw:
            parsed = urlparse(raw)
            if (parsed.scheme != 'https' or parsed.hostname != 'api.muaproxyxoay.net'
                    or parsed.path.rstrip('/') != '/user/package/changeip'
                    or parsed.username or parsed.password or parsed.fragment
                    or parsed.port not in (None, 443)):
                raise ValueError('Link đổi IP muaproxyxoay.net không hợp lệ.')
            query = parse_qs(parsed.query)
            keys = query.get('package_api_key', [])
            if len(keys) != 1:
                raise ValueError('Link cần có một package_api_key.')
            raw = keys[0].strip()
            apps = query.get('app_id', [])
            if len(apps) > 1:
                raise ValueError('Link có nhiều app_id.')
            app_id = apps[0] if apps else app_id
        if not raw or any(char.isspace() for char in raw) or '|' in raw:
            raise ValueError('Key xoay muaproxyxoay.net không hợp lệ.')
        if app_id is not None and (isinstance(app_id, bool) or not str(app_id).isdigit()):
            raise ValueError('app_id phải là số nguyên không âm.')
        super().__init__(raw, current_endpoint=current_endpoint)
        self.app_id = int(app_id) if app_id is not None else None
        self._last_info = {}
        if self._current_proxy:
            self._current_proxy.provider = 'muaproxyxoay.net'

    def key_reference(self):
        params = {'package_api_key': self.api_key}
        if self.app_id is not None:
            params['app_id'] = self.app_id
        return self.API_ENDPOINT + '?' + urlencode(params)

    def _response_proxy(self, data):
        rows = data.get('Data')
        if isinstance(rows, dict):
            rows = [rows]
        if not rows:
            if self._current_proxy:
                return self._current_proxy
            raise ValueError('API báo thành công nhưng chưa có endpoint proxy.')
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError('Data của API proxy không hợp lệ.')
        current = self._current_proxy
        protocol = current.protocol if current else 'http'
        port_field = 'socks_port' if protocol.startswith('socks') else 'https_port' if protocol == 'https' else 'http_port'
        candidates = rows
        if len(rows) > 1 and current:
            by_port = [row for row in rows if str(row.get(port_field)) == str(current.port)]
            by_host = [row for row in rows if current.host in (row.get('public_ip'), row.get('public_origin_ip'))]
            candidates = by_port if len(by_port) == 1 else by_host
        if len(candidates) != 1:
            raise ValueError('API trả nhiều proxy; không xác định được endpoint tương ứng.')
        row = candidates[0]
        host = row.get('public_ip') or row.get('public_origin_ip')
        port = row.get(port_field)
        if not isinstance(host, str) or not host.strip() or isinstance(port, bool):
            raise ValueError('API thiếu IP hoặc cổng proxy hợp lệ.')
        host = str(ip_address(host.strip()))
        try:
            port = int(str(port))
        except (ValueError, TypeError) as exc:
            raise ValueError('Cổng proxy từ API không hợp lệ.') from exc
        auth = str(row.get('proxy_auth_type', '')).upper()
        if not current and auth and auth != 'IP_ADDRESS':
            raise ValueError('Gói dùng mật khẩu; nhập ip:port:user:pass|key vào Proxy Pool.')
        return ProxyModel(host, port, username=current.username if current else None,
            password=current.password if current else None, protocol=protocol,
            provider='muaproxyxoay.net', rotation_url=self.key_reference())

    async def get_current_or_rotate(self, force_rotate=False, wait_until_rotated=False,
            max_wait_seconds=300.0, on_wait_progress=None):
        deadline = time.monotonic() + max(0.0, max_wait_seconds)
        cooling_until = 0.0
        try:
            async with asyncio.timeout(max(0.0, max_wait_seconds)):
                async with self._lock:
                    if self._current_proxy is not None and not force_rotate:
                        return RotationResult(True, self._current_proxy, info=self._last_info)
                    params = {'package_api_key': self.api_key}
                    if self.app_id is not None:
                        params['app_id'] = self.app_id
                    async with AsyncSession(timeout=30.0, trust_env=False) as session:
                        while True:
                            remaining = max(0.01, deadline - time.monotonic())
                            try:
                                response = await session.get(self.API_ENDPOINT, params=params, timeout=min(30.0, remaining))
                                if response.status_code in (401, 403):
                                    return RotationResult(False, error=f'muaproxyxoay.net từ chối quyền truy cập (HTTP {response.status_code}).')
                                if response.status_code == 429:
                                    try:
                                        retry_after = float((getattr(response, 'headers', {}) or {}).get('Retry-After', 10))
                                    except (TypeError, ValueError):
                                        retry_after = 10.0
                                    data = {'Status': 'Error', 'Message': f'Vui lòng chờ sau {max(0.2, retry_after)} giây'}
                                elif response.status_code != 200:
                                    raise RuntimeError(f'HTTP {response.status_code}')
                                else:
                                    data = response.json()
                                if not isinstance(data, dict):
                                    raise ValueError('Phản hồi không phải đối tượng JSON.')
                            except Exception as exc:
                                if not wait_until_rotated:
                                    return RotationResult(False, error=f'Không đọc được API muaproxyxoay.net ({type(exc).__name__}).')
                                wait = 2.0
                            else:
                                self._last_info = data
                                status = str(data.get('Status', '')).strip().casefold()
                                original_message = str(data.get('Message') or '')
                                message = original_message.replace(self.api_key, '[ẩn key]')
                                if status == 'success':
                                    try:
                                        model = self._response_proxy(data)
                                    except (ValueError, TypeError) as exc:
                                        return RotationResult(False, error=str(exc).replace(self.api_key, '[ẩn key]'), info=data)
                                    self._current_proxy = model
                                    self._last_rotated_at = time.time()
                                    return RotationResult(True, model, info=data)
                                fatal = is_real_proxy_error(original_message) or any(term in original_message.casefold()
                                    for term in ('wrong key', 'not found', 'access denied', 'not exist'))
                                if fatal or status != 'error' or not is_cooldown_or_wait_message(message):
                                    return RotationResult(False, error=message or 'API không xác nhận đổi IP thành công.', info=data)
                                match = re.search(r'(\d+(?:\.\d+)?)\s*(?:giây|giay|s\b|seconds?)', message, re.I)
                                wait = max(0.2, float(match.group(1))) if match else 10.0
                                cooling_until = time.monotonic() + wait
                                if not wait_until_rotated:
                                    return RotationResult(False, proxy=self._current_proxy, is_cooling=True,
                                        wait_seconds=wait, next_rotation_allowed_at=time.time() + wait,
                                        error=message, info=data)
                            until = min(deadline, time.monotonic() + wait)
                            while time.monotonic() < until:
                                rem = until - time.monotonic()
                                if on_wait_progress:
                                    on_wait_progress(rem, f'Đang chờ muaproxyxoay.net đổi IP: còn {rem:.0f}s...')
                                await asyncio.sleep(min(1.0, rem))
        except TimeoutError:
            remaining = max(0.0, cooling_until - time.monotonic())
            return RotationResult(False, proxy=self._current_proxy, is_cooling=remaining > 0,
                wait_seconds=remaining, next_rotation_allowed_at=time.time() + remaining,
                error='Hết thời gian chờ muaproxyxoay.net đổi IP.', info=self._last_info)

    async def rotate_and_wait(self, timeout=300.0, on_progress=None):
        return await self.get_current_or_rotate(force_rotate=True, wait_until_rotated=True,
            max_wait_seconds=timeout, on_wait_progress=on_progress)


def rotating_provider_id(key_or_url='', provider_id='proxyvn'):
    value = str(key_or_url).strip()
    if '://' in value and urlparse(value).hostname == 'api.muaproxyxoay.net':
        return 'muaproxyxoay'
    if '://' in value and urlparse(value).hostname in ('proxyxoay.shop', 'proxy.vn', 'www.proxy.vn'):
        return 'proxyvn'
    return 'muaproxyxoay' if provider_id in ('muaproxyxoay', 'muaproxyxoay.net') else 'proxyvn'


def make_rotating_provider(key_or_url, *, provider_id='proxyvn', current_endpoint=None, **options):
    if rotating_provider_id(key_or_url, provider_id) == 'muaproxyxoay':
        return MuaProxyXoayProvider(key_or_url, current_endpoint=current_endpoint, app_id=options.get('app_id'))
    return ProxyVnProvider(key_or_url, current_endpoint=current_endpoint,
        nhamang=options.get('nhamang', 'random'), tinhthanh=options.get('tinhthanh', '0'))


def rotation_key_reference(key_or_url, provider_id='proxyvn', *, canonical=False):
    if not key_or_url:
        return ''
    if rotating_provider_id(key_or_url, provider_id) == 'muaproxyxoay':
        return MuaProxyXoayProvider(key_or_url).key_reference()
    if canonical:
        provider = ProxyVnProvider(key_or_url)
        params = {'key': provider.api_key}
        if provider.nhamang.lower() not in ('', 'random'):
            params['nhamang'] = provider.nhamang
        if provider.tinhthanh not in ('', '0'):
            params['tinhthanh'] = provider.tinhthanh
        return provider.API_ENDPOINT + '?' + urlencode(params)
    return str(key_or_url).strip()


def provider_shared_identity(provider):
    digest = hashlib.sha256(provider.api_key.encode()).hexdigest()
    scope = 'muaproxyxoay.net:' if getattr(provider, 'provider_id', 'proxyvn') == 'muaproxyxoay' else ''
    return 'rotation:' + scope + digest
