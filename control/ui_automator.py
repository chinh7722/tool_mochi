"""
Local Desktop UI Automator (control.ui_automator).

Full Python implementation for interacting directly with the native GUI
of MiraiMind / TTM-Tool by Hermit Coder on Windows.

Supports:
1. "Miraimind tool - by Hermit Coder" (Standalone MiraiMind Client v2.6.3)
2. "TTM-Tool - by Hermit Coder" (Multi-Platform Client v3.6.7)
"""
from __future__ import annotations

import sys
from pathlib import Path

# Clean any reference to parent directory containing Python 3.11 .pyd files
_parent = Path(__file__).resolve().parent.parent
sys.path = [p for p in sys.path if p and p != "." and Path(p).resolve() != _parent]

import ctypes
import ctypes.wintypes
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("UIAutomator")

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="uiautomation")
warnings.filterwarnings("ignore", module="comtypes")

try:
    import uiautomation as auto
    _HAS_UIA = True
except ImportError:
    _HAS_UIA = False


def _run_on_default_desktop(
    func: Callable[..., Any], *args: Any, _timeout: Optional[float] = 5.0, **kwargs: Any
) -> Any:
    """
    Executes a function inside a fresh worker thread attached to WinSta0\\default.
    Initializes COM (CoInitialize) and UIAutomationInitializerInThread so UIAutomation works safely.
    """
    box: Dict[str, Any] = {"res": None, "err": None}

    def worker() -> None:
        h_default = None
        previous_desktop = None
        try:
            user32 = ctypes.windll.user32
            # Desktop handles are pointer-sized on 64-bit Windows.
            user32.OpenDesktopW.argtypes = [ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD,
                                           ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
            user32.OpenDesktopW.restype = ctypes.wintypes.HANDLE
            user32.GetThreadDesktop.argtypes = [ctypes.wintypes.DWORD]
            user32.GetThreadDesktop.restype = ctypes.wintypes.HANDLE
            user32.SetThreadDesktop.argtypes = [ctypes.wintypes.HANDLE]
            user32.CloseDesktop.argtypes = [ctypes.wintypes.HANDLE]
            previous_desktop = user32.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())
            h_default = user32.OpenDesktopW("default", 0, False, 0x01FF)
            if h_default:
                user32.SetThreadDesktop(h_default)

            ctypes.windll.ole32.CoInitialize(None)
            try:
                if _HAS_UIA and hasattr(auto, "UIAutomationInitializerInThread"):
                    with auto.UIAutomationInitializerInThread():
                        box["res"] = func(*args, **kwargs)
                else:
                    box["res"] = func(*args, **kwargs)
            finally:
                ctypes.windll.ole32.CoUninitialize()
        except Exception as e:
            box["err"] = e
        finally:
            if h_default:
                if previous_desktop:
                    user32.SetThreadDesktop(previous_desktop)
                user32.CloseDesktop(h_default)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=_timeout)

    if t.is_alive():
        raise TimeoutError("Quá thời gian chờ đọc giao diện bản local.")
    if box["err"]:
        raise box["err"]
    return box["res"]


class LocalAppAutomator:
    """Controls the local tool native GUI via Windows Accessibility & UIAutomation."""

    WINDOW_TITLES = [
        "Miraimind tool - by Hermit Coder",
        "TTM-Tool - by Hermit Coder",
        "by Hermit Coder",
    ]

    def __init__(self, log_callback: Optional[Callable[[str], None]] = None):
        self.log_callback = log_callback or (lambda msg: logger.info(msg))
        self.hwnd: Optional[int] = None
        self.window_title: str = ""

    def _log(self, msg: str) -> None:
        self.log_callback(msg)

    # ─── 1. WINDOW DETECTION ─────────────────────────────────────────────────────
    def find_local_window(self) -> Optional[int]:
        """Finds any running Hermit Coder tool window on the user desktop."""
        import os
        my_pid = os.getpid()

        def _scan() -> Optional[Tuple[int, str]]:
            user32 = ctypes.windll.user32
            found: List[Tuple[int, int, str]] = []

            def cb(h: int, _: int) -> bool:
                # Do NOT inspect windows belonging to our own process (prevents WM_GETTEXT deadlock!)
                pid = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                if pid.value == my_pid:
                    return True

                l = user32.GetWindowTextLengthW(h)
                if l > 0:
                    b = ctypes.create_unicode_buffer(l + 1)
                    user32.GetWindowTextW(h, b, l + 1)
                    t = b.value
                    t_low = t.lower()
                    if any(x in t_low for x in ["visual studio", "file explorer", "cmd", "antigravity", ".py"]):
                        return True
                    for priority, target in enumerate(self.WINDOW_TITLES):
                        if target.lower() in t_low:
                            found.append((priority, h, t))
                            break
                return True

            user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)(cb), 0)
            if found:
                found.sort(key=lambda x: x[0])  # Prioritize Miraimind tool first
                return (found[0][1], found[0][2])
            return None

        res = _run_on_default_desktop(_scan)
        if res:
            self.hwnd, self.window_title = res
            self._log(f"[+] Tìm thấy cửa sổ: '{self.window_title}' (HWND: {self.hwnd})")
            return self.hwnd

        self._log("[-] Chưa thấy cửa sổ Miraimind tool nào đang mở.")
        return None

    # ─── 2. RECURSIVE ELEMENT SEARCH HELPERS ──────────────────────────────────────
    @staticmethod
    def _find_by_class(ctrl: Any, cls_name: str) -> Optional[Any]:
        if ctrl.ClassName == cls_name:
            return ctrl
        for c in ctrl.GetChildren():
            res = LocalAppAutomator._find_by_class(c, cls_name)
            if res:
                return res
        return None

    @staticmethod
    def _find_all_by_class(ctrl: Any, cls_name: str, out: Optional[List[Any]] = None) -> List[Any]:
        if out is None:
            out = []
        if ctrl.ClassName == cls_name:
            out.append(ctrl)
        for c in ctrl.GetChildren():
            LocalAppAutomator._find_all_by_class(c, cls_name, out)
        return out

    @staticmethod
    def _find_by_name(ctrl: Any, text: str, ctrl_type: Optional[str] = None) -> Optional[Any]:
        matches_type = True if not ctrl_type else (ctrl.ControlTypeName == ctrl_type)
        if matches_type and text.lower() in ctrl.Name.lower():
            return ctrl
        for c in ctrl.GetChildren():
            res = LocalAppAutomator._find_by_name(c, text, ctrl_type)
            if res:
                return res
        return None

    @staticmethod
    def _qt_background_press(ctrl: Any, label: str) -> Callable[[], None]:
        """Qt's UIA Invoke calls animateClick(), which focuses checkable buttons.

        Address mouse messages to the Qt window instead. These are window-local
        messages: no OS mouse input, cursor movement, or WM_MOUSEACTIVATE.
        """
        def execute() -> None:
            if not ctrl.IsEnabled or ctrl.IsOffscreen:
                raise RuntimeError(f"'{label}' đang bị ẩn hoặc vô hiệu hóa; chưa thực hiện thao tác.")
            rect = ctrl.BoundingRectangle
            if rect.right <= rect.left or rect.bottom <= rect.top:
                raise RuntimeError(f"Không xác định được vùng nút '{label}'.")
            ancestor = ctrl
            while ancestor and not ancestor.NativeWindowHandle:
                ancestor = ancestor.GetParentControl()
            if not ancestor:
                raise RuntimeError(f"Không xác định được cửa sổ chứa '{label}'.")
            hwnd = ancestor.NativeWindowHandle
            user32 = ctypes.windll.user32
            user32.ScreenToClient.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.POINT)]
            user32.PostMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT,
                                           ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
            user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.wintypes.HANDLE]
            user32.SetThreadDpiAwarenessContext.restype = ctypes.wintypes.HANDLE
            # UIA returns physical pixels. Convert in a per-monitor-aware thread
            # so Windows DPI virtualization doesn't shift the addressed point.
            previous_dpi = user32.SetThreadDpiAwarenessContext(-4)
            try:
                point = ctypes.wintypes.POINT((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
                if not user32.ScreenToClient(hwnd, ctypes.byref(point)):
                    raise RuntimeError(f"Không đổi được tọa độ nút '{label}'.")
                if not (-32768 <= point.x <= 32767 and -32768 <= point.y <= 32767):
                    raise RuntimeError(f"Tọa độ nút '{label}' nằm ngoài phạm vi thông điệp Windows.")
                position = (point.x & 0xFFFF) | ((point.y & 0xFFFF) << 16)
                if not user32.PostMessageW(hwnd, 0x0201, 1, position):  # WM_LBUTTONDOWN, MK_LBUTTON
                    raise RuntimeError(f"Không gửi được lệnh nhấn nền cho '{label}'.")
                if not user32.PostMessageW(hwnd, 0x0202, 0, position):  # WM_LBUTTONUP
                    raise RuntimeError(f"Không gửi được lệnh thả nền cho '{label}'; hãy kiểm tra cửa sổ local.")
            finally:
                if previous_dpi:
                    user32.SetThreadDpiAwarenessContext(previous_dpi)

        return execute

    @staticmethod
    def _background_action(ctrl: Any, label: str) -> Callable[[], None]:
        """Resolve a UIA action without mouse input, keyboard input or focus changes.

        Only an unsupported pattern permits trying another pattern. Once an action
        starts, propagate its failure; retrying a submit could create a duplicate job.
        """
        if ctrl.ControlTypeName == "TabItemControl":
            pattern = ctrl.GetPattern(auto.PatternId.SelectionItemPattern)
            if pattern:
                if pattern.IsSelected:
                    return lambda: None
            # Qt's accessible tab selection also focuses its parent window.
            return LocalAppAutomator._qt_background_press(ctrl, label)
        else:
            method = None
            if ctrl.ControlTypeName == "CheckBoxControl":
                toggle = ctrl.GetPattern(auto.PatternId.TogglePattern)
                if toggle and toggle.ToggleState == auto.ToggleState.On:
                    return lambda: None
                if ctrl.ClassName in ("QPushButton", "QToolButton"):
                    return LocalAppAutomator._qt_background_press(ctrl, label)

        if method is None:
            pattern = ctrl.GetPattern(auto.PatternId.InvokePattern)
            if pattern:
                method = pattern.Invoke
        if method is None:
            pattern = ctrl.GetPattern(auto.PatternId.LegacyIAccessiblePattern)
            if pattern and pattern.DefaultAction:
                method = pattern.DoDefaultAction
        if method is None:
            raise RuntimeError(f"'{label}' không hỗ trợ thao tác nền. Không sử dụng chuột hoặc bàn phím thay thế.")

        def execute() -> None:
            if method(waitTime=0) is False:
                raise RuntimeError(f"Thao tác nền thất bại tại '{label}'; hãy kiểm tra giao diện local trước khi thử lại.")

        return execute

    @staticmethod
    def _background_value_pattern(ctrl: Any, label: str) -> Any:
        pattern = ctrl.GetPattern(auto.PatternId.LegacyIAccessiblePattern)
        if not pattern:
            pattern = ctrl.GetPattern(auto.PatternId.ValuePattern)
        if not pattern:
            raise RuntimeError(f"Ô '{label}' không hỗ trợ nhập nền. Không sử dụng chuột hoặc bàn phím thay thế.")
        if getattr(pattern, "IsReadOnly", False):
            raise RuntimeError(f"Ô '{label}' đang ở chế độ chỉ đọc.")
        return pattern

    # ─── 3. FORM FILLING & SUBMISSION ON LOCAL GUI ──────────────────────────────
    def _run_without_activation(self, action: Callable[[], Any]) -> Any:
        """Keep accessibility providers from activating this background window.

        Restore only the bit we added, preserving other style changes by the app.
        This guard never changes the foreground window or restores cursor position.
        """
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
        no_activate = 0x08000000  # WS_EX_NOACTIVATE

        def get_style() -> int:
            ctypes.set_last_error(0)
            style = user32.GetWindowLongW(self.hwnd, -20)  # GWL_EXSTYLE
            if not style and ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())
            return style

        def set_style(style: int) -> None:
            ctypes.set_last_error(0)
            previous = user32.SetWindowLongW(self.hwnd, -20, style)
            if not previous and ctypes.get_last_error():
                raise ctypes.WinError(ctypes.get_last_error())

        original = get_style()
        added = not (original & no_activate)
        if added:
            set_style(original | no_activate)
        try:
            return action()
        finally:
            if added and user32.IsWindow(self.hwnd):
                set_style(get_style() & ~no_activate)

    def enter_job_and_run(
        self,
        invitation_code: str,
        target_points: int = 2000,
        job_name: Optional[str] = None,
        unit_price: int = 0,
    ) -> Dict[str, Any]:
        """
        Operates directly on the GUI of the running local tool:
        Navigate, fill and submit using UIA patterns in the background.
        Never move the mouse, send keyboard input or activate the local window.
        """
        if not self.hwnd and not self.find_local_window():
            raise RuntimeError("Không tìm thấy cửa sổ Miraimind tool nào đang chạy trên máy.")

        if not _HAS_UIA:
            raise RuntimeError("Chưa cài thư viện uiautomation để thao tác nền trên giao diện local.")
        self._log("[*] Thao tác nền trên giao diện local — không sử dụng chuột/bàn phím.")

        def _do_automate() -> Dict[str, Any]:
            import uiautomation as auto
            win = auto.ControlFromHandle(self.hwnd)
            if not win.Exists(0, 0):
                raise RuntimeError("Cửa sổ tool đã bị đóng hoặc không phản hồi.")

            # Step 1: Sidebar Navigation (if on TTM-Tool, click '  MiraiMind'; if on standalone, click '  Jobs')
            sidebar_btn = self._find_by_name(win, "MiraiMind", "CheckBoxControl") or \
                          self._find_by_name(win, "Jobs", "CheckBoxControl") or \
                          self._find_by_name(win, "MiraiMind", "ButtonControl") or \
                          self._find_by_name(win, "Jobs", "ButtonControl")
            if sidebar_btn:
                self._log(f"[*] Chuyển mục sidebar -> {sidebar_btn.Name.strip()}...")
                self._background_action(sidebar_btn, sidebar_btn.Name.strip())()
                time.sleep(0.3)

            # Step 2: Tab navigation (if tabbed interface exists, ensure 'Thiết lập job')
            tab_setup = self._find_by_name(win, "Thiết lập job", "TabItemControl")
            if tab_setup:
                self._background_action(tab_setup, "Thiết lập job")()
                time.sleep(0.2)

            # Step 3: Locate fields by robust label context
            def find_edit_by_label(lbl_pattern: str) -> Optional[Any]:
                all_labels = self._find_all_by_class(win, "QLabel")
                for lbl in all_labels:
                    if lbl_pattern.lower() in lbl.Name.lower():
                        parent = lbl.GetParentControl()
                        if parent:
                            for child in parent.GetChildren():
                                if "LineEdit" in child.ClassName or child.ControlTypeName == "EditControl":
                                    return child
                return None

            final_job_name = job_name or f"Nạp mã {invitation_code}"
            code_field = find_edit_by_label("Mã mời (*)") or find_edit_by_label("Mã mời") or self._find_by_class(win, "InviteCodeLineEdit")
            fields = [
                ("Mã mời", code_field, invitation_code),
                ("Tên job", find_edit_by_label("Tên job"), final_job_name),
                ("Điểm mục tiêu", find_edit_by_label("Điểm mục tiêu"), str(target_points)),
                ("Tổng tiền", find_edit_by_label("Tổng tiền"), str(unit_price)),
            ]
            # Resolve every field and submit capability before changing form values.
            prepared = []
            for label, field, value in fields:
                if not field:
                    raise RuntimeError(f"Không định vị được ô '{label}' trên giao diện local; chưa gửi job.")
                prepared.append((label, self._background_value_pattern(field, label), value))

            run_btn = self._find_by_name(win, "chạy job", "ButtonControl")
            if not run_btn:
                raise RuntimeError("Không tìm thấy nút 'Tạo & chạy job' trên giao diện local.")
            submit = self._background_action(run_btn, "Tạo & chạy job")

            for label, pattern, value in prepared:
                if pattern.SetValue(value, waitTime=0) is False or pattern.Value != value:
                    raise RuntimeError(f"Không xác nhận được dữ liệu ô '{label}'; chưa gửi job.")
                self._log(f"[✔] Đã nhập nền {label}: '{value}'")
            # Recheck all fields immediately before submission (including user edits).
            for label, pattern, value in prepared:
                if pattern.Value != value:
                    raise RuntimeError(f"Dữ liệu ô '{label}' đã thay đổi; chưa gửi job.")

            self._log("[*] Đang kích hoạt nền nút '▶ Tạo & chạy job'...")
            submit()
            time.sleep(0.8)
            self._log("[★] Đã kích hoạt job thành công trên giao diện bản local!")

            return {
                "status": "success",
                "invitation_code": invitation_code,
                "target_points": target_points,
                "target_accounts": max(1, target_points // 200),
                "job_name": final_job_name,
                "filled_fields": [label for label, _, _ in prepared],
            }

        # This runs inside the controller's worker, never its GUI thread. Wait for
        # the real outcome; a timed-out mutation must not continue as a hidden job.
        def _do_in_background() -> Dict[str, Any]:
            return self._run_without_activation(_do_automate)

        return _run_on_default_desktop(_do_in_background, _timeout=None)

    # ─── 4. READ PROGRESS FROM LOCAL APP TABLE ──────────────────────────────────
    def read_active_jobs_from_ui(self) -> List[Dict[str, str]]:
        """Reads the rows currently displayed in the local app's 'Job đang chạy' table."""
        def _read() -> List[Dict[str, str]]:
            import uiautomation as auto
            win = auto.ControlFromHandle(self.hwnd)
            data_items = self._find_all_by_class(win, "")
            # Filter DataItemControl elements
            items = [c for c in data_items if c.ControlTypeName == "DataItemControl"]
            rows: List[Dict[str, str]] = []

            # Each row in Hermit table has 9 cells:
            # [Thời gian, Tên job, Loại, Đối tượng, Trạng thái, Tiến độ, Điểm, Tổng tiền, Thao tác]
            step = 9
            for i in range(0, len(items), step):
                chunk = items[i:i + step]
                if len(chunk) >= 6:
                    row_data = {
                        "time": chunk[0].Name,
                        "job_name": chunk[1].Name,
                        "type": chunk[2].Name,
                        "code": chunk[3].Name,
                        "status": chunk[4].Name,
                        "progress": chunk[5].Name,
                        "points": chunk[6].Name if len(chunk) > 6 else "",
                        "money": chunk[7].Name if len(chunk) > 7 else "",
                    }
                    rows.append(row_data)
            return rows

        return _run_on_default_desktop(_read)
