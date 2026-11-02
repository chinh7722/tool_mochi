"""Proxy Data Models for TTM Tool.

Represents individual proxy endpoints, connection strings, authentication credentials,
and conversions for Playwright, curl_cffi, and HTTP clients.
100% local implementation compatible with original core.proxy architecture.
"""

from dataclasses import dataclass, field
import re
from typing import Any, Dict, Optional
from urllib.parse import quote, unquote, urlsplit


@dataclass
class ProxyModel:
    """Mô hình dữ liệu proxy hỗ trợ HTTP và SOCKS5."""

    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: str = "http"
    raw: str = ""
    rotation_url: Optional[str] = None
    provider: str = "static"

    def __post_init__(self):
        self.protocol = self.protocol.removesuffix('://').lower()
        if self.protocol not in {'http', 'https', 'socks5', 'socks5h', 'socks4'}:
            raise ValueError('Giao thức proxy không được hỗ trợ.')
        if not self.host or not 1 <= self.port <= 65535:
            raise ValueError('Proxy cần có host và port từ 1 đến 65535.')

    @property
    def server(self) -> str:
        """Địa chỉ host:port kèm protocol, ví dụ 'http://1.2.3.4:8080'."""
        proto = self.protocol.lower()
        if not proto.endswith("://"):
            proto = f"{proto}://"
        host = f'[{self.host}]' if ':' in self.host else self.host
        return f"{proto}{host}:{self.port}"

    def to_url(self) -> str:
        """Chuỗi URL đầy đủ có credentials, ví dụ 'http://user:pass@1.2.3.4:8080'."""
        proto = self.protocol.lower()
        if proto.endswith("://"):
            proto = proto[:-3]
        if self.username:
            auth = quote(self.username, safe='')
            if self.password is not None:
                auth += ':' + quote(self.password, safe='')
            return self.server.replace('://', f'://{auth}@', 1)
        return self.server

    def to_playwright_dict(self) -> Dict[str, str]:
        """Format cấu hình proxy tương thích Playwright."""
        d: Dict[str, str] = {
            "server": self.server
        }
        if self.username:
            d["username"] = self.username
        if self.password:
            d["password"] = self.password
        return d

    def to_curl_cffi_dict(self) -> Dict[str, str]:
        """Format cấu hình proxies dict cho curl_cffi / requests."""
        url = self.to_url()
        return {
            "http": url,
            "https": url,
        }

    @property
    def proxy_url(self) -> str:
        """Match the proxy_core.schemas.ProxyModel interface in the original build."""
        return self.to_url()

    @property
    def proxies(self) -> Dict[str, str]:
        return self.to_curl_cffi_dict()

    async def check_info(self, timeout: int = 10000, max_timeout_retries: int = 2,
                         raise_for_status: bool = False):
        from core.proxy.checker import probe_proxy_connection
        result = await probe_proxy_connection(self, timeout=timeout / 1000,
            max_timeout_retries=max_timeout_retries)
        if raise_for_status and not result.success:
            from core.proxy.protocol import ProxyTransportError
            raise ProxyTransportError(proxy=self.server, cause=result.error, kind='proxy_check_failed')
        return result

    def __str__(self) -> str:
        if self.username and self.password:
            return f"{self.host}:{self.port}:{self.username}:{self.password}"
        return f"{self.host}:{self.port}"

    @classmethod
    def from_string(cls, raw: str, provider: str = "static", rotation_url: Optional[str] = None) -> "ProxyModel":
        """Parse chuỗi proxy nhiều định dạng:
        - host:port
        - host:port:user:pass
        - user:pass@host:port
        - http://user:pass@host:port
        - socks5://user:pass@host:port
        """
        raw_clean = raw.strip()
        if not raw_clean:
            raise ValueError("Chuỗi proxy không được rỗng")

        protocol, separator, work = raw_clean.partition('://')
        if not separator:
            protocol, work = 'http', raw_clean
        username = password = None
        parsed = None
        if '@' in work or work.startswith('['):
            try:
                candidate = urlsplit(f'{protocol}://{work}')
                if candidate.hostname and candidate.port is not None:
                    parsed = candidate
            except ValueError:
                pass
        if parsed is not None:
            host, port = parsed.hostname, parsed.port
            username = unquote(parsed.username) if parsed.username is not None else None
            password = unquote(parsed.password) if parsed.password is not None else None
        else:
            # A literal @ inside the legacy password is not a URL authority.
            # Keep valid user:password@host:port URLs above, including passwords
            # containing colons, and fall back only when that URL has no port.
            parts = work.split(':', 3)
            if len(parts) not in (2, 4):
                raise ValueError('Proxy phải có dạng host:port hoặc host:port:user:pass.')
            host, port = parts[0], int(parts[1])
            if len(parts) == 4:
                username, password = parts[2], parts[3]
        if port is None:
            raise ValueError('Thiếu port proxy.')
        return cls(host=host, port=port, username=username, password=password,
                   protocol=protocol, raw=raw_clean, provider=provider, rotation_url=rotation_url)


def curl_proxy_options(proxy: Any) -> Optional[Dict[str, str]]:
    """Normalize both Playwright-style and requests-style proxy configurations."""
    if not proxy:
        return None
    if isinstance(proxy, ProxyModel):
        return proxy.to_curl_cffi_dict()
    if isinstance(proxy, dict):
        if 'server' not in proxy:
            return proxy
        model = ProxyModel.from_string(proxy['server'])
        model.username = proxy.get('username', model.username)
        model.password = proxy.get('password', model.password)
        return model.to_curl_cffi_dict()
    if isinstance(proxy, str):
        return ProxyModel.from_string(proxy).to_curl_cffi_dict()
    model = ProxyModel.from_string(proxy.server)
    model.username = getattr(proxy, 'username', model.username)
    model.password = getattr(proxy, 'password', model.password)
    return model.to_curl_cffi_dict()

