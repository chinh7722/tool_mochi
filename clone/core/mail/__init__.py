"""
Disposable Email Providers Package.
"""
from .mail_providers import (
    BaseMailProvider,
    TempMailLolProvider,
    MailTmProvider,
    get_mail_provider,
    OTP_REGEX,
)

__all__ = [
    "BaseMailProvider",
    "TempMailLolProvider",
    "MailTmProvider",
    "get_mail_provider",
    "OTP_REGEX",
]
