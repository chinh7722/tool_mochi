import sys
from pathlib import Path

# Ensure control dir is in sys.path and remove workspace root if present
this_dir = Path(__file__).resolve().parent
if str(this_dir) not in sys.path:
    sys.path.insert(0, str(this_dir))
parent_dir = str(this_dir.parent)
if parent_dir in sys.path:
    sys.path.remove(parent_dir)

from ui_automator import LocalAppAutomator

automator = LocalAppAutomator()
hwnd = automator.find_local_window()
print(f"Detected Local Window HWND: {hwnd}")
