"""Quản lý vòng đời và tham số Job đặc thù cho nền tảng Mochi Chat."""

from typing import Any, Dict, Optional

from core.platforms.mochichat.constants import (
    MOCHI_INVITATION_REWARD_POINTS,
    PLATFORM_ID,
)


def build_mochi_cloud_start_job_kwargs(
    *,
    job_id: str,
    invite_code: str,
    target_count: int,
    customer_name: Optional[str] = None,
    job_name: Optional[str] = None,
    unit_price: int = 0,
    continued_from_job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Sinh bộ tham số chuẩn cho Job của Mochi Chat."""
    clean_code = str(invite_code).strip().upper()
    effective_job_name = job_name or f"Mochi Invite {clean_code}"
    effective_customer_name = customer_name or ""

    return {
        "job_id": job_id,
        "platform": PLATFORM_ID,
        "job_type": "CUSTOMER_INVITE",
        "customer_name": effective_customer_name,
        "job_name": effective_job_name,
        "invitation_code": clean_code,
        "target_count": target_count,
        "reward_points_per_account": MOCHI_INVITATION_REWARD_POINTS,
        "unit_price": unit_price,
        "continued_from_job_id": continued_from_job_id,
    }
