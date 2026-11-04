"""Open the desktop VPS manager using the shared settings and SSH connection."""
from pathlib import Path
import runpy
import sys

if __name__ == '__main__':
    entry=Path(__file__).resolve().with_name('main_mochi_gui.py')
    sys.argv=[str(entry),'--remote']
    runpy.run_path(str(entry),run_name='__main__')
