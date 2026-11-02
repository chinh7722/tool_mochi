"""Proxy Broker and Pool Manager.

Core orchestrator managing proxy allocation, lease lifecycles, concurrency slots,
cooldown periods, rotating IP provider hooks, and failure quarantine.
100% local fidelity with original core.proxy.broker architecture.
"""

import asyncio
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import hashlib
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
import uuid

from core.proxy.models import ProxyModel
from core.proxy.protocol import (
    ProxyLease,
    ProxyLeasePurpose,
    ProxyPoolSnapshot,
    ProxyReleaseOutcome,
    ProxyStateSnapshot,
    ProxyTransportError,
)
from core.proxy.rotators import BaseRotatingProvider, make_rotating_provider, provider_shared_identity

logger = logging.getLogger("core.proxy.broker")

# GUI jobs use different threads/event loops. Protect only synchronous bookkeeping
# with this mutex; never hold it across network I/O or an await.
_SHARED_LOCK = threading.Lock()
_SHARED_USAGE: Dict[str, int] = defaultdict(int)
_SHARED_ROTATING: Set[str] = set()
_SHARED_MODELS: Dict[str, ProxyModel] = {}
_SHARED_LIMITS: Dict[str, int] = {}
_SHARED_ROTATION_REQUIRED: Set[str] = set()


def _rotation_identity(key: str) -> str:
    return provider_shared_identity(make_rotating_provider(key))


def proxy_activity(key: str):
    identity = _rotation_identity(key)
    with _SHARED_LOCK:
        return (_SHARED_USAGE.get(identity, 0), identity in _SHARED_ROTATING,
                _SHARED_MODELS.get(identity))


def update_proxy_endpoint(key: str, model: ProxyModel):
    with _SHARED_LOCK:
        identity = _rotation_identity(key)
        _SHARED_MODELS[identity] = model
        _SHARED_ROTATION_REQUIRED.discard(identity)


def require_proxy_rotation(key: str):
    with _SHARED_LOCK:
        _SHARED_ROTATION_REQUIRED.add(_rotation_identity(key))


@contextmanager
def proxy_maintenance(key: str):
    """Prevent a GUI check/rotation from changing an IP held by another job."""
    identity = _rotation_identity(key)
    with _SHARED_LOCK:
        if _SHARED_USAGE.get(identity, 0) or identity in _SHARED_ROTATING:
            raise ProxyTransportError(cause='Proxy đang được job sử dụng hoặc đang xoay.', kind='proxy_in_use')
        _SHARED_ROTATING.add(identity)
    try:
        yield
    finally:
        with _SHARED_LOCK:
            _SHARED_ROTATING.discard(identity)


@dataclass
class _ProxyInternalState:
    model: ProxyModel
    in_use: int = 0
    max_slots: int = 1
    cooling_until: float = 0.0
    quarantined_until: float = 0.0
    consecutive_failures: int = 0
    total_leases: int = 0
    total_success: int = 0
    total_failures: int = 0
    provider: Optional[BaseRotatingProvider] = None
    rotating: bool = False
    shared_key: str = ""


class ProxyBroker:
    """Bộ điều phối Proxy cục bộ cho toàn hệ thống."""

    def __init__(
        self,
        static_proxies: Optional[List[ProxyModel]] = None,
        account_concurrency: int = 1,
        slot_delay_seconds: float = 0.0,
        regular_cooldown_seconds: float = 0.0,
        rate_limit_cooldown_seconds: float = 60.0,
        quarantine_failures_threshold: int = 3,
        quarantine_duration_seconds: float = 180.0,
        allow_direct_without_proxies: bool = True,
        rotating_provider: Optional[BaseRotatingProvider] = None,
    ):
        self.account_concurrency = max(1, account_concurrency)
        self.slot_delay_seconds = max(0.0, slot_delay_seconds)
        self.regular_cooldown_seconds = max(0.0, regular_cooldown_seconds)
        self.rate_limit_cooldown_seconds = max(0.0, rate_limit_cooldown_seconds)
        self.quarantine_failures_threshold = max(1, quarantine_failures_threshold)
        self.quarantine_duration_seconds = max(10.0, quarantine_duration_seconds)
        self.allow_direct_without_proxies = allow_direct_without_proxies
        self.rotating_provider = rotating_provider

        self._proxies: Dict[str, _ProxyInternalState] = {}
        self._leases: Dict[str, ProxyLease] = {}
        self._job_leases: Dict[str, Set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._round_robin_idx: int = 0

        if static_proxies:
            for p in static_proxies:
                self.add_proxy(p)
        if rotating_provider:
            key = 'rotating_' + uuid.uuid4().hex
            model = ProxyModel(host='rotating.invalid', port=1, raw=key)
            self._proxies[key] = _ProxyInternalState(model=model, max_slots=self.account_concurrency,
                provider=rotating_provider, shared_key=provider_shared_identity(rotating_provider))

    def add_proxy(self, proxy: ProxyModel, max_slots: Optional[int] = None) -> None:
        """Thêm proxy vào pool."""
        raw = proxy.raw or str(proxy)
        provider = make_rotating_provider(proxy.rotation_url, current_endpoint=proxy.to_url()) if proxy.rotation_url else None
        identity = (provider_shared_identity(provider) if provider
                    else 'static:' + hashlib.sha256(proxy.to_url().encode()).hexdigest())
        if raw in self._proxies and self._proxies[raw].shared_key != identity:
            raw += '|' + identity
        if raw not in self._proxies:
            slots = max_slots if max_slots is not None else self.account_concurrency
            self._proxies[raw] = _ProxyInternalState(model=proxy, max_slots=max(1, slots), provider=provider, shared_key=identity)

    def remove_proxy(self, raw: str) -> None:
        """Xóa proxy khỏi pool."""
        if raw in self._proxies:
            if self._proxies[raw].in_use or self._proxies[raw].rotating:
                raise RuntimeError('Không thể xóa proxy đang được cấp phát.')
            del self._proxies[raw]

    def clear(self) -> None:
        """Xóa toàn bộ proxy trong pool."""
        if any(s.in_use or s.rotating for s in self._proxies.values()):
            raise RuntimeError('Cần trả hết proxy leases trước khi xóa pool.')
        self._proxies.clear()
        self._leases.clear()
        self._job_leases.clear()

    @property
    def has_proxies(self) -> bool:
        return bool(self._proxies)

    @property
    def account_capacity(self) -> int:
        """Configured account slots, counting each shared gateway/key once.

        Current holders or cooldowns do not reduce this scheduling budget: a job
        can wait for a temporary busy slot without treating the input as empty.
        """
        capacities = {}
        for state in self._proxies.values():
            identity = state.shared_key
            capacities[identity] = min(capacities.get(identity, state.max_slots), state.max_slots)
        return sum(capacities.values())

    async def lease(
        self,
        task_id: str,
        platform_id: str = "mochichat",
        purpose: ProxyLeasePurpose = ProxyLeasePurpose.ACCOUNT,
        job_id: str = "",
        timeout: float = 300.0,
    ) -> ProxyLease:
        """Cấp phát 1 ProxyLease cho task.

        Nếu không có proxy nào và cho phép Direct, cấp phát Direct IP lease (proxy=None).
        Dùng endpoint hiện tại; chỉ xoay sau lỗi request mạng/proxy.
        Nếu chỉ có key và chưa có endpoint, lấy endpoint trước khi cấp phát.
        Nếu dùng static proxies, chọn proxy hợp lệ có sẵn slot theo round-robin.
        """
        deadline = time.monotonic() + max(0.0, timeout)
        lease_id = f'lease_{uuid.uuid4().hex[:12]}'

        def register(proxy, pool_key=None, **details):
            lease = ProxyLease(lease_id=lease_id, task_id=task_id,
                platform_id=platform_id, proxy=proxy, purpose=purpose,
                metadata={'pool_key': pool_key, 'job_id': job_id, **details})
            self._leases[lease_id] = lease
            if job_id:
                self._job_leases[job_id].add(lease_id)
            return lease

        while True:
            selected = None
            async with self._lock:
                now = time.time()
                if not self._proxies:
                    if self.allow_direct_without_proxies:
                        return register(None)
                    raise ProxyTransportError(cause='Không có proxy khả dụng và Direct bị tắt', kind='no_proxies_configured')
                eligible = [(key, state) for key, state in self._proxies.items()
                    if not state.rotating and state.quarantined_until <= now
                    and state.cooling_until <= now and state.in_use < state.max_slots]
                if eligible:
                    for offset in range(len(eligible)):
                        key, state = eligible[(self._round_robin_idx + offset) % len(eligible)]
                        with _SHARED_LOCK:
                            used = _SHARED_USAGE[state.shared_key]
                            limit = min(state.max_slots, _SHARED_LIMITS.get(state.shared_key, state.max_slots))
                            if used >= limit or state.shared_key in _SHARED_ROTATING:
                                continue
                            if state.provider and state.shared_key in _SHARED_MODELS:
                                state.model = _SHARED_MODELS[state.shared_key]
                            force_rotation = state.provider is not None and state.shared_key in _SHARED_ROTATION_REQUIRED
                            # Wait for every holder of the failed IP before changing it.
                            if force_rotation and used:
                                continue
                            has_endpoint = state.model.host != 'rotating.invalid'
                            needs_refresh = state.provider is not None and (force_rotation or not has_endpoint)
                            _SHARED_USAGE[state.shared_key] += 1
                            _SHARED_LIMITS.setdefault(state.shared_key, state.max_slots)
                            if needs_refresh:
                                _SHARED_ROTATING.add(state.shared_key)
                        self._round_robin_idx += offset + 1
                        state.in_use += 1
                        if not needs_refresh:
                            state.total_leases += 1
                            return register(state.model, key)
                        state.rotating = True
                        selected = key, state, force_rotation
                        break

            if selected:
                key, state, force_rotation = selected
                try:
                    remaining = max(0.0, deadline - time.monotonic())
                    # Rotation must never hold the broker lock: releases stay responsive.
                    result = await asyncio.wait_for(state.provider.get_current_or_rotate(
                        force_rotate=force_rotation, wait_until_rotated=True, max_wait_seconds=remaining), timeout=remaining)
                    if not result.proxy or (force_rotation and (not result.success or result.is_cooling)):
                        raise ProxyTransportError(cause=result.error or 'Proxy vẫn đang chờ xoay', kind='rotation_not_ready')
                    async with self._lock:
                        state.model = result.proxy
                        with _SHARED_LOCK:
                            _SHARED_MODELS[state.shared_key] = result.proxy
                            _SHARED_ROTATION_REQUIRED.discard(state.shared_key)
                            _SHARED_ROTATING.discard(state.shared_key)
                        state.total_leases += 1
                        state.rotating = False
                        return register(state.model, key,
                            provider_status=(result.info or {}).get('status', (result.info or {}).get('Status')), refreshed=True)
                except BaseException:
                    async with self._lock:
                        state.in_use = max(0, state.in_use - 1)
                        state.rotating = False
                        with _SHARED_LOCK:
                            _SHARED_USAGE[state.shared_key] = max(0, _SHARED_USAGE[state.shared_key] - 1)
                            _SHARED_ROTATING.discard(state.shared_key)
                            if not _SHARED_USAGE[state.shared_key]:
                                _SHARED_LIMITS.pop(state.shared_key, None)
                    raise

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError(f'Hết thời gian chờ proxy ({timeout}s) cho {task_id}')
            await asyncio.sleep(min(0.1, remaining))

    async def release(
        self,
        lease: ProxyLease,
        platform_id: str = "mochichat",
        outcome: ProxyReleaseOutcome = ProxyReleaseOutcome.SUCCESS,
        success: bool = True,
    ) -> None:
        """Hoàn trả ProxyLease vào pool và cập nhật trạng thái."""
        async with self._lock:
            lid = lease.lease_id
            recorded = self._leases.pop(lid, None)
            if recorded is None:
                return  # Releasing twice must not decrement another task's slot.
            job_id = recorded.metadata.get('job_id')
            if job_id in self._job_leases:
                self._job_leases[job_id].discard(lid)
                if not self._job_leases[job_id]:
                    del self._job_leases[job_id]

            if lease.proxy is None:
                return

            raw = recorded.metadata.get('pool_key') or lease.proxy.raw or str(lease.proxy)
            state = self._proxies.get(raw)
            if not state:
                return

            state.in_use = max(0, state.in_use - 1)
            with _SHARED_LOCK:
                _SHARED_USAGE[state.shared_key] = max(0, _SHARED_USAGE[state.shared_key] - 1)
                if state.provider and outcome in (ProxyReleaseOutcome.NETWORK_ERROR, ProxyReleaseOutcome.QUARANTINED):
                    _SHARED_ROTATION_REQUIRED.add(state.shared_key)
                if not _SHARED_USAGE[state.shared_key]:
                    _SHARED_LIMITS.pop(state.shared_key, None)
            now = time.time()

            if outcome == ProxyReleaseOutcome.SUCCESS and success:
                state.total_success += 1
                state.consecutive_failures = 0
                if self.regular_cooldown_seconds > 0:
                    state.cooling_until = now + self.regular_cooldown_seconds
            elif outcome == ProxyReleaseOutcome.RATE_LIMITED:
                state.cooling_until = now + self.rate_limit_cooldown_seconds
            elif outcome in (ProxyReleaseOutcome.NETWORK_ERROR, ProxyReleaseOutcome.QUARANTINED):
                state.total_failures += 1
                state.consecutive_failures += 1
                if state.provider:
                    # Recover rotating proxies by requesting a new IP, not by
                    # quarantining the key before it has had a chance to rotate.
                    state.quarantined_until = 0.0
                elif state.consecutive_failures >= self.quarantine_failures_threshold:
                    state.quarantined_until = now + self.quarantine_duration_seconds
                    logger.warning(
                        "Proxy %s liên tục lỗi %d lần, tạm cách ly trong %ds.",
                        raw,
                        state.consecutive_failures,
                        int(self.quarantine_duration_seconds),
                    )

    async def report_failure(self, lease: ProxyLease, reason: str, platform_id: str = "mochichat") -> None:
        """Ghi nhận lỗi từ một lease."""
        await self.release(
            lease,
            platform_id=platform_id,
            outcome=ProxyReleaseOutcome.NETWORK_ERROR,
            success=False,
        )

    async def release_job(self, job_id: str, platform_id: str = "mochichat") -> None:
        """Giải phóng toàn bộ proxy leases thuộc một job cụ thể."""
        async with self._lock:
            lease_ids = list(self._job_leases.get(job_id, set()))
            if job_id in self._job_leases:
                del self._job_leases[job_id]

        for lid in lease_ids:
            lease = self._leases.get(lid)
            if lease:
                await self.release(lease, platform_id=platform_id, outcome=ProxyReleaseOutcome.ABORTED, success=False)

    def get_snapshot(self) -> ProxyPoolSnapshot:
        """Tạo ảnh chụp trạng thái hiện tại của pool."""
        snaps: List[ProxyStateSnapshot] = []
        for raw, s in self._proxies.items():
            snaps.append(
                ProxyStateSnapshot(
                    raw=raw,
                    is_active=True,
                    in_use=s.in_use,
                    max_slots=s.max_slots,
                    cooling_until=s.cooling_until,
                    quarantined_until=s.quarantined_until,
                    consecutive_failures=s.consecutive_failures,
                )
            )
        return ProxyPoolSnapshot(proxies=snaps, timestamp=time.time())

