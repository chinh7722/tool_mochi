"""1TrieuMail.com Mail Shop Provider.

Tích hợp API mua tài khoản Gmail tự động từ dịch vụ 1TrieuMail.com (https://1trieumail.com/).
Hỗ trợ khóa tuần tự cấp class (_buy_lock) và deduplication cache để chống race condition.
"""

import asyncio
from dataclasses import dataclass
import logging
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Set, Union

try:
    from curl_cffi.requests import AsyncSession
except ImportError:
    AsyncSession = Any

from core.proxy.models import curl_proxy_options
from core.mail.providers.purchase_policy import purchase_json
from core.mail.providers.priority import MailProductUnavailable, is_product_unavailable
from core.mail.providers.accounting import PurchaseAccounting, read_balance_units

logger = logging.getLogger("core.mail.providers.one_trieu_mail")

DEFAULT_1TRIEUMAIL_PRODUCT_IDS = [18486, 1, 2862, 3405]
DEFAULT_1TRIEUMAIL_PRODUCT_IDS_STR = "18486, 1, 2862, 3405"


def parse_1trieumail_product_ids(raw: Union[str, Sequence[Union[int, str]], int, None]) -> List[int]:
    """Chuẩn hóa danh sách ID sản phẩm 1TrieuMail.com từ chuỗi hoặc mảng số nguyên.
    Giữ nguyên thứ tự ưu tiên, loại bỏ phần tử trùng lặp và tự động quay về
    danh sách mặc định [1, 2862, 3405] nếu đầu vào rỗng.
    """
    if raw is None:
        return list(DEFAULT_1TRIEUMAIL_PRODUCT_IDS)
    if isinstance(raw, int):
        return [raw]
    tokens: List[str] = []
    if isinstance(raw, str):
        tokens = [t.strip() for t in re.split(r"[\s,;]+", raw.strip()) if t.strip()]
    elif isinstance(raw, (list, tuple)):
        tokens = [str(x).strip() for x in raw if str(x).strip()]

    result: List[int] = []
    for t in tokens:
        try:
            pid = int(t)
            if pid not in result:
                result.append(pid)
        except (ValueError, TypeError):
            continue

    return result if result else list(DEFAULT_1TRIEUMAIL_PRODUCT_IDS)


@dataclass(frozen=True, slots=True)
class GmailDomainInfo:
    username: str
    password: str


class OneTrieuMail:
    """HTTP Client giao tiếp API mua tài khoản từ 1TrieuMail.com."""

    PROFILE_INFO_ENTRYPOINT = "/profile.php"
    BUY_PRODUCT_INFO = "/buy_product"

    _buy_locks: Dict[Any, asyncio.Lock] = {}
    _seen_usernames: Set[str] = set()

    def __init__(self, api_key: str, domain: str = "1trieumail.com", *, proxy=None, on_wait=None, on_rate_limit=None,
            on_purchase=None, job_id='', purchase_route=None):
        self.api_key = api_key.strip()
        self.domain = domain
        self.proxy = proxy
        self.on_wait = on_wait
        self.on_rate_limit = on_rate_limit
        self.on_purchase, self.job_id = on_purchase, job_id
        self.purchase_route=purchase_route
        self.api_url = f"https://{domain}/api"
        self._session: Optional[Any] = None

    @classmethod
    def _get_buy_lock(cls) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = cls._buy_locks.get(loop)
        if lock is None:
            cls._buy_locks = {lp: lk for lp, lk in cls._buy_locks.items() if not getattr(lp, "is_closed", lambda: False)()}
            lock = asyncio.Lock()
            cls._buy_locks[loop] = lock
        return lock

    @classmethod
    def clear_seen_cache(cls) -> None:
        """Xóa danh sách tài khoản đã ghi nhận trong cache."""
        cls._seen_usernames.clear()

    async def __aenter__(self) -> "OneTrieuMail":
        if self._session is None:
            self._session = AsyncSession(impersonate="chrome_android", timeout=30.0, proxies=curl_proxy_options(self.proxy), trust_env=False, verify=True)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def check_balance(self) -> float:
        """Kiểm tra số dư tài khoản 1TrieuMail.com."""
        url = f"{self.api_url}{self.PROFILE_INFO_ENTRYPOINT}"
        params = {"api_key": self.api_key}
        close_session = False
        session = self._session
        if session is None:
            session = AsyncSession(impersonate="chrome_android", timeout=15.0, proxies=curl_proxy_options(self.proxy), trust_env=False, verify=True)
            close_session = True

        try:
            last_err: Optional[Exception] = None
            for attempt in range(1, 4):
                try:
                    resp = await session.get(url, params=params)
                    if not resp or not resp.text or not resp.text.strip():
                        if attempt < 3:
                            await asyncio.sleep(1.0)
                            continue
                        raise ValueError("Máy chủ 1TrieuMail.com phản hồi rỗng (vui lòng kiểm tra lại kết nối mạng hoặc thử lại sau).")

                    try:
                        data = resp.json()
                    except Exception:
                        text = (resp.text or "").strip()
                        raise ValueError(f"Máy chủ phản hồi không đúng định dạng JSON: {text[:100]}")

                    msg = str(data.get("msg", "")).lower()
                    if data.get("status") != "success" and any(k in msg for k in ["đăng nhập", "api key", "hợp lệ", "chính xác"]):
                        raise ValueError(f"API_KEY không chính xác: {data.get('msg', 'API Key không hợp lệ')}")

                    if data.get("status") != "success":
                        raise ValueError(f"Lỗi khi kiểm tra số dư: {data.get('msg', 'Không xác định')}")

                    money = 0.0
                    if isinstance(data.get("data"), dict) and "money" in data["data"]:
                        money = float(data["data"]["money"])
                    elif "money" in data:
                        money = float(data["money"])
                    elif "data" in data and isinstance(data["data"], (int, float)):
                        money = float(data["data"])
                    return money
                except Exception as e:
                    last_err = e
                    if attempt < 3:
                        await asyncio.sleep(1.0)
                        continue
                    raise last_err
            raise last_err or ValueError("Không thể kiểm tra số dư")
        finally:
            if close_session:
                await session.close()

    async def buy_product(self, product_id: int, amount: int = 1) -> List[str]:
        """Mua sản phẩm theo ID."""
        if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
            raise ValueError('Số lượng mail phải là số nguyên dương.')
        url = f"{self.api_url}{self.BUY_PRODUCT_INFO}"
        payload = {
            "action": "buyProduct",
            "api_key": self.api_key,
            "id": product_id,
            "amount": amount,
        }
        close_session = False
        session = self._session
        if session is None:
            session = AsyncSession(impersonate="chrome_android", timeout=25.0, proxies=curl_proxy_options(self.proxy), trust_env=False, verify=True)
            close_session = True

        async def renew_session():
            nonlocal session
            proxy = await self.on_rate_limit()
            if proxy is None:
                raise ValueError('Không nhận được proxy mới cho shop mail.')
            replacement = AsyncSession(impersonate="chrome_android", timeout=30.0,
                proxies=curl_proxy_options(proxy), trust_env=False, verify=True)
            previous, session = session, replacement
            self.proxy = proxy
            if not close_session:
                self._session = replacement
            try:
                await previous.close()
            except Exception:
                logger.debug('Không đóng được phiên mua mail cũ', exc_info=True)
            return replacement

        finance_options = {}
        if self.on_purchase is not None:
            accounting = PurchaseAccounting(self.domain, self.api_key, product_id,
                amount, self.on_purchase, self.job_id)
            finance_options = {
                'on_transaction': accounting.report,
                'balance_probe': lambda connection: read_balance_units(connection, self.api_url, self.api_key),
            }
        try:
            data = await purchase_json(session, url, payload, self.domain, self.api_key, self.on_wait,
                on_rate_limit=renew_session if self.proxy is not None and self.on_rate_limit is not None else None,
                purchase_route=self.purchase_route,
                **finance_options)
            if is_product_unavailable(data):
                raise MailProductUnavailable(f"Sản phẩm {product_id} hết hàng/không đủ số lượng hoặc không còn.")
            msg = str(data.get("msg", "")).lower()
            if data.get("status") != "success" and any(k in msg for k in ["đăng nhập", "api key", "hợp lệ", "chính xác"]):
                raise ValueError("Lỗi khi mua hàng: API_KEY không chính xác")

            if data.get("status") != "success":
                raise RuntimeError(f"Lỗi khi mua sản phẩm {product_id}: {data.get('msg', 'Lỗi không xác định')}")

            raw = data.get("data", [])
            if isinstance(raw, str):
                return [line.strip() for line in raw.splitlines() if line.strip()]
            elif isinstance(raw, list):
                return [str(x).strip() for x in raw if str(x).strip()]
            raise ValueError('Shop báo mua thành công nhưng dữ liệu tài khoản không hợp lệ.')
        finally:
            if close_session:
                await session.close()

    async def buy_gmail_domain_info(
        self, product_ids: Union[int, List[int], str] = None, amount: int = 1
    ) -> List[GmailDomainInfo]:
        """Mua thông tin Gmail từ 1TrieuMail.com."""
        lock = self._get_buy_lock()
        async with lock:
            return await self._buy_gmail_domain_info_locked(product_ids, amount)

    async def _buy_gmail_domain_info_locked(
        self, product_ids: Union[int, List[int], str], amount: int
    ) -> List[GmailDomainInfo]:
        pids = parse_1trieumail_product_ids(product_ids)
        last_error = ""

        for pid in pids:
            try:
                raw_items = await self.buy_product(pid, amount)
                if not raw_items:
                    raise ValueError('Shop báo mua thành công nhưng không trả về tài khoản; cần kiểm tra lịch sử giao dịch.')
                
                domain_infos: List[GmailDomainInfo] = []
                has_duplicate = False
                batch_usernames = set()

                for item in raw_items:
                    parts = [p.strip() for p in item.split("|")]
                    if len(parts) >= 2:
                        u, p = parts[0], parts[1]
                        if u.lower() in self._seen_usernames or u.lower() in batch_usernames:
                            has_duplicate = True
                            logger.warning(
                                f"⚠️ 1TrieuMail.com trả về tài khoản đã từng mua trước đó ({u}). "
                                "Dừng mua để kiểm tra lịch sử giao dịch."
                            )
                            break
                        if not u or not p:
                            raise ValueError('Dữ liệu tài khoản thiếu email hoặc mật khẩu; kiểm tra lịch sử shop.')
                        batch_usernames.add(u.lower())
                        domain_infos.append(GmailDomainInfo(username=u, password=p))

                if has_duplicate:
                    raise ValueError('Shop trả về tài khoản trùng. Dừng mua để kiểm tra lịch sử giao dịch.')

                if domain_infos:
                    for info in domain_infos:
                        self._seen_usernames.add(info.username.lower())
                    return domain_infos
                raise ValueError('Dữ liệu tài khoản đã mua không hợp lệ; cần kiểm tra lịch sử shop.')

            except MailProductUnavailable as exc:
                last_error = str(exc)
                logger.debug("Sản phẩm %s chưa mua được do thiếu hàng.", pid)
                continue
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError('Giao dịch mail không được xác nhận an toàn; dừng, không mua sản phẩm khác.') from exc

        raise MailProductUnavailable(
            f"Không có sản phẩm hợp lệ để mua (tất cả product_ids đều hết hàng hoặc lỗi): {last_error}"
        )
