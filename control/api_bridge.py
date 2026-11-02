"""
Local Backend API Bridge (control.api_bridge).

Lightweight HTTP client interfacing directly with Miraimind Local backend (127.0.0.1:18888).
Provides zero-dependency fallback communications using standard library urllib.
"""
from __future__ import annotations

import json
import logging
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.parse
import urllib.request
from backend_config import local_backend_key

logger = logging.getLogger("ApiBridge")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18888
DEFAULT_KEY = local_backend_key()


class LocalBackendBridge:
    """Interacts with the local HTTP backend server."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        api_key: str = DEFAULT_KEY,
        timeout: int = 4,
    ):
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url += f"?{urllib.parse.urlencode(params)}"

        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                content = resp.read().decode("utf-8")
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            try:
                return json.loads(err_body)
            except Exception:
                return {"error": {"status": e.code, "message": str(e)}}
        except Exception as e:
            return {"error": {"message": str(e)}}

    def is_port_listening(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex((self.host, self.port)) == 0

    def check_health(self) -> Dict[str, Any]:
        return self._request("GET", "/health")

    def is_alive(self) -> bool:
        try:
            res = self.check_health()
            return res.get("application") == "miraimind-local" or res.get("database") == "ready"
        except Exception:
            return False

    def get_jobs(self, limit: int = 50, page: int = 1) -> Dict[str, Any]:
        return self._request("GET", "/api/jobs", params={"limit": limit, "page": page})

    def get_job(self, job_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/api/jobs/{job_id}")

    def create_job(
        self,
        invitation_code: str,
        target_count: int = 10,
        job_name: Optional[str] = None,
        job_type: str = "customer_invite",
        unit_price: int = 0,
    ) -> Dict[str, Any]:
        body = {
            "job_type": job_type,
            "target_count": target_count,
            "invitation_code": invitation_code,
            "job_name": job_name or f"Nạp mã {invitation_code}",
            "job_payload": {"invitation_code": invitation_code},
            "unit_price": unit_price,
        }
        return self._request("POST", "/api/jobs/start", body=body)

    def cancel_job(self, job_id: str) -> Dict[str, Any]:
        return self._request("POST", f"/api/jobs/{job_id}/finish", body={"status": "cancelled"})

    def get_recent_logs(self) -> List[Dict[str, Any]]:
        res = self._request("GET", "/api/logs/stream")
        return res.get("events", [])
