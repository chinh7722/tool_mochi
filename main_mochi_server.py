"""Linux service entry point. All workers live here, independently of clients."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:] = [p for p in sys.path if p and Path(p).resolve() != ROOT]
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

if __name__ == '__main__':
    from core.remote.server import main
    main()
