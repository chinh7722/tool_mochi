"""Open the local browser manager; automation continues on the VPS."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != ROOT]
sys.path.insert(0, str(ROOT/'src'))

if __name__ == '__main__':
    from core.remote.local_web import main
    main()
