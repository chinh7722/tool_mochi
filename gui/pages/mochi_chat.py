"""Mochi Chat GUI Page and Worker Thread.

Provides complete PySide6/PyQt6 graphical interface for running Mochi Chat invite jobs,
configuring mail providers (GmailVip.vn, 1TrieuMail.com), viewing live progress,
inspecting real-time color logs, and managing accounts.
Runs 100% locally with zero backend dependencies.
"""

import asyncio
import html
import json
from collections import defaultdict, deque
from datetime import datetime, timedelta
import logging
import math
import os
from pathlib import Path
import re
import sys
import threading
from typing import Any, Dict, List, Optional, Tuple
import uuid

# Qt Compatibility (PySide6 preferred, PyQt6 fallback)
try:
    from PySide6.QtCore import QDateTime, QEvent, QObject, QSettings, QSize, Qt, QThread, QTimer, Signal
    from PySide6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPixmap
    from PySide6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QCheckBox,
        QComboBox,
        QDateTimeEdit,
        QFileDialog,
        QFrame,
        QGraphicsOpacityEffect,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QSplitter,
        QStackedWidget,
        QTabBar,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    from PyQt6.QtCore import QDateTime, QEvent, QObject, QSettings, QSize, Qt, QThread, QTimer, pyqtSignal as Signal
    from PyQt6.QtGui import QColor, QCursor, QFont, QIcon, QPainter, QPixmap
    from PyQt6.QtWidgets import (
        QAbstractItemView,
        QApplication,
        QCheckBox,
        QComboBox,
        QDateTimeEdit,
        QFileDialog,
        QFrame,
        QGraphicsOpacityEffect,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMessageBox,
        QPushButton,
        QRadioButton,
        QScrollArea,
        QSizePolicy,
        QSpinBox,
        QSplitter,
        QStackedWidget,
        QTabBar,
        QTableWidget,
        QTableWidgetItem,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

from core.browser import browser_manager, resolve_browser_headless
from core.job_schedule import VIETNAM_TIME, schedule_time, schedule_text
from core.mail.user_keys import validated_user_mail_keys
from core.config.constants import (
    DEFAULT_1TRIEUMAIL_PRODUCT_IDS_STR,
    DEFAULT_GMAILVIP_PRODUCT_IDS_STR,
    MOCHI_INVITATION_REWARD_POINTS,
    required_mochi_invitation_accounts,
)
from core.config.runtime_config import MochiChatConfig
from core.job_events import JobLogEvent, JobLogLevel, JobProgressEvent
from core.job_history import JOB_TIMEZONE, JobHistoryEntry, JobHistoryStore
from core.mail.batch_inventory import load_batch
from core.mail.providers.gmailvip_vn import GmailVipVn
from core.mail.providers.one_trieu_mail import OneTrieuMail
from core.mail.providers.priority import MULTI_SHOP, GMAILVIP, ONE_TRIEU, SHOP_NAMES, parse_mail_priority
from core.platforms.mochichat.runners.invite_referral import (
    MochiAccountItem,
    MochiInviteJobSummary,
    MochiInviteRunner,
)
from core.proxy.broker import ProxyBroker, proxy_maintenance
from core.proxy.client import ProxyPoolClient
from core.proxy.models import ProxyModel
from core.proxy.rotators import (ProxyVnProvider, make_rotating_provider,
    provider_shared_identity, rotating_provider_id, rotation_key_reference)
from core.auth.google import resolve_google_login_via_proxy
from gui.background import start_background
from gui.branding import WORKSPACE_STYLE, icon
from gui.pages.account_lookup import AccountLookupPage
from gui.pages.job_history import JobHistoryPage

logger = logging.getLogger("gui.pages.mochi_chat")


class _JobTypeCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, title: str, subtitle: str, icon_str: str = "⚡", accent: str = "#3b82f6", badge: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.key = key
        self._selected = False
        self.setObjectName("jobTypeCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(84)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)

        icon_label = QLabel(icon_str, self)
        asset = "user_add_blue.svg" if key == "CUSTOMER_INVITE" else "account_stock_green.svg"
        icon_label.setPixmap(icon("job_type", asset).pixmap(32, 32))
        icon_label.setFixedSize(36, 36)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_label)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)

        title_row = QHBoxLayout()
        title_label = QLabel(title, self)
        title_label.setStyleSheet("font-weight: 700; font-size: 14px; color: #f8fafc;")
        title_row.addWidget(title_label)

        if badge:
            badge_label = QLabel(badge, self)
            badge_label.setStyleSheet(
                "background-color: rgba(245, 158, 11, 0.22); color: #fbbf24; "
                "border: 1px solid rgba(245, 158, 11, 0.60); border-radius: 4px; "
                "padding: 1px 7px; font-size: 11px; font-weight: 700;"
            )
            title_row.addWidget(badge_label)

        title_row.addStretch()
        text_layout.addLayout(title_row)

        subtitle_label = QLabel(subtitle, self)
        subtitle_label.setStyleSheet("color: #94a3b8; font-size: 12px;")
        subtitle_label.setWordWrap(True)
        text_layout.addWidget(subtitle_label)

        layout.addLayout(text_layout)

        self.radio_dot = QLabel("○", self)
        self.radio_dot.setStyleSheet("color: #64748b; font-size: 18px;")
        self.radio_dot.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.radio_dot)

        self._update_style()

    def set_selected(self, selected: bool):
        self._selected = selected
        self.radio_dot.setText("●" if selected else "○")
        self.radio_dot.setStyleSheet("color: #60a5fa; font-size: 18px;" if selected else "color: #64748b; font-size: 18px;")
        self._update_style()

    def _update_style(self):
        if self._selected:
            self.setStyleSheet(
                "QFrame#jobTypeCard { background-color: #242e45; border: 1px solid #3b82f6; border-radius: 8px; }"
            )
        else:
            self.setStyleSheet(
                "QFrame#jobTypeCard { background-color: #1d1d20; border: 1px solid #3a3a42; border-radius: 8px; }"
                "QFrame#jobTypeCard:hover { border: 1px solid #4a4b55; background-color: #232326; }"
            )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(event)


class _MochiJobSessionRecord:
    def __init__(
        self,
        job_id: str,
        invite_code: str,
        target_count: int,
        job_name: str = "",
        customer_name: str = "",
        status: str = "Đang chạy",
        creator_name: str = "Local",
        creator_chat_id: Optional[int] = None,
    ):
        self.job_id = job_id
        self.invite_code = invite_code
        self.target_count = target_count
        self.job_name = job_name
        self.customer_name = customer_name
        self.creator_name = creator_name or ('Telegram' if creator_chat_id is not None else 'Local')
        self.creator_chat_id = creator_chat_id
        self.attempted_count = 0
        self.success_count = 0
        self.failure_count = 0
        self.status = status
        self.created_at = datetime.now(JOB_TIMEZONE)

    @property
    def achieved_points(self) -> int:
        return self.success_count * MOCHI_INVITATION_REWARD_POINTS

    @property
    def target_points(self) -> int:
        return self.target_count * MOCHI_INVITATION_REWARD_POINTS

    @property
    def progress(self) -> str:
        return f"{self.success_count} / {self.target_count}"

    @property
    def is_terminal(self) -> bool:
        return self.status in ("Hoàn thành", "Thất bại", "Đã hủy", "Mã không hợp lệ", "Mã mời lỗi", "Đạt giới hạn 24h")

    @property
    def is_running_like(self) -> bool:
        return self.status in ("Đang chạy", "Đang khởi tạo", "Đang chờ", "Hẹn giờ")


class MochiWorkerThread(QThread):
    log_signal = Signal(object, str)       # (JobLogEvent, job_id)
    progress_signal = Signal(object)      # JobProgressEvent
    finished_signal = Signal(object)      # MochiInviteJobSummary
    error_signal = Signal(str, str)       # (err_msg, job_id)

    def __init__(
        self,
        runner: MochiInviteRunner,
        job_id: str,
        invite_code: str,
        target_count: int,
        job_name: str = "",
        customer_name: str = "",
        unit_price: int = 0,
        continued_from_job_id: Optional[str] = None,
        initial_success_count: int = 0,
        parent: Optional[QObject] = None,
        accounts: Optional[Tuple[MochiAccountItem, ...]] = None,
        source_mail_batch: Optional[str] = None,
    ):
        super().__init__(parent)
        self.runner = runner
        self.job_id = job_id
        self.invite_code = invite_code
        self.target_count = target_count
        self.job_name = job_name
        self.customer_name = customer_name
        self.unit_price = unit_price
        self.continued_from_job_id = continued_from_job_id
        self.initial_success_count = initial_success_count
        self.accounts = accounts
        self.source_mail_batch = source_mail_batch

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._main_task: Optional[asyncio.Task] = None
        self._cancel_event = threading.Event()
        self.runner.cancel_event = self._cancel_event
        self.runner.log_sink = lambda evt: self.log_signal.emit(evt, self.job_id)
        self.runner.progress_sink = lambda evt: self.progress_signal.emit(evt)

    def request_cancel(self):
        if self._cancel_event.is_set():
            return
        self._cancel_event.set()
        if self._loop and not self._loop.is_closed() and self._main_task:
            try:
                self._loop.call_soon_threadsafe(self._main_task.cancel)
            except Exception:
                pass

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _execute():
            self._main_task = asyncio.create_task(
                self.runner.run(
                    job_id=self.job_id,
                    invite_code=self.invite_code,
                    target_count=self.target_count,
                    job_name=self.job_name,
                    customer_name=self.customer_name,
                    unit_price=self.unit_price,
                    continued_from_job_id=self.continued_from_job_id,
                    initial_success_count=self.initial_success_count,
                    accounts=self.accounts,
                    source_mail_batch=self.source_mail_batch,
                )
            )
            try:
                summary = await self._main_task
                self.finished_signal.emit(summary)
            except asyncio.CancelledError:
                summary = MochiInviteJobSummary(
                    job_id=self.job_id,
                    invite_code=self.invite_code,
                    target_count=self.target_count,
                    attempted_count=0,
                    success_count=self.initial_success_count,
                    failure_count=0,
                    already_redeemed_count=0,
                    status="cancelled",
                    results=(),
                    error_message="Người dùng đã hủy nhiệm vụ.",
                )
                self.finished_signal.emit(summary)
            except Exception as e:
                self.error_signal.emit(str(e), self.job_id)
            finally:
                try:
                    await browser_manager.shutdown_for_loop(self._loop)
                except Exception:
                    pass

        try:
            self._loop.run_until_complete(_execute())
        except Exception as e:
            self.error_signal.emit(str(e), self.job_id)
        finally:
            try:
                pending = asyncio.all_tasks(self._loop)
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                self._loop.close()
            except Exception:
                pass


class MochiChatPage(QWidget):
    """Trang giao diện Mochi Chat hoàn chỉnh, độc lập và chạy 100% Local."""

    job_finished = Signal(object)
    job_failed = Signal(str, str)
    job_progress = Signal(object)
    job_dispatch_changed = Signal(str, str)
    mail_purchase_recorded = Signal(object)

    def __init__(self, settings: Optional[QSettings] = None, parent: Optional[QWidget] = None, proxy_page: Optional[Any] = None):
        super().__init__(parent)
        self.settings = settings or QSettings("Hermit Coder", "TTM Tool")
        self.proxy_page = proxy_page
        self.mail_proxy_page=None
        self.setObjectName("mochiChatPage")
        self._workers: Dict[str, MochiWorkerThread] = {}
        self._pending_jobs = deque()
        self._scheduled_jobs = {}
        self.scheduled_job_claim = None
        self.scheduled_job_cancel = None
        self.scheduled_job_defer = None
        self.job_mail_keys_resolver = None
        self._active_job_ids = set()
        self._dispatching_jobs = False
        self._closing_jobs = False
        self._session_jobs: Dict[str, _MochiJobSessionRecord] = {}
        self._job_logs: Dict[str, List[str]] = defaultdict(list)
        self._all_logs: List[str] = []
        self._selected_job_id: Optional[str] = None
        self._job_type: str = "CUSTOMER_INVITE"
        self.mail_purchase_recorder = None
        self.local_job_registrar = None
        self.job_order_lookup = None
        base=Path(__file__).resolve().parents[2]
        if base.name=='src':
            base=base.parent
        history_directory=(Path(self.settings.fileName()).parent
            if self.settings.format()==QSettings.Format.IniFormat else base/'data')
        self.job_history_store=JobHistoryStore(history_directory/'job_history.csv')

        # Load persisted settings
        self.config = MochiChatConfig.from_settings(self.settings)
        self._manual_concurrency = min(50, max(1, self.config.account_concurrency))
        self._proxy_concurrency_active = False

        self._init_ui()
        self._update_proxy_tab_status()
        self._schedule_timer = QTimer(self)
        self._schedule_timer.setInterval(1000)
        self._schedule_timer.timeout.connect(self._check_scheduled_jobs)
        self._schedule_timer.start()

    def set_proxy_page(self, proxy_page: Any):
        self.proxy_page = proxy_page
        self._update_proxy_tab_status()

    def _update_proxy_tab_status(self):
        active_count = self._configured_proxy_count()

        if hasattr(self, "lbl_tab_proxy_status"):
            if active_count > 0:
                self.lbl_tab_proxy_status.setText(f"({active_count} proxy sẵn sàng)")
                self.lbl_tab_proxy_status.setStyleSheet("color: #34d399; font-weight: 600; font-size: 11px;")
            else:
                self.lbl_tab_proxy_status.setText("(0 proxy - Cần import)")
                self.lbl_tab_proxy_status.setStyleSheet("color: #f87171; font-weight: 600; font-size: 11px;")
        self._refresh_concurrency_display()

    def _active_proxy_entries(self):
        if self.proxy_page and hasattr(self.proxy_page, "get_active_proxies"):
            return list(self.proxy_page.get_active_proxies())
        try:
            raw_str = str(self.settings.value("proxy/managed_list", "") or "")
            items = json.loads(raw_str) if raw_str else []
            return [(it['endpoint'], rotation_key_reference(it.get('key_xoay', ''),
                    it.get('provider_id', 'proxyvn'))) for it in items
                if isinstance(it, dict) and it.get('endpoint') and not it.get('is_failed')]
        except (TypeError, ValueError):
            return []

    def _configured_proxy_count(self):
        identities = set()
        for endpoint, key in self._active_proxy_entries():
            try:
                model = ProxyModel.from_string(endpoint)
                identity = ('rotation', provider_shared_identity(make_rotating_provider(key))) if key else ('static', model.to_url())
                identities.add(identity)
            except (TypeError, ValueError):
                continue
        return len(identities)

    def _on_manual_concurrency_changed(self, value):
        if not self._proxy_concurrency_active:
            self._manual_concurrency = value

    def _on_proxy_slots_changed(self, value):
        self.config.proxy_account_concurrency = value
        self.settings.setValue('runtime/mochi_chat/proxy_account_concurrency', value)
        self._refresh_concurrency_display()

    def _refresh_concurrency_display(self, *_):
        if not hasattr(self, 'spin_proxy_account_concurrency'):
            return
        automatic = self.chk_use_proxy.isChecked() and any((
            self.chk_google_via_proxy.isChecked(), self.chk_app_login_via_proxy.isChecked(),
            self.chk_redeem_via_proxy.isChecked()))
        self._proxy_concurrency_active = automatic
        self.spin_concurrency.blockSignals(True)
        if automatic:
            count = (self._configured_proxy_count() if self.radio_proxy_tab.isChecked()
                else int(bool(self.input_proxy_vn_key.text().strip())))
            slots = self.spin_proxy_account_concurrency.value()
            total = count * slots
            self.spin_concurrency.setRange(0, max(50, total))
            self.spin_concurrency.setValue(total)
            self.lbl_concurrency_summary.setText(f'Tự động: {count} proxy × {slots} luồng/proxy = {total} luồng tối đa.')
        else:
            self.spin_concurrency.setRange(1, 50)
            self.spin_concurrency.setValue(self._manual_concurrency)
            self.lbl_concurrency_summary.setText('Số luồng dùng cho các tài khoản chạy qua mạng trực tiếp.')
        self.spin_concurrency.setEnabled(not automatic)
        self.spin_concurrency.blockSignals(False)

    def _switch_to_proxy_tab(self):
        win = self.window()
        if win and hasattr(win, "switch_to_proxy_tab"):
            win.switch_to_proxy_tab()

    def _account_lookup_route(self,owner_id):
        use_proxy=self.chk_use_proxy.isChecked()
        google=use_proxy and self.chk_google_via_proxy.isChecked()
        app=use_proxy and self.chk_app_login_via_proxy.isChecked()
        client=None
        if google or app:
            proxies=None
            provider=None
            if self.radio_proxy_tab.isChecked():
                entries=self._active_proxy_entries()
                if not entries:raise ValueError('Proxy Pool chưa có proxy hoạt động.')
                proxies=[ProxyModel.from_string(endpoint,rotation_url=key or None) for endpoint,key in entries]
            else:
                key=self.input_proxy_vn_key.text().strip()
                if not key:raise ValueError('Chưa nhập key proxy.')
                provider=make_rotating_provider(key,nhamang=self.combo_proxy_isp.currentData())
            broker=ProxyBroker(static_proxies=proxies,rotating_provider=provider,allow_direct_without_proxies=False,
                account_concurrency=self.spin_proxy_account_concurrency.value())
            client=ProxyPoolClient(job_id=owner_id,platform_id='mochichat',broker=broker)
        return dict(proxy_client=client,google_via_proxy=google,app_via_proxy=app,headless=self.chk_headless.isChecked())

    def _init_ui(self):
        self.setStyleSheet(WORKSPACE_STYLE)
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(28, 24, 28, 20)
        root_layout.setSpacing(14)
        self.workspace_tabs = QTabBar(self)
        self.workspace_tabs.setDrawBase(False)
        self.workspace_tabs.setExpanding(False)
        self.workspace_tabs.addTab(icon('nav_mochi', 'nav_jobs.svg'), "Nhiệm vụ")
        self.workspace_tabs.addTab(icon('nav_stock', 'nav_stock.svg'), "Kho tài khoản")
        self.workspace_tabs.addTab(icon('nav_mochi','nav_jobs.svg'), 'Lấy mã mời')
        root_layout.addWidget(self.workspace_tabs, 0, Qt.AlignmentFlag.AlignLeft)
        self.validation_label = QLabel(self)
        self.validation_label.setVisible(False)
        self.validation_label.setWordWrap(True)
        self.validation_label.setObjectName("validationError")
        root_layout.addWidget(self.validation_label)
        self.workspace_stack = QStackedWidget(self)
        jobs_page = QWidget(self)
        jobs_layout = QVBoxLayout(jobs_page)
        jobs_layout.setContentsMargins(0, 8, 0, 0)
        jobs_layout.setSpacing(12)
        heading = QLabel("Jobs", jobs_page)
        heading.setStyleSheet("font-size: 25px; font-weight: 700; color: #f8fafc;")
        jobs_layout.addWidget(heading)
        subtitle = QLabel("Tạo job nạp bean cho khách hàng và theo dõi tiến độ thực hiện.", jobs_page)
        subtitle.setStyleSheet("color: #9aa8c4; margin-bottom: 4px;")
        jobs_layout.addWidget(subtitle)
        self.job_tabs = QTabBar(self)
        self.job_tabs.setDrawBase(False)
        self.job_tabs.setExpanding(True)
        self.job_tabs.addTab(icon('setup', 'user_add_blue.svg'), "Thiết lập job")
        self.job_tabs.addTab(icon('running', 'pending_tasks_blue.svg'), "Job đang chạy")
        self.job_tabs.addTab(icon('logs', 'log_document_blue.svg'), "Log job")
        self.job_tabs.addTab(icon('history', 'log_document_blue.svg'), "Lịch sử job")
        jobs_layout.addWidget(self.job_tabs)
        self.job_stack = QStackedWidget(self)
        form_card = self._build_form_card()
        setup_scroll = QScrollArea(self)
        setup_scroll.setWidgetResizable(True)
        setup_scroll.setWidget(form_card)
        self.job_stack.addWidget(setup_scroll)
        self.job_stack.addWidget(self._build_running_jobs_card())
        self.job_stack.addWidget(self._build_logs_card())
        self.history_page=JobHistoryPage(self.job_history_store,self)
        self.job_stack.addWidget(self.history_page)
        self.job_tabs.currentChanged.connect(self.job_stack.setCurrentIndex)
        self.job_tabs.currentChanged.connect(lambda index:self.history_page.refresh() if index==3 else None)
        jobs_layout.addWidget(self.job_stack, 1)
        self.workspace_stack.addWidget(jobs_page)
        self.workspace_stack.addWidget(self.inventory_page)
        self.account_lookup_page=AccountLookupPage(self.job_history_store.path.parent/'mochi_account_profiles.sqlite3',
            self._account_lookup_route,self)
        self.workspace_stack.addWidget(self.account_lookup_page)
        self.workspace_tabs.currentChanged.connect(self.workspace_stack.setCurrentIndex)
        root_layout.addWidget(self.workspace_stack, 1)
        # Settings and bot share these controls, with no duplicate configuration.
        for container in (self, self.settings_page):
            for widget_type in (QLineEdit, QComboBox, QSpinBox, QPushButton):
                for widget in container.findChildren(widget_type):
                    widget.setStyleSheet("")
            for widget in container.findChildren(QPushButton):
                widget.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.setProperty("primary", True)
        self.btn_settings_save.setProperty("primary", True)
        self._restore_job_draft()

    def _build_form_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("mochiFormCard")
        card.setStyleSheet("QFrame#mochiFormCard { background: #232326; border: 1px solid #323239; border-radius: 10px; }")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(12)

        config_grid = QGridLayout()
        config_grid.setVerticalSpacing(16)
        config_grid.setHorizontalSpacing(16)
        config_grid.setColumnStretch(1, 1)
        self.inventory_page = QWidget(self)
        inventory_layout = QVBoxLayout(self.inventory_page)
        inventory_layout.setContentsMargins(0, 12, 0, 0)
        inventory_layout.setSpacing(14)
        inventory_title = QLabel("Kho tài khoản", self.inventory_page)
        inventory_title.setStyleSheet("font-size: 25px; font-weight: 700;")
        inventory_layout.addWidget(inventory_title)
        inventory_help = QLabel("Chọn lô mail đã mua cho job tiếp theo. Mail mới được lưu tự động vào data/mail_batches.", self.inventory_page)
        inventory_help.setWordWrap(True)
        inventory_help.setStyleSheet("color: #9aa8c4;")
        inventory_layout.addWidget(inventory_help)

        # Job Type Cards
        type_title = QLabel("Thiết lập job", self)
        type_title.setStyleSheet("font-size: 14px; font-weight: 700; color: #f8fafc;")
        card_layout.addWidget(type_title)

        self.card_invite = _JobTypeCard(
            key="CUSTOMER_INVITE",
            title="Nhập mã mời để nạp bean",
            subtitle="Dùng mã mời khách để nạp bean từ tài khoản mới.",
            icon_str="🎁",
            badge="",
            parent=self,
        )
        self.card_invite.set_selected(True)
        self.card_invite.clicked.connect(self._select_job_type)
        type_row = QHBoxLayout()
        type_row.setSpacing(12)
        type_row.addWidget(self.card_invite, 1)
        self.card_stock = _JobTypeCard("STOCK_ACCOUNT", "Tạo account có sẵn bean",
            "Tạo tài khoản và tự nhận thưởng.", icon_str="▤", badge="Chưa hỗ trợ", parent=self)
        self.card_stock.setEnabled(False)
        type_row.addWidget(self.card_stock, 1)
        card_layout.addLayout(type_row)

        # Form fields Grid
        grid = QGridLayout()
        grid.setVerticalSpacing(10)
        grid.setHorizontalSpacing(10)

        # 1. Invite Code
        lbl_code = QLabel("Mã mời Mochi (*):", self)
        lbl_code.setStyleSheet("font-weight: 600; color: #e2e8f0;")
        self.input_code = QLineEdit(self)
        self.input_code.setPlaceholderText("VD: A75FNX hoặc dán tin nhắn có Mã: ...")
        self.input_code.setStyleSheet("background-color: #1d1d20; color: #f8fafc; border: 1px solid #4a4b55; border-radius: 6px; padding: 8px;")
        self.input_code.textChanged.connect(self._on_code_text_changed)
        grid.addWidget(lbl_code, 0, 1)
        grid.addWidget(self.input_code, 1, 1)

        # 2. Target points
        lbl_target = QLabel("Điểm mục tiêu (*):", self)
        lbl_target.setStyleSheet("font-weight: 600; color: #e2e8f0;")
        target_box = QHBoxLayout()
        self.input_target_points = QLineEdit(self)
        self.input_target_points.setText("1000")
        self.input_target_points.setPlaceholderText("Số điểm kẹo (bội số 100)")
        self.input_target_points.setStyleSheet("background-color: #1d1d20; color: #f8fafc; border: 1px solid #4a4b55; border-radius: 6px; padding: 8px;")
        self.input_target_points.textChanged.connect(self._on_target_points_changed)

        self.lbl_acc_count = QLabel("(= 10 accounts)", self)
        self.lbl_acc_count.setStyleSheet("color: #60a5fa; font-weight: 600;")
        target_box.addWidget(self.input_target_points)
        target_box.addWidget(self.lbl_acc_count)

        grid.addWidget(lbl_target, 2, 0)
        grid.addLayout(target_box, 3, 0)

        # 3. Customer name / note
        lbl_cust = QLabel("Tên khách / Ghi chú:", self)
        lbl_cust.setStyleSheet("font-weight: 600; color: #e2e8f0;")
        self.input_customer = QLineEdit(self)
        self.input_customer.setPlaceholderText("Tùy chọn (để nhận diện job)")
        self.input_customer.setStyleSheet("background-color: #1d1d20; color: #f8fafc; border: 1px solid #4a4b55; border-radius: 6px; padding: 8px;")
        grid.addWidget(lbl_cust, 0, 0)
        grid.addWidget(self.input_customer, 1, 0)
        lbl_total = QLabel("Tổng tiền (VND):", self)
        self.input_total_vnd = QLineEdit(self)
        self.input_total_vnd.setPlaceholderText("VD: 0")
        self.input_total_vnd.setToolTip("Thông tin ghi chú giá trị job. Thống kê Local dùng đơn giá chung VND / 100 bean đã lưu ở Telegram.")
        grid.addWidget(lbl_total, 2, 1)
        grid.addWidget(self.input_total_vnd, 3, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        schedule_row = QHBoxLayout()
        self.chk_schedule_job = QCheckBox('Hẹn giờ chạy', self)
        self.input_schedule_time = QDateTimeEdit(self)
        self.input_schedule_time.setCalendarPopup(True)
        self.input_schedule_time.setDisplayFormat('dd/MM/yyyy HH:mm')
        self.input_schedule_time.setDateTime(QDateTime.fromString(
            (datetime.now(VIETNAM_TIME) + timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M'), 'yyyy-MM-dd HH:mm'))
        self.input_schedule_time.setEnabled(False)
        self.input_schedule_time.setStyleSheet(self.input_code.styleSheet())
        self.chk_schedule_job.toggled.connect(self.input_schedule_time.setEnabled)
        schedule_row.addWidget(self.chk_schedule_job)
        schedule_row.addWidget(self.input_schedule_time)
        schedule_row.addStretch()
        grid.addLayout(schedule_row, 4, 0, 1, 2)
        schedule_hint = QLabel('Giờ Việt Nam · Lịch chạy một lần. Tool cần mở để thực hiện; lịch được giữ khi đóng/mở lại.', self)
        schedule_hint.setWordWrap(True)
        schedule_hint.setStyleSheet('color: #94a3b8; font-size: 11px;')
        grid.addWidget(schedule_hint, 5, 0, 1, 2)

        # 4. Mail Provider
        lbl_prov = QLabel("Nguồn mua Mail:", self)
        lbl_prov.setStyleSheet("font-weight: 600; color: #e2e8f0;")
        self.combo_provider = QComboBox(self)
        self.combo_provider.addItem("GmailVip.vn (Tự động mua)", "gmailvip_vn")
        self.combo_provider.addItem("1TrieuMail.com (Tự động mua)", "1trieumail_com")
        self.combo_provider.addItem("Cả hai shop (theo thứ tự ưu tiên)", MULTI_SHOP)
        self.combo_provider.setStyleSheet("background-color: #1d1d20; color: #f8fafc; border: 1px solid #4a4b55; border-radius: 6px; padding: 6px;")
        saved_index = self.combo_provider.findData(self.config.mail_provider)
        self.combo_provider.setCurrentIndex(max(0, saved_index))
        self.combo_provider.currentIndexChanged.connect(self._on_provider_changed)
        config_grid.addWidget(lbl_prov, 0, 0)
        config_grid.addWidget(self.combo_provider, 0, 1)

        # 5. Mail Provider API Key
        lbl_key = QLabel("API Key Shop Mail (*):", self)
        self.lbl_mail_key = lbl_key
        lbl_key.setStyleSheet("font-weight: 600; color: #e2e8f0;")
        key_box = QHBoxLayout()
        self.input_api_key = QLineEdit(self)
        self.input_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_api_key.setText(self.config.mail_provider_api_key)
        self.input_api_key.setPlaceholderText("Nhập API Key shop mua mail")
        self.input_api_key.setStyleSheet("background-color: #1d1d20; color: #f8fafc; border: 1px solid #4a4b55; border-radius: 6px; padding: 8px;")

        self.btn_check_balance = QPushButton("Kiểm tra", self)
        self.btn_check_balance.setStyleSheet(
            "background-color: #0284c7; color: white; border: none; border-radius: 6px; padding: 8px 12px; font-weight: 600;"
        )
        self.btn_check_balance.clicked.connect(self._check_mail_balance)
        key_box.addWidget(self.input_api_key)
        key_box.addWidget(self.btn_check_balance)

        self.mail_key_stack = QStackedWidget(self)
        single_keys = QWidget(self)
        single_keys.setLayout(key_box)
        key_box.setContentsMargins(0, 0, 0, 0)
        self.mail_key_stack.addWidget(single_keys)
        both_keys = QWidget(self)
        both_layout = QGridLayout(both_keys)
        both_layout.setContentsMargins(0, 0, 0, 0)
        both_layout.setVerticalSpacing(8)
        self.input_gmailvip_key, self.input_1trieumail_key = QLineEdit(self), QLineEdit(self)
        self.btn_check_gmailvip_balance = QPushButton("Kiểm tra", self)
        self.btn_check_1trieumail_balance = QPushButton("Kiểm tra", self)
        for row, shop, field, button in (
                (0, GMAILVIP, self.input_gmailvip_key, self.btn_check_gmailvip_balance),
                (1, ONE_TRIEU, self.input_1trieumail_key, self.btn_check_1trieumail_balance)):
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText(f"API key {SHOP_NAMES[shop]}")
            field.setText(self.config.mail_api_keys.get(shop, ''))
            button.clicked.connect(lambda _checked=False, s=shop, f=field, b=button: self._check_shop_balance(s, f, b))
            both_layout.addWidget(QLabel(SHOP_NAMES[shop], self), row, 0)
            both_layout.addWidget(field, row, 1)
            both_layout.addWidget(button, row, 2)
        self.mail_key_stack.addWidget(both_keys)
        config_grid.addWidget(lbl_key, 1, 0)
        config_grid.addWidget(self.mail_key_stack, 1, 1)

        # 6. Mail Product IDs
        lbl_pids = QLabel("Product IDs:", self)
        self.lbl_mail_pids = lbl_pids
        lbl_pids.setStyleSheet("font-weight: 600; color: #e2e8f0;")
        self.input_pids = QLineEdit(self)
        self.input_pids.setText(self.config.mail_product_ids)
        self.input_pids.setStyleSheet("background-color: #1d1d20; color: #f8fafc; border: 1px solid #4a4b55; border-radius: 6px; padding: 8px;")
        config_grid.addWidget(lbl_pids, 2, 0)
        config_grid.addWidget(self.input_pids, 2, 1)
        self._apply_mail_mode_ui()

        self.chk_use_mail_batch = QCheckBox("Dùng lô mail đã mua", self)
        self.chk_use_mail_batch.setStyleSheet("color: #60a5fa; font-weight: 600;")
        inventory_layout.addWidget(self.chk_use_mail_batch)

        batch_box = QHBoxLayout()
        self.input_mail_batch = QLineEdit(self)
        self.input_mail_batch.setPlaceholderText("Chọn file trong data/mail_batches — không mua thêm mail")
        self.input_mail_batch.setText(str(self.settings.value("runtime/mochi_chat/mail_batch_path", "") or ""))
        self.input_mail_batch.setStyleSheet("background-color: #1d1d20; color: #f8fafc; border: 1px solid #4a4b55; border-radius: 6px; padding: 8px;")
        self.btn_choose_mail_batch = QPushButton("Chọn file…", self)
        self.btn_choose_mail_batch.setStyleSheet("background-color: #0284c7; color: white; border: none; border-radius: 6px; padding: 8px 12px;")
        self.btn_choose_mail_batch.clicked.connect(self._choose_mail_batch)
        batch_box.addWidget(self.input_mail_batch)
        batch_box.addWidget(self.btn_choose_mail_batch)
        inventory_layout.addLayout(batch_box)
        self.lbl_batch_summary = QLabel("Tool mua 1 mail thử mã trước; thành công mới mua phần còn thiếu.", self)
        self.lbl_batch_summary.setWordWrap(True)
        self.lbl_batch_summary.setStyleSheet("color: #9aa8c4;")
        inventory_layout.addWidget(self.lbl_batch_summary)
        inspect_batch = QPushButton("Kiểm tra số mail còn lại", self)
        inspect_batch.clicked.connect(self._inspect_mail_batch)
        inventory_layout.addWidget(inspect_batch, 0, Qt.AlignmentFlag.AlignLeft)
        back_to_jobs = QPushButton("Về thiết lập job", self)
        back_to_jobs.clicked.connect(lambda: self.workspace_tabs.setCurrentIndex(0))
        inventory_layout.addWidget(back_to_jobs, 0, Qt.AlignmentFlag.AlignLeft)
        inventory_layout.addStretch()
        self.chk_use_mail_batch.toggled.connect(self._update_job_source_summary)
        self.chk_use_mail_batch.toggled.connect(self._on_mail_batch_mode_changed)
        self._on_mail_batch_mode_changed(False)

        # 7. Concurrency & Headless
        lbl_conc = QLabel("Số luồng đồng thời:", self)
        lbl_conc.setStyleSheet("font-weight: 600; color: #e2e8f0;")
        opt_box = QHBoxLayout()
        self.spin_concurrency = QSpinBox(self)
        self.spin_concurrency.setRange(1, 50)
        self.spin_concurrency.setValue(self.config.account_concurrency or 5)
        self.spin_concurrency.valueChanged.connect(self._on_manual_concurrency_changed)
        self.spin_concurrency.setStyleSheet("background-color: #1d1d20; color: #f8fafc; border: 1px solid #4a4b55; border-radius: 6px; padding: 6px;")

        self.chk_headless = QCheckBox("Chạy ẩn (Headless)", self)
        self.chk_headless.setChecked(resolve_browser_headless(settings=self.settings))
        self.chk_headless.setStyleSheet("color: #cbd5e1; font-weight: 600;")

        opt_box.addWidget(self.spin_concurrency)
        opt_box.addWidget(self.chk_headless)
        opt_box.addStretch()

        config_grid.addWidget(lbl_conc, 3, 0)
        config_grid.addLayout(opt_box, 3, 1)
        self.lbl_concurrency_summary = QLabel(self)
        self.lbl_concurrency_summary.setWordWrap(True)
        self.lbl_concurrency_summary.setStyleSheet('color: #94a3b8; font-size: 11px;')
        config_grid.addWidget(self.lbl_concurrency_summary, 4, 0, 1, 2)
        self.spin_job_concurrency = QSpinBox(self)
        self.spin_job_concurrency.setRange(1, 100)
        self.spin_job_concurrency.setValue(max(1, self.config.job_concurrency))
        self.config.job_concurrency = self.spin_job_concurrency.value()
        self.spin_job_concurrency.setStyleSheet(self.spin_concurrency.styleSheet())
        self.spin_job_concurrency.setToolTip('Giới hạn tổng job từ máy tính và Telegram. Job còn lại xếp hàng theo thứ tự tạo.')
        lbl_job_limit = QLabel('Số job chạy cùng lúc:', self)
        lbl_job_limit.setStyleSheet('font-weight: 600; color: #e2e8f0;')
        config_grid.addWidget(lbl_job_limit, 5, 0)
        config_grid.addWidget(self.spin_job_concurrency, 5, 1)
        self.lbl_job_queue = QLabel(self)
        self.lbl_job_queue.setWordWrap(True)
        self.lbl_job_queue.setStyleSheet('color: #94a3b8; font-size: 11px;')
        config_grid.addWidget(self.lbl_job_queue, 6, 0, 1, 2)
        self.spin_job_concurrency.valueChanged.connect(self._on_job_limit_changed)
        self._update_job_queue_label()

        card_layout.addLayout(grid)

        # 8. Proxy Xoay (Proxy.vn) Settings Box
        proxy_box = QGroupBox("Nguồn proxy và đường mạng từng bước", self)
        proxy_box.setStyleSheet(
            "QGroupBox { font-weight: 700; color: #60a5fa; border: 1px solid #232326; border-radius: 8px; margin-top: 6px; padding-top: 10px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }"
        )
        p_layout = QVBoxLayout(proxy_box)
        p_layout.setSpacing(6)

        # Checkbox enable proxy
        p_row_enable = QHBoxLayout()
        self.chk_use_proxy = QCheckBox("Sử dụng proxy", self)
        saved_use_proxy = str(self.settings.value("proxy/proxyvn_enabled", "false")).lower() == "true"
        self.chk_use_proxy.setChecked(saved_use_proxy)
        self.chk_use_proxy.setStyleSheet("color: #60a5fa; font-weight: 700; font-size: 12px;")
        p_row_enable.addWidget(self.chk_use_proxy)
        p_row_enable.addStretch()
        p_layout.addLayout(p_row_enable)

        # Proxy Source Mode (Tab Quản lý Proxy vs Nhập nhanh 1 Key)
        saved_source = str(self.settings.value("proxy/proxy_source", "tab"))

        p_row_tab = QHBoxLayout()
        self.radio_proxy_tab = QRadioButton("Dùng Proxy Pool", self)
        self.radio_proxy_tab.setChecked(saved_source == "tab")
        self.radio_proxy_tab.setStyleSheet("color: #f1f5f9; font-weight: 600; font-size: 12px;")

        self.lbl_tab_proxy_status = QLabel("(Đang tải...)", self)
        self.lbl_tab_proxy_status.setStyleSheet("color: #94a3b8; font-size: 11px;")

        self.btn_goto_proxy_tab = QPushButton("Quản lý Proxy", self)
        self.btn_goto_proxy_tab.setStyleSheet("""
            QPushButton {
                background-color: #232326;
                color: #60a5fa;
                border: 1px solid #3a3a42;
                font-size: 11px;
                font-weight: 600;
                border-radius: 4px;
                padding: 3px 8px;
            }
            QPushButton:hover {
                background-color: #3a3a42;
            }
        """)
        self.btn_goto_proxy_tab.clicked.connect(self._switch_to_proxy_tab)

        p_row_tab.addWidget(self.radio_proxy_tab)
        p_row_tab.addWidget(self.lbl_tab_proxy_status)
        p_row_tab.addStretch()
        p_row_tab.addWidget(self.btn_goto_proxy_tab)
        p_layout.addLayout(p_row_tab)

        # Quick single key mode
        p_row_key = QHBoxLayout()
        self.radio_proxy_key = QRadioButton("Key xoay:", self)
        self.radio_proxy_key.setChecked(saved_source != "tab")
        self.radio_proxy_key.setStyleSheet("color: #cbd5e1; font-weight: 600; font-size: 11px;")
        p_row_key.addWidget(self.radio_proxy_key)

        self.input_proxy_vn_key = QLineEdit(self)
        self.input_proxy_vn_key.setPlaceholderText("Key Proxy.vn hoặc link API đổi IP; proxy có mật khẩu nhập tại Proxy Pool")
        saved_key = str(self.settings.value("proxy/provider/proxyvn_key", ""))
        self.input_proxy_vn_key.setText(saved_key)
        self.input_proxy_vn_key.setStyleSheet(
            "background-color: #1d1d20; color: #f8fafc; border: 1px solid #4a4b55; border-radius: 6px; padding: 6px;"
        )

        self.btn_check_proxy = QPushButton("Kiểm tra IP", self)
        self.btn_check_proxy.setStyleSheet(
            "QPushButton { background-color: #0284c7; color: white; font-weight: 600; border-radius: 6px; padding: 6px 12px; }"
            "QPushButton:hover { background-color: #0369a1; }"
            "QPushButton:disabled { background-color: #4a4b55; color: #94a3b8; }"
        )
        self.btn_check_proxy.clicked.connect(self._check_proxy_vn)

        p_row_key.addWidget(self.input_proxy_vn_key)
        p_row_key.addWidget(self.btn_check_proxy)
        p_layout.addLayout(p_row_key)

        # Each step can use the selected proxy or connect directly.
        p_routing_grid = QGridLayout()
        self.chk_mail_via_proxy = QCheckBox("Mua mail qua Proxy mua mail riêng", self)
        self.chk_mail_via_proxy.setChecked(self.config.mail_via_proxy)
        self.chk_google_via_proxy = QCheckBox("Đăng nhập Google qua proxy", self)
        self.chk_google_via_proxy.setChecked(resolve_google_login_via_proxy(self.settings))
        self.chk_app_login_via_proxy = QCheckBox("Đăng nhập Mochi qua proxy", self)
        self.chk_app_login_via_proxy.setChecked(self.config.app_login_via_proxy)
        self.chk_redeem_via_proxy = QCheckBox("Nhập mã mời qua proxy", self)
        self.chk_redeem_via_proxy.setChecked(self.config.redeem_via_proxy)
        routing_checkboxes = (
            self.chk_mail_via_proxy,
            self.chk_google_via_proxy,
            self.chk_app_login_via_proxy,
            self.chk_redeem_via_proxy,
        )
        for index, checkbox in enumerate(routing_checkboxes):
            checkbox.setStyleSheet("color: #cbd5e1; font-weight: 600; font-size: 11px;")
            checkbox.setToolTip("Dùng proxy đã chọn khi bật Sử dụng Proxy. Bỏ chọn để bước này dùng mạng trực tiếp.")
            checkbox.toggled.connect(self._save_proxy_routing_preferences)
            p_routing_grid.addWidget(checkbox, index // 2, index % 2)
        self.chk_mail_via_proxy.setToolTip('Khi bật Sử dụng Proxy, mua mail dùng danh sách tại tab Proxy mua mail; '
            'mỗi POST mua xong xoay IP, không dùng proxy tài khoản.')
        self.btn_mail_proxy=QPushButton('Mở Proxy mua mail',self)
        self.btn_mail_proxy.clicked.connect(lambda:self.window().switch_to_mail_proxy_tab()
            if hasattr(self.window(),'switch_to_mail_proxy_tab') else None)
        p_routing_grid.addWidget(self.btn_mail_proxy,2,0,1,2)
        p_layout.addLayout(p_routing_grid)

        p_row_opts = QHBoxLayout()

        lbl_slots = QLabel("Luồng/proxy:", self)
        lbl_slots.setStyleSheet("color: #cbd5e1; font-weight: 600; font-size: 11px;")
        self.spin_proxy_account_concurrency = QSpinBox(self)
        self.spin_proxy_account_concurrency.setRange(1, 10)
        self.spin_proxy_account_concurrency.setValue(max(1, self.config.proxy_account_concurrency))
        self.spin_proxy_account_concurrency.valueChanged.connect(self._on_proxy_slots_changed)
        self.input_proxy_vn_key.textChanged.connect(self._refresh_concurrency_display)
        self.spin_proxy_account_concurrency.setStyleSheet("background-color: #1d1d20; color: #f8fafc; border: 1px solid #4a4b55; border-radius: 6px; padding: 2px;")

        lbl_isp = QLabel("Mạng:", self)
        lbl_isp.setStyleSheet("color: #cbd5e1; font-weight: 600; font-size: 11px;")
        self.combo_proxy_isp = QComboBox(self)
        self.combo_proxy_isp.addItem("Random", "random")
        self.combo_proxy_isp.addItem("Viettel", "viettel")
        self.combo_proxy_isp.addItem("FPT", "fpt")
        self.combo_proxy_isp.addItem("VNPT", "vnpt")
        saved_isp = str(self.settings.value("proxy/proxyvn_isp", "random"))
        isp_idx = self.combo_proxy_isp.findData(saved_isp)
        if isp_idx >= 0:
            self.combo_proxy_isp.setCurrentIndex(isp_idx)
        self.combo_proxy_isp.setStyleSheet("background-color: #1d1d20; color: #f8fafc; border: 1px solid #4a4b55; border-radius: 6px; padding: 2px; font-size: 11px;")

        p_row_opts.addStretch()
        p_row_opts.addWidget(lbl_slots)
        p_row_opts.addWidget(self.spin_proxy_account_concurrency)
        p_row_opts.addWidget(lbl_isp)
        p_row_opts.addWidget(self.combo_proxy_isp)
        p_layout.addLayout(p_row_opts)

        def _update_proxy_fields_state():
            enabled = self.chk_use_proxy.isChecked()
            self.radio_proxy_tab.setEnabled(enabled)
            self.radio_proxy_key.setEnabled(enabled)
            self.btn_goto_proxy_tab.setEnabled(enabled)

            is_key_mode = self.radio_proxy_key.isChecked()
            self.input_proxy_vn_key.setEnabled(enabled and is_key_mode)
            self.btn_check_proxy.setEnabled(enabled and is_key_mode)

            for checkbox in routing_checkboxes:
                checkbox.setEnabled(enabled)
            self.spin_proxy_account_concurrency.setEnabled(enabled)
            self._refresh_concurrency_display()
            self.combo_proxy_isp.setEnabled(enabled)

        self.chk_use_proxy.toggled.connect(lambda: _update_proxy_fields_state())
        self.radio_proxy_tab.toggled.connect(lambda: _update_proxy_fields_state())
        self.radio_proxy_key.toggled.connect(lambda: _update_proxy_fields_state())
        _update_proxy_fields_state()

        self.settings_page = self._build_settings_page(config_grid, proxy_box)
        self.lbl_job_source = QLabel(self)
        self.lbl_job_source.setWordWrap(True)
        self.lbl_job_source.setStyleSheet("color: #9aa8c4; font-size: 12px;")
        card_layout.addWidget(self.lbl_job_source)
        self._update_job_source_summary()
        btn_config = QPushButton("Cấu hình mail, proxy và số luồng", self)
        btn_config.clicked.connect(self._open_settings)
        card_layout.addWidget(btn_config, 0, Qt.AlignmentFlag.AlignLeft)
        card_layout.addStretch()

        # Action Buttons
        btn_layout = QHBoxLayout()
        self.btn_start = QPushButton("Tạo & chạy job", self)
        self.btn_start.setFixedHeight(44)
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.setStyleSheet(
            "QPushButton { background-color: #10b981; color: white; font-weight: 700; font-size: 14px; border: none; border-radius: 8px; }"
            "QPushButton:hover { background-color: #059669; }"
            "QPushButton:disabled { background-color: #4a4b55; color: #94a3b8; }"
        )
        self.btn_start.clicked.connect(self._on_start_clicked)
        self.chk_schedule_job.toggled.connect(lambda enabled:
            self.btn_start.setText('Tạo lịch job' if enabled else 'Tạo & chạy job'))

        self.btn_stop = QPushButton("Dừng job", self)
        self.btn_stop.setFixedHeight(44)
        self.btn_stop.setFixedWidth(140)
        self.btn_stop.setEnabled(False)
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.setStyleSheet(
            "QPushButton { background-color: #ef4444; color: white; font-weight: 700; font-size: 14px; border: none; border-radius: 8px; }"
            "QPushButton:hover { background-color: #dc2626; }"
            "QPushButton:disabled { background-color: #4a4b55; color: #94a3b8; }"
        )
        self.btn_stop.clicked.connect(self._on_stop_clicked)

        self.btn_save_draft = QPushButton("Lưu nháp", self)
        self.btn_save_draft.clicked.connect(self._save_job_draft)
        btn_layout.addWidget(self.btn_save_draft)
        self.lbl_draft_status = QLabel(self)
        self.lbl_draft_status.setStyleSheet("color: #34d399; font-size: 11px;")
        btn_layout.addWidget(self.lbl_draft_status)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_stop)
        btn_layout.addWidget(self.btn_start)
        card_layout.addLayout(btn_layout)

        return card

    def _build_running_jobs_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("mochiContentCard")
        card.setStyleSheet("QFrame#mochiContentCard { background-color: #232326; border: 1px solid #3a3a42; border-radius: 10px; }")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        head_layout = QHBoxLayout()
        title = QLabel("DANH SÁCH NHIỆM VỤ TRONG PHIÊN", self)
        title.setStyleSheet("font-size: 12px; font-weight: 700; color: #94a3b8; letter-spacing: 0.5px;")
        head_layout.addWidget(title)
        head_layout.addStretch()
        layout.addLayout(head_layout)
        self.lbl_running_job_queue = QLabel(self)
        self.lbl_running_job_queue.setWordWrap(True)
        self.lbl_running_job_queue.setStyleSheet('color: #9aa8c4; font-size: 12px;')
        layout.addWidget(self.lbl_running_job_queue)
        self._update_job_queue_label()

        # Table
        self.table_jobs = QTableWidget(self)
        self.table_jobs.setColumnCount(8)
        self.table_jobs.setHorizontalHeaderLabels([
            "Khách hàng / Job", "Người tạo job", "Mã mời", "Tiến độ", "Điểm kẹo", "Trạng thái", "Thời gian", "Thao tác"
        ])
        self.table_jobs.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table_jobs.horizontalHeader().setMinimumSectionSize(90)
        self.table_jobs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.table_jobs.setColumnWidth(0, 160)
        self.table_jobs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        self.table_jobs.setColumnWidth(1, 220)
        self.table_jobs.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Interactive)
        self.table_jobs.setColumnWidth(7, 112)
        self.table_jobs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table_jobs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_jobs.setStyleSheet(
            "QTableWidget { background-color: #1d1d20; color: #f8fafc; gridline-color: #3a3a42; border-radius: 6px; border: 1px solid #3a3a42; }"
            "QHeaderView::section { background-color: #232326; color: #cbd5e1; font-weight: 700; padding: 6px; border: none; }"
            "QTableWidget::item:selected { background-color: #3a3a42; }"
        )
        self.table_jobs.cellClicked.connect(self._on_table_cell_clicked)
        layout.addWidget(self.table_jobs)

        return card

    def _build_logs_card(self) -> QFrame:
        card = QFrame(self)
        card.setObjectName("mochiContentCard")
        card.setStyleSheet("QFrame#mochiContentCard { background-color: #232326; border: 1px solid #3a3a42; border-radius: 10px; }")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        log_head = QHBoxLayout()
        title = QLabel("NHẬT KÝ THỰC THI (REAL-TIME LOGS)", self)
        title.setStyleSheet("font-size: 12px; font-weight: 700; color: #94a3b8; letter-spacing: 0.5px;")
        log_head.addWidget(title)

        self.lbl_log_filter = QLabel("(Tất cả jobs)", self)
        self.lbl_log_filter.setStyleSheet("font-size: 11px; font-weight: 600; color: #60a5fa; margin-left: 6px;")
        log_head.addWidget(self.lbl_log_filter)

        log_head.addStretch()

        btn_show_all = QPushButton("Tất cả", self)
        btn_show_all.setStyleSheet("background-color: #3a3a42; color: #cbd5e1; border-radius: 4px; padding: 4px 10px; font-size: 11px;")
        btn_show_all.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_show_all.clicked.connect(self._show_all_logs)
        log_head.addWidget(btn_show_all)

        btn_clear = QPushButton("Xóa log", self)
        btn_clear.setStyleSheet("background-color: #3a3a42; color: #cbd5e1; border-radius: 4px; padding: 4px 10px; font-size: 11px;")
        btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear.clicked.connect(self._clear_logs)
        log_head.addWidget(btn_clear)
        layout.addLayout(log_head)

        self.txt_log = QTextEdit(self)
        self.txt_log.setReadOnly(True)
        self.txt_log.setFont(QFont("Consolas", 10))
        self.txt_log.setStyleSheet(
            "QTextEdit { background-color: #1b1b1e; color: #f1f5f9; border: 1px solid #232326; border-radius: 6px; padding: 8px; }"
        )
        layout.addWidget(self.txt_log)

        return card

    def _build_settings_page(self, config_grid, proxy_box):
        page = QWidget(self)
        page.setObjectName("hoshinoSettings")
        page.setStyleSheet(WORKSPACE_STYLE)
        page.hide()
        root = QVBoxLayout(page)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)
        heading = QLabel("Cài đặt", page)
        heading.setStyleSheet("font-size: 25px; font-weight: 700;")
        root.addWidget(heading)
        subtitle = QLabel("Thiết lập đường mạng, nguồn mail và số luồng cho các job mới.", page)
        subtitle.setStyleSheet("color: #9aa8c4;")
        root.addWidget(subtitle)
        self.settings_tabs = QTabBar(page)
        self.settings_tabs.setDrawBase(False)
        self.settings_tabs.setExpanding(False)
        self.settings_tabs.addTab("Hạ tầng chung")
        self.settings_tabs.addTab("Mochi Chat")
        root.addWidget(self.settings_tabs, 0, Qt.AlignmentFlag.AlignLeft)
        panels = QStackedWidget(page)
        self.settings_tabs.currentChanged.connect(panels.setCurrentIndex)

        def panel(title, content_layout, body, content_widget=None):
            scroll = QScrollArea(page)
            scroll.setWidgetResizable(True)
            content = QWidget(scroll)
            columns = QHBoxLayout(content)
            columns.setContentsMargins(0, 12, 0, 0)
            columns.setSpacing(16)
            left = QFrame(content)
            left.setObjectName("settingsCard")
            left.setStyleSheet("QFrame#settingsCard { background: #232326; border: 1px solid #323239; border-radius: 10px; }")
            form = QVBoxLayout(left)
            form.setContentsMargins(18, 18, 18, 18)
            form.setSpacing(18)
            title_label = QLabel(title, left)
            title_label.setStyleSheet("font-size: 15px; font-weight: 700;")
            form.addWidget(title_label)
            if content_layout is not None:
                form.addLayout(content_layout)
            if content_widget is not None:
                form.addWidget(content_widget)
            form.addStretch()
            help_card = QFrame(content)
            help_card.setObjectName("settingsHelp")
            help_card.setStyleSheet("QFrame#settingsHelp { background: #27272c; border: 1px solid #383840; border-top: 2px solid #3b82f6; border-radius: 10px; }")
            help_layout = QVBoxLayout(help_card)
            help_layout.setContentsMargins(18, 18, 18, 18)
            help_label = QLabel(body, help_card)
            help_label.setTextFormat(Qt.TextFormat.RichText)
            help_label.setWordWrap(True)
            help_label.setAlignment(Qt.AlignmentFlag.AlignTop)
            help_label.setStyleSheet("color: #aebed9; line-height: 1.5;")
            help_layout.addWidget(help_label)
            help_layout.addStretch()
            columns.addWidget(left, 3)
            columns.addWidget(help_card, 2)
            scroll.setWidget(content)
            panels.addWidget(scroll)

        proxy_box.setStyleSheet("QGroupBox { color: #bcc8de; border: 0; padding-top: 16px; margin-top: 4px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 0; }")
        panel("Hạ tầng chung", None,
            "<h3>Cấu hình proxy theo từng bước</h3>"
            "<p>Chọn danh sách từ Proxy Pool hoặc nhập một key xoay.</p>"
            "<p>Bốn lựa chọn độc lập cho mua mail, Google, đăng nhập Mochi và nhập mã mời. "
            "Bỏ chọn bước nào thì bước đó dùng mạng trực tiếp.</p>"
            "<h3>Luồng trên mỗi proxy</h3><p>Tổng luồng tự tính theo số proxy duy nhất × luồng/proxy. "
            "Ví dụ 5 proxy × 1 luồng/proxy = 5 tài khoản đồng thời, khi có bước tài khoản dùng proxy.</p>"
            "<p>Proxy chưa tới hạn xoay vẫn được sử dụng. Khi request lỗi, tool thực hiện phục hồi theo cấu hình hiện có.</p>",
            content_widget=proxy_box)
        for row in (0, 1, 2, 3, 5):
            item = config_grid.itemAtPosition(row, 0)
            if item and isinstance(item.widget(), QLabel):
                item.widget().setWordWrap(True)
                item.widget().setMaximumWidth(145)
        config_grid.setColumnMinimumWidth(0, 145)
        panel("Mochi Chat", config_grid,
            "<h3>Mail và tài khoản</h3><p>Nhập API key và danh sách ID sản phẩm theo nhà cung cấp. "
            "Mỗi nhà cung cấp lưu key và ID sản phẩm riêng.</p>"
            "<p>Chọn <b>Cả hai shop</b> để nhập đồng thời hai API key. Thứ tự ưu tiên có dạng "
            "<b>gmailvip:3445, 1trieumail:3423, gmailvip:2862</b>, thử từ trái sang phải. "
            "Hết hàng hoặc thiếu số lượng thì chuyển mục tiếp theo; đã mua thành công thì dừng mua.</p>"
            "<p>Job mua 1 mail thử mã. Thành công mới mua phần còn thiếu; không mua bù khi tài khoản lỗi. Để sử dụng lại mail đã mua, chọn file JSON trong "
            "tab <b>Kho tài khoản</b>.</p><h3>Số luồng đồng thời</h3>"
            "<p>Khi các bước tài khoản chạy trực tiếp, dùng số luồng nhập tại đây. "
            "Khi đi qua proxy, tổng luồng được tự tính theo dung lượng Proxy Pool.</p>"
            "<h3>Số job chạy cùng lúc</h3><p>Giới hạn chung cho job Local, admin, partner và khách hàng. "
            "Job vượt giới hạn chờ theo thứ tự tạo, chưa mua mail. Tăng giới hạn sẽ chạy thêm job đang chờ; "
            "giảm giới hạn không dừng job đã chạy.</p>"
            "<h3>Chạy ẩn</h3><p>Bật để ẩn trình duyệt Google. Tắt để quan sát tiến trình đăng nhập.</p>"
            "<p>Cấu hình được dùng cho job tạo từ tool và từ Telegram. Job đang chạy giữ cấu hình lúc khởi tạo.</p>")
        root.addWidget(panels, 1)
        footer = QHBoxLayout()
        self.lbl_settings_saved = QLabel("Cấu hình tài khoản dùng cho job mới; giới hạn số job áp dụng ngay.", page)
        self.lbl_settings_saved.setWordWrap(True)
        self.lbl_settings_saved.setStyleSheet("color: #9aa8c4;")
        footer.addWidget(self.lbl_settings_saved, 1)
        self.btn_settings_save = QPushButton("Lưu thay đổi", page)
        self.btn_settings_save.clicked.connect(self._save_workspace_settings)
        footer.addWidget(self.btn_settings_save)
        root.addLayout(footer)
        return page

    def _open_settings(self):
        window = self.window()
        if hasattr(window, "switch_to_settings_tab"):
            window.switch_to_settings_tab()
        else:
            self.settings_page.show()

    def _save_workspace_settings(self):
        self._capture_mail_config()
        self.config.account_concurrency = self._manual_concurrency
        self.config.proxy_account_concurrency = self.spin_proxy_account_concurrency.value()
        self._save_proxy_routing_preferences()
        self.config.save_to_settings(self.settings)
        self.settings.setValue("runtime/browser_worker_headless", self.chk_headless.isChecked())
        self.settings.setValue("proxy/proxyvn_enabled", self.chk_use_proxy.isChecked())
        self.settings.setValue("proxy/proxy_source", "tab" if self.radio_proxy_tab.isChecked() else "key")
        self.settings.setValue("proxy/provider/proxyvn_key", self.input_proxy_vn_key.text().strip())
        self.settings.setValue("proxy/proxyvn_isp", self.combo_proxy_isp.currentData())
        self.settings.sync()
        self.lbl_settings_saved.setText("Đã lưu thay đổi")
        self.lbl_settings_saved.setStyleSheet("color: #34d399;")
        self._update_job_source_summary()

    def _update_job_source_summary(self, *_):
        if not hasattr(self, "lbl_job_source"):
            return
        if self.chk_use_mail_batch.isChecked():
            self.lbl_job_source.setText("Nguồn mail: dùng lô đã mua trong Kho tài khoản; không mua thêm mail.")
        elif self.combo_provider.currentData() == MULTI_SHOP:
            self.lbl_job_source.setText("Nguồn mail: hai shop theo ưu tiên. Mua 1 mail thử mã; thành công mới mua phần còn thiếu.")
        else:
            self.lbl_job_source.setText("Nguồn mail: mua 1 mail thử mã, thành công mới mua phần còn thiếu. Thiết lập tại Cài đặt chung.")

    def _inspect_mail_batch(self):
        try:
            path = self.input_mail_batch.text().strip()
            if not path:
                raise ValueError("Chọn file lô mail trước.")
            accounts = load_batch(path)
            self.lbl_batch_summary.setText(f"Lô còn {len(accounts):,} mail chưa hoàn tất. Tool chỉ dùng mail còn lại và không mua bù.")
        except (OSError, ValueError) as exc:
            self.lbl_batch_summary.setText(f"Không đọc được lô mail: {exc}")

    def _save_job_draft(self):
        for key, widget in (("code", self.input_code), ("points", self.input_target_points),
                ("customer", self.input_customer), ("total_vnd", self.input_total_vnd)):
            self.settings.setValue(f"ui/mochi_draft/{key}", widget.text())
        self.settings.sync()
        self.lbl_draft_status.setText("Đã lưu nháp")

    def _restore_job_draft(self):
        for key, widget, default in (("code", self.input_code, ""),
                ("points", self.input_target_points, "1000"),
                ("customer", self.input_customer, ""), ("total_vnd", self.input_total_vnd, "")):
            widget.setText(str(self.settings.value(f"ui/mochi_draft/{key}", default) or ""))

    def show_job_logs(self, job_id):
        if job_id in self._session_jobs:
            self._select_job(job_id)
        self.workspace_tabs.setCurrentIndex(0)
        self.job_tabs.setCurrentIndex(2)

    def _select_job_type(self, key: str):
        self._job_type = key
        self.card_invite.set_selected(key == "CUSTOMER_INVITE")

    def _on_code_text_changed(self, text: str):
        match = re.search(r"(?:^|\s)Mã\s*:\s*([A-Za-z0-9_-]+)", text, re.IGNORECASE)
        if match:
            extracted = match.group(1).upper()
            self.input_code.blockSignals(True)
            self.input_code.setText(extracted)
            self.input_code.blockSignals(False)

    def _on_target_points_changed(self, text: str):
        try:
            pts = int(text.strip())
            accs = required_mochi_invitation_accounts(pts)
            self.lbl_acc_count.setText(f"(= {accs} accounts)")
        except ValueError:
            self.lbl_acc_count.setText("(= 0 accounts)")

    def _on_provider_changed(self, index: int):
        prov = self.combo_provider.currentData()
        self._capture_mail_config(provider=self.config.mail_provider)
        self._save_proxy_routing_preferences()
        self.config.save_to_settings(self.settings)
        self.config = MochiChatConfig.from_settings(self.settings, provider=prov)
        self.input_api_key.setText(self.config.mail_provider_api_key)
        self.input_pids.setText(self.config.mail_product_ids)
        self.input_gmailvip_key.setText(self.config.mail_api_keys.get(GMAILVIP, ''))
        self.input_1trieumail_key.setText(self.config.mail_api_keys.get(ONE_TRIEU, ''))
        self._apply_mail_mode_ui()

    def _capture_mail_config(self, provider=None):
        mode = provider or self.combo_provider.currentData()
        self.config.mail_provider = mode
        self.config.mail_product_ids = self.input_pids.text().strip()
        if mode == MULTI_SHOP:
            self.config.mail_provider_api_key = ''
            self.config.mail_api_keys = {GMAILVIP: self.input_gmailvip_key.text().strip(),
                ONE_TRIEU: self.input_1trieumail_key.text().strip()}
        else:
            self.config.mail_provider_api_key = self.input_api_key.text().strip()

    def _apply_mail_mode_ui(self):
        combined = self.combo_provider.currentData() == MULTI_SHOP
        self.mail_key_stack.setCurrentIndex(1 if combined else 0)
        self.mail_key_stack.setFixedHeight(90 if combined else 42)
        self.lbl_mail_key.setText('API key hai shop:' if combined else 'API Key Shop Mail (*):')
        self.lbl_mail_pids.setText('Thứ tự ưu tiên:' if combined else 'Product IDs:')
        self.input_pids.setPlaceholderText('gmailvip:3445, 1trieumail:3423, gmailvip:2862' if combined else 'Ví dụ: 3445, 2862')
        self.input_pids.setToolTip('Mua theo thứ tự từ trái sang phải. Ví dụ gmailvip:3445, 1trieumail:3423. '
            'Hết hàng hoặc thiếu số lượng thì thử mục tiếp theo; dừng sau lô đã mua thành công.' if combined
            else 'Danh sách ID sản phẩm của shop đang chọn, theo thứ tự ưu tiên.')
        self._update_job_source_summary()

    def _save_proxy_routing_preferences(self, *_):
        self.config.mail_via_proxy = self.chk_mail_via_proxy.isChecked()
        self.config.app_login_via_proxy = self.chk_app_login_via_proxy.isChecked()
        self.config.redeem_via_proxy = self.chk_redeem_via_proxy.isChecked()
        self.settings.setValue("runtime/mochi_chat/mail_via_proxy", self.config.mail_via_proxy)
        self.settings.setValue("runtime/mochi_chat/app_login_via_proxy", self.config.app_login_via_proxy)
        self.settings.setValue("runtime/mochi_chat/redeem_via_proxy", self.config.redeem_via_proxy)
        self.settings.setValue("runtime/google_login_via_proxy", self.chk_google_via_proxy.isChecked())
        self._refresh_concurrency_display()

    def _set_validation_message(self, msg: str, is_warning: bool = False):
        if not msg:
            self.validation_label.setVisible(False)
            return
        self.validation_label.setText(msg)
        self.validation_label.setVisible(True)
        if is_warning:
            self.validation_label.setStyleSheet(
                "QLabel#validationError { color: #fbbf24; background-color: rgba(245, 158, 11, 0.16); "
                "border: 1px solid rgba(245, 158, 11, 0.50); border-radius: 6px; padding: 6px 14px; font-weight: 700; font-size: 13px; }"
            )
        else:
            self.validation_label.setStyleSheet(
                "QLabel#validationError { color: #f87171; background-color: rgba(239, 68, 68, 0.14); "
                "border: 1px solid rgba(239, 68, 68, 0.45); border-radius: 6px; padding: 6px 14px; font-weight: 700; font-size: 13px; }"
            )

    def _check_mail_balance(self):
        self._check_shop_balance(self.combo_provider.currentData(), self.input_api_key, self.btn_check_balance)

    def _check_shop_balance(self, prov, field, button):
        api_key = field.text().strip()
        if not api_key:
            QMessageBox.warning(self, 'Thông báo', 'Vui lòng nhập API Key.')
            return
        button.setEnabled(False)
        button.setText('...')

        async def check():
            cls = OneTrieuMail if '1trieu' in prov else GmailVipVn
            async with cls(api_key) as client:
                return await client.check_balance()

        def restore():
            button.setEnabled(not self.chk_use_mail_batch.isChecked())
            button.setText('Kiểm tra')

        start_background(self, check,
            lambda balance: QMessageBox.information(self, 'Số dư tài khoản', f'Số dư shop {prov}: {balance:,.0f} VNĐ'),
            lambda error: QMessageBox.critical(self, 'Lỗi API Key', error), restore)

    def _check_proxy_vn(self):
        key = self.input_proxy_vn_key.text().strip()
        if not key:
            QMessageBox.warning(self, 'Thông báo', 'Vui lòng nhập Key hoặc Link API từ Proxy.vn.')
            return
        isp = self.combo_proxy_isp.currentData()
        self.btn_check_proxy.setEnabled(False)
        self.btn_check_proxy.setText('...')

        async def check():
            with proxy_maintenance(key):
                return await make_rotating_provider(key, nhamang=isp).get_current_or_rotate()

        def show(result):
            if result.is_cooling:
                QMessageBox.information(self, 'Proxy', f'Proxy đang chờ xoay ({result.wait_seconds:.0f}s).')
            elif result.success and result.proxy:
                QMessageBox.information(self, 'Proxy', f'Proxy sẵn sàng: {result.proxy.server}')
            else:
                QMessageBox.warning(self, 'Proxy', result.error or 'Không lấy được proxy.')

        def restore():
            self.btn_check_proxy.setEnabled(True)
            self.btn_check_proxy.setText('Kiểm tra IP')

        start_background(self, check, show,
            lambda error: QMessageBox.warning(self, 'Proxy.vn', error), restore)

    def _on_mail_batch_mode_changed(self, enabled):
        for widget in (self.combo_provider, self.input_api_key, self.input_pids, self.btn_check_balance,
                self.input_gmailvip_key, self.input_1trieumail_key,
                self.btn_check_gmailvip_balance, self.btn_check_1trieumail_balance):
            widget.setEnabled(not enabled)
        self.input_mail_batch.setEnabled(enabled)
        self.btn_choose_mail_batch.setEnabled(enabled)

    def _choose_mail_batch(self):
        base = Path(__file__).resolve().parents[2]
        if base.name == 'src':
            base = base.parent
        current = self.input_mail_batch.text().strip()
        initial = current or str(base / 'data' / 'mail_batches')
        path, _ = QFileDialog.getOpenFileName(self, 'Chọn lô mail đã mua', initial, 'Lô mail JSON (*.json)')
        if path:
            self.input_mail_batch.setText(path)
            self.settings.setValue('runtime/mochi_chat/mail_batch_path', path)

    def _record_mail_purchase(self, receipt):
        # Called in a worker thread: persist first; signal delivery alone is not durable.
        if self.mail_purchase_recorder is not None:
            self.mail_purchase_recorder(receipt)
        self.mail_purchase_recorded.emit(receipt)

    def _on_start_clicked(self, *, code=None, points=None, customer_name=None, before_start=None,
            creator_name='Local', creator_chat_id=None, scheduled_at=None, _scheduled_record=None,
            mail_api_keys=None):
        if self._closing_jobs:
            self._set_validation_message('Tool đang đóng; không nhận thêm job mới.')
            return None
        local_request = code is None
        code = str(self.input_code.text() if code is None else code).strip().upper()
        if not code:
            self._set_validation_message("Vui lòng nhập mã mời Mochi!")
            return

        try:
            points = int(self.input_target_points.text().strip() if points is None else points)
            if points <= 0:
                raise ValueError()
        except ValueError:
            self._set_validation_message("Điểm mục tiêu phải là số nguyên dương!")
            return

        try:
            total_vnd = int(self.input_total_vnd.text().strip() or "0") if local_request else 0
            if total_vnd < 0:
                raise ValueError()
        except ValueError:
            self._set_validation_message("Tổng tiền phải là số nguyên không âm.")
            return

        target_accs = required_mochi_invitation_accounts(points)
        customer_name = self.input_customer.text().strip() if customer_name is None else str(customer_name).strip()
        if mail_api_keys is not None:
            try:
                mail_api_keys = validated_user_mail_keys(mail_api_keys)
            except ValueError as exc:
                self._set_validation_message(str(exc))
                return None
        if local_request and self.chk_schedule_job.isChecked():
            selected = self.input_schedule_time.dateTime()
            scheduled_at = datetime(selected.date().year(),selected.date().month(),selected.date().day(),
                selected.time().hour(),selected.time().minute(),tzinfo=VIETNAM_TIME)
        if scheduled_at is not None:
            return self._create_scheduled_job(code, points, customer_name, total_vnd, scheduled_at,
                before_start, creator_name, creator_chat_id)
        if _scheduled_record is not None:
            total_vnd = _scheduled_record.total_vnd
        accounts = None
        source_mail_batch = None
        use_mail_batch = bool(_scheduled_record.source_mail_batch) if _scheduled_record else self.chk_use_mail_batch.isChecked()
        if use_mail_batch:
            batch_path = _scheduled_record.source_mail_batch if _scheduled_record else self.input_mail_batch.text().strip()
            if not batch_path:
                self._set_validation_message('Vui lòng chọn file lô mail đã mua; tool sẽ không mua thêm mail.')
                return
            try:
                source_mail_batch = str(Path(batch_path).expanduser().resolve())
                accounts = tuple(load_batch(source_mail_batch))
            except (OSError, ValueError) as exc:
                self._set_validation_message(f'Không đọc được lô mail đã mua: {exc}')
                return
            if len(accounts) < target_accs:
                self._set_validation_message(f'Lô chỉ còn {len(accounts)} mail chưa thành công, cần {target_accs}. Giảm mục tiêu hoặc chọn lô khác; tool không mua bù.')
                return

        api_key = self.input_api_key.text().strip()
        prov = self.combo_provider.currentData()
        effective_keys = (mail_api_keys if mail_api_keys is not None else {
            GMAILVIP: self.input_gmailvip_key.text().strip(), ONE_TRIEU: self.input_1trieumail_key.text().strip()})
        if mail_api_keys is not None:
            api_key = mail_api_keys.get(prov, '')
        if not use_mail_batch:
            if prov == MULTI_SHOP:
                try:
                    choices = parse_mail_priority(self.input_pids.text())
                    keys = effective_keys
                    for choice in choices:
                        if not keys[choice.provider]:
                            raise ValueError(f'Vui lòng nhập API key {SHOP_NAMES[choice.provider]}.')
                except ValueError as exc:
                    self._set_validation_message(str(exc))
                    return
            elif not api_key:
                self._set_validation_message("Vui lòng nhập API Key nhà cung cấp mail để mua tài khoản tự động!")
                return

        self._set_validation_message("")

        prov = self.combo_provider.currentData()
        pids = self.input_pids.text().strip()
        concurrency = self.spin_concurrency.value()
        headless = self.chk_headless.isChecked()

        # Save config
        self._capture_mail_config()
        self.config.account_concurrency = self._manual_concurrency
        self.config.proxy_account_concurrency = self.spin_proxy_account_concurrency.value()
        self._save_proxy_routing_preferences()
        self.config.save_to_settings(self.settings)
        self.settings.setValue("runtime/browser_worker_headless", headless)
        if source_mail_batch:
            self.settings.setValue('runtime/mochi_chat/mail_batch_path', source_mail_batch)

        job_id = _scheduled_record.job_id if _scheduled_record else f"mochi_{uuid.uuid4().hex[:8]}"

        # Setup Proxy Broker & Client (Exclusively Proxy.vn)
        use_proxy = self.chk_use_proxy.isChecked()
        is_tab_proxy = self.radio_proxy_tab.isChecked()
        rot_key = self.input_proxy_vn_key.text().strip()
        google_via_proxy = use_proxy and self.chk_google_via_proxy.isChecked()
        mail_via_proxy = use_proxy and self.chk_mail_via_proxy.isChecked()
        app_login_via_proxy = use_proxy and self.chk_app_login_via_proxy.isChecked()
        redeem_via_proxy = use_proxy and self.chk_redeem_via_proxy.isChecked()
        needs_proxy = any((google_via_proxy,app_login_via_proxy,redeem_via_proxy))
        mail_proxy_pool=None
        if mail_via_proxy and accounts is None:
            if self.mail_proxy_page is None or not self.mail_proxy_page.get_active_proxies():
                self._set_validation_message('Tab Proxy mua mail chưa có proxy xoay. Thêm endpoint|key_xoay hoặc bỏ chọn mua mail qua proxy.')
                return
            try:
                mail_proxy_pool=self.mail_proxy_page.create_pool()
            except ValueError as exc:
                self._set_validation_message(str(exc))
                return
        slots_per_proxy = self.spin_proxy_account_concurrency.value()
        isp = self.combo_proxy_isp.currentData()

        self.settings.setValue("proxy/proxyvn_enabled", use_proxy)
        self.settings.setValue("proxy/proxy_source", "tab" if is_tab_proxy else "key")
        self.settings.setValue("proxy/provider/proxyvn_key", rot_key)
        self.settings.setValue("proxy/proxyvn_isp", isp)
        self.settings.setValue("runtime/google_login_via_proxy", self.chk_google_via_proxy.isChecked())
        self.settings.setValue("runtime/mochi_chat/proxy_account_concurrency", slots_per_proxy)

        static_proxies = None
        rotating_provider = None

        if needs_proxy:
            if is_tab_proxy:
                active_proxies = self._active_proxy_entries()

                if not active_proxies:
                    self._set_validation_message("Tab 'Quản lý Proxy' chưa có proxy nào hoạt động! Vui lòng mở Tab Quản lý Proxy để Import proxy dạng ip:port|key.")
                    return

                static_proxies = []
                for endpoint, key_xoay in active_proxies:
                    try:
                        p_model = ProxyModel.from_string(
                            endpoint,
                            provider=('muaproxyxoay.net' if rotating_provider_id(key_xoay) == 'muaproxyxoay' else 'proxy.vn'),
                            rotation_url=key_xoay or None,
                        )
                        p_model.key_xoay = key_xoay
                        static_proxies.append(p_model)
                    except Exception as pe:
                        logger.warning("Lỗi parse proxy model: %s", pe)

                if not static_proxies:
                    self._set_validation_message("Không thể phân tích danh sách proxy từ Tab Quản lý Proxy!")
                    return
            else:
                if not rot_key:
                    self._set_validation_message("Vui lòng nhập Key hoặc Link API từ Proxy.vn (hoặc bỏ chọn 'Sử dụng Proxy' để chạy Direct IP)!")
                    return
                rotating_provider = make_rotating_provider(rot_key, nhamang=isp)

        broker = ProxyBroker(
            static_proxies=static_proxies,
            account_concurrency=slots_per_proxy,
            allow_direct_without_proxies=(not needs_proxy),
            rotating_provider=rotating_provider,
        )
        account_proxy_enabled = use_proxy and any((google_via_proxy, app_login_via_proxy, redeem_via_proxy))
        concurrency = broker.account_capacity if account_proxy_enabled else self._manual_concurrency
        proxy_client = ProxyPoolClient(job_id=job_id, platform_id="mochichat", broker=broker)

        register_job = before_start if before_start is not None else self.local_job_registrar
        if register_job is not None and _scheduled_record is None:
            try:
                if not register_job(job_id, code, points, target_accs):
                    self._set_validation_message('Yêu cầu này đã có job; không tạo thêm job trùng.')
                    return
            except Exception:
                self._set_validation_message('Không lưu được chủ sở hữu và lịch sử job. Chưa khởi chạy hoặc mua mail.')
                return

        record = _scheduled_record or _MochiJobSessionRecord(
            job_id=job_id,
            invite_code=code,
            target_count=target_accs,
            customer_name=customer_name,
            status="Đang chờ",
            creator_name=creator_name,
            creator_chat_id=creator_chat_id,
        )
        record.status = 'Đang chờ'
        record.total_vnd = total_vnd
        self._session_jobs[job_id] = record
        self._selected_job_id = job_id
        self._lbl_log_filter_update()

        runner = MochiInviteRunner(
            proxy_client=proxy_client,
            concurrency=concurrency,
            mail_provider=prov,
            mail_provider_api_key=api_key,
            mail_api_keys=dict(effective_keys) if prov == MULTI_SHOP else None,
            mail_product_ids=pids,
            browser_headless=headless,
            google_login_via_proxy=google_via_proxy,
            mail_via_proxy=mail_via_proxy,
            app_login_via_proxy=app_login_via_proxy,
            redeem_via_proxy=redeem_via_proxy,
            mail_purchase_sink=self._record_mail_purchase if self.mail_purchase_recorder is not None else None,
            mail_proxy_pool=mail_proxy_pool,
        )
        if mail_proxy_pool is not None:
            mail_proxy_pool.on_wait=lambda message:runner._emit_log(job_id,message)

        worker = MochiWorkerThread(
            runner=runner,
            job_id=job_id,
            invite_code=code,
            target_count=target_accs,
            customer_name=customer_name,
            unit_price=total_vnd // target_accs,
            parent=self,
            accounts=accounts,
            source_mail_batch=source_mail_batch,
        )
        worker.log_signal.connect(self._handle_runner_log)
        worker.progress_signal.connect(self._handle_runner_progress)
        worker.finished_signal.connect(self._on_job_finished)
        worker.error_signal.connect(self._on_job_error)
        worker.finished.connect(lambda jid=job_id: self._on_worker_stopped(jid))

        self._workers[job_id] = worker
        self._pending_jobs.append(job_id)
        self.job_dispatch_changed.emit(job_id, 'queued')
        self._update_action_buttons_state()
        self._render_jobs_table()
        self._drain_job_queue()
        if record.status == 'Thất bại':
            self._set_validation_message('Không khởi chạy được job; đã ghi nhận lỗi trong thống kê.')
            return None
        self._update_action_buttons_state()
        self._append_log_line(
            f"Đã tạo Job [{job_id}] cho mã [{code}] — {self.job_dispatch_text(job_id)} (Mục tiêu: {points:,} kẹo / {target_accs} tài khoản)...",
            color="#10b981",
            job_id=job_id,
        )
        if source_mail_batch:
            self._append_log_line(f'Dùng lô mail đã mua: {source_mail_batch}. Không mua thêm mail.',
                color='#60a5fa', job_id=job_id)
        return job_id

    def _create_scheduled_job(self, code, points, customer, total_vnd, when, registrar, creator, chat_id):
        try:
            when = schedule_time(when, future=True)
            if not re.fullmatch(r'[A-Z0-9_-]{1,64}', code) or not 1 <= points <= 1_000_000:
                raise ValueError('Mã mời hoặc số bean không hợp lệ để hẹn giờ.')
            registrar = registrar or self.local_job_registrar
            if registrar is None:
                raise ValueError('Chưa sẵn sàng lưu lịch job. Mở tool đầy đủ để đặt lịch.')
            job_id = f'mochi_{uuid.uuid4().hex[:8]}'
            source = self.input_mail_batch.text().strip() if self.chk_use_mail_batch.isChecked() else None
            if self.chk_use_mail_batch.isChecked() and not source:
                raise ValueError('Vui lòng chọn lô mail đã mua trước khi đặt lịch.')
            data = dict(customer_name=customer, creator_name=creator, total_vnd=total_vnd,
                source_mail_batch=str(Path(source).expanduser().resolve()) if source else None)
            target = required_mochi_invitation_accounts(points)
            if not registrar(job_id, code, points, target, scheduled_at=when.isoformat(), schedule_data=data):
                raise ValueError('Yêu cầu này đã có job; không tạo thêm lịch trùng.')
            self.restore_scheduled_job(dict(job_id=job_id, code=code, target_count=target,
                chat_id=chat_id if chat_id is not None else -1, owner_name=creator,
                created_at=datetime.now(VIETNAM_TIME).isoformat(), scheduled_at=when.isoformat(),
                schedule_data=json.dumps(data, ensure_ascii=False)))
            self._set_validation_message('')
            self.job_dispatch_changed.emit(job_id, 'scheduled')
            self._append_log_line(f'Đã hẹn Job [{job_id}]: {schedule_text(when)}.', color='#60a5fa', job_id=job_id)
            return job_id
        except Exception as exc:
            self._set_validation_message(str(exc) if isinstance(exc, ValueError)
                else 'Không lưu được lịch job. Chưa khởi chạy hoặc mua mail.')
            return None

    def restore_scheduled_job(self, job):
        job_id = job['job_id']
        if job_id in self._session_jobs:
            return
        when = schedule_time(job['scheduled_at'])
        data = json.loads(job['schedule_data'])
        record = _MochiJobSessionRecord(job_id, job['code'], job['target_count'],
            customer_name=data.get('customer_name', ''), status='Hẹn giờ',
            creator_name=data.get('creator_name') or job['owner_name'],
            creator_chat_id=job['chat_id'] if job['chat_id'] > 0 else None)
        record.created_at = datetime.fromisoformat(job['created_at']).astimezone(VIETNAM_TIME)
        record.scheduled_at = when
        record.source_mail_batch = data.get('source_mail_batch')
        record.total_vnd = int(data.get('total_vnd') or 0)
        self._session_jobs[job_id] = record
        self._scheduled_jobs[job_id] = record
        self._selected_job_id = job_id
        self._render_jobs_table()
        self._update_action_buttons_state()
        self._update_job_queue_label()

    def _check_scheduled_jobs(self, now=None):
        if self._closing_jobs:
            return
        now = now or datetime.now(VIETNAM_TIME)
        due = [(jid, record) for jid, record in self._scheduled_jobs.items() if record.scheduled_at <= now]
        if not due:
            return
        for job_id, record in sorted(due, key=lambda pair: pair[1].scheduled_at):
            try:
                if self.scheduled_job_claim is None:
                    raise RuntimeError('Chưa sẵn sàng xác minh lịch job đã lưu.')
                claimed = self.scheduled_job_claim(job_id)
            except PermissionError as exc:
                self._scheduled_jobs.pop(job_id, None)
                self._on_job_error(str(exc), job_id)
                continue
            except Exception:
                self._set_validation_message('Chưa đọc/ghi được lịch job; đang giữ lịch để thử lại, chưa mua mail.')
                continue
            self._scheduled_jobs.pop(job_id, None)
            try:
                if not claimed:
                    record.status = 'Đã xử lý ở phiên khác'
                    continue
                keys = self.job_mail_keys_resolver(job_id) if self.job_mail_keys_resolver else None
                started = self._on_start_clicked(code=record.invite_code, points=record.target_points,
                    customer_name=record.customer_name, creator_name=record.creator_name,
                    creator_chat_id=record.creator_chat_id, _scheduled_record=record, mail_api_keys=keys)
                if not started and not record.is_terminal:
                    self._on_job_error(self.validation_label.text() or 'Không khởi chạy được lịch job.', job_id)
            except Exception as exc:
                self._on_job_error(str(exc), job_id)
        self._update_job_queue_label()
        self._render_jobs_table()
        self._update_action_buttons_state()

    def _on_job_limit_changed(self, value):
        self.config.job_concurrency = value
        self.settings.setValue('runtime/mochi_chat/job_concurrency', value)
        self._drain_job_queue()

    def _update_job_queue_label(self):
        text = (f'Đang xử lý: {len(self._active_job_ids)} / '
            f'{self.spin_job_concurrency.value()} job · Đang chờ: {len(self._pending_jobs)} job'
            f' · Hẹn giờ: {len(self._scheduled_jobs)} job')
        for name in ('lbl_job_queue', 'lbl_running_job_queue'):
            if hasattr(self, name):
                getattr(self, name).setText(text)

    def job_dispatch_text(self, job_id):
        if job_id in self._scheduled_jobs:
            return 'Hẹn giờ: ' + schedule_text(self._scheduled_jobs[job_id].scheduled_at)
        if job_id in self._pending_jobs:
            return f'Đang chờ (vị trí {list(self._pending_jobs).index(job_id) + 1})'
        if job_id in self._active_job_ids:
            return 'Đang xử lý'
        record = self._session_jobs.get(job_id)
        return record.status if record else 'Chưa có trạng thái'

    def _drain_job_queue(self):
        # Reserve a slot before QThread.start(): isRunning() alone races with startup.
        if self._closing_jobs or self._dispatching_jobs:
            return
        self._dispatching_jobs = True
        try:
            while self._pending_jobs and len(self._active_job_ids) < self.spin_job_concurrency.value():
                job_id = self._pending_jobs.popleft()
                worker = self._workers.get(job_id)
                if worker is None:
                    continue
                self._active_job_ids.add(job_id)
                self._session_jobs[job_id].status = 'Đang chạy'
                try:
                    worker.start()
                except Exception as exc:
                    self._on_job_error(str(exc), job_id)
                    if worker.isRunning():
                        worker.request_cancel()
                    else:
                        self._on_worker_stopped(job_id)
                    continue
                self.job_dispatch_changed.emit(job_id, 'running')
                self._append_log_line(f'🚀 Job [{job_id}] đang xử lý.', color='#10b981', job_id=job_id)
        finally:
            self._dispatching_jobs = False
            self._update_job_queue_label()
            self._render_jobs_table()
            self._update_action_buttons_state()

    def _update_action_buttons_state(self):
        # Nút Bắt đầu luôn mở để thêm các job độc lập bất cứ lúc nào
        self.btn_start.setEnabled(True)

        # Cập nhật nút Dừng
        running_workers = list(self._workers.values()) + list(self._scheduled_jobs.values())
        any_running = len(running_workers) > 0
        self.btn_stop.setEnabled(any_running)

        if self._selected_job_id and (self._selected_job_id in self._workers or self._selected_job_id in self._scheduled_jobs):
            self.btn_stop.setText(f"HỦY LỊCH [{self._selected_job_id[-4:]}]" if self._selected_job_id in self._scheduled_jobs
                else f"🛑 DỪNG [{self._selected_job_id[-4:]}]")
        elif any_running:
            self.btn_stop.setText(f"🛑 DỪNG ({len(running_workers)})")
        else:
            self.btn_stop.setText("🛑 DỪNG")

    def _on_stop_clicked(self):
        if self._selected_job_id and (self._selected_job_id in self._workers or self._selected_job_id in self._scheduled_jobs):
            self._stop_job(self._selected_job_id)
        else:
            # Dừng tất cả các job đang chạy
            running_jids = list(self._scheduled_jobs) + list(self._pending_jobs) + list(self._active_job_ids)
            for jid in running_jids:
                self._stop_job(jid)

    def _stop_job(self, job_id: str):
        worker = self._workers.get(job_id)
        rec = self._session_jobs.get(job_id)
        if rec and rec.is_terminal:
            return
        if job_id in self._scheduled_jobs:
            try:
                if self.scheduled_job_cancel is None:
                    raise RuntimeError('Chưa sẵn sàng lưu hủy lịch.')
                if not self.scheduled_job_cancel(job_id):
                    self._scheduled_jobs.pop(job_id)
                    rec.status = 'Đã xử lý ở phiên khác'
                    self._render_jobs_table()
                    self._update_job_queue_label()
                    return
            except Exception:
                self._set_validation_message('Chưa lưu được hủy lịch. Lịch và tiền tạm giữ vẫn được giữ.')
                return
            self._scheduled_jobs.pop(job_id)
            self._on_job_finished(MochiInviteJobSummary(job_id, rec.invite_code, rec.target_count,
                0, 0, 0, 0, 'cancelled', ()))
            self._update_job_queue_label()
        elif worker and job_id in self._pending_jobs:
            self._pending_jobs.remove(job_id)
            rec = self._session_jobs[job_id]
            self._on_job_finished(MochiInviteJobSummary(job_id, rec.invite_code, rec.target_count,
                0, 0, 0, 0, 'cancelled', ()))
            self._on_worker_stopped(job_id)
        elif worker and (job_id in self._active_job_ids or worker.isRunning()):
            self._append_log_line(f"⚠️ Đang gửi yêu cầu dừng job [{job_id}]...", color="#f59e0b", job_id=job_id)
            worker.request_cancel()
            rec = self._session_jobs.get(job_id)
            if rec:
                rec.status = "Đang dừng..."
                self._render_jobs_table()
        self._update_action_buttons_state()

    def _delete_job(self, job_id: str):
        worker = self._workers.get(job_id)
        if worker or job_id in self._scheduled_jobs:
            QMessageBox.warning(self, "Thông báo", "Job đang chạy hoặc đang chờ, vui lòng bấm 'Dừng' trước khi xóa khỏi danh sách.")
            return

        self._session_jobs.pop(job_id, None)
        self._job_logs.pop(job_id, None)
        if self._selected_job_id == job_id:
            self._selected_job_id = None
            self._lbl_log_filter_update()
            self._refresh_log_display()

        self._render_jobs_table()
        self._update_action_buttons_state()

    def _select_job(self, job_id: str):
        self._selected_job_id = job_id
        self._lbl_log_filter_update()
        self._refresh_log_display()
        self._update_action_buttons_state()

    def _show_all_logs(self):
        self._selected_job_id = None
        self._lbl_log_filter_update()
        self._refresh_log_display()
        self._update_action_buttons_state()

    def _lbl_log_filter_update(self):
        if self._selected_job_id:
            self.lbl_log_filter.setText(f"({self._selected_job_id})")
        else:
            self.lbl_log_filter.setText("(Tất cả jobs)")

    def _refresh_log_display(self):
        self.txt_log.clear()
        target_logs = self._job_logs.get(self._selected_job_id, []) if self._selected_job_id else self._all_logs
        for line_html in target_logs[-400:]:
            self.txt_log.append(line_html)
        sb = self.txt_log.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())

    def _handle_runner_log(self, evt: JobLogEvent, job_id: str):
        color = "#f8fafc"
        if evt.level == JobLogLevel.SUCCESS:
            color = "#10b981"
        elif evt.level == JobLogLevel.ERROR or evt.level == JobLogLevel.CRITICAL:
            color = "#ef4444"
        elif evt.level == JobLogLevel.WARNING:
            color = "#fbbf24"
        elif evt.level == JobLogLevel.INFO:
            color = "#60a5fa"

        now_str = datetime.now().strftime("%H:%M:%S")
        self._append_log_line(f"[{now_str}] [{job_id}] {evt.message}", color=color, job_id=job_id)

    def _handle_runner_progress(self, evt: JobProgressEvent):
        record = self._session_jobs.get(evt.job_id)
        if record:
            record.attempted_count = evt.attempted_count
            record.success_count = evt.success_count
            record.failure_count = evt.failure_count
            if evt.status == "running":
                record.status = "Đang chạy"
            self._render_jobs_table()
        self.job_progress.emit(evt)

    def _on_job_finished(self, summary: MochiInviteJobSummary):
        jid = summary.job_id
        self._update_action_buttons_state()

        record = self._session_jobs.get(jid)
        if record:
            record.success_count = max(record.success_count,summary.success_count)
            record.failure_count = max(record.failure_count,summary.failure_count)
            record.attempted_count = max(record.attempted_count,summary.attempted_count)
            if summary.status == "completed":
                record.status = "Hoàn thành"
            elif summary.status == "cancelled":
                record.status = "Đã hủy"
            elif summary.status == "invalid_code":
                record.status = "Mã không hợp lệ"
            elif summary.status == "invite_limit_reached":
                record.status = "Đạt giới hạn 24h"
            else:
                record.status = "Thất bại"
            self._render_jobs_table()
            self._save_job_history(record,summary.status)

        self._append_log_line(
            f"=== JOB [{jid}] KẾT THÚC: {summary.status.upper()} (Thành công: {summary.success_count}/{summary.target_count}) ===",
            color="#10b981" if summary.status == "completed" else "#f59e0b",
            job_id=jid,
        )

        self.job_finished.emit(summary)

    def _on_worker_stopped(self, job_id):
        self._active_job_ids.discard(job_id)
        worker = self._workers.pop(job_id, None)
        if worker:
            worker.deleteLater()
        self._update_action_buttons_state()
        self._drain_job_queue()

    def _on_job_error(self, err_msg: str, job_id: str):
        self._update_action_buttons_state()

        record = self._session_jobs.get(job_id)
        if record:
            record.status = "Thất bại"
            record.failure_message = err_msg
            self._render_jobs_table()
            self._save_job_history(record,'failed')

        self._append_log_line(f"🛑 Lỗi ngoại lệ job [{job_id}]: {err_msg}", color="#ef4444", job_id=job_id)
        self.job_failed.emit(err_msg, job_id)

    def _save_job_history(self,record,status):
        warning=''
        order_id=''
        if self.job_order_lookup is not None:
            try:
                order=self.job_order_lookup(record.job_id)
                order_id=order['order_id'] if order else ''
            except Exception:
                warning='Không đọc được mã đơn; lịch sử vẫn giữ mã job để đối chiếu.'
        try:
            created=record.created_at
            if created.tzinfo is None:
                created=created.replace(tzinfo=JOB_TIMEZONE)
            entry=JobHistoryEntry(record.job_id,order_id,record.creator_name,record.creator_chat_id,
                record.customer_name,record.invite_code,created.astimezone(JOB_TIMEZONE).isoformat(timespec='seconds'),
                datetime.now(JOB_TIMEZONE).isoformat(timespec='seconds'),status,record.target_count,
                record.attempted_count,record.success_count,record.failure_count,record.target_points,record.achieved_points)
            self.job_history_store.record(entry)
        except Exception as exc:
            warning='Chưa cập nhật được CSV lịch sử. Bấm Làm mới trong Lịch sử job để thử lại. '+str(exc)
            self._append_log_line(warning,color='#fbbf24',job_id=record.job_id)
        self.history_page.refresh(sync=False,warning=warning)

    def _render_jobs_table(self):
        self.table_jobs.setRowCount(len(self._session_jobs))
        for row, (jid, rec) in enumerate(self._session_jobs.items()):
            job_item = QTableWidgetItem(rec.customer_name or rec.job_id)
            job_item.setToolTip(f"{rec.job_id}\nKhách hàng: {rec.customer_name or '—'}\nTổng tiền: {getattr(rec, 'total_vnd', 0):,} VND")
            self.table_jobs.setItem(row, 0, job_item)
            creator = rec.creator_name
            if rec.creator_chat_id is not None:
                creator += f' ({rec.creator_chat_id})'
            creator_item = QTableWidgetItem(creator)
            creator_item.setToolTip(creator)
            creator_item.setData(Qt.ItemDataRole.UserRole, rec.creator_chat_id)
            self.table_jobs.setItem(row, 1, creator_item)
            self.table_jobs.setItem(row, 2, QTableWidgetItem(rec.invite_code))
            self.table_jobs.setItem(row, 3, QTableWidgetItem(rec.progress))
            self.table_jobs.setItem(row, 4, QTableWidgetItem(f"{rec.achieved_points:,} / {rec.target_points:,}"))

            item_status = QTableWidgetItem(rec.status)
            if rec.status == "Hoàn thành":
                item_status.setForeground(QColor("#10b981"))
            elif rec.status in ("Đang chạy", "Đang dừng...", "Đang chờ", "Hẹn giờ"):
                item_status.setForeground(QColor("#60a5fa"))
            elif rec.status in ("Thất bại", "Mã không hợp lệ"):
                item_status.setForeground(QColor("#ef4444"))
            elif rec.status == "Đã hủy":
                item_status.setForeground(QColor("#f59e0b"))
            self.table_jobs.setItem(row, 5, item_status)

            time_str = rec.created_at.strftime("%H:%M:%S")
            if jid in self._scheduled_jobs:
                time_str = rec.scheduled_at.strftime('%d/%m %H:%M')
            time_item = QTableWidgetItem(time_str)
            time_item.setToolTip(('Hẹn chạy: ' + schedule_text(rec.scheduled_at) + '\n' if hasattr(rec,'scheduled_at') else '')
                + 'Tạo lúc: ' + rec.created_at.strftime('%d/%m/%Y %H:%M:%S'))
            self.table_jobs.setItem(row, 6, time_item)

            # Action Buttons widget
            act_widget = QWidget()
            act_layout = QHBoxLayout(act_widget)
            act_layout.setContentsMargins(2, 2, 2, 2)
            act_layout.setSpacing(4)

            is_running = jid in self._workers or jid in self._scheduled_jobs
            if is_running:
                btn_stop = QPushButton("Hủy" if jid in self._scheduled_jobs else "Dừng", act_widget)
                btn_stop.setFixedHeight(24)
                btn_stop.setStyleSheet(
                    "QPushButton { background-color: #ef4444; color: white; border-radius: 4px; padding: 2px 8px; font-weight: 700; font-size: 11px; border: none; }"
                    "QPushButton:hover { background-color: #dc2626; }"
                )
                btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_stop.clicked.connect(lambda _, x=jid: self._stop_job(x))
                act_layout.addWidget(btn_stop)
            else:
                btn_del = QPushButton("Xóa", act_widget)
                btn_del.setFixedHeight(24)
                btn_del.setStyleSheet(
                    "QPushButton { background-color: #3a3a42; color: #cbd5e1; border-radius: 4px; padding: 2px 8px; font-weight: 600; font-size: 11px; border: none; }"
                    "QPushButton:hover { background-color: #4a4b55; color: #f8fafc; }"
                )
                btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
                btn_del.clicked.connect(lambda _, x=jid: self._delete_job(x))
                act_layout.addWidget(btn_del)

            btn_log = QPushButton("Log", act_widget)
            btn_log.setFixedHeight(24)
            btn_log.setStyleSheet(
                "QPushButton { background-color: #0284c7; color: white; border-radius: 4px; padding: 2px 8px; font-weight: 600; font-size: 11px; border: none; }"
                "QPushButton:hover { background-color: #0369a1; }"
            )
            btn_log.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_log.clicked.connect(lambda _, x=jid: self.show_job_logs(x))
            act_layout.addWidget(btn_log)

            self.table_jobs.setCellWidget(row, 7, act_widget)

    def _on_table_cell_clicked(self, row: int, col: int):
        jids = list(self._session_jobs.keys())
        if row < len(jids):
            jid = jids[row]
            if col != 7:  # Không ghi đè khi bấm vào nút thao tác
                self._select_job(jid)

    def _append_log_line(self, text: str, color: str = "#f1f5f9", job_id: Optional[str] = None):
        line_html = f"<span style='color: {color};'>{html.escape(text)}</span>"
        self._all_logs.append(line_html)
        if job_id:
            self._job_logs[job_id].append(line_html)

        if self._selected_job_id is None or self._selected_job_id == job_id:
            self.txt_log.append(line_html)
            sb = self.txt_log.verticalScrollBar()
            if sb:
                sb.setValue(sb.maximum())

    def _clear_logs(self):
        if self._selected_job_id:
            self._job_logs[self._selected_job_id].clear()
        else:
            self._all_logs.clear()
            self._job_logs.clear()
        self.txt_log.clear()

    def shutdown_workers(self):
        self._closing_jobs = True
        self._schedule_timer.stop()
        lookup_busy=self.account_lookup_page.shutdown_workers()
        pending_save = False
        for job_id in list(self._pending_jobs):
            record = self._session_jobs[job_id]
            if hasattr(record,'scheduled_at'):
                try:
                    if self.scheduled_job_defer and self.scheduled_job_defer(job_id):
                        self._pending_jobs.remove(job_id)
                        worker = self._workers.pop(job_id)
                        worker.deleteLater()
                        record.status = 'Hẹn giờ'
                        self._scheduled_jobs[job_id] = record
                        continue
                except Exception:
                    pending_save = True
                    self._set_validation_message('Chưa lưu được lịch đang chờ; sẽ thử lưu lại trước khi đóng tool.')
                    continue
            self._stop_job(job_id)
        workers = list(self._workers.values()) + list(getattr(self, '_background_workers', ()))
        for worker in workers:
            if worker.isRunning():
                worker.request_cancel()
        return lookup_busy or pending_save or any(worker.isRunning() for worker in workers)

    def closeEvent(self, event):
        if self.shutdown_workers():
            event.ignore()
            QTimer.singleShot(100, self.close)
        else:
            super().closeEvent(event)
