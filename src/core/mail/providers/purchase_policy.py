"""Pace confirmed mail purchases without repeating uncertain transactions."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import logging
import math
import threading
import time
import unicodedata
from typing import Any, Callable, Dict, Optional
from core.mail.providers.priority import is_product_unavailable


MIN_PURCHASE_INTERVAL = 1.0
MIN_FAILED_PURCHASE_INTERVAL = 10.0
MAX_PURCHASE_ATTEMPTS = 3
MAX_RETRY_WAIT = 60.0
_logger = logging.getLogger(__name__)
_registry_lock = threading.Lock()
_slots: Dict[str, "_PurchaseSlot"] = {}


class PurchaseRateLimitError(ValueError):
    """The shop explicitly rejected a purchase because requests arrived too fast."""

    def __init__(self, product_id: int, message: str, retry_after: float):
        self.product_id = product_id
        self.retry_after = retry_after
        super().__init__(f"Shop giới hạn tốc độ mua sản phẩm {product_id}: {message}")


def notify_wait(callback: Optional[Callable[[str], None]], message: str) -> None:
    if callback is not None:
        try:
            callback(message)
        except Exception:
            _logger.warning("Không thể ghi tiến trình chờ shop", exc_info=True)


def has_account_payload(data: Any) -> bool:
    """A contradictory rejection carrying accounts must never trigger another charge."""
    items = data.splitlines() if isinstance(data, str) else data if isinstance(data, list) else []
    return any(isinstance(item, str) and "|" in item for item in items)


def is_rate_limit_rejection(data: dict) -> bool:
    if "status" not in data or data["status"] == "success" or has_account_payload(data.get("data")):
        return False
    message = str(data.get("msg", "")).lower()
    plain = "".join(c for c in unicodedata.normalize("NFD", message) if not unicodedata.combining(c))
    return any(term in plain for term in (
        "thao tac qua nhanh", "qua nhieu yeu cau", "too fast", "too many requests", "rate limit",
    ))


def retry_wait(response: Any, data: dict, attempt: int) -> float:
    """Honor Retry-After; a long cooldown is returned so callers can stop safely."""
    headers = getattr(response, "headers", {}) or {}
    raw = headers.get("Retry-After", headers.get("retry-after"))
    if raw is None:
        raw = data.get("retry_after")
    delay = None
    if raw is not None:
        try:
            delay = float(raw)
        except (TypeError, ValueError):
            try:
                deadline = parsedate_to_datetime(str(raw))
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                delay = (deadline - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                pass
    if delay is None or not math.isfinite(delay) or delay < 0:
        delay = 3.0 * attempt
    return max(MIN_PURCHASE_INTERVAL, MIN_FAILED_PURCHASE_INTERVAL, delay)


async def wait_for_purchase_retry(callback=None):
    """Pace stock fallback even when the next choice uses another shop/key."""
    notify_wait(callback, f'Mua mail chưa thành công; chờ {MIN_FAILED_PURCHASE_INTERVAL:.0f}s trước yêu cầu mua tiếp theo.')
    await asyncio.sleep(MIN_FAILED_PURCHASE_INTERVAL)


class _PurchaseSlot:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.next_request_at = 0.0

    def defer(self, delay: float) -> None:
        self.next_request_at = max(self.next_request_at, time.monotonic() + delay)

    async def before_request(self, callback: Optional[Callable[[str], None]], message: Optional[str] = None) -> None:
        delay = max(0.0, self.next_request_at - time.monotonic())
        if delay:
            notify_wait(callback, message or f"Chờ {delay:.1f}s trước giao dịch mua mail tiếp theo.")
            await asyncio.sleep(delay)
        self.next_request_at = time.monotonic() + MIN_PURCHASE_INTERVAL


@asynccontextmanager
async def purchase_gate(domain: str, api_key: str, callback: Optional[Callable[[str], None]] = None):
    # Never retain API keys in this process-wide registry. Thread locks also cover
    # independent asyncio loops used by the GUI's worker threads.
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    key = f"{domain.strip().lower()}:{digest}"
    with _registry_lock:
        slot = _slots.setdefault(key, _PurchaseSlot())
    announced = False
    while not slot.lock.acquire(blocking=False):
        if not announced:
            notify_wait(callback, "Đang chờ giao dịch mua mail cùng API key hoàn tất.")
            announced = True
        await asyncio.sleep(0.1)
    try:
        yield slot
    finally:
        slot.lock.release()


@asynccontextmanager
async def _same_session(session):
    yield session


async def purchase_json(session, url, payload, domain, api_key, on_wait=None, on_rate_limit=None,
        *, balance_probe=None, on_transaction=None, purchase_route=None):
    """Retry only an explicit rejected transaction, never a lost response."""
    async with purchase_gate(domain, api_key, on_wait) as slot:
        for attempt in range(1, MAX_PURCHASE_ATTEMPTS + 1):
            await slot.before_request(on_wait)
            try:
                async with (purchase_route() if purchase_route is not None else _same_session(session)) as connection:
                    data,response=await _purchase_once(connection,url,payload,balance_probe,on_transaction)
            except Exception:
                slot.defer(MIN_FAILED_PURCHASE_INTERVAL)
                raise
            if not is_rate_limit_rejection(data):
                if data.get('status') != 'success':
                    slot.defer(MIN_FAILED_PURCHASE_INTERVAL)
                return data
            delay = retry_wait(response, data, attempt)
            message = str(data.get('msg') or 'Thao tác quá nhanh, vui lòng chờ')
            slot.defer(delay)
            if attempt == MAX_PURCHASE_ATTEMPTS or delay > MAX_RETRY_WAIT:
                raise PurchaseRateLimitError(payload['id'], message, delay)
            notify_wait(on_wait, f'Shop yêu cầu chờ {delay:.0f}s; sẽ thử lại cùng lô {payload["amount"]} mail (lần {attempt + 1}/{MAX_PURCHASE_ATTEMPTS}).')
            if purchase_route is None and on_rate_limit is not None:
                try:
                    session = await on_rate_limit()
                    if session is None:
                        raise RuntimeError('Không có phiên kết nối mới sau khi xoay proxy.')
                except Exception as exc:
                    raise ValueError(f'Không khôi phục được proxy mua mail sau giới hạn tốc độ: {exc}') from exc


async def _purchase_once(session,url,payload,balance_probe,on_transaction):
    # One route spans both balance checks, POST, and durable accounting callbacks.
    before = None
    if balance_probe is not None:
        try:
            before = await balance_probe(session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ValueError('Không lấy được số dư trước giao dịch; chưa gửi yêu cầu mua mail.') from exc

    def report_pending():
        if on_transaction is not None:
            on_transaction(None, before, None)

    async def report(data):
        after = None
        if on_transaction is not None and data is not None:
            on_transaction(data, before, None)
        if balance_probe is not None:
            try:
                after = await balance_probe(session)
            except asyncio.CancelledError:
                report_pending()
                raise
            except Exception:
                pass  # Keep an unresolved receipt; never assume zero cost.
        if on_transaction is not None:
            on_transaction(data, before, after)
        return after

    if on_transaction is not None:
        on_transaction({'_accounting_intent': True}, before, None)
    try:
        response = await session.post(url, data=payload)
    except asyncio.CancelledError:
        report_pending()
        raise
    except Exception as exc:
        await report(None)
        raise ValueError('Không xác định được trạng thái giao dịch mua mail. Kiểm tra lịch sử shop trước khi chạy lại.') from exc
    try:
        data = response.json()
    except Exception as exc:
        await report(None)
        raise ValueError('Phản hồi mua mail không phải JSON; cần kiểm tra lịch sử giao dịch trước khi chạy lại.') from exc
    if not isinstance(data, dict):
        await report(None)
        raise ValueError('Phản hồi mua mail không hợp lệ; cần kiểm tra lịch sử giao dịch.')
    if not isinstance(data.get('status'), str) or not data['status'].strip():
        await report(None)
        raise ValueError('Phản hồi mua mail thiếu trạng thái giao dịch; kiểm tra lịch sử shop trước khi chạy lại.')
    if data.get('status') != 'success' and has_account_payload(data.get('data')):
        await report(data)
        raise ValueError('Shop vừa báo lỗi vừa trả tài khoản; kiểm tra lịch sử giao dịch, không tự mua lại.')
    if not is_rate_limit_rejection(data):
        if data.get('status') == 'success':
            await report(data)
        elif balance_probe is not None and is_product_unavailable(data):
            after = await report(data)
            if after is None or after != before:
                raise ValueError('Shop báo hết hàng nhưng chưa xác nhận số dư không đổi; dừng để đối soát, không mua nguồn tiếp theo.')
        elif on_transaction is not None:
            on_transaction(data, before, before)
        return data,response
    if on_transaction is not None:
        on_transaction(data, before, before)
    return data,response
