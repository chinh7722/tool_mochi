"""Local credentials are configuration, never embedded in publishable source."""
import json
import os
from pathlib import Path


def local_backend_key():
    if os.environ.get("MIRAIMIND_LOCAL_API_KEY"):
        return os.environ["MIRAIMIND_LOCAL_API_KEY"]
    try:
        config = Path(__file__).resolve().parent.parent / "MiraimindLocal" / "local_config.json"
        return str(json.loads(config.read_text(encoding="utf-8"))["local_key"])
    except (OSError, ValueError, KeyError):
        return ""
