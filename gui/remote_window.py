"""Windows management workspace. Closing it never stops the VPS runtime."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv
import json
from pathlib import Path

from PySide6.QtCore import QDateTime, QSettings, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QDateTimeEdit, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)
from gui.branding import BrandSidebar, WORKSPACE_STYLE, icon
from core.remote.client import RemoteClient
from core.remote.protocol import RemoteError


class RequestWorker(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback

    def run(self):
        try:
            self.result.emit(self.callback())
        except RemoteError as exc:
            self.error.emit(str(exc))
        except Exception:
            self.error.emit('Không đọc được phản hồi từ VPS.')
        finally:
            self.callback = None


def table(headers):
    result = QTableWidget(0,len(headers))
    result.setHorizontalHeaderLabels(headers)
    result.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    result.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    result.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    result.horizontalHeader().setStretchLastSection(True)
    result.verticalHeader().hide()
    result._rows = []
    return result


def fill(widget, rows, keys):
    selected = widget.currentRow()
    prior = widget._rows[selected].get(keys[0]) if 0<=selected<len(widget._rows) else None
    widget.setRowCount(len(rows))
    widget._rows = rows
    for row,values in enumerate(rows):
        for column,key in enumerate(keys):
            value = values.get(key,'')
            item = QTableWidgetItem('' if value is None else str(value))
            item.setToolTip(item.text())
            widget.setItem(row,column,item)
        if values.get(keys[0])==prior:
            widget.selectRow(row)


class RemoteMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('hoshino-tool · Quản lý VPS')
        self.setWindowIcon(icon('app_icon'))
        self.resize(1280,900)
        self.setMinimumSize(1120,760)
        self.setStyleSheet(WORKSPACE_STYLE + '''
            QMainWindow,QWidget {background:#18181b;color:#edf2fc;font-family:'Segoe UI';font-size:13px;}
            QLineEdit,QSpinBox,QComboBox,QPlainTextEdit,QDateTimeEdit,QTableWidget {
                background:#232326;border:1px solid #41414a;border-radius:5px;padding:7px;}
            QPushButton {background:#29292e;border:1px solid #41414a;border-radius:6px;padding:9px 13px;}
            QPushButton:hover {background:#303744;border-color:#3b82f6;}
            QPushButton:disabled {color:#71717a;}
            QHeaderView::section {background:#29292e;padding:8px;border:1px solid #41414a;}
            QTabBar::tab {padding:10px 14px;background:#232326;}
            QTabBar::tab:selected {background:#253654;color:#60a5fa;}
        ''')
        self.preferences = QSettings('Hermit Coder','Hoshino Remote')
        self.client = None
        self.snapshot = {}
        self._workers = set()
        self._polling = False
        self._mutating = False
        self._closing = False
        self._mutation_buttons = []
        self._config_loaded = False
        container = QWidget()
        root = QHBoxLayout(container)
        sidebar = BrandSidebar()
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        title = QLabel('hoshino-tool')
        title.setStyleSheet('font-size:24px;font-weight:800;padding:18px 0;')
        side.addWidget(title)
        side.addWidget(QLabel('QUẢN LÝ VPS'))
        self.pages = QStackedWidget()
        self.nav = []
        for caption,builder in (
            ('Dashboard',self.build_dashboard),('Mochi Chat',self.build_mochi),
            ('Proxy Pool',lambda:self.build_proxy('account')),
            ('Proxy mua mail',lambda:self.build_proxy('mail')),
            ('Telegram',self.build_telegram),('Cài đặt chung',self.build_settings)):
            page = builder()
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            index = self.pages.addWidget(scroll)
            button = QPushButton(caption)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False,i=index:self.activate(i))
            self.nav.append(button)
            side.addWidget(button)
        side.addStretch()
        note = QLabel('Job và bot chạy trên VPS.\nĐóng cửa sổ này vẫn tiếp tục chạy.')
        note.setWordWrap(True)
        side.addWidget(note)
        root.addWidget(sidebar)
        right = QVBoxLayout()
        connection = QHBoxLayout()
        self.url = QLineEdit(str(self.preferences.value('url','http://127.0.0.1:8765')))
        self.url.setPlaceholderText('Địa chỉ qua SSH tunnel')
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText('Token quản lý VPS')
        self.connect_button = QPushButton('Kết nối VPS')
        self.connect_button.clicked.connect(self.connect_server)
        for widget in (self.url,self.token,self.connect_button):
            connection.addWidget(widget)
        right.addLayout(connection)
        self.status = QLabel('Mở SSH tunnel rồi kết nối VPS.')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        right.addWidget(self.pages,1)
        root.addLayout(right,1)
        self.setCentralWidget(container)
        self.activate(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(2500)
        # Deployment may provision a local, gitignored API token; never an SSH password.
        root_path = Path(__file__).resolve().parents[1]
        if root_path.name=='src':
            root_path = root_path.parent
        self.connection_path = root_path/'data'/'remote_connection.json'
        try:
            config = json.loads(self.connection_path.read_text(encoding='utf-8'))
            self.url.setText(config.get('url','http://127.0.0.1:8765'))
            self.token.setText(config.get('token',''))
        except (OSError,ValueError,TypeError):
            pass
        self.enable_actions(False)

    def activate(self,index):
        self.pages.setCurrentIndex(index)
        for number,button in enumerate(self.nav):
            button.setChecked(number==index)

    def page(self,caption):
        widget = QWidget()
        box = QVBoxLayout(widget)
        box.setContentsMargins(20,20,20,20)
        title = QLabel(caption)
        title.setStyleSheet('font-size:24px;font-weight:700;')
        box.addWidget(title)
        return widget,box

    def button(self,caption,callback,layout,*,mutation=True):
        button = QPushButton(caption)
        button.clicked.connect(callback)
        layout.addWidget(button)
        if mutation:
            self._mutation_buttons.append(button)
        return button

    def selected(self,widget,key):
        row = widget.currentRow()
        if not 0<=row<len(widget._rows):
            self.status.setText('Chọn một dòng trước khi thao tác.')
            return None
        return widget._rows[row].get(key)

    def enable_actions(self,enabled):
        for button in self._mutation_buttons:
            button.setEnabled(enabled and not self._mutating and not self._closing)

    def task(self,callback,on_result):
        worker = RequestWorker(callback,self)
        self._workers.add(worker)
        worker.result.connect(on_result)
        worker.error.connect(self.failure)
        def done():
            self._workers.discard(worker)
            worker.deleteLater()
            if self._closing and not self._workers:
                self.close()
        worker.finished.connect(done)
        worker.start()
        return worker

    def failure(self,message):
        self.status.setText(message)

    def connect_server(self):
        if self._workers:
            self.status.setText('Chờ yêu cầu hiện tại kết thúc trước khi đổi kết nối.')
            return
        try:
            self.client = RemoteClient(self.url.text().strip(),self.token.text().strip())
        except RemoteError as exc:
            self.failure(str(exc))
            return
        self.preferences.setValue('url',self.url.text().strip())
        self._config_loaded = False
        self.poll()

    def poll(self):
        if self.client is None or self._polling or self._mutating or self._closing:
            return
        self._polling = True
        worker = self.task(self.client.state,self.render)
        worker.error.connect(lambda _:self.enable_actions(False))
        worker.finished.connect(lambda:setattr(self,'_polling',False))

    def send(self,action,payload=None,callback=None):
        if self.client is None or self._mutating or self._closing:
            return
        self._mutating = True
        self.enable_actions(False)
        client = self.client
        def received(response):
            result = response.get('result',{})
            self.status.setText(result.get('message','Đã thực hiện trên VPS.'))
            if callback:
                callback(result)
        worker = self.task(lambda:client.command(action,payload),received)
        def done():
            self._mutating = False
            self.enable_actions(bool(self.snapshot))
            self.poll()
        worker.finished.connect(done)

    def build_dashboard(self):
        page,box = self.page('Dashboard')
        self.dashboard_summary = QLabel('Chưa kết nối VPS.')
        self.dashboard_summary.setStyleSheet('font-size:18px;padding:20px;')
        self.dashboard_summary.setWordWrap(True)
        box.addWidget(self.dashboard_summary)
        self.dashboard_jobs = table(['Mã job','Mã mời','Người tạo','Trạng thái','Thành công','Mục tiêu'])
        box.addWidget(self.dashboard_jobs,1)
        return page

    def build_mochi(self):
        page,box = self.page('Mochi Chat')
        tabs = QTabWidget()
        setup = QWidget(); form = QFormLayout(setup)
        self.job_code = QLineEdit(); self.job_customer = QLineEdit()
        self.job_points = QSpinBox(); self.job_points.setRange(1,1000000); self.job_points.setValue(1000)
        self.job_batch = QComboBox(); self.job_batch.addItem('Mua mail theo cấu hình VPS','')
        self.schedule = QCheckBox('Hẹn giờ chạy (giờ Việt Nam)')
        self.schedule_time = QDateTimeEdit()
        self.schedule_time.setCalendarPopup(True)
        self.schedule_time.setDisplayFormat('dd/MM/yyyy HH:mm')
        vn = datetime.now(timezone(timedelta(hours=7)))+timedelta(hours=1)
        self.schedule_time.setDateTime(QDateTime.fromString(vn.strftime('%Y-%m-%d %H:%M'),'yyyy-MM-dd HH:mm'))
        for label,field in (('Mã mời',self.job_code),('Số bean',self.job_points),('Khách hàng / Job',self.job_customer),
                ('Lô mail',self.job_batch),('',self.schedule),('Giờ chạy',self.schedule_time)):
            form.addRow(label,field)
        actions = QHBoxLayout()
        self.button('Tạo job trên VPS',self.create_job,actions)
        self.button('Đưa lô mail đã mua lên VPS',self.upload_batch,actions)
        self.button('Làm mới danh sách lô',lambda:self.send('batch.list',callback=self.show_batches),actions)
        form.addRow(actions)
        tabs.addTab(setup,'Thiết lập job')
        running = QWidget(); running_box = QVBoxLayout(running)
        self.jobs = table(['Mã job','Khách hàng / Job','Người tạo','Mã mời','Trạng thái','Thành công','Mục tiêu','Hẹn giờ'])
        running_box.addWidget(self.jobs)
        actions = QHBoxLayout()
        self.button('Dừng / Hủy lịch',lambda:self.selected_send(self.jobs,'job_id','job.stop'),actions)
        self.button('Xem log',self.load_logs,actions)
        running_box.addLayout(actions)
        tabs.addTab(running,'Job đang chạy')
        logs = QWidget(); logs_box = QVBoxLayout(logs)
        self.logs = QPlainTextEdit(); self.logs.setReadOnly(True)
        logs_box.addWidget(self.logs)
        tabs.addTab(logs,'Log job')
        self.history = table(['Mã job','Mã đơn','Người tạo','Mã mời','Trạng thái','Bean thực tế','Kết thúc'])
        tabs.addTab(self.history,'Lịch sử job')
        lookup = QWidget(); lookup_box = QVBoxLayout(lookup); lookup_form = QFormLayout()
        self.lookup_email = QLineEdit(); self.lookup_password = QLineEdit()
        self.lookup_password.setEchoMode(QLineEdit.EchoMode.Password)
        lookup_form.addRow('Email Google',self.lookup_email); lookup_form.addRow('Mật khẩu',self.lookup_password)
        lookup_box.addLayout(lookup_form); actions = QHBoxLayout()
        self.button('Đăng nhập & lấy mã',self.lookup_start,actions)
        self.button('Hủy',lambda:self.send('lookup.cancel'),actions)
        self.button('Sao chép mã',self.copy_invite,actions,mutation=False)
        self.button('Xuất CSV',self.export_profiles,actions,mutation=False)
        lookup_box.addLayout(actions)
        self.lookup_status = QLabel(); self.lookup_status.setWordWrap(True); lookup_box.addWidget(self.lookup_status)
        self.profiles = table(['Email','ID tài khoản','Tên hiển thị','Mã mời','Beans','Cập nhật'])
        lookup_box.addWidget(self.profiles)
        tabs.addTab(lookup,'Lấy mã mời')
        box.addWidget(tabs,1)
        self.mochi_tabs = tabs
        return page

    def create_job(self):
        payload = dict(code=self.job_code.text().strip(),points=self.job_points.value(),
            customer=self.job_customer.text(),batch=self.job_batch.currentData() or '')
        if self.schedule.isChecked():
            payload['scheduled_at'] = self.schedule_time.dateTime().toString('yyyy-MM-ddTHH:mm:ss')+'+07:00'
        self.send('job.create',payload)

    def selected_send(self,widget,key,action,extra=None):
        value = self.selected(widget,key)
        if value is not None:
            self.send(action,{key:value,**(extra or {})})

    def load_logs(self):
        jid = self.selected(self.jobs,'job_id')
        if jid:
            self.send('job.logs',{'job_id':jid},lambda r:(self.logs.setPlainText('\n'.join(r['lines'])),self.mochi_tabs.setCurrentIndex(2)))

    def upload_batch(self):
        path,_ = QFileDialog.getOpenFileName(self,'Chọn lô mail đã mua','','JSON (*.json)')
        if not path:
            return
        try:
            if Path(path).stat().st_size>1500000:
                raise ValueError()
            data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            if not isinstance(data,dict) or not isinstance(data.get('accounts'),list):
                raise ValueError()
        except (OSError,ValueError):
            self.status.setText('Không đọc được lô mail hoặc file quá lớn.')
            return
        self.send('batch.upload',{'accounts':data['accounts']},
            lambda r:(self.job_batch.addItem(r['batch'],r['batch']),self.job_batch.setCurrentIndex(self.job_batch.count()-1)))

    def show_batches(self,result):
        chosen = self.job_batch.currentData()
        self.job_batch.clear(); self.job_batch.addItem('Mua mail theo cấu hình VPS','')
        for name in result['batches']:
            self.job_batch.addItem(name,name)
        self.job_batch.setCurrentIndex(max(0,self.job_batch.findData(chosen)))

    def lookup_start(self):
        password = self.lookup_password.text()
        self.lookup_password.clear()
        self.send('lookup.start',{'email':self.lookup_email.text(),'password':password})

    def copy_invite(self):
        code = self.selected(self.profiles,'invite_code')
        if code:
            QApplication.clipboard().setText(code)

    def export_profiles(self):
        rows = self.snapshot.get('lookup',{}).get('profiles',[])
        if not rows:
            return
        path,_ = QFileDialog.getSaveFileName(self,'Xuất tài khoản','mochi_accounts.csv','CSV (*.csv)')
        if not path:
            return
        from core.platforms.mochichat.account_lookup import AccountProfile,export_profiles_csv
        try:
            export_profiles_csv(path,[AccountProfile(**row) for row in rows])
            self.status.setText('Đã xuất CSV.')
        except (OSError,ValueError):
            self.status.setText('Chưa ghi được CSV. Kiểm tra file đang mở.')

    def build_proxy(self,pool):
        page,box = self.page('Proxy Pool' if pool=='account' else 'Proxy mua mail')
        provider = QComboBox(); provider.addItem('Proxy.vn','proxyvn'); provider.addItem('muaproxyxoay.net','muaproxyxoay')
        box.addWidget(provider)
        text = QPlainTextEdit(); text.setMaximumHeight(115)
        text.setPlaceholderText('Mỗi dòng một proxy: ip:port|key_xoay hoặc ip:port:user:pass|key_xoay')
        box.addWidget(text); actions = QHBoxLayout()
        self.button('Thêm proxy',lambda:self.send('proxy.import',dict(pool=pool,provider=provider.currentData(),text=text.toPlainText()),lambda _:text.clear()),actions)
        self.button('Nhập TXT',lambda:self.import_proxy_file(pool,provider),actions)
        self.button('Kiểm tra tất cả',lambda:self.send('proxy.check',{'pool':pool}),actions)
        box.addLayout(actions)
        listing = table(['ID','Proxy','Trạng thái','Nhà cung cấp','Tốc độ','Lỗi'])
        setattr(self,'proxy_table_'+pool,listing)
        box.addWidget(listing,1); actions = QHBoxLayout()
        for caption,action in (('Kiểm tra','proxy.check'),('Đổi IP','proxy.rotate'),('Xóa','proxy.delete')):
            self.button(caption,lambda _=False,a=action:self.selected_send(listing,'id',a,{'pool':pool}),actions)
        box.addLayout(actions)
        return page

    def import_proxy_file(self,pool,provider):
        path,_ = QFileDialog.getOpenFileName(self,'Nhập proxy','','TXT (*.txt)')
        if path:
            try:
                raw = Path(path).read_bytes()
                if len(raw)>500000:
                    raise ValueError()
                encoding = 'utf-32' if raw.startswith((b'\xff\xfe\x00\x00',b'\x00\x00\xfe\xff')) else (
                    'utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
                text = raw.decode(encoding)
            except (OSError,ValueError):
                self.status.setText('Không đọc được TXT hoặc file quá lớn.'); return
            self.send('proxy.import',dict(pool=pool,provider=provider.currentData(),text=text))

    def build_telegram(self):
        page,box = self.page('Telegram')
        tabs = QTabWidget(); box.addWidget(tabs)
        users = QWidget(); users_box = QVBoxLayout(users)
        self.users = table(['Chat ID','Tên','Vai trò','Cấp quyền','Job xong','Job lỗi','Bean','Doanh thu','Chi phí'])
        users_box.addWidget(self.users)
        form = QFormLayout(); self.user_id = QLineEdit(); self.user_name = QLineEdit()
        self.user_role = QComboBox(); self.user_role.addItems(['partner','admin'])
        for caption,field in (('Chat ID',self.user_id),('Tên',self.user_name),('Vai trò',self.user_role)):
            form.addRow(caption,field)
        self.user_gmail = QLineEdit(); self.user_gmail.setEchoMode(QLineEdit.EchoMode.Password)
        self.user_trieu = QLineEdit(); self.user_trieu.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('API GmailVip riêng',self.user_gmail); form.addRow('API 1TrieuMail riêng',self.user_trieu)
        users_box.addLayout(form); actions = QHBoxLayout()
        self.button('Thêm / Lưu người dùng',self.save_user,actions)
        self.button('Lưu 2 API riêng',self.save_user_keys,actions)
        self.button('Thu hồi quyền',lambda:self.selected_send(self.users,'chat_id','user.active',{'active':False}),actions)
        self.button('Khôi phục quyền',lambda:self.selected_send(self.users,'chat_id','user.active',{'active':True}),actions)
        self.button('Xem job',lambda:self.selected_details(self.users,'chat_id','user.jobs'),actions)
        users_box.addLayout(actions)
        self.users.cellClicked.connect(self.pick_user)
        tabs.addTab(users,'Người dùng')
        bot = QWidget(); bot_box = QVBoxLayout(bot); form = QFormLayout()
        self.bot_token = QLineEdit(); self.bot_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.bot_auto = QCheckBox('Tự bật bot khi dịch vụ VPS khởi động')
        form.addRow('Bot Token (để trống giữ nguyên)',self.bot_token); form.addRow('',self.bot_auto)
        bot_box.addLayout(form); actions = QHBoxLayout()
        self.button('Lưu cấu hình bot',self.save_bot,actions)
        self.button('Bật bot',lambda:self.send('telegram.start'),actions)
        self.button('Tắt bot',lambda:self.send('telegram.stop'),actions)
        bot_box.addLayout(actions)
        self.bot_status = QLabel(); self.bot_status.setWordWrap(True); bot_box.addWidget(self.bot_status)
        bot_box.addWidget(QLabel('Gửi thông báo đến admin / partner'))
        self.broadcast = QPlainTextEdit(); bot_box.addWidget(self.broadcast)
        actions = QHBoxLayout()
        self.button('Gửi thông báo',lambda:self.send('telegram.broadcast',{'text':self.broadcast.toPlainText()}),actions)
        self.button('Dừng gửi',lambda:self.send('telegram.cancel_broadcast'),actions)
        bot_box.addLayout(actions)
        self.broadcast_status = QLabel(); self.broadcast_status.setWordWrap(True); bot_box.addWidget(self.broadcast_status)
        tabs.addTab(bot,'Bot và thông báo')
        customers = QWidget(); customer_box = QVBoxLayout(customers)
        self.customers = table(['Chat ID','Tên','Số dư khả dụng','Đơn đã tạo','Hoàn thành','Thất bại'])
        customer_box.addWidget(self.customers)
        self.customer_id = QLineEdit(); self.customer_id.setPlaceholderText('Chat ID để thêm / khôi phục')
        customer_box.addWidget(self.customer_id); actions = QHBoxLayout()
        self.button('Thêm / Khôi phục',lambda:self.numeric_send('customer.save','chat_id',self.customer_id),actions)
        self.button('Xóa khách',lambda:self.selected_send(self.customers,'chat_id','customer.delete'),actions)
        self.button('Xem ví / đơn',lambda:self.selected_details(self.customers,'chat_id','customer.history'),actions)
        self.button('Sửa số dư',self.edit_balance,actions)
        customer_box.addLayout(actions)
        self.settle_id = QLineEdit(); self.settle_id.setPlaceholderText('Mã job gián đoạn đã kiểm tra log')
        customer_box.addWidget(self.settle_id); actions = QHBoxLayout()
        self.button('Chốt đơn gián đoạn',lambda:self.send('customer.settle',{'job_id':self.settle_id.text()}),actions)
        customer_box.addLayout(actions)
        tabs.addTab(customers,'Khách hàng')
        sepay = QWidget(); sepay_box = QVBoxLayout(sepay); form = QFormLayout(); self.sepay_fields = {}
        for key,caption in (('token','Token SePay v2 (để trống giữ nguyên)'),('bank_id','Bank Account ID (UUID)'),
                ('account_number','Số tài khoản'),('bank_name','Tên ngân hàng'),('account_name','Chủ tài khoản')):
            field = QLineEdit(); self.sepay_fields[key] = field
            if key=='token':
                field.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow(caption,field)
        self.sepay_auto = QCheckBox('Tự bật đồng bộ khi dịch vụ VPS khởi động')
        form.addRow('',self.sepay_auto); sepay_box.addLayout(form); actions = QHBoxLayout()
        self.button('Lưu SePay',self.save_sepay,actions)
        self.button('Bật đồng bộ',lambda:self.send('sepay.start'),actions)
        self.button('Tắt đồng bộ',lambda:self.send('sepay.stop'),actions)
        sepay_box.addLayout(actions)
        self.sepay_status = QLabel(); self.sepay_status.setWordWrap(True); sepay_box.addWidget(self.sepay_status)
        self.transactions = table(['ID giao dịch','Chat ID','Số tiền','Nội dung','Trạng thái','Lý do'])
        sepay_box.addWidget(self.transactions)
        self.review_id = QLineEdit(); self.review_id.setPlaceholderText('Chat ID nhận giao dịch cần đối soát')
        sepay_box.addWidget(self.review_id); actions = QHBoxLayout()
        self.button('Đối soát giao dịch đã chọn',self.reconcile_sepay,actions)
        sepay_box.addLayout(actions)
        tabs.addTab(sepay,'Thanh toán SePay')
        finance = QWidget(); finance_box = QVBoxLayout(finance); actions = QHBoxLayout()
        self.button('Xem giao dịch / đối soát',lambda:self.send('finance.ledger',callback=self.details),actions)
        finance_box.addLayout(actions); form = QFormLayout()
        self.finance_id = QLineEdit(); self.finance_amount = QLineEdit()
        form.addRow('Mã giao dịch / Job',self.finance_id); form.addRow('Số tiền VND',self.finance_amount)
        finance_box.addLayout(form); actions = QHBoxLayout()
        self.button('Chốt chi phí giao dịch',lambda:self.send('finance.reconcile',{'purchase_id':self.finance_id.text(),'amount':self.finance_amount.text()}),actions)
        self.button('Bổ sung đơn giá job',lambda:self.send('finance.price',{'job_id':self.finance_id.text(),'amount':self.finance_amount.text()}),actions)
        finance_box.addLayout(actions); finance_box.addStretch()
        tabs.addTab(finance,'Tài chính')
        return page

    def pick_user(self,row,_):
        if 0<=row<len(self.users._rows):
            user = self.users._rows[row]
            self.user_id.setText(str(user['chat_id'])); self.user_name.setText(user['name'])
            self.user_role.setCurrentText(user['role']); self.user_gmail.clear(); self.user_trieu.clear()

    def numeric(self,field):
        try:
            return int(field.text().strip())
        except ValueError:
            self.status.setText('Nhập Chat ID số nguyên.'); return None

    def numeric_send(self,action,key,field,extra=None):
        value = self.numeric(field)
        if value is not None:
            self.send(action,{key:value,**(extra or {})})

    def save_user(self):
        self.numeric_send('user.save','chat_id',self.user_id,{'name':self.user_name.text(),'role':self.user_role.currentText()})

    def save_user_keys(self):
        selected = self.selected(self.users,'chat_id')
        typed = self.numeric(self.user_id)
        if selected is None or selected!=typed or typed<=0:
            self.status.setText('Chọn đúng người dùng và Chat ID trước khi lưu API.'); return
        self.send('user.mail_keys',dict(chat_id=typed,gmailvip_key=self.user_gmail.text(),one_trieu_key=self.user_trieu.text()),
            lambda _:(self.user_gmail.clear(),self.user_trieu.clear()))

    def save_bot(self):
        self.send('telegram.save',{'token':self.bot_token.text(),'auto_start':self.bot_auto.isChecked()},lambda _:self.bot_token.clear())

    def save_sepay(self):
        self.send('sepay.save',{**{k:f.text() for k,f in self.sepay_fields.items()},'enabled':self.sepay_auto.isChecked()},
                  lambda _:self.sepay_fields['token'].clear())

    def reconcile_sepay(self):
        transaction = self.selected(self.transactions,'transaction_id'); chat_id = self.numeric(self.review_id)
        if transaction and chat_id is not None:
            self.send('sepay.reconcile',{'transaction_id':transaction,'chat_id':chat_id})

    def selected_details(self,widget,key,action):
        value = self.selected(widget,key)
        if value is not None:
            self.send(action,{key:value},self.details)

    def details(self,result):
        dialog = QDialog(self); dialog.setWindowTitle('Chi tiết trên VPS'); dialog.resize(900,550)
        box = QVBoxLayout(dialog); tabs = QTabWidget(); box.addWidget(tabs)
        for name,rows in result.items():
            if isinstance(rows,list):
                keys = list(dict.fromkeys(k for row in rows for k in row)) if rows else []
                listing = table(keys); fill(listing,rows,keys); tabs.addTab(listing,name)
        close = QPushButton('Đóng'); close.clicked.connect(dialog.accept); box.addWidget(close)
        dialog.exec()

    def edit_balance(self):
        row = self.customers.currentRow()
        if not 0<=row<len(self.customers._rows):
            self.status.setText('Chọn khách hàng.'); return
        customer = dict(self.customers._rows[row])
        dialog = QDialog(self); dialog.setWindowTitle('Sửa số dư khả dụng'); box = QFormLayout(dialog)
        amount = QLineEdit(str(Decimal(customer['balance_units'])/100)); reason = QLineEdit()
        box.addRow('Số dư mới (VND)',amount); box.addRow('Lý do',reason)
        save = QPushButton('Lưu số dư'); box.addRow(save)
        def commit():
            self.send('customer.balance',{'chat_id':customer['chat_id'],'amount':amount.text(),
                'reason':reason.text(),'expected_units':customer['balance_units']})
            dialog.accept()
        save.clicked.connect(commit); dialog.exec()

    def build_settings(self):
        page,box = self.page('Cài đặt chung trên VPS')
        form = QFormLayout(); self.settings_fields = {}
        provider = QComboBox()
        for caption,value in (('GmailVip.vn','gmailvip_vn'),('1TrieuMail.com','1trieumail_com'),('Cả hai shop','multi_shop')):
            provider.addItem(caption,value)
        self.settings_fields['provider'] = provider; form.addRow('Nguồn mail',provider)
        for key,caption in (('gmailvip_key','API GmailVip'),('one_trieu_key','API 1TrieuMail'),
                            ('product_ids','Product IDs / Thứ tự ưu tiên'),('sale_rate','Giá bán VND / 100 bean')):
            field = QLineEdit(); self.settings_fields[key] = field
            if key.endswith('_key'):
                field.setEchoMode(QLineEdit.EchoMode.Password)
                field.setPlaceholderText('Để trống giữ key đã lưu trên VPS')
            form.addRow(caption,field)
        for key,caption,limit in (('concurrency','Số luồng Direct',50),('proxy_slots','Số luồng / proxy',50),('job_limit','Số job chạy cùng lúc',100)):
            field = QSpinBox(); field.setRange(1,limit); self.settings_fields[key] = field; form.addRow(caption,field)
        for key,caption in (('use_proxy','Sử dụng proxy'),('google_proxy','Google qua Proxy Pool'),
                ('app_proxy','Đăng nhập Mochi qua Proxy Pool'),('redeem_proxy','Nhập mã qua Proxy Pool'),('mail_proxy','Mua mail qua Proxy mua mail')):
            field = QCheckBox(caption); self.settings_fields[key] = field; form.addRow('',field)
        box.addLayout(form)
        self.settings_status = QLabel(); self.settings_status.setWordWrap(True); box.addWidget(self.settings_status)
        actions = QHBoxLayout()
        self.button('Lưu lên VPS',self.save_settings,actions)
        self.button('Nạp lại cấu hình VPS',lambda:self.load_settings(self.snapshot),actions,mutation=False)
        box.addLayout(actions); box.addStretch()
        return page

    def save_settings(self):
        values = {}
        for key,field in self.settings_fields.items():
            values[key] = (field.currentData() if isinstance(field,QComboBox) else field.value() if isinstance(field,QSpinBox)
                else field.isChecked() if isinstance(field,QCheckBox) else field.text())
        self.send('settings.save',values,lambda _:(self.settings_fields['gmailvip_key'].clear(),self.settings_fields['one_trieu_key'].clear()))

    def load_settings(self,state):
        config = state.get('settings',{})
        for key,field in self.settings_fields.items():
            if key not in config:
                continue
            value = config[key]
            if isinstance(field,QComboBox): field.setCurrentIndex(max(0,field.findData(value)))
            elif isinstance(field,QSpinBox): field.setValue(value)
            elif isinstance(field,QCheckBox): field.setChecked(value)
            else: field.setText(str(value))
        self.bot_auto.setChecked(state.get('telegram',{}).get('auto_start',False))
        payment = state.get('sepay',{})
        self.sepay_auto.setChecked(payment.get('enabled',False))
        for key,value in payment.get('fields',{}).items():
            self.sepay_fields[key].setText(value)

    def render(self,response):
        state = response.get('state',{})
        if state.get('version')!=1:
            self.failure('Phiên bản dịch vụ VPS không tương thích.'); self.enable_actions(False); return
        self.snapshot = state
        self.enable_actions(True)
        self.status.setText('Đã kết nối VPS · '+datetime.now().strftime('%H:%M:%S'))
        self.dashboard_summary.setText(f"Đang chạy: {state['active_jobs']} / {state['job_limit']} job     "
            f"Đang chờ: {state['queued_jobs']}     Hẹn giờ: {state['scheduled_jobs']}\n"
            f"Proxy Pool: {len(state['proxies'])}     Proxy mua mail: {len(state['mail_proxies'])}")
        fill(self.dashboard_jobs,state['jobs'],['job_id','invite_code','creator_name','status','success_count','target_count'])
        fill(self.jobs,state['jobs'],['job_id','customer_name','creator_name','invite_code','status','success_count','target_count','scheduled_at'])
        fill(self.history,state['history'],['job_id','order_id','creator_name','invite_code','status','achieved_beans','finished_at'])
        fill(self.proxy_table_account,state['proxies'],['id','endpoint','status','provider','speed','error'])
        fill(self.proxy_table_mail,state['mail_proxies'],['id','endpoint','status','provider','speed','error'])
        users = [{**row,'revenue':str(Decimal(row['revenue_units'])/100),'cost':str(Decimal(row['cost_units'])/100)} for row in state['users']]
        fill(self.users,users,['chat_id','name','role','active','completed_jobs','failed_jobs','sold_beans','revenue','cost'])
        customers = [{**row,'balance':str(Decimal(row['balance_units'])/100)} for row in state['customers']]
        fill(self.customers,customers,['chat_id','name','balance','total_orders','completed_orders','failed_orders'])
        payments = [{**row,'amount':str(Decimal(row['amount_units'])/100)} for row in state['sepay']['transactions']]
        fill(self.transactions,payments,['transaction_id','chat_id','amount','content','state','reason'])
        self.bot_status.setText(state['telegram']['status']); self.broadcast_status.setText(state['telegram']['broadcast'])
        self.sepay_status.setText(state['sepay']['status']); self.lookup_status.setText(state['lookup']['status'])
        fill(self.profiles,state['lookup']['profiles'],['email','user_id','display_name','invite_code','beans','updated_at'])
        config = state['settings']
        self.settings_status.setText('API GmailVip: '+('đã lưu' if config['has_gmailvip_key'] else 'chưa có')+
            ' · API 1TrieuMail: '+('đã lưu' if config['has_one_trieu_key'] else 'chưa có'))
        if not self._config_loaded:
            self.load_settings(state); self._config_loaded = True

    def closeEvent(self,event):
        self._closing = True
        self.timer.stop()
        self.enable_actions(False)
        if self._workers:
            self.status.setText('Đang đóng kết nối quản lý. Job trên VPS vẫn chạy.')
            event.ignore()
        else:
            event.accept()
