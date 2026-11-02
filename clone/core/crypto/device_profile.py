"""
MiraiMind / Momo Android Device Identity and Fingerprint Profile (core.device_profile).

Generates consistent hardware fingerprints and correlation IDs required by:
1. MMUID registration (commands 101, 107, and v3 supplement).
2. loginGuest and loginWeb MDP common parameters.
3. HData hardware attestation.
"""
from __future__ import annotations

import hashlib
import random
import secrets
import time
import uuid
from typing import Any, Dict, Optional


class DeviceProfile:
    """Represents a virtual Android device hardware profile."""

    BRANDS_MODELS = [
        ("Samsung", "SM-S918B", "Android 14", "34", "1440x3088"),
        ("Google", "Pixel 8 Pro", "Android 14", "34", "1344x2992"),
        ("Xiaomi", "23127PN0CG", "Android 14", "34", "1200x2670"),
        ("OnePlus", "CPH2581", "Android 14", "34", "1440x3168"),
        ("Samsung", "SM-G998B", "Android 13", "33", "1440x3200"),
        ("Google", "Pixel 7 Pro", "Android 13", "33", "1440x3120"),
    ]

    def __init__(
        self,
        android_id: Optional[str] = None,
        imei: Optional[str] = None,
        mac: Optional[str] = None,
        device_seed: Optional[str] = None,
    ):
        self.android_id = android_id or secrets.token_hex(8)
        self.imei = imei or self._generate_imei()
        self.mac = mac or self._generate_mac()
        self.device_seed = device_seed or secrets.token_hex(64)  # 128 hex chars

        brand, model, rom, sdk_ver, screen = random.choice(self.BRANDS_MODELS)
        self.brand = brand
        self.model = model
        self.rom = rom
        self.sdk_version = sdk_ver
        self.screen_resolution = screen

        self.app_version = "1.1.95"
        self.app_version_code = "1005605"
        self.package_name = "com.immomo.miraimind"

        # Session state
        self.session_id: Optional[str] = None
        self.chat_user_id: Optional[str] = None
        self.guest_token: Optional[str] = None
        self.mmuid: Optional[str] = None
        self.mmuid_v3: Optional[str] = None

    @staticmethod
    def _generate_imei() -> str:
        # Standard 15-digit TAC + SNR + Luhn
        base = "86" + "".join([str(random.randint(0, 9)) for _ in range(12)])
        # Luhn checksum
        digits = [int(c) for c in base]
        for i in range(len(digits) - 1, -1, -2):
            digits[i] = digits[i] * 2
            if digits[i] > 9:
                digits[i] -= 9
        checksum = (10 - (sum(digits) % 10)) % 10
        return base + str(checksum)

    @staticmethod
    def _generate_mac() -> str:
        return ":".join([f"{random.randint(0, 255):02x}" for _ in range(6)])

    def generate_request_session_id(self) -> str:
        """Generates the per-invocation 8-4-4-4-12 correlation UUID."""
        return str(uuid.uuid4())

    def get_user_agent(self) -> str:
        """MiraiMind official Android User-Agent header."""
        return (
            f"MiraiMind/{self.app_version} (Android {self.rom}; {self.brand} {self.model}; "
            f"Build/{secrets.token_hex(4).upper()}; Scale/3.00)"
        )

    def build_mmuid_fingerprint(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Builds ordered fingerprint for Command 101/108."""
        return {
            "androidid": self.android_id,
            "imei": self.imei,
            "mac": self.mac,
            "screen": self.screen_resolution,
            "brand": self.brand,
            "model": self.model,
            "rom": self.rom,
            "sessionid": session_id or self.session_id or self.generate_request_session_id(),
            "timestamp": int(time.time()),
        }

    def build_mmuid_v3_fingerprint(self, token: Optional[str] = None) -> Dict[str, Any]:
        """Builds schema observed for /v3/log/upload_info/supplement."""
        return {
            "token": token or self.guest_token or "",
            "android_id": self.android_id,
            "model": self.model,
            "brand": self.brand,
            "sdk_int": self.sdk_version,
            "version_code": self.app_version_code,
            "version_name": self.app_version,
            "carrier": "Viettel",
            "country": "VN",
            "lang": "vi",
            "timezone": "Asia/Ho_Chi_Minh",
        }

    def build_common_params(self) -> Dict[str, Any]:
        """Common URL/body query parameters used in MiraiMind API calls."""
        return {
            "app_version": self.app_version,
            "version_code": self.app_version_code,
            "platform": "android",
            "os_version": self.rom,
            "device_model": self.model,
            "device_brand": self.brand,
            "channel": "google_play",
            "lang": "vi",
            "region": "VN",
            "mmuid": self.mmuid or "",
        }
