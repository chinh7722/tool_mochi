"""Validated, short-lived proxy CONNECT routes using local DNS.

CONNECT_TO changes the tunnel destination only. The original HTTPS URL, Host,
SNI and certificate verification remain under the HTTP client's control.
"""

import asyncio
import hashlib
import ipaddress
import json
import socket
import threading
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlsplit

from curl_cffi import CurlOpt, ffi, lib

from core.proxy.models import curl_proxy_options


class ProxyDnsRoute:
    """Own the curl_slist for one exact HTTPS hostname and port."""

    def __init__(self, host: str, port: int, address: str, ttl: float = 120):
        normalized_host = str(host).encode('idna').decode('ascii').lower()
        if (not normalized_host or any(c in normalized_host for c in '*:,/\\\r\n\t ')
                or not 1 <= int(port) <= 65535):
            raise ValueError('Route requires an exact hostname and valid HTTPS port.')
        self.host = normalized_host
        self.port = int(port)
        self.address = str(ipaddress.IPv4Address(address))
        self.ttl = max(0.0, float(ttl))
        rule = f'{self.host}:{self.port}:{self.address}:{self.port}'.encode('ascii')
        pointer = lib.curl_slist_append(ffi.NULL, rule)
        if pointer == ffi.NULL:
            raise MemoryError('Could not allocate the proxy DNS route.')
        self._rules = ffi.gc(pointer, lib.curl_slist_free_all)

    def curl_options(self) -> Dict:
        # Each returned dict retains the managed pointer. It stays alive even if
        # the cache expires while an AsyncSession still holds these options.
        return {CurlOpt.CONNECT_TO: self._rules}


_CACHE: Dict[Tuple[str, str, int], Tuple[float, ProxyDnsRoute]] = {}
_CACHE_LOCK = threading.Lock()
_DNS_TIMEOUT = 5.0


def _target(target_url: str):
    try:
        parsed = urlsplit(target_url)
        if parsed.scheme.lower() != 'https' or not parsed.hostname:
            return None
        host = parsed.hostname.encode('idna').decode('ascii').lower()
        port = parsed.port or 443
        if any(c in host for c in '*:,/\\\r\n\t '):
            return None
        return host, port
    except (TypeError, ValueError, UnicodeError):
        return None


def _key(proxy, target_url):
    target = _target(target_url)
    if target is None or not proxy:
        return None
    try:
        explicit = curl_proxy_options(proxy)
        if not explicit:
            return None
        serialized = json.dumps(explicit, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError, AttributeError):
        return None
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest(), target[0], target[1]


async def resolve_proxy_dns_route(proxy, target_url: str) -> Optional[ProxyDnsRoute]:
    """Resolve locally without caching; callers must validate the HTTPS path."""
    key = _key(proxy, target_url)
    if key is None:
        return None
    _, host, port = key
    try:
        addresses = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
            host, port, family=socket.AF_INET, type=socket.SOCK_STREAM), timeout=_DNS_TIMEOUT)
        for family, socktype, proto, canonname, sockaddr in addresses:
            if family == socket.AF_INET and socktype == socket.SOCK_STREAM:
                return ProxyDnsRoute(host, port, sockaddr[0])
    except (OSError, ValueError, TimeoutError):
        return None
    return None


def get_proxy_dns_route(proxy, target_url: str) -> Optional[ProxyDnsRoute]:
    key = _key(proxy, target_url)
    if key is None:
        return None
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is None:
            return None
        expires_at, route = cached
        if time.monotonic() >= expires_at:
            _CACHE.pop(key, None)
            return None
        return route


def remember_proxy_dns_route(proxy, target_url: str, route: ProxyDnsRoute):
    """Remember only a route the caller has successfully verified over HTTPS."""
    key = _key(proxy, target_url)
    if key is None:
        return
    if (route.host, route.port) != (key[1], key[2]):
        raise ValueError('A proxy DNS route cannot be cached for another target.')
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic() + route.ttl, route)


def clear_proxy_dns_route(proxy, target_url: str):
    key = _key(proxy, target_url)
    if key is not None:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
