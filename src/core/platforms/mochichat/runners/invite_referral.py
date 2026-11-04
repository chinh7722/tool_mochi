"""Mochi Chat Invite Referral Runner.

Quản lý luồng chạy tự động nhập mã mời Mochi Chat đa luồng,
quản lý concurrency động và hệ thống phát sự kiện GUI.
Hoạt động hoàn toàn ở LOCAL, không phụ thuộc Cloudflare Worker.
"""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
import logging
import time
from typing import Any, List, Optional, Tuple, Union
import uuid

from core.browser import browser_manager
from core.job_events import JobLogEvent, JobLogLevel, JobProgressEvent
from core.mail.providers.gmailvip_vn import GmailVipVn, parse_gmailvip_product_ids
from core.mail.providers.one_trieu_mail import OneTrieuMail, parse_1trieumail_product_ids
from core.mail.providers.priority import MULTI_SHOP, GMAILVIP, ONE_TRIEU, buy_priority_batch
from core.mail.batch_inventory import save_reserved_batch, write_batch, load_batch, update_batch, reserve_batch
from core.platforms.mochichat.device import DeviceInfo
from core.platforms.mochichat.fetcher import MochiChatFetcher, MochiRedeemResult
from core.platforms.mochichat.invite_quota_lock import invite_code_guard
from core.proxy.broker import ProxyBroker
from core.proxy.client import ProxyPoolClient
from core.proxy.models import ProxyModel
from core.proxy.checker import probe_proxy_connection
from core.proxy.protocol import (ProxyLease, ProxyLeasePurpose, ProxyReleaseOutcome,
    ProxyTransportError, is_request_transport_error)

logger = logging.getLogger("core.platforms.mochichat.runners.invite_referral")


@dataclass(frozen=True, slots=True)
class MochiAccountItem:
    """Đại diện một tài khoản đầu vào để chạy nhiệm vụ."""
    identifier: str
    email: str
    password: str
    token: Optional[str] = None
    master_token: Optional[str] = None
    device: Optional[DeviceInfo] = None


@dataclass(frozen=True, slots=True)
class MochiAccountResult:
    """Kết quả xử lý của một tài khoản."""
    account_identifier: str
    success: bool
    status: str
    message: Optional[str] = None
    error_code: Optional[str] = None
    data: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MochiInviteJobSummary:
    """Tổng kết toàn bộ phiên chạy job mã mời Mochi Chat."""
    job_id: str
    invite_code: str
    target_count: int
    attempted_count: int
    success_count: int
    failure_count: int
    already_redeemed_count: int
    status: str
    results: Tuple[MochiAccountResult, ...]
    error_message: Optional[str] = None


class MochiInviteRunner:
    """Bộ điều phối chạy nhiệm vụ mã mời Mochi Chat bất đồng bộ hoàn toàn ở Local."""

    def __init__(
        self,
        cloud_client: Optional[Any] = None,
        proxy_client: Optional[Any] = None,
        concurrency: int = 5,
        concurrency_limit: Optional[Any] = None,
        log_sink: Optional[Callable[[JobLogEvent], None]] = None,
        progress_sink: Optional[Callable[[JobProgressEvent], None]] = None,
        cancel_event: Optional[Any] = None,
        mail_provider: str = "gmailvip_vn",
        mail_provider_api_key: str = "",
        mail_product_ids: Optional[Union[Sequence[int], str]] = None,
        browser_headless: bool = True,
        proxies: Optional[Sequence[Any]] = None,
        google_login_via_proxy: bool = True,
        proxy_probe: Optional[Callable] = None,
        mail_via_proxy: bool = True,
        app_login_via_proxy: bool = True,
        redeem_via_proxy: bool = True,
        mail_batch_dir: Optional[Any] = None,
        mail_purchase_sink: Optional[Callable] = None,
        mail_api_keys: Optional[dict] = None,
        mail_proxy_pool=None,
        probe_invite_first: bool = True,
        invite_quota_store=None,
        failure_budget_used=0,
    ):
        self.cloud_client = cloud_client
        if type(failure_budget_used) is not int or failure_budget_used < 0:
            raise ValueError('Tổng lỗi đã ghi nhận không hợp lệ.')
        self.failure_budget_used = failure_budget_used
        self.proxy_client = proxy_client
        self.concurrency = max(1, concurrency)
        self.concurrency_limit = concurrency_limit
        self.log_sink = log_sink
        self.progress_sink = progress_sink
        self.cancel_event = cancel_event

        self.mail_provider = str(mail_provider).strip().lower()
        self.mail_provider_api_key = str(mail_provider_api_key).strip()
        self.mail_api_keys = dict(mail_api_keys or {})
        self.mail_product_ids = mail_product_ids
        self.browser_headless = browser_headless
        self.google_login_via_proxy = google_login_via_proxy
        self.mail_via_proxy = mail_via_proxy
        self.app_login_via_proxy = app_login_via_proxy
        self.redeem_via_proxy = redeem_via_proxy
        self.mail_batch_dir = mail_batch_dir
        self.mail_purchase_sink = mail_purchase_sink
        self.mail_proxy_pool=mail_proxy_pool
        self.probe_invite_first=probe_invite_first
        self.invite_quota_store=invite_quota_store
        self.mail_batch_path = None
        self.purchased_accounts = ()
        self._purchased_batch_lock = None
        self.proxy_probe = proxy_probe or probe_proxy_connection
        self.proxies = list(proxies) if proxies else []

        if self.proxy_client is None:
            models = []
            for p in self.proxies:
                if isinstance(p, ProxyModel):
                    models.append(p)
                elif isinstance(p, str) and p.strip():
                    try:
                        models.append(ProxyModel.from_string(p.strip()))
                    except Exception as exc:
                        raise ValueError('Danh sách proxy chứa endpoint không hợp lệ.') from exc
            broker = ProxyBroker(
                static_proxies=models,
                account_concurrency=1,
                allow_direct_without_proxies=not bool(self.proxies),
            )
            self.proxy_client = ProxyPoolClient(job_id="", platform_id="mochichat", broker=broker)

    def current_concurrency(self) -> int:
        """Use every configured account proxy slot; keep the manual direct limit."""
        if any((self.google_login_via_proxy, self.app_login_via_proxy, self.redeem_via_proxy)):
            broker = getattr(self.proxy_client, 'broker', None)
            capacity = getattr(broker, 'account_capacity', 0)
            if isinstance(capacity, int) and capacity > 0:
                return capacity
        return self.concurrency

    def _emit_log(
        self,
        job_id: str,
        message: str,
        level: JobLogLevel = JobLogLevel.INFO,
        account: Optional[str] = None,
        stage: str = "mochi_invite",
        emphasis: Optional[str] = None,
    ) -> None:
        try:
            evt = JobLogEvent(
                job_id=job_id,
                message=message,
                level=level,
                account=account,
                stage=stage,
                emphasis=emphasis,
            )
            if self.log_sink:
                self.log_sink(evt)
            lvl_name = level.value if hasattr(level, "value") else str(level)
            log_fn = logger.info
            if lvl_name == "DEBUG":
                log_fn = logger.debug
            elif lvl_name == "WARNING":
                log_fn = logger.warning
            elif lvl_name in ("ERROR", "CRITICAL"):
                log_fn = logger.error
            log_fn(f"[{job_id}] {message}")
        except Exception as e:
            logger.debug(f"Lỗi emit log event: {e}")

    def _emit_progress(
        self,
        job_id: str,
        target_count: int,
        attempted_count: int,
        success_count: int,
        failure_count: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        try:
            evt = JobProgressEvent(
                job_id=job_id,
                target_points=target_count * 100,
                attempted_count=attempted_count,
                success_count=success_count,
                failure_count=failure_count,
                status=status,
                error_message=error_message,
                achieved_points_value=success_count * 100,
            )
            if self.progress_sink:
                self.progress_sink(evt)
        except Exception as e:
            logger.debug(f"Lỗi emit progress event: {e}")

    def _is_cancelled(self) -> bool:
        if self.cancel_event is not None:
            if hasattr(self.cancel_event, "is_set"):
                return self.cancel_event.is_set()
            return bool(self.cancel_event)
        return False

    def _quota_snapshot(self,code):
        if self.invite_quota_store is None:
            return None
        try:
            return self.invite_quota_store.snapshot(code)
        except Exception:
            raise RuntimeError('Không đọc được bộ đếm lượt nhập mã; dừng job, chưa mua thêm mail.') from None

    def _quota_message(self,snapshot):
        from core.job_schedule import schedule_text
        message=f'Mã mời bị giới hạn 24 giờ: đã có {snapshot.count}/10 lượt nhập thành công trong 24 giờ gần nhất.'
        if snapshot.next_available_at:
            message+=' Có lượt trống trở lại từ '+schedule_text(snapshot.next_available_at)+'.'
        return message

    def _record_quota_success(self,job_id,code,item,result):
        if not result.success or self.invite_quota_store is None or result.data.get('_quota_recorded'):
            return result
        try:
            self.invite_quota_store.record_success(code,job_id,item.identifier)
            return replace(result,data={**result.data,'_quota_recorded':True})
        except Exception:
            # The service accepted the redeem. Preserve it for partial billing,
            # but never continue buying after losing the durable quota record.
            return replace(result,data={**result.data,'quota_tracking_error':True,'stop_job':True})

    async def run(self,job_id,invite_code,target_count,accounts=None,job_name=None,customer_name=None,
            unit_price=0,continued_from_job_id=None,initial_success_count=0,source_mail_batch=None):
        arguments=dict(job_id=job_id,invite_code=invite_code,target_count=target_count,accounts=accounts,
            job_name=job_name,customer_name=customer_name,unit_price=unit_price,
            continued_from_job_id=continued_from_job_id,initial_success_count=initial_success_count,
            source_mail_batch=source_mail_batch)
        if self.invite_quota_store is None:
            return await self._run_owned(**arguments)
        started=False
        try:
            async with invite_code_guard(self.invite_quota_store,invite_code,cancelled=self._is_cancelled,
                    on_wait=lambda:self._emit_log(job_id,'Mã mời đang được job khác xử lý; chờ đối chiếu lượt trước khi mua mail.')):
                started=True
                return await self._run_owned(**arguments)
        except asyncio.CancelledError:
            if started:
                raise
            return MochiInviteJobSummary(job_id,str(invite_code).strip().upper(),target_count,0,
                max(0,min(initial_success_count,target_count)),0,0,'cancelled',(),
                'Đã hủy trong khi chờ job cùng mã mời.')
        except Exception as exc:
            self._emit_log(job_id,'Không đối chiếu được bộ đếm mã mời; dừng trước khi mua thêm mail.',level=JobLogLevel.ERROR)
            return MochiInviteJobSummary(job_id,str(invite_code).strip().upper(),target_count,0,
                max(0,min(initial_success_count,target_count)),0,0,'failed',(),
                'Không đối chiếu được bộ đếm mã mời; kiểm tra dữ liệu trên VPS.')

    async def _run_owned(
        self,
        job_id: str,
        invite_code: str,
        target_count: int,
        accounts: Optional[Sequence[MochiAccountItem]] = None,
        job_name: Optional[str] = None,
        customer_name: Optional[str] = None,
        unit_price: int = 0,
        continued_from_job_id: Optional[str] = None,
        initial_success_count: int = 0,
        source_mail_batch: Optional[Any] = None,
    ) -> MochiInviteJobSummary:
        """Thực thi luồng nhập mã mời bằng tài khoản mua tự động từ GmailVip.vn hoặc 1TrieuMail.com."""
        clean_code = str(invite_code).strip().upper()
        if not clean_code:
            raise ValueError("invite_code không được để trống")
        if target_count <= 0:
            raise ValueError("target_count phải lớn hơn 0")

        base_success_count = max(0, min(initial_success_count, target_count))
        self.mail_batch_path = None
        self.purchased_accounts = ()
        self._purchased_batch_lock = None
        if isinstance(self.proxy_client, ProxyPoolClient):
            self.proxy_client.job_id = job_id
        proxy_info = "Direct IP"
        if hasattr(self.proxy_client, "broker") and self.proxy_client.broker.has_proxies:
            proxy_info = "Quản lý qua Proxy Broker"
        elif self.proxies:
            proxy_info = f"Proxy: {len(self.proxies)} proxy"

        if base_success_count > 0:
            self._emit_log(
                job_id,
                f"Tiếp tục Job Mochi Chat: Mã mời [{clean_code}], còn thiếu {target_count - base_success_count} tài khoản. Số luồng tối đa: {self.current_concurrency()} ({proxy_info})",
                level=JobLogLevel.INFO,
            )
        else:
            self._emit_log(
                job_id,
                f"Bắt đầu Job Mochi Chat: Mã mời [{clean_code}], mục tiêu: {target_count}. Số luồng tối đa: {self.current_concurrency()} ({proxy_info})",
                level=JobLogLevel.INFO,
            )

        latest_success_ref = [base_success_count]
        source_batch_lock = None

        try:
            if self.failure_budget_used >= 5 and base_success_count < target_count:
                return MochiInviteJobSummary(job_id,clean_code,target_count,0,base_success_count,0,0,
                    'failed',(),'Đạt giới hạn tổng 5 lỗi trong job.')
            quota=self._quota_snapshot(clean_code)
            if quota is not None:
                self._emit_log(job_id,f'Mã mời [{clean_code}]: {quota.count}/10 lượt thành công trong 24 giờ gần nhất, còn {quota.remaining} lượt.')
                if quota.limit_reached and base_success_count<target_count:
                    message=self._quota_message(quota)
                    self._emit_log(job_id,message,level=JobLogLevel.WARNING)
                    self._emit_progress(job_id,target_count,0,base_success_count,0,'invite_limit_reached',error_message=message)
                    return MochiInviteJobSummary(job_id,clean_code,target_count,0,base_success_count,0,0,
                        'invite_limit_reached',(),message)
            if source_mail_batch is not None:
                source_batch_lock = reserve_batch(source_mail_batch)
                current_accounts = load_batch(source_mail_batch)
                if accounts is not None:
                    requested = {item.email.lower() for item in accounts}
                    current_accounts = tuple(item for item in current_accounts if item.email.lower() in requested)
                accounts = current_accounts
                self.mail_batch_path = source_mail_batch
                self._emit_log(job_id, f'Dùng mail từ lô đã mua: {source_mail_batch}; không mua mail mới.')
            summary = await self._run_invite_loop(
                job_id=job_id,
                clean_code=clean_code,
                target_count=target_count,
                initial_success_count=base_success_count,
                latest_success_ref=latest_success_ref,
                accounts=accounts,
            )
            if self.mail_batch_path:
                try:
                    if source_mail_batch is not None:
                        update_batch(self.mail_batch_path, job_id, summary.results)
                    else:
                        write_batch(self.mail_batch_path, job_id, self.purchased_accounts, summary.results)
                except (OSError, ValueError, TypeError) as exc:
                    self._emit_log(job_id, f'Không cập nhật được trạng thái lô mail: {exc}', level=JobLogLevel.WARNING)
            return summary
        except asyncio.CancelledError:
            self._emit_log(job_id, "Người dùng đã hủy nhiệm vụ.", level=JobLogLevel.WARNING)
            return MochiInviteJobSummary(
                job_id=job_id,
                invite_code=clean_code,
                target_count=target_count,
                attempted_count=0,
                success_count=latest_success_ref[0],
                failure_count=0,
                already_redeemed_count=0,
                status="cancelled",
                results=(),
                error_message="Người dùng đã hủy nhiệm vụ.",
            )
        except Exception as exc:
            self._emit_log(job_id, f"Lỗi thực thi Job: {exc}", level=JobLogLevel.ERROR)
            return MochiInviteJobSummary(
                job_id=job_id,
                invite_code=clean_code,
                target_count=target_count,
                attempted_count=0,
                success_count=latest_success_ref[0],
                failure_count=0,
                already_redeemed_count=0,
                status="failed",
                results=(),
                error_message=str(exc),
            )
        finally:
            if source_batch_lock is not None:
                source_batch_lock.release()
            if self._purchased_batch_lock is not None:
                self._purchased_batch_lock.release()
                self._purchased_batch_lock = None
            # Release all browser contexts belonging to this job
            await browser_manager.release_by_owner(job_id)
            if self.proxy_client:
                await self.proxy_client.release_job(job_id, platform_id='mochichat')

    async def _run_invite_loop(
        self,
        job_id: str,
        clean_code: str,
        target_count: int,
        initial_success_count: int,
        latest_success_ref: List[int],
        accounts: Optional[Sequence[MochiAccountItem]] = None,
    ) -> MochiInviteJobSummary:
        results: List[MochiAccountResult] = []
        error_msg = None
        active_tasks = set()
        success_count = initial_success_count
        failure_count = already_redeemed_count = attempted_count = consecutive_failures = 0
        terminal_status = None
        quota_tracking_failed=False
        cancelled = False
        provided = list(accounts) if accounts is not None else None
        account_idx = 0
        probe_mode=provided is None and self.probe_invite_first
        probe_passed=False
        purchase_round=0
        remaining_spares=[]
        may_replenish = accounts is None
        covered_failures = 0
        replacement_amount = None

        def progress():
            self._emit_progress(job_id, target_count, attempted_count, success_count, failure_count+already_redeemed_count, 'running')

        async def buy_accounts(assigned_proxy=None,rotate_mail_proxy=None):
            nonlocal provided,account_idx,terminal_status,error_msg,covered_failures,replacement_amount
            provider_type = OneTrieuMail if '1trieu' in self.mail_provider else GmailVipVn
            amount=(1 if probe_mode and purchase_round==0 else
                target_count-success_count-len(remaining_spares) if probe_mode else target_count-success_count)
            if replacement_amount is not None:
                amount = min(amount, replacement_amount)
                replacement_amount = None
            quota=self._quota_snapshot(clean_code)
            if quota is not None:
                if quota.limit_reached:
                    terminal_status='invite_limit_reached'
                    error_msg=self._quota_message(quota)
                    return None
                amount=min(amount,max(0,quota.remaining-len(remaining_spares)))
                if amount<=0:
                    provided=list(remaining_spares)
                    account_idx=1
                    return provided[0] if provided else None
            route=('qua Proxy mua mail riêng' if self.mail_proxy_pool is not None and self.mail_via_proxy else
                'qua proxy' if assigned_proxy and self.mail_via_proxy else 'bằng mạng trực tiếp')
            action='mua 1 mail để thử mã mời trước' if probe_mode and purchase_round==0 else f'mua lô còn lại {amount} tài khoản Gmail'
            self._emit_log(job_id, f'Đang {action} {route}...', level=JobLogLevel.INFO)

            try:
                accounting_options = ({'on_purchase': self.mail_purchase_sink, 'job_id': job_id}
                    if self.mail_purchase_sink is not None else {})
                purchase_options = dict(proxy=assigned_proxy if self.mail_via_proxy else None,
                    on_wait=lambda msg: self._emit_log(job_id, msg),
                    on_rate_limit=rotate_mail_proxy if assigned_proxy and self.mail_via_proxy else None,
                    **accounting_options)
                if self.mail_proxy_pool is not None and self.mail_via_proxy:
                    purchase_options.update(proxy=None,on_rate_limit=None,purchase_route=self.mail_proxy_pool.session)
                if self.mail_provider == MULTI_SHOP:
                    purchased = await buy_priority_batch(self.mail_product_ids, self.mail_api_keys, amount,
                        provider_types={GMAILVIP: GmailVipVn, ONE_TRIEU: OneTrieuMail},
                        on_choice=lambda msg: self._emit_log(job_id, msg), **purchase_options)
                else:
                    async with provider_type(self.mail_provider_api_key, **purchase_options) as client:
                        purchased = await client.buy_gmail_domain_info(self.mail_product_ids, amount=amount)
            except Exception as exc:
                terminal_status = 'failed'
                error_msg = f'Dừng job do shop mail báo lỗi: {exc}'
                raise
            if not purchased:
                raise RuntimeError('Shop mail không trả về tài khoản.')
            received = tuple(MochiAccountItem(identifier=info.username,
                email=info.username, password=info.password, device=DeviceInfo()) for info in purchased)
            self.purchased_accounts += received
            covered_failures = failure_count + already_redeemed_count
            # Save the entire paid batch, including any extra delivery, before processing.
            if self.mail_batch_path is None:
                self.mail_batch_path, self._purchased_batch_lock = save_reserved_batch(
                    job_id, self.purchased_accounts, self.mail_batch_dir)
            else:
                write_batch(self.mail_batch_path,job_id,self.purchased_accounts,results)
            provided = (list(remaining_spares)+list(received[:amount])
                if probe_mode and purchase_round>0 else list(received[:amount]))
            account_idx = 1
            self._emit_log(job_id, f'Đã mua {len(purchased)}/{amount} mail. Lưu lô tại {self.mail_batch_path}.')
            if len(purchased) != amount:
                self._emit_log(job_id, 'Số mail shop trả về khác số yêu cầu; chỉ dùng mail đã nhận, không tự mua bù.', level=JobLogLevel.WARNING)
            return provided[0]

        async def run_one():
            nonlocal success_count, failure_count, already_redeemed_count, attempted_count
            nonlocal consecutive_failures, terminal_status, error_msg, account_idx, provided
            nonlocal probe_passed
            nonlocal quota_tracking_failed
            if self._is_cancelled() or terminal_status:
                return
            item = None
            if provided is not None:
                if account_idx >= len(provided):
                    return
                item = provided[account_idx]
                account_idx += 1
            task_ident = f'{job_id}:task_{uuid.uuid4().hex[:8]}'
            lease = None
            outcome = ProxyReleaseOutcome.ABORTED
            succeeded = False
            account_started = False
            try:
                if item is None and self.mail_proxy_pool is not None and self.mail_via_proxy:
                    item=await buy_accounts()
                    if self._is_cancelled() or terminal_status or item is None:
                        return
                needs_proxy = any((self.mail_via_proxy if provided is None and self.mail_proxy_pool is None else False,
                    self.google_login_via_proxy, self.app_login_via_proxy, self.redeem_via_proxy))
                if self.proxy_client and needs_proxy:
                    # Account proxies protect Google/Mochi; mail has its own route.
                    try:
                        lease = await self.proxy_client.lease(task_ident, platform_id='mochichat', purpose=ProxyLeasePurpose.ACCOUNT)
                    except Exception as exc:
                        terminal_status = 'failed'
                        error_msg = f'Không cấp được proxy: {exc}'
                        raise RuntimeError(error_msg) from exc
                if self._is_cancelled() or terminal_status:
                    return
                assigned_proxy = lease.proxy if lease else None

                async def replace_proxy(release_outcome=ProxyReleaseOutcome.NETWORK_ERROR, reason='lỗi request'):
                    nonlocal lease, assigned_proxy, terminal_status, error_msg
                    if lease is None or lease.is_direct:
                        raise RuntimeError('Không có proxy lease để khôi phục.')
                    previous_server = lease.proxy.server
                    failed_lease, lease = lease, None
                    await self.proxy_client.release(failed_lease, platform_id='mochichat',
                        outcome=release_outcome, success=False)
                    self._emit_log(job_id, f'Đang chờ cấp lại proxy sau {reason}...',
                        level=JobLogLevel.WARNING, account=item.identifier if item else None)
                    if self._is_cancelled():
                        raise asyncio.CancelledError()
                    try:
                        lease = await self.proxy_client.lease(task_ident, platform_id='mochichat',
                            purpose=ProxyLeasePurpose.ACCOUNT)
                        if lease.is_direct:
                            raise RuntimeError('Proxy mới không hợp lệ; không tự chuyển API sang Direct.')
                    except Exception as exc:
                        terminal_status = 'failed'
                        error_msg = f'Không khôi phục được proxy: {exc}'
                        raise
                    assigned_proxy = lease.proxy
                    state = 'endpoint giữ nguyên' if assigned_proxy.server == previous_server else 'endpoint thay đổi'
                    provider_status = lease.metadata.get('provider_status')
                    details = f', provider status={provider_status}' if provider_status is not None else ''
                    self._emit_log(job_id, f'Proxy được cấp lại: {assigned_proxy.server} ({state}{details}); sẽ kiểm tra IP qua proxy.',
                        account=item.identifier if item else None)
                    return assigned_proxy

                async def check_proxy():
                    self._emit_log(job_id, f'Kiểm tra IP qua proxy {assigned_proxy.server}...',
                        account=item.identifier if item else None)
                    health = await self.proxy_probe(assigned_proxy)
                    if health.success:
                        if health.exit_ip:
                            self._emit_log(job_id, f'Đã lấy IP qua proxy: {health.exit_ip}, {health.latency_ms} ms, nguồn {health.source_api}. Phép kiểm tra IP chưa xác nhận HTTPS tới Mochi.',
                                account=item.identifier if item else None)
                        else:
                            self._emit_log(job_id, f'Kiểm tra proxy thành công ({health.latency_ms} ms).',
                                account=item.identifier if item else None)
                        if health.dns_fallback:
                            self._emit_log(job_id, f'Dùng DNS phân giải từ máy tới {health.target_address}; API vẫn đi qua proxy và xác minh TLS theo tên miền Mochi.',
                                account=item.identifier if item else None)
                    else:
                        self._emit_log(job_id, f'Kiểm tra proxy thất bại ở bước {health.stage}: {health.error}',
                            level=JobLogLevel.WARNING, account=item.identifier if item else None)
                    return health

                if assigned_proxy:
                    for preflight in range(2):
                        health = await check_proxy()
                        if health.success:
                            break
                        if preflight == 0 and health.transport_error:
                            await replace_proxy()
                            continue
                        outcome = (ProxyReleaseOutcome.NETWORK_ERROR if health.transport_error
                                   else ProxyReleaseOutcome.BUSINESS_FAILURE)
                        purchase_state = ('Chưa mua mail hoặc đăng nhập tài khoản.' if provided is None
                            else 'Không bắt đầu đăng nhập tài khoản này.')
                        raise ProxyTransportError(proxy=assigned_proxy.server,
                            cause=f'{health.error} {purchase_state}', kind='preflight_failed')

                if item is None:
                    async def rotate_mail_proxy():
                        nonlocal outcome
                        self._emit_log(job_id, 'Shop giới hạn tốc độ mua; đang xoay proxy trước khi thử lại cùng lô mail.',
                            level=JobLogLevel.WARNING)
                        await replace_proxy(release_outcome=ProxyReleaseOutcome.QUARANTINED,
                            reason='giới hạn tốc độ mua mail')
                        health = await check_proxy()
                        if not health.success:
                            outcome = (ProxyReleaseOutcome.NETWORK_ERROR if health.transport_error
                                else ProxyReleaseOutcome.BUSINESS_FAILURE)
                            raise ProxyTransportError(proxy=assigned_proxy.server,
                                cause=health.error, kind='mail_recovery_probe_failed')
                        return assigned_proxy

                    item=await buy_accounts(assigned_proxy,rotate_mail_proxy)
                if self._is_cancelled() or terminal_status:
                    return
                attempted_count += 1
                account_started = True

                async def recover_proxy():
                    self._emit_log(job_id, 'Lỗi request API: đổi IP và tiếp tục tài khoản hiện tại, giữ nguyên token Google.',
                        level=JobLogLevel.WARNING, account=item.identifier)
                    await replace_proxy()
                    health = await check_proxy()
                    if not health.success:
                        raise ProxyTransportError(proxy=assigned_proxy.server, cause=health.error, kind='recovery_probe_failed')
                    return assigned_proxy

                result = await self._process_account(job_id, clean_code, item, assigned_proxy, task_ident,
                    recover_proxy=recover_proxy if assigned_proxy else None)
                result=self._record_quota_success(job_id,clean_code,item,result)
                results.append(result)
                succeeded = result.success
                outcome = ProxyReleaseOutcome.SUCCESS if succeeded else ProxyReleaseOutcome.BUSINESS_FAILURE
                if result.status == 'network_error' and result.data.get('proxy_used') is not False:
                    outcome = ProxyReleaseOutcome.NETWORK_ERROR
                    if assigned_proxy:
                        attempt_text = f" sau {result.data['api_attempts']} lần gửi API" if result.data.get('api_attempts') else ''
                        recovery_text = ('; đã yêu cầu cấp lại proxy' if result.data.get('proxy_recovery_attempted')
                            else '; chưa yêu cầu cấp lại proxy')
                        message = (f'Kết nối proxy vẫn lỗi{attempt_text}{recovery_text}; dừng job, giữ lại mail chưa dùng trong lô đã lưu.'
                            if result.data.get('stop_job') else
                            'Request qua proxy bị lỗi; sẽ đổi IP trước khi cấp proxy cho tác vụ tiếp theo.')
                        self._emit_log(job_id, message,
                            level=JobLogLevel.WARNING, account=item.identifier)
                if succeeded:
                    success_count += 1
                    latest_success_ref[0] = success_count
                    consecutive_failures = 0
                    if probe_mode and purchase_round==0:
                        probe_passed=True
                    if result.data.get('quota_tracking_error'):
                        quota_tracking_failed=True
                        terminal_status='failed'
                        error_msg='Đã nhập mã thành công nhưng chưa ghi được bộ đếm lượt nhập; dừng để đối soát, không mua thêm mail.'
                elif result.status == 'already_redeemed':
                    already_redeemed_count += 1
                    consecutive_failures += 1
                else:
                    failure_count += 1
                    consecutive_failures += 1
                    if result.status in ('invalid_code', 'invite_limit_reached'):
                        terminal_status = result.status
                        error_msg = result.message or result.status
                    elif result.data.get('quota_tracking_error'):
                        terminal_status = 'failed'
                        error_msg = result.message or 'Dừng job sau khi phục hồi kết nối không thành công.'
                if not terminal_status and (consecutive_failures >= 3 or self.failure_budget_used + failure_count + already_redeemed_count >= 5):
                    terminal_status = 'failed'
                    error_msg = ('Đạt giới hạn 3 lỗi liên tiếp.' if consecutive_failures >= 3
                        else 'Đạt giới hạn tổng 5 lỗi trong job.')
                message = (f'Nhập mã thành công ({success_count}/{target_count})' if succeeded
                           else f'{result.status}: {result.message}')
                self._emit_log(job_id, message, account=item.identifier,
                    level=JobLogLevel.SUCCESS if succeeded else JobLogLevel.WARNING)
            except Exception as exc:
                if account_started:
                    failure_count += 1
                    consecutive_failures += 1
                    results.append(MochiAccountResult(item.identifier, False, 'processing_error', str(exc)))
                    if consecutive_failures >= 3 or self.failure_budget_used + failure_count + already_redeemed_count >= 5:
                        terminal_status = 'failed'
                        error_msg = ('Đạt giới hạn 3 lỗi liên tiếp.' if consecutive_failures >= 3
                            else 'Đạt giới hạn tổng 5 lỗi trong job.')
                else:
                    terminal_status = 'failed'
                    error_msg = str(exc)
                self._emit_log(job_id, f'Tác vụ thất bại: {exc}', level=JobLogLevel.ERROR,
                    account=item.identifier if item else None)
            finally:
                if self.proxy_client and lease:
                    await self.proxy_client.release(lease, platform_id='mochichat', outcome=outcome, success=succeeded)
                progress()

        progress()
        try:
            while success_count < target_count and not self._is_cancelled() and not terminal_status:
                quota=self._quota_snapshot(clean_code)
                if quota is not None and quota.limit_reached and not active_tasks:
                    terminal_status='invite_limit_reached'
                    error_msg=self._quota_message(quota)
                    break
                if probe_mode and purchase_round==0 and probe_passed and not active_tasks:
                    purchase_round=1
                    goal=min(target_count-success_count,quota.remaining) if quota is not None else target_count-success_count
                    used = {r.account_identifier for r in results}
                    remaining_spares=[a for a in self.purchased_accounts if a.identifier not in used][:goal]
                    if len(remaining_spares)>=goal:
                        provided=remaining_spares
                    else:
                        provided=None
                    account_idx=0
                    self._emit_log(job_id,f'Mã mời đã thử thành công; còn {target_count-success_count} lượt, '
                        f'sẽ mua phần còn thiếu sau khi tính {len(remaining_spares)} mail shop đã giao dư.')
                if consecutive_failures >= 3 or self.failure_budget_used + failure_count + already_redeemed_count >= 5:
                    terminal_status = 'failed'
                    error_msg = 'Đạt giới hạn 3 lỗi liên tiếp hoặc tổng 5 lỗi trong job.'
                    break
                if provided is not None and account_idx >= len(provided) and not active_tasks and may_replenish:
                    used = {r.account_identifier for r in results}
                    spare = [a for a in self.purchased_accounts if a.identifier not in used]
                    if spare:
                        goal = 1 if probe_mode and purchase_round==0 else target_count-success_count
                        provided = spare[:goal]
                        account_idx = 0
                        self._emit_log(job_id, f'Dùng {len(provided)} mail đã mua còn dư trước khi mua bù.')
                    credit = failure_count + already_redeemed_count - covered_failures
                    if not spare and credit > 0:
                        replacement_amount = min(target_count-success_count, credit)
                        provided = None
                        account_idx = 0
                        remaining_spares = []
                        self._emit_log(job_id, f'Mua bù {replacement_amount} mail cho lượt lỗi; '
                            f'đã lỗi {self.failure_budget_used+failure_count+already_redeemed_count}/5, liên tiếp {consecutive_failures}/3.')
                # Outstanding tasks already reserve part of the remaining target.
                available = min(self.current_concurrency() - len(active_tasks),
                    target_count - success_count - len(active_tasks))
                if quota is not None:
                    available=min(available,quota.remaining-len(active_tasks))
                if provided is not None:
                    available = min(available, len(provided) - account_idx)
                else:
                    # Only one worker may buy the batch. Other workers start
                    # consuming it once the shop response has been saved.
                    available = min(available, 1) if not active_tasks else 0
                for _ in range(max(0, available)):
                    active_tasks.add(asyncio.create_task(run_one()))
                if not active_tasks:
                    if provided is not None and account_idx >= len(provided):
                        error_msg = 'Đã hết tài khoản đầu vào trước khi đạt mục tiêu.'
                    break
                done, active_tasks = await asyncio.wait(active_tasks, timeout=0.2,
                    return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()  # Do not silently discard worker exceptions.
        except asyncio.CancelledError:
            cancelled = True
        finally:
            for task in active_tasks:
                task.cancel()
            if active_tasks:
                await asyncio.gather(*active_tasks, return_exceptions=True)
            if self.proxy_client:
                await self.proxy_client.release_job(job_id, platform_id='mochichat')

        if quota_tracking_failed or terminal_status == 'failed':
            final_status='failed'
        elif success_count >= target_count:
            final_status = 'completed'
        elif cancelled or self._is_cancelled():
            final_status = 'cancelled'
        else:
            final_status = terminal_status or 'failed'
        total_failures = failure_count + already_redeemed_count
        self._emit_log(job_id, f'Kết thúc job: {final_status}, thành công {success_count}/{target_count}, lỗi {total_failures}.')
        self._emit_progress(job_id, target_count, attempted_count, success_count, total_failures,
            final_status, error_message=error_msg)
        return MochiInviteJobSummary(job_id=job_id, invite_code=clean_code,
            target_count=target_count, attempted_count=attempted_count, success_count=success_count,
            failure_count=total_failures, already_redeemed_count=already_redeemed_count,
            status=final_status, results=tuple(results), error_message=error_msg)

    async def _process_account(
        self,
        job_id: str,
        invite_code: str,
        item: MochiAccountItem,
        proxy: Optional[Any],
        owner_id: str,
        recover_proxy: Optional[Callable] = None,
    ) -> MochiAccountResult:
        """Xử lý nhập mã mời cho 1 tài khoản duy nhất (không có cơ chế thử lại)."""
        if not item.email or not item.password:
            return MochiAccountResult(
                account_identifier=item.identifier,
                success=False,
                status="auth_failed",
                message="Tài khoản thiếu email hoặc password để đăng nhập Google.",
            )

        if self._is_cancelled():
            return MochiAccountResult(
                account_identifier=item.identifier,
                success=False,
                status="cancelled",
                message="Đã hủy bởi người dùng",
            )

        if proxy:
            proxy_str = proxy.server if hasattr(proxy, "server") else str(proxy)
            self._emit_log(job_id, f"🌐 Sử dụng proxy: {proxy_str}", level=JobLogLevel.INFO, account=item.identifier)

        fetcher = MochiChatFetcher(
            device=item.device or DeviceInfo(),
            proxy=proxy,
            app_login_via_proxy=self.app_login_via_proxy,
            redeem_via_proxy=self.redeem_via_proxy,
        )

        def _on_step(step_num: int, msg: str) -> None:
            self._emit_log(
                job_id,
                f"[{step_num}/3] {msg}",
                level=JobLogLevel.INFO,
                account=item.identifier,
            )

        confirmed_result=None
        try:
            # Step 1 & 2: Local Google login + Mochi backend login
            login_res = await fetcher.login(
                owner_id=owner_id,
                email=item.email,
                password=item.password,
                proxy=proxy,
                on_step=_on_step,
                headless=self.browser_headless,
                google_login_via_proxy=self.google_login_via_proxy,
                recover_proxy=recover_proxy,
            )

            if not login_res.get("ok"):
                err_reason = str(login_res.get("error", "Unknown"))
                if login_res.get('diagnostic'):
                    err_reason += f". Chẩn đoán HTTPS: {login_res['diagnostic']}"
                is_net_err = login_res.get('transport_error', is_request_transport_error(err_reason))
                status_val = "network_error" if is_net_err else "auth_failed"
                if login_res.get('code') == 'TLS_CERTIFICATE_ERROR':
                    status_val = 'tls_error'
                return MochiAccountResult(
                    account_identifier=item.identifier,
                    success=False,
                    status=status_val,
                    message=f"Lỗi đăng nhập/kích hoạt Mochi: {err_reason}",
                    data={'stop_job': bool(login_res.get('stop_job')),
                        'proxy_used': login_res.get('proxy_used'),
                        'api_attempts': login_res.get('attempts'),
                        'proxy_recovery_attempted': login_res.get('proxy_recovery_attempted')},
                )

            # Step 3: Redeem invite code
            if self._is_cancelled():
                return MochiAccountResult(account_identifier=item.identifier, success=False,
                    status='cancelled', message='Đã hủy bởi người dùng')
            route = 'qua proxy' if fetcher.proxy and self.redeem_via_proxy else 'bằng mạng trực tiếp'
            _on_step(3, f"🎁 Đang gửi yêu cầu nhập mã mời [{invite_code}] {route}...")
            redeem_res = await fetcher.redeem_invite_code(invite_code)
            result=self._parse_redeem_result(item.identifier, redeem_res)
            result=self._record_quota_success(job_id,invite_code,item,result)
            if result.success:
                confirmed_result=result
            return result

        except Exception as ex:
            logger.exception(f"Ngoại lệ khi xử lý account {item.identifier}: {ex}")
            return MochiAccountResult(
                account_identifier=item.identifier,
                success=False,
                status=("network_error" if is_request_transport_error(ex)
                        and not (isinstance(ex, ProxyTransportError) and ex.proxy is None) else "exception"),
                message=f"Lỗi kết nối/ngoại lệ: {ex}",
            )
        finally:
            try:
                try:
                    await fetcher.close()
                except (Exception,asyncio.CancelledError):
                    if confirmed_result is None:
                        raise
                    self._emit_log(job_id,'Đã xác nhận nhập mã thành công; có lỗi khi đóng phiên tài khoản.',level=JobLogLevel.WARNING)
            finally:
                try:
                    await browser_manager.release_by_owner(owner_id)
                except (Exception,asyncio.CancelledError):
                    if confirmed_result is None:
                        raise
                    self._emit_log(job_id,'Đã xác nhận nhập mã thành công; có lỗi khi dọn trình duyệt.',level=JobLogLevel.WARNING)

    def _parse_redeem_result(
        self, identifier: str, res: MochiRedeemResult
    ) -> MochiAccountResult:
        if res.success:
            return MochiAccountResult(
                account_identifier=identifier,
                success=True,
                status="success",
                message=res.message,
                data=res.data,
            )

        err_code = res.error_code or ""
        msg = res.message or ""
        msg_lower = msg.lower()

        if err_code == "NETWORK_ERROR":
            status = "network_error"
        elif err_code == "INVITE_LIMIT_REACHED" or "rolling 24-hour" in msg_lower or "giới hạn" in msg_lower:
            status = "invite_limit_reached"
        elif err_code == "ALREADY_REDEEMED" or "already" in msg_lower or "đã từng" in msg_lower:
            status = "already_redeemed"
        elif err_code == "INVALID_CODE" or "invalid" in msg_lower or "không hợp lệ" in msg_lower:
            status = "invalid_code"
        else:
            status = "failed"

        return MochiAccountResult(
            account_identifier=identifier,
            success=False,
            status=status,
            message=msg,
            error_code=err_code,
            data=res.data,
        )
