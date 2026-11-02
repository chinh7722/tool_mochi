import math
from typing import Sequence, Tuple

MOCHI_INVITATION_REWARD_POINTS = 100
CUSTOMER_INVITATION_REWARD_POINTS = 200

# Timeouts & intervals
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
CLOUD_REQUEST_TIMEOUT_SECONDS = 10.0

# Mail & Proxy settings
DEFAULT_GMAILVIP_PRODUCT_IDS = [1, 1170, 2862, 1055, 1053, 3405, 3072, 2840, 6, 1054, 2050]
DEFAULT_GMAILVIP_PRODUCT_IDS_STR = "1, 1170, 2862, 1055, 1053, 3405, 3072, 2840, 6, 1054, 2050"

DEFAULT_1TRIEUMAIL_PRODUCT_IDS = [1, 2862, 3405]
DEFAULT_1TRIEUMAIL_PRODUCT_IDS_STR = "1, 2862, 3405"


def required_mochi_invitation_accounts(target_points: int) -> int:
    """Tính số account cần chạy cho Mochi Chat, mỗi lượt thành công cộng 100 kẹo."""
    if target_points <= 0:
        return 0
    return math.ceil(target_points / MOCHI_INVITATION_REWARD_POINTS)


def required_invitation_accounts(target_points: int) -> int:
    """Tính số account cần chạy, làm tròn lên theo mỗi lượt 200 điểm."""
    if target_points <= 0:
        raise ValueError("target_points must be greater than zero")
    return math.ceil(target_points / CUSTOMER_INVITATION_REWARD_POINTS)
