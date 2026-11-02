"""Static proxies and rotating proxies from Proxy.vn / muaproxyxoay.net."""

from core.proxy.models import ProxyModel
from core.proxy.protocol import (
    ProxyLease,
    ProxyLeasePurpose,
    ProxyPoolSnapshot,
    ProxyReleaseOutcome,
    ProxyStateSnapshot,
    ProxyTransportError,
)
from core.proxy.rotators import (
    BaseRotatingProvider,
    ProxyVnProvider,
    MuaProxyXoayProvider,
    make_rotating_provider,
    RotationResult,
)
from core.proxy.broker import ProxyBroker
from core.proxy.client import ProxyPoolClient

__all__ = [
    "ProxyModel",
    "ProxyLease",
    "ProxyLeasePurpose",
    "ProxyReleaseOutcome",
    "ProxyTransportError",
    "ProxyPoolSnapshot",
    "ProxyStateSnapshot",
    "BaseRotatingProvider",
    "ProxyVnProvider",
    "MuaProxyXoayProvider",
    "make_rotating_provider",
    "RotationResult",
    "ProxyBroker",
    "ProxyPoolClient",
]
