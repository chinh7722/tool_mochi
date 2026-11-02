"""
Disposable Email Providers and OTP Extractor (core.mail.mail_providers).

Provides integration with:
1. TempMail.lol (Free public API v2).
2. Mail.tm (Open REST API).
3. NiMail (nimail.cn).
4. GPTMail (mail.chatgpt.org.uk).
Includes regex-based OTP code extraction for MiraiMind registration.
"""
from __future__ import annotations

import logging
import re
import secrets
import string
import time
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

logger = logging.getLogger("MailProviders")

OTP_REGEX = re.compile(r"\b(\d{6})\b")


class BaseMailProvider:
    """Base class for temporary email providers."""

    def __init__(self, proxy: Optional[str] = None, timeout: int = 15):
        self.proxy = proxy
        self.timeout = timeout
        self.session = requests.Session() if _HAS_REQUESTS else None
        if self.session and self.proxy:
            self.session.proxies.update({"http": self.proxy, "https": self.proxy})

    def create_mailbox(self) -> Tuple[str, Any]:
        """Creates a mailbox and returns (email_address, token/session_info)."""
        raise NotImplementedError

    def fetch_inbox(self, email: str, token: Any) -> List[Dict[str, Any]]:
        """Returns list of incoming email objects."""
        raise NotImplementedError

    def wait_for_otp(self, email: str, token: Any, timeout_seconds: int = 60, poll_interval: int = 3) -> Optional[str]:
        """Polls inbox until OTP is found or timeout occurs."""
        start_time = time.time()
        logger.info(f"Waiting for OTP on {email} (max {timeout_seconds}s)...")
        while time.time() - start_time < timeout_seconds:
            try:
                emails = self.fetch_inbox(email, token)
                for mail in emails:
                    subject = mail.get("subject", "")
                    body = mail.get("body", "") or mail.get("text", "") or mail.get("intro", "")
                    content = f"{subject} {body}"
                    matches = OTP_REGEX.findall(content)
                    if matches:
                        otp = matches[0]
                        logger.info(f"Found OTP {otp} for {email}")
                        return otp
            except Exception as e:
                logger.debug(f"Polling inbox error on {email}: {e}")
            time.sleep(poll_interval)

        logger.warning(f"OTP timeout on {email}")
        return None


class TempMailLolProvider(BaseMailProvider):
    """TempMail.lol integration (API v2)."""

    BASE_URL = "https://api.tempmail.lol/v2"

    def create_mailbox(self) -> Tuple[str, str]:
        resp = self.session.get(f"{self.BASE_URL}/inbox/create", timeout=self.timeout).json()
        address = resp.get("address")
        token = resp.get("token")
        return address, token

    def fetch_inbox(self, email: str, token: str) -> List[Dict[str, Any]]:
        resp = self.session.get(f"{self.BASE_URL}/inbox", params={"token": token}, timeout=self.timeout).json()
        return resp.get("emails", [])


class MailTmProvider(BaseMailProvider):
    """Mail.tm integration."""

    BASE_URL = "https://api.mail.tm"

    def create_mailbox(self) -> Tuple[str, str]:
        # 1. Fetch available domain
        domains_resp = self.session.get(f"{self.BASE_URL}/domains", timeout=self.timeout).json()
        domain = domains_resp["hydra:member"][0]["domain"]
        user = "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(10))
        email = f"{user}@{domain}"
        password = secrets.token_hex(8)

        # 2. Create account
        self.session.post(
            f"{self.BASE_URL}/accounts",
            json={"address": email, "password": password},
            timeout=self.timeout,
        )

        # 3. Get JWT token
        token_resp = self.session.post(
            f"{self.BASE_URL}/token",
            json={"address": email, "password": password},
            timeout=self.timeout,
        ).json()
        jwt_token = token_resp["token"]
        return email, jwt_token

    def fetch_inbox(self, email: str, token: str) -> List[Dict[str, Any]]:
        headers = {"Authorization": f"Bearer {token}"}
        resp = self.session.get(f"{self.BASE_URL}/messages", headers=headers, timeout=self.timeout).json()
        messages = resp.get("hydra:member", [])
        results = []
        for msg in messages:
            msg_id = msg["id"]
            detail = self.session.get(f"{self.BASE_URL}/messages/{msg_id}", headers=headers, timeout=self.timeout).json()
            results.append({
                "subject": detail.get("subject", ""),
                "body": detail.get("text", "") or detail.get("html", [""])[0],
            })
        return results


def get_mail_provider(name: str = "tempmail_lol", proxy: Optional[str] = None) -> BaseMailProvider:
    """Factory for mail provider instances."""
    name_lower = name.lower()
    if "mail_tm" in name_lower:
        return MailTmProvider(proxy=proxy)
    # Default to TempMail.lol
    return TempMailLolProvider(proxy=proxy)
