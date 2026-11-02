"""Persisted runtime and platform configuration models.

Provides strongly typed dataclasses for application settings:
- ``CommonConfig``: Global infrastructure (browser headless, updates, Google Auth proxy, rotating proxy providers).
- ``MochiChatConfig``: Mochi Chat platform-specific concurrency, proxy and mail credentials.
- ``RuntimeConfig``: Root container aggregating all platform configs with QSettings load/save.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def _read_bool(settings: Any, key: str, default: bool = True) -> bool:
    if settings is None:
        return default
    val = settings.value(key, default)
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() not in {"", "off", "no", "0", "false"}
    return bool(val)


def _read_int(settings: Any, key: str, default: int) -> int:
    if settings is None:
        return default
    val = settings.value(key, default)
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _read_float(settings: Any, key: str, default: float) -> float:
    if settings is None:
        return default
    val = settings.value(key, default)
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _read_str(settings: Any, key: str, default: str) -> str:
    if settings is None:
        return default
    val = settings.value(key, default)
    return str(val) if val is not None else default


@dataclass
class CommonConfig:
    """Cấu hình hạ tầng dùng chung cho toàn bộ app."""
    headless: bool = True
    google_login_via_proxy: bool = True
    auto_check_update: bool = False
    enable_cloud_lifecycle_refresh: bool = False
    proxy_muaproxyxoay_key: str = ""
    proxy_proxyvn_key: str = ""
    proxy_rotation_cooldown_seconds: float = 3.0
    proxy_rotation_retries: int = 3
    proxy_check_timeout_seconds: int = 20
    proxy_verify_after_rotation: bool = False

    @classmethod
    def from_settings(cls, settings: Any) -> "CommonConfig":
        return cls(
            headless=_read_bool(settings, "runtime/browser_worker_headless", _read_bool(settings, "runtime/captcha_headless", True)),
            google_login_via_proxy=_read_bool(settings, "runtime/google_login_via_proxy", True),
            auto_check_update=_read_bool(settings, "app/auto_check_update", False),
            enable_cloud_lifecycle_refresh=_read_bool(settings, "runtime/enable_cloud_lifecycle_refresh", False),
            proxy_muaproxyxoay_key=_read_str(settings, "proxy/provider/muaproxyxoay_key", ""),
            proxy_proxyvn_key=_read_str(settings, "proxy/provider/proxyvn_key", ""),
            proxy_rotation_cooldown_seconds=_read_float(settings, "proxy/rotation_min_wait_seconds", 3.0),
            proxy_rotation_retries=_read_int(settings, "proxy/rotation_max_retries", 3),
            proxy_check_timeout_seconds=_read_int(settings, "proxy/check_timeout_seconds", 20),
            proxy_verify_after_rotation=_read_bool(settings, "proxy/verify_after_rotation", False),
        )

    def save_to_settings(self, settings: Any) -> None:
        if settings is None:
            return
        settings.setValue("runtime/captcha_headless", self.headless)
        settings.setValue("runtime/browser_worker_headless", self.headless)
        settings.setValue("runtime/google_login_via_proxy", self.google_login_via_proxy)
        settings.setValue("app/auto_check_update", self.auto_check_update)
        settings.setValue("runtime/enable_cloud_lifecycle_refresh", self.enable_cloud_lifecycle_refresh)
        settings.setValue("proxy/provider/muaproxyxoay_key", self.proxy_muaproxyxoay_key)
        settings.setValue("proxy/provider/proxyvn_key", self.proxy_proxyvn_key)
        settings.setValue("proxy/rotation_min_wait_seconds", self.proxy_rotation_cooldown_seconds)
        settings.setValue("proxy/rotation_max_retries", self.proxy_rotation_retries)
        settings.setValue("proxy/check_timeout_seconds", self.proxy_check_timeout_seconds)
        settings.setValue("proxy/verify_after_rotation", self.proxy_verify_after_rotation)


@dataclass
class MochiChatConfig:
    """Cấu hình đặc thù cho nền tảng Mochi Chat."""
    account_concurrency: int = 10
    job_concurrency: int = 2
    proxy_account_concurrency: int = 1
    proxy_slot_delay_seconds: int = 5
    mail_provider: str = "gmailvip_vn"
    mail_provider_api_key: str = ""
    mail_api_keys: Dict[str, str] = field(default_factory=dict)
    mail_product_ids: str = "1, 1170, 2862, 1055, 1053, 3405, 3072, 2840, 6, 1054, 2050"
    default_source: str = "gmailvip_vn"
    mail_via_proxy: bool = True
    app_login_via_proxy: bool = True
    redeem_via_proxy: bool = True

    @classmethod
    def from_settings(cls, settings: Any, provider: Optional[str] = None) -> "MochiChatConfig":
        prov = _read_str(settings, "runtime/mochi_chat/mail_provider", "gmailvip_vn") if provider is None else provider
        default_pids = "1, 2862, 3405" if "1trieu" in prov.lower() else "1, 1170, 2862, 1055, 1053, 3405, 3072, 2840, 6, 1054, 2050"

        # Check provider-specific keys first, fallback to generic
        api_key_key = f"runtime/mochi_chat/mail_provider_api_key_{'1trieumail' if '1trieu' in prov.lower() else 'gmailvip'}"
        pids_key = f"runtime/mochi_chat/mail_product_ids_{'1trieumail' if '1trieu' in prov.lower() else 'gmailvip'}"
        
        same_provider = prov == _read_str(settings, "runtime/mochi_chat/mail_provider", "gmailvip_vn")
        legacy_key = _read_str(settings, "runtime/mochi_chat/mail_provider_api_key", "") if same_provider else ""
        legacy_pids = _read_str(settings, "runtime/mochi_chat/mail_product_ids", default_pids) if same_provider else default_pids
        api_key = _read_str(settings, api_key_key, legacy_key)
        pids = _read_str(settings, pids_key, legacy_pids)
        keys = {}
        saved_provider = _read_str(settings, "runtime/mochi_chat/mail_provider", "gmailvip_vn")
        for shop, suffix in (('gmailvip_vn', 'gmailvip'), ('1trieumail_com', '1trieumail')):
            fallback = _read_str(settings, "runtime/mochi_chat/mail_provider_api_key", '') if saved_provider == shop else ''
            keys[shop] = _read_str(settings, f"runtime/mochi_chat/mail_provider_api_key_{suffix}", fallback)
        if prov == 'multi_shop':
            api_key = ''
            pids = _read_str(settings, 'runtime/mochi_chat/mail_product_priority', '')

        return cls(
            account_concurrency=max(1, _read_int(settings, "runtime/mochi_chat/account_concurrency", 10)),
            job_concurrency=max(1, _read_int(settings, "runtime/mochi_chat/job_concurrency", 2)),
            proxy_account_concurrency=max(1, _read_int(settings, "runtime/mochi_chat/proxy_account_concurrency", 1)),
            proxy_slot_delay_seconds=_read_int(settings, "runtime/mochi_chat/proxy_slot_delay_seconds", 5),
            mail_provider=prov,
            mail_provider_api_key=api_key,
            mail_api_keys=keys,
            mail_product_ids=pids,
            default_source=_read_str(settings, "runtime/mochi_chat/default_source", "gmailvip_vn"),
            mail_via_proxy=_read_bool(settings, "runtime/mochi_chat/mail_via_proxy", True),
            app_login_via_proxy=_read_bool(settings, "runtime/mochi_chat/app_login_via_proxy", True),
            redeem_via_proxy=_read_bool(settings, "runtime/mochi_chat/redeem_via_proxy", True),
        )

    def save_to_settings(self, settings: Any) -> None:
        if settings is None:
            return
        settings.setValue("runtime/mochi_chat/account_concurrency", self.account_concurrency)
        settings.setValue("runtime/mochi_chat/job_concurrency", self.job_concurrency)
        settings.setValue("runtime/mochi_chat/proxy_account_concurrency", self.proxy_account_concurrency)
        settings.setValue("runtime/mochi_chat/proxy_slot_delay_seconds", self.proxy_slot_delay_seconds)
        settings.setValue("runtime/mochi_chat/mail_provider", self.mail_provider)
        settings.setValue("runtime/mochi_chat/mail_provider_api_key", self.mail_provider_api_key)
        settings.setValue("runtime/mochi_chat/mail_product_ids", self.mail_product_ids)
        settings.setValue("runtime/mochi_chat/default_source", self.default_source)
        settings.setValue("runtime/mochi_chat/mail_via_proxy", self.mail_via_proxy)
        settings.setValue("runtime/mochi_chat/app_login_via_proxy", self.app_login_via_proxy)
        settings.setValue("runtime/mochi_chat/redeem_via_proxy", self.redeem_via_proxy)

        if self.mail_provider == 'multi_shop':
            settings.setValue('runtime/mochi_chat/mail_product_priority', self.mail_product_ids)
            for shop, suffix in (('gmailvip_vn', 'gmailvip'), ('1trieumail_com', '1trieumail')):
                settings.setValue(f'runtime/mochi_chat/mail_provider_api_key_{suffix}', self.mail_api_keys.get(shop, ''))
        else:
            prov_suffix = "1trieumail" if "1trieu" in self.mail_provider.lower() else "gmailvip"
            settings.setValue(f"runtime/mochi_chat/mail_provider_api_key_{prov_suffix}", self.mail_provider_api_key)
            settings.setValue(f"runtime/mochi_chat/mail_product_ids_{prov_suffix}", self.mail_product_ids)


@dataclass
class RuntimeConfig:
    """Cấu hình tổng thể của ứng dụng."""
    common: CommonConfig = field(default_factory=CommonConfig)
    mochi_chat: MochiChatConfig = field(default_factory=MochiChatConfig)

    @classmethod
    def from_settings(cls, settings: Any) -> "RuntimeConfig":
        return cls(
            common=CommonConfig.from_settings(settings),
            mochi_chat=MochiChatConfig.from_settings(settings),
        )

    def save_to_settings(self, settings: Any) -> None:
        if settings is None:
            return
        self.common.save_to_settings(settings)
        self.mochi_chat.save_to_settings(settings)

