"""Proxy IP checks reconstructed from the original release's visible constants.

The original checker uses public IP APIs with provider fallback. A valid result
proves that proxy requests work; it does not prove connectivity to Mochi.
"""

import asyncio
import ipaddress
import re
import time

from curl_cffi.requests import AsyncSession
from curl_cffi.requests.exceptions import ProxyError, Timeout

from core.proxy.health import ProxyHealthResult
from core.proxy.models import ProxyModel, curl_proxy_options


# Exact order and URLs retained in proxy_core.proxy_info.CHECK_APIS.
CHECK_APIS = (
    ('http://ip-api.com/json/?fields=status,message,query,country,countryCode,continent,regionName,city,zip,timezone,isp,org,as', 'ip-api.com'),
    ('https://ipwho.is/', 'ipwho.is'),
    ('https://ipinfo.io/json', 'ipinfo.io'),
    ('https://api.ip2location.io/', 'ip2location.io'),
    ('https://ipapi.co/json/', 'ipapi.co'),
    ('https://api.ipify.org?format=json', 'ipify.org'),
    ('https://api64.ipify.org?format=json', 'ipify64.org'),
)


def _curl_code(exc):
    code = getattr(exc, 'code', None)
    if code is not None:
        try:
            return int(code)
        except (TypeError, ValueError):
            pass
    match = re.search(r'curl:\s*\((\d+)\)', str(exc), re.I)
    return int(match.group(1)) if match else None


def _validated_ip(payload):
    if not isinstance(payload, dict):
        return None
    if payload.get('status') == 'fail' or payload.get('success') is False or payload.get('error'):
        return None
    value = payload.get('query') or payload.get('ip')
    if not isinstance(value, str):
        return None
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def _connection_interrupted(exc):
    return (_curl_code(exc) in (52, 55, 56) or isinstance(exc, (ConnectionResetError, BrokenPipeError))
        or getattr(exc, 'winerror', None) == 10054)


def _request_error(source, exc):
    """Report a useful cause without exposing request URLs or credentials."""
    code = _curl_code(exc)
    if _connection_interrupted(exc):
        reason = 'Kết nối proxy bị ngắt/reset khi gửi hoặc nhận dữ liệu'
    elif code in (5, 7, 97) or isinstance(exc, (ProxyError, ConnectionRefusedError)):
        reason = 'Không kết nối được proxy hoặc gateway từ chối kết nối'
    elif code in (35, 51, 60, 77, 83, 90, 91):
        reason = 'Không hoàn tất kết nối HTTPS/TLS qua proxy'
    elif isinstance(exc, (Timeout, TimeoutError)) or code == 28:
        reason = 'Kiểm tra IP hết thời gian chờ'
    else:
        reason = f'Lỗi request kiểm tra IP ({type(exc).__name__})'
    return f'{source}: {reason}' + (f' (curl {code})' if code is not None else '')


async def probe_proxy_connection(proxy, timeout=10.0, max_timeout_retries=2) -> ProxyHealthResult:
    """Read the exit IP through the proxy using the original provider chain.

    ``timeout`` is seconds per call; at most ``max_timeout_retries`` timed-out
    calls are allowed across all providers. The complete check has a 30 s cap.
    One interrupted request may be retried using a fresh connection to the same
    proxy. No rotation API, account actions or Mochi requests are made here.
    """
    timeout = float(timeout)
    max_timeout_retries = int(max_timeout_retries)
    if timeout <= 0 or max_timeout_retries <= 0:
        raise ValueError('Timeout và số lần timeout tối đa phải lớn hơn 0.')
    mapping = curl_proxy_options(proxy)
    proxy_url = mapping.get('https') or mapping.get('http') if mapping else None
    endpoint = ProxyModel.from_string(proxy_url).server if proxy_url else 'Direct'
    started = time.monotonic()
    timeout_count = 0
    last_status = None
    last_code = None
    source = None
    transport_error = False
    interrupted_retry = False
    failures = []
    detail = 'Tất cả API kiểm tra proxy đều thất bại.'

    def result(success=False, *, exit_ip=None):
        error = detail
        if len(failures) > 1:
            error = f'Không lấy được IP qua {len(failures)} nguồn kiểm tra. ' + '; '.join(failures)
        if interrupted_retry and not success:
            error += '. Đã thử lại một lần bằng kết nối mới tới cùng proxy.'
        return ProxyHealthResult(success, endpoint, 'ip', error='' if success else error,
            latency_ms=int((time.monotonic() - started) * 1000),
            http_status=last_status, curl_code=last_code,
            transport_error=False if success else transport_error,
            exit_ip=exit_ip, source_api=source)

    try:
        async with asyncio.timeout(30.0):
            # Keep every request on the explicitly selected proxy, independent
            # of Windows HTTP(S)_PROXY / NO_PROXY environment variables.
            async with AsyncSession(trust_env=False) as session:
                for url, source in CHECK_APIS:
                    last_status = None
                    last_code = None
                    try:
                        try:
                            response = await session.get(url, proxy=proxy_url, timeout=timeout)
                        except Exception as first_error:
                            if not _connection_interrupted(first_error) or interrupted_retry:
                                raise
                            interrupted_retry = True
                            await asyncio.sleep(0.2)
                            async with AsyncSession(trust_env=False) as fresh:
                                response = await fresh.get(url, proxy=proxy_url, timeout=timeout)
                        last_status = response.status_code
                        if last_status == 407:
                            detail = 'Proxy yêu cầu xác thực hoặc sai gateway/forward port (HTTP 407).'
                            transport_error = True
                            failures.clear()
                            return result()
                        if not 200 <= last_status < 300:
                            detail = f'API kiểm tra IP {source} trả HTTP {last_status}.'
                            failures.append(detail)
                            continue
                        try:
                            address = _validated_ip(response.json())
                        except (TypeError, ValueError):
                            address = None
                        if address is not None:
                            return result(True, exit_ip=address)
                        detail = f'API kiểm tra IP {source} không trả địa chỉ IP hợp lệ.'
                        failures.append(detail)
                    except Exception as exc:
                        last_code = _curl_code(exc)
                        last_status = getattr(getattr(exc, 'response', None), 'status_code', None)
                        if last_status == 407:
                            detail = 'Proxy yêu cầu xác thực hoặc sai gateway/forward port (HTTP 407).'
                            transport_error = True
                            failures.clear()
                            return result()
                        detail = _request_error(source, exc)
                        failures.append(detail)
                        if isinstance(exc, (Timeout, TimeoutError)) or last_code == 28:
                            timeout_count += 1
                            transport_error = True
                            if timeout_count >= max_timeout_retries:
                                return result()
                        elif isinstance(exc, ProxyError) or isinstance(exc, ConnectionRefusedError) or last_code in (5, 7, 97):
                            transport_error = True
                            return result()
                        else:
                            # TLS or provider-specific failures may affect only
                            # one IP API. Continue with the original fallback.
                            transport_error = True
    except TimeoutError:
        detail = 'Kiểm tra proxy hết tổng thời gian chờ 30s.'
        failures.clear()
        last_code = 28
        transport_error = True
    except Exception as exc:
        last_code = _curl_code(exc)
        detail = f'Không mở được phiên kiểm tra proxy ({type(exc).__name__}).'
        transport_error = True
    return result()
