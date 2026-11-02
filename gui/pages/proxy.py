"""Proxy Pool with static/rotating proxies and one-proxy-per-line TXT files.

Dedicated workspace page for inspecting, importing, rotating, and managing
proxies formatted as endpoints with optional `|key xoay` for Proxy.vn rotation.
Lists persist locally and can be imported, saved and exported as UTF-8 TXT.
"""

import asyncio
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

try:
    import PySide6.QtSvg  # Enables crisp SVG rendering
except Exception:
    pass

from PySide6.QtCore import QEvent, QIODevice, QObject, QPoint, QSaveFile, QSettings, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from curl_cffi.requests import AsyncSession
from contextlib import nullcontext

from core.proxy.models import ProxyModel
from core.proxy.checker import probe_proxy_connection
from core.proxy.broker import proxy_activity, proxy_maintenance, update_proxy_endpoint
from core.proxy.protocol import ProxyTransportError
from core.proxy.rotators import (
    ProxyVnProvider,
    make_rotating_provider,
    provider_shared_identity,
    rotating_provider_id,
    rotation_key_reference,
    RotationResult,
    is_cooldown_or_wait_message,
    is_real_proxy_error,
)

logger = logging.getLogger("gui.pages.proxy")


from core.proxy.models import ProxyModel
from gui.background import AsyncTaskThread, start_background


class _ProxyRotateThread(AsyncTaskThread):
    progress_signal = Signal(str, float, str)
    finished_signal = Signal(str, object, int)

    def __init__(self, pid, key_xoay, endpoint, parent=None):
        async def rotate():
            provider = make_rotating_provider(key_xoay, current_endpoint=endpoint)
            started = time.monotonic()
            try:
                with proxy_maintenance(key_xoay):
                    result = await provider.rotate_and_wait(timeout=300.0 if provider.provider_id == 'muaproxyxoay' else 180.0,
                        on_progress=lambda remaining, message: self.progress_signal.emit(pid, remaining, message))
                    if result.success and result.proxy and not result.is_cooling:
                        self.progress_signal.emit(pid, 0.0, 'Kiểm tra IP qua proxy...')
                        health = await probe_proxy_connection(result.proxy)
                        if not health.success:
                            result = RotationResult(success=False, proxy=result.proxy,
                                error=f'{health.stage}: {health.error}', info=result.info)
            except ProxyTransportError:
                result = RotationResult(success=False, is_cooling=True, wait_seconds=2.0,
                    error='Proxy đang được job sử dụng.')
            return result, int((time.monotonic() - started) * 1000)
        super().__init__(rotate, parent)
        self.result.connect(lambda pair: self.finished_signal.emit(pid, pair[0], pair[1]))
        self.failed.connect(lambda error: self.finished_signal.emit(pid, RotationResult(success=False, error=error), 0))


def _get_asset_icon(name: str, size: int = 16) -> QIcon:
    """Helper loads SVG/PNG assets as crisp QIcons via QSvgRenderer."""
    search_dirs = [
        os.path.join(os.path.dirname(__file__), "..", "assets"),
        os.path.join(os.path.dirname(__file__), "..", "..", "gui", "assets"),
        os.path.join(os.path.abspath("."), "gui", "assets"),
        os.path.join(os.path.abspath("."), "src", "gui", "assets"),
    ]
    for d in search_dirs:
        path = os.path.join(d, name)
        if os.path.exists(path):
            if name.endswith(".svg"):
                try:
                    from PySide6.QtSvg import QSvgRenderer
                    renderer = QSvgRenderer(path)
                    if renderer.isValid():
                        pix = QPixmap(size, size)
                        pix.fill(Qt.GlobalColor.transparent)
                        p = QPainter(pix)
                        renderer.render(p)
                        p.end()
                        return QIcon(pix)
                except Exception:
                    pass
            return QIcon(path)
    return QIcon()


class _SparklineWaveWidget(QWidget):
    """Mini glowing wave curve graph."""

    def __init__(self, color_hex: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.color = QColor(color_hex)
        self.setFixedSize(70, 32)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        path = QPainterPath()

        # Draw smooth wave
        path.moveTo(2, h * 0.75)
        path.cubicTo(w * 0.25, h * 0.2, w * 0.45, h * 0.85, w * 0.7, h * 0.3)
        path.cubicTo(w * 0.85, h * 0.1, w * 0.95, h * 0.5, w - 2, h * 0.4)

        pen = QPen(self.color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()


class _StatCard(QFrame):
    """Metric Summary Card."""

    def __init__(
        self,
        title: str,
        initial_val: str,
        subtext: str,
        accent_hex: str,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.setStyleSheet(f"""
            QFrame#statCard {{
                background-color: #0d1322;
                border: 1px solid #1e293b;
                border-radius: 8px;
                padding: 8px 12px;
            }}
            QFrame#statCard:hover {{
                border-color: {accent_hex};
            }}
        """)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(2)

        self.lbl_title = QLabel(title, self)
        self.lbl_title.setStyleSheet("font-size: 11px; font-weight: 600; color: #94a3b8;")

        self.lbl_val = QLabel(initial_val, self)
        self.lbl_val.setStyleSheet(f"font-size: 24px; font-weight: 700; color: #f8fafc;")

        self.lbl_sub = QLabel(subtext, self)
        self.lbl_sub.setStyleSheet("font-size: 11px; color: #64748b;")

        left.addWidget(self.lbl_title)
        left.addWidget(self.lbl_val)
        left.addWidget(self.lbl_sub)
        layout.addLayout(left)

        layout.addStretch()
        self.wave = _SparklineWaveWidget(accent_hex, self)
        layout.addWidget(self.wave, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_value(self, val: int):
        self.lbl_val.setText(str(val))


class _ProxyImportDialog(QDialog):
    """Paste endpoints, optionally followed by a Proxy.vn rotation key."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Dán danh sách proxy")
        self.resize(620, 500)
        self.setStyleSheet("""
            QDialog {
                background-color: #0b0f19;
                color: #f8fafc;
            }
            QLabel {
                color: #e2e8f0;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Dán danh sách proxy", self)
        title.setStyleSheet("font-size: 16px; font-weight: 700; color: #38bdf8;")
        layout.addWidget(title)

        desc = QLabel(
            "Nhập mỗi proxy một dòng theo định dạng chuẩn:\n"
            "<b>ip:port</b>, <b>ip:port:user:pass</b> hoặc URL HTTP/SOCKS.<br>"
            "Proxy xoay thêm <b>|key_xoay</b> ở cuối dòng.<br>"
            "Ví dụ: <code>127.0.0.1:8080</code><br>"
            "<code>127.0.0.1:8081|key_xoay</code>",
            self,
        )
        desc.setStyleSheet("font-size: 12px; color: #94a3b8; line-height: 1.4;")
        layout.addWidget(desc)
        self.combo_provider = QComboBox(self)
        self.combo_provider.addItem('Proxy.vn', 'proxyvn')
        self.combo_provider.addItem('muaproxyxoay.net', 'muaproxyxoay')
        self.combo_provider.setStyleSheet('QComboBox {background: #1e293b; color: #f1f5f9; border: 1px solid #334155; padding: 6px; border-radius: 5px;}'
            'QComboBox QAbstractItemView {background: #0f172a; color: #f1f5f9; selection-background-color: #2563eb;}')
        selected = parent.combo_import_provider.currentData() if parent and hasattr(parent, 'combo_import_provider') else 'proxyvn'
        self.combo_provider.setCurrentIndex(max(0, self.combo_provider.findData(selected)))
        layout.addWidget(QLabel('Nhà cung cấp cho key nhập bên dưới:', self))
        layout.addWidget(self.combo_provider)

        self.txt_input = QTextEdit(self)
        def update_format():
            endpoint = ('192.0.2.10:32001:USER:PASS' if self.combo_provider.currentData() == 'muaproxyxoay'
                else '192.0.2.10:32001')
            self.txt_input.setPlaceholderText(endpoint + '|KEY_XOAY_1\n' + endpoint + '|KEY_XOAY_2')
        self.combo_provider.currentIndexChanged.connect(update_format)
        update_format()
        self.txt_input.setFont(QFont("Consolas", 11))
        self.txt_input.setStyleSheet("""
            QTextEdit {
                background-color: #090d16;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 10px;
            }
            QTextEdit:focus {
                border-color: #38bdf8;
            }
        """)
        layout.addWidget(self.txt_input)

        self.chk_auto_check = QCheckBox("Tự động kiểm tra / xoay thử IP ngay sau khi thêm", self)
        self.chk_auto_check.setChecked(True)
        self.chk_auto_check.setStyleSheet("color: #cbd5e1; font-weight: 600;")
        layout.addWidget(self.chk_auto_check)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self.btn_cancel = QPushButton("Hủy", self)
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #94a3b8;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #334155;
                color: #f8fafc;
            }
        """)
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_ok = QPushButton("📥 Thêm vào danh sách", self)
        self.btn_ok.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: white;
                border-radius: 6px;
                padding: 8px 20px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)
        self.btn_ok.clicked.connect(self.accept)

        btn_row.addWidget(self.btn_cancel)
        btn_row.addWidget(self.btn_ok)
        layout.addLayout(btn_row)

    def get_lines(self) -> List[str]:
        raw = self.txt_input.toPlainText().strip()
        lines = []
        for line in raw.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                lines.append(line)
        return lines

    def should_auto_check(self) -> bool:
        return self.chk_auto_check.isChecked()


class ProxyPage(QWidget):
    """Manage static and rotating proxy endpoints, including TXT file lists."""

    proxies_updated = Signal()

    def __init__(self, settings: Optional[QSettings] = None, parent: Optional[QWidget] = None,
            *, settings_prefix='proxy', title='Proxy', subtitle='Quản lý proxy độc quyền cho từng account...',
            usage_hint='Số account dùng chung mỗi proxy được đặt trong tab Cấu hình.'):
        super().__init__(parent)
        self.settings = settings or QSettings("Hermit Coder", "TTM Tool")
        self.settings_prefix=settings_prefix
        self.page_title,self.page_subtitle=title,subtitle
        self.usage_hint=usage_hint

        # In-memory proxy items:
        # dict: id -> dict(id, endpoint, key_xoay, status, provider, speed, is_failed, error_reason, error_time, in_use)
        self._proxies: Dict[str, Dict[str, Any]] = {}
        self._is_checking_all: bool = False
        self._active_rotations: Dict[str, _ProxyRotateThread] = {}
        self._proxy_file_path = str(self.settings.value(f"{self.settings_prefix}/txt_path", "") or "")

        self._init_ui()
        self._load_from_settings()
        self._runtime_timer = QTimer(self)
        self._runtime_timer.setInterval(1000)
        self._runtime_timer.timeout.connect(self._refresh_runtime_status)
        self._runtime_timer.start()

    def _refresh_runtime_status(self):
        changed = False
        for pid, item in list(self._proxies.items()):
            if not item.get('key_xoay'):
                # Static endpoints have distinct broker identities, without a rotation key.
                continue
            users, rotating, model = proxy_activity(self._item_key_reference(item))
            before = (item.get('in_use'), item.get('status'), item.get('endpoint'), item.get('speed'))
            previously_used = item.get('in_use', False)
            item['in_use'] = users > 0
            if model and not item.get('is_failed'):
                item['endpoint'] = model.to_url()
            if users:
                item['status'] = 'WAITING' if rotating else 'RUNNING'
            elif previously_used:
                item['status'] = 'READY'
            elif item.get('status') == 'COOLING' and not rotating:
                remaining = item.get('cooling_until', 0) - time.time()
                if remaining > 0:
                    item['speed'] = f'Chờ {remaining:.0f}s'
                elif pid not in self._active_rotations:
                    item['status'] = 'READY'
                    item['speed'] = '-'
            changed |= before != (item.get('in_use'), item.get('status'), item.get('endpoint'), item.get('speed'))
        if changed:
            self._update_metrics_and_render()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 16, 20, 16)
        main_layout.setSpacing(14)

        # 1. Header with Title and Search Bar
        head_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)

        lbl_title = QLabel(self.page_title, self)
        lbl_title.setStyleSheet("font-size: 22px; font-weight: 700; color: #f8fafc;")

        lbl_sub = QLabel(self.page_subtitle, self)
        lbl_sub.setWordWrap(True)
        lbl_sub.setStyleSheet("font-size: 12px; color: #94a3b8;")

        title_box.addWidget(lbl_title)
        title_box.addWidget(lbl_sub)
        head_row.addLayout(title_box,1)

        head_row.addStretch()

        self.input_search = QLineEdit(self)
        self.input_search.setPlaceholderText("Tìm kiếm proxy...")
        self.input_search.setFixedWidth(240)
        s_ico = _get_asset_icon("search_muted.svg", 14)
        if not s_ico.isNull():
            self.input_search.addAction(s_ico, QLineEdit.ActionPosition.LeadingPosition)
        self.input_search.setStyleSheet("""
            QLineEdit {
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #38bdf8;
            }
        """)
        self.input_search.textChanged.connect(self._apply_search_filter)
        head_row.addWidget(self.input_search)

        main_layout.addLayout(head_row)

        # 2. Top 4 Metric Cards
        cards_row = QHBoxLayout()
        cards_row.setSpacing(12)

        self.card_active = _StatCard("Hoạt động", "0", "proxy trong danh sách", "#10b981", self)
        self.card_using = _StatCard("Đang dùng", "0", "đang nhận job / checking", "#3b82f6", self)
        self.card_cooling = _StatCard("Đang nghỉ", "0", "đang nghỉ / chờ xoay", "#a855f7", self)
        self.card_failed = _StatCard("Đã lỗi", "0", "proxy bị loại", "#ef4444", self)

        cards_row.addWidget(self.card_active)
        cards_row.addWidget(self.card_using)
        cards_row.addWidget(self.card_cooling)
        cards_row.addWidget(self.card_failed)

        main_layout.addLayout(cards_row)

        # Splitter between Active Proxies and Failed Proxies
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #1e293b;
                height: 4px;
            }
        """)

        # 3. Section 1: Active Proxies Box
        w_active = QWidget(self)
        l_active = QVBoxLayout(w_active)
        l_active.setContentsMargins(0, 4, 0, 4)
        l_active.setSpacing(8)

        sec1_header = QHBoxLayout()
        sec1_title_box = QVBoxLayout()
        sec1_title_box.setSpacing(2)

        lbl_sec1 = QLabel("Trạng thái proxy", self)
        lbl_sec1.setStyleSheet("font-size: 14px; font-weight: 700; color: #f1f5f9;")

        lbl_sec1_sub = QLabel(self.usage_hint, self)
        lbl_sec1_sub.setStyleSheet("font-size: 11px; color: #64748b;")

        sec1_title_box.addWidget(lbl_sec1)
        sec1_title_box.addWidget(lbl_sec1_sub)
        sec1_header.addLayout(sec1_title_box)

        # Action Buttons on Left
        self.btn_import = QPushButton("Dán proxy", self)
        self.btn_import.setIcon(_get_asset_icon("import_white.svg", 14))
        self.btn_import.setIconSize(QSize(14, 14))
        self.btn_import.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: white;
                font-weight: 600;
                font-size: 12px;
                border-radius: 6px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)
        self.btn_import.clicked.connect(self._on_import_clicked)

        self.btn_export = QPushButton("Xuất TXT…", self)
        self.btn_export.setIcon(_get_asset_icon("export_blue.svg", 14))
        self.btn_export.setIconSize(QSize(14, 14))
        self.btn_export.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #cbd5e1;
                font-weight: 600;
                font-size: 12px;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #334155;
            }
        """)
        self.btn_export.clicked.connect(self._on_export_clicked)

        self.btn_free_proxy = QPushButton("Lấy proxy miễn phí", self)
        self.btn_free_proxy.setIcon(_get_asset_icon("info_muted.svg", 14))
        self.btn_free_proxy.setIconSize(QSize(14, 14))
        self.btn_free_proxy.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #38bdf8;
                font-weight: 600;
                font-size: 12px;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 14px;
            }
            QPushButton:hover {
                background-color: #334155;
            }
        """)
        self.btn_free_proxy.clicked.connect(self._on_free_proxy_clicked)

        sec1_header.addStretch()

        # Action Buttons on Right
        self.btn_check_all_active = QPushButton("Check all", self)
        self.btn_check_all_active.setIcon(_get_asset_icon("tick_green.svg", 13))
        self.btn_check_all_active.setIconSize(QSize(13, 13))
        self.btn_check_all_active.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                border: 1px solid #334155;
                color: #cbd5e1;
                font-weight: 600;
                font-size: 11px;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #334155;
            }
        """)
        self.btn_check_all_active.clicked.connect(self._check_all_active_proxies)

        self.btn_delete_all_active = QPushButton("Delete All", self)
        self.btn_delete_all_active.setIcon(_get_asset_icon("trash_red.svg", 13))
        self.btn_delete_all_active.setIconSize(QSize(13, 13))
        self.btn_delete_all_active.setStyleSheet("""
            QPushButton {
                background-color: #450a0a;
                border: 1px solid #991b1b;
                color: #fca5a5;
                font-weight: 600;
                font-size: 11px;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #7f1d1d;
            }
        """)
        self.btn_delete_all_active.clicked.connect(self._delete_all_active)

        sec1_header.addWidget(self.btn_check_all_active)
        sec1_header.addWidget(self.btn_delete_all_active)
        l_active.addLayout(sec1_header)

        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel('Nhà cung cấp khi nhập:', self))
        self.combo_import_provider = QComboBox(self)
        self.combo_import_provider.addItem('Proxy.vn', 'proxyvn')
        self.combo_import_provider.addItem('muaproxyxoay.net', 'muaproxyxoay')
        selected = str(self.settings.value(f'{self.settings_prefix}/import_provider', 'proxyvn'))
        self.combo_import_provider.setCurrentIndex(max(0, self.combo_import_provider.findData(selected)))
        self.combo_import_provider.setStyleSheet('QComboBox {background: #1e293b; color: #e2e8f0; border: 1px solid #334155; padding: 5px 10px; border-radius: 5px;}'
            'QComboBox QAbstractItemView {background: #0f172a; color: #f1f5f9; selection-background-color: #2563eb;}')
        self.combo_import_provider.currentIndexChanged.connect(lambda: self.settings.setValue(
            f'{self.settings_prefix}/import_provider', self.combo_import_provider.currentData()))
        provider_row.addWidget(self.combo_import_provider)
        format_hint = QLabel('Proxy.vn: ip:port|key_xoay · muaproxyxoay.net: ip:port:user:pass|key_xoay', self)
        format_hint.setWordWrap(True)
        format_hint.setStyleSheet('color: #94a3b8; font-size: 11px;')
        provider_row.addWidget(format_hint, 1)
        l_active.addLayout(provider_row)

        file_actions = QHBoxLayout()
        self.btn_import_file = QPushButton("Nhập TXT…", self)
        self.btn_import_file.setIcon(_get_asset_icon("import_white.svg", 14))
        self.btn_import_file.setStyleSheet(self.btn_import.styleSheet())
        self.btn_import_file.clicked.connect(self._on_import_file_clicked)
        self.btn_import_file.setToolTip("Nhập file TXT, mỗi dòng một proxy. Thêm vào danh sách hiện có, bỏ qua proxy trùng.")
        self.btn_save_file = QPushButton("Lưu TXT", self)
        self.btn_save_file.setIcon(_get_asset_icon("export_blue.svg", 14))
        self.btn_save_file.setStyleSheet(self.btn_export.styleSheet())
        self.btn_save_file.clicked.connect(self._on_save_file_clicked)
        self.btn_save_file.setToolTip("Lưu toàn bộ danh sách vào file đang mở, bao gồm proxy lỗi. Lần đầu sẽ chọn nơi lưu.")
        self.btn_export.setToolTip("Xuất toàn bộ danh sách thành bản sao TXT; không đổi file đang mở.")
        for button in (self.btn_import_file, self.btn_save_file, self.btn_export,
                self.btn_import, self.btn_free_proxy):
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            file_actions.addWidget(button)
        file_actions.addStretch()
        l_active.addLayout(file_actions)
        self.lbl_proxy_file = QLabel(self)
        self.lbl_proxy_file.setWordWrap(True)
        self.lbl_proxy_file.setStyleSheet("color: #94a3b8; font-size: 11px;")
        l_active.addWidget(self.lbl_proxy_file)
        self._update_proxy_file_label()

        # Active Table
        self.tbl_active = QTableWidget(self)
        self.tbl_active.setColumnCount(5)
        self.tbl_active.setHorizontalHeaderLabels(["Proxy", "Trạng thái", "Loại - Provider", "Tốc độ", "Thao tác"])
        self.tbl_active.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_active.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_active.verticalHeader().setVisible(False)
        self.tbl_active.setShowGrid(False)
        self.tbl_active.setStyleSheet("""
            QTableWidget {
                background-color: #0b0f19;
                color: #f1f5f9;
                border: 1px solid #1e293b;
                border-radius: 8px;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #94a3b8;
                font-weight: 600;
                font-size: 12px;
                border: none;
                border-bottom: 1px solid #1e293b;
                padding: 8px;
            }
            QTableWidget::item {
                padding: 4px;
                border-bottom: 1px solid #151d2f;
            }
        """)

        header1 = self.tbl_active.horizontalHeader()
        header1.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header1.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header1.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header1.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        header1.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.tbl_active.setColumnWidth(1, 130)
        self.tbl_active.setColumnWidth(2, 170)
        self.tbl_active.setColumnWidth(3, 110)
        self.tbl_active.setColumnWidth(4, 130)

        l_active.addWidget(self.tbl_active)

        # Pagination row Active (matching screenshot: info, < 1 >, page size)
        row_page_active = QHBoxLayout()
        row_page_active.setContentsMargins(4, 2, 4, 2)
        self.lbl_active_count = QLabel("Hiển thị 0 của 0 proxy", self)
        self.lbl_active_count.setStyleSheet("font-size: 11px; color: #64748b;")
        row_page_active.addWidget(self.lbl_active_count)
        row_page_active.addStretch()

        btn_prev1 = QPushButton(self)
        btn_prev1.setIcon(_get_asset_icon("chevron_left.svg", 12))
        btn_prev1.setIconSize(QSize(12, 12))
        btn_prev1.setFixedSize(26, 24)
        btn_prev1.setStyleSheet("background-color: #1e293b; color: #64748b; border: 1px solid #334155; border-radius: 4px;")
        btn_prev1.setEnabled(False)

        lbl_p1 = QLabel("1", self)
        lbl_p1.setFixedSize(26, 24)
        lbl_p1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_p1.setStyleSheet("background-color: #0284c7; color: white; font-weight: 700; border-radius: 4px; font-size: 11px;")

        btn_next1 = QPushButton(self)
        btn_next1.setIcon(_get_asset_icon("chevron_right.svg", 12))
        btn_next1.setIconSize(QSize(12, 12))
        btn_next1.setFixedSize(26, 24)
        btn_next1.setStyleSheet("background-color: #1e293b; color: #64748b; border: 1px solid #334155; border-radius: 4px;")
        btn_next1.setEnabled(False)

        row_page_active.addWidget(btn_prev1)
        row_page_active.addWidget(lbl_p1)
        row_page_active.addWidget(btn_next1)
        row_page_active.addStretch()

        combo_ps1 = QComboBox(self)
        combo_ps1.addItems(["Hiển thị 15/trang", "Hiển thị 30/trang", "Hiển thị 50/trang"])
        combo_ps1.setStyleSheet("background-color: #0f172a; color: #94a3b8; border: 1px solid #334155; border-radius: 4px; padding: 2px 6px; font-size: 11px;")
        row_page_active.addWidget(combo_ps1)

        l_active.addLayout(row_page_active)

        splitter.addWidget(w_active)

        # 4. Section 2: Failed Proxies Box
        w_failed = QWidget(self)
        l_failed = QVBoxLayout(w_failed)
        l_failed.setContentsMargins(0, 4, 0, 4)
        l_failed.setSpacing(8)

        sec2_header = QHBoxLayout()
        lbl_sec2 = QLabel("Proxy lỗi", self)
        lbl_sec2.setStyleSheet("font-size: 14px; font-weight: 700; color: #f87171;")
        sec2_header.addWidget(lbl_sec2)
        sec2_header.addStretch()

        self.btn_check_all_failed = QPushButton("Check all", self)
        self.btn_check_all_failed.setIcon(_get_asset_icon("tick_green.svg", 13))
        self.btn_check_all_failed.setIconSize(QSize(13, 13))
        self.btn_check_all_failed.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                border: 1px solid #334155;
                color: #cbd5e1;
                font-weight: 600;
                font-size: 11px;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #334155;
            }
        """)
        self.btn_check_all_failed.clicked.connect(self._check_all_failed_proxies)

        self.btn_delete_all_failed = QPushButton("Delete All", self)
        self.btn_delete_all_failed.setIcon(_get_asset_icon("trash_red.svg", 13))
        self.btn_delete_all_failed.setIconSize(QSize(13, 13))
        self.btn_delete_all_failed.setStyleSheet("""
            QPushButton {
                background-color: #450a0a;
                border: 1px solid #991b1b;
                color: #fca5a5;
                font-weight: 600;
                font-size: 11px;
                border-radius: 6px;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #7f1d1d;
            }
        """)
        self.btn_delete_all_failed.clicked.connect(self._delete_all_failed)

        sec2_header.addWidget(self.btn_check_all_failed)
        sec2_header.addWidget(self.btn_delete_all_failed)
        l_failed.addLayout(sec2_header)

        # Failed Table
        self.tbl_failed = QTableWidget(self)
        self.tbl_failed.setColumnCount(4)
        self.tbl_failed.setHorizontalHeaderLabels(["Proxy", "Lý do lỗi", "Thời gian lỗi", "Thao tác"])
        self.tbl_failed.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_failed.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_failed.verticalHeader().setVisible(False)
        self.tbl_failed.setShowGrid(False)
        self.tbl_failed.setStyleSheet("""
            QTableWidget {
                background-color: #0b0f19;
                color: #f1f5f9;
                border: 1px solid #1e293b;
                border-radius: 8px;
            }
            QHeaderView::section {
                background-color: #0f172a;
                color: #f87171;
                font-weight: 600;
                font-size: 12px;
                border: none;
                border-bottom: 1px solid #1e293b;
                padding: 8px;
            }
            QTableWidget::item {
                padding: 4px;
                border-bottom: 1px solid #151d2f;
            }
        """)

        header2 = self.tbl_failed.horizontalHeader()
        header2.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header2.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header2.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header2.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.tbl_failed.setColumnWidth(0, 240)
        self.tbl_failed.setColumnWidth(2, 170)
        self.tbl_failed.setColumnWidth(3, 130)

        l_failed.addWidget(self.tbl_failed)

        # Pagination row Failed (matching screenshot: info, < 1 >, page size)
        row_page_failed = QHBoxLayout()
        row_page_failed.setContentsMargins(4, 2, 4, 2)
        self.lbl_failed_count = QLabel("Hiển thị 0 của 0 proxy lỗi", self)
        self.lbl_failed_count.setStyleSheet("font-size: 11px; color: #64748b;")
        row_page_failed.addWidget(self.lbl_failed_count)
        row_page_failed.addStretch()

        btn_prev2 = QPushButton(self)
        btn_prev2.setIcon(_get_asset_icon("chevron_left.svg", 12))
        btn_prev2.setIconSize(QSize(12, 12))
        btn_prev2.setFixedSize(26, 24)
        btn_prev2.setStyleSheet("background-color: #1e293b; color: #64748b; border: 1px solid #334155; border-radius: 4px;")
        btn_prev2.setEnabled(False)

        lbl_p2 = QLabel("1", self)
        lbl_p2.setFixedSize(26, 24)
        lbl_p2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_p2.setStyleSheet("background-color: #0284c7; color: white; font-weight: 700; border-radius: 4px; font-size: 11px;")

        btn_next2 = QPushButton(self)
        btn_next2.setIcon(_get_asset_icon("chevron_right.svg", 12))
        btn_next2.setIconSize(QSize(12, 12))
        btn_next2.setFixedSize(26, 24)
        btn_next2.setStyleSheet("background-color: #1e293b; color: #64748b; border: 1px solid #334155; border-radius: 4px;")
        btn_next2.setEnabled(False)

        row_page_failed.addWidget(btn_prev2)
        row_page_failed.addWidget(lbl_p2)
        row_page_failed.addWidget(btn_next2)
        row_page_failed.addStretch()

        combo_ps2 = QComboBox(self)
        combo_ps2.addItems(["Hiển thị 15/trang", "Hiển thị 30/trang", "Hiển thị 50/trang"])
        combo_ps2.setStyleSheet("background-color: #0f172a; color: #94a3b8; border: 1px solid #334155; border-radius: 4px; padding: 2px 6px; font-size: 11px;")
        row_page_failed.addWidget(combo_ps2)

        l_failed.addLayout(row_page_failed)

        splitter.addWidget(w_failed)
        splitter.setSizes([380, 240])

        main_layout.addWidget(splitter)

    # -------------------------------------------------------------
    # Parsing, Persistence & Rendering
    # -------------------------------------------------------------
    def _parse_proxy_line(self, line: str, provider_id=None) -> Optional[Dict[str, Any]]:
        """Accept static endpoints/URLs or an endpoint followed by |rotation-key."""
        raw = line.strip().lstrip('\ufeff').strip()
        if not raw:
            return None

        if '|' in raw:
            endpoint, key_xoay = (part.strip() for part in raw.split('|', 1))
            if not key_xoay:
                return None
        else:
            endpoint, key_xoay = raw, ''
        try:
            ProxyModel.from_string(endpoint)
            selected = provider_id or self.combo_import_provider.currentData()
            selected = rotating_provider_id(key_xoay, selected)
            if key_xoay:
                # Validate a provider URL/key without making a request.
                make_rotating_provider(key_xoay, provider_id=selected)
        except (ValueError, TypeError):
            return None

        pid = f"px_{uuid.uuid4().hex[:8]}"
        return {
            "id": pid,
            "endpoint": endpoint,
            "key_xoay": key_xoay,
            "status": "READY",
            "provider": ("Xoay - muaproxyxoay.net" if selected == 'muaproxyxoay' else "Xoay - proxy.vn") if key_xoay else "Normal - Proxy thường",
            "provider_id": selected if key_xoay else 'static',
            "speed": "-",
            "is_failed": False,
            "error_reason": "",
            "error_time": "",
            "in_use": False,
            "cooling_until": 0.0,
        }

    def _item_key_reference(self, item):
        return rotation_key_reference(item.get('key_xoay', ''), item.get('provider_id', 'proxyvn'))

    def _proxy_identity(self, item):
        """Normalize equivalent inputs without merging different static credentials."""
        key = item.get('key_xoay', '')
        if key:
            return ('rotation', provider_shared_identity(make_rotating_provider(self._item_key_reference(item))))
        return ('static', ProxyModel.from_string(item['endpoint']).to_url())

    def _save_to_settings(self):
        try:
            data_str = json.dumps(list(self._proxies.values()), ensure_ascii=False)
            self.settings.setValue(f"{self.settings_prefix}/managed_list", data_str)
        except Exception as e:
            logger.warning("Lỗi lưu proxy vào QSettings: %s", e)
        self._update_metrics_and_render()
        self.proxies_updated.emit()

    def _load_from_settings(self):
        raw_str = str(self.settings.value(f"{self.settings_prefix}/managed_list", "") or "")
        if raw_str:
            try:
                items = json.loads(raw_str)
                if not isinstance(items, list):
                    items = []
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    endpoint = str(it.get('endpoint', '') or '')
                    key = str(it.get('key_xoay', '') or '')
                    source = it.get('provider_id') or ('muaproxyxoay' if 'muaproxyxoay.net' in str(it.get('provider', '')) else 'proxyvn')
                    defaults = self._parse_proxy_line(f'{endpoint}|{key}' if key else endpoint, provider_id=source)
                    if defaults is None:
                        continue
                    defaults.update(it)
                    it = defaults
                    pid = it.get("id") or f"px_{uuid.uuid4().hex[:8]}"
                    it["id"] = pid
                    it['in_use'] = False
                    if it.get('status') in ('RUNNING', 'WAITING'):
                        it['status'] = 'READY'
                    # Tự động khôi phục nếu proxy bị nhầm là lỗi do đang chờ xoay IP
                    if it.get("is_failed") and is_cooldown_or_wait_message(it.get("error_reason", "")):
                        it["is_failed"] = False
                        it["status"] = "COOLING"
                        it["error_reason"] = ""
                    self._proxies[pid] = it
            except Exception as e:
                logger.warning("Lỗi đọc proxy từ QSettings: %s", e)
        self._update_metrics_and_render()

    def _update_metrics_and_render(self):
        active_items = [p for p in self._proxies.values() if not p["is_failed"]]
        failed_items = [p for p in self._proxies.values() if p["is_failed"]]

        using_count = sum(1 for p in active_items if p.get("in_use", False))
        cooling_count = sum(1 for p in active_items if p.get("status") in ("COOLING", "WAITING") or p.get("cooling_until", 0) > time.time())

        self.card_active.set_value(len(active_items))
        self.card_using.set_value(using_count)
        self.card_cooling.set_value(cooling_count)
        self.card_failed.set_value(len(failed_items))

        self._render_active_table(active_items)
        self._render_failed_table(failed_items)

    def _apply_search_filter(self):
        self._update_metrics_and_render()

    def _matches_search(self, item: Dict[str, Any], query: str) -> bool:
        if not query:
            return True
        q = query.lower()
        return (
            q in str(item.get("endpoint", "")).lower()
            or q in str(item.get("key_xoay", "")).lower()
            or q in str(item.get("status", "")).lower()
            or q in str(item.get("error_reason", "")).lower()
        )

    def _render_active_table(self, items: List[Dict[str, Any]]):
        query = self.input_search.text().strip()
        filtered = [it for it in items if self._matches_search(it, query)]

        self.tbl_active.setRowCount(len(filtered))
        for row, it in enumerate(filtered):
            # 1. Proxy endpoint
            item_proxy = QTableWidgetItem(it["endpoint"])
            item_proxy.setFont(QFont("Consolas", 10))
            item_proxy.setForeground(QColor("#f8fafc"))
            self.tbl_active.setItem(row, 0, item_proxy)

            # 2. Status Badge Widget
            status_txt = it.get("status", "SUCCESS")
            bg_color = "#064e3b"
            fg_color = "#34d399"
            if status_txt == "RUNNING" or it.get("in_use"):
                bg_color = "#1e3a8a"
                fg_color = "#60a5fa"
                status_txt = "RUNNING"
            elif status_txt == "WAITING":
                bg_color = "#451a03"
                fg_color = "#fbbf24"
                status_txt = "CHỜ XOAY"
            elif status_txt == "COOLING" or it.get("cooling_until", 0) > time.time():
                bg_color = "#3b0764"
                fg_color = "#c084fc"
                status_txt = "COOLING"
            elif status_txt == "CHECKING":
                bg_color = "#075985"
                fg_color = "#38bdf8"
                status_txt = "CHECKING"

            badge_box = QWidget()
            b_layout = QHBoxLayout(badge_box)
            b_layout.setContentsMargins(6, 4, 6, 4)
            b_lbl = QLabel(status_txt)
            b_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b_lbl.setStyleSheet(f"""
                background-color: {bg_color};
                color: {fg_color};
                font-weight: 700;
                font-size: 10px;
                border-radius: 4px;
                padding: 2px 8px;
            """)
            b_layout.addWidget(b_lbl)
            self.tbl_active.setCellWidget(row, 1, badge_box)

            # 3. Provider
            item_prov = QTableWidgetItem(it.get("provider", "Xoay - proxy.vn"))
            item_prov.setForeground(QColor("#94a3b8"))
            item_prov.setFont(QFont("Segoe UI", 10))
            self.tbl_active.setItem(row, 2, item_prov)

            # 4. Speed / Ping
            speed_txt = str(it.get("speed", "-"))
            rem_cooling = it.get("cooling_until", 0) - time.time()
            if rem_cooling > 0 and "Chờ" not in speed_txt:
                speed_txt = f"Chờ {int(rem_cooling)}s"
            item_speed = QTableWidgetItem(speed_txt)
            if "ms" in speed_txt:
                item_speed.setForeground(QColor("#a7f3d0"))
            elif "Chờ" in speed_txt or "cho" in speed_txt.lower():
                item_speed.setForeground(QColor("#c084fc"))
            else:
                item_speed.setForeground(QColor("#94a3b8"))
            item_speed.setFont(QFont("Segoe UI", 10))
            self.tbl_active.setItem(row, 3, item_speed)

            # 5. Actions (Rotate, Check, Delete)
            act_box = QWidget()
            a_layout = QHBoxLayout(act_box)
            a_layout.setContentsMargins(4, 2, 4, 2)
            a_layout.setSpacing(6)
            a_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            pid = it["id"]

            btn_rot = QPushButton(act_box)
            btn_rot.setIcon(_get_asset_icon("rotate_proxy_blue.svg", 14))
            btn_rot.setIconSize(QSize(14, 14))
            btn_rot.setFixedSize(28, 24)
            btn_rot.setToolTip("Xoay IP ngay")
            btn_rot.setEnabled(bool(it.get('key_xoay')))
            btn_rot.setStyleSheet("QPushButton { background-color: #1e293b; border: 1px solid #334155; border-radius: 4px; } QPushButton:hover { background-color: #334155; }")
            btn_rot.clicked.connect(lambda _, p=pid: self._rotate_single_proxy(p))

            btn_chk = QPushButton(act_box)
            btn_chk.setIcon(_get_asset_icon("play_blue.svg", 14))
            btn_chk.setIconSize(QSize(14, 14))
            btn_chk.setFixedSize(28, 24)
            btn_chk.setToolTip("Kiểm tra tốc độ / Ping")
            btn_chk.setStyleSheet("QPushButton { background-color: #1e293b; border: 1px solid #334155; border-radius: 4px; } QPushButton:hover { background-color: #334155; }")
            btn_chk.clicked.connect(lambda _, p=pid: self._check_single_proxy(p))

            btn_del = QPushButton(act_box)
            btn_del.setIcon(_get_asset_icon("trash_red.svg", 14))
            btn_del.setIconSize(QSize(14, 14))
            btn_del.setFixedSize(28, 24)
            btn_del.setToolTip("Xóa proxy")
            btn_del.setStyleSheet("QPushButton { background-color: #1e293b; border: 1px solid #334155; border-radius: 4px; } QPushButton:hover { background-color: #450a0a; border-color: #991b1b; }")
            btn_del.clicked.connect(lambda _, p=pid: self._delete_single_proxy(p))

            a_layout.addWidget(btn_rot)
            a_layout.addWidget(btn_chk)
            a_layout.addWidget(btn_del)
            self.tbl_active.setCellWidget(row, 4, act_box)

        if filtered:
            self.lbl_active_count.setText(f"Hiển thị 1 - {len(filtered)} của {len(items)} proxy")
        else:
            self.lbl_active_count.setText("Hiển thị 0 của 0 proxy")

    def _render_failed_table(self, items: List[Dict[str, Any]]):
        query = self.input_search.text().strip()
        filtered = [it for it in items if self._matches_search(it, query)]

        self.tbl_failed.setRowCount(len(filtered))
        for row, it in enumerate(filtered):
            # 1. Proxy endpoint
            item_proxy = QTableWidgetItem(it["endpoint"])
            item_proxy.setFont(QFont("Consolas", 10))
            item_proxy.setForeground(QColor("#fca5a5"))
            self.tbl_failed.setItem(row, 0, item_proxy)

            # 2. Error reason
            item_err = QTableWidgetItem(it.get("error_reason", "Xoay proxy thất bại: key khong ton tai"))
            item_err.setForeground(QColor("#f87171"))
            item_err.setFont(QFont("Segoe UI", 10))
            self.tbl_failed.setItem(row, 1, item_err)

            # 3. Error time
            item_time = QTableWidgetItem(it.get("error_time", datetime.now().strftime("%d/%m/%Y %H:%M:%S")))
            item_time.setForeground(QColor("#94a3b8"))
            item_time.setFont(QFont("Segoe UI", 10))
            self.tbl_failed.setItem(row, 2, item_time)

            # 4. Actions (Retry, Check, Delete)
            act_box = QWidget()
            a_layout = QHBoxLayout(act_box)
            a_layout.setContentsMargins(4, 2, 4, 2)
            a_layout.setSpacing(6)
            a_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            pid = it["id"]

            btn_rot = QPushButton(act_box)
            btn_rot.setIcon(_get_asset_icon("rotate_proxy_blue.svg", 14))
            btn_rot.setIconSize(QSize(14, 14))
            btn_rot.setFixedSize(28, 24)
            btn_rot.setToolTip("Thử xoay lại")
            btn_rot.setEnabled(bool(it.get('key_xoay')))
            btn_rot.setStyleSheet("QPushButton { background-color: #1e293b; border: 1px solid #334155; border-radius: 4px; } QPushButton:hover { background-color: #334155; }")
            btn_rot.clicked.connect(lambda _, p=pid: self._rotate_single_proxy(p))

            btn_chk = QPushButton(act_box)
            btn_chk.setIcon(_get_asset_icon("play_blue.svg", 14))
            btn_chk.setIconSize(QSize(14, 14))
            btn_chk.setFixedSize(28, 24)
            btn_chk.setToolTip("Kiểm tra lại")
            btn_chk.setStyleSheet("QPushButton { background-color: #1e293b; border: 1px solid #334155; border-radius: 4px; } QPushButton:hover { background-color: #334155; }")
            btn_chk.clicked.connect(lambda _, p=pid: self._check_single_proxy(p))

            btn_del = QPushButton(act_box)
            btn_del.setIcon(_get_asset_icon("trash_red.svg", 14))
            btn_del.setIconSize(QSize(14, 14))
            btn_del.setFixedSize(28, 24)
            btn_del.setToolTip("Xóa proxy")
            btn_del.setStyleSheet("QPushButton { background-color: #1e293b; border: 1px solid #334155; border-radius: 4px; } QPushButton:hover { background-color: #450a0a; border-color: #991b1b; }")
            btn_del.clicked.connect(lambda _, p=pid: self._delete_single_proxy(p))

            a_layout.addWidget(btn_rot)
            a_layout.addWidget(btn_chk)
            a_layout.addWidget(btn_del)
            self.tbl_failed.setCellWidget(row, 3, act_box)

        if filtered:
            self.lbl_failed_count.setText(f"Hiển thị 1 - {len(filtered)} của {len(items)} proxy lỗi")
        else:
            self.lbl_failed_count.setText("Hiển thị 0 của 0 proxy lỗi")

    # -------------------------------------------------------------
    # User Actions (Import, Export, Delete, Rotate, Check)
    # -------------------------------------------------------------
    def _update_proxy_file_label(self, message=""):
        if self._proxy_file_path:
            text = f"File đang mở: {Path(self._proxy_file_path).name}"
            self.lbl_proxy_file.setToolTip(self._proxy_file_path)
        else:
            text = "Chưa chọn file TXT. Mỗi dòng một proxy; Lưu/Xuất bao gồm cả proxy lỗi."
            self.lbl_proxy_file.setToolTip("")
        self.lbl_proxy_file.setText(f"{text} — {message}" if message else text)

    def _bind_proxy_file(self, path):
        self._proxy_file_path = str(Path(path).expanduser().resolve())
        self.settings.setValue(f"{self.settings_prefix}/txt_path", self._proxy_file_path)
        self.settings.sync()
        self._update_proxy_file_label()

    def _import_proxy_lines(self, lines, provider_id=None):
        """Merge input without replacing existing entries or contacting proxies."""
        identities = {self._proxy_identity(item) for item in self._proxies.values()}
        new_ids, invalid_lines, duplicates = [], [], 0
        for number, line in enumerate(lines, 1):
            line = line.strip().lstrip('\ufeff').strip()
            if not line or line.startswith('#'):
                continue
            item = self._parse_proxy_line(line, provider_id=provider_id)
            if item is None:
                invalid_lines.append(number)
                continue
            identity = self._proxy_identity(item)
            if identity in identities:
                duplicates += 1
                continue
            identities.add(identity)
            self._proxies[item['id']] = item
            new_ids.append(item['id'])
        if new_ids:
            self._save_to_settings()
        return new_ids, duplicates, invalid_lines

    def _show_proxy_import_result(self, new_ids, duplicates, invalid_lines):
        message = f"Đã thêm {len(new_ids)} proxy. Bỏ qua {duplicates} proxy trùng."
        if invalid_lines:
            numbers = ', '.join(map(str, invalid_lines[:12]))
            if len(invalid_lines) > 12:
                numbers += ', …'
            message += f"\n{len(invalid_lines)} dòng không hợp lệ: {numbers}."
        self._update_proxy_file_label(message.replace('\n', ' '))
        notify = QMessageBox.warning if invalid_lines else QMessageBox.information
        notify(self, "Nhập proxy", message)

    def _on_import_clicked(self):
        dlg = _ProxyImportDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            lines = dlg.get_lines()
            if not lines:
                return
            new_ids, duplicates, invalid_lines = self._import_proxy_lines(lines, provider_id=dlg.combo_provider.currentData())
            self._show_proxy_import_result(new_ids, duplicates, invalid_lines)
            if dlg.should_auto_check() and new_ids:
                QTimer.singleShot(200, lambda: self._check_proxy_batch(new_ids))

    def _on_import_file_clicked(self):
        path, _ = QFileDialog.getOpenFileName(self, "Nhập file proxy TXT",
            self._proxy_file_path, "File proxy (*.txt)")
        if not path:
            return
        try:
            data = Path(path).read_bytes()
            if data.startswith((b'\xff\xfe\x00\x00', b'\x00\x00\xfe\xff')):
                encoding = 'utf-32'
            elif data.startswith((b'\xff\xfe', b'\xfe\xff')):
                encoding = 'utf-16'
            else:
                encoding = 'utf-8-sig'
            lines = data.decode(encoding).splitlines()
        except (OSError, UnicodeError) as exc:
            QMessageBox.warning(self, "Không đọc được file proxy", str(exc))
            return
        new_ids, duplicates, invalid_lines = self._import_proxy_lines(lines)
        if new_ids or duplicates:
            self._bind_proxy_file(path)
        elif not invalid_lines:
            QMessageBox.warning(self, "Nhập proxy", "File không có dòng proxy nào.")
            return
        self._show_proxy_import_result(new_ids, duplicates, invalid_lines)

    def _serialize_proxy_lines(self):
        lines = []
        # A mixed-provider TXT must remain unambiguous regardless of the next
        # import selector. Keep the legacy bare-key format for Proxy.vn-only files.
        sources = {rotating_provider_id(self._item_key_reference(item))
            for item in self._proxies.values() if item.get('key_xoay')}
        for item in self._proxies.values():
            endpoint, key = item['endpoint'].strip(), self._item_key_reference(item)
            if key and len(sources) > 1:
                key = rotation_key_reference(key, canonical=True)
            if any(char in endpoint or char in key for char in ('\r', '\n')):
                raise ValueError("Proxy chứa ký tự xuống dòng; không thể xuất TXT.")
            lines.append(f'{endpoint}|{key}' if key else endpoint)
        return lines

    @staticmethod
    def _txt_file_path(path):
        target = Path(path).expanduser().resolve()
        if target.suffix.lower() != '.txt':
            target = target.with_name(target.name + '.txt')
        return str(target)

    def _choose_proxy_save_path(self, title):
        initial = self._proxy_file_path or "proxies.txt"
        path, _ = QFileDialog.getSaveFileName(self, title, initial, "File proxy (*.txt)")
        if not path:
            return ""
        target = self._txt_file_path(path)
        # The native dialog checks overwrite before our suffix normalization.
        if Path(target) != Path(path).expanduser().resolve() and Path(target).exists():
            overwrite = QMessageBox.question(self, "Lưu file proxy",
                f"File {Path(target).name} đã tồn tại. Ghi đè file này?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if overwrite != QMessageBox.StandardButton.Yes:
                return ""
        return target

    def _write_proxy_file(self, path):
        """Commit atomically so failed writes leave the previous file intact."""
        target = self._txt_file_path(path)
        lines = self._serialize_proxy_lines()
        payload = ('\n'.join(lines) + ('\n' if lines else '')).encode('utf-8')
        output = QSaveFile(target)
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if output.write(payload) != len(payload):
            error = output.errorString()
            output.cancelWriting()
            raise OSError(error or "Không ghi đủ nội dung file proxy.")
        if not output.commit():
            raise OSError(output.errorString())
        return target

    def _on_save_file_clicked(self):
        path = self._proxy_file_path or self._choose_proxy_save_path("Lưu danh sách proxy TXT")
        if not path:
            return
        try:
            target = self._write_proxy_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Không lưu được file proxy", str(exc))
            return
        self._bind_proxy_file(target)
        self._update_proxy_file_label(f"Đã lưu {len(self._proxies)} proxy")
        QMessageBox.information(self, "Lưu proxy", f"Đã lưu {len(self._proxies)} proxy vào file TXT.")

    def _on_export_clicked(self):
        if not self._proxies:
            QMessageBox.warning(self, "Xuất proxy", "Danh sách chưa có proxy để xuất.")
            return
        path = self._choose_proxy_save_path("Xuất bản sao danh sách proxy TXT")
        if not path:
            return
        try:
            target = self._write_proxy_file(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Không xuất được file proxy", str(exc))
            return
        self._update_proxy_file_label(f"Đã xuất {len(self._proxies)} proxy vào {Path(target).name}")
        QMessageBox.information(self, "Xuất proxy", f"Đã xuất {len(self._proxies)} proxy vào file TXT.")

    def _on_free_proxy_clicked(self):
        import webbrowser
        try:
            webbrowser.open("https://proxy.vn/?home=apixoay")
        except Exception:
            pass

    def _delete_single_proxy(self, pid: str):
        if pid in self._proxies:
            del self._proxies[pid]
            self._save_to_settings()

    def _delete_all_active(self):
        res = QMessageBox.question(self, "Xác nhận", "Bạn có chắc muốn xóa tất cả proxy đang hoạt động?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if res == QMessageBox.StandardButton.Yes:
            self._proxies = {k: v for k, v in self._proxies.items() if v["is_failed"]}
            self._save_to_settings()

    def _delete_all_failed(self):
        res = QMessageBox.question(self, "Xác nhận", "Bạn có chắc muốn xóa tất cả proxy lỗi?", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if res == QMessageBox.StandardButton.Yes:
            self._proxies = {k: v for k, v in self._proxies.items() if not v["is_failed"]}
            self._save_to_settings()

    def _rotate_single_proxy(self, pid: str):
        it = self._proxies.get(pid)
        if not it or not it.get('key_xoay'):
            return

        if pid in self._active_rotations:
            return

        it["status"] = "WAITING"
        it["speed"] = "Đang kết nối..."
        self._update_metrics_and_render()

        worker = _ProxyRotateThread(pid, self._item_key_reference(it), it.get("endpoint", ""), parent=self)
        worker.progress_signal.connect(self._on_rotate_progress)
        worker.finished_signal.connect(self._on_rotate_finished)
        worker.finished.connect(lambda: self._rotation_stopped(pid, worker))
        self._active_rotations[pid] = worker
        worker.start()

    def _on_rotate_progress(self, pid: str, remaining_sec: float, msg: str):
        it = self._proxies.get(pid)
        if not it:
            return
        it["status"] = "WAITING"
        it["speed"] = f"Chờ xoay ({int(remaining_sec)}s)" if remaining_sec > 0 else msg
        self._update_metrics_and_render()

    def _on_rotate_finished(self, pid: str, rot_res: RotationResult, latency_ms: int):
        it = self._proxies.get(pid)
        if not it:
            return

        if rot_res.proxy:
            it['endpoint'] = rot_res.proxy.to_url()

        if rot_res.success and not rot_res.is_cooling and rot_res.proxy:
            it["endpoint"] = rot_res.proxy.to_url()
            if it.get('key_xoay'):
                update_proxy_endpoint(self._item_key_reference(it), rot_res.proxy)
            it["status"] = "SUCCESS"
            it["speed"] = f"IP {latency_ms} ms"
            it["is_failed"] = False
            it["error_reason"] = ""
            it["cooling_until"] = 0.0
        elif rot_res.is_cooling or is_cooldown_or_wait_message(rot_res.error):
            it["is_failed"] = False
            it["status"] = "COOLING"
            it["cooling_until"] = time.time() + rot_res.wait_seconds
            it["speed"] = f"Chờ {int(rot_res.wait_seconds)}s"
            it["error_reason"] = ""
        else:
            if is_real_proxy_error(rot_res.error):
                it["is_failed"] = True
                it["status"] = "ERROR"
                it["error_reason"] = rot_res.error or "Xoay proxy thất bại"
                it["error_time"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            else:
                it["is_failed"] = True
                it["status"] = "ERROR"
                it["error_reason"] = rot_res.error or "Không kết nối được nhà cung cấp proxy"
                it["error_time"] = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        self._save_to_settings()

    def _rotation_stopped(self, pid, worker):
        self._active_rotations.pop(pid, None)
        worker.deleteLater()

    def _check_single_proxy(self, pid):
        self._check_proxy_batch([pid])

    def _check_proxy_batch(self, pids):
        # Match the original checker: query exit-IP APIs through the proxy.
        items = [(pid, dict(self._proxies[pid])) for pid in pids
                 if pid in self._proxies and pid not in self._active_rotations]
        if not items:
            return
        self.btn_check_all_active.setEnabled(False)
        self.btn_check_all_failed.setEnabled(False)

        async def batch():
            semaphore = asyncio.Semaphore(5)
            async def check(pid, item):
                async with semaphore:
                    started = time.monotonic()
                    try:
                        key = self._item_key_reference(item)
                        with proxy_maintenance(key) if key else nullcontext():
                            model = ProxyModel.from_string(item['endpoint'])
                            health = await probe_proxy_connection(model)
                            result = RotationResult(success=health.success, proxy=model,
                                error=f'{health.stage}: {health.error}' if not health.success else None)
                    except ProxyTransportError:
                        result = RotationResult(success=False, is_cooling=True, wait_seconds=2.0)
                    return pid, result, int((time.monotonic() - started) * 1000)
            return await asyncio.gather(*(check(pid, item) for pid, item in items))

        def finished(results):
            for pid, result, latency in results:
                if result.proxy and not result.is_cooling:
                    item = self._proxies.get(pid)
                    if item:
                        item['endpoint'] = result.proxy.to_url()
                self._on_rotate_finished(pid, result, latency)

        def restore():
            self.btn_check_all_active.setEnabled(True)
            self.btn_check_all_failed.setEnabled(True)

        start_background(self, batch, finished,
            lambda error: logger.warning('Kiểm tra proxy thất bại: %s', error), restore)

    def _check_all_active_proxies(self):
        self._check_proxy_batch([pid for pid, item in self._proxies.items() if not item['is_failed']])

    def _check_all_failed_proxies(self):
        self._check_proxy_batch([pid for pid, item in self._proxies.items() if item['is_failed']])

    def shutdown_workers(self):
        self._runtime_timer.stop()
        workers = list(self._active_rotations.values()) + list(getattr(self, '_background_workers', ()))
        for worker in workers:
            if worker.isRunning():
                worker.request_cancel()
        return any(worker.isRunning() for worker in workers)

    def get_active_proxies(self) -> List[Tuple[str, str]]:
        """Trả về danh sách các proxy đang hoạt động dạng [(endpoint, key_xoay), ...]."""
        return [
            (it["endpoint"], self._item_key_reference(it))
            for it in self._proxies.values()
            if not it["is_failed"]
        ]
