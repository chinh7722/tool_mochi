"""Local overview of the current Mochi session and connected tool pages."""

from pathlib import Path

try:
    from PySide6.QtCore import QRectF, Qt, QTimer
    from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
    from PySide6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout,
        QHBoxLayout, QHeaderView, QLabel, QPushButton, QScrollArea, QTableWidget,
        QTableWidgetItem, QVBoxLayout, QWidget)
except ImportError:
    from PyQt6.QtCore import QRectF, Qt, QTimer
    from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPixmap
    from PyQt6.QtWidgets import (QAbstractItemView, QFrame, QGridLayout,
        QHBoxLayout, QHeaderView, QLabel, QPushButton, QScrollArea, QTableWidget,
        QTableWidgetItem, QVBoxLayout, QWidget)

from core.config.constants import MOCHI_INVITATION_REWARD_POINTS


class _DashboardBanner(QFrame):
    """Cover the banner with optional artwork while keeping its text readable."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('dashboardCard')
        self.setMinimumHeight(120)
        self._artwork = QPixmap()

    def set_artwork(self, pixmap):
        self._artwork = pixmap
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._artwork.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 10, 10)
        painter.setClipPath(clip)
        scaled = self._artwork.scaled(self.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((self.width() - scaled.width()) // 2,
            (self.height() - scaled.height()) // 2, scaled)
        painter.fillRect(self.rect(), QColor(10, 17, 32, 185))


class DashboardPage(QWidget):
    """Read the existing pages without starting workers or altering settings."""

    def __init__(self, mochi_page, proxy_page, telegram_page, parent=None):
        super().__init__(parent)
        self.mochi_page = mochi_page
        self.proxy_page = proxy_page
        self.telegram_page = telegram_page
        self._table_signature = None
        self.setObjectName('dashboardPage')
        self.setStyleSheet('''
            QWidget#dashboardPage { background: #18191b; color: #f4f6fa; }
            QWidget#dashboardPage QLabel { background: transparent; border: none; color: #a1afc5; }
            QWidget#dashboardPage QFrame#dashboardCard {
                background: #232426; border: 1px solid #303238; border-radius: 10px;
            }
            QWidget#dashboardPage QTableWidget {
                background: #1e1f21; color: #e9eef8; border: 1px solid #35373d;
                gridline-color: #35373d; border-radius: 6px; selection-background-color: #20375b;
            }
            QWidget#dashboardPage QHeaderView::section {
                background: #292a2e; color: #a9bbd5; border: none;
                border-bottom: 1px solid #35373d; padding: 10px 8px; font-weight: 600;
            }
            QWidget#dashboardPage QPushButton {
                background: #292c32; color: #a9ccff; border: 1px solid #385078;
                border-radius: 6px; padding: 9px 14px; font-weight: 600;
            }
            QWidget#dashboardPage QPushButton:hover { background: #303b4f; border-color: #4a8bff; }
            QWidget#dashboardPage QPushButton#primaryButton {
                background: #2563eb; color: white; border: 1px solid #2563eb;
            }
            QWidget#dashboardPage QPushButton#primaryButton:hover { background: #3475ff; }
        ''')
        self._build_ui()
        for name in ('job_progress', 'job_finished', 'job_failed'):
            signal = getattr(mochi_page, name, None)
            if signal is not None:
                signal.connect(self.refresh)
        signal = getattr(proxy_page, 'proxies_updated', None)
        if signal is not None:
            signal.connect(self.refresh)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1500)
        self._refresh_timer.timeout.connect(self._refresh_if_visible)
        self._refresh_timer.start()
        self.refresh()

    def _card(self):
        card = QFrame(self)
        card.setObjectName('dashboardCard')
        return card

    @staticmethod
    def _heading(text, parent, size=15):
        label = QLabel(text, parent)
        label.setStyleSheet(f'color: #f4f6fa; font-size: {size}px; font-weight: 700;')
        return label

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet('QScrollArea { background: transparent; border: none; }')
        content = QWidget(self.scroll_area)
        content.setObjectName('dashboardContent')
        content.setStyleSheet('QWidget#dashboardContent { background: #18191b; }')
        self.scroll_area.setWidget(content)
        outer.addWidget(self.scroll_area)
        root = QVBoxLayout(content)
        root.setContentsMargins(28, 26, 28, 24)
        root.setSpacing(18)
        title_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        title_box.addWidget(self._heading('Dashboard', self, 25))
        title_box.addWidget(QLabel('Tổng quan hoạt động của hoshino-tool.', self))
        title_row.addLayout(title_box)
        title_row.addStretch()
        self.btn_new_job = self._button('Tạo job mới', self._open_new_job, primary=True)
        title_row.addWidget(self.btn_new_job)
        root.addLayout(title_row)

        banner = self.banner_frame = _DashboardBanner(self)
        banner.setStyleSheet('QFrame#dashboardCard { background: #202a3c; border: 1px solid #344d75; border-radius: 10px; }')
        banner_row = QHBoxLayout(banner)
        banner_row.setContentsMargins(18, 16, 18, 16)
        banner_text = QVBoxLayout()
        banner_text.setSpacing(6)
        banner_text.addWidget(self._heading('Không gian làm việc của bạn', banner, 17))
        description = QLabel('Theo dõi job, quản lý proxy và tiếp nhận yêu cầu từ Telegram.', banner)
        description.setWordWrap(True)
        banner_text.addWidget(description)
        banner_row.addLayout(banner_text, 1)
        self._load_banner_image()
        root.addWidget(banner)

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(14)
        metrics.setVerticalSpacing(14)
        definitions = (
            ('Tổng job', 'lbl_total_jobs', 'Job còn trong danh sách phiên này', '#65a2ff'),
            ('Đang chạy', 'lbl_running_jobs', 'Bao gồm job đang chờ hoặc đang dừng', '#65a2ff'),
            ('Hoàn thành', 'lbl_completed_jobs', 'Job đã đạt mục tiêu', '#2dd381'),
            ('Thất bại', 'lbl_failed_jobs', 'Không tính job đã hủy', '#ff6978'),
            ('Bean thành công', 'lbl_successful_beans', 'Bean thực tế từ các job trong phiên', '#d09aff'),
            ('Proxy sẵn sàng', 'lbl_active_proxies', 'Proxy trong danh sách hoạt động', '#2dd381'),
        )
        for index, (caption, attribute, hint, color) in enumerate(definitions):
            card = self._card()
            card.setStyleSheet(f'QFrame#dashboardCard {{ background: #232426; border: 1px solid #303238; border-top: 2px solid {color}; border-radius: 10px; }}')
            layout = QVBoxLayout(card)
            layout.setContentsMargins(16, 12, 16, 12)
            layout.setSpacing(5)
            layout.addWidget(QLabel(caption, card))
            value = QLabel('0', card)
            value.setStyleSheet(f'color: {color}; font-size: 25px; font-weight: 700;')
            setattr(self, attribute, value)
            layout.addWidget(value)
            note = QLabel(hint, card)
            note.setWordWrap(True)
            note.setStyleSheet('color: #7f90aa; font-size: 11px;')
            layout.addWidget(note)
            metrics.addWidget(card, index // 3, index % 3)
        for column in range(3):
            metrics.setColumnStretch(column, 1)
        root.addLayout(metrics)

        recent = self._card()
        recent_layout = QVBoxLayout(recent)
        recent_layout.setContentsMargins(18, 16, 18, 16)
        recent_layout.setSpacing(12)
        recent_header = QHBoxLayout()
        recent_header.addWidget(self._heading('Job gần đây', recent))
        recent_header.addStretch()
        recent_header.addWidget(QLabel('10 job mới nhất trong phiên', recent))
        recent_layout.addLayout(recent_header)
        self.table_recent_jobs = QTableWidget(0, 6, recent)
        self.table_recent_jobs.setHorizontalHeaderLabels(['Khách hàng / Job', 'Mã mời', 'Tiến độ', 'Bean', 'Trạng thái', 'Bắt đầu'])
        self.table_recent_jobs.verticalHeader().setVisible(False)
        self.table_recent_jobs.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_recent_jobs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table_recent_jobs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_recent_jobs.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table_recent_jobs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_recent_jobs.setMinimumHeight(150)
        self.table_recent_jobs.setToolTip('Nhấp đúp một job để mở nhật ký.')
        self.table_recent_jobs.cellDoubleClicked.connect(self._open_job)
        recent_layout.addWidget(self.table_recent_jobs, 1)
        self.lbl_empty_jobs = QLabel('Chưa có job trong phiên. Chọn “Tạo job mới” để bắt đầu.', recent)
        self.lbl_empty_jobs.setAlignment(Qt.AlignmentFlag.AlignCenter)
        recent_layout.addWidget(self.lbl_empty_jobs)
        root.addWidget(recent, 1)

        footer = QHBoxLayout()
        footer.setSpacing(14)
        telegram_card = self._card()
        telegram_layout = QHBoxLayout(telegram_card)
        telegram_layout.setContentsMargins(18, 15, 18, 15)
        telegram_text = QVBoxLayout()
        telegram_text.setSpacing(5)
        telegram_text.addWidget(self._heading('Telegram', telegram_card))
        self.lbl_telegram_users = QLabel('0 người dùng được cấp quyền', telegram_card)
        telegram_text.addWidget(self.lbl_telegram_users)
        self.lbl_telegram_status = QLabel('Bot chưa bật', telegram_card)
        self.lbl_telegram_status.setStyleSheet('color: #7f90aa; font-size: 11px;')
        telegram_text.addWidget(self.lbl_telegram_status)
        telegram_layout.addLayout(telegram_text, 1)
        self.btn_telegram = self._button('Quản lý', 'switch_to_telegram_tab')
        telegram_layout.addWidget(self.btn_telegram)
        footer.addWidget(telegram_card, 1)
        shortcuts = self._card()
        shortcuts_layout = QVBoxLayout(shortcuts)
        shortcuts_layout.setContentsMargins(18, 15, 18, 15)
        shortcuts_layout.setSpacing(10)
        shortcuts_layout.addWidget(self._heading('Truy cập nhanh', shortcuts))
        shortcuts_row = QHBoxLayout()
        self.btn_proxy = self._button('Proxy Pool', 'switch_to_proxy_tab')
        self.btn_settings = self._button('Cài đặt chung', 'switch_to_settings_tab')
        shortcuts_row.addWidget(self.btn_proxy)
        shortcuts_row.addWidget(self.btn_settings)
        shortcuts_layout.addLayout(shortcuts_row)
        footer.addWidget(shortcuts, 1)
        root.addLayout(footer)

    def _button(self, text, destination, *, primary=False):
        button = QPushButton(text, self)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            button.setObjectName('primaryButton')
        button.clicked.connect(lambda _checked=False: self._navigate(destination))
        return button

    def _navigate(self, destination):
        if callable(destination):
            destination()
            return
        callback = getattr(self.window(), destination, None)
        if callable(callback):
            callback()

    def _open_new_job(self):
        self.mochi_page.workspace_tabs.setCurrentIndex(0)
        self.mochi_page.job_tabs.setCurrentIndex(0)
        self._navigate('switch_to_mochi_tab')

    def _load_banner_image(self):
        try:
            from gui.branding import asset_path
            path = asset_path('dashboard_banner')
            pixmap = QPixmap(str(path)) if path and Path(path).is_file() else QPixmap()
        except (ImportError, KeyError, TypeError, OSError):
            pixmap = QPixmap()
        self.banner_frame.set_artwork(pixmap)

    def _refresh_if_visible(self):
        if self.isVisible():
            self.refresh()

    def refresh(self, *_):
        """Refresh actual session values; this method performs no network calls."""
        records = list(getattr(self.mochi_page, '_session_jobs', {}).values())
        terminal_failed = {'Thất bại', 'Mã không hợp lệ', 'Mã mời lỗi', 'Đạt giới hạn 24h'}
        active = {'Đang chạy', 'Đang khởi tạo', 'Đang chờ', 'Đang dừng...', 'Hẹn giờ'}
        self.lbl_total_jobs.setText(f'{len(records):,}')
        self.lbl_running_jobs.setText(f'{sum(record.status in active for record in records):,}')
        self.lbl_completed_jobs.setText(f'{sum(record.status == "Hoàn thành" for record in records):,}')
        self.lbl_failed_jobs.setText(f'{sum(record.status in terminal_failed for record in records):,}')
        successful = sum(max(0, record.success_count) for record in records)
        self.lbl_successful_beans.setText(f'{successful * MOCHI_INVITATION_REWARD_POINTS:,}')
        self.lbl_active_proxies.setText(f'{len(self.proxy_page.get_active_proxies()):,}')

        users = self.telegram_page.store.list_users()
        self.lbl_telegram_users.setText(f'{sum(user.active for user in users):,} người dùng được cấp quyền')
        accepting = bool(getattr(self.telegram_page, '_accepting_requests', False))
        self.lbl_telegram_status.setText('Bot đang nhận yêu cầu' if accepting else 'Bot chưa nhận yêu cầu')
        self.lbl_telegram_status.setStyleSheet(f'color: {"#2dd381" if accepting else "#7f90aa"}; font-size: 11px;')

        # Insertion order is chronological and avoids comparing timestamps from different sources.
        latest = list(reversed(records))[:10]
        signature = tuple((record.job_id, record.customer_name, record.invite_code,
            record.success_count, record.target_count, record.status, record.created_at)
            for record in latest)
        if signature == self._table_signature:
            return
        self._table_signature = signature
        self.table_recent_jobs.setRowCount(len(latest))
        self.lbl_empty_jobs.setVisible(not latest)
        for row, record in enumerate(latest):
            values = (record.customer_name or record.job_id, record.invite_code,
                f'{record.success_count} / {record.target_count}',
                f'{record.success_count * MOCHI_INVITATION_REWARD_POINTS:,}',
                record.status, record.created_at.strftime('%H:%M:%S'))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, record.job_id)
                item.setToolTip(record.job_id)
                if column == 4:
                    color = ('#2dd381' if record.status == 'Hoàn thành' else
                        '#ff6978' if record.status in terminal_failed else
                        '#f1bc61' if record.status == 'Đã hủy' else '#79acff')
                    item.setForeground(QColor(color))
                self.table_recent_jobs.setItem(row, column, item)

    def _open_job(self, row, _column):
        item = self.table_recent_jobs.item(row, 0)
        if item is None:
            return
        self._navigate('switch_to_mochi_tab')
        job_id = item.data(Qt.ItemDataRole.UserRole)
        show_logs = getattr(self.mochi_page, 'show_job_logs', None)
        if callable(show_logs):
            show_logs(job_id)
        else:
            self.mochi_page._select_job(job_id)
