"""Sidebar, dashboard and general settings for the hoshino desktop shell."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from job_history import status_text

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

ASSETS = Path(__file__).resolve().parent / "assets"


def card(title=None):
    frame = QFrame()
    frame.setProperty("class", "cardFrame")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    if title:
        heading = QLabel(title)
        heading.setProperty("class", "sectionTitle")
        layout.addWidget(heading)
    return frame, layout


def muted(text):
    label = QLabel(text)
    label.setProperty("class", "muted")
    label.setWordWrap(True)
    return label


def action(text, callback, primary=False):
    button = QPushButton(text.replace("&", "&&"))
    button.setProperty("class", "actionBlueBtn" if primary else "secondaryBtn")
    button.clicked.connect(callback)
    return button


class Sidebar(QFrame):
    navigate = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 25, 16, 18)
        layout.setSpacing(7)
        avatar = QLabel()
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setPixmap(QPixmap(str(ASSETS / "avatar.png")).scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(avatar)
        brand = QLabel("hoshino-tool")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setObjectName("brandName")
        layout.addWidget(brand)
        subtitle = QLabel("MIRAIMIND · TELEGRAM")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setObjectName("brandSubtitle")
        layout.addWidget(subtitle)
        layout.addSpacing(28)
        self.buttons = {}
        for section, entries in (
            ("TỔNG QUAN", (("dashboard", "Dashboard", "dashboard.svg"),)),
            ("NỀN TẢNG", (("jobs", "Job & hàng chờ", "jobs.svg"),)),
            ("HỆ THỐNG", (("telegram", "Telegram", "telegram.svg"), ("settings", "Cài đặt chung", "settings.svg"))),
        ):
            caption = QLabel(section)
            caption.setObjectName("navCaption")
            layout.addSpacing(12)
            layout.addWidget(caption)
            for key, text, icon in entries:
                button = QPushButton(text.replace("&", "&&"))
                button.setObjectName("navButton")
                button.setCheckable(True)
                button.setIcon(QIcon(str(ASSETS / icon)))
                button.setIconSize(QSize(21, 21))
                button.setMinimumHeight(44)
                button.clicked.connect(lambda checked=False, route=key: self.navigate.emit(route))
                self.buttons[key] = button
                layout.addWidget(button)
        layout.addStretch()
        self.connection = QLabel("●  Đang kiểm tra bản local")
        self.connection.setObjectName("sidebarConnection")
        self.connection.setWordWrap(True)
        layout.addWidget(self.connection)
        footer = QLabel("Thao tác nền · Không chiếm chuột")
        footer.setObjectName("sidebarFooter")
        footer.setWordWrap(True)
        layout.addWidget(footer)
        version = QLabel("Local edition · Konpeito")
        version.setObjectName("sidebarFooter")
        layout.addWidget(version)

    def select(self, route):
        for key, button in self.buttons.items():
            button.setChecked(key == route)


class DashboardPage(QWidget):
    navigate = pyqtSignal(str)
    create_job = pyqtSignal()

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 26, 24, 24)
        layout.setSpacing(16)
        header = QHBoxLayout()
        heading = QVBoxLayout()
        title = QLabel("Dashboard")
        title.setProperty("class", "pageTitle")
        heading.addWidget(title)
        heading.addWidget(muted("Tổng quan hoạt động của hoshino-tool."))
        header.addLayout(heading, 1)
        self.btn_create = action("Tạo job mới", self.create_job.emit, True)
        header.addWidget(self.btn_create, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        hero, hero_layout = card("Không gian làm việc của bạn")
        hero.setObjectName("workspaceHero")
        hero.setMinimumHeight(112)
        hero_layout.addStretch()
        hero_layout.addWidget(muted("Theo dõi job, quản lý hàng chờ và tiếp nhận yêu cầu từ Telegram."))
        layout.addWidget(hero)
        grid = QGridLayout()
        grid.setSpacing(14)
        self.metrics = {}
        for index, (key, title, hint, color) in enumerate((
            ("total", "Tổng job", "Các job được Controller ghi nhận", "#60a5fa"),
            ("running", "Đang chạy", "Job đang được xử lý trên bản local", "#60a5fa"),
            ("completed", "Hoàn thành", "Job đã kết thúc thành công", "#25dc96"),
            ("failed", "Thất bại", "Không tính job đã hủy", "#ff6481"),
            ("konpeito", "Konpeito thành công", "Konpeito thực tế từ các job đã ghi nhận", "#c390ff"),
            ("queued", "Đang chờ", "Mã chờ đến lượt trong hàng chờ", "#25dc96"),
        )):
            metric, metric_layout = card()
            metric.setObjectName("metricCard")
            metric.setStyleSheet(f"QFrame#metricCard {{border-top:2px solid {color};}}")
            metric.setMinimumHeight(102)
            metric_layout.setContentsMargins(16, 12, 16, 12)
            metric_layout.setSpacing(5)
            metric_layout.addWidget(muted(title))
            value = QLabel("0")
            value.setStyleSheet(f"font-size:25px;font-weight:700;color:{color};background:transparent;")
            metric_layout.addWidget(value)
            note = muted(hint)
            note.setProperty("class", "metricHint")
            metric_layout.addWidget(note)
            self.metrics[key] = value
            grid.addWidget(metric, index // 3, index % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        layout.addLayout(grid)

        recent, recent_layout = card()
        recent_header = QHBoxLayout()
        title = QLabel("Job gần đây")
        title.setProperty("class", "sectionTitle")
        recent_header.addWidget(title)
        recent_header.addStretch()
        recent_note = muted("10 job mới nhất được ghi nhận")
        recent_note.setWordWrap(False)
        recent_header.addWidget(recent_note)
        recent_layout.addLayout(recent_header)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Khách hàng / Job", "Mã mời", "Tiến độ", "Konpeito", "Trạng thái", "Tạo lúc"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(245)
        recent_layout.addWidget(self.table)
        self.empty = muted("Chưa có job. Bấm Tạo job mới để thêm mã vào hàng chờ.")
        recent_layout.addWidget(self.empty)
        layout.addWidget(recent, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        telegram, telegram_layout = card()
        telegram_row = QHBoxLayout()
        telegram_info = QVBoxLayout()
        label = QLabel("Telegram")
        label.setProperty("class", "sectionTitle")
        telegram_info.addWidget(label)
        self.telegram_users = muted("0 người dùng được cấp quyền")
        telegram_info.addWidget(self.telegram_users)
        self.telegram_status = QLabel("Bot chưa bật")
        self.telegram_status.setObjectName("telegramSummaryStatus")
        telegram_info.addWidget(self.telegram_status)
        telegram_row.addLayout(telegram_info, 1)
        telegram_row.addWidget(action("Quản lý", lambda: self.navigate.emit("telegram")))
        telegram_layout.addLayout(telegram_row)
        bottom.addWidget(telegram, 1)
        quick, quick_layout = card("Truy cập nhanh")
        buttons = QHBoxLayout()
        buttons.addWidget(action("Job & hàng chờ", lambda: self.navigate.emit("jobs")))
        buttons.addWidget(action("Cài đặt chung", lambda: self.navigate.emit("settings")))
        quick_layout.addLayout(buttons)
        bottom.addWidget(quick, 1)
        layout.addLayout(bottom)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def refresh(self, store, active=None, progress=None, bot_active=False):
        totals = store.dashboard_stats()
        rows = store.recent_jobs(10)
        if active and progress:
            achieved = progress[2]
            stored = (store.job(active.record_id) or {}).get("achieved", 0)
            # Only a running record needs an overlay; final results are already persisted.
            if active.status == "running":
                totals["konpeito"] += max(0, achieved - stored)
        for key, label in self.metrics.items():
            label.setText(f"{totals[key]:,}")
        self.telegram_users.setText(f"{totals['users']} người dùng được cấp quyền")
        self.telegram_status.setText("Bot đang nhận yêu cầu" if bot_active else "Bot chưa bật")
        self.telegram_status.setStyleSheet(f"color:{'#25dc96' if bot_active else '#9aa8c4'};font-size:11px;background:transparent;")
        self.table.setRowCount(len(rows))
        statuses = {"queued": "Đang chờ", "submitting": "Đang tạo job", "running": "Đang chạy",
                    "completed": "Hoàn thành", "failed": "Thất bại", "invalid_code": "Mã không hợp lệ",
                    "cancelled": "Đã hủy", "error": "Cần kiểm tra", "interrupted": "Bị gián đoạn"}
        for row_index, row in enumerate(rows):
            current, total, achieved = row["achieved"] // 200, max(1, row["points"] // 200), row["achieved"]
            if active and row["id"] == active.record_id and progress and active.status == "running":
                current, total, achieved = progress[:3]
            try:
                started = datetime.fromisoformat(row["created_at"]).astimezone(timezone(timedelta(hours=7))).strftime("%H:%M:%S")
            except ValueError:
                started = "—"
            values = (row["name"], row["code"], f"{current} / {total}", f"{achieved:,}", status_text(row["status"], row["error"]), started)
            for col, value in enumerate(values):
                entry = QTableWidgetItem(value)
                if col == 4:
                    color = "#25dc96" if row["status"] == "completed" else ("#ff6481" if row["status"] in ("failed", "invalid_code", "error", "interrupted") else "#a9bedb")
                    entry.setForeground(QColor(color))
                entry.setToolTip(row["error"] or row["name"])
                self.table.setItem(row_index, col, entry)
        self.empty.setVisible(not rows)


class SettingsPage(QWidget):
    defaults_saved = pyqtSignal(int, int, str)
    navigate = pyqtSignal(str)
    refresh_requested = pyqtSignal()

    def __init__(self, store):
        super().__init__()
        self.store = store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 24, 24)
        layout.setSpacing(16)
        title = QLabel("Cài đặt chung")
        title.setProperty("class", "pageTitle")
        layout.addWidget(title)
        layout.addWidget(muted("Thiết lập mặc định cho job mới và kiểm tra kết nối bản local."))
        defaults, defaults_layout = card("Thiết lập job mặc định")
        form = QGridLayout()
        self.points = QLineEdit(str(store.config("default_points", 2000)))
        self.money = QLineEdit(str(store.config("default_money", 0)))
        self.name = QLineEdit(store.config("default_name", ""))
        self.name.setPlaceholderText("Để trống: tự đặt tên theo từng mã")
        for row, (text, edit) in enumerate((("Konpeito / mã", self.points), ("Tổng tiền / mã (VND)", self.money), ("Tên job / khách hàng", self.name))):
            form.addWidget(QLabel(text), row, 0)
            form.addWidget(edit, row, 1)
        form.setColumnStretch(1, 1)
        defaults_layout.addLayout(form)
        defaults_layout.addWidget(muted("Thay đổi chỉ áp dụng cho các mã thêm sau. Job đã vào hàng chờ giữ nguyên thiết lập."))
        save_row = QHBoxLayout()
        self.message = muted("")
        save_row.addWidget(self.message, 1)
        save_row.addWidget(action("Lưu cài đặt", self.save, True))
        defaults_layout.addLayout(save_row)
        layout.addWidget(defaults)
        connections, connection_layout = card("Kết nối & thao tác nền")
        self.local_status = muted("Bản local: Đang kiểm tra...")
        self.backend_status = muted("Dịch vụ theo dõi: Đang kiểm tra...")
        connection_layout.addWidget(self.local_status)
        connection_layout.addWidget(self.backend_status)
        connection_layout.addWidget(muted("Giữ app local mở, không thu nhỏ; có thể để sau cửa sổ khác. Controller điền form và chạy job ở nền, không chiếm chuột/bàn phím."))
        connection_layout.addWidget(action("Kiểm tra lại kết nối", self.refresh_requested.emit), alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(connections)
        telegram, telegram_layout = card("Telegram & dữ liệu")
        telegram_layout.addWidget(muted("Quản lý bot, đơn giá và quyền người dùng tại Telegram. Hàng chờ và lịch sử được lưu tự động; khi mở lại tool, bấm Tiếp tục để khôi phục."))
        telegram_layout.addWidget(action("Mở Telegram", lambda: self.navigate.emit("telegram")), alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(telegram)
        layout.addStretch()

    def save(self):
        try:
            points, money = int(self.points.text().strip()), int(self.money.text().strip())
            if points <= 0 or money < 0:
                raise ValueError()
            name = self.name.text().strip()
            self.store.set_config(default_points=points, default_money=money, default_name=name)
            self.defaults_saved.emit(points, money, name)
            self.message.setText("Đã lưu cài đặt cho job mới.")
        except ValueError:
            self.message.setText("Konpeito phải lớn hơn 0; tổng tiền không được âm.")
