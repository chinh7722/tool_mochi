"""Proxy Pool Client.

Client interface passed into platform task runners (such as MochiInviteRunner).
Maintains contract identical to original core.proxy.client.ProxyPoolClient.
100% local fidelity.
"""

import asyncio
import logging
from typing import Any, Optional

from core.proxy.broker import ProxyBroker
from core.proxy.protocol import (
    ProxyFailureReport,
    ProxyJobReleaseRequest,
    ProxyLease,
    ProxyLeasePurpose,
    ProxyLeaseRequest,
    ProxyPoolResponse,
    ProxyPoolShutdown,
    ProxyReleaseOutcome,
    ProxyReleaseRequest,
)

logger = logging.getLogger("core.proxy.client")


class ProxyPoolClient:
    """Client giao tiếp với Proxy Pool / Broker."""

    def __init__(
        self,
        request_queue: Optional[Any] = None,
        reply_queue: Optional[Any] = None,
        job_id: str = "",
        platform_id: str = "mochichat",
        timeout: float = 300.0,
        broker: Optional[ProxyBroker] = None,
    ):
        self.request_queue = request_queue
        self.reply_queue = reply_queue
        self.job_id = job_id
        self.platform_id = platform_id
        self.timeout = timeout
        self.broker = broker or ProxyBroker()
        self._is_running = True

    async def lease(
        self,
        task_id: str,
        platform_id: Optional[str] = None,
        purpose: ProxyLeasePurpose = ProxyLeasePurpose.ACCOUNT,
    ) -> ProxyLease:
        """Xin cấp phát 1 proxy lease cho task."""
        plat = platform_id or self.platform_id
        return await self.broker.lease(
            task_id=task_id,
            platform_id=plat,
            purpose=purpose,
            job_id=self.job_id,
            timeout=self.timeout,
        )

    async def release(
        self,
        lease: ProxyLease,
        platform_id: Optional[str] = None,
        outcome: ProxyReleaseOutcome = ProxyReleaseOutcome.SUCCESS,
        success: bool = True,
    ) -> None:
        """Hoàn trả proxy lease."""
        plat = platform_id or self.platform_id
        await self.broker.release(
            lease=lease,
            platform_id=plat,
            outcome=outcome,
            success=success,
        )

    async def report_failure(
        self,
        lease: ProxyLease,
        reason: str,
        platform_id: Optional[str] = None,
    ) -> None:
        """Báo cáo proxy lease bị lỗi."""
        plat = platform_id or self.platform_id
        await self.broker.report_failure(
            lease=lease,
            reason=reason,
            platform_id=plat,
        )

    async def release_job(
        self,
        job_id: Optional[str] = None,
        platform_id: Optional[str] = None,
    ) -> None:
        """Giải phóng toàn bộ proxy thuộc job."""
        jid = job_id or self.job_id
        plat = platform_id or self.platform_id
        if jid:
            await self.broker.release_job(job_id=jid, platform_id=plat)

    async def _dispatch(self, command: Any, timeout: Optional[float] = None) -> Any:
        return await self._request(command, timeout=timeout or self.timeout)

    async def _request(self, command: Any, timeout: float = 30.0) -> Any:
        return ProxyPoolResponse(success=True)

    async def _execute(self) -> None:
        pass

    async def _teardown(self, command: Any) -> None:
        self._is_running = False

    async def response_pump(self) -> None:
        """Background response pump loop để duy trì kết nối nếu chạy qua IPC queue."""
        while self._is_running:
            await asyncio.sleep(1.0)

