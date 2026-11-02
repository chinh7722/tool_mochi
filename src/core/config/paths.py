"""One writable data directory for desktop and Linux service deployments."""
import os
from pathlib import Path


def data_directory():
    configured = os.environ.get('HOSHINO_DATA_DIR', '').strip()
    if configured:
        return Path(configured).expanduser().resolve()
    base = Path(__file__).resolve().parents[2]
    if base.name == 'src':
        base = base.parent
    return base / 'data'
