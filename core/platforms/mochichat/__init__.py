"""Mochi Chat Platform Package for TTM-Tool (Local Rebuild)."""

from core.platforms.mochichat.constants import (
    MOCHI_API_BASE,
    MOCHI_APP_BUILD,
    MOCHI_APP_PKG,
    MOCHI_APP_VERSION,
    MOCHI_CERT_SHA1,
    MOCHI_INVITATION_REWARD_POINTS,
    MOCHI_PLATFORM,
    MOCHI_WEB_CLIENT_ID,
    PLATFORM_ID,
)
from core.platforms.mochichat.device import DeviceInfo
from core.platforms.mochichat.fetcher import MochiChatFetcher, MochiRedeemResult
from core.platforms.mochichat.job_lifecycle import build_mochi_cloud_start_job_kwargs
from core.platforms.mochichat.runners import (
    MochiAccountItem,
    MochiAccountResult,
    MochiInviteJobSummary,
    MochiInviteRunner,
)

__all__ = [
    "PLATFORM_ID",
    "build_mochi_cloud_start_job_kwargs",
    "MochiChatFetcher",
    "DeviceInfo",
    "MochiRedeemResult",
    "MochiAccountItem",
    "MochiAccountResult",
    "MochiInviteJobSummary",
    "MochiInviteRunner",
    "MOCHI_API_BASE",
    "MOCHI_WEB_CLIENT_ID",
    "MOCHI_APP_PKG",
    "MOCHI_CERT_SHA1",
    "MOCHI_APP_VERSION",
    "MOCHI_APP_BUILD",
    "MOCHI_PLATFORM",
    "MOCHI_INVITATION_REWARD_POINTS",
]
