"""Central Browser Lifecycle Manager.

Manages isolated browser contexts for account SSO and automation using Playwright.
Automatically utilizes local Google Chrome installation or Playwright Chromium.
Supports proxy routing and context ownership isolation.
"""

import asyncio
import logging
import os
import socket
import threading
from collections import defaultdict
from typing import Any, Dict, Optional, Set
from urllib.parse import urlsplit

try:
    from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright
except ImportError:
    try:
        from patchright.async_api import Browser, BrowserContext, Playwright, async_playwright
    except ImportError:
        Browser = Any
        BrowserContext = Any
        Playwright = Any
        async_playwright = None

logger = logging.getLogger("core.browser.manager")


def _find_free_port() -> int:
    """Tìm 1 cổng TCP trống trên 127.0.0.1 để dùng cho CDP/Debug."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _find_system_chrome() -> Optional[str]:
    """Tìm đường dẫn Chrome/Edge cài sẵn trên máy."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def resolve_browser_headless(headless: Optional[bool] = None, settings: Any = None) -> bool:
    """Xác định chế độ headless: ưu tiên tham số truyền vào, fallback đọc QSettings, mặc định True."""
    if headless is not None:
        return headless
    if settings is not None:
        val = settings.value("runtime/browser_worker_headless", None)
        if val is None:
            val = settings.value("runtime/captcha_headless", True)
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() not in {"", "0", "false", "no", "off"}
    return True


class CentralBrowserManager:
    """Quản lý trình duyệt trung tâm, cấp phát BrowserContext độc lập cho từng owner/account/job.
    Hỗ trợ đa luồng và đa event loop hoàn toàn độc lập mà không bị kẹt khi dừng hoặc đổi job.
    """

    def __init__(self):
        self._server_lock = threading.Lock()
        self._chrome_path = _find_system_chrome()
        self._loop_resources: Dict[Any, Dict[str, Any]] = {}
        self._owner_contexts: Dict[str, Set[BrowserContext]] = defaultdict(set)
        self._context_to_owner: Dict[BrowserContext, str] = {}
        self._context_to_loop: Dict[BrowserContext, Any] = {}

    def _get_loop_state(self) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        with self._server_lock:
            dead = [lp for lp in self._loop_resources if getattr(lp, "is_closed", lambda: False)()]
            for d in dead:
                self._loop_resources.pop(d, None)

            if loop not in self._loop_resources:
                self._loop_resources[loop] = {
                    "lock": asyncio.Lock(),
                    "pw": None,
                    "browsers": {},
                }
            return self._loop_resources[loop]

    async def _ensure_browser(self, headless: bool = True) -> Browser:
        loop_state = self._get_loop_state()
        async_lock: asyncio.Lock = loop_state["lock"]

        async with async_lock:
            browser = loop_state["browsers"].get(headless)
            pw = loop_state.get("pw")

            if browser is not None:
                try:
                    if browser.is_connected():
                        return browser
                except Exception:
                    pass

                try:
                    await browser.close()
                except Exception:
                    pass
                loop_state["browsers"].pop(headless, None)

            if async_playwright is None:
                raise RuntimeError("Playwright is required for browser automation.")

            logger.info(f"Khởi động Chromium cho phiên job (headless={headless}, path={self._chrome_path})...")
            if pw is None:
                pw = await async_playwright().start()
                loop_state["pw"] = pw

            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process,Translate,OptimizationHints,MediaRouter,DialMediaRouteProvider",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-component-update",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-sync",
                "--disable-default-apps",
                "--mute-audio",
                "--no-first-run",
                "--no-default-browser-check",
            ]
            launch_kwargs: Dict[str, Any] = {
                "headless": headless,
                "args": launch_args,
            }
            if self._chrome_path and os.path.exists(self._chrome_path):
                launch_kwargs["executable_path"] = self._chrome_path

            browser = await pw.chromium.launch(**launch_kwargs)
            loop_state["browsers"][headless] = browser
            return browser

    async def acquire_context(
        self,
        owner_id: str,
        proxy: Optional[Any] = None,
        headless: Optional[bool] = None,
        **kwargs
    ) -> BrowserContext:
        """Context riêng cho SSO; owner_id bắt buộc để quản lý vòng đời."""
        if not owner_id or not str(owner_id).strip():
            raise ValueError("owner_id là bắt buộc để quản lý browser session.")

        loop = asyncio.get_running_loop()
        is_headless = resolve_browser_headless(headless)
        browser = await self._ensure_browser(headless=is_headless)

        context_kwargs: Dict[str, Any] = {
            "viewport": {"width": 412, "height": 915},  # Android Chrome dimension
            "user_agent": (
                "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"
            ),
            "locale": "vi-VN",
            "timezone_id": "Asia/Ho_Chi_Minh",
            "service_workers": "block",
        }
        if proxy:
            if isinstance(proxy, str):
                context_kwargs["proxy"] = {"server": proxy}
            elif hasattr(proxy, "server"):
                p_dict = {"server": proxy.server}
                if hasattr(proxy, "username") and proxy.username:
                    p_dict["username"] = proxy.username
                if hasattr(proxy, "password") and proxy.password:
                    p_dict["password"] = proxy.password
                context_kwargs["proxy"] = p_dict
            elif isinstance(proxy, dict):
                context_kwargs["proxy"] = proxy

        context_kwargs.update(kwargs)
        ctx = await browser.new_context(**context_kwargs)
        ctx.set_default_timeout(8000)
        ctx.set_default_navigation_timeout(30000)

        # Register immediately so cancellation during setup cannot leak the context.
        with self._server_lock:
            self._owner_contexts[owner_id].add(ctx)
            self._context_to_owner[ctx] = owner_id
            self._context_to_loop[ctx] = loop

        # Inject anti-detection stealth script to bypass bot detection (hide navigator.webdriver)
        await ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.chrome = { runtime: {} };
        """)

        # Tối ưu hóa mạng qua Proxy: Chặn ảnh, media, font và tracking/telemetry (tăng tốc 3-5x)
        async def _block_unneeded_resources(route):
            try:
                req = route.request
                res_type = req.resource_type
                url = req.url.lower()
                parsed = urlsplit(url)
                path = parsed.path
                # Verification images must remain visible when Google requires them.
                if "captcha" in path or "recaptcha" in parsed.netloc:
                    await route.continue_()
                    return

                # 1. Chặn hình ảnh, media, font
                if res_type in ("image", "media", "font"):
                    await route.abort()
                    return

                # 2. Chặn các file tĩnh nặng theo đuôi mở rộng
                if path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf")):
                    await route.abort()
                    return

                # 3. Chặn logging / telemetry của Google không cần thiết cho luồng xác thực
                blocked_patterns = (
                    "play.google.com/log",
                    "google-analytics.com",
                    "doubleclick.net",
                    "adservice.google.com",
                    "/common/diagnostics",
                    "/gen_204",
                )
                if any(bp in url for bp in blocked_patterns):
                    await route.abort()
                    return

                await route.continue_()
            except Exception:
                try:
                    await route.continue_()
                except Exception:
                    pass

        await ctx.route("**/*", _block_unneeded_resources)

        return ctx

    async def release_context(self, context: BrowserContext) -> None:
        """Giải phóng 1 context an toàn."""
        try:
            with self._server_lock:
                owner_id = self._context_to_owner.pop(context, None)
                self._context_to_loop.pop(context, None)
                if owner_id and owner_id in self._owner_contexts:
                    self._owner_contexts[owner_id].discard(context)
                    if not self._owner_contexts[owner_id]:
                        del self._owner_contexts[owner_id]
            await context.close()
        except Exception as e:
            logger.debug(f"Lỗi khi release context: {e}")

    async def release_by_owner(self, owner_id: str) -> None:
        """Đóng tất cả contexts thuộc về một owner (khi task hoàn thành hoặc bị hủy)."""
        with self._server_lock:
            contexts = list(self._owner_contexts.pop(owner_id, set()))
            for ctx in contexts:
                self._context_to_owner.pop(ctx, None)
                self._context_to_loop.pop(ctx, None)

        for ctx in contexts:
            try:
                await ctx.close()
            except Exception as e:
                logger.debug(f"Lỗi khi release context của owner {owner_id}: {e}")

    async def shutdown_for_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """Đóng sạch sẽ browser và contexts thuộc về một event loop cụ thể khi worker kết thúc hoặc bị hủy."""
        try:
            target_loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            return

        contexts_to_close = []
        with self._server_lock:
            for ctx, lp in list(self._context_to_loop.items()):
                if lp == target_loop:
                    contexts_to_close.append(ctx)
                    self._context_to_loop.pop(ctx, None)
                    owner_id = self._context_to_owner.pop(ctx, None)
                    if owner_id and owner_id in self._owner_contexts:
                        self._owner_contexts[owner_id].discard(ctx)
                        if not self._owner_contexts[owner_id]:
                            del self._owner_contexts[owner_id]

        for ctx in contexts_to_close:
            try:
                await ctx.close()
            except Exception:
                pass

        with self._server_lock:
            loop_state = self._loop_resources.pop(target_loop, None)

        if loop_state:
            pw = loop_state.get("pw")
            for browser in loop_state["browsers"].values():
                try:
                    await browser.close()
                except Exception:
                    pass
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass

    async def shutdown(self) -> None:
        """Dọn dẹp toàn bộ trình duyệt trên tất cả các loop."""
        for ctx in list(self._context_to_owner.keys()):
            try:
                await ctx.close()
            except Exception:
                pass
        self._owner_contexts.clear()
        self._context_to_owner.clear()
        self._context_to_loop.clear()

        with self._server_lock:
            states = list(self._loop_resources.values())
            self._loop_resources.clear()

        for s in states:
            pw = s.get("pw")
            for browser in s["browsers"].values():
                try:
                    await browser.close()
                except Exception:
                    pass
            if pw:
                try:
                    await pw.stop()
                except Exception:
                    pass


# Global singleton instance
browser_manager = CentralBrowserManager()
