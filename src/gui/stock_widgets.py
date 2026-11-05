"""Account creation and inventory widgets for desktop and VPS management."""
import json
from types import SimpleNamespace
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QSpinBox,
    QCheckBox,QLineEdit,QPushButton,QTableWidget,QTableWidgetItem,QTabWidget,QDialog,
    QPlainTextEdit,QApplication,QMessageBox,QAbstractItemView)

STATUS = {'waiting':'Chờ tích bean','accumulating':'Đang tích bean','ready':'Đủ bean',
    'priority_wait':'Tạm dừng ưu tiên khách',
    'stopping':'Đang dừng','paused':'Đã dừng','failed':'Lỗi tích bean','exporting':'Đang xuất',
    'queued':'Chờ mua mail','buying':'Đang mua mail','creating':'Đang tạo acc',
    'uncertain':'Cần đối soát','partial':'Có acc lỗi','completed':'Hoàn tất','pending':'Chưa đóng phiếu'}


class StockCreateForm(QWidget):
    def __init__(self, command, product_default, parent=None):
        super().__init__(parent)
        self.command = command
        layout = QFormLayout(self)
        self.count = QSpinBox(); self.count.setRange(1,500);self.count.setValue(1)
        self.beans = QSpinBox();self.beans.setRange(100,1_000_000);self.beans.setSingleStep(100);self.beans.setValue(2000)
        self.product = QLineEdit();self.product.setPlaceholderText('ID product mua mail tạo acc và tích bean')
        self.product_default = product_default
        self.gmailvip_key=QLineEdit();self.gmailvip_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.one_trieu_key=QLineEdit();self.one_trieu_key.setEchoMode(QLineEdit.EchoMode.Password)
        for entry in (self.gmailvip_key,self.one_trieu_key):entry.setPlaceholderText('Chỉ dùng mua mail tạo acc — không dùng nhập mã mời')
        self.infinite = QCheckBox('Tích bean vô hạn — 10 lượt / 24 giờ (1.000 bean)')
        self.infinite.toggled.connect(lambda checked:self.beans.setEnabled(not checked))
        layout.addRow('Số acc cần tạo',self.count);layout.addRow('Bean mục tiêu mỗi acc',self.beans)
        layout.addRow('Product ID',self.product)
        layout.addRow('API GmailVip tạo acc',self.gmailvip_key);layout.addRow('API 1TrieuMail tạo acc',self.one_trieu_key)
        layout.addRow(self.infinite)
        note = QLabel('Mua một lô bằng số acc yêu cầu, tạo/đăng nhập từng acc và lấy mã mời. '
            'API tạo acc có thể trùng API nhập mã mời. Giữ tiến độ trên VPS; đạt giới hạn thì chờ 24 giờ 1 phút. Bean mục tiêu là tổng bean trong acc.')
        note.setWordWrap(True);layout.addRow(note)
        self.message = QLabel();self.message.setWordWrap(True);layout.addRow(self.message)

    def create(self):
        payload = dict(count=self.count.value(),beans=self.beans.value(),infinite=self.infinite.isChecked(),
            product_ids=self.product.text().strip() or self.product_default(),
            gmailvip_key=self.gmailvip_key.text().strip(),one_trieu_key=self.one_trieu_key.text().strip())
        def accepted(result):
            self.gmailvip_key.clear();self.one_trieu_key.clear();self.message.setText(result.get('message','Đã tạo lô.'))
        self.command('stock.create',payload,accepted)


class StockInventoryPanel(QWidget):
    def __init__(self, command, loader, parent=None, *, open_logs=None):
        super().__init__(parent)
        self.command,self.loader = command,loader
        self.open_logs=open_logs
        self.offset=0;self.export_offset=0;self.busy=False;self.selected=set();self.rows=[];self.signature=None
        layout=QVBoxLayout(self)
        self.summary=QLabel('Kho tài khoản đã tạo · Chưa tải dữ liệu.');layout.addWidget(self.summary)
        self.note=QLabel('Dừng tích bean và chờ job kết thúc trước khi xuất/xóa acc. Đóng popup xuất sẽ đưa acc khỏi kho.');self.note.setWordWrap(True);layout.addWidget(self.note)
        actions=QHBoxLayout()
        for title,callback in [('Chọn trang này',self.select_page),('Bỏ chọn',self.clear_selection),
                ('Dừng nhập mã',lambda:self.change('stock.stop')),('Tiếp tục',lambda:self.change('stock.resume')),
                ('Xóa acc đã chọn',self.delete),('Xuất acc đã chọn',self.export_selected),('Làm mới',self.load)]:
            button=QPushButton(title);button.clicked.connect(callback);actions.addWidget(button)
        layout.addLayout(actions)
        self.tabs=QTabWidget();layout.addWidget(self.tabs,1)
        self.table=self._table(['Chọn','STT','Email','Mật khẩu','Bean','Mục tiêu','Mã mời','Trạng thái','Dừng nhập mã','Log','Chi tiết'])
        for column,width in enumerate((38,45,165,125,70,75,75,100,90)):self.table.setColumnWidth(column,width)
        self.table.setColumnWidth(9,245);self.table.setColumnWidth(10,250)
        self.table.setStyleSheet('QTableView::indicator {width:15px;height:15px;border:1px solid #8293b0;border-radius:3px;background:#20232b;} '
            'QTableView::indicator:checked {background:#3b82f6;border-color:#93c5fd;}')
        self.table.itemChanged.connect(self.check_changed)
        self.tabs.addTab(self.table,'Tài khoản')
        self.history=self._table(['Phiếu xuất','Số acc','Người xuất','Trạng thái','Ngày xuất','Xem'])
        self.tabs.addTab(self.history,'Lịch sử xuất acc')
        self.batches=self._table(['Mã lô','Số acc','Trạng thái','Chi tiết','Thử tạo lại','Log tạo acc'])
        self.tabs.addTab(self.batches,'Tiến độ tạo acc')
        paging=QHBoxLayout()
        previous=QPushButton('Trang trước');previous.clicked.connect(lambda:self.move(-1));paging.addWidget(previous)
        self.page_label=QLabel();paging.addWidget(self.page_label)
        next_button=QPushButton('Trang sau');next_button.clicked.connect(lambda:self.move(1));paging.addWidget(next_button)
        paging.addStretch();layout.addLayout(paging)
        self.tabs.currentChanged.connect(lambda _i:self.load())
        self.timer=QTimer(self);self.timer.timeout.connect(lambda:self.load() if self.isVisible() else None);self.timer.start(2500)

    def _table(self,headers):
        table=QTableWidget(0,len(headers));table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide();table.verticalHeader().setDefaultSectionSize(40)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def load(self):
        if self.busy:return
        self.busy=True
        def result(r):
            self.busy=False;self.render(r)
            if self.tabs.currentIndex()==1:
                self.loader('stock.exports',{'offset':self.export_offset},lambda v:self.render_history(v['rows']),lambda:self._failed())
        self.loader('stock.list',{'offset':self.offset},result,self._failed)

    def _failed(self):
        self.busy=False

    def render(self,r):
        self.note.setText(r.get('error') or 'Dừng tích bean và chờ job kết thúc trước khi xuất/xóa acc. Đóng popup xuất sẽ đưa acc khỏi kho.')
        self.summary.setText(f"Tổng số acc: {r['total']} · Tổng bean: {r['beans']} · Đã chọn: {len(self.selected)}")
        self.page_label.setText(f"Acc {self.offset+1 if r['total'] else 0}–{min(self.offset+100,r['total'])} / {r['total']} · Trang xuất {self.export_offset//50+1}")
        signature=json.dumps(r,sort_keys=True)
        if signature==self.signature:return
        self.signature=signature;self.rows=r['rows']
        self.table.blockSignals(True);self.table.setRowCount(len(self.rows))
        for index,row in enumerate(self.rows):
            item=QTableWidgetItem();item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.ItemDataRole.UserRole,row['id']);item.setCheckState(Qt.CheckState.Checked if row['id'] in self.selected else Qt.CheckState.Unchecked)
            self.table.setItem(index,0,item)
            values=[row['id'],row['email'],row['password'],row['beans'],'Vô hạn' if row['infinite'] else row['target'],row['invite_code'],STATUS.get(row['status'],row['status'])]
            for column,value in enumerate(values,1):
                item=QTableWidgetItem(str(value));item.setToolTip(str(value));self.table.setItem(index,column,item)
            button=QPushButton('Dừng');button.setStyleSheet('font-size:11px;padding:3px;')
            button.setEnabled(row['status'] not in ('paused','exporting'))
            button.clicked.connect(lambda _checked=False,i=row['id']:self.stop_ids([i]))
            self.table.setCellWidget(index,8,button)
            actions=QWidget();action_box=QHBoxLayout(actions);action_box.setContentsMargins(0,0,0,0)
            for caption,jid in [('Log tích bean',row.get('job_id') or row.get('last_job_id')),('Log tạo acc',row.get('batch_id'))]:
                log=QPushButton(caption);log.setEnabled(bool(jid and self.open_logs))
                log.clicked.connect(lambda _c=False,j=jid:self.open_logs(j));action_box.addWidget(log)
            self.table.setCellWidget(index,9,actions)
            detail=QTableWidgetItem(row.get('message',''));detail.setToolTip(detail.text());self.table.setItem(index,10,detail)
        self.table.blockSignals(False)
        self.render_history(r['exports'])
        self.batches.setRowCount(len(r['batches']))
        for index,row in enumerate(r['batches']):
            for column,value in enumerate([row['id'],row['count'],STATUS.get(row['state'],row['state']),row['message']]):
                item=QTableWidgetItem(str(value));item.setToolTip(str(value));self.batches.setItem(index,column,item)
            button=QPushButton('Thử tạo lại');button.setEnabled(row['state']=='partial')
            button.clicked.connect(lambda _c=False,i=row['id']:self.command('stock.retry',{'batch_id':i},lambda _r:self.load()))
            self.batches.setCellWidget(index,4,button)
            log=QPushButton('Log tạo acc');log.setEnabled(self.open_logs is not None)
            log.clicked.connect(lambda _c=False,j=row['id']:self.open_logs(j));self.batches.setCellWidget(index,5,log)
        if self.offset and not self.rows:self.offset=max(0,self.offset-100)

    def render_history(self,rows):
        self.history.setRowCount(len(rows))
        for index,row in enumerate(rows):
            for column,value in enumerate([row['id'],row['count'],row['actor'],STATUS.get(row['state'],row['state']),row['created_at']]):
                self.history.setItem(index,column,QTableWidgetItem(str(value)))
            button=QPushButton('Xem / Sao chép')
            button.clicked.connect(lambda _c=False,i=row['id']:self.loader('stock.export.get',{'export_id':i},self.show_export,self._failed))
            self.history.setCellWidget(index,5,button)

    def move(self,direction):
        if self.tabs.currentIndex()==1:self.export_offset=max(0,self.export_offset+direction*50)
        else:self.offset=max(0,self.offset+direction*100)
        self.load()

    def check_changed(self,item):
        if item.column()!=0:return
        aid=item.data(Qt.ItemDataRole.UserRole)
        if item.checkState()==Qt.CheckState.Checked:self.selected.add(aid)
        else:self.selected.discard(aid)

    def select_page(self):
        for row in range(self.table.rowCount()):self.table.item(row,0).setCheckState(Qt.CheckState.Checked)

    def clear_selection(self):
        self.selected.clear()
        for row in range(self.table.rowCount()):self.table.item(row,0).setCheckState(Qt.CheckState.Unchecked)

    def change(self,action):
        if self.selected:
            if action=='stock.stop':self.stop_ids(sorted(self.selected))
            else:self.command(action,{'ids':sorted(self.selected)},lambda _r:self.load())

    def stop_ids(self,ids):
        if QMessageBox.question(self,'Dừng tích bean','Bạn có muốn dừng nhập mã cho '+str(len(ids))+' acc không?',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            self.command('stock.stop',{'ids':ids},lambda _r:self.load())

    def delete(self):
        if self.selected and QMessageBox.question(self,'Xóa acc','Bạn có muốn xóa các acc đã chọn không?',
                QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            self.command('stock.delete',{'ids':sorted(self.selected)},lambda _r:(self.clear_selection(),self.load()))

    def export_selected(self):
        if self.selected:self.command('stock.export.prepare',{'ids':sorted(self.selected)},self.show_export)

    def show_export(self,result):
        dialog=QDialog(self);dialog.setWindowTitle('Xuất acc · '+result['id']);dialog.resize(760,460)
        layout=QVBoxLayout(dialog);layout.addWidget(QLabel('Mỗi acc một dòng: mail|mật khẩu. Phiếu đã lưu trong Lịch sử xuất acc.'))
        text=QPlainTextEdit();text.setReadOnly(True);text.setPlainText(result['content']);layout.addWidget(text,1)
        actions=QHBoxLayout();copy=QPushButton('Sao chép');copy.clicked.connect(lambda:QApplication.clipboard().setText(text.toPlainText()));actions.addWidget(copy)
        close=QPushButton('Đóng / Hoàn tất xuất');close.clicked.connect(dialog.accept);actions.addWidget(close);layout.addLayout(actions)
        def closed(_code):
            if result['state']=='pending':self.command('stock.export.finish',{'export_id':result['id']},lambda _r:(self.clear_selection(),self.load()))
            dialog.deleteLater()
        dialog.finished.connect(closed);dialog.setModal(True);dialog.open()


def install_local_stock(m):
    from core.remote.runtime import Runtime
    fake=SimpleNamespace(mochi=m)
    def command(action,payload,callback=None):
        try:
            result=getattr(Runtime,'do_'+action.replace('.','_'))(fake,payload)
            if callback:callback(result)
        except Exception as exc:
            QMessageBox.warning(m,'Kho tài khoản',str(exc) if isinstance(exc,ValueError) else 'Chưa xử lý được kho acc.')
    def loader(action,payload,callback,on_error):
        try:callback(getattr(Runtime,'do_'+action.replace('.','_'))(fake,payload))
        except Exception:on_error()
    box=m.inventory_page.layout()
    for index in range(box.count()):
        widget=box.itemAt(index).widget()
        if widget:widget.hide()
    m.stock_inventory_panel=StockInventoryPanel(command,loader,m.inventory_page,open_logs=m.show_job_logs)
    box.addWidget(m.stock_inventory_panel,1)
    m.stock_create_panel=StockCreateForm(command,lambda:m.input_pids.text(),m)
    m.card_stock.setEnabled(True)
    m.card_stock.clicked.connect(m._select_job_type)
    # The card belongs to the page; attach the form to its enclosing form layout.
    target=m.card_stock.parentWidget()
    while target is not None and target.objectName()!='mochiFormCard':target=target.parentWidget()
    if target is not None:target.layout().insertWidget(2,m.stock_create_panel)
    m.stock_create_panel.hide()
    original=m._select_job_type
    def select(key):
        original(key);m.card_stock.set_selected(key=='STOCK_ACCOUNT');m.stock_create_panel.setVisible(key=='STOCK_ACCOUNT')
        m.btn_start.setText('Bắt đầu tạo acc tích bean' if key=='STOCK_ACCOUNT' else 'Tạo & chạy job')
        for field in (m.input_code,m.input_target_points,m.input_customer,m.input_total_vnd,m.chk_combo_job,m.chk_schedule_job):
            field.setEnabled(key!='STOCK_ACCOUNT')
    m._select_job_type=select
    m.card_stock.clicked.disconnect();m.card_invite.clicked.disconnect()
    m.card_stock.clicked.connect(select);m.card_invite.clicked.connect(select)


def remote_inventory(window):
    def loader(action,payload,callback,on_error):
        if window.client is None or not window._connected or window._closing:
            on_error();return
        client=window.client
        def result(response):
            if window.client is client:callback(response.get('result',{}))
            else:on_error()
        worker=window.task(lambda:client.command(action,payload),result)
        worker.error.connect(lambda _e:on_error())
    return StockInventoryPanel(lambda a,p,c=None:window.send(a,p,c),loader,window,open_logs=window.open_job_logs)
