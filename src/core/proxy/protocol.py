"""Proxy Protocol Definitions and IPC Contracts.

Matches original core.proxy.protocol architecture with 100% local fidelity.
Provides Lease/Release lifecycle contracts, snapshots, and error classifications.
"""

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Dict, List, Optional, Set

from core.proxy.models import ProxyModel


class ProxyTransportError(Exception):
    """Lỗi vận chuyển kết nối qua Proxy."""

    def __init__(self, proxy: Optional[Any] = None, cause: Optional[Any] = None, kind: str = "transport_error"):
        self.proxy = proxy
        self.cause = cause
        self.kind = kind
        super().__init__(f"ProxyTransportError ({kind}) [proxy={proxy}]: {cause}")


def is_request_transport_error(error: Any) -> bool:
    """Separate request transport failures from account/business rejections."""
    if isinstance(error, (TimeoutError, ConnectionError, ProxyTransportError)):
        return True
    message = str(error).lower()
    return any(marker in message for marker in (
        'curl:', 'curl (', 'net::err_', 'ssl', 'tls', 'timed out', 'timeout',
        'failed to perform', 'could not resolve', 'connection refused',
        'connection reset', 'connection closed', 'connection aborted',
        'connection failed', 'failed to connect', 'proxyconnect', 'proxy connection',
        'proxy authentication required', 'tunnel connection failed',
    ))


class ProxyIpcModel:
    """Base class cho các message IPC của proxy protocol."""
    pass


class ProxyLeasePurpose(str, Enum):
    """Mục đích cấp phát proxy lease."""
    ACCOUNT = "account"
    GENERAL = "general"
    HEALTH_CHECK = "health_check"


class ProxyReleaseOutcome(str, Enum):
    """Kết quả hoàn trả proxy lease."""
    SUCCESS = "success"
    BUSINESS_FAILURE = "business_failure"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    QUARANTINED = "quarantined"
    ABORTED = "aborted"


@dataclass
class ProxyLease:
    """Đại diện cho một phiên cấp phát proxy (lease) được giữ bởi một tác vụ."""

    lease_id: str
    task_id: str
    platform_id: str
    proxy: Optional[ProxyModel] = None
    purpose: ProxyLeasePurpose = ProxyLeasePurpose.ACCOUNT
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_direct(self) -> bool:
        """Kiểm tra lease này có phải kết nối Direct IP (không qua proxy) hay không."""
        return self.proxy is None

    def validate_lease(self) -> bool:
        """Xác minh tính hợp lệ cơ bản của lease."""
        if not self.lease_id or not self.task_id:
            return False
        return True


@dataclass
class ProxyLeaseRequest(ProxyIpcModel):
    task_id: str
    platform_id: str
    purpose: ProxyLeasePurpose = ProxyLeasePurpose.ACCOUNT
    job_id: str = ""
    timeout: float = 30.0


@dataclass
class ProxyReleaseRequest(ProxyIpcModel):
    lease: ProxyLease
    platform_id: str
    outcome: ProxyReleaseOutcome = ProxyReleaseOutcome.SUCCESS
    success: bool = True


@dataclass
class ProxyFailureReport(ProxyIpcModel):
    lease: ProxyLease
    reason: str
    platform_id: str


@dataclass
class ProxyJobReleaseRequest(ProxyIpcModel):
    job_id: str
    platform_id: str


@dataclass
class ProxyPoolShutdown(ProxyIpcModel):
    reason: str = "shutdown"


@dataclass
class ProxyPoolResponse(ProxyIpcModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None

    def validate_response(self) -> bool:
        return self.success or bool(self.error)


@dataclass
class ProxyFailureEvent(ProxyIpcModel):
    proxy_raw: str
    reason: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ProxyQuarantineEvent(ProxyIpcModel):
    proxy_raw: str
    duration_seconds: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class ProxyCheckLeaseExpiredEvent(ProxyIpcModel):
    lease_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class ProxyCheckReservationRequest(ProxyIpcModel):
    task_id: str
    proxies: List[str] = field(default_factory=list)

    def validate_request(self) -> bool:
        return len(self.proxies) > 0


@dataclass
class ProxyCheckReservationBatch(ProxyIpcModel):
    batch_id: str
    items: List[Dict[str, Any]] = field(default_factory=list)

    def validate_reservations(self) -> bool:
        return len(self.items) > 0


@dataclass
class ProxyCheckAttemptResult(ProxyIpcModel):
    proxy_raw: str
    success: bool
    latency_ms: float = 0.0
    error: Optional[str] = None


@dataclass
class ProxyStateSnapshot:
    raw: str
    is_active: bool = True
    in_use: int = 0
    max_slots: int = 1
    cooling_until: float = 0.0
    quarantined_until: float = 0.0
    consecutive_failures: int = 0


@dataclass
class ProxyRuntimeStateSnapshot:
    proxies: Dict[str, ProxyStateSnapshot] = field(default_factory=dict)
    active_leases: int = 0


@dataclass
class ProxyRateLimitStateSnapshot:
    rate_limited_until: Dict[str, float] = field(default_factory=dict)


@dataclass
class ProxyPoolSnapshot:
    """Ảnh chụp trạng thái toàn bộ Proxy Pool tại một thời điểm."""

    proxies: List[ProxyStateSnapshot] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def active_count(self) -> int:
        """Tổng số proxy đang hoạt động không bị cách ly."""
        now = time.time()
        return sum(1 for p in self.proxies if p.is_active and p.quarantined_until <= now)

    @property
    def eligible_count(self) -> int:
        """Số lượng proxy sẵn sàng để lease ngay lập tức."""
        now = time.time()
        return sum(
            1 for p in self.proxies
            if p.is_active and p.quarantined_until <= now and p.cooling_until <= now and p.in_use < p.max_slots
        )

    @property
    def in_use_count(self) -> int:
        """Số proxy đang có ít nhất một slot được sử dụng."""
        return sum(1 for p in self.proxies if p.in_use > 0)

    @property
    def cooling_count(self) -> int:
        """Số proxy đang trong thời gian nghỉ cooldown."""
        now = time.time()
        return sum(1 for p in self.proxies if p.cooling_until > now)

    @property
    def account_slots(self) -> int:
        """Tổng số slot khả dụng trên tất cả proxy hoạt động."""
        now = time.time()
        return sum(
            max(0, p.max_slots - p.in_use)
            for p in self.proxies
            if p.is_active and p.quarantined_until <= now and p.cooling_until <= now
        )


def parse_proxy_command(payload: Any) -> Any:
    """Parse payload command."""
    if isinstance(payload, ProxyIpcModel):
        return payload
    return payload


def parse_proxy_response(payload: Any) -> Any:
    """Parse payload response."""
    if isinstance(payload, ProxyPoolResponse):
        return payload
    return payload

