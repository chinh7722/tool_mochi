"""Local Telegram settings and allowlisted creation of Mochi jobs."""

import hashlib
import uuid
from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from PySide6.QtCore import Qt, QTimer, QSettings
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import (QCheckBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
        QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
        QDialog, QFrame, QAbstractItemView, QPushButton, QPlainTextEdit, QScrollArea, QTabWidget, QVBoxLayout, QWidget)
except ImportError:
    from PyQt6.QtCore import Qt, QTimer, QSettings
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import (QCheckBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
        QLineEdit, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
        QDialog, QFrame, QAbstractItemView, QPushButton, QPlainTextEdit, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from core.config.constants import required_mochi_invitation_accounts
from core.finance import format_vnd, money_units
from core.job_failure import job_failure_reason
from core.job_schedule import schedule_text
from core.mail.providers.priority import GMAILVIP, ONE_TRIEU
from core.telegram_bot import TelegramConfig, parse_chat_ids, validate_bot_token
from core.telegram_users import LOCAL_USER_ID, TelegramUserStore
from core.telegram_broadcast import BroadcastProgress, announcement_length, validate_announcement
from gui.telegram_bot import TelegramBotThread
from gui.customer_management import CustomerManagement, SePayPanel, vnd


class TelegramPage(QWidget):
    def __init__(self, settings, mochi_page, parent=None, *, user_store=None):
        super().__init__(parent)
        self.setObjectName('telegramPage')
        self.setStyleSheet('''
            QWidget#telegramPage, QWidget#telegramContent { background-color: #18181b; color: #f4f4f5; }
            QLabel, QCheckBox { color: #d4d4d8; font-size: 12px; }
            QFrame#telegramCard { background-color: #232326; border: 1px solid #323238; border-radius: 10px; }
            QScrollArea#telegramScroll { background-color: #18181b; border: none; }
            QTabWidget::pane { background: #18181b; border: 1px solid #323238; border-radius: 8px; }
            QTabBar::tab { background: #111827; color: #9ba9c0; padding: 11px 18px;
                border: 1px solid #323238; border-radius: 5px; margin-right: 4px; }
            QTabBar::tab:selected { background: #192b4a; color: #60a5fa; border-color: #3b639b; }
            QLineEdit, QComboBox, QTableWidget {
                background-color: #1e1e21; color: #f4f4f5;
                border: 1px solid #3b3b43; border-radius: 6px; padding: 8px;
            }
            QLineEdit:focus, QComboBox:focus { border-color: #3b82f6; }
            QLineEdit:disabled, QComboBox:disabled { color: #71717a; }
            QComboBox::drop-down { width: 24px; border: none; }
            QTableWidget { gridline-color: #34343b; padding: 0px; }
            QTableWidget::item { padding: 7px; }
            QTableWidget::item:selected { background-color: #253654; color: #f8fafc; }
            QHeaderView::section { background-color: #29292e; color: #a7b8d5; padding: 10px 7px;
                border: none; border-right: 1px solid #3b3b43; border-bottom: 1px solid #3b3b43; font-weight: 600; }
            QPushButton { background-color: #29292e; color: #b9d3fa;
                border: 1px solid #3a4b67; border-radius: 6px; padding: 9px 13px; font-weight: 600; }
            QPushButton:hover { background-color: #303744; border-color: #3b82f6; }
            QPushButton:disabled { background-color: #252529; color: #62626c; border-color: #34343b; }
            QPushButton#telegramPrimary { background-color: #2563eb; color: #ffffff; border-color: #2563eb; }
            QPushButton#telegramPrimary:hover { background-color: #3474f5; }
            QPushButton#telegramPrimary:disabled { background-color: #243c70; color: #8096bd; border-color: #243c70; }
            QPushButton#telegramDanger { background-color: #2a1d24; color: #fb7185; border-color: #60313d; }
            QPushButton#telegramDanger:hover { background-color: #42232d; }
            QCheckBox { spacing: 8px; }
            QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #52525b; border-radius: 3px; background-color: #1e1e21; }
            QCheckBox::indicator:checked { background-color: #2563eb; border-color: #60a5fa; }
        ''')
        self.settings, self.mochi_page = settings, mochi_page
        self.config = TelegramConfig.from_settings(settings)
        if user_store is None:
            base = Path(__file__).resolve().parents[2]
            if base.name == 'src':
                base = base.parent
            directory = (Path(settings.fileName()).parent if settings.format() == QSettings.Format.IniFormat
                else base / 'data')
            user_store = TelegramUserStore(directory / 'telegram_users.sqlite3')
        self.store = user_store
        # Seed before restoring schedules or auto-starting bot/SePay workers.
        # Detailed success events are idempotent and never added twice on restart.
        try:
            if mochi_page.invite_quota_store is None:
                raise RuntimeError('Quota store unavailable')
            mochi_page.invite_quota_store.seed_legacy_jobs(self.store.invite_quota_history())
        except Exception:
            mochi_page._invite_quota_error=(
                'Không khôi phục được bộ đếm lượt nhập mã từ lịch sử. Chưa khởi chạy hoặc mua mail.')
        mochi_page.mail_purchase_recorder = self.store.record_purchase
        mochi_page.local_job_registrar = self._register_local_job
        mochi_page.job_order_lookup = self.store.get_customer_order_for_job
        mochi_page.scheduled_job_claim = self.store.claim_scheduled_job
        mochi_page.scheduled_job_cancel = self.store.cancel_scheduled_job
        mochi_page.scheduled_job_defer = self.store.defer_queued_schedule
        mochi_page.combo_job_reschedule = self.store.reschedule_combo_job
        mochi_page.job_mail_keys_resolver = self._job_mail_keys
        self.store.migrate_legacy(self.config.chat_ids)
        self.store.sync_customer_profiles()
        self.config = TelegramConfig(self.config.token, self.store.active_ids(), self.config.auto_start,
            tuple(self.store.roles().items()))
        self.worker = None
        self._accepting_requests = False
        self._closing = False
        self._job_chats = {}
        self._broadcast_id = None
        self._broadcast_fingerprint = None
        self._broadcast_last = None
        self._broadcast_recipient_count = 0
        self._build_ui()
        if mochi_page._invite_quota_error:
            self.lbl_status.setText(mochi_page._invite_quota_error)
        self._render_users()
        try:
            archived={entry.job_id for entry in mochi_page.job_history_store.read()}
            mochi_page.job_history_store.record_many(entry for entry in self.store.finished_job_history()
                if entry.job_id not in archived)
            mochi_page.history_page.refresh(sync=False)
        except Exception:
            mochi_page.history_page.refresh(sync=False,warning='Chưa đồng bộ đủ lịch sử cũ sang CSV. '
                'Dữ liệu gốc được giữ trong tool; đóng file CSV nếu đang mở và mở lại tool để thử lại.')
        mochi_page.job_finished.connect(self._on_job_finished)
        mochi_page.job_failed.connect(self._on_job_failed)
        mochi_page.job_dispatch_changed.connect(self._on_job_dispatch_changed)
        mochi_page.job_progress.connect(self._on_job_progress)
        if hasattr(mochi_page, 'job_combo_waiting'):
            mochi_page.job_combo_waiting.connect(self._on_combo_waiting)
        if hasattr(mochi_page, 'mail_purchase_recorded'):
            mochi_page.mail_purchase_recorded.connect(self._on_mail_purchase_recorded)
        try:
            self.store.recover_combo_jobs()
            for job in self.store.scheduled_jobs():
                try:
                    mochi_page.restore_scheduled_job(job)
                    if job['chat_id'] > 0:
                        self._job_chats[job['job_id']] = (job['bot_fingerprint'],job['chat_id'])
                except Exception:
                    self.lbl_status.setText('Có lịch job chưa khôi phục được; dữ liệu đã lưu vẫn được giữ.')
        except Exception:
            self.lbl_status.setText('Chưa khôi phục đủ lịch job. Kiểm tra dữ liệu; lịch đã lưu vẫn được giữ.')
        if self.config.auto_start:
            QTimer.singleShot(0, self._start_bot)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 22)
        layout.setSpacing(7)
        title = QLabel('Telegram', self)
        title.setStyleSheet('font-size: 26px; font-weight: 700; color: #fafafa;')
        layout.addWidget(title)
        description = QLabel('Kết nối bot, quản lý người dùng và theo dõi số bean đã giao thành công.', self)
        description.setStyleSheet('color: #9ba9c0; font-size: 12px;')
        description.setWordWrap(True)
        layout.addWidget(description)
        layout.addSpacing(12)

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName('telegramScroll')
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(self)
        content.setObjectName('telegramContent')
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(18)
        self.scroll.setWidget(content)
        self.tabs = QTabWidget(self)
        self.tabs.addTab(self.scroll,'Người dùng và bot')
        layout.addWidget(self.tabs,1)

        bot_card = QFrame(content)
        bot_card.setObjectName('telegramCard')
        bot_box = QVBoxLayout(bot_card)
        bot_box.setContentsMargins(18, 16, 18, 16)
        bot_box.setSpacing(12)
        bot_title = QLabel('Cấu hình bot', bot_card)
        bot_title.setStyleSheet('font-size: 14px; font-weight: 700; color: #f4f4f5;')
        bot_box.addWidget(bot_title)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        self.input_token = QLineEdit(bot_card)
        self.input_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_token.setPlaceholderText('Nhập Bot Token do @BotFather cấp')
        self.input_token.setText(self.config.token)
        form.addRow('Bot Token', self.input_token)
        bot_box.addLayout(form)
        bot_actions = QHBoxLayout()
        self.chk_auto_start = QCheckBox('Tự bật bot khi mở tool', bot_card)
        self.chk_auto_start.setChecked(self.config.auto_start)
        bot_actions.addWidget(self.chk_auto_start)
        bot_actions.addStretch()
        self.btn_save = QPushButton('Lưu cấu hình', bot_card)
        self.btn_start = QPushButton('Bật bot', bot_card)
        self.btn_start.setObjectName('telegramPrimary')
        self.btn_stop = QPushButton('Tắt bot', bot_card)
        self.btn_stop.setEnabled(False)
        self.btn_save.clicked.connect(self._save_config)
        self.btn_start.clicked.connect(self._start_bot)
        self.btn_stop.clicked.connect(self._stop_bot)
        for button in (self.btn_save, self.btn_start, self.btn_stop):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            bot_actions.addWidget(button)
        bot_box.addLayout(bot_actions)
        self.lbl_status = QLabel('Bot chưa bật.', bot_card)
        self.lbl_status.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet('color: #60a5fa; font-size: 12px;')
        bot_box.addWidget(self.lbl_status)
        help_text = QLabel('/id — lấy Chat ID   ·   /job MÃ_MỜI SỐ_KẸO [TÊN_KHÁCH] — tạo job   ·   /stats — thống kê cá nhân\n'
            '/combo MÃ_MỜI SỐ_BEAN [TÊN_KHÁCH] — tự tiếp tục sau 24 giờ 1 phút khi đạt giới hạn ngày\n'
            '/mailapi KEY_GMAILVIP KEY_1TRIEUMAIL — lưu đủ 2 API riêng cho admin/partner; /mailapi — xem trạng thái\n'
            '/hen MÃ_MỜI SỐ_BEAN DD/MM/YYYY HH:MM — hẹn giờ Việt Nam · /lich — xem lịch · /huy MÃ_JOB — hủy job chưa chạy\n'
            'Ví dụ: /job VBRN5H 1000 Khách A. Bot chỉ nhận chat riêng khi tool đang mở; '
            'lệnh tồn lúc bot tắt được bỏ qua. Job dùng cấu hình trong Cài đặt chung.', bot_card)
        help_text.setWordWrap(True)
        help_text.setStyleSheet('color: #9ba9c0; font-size: 11px;')
        bot_box.addWidget(help_text)
        body.addWidget(bot_card)

        broadcast_card = QFrame(content)
        broadcast_card.setObjectName('telegramCard')
        broadcast_box = QVBoxLayout(broadcast_card)
        broadcast_box.setContentsMargins(18,16,18,16)
        broadcast_title = QLabel('Gửi thông báo đến admin / partner',broadcast_card)
        broadcast_title.setStyleSheet('font-size: 14px; font-weight: 700; color: #f4f4f5;')
        broadcast_box.addWidget(broadcast_title)
        self.lbl_broadcast_audience = QLabel(broadcast_card)
        self.lbl_broadcast_audience.setWordWrap(True)
        self.lbl_broadcast_audience.setStyleSheet('color: #9ba9c0; font-size: 11px;')
        broadcast_box.addWidget(self.lbl_broadcast_audience)
        self.input_broadcast = QPlainTextEdit(broadcast_card)
        self.input_broadcast.setPlaceholderText('Nhập nội dung thông báo gửi tới tất cả admin và partner đang được cấp quyền…')
        self.input_broadcast.setMinimumHeight(100)
        self.input_broadcast.setMaximumHeight(160)
        self.input_broadcast.setStyleSheet('QPlainTextEdit { background: #1d1d20; color: #f4f4f5; '
            'border: 1px solid #3f3f46; border-radius: 6px; padding: 10px; }')
        broadcast_box.addWidget(self.input_broadcast)
        broadcast_actions = QHBoxLayout()
        self.lbl_broadcast_length = QLabel('0 / 4000',broadcast_card)
        broadcast_actions.addWidget(self.lbl_broadcast_length)
        broadcast_actions.addStretch()
        self.btn_cancel_broadcast = QPushButton('Dừng gửi',broadcast_card)
        self.btn_cancel_broadcast.setEnabled(False)
        self.btn_cancel_broadcast.clicked.connect(self._cancel_broadcast)
        broadcast_actions.addWidget(self.btn_cancel_broadcast)
        self.btn_send_broadcast = QPushButton('Gửi thông báo',broadcast_card)
        self.btn_send_broadcast.setObjectName('telegramPrimary')
        self.btn_send_broadcast.clicked.connect(self._send_broadcast)
        broadcast_actions.addWidget(self.btn_send_broadcast)
        broadcast_box.addLayout(broadcast_actions)
        self.lbl_broadcast_status = QLabel('Bật bot, nhập nội dung rồi bấm Gửi thông báo.',broadcast_card)
        self.lbl_broadcast_status.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_broadcast_status.setWordWrap(True)
        self.lbl_broadcast_status.setStyleSheet('color: #60a5fa; font-size: 12px;')
        broadcast_box.addWidget(self.lbl_broadcast_status)
        self.input_broadcast.textChanged.connect(self._update_broadcast_controls)
        body.addWidget(broadcast_card)

        finance_card = QFrame(content)
        finance_card.setObjectName('telegramCard')
        finance_box = QVBoxLayout(finance_card)
        finance_box.setContentsMargins(18, 14, 18, 14)
        price_row = QHBoxLayout()
        price_row.addWidget(QLabel('Giá bán (VND / 100 bean)', finance_card))
        self.input_sale_rate = QLineEdit(finance_card)
        self.input_sale_rate.setPlaceholderText('Nhập đơn giá chung, ví dụ 1000')
        self.input_sale_rate.setToolTip('Ví dụ 1000 hoặc 1000.25; dấu chấm là phần thập phân, không nhập dấu ngăn cách hàng nghìn.')
        self.input_sale_rate.setText(str(self.settings.value('finance/sale_rate_per_100', '') or ''))
        price_row.addWidget(self.input_sale_rate, 1)
        self.btn_save_finance = QPushButton('Lưu đơn giá', finance_card)
        self.btn_save_finance.clicked.connect(self._save_finance_settings)
        price_row.addWidget(self.btn_save_finance)
        self.btn_finance_ledger = QPushButton('Giao dịch / đối soát', finance_card)
        self.btn_finance_ledger.clicked.connect(self._show_finance_ledger)
        price_row.addWidget(self.btn_finance_ledger)
        finance_box.addLayout(price_row)
        self.lbl_finance_status = QLabel('Giá được chốt khi tạo job. Đơn khách hàng thu theo bean thành công đã chốt; '
            'job Local và ID được cấp quyền cộng doanh thu khi hoàn thành. '
            'chi phí ghi ngay khi mua mail, kể cả mail chưa dùng. Lợi nhuận = doanh thu − chi phí. '
            'Local gom các job tạo từ máy tính.', finance_card)
        self.lbl_finance_status.setWordWrap(True)
        self.lbl_finance_status.setStyleSheet('color: #9ba9c0; font-size: 11px;')
        finance_box.addWidget(self.lbl_finance_status)
        body.addWidget(finance_card)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        for caption_text, attribute, accent in (
                ('Người dùng được cấp quyền', 'lbl_user_count', '#3b82f6'),
                ('Job hoàn thành', 'lbl_completed_jobs', '#22c55e'),
                ('Job thất bại', 'lbl_failed_jobs', '#f87171'),
                ('Tổng bean đã bán', 'lbl_sold_beans', '#c084fc')):
            card = QFrame(content)
            card.setObjectName('telegramStat')
            card.setStyleSheet('QFrame#telegramStat {background: #232326; border: 1px solid #323238; '
                'border-top: 2px solid ' + accent + '; border-radius: 9px;}')
            box = QVBoxLayout(card)
            box.setContentsMargins(16, 12, 16, 12)
            box.setSpacing(6)
            caption, value = QLabel(caption_text, card), QLabel('0', card)
            caption.setWordWrap(True)
            caption.setStyleSheet('color: #9ba9c0; font-size: 11px;')
            value.setStyleSheet('color: #f4f4f5; font-size: 23px; font-weight: 700;')
            box.addWidget(caption)
            box.addWidget(value)
            cards.addWidget(card, 1)
            setattr(self, attribute, value)
        body.addLayout(cards)

        money_cards = QHBoxLayout()
        for text, attribute, color in (('Doanh thu (VND)', 'lbl_revenue', '#60a5fa'),
                ('Chi phí mail (VND)', 'lbl_cost', '#fb7185'), ('Lợi nhuận (VND)', 'lbl_profit', '#34d399')):
            card = QFrame(content)
            card.setObjectName('telegramCard')
            box = QVBoxLayout(card)
            caption, value = QLabel(text, card), QLabel('0', card)
            caption.setStyleSheet('color: #9ba9c0; font-size: 11px;')
            value.setStyleSheet(f'font-size: 22px; font-weight: 700; color: {color};')
            box.addWidget(caption)
            box.addWidget(value)
            money_cards.addWidget(card, 1)
            setattr(self, attribute, value)
        body.addLayout(money_cards)

        users_card = QFrame(content)
        users_card.setObjectName('telegramCard')
        users_box = QVBoxLayout(users_card)
        users_box.setContentsMargins(18, 16, 18, 16)
        users_box.setSpacing(12)
        users_head = QHBoxLayout()
        headings = QVBoxLayout()
        headings.setSpacing(4)
        users_title = QLabel('Quản lý người dùng', users_card)
        users_title.setStyleSheet('font-size: 14px; font-weight: 700; color: #f4f4f5;')
        headings.addWidget(users_title)
        classification = QLabel('Admin / partner dùng API mua mail riêng. Nguồn mua, product ID và số luồng do Cài đặt chung quản lý.', users_card)
        classification.setStyleSheet('color: #9ba9c0; font-size: 11px;')
        classification.setWordWrap(True)
        headings.addWidget(classification)
        users_head.addLayout(headings, 2)
        self.input_search = QLineEdit(users_card)
        self.input_search.setPlaceholderText('Tìm tên, Chat ID hoặc vai trò…')
        self.input_search.textChanged.connect(self._render_users)
        users_head.addWidget(self.input_search, 1)
        users_box.addLayout(users_head)

        fields = QGridLayout()
        fields.setHorizontalSpacing(12)
        fields.setVerticalSpacing(6)
        self.input_chat_id = QLineEdit(users_card)
        self.input_chat_id.setPlaceholderText('Ví dụ: 123456789')
        self.input_name = QLineEdit(users_card)
        self.input_name.setPlaceholderText('Tên người dùng / đối tác')
        self.input_name.setMaxLength(120)
        self.combo_role = QComboBox(users_card)
        self.combo_role.addItem('partner', 'partner')
        self.combo_role.addItem('admin', 'admin')
        self.combo_role.setToolTip('Nhãn phân loại người dùng; không thay đổi quyền trên bot.')
        for column, (label, field) in enumerate((('Chat ID', self.input_chat_id),
                ('Tên người dùng', self.input_name), ('Vai trò', self.combo_role))):
            fields.addWidget(QLabel(label, users_card), 0, column)
            fields.addWidget(field, 1, column)
        fields.setColumnStretch(0, 2)
        fields.setColumnStretch(1, 3)
        fields.setColumnStretch(2, 1)
        users_box.addLayout(fields)

        mail_fields = QGridLayout()
        self.input_user_gmailvip_key = QLineEdit(users_card)
        self.input_user_one_trieu_key = QLineEdit(users_card)
        for column, (title, entry) in enumerate((('API riêng GmailVip.vn',self.input_user_gmailvip_key),
                ('API riêng 1TrieuMail.com',self.input_user_one_trieu_key))):
            entry.setEchoMode(QLineEdit.EchoMode.Password)
            entry.setMaxLength(2048)
            entry.setPlaceholderText('Chọn người dùng trong bảng rồi nhập API key')
            mail_fields.addWidget(QLabel(title,users_card),0,column)
            mail_fields.addWidget(entry,1,column)
            mail_fields.setColumnStretch(column,1)
        users_box.addLayout(mail_fields)
        mail_actions = QHBoxLayout()
        self.lbl_user_mail_keys = QLabel('Chọn admin/partner để quản lý API riêng.',users_card)
        self.lbl_user_mail_keys.setWordWrap(True)
        self.lbl_user_mail_keys.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_user_mail_keys.setStyleSheet('color: #9ba9c0; font-size: 11px;')
        mail_actions.addWidget(self.lbl_user_mail_keys,1)
        self.btn_save_user_mail_keys = QPushButton('Lưu 2 API mua mail',users_card)
        self.btn_save_user_mail_keys.clicked.connect(self._save_user_mail_keys)
        mail_actions.addWidget(self.btn_save_user_mail_keys)
        users_box.addLayout(mail_actions)

        actions = QHBoxLayout()
        self.btn_add_id = QPushButton('+ Thêm người dùng', users_card)
        self.btn_add_id.setObjectName('telegramPrimary')
        self.btn_add_id.clicked.connect(self._add_id)
        self.input_chat_id.returnPressed.connect(self._add_id)
        self.btn_update_user = QPushButton('Lưu tên / vai trò', users_card)
        self.btn_update_user.clicked.connect(self._save_user)
        self.btn_remove_id = QPushButton('Thu hồi quyền', users_card)
        self.btn_remove_id.setObjectName('telegramDanger')
        self.btn_remove_id.clicked.connect(self._remove_ids)
        self.btn_restore_id = QPushButton('Khôi phục quyền', users_card)
        self.btn_restore_id.clicked.connect(self._restore_ids)
        self.btn_view_jobs = QPushButton('Xem job', users_card)
        self.btn_view_jobs.clicked.connect(self._show_user_jobs)
        for button in (self.btn_add_id, self.btn_update_user, self.btn_remove_id, self.btn_restore_id, self.btn_view_jobs):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button)
        actions.addStretch()
        users_box.addLayout(actions)
        self.table_users = QTableWidget(0, 10, users_card)
        self.table_users.setHorizontalHeaderLabels(['Chat ID', 'Tên', 'Vai trò',
            'Job\nhoàn thành', 'Job\nthất bại', 'Bean\nđã bán', 'Quyền\ntruy cập',
            'Doanh thu\n(VND)', 'Chi phí\n(VND)', 'Lợi nhuận\n(VND)'])
        self.table_users.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_users.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table_users.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_users.verticalHeader().setVisible(False)
        self.table_users.verticalHeader().setDefaultSectionSize(38)
        self.table_users.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table_users.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((85, 140, 65, 90, 75, 80, 100, 100, 100, 100)):
            self.table_users.setColumnWidth(column, width)
        self.table_users.horizontalHeader().setStretchLastSection(True)
        self.table_users.currentCellChanged.connect(self._load_selected_user)
        self.table_users.itemSelectionChanged.connect(self._update_user_actions)
        self.table_users.cellDoubleClicked.connect(lambda _row, _column: self._show_user_jobs())
        self.table_users.setToolTip('Chọn người dùng để sửa tên/vai trò. Thu hồi quyền giữ nguyên lịch sử và thống kê.')
        self.table_users.setMinimumHeight(225)
        users_box.addWidget(self.table_users, 1)
        rule = QLabel('Bean đã bán tính theo số bean thực tế đã chạy thành công, kể cả phần thành công trong job lỗi/hủy. '
            'Job hủy không tính là thất bại. Thu hồi quyền giữ nguyên lịch sử.', users_card)
        rule.setWordWrap(True)
        rule.setStyleSheet('color: #9ba9c0; font-size: 11px;')
        users_box.addWidget(rule)
        body.addWidget(users_card, 1)

        self.customer_page = CustomerManagement(self.store,self.mochi_page,self)
        self.tabs.addTab(self.customer_page,'Quản lý khách hàng')
        self.sepay_page = SePayPanel(self.settings,self.store,self._refresh_customer_finance,self)
        self.tabs.addTab(self.sepay_page,'Thanh toán SePay')

    def _refresh_customer_finance(self):
        self.customer_page.refresh()
        self._render_users()

    def _register_local_job(self, job_id, code, points, target_count, **schedule_options):
        created = self.store.record_local_job(job_id, code, points, target_count,
            sale_rate_units=self._sale_rate_snapshot(), **schedule_options)
        if created:
            try:
                self._render_users()
            except Exception:
                self.lbl_status.setText('Job Local đã được lưu; bảng thống kê sẽ cập nhật lại khi có tiến độ.')
        return created

    def _show_user_jobs(self):
        selected = self._selected_ids()
        if len(selected) != 1:
            self.lbl_status.setText('Chọn một người dùng hoặc Local để xem job.')
            return
        owner_id = selected[0]
        user = self.store.get_user(owner_id)
        if user is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f'Job của {user.name}')
        dialog.resize(1100, 560)
        dialog.setStyleSheet(self.styleSheet())
        box = QVBoxLayout(dialog)
        table = QTableWidget(0, 9, dialog)
        table.setHorizontalHeaderLabels(['Job', 'Mã mời', 'Bean yêu cầu', 'Bean thành công',
            'Trạng thái', 'Doanh thu (VND)', 'Chi phí (VND)', 'Lợi nhuận (VND)', 'Tạo lúc (GMT+7)'])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.verticalHeader().setVisible(False)
        box.addWidget(table, 1)
        status = QLabel(dialog)
        status.setWordWrap(True)
        box.addWidget(status)

        def refresh():
            try:
                jobs = self.store.list_jobs(owner_id)
                table.setRowCount(len(jobs))
                for row, job in enumerate(jobs):
                    revenue = job['success_count'] * (job['sale_rate_units'] or 0) if job['status'] == 'completed' else 0
                    incomplete = bool(job['pending_costs'] or (job['status'] == 'completed'
                        and job['finance_tracked'] and job['sale_rate_units'] is None))
                    label = self._job_status_text(job)
                    stamp = datetime.fromisoformat(job['created_at']).astimezone(
                        timezone(timedelta(hours=7))).strftime('%d/%m/%Y %H:%M')
                    values = (job['job_id'], job['code'], f"{job['requested_beans']:,}",
                        f"{job['success_count'] * 100:,}", label, format_vnd(Decimal(revenue) / 100),
                        format_vnd(Decimal(job['cost_units']) / 100) + (' *' if job['pending_costs'] else ''),
                        'Chờ đối soát' if incomplete else format_vnd(Decimal(revenue - job['cost_units']) / 100), stamp)
                    for column, value in enumerate(values):
                        item = QTableWidgetItem(value)
                        item.setToolTip(value)
                        table.setItem(row, column, item)
                status.setText(f'{len(jobs)} job đã lưu của {user.name}.')
            except Exception:
                status.setText('Không đọc được lịch sử job. Kiểm tra file dữ liệu.')
        actions = QHBoxLayout()
        reload_button, close_button = QPushButton('Làm mới', dialog), QPushButton('Đóng', dialog)
        reload_button.clicked.connect(refresh)
        close_button.clicked.connect(dialog.accept)
        actions.addWidget(reload_button)
        actions.addStretch()
        actions.addWidget(close_button)
        box.addLayout(actions)
        refresh()
        dialog.exec()

    def _sale_rate_snapshot(self):
        value = self.settings.value('finance/sale_rate_per_100', None)
        return None if value is None or str(value).strip() == '' else money_units(value)

    def _save_finance_settings(self):
        try:
            value = self.input_sale_rate.text().strip()
            units = money_units(value)
            self.settings.setValue('finance/sale_rate_per_100', str(Decimal(units) / 100))
            self.settings.sync()
        except ValueError as exc:
            self.lbl_finance_status.setText(str(exc))
            return False
        self.lbl_finance_status.setText(f'Đã lưu {format_vnd(Decimal(units) / 100)} VND / 100 bean. '
            'Chỉ áp dụng cho job mới; doanh thu cộng khi job hoàn thành.')
        return True

    def _on_mail_purchase_recorded(self, _receipt):
        try:
            self._render_users()
        except Exception:
            self.lbl_status.setText('Biên nhận đã được lưu; chưa cập nhật được bảng thống kê.')

    def _show_finance_ledger(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Giao dịch và đối soát tài chính')
        dialog.resize(1060, 600)
        dialog.setStyleSheet(self.styleSheet())
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget(dialog)
        layout.addWidget(tabs, 1)
        status = QLabel(dialog)
        status.setWordWrap(True)
        layout.addWidget(status)
        costs = QWidget(dialog)
        cost_layout = QVBoxLayout(costs)
        guide = QLabel('Chọn giao dịch thiếu số tiền để nhập chi phí thực tế sau khi đối chiếu lịch sử shop. '
            'Không sửa chi phí đã xác định. Không đối soát khi job đang chạy.', costs)
        guide.setWordWrap(True)
        cost_layout.addWidget(guide)
        cost_table = QTableWidget(0, 9, costs)
        cost_table.setHorizontalHeaderLabels(['Người dùng', 'Job', 'Shop', 'Số mail', 'Chi phí (VND)',
            'Số dư trước', 'Số dư sau', 'Trạng thái', 'Mã giao dịch'])
        cost_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        cost_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        cost_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        cost_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        cost_layout.addWidget(cost_table, 1)
        cost_row = QHBoxLayout()
        cost_input = QLineEdit(costs)
        cost_input.setPlaceholderText('Số tiền thực tế đã trừ (VND)')
        cost_input.setToolTip('Ví dụ 5607.73; tối đa hai chữ số thập phân.')
        cost_row.addWidget(cost_input, 1)
        save_cost = QPushButton('Xác nhận chi phí', costs)
        cost_row.addWidget(save_cost)
        cost_layout.addLayout(cost_row)
        tabs.addTab(costs, 'Mua mail')
        prices = QWidget(dialog)
        price_layout = QVBoxLayout(prices)
        price_guide = QLabel('Bổ sung đơn giá cho job được tạo khi chưa cấu hình giá bán. '
            'Giá đã chốt không bị thay đổi khi lưu đơn giá chung.', prices)
        price_guide.setWordWrap(True)
        price_layout.addWidget(price_guide)
        price_table = QTableWidget(0, 5, prices)
        price_table.setHorizontalHeaderLabels(['Job', 'Người dùng', 'Chat ID', 'Bean yêu cầu', 'Trạng thái'])
        price_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        price_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        price_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        price_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        price_layout.addWidget(price_table, 1)
        price_row = QHBoxLayout()
        rate_input = QLineEdit(prices)
        rate_input.setPlaceholderText('Đơn giá đã thỏa thuận (VND / 100 bean)')
        price_row.addWidget(rate_input, 1)
        save_rate = QPushButton('Bổ sung giá cho job', prices)
        price_row.addWidget(save_rate)
        price_layout.addLayout(price_row)
        tabs.addTab(prices, 'Job chưa có giá')

        def refresh():
            rows = self.store.list_purchases()
            cost_table.setRowCount(len(rows))
            for row, purchase in enumerate(rows):
                amount = purchase['cost_units']
                values = (purchase['name'], purchase['job_id'], purchase['provider'], str(purchase['quantity']),
                    'Chờ đối soát' if amount is None else format_vnd(Decimal(amount) / 100),
                    '—' if purchase['balance_before_units'] is None else format_vnd(Decimal(purchase['balance_before_units']) / 100),
                    '—' if purchase['balance_after_units'] is None else format_vnd(Decimal(purchase['balance_after_units']) / 100),
                    {'intent': 'Chưa xác nhận', 'confirmed': 'Đã ghi nhận' if amount is not None else 'Thiếu số dư sau',
                     'uncertain': 'Mất phản hồi', 'rejected': 'Shop từ chối', 'duplicate': 'Đã ghi nhận trước'}.get(
                         purchase['state'], purchase['state']), purchase['order_id'] or purchase['purchase_id'])
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, purchase)
                    cost_table.setItem(row, column, item)
            jobs = self.store.list_unpriced_jobs()
            price_table.setRowCount(len(jobs))
            for row, job in enumerate(jobs):
                for column, value in enumerate((job['job_id'], job['name'], 'LOCAL' if job['chat_id'] == LOCAL_USER_ID else str(job['chat_id']),
                        f"{job['requested_beans']:,}", job['status'])):
                    price_table.setItem(row, column, QTableWidgetItem(value))

        def reconcile():
            try:
                row = cost_table.currentRow()
                if row < 0:
                    raise ValueError('Chọn một giao dịch mua mail.')
                purchase = cost_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                worker = getattr(self.mochi_page, '_workers', {}).get(purchase['job_id'])
                if worker is not None and worker.isRunning():
                    raise ValueError('Chờ job kết thúc trước khi đối soát.')
                self.store.reconcile_purchase(purchase['purchase_id'], money_units(cost_input.text()))
                refresh()
                self._render_users()
                status.setText('Đã lưu chi phí đối soát.')
            except Exception as exc:
                status.setText(str(exc))

        def price_job():
            try:
                if price_table.currentRow() < 0:
                    raise ValueError('Chọn một job chưa có giá.')
                job_id = price_table.item(price_table.currentRow(), 0).text()
                self.store.price_job(job_id, money_units(rate_input.text()))
                refresh()
                self._render_users()
                status.setText('Đã bổ sung đơn giá cho job được chọn.')
            except Exception as exc:
                status.setText(str(exc))

        save_cost.clicked.connect(reconcile)
        save_rate.clicked.connect(price_job)
        buttons = QHBoxLayout()
        refresh_button, close_button = QPushButton('Làm mới', dialog), QPushButton('Đóng', dialog)
        refresh_button.clicked.connect(refresh)
        close_button.clicked.connect(dialog.accept)
        buttons.addWidget(refresh_button)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        try:
            refresh()
        except Exception:
            status.setText('Không đọc được sổ giao dịch; kiểm tra file dữ liệu.')
        dialog.exec()

    def _ids(self):
        return self.store.active_ids()

    def _sync_access(self):
        ids, roles = self.store.active_ids(), self.store.roles()
        self.store.sync_customer_profiles()
        self.customer_page.refresh()
        self.settings.setValue('telegram/allowed_chat_ids', '\n'.join(map(str, ids)))
        if self.worker is not None:
            self.worker.access.replace(ids, roles=roles)

    def _render_users(self, *_ , select_chat_id=None):
        selected = select_chat_id
        if selected is None and self.table_users.currentRow() >= 0:
            item = self.table_users.item(self.table_users.currentRow(), 0)
            selected = item.data(Qt.ItemDataRole.UserRole) if item else None
        users = self.store.list_users(include_local=True)
        query = self.input_search.text().strip().casefold()
        visible = [user for user in users if not query or query in str(user.chat_id)
            or query in user.name.casefold() or query in ('local' if user.is_local else user.role).casefold()]
        self.table_users.blockSignals(True)
        self.table_users.setRowCount(len(visible))
        self.table_users.clearSelection()
        self.table_users.setCurrentCell(-1, -1)
        for row, user in enumerate(visible):
            values = ('LOCAL' if user.is_local else str(user.chat_id), user.name,
                'local' if user.is_local else user.role, f'{user.completed_jobs:,}',
                f'{user.failed_jobs:,}', f'{user.sold_beans:,}',
                'Máy tính' if user.is_local else 'Được phép' if user.active else 'Đã thu hồi',
                format_vnd(user.revenue_vnd) + (' *' if user.unpriced_jobs else ''),
                format_vnd(user.cost_vnd) + (' *' if user.pending_costs else ''),
                'Chờ đối soát' if user.pending_costs or user.unpriced_jobs else format_vnd(user.profit_vnd))
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, user.chat_id)
                    if user.is_local:
                        item.setToolTip('Job tạo trực tiếp trên máy tính; không có Chat ID Telegram.')
                if column == 2:
                    item.setForeground(QColor('#c084fc' if user.role == 'admin' else '#38bdf8'))
                if column == 6:
                    item.setForeground(QColor('#34d399' if user.active else '#94a3b8'))
                if column in (7, 8, 9):
                    item.setToolTip(f'{value}\n{user.unpriced_jobs} job chưa có đơn giá; '
                        f'{user.pending_costs} giao dịch mail chưa xác định chi phí. '
                        'Số tiền có dấu * là phần đã xác định.')
                if column == 9 and not (user.pending_costs or user.unpriced_jobs):
                    item.setForeground(QColor('#34d399' if user.profit_vnd >= 0 else '#fb7185'))
                self.table_users.setItem(row, column, item)
            if user.chat_id == selected:
                self.table_users.setCurrentCell(row, 0)
                self.table_users.selectRow(row)
        self.table_users.blockSignals(False)
        self.lbl_user_count.setText(f'{sum(user.active and not user.is_local for user in users):,}')
        self.lbl_completed_jobs.setText(f'{sum(user.completed_jobs for user in users):,}')
        self.lbl_failed_jobs.setText(f'{sum(user.failed_jobs for user in users):,}')
        self.lbl_sold_beans.setText(f'{sum(user.sold_beans for user in users):,}')
        revenue = sum((user.revenue_vnd for user in users), Decimal(0))
        costs = sum((user.cost_vnd for user in users), Decimal(0))
        pending = sum(user.pending_costs + user.unpriced_jobs for user in users) + self.store.recovery_errors
        self.lbl_revenue.setText(format_vnd(revenue) + (' *' if any(u.unpriced_jobs for u in users) else ''))
        self.lbl_cost.setText(format_vnd(costs) + (' *' if any(u.pending_costs for u in users) else ''))
        self.lbl_profit.setText('Chờ đối soát' if pending else format_vnd(revenue - costs))
        if select_chat_id is not None:
            self._load_selected_user()
        self._update_user_actions()
        self._broadcast_recipient_count = sum(user.active and not user.is_local and user.role in ('admin','partner')
            for user in users)
        self._update_broadcast_controls()

        if hasattr(self,'customer_page'):
            self.customer_page.refresh()

    def _selected_ids(self):
        return tuple(self.table_users.item(row.row(), 0).data(Qt.ItemDataRole.UserRole)
            for row in self.table_users.selectionModel().selectedRows())

    def _load_selected_user(self, *_):
        row = self.table_users.currentRow()
        item = self.table_users.item(row, 0) if row >= 0 else None
        if item is None:
            return
        user = self.store.get_user(item.data(Qt.ItemDataRole.UserRole))
        if user:
            self.input_chat_id.setText('' if user.is_local else str(user.chat_id))
            self.input_name.setText('' if user.is_local else user.name)
            if not user.is_local:
                self.combo_role.setCurrentIndex(self.combo_role.findData(user.role))
            self._load_selected_mail_keys(user)
        self._update_user_actions()

    @staticmethod
    def _mail_keys_status(keys):
        return ('GmailVip.vn: ' + ('Đã nhập' if keys.get(GMAILVIP) else 'Chưa nhập')
            + ' · 1TrieuMail.com: ' + ('Đã nhập' if keys.get(ONE_TRIEU) else 'Chưa nhập'))

    def _load_selected_mail_keys(self,user):
        self.input_user_gmailvip_key.clear()
        self.input_user_one_trieu_key.clear()
        if user.is_local:
            self.lbl_user_mail_keys.setText('Local và khách hàng dùng API chung trong Cài đặt chung.')
            return
        if not user.active:
            self.lbl_user_mail_keys.setText('Người dùng đã bị thu hồi quyền; khôi phục quyền để cập nhật API.')
            return
        try:
            keys = self.store.get_user_mail_keys(user.chat_id)
            self.input_user_gmailvip_key.setText(keys.get(GMAILVIP,''))
            self.input_user_one_trieu_key.setText(keys.get(ONE_TRIEU,''))
            self.lbl_user_mail_keys.setText(f'API của {user.name} ({user.chat_id}) · '+self._mail_keys_status(keys))
        except Exception:
            self.lbl_user_mail_keys.setText('Không đọc được API riêng; kiểm tra dữ liệu trong tool.')

    def _save_user_mail_keys(self):
        ids = self._selected_ids()
        if len(ids)!=1 or ids[0]==LOCAL_USER_ID:
            self.lbl_user_mail_keys.setText('Chọn một admin/partner trong bảng để lưu API riêng.')
            return
        if self.input_chat_id.text().strip()!=str(ids[0]):
            self.lbl_user_mail_keys.setText('Chat ID đang nhập khác dòng được chọn. Chọn đúng người dùng trong bảng trước khi lưu API.')
            return
        try:
            self.store.set_user_mail_keys(ids[0],self.input_user_gmailvip_key.text(),self.input_user_one_trieu_key.text())
            self._load_selected_user()
            self.lbl_status.setText(f'Đã lưu đủ 2 API mua mail riêng cho Chat ID {ids[0]}.')
        except (ValueError,PermissionError) as exc:
            self.lbl_user_mail_keys.setText(str(exc))
        except Exception:
            self.lbl_user_mail_keys.setText('Không lưu được API riêng; dữ liệu cũ được giữ.')

    def _update_user_actions(self):
        ids = self._selected_ids()
        remote = [chat_id for chat_id in ids if chat_id != LOCAL_USER_ID]
        self.btn_update_user.setEnabled(len(ids) == 1 and bool(remote))
        self.btn_remove_id.setEnabled(bool(remote))
        self.btn_restore_id.setEnabled(bool(remote))
        self.btn_view_jobs.setEnabled(len(ids) == 1)
        user = self.store.get_user(remote[0]) if len(ids)==1 and remote else None
        editable = bool(user and user.active)
        self.btn_save_user_mail_keys.setEnabled(editable)
        for entry in (self.input_user_gmailvip_key,self.input_user_one_trieu_key):
            entry.setEnabled(editable)
            if not editable:
                entry.clear()
        if not ids:
            self.lbl_user_mail_keys.setText('Chọn admin/partner để quản lý API riêng.')

    def _add_id(self):
        try:
            ids = parse_chat_ids(self.input_chat_id.text())
            name = self.input_name.text().strip()
            if not ids:
                raise ValueError('Nhập Chat ID trước khi thêm người dùng.')
            if len(ids) > 1 and name:
                raise ValueError('Thêm từng Chat ID khi nhập tên riêng cho người dùng.')
            for chat_id in ids:
                if self.store.get_user(chat_id) is not None:
                    raise ValueError('Chat ID đã có trong bảng. Chọn người dùng để sửa hoặc khôi phục quyền.')
            for chat_id in ids:
                self.store.upsert_user(chat_id, name or f'Telegram {chat_id}', self.combo_role.currentData())
        except Exception as exc:
            self.lbl_status.setText(str(exc))
            return
        self.input_chat_id.clear()
        self.input_name.clear()
        self._render_users()
        self._sync_access()
        self._save_config()

    def _save_user(self):
        try:
            ids = parse_chat_ids(self.input_chat_id.text())
            if len(ids) != 1:
                raise ValueError('Chọn một Chat ID để sửa tên/vai trò.')
            user = self.store.get_user(ids[0])
            if user is None:
                raise ValueError('Chat ID chưa có. Dùng Thêm người dùng để tạo mới.')
            self.store.upsert_user(user.chat_id, self.input_name.text().strip(),
                self.combo_role.currentData(), active=user.active)
        except Exception as exc:
            self.lbl_status.setText(str(exc))
            return
        self._render_users(select_chat_id=user.chat_id)
        self._sync_access()
        self._save_config()

    def _remove_ids(self):
        for chat_id in self._selected_ids():
            if chat_id != LOCAL_USER_ID:
                self.store.set_active(chat_id, False)
        self._render_users()
        self._sync_access()
        self._save_config()

    def _restore_ids(self):
        for chat_id in self._selected_ids():
            if chat_id != LOCAL_USER_ID:
                self.store.set_active(chat_id, True)
        self._render_users()
        self._sync_access()
        self._save_config()

    def _save_config(self):
        token = self.input_token.text().strip()
        try:
            if token:
                validate_bot_token(token)
            ids = self._ids()
        except ValueError as exc:
            self.lbl_status.setText(str(exc))
            return False
        except Exception:
            self.lbl_status.setText('Không đọc được dữ liệu người dùng Telegram. Chưa cập nhật cấu hình.')
            return False
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        if self.worker is not None and self.worker.fingerprint != fingerprint:
            self.lbl_status.setText('Tắt bot trước khi đổi Bot Token.')
            return False
        self.config = TelegramConfig(token, ids, self.chk_auto_start.isChecked(), tuple(self.store.roles().items()))
        self.config.save(self.settings)
        if self.worker is not None:
            self.worker.access.replace(ids, roles=self.store.roles())
        self.lbl_status.setText(f'Đã lưu {len(ids)} Chat ID được phép. Quyền mới áp dụng ngay cho lệnh tiếp theo.')
        return True

    def _update_broadcast_controls(self):
        text=self.input_broadcast.toPlainText()
        try:
            size=announcement_length(text)
        except ValueError:
            size=4001
        self.lbl_broadcast_length.setText(f'{size:,} / 4000')
        self.lbl_broadcast_length.setStyleSheet('color: #f87171;' if size>4000 else 'color: #9ba9c0;')
        self.lbl_broadcast_audience.setText(f'Người nhận: {self._broadcast_recipient_count} admin/partner đang được cấp quyền. '
            'Mỗi người nhận một tin nhắn riêng.')
        busy=self._broadcast_id is not None
        self.btn_send_broadcast.setEnabled(not self._closing and not busy and bool(text.strip()) and size<=4000)
        self.btn_cancel_broadcast.setEnabled(not self._closing and busy)

    def _send_broadcast(self):
        if self._broadcast_id is not None or self._closing:
            return
        try:
            text=validate_announcement(self.input_broadcast.toPlainText())
            worker=self.worker
            if worker is None or not self._accepting_requests or not callable(getattr(worker,'broadcast',None)):
                raise ValueError('Bật bot và chờ kết nối sẵn sàng trước khi gửi thông báo.')
            recipients=tuple(user.chat_id for user in self.store.list_users()
                if user.active and user.chat_id>0 and user.role in ('admin','partner'))
            if not recipients:
                raise ValueError('Chưa có admin/partner đang được cấp quyền để nhận thông báo.')
            worker.access.replace(self.store.active_ids(),roles=self.store.roles())
            self._broadcast_id=uuid.uuid4().hex
            self._broadcast_fingerprint=worker.fingerprint
            self._broadcast_last=BroadcastProgress(self._broadcast_id,len(recipients))
            self.lbl_broadcast_status.setText(f'Đang chuẩn bị gửi tới {len(recipients)} người…')
            self._update_broadcast_controls()
            if not worker.broadcast(self._broadcast_id,recipients,text):
                self._on_broadcast_failed(worker.fingerprint,self._broadcast_id,
                    'Bot chưa sẵn sàng. Nội dung được giữ; chưa bắt đầu lượt gửi.')
        except ValueError as exc:
            self.lbl_broadcast_status.setText(str(exc))
        except Exception:
            if self._broadcast_id:
                self._on_broadcast_failed(self._broadcast_fingerprint,self._broadcast_id,'Không bắt đầu được lượt gửi thông báo.')
            else:
                self.lbl_broadcast_status.setText('Không đọc được danh sách người nhận; chưa gửi thông báo.')

    def _show_broadcast_progress(self,state):
        self._broadcast_last=state
        heading=('Đã dừng gửi' if state.cancelled else 'Đã hoàn tất' if state.finished else 'Đang gửi')
        text=(f'{heading} · Thành công: {state.sent}/{state.total} · Lỗi/chưa xác nhận: {state.failed}'
            f' · Bỏ qua: {state.skipped} · Chưa hoàn tất: {state.remaining}')
        if state.error:
            text+='\n'+state.error
        self.lbl_broadcast_status.setText(text)
        if state.finished:
            self._broadcast_id=None
            self._broadcast_fingerprint=None
        self._update_broadcast_controls()

    def _on_broadcast_progress(self,fingerprint,state):
        if (not isinstance(state,BroadcastProgress) or state.broadcast_id!=self._broadcast_id
                or fingerprint!=self._broadcast_fingerprint):
            return
        self._show_broadcast_progress(state)

    def _on_broadcast_failed(self,fingerprint,broadcast_id,message):
        if broadcast_id!=self._broadcast_id or fingerprint!=self._broadcast_fingerprint:
            return
        self._show_broadcast_progress(replace(self._broadcast_last,finished=True,cancelled=True,error=message))

    def _cancel_broadcast(self):
        if self._broadcast_id is not None and self.worker is not None:
            cancel=getattr(self.worker,'cancel_broadcast',None)
            if callable(cancel):
                cancel()
                self.lbl_broadcast_status.setText('Đang dừng gửi thông báo; chờ xác nhận kết quả đã gửi…')

    def _start_bot(self):
        if self._closing or self.worker is not None or not self._save_config():
            return
        if not self.config.token:
            self.lbl_status.setText('Nhập Bot Token trước khi bật bot.')
            return
        worker = TelegramBotThread(self.config, self)
        self.worker = worker
        self._accepting_requests = True
        worker.job_requested.connect(self._on_job_requested)
        worker.control_requested.connect(self._on_control_requested)
        worker.customer_requested.connect(self._on_customer_requested)
        worker.status_changed.connect(self.lbl_status.setText)
        if hasattr(worker,'broadcast_progress'):
            worker.broadcast_progress.connect(self._on_broadcast_progress)
            worker.broadcast_failed.connect(self._on_broadcast_failed)
        worker.finished.connect(self._worker_stopped)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.lbl_status.setText('Đang kết nối bot Telegram...')
        worker.start()

    def _stop_bot(self):
        self._accepting_requests = False
        if self.worker is not None:
            self.worker.request_stop()
            self.btn_stop.setEnabled(False)
            self.lbl_status.setText('Đang tắt bot...')

    def _worker_stopped(self):
        self._accepting_requests = False
        if self._broadcast_id is not None and self._broadcast_last is not None:
            self._show_broadcast_progress(replace(self._broadcast_last,finished=True,cancelled=True))
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        if self.lbl_status.text() == 'Đang tắt bot...':
            self.lbl_status.setText('Bot đã tắt.')

    def shutdown_workers(self):
        self._accepting_requests = False
        self._closing = True
        payment_running = self.sepay_page.shutdown()
        if self.worker is None:
            return payment_running
        self.worker.request_stop()
        return self.worker.isRunning() or payment_running

    def _on_customer_requested(self,envelope):
        worker = self.worker
        if not isinstance(envelope,tuple) or len(envelope)!=2:
            return
        fingerprint,request = envelope
        if worker is None or worker.fingerprint!=fingerprint:
            return
        def reply(text):
            worker.reply(request.chat_id,text,request.update_id,public=True)
        if not self._accepting_requests:
            worker.complete_request(request.update_id)
            return
        try:
            actor = self.store.get_user(request.chat_id)
            if worker.access.allowed(request.chat_id) or (actor is not None and actor.active):
                self.customer_page.refresh()
                reply('Quyền của bạn đã thay đổi. Gửi lại lệnh để dùng cấu hình người dùng được cấp quyền.')
                return
            profile = self.store.ensure_customer(request.chat_id,request.name)
            self.customer_page.refresh()
            if request.action=='register':
                worker.complete_request(request.update_id)
                return
            if request.action=='job':
                self._create_customer_order(fingerprint,request,reply)
                return
            if request.action=='schedules':
                text = self._schedule_list_text(request.chat_id,fingerprint)
            elif request.action=='cancel_schedule':
                text = self._cancel_schedule_text(request.chat_id,fingerprint,request.job_id)
            elif request.action=='id':
                text = f'ID khách hàng / Chat ID của bạn: {request.chat_id}'
            elif request.action=='balance':
                text = (f'Khách hàng {request.chat_id}\nSố dư khả dụng: {vnd(profile["balance_units"])}\n'
                    f'Đơn đã tạo: {profile["total_orders"]}\nHoàn thành: {profile["completed_orders"]}\n'
                    f'Thất bại: {profile["failed_orders"]}')
            elif request.action=='deposit':
                config = self.sepay_page.config
                config.validate()
                text = (f'Nạp tiền qua SePay\nNgân hàng: {config.bank_name}\nTài khoản: {config.account_number}\n'
                    f'Chủ tài khoản: {config.account_name}\nNội dung chuyển khoản: {request.chat_id}\n'
                    'Tối thiểu: 10.000 VND; tối đa: 100.000 VND mỗi giao dịch.\n'
                    'Chỉ ghi đúng Chat ID ở nội dung. Tiền được cập nhật sau khi SePay xác nhận và tool đồng bộ. '
                    'Giao dịch sai nội dung/ngoài giới hạn cần liên hệ quản trị viên kiểm tra.')
            elif request.action=='orders':
                rows = self.store.customer_orders(request.chat_id)[:8]
                text = '\n\n'.join(f'{x["order_id"]}\n{x["code"]}: {self._job_status_text(x)}, '
                    f'{x["success_count"]*100}/{x["target_count"]*100} bean; '
                    + ('đang giữ '+vnd(x['reserved_units']) if x['charged_units'] is None else 'đã thu '+vnd(x['charged_units']))
                    for x in rows) or 'Bạn chưa có đơn hàng.'
            elif request.action=='history':
                rows = self.store.customer_history(request.chat_id)[:10]
                text = '\n\n'.join(f'{x["created_at"]}\n{x["description"]}\n{x["kind"]}: {vnd(x["amount_units"])}'
                    for x in rows) or 'Bạn chưa có giao dịch.'
            else:
                text = (f'Khách hàng {request.chat_id}\n'
                    '/id — ID khách hàng\n/sodu — số dư và thống kê\n/nap — hướng dẫn nạp SePay\n'
                    '/job MÃ_MỜI SỐ_BEAN — đặt đơn\n/donhang — đơn của bạn\n/giaodich — lịch sử giao dịch\n'
                    '/combo MÃ_MỜI SỐ_BEAN — đặt combo, tự tiếp tục sau 24 giờ 1 phút khi đạt giới hạn ngày\n'
                    '/hen MÃ_MỜI SỐ_BEAN DD/MM/YYYY HH:MM — hẹn giờ Việt Nam\n/lich — xem lịch\n/huy MÃ_JOB — hủy job chưa chạy\n'
                    'Tiền được giữ khi tạo đơn, thu theo bean thực tế thành công và hoàn phần chưa chạy được.')
            reply(text)
        except ValueError as exc:
            self.customer_page.refresh()
            reply(str(exc) if request.action!='deposit' else 'Chưa cấu hình xong SePay. Liên hệ quản trị viên trước khi chuyển tiền.')
        except Exception:
            reply('Chưa xử lý được dữ liệu khách hàng. Nhờ quản trị viên kiểm tra trong tool.')

    def _create_customer_order(self,fingerprint,request,reply):
        previous = self.store.lookup_request(fingerprint,request.update_id)
        if previous is not None:
            order = self.store.get_customer_order_for_job(previous.job_id)
            reply('Yêu cầu đã có đơn '+order['order_id'] if order and previous.chat_id==request.chat_id
                else 'Yêu cầu này đã được ghi nhận; chưa tạo thêm đơn.')
            return
        rate = self._sale_rate_snapshot()
        if rate is None or rate<=0:
            raise ValueError('Chưa cấu hình đơn giá bán. Liên hệ quản trị viên.')
        required = required_mochi_invitation_accounts(request.points) * rate
        profile = self.store.get_customer(request.chat_id)
        balance = profile['balance_units']
        if balance < required:
            raise ValueError(f'Số dư không đủ. Cần {vnd(required)}, khả dụng {vnd(balance)}. Dùng /nap để nạp tiền.')
        registered = []
        registration_errors = []
        def before_start(job_id,code,points,target_count,**schedule_options):
            try:
                created = self.store.record_customer_order(job_id,request.chat_id,code,points,target_count,
                    fingerprint,request.update_id,rate,**schedule_options)
            except Exception as exc:
                registration_errors.append(str(exc))
                raise
            if created:
                registered.append(job_id)
            return created
        error = ''
        try:
            job_id = self.mochi_page._on_start_clicked(code=request.code,points=request.points,
                customer_name=request.customer or f'Khách {request.chat_id}',before_start=before_start,
                creator_name=profile['name'],creator_chat_id=request.chat_id,scheduled_at=request.scheduled_at,
                combo=request.combo)
        except ValueError as exc:
            error,job_id = str(exc),None
        except Exception:
            job_id = None
        if not job_id:
            if registered:
                self.store.finish_job(registered[0],'failed',0)
            self._refresh_customer_finance()
            reply(error or (registration_errors[0] if registration_errors else 'Chưa tạo được đơn. '
                'Nhờ quản trị viên kiểm tra cấu hình mail/proxy. Những khoản đã giữ cho lần tạo lỗi đã được hoàn lại.'))
            return
        order = self.store.get_customer_order_for_job(job_id)
        self._job_chats[job_id] = (fingerprint,request.chat_id)
        self._refresh_customer_finance()
        combo_text = 'Combo tự tiếp tục sau 24 giờ 1 phút khi đạt giới hạn ngày, đến khi đủ mục tiêu.\n' if request.combo else ''
        reply(f'Đã tạo đơn {order["order_id"]}\nMã job: {job_id}\nMã mời: {request.code}\n' + combo_text +
            f'Trạng thái: {self.mochi_page.job_dispatch_text(job_id)}\n'
            f'Mục tiêu: {required_mochi_invitation_accounts(request.points)*100} bean\n'
            f'Tạm giữ: {vnd(order["reserved_units"])}. Thu theo bean thực tế thành công và hoàn phần còn lại.')

    def _on_job_requested(self, envelope):
        worker = self.worker
        if not isinstance(envelope, tuple) or len(envelope) != 2:
            return
        fingerprint, request = envelope
        # Permissions may have been revoked after the polling thread emitted.
        if worker is None or worker.fingerprint != fingerprint:
            return
        try:
            user = self.store.get_user(request.chat_id)
        except Exception:
            worker.reply(request.chat_id, 'Không kiểm tra được quyền truy cập. Nhờ quản trị viên kiểm tra dữ liệu trong tool.', request.update_id)
            return
        if (not self._accepting_requests or not worker.access.allowed(request.chat_id)
                or user is None or user.is_local or not user.active):
            worker.complete_request(request.update_id)
            return
        try:
            previous = self.store.lookup_request(fingerprint, request.update_id)
        except Exception:
            worker.reply(request.chat_id, 'Không đọc được lịch sử yêu cầu. Chưa tạo job mới.', request.update_id)
            return
        if previous is not None:
            text = (f'Yêu cầu này đã có job {previous.job_id}; không tạo job trùng.'
                if previous.chat_id == request.chat_id else 'Yêu cầu Telegram không hợp lệ.')
            worker.reply(request.chat_id, text, request.update_id)
            return
        try:
            private_keys = self.store.get_user_mail_keys(request.chat_id,require_complete=True)
        except (ValueError,PermissionError) as exc:
            worker.reply(request.chat_id,'Không tạo được job.\nNguyên nhân: '+str(exc),request.update_id)
            return
        except Exception:
            worker.reply(request.chat_id,'Không đọc được API mua mail riêng. Chưa tạo job.',request.update_id)
            return
        registered = []

        def before_start(job_id, code, points, target_count, **schedule_options):
            created = self.store.record_job(job_id, request.chat_id, code, points, target_count,
                bot_fingerprint=fingerprint, update_id=request.update_id,
                sale_rate_units=self._sale_rate_snapshot(), **schedule_options)
            if created:
                registered.append(job_id)
            return created

        creation_error=''
        try:
            job_id = self.mochi_page._on_start_clicked(code=request.code, points=request.points,
                customer_name=request.customer or user.name, before_start=before_start,
                creator_name=user.name, creator_chat_id=request.chat_id, scheduled_at=request.scheduled_at,
                mail_api_keys=private_keys, combo=request.combo)
        except Exception as exc:
            creation_error=str(exc)
            job_id = None
        if not job_id:
            if registered:
                self.store.finish_job(registered[0], 'failed', 0)
                self._render_users()
            if registered:
                record=self.mochi_page._session_jobs.get(registered[0])
                creation_error=getattr(record,'failure_message','') or creation_error
            creation_error=creation_error or self.mochi_page.validation_label.text()
            worker.reply(request.chat_id,'Không tạo được job.\nNguyên nhân: '+job_failure_reason(message=creation_error),
                request.update_id)
            return
        self._render_users()
        self._job_chats[job_id] = (worker.fingerprint, request.chat_id)
        combo_text = 'Combo tự tiếp tục sau 24 giờ 1 phút khi đạt giới hạn ngày, đến khi đủ mục tiêu.\n' if request.combo else ''
        worker.reply(request.chat_id,
            f'Đã tạo job {job_id}\nMã mời: {request.code}\nMục tiêu: {request.points:,} kẹo / '
            f'{required_mochi_invitation_accounts(request.points)} tài khoản.\n' + combo_text +
            f'Trạng thái: {self.mochi_page.job_dispatch_text(job_id)}', request.update_id)

    @staticmethod
    def _job_status_text(job):
        state = job['status']
        if state == 'running':
            if job.get('dispatch_state') == 'scheduled':
                return 'Hẹn giờ: ' + schedule_text(job['scheduled_at'])
            return 'Đang chờ' if job.get('dispatch_state') == 'queued' else 'Đang xử lý'
        return {'completed': 'Đã hoàn thành', 'cancelled': 'Đã hủy', 'failed': 'Thất bại'}.get(state, 'Thất bại')

    def _on_job_dispatch_changed(self, job_id, state):
        try:
            changed = self.store.update_dispatch(job_id, state)
            origin = self._job_chats.get(job_id)
            worker = self.worker
            if not changed or not origin or worker is None or worker.fingerprint != origin[0]:
                return
            job = self.store.get_job(job_id)
            if job is None or job.chat_id != origin[1] or job.finalized_at is not None:
                return
            order = self.store.get_customer_order_for_job(job_id)
            text = f'Job {job_id}: {self.mochi_page.job_dispatch_text(job_id)}'
            if order and order['chat_id'] == origin[1]:
                worker.reply(origin[1], f'Đơn {order["order_id"]}\n' + text, public=True)
            else:
                user = self.store.get_user(origin[1])
                if user and user.active and worker.access.allowed(origin[1]):
                    worker.reply(origin[1], text)
        except Exception:
            self.lbl_status.setText('Không cập nhật được trạng thái hàng đợi Telegram. Kiểm tra dữ liệu trong tool.')

    def _on_combo_waiting(self, job_id, when):
        """A saved next round keeps the same order, hold and notification origin."""
        try:
            self._refresh_customer_finance()
            origin = self._job_chats.get(job_id)
            worker = self.worker
            if not origin or worker is None or worker.fingerprint != origin[0]:
                return
            job = self.store.get_job(job_id)
            if job is None or job.chat_id != origin[1] or job.finalized_at is not None:
                return
            text = (f'Combo {job_id}: đang chờ lượt tiếp theo.\n'
                f'Đã đạt: {job.success_count * 100:,}/{job.target_count * 100:,} bean.\n'
                f'Còn thiếu: {max(0, job.target_count - job.success_count) * 100:,} bean.\n'
                f'Tự tiếp tục: {schedule_text(when)}.')
            order = self.store.get_customer_order_for_job(job_id)
            if order and order['chat_id'] == origin[1]:
                worker.reply(origin[1], f'Đơn {order["order_id"]}\n' + text, public=True)
            else:
                user = self.store.get_user(origin[1])
                if user and user.active and worker.access.allowed(origin[1]):
                    worker.reply(origin[1], text)
        except Exception:
            self.lbl_status.setText('Chưa gửi được thông báo lịch combo. Lịch đã lưu vẫn được giữ trong tool.')

    def _result_reply(self, job_id, text, *, failure_reason=''):
        origin = self._job_chats.pop(job_id, None)
        try:
            order = self.store.get_customer_order_for_job(job_id)
        except Exception:
            self.lbl_status.setText('Không đọc được sổ đơn hàng để gửi kết quả. Kiểm tra dữ liệu và số dư trong tool.')
            return
        if (origin and order and order['chat_id']==origin[1] and self.worker is not None
                and self.worker.fingerprint==origin[0]):
            text = (f'Đơn {order["order_id"]}\n'+text+
                ('\nĐang chờ chốt tiền; quản trị viên cần kiểm tra trong tool.' if order['charged_units'] is None
                    else f'\nĐã thu: {vnd(order["charged_units"])}')+'\nSố dư khả dụng: '
                +vnd(self.store.get_customer(origin[1])['balance_units']))
            self.worker.reply(origin[1],text,public=True)
            self.customer_page.refresh()
            return
        try:
            user = self.store.get_user(origin[1]) if origin else None
        except Exception:
            self.lbl_status.setText('Không kiểm tra được quyền nhận kết quả. Chưa gửi thông báo Telegram.')
            return
        if (origin and self.worker is not None and self.worker.fingerprint == origin[0]
                and user is not None and user.active and self.worker.access.allowed(origin[1])):
            if failure_reason and user.role in ('admin','partner'):
                text+='\nNguyên nhân: '+failure_reason
            self.worker.reply(origin[1], text)

    @staticmethod
    def _user_stats_text(user):
        return (f'{user.name} — {user.chat_id} ({user.role})\n'
            f'Quyền: {"Được phép" if user.active else "Đã thu hồi"}\n'
            f'Job hoàn thành: {user.completed_jobs:,}\nJob thất bại: {user.failed_jobs:,}\n'
            f'Bean đã bán: {user.sold_beans:,}\n'
            f'Doanh thu: {format_vnd(user.revenue_vnd)} VND\n'
            f'Chi phí mail: {format_vnd(user.cost_vnd)} VND\n'
            f'Lợi nhuận: {"Chờ đối soát" if user.pending_costs or user.unpriced_jobs else format_vnd(user.profit_vnd) + " VND"}'
            + (f'\nCần đối soát: {user.pending_costs} giao dịch mail, {user.unpriced_jobs} job chưa có giá.'
                if user.pending_costs or user.unpriced_jobs else ''))

    def _on_control_requested(self, envelope):
        worker = self.worker
        if not isinstance(envelope, tuple) or len(envelope) != 2:
            return
        fingerprint, request = envelope
        if worker is None or worker.fingerprint != fingerprint:
            return
        try:
            actor = self.store.get_user(request.chat_id)
        except Exception:
            worker.reply(request.chat_id, 'Không kiểm tra được quyền truy cập. Nhờ quản trị viên kiểm tra dữ liệu trong tool.', request.update_id)
            return
        if (not self._accepting_requests or actor is None or actor.is_local or not actor.active
                or not worker.access.allowed(request.chat_id)):
            worker.complete_request(request.update_id)
            return
        if request.action in ('mail_keys_status','mail_keys_save'):
            try:
                if request.action=='mail_keys_save':
                    self.store.set_user_mail_keys(request.chat_id,request.gmailvip_api_key,request.one_trieu_api_key)
                    text='Đã lưu đủ 2 API mua mail riêng của bạn. Nguồn mua, product ID và số luồng do tool quản lý.'
                    if self._selected_ids()==(request.chat_id,):
                        self._load_selected_user()
                else:
                    text=self._mail_keys_status(self.store.get_user_mail_keys(request.chat_id))
            except (ValueError,PermissionError) as exc:
                text=str(exc)
            except Exception:
                text='Không đọc/ghi được API riêng. Dữ liệu cũ được giữ; nhờ quản trị viên kiểm tra trong tool.'
            worker.reply(request.chat_id,text,request.update_id)
            return
        if request.action in ('schedules','cancel_schedule'):
            try:
                text = (self._schedule_list_text(request.chat_id,fingerprint) if request.action=='schedules'
                    else self._cancel_schedule_text(request.chat_id,fingerprint,request.job_id))
            except Exception:
                text = 'Không xử lý được lịch job; nhờ quản trị viên kiểm tra dữ liệu trong tool.'
            worker.reply(request.chat_id,text,request.update_id)
            return
        if request.action != 'stats' or request.target_chat_id != request.chat_id:
            worker.reply(request.chat_id, 'Bot chỉ cho xem thống kê của chính bạn. Quản lý người dùng thực hiện trong tool.', request.update_id)
            return
        try:
            text = self._user_stats_text(actor)
        except Exception:
            text = 'Không đọc được thống kê. Nhờ quản trị viên kiểm tra dữ liệu trong tool.'
        worker.reply(request.chat_id, text, request.update_id)

    def _job_mail_keys(self, job_id):
        job=self.store.get_job(job_id)
        if job is None:
            raise ValueError('Không tìm thấy chủ sở hữu job để lấy API mua mail.')
        if job.chat_id==LOCAL_USER_ID or self.store.get_customer_order_for_job(job_id) is not None:
            return None
        return self.store.get_user_mail_keys(job.chat_id,require_complete=True)

    def _schedule_list_text(self, chat_id, fingerprint):
        jobs = [job for job in self.store.list_jobs(chat_id) if job['bot_fingerprint']==fingerprint
            and job['scheduled_at'] and job['finalized_at'] is None]
        return '\n\n'.join(f'{job["job_id"]} · {job["code"]}\n{self._job_status_text(job)}'
            for job in jobs[:20]) or 'Bạn chưa có job hẹn giờ chưa kết thúc.'

    def _cancel_schedule_text(self, chat_id, fingerprint, job_id):
        job = self.store.get_job(job_id)
        if job is None or job.chat_id!=chat_id or job.bot_fingerprint!=fingerprint:
            return 'Không tìm thấy job của bạn với mã này.'
        if job_id not in self.mochi_page._scheduled_jobs and job_id not in self.mochi_page._pending_jobs:
            return 'Job đã chạy hoặc kết thúc; không thể hủy lịch qua bot.'
        self.mochi_page._stop_job(job_id)
        if self.store.get_job(job_id).status != 'cancelled':
            return 'Chưa hủy được job; job có thể đã được cấp xử lý. Kiểm tra trạng thái trong tool.'
        return f'Đã hủy job chưa chạy {job_id}. Phần tiền tạm giữ chưa sử dụng được hoàn theo sổ đơn hàng.'

    def _on_job_finished(self, summary):
        try:
            self.store.finish_job(summary.job_id, summary.status, summary.success_count,
                failure_count=summary.failure_count, attempted_count=summary.attempted_count)
            # A scheduled cancellation may already have settled atomically.
            # Refresh wallets even with the bot offline or duplicate finalization.
            self._refresh_customer_finance()
        except Exception:
            self.lbl_status.setText('Không lưu được kết quả job vào thống kê Telegram. Kiểm tra file dữ liệu.')
        status_text = self._job_status_text({'status': summary.status})
        self._result_reply(summary.job_id, f'Job {summary.job_id}: {status_text}\n'
            f'Thành công: {summary.success_count}/{summary.target_count}\n'
            f'Lỗi: {summary.failure_count}\nKẹo đạt: {summary.success_count * 100:,}',
            failure_reason=job_failure_reason(summary) if summary.status not in ('completed','cancelled') else '')

    def _on_job_failed(self, message, job_id):
        try:
            if self.store.finish_job(job_id, 'failed', 0):
                self._render_users()
        except Exception:
            self.lbl_status.setText('Không lưu được lỗi job vào thống kê Telegram. Kiểm tra file dữ liệu.')
        self._result_reply(job_id, f'Job {job_id} gặp lỗi thực thi. Nhờ quản trị viên kiểm tra log trong tool.',
            failure_reason=job_failure_reason(message=message))

    def _on_job_progress(self, event):
        try:
            if self.store.update_progress(event.job_id, event.success_count,
                    attempted_count=event.attempted_count, failure_count=event.failure_count):
                self._render_users()
        except Exception:
            self.lbl_status.setText('Không lưu được tiến độ job vào thống kê Telegram. Kiểm tra file dữ liệu.')
