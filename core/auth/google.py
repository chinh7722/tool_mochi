"""Google Android Identity and Play Services Auth Client.

Module dùng chung cung cấp API cấp cao:
`get_google_id_token_for_app` để xác thực Google Account và cấp Google ID Token (JWT)
cho bất kỳ ứng dụng di động Android nào (thông qua Web Client ID, Package Name, SHA1).
Chạy hoàn toàn cục bộ (Local), không qua bất kỳ dịch vụ hay backend xác thực trung gian nào.
"""

import asyncio
import logging
import secrets
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

from core.proxy.models import curl_proxy_options
from core.proxy.protocol import ProxyTransportError, is_request_transport_error

try:
    from curl_cffi.requests import AsyncSession
except ImportError:
    AsyncSession = Any

from core.browser import browser_manager, resolve_browser_headless

logger = logging.getLogger("core.auth.google")

GOOGLE_AUTH_URL = "https://android.clients.google.com/auth"
DEFAULT_SDK_VERSION = 29
DEFAULT_PLAY_SERVICES_VERSION = 240913000
DEFAULT_USER_AGENT = "GoogleAuth/1.4"
GOOGLE_LOGIN_VIA_PROXY_SETTING_KEY = "runtime/google_login_via_proxy"


def resolve_google_login_via_proxy(settings: Any = None) -> bool:
    """Xác định cấu hình login Google qua proxy từ QSettings, mặc định True."""
    if settings is None:
        return True
    val = settings.value(GOOGLE_LOGIN_VIA_PROXY_SETTING_KEY, True)
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(val)


def _parse_google_kv_response(text: str) -> Dict[str, str]:
    """Parse văn bản phản hồi dạng Key=Value của Google Auth endpoint."""
    res = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            res[k.strip()] = v.strip()
    return res


async def _post_google_auth(
    headers: Dict[str, str],
    payload: Dict[str, str],
    timeout: float = 15.0,
    proxy: Optional[Any] = None,
    session: Optional[Any] = None,
) -> Dict[str, str]:
    """Gửi request tới Google Play Services Auth (android.clients.google.com)."""
    proxies = curl_proxy_options(proxy)

    close_session = False
    if session is None:
        session = AsyncSession(impersonate="chrome_android", timeout=timeout)
        close_session = True

    try:
        resp = await session.post(
            GOOGLE_AUTH_URL,
            headers=headers,
            data=payload,
            proxies=proxies,
            timeout=timeout,
        )
        return _parse_google_kv_response(resp.text)
    finally:
        if close_session:
            await session.close()


class GoogleLoginError(RuntimeError):
    """A credential/verification error that should not restart the login flow."""


# One browser round-trip per poll. Never select an unrelated account or a generic
# container just because its text happens to contain an email address.
_LOGIN_STATE_SCRIPT = r"""
(targetEmail) => {
    const visible = el => !!el && el.getClientRects().length > 0 &&
        getComputedStyle(el).visibility !== 'hidden' &&
        getComputedStyle(el).opacity !== '0' &&
        !el.closest('[hidden], [aria-hidden="true"], [inert]');
    // A previous Google screen may stay in the DOM, hidden behind the new one.
    document.querySelectorAll('[data-ttm-login-action]').forEach(el =>
        el.removeAttribute('data-ttm-login-action'));
    const email = document.querySelector('input[name="identifier"]');
    const password = document.querySelector('input[name="Passwd"]');
    const text = document.body ? document.body.innerText : '';
    const invalid = [...document.querySelectorAll('[aria-invalid="true"]')].some(visible);
    const errors = [...document.querySelectorAll('[role="alert"], div[jsname="B34EJ"], div[jsname="uybdVe"]')]
        .filter(visible).map(el => el.innerText.trim()).filter(Boolean);
    const knownError = /wrong password|incorrect password|couldn.t find your google account|sai mật khẩu|mật khẩu không chính xác|không tìm thấy tài khoản google|this account has been disabled/i.test(text);
    if (knownError || (invalid && errors.length))
        return {kind: 'error', message: errors.join(' ') || 'Google từ chối thông tin đăng nhập.'};
    const busy = [...document.querySelectorAll('[aria-busy="true"], [role="progressbar"], progress')]
        .some(visible);
    if (busy) return {kind: 'busy'};
    const challenge = /\/challenge\/(?!pwd|password|identifier|selection)/i.test(location.pathname);
    if (challenge && /verify|verification|xác minh|2-step|2 bước|captcha/i.test(text))
        return {kind: 'verification'};
    if (visible(email) && !email.disabled) return {kind: 'email'};
    if (visible(password) && !password.disabled) return {kind: 'password'};
    // Helper to detect consent / confirmation / continue buttons
    const isConsentButton = (el) => {
        if (!visible(el) || el.disabled || el.closest('[aria-disabled="true"], fieldset[disabled]')) return false;
        if (['identifierNext', 'passwordNext'].includes(el.id)) return false;
        if (el.closest('#identifierNext, #passwordNext')) return false;
        if (['submit_approve_access', 'agreeNext'].includes(el.id)) return true;

        const aria = (el.getAttribute('aria-label') || '').trim();
        const inner = (el.innerText || el.textContent || '').trim();
        const val = (el.value || '').trim();

        const isNegative = (s) => /^(cancel|hủy|deny|từ chối|back|quay lại|close|đóng)$/i.test(s);
        if (isNegative(aria) || isNegative(inner) || isNegative(val)) return false;

        const consentPattern = /(?:^|\b)(continue|tiếp tục|cho phép|allow|i understand|tôi hiểu|i agree|tôi đồng ý|đồng ý|accept|chấp nhận|confirm|xác nhận)(?:\b|$)/i;
        return consentPattern.test(aria) || consentPattern.test(inner) || consentPattern.test(val);
    };

    const buttons = [...document.querySelectorAll('button, [role="button"], input[type="submit"]')];
    let consentBtn = buttons.find(isConsentButton);
    if (consentBtn) {
        consentBtn = consentBtn.closest('button, [role="button"], input[type="submit"]') || consentBtn;
        consentBtn.setAttribute('data-ttm-login-action', 'consent');
        const label = (consentBtn.innerText || consentBtn.getAttribute('aria-label') || consentBtn.value || 'tiếp tục').trim();
        return {kind: 'consent', label: label, screen: text.trim().replace(/\s+/g, ' ').slice(0, 1200)};
    }

    const chooser = /choose an account|chọn (?:một )?tài khoản|choisir un compte|selecciona una cuenta/i.test(text);
    const candidates = [...document.querySelectorAll('[data-identifier], [data-email], [data-profile-identifier]')];
    let account = candidates.find(el => visible(el) && ['data-identifier','data-email','data-profile-identifier']
        .some(attr => (el.getAttribute(attr) || '').toLowerCase() === targetEmail));
    if (!account && chooser) {
        account = [...document.querySelectorAll('li, [role="link"], [role="button"]')]
            .find(el => visible(el) && [...el.querySelectorAll('*')].some(child =>
                child.children.length === 0 && child.textContent.trim().toLowerCase() === targetEmail));
    }
    if (account && chooser) {
        account = account.closest('[role="link"], [role="button"], li') || account;
        account.setAttribute('data-ttm-login-action', 'account');
        return {kind: 'account', screen: text.trim().replace(/\s+/g, ' ').slice(0, 1200)};
    }
    return {kind: 'waiting'};
}
"""


async def _browser_login(
    owner_id: str, email: str, password: str, *, client_id: Optional[str] = None,
    redirect_uri: str = "https://mochi.lightchaser.xyz", proxy: Optional[Any] = None,
    timeout: float = 60.0, headless: Optional[bool] = None, max_retries: int = 2,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Drive either OAuth flow with a single state loop and a bounded attempt."""
    for attempt in range(1, max(1, max_retries) + 1):
        context = None
        token = None
        token_event = asyncio.Event()
        started = asyncio.get_running_loop().time()
        try:
            async with asyncio.timeout(timeout):
                if on_progress:
                    route_name = "qua proxy" if proxy else "bằng mạng trực tiếp"
                    on_progress(f"Mở Google {route_name} (lần {attempt}/{max(1, max_retries)})...")
                context = await browser_manager.acquire_context(owner_id=owner_id, proxy=proxy, headless=headless)
                page = await context.new_page()
                page.set_default_timeout(2500)
                pending_auth_requests = set()
                action_attempts = {}
                last_wait_log = 0.0

                def track_request(request):
                    parsed = urlsplit(request.url)
                    if (parsed.hostname == 'accounts.google.com'
                            and request.resource_type in ('document', 'xhr', 'fetch')
                            and not any(part in parsed.path for part in ('/gen_204', '/common/diagnostics', '/log'))):
                        pending_auth_requests.add(request)

                page.on('request', track_request)
                page.on('requestfinished', lambda request: pending_auth_requests.discard(request))
                page.on('requestfailed', lambda request: pending_auth_requests.discard(request))

                def capture_url(url):
                    nonlocal token
                    parsed = urlsplit(url)
                    expected = urlsplit(redirect_uri)
                    if (parsed.scheme, parsed.netloc, parsed.path.rstrip('/')) != (expected.scheme, expected.netloc, expected.path.rstrip('/')):
                        return
                    value = parse_qs(parsed.fragment).get('id_token', [''])[0]
                    if value.startswith('eyJ'):
                        token = value
                        token_event.set()

                if client_id:
                    page.on('framenavigated', lambda frame: capture_url(frame.url) if frame == page.main_frame else None)

                    async def finish_redirect(route):
                        # The OAuth callback needs no Mochi SPA, images or API calls.
                        if route.request.is_navigation_request():
                            await route.fulfill(status=200, content_type='text/html', body='<html><body></body></html>')
                        else:
                            await route.continue_()

                    await page.route(redirect_uri.rstrip('/') + '**', finish_redirect)
                    query = urlencode(dict(client_id=client_id, response_type='id_token',
                        scope='openid profile email', redirect_uri=redirect_uri,
                        nonce=secrets.token_hex(16), login_hint=email))
                    url = 'https://accounts.google.com/o/oauth2/v2/auth?' + query
                else:
                    url = 'https://accounts.google.com/embedded/setup/android'
                await page.goto(url, wait_until='commit', timeout=max(1, int(timeout * 1000)))

                submitted = set()
                last_action = None
                last_click = 0.0
                last_scroll = 0.0
                while not token_event.is_set():
                    if client_id:
                        capture_url(page.url)
                    else:
                        cookies = await context.cookies('https://accounts.google.com')
                        token = next((c['value'] for c in cookies if c['name'] == 'oauth_token' and c['value']), None)
                        if token:
                            token_event.set()
                    if token_event.is_set():
                        break
                    try:
                        state = await page.evaluate(_LOGIN_STATE_SCRIPT, email.strip().lower())
                        kind = state['kind']
                        now = asyncio.get_running_loop().time()
                        if kind == 'error':
                            raise GoogleLoginError(state['message'])
                        if kind == 'verification':
                            raise GoogleLoginError('Google yêu cầu xác minh bổ sung; cần người dùng xử lý xác minh tài khoản.')
                        if kind in ('email', 'password') and kind not in submitted:
                            field = 'identifier' if kind == 'email' else 'Passwd'
                            value = email if kind == 'email' else password
                            await page.locator(f'input[name="{field}"]').fill(value)
                            button = '#identifierNext' if kind == 'email' else '#passwordNext'
                            await page.locator(button).click(no_wait_after=True)
                            submitted.add(kind)
                            last_click = now
                            if on_progress:
                                elapsed = now - started
                                label = 'email' if kind == 'email' else 'mật khẩu'
                                on_progress(f'Đã gửi {label} Google ({elapsed:.1f}s).')
                        elif kind in ('account', 'consent'):
                            action_key = (kind, page.url, state.get('screen', ''), state.get('label', ''))
                            attempts, clicked_at = action_attempts.get(action_key, (0, 0.0))
                            retry_delay = 3.0 * (2 ** max(0, attempts - 1))
                            # One consent RPC can take many seconds on a proxy.
                            # Do not resubmit it while it is still in flight.
                            ready = (not pending_auth_requests and (attempts == 0 or
                                (attempts < 3 and now - clicked_at >= retry_delay)))
                            if ready:
                                target = page.locator(f'[data-ttm-login-action="{kind}"]').first
                                # Record before clicking: a timeout can occur after
                                # the actual click has already reached Google.
                                action_attempts[action_key] = (attempts + 1, now)
                                last_action, last_click = kind, now
                                await target.click(timeout=2000, no_wait_after=True)
                                if on_progress:
                                    label = f'chọn tài khoản {email}' if kind == 'account' else state.get('label', 'xác nhận')
                                    on_progress(f'Đã bấm {label}; đang chờ Google phản hồi.')
                        elif kind == 'waiting' and now - last_scroll >= 1.0:
                            # Scroll only; never click unlabeled buttons based on their position.
                            await page.evaluate("""() => {
                                window.scrollTo(0, document.body ? document.body.scrollHeight : 0);
                                document.querySelectorAll('main, section, form, div, [role="dialog"]').forEach(el => {
                                    if (el.clientHeight > 40 && el.scrollHeight > el.clientHeight + 10)
                                        el.scrollTop = el.scrollHeight;
                                });
                                const scroll = document.querySelector('button[aria-label*="scroll" i], button[aria-label*="cuộn" i]');
                                if (scroll) scroll.click();
                            }""")
                            last_scroll = now
                        if last_action == 'consent' and now - last_wait_log >= 8.0 and now - last_click >= 3.0:
                            if on_progress:
                                on_progress('Đang chờ Google xử lý xác nhận quyền...')
                            last_wait_log = now
                    except GoogleLoginError:
                        raise
                    except Exception:
                        if page.is_closed():
                            raise RuntimeError('Cửa sổ đăng nhập Google đã đóng.')
                        # Navigation can destroy the JS execution context; retry the next state.
                    try:
                        await asyncio.wait_for(token_event.wait(), timeout=0.2)
                    except asyncio.TimeoutError:
                        pass
                if on_progress:
                    on_progress(f'Google đăng nhập thành công ({asyncio.get_running_loop().time() - started:.1f}s).')
                return token
        except GoogleLoginError:
            raise
        except Exception as exc:
            if is_request_transport_error(exc):
                # Return request failures to the broker instead of trying another
                # browser flow repeatedly through the same failed proxy.
                raise ProxyTransportError(proxy=proxy, cause=exc, kind='google_request') from exc
            if on_progress:
                on_progress(f'Google lần {attempt} chưa thành công: {type(exc).__name__}: {exc}')
            logger.warning('Google browser attempt %s failed: %s', attempt, type(exc).__name__)
        finally:
            if context is not None:
                await browser_manager.release_context(context)
    return None


async def _extract_id_token_via_browser(
    owner_id: str, email: str, password: str, client_id: str,
    redirect_uri: str = "https://mochi.lightchaser.xyz", proxy: Optional[Any] = None,
    timeout: float = 60.0, headless: Optional[bool] = None, max_retries: int = 2,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    return await _browser_login(owner_id, email, password, client_id=client_id,
        redirect_uri=redirect_uri, proxy=proxy, timeout=timeout, headless=headless,
        max_retries=max_retries, on_progress=on_progress)


async def _extract_oauth_token(
    owner_id: str, email: str, password: str, proxy: Optional[Any] = None,
    timeout: float = 60.0, headless: Optional[bool] = None, max_retries: int = 2,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    return await _browser_login(owner_id, email, password, proxy=proxy,
        timeout=timeout, headless=headless, max_retries=max_retries, on_progress=on_progress)


async def _exchange_oauth_to_master_token(
    email: str,
    oauth_token: str,
    android_id: Optional[str] = None,
    client_sig: Optional[str] = None,
    device_country: str = "vn",
    operator_country: str = "vn",
    lang: str = "vi",
    sdk_version: int = DEFAULT_SDK_VERSION,
    gms_version: int = DEFAULT_PLAY_SERVICES_VERSION,
    proxy: Optional[Any] = None,
    session: Optional[Any] = None,
) -> str:
    """Đổi oauth_token lấy Master Token (aas_et/...)."""
    aid = android_id or secrets.token_hex(8)
    sig = client_sig or "e74ee6f736b94f80c452cbfc6c7c5dc6973ddbcb"

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "identity",
    }
    payload = {
        "accountType": "HOSTED_OR_GOOGLE",
        "Email": email,
        "has_permission": "1",
        "add_account": "1",
        "ACCESS_TOKEN": "1",
        "Token": oauth_token,
        "service": "ac2dm",
        "source": "android",
        "androidId": aid,
        "device_country": device_country,
        "operatorCountry": operator_country,
        "lang": lang,
        "sdk_version": str(sdk_version),
        "google_play_services_version": str(gms_version),
        "callerSig": sig,
        "droidguard_results": "dummy123",
    }

    parsed = await _post_google_auth(headers, payload, timeout=15.0, proxy=proxy, session=session)
    if "Error" in parsed:
        error_msg = parsed.get("Error", "Unknown")
        raise RuntimeError(f"Google Play Services Auth lỗi: {error_msg} (Response: {parsed})")

    master_token = parsed.get("Token") or parsed.get("Auth")
    if not master_token:
        raise RuntimeError(f"Không tìm thấy Master Token trong Google Auth response: {parsed}")

    return master_token


async def _exchange_master_to_id_token(
    email: str,
    master_token: str,
    client_id: str,
    app_pkg: str = "com.google.android.gms",
    client_sig: Optional[str] = None,
    android_id: Optional[str] = None,
    device_country: str = "vn",
    operator_country: str = "vn",
    lang: str = "vi",
    sdk_version: int = DEFAULT_SDK_VERSION,
    gms_version: int = DEFAULT_PLAY_SERVICES_VERSION,
    proxy: Optional[Any] = None,
    session: Optional[Any] = None,
) -> Optional[str]:
    """Dùng Master Token (aas_et/...) xin Google ID Token (JWT) cho Web Client ID của App."""
    aid = android_id or secrets.token_hex(8)
    sig = client_sig or "e74ee6f736b94f80c452cbfc6c7c5dc6973ddbcb"
    pkg = app_pkg or "com.google.android.gms"

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Encoding": "identity",
    }

    # Các cấu hình payload theo chuẩn Google Play Services GLS Auth
    configs = [
        # Strategy 1: service=audience:server:client_id:..., app=com.google.android.gms, callerPkg=app_pkg
        (f"audience:server:client_id:{client_id}", "com.google.android.gms", pkg, None),
        # Strategy 2: service=oauth2:server:client_id:... with audience
        (
            f"oauth2:server:client_id:{client_id}:api_scope:https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/userinfo.email",
            "com.google.android.gms",
            pkg,
            f"server:client_id:{client_id}",
        ),
        # Strategy 3: service=audience:server:client_id:... with app=pkg, callerPkg=com.google.android.gms
        (f"audience:server:client_id:{client_id}", pkg, "com.google.android.gms", None),
        # Strategy 4: service=oauth2:..., app=pkg, callerPkg=pkg
        (
            f"oauth2:server:client_id:{client_id}:api_scope:https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/userinfo.email",
            pkg,
            pkg,
            f"server:client_id:{client_id}",
        ),
        # Strategy 5: plain userinfo scope
        (
            "oauth2:https://www.googleapis.com/auth/userinfo.profile https://www.googleapis.com/auth/userinfo.email",
            "com.google.android.gms",
            pkg,
            f"server:client_id:{client_id}",
        ),
    ]

    last_error = ""
    last_response = {}

    for scope_service, app_name, caller_pkg_name, aud in configs:
        payload = {
            "accountType": "HOSTED_OR_GOOGLE",
            "Email": email,
            "has_permission": "1",
            "add_account": "1",
            "EncryptedPasswd": master_token,
            "service": scope_service,
            "source": "android",
            "app": app_name,
            "callerPkg": caller_pkg_name,
            "callerSig": sig,
            "client_sig": sig,
            "oauth2_include_id_token": "1",
            "oauth2_include_email": "1",
            "androidId": aid,
            "device_country": device_country,
            "operatorCountry": operator_country,
            "lang": lang,
            "sdk_version": str(sdk_version),
            "google_play_services_version": str(gms_version),
        }
        if aud:
            payload["audience"] = aud

        try:
            parsed = await _post_google_auth(headers, payload, timeout=15.0, proxy=proxy, session=session)
            last_response = parsed

            if "Error" in parsed:
                last_error = f"{parsed.get('Error')} ({parsed.get('LoggableAuthErrorMessage', '')})"
                logger.debug(f"Strategy ({scope_service[:25]}..., app={app_name}) trả về lỗi: {last_error}")
                continue

            for k in ["id_token", "IdToken", "ID_TOKEN"]:
                if k in parsed and parsed[k]:
                    return parsed[k]

            auth_val = parsed.get("Auth", "")
            if auth_val.startswith("eyJ"):
                return auth_val

        except Exception as e:
            last_error = str(e)
            continue

    if last_error:
        raise RuntimeError(f"Google ID Token exchange lỗi: {last_error} (Response: {last_response})")

    return None


async def get_google_id_token_for_app(
    owner_id: str,
    email: str,
    password: str,
    client_id: str,
    app_pkg: str = "com.google.android.gms",
    client_sig: Optional[str] = None,
    proxy: Optional[Any] = None,
    timeout: float = 60.0,
    headless: Optional[bool] = None,
    max_retries: int = 2,
    android_id: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
    google_login_via_proxy: bool = True,
) -> Optional[str]:
    """Hàm duy nhất công khai: Đăng nhập Google và cấp Google ID Token (JWT) cho App Android hoàn toàn ở local."""
    eff_proxy = proxy
    if not google_login_via_proxy:
        logger.info(f"Cấu hình login Google qua proxy đang tắt (False). Ghi đè proxy=None cho job {owner_id}.")
        eff_proxy = None

    if on_progress:
        on_progress(f"Đang login Google ({email}) để xin Google ID Token...")

    # Strategy 1: Trực tiếp qua Google OAuth 2.0 Web flow trên browser (bỏ qua check Android Console SHA-1)
    id_token = await _extract_id_token_via_browser(
        owner_id=owner_id,
        email=email,
        password=password,
        client_id=client_id,
        redirect_uri="https://mochi.lightchaser.xyz",
        proxy=eff_proxy,
        timeout=timeout,
        headless=headless,
        max_retries=max_retries,
        on_progress=on_progress,
    )
    if id_token:
        if on_progress:
            on_progress("Lấy Google ID Token thành công.")
        return id_token

    # Strategy 2: Fallback sang GLS Android Play Services Auth
    logger.info("Browser OAuth không lấy được token, thử fallback sang Play Services Auth...")
    oauth_token = await _extract_oauth_token(
        owner_id=owner_id,
        email=email,
        password=password,
        proxy=eff_proxy,
        timeout=timeout,
        headless=headless,
        max_retries=1,
        on_progress=on_progress,
    )
    if not oauth_token:
        logger.warning(f"Không thể lấy oauth_token cho {email}")
        return None

    if on_progress:
        on_progress("Lấy Google OAuth Token thành công. Đang đổi Master Token & xin Google ID Token...")

    master_token = await _exchange_oauth_to_master_token(
        email=email,
        oauth_token=oauth_token,
        android_id=android_id,
        client_sig=client_sig,
        proxy=eff_proxy,
    )

    id_token = await _exchange_master_to_id_token(
        email=email,
        master_token=master_token,
        client_id=client_id,
        app_pkg=app_pkg,
        client_sig=client_sig,
        android_id=android_id,
        proxy=eff_proxy,
    )

    if id_token and on_progress:
        on_progress("Lấy Google ID Token thành công.")

    return id_token
