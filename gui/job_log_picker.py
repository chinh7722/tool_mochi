"""Server-side job identity picker; does not replace the currently streamed log."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLineEdit,QLabel,QPushButton


class JobLogPicker(QWidget):
    def __init__(self,window):
        super().__init__();self.window=window;self.offset=0;self.total=0;self.generation=0
        from gui.remote_window import table
        box=QVBoxLayout(self);box.setContentsMargins(0,0,0,0)
        self.search=QLineEdit();self.search.setMaxLength(160)
        self.search.setPlaceholderText('Tìm theo mã mời, mã job, người tạo hoặc Chat ID…');box.addWidget(self.search)
        self.table=table(['Mã job','Mã mời','Người tạo','Trạng thái'])
        for column,width in enumerate((200,130,230)):self.table.setColumnWidth(column,width)
        self.table.setMaximumHeight(160);self.table.setMinimumHeight(110);box.addWidget(self.table)
        self.table.cellDoubleClicked.connect(lambda row,_:self.open(row))
        actions=QHBoxLayout()
        for caption,callback in [('Trang trước',lambda:self.turn(-1)),('Trang sau',lambda:self.turn(1)),('Xem log',lambda:self.open(self.table.currentRow()))]:
            b=QPushButton(caption);b.clicked.connect(callback);actions.addWidget(b)
        self.status=QLabel('Nhập từ khóa để tìm cả job cũ đã lưu trên VPS.');actions.addWidget(self.status,1);box.addLayout(actions)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(250);self.timer.timeout.connect(self.load)
        self.search.textChanged.connect(self.changed)

    def changed(self):
        self.offset=0;self.generation+=1;self.timer.start()

    def turn(self,direction):
        offset=self.offset+direction*100
        if 0<=offset<self.total:self.offset=offset;self.generation+=1;self.load()

    def open(self,row):
        if 0<=row<len(self.table._rows):self.window.open_job_logs(self.table._rows[row]['job_id'])

    def load(self):
        w=self.window
        if not w.client or not w._connected or w._closing:return
        client=w.client;generation=self.generation;offset=self.offset;query=self.search.text()
        def display(response):
            if client is not w.client or generation!=self.generation:return
            from gui.remote_window import fill
            r=response['result'];self.total=r['total']
            fill(self.table,[{**v,'person':f"{v['name']} ({v['chat_id']})"} for v in r['rows']],
                ['job_id','code','person','status'],identity_key='job_id')
            self.status.setText(f'{min(offset+1,self.total)}–{min(offset+100,self.total)} / {self.total} job · Nhấp đúp để xem log')
        w.task(lambda:client.command('job.search',dict(query=query,offset=offset)),display)
