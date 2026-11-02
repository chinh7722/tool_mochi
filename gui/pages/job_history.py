"""Searchable persistent job history, backed by the automatic CSV."""
from datetime import datetime
from PySide6.QtCore import Qt,QUrl
from PySide6.QtGui import QDesktopServices,QColor
from PySide6.QtWidgets import (QFrame,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QComboBox,
    QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView)
from core.job_history import JOB_TIMEZONE,STATUS_LABELS


class JobHistoryPage(QFrame):
    def __init__(self,store,parent=None):
        super().__init__(parent)
        self.store=store
        self.setObjectName('mochiContentCard')
        self.setStyleSheet('QFrame#mochiContentCard {background:#232326;border:1px solid #3a3a42;border-radius:10px;}')
        self._rows=()
        box=QVBoxLayout(self)
        box.setContentsMargins(14,12,14,12)
        heading=QLabel('Lịch sử job',self)
        heading.setStyleSheet('font-size:17px;font-weight:700;color:#f8fafc;')
        box.addWidget(heading)
        note=QLabel('Tự lưu khi job kết thúc. Lịch sử được giữ qua lần mở lại tool và khi xóa job khỏi danh sách phiên.',self)
        note.setWordWrap(True)
        box.addWidget(note)
        actions=QHBoxLayout()
        self.search=QLineEdit(self)
        self.search.setPlaceholderText('Tìm mã job, mã đơn, người tạo, Chat ID, khách hàng hoặc mã mời…')
        self.search.textChanged.connect(self.render)
        actions.addWidget(self.search,1)
        self.filter_status=QComboBox(self)
        self.filter_status.addItem('Tất cả kết quả','')
        for key,label in STATUS_LABELS.items():
            self.filter_status.addItem(label,key)
        self.filter_status.currentIndexChanged.connect(self.render)
        actions.addWidget(self.filter_status)
        self.btn_refresh=QPushButton('Làm mới',self)
        self.btn_refresh.clicked.connect(self.refresh)
        actions.addWidget(self.btn_refresh)
        box.addLayout(actions)
        self.summary=QLabel('',self)
        box.addWidget(self.summary)
        self.table=QTableWidget(0,8,self)
        self.table.setStyleSheet('''QTableWidget {background:#1d1d20;color:#f8fafc;gridline-color:#3a3a42;
            border:1px solid #3a3a42;border-radius:6px;}
            QHeaderView::section {background:#29292e;color:#b9c8e0;padding:7px;border:1px solid #3a3a42;}
            QTableWidget::item:selected {background:#253654;color:#f8fafc;}
            QTableCornerButton::section {background:#29292e;border:1px solid #3a3a42;}''')
        self.table.setHorizontalHeaderLabels(['Kết thúc (VN)','Người tạo job','Khách hàng / Job','Mã mời',
            'Kết quả','Thành công / Tổng','Bean thực tế','Mã job'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        for column,width in enumerate((150,200,130,80,120,135,110,170)):
            self.table.setColumnWidth(column,width)
        self.table.horizontalHeader().setStretchLastSection(True)
        box.addWidget(self.table,1)
        self.path_label=QLabel('CSV: '+str(store.path),self)
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.path_label)
        buttons=QHBoxLayout()
        self.btn_open=QPushButton('Mở CSV',self)
        self.btn_folder=QPushButton('Mở thư mục',self)
        self.btn_open.clicked.connect(self.open_csv)
        self.btn_folder.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.path.parent))))
        buttons.addWidget(self.btn_open)
        buttons.addWidget(self.btn_folder)
        buttons.addStretch()
        box.addLayout(buttons)
        self.status=QLabel('',self)
        self.status.setWordWrap(True)
        self.status.setStyleSheet('color:#fbbf24;')
        box.addWidget(self.status)
        self.refresh()

    def refresh(self,*_,sync=True,warning=''):
        message=warning
        try:
            if sync:
                self.store.sync()
        except Exception as exc:
            message='Chưa ghi được CSV; bản ghi đang chờ được giữ lại. Đóng file trong Excel nếu đang mở, rồi bấm Làm mới. '+str(exc)
        try:
            self._rows=self.store.read()
        except Exception as exc:
            message='Không đọc được toàn bộ lịch sử; dữ liệu trên đĩa được giữ nguyên. '+str(exc)
        self.status.setText(message)
        self.btn_open.setEnabled(self.store.path.is_file())
        self.render()

    def render(self,*_):
        query=self.search.text().strip().casefold()
        status=self.filter_status.currentData()
        rows=[row for row in self._rows if (not status or row.status==status)
            and (not query or query in ' '.join((row.job_id,row.order_id,row.creator_name,
                str(row.creator_chat_id or ''),row.customer_name,row.invite_code)).casefold())]
        self.table.setRowCount(len(rows))
        self.summary.setText(f'Hiển thị {len(rows):,}/{len(self._rows):,} job · '
            f'Hoàn thành: {sum(r.status=="completed" for r in rows):,} · '
            f'Bean thành công: {sum(r.achieved_beans for r in rows):,}')
        for index,row in enumerate(rows):
            creator=row.creator_name+(f' ({row.creator_chat_id})' if row.creator_chat_id is not None else '')
            finished=datetime.fromisoformat(row.finished_at).astimezone(JOB_TIMEZONE).strftime('%d/%m/%Y %H:%M:%S')
            values=(finished,creator,row.customer_name or '—',row.invite_code,STATUS_LABELS.get(row.status,row.status),
                f'{row.success_count} / {row.target_count}',f'{row.achieved_beans:,}',row.job_id)
            detail=(f'Mã job: {row.job_id}\nMã đơn: {row.order_id or "—"}\n'
                f'Tạo: {row.created_at}\nKết thúc: {row.finished_at}\nĐã thử: {row.attempted_count}, lỗi: {row.failure_count}')
            for column,value in enumerate(values):
                item=QTableWidgetItem(value)
                item.setToolTip(str(value)+'\n'+detail)
                if column==4:
                    item.setForeground(QColor('#34d399' if row.status=='completed' else '#fbbf24' if row.status=='cancelled' else '#f87171'))
                self.table.setItem(index,column,item)

    def open_csv(self):
        if self.store.path.is_file() and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.path))):
            self.status.setText('Không mở được CSV bằng ứng dụng mặc định. Dùng Mở thư mục để chọn file.')
