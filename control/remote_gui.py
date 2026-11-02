"""Desktop administration for the Ubuntu service. Closing this window stops no jobs."""
from pathlib import Path
import sys

THIS_DIR = Path(__file__).resolve().parent
sys.path[:] = [str(THIS_DIR)] + [p for p in sys.path if p and Path(p).resolve() not in (THIS_DIR.parent, THIS_DIR)]

import json
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QStackedWidget, QVBoxLayout, QWidget
from desktop_pages import DashboardPage, SettingsPage, Sidebar, action, card, muted
from jobs_page import JobsPage
from job_queue import QueuedJob
from remote_client import RemoteConnection, RemoteStore
from telegram_page import TelegramPage
from telegram_protocol import TelegramClient


class RemoteTelegramPage(TelegramPage):
    def __init__(self, store):
        super().__init__(store, auto_start=False)
        self.auto_start.setText("Tự bật bot khi VPS khởi động")
        self.edit_token.setPlaceholderText("Token lưu trên VPS; để trống để giữ token hiện tại")

    def save_config(self):
        try:
            token = self.edit_token.text().strip()
            if token:
                TelegramClient(token)
            self.store.save_bot(token, self.auto_start.isChecked())
            self.status.setText("Đã gửi yêu cầu lưu cấu hình lên VPS...")
            self.edit_token.clear()
            return True
        except ValueError as exc:
            self.status.setText(str(exc))
            return False

    def start_bot(self):
        try:
            self.store.connection.submit("start_bot")
        except ValueError as exc:
            self.status.setText(str(exc))

    def stop_bot(self):
        try:
            self.store.connection.submit("stop_bot")
        except ValueError as exc:
            self.status.setText(str(exc))

    def change_access(self, active):
        try:
            super().change_access(active)
            self.user_message.setText("Đã gửi yêu cầu đổi quyền lên VPS...")
        except ValueError as exc:
            self.user_message.setText(str(exc))

    def save_user(self, create=False):
        super().save_user(create)
        if self.user_message.text().startswith("Đã "):
            self.user_message.setText("Đã gửi yêu cầu lưu người dùng lên VPS...")

    def save_rate(self):
        super().save_rate()
        if self.status.text().startswith("Đã "):
            self.status.setText("Đã gửi yêu cầu lưu đơn giá lên VPS...")


class RemoteWindow(QMainWindow):
    def __init__(self, connection):
        super().__init__()
        self.connection = connection
        self.store = RemoteStore(connection)
        self.rows, self.hidden_ids = [], set()
        self._initialized = False
        self._closing = False
        self.setWindowTitle("hoshino-tool · Quản lý VPS")
        self.resize(1280, 930)
        self.setMinimumSize(1120, 800)
        self.setStyleSheet("""
            QWidget {background:#18191b;color:#e6edf7;font-family:'Segoe UI';font-size:13px;}
            QFrame[class=cardFrame], QFrame#telegramCard {background:#222325;border:1px solid #34363b;border-radius:9px;}
            QLabel {background:transparent;} QLabel[class=pageTitle] {font-size:26px;font-weight:700;}
            QLabel[class=sectionTitle] {font-size:15px;font-weight:700;}
            QLabel[class=muted] {color:#adc5e8;} QLabel#brandName {font-size:25px;font-weight:700;}
            QLabel#brandSubtitle,QLabel#navCaption,QLabel#sidebarFooter {color:#8ca5c6;font-size:10px;}
            QFrame#sidebar {background:#202123;border-right:1px solid #34363b;}
            QPushButton {padding:9px 14px;background:#282c33;border:1px solid #405375;border-radius:6px;}
            QPushButton:disabled {color:#667087;border-color:#34363b;}
            QPushButton[class=actionBlueBtn],QPushButton#navButton:checked {background:#2563eb;border-color:#2563eb;color:white;}
            QPushButton#navButton {text-align:left;padding:12px;border:0;background:transparent;}
            QLineEdit,QTextEdit,QComboBox,QDateTimeEdit {background:#202123;border:1px solid #3a3a42;border-radius:5px;padding:8px;}
            QTableWidget {background:#202023;gridline-color:#34343a;border:1px solid #38383e;}
            QHeaderView::section {background:#27272d;color:#b5c9e8;border:1px solid #3b3b42;padding:9px;}
            QProgressBar {border:1px solid #384454;border-radius:5px;background:#202023;}
            QProgressBar::chunk {background:#2563eb;}
        """)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.sidebar = Sidebar()
        self.sidebar.navigate.connect(self.navigate)
        layout.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.dashboard = DashboardPage()
        self.dashboard.navigate.connect(self.navigate)
        self.dashboard.create_job.connect(lambda: self.navigate("jobs"))
        self.jobs = JobsPage(self.store)
        self.jobs.btn_ui_run.setText("Tạo, chạy trên VPS")
        self.jobs.btn_api_run.hide()
        self.jobs.start_requested.connect(self.enqueue)
        self.jobs.pause_requested.connect(self.pause)
        self.jobs.remove_requested.connect(self.remove)
        self.jobs.settings_requested.connect(lambda: self.navigate("settings"))
        self.telegram = RemoteTelegramPage(self.store)
        self.executor_note = QLabel("Đang kiểm tra bộ thực thi trên VPS...")
        self.executor_note.setWordWrap(True)
        self.jobs.layout().insertWidget(2, self.executor_note)
        self.settings = SettingsPage(self.store)
        # Replace local-only operational help with the actual VPS workflow.
        for label in self.settings.findChildren(QLabel):
            if "Giữ app local" in label.text():
                label.setText("VPS xử lý bot và job liên tục. Đóng giao diện hoặc mất kết nối máy tính không dừng VPS.")
            elif "bấm Tiếp tục để khôi phục" in label.text():
                label.setText("Hàng chờ và lịch sử nằm trên VPS. Khởi động lại giao diện chỉ tải lại dữ liệu.")
        self.settings.defaults_saved.connect(self.apply_defaults)
        self.settings.navigate.connect(self.navigate)
        self.settings.refresh_requested.connect(self.connection.wakeup.set)
        provider_card, provider_body = card("Bộ thực thi Ubuntu")
        self.provider = QComboBox()
        self.provider.addItems(["mail_tm", "tempmail_lol"])
        provider_body.addWidget(muted("Nguồn mail cho bộ thực thi Linux. Đây là bộ xử lý từ mã clone, cần kiểm tra job thật trước khi vận hành."))
        provider_body.addWidget(self.provider)
        provider_body.addWidget(action("Lưu nguồn mail", self.save_provider))
        self.settings.layout().insertWidget(2, provider_card)
        self.routes = {"dashboard": self.dashboard, "jobs": self.jobs, "telegram": self.telegram, "settings": self.settings}
        for page in self.routes.values():
            self.pages.addWidget(page)
        self.connection.snapshot_received.connect(self.apply_snapshot)
        self.connection.completed.connect(self.completed)
        self.connection.failed.connect(self.failed)
        self.navigate("dashboard")
        self.statusBar().showMessage("Đang kết nối VPS qua SSH tunnel...")
        self.connection.start()

    def navigate(self, route):
        if route in self.routes:
            self.pages.setCurrentWidget(self.routes[route])
            self.sidebar.select(route)

    def apply_defaults(self, points, money, name):
        self.jobs.edit_points.setText(str(points))
        self.jobs.edit_money.setText(str(money))
        self.jobs.edit_name.setText(name)

    def apply_snapshot(self, state):
        if self._closing:
            return
        self.store.apply_snapshot(state)
        self.rows = list(reversed(state["jobs"]))
        if not self._initialized:
            self._initialized = True
            self.apply_defaults(self.store.config("default_points", 2000), self.store.config("default_money", 0), self.store.config("default_name", ""))
            self.jobs.restore_draft()
            self.telegram.auto_start.setChecked(self.store.config("auto_start", False))
            self.telegram.edit_rate.setText(str(self.store.config("rate", 1000)))
            self.settings.points.setText(str(self.store.config("default_points", 2000)))
            self.settings.money.setText(str(self.store.config("default_money", 0)))
            self.settings.name.setText(self.store.config("default_name", ""))
        self.provider.setCurrentText(self.store.config("mail_provider", "mail_tm"))
        items = [QueuedJob(i, row["code"], row["points"], row["name"], row["money"], False,
                           status=row["status"], record_id=row["id"], chat_id=row["chat_id"],
                           backend_id=row["backend_id"], scheduled_at=row["scheduled_at"])
                 for i, row in enumerate(self.rows) if row["id"] not in self.hidden_ids]
        active = next((item for item in items if item.status == "running"), None)
        self.jobs.render_queue(items, active, self.store.config("queue_paused", True))
        self.jobs.history.refresh()
        self.jobs.logs.load()
        self.executor_note.setText("Bộ thực thi VPS: " + state["executor"]["status"])
        self.executor_note.setStyleSheet("color:#25dc96;" if state["executor"]["ready"] else "color:#ffc54d;")
        bot = state["bot"]
        self.telegram.accepting = bot["accepting"]
        self.telegram.status.setText(bot["status"])
        self.telegram.btn_start.setEnabled(not bot["running"])
        self.telegram.btn_stop.setEnabled(bot["running"])
        self.telegram.refresh()
        self.dashboard.refresh(self.store, None, None, bot["accepting"])
        self.sidebar.connection.setText("●  Đã kết nối VPS")
        self.settings.local_status.setText("Bộ thực thi Ubuntu: " + state["executor"]["status"])
        self.settings.backend_status.setText("Dịch vụ VPS: Đã kết nối qua SSH tunnel.")
        self.statusBar().showMessage("Đã đồng bộ VPS. Đóng giao diện không dừng bot hoặc job.")

    def enqueue(self, _use_ui):
        try:
            values = {"codes": self.jobs.edit_code.toPlainText(), "points": int(self.jobs.edit_points.text()),
                      "money": int(self.jobs.edit_money.text()), "name": self.jobs.edit_name.text(),
                      "scheduled_at": self.jobs.schedule_value()}
            self.connection.submit("enqueue", values)
            self.jobs.form_message.setText("Đang gửi mã lên VPS...")
        except ValueError as exc:
            self.failed(str(exc))

    def pause(self):
        try:
            self.store.set_config(queue_paused=not self.store.config("queue_paused", True))
        except ValueError as exc:
            self.failed(str(exc))

    def remove(self, number):
        row = self.rows[number]
        try:
            self.connection.submit("remove", {"id": row["id"]})
            if row["status"] not in ("queued", "running", "submitting"):
                self.hidden_ids.add(row["id"])
        except ValueError as exc:
            self.failed(str(exc))

    def save_provider(self):
        try:
            self.store.set_config(mail_provider=self.provider.currentText())
        except ValueError as exc:
            self.failed(str(exc))

    def completed(self, operation, result):
        if self._closing:
            return
        if operation == "logs":
            self.store.apply_logs(result)
            self.jobs.logs.load()
        elif operation == "enqueue":
            self.jobs.form_message.setText(f"VPS đã nhận {len(result['added'])} mã; bỏ qua {len(result['skipped'])} mã trùng.")
            self.jobs.tabs.setCurrentIndex(1)
        self.statusBar().showMessage("VPS đã xác nhận: " + operation)

    def failed(self, message):
        if self._closing:
            return
        self.statusBar().showMessage(message)
        self.jobs.form_message.setText(message)
        if not self.connection.online:
            self.sidebar.connection.setText("●  Mất kết nối VPS · dữ liệu đang xem có thể cũ")

    def closeEvent(self, event):
        self._closing = True
        self.connection.stop()
        if self.connection.isRunning():
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        self.jobs.shutdown()
        self.telegram.shutdown()  # No local poller exists in this page.
        self.store.close()
        self.store.folder.cleanup()
        event.accept()


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    from telegram_store import TelegramStore
    preferences = TelegramStore(THIS_DIR / "data" / "remote.sqlite3")
    dialog = QDialog()
    dialog.setWindowTitle("Kết nối VPS hoshino-tool")
    form = QFormLayout(dialog)
    url = QLineEdit(preferences.config("url", "http://127.0.0.1:18900"))
    token = QLineEdit()
    token.setEchoMode(QLineEdit.EchoMode.Password)
    try:
        token.setText(preferences.token())
    except Exception:
        pass
    form.addRow(QLabel("Mở SSH tunnel trước. Giao diện này quản lý bot và job trên VPS."))
    form.addRow("URL tunnel", url)
    form.addRow("Khóa quản lý VPS", token)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    form.addRow(buttons)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        preferences.close()
        return
    try:
        connection = RemoteConnection(url.text().strip(), token.text().strip())
        preferences.set_config(url=url.text().strip())
        preferences.save_bot(token.text().strip(), False)  # DPAPI on Windows; never sent to Telegram.
    except Exception as exc:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(None, "Kết nối VPS", str(exc))
        preferences.close()
        return
    preferences.close()
    window = RemoteWindow(connection)
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
