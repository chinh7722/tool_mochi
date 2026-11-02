"""
Desktop Control Panel GUI (control.gui).

Full Python GUI for controlling the local MiraiMind application:
- Auto-detects and connects to the local tool window.
- Inputs jobs directly on the local GUI with 1-click.
- Real-time progress monitoring dashboard (live percentage, accounts, points, logs).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import threading
from typing import Any, Optional
import warnings

# Ensure control folder is in sys.path and remove workspace root & empty strings
THIS_DIR = Path(__file__).resolve().parent
parent_dir = THIS_DIR.parent
cleaned_path = [str(THIS_DIR)]
for p in sys.path:
    if not p or p == ".":
        continue
    try:
        if Path(p).resolve() == parent_dir:
            continue
    except Exception:
        pass
    if p != str(THIS_DIR) and p not in cleaned_path:
        cleaned_path.append(p)
sys.path = cleaned_path

# Suppress harmless 3rd-party warnings on Python 3.15 & Qt DPI messages
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", module="uiautomation")
warnings.filterwarnings("ignore", module="comtypes")
os.environ["QT_LOGGING_RULES"] = "qt.qpa.*=false;qt.qpa.window=false"

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QWidget,
)

from api_bridge import LocalBackendBridge
from desktop_pages import DashboardPage, SettingsPage, Sidebar, ASSETS
from job_queue import JobQueue, QueuedJob, parse_codes
from job_worker import AutomationWorkerThread
from jobs_page import JobsPage
from telegram_page import TelegramPage
from telegram_store import TelegramStore
from ui_automator import LocalAppAutomator

logger = logging.getLogger("ControlGUI")


class StatusPollingThread(QThread):
    status_updated = pyqtSignal(bool, str, int, bool, list)

    def __init__(self):
        super().__init__()
        self.running = True
        self._stop_event = threading.Event()
        self.automator = LocalAppAutomator()
        self.bridge = LocalBackendBridge()

    def run(self):
        while self.running:
            try:
                hwnd = self.automator.find_local_window() or 0
                title = self.automator.window_title if hwnd else ""
                alive = self.bridge.is_alive()
                jobs = []
                if alive:
                    try:
                        data = self.bridge.get_jobs(limit=12)
                        jobs = data.get("items", [])
                    except Exception:
                        pass
                self.status_updated.emit(bool(hwnd), title, hwnd, alive, jobs)
            except Exception:
                pass
            self._stop_event.wait(2.5)

    def stop(self):
        self.running = False
        self._stop_event.set()


class ControlMainWindow(QMainWindow):
    """Main window for MiraiMind Local Controller."""

    def __init__(self, state_path=None, auto_start_telegram=True):
        super().__init__()
        self.automator = LocalAppAutomator()
        self.bridge = LocalBackendBridge()
        self.telegram_store = TelegramStore(state_path or THIS_DIR / "data" / "telegram.sqlite3")
        self._auto_start_telegram = auto_start_telegram
        self.worker: Optional[AutomationWorkerThread] = None
        self.job_queue = JobQueue()
        self.active_item = None
        self.queue_paused = False
        self._closing = False
        self._active_progress = None

        self.setWindowTitle("hoshino-tool")
        self.setWindowIcon(QIcon(str(ASSETS / "avatar.png")))
        self.resize(1280, 930)
        self.setMinimumSize(1120, 800)

        self._apply_theme()
        self._init_ui()
        self._restore_queue()
        self.dashboard_timer = QTimer(self)
        self.dashboard_timer.timeout.connect(self._refresh_dashboard)
        self.dashboard_timer.start(1500)
        self.queue_timer = QTimer(self)
        self.queue_timer.timeout.connect(self._pump_queue)
        self.queue_timer.start(1000)

        # Background status poller thread (NEVER blocks the GUI event loop)
        self.status_thread = StatusPollingThread()
        self.status_thread.status_updated.connect(self._on_status_updated)
        self.status_thread.start()

    def closeEvent(self, event: Any) -> None:
        self._closing = True
        self.dashboard_timer.stop()
        self.queue_timer.stop()
        self.jobs_page.shutdown()
        self.queue_paused = True
        if self.worker:
            self.worker.stop()
        if hasattr(self, "status_thread"):
            self.status_thread.stop()
        telegram_running = self.telegram_page.shutdown()
        if ((self.worker and self.worker.isRunning())
                or (hasattr(self, "status_thread") and self.status_thread.isRunning()) or telegram_running):
            event.ignore()
            self.btn_ui_run.setEnabled(False)
            self.btn_api_run.setEnabled(False)
            self.lbl_status.setText("Đang đóng Controller...")
            QTimer.singleShot(150, self.close)
            return
        event.accept()

    def _apply_theme(self) -> None:
        self.setStyleSheet("""
            QMainWindow, QWidget {
                background-color: #18191b;
                color: #e6edf7;
                font-family: 'Segoe UI';
                font-size: 13px;
            }
            QFrame.cardFrame {
                background-color: #222325;
                border: 1px solid #34363b;
                border-radius: 8px;
            }
            QLabel.cardTitle {
                color: #e6edf7;
                font-size: 14px;
                font-weight: bold;
            }
            QLineEdit {
                background-color: #202123;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 8px 12px;
                color: #f0f6fc;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #58a6ff;
            }
            QPushButton.primaryBtn {
                background-color: #2563eb;
                color: #ffffff;
                font-weight: bold;
                border: 1px solid #3476f7;
                border-radius: 6px;
                padding: 10px 18px;
                font-size: 13px;
            }
            QPushButton.primaryBtn:hover {
                background-color: #3476f7;
            }
            QPushButton.actionBlueBtn {
                background-color: #2563eb;
                color: #ffffff;
                font-weight: bold;
                border: 1px solid #388bfd;
                border-radius: 6px;
                padding: 10px 18px;
                font-size: 13px;
            }
            QPushButton.actionBlueBtn:hover {
                background-color: #388bfd;
            }
            QPushButton.secondaryBtn {
                background-color: #27282e;
                color: #c9d1d9;
                border: 1px solid #30363d;
                border-radius: 6px;
                padding: 8px 14px;
            }
            QPushButton.secondaryBtn:hover {
                background-color: #30363d;
            }
            QProgressBar {
                background-color: #21262d;
                border: 1px solid #30363d;
                border-radius: 6px;
                text-align: center;
                color: #ffffff;
                font-weight: bold;
                height: 24px;
            }
            QProgressBar::chunk {
                background-color: #238636;
                border-radius: 5px;
            }
            QTextEdit#logBox {
                background-color: #1e1f21;
                border: 1px solid #21262d;
                border-radius: 6px;
                color: #7ee787;
                font-family: 'Consolas', monospace;
                font-size: 12px;
                padding: 10px;
            }
            QTableWidget {
                background-color: #1e1f21;
                gridline-color: #21262d;
                border: 1px solid #30363d;
                border-radius: 6px;
                color: #c9d1d9;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #27282e;
                color: #58a6ff;
                font-weight: bold;
                padding: 6px;
                border: 1px solid #21262d;
            }
            QTableWidget::item:selected {
                background-color: #1f6feb;
                color: #ffffff;
            }
            QLabel { background: transparent; }
            QLabel.pageTitle { font-size: 26px; font-weight: 700; color: #f5f7fa; }
            QLabel.sectionTitle { font-size: 14px; font-weight: 700; color: #f5f7fa; }
            QLabel.muted { color: #a9bedb; font-size: 12px; }
            QLabel.metricHint { color: #91accf; font-size: 11px; }
            QFrame#sidebar { background: #202123; border-right: 1px solid #34363b; }
            QLabel#brandName { font-size: 25px; font-weight: 700; color: #edf5ff; }
            QLabel#brandSubtitle { color: #8faed7; font-size: 9px; }
            QLabel#navCaption { color: #7586a0; font-size: 10px; font-weight: 600; padding: 7px 12px; }
            QPushButton#navButton { background: transparent; color: #b4c7e4; border: none;
                border-radius: 8px; text-align: left; padding: 10px 14px; }
            QPushButton#navButton:hover { background: #2a303d; }
            QPushButton#navButton:checked { background: #2563eb; color: white; font-weight: 600; }
            QLabel#sidebarConnection { background: #27282e; border: 1px solid #393b43;
                border-radius: 8px; padding: 12px; color: #a8c5eb; font-size: 11px; }
            QLabel#sidebarFooter { color: #8399ba; font-size: 10px; padding: 5px; }
            QFrame#workspaceHero { background: #202c3e; border: 1px solid #3d5374; }
            QScrollArea { border: none; }
            QTabWidget::pane { border: 1px solid #34363b; border-radius: 5px; }
            QTabBar::tab { background: #222325; color: #9fb6d6; padding: 9px 16px; border: none; }
            QTabBar::tab:selected { background: #2b3547; color: #edf5ff; }
            QTableWidget { selection-background-color: #28477b; }
            QHeaderView::section { border: 1px solid #34363b; color: #adc5e8; }
            QScrollBar:vertical { background: #18191b; width: 10px; margin: 2px; }
            QScrollBar::handle:vertical { background: #414550; border-radius: 4px; min-height: 28px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            QScrollBar:horizontal { background: #18191b; height: 10px; margin: 2px; }
            QScrollBar::handle:horizontal { background: #414550; border-radius: 4px; min-width: 28px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
            QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #56627a;
                border-radius: 3px; background: #202123; }
            QCheckBox::indicator:checked { background: #2563eb; border-color: #4384ff; }
            QToolTip { background: #27282e; color: #e6edf7; border: 1px solid #465b79; padding: 6px; }
        """)

    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer_layout = QHBoxLayout(central)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        self.sidebar = Sidebar()
        self.sidebar.navigate.connect(self._navigate)
        outer_layout.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        outer_layout.addWidget(self.pages, 1)
        self.dashboard = DashboardPage()
        self.dashboard.navigate.connect(self._navigate)
        self.dashboard.create_job.connect(self._new_job_page)
        self.pages.addWidget(self.dashboard)
        self.jobs_page = JobsPage(self.telegram_store)
        self.pages.addWidget(self.jobs_page)
        self.routes = {"dashboard": self.dashboard, "jobs": self.jobs_page}
        for name in ("edit_code", "edit_name", "edit_points", "edit_money", "btn_ui_run", "btn_api_run",
                     "btn_pause_queue", "lbl_queue", "lbl_status", "lbl_acc_count", "lbl_points", "lbl_pct", "prog_bar"):
            setattr(self, name, getattr(self.jobs_page, name))
        self.tbl_queue = self.jobs_page.table
        self.log_box = self.jobs_page.logs.box
        self.queue_tabs = self.jobs_page.tabs
        self.jobs_page.start_requested.connect(self._start_job)
        self.jobs_page.pause_requested.connect(self._toggle_queue_pause)
        self.jobs_page.remove_requested.connect(self._remove_job)
        self.jobs_page.settings_requested.connect(lambda: self._navigate("settings"))
        self.telegram_page = TelegramPage(self.telegram_store, auto_start=self._auto_start_telegram)
        self.telegram_page.job_requested.connect(self._enqueue_telegram_job)
        self.telegram_page.access_revoked.connect(self._revoke_queued_jobs)
        self.telegram_page.log_signal.connect(self._append_log)
        self.pages.addWidget(self.telegram_page)
        self.routes["telegram"] = self.telegram_page
        self.settings_page = SettingsPage(self.telegram_store)
        self.settings_page.navigate.connect(self._navigate)
        self.settings_page.defaults_saved.connect(self._apply_job_defaults)
        self.settings_page.refresh_requested.connect(self._check_connections)
        self.pages.addWidget(self.settings_page)
        self.routes["settings"] = self.settings_page
        self._apply_job_defaults(int(self.settings_page.points.text()), int(self.settings_page.money.text()), self.settings_page.name.text())
        self.jobs_page.restore_draft()
        self._navigate("dashboard")

    # ─── LOGIC & WORKERS ──────────────────────────────────────────────────────
    def _navigate(self, route):
        if route in self.routes:
            self.pages.setCurrentWidget(self.routes[route])
            self.sidebar.select(route)
            if route == "dashboard":
                self._refresh_dashboard()
            elif route == "telegram":
                self.telegram_page.refresh()
            elif route == "jobs":
                self.jobs_page.history.refresh()

    def _new_job_page(self):
        self._navigate("jobs")
        self.jobs_page.tabs.setCurrentIndex(0)
        self.edit_code.setFocus()

    def _apply_job_defaults(self, points, money, name):
        self.edit_points.setText(str(points))
        self.edit_money.setText(str(money))
        self.edit_name.setText(name)

    def _refresh_dashboard(self):
        if not self._closing:
            self.dashboard.refresh(self.telegram_store, self.active_item, self._active_progress, self.telegram_page.accepting)

    def _restore_queue(self):
        rows = self.telegram_store.recover_jobs()
        # Finish monitoring outstanding submissions before creating any new job.
        rows.sort(key=lambda row: not bool(row["backend_id"]))
        for row in rows:
            self.job_queue.restore(QueuedJob(
                number=0, invitation_code=row["code"], target_points=row["points"],
                job_name=row["name"], unit_price=row["money"], use_ui_automation=bool(row["use_ui"]),
                record_id=row["id"], chat_id=row["chat_id"], bot_id=row["bot_id"],
                backend_id=row["backend_id"], resume_job_id=row["backend_id"],
                scheduled_at=row["scheduled_at"],
            ))
        if rows:
            self.queue_paused = True
            self._append_log(f"[*] Đã khôi phục {len(rows)} mục. Bấm Tiếp tục để chạy mã chờ/theo dõi ID cũ; không tự gửi lại job đã có ID.")
        interrupted = sum(row["status"] == "interrupted" for row in self.telegram_store.jobs())
        if interrupted:
            self._append_log(f"[!] Có {interrupted} job gián đoạn chưa xác định ID. Xem Telegram → Giao dịch / đối soát và kiểm tra bản local trước khi tạo lại các mã đó.")
        self._render_queue()
        self.jobs_page.history.reload()

    def _enqueue_telegram_job(self, request):
        chat_id, bot_id = request["chat_id"], request["bot_id"]
        if self._closing:
            return
        user = self.telegram_store.user(chat_id)
        if not user or not user["active"]:
            self.telegram_store.notify(bot_id, chat_id, "Quyền truy cập đã bị thu hồi; chưa thêm job.")
            return
        if self.telegram_store.command_seen(request["external_key"]):
            return
        existing = self.telegram_store.find_external(request["external_key"])
        if existing:
            # Re-delivery of a Telegram update must never enqueue another job.
            return
        added, skipped = self.job_queue.add([request["code"]], request["points"], request["name"], request["money"], True)
        if skipped:
            self.telegram_store.remember_command(request["external_key"])
            self.telegram_store.notify(bot_id, chat_id, "Mã này đã có job đang chạy hoặc đang chờ. Chưa tạo thêm job trùng.")
            return
        item = added[0]
        item.chat_id, item.bot_id = chat_id, bot_id
        try:
            item.record_id = self.telegram_store.add_job(item, request["external_key"], request["rate"])
        except Exception:
            self.job_queue.remove_waiting({item.number})
            self.queue_paused = True
            self._append_log("[!] Không lưu được job Telegram xuống ổ đĩa; chưa gửi job này sang bản local. Đã tạm dừng hàng chờ.")
            self._render_queue()
            return
        if not item.record_id:
            self.job_queue.remove_waiting({item.number})
            return
        self.telegram_store.remember_command(request["external_key"])
        waiting = sum(entry.status == "queued" for entry in self.job_queue.items)
        self.telegram_store.notify(bot_id, chat_id,
            f"Đã nhận job #{item.number}: {item.invitation_code}\nMục tiêu: {item.target_points:,} konpeito\n"
            f"Giá chốt: {item.unit_price:,} VND\nVị trí trong hàng chờ: {waiting}."
            + (" Hàng chờ đang tạm dừng." if self.queue_paused else " Mã thêm sau chạy sau."))
        self._append_log(f"[Telegram] {user['name']} ({chat_id}) thêm mã {item.invitation_code} vào cuối hàng chờ.")
        self._render_queue()
        self._pump_queue()

    def _cancel_waiting_items(self, items, reason):
        for item in items:
            self.telegram_store.mark(item.record_id, "cancelled", error=reason)
            self.telegram_store.notify(item.bot_id, item.chat_id, f"Job {item.invitation_code} đã hủy trước khi chạy: {reason}")
        self.job_queue.remove_waiting({item.number for item in items})
        self.jobs_page.history.reload()

    def _revoke_queued_jobs(self, chat_id):
        items = [item for item in self.job_queue.items if item.chat_id == chat_id and item.status == "queued" and not item.resume_job_id]
        self._cancel_waiting_items(items, "Người quản lý thu hồi quyền truy cập.")
        self._render_queue()

    def _check_connections(self) -> None:
        self.settings_page.local_status.setText("Bản local: Đang kiểm tra...")
        self.settings_page.backend_status.setText("Dịch vụ theo dõi: Đang kiểm tra...")

    def _on_status_updated(
        self,
        app_found: bool,
        title: str,
        hwnd: int,
        backend_alive: bool,
        jobs: list,
    ) -> None:
        self.sidebar.connection.setText("●  Bản local đã kết nối" if app_found else "●  Chưa kết nối bản local")
        self.settings_page.local_status.setText(f"Bản local: {title}" if app_found else "Bản local: Chưa tìm thấy cửa sổ ứng dụng.")
        self.settings_page.backend_status.setText("Dịch vụ theo dõi: Đã kết nối (127.0.0.1:18888)." if backend_alive else "Dịch vụ theo dõi: Chưa kết nối.")


    def _on_error(self, err: str) -> None:
        self.queue_paused = True
        if self.active_item:
            self.active_item.status = "error"
            self.active_item.progress = err
            self.telegram_store.mark(self.active_item.record_id, "error", error=err)
            self.telegram_store.notify(self.active_item.bot_id, self.active_item.chat_id,
                f"Job {self.active_item.invitation_code} cần kiểm tra: {err}\nHàng chờ đã tạm dừng; chưa tự gửi lại job.")
        self.lbl_status.setText("LỖI")
        self.lbl_status.setStyleSheet("color: #f85149; font-weight: bold;")
        self._append_log(f"[!] {err}\n[!] Đã tạm dừng hàng chờ. Kiểm tra job trên bản local trước khi bấm Tiếp tục.", self.active_item.record_id if self.active_item else None)
        self._render_queue()
        self.jobs_page.history.reload()

    def _start_job(self, use_ui: bool) -> None:
        if self._closing:
            return
        try:
            codes = parse_codes(self.edit_code.toPlainText())
            pts = int(self.edit_points.text().strip())
            money = int(self.edit_money.text().strip() or "0")
            scheduled_at = self.jobs_page.schedule_value()
            added, skipped = self.job_queue.add(codes, pts, self.edit_name.text(), money, use_ui, scheduled_at=scheduled_at)
        except ValueError as error:
            QMessageBox.warning(self, "Dữ liệu chưa hợp lệ", str(error))
            return
        try:
            record_ids = self.telegram_store.add_jobs([(item, None, None) for item in added])
            for item, record_id in zip(added, record_ids):
                item.record_id = record_id
        except Exception:
            self.job_queue.remove_waiting({item.number for item in added})
            self.queue_paused = True
            self._append_log("[!] Không lưu được hàng chờ xuống ổ đĩa. Chưa thêm đợt mã này; kiểm tra ổ đĩa rồi thử lại.")
            self._render_queue()
            return
        self.edit_code.clear()
        self.queue_tabs.setCurrentIndex(1)
        if added:
            self.telegram_store.set_config(job_draft=None)
            self.jobs_page.form_message.setText(f"Đã thêm {len(added)} mã vào hàng chờ.")
            self._append_log(f"[+] Đã thêm {len(added)} mã vào cuối hàng chờ ({pts} konpeito/mã): "
                             + ", ".join(item.invitation_code for item in added))
        if skipped:
            self._append_log("[*] Bỏ qua mã đang chạy/đang chờ: " + ", ".join(skipped))
        self._render_queue()
        self._pump_queue()

    def _pump_queue(self) -> None:
        if self._closing or self.queue_paused or self.worker is not None:
            return
        item = self.job_queue.take_next()
        while item and item.chat_id != "LOCAL" and not item.resume_job_id:
            user = self.telegram_store.user(item.chat_id)
            if user and user["active"]:
                break
            item.status = "cancelled"
            self.telegram_store.mark(item.record_id, "cancelled", error="Người dùng không còn quyền truy cập.")
            item = self.job_queue.take_next()
        if item is None:
            return
        self.active_item = item
        self.telegram_store.mark(item.record_id, "running" if item.resume_job_id else "submitting")
        self._append_log(f"[▶] Bắt đầu #{item.number}: {item.invitation_code} — {item.job_name}", item.record_id)
        self._on_progress_update(0, max(1, item.target_points // 200), 0, "running")
        settings = dict(
            invitation_code=item.invitation_code,
            target_points=item.target_points,
            job_name=item.job_name,
            unit_price=item.unit_price,
            use_ui_automation=item.use_ui_automation,
        )
        if item.resume_job_id:
            settings["existing_job_id"] = item.resume_job_id
        self.worker = AutomationWorkerThread(**settings)
        self.worker.log_signal.connect(lambda text, rid=item.record_id: self._append_log(text, rid))
        if hasattr(self.worker, "backend_log_signal"):
            self.worker.backend_log_signal.connect(lambda event, rid=item.record_id: self._append_backend_log(event, rid))
        self.worker.progress_signal.connect(self._on_progress_update)
        self.worker.finished_signal.connect(self._on_job_finished)
        self.worker.identified_signal.connect(self._on_job_identified)
        self.worker.error_signal.connect(self._on_error)
        # Advance only after the old QThread actually exits, not on its result signal.
        self.worker.finished.connect(self._on_worker_stopped)
        self._render_queue()
        self.telegram_store.notify(item.bot_id, item.chat_id, f"Job {item.invitation_code} đang được xử lý ({item.target_points:,} konpeito).")
        self.worker.start()

    def _on_job_identified(self, job_id: str) -> None:
        if self.active_item:
            self.active_item.backend_id = job_id
            self.telegram_store.mark(self.active_item.record_id, "running", backend_id=job_id)

    def _on_worker_stopped(self) -> None:
        old_worker = self.worker
        if self.active_item and self.active_item.status == "running" and not self._closing:
            self._on_error("Luồng theo dõi đã dừng trước khi job kết thúc.")
        self.worker = None
        self.active_item = None
        if old_worker:
            old_worker.deleteLater()
        self._render_queue()
        self._pump_queue()

    def _toggle_queue_pause(self) -> None:
        if self._closing:
            return
        self.queue_paused = not self.queue_paused
        self._append_log("[⏸] Tạm dừng hàng chờ sau job hiện tại." if self.queue_paused
                         else "[▶] Tiếp tục hàng chờ theo thứ tự đã nhập.")
        self._render_queue()
        self._pump_queue()

    def _remove_queued_rows(self) -> None:
        numbers = {self.tbl_queue.item(index.row(), 0).data(Qt.ItemDataRole.UserRole)
                   for index in self.tbl_queue.selectionModel().selectedRows()}
        items = [item for item in self.job_queue.items if item.number in numbers and item.status == "queued" and not item.resume_job_id]
        self._cancel_waiting_items(items, "Người quản lý xóa khỏi hàng chờ.")
        removed = len(items)
        if removed:
            self._append_log(f"[*] Đã xóa {removed} mã đang chờ; job đang chạy tiếp tục bình thường.")
        if any(item.number in numbers and item.resume_job_id for item in self.job_queue.items):
            self._append_log("[*] Job đã có ID backend được giữ lại để theo dõi; muốn hủy job đó, thao tác trên bản local.")
        self._render_queue()

    def _remove_job(self, number):
        item = next((item for item in self.job_queue.items if item.number == number), None)
        if not item or item.status == "running" or item.resume_job_id or (self.worker and self.active_item is item):
            return
        if item.status == "queued":
            self._cancel_waiting_items([item], "Người quản lý xóa khỏi hàng chờ.")
        else:
            self.job_queue.items.remove(item)
        self._append_log(f"[*] Đã xóa {item.invitation_code} khỏi danh sách phiên; lịch sử và log được giữ lại.")
        self._render_queue()

    @staticmethod
    def _queue_status_text(item) -> str:
        if item.status == "queued" and item.resume_job_id:
            return "Chờ theo dõi job cũ (không gửi lại)"
        status = {"queued": "Đang chờ", "running": "Đang chạy", "completed": "Hoàn thành",
                  "failed": "Thất bại", "cancelled": "Đã hủy", "invalid_code": "Mã không hợp lệ",
                  "error": "Lỗi — kiểm tra bản local"}.get(item.status, item.status)
        return f"{status} | {item.progress}" if item.progress else status

    def _render_queue(self) -> None:
        self.jobs_page.render_queue(self.job_queue.items, self.active_item, self.queue_paused, self._active_progress)
        self.jobs_page.history.refresh()
        self._refresh_dashboard()

    def _on_progress_update(self, cur: int, total: int, points: int, status: str) -> None:
        self._active_progress = (cur, total, points, status)
        pct = int((cur / max(1, total)) * 100)
        self.prog_bar.setValue(pct)
        self.lbl_acc_count.setText(f"{cur} / {total} account")
        self.lbl_points.setText(f"+{points} konpeito")
        self.lbl_pct.setText(f"{pct}%")
        self.lbl_status.setText(status.upper())
        if status == "running":
            self.lbl_status.setStyleSheet("color: #d29922; font-weight: bold;")
        elif status == "completed":
            self.lbl_status.setStyleSheet("color: #3fb950; font-weight: bold;")
        if self.active_item:
            self.active_item.progress = f"{cur}/{total} account (+{points} konpeito)"
            self.telegram_store.update_progress(self.active_item.record_id, cur, total, points)
            self.jobs_page.update_progress(self.active_item.number, cur, total, points, self.active_item.target_points, status)
        self._refresh_dashboard()

    def _on_job_finished(self, job_data: dict) -> None:
        if self.active_item:
            self.active_item.status = job_data.get("status", "completed")
            self.active_item.resume_job_id = None
            self.active_item.backend_id = job_data.get("id")
            self.telegram_store.mark(self.active_item.record_id, self.active_item.status,
                                     backend_id=self.active_item.backend_id, result=job_data)
            self.telegram_store.notify(self.active_item.bot_id, self.active_item.chat_id,
                f"Job {self.active_item.invitation_code} kết thúc: {self.active_item.status}\n"
                f"Thành công: {job_data.get('success_count', 0)}/{job_data.get('target_count', 0)} account\n"
                f"Konpeito đạt: {job_data.get('achieved_points') or int(job_data.get('success_count') or 0) * 200:,} konpeito")
        self._render_queue()
        self.jobs_page.history.reload()
        self.telegram_page.refresh()
        self._append_log(
            f"[★] Job đã kết thúc. Trạng thái: {job_data.get('status', 'completed')} | "
            f"Thành công: {job_data.get('success_count', 0)}/{job_data.get('target_count', 0)} account.",
            self.active_item.record_id if self.active_item else None,
        )

    def _append_log(self, text: str, record_id=None) -> None:
        level = "error" if text.startswith("[!]") else "success" if text.startswith(("[✔]", "[★]")) else "info"
        row = self.telegram_store.append_log(record_id, text, level)
        if row:
            self.jobs_page.logs.ensure_job(record_id)
            self.jobs_page.logs.append(row)

    def _append_backend_log(self, event, record_id):
        key = str(event.get("id") or (str(event.get("created_at")) + str(event.get("message"))))
        row = self.telegram_store.append_log(record_id, str(event.get("message", "")), event.get("level", "info"),
            source_key=f"backend:{event.get('job_id')}:{key}", created_at=event.get("created_at"))
        if row:
            self.jobs_page.logs.append(row)


def launch_gui() -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    win = ControlMainWindow()
    win.show()
    app.exec()


if __name__ == "__main__":
    launch_gui()
