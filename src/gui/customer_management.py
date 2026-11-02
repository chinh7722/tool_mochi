"""Desktop customer and SePay views, sharing the Telegram SQLite store."""
from datetime import datetime, timezone
from decimal import Decimal
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QLineEdit,
    QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QDialog,QTabWidget,QCheckBox)
from core.finance import format_vnd, money_units
from core.sepay import SePayConfig
from gui.sepay import SePayThread


def vnd(units):
    return format_vnd(Decimal(units)/100) + ' VND'


def table(headers, parent=None):
    widget = QTableWidget(0,len(headers),parent)
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.verticalHeader().setVisible(False)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def fill(widget, rows):
    widget.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            widget.setItem(row,column,item)


class CustomerManagement(QWidget):
    def __init__(self,store,mochi_page,parent=None):
        super().__init__(parent)
        self.setObjectName('telegramContent')
        self.store,self.mochi_page = store,mochi_page
        box = QVBoxLayout(self)
        box.setContentsMargins(18,18,18,18)
        title = QLabel('Quản lý khách hàng',self)
        title.setStyleSheet('font-size:20px;font-weight:700;')
        box.addWidget(title)
        note = QLabel('Khách hàng tự xuất hiện khi nhắn bot và chưa được cấp quyền admin/partner. '
            'Có thể thêm hoặc khôi phục theo Chat ID; sửa số dư luôn lưu lịch sử.',self)
        note.setWordWrap(True)
        box.addWidget(note)
        actions = QHBoxLayout()
        self.input_id = QLineEdit(self)
        self.input_id.setPlaceholderText('Chat ID để thêm / khôi phục (tùy chọn)')
        self.btn_add = QPushButton('Thêm / Khôi phục',self)
        self.btn_add.clicked.connect(self.add_customer)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText('Tìm Chat ID hoặc tên…')
        self.search.textChanged.connect(self.refresh)
        self.btn_refresh = QPushButton('Làm mới',self)
        self.btn_refresh.clicked.connect(self.refresh)
        for widget in (self.input_id,self.btn_add,self.search,self.btn_refresh):
            actions.addWidget(widget)
        box.addLayout(actions)
        management=QHBoxLayout()
        self.btn_edit_balance=QPushButton('Sửa số dư',self)
        self.btn_edit_balance.clicked.connect(self.edit_balance)
        self.btn_delete=QPushButton('Xóa khách hàng',self)
        self.btn_delete.setObjectName('telegramDanger')
        self.btn_delete.clicked.connect(self.delete_customer)
        management.addWidget(self.btn_edit_balance)
        management.addWidget(self.btn_delete)
        management.addStretch()
        box.addLayout(management)
        self.table = table(['ID khách hàng','Số dư khả dụng','Số đơn đã tạo','Đã hoàn thành','Thất bại','Lịch sử giao dịch'],self)
        self.table.verticalHeader().setDefaultSectionSize(46)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemSelectionChanged.connect(self.update_actions)
        self.table.cellDoubleClicked.connect(lambda row,_col:self.show_history(self._ids[row]))
        box.addWidget(self.table,1)
        self.status = QLabel('',self)
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        self.refresh()

    def add_customer(self):
        try:
            raw = self.input_id.text().strip()
            if not raw.isascii() or not raw.isdigit():
                raise ValueError('Nhập một Chat ID số nguyên dương.')
            value = int(raw)
            actor = self.store.get_user(value)
            if actor is not None and actor.active:
                raise ValueError('ID này đang được cấp quyền, thuộc bảng người dùng.')
            self.store.ensure_customer(value,restore=True)
            self.status.setText('Đã lưu khách hàng; chưa cộng tiền hoặc cấp quyền.')
            self.refresh()
        except Exception as exc:
            self.status.setText(str(exc))

    def refresh(self,*_):
        selected=self.selected_id()
        for index in range(self.table.rowCount()):
            widget=self.table.cellWidget(index,5)
            if widget:
                widget.hide()
        self.table.setRowCount(0)
        query = self.search.text().strip().casefold()
        rows = [x for x in self.store.list_customers()
            if query in str(x['chat_id']) or query in x['name'].casefold()]
        self._ids = [x['chat_id'] for x in rows]
        fill(self.table,[(x['chat_id'],vnd(x['balance_units']),x['total_orders'],x['completed_orders'],x['failed_orders'],'') for x in rows])
        for index,profile in enumerate(rows):
            self.table.item(index,0).setToolTip(profile['name'])
            cell=QWidget()
            cell_layout=QHBoxLayout(cell)
            cell_layout.setContentsMargins(5,4,5,4)
            button = QPushButton('Xem giao dịch / đơn',cell)
            button.setStyleSheet('QPushButton {padding:2px 10px; min-height:18px;}')
            button.clicked.connect(lambda _checked=False,value=profile['chat_id']:self.show_history(value))
            cell_layout.addWidget(button)
            self.table.setCellWidget(index,5,cell)
            if profile['chat_id']==selected:
                self.table.selectRow(index)
        self.update_actions()

    def selected_id(self):
        if not hasattr(self,'table') or not hasattr(self,'_ids'):
            return None
        row=self.table.currentRow()
        return self._ids[row] if 0<=row<len(self._ids) else None

    def update_actions(self):
        enabled=self.selected_id() is not None
        self.btn_edit_balance.setEnabled(enabled)
        self.btn_delete.setEnabled(enabled)

    def delete_customer(self):
        customer_id=self.selected_id()
        if customer_id is None:
            return
        try:
            self.store.delete_customer(customer_id)
            self.input_id.setText(str(customer_id))
            self.refresh()
            self.status.setText(f'Đã xóa khách {customer_id} khỏi danh sách. Số dư và lịch sử được giữ; '
                'bấm Thêm / Khôi phục để dùng lại hồ sơ.')
        except Exception as exc:
            self.status.setText(str(exc))

    def save_balance(self,customer_id,amount,reason,expected_units):
        self.store.set_customer_balance(customer_id,money_units(amount),reason,expected_units=expected_units)
        self.refresh()
        self.status.setText(f'Đã lưu số dư khách {customer_id} và ghi lịch sử điều chỉnh.')

    def edit_balance(self):
        customer_id=self.selected_id()
        if customer_id is None:
            return
        profile=self.store.get_customer(customer_id)
        dialog=QDialog(self)
        dialog.setWindowTitle(f'Sửa số dư khách hàng {customer_id}')
        dialog.setStyleSheet('QDialog {background:#18181b;color:#f4f4f5;}')
        dialog.resize(550,250)
        box=QVBoxLayout(dialog)
        box.addWidget(QLabel('Số dư khả dụng hiện tại: '+vnd(profile['balance_units']),dialog))
        note=QLabel('Số dư khả dụng là tiền có thể dùng cho đơn mới. Tiền đã giữ cho đơn đang chạy '
            'vẫn được chốt/hoàn theo kết quả của đơn.',dialog)
        note.setWordWrap(True)
        box.addWidget(note)
        form=QFormLayout()
        amount=QLineEdit(str(Decimal(profile['balance_units'])/100),dialog)
        amount.setPlaceholderText('Số dư mới (VND), ví dụ 50000 hoặc 50000.25')
        reason=QLineEdit('Điều chỉnh số dư trong tool',dialog)
        reason.setMaxLength(300)
        form.addRow('Số dư mới (VND)',amount)
        form.addRow('Lý do',reason)
        box.addLayout(form)
        error=QLabel('',dialog)
        error.setWordWrap(True)
        box.addWidget(error)
        actions=QHBoxLayout()
        save,cancel=QPushButton('Lưu số dư',dialog),QPushButton('Hủy',dialog)
        def commit():
            try:
                self.save_balance(customer_id,amount.text(),reason.text(),profile['balance_units'])
                dialog.accept()
            except Exception as exc:
                error.setText(str(exc))
        save.clicked.connect(commit)
        cancel.clicked.connect(dialog.reject)
        actions.addWidget(cancel)
        actions.addWidget(save)
        box.addLayout(actions)
        dialog.exec()

    def show_history(self,customer_id):
        dialog = QDialog(self)
        dialog.setStyleSheet('QDialog {background:#18181b; color:#f4f4f5;}')
        dialog.setWindowTitle(f'Khách hàng {customer_id} — Giao dịch và đơn hàng')
        dialog.resize(1100,600)
        layout = QVBoxLayout(dialog)
        profile = self.store.get_customer(customer_id)
        balance_label = QLabel('Số dư khả dụng: '+vnd(profile['balance_units']),dialog)
        layout.addWidget(balance_label)
        tabs = QTabWidget(dialog)
        wallet = table(['Thời gian','Loại','Số tiền','Mã tham chiếu','Nội dung'],tabs)
        orders = table(['Mã đơn','Mã job','Mã mời','Bean đặt','Bean thành công','Trạng thái','Đã giữ','Đã thu'],tabs)
        def render():
            fill(wallet,[(r['created_at'],r['kind'],vnd(r['amount_units']),r['reference'],r['description'])
                for r in self.store.customer_history(customer_id)])
            fill(orders,[(r['order_id'],r['job_id'],r['code'],r['requested_beans'],r['success_count']*100,
                r['status'],vnd(r['reserved_units']),'Đang giữ tiền' if r['charged_units'] is None else vnd(r['charged_units']))
                for r in self.store.customer_orders(customer_id)])
            balance_label.setText('Số dư khả dụng: '+vnd(self.store.get_customer(customer_id)['balance_units']))
        render()
        tabs.addTab(wallet,'Lịch sử giao dịch')
        tabs.addTab(orders,'Đơn hàng')
        layout.addWidget(tabs,1)
        # A process stopped mid-job needs an explicit review; never run it again automatically.
        finish = QPushButton('Chốt đơn bị gián đoạn theo tiến độ đã lưu',dialog)
        result = QLabel('',dialog)
        result.setWordWrap(True)
        def settle_interrupted():
            index = orders.currentRow()
            rows = self.store.customer_orders(customer_id)
            if not 0 <= index < len(rows):
                result.setText('Chọn một đơn trong tab Đơn hàng.')
                return
            order = rows[index]
            if (order['job_id'] in self.mochi_page._workers
                    or order['job_id'] in getattr(self.mochi_page,'_scheduled_jobs',{})):
                result.setText('Job vẫn đang chạy trong tool; chờ job kết thúc.')
                return
            self.store.finish_job(order['job_id'],'failed',order['success_count'])
            render()
            self.refresh()
            result.setText('Đã chốt theo bean thành công đã lưu và hoàn phần còn lại. Kiểm tra log nếu cần đối soát thêm.')
        finish.clicked.connect(settle_interrupted)
        layout.addWidget(finish)
        layout.addWidget(result)
        close = QPushButton('Đóng',dialog)
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()


class SePayPanel(QWidget):
    def __init__(self,settings,store,on_sync,parent=None):
        super().__init__(parent)
        self.setObjectName('telegramContent')
        self.settings,self.store,self.on_sync = settings,store,on_sync
        self.config = SePayConfig.from_settings(settings)
        self.worker = None
        self.closing = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18,18,18,18)
        title = QLabel('Thanh toán SePay',self)
        title.setStyleSheet('font-size:20px;font-weight:700;')
        layout.addWidget(title)
        note = QLabel('Nạp tối thiểu 10.000 VND, tối đa 100.000 VND mỗi giao dịch. Nội dung chuyển khoản '
            'chỉ gồm Chat ID khách hàng. Tool đọc giao dịch SePay mỗi 30 giây khi đang mở.',self)
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.fields = {}
        for key,label in (('token','API Token SePay v2'),('bank_id','Bank Account ID (UUID)'),
                ('account_number','Số tài khoản nhận'),('bank_name','Tên ngân hàng'),('account_name','Tên chủ tài khoản')):
            field = QLineEdit(self)
            field.setText(getattr(self.config,key))
            if key=='token':
                field.setEchoMode(QLineEdit.EchoMode.Password)
            self.fields[key] = field
            form.addRow(label,field)
        layout.addLayout(form)
        self.auto = QCheckBox('Tự bật đồng bộ SePay khi mở tool',self)
        self.auto.setChecked(self.config.enabled)
        layout.addWidget(self.auto)
        self.start_label = QLabel('',self)
        self.start_label.setWordWrap(True)
        layout.addWidget(self.start_label)
        actions = QHBoxLayout()
        self.btn_save,self.btn_start,self.btn_stop = [QPushButton(text,self) for text in ('Lưu cấu hình','Bật đồng bộ','Tắt đồng bộ')]
        self.btn_save.clicked.connect(self.save)
        self.btn_start.clicked.connect(self.start)
        self.btn_stop.clicked.connect(self.stop)
        self.btn_stop.setEnabled(False)
        for widget in (self.btn_save,self.btn_start,self.btn_stop):
            actions.addWidget(widget)
        layout.addLayout(actions)
        self.status = QLabel('SePay chưa bật.',self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = table(['ID giao dịch','Chat ID','Số tiền','Nội dung','Trạng thái','Lý do cần kiểm tra'],self)
        layout.addWidget(self.table,1)
        review=QHBoxLayout()
        self.review_chat_id=QLineEdit(self)
        self.review_chat_id.setPlaceholderText('Chat ID nhận giao dịch cần đối soát')
        self.btn_reconcile=QPushButton('Cộng giao dịch đã chọn vào Chat ID',self)
        self.btn_reconcile.clicked.connect(self.reconcile)
        review.addWidget(self.review_chat_id)
        review.addWidget(self.btn_reconcile)
        layout.addLayout(review)
        review_note=QLabel('Đối soát chỉ áp dụng tiền vào đúng ngân hàng và giới hạn nạp, '
            'đang chờ khớp Chat ID. Kiểm tra người chuyển và số tiền trước khi bấm cộng.',self)
        review_note.setWordWrap(True)
        layout.addWidget(review_note)
        self.refresh()
        if self.config.enabled:
            QTimer.singleShot(0,self.start)

    def save(self):
        if self.worker is not None:
            self.status.setText('Tắt đồng bộ trước khi thay đổi cấu hình.')
            return False
        values = {key:field.text().strip() for key,field in self.fields.items()}
        old = self.config
        same_account = values['bank_id']==old.bank_id and values['account_number']==old.account_number
        start_at = old.start_at if same_account and old.start_at else datetime.now(timezone.utc).isoformat()
        try:
            config = SePayConfig(**values,start_at=start_at,enabled=self.auto.isChecked())
            config.validate()
            config.save(self.settings)
            self.settings.sync()
        except Exception as exc:
            self.status.setText(str(exc))
            return False
        self.config = config
        self.status.setText('Đã lưu cấu hình SePay. Bấm Bật đồng bộ để nhận giao dịch.')
        self.refresh()
        return True

    def start(self):
        if self.closing or self.worker is not None or not self.save():
            return
        self.worker = SePayThread(self.config,self.store,self)
        self.worker.status_changed.connect(self.status.setText)
        self.worker.synced.connect(self._synced)
        self.worker.finished.connect(self._stopped)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        for field in self.fields.values():
            field.setEnabled(False)
        self.worker.start()

    def stop(self):
        if self.worker is not None:
            self.worker.request_stop()
            self.btn_stop.setEnabled(False)

    def _stopped(self):
        worker,self.worker = self.worker,None
        if worker:
            worker.deleteLater()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        for field in self.fields.values():
            field.setEnabled(True)

    def _synced(self,_count):
        self.refresh()
        self.on_sync()

    def refresh(self):
        self.start_label.setText('Nhận giao dịch từ: '+(self.config.start_at or 'lần lưu cấu hình đầu tiên')+
            '. Giao dịch trước mốc này không tự cộng. Giao dịch cần kiểm tra vẫn được lưu ở bảng dưới.')
        fill(self.table,[(r['transaction_id'],r['chat_id'] or '',vnd(r['amount_units']),r['content'],
            'Đã cộng tiền' if r['state']=='credited' else 'Cần kiểm tra',r['reason']) for r in self.store.list_sepay_transactions()])

    def reconcile(self):
        try:
            row=self.table.currentRow()
            if row<0:
                raise ValueError('Chọn một giao dịch SePay cần đối soát.')
            raw=self.review_chat_id.text().strip()
            if not raw.isascii() or not raw.isdigit():
                raise ValueError('Nhập Chat ID nhận tiền để đối soát.')
            self.store.reconcile_sepay_credit(self.table.item(row,0).text(),int(raw))
            self.refresh()
            self.on_sync()
            self.status.setText('Đã đối soát và cộng tiền. Giao dịch được khóa chống cộng trùng.')
        except Exception as exc:
            self.status.setText(str(exc))

    def shutdown(self):
        self.closing = True
        self.stop()
        return self.worker is not None and self.worker.isRunning()
