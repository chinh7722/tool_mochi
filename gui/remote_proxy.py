"""Proxy workspaces matching the desktop tool, backed only by the VPS API."""
from PySide6.QtCore import QSize, Qt
from PySide6 import QtSvg  # Register SVG icon support for standalone previews too.
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from gui.branding import icon


class _Wave(QWidget):
    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.setFixedSize(62, 30)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.moveTo(2, 23)
        path.cubicTo(16, 4, 27, 26, 43, 9)
        path.cubicTo(50, 2, 56, 15, 60, 12)
        pen = QPen(self.color, 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()


class _Metric(QFrame):
    def __init__(self, title, subtitle, color, parent=None):
        super().__init__(parent)
        self.setObjectName('remoteProxyMetric')
        self.setStyleSheet('QFrame#remoteProxyMetric {background:#0d1322; border:1px solid #1e293b; '
            'border-radius:8px;} QLabel {background:transparent;}')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 13, 13, 13)
        left = QVBoxLayout()
        left.setSpacing(3)
        caption = QLabel(title)
        caption.setStyleSheet('color:#94a3b8;font-size:11px;font-weight:600;')
        self.value = QLabel('0')
        self.value.setStyleSheet('color:#f8fafc;font-size:25px;font-weight:700;')
        hint = QLabel(subtitle)
        hint.setStyleSheet('color:#64748b;font-size:10px;')
        for label in (caption, self.value, hint):
            left.addWidget(label)
        layout.addLayout(left)
        layout.addStretch()
        layout.addWidget(_Wave(color, self))


class RemoteProxyView(QWidget):
    """Pure presentation and explicit remote commands; it owns no proxy workers."""
    def __init__(self, window, pool):
        super().__init__()
        from gui.remote_window import table
        self.window, self.pool = window, pool
        self.rows = []
        self._inline_buttons = []
        self._actions_enabled = False
        self._rendering = False
        self._txt_path = str(window.preferences.value('proxy/'+pool+'/txt_path', '') or '')
        self.setObjectName('remoteProxyPage')
        self.setStyleSheet('''
            QWidget#remoteProxyPage {background:transparent;}
            QTableWidget {background:#0b0f19;color:#f1f5f9;border:1px solid #1e293b;
                border-radius:8px;padding:0;gridline-color:#151d2f;}
            QHeaderView::section {background:#0f172a;color:#94a3b8;font-weight:600;
                border:none;border-bottom:1px solid #1e293b;padding:9px 8px;}
            QTableWidget::item {padding:6px;border-bottom:1px solid #151d2f;}
            QTableWidget::item:selected {background:#193455;}
            QLineEdit,QComboBox {background:#0f172a;border:1px solid #334155;
                border-radius:6px;padding:7px 10px;color:#e2e8f0;}
            QSplitter::handle {background:#1e293b;height:3px;}
            QToolButton {background:#142238;border:1px solid #29405e;border-radius:5px;padding:5px;}
            QToolButton:hover {background:#203b5a;border-color:#3b82f6;}
            QToolButton:disabled {background:#111827;border-color:#1e293b;}
        ''')
        box = QVBoxLayout(self)
        box.setContentsMargins(20, 16, 20, 16)
        box.setSpacing(14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(3)
        title = QLabel('Proxy Pool' if pool == 'account' else 'Proxy mua mail')
        title.setStyleSheet('font-size:22px;font-weight:700;color:#f8fafc;')
        subtitle = QLabel('Quản lý proxy xoay, theo dõi trạng thái và phân bổ cho các job trên VPS.'
            if pool == 'account' else
            'Mỗi lần mua xoay IP; dùng proxy sẵn sàng trước, chỉ chờ khi tất cả đều đang xoay.')
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet('font-size:12px;color:#94a3b8;')
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Tìm kiếm proxy...')
        self.search.setFixedWidth(225)
        self.search.addAction(icon('search', 'search_muted.svg'), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self.refresh)
        header.addWidget(self.search)
        box.addLayout(header)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.metrics = {}
        for key, title, hint, color in (
            ('active', 'Hoạt động', 'proxy trong danh sách', '#10b981'),
            ('using', 'Đang dùng', 'đang nhận job / checking', '#3b82f6'),
            ('cooling', 'Đang nghỉ', 'đang nghỉ / chờ xoay', '#a855f7'),
            ('failed', 'Đã lỗi', 'proxy cần kiểm tra lại', '#ef4444'),
        ):
            card = _Metric(title, hint, color, self)
            self.metrics[key] = card
            metrics.addWidget(card, 1)
        box.addLayout(metrics)

        splitter = QSplitter(Qt.Orientation.Vertical)
        active = QWidget()
        active_box = QVBoxLayout(active)
        active_box.setContentsMargins(0, 2, 0, 4)
        active_box.setSpacing(8)
        row = QHBoxLayout()
        label = QLabel('Trạng thái proxy')
        label.setStyleSheet('font-size:14px;font-weight:700;color:#f1f5f9;')
        row.addWidget(label)
        row.addStretch()
        check = window.button('Check all', lambda:self.check_all(), row)
        check.setIcon(icon('check', 'tick_green.svg'))
        check.setToolTip('Kiểm tra toàn bộ danh sách trên VPS, bao gồm proxy lỗi.')
        active_box.addLayout(row)
        provider_row = QHBoxLayout()
        provider_row.addWidget(QLabel('Nhà cung cấp khi nhập:'))
        self.provider = QComboBox()
        self.provider.addItem('Proxy.vn', 'proxyvn')
        self.provider.addItem('muaproxyxoay.net', 'muaproxyxoay')
        self.provider.setMinimumWidth(160)
        self.provider.currentIndexChanged.connect(self.update_format_hint)
        provider_row.addWidget(self.provider)
        self.format_hint = QLabel()
        self.format_hint.setWordWrap(True)
        self.format_hint.setStyleSheet('color:#94a3b8;font-size:11px;')
        provider_row.addWidget(self.format_hint, 1)
        active_box.addLayout(provider_row)
        toolbar = QHBoxLayout()
        self.import_button = window.button('Nhập TXT…', lambda:window.import_proxy_file(pool, self.provider), toolbar)
        self.import_button.setProperty('primary', True)
        self.import_button.setIcon(icon('import', 'import_white.svg'))
        window.button('Lưu TXT', self.save, toolbar).setIcon(icon('export', 'export_blue.svg'))
        window.button('Xuất TXT…', self.export, toolbar).setIcon(icon('export', 'export_blue.svg'))
        paste = window.button('Dán proxy', self.paste_dialog, toolbar)
        paste.setIcon(icon('import', 'import_white.svg'))
        toolbar.addStretch()
        active_box.addLayout(toolbar)
        hint = QLabel('Mỗi proxy xử lý 1 lượt mua tại một thời điểm; mua xong xoay IP.' if pool == 'mail'
            else 'Proxy được cấp và kiểm tra trên VPS; các job dùng chung giới hạn luồng/proxy.')
        hint.setWordWrap(True)
        hint.setStyleSheet('color:#64748b;font-size:11px;')
        active_box.addWidget(hint)
        self.active_table = table(['Proxy', 'Trạng thái', 'Loại - Provider', 'Tốc độ', 'Thao tác'])
        self.configure_table(self.active_table)
        self.active_table.setMinimumHeight(190)
        self.active_table.itemSelectionChanged.connect(lambda:self.selection_changed(self.active_table))
        active_box.addWidget(self.active_table, 1)
        self.active_count = QLabel('Hiển thị 0 của 0 proxy')
        self.active_count.setStyleSheet('font-size:11px;color:#64748b;')
        active_box.addWidget(self.active_count)
        splitter.addWidget(active)

        failed = QWidget()
        failed_box = QVBoxLayout(failed)
        failed_box.setContentsMargins(0, 5, 0, 0)
        failed_box.setSpacing(8)
        failed_header = QHBoxLayout()
        failed_title = QLabel('Proxy lỗi')
        failed_title.setStyleSheet('font-size:14px;font-weight:700;color:#f1f5f9;')
        failed_header.addWidget(failed_title)
        failed_header.addStretch()
        retry = window.button('Kiểm tra đã chọn', lambda:self.selected_action('proxy.check'), failed_header)
        retry.setIcon(icon('check', 'tick_green.svg'))
        failed_box.addLayout(failed_header)
        self.failed_table = table(['Proxy', 'Lý do lỗi', 'Nhà cung cấp', 'Tốc độ', 'Thao tác'])
        self.configure_table(self.failed_table)
        self.failed_table.setMinimumHeight(135)
        self.failed_table.itemSelectionChanged.connect(lambda:self.selection_changed(self.failed_table))
        failed_box.addWidget(self.failed_table, 1)
        self.failed_count = QLabel('Hiển thị 0 của 0 proxy lỗi')
        self.failed_count.setStyleSheet('font-size:11px;color:#64748b;')
        failed_box.addWidget(self.failed_count)
        splitter.addWidget(failed)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        box.addWidget(splitter, 1)
        actions = QHBoxLayout()
        for caption, action, image_name in (
            ('Kiểm tra đã chọn', 'proxy.check', 'tick_green.svg'),
            ('Đổi IP', 'proxy.rotate', 'rotate_proxy_blue.svg'),
            ('Xóa proxy đã chọn', 'proxy.delete', 'trash_red.svg'),
        ):
            button = window.button(caption, lambda _=False,a=action:self.selected_action(a), actions)
            button.setIcon(icon('proxy_action', image_name))
        actions.addStretch()
        box.addLayout(actions)
        self.update_format_hint()

    @staticmethod
    def configure_table(listing):
        listing.setShowGrid(False)
        listing.verticalHeader().setDefaultSectionSize(43)
        header = listing.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 145), (2, 175), (3, 90), (4, 118)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            listing.setColumnWidth(column, width)

    def update_format_hint(self, *_):
        self.format_hint.setText('ip:port:user:pass|key_xoay' if self.provider.currentData() == 'muaproxyxoay'
            else 'ip:port|key_xoay — mỗi dòng một proxy')

    def export(self):
        self.window.send('proxy.export', {'pool':self.pool},
            lambda result:self.window.save_text(result,
                'proxy_pool.txt' if self.pool == 'account' else 'proxy_mua_mail.txt', 'TXT (*.txt)'))

    def save(self):
        path = self._txt_path
        if not path:
            path, _ = QFileDialog.getSaveFileName(self.window, 'Lưu danh sách proxy',
                'proxy_pool.txt' if self.pool == 'account' else 'proxy_mua_mail.txt', 'TXT (*.txt)')
        if not path:
            return
        def saved(result):
            from PySide6.QtCore import QIODevice, QSaveFile
            target = QSaveFile(path)
            content = result.get('text', '').encode('utf-8')
            if not target.open(QIODevice.OpenModeFlag.WriteOnly):
                self.window.status.setText('Chưa mở được file proxy để lưu.')
                return
            if target.write(content) != len(content) or not target.commit():
                target.cancelWriting()
                self.window.status.setText('Chưa lưu được file proxy; dữ liệu trước được giữ nguyên.')
                return
            self._txt_path = path
            self.window.preferences.setValue('proxy/'+self.pool+'/txt_path', path)
            self.window.status.setText('Đã lưu danh sách proxy từ VPS.')
        self.window.send('proxy.export', {'pool':self.pool}, saved)

    def paste_dialog(self):
        dialog = QDialog(self.window)
        dialog.setWindowTitle('Dán proxy — '+self.provider.currentText())
        dialog.resize(640, 400)
        box = QVBoxLayout(dialog)
        hint = QLabel('Nhập mỗi dòng một proxy. '+self.format_hint.text())
        hint.setWordWrap(True)
        box.addWidget(hint)
        editor = QPlainTextEdit()
        editor.setPlaceholderText(self.format_hint.text())
        box.addWidget(editor, 1)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = QPushButton('Hủy')
        cancel.clicked.connect(dialog.reject)
        actions.addWidget(cancel)
        submit = QPushButton('Thêm vào danh sách')
        submit.setProperty('primary', True)
        def add():
            if not self._actions_enabled:
                hint.setText('Kết nối lại VPS trước khi thêm proxy.')
                return
            content = editor.toPlainText()
            if not content.strip():
                hint.setText('Dán ít nhất một proxy trước khi thêm.')
                return
            self.window.send('proxy.import', {'pool':self.pool,
                'provider':self.provider.currentData(), 'text':content})
            dialog.accept()
        submit.clicked.connect(add)
        submit.setEnabled(self._actions_enabled)
        actions.addWidget(submit)
        box.addLayout(actions)
        dialog.exec()

    def check_all(self):
        if not self.rows:
            self.window.status.setText('Danh sách này chưa có proxy để kiểm tra.')
            return
        self.window.send('proxy.check', {'pool':self.pool})

    @staticmethod
    def is_failed(row):
        return str(row.get('status', '')).upper() in ('FAILED', 'ERROR', 'DEAD')

    def update_rows(self, rows):
        incoming = [dict(row) for row in rows]
        if incoming == self.rows:
            return
        self.rows = incoming
        self.metrics['active'].value.setText(str(sum(not self.is_failed(row) for row in self.rows)))
        self.metrics['using'].value.setText(str(sum(str(row.get('status', '')).upper() in
            ('RUNNING', 'CHECKING', 'IN_USE', 'BUSY') for row in self.rows)))
        self.metrics['cooling'].value.setText(str(sum(str(row.get('status', '')).upper() in
            ('COOLING', 'WAITING', 'ROTATING') for row in self.rows)))
        self.metrics['failed'].value.setText(str(sum(self.is_failed(row) for row in self.rows)))
        self.refresh()

    def selected_identifier(self):
        for listing in (self.active_table, self.failed_table):
            indexes = listing.selectionModel().selectedRows()
            if indexes and 0 <= indexes[0].row() < len(listing._rows):
                return listing._rows[indexes[0].row()]['id']
        return None

    def selection_changed(self, listing):
        if self._rendering or not listing.selectionModel().selectedRows():
            return
        other = self.failed_table if listing is self.active_table else self.active_table
        other.clearSelection()

    def selected_action(self, action):
        identifier = self.selected_identifier()
        if identifier is None:
            self.window.status.setText('Chọn một dòng proxy trước khi thao tác.')
            return
        self.command(action, identifier)

    def command(self, action, identifier):
        if self._actions_enabled:
            self.window.send(action, {'pool':self.pool, 'id':identifier})

    def set_actions_enabled(self, enabled):
        self._actions_enabled = bool(enabled)
        for button in self._inline_buttons:
            button.setEnabled(self._actions_enabled and button.property('available'))

    def refresh(self, *_):
        from gui.remote_window import fill
        query = self.search.text().strip().casefold()
        selected = self.selected_identifier()
        matching = [row for row in self.rows if not query or query in ' '.join(str(row.get(key, ''))
            for key in ('id', 'endpoint', 'status', 'provider', 'error')).casefold()]
        active = [row for row in matching if not self.is_failed(row)]
        failed = [row for row in matching if self.is_failed(row)]
        self._rendering = True
        self._inline_buttons.clear()
        try:
            for listing, rows, key in ((self.active_table, active, 'status'),
                    (self.failed_table, failed, 'error')):
                for old_row in range(listing.rowCount()):
                    for old_column in (1, 4):
                        old_widget = listing.cellWidget(old_row, old_column)
                        if old_widget is not None:
                            old_widget.hide()
                            listing.removeCellWidget(old_row, old_column)
                fill(listing, rows, ['endpoint', key, 'provider', 'speed', 'id'])
                listing.clearSelection()
                for index, row in enumerate(rows):
                    listing.item(index, 4).setText('')
                    if listing is self.active_table:
                        status = str(row.get('status', 'READY')).upper()
                        foreground, background = ('#c084fc', '#3b0764') if status in ('COOLING', 'WAITING', 'ROTATING') else (
                            ('#60a5fa', '#1e3a8a') if status in ('RUNNING', 'CHECKING', 'IN_USE') else ('#34d399', '#064e3b'))
                        badge_cell = QWidget()
                        badge_cell.setStyleSheet('background:transparent;')
                        badge_layout = QHBoxLayout(badge_cell)
                        badge_layout.setContentsMargins(6, 5, 6, 5)
                        badge = QLabel('CHỜ XOAY' if status == 'WAITING' else status)
                        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                        badge.setStyleSheet(f'background:{background};color:{foreground};border-radius:5px;'
                            'font-size:10px;font-weight:700;padding:3px 8px;')
                        badge_layout.addWidget(badge)
                        badge_layout.addStretch()
                        listing.item(index, 1).setText('')
                        listing.setCellWidget(index, 1, badge_cell)
                    cell = QWidget()
                    cell.setStyleSheet('background:transparent;')
                    actions = QHBoxLayout(cell)
                    actions.setContentsMargins(4, 4, 4, 4)
                    actions.setSpacing(4)
                    for action, tooltip, image_name in (
                        ('proxy.check', 'Kiểm tra proxy', 'tick_green.svg'),
                        ('proxy.rotate', 'Đổi IP', 'rotate_proxy_blue.svg'),
                        ('proxy.delete', 'Xóa proxy', 'trash_red.svg'),
                    ):
                        button = QToolButton()
                        button.setIcon(icon('proxy_action', image_name))
                        button.setIconSize(QSize(13, 13))
                        button.setToolTip(tooltip)
                        button.setCursor(Qt.CursorShape.PointingHandCursor)
                        available = action != 'proxy.rotate' or bool(row.get('has_key'))
                        button.setProperty('available', available)
                        button.setEnabled(self._actions_enabled and available)
                        button.clicked.connect(lambda _=False,a=action,pid=row['id']:self.command(a, pid))
                        actions.addWidget(button)
                        self._inline_buttons.append(button)
                    listing.setCellWidget(index, 4, cell)
                    listing.item(index, 1).setForeground(QColor('#ef4444' if self.is_failed(row)
                        else '#a855f7' if str(row.get('status', '')).upper() in ('COOLING', 'WAITING', 'ROTATING')
                        else '#60a5fa' if str(row.get('status', '')).upper() in ('RUNNING', 'CHECKING', 'IN_USE')
                        else '#34d399'))
                    if row['id'] == selected:
                        listing.selectRow(index)
        finally:
            self._rendering = False
        total_active = sum(not self.is_failed(row) for row in self.rows)
        total_failed = len(self.rows) - total_active
        self.active_count.setText(f'Hiển thị {len(active)} của {total_active} proxy')
        self.failed_count.setText(f'Hiển thị {len(failed)} của {total_failed} proxy lỗi')


def build_proxy_page(window, pool):
    page = RemoteProxyView(window, pool)
    if not hasattr(window, 'proxy_views'):
        window.proxy_views = {}
    window.proxy_views[pool] = page
    setattr(window, 'proxy_table_'+pool, page.active_table)
    setattr(window, 'proxy_failed_table_'+pool, page.failed_table)
    return page
