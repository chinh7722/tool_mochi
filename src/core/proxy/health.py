"""Read-only TCP and target HTTPS checks, without account or referral requests."""

import asyncio
from dataclasses import dataclass
import re
import time
from typing import Optional
from urllib.parse import urlsplit

from curl_cffi import CurlHttpVersion, CurlOpt
from curl_cffi.requests import AsyncSession

from core.proxy.models import ProxyModel, curl_proxy_options
from core.proxy.dns_routes import (get_proxy_dns_route, resolve_proxy_dns_route,
    remember_proxy_dns_route, clear_proxy_dns_route)


@dataclass
class ProxyHealthResult:
    success: bool
    endpoint: str
    stage: str
    error: str = ''
    latency_ms: int = 0
    http_status: Optional[int] = None
    curl_code: Optional[int] = None
    transport_error: bool = True
    dns_fallback: bool = False
    target_address: Optional[str] = None
    exit_ip: Optional[str] = None
    source_api: Optional[str] = None

    @property
    def is_active(self):
        return self.success

    @property
    def ip(self):
        return self.exit_ip


async def probe_proxy_target(proxy, target_url=None, timeout=12.0, *, recover_dns=False) -> ProxyHealthResult:
    """A provider's status 100 is not evidence that CONNECT/TLS actually works."""
    if target_url is None:
        # Lazy import avoids loading the complete Mochi runner from this utility.
        from core.platforms.mochichat.constants import MOCHI_API_BASE
        target_url = MOCHI_API_BASE
    model = proxy if isinstance(proxy, ProxyModel) else ProxyModel.from_string(
        proxy['server'] if isinstance(proxy, dict) else proxy if isinstance(proxy, str) else proxy.server)
    target = urlsplit(target_url)
    origin = f'{target.scheme}://{target.netloc}/'
    started = time.monotonic()
    stage = 'tcp'
    writer = None
    route = get_proxy_dns_route(proxy, origin)
    candidate_to_cache = None
    try:
        async with asyncio.timeout(timeout):
            _, writer = await asyncio.wait_for(asyncio.open_connection(model.host, model.port),
                timeout=min(3.0, timeout))
            writer.close()
            await writer.wait_closed()
            writer = None
            stage = 'https'
            async def head(active_route, limit):
                options = {CurlOpt.HTTP_VERSION: CurlHttpVersion.V1_1,
                    CurlOpt.CONNECTTIMEOUT_MS: max(1, int(limit * 1000))}
                if active_route is not None:
                    options.update(active_route.curl_options())
                async with asyncio.timeout(limit):
                    async with AsyncSession(impersonate='chrome_android', proxies=curl_proxy_options(proxy),
                        verify=True, trust_env=False, timeout=limit, curl_options=options) as session:
                        return await session.head(origin, allow_redirects=False)

            remaining = max(0.01, timeout - (time.monotonic() - started))
            async def local_route():
                remaining = max(0.01, timeout - (time.monotonic() - started))
                return await asyncio.wait_for(resolve_proxy_dns_route(proxy, origin),
                    timeout=min(2.0, remaining))

            if recover_dns:
                # The caller already observed a failed target request. Do not
                # spend another handshake timeout repeating that same route.
                clear_proxy_dns_route(proxy, origin)
                route = await local_route()
                if route is None:
                    raise OSError('Không phân giải được DNS từ máy để phục hồi tuyến HTTPS.')
                remaining = max(0.01, timeout - (time.monotonic() - started))
                response = await head(route, remaining)
                candidate_to_cache = route
            else:
                try:
                    response = await head(route, min(5.0, remaining / 2))
                except Exception as first_error:
                    if route is not None:
                        clear_proxy_dns_route(proxy, origin)
                    if getattr(first_error, 'code', None) in (51, 60, 77, 83, 90, 91):
                        raise
                    # Keep URL/SNI/certificate identity while changing only the
                    # CONNECT destination; remember it after HTTPS succeeds.
                    candidate = await local_route()
                    if candidate is None:
                        raise first_error
                    remaining = max(0.01, timeout - (time.monotonic() - started))
                    response = await head(candidate, remaining)
                    route = candidate
                    candidate_to_cache = candidate
            status = response.status_code
            elapsed = int((time.monotonic() - started) * 1000)
            if status == 407:
                return ProxyHealthResult(False, model.server, 'connect',
                    'Proxy yêu cầu xác thực (HTTP 407).', elapsed, http_status=status)
            if status >= 500:
                return ProxyHealthResult(False, model.server, 'http',
                    f'Máy chủ Mochi phản hồi HTTP {status}.', elapsed,
                    http_status=status, transport_error=False)
            if candidate_to_cache is not None:
                remember_proxy_dns_route(proxy, origin, candidate_to_cache)
            return ProxyHealthResult(True, model.server, 'https', latency_ms=elapsed,
                http_status=status, transport_error=False, dns_fallback=route is not None,
                target_address=route.address if route is not None else None)
    except Exception as exc:
        raw_code = getattr(exc, 'code', None)
        match = re.search(r'curl:\s*\((\d+)\)', str(exc), re.I)
        code = int(raw_code) if raw_code is not None else int(match.group(1)) if match else None
        if stage == 'tcp':
            detail = f'Không mở được kết nối TCP tới proxy {model.server} ({type(exc).__name__}).'
        else:
            if code in (35, 51, 60, 77, 83, 90, 91):
                stage = 'tls'
            elif code == 97 or 'tunnel' in str(exc).lower():
                stage = 'connect'
            reason = str(exc) or f'hết thời gian chờ {timeout:g}s ở bước CONNECT/TLS/HTTPS'
            detail = f'Proxy mở được TCP nhưng không hoàn tất HTTPS tới Mochi qua {model.server}: {reason}'
        return ProxyHealthResult(False, model.server, stage, detail,
            int((time.monotonic() - started) * 1000), curl_code=code,
            transport_error=code not in (51, 60, 77, 83, 90, 91))
    finally:
        if writer is not None:
            writer.close()
