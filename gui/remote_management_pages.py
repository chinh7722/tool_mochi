"""Price milestones and disposable history views backed by the VPS."""
from decimal import Decimal
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QSpinBox,QMessageBox,QPushButton
from core.finance import money_units


class PricingPanel(QWidget):
    def __init__(self,window):
        super().__init__();self.window=window;self.rows=[];self.dirty=False
        from gui.remote_window import table
        box=QVBoxLayout(self);box.setContentsMargins(24,20,24,16)
        title=QLabel('Bảng giá');title.setStyleSheet('font-size:24px;font-weight:700');box.addWidget(title)
        note=QLabel('Nhập giá cho một mốc bean, ví dụ 2.000 bean = 15.000 VND. Giá job tính theo tỷ lệ; '
            'nhiều mốc dùng mốc cao nhất không vượt số bean của job. Giá cũ của job đã tạo được giữ nguyên.');note.setWordWrap(True);box.addWidget(note)
        form=QHBoxLayout();self.beans=QSpinBox();self.beans.setRange(100,1_000_000);self.beans.setSingleStep(100);self.beans.setValue(2000)
        self.price=QLineEdit();self.price.setPlaceholderText('15000 (VND)')
        form.addWidget(QLabel('Mốc bean'));form.addWidget(self.beans);form.addWidget(QLabel('Giá VND'));form.addWidget(self.price)
        window.button('Thêm / Cập nhật',self.add,form,mutation=False);box.addLayout(form)
        self.table=table(['Mốc bean','Giá bán (VND)']);self.table.cellClicked.connect(self.select);box.addWidget(self.table,1)
        actions=QHBoxLayout();window.button('Xóa mốc đang chọn',self.remove,actions,mutation=False)
        window.button('Lưu bảng giá',self.save,actions);box.addLayout(actions)
        self.status=QLabel('Bảng rỗng: dùng đơn giá VND / 100 bean ở Cài đặt chung.');self.status.setWordWrap(True);box.addWidget(self.status)

    def refresh(self,state):
        if not self.dirty:
            self.rows=list(state.get('settings',{}).get('prices',[]));self.render()
            self.status.setText(f'{len(self.rows)} mốc giá đã lưu trên VPS; dùng cho job tạo mới.' if self.rows else 'Bảng rỗng: dùng đơn giá VND / 100 bean ở Cài đặt chung.')

    def render(self):
        from gui.remote_window import fill
        self.rows.sort(key=lambda r:r['beans'])
        fill(self.table,[dict(beans=r['beans'],price=format(Decimal(r['price_units'])/100,',.2f')) for r in self.rows],['beans','price'])

    def select(self,row,*_):
        if 0<=row<len(self.rows):
            self.beans.setValue(self.rows[row]['beans']);self.price.setText(str(Decimal(self.rows[row]['price_units'])/100))

    def add(self):
        try:
            from core.pricing import validate_prices
            new=dict(beans=self.beans.value(),price_units=money_units(self.price.text()))
            self.rows=validate_prices([r for r in self.rows if r['beans']!=new['beans']]+[new])
            self.dirty=True;self.render();self.status.setText('Chưa lưu. Bấm Lưu bảng giá để áp dụng trên VPS.')
        except ValueError as exc:self.status.setText(str(exc))

    def remove(self):
        row=self.table.currentRow()
        if 0<=row<len(self.rows):self.rows.pop(row);self.dirty=True;self.render();self.status.setText('Chưa lưu thay đổi.')

    def save(self):
        def saved(result):self.rows=result['prices'];self.dirty=False;self.render();self.status.setText(result['message'])
        self.window.send('pricing.save',{'prices':[dict(row) for row in self.rows]},saved)


class TemporaryHistoryPanel(QWidget):
    def __init__(self,window):
        super().__init__();self.window=window;self.offset=0;self.total=0;self.busy=False;self.pending=False
        self.row_buttons=[]
        from gui.remote_window import table
        box=QVBoxLayout(self)
        note=QLabel('Lịch sử hiển thị tạm của job đã kết thúc. Xóa ở đây chỉ ẩn các job hiện có; '
            'job kết thúc sau đó sẽ xuất hiện. Lịch sử Thống kê và log không bị xóa.');note.setWordWrap(True);box.addWidget(note)
        self.table=table(['Job','Người tạo','Mã mời','Trạng thái','Bean thực tế','Kết thúc','Thao tác'])
        self.table.cellDoubleClicked.connect(lambda row,_:window.open_job_logs(self.table._rows[row]['job_id']) if row<len(self.table._rows) else None)
        box.addWidget(self.table,1)
        actions=QHBoxLayout()
        window.button('Log',self.logs,actions);window.button('Tiếp tục job thất bại',self.resume,actions)
        window.button('Xóa lịch sử tạm',self.clear,actions);window.button('Làm mới',self.load,actions,mutation=False);box.addLayout(actions)
        actions=QHBoxLayout();window.button('Trang trước',lambda:self.turn(-1),actions,mutation=False)
        self.status=QLabel();actions.addWidget(self.status);window.button('Trang sau',lambda:self.turn(1),actions,mutation=False);box.addLayout(actions)

    def turn(self,direction):
        next_offset=self.offset+direction*100
        if 0<=next_offset<self.total:self.offset=next_offset;self.load()

    def logs(self):
        jid=self.window.selected(self.table,'job_id')
        if jid:self.window.open_job_logs(jid)

    def resume(self):
        jid=self.window.selected(self.table,'job_id')
        row=self.table.currentRow()
        if jid and self.table._rows[row].get('can_continue'):
            self.window.send('job.continue',dict(job_id=jid),self.window.job_created)
        elif jid:self.status.setText('Chỉ tiếp tục job thất bại còn thiếu bean và chưa có job con.')

    def clear(self):
        answer=QMessageBox.question(self,'Xác nhận xóa lịch sử tạm',
            'Bạn có muốn xóa lịch sử hiển thị tạm ở Mochi Chat? Lịch sử Thống kê và log vẫn được giữ.',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer==QMessageBox.StandardButton.Yes:
            self.window.send('history.temporary.clear',{},lambda r:self.cleared(r))

    def cleared(self,result):self.offset=0;self.load()

    def set_enabled(self,enabled):
        for button,allowed in self.row_buttons:button.setEnabled(enabled and allowed)

    def load(self,*_):
        w=self.window
        if not w.client or not w._connected or w._closing:return
        if self.busy:self.pending=True;return
        self.busy=True;client=w.client
        def display(response):
            if client is not w.client:return
            from gui.remote_window import fill
            from gui.remote_workspace import format_time
            result=response['result'];self.total=result['total']
            rows=[{**r,'person':f"{r['name']} ({r['chat_id']})",'beans':r['success_count']*100,
                'status_label':{'failed':'Thất bại','completed':'Hoàn thành','cancelled':'Đã hủy'}.get(r['status'],r['status']),
                'finished':format_time(r['finalized_at'])} for r in result['rows']]
            for row in range(self.table.rowCount()):
                old=self.table.cellWidget(row,6)
                if old is not None:old.hide();self.table.removeCellWidget(row,6)
            fill(self.table,rows,['job_id','person','code','status_label','beans','finished','actions'])
            self.row_buttons=[]
            for index,r in enumerate(rows):
                widget=QWidget();layout=QHBoxLayout(widget);layout.setContentsMargins(2,2,2,2)
                for caption,callback,allowed in (
                    ('Log',lambda _=False,jid=r['job_id']:w.open_job_logs(jid),True),
                    ('Tiếp tục',lambda _=False,jid=r['job_id']:w.send('job.continue',{'job_id':jid},w.job_created),bool(r['can_continue']))):
                    button=QPushButton(caption);button.setStyleSheet('padding:4px 8px;font-size:11px;')
                    button.clicked.connect(callback);layout.addWidget(button);self.row_buttons.append((button,allowed))
                self.table.setCellWidget(index,6,widget)
            self.set_enabled(w._connected and not w._mutating and not w._closing)
            self.status.setText(f'{min(self.offset+1,self.total)}–{min(self.offset+100,self.total)} / {self.total} job tạm')
            if self.offset and not rows:self.offset=max(0,self.offset-100);self.pending=True
        worker=w.task(lambda:client.command('history.temporary',dict(offset=self.offset)),display)
        def done():
            self.busy=False
            if self.pending:self.pending=False;self.load()
        worker.finished.connect(done)
