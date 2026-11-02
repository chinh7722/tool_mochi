"""Regression checks; creates jobs only in an isolated Qt test window.

Run from control/: python -I test_background_automation.py
"""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_automator as ui


class Control:
    def __init__(self, name="", cls="QWidget", kind="GroupControl", children=(), patterns=None):
        self.Name, self.ClassName, self.ControlTypeName = name, cls, kind
        self.children, self.patterns = list(children), patterns or {}
        for child in self.children:
            child.parent = self

    def GetChildren(self):
        return self.children

    def GetParentControl(self):
        return self.parent

    def GetPattern(self, pattern_id):
        return self.patterns.get(pattern_id)

    def Exists(self, *args):
        return True

    def Click(self, *args, **kwargs):
        raise AssertionError("Physical mouse input is forbidden")

    SendKeys = SetFocus = Click


def form():
    patterns, groups = [], []
    for label in ("Mã mời (*)", "Tên job/khách hàng", "Điểm mục tiêu (*)", "Tổng tiền (VND)"):
        pattern = Mock(IsReadOnly=False, Value="")

        def set_value(value, waitTime=0, pattern=pattern):
            pattern.Value = value
            return True

        pattern.SetValue.side_effect = set_value
        patterns.append(pattern)
        groups.append(Control(children=[
            Control(label, "QLabel", "TextControl"),
            Control(cls="QLineEdit", kind="EditControl", patterns={ui.auto.PatternId.ValuePattern: pattern}),
        ]))
    invoke = Mock()
    invoke.Invoke.return_value = True
    submit = Control("Tạo && chạy job", "LoadingButton", "ButtonControl",
                     patterns={ui.auto.PatternId.InvokePattern: invoke})
    return Control(children=[*groups, submit]), patterns, invoke


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        self.root, self.values, self.submit = form()
        self.app = ui.LocalAppAutomator()
        self.app.hwnd = 123
        self.patches = [
            patch.object(ui.auto, "ControlFromHandle", return_value=self.root),
            patch.object(ui, "_run_on_default_desktop", side_effect=lambda fn, **kw: fn()),
            patch.object(ui.LocalAppAutomator, "_run_without_activation", side_effect=lambda fn: fn()),
            patch.object(ui.time, "sleep"),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def run_form(self):
        return self.app.enter_job_and_run("abc123", 2000, "Tên {job} + tiếng Việt", 12000)

    def test_fill_and_submit_without_physical_input(self):
        result = self.run_form()
        self.assertEqual([p.Value for p in self.values],
                         ["abc123", "Tên {job} + tiếng Việt", "2000", "12000"])
        self.assertEqual(result["status"], "success")
        self.submit.Invoke.assert_called_once_with(waitTime=0)
        ui._run_on_default_desktop.assert_called_once()
        self.assertIsNone(ui._run_on_default_desktop.call_args.kwargs["_timeout"])

    def test_missing_field_never_submits_or_writes_other_fields(self):
        self.root.children.pop(2)
        with self.assertRaisesRegex(RuntimeError, "Điểm mục tiêu"):
            self.run_form()
        self.submit.Invoke.assert_not_called()
        for value in self.values:
            value.SetValue.assert_not_called()

    def test_unsupported_value_never_uses_input_or_submits(self):
        self.root.children[0].children[1].patterns.clear()
        with self.assertRaisesRegex(RuntimeError, "không hỗ trợ nhập nền"):
            self.run_form()
        self.submit.Invoke.assert_not_called()

    def test_rejected_value_never_submits(self):
        self.values[2].SetValue.side_effect = None
        self.values[2].SetValue.return_value = False
        with self.assertRaisesRegex(RuntimeError, "Điểm mục tiêu"):
            self.run_form()
        self.submit.Invoke.assert_not_called()

    def test_later_field_change_prevents_submission(self):
        def mutate(value, **kwargs):
            self.values[3].Value = value
            self.values[0].Value = "changed"
            return True
        self.values[3].SetValue.side_effect = mutate
        with self.assertRaisesRegex(RuntimeError, "đã thay đổi"):
            self.run_form()
        self.submit.Invoke.assert_not_called()

    def test_failed_submit_is_not_retried(self):
        self.submit.Invoke.side_effect = RuntimeError("provider error")
        with self.assertRaisesRegex(RuntimeError, "provider error"):
            self.run_form()
        self.submit.Invoke.assert_called_once()

    def test_selected_sidebar_is_not_toggled_off(self):
        toggle = Mock(ToggleState=ui.auto.ToggleState.On)
        sidebar = Control("Jobs", kind="CheckBoxControl", patterns={
            ui.auto.PatternId.TogglePattern: toggle,
            ui.auto.PatternId.InvokePattern: self.submit,
        })
        self.app._background_action(sidebar, "Jobs")()
        self.submit.Invoke.assert_not_called()

    def test_tab_selection_uses_window_messages_instead_of_focusing_uia_select(self):
        selection = Mock(IsSelected=False)
        selection.Select.return_value = True
        tab = Control(kind="TabItemControl", patterns={ui.auto.PatternId.SelectionItemPattern: selection})
        with patch.object(ui.LocalAppAutomator, "_qt_background_press") as press:
            self.app._background_action(tab, "Thiết lập job")()
            press.assert_called_once_with(tab, "Thiết lập job")
            press.return_value.assert_called_once_with()
        selection.Select.assert_not_called()

    def test_gui_failure_does_not_create_api_job(self):
        from gui import AutomationWorkerThread
        worker = AutomationWorkerThread("abc123", 2000, "Test", 0)
        worker.automator = Mock()
        worker.automator.enter_job_and_run.side_effect = RuntimeError("unsupported")
        worker.bridge = Mock()
        worker.bridge.get_jobs.return_value = {"items": []}
        errors = []
        worker.error_signal.connect(errors.append)
        worker.run()
        self.assertEqual(errors, ["unsupported"])
        worker.bridge.create_job.assert_not_called()
        worker.bridge.get_jobs.assert_called_once()


def qt_fixture(info_path, result_path):
    """Real Qt provider, with no backend and no foreground activation."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, QTabWidget
    app = QApplication([])
    window = QWidget()
    window.setWindowTitle("Background automation regression fixture")
    window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    layout = QVBoxLayout(window)
    sidebar = QPushButton("Jobs")
    sidebar.setCheckable(True)
    layout.addWidget(sidebar)
    tabs = QTabWidget()
    setup = QWidget()
    form_layout = QVBoxLayout(setup)
    tabs.addTab(setup, "Thiết lập job")
    tabs.addTab(QWidget(), "Other")
    tabs.setCurrentIndex(1)
    layout.addWidget(tabs)
    edits = []
    for text in ("Mã mời (*)", "Tên job/khách hàng", "Điểm mục tiêu (*)", "Tổng tiền (VND)"):
        group = QWidget()
        group_layout = QVBoxLayout(group)
        group_layout.addWidget(QLabel(text))
        edit = QLineEdit()
        edits.append(edit)
        group_layout.addWidget(edit)
        form_layout.addWidget(group)
    submit = QPushButton("Tạo && chạy job")
    submit.clicked.connect(lambda: Path(result_path).write_text(json.dumps([edit.text() for edit in edits]), encoding="utf-8"))
    form_layout.addWidget(submit)
    window.show()
    # A second window covers the form without taking focus from the user's app.
    cover = QWidget()
    cover.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    cover.setGeometry(window.geometry())
    cover.show()
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HWND,
                                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                    ctypes.wintypes.UINT]
    # Keep both test windows underneath the user's applications. Showing a test
    # window above their work would let real mouse clicks accidentally activate it.
    user32.SetWindowPos(int(cover.winId()), 1, 0, 0, 0, 0, 0x13)
    user32.SetWindowPos(int(window.winId()), 1, 0, 0, 0, 0, 0x13)
    Path(info_path).write_text(str(int(window.winId())), encoding="utf-8")
    app.exec()


class MouseMonitor:
    """Count real/injected mouse events without recording the user's input."""
    def __enter__(self):
        self.real, self.injected = 0, 0
        self.error = None
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self._start, daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("Mouse monitor did not start")
        if self.error:
            raise self.error
        return self

    def _start(self):
        try:
            ui._run_on_default_desktop(self._listen, _timeout=None)
        except Exception as exc:
            self.error = exc
            self.ready.set()

    def _listen(self):
        class MouseEvent(ctypes.Structure):
            _fields_ = [("pt", ctypes.wintypes.POINT), ("data", ctypes.wintypes.DWORD),
                        ("flags", ctypes.wintypes.DWORD), ("time", ctypes.wintypes.DWORD),
                        ("extra", ctypes.c_size_t)]

        user32 = ctypes.windll.user32
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                          ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
        user32.CallNextHookEx.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_int,
                                         ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
        user32.CallNextHookEx.restype = ctypes.c_ssize_t

        def observe(code, message, data):
            if code >= 0:
                if ctypes.cast(data, ctypes.POINTER(MouseEvent)).contents.flags & 1:
                    self.injected += 1
                else:
                    self.real += 1
            return user32.CallNextHookEx(None, code, message, data)

        callback = callback_type(observe)
        user32.SetWindowsHookExW.argtypes = [ctypes.c_int, callback_type,
                                            ctypes.wintypes.HINSTANCE, ctypes.wintypes.DWORD]
        user32.SetWindowsHookExW.restype = ctypes.wintypes.HANDLE
        user32.UnhookWindowsHookEx.argtypes = [ctypes.wintypes.HANDLE]
        hook = user32.SetWindowsHookExW(14, callback, None, 0)  # WH_MOUSE_LL
        if not hook:
            raise ctypes.WinError()
        try:
            self.thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
            message = ctypes.wintypes.MSG()
            # Create the message queue before another thread can post WM_QUIT.
            user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            self.ready.set()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(hook)

    def __exit__(self, *args):
        ctypes.windll.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
        self.thread.join(5)
        if self.thread.is_alive():
            raise RuntimeError("Mouse monitor did not stop")


class QtIntegrationTests(unittest.TestCase):
    def test_real_qt_form_while_covered_preserves_mouse_and_foreground(self):
        with tempfile.TemporaryDirectory(prefix="mirai-background-") as folder:
            info, result = Path(folder) / "hwnd.txt", Path(folder) / "result.json"
            process = subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).resolve()), "--fixture", str(info), str(result)],
                creationflags=subprocess.CREATE_NO_WINDOW,
                # Other Qt tests use offscreen. This fixture needs real HWNDs, even
                # when discovered in the same process as those test modules.
                env={key: value for key, value in os.environ.items() if key != "QT_QPA_PLATFORM"},
            )
            try:
                deadline = time.monotonic() + 10
                while not info.exists() and time.monotonic() < deadline:
                    self.assertIsNone(process.poll(), "Qt fixture exited before startup")
                    time.sleep(0.05)
                self.assertTrue(info.exists(), "Qt fixture did not start")
                automator = ui.LocalAppAutomator()
                automator.hwnd = int(info.read_text(encoding="utf-8"))
                user32 = ctypes.windll.user32
                user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
                original_style = user32.GetWindowLongW(automator.hwnd, -20)

                def desktop_state():
                    pos = ctypes.wintypes.POINT()
                    user32 = ctypes.windll.user32
                    user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
                    user32.GetCursorPos(ctypes.byref(pos))
                    return user32.GetForegroundWindow(), (pos.x, pos.y)

                with MouseMonitor() as mouse:
                    before = ui._run_on_default_desktop(desktop_state)
                    data = automator.enter_job_and_run("test123", 2000, "Thử {job} tiếng Việt", 12000)
                    after = ui._run_on_default_desktop(desktop_state)
                self.assertEqual(data["status"], "success")
                self.assertEqual(json.loads(result.read_text(encoding="utf-8")),
                                 ["test123", "Thử {job} tiếng Việt", "2000", "12000"])
                self.assertNotEqual(after[0], automator.hwnd, "Automation activated the target window")
                self.assertEqual(mouse.injected, 0, "Automation injected mouse events")
                if mouse.real == 0:
                    self.assertEqual(before[1], after[1], "Automation moved the cursor")
                self.assertEqual(user32.GetWindowLongW(automator.hwnd, -20), original_style,
                                 "Window style was not restored after success")

                def fail_inside_guard():
                    self.assertTrue(user32.GetWindowLongW(automator.hwnd, -20) & 0x08000000)
                    raise RuntimeError("simulated provider failure")

                with self.assertRaisesRegex(RuntimeError, "simulated provider failure"):
                    ui._run_on_default_desktop(lambda: automator._run_without_activation(fail_inside_guard), _timeout=None)
                self.assertEqual(user32.GetWindowLongW(automator.hwnd, -20), original_style,
                                 "Window style was not restored after failure")
                print("Qt integration: no injected mouse events; target stayed in background.")
            finally:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--fixture":
        qt_fixture(sys.argv[2], sys.argv[3])
    else:
        unittest.main(verbosity=2)
