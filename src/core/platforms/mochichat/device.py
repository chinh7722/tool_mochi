"""Device Identity and Hardware Emulator for Mochi Chat."""

from dataclasses import dataclass, field
import secrets
from typing import Any, Dict
import uuid


@dataclass
class DeviceInfo:
    """Đại diện thông tin thiết bị Android ảo cho mỗi phiên làm việc Mochi Chat.

    Tự động sinh ngẫu nhiên:
    - android_id: 16 ký tự hex đại diện cho Android GSF ID (vd: 371031780e9cf72c).
    - device_id: Chuỗi UUID v4 đại diện cho phần cứng nhận dạng của Mochi Chat.
    """
    android_id: str = field(default_factory=lambda: secrets.token_hex(8))
    device_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    device_country: str = "vn"
    operator_country: str = "vn"
    lang: str = "vi"
    sdk_version: int = 29
    google_play_services_version: int = 240913000
    app_version: str = "1.3.0"
    app_build: str = "7"

    def to_dict(self) -> Dict[str, Any]:
        """Xuất thông tin thiết bị ra dict để lưu JSON."""
        return {
            "android_id": self.android_id,
            "device_id": self.device_id,
            "device_country": self.device_country,
            "operator_country": self.operator_country,
            "lang": self.lang,
            "sdk_version": self.sdk_version,
            "google_play_services_version": self.google_play_services_version,
            "app_version": self.app_version,
            "app_build": self.app_build,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeviceInfo":
        """Khôi phục thông tin thiết bị từ dict."""
        return cls(
            android_id=data.get("android_id", secrets.token_hex(8)),
            device_id=data.get("device_id", str(uuid.uuid4())),
            device_country=data.get("device_country", "vn"),
            operator_country=data.get("operator_country", "vn"),
            lang=data.get("lang", "vi"),
            sdk_version=data.get("sdk_version", 29),
            google_play_services_version=data.get("google_play_services_version", 240913000),
            app_version=data.get("app_version", "1.3.0"),
            app_build=data.get("app_build", "7"),
        )
