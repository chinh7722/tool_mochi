"""Keep the supplied Windows binary dependencies out of Python's import path."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path[:] = [str(ROOT / "control"), str(ROOT / "clone"), str(ROOT / "vps")] + [
    path for path in sys.path if path and Path(path).resolve() != ROOT
    and path not in (str(ROOT / "control"), str(ROOT / "clone"), str(ROOT / "vps"))]
