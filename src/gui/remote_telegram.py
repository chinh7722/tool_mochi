"""Telegram management presentation matching the established desktop workspace.

All controls call the remote manager's existing API callbacks. This module never
creates a bot, a worker controller or an operational SQLite store.
"""
from decimal import Decimal
from gui.broadcast_recipients import BroadcastRecipients

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QTabWidget, QVBoxLayout, QWidget,
)


def _label(text='', *, muted=False, size=12, bold=False):
    widget=QLabel(text)
    widget.setWordWrap(True)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setStyleSheet('background:transparent; color:'+('#9ba9c0' if muted else '#f4f4f5')+
        f'; font-size:{size}px;'+('font-weight:700;' if bold else ''))
    return widget


def _card(title='',subtitle=''):
    widget=QFrame()
    widget.setObjectName('remoteTelegramCard')
    widget.setStyleSheet('QFrame#remoteTelegramCard {background:#232326; '
        'border:1px solid #323238; border-radius:10px;}')
    box=QVBoxLayout(widget)
    box.setContentsMargins(18,16,18,16)
    box.setSpacing(12)
    if title:
        box.addWidget(_label(title,size=14,bold=True))
    if subtitle:
        box.addWidget(_label(subtitle,muted=True,size=11))
    return widget,box


def _stat(title,accent):
    widget=QFrame()
    widget.setObjectName('remoteTelegramStat')
    widget.setStyleSheet('QFrame#remoteTelegramStat {background:#232326; border:1px solid #323238; '
        f'border-top:2px solid {accent}; border-radius:9px;}}')
    box=QVBoxLayout(widget)
    box.setContentsMargins(16,12,16,12)
    box.setSpacing(6)
    box.addWidget(_label(title,muted=True,size=11))
    value=_label('0',size=23,bold=True)
    value.setStyleSheet(f'background:transparent; color:{accent}; font-size:23px; font-weight:700;')
    box.addWidget(value)
    return widget,value


def _scroll(content):
    scroll=QScrollArea()
    scroll.setObjectName('remoteTelegramScroll')
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    return scroll


def _primary(button):
    button.setProperty('primary',True)
    return button


def _danger(button):
    button.setStyleSheet('QPushButton {background:#2a1d24; color:#fb7185; '
        'border:1px solid #60313d; border-radius:6px; padding:9px 13px;} '
        'QPushButton:hover {background:#42232d;} '
        'QPushButton:disabled {background:#252529; color:#62626c; border-color:#34343b;}')
    return button


def _vnd(units):
    amount=Decimal(units)/100
    return format(amount,',.2f').rstrip('0').rstrip('.')


class TelegramPresentation:
    """Refresh view-only summaries and table filters without editing form drafts."""
    def __init__(self,window):
        self.window=window
        self._broadcast_pending=False

    def _filter(self,table,text,keys):
        query=text.strip().casefold()
        selected=table.currentRow()
        for index,row in enumerate(table._rows):
            hidden=bool(query) and not any(query in str(row.get(key,'')).casefold() for key in keys)
            table.setRowHidden(index,hidden)
            if hidden and index==selected:
                table.clearSelection()
                table.setCurrentCell(-1,-1)

    def filter_users(self):
        w=self.window
        self._filter(w.users,w.user_search.text(),('chat_id','name','role_label'))

    def filter_customers(self):
        w=self.window
        self._filter(w.customers,w.customer_search.text(),('chat_id','name'))

    def update_broadcast_length(self):
        w=self.window
        size=len(w.broadcast.toPlainText().encode('utf-16-le'))//2
        w.broadcast_length.setText(f'{size:,} / 4000')
        w.broadcast_length.setStyleSheet('background:transparent; color:'+
            ('#fb7185' if size>4000 else '#9ba9c0')+'; font-size:11px;')
        self.update_broadcast_controls()

    def update_broadcast_controls(self):
        w = self.window
        if not hasattr(w,'broadcast_send_button'):
            return
        recipients = w.broadcast_recipients.recipient_ids()
        connected = w._connected and not w._closing and not w.snapshot.get('closing',False)
        supported = w.snapshot.get('telegram',{}).get('broadcast_recipient_selection') is True
        ready = bool(w.snapshot.get('telegram',{}).get('running') and w.snapshot.get('telegram',{}).get('ready'))
        busy = self._broadcast_pending or bool(w.snapshot.get('telegram',{}).get('broadcast_running',False))
        mutable = connected and not w._mutating
        text = w.broadcast.toPlainText()
        size = len(text.encode('utf-16-le'))//2
        w.broadcast_send_button.setEnabled(mutable and supported and ready and not busy and 0<len(recipients)<=2000
            and bool(text.strip()) and size<=4000)
        w.broadcast_cancel_button.setEnabled(mutable and busy)
        w.broadcast_recipients.setEnabled(not busy)
        w.broadcast_audience.setText((f'Người nhận: {len(recipients)} admin/partner đã chọn. '
            'Mỗi người nhận một tin nhắn riêng.'+(' Tối đa 2000 người mỗi lượt.' if len(recipients)>2000 else ''))
            if supported else 'VPS cần cập nhật để chọn người nhận.')

    def send_broadcast(self):
        w = self.window
        if w.snapshot.get('telegram',{}).get('broadcast_recipient_selection') is not True:
            w.broadcast_status.setText('VPS cần cập nhật để chọn người nhận.')
            return
        if (not w._connected or w._mutating or w._closing or w.snapshot.get('closing',False)
                or self._broadcast_pending or w.snapshot.get('telegram',{}).get('broadcast_running',False)):
            return
        if not (w.snapshot.get('telegram',{}).get('running') and w.snapshot.get('telegram',{}).get('ready')):
            w.broadcast_status.setText('Bật bot và chờ kết nối sẵn sàng trước khi gửi thông báo.')
            return
        recipients = w.broadcast_recipients.recipient_ids()
        if not recipients:
            w.broadcast_status.setText('Chọn ít nhất một admin/partner trước khi gửi thông báo.')
            return
        if len(recipients)>2000:
            w.broadcast_status.setText('Chọn tối đa 2000 người cho mỗi lượt gửi thông báo.')
            return
        text = w.broadcast.toPlainText()
        if not text.strip() or len(text.encode('utf-16-le'))//2>4000:
            w.broadcast_status.setText('Nhập thông báo từ 1 đến 4000 ký tự theo bộ đếm trước khi gửi.')
            return
        self._broadcast_pending=True
        self.update_broadcast_controls()
        w.send('telegram.broadcast',{'text':text,'recipients':list(recipients)})

    def refresh(self,state):
        # Import lazily: remote_window delegates construction to this module.
        from gui.remote_window import fill
        w=self.window
        # Keep Send locked between the accepted POST and its following state poll.
        if not w._mutating and not w._refresh_requested:
            self._broadcast_pending=False
        records=state.get('users',[])
        users=[{**row,'role_label':'local' if row.get('chat_id')==-1 else row.get('role',''),
            'access_label':'Máy tính' if row.get('chat_id')==-1 else
                ('Đã cấp quyền' if row.get('active') else 'Đã thu hồi'),
            'revenue':_vnd(row.get('revenue_units',0)),
            'cost':_vnd(row.get('cost_units',0)),
            'profit':_vnd(row.get('revenue_units',0)-row.get('cost_units',0))}
            for row in records]
        fill(w.users,users,['chat_id','name','role_label','access_label','completed_jobs','failed_jobs',
                          'sold_beans','revenue','cost','profit'])
        self.filter_users()
        active=sum(1 for row in records if row.get('active') and row.get('chat_id',0)>0)
        values=(active,sum(r.get('completed_jobs',0) for r in records),
            sum(r.get('failed_jobs',0) for r in records),sum(r.get('sold_beans',0) for r in records))
        for name,value in zip(('users','completed','failed','beans'),values):
            w.telegram_stat_values[name].setText(f'{value:,}')
        revenue=sum(r.get('revenue_units',0) for r in records)
        cost=sum(r.get('cost_units',0) for r in records)
        for name,value in (('revenue',revenue),('cost',cost),('profit',revenue-cost)):
            w.telegram_money_values[name].setText(_vnd(value))
        w.broadcast_recipients.set_users(records)
        w.telegram_sale_rate.setText(str(state.get('settings',{}).get('sale_rate') or 'Chưa cấu hình')+' VND / 100 bean')
        customers=[{**row,'balance':_vnd(row.get('balance_units',0))} for row in state.get('customers',[])]
        fill(w.customers,customers,['chat_id','name','balance','total_orders','completed_orders','failed_orders'])
        self.filter_customers()
        transactions=[{**row,'amount':_vnd(row.get('amount_units',0))}
            for row in state.get('sepay',{}).get('transactions',[])]
        fill(w.transactions,transactions,['transaction_id','chat_id','amount','content','state','reason'])
        w.bot_status.setText(state.get('telegram',{}).get('status','Bot chưa bật.'))
        w.broadcast_status.setText(state.get('telegram',{}).get('broadcast',''))
        w.sepay_status.setText(state.get('sepay',{}).get('status','SePay chưa bật.'))
        self.update_broadcast_controls()


def build_telegram_page(window):
    """Build the old three-tab Telegram layout around remote API operations."""
    from gui.remote_window import table
    w=window
    presentation=TelegramPresentation(w)
    w.telegram_presentation=presentation
    page=QWidget()
    page.setObjectName('remoteTelegramPage')
    outer=QVBoxLayout(page)
    outer.setContentsMargins(28,26,28,22)
    outer.setSpacing(7)
    outer.addWidget(_label('Telegram',size=26,bold=True))
    outer.addWidget(_label('Kết nối bot, quản lý người dùng và theo dõi số bean đã giao thành công.',muted=True))
    outer.addSpacing(12)
    tabs=QTabWidget()
    tabs.setObjectName('remoteTelegramTabs')
    w.telegram_tabs=tabs
    outer.addWidget(tabs,1)

    operators=QWidget()
    body=QVBoxLayout(operators)
    body.setContentsMargins(0,0,0,0)
    body.setSpacing(18)
    tabs.addTab(_scroll(operators),'Người dùng và bot')

    bot,bot_box=_card('Cấu hình bot')
    form=QFormLayout()
    form.setHorizontalSpacing(18)
    w.bot_token=QLineEdit()
    w.bot_token.setEchoMode(QLineEdit.EchoMode.Password)
    w.bot_token.setPlaceholderText('Nhập Bot Token do @BotFather cấp; để trống giữ token đã lưu')
    form.addRow('Bot Token',w.bot_token)
    bot_box.addLayout(form)
    actions=QHBoxLayout()
    w.bot_auto=QCheckBox('Tự bật bot khi VPS khởi động')
    actions.addWidget(w.bot_auto)
    actions.addStretch()
    w.button('Lưu cấu hình',w.save_bot,actions)
    _primary(w.button('Bật bot',lambda:w.send('telegram.start'),actions))
    w.button('Tắt bot',lambda:w.send('telegram.stop'),actions)
    bot_box.addLayout(actions)
    w.bot_status=_label('Bot chưa bật.')
    w.bot_status.setStyleSheet('background:transparent; color:#60a5fa; font-size:12px;')
    bot_box.addWidget(w.bot_status)
    bot_box.addWidget(_label('/id — lấy Chat ID   ·   /job MÃ_MỜI SỐ_BEAN [TÊN_KHÁCH] — tạo job   ·   /stats — thống kê cá nhân\n'
        '/mailapi KEY_GMAILVIP KEY_1TRIEUMAIL — lưu đủ 2 API riêng; /mailapi — xem trạng thái\n'
        '/hen MÃ_MỜI SỐ_BEAN DD/MM/YYYY HH:MM — hẹn giờ Việt Nam · /lich — xem lịch · /huy MÃ_JOB — hủy job chưa chạy\n'
        'Admin: /partner_add CHAT_ID TÊN · /partner_remove CHAT_ID · /partners [TRANG] · /partner CHAT_ID\n'
        'Bot và job tiếp tục chạy trên VPS khi đóng ứng dụng quản lý. Job dùng cấu hình trong Cài đặt chung.',muted=True,size=11))
    body.addWidget(bot)

    announcement,announcement_box=_card('Gửi thông báo đến admin / partner')
    w.broadcast_audience=_label('Người nhận: chưa kết nối VPS.',muted=True,size=11)
    announcement_box.addWidget(w.broadcast_audience)
    w.broadcast_recipients=BroadcastRecipients()
    announcement_box.addWidget(w.broadcast_recipients)
    w.broadcast=QPlainTextEdit()
    w.broadcast.setPlaceholderText('Nhập nội dung thông báo gửi tới những người đã chọn…')
    w.broadcast.setMinimumHeight(100)
    w.broadcast.setMaximumHeight(160)
    announcement_box.addWidget(w.broadcast)
    actions=QHBoxLayout()
    w.broadcast_length=_label('0 / 4000',muted=True,size=11)
    actions.addWidget(w.broadcast_length)
    actions.addStretch()
    w.broadcast_cancel_button=w.button('Dừng gửi',lambda:w.send('telegram.cancel_broadcast'),actions)
    w.broadcast_send_button=_primary(w.button('Gửi thông báo',presentation.send_broadcast,actions))
    announcement_box.addLayout(actions)
    w.broadcast_status=_label('Bật bot, nhập nội dung rồi bấm Gửi thông báo.')
    w.broadcast_status.setStyleSheet('background:transparent; color:#60a5fa; font-size:12px;')
    announcement_box.addWidget(w.broadcast_status)
    w.broadcast.textChanged.connect(presentation.update_broadcast_length)
    w.broadcast_recipients.selection_changed.connect(presentation.update_broadcast_controls)
    body.addWidget(announcement)

    finance,finance_box=_card()
    price=QHBoxLayout()
    price.addWidget(_label('Giá bán (VND / 100 bean)',bold=True))
    w.telegram_sale_rate=_label('Chưa cấu hình',muted=True)
    price.addWidget(w.telegram_sale_rate,1)
    w.button('Cài đặt đơn giá',lambda:getattr(w,'switch_to_settings_tab',lambda:w.activate(5))(),price,mutation=False)
    w.button('Giao dịch / đối soát',lambda:w.send('finance.ledger',callback=w.details),price)
    finance_box.addLayout(price)
    finance_box.addWidget(_label('Giá được chốt khi tạo job. Đơn khách hàng thu theo bean thành công; '
        'chi phí ghi ngay khi mua mail, kể cả mail chưa dùng. Lợi nhuận = doanh thu − chi phí. '
        'Local gom các job tạo từ ứng dụng quản lý.',muted=True,size=11))
    detail=QWidget()
    details=QVBoxLayout(detail)
    details.setContentsMargins(0,4,0,0)
    detail.hide()
    form=QFormLayout()
    w.finance_id=QLineEdit()
    w.finance_id.setPlaceholderText('Mã giao dịch mua mail hoặc mã job cần đối soát')
    w.finance_amount=QLineEdit()
    w.finance_amount.setPlaceholderText('Số tiền VND, có thể dùng dấu chấm cho phần lẻ')
    form.addRow('Mã giao dịch / Job',w.finance_id)
    form.addRow('Số tiền VND',w.finance_amount)
    details.addLayout(form)
    actions=QHBoxLayout()
    w.button('Chốt chi phí giao dịch',lambda:w.send('finance.reconcile',{'purchase_id':w.finance_id.text(),'amount':w.finance_amount.text()}),actions)
    w.button('Bổ sung đơn giá job',lambda:w.send('finance.price',{'job_id':w.finance_id.text(),'amount':w.finance_amount.text()}),actions)
    actions.addStretch()
    details.addLayout(actions)
    reveal=QPushButton('Mở đối soát thủ công')
    reveal.setCheckable(True)
    reveal.toggled.connect(detail.setVisible)
    finance_box.addWidget(reveal,alignment=Qt.AlignmentFlag.AlignLeft)
    finance_box.addWidget(detail)
    body.addWidget(finance)

    counters=QHBoxLayout()
    counters.setSpacing(12)
    w.telegram_stat_values={}
    for name,caption,color in (('users','Người dùng được cấp quyền','#3b82f6'),
            ('completed','Job hoàn thành','#22c55e'),('failed','Job thất bại','#f87171'),
            ('beans','Tổng bean đã bán','#c084fc')):
        stat,value=_stat(caption,color)
        w.telegram_stat_values[name]=value
        counters.addWidget(stat,1)
    body.addLayout(counters)
    money=QHBoxLayout()
    money.setSpacing(12)
    w.telegram_money_values={}
    for name,caption,color in (('revenue','Doanh thu (VND)','#60a5fa'),
            ('cost','Chi phí mail (VND)','#fb7185'),('profit','Lợi nhuận (VND)','#34d399')):
        stat,value=_stat(caption,color)
        w.telegram_money_values[name]=value
        money.addWidget(stat,1)
    body.addLayout(money)

    users,users_box=_card('Quản lý người dùng',
        'Admin / partner dùng API mua mail riêng. Nguồn mua, product ID và số luồng do Cài đặt chung quản lý.')
    search_row=QHBoxLayout()
    search_row.addStretch()
    w.user_search=QLineEdit()
    w.user_search.setPlaceholderText('Tìm tên, Chat ID hoặc vai trò…')
    w.user_search.setMaximumWidth(360)
    w.user_search.textChanged.connect(presentation.filter_users)
    search_row.addWidget(w.user_search)
    users_box.addLayout(search_row)
    fields=QGridLayout()
    fields.setHorizontalSpacing(12)
    fields.setVerticalSpacing(6)
    w.user_id=QLineEdit(); w.user_id.setPlaceholderText('Ví dụ: 123456789')
    w.user_name=QLineEdit(); w.user_name.setPlaceholderText('Tên người dùng / đối tác')
    w.user_name.setMaxLength(120)
    w.user_role=QComboBox(); w.user_role.addItems(['partner','admin'])
    w.user_role.setToolTip('Admin được thêm, xóa và xem thông tin partner qua bot. Partner chỉ quản lý job của mình.')
    for col,(caption,field) in enumerate((('Chat ID',w.user_id),('Tên người dùng',w.user_name),('Vai trò',w.user_role))):
        fields.addWidget(_label(caption),0,col)
        fields.addWidget(field,1,col)
    fields.setColumnStretch(0,2); fields.setColumnStretch(1,3); fields.setColumnStretch(2,1)
    users_box.addLayout(fields)
    keys=QGridLayout()
    keys.setHorizontalSpacing(12)
    w.user_gmail=QLineEdit(); w.user_trieu=QLineEdit()
    for col,(caption,field) in enumerate((('API riêng GmailVip.vn',w.user_gmail),('API riêng 1TrieuMail.com',w.user_trieu))):
        field.setEchoMode(QLineEdit.EchoMode.Password)
        field.setMaxLength(2048)
        field.setPlaceholderText('Chọn người dùng trong bảng rồi nhập API key')
        keys.addWidget(_label(caption),0,col)
        keys.addWidget(field,1,col)
        keys.setColumnStretch(col,1)
    users_box.addLayout(keys)
    key_actions=QHBoxLayout()
    key_actions.addWidget(_label('Chọn admin/partner để quản lý API riêng.',muted=True,size=11),1)
    w.button('Lưu 2 API mua mail',w.save_user_keys,key_actions)
    users_box.addLayout(key_actions)
    actions=QHBoxLayout()
    _primary(w.button('+ Thêm / Lưu người dùng',w.save_user,actions))
    _danger(w.button('Thu hồi quyền',lambda:w.selected_send(w.users,'chat_id','user.active',{'active':False}),actions))
    w.button('Khôi phục quyền',lambda:w.selected_send(w.users,'chat_id','user.active',{'active':True}),actions)
    w.button('Xem job',lambda:w.selected_details(w.users,'chat_id','user.jobs'),actions)
    actions.addStretch()
    users_box.addLayout(actions)
    w.users=table(['Chat ID','Tên','Vai trò','Quyền truy cập','Job\nhoàn thành','Job\nthất bại',
                   'Bean\nđã bán','Doanh thu\n(VND)','Chi phí\n(VND)','Lợi nhuận\n(VND)'])
    w.users.setMinimumHeight(255)
    w.users.verticalHeader().setDefaultSectionSize(38)
    w.users.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    for column,width in enumerate((95,145,75,105,95,85,95,110,110,110)):
        w.users.setColumnWidth(column,width)
    w.users.cellClicked.connect(w.pick_user)
    w.users.cellDoubleClicked.connect(lambda *_:w.selected_details(w.users,'chat_id','user.jobs'))
    users_box.addWidget(w.users)
    users_box.addWidget(_label('Bean đã bán tính theo số bean thực tế đã chạy thành công, kể cả phần thành công '
        'trong job lỗi/hủy. Thu hồi quyền giữ nguyên lịch sử và thống kê.',muted=True,size=11))
    body.addWidget(users)
    body.addStretch()

    customers=QWidget()
    customer_box=QVBoxLayout(customers)
    customer_box.setContentsMargins(18,18,18,18)
    customer_box.setSpacing(14)
    customer_box.addWidget(_label('Quản lý khách hàng',size=20,bold=True))
    customer_box.addWidget(_label('Khách hàng tự xuất hiện khi nhắn bot và chưa được cấp quyền admin/partner. '
        'Có thể thêm hoặc khôi phục theo Chat ID; sửa số dư luôn lưu lịch sử.',muted=True))
    customer_actions=QHBoxLayout()
    w.customer_id=QLineEdit()
    w.customer_id.setPlaceholderText('Chat ID để thêm / khôi phục (tùy chọn)')
    customer_actions.addWidget(w.customer_id,2)
    w.button('Thêm / Khôi phục',lambda:w.numeric_send('customer.save','chat_id',w.customer_id),customer_actions)
    w.customer_search=QLineEdit()
    w.customer_search.setPlaceholderText('Tìm Chat ID hoặc tên…')
    w.customer_search.textChanged.connect(presentation.filter_customers)
    customer_actions.addWidget(w.customer_search,2)
    w.button('Làm mới',w.poll,customer_actions,mutation=False)
    customer_box.addLayout(customer_actions)
    actions=QHBoxLayout()
    w.button('Sửa số dư',w.edit_balance,actions)
    _danger(w.button('Xóa khách hàng',lambda:w.selected_send(w.customers,'chat_id','customer.delete'),actions))
    w.button('Xem giao dịch / đơn',lambda:w.selected_details(w.customers,'chat_id','customer.history'),actions)
    actions.addStretch()
    customer_box.addLayout(actions)
    w.customers=table(['ID khách hàng','Tên','Số dư khả dụng','Số đơn đã tạo','Đã hoàn thành','Thất bại'])
    w.customers.setMinimumHeight(330)
    w.customers.verticalHeader().setDefaultSectionSize(42)
    w.customers.cellDoubleClicked.connect(lambda *_:w.selected_details(w.customers,'chat_id','customer.history'))
    customer_box.addWidget(w.customers,1)
    interrupted,interrupted_box=_card('Chốt đơn gián đoạn',
        'Kiểm tra log và tiến độ đã lưu trước khi chốt. Job còn worker trên VPS không thể chốt tại đây.')
    row=QHBoxLayout()
    w.settle_id=QLineEdit()
    w.settle_id.setPlaceholderText('Mã job gián đoạn đã kiểm tra log')
    row.addWidget(w.settle_id,1)
    w.button('Chốt đơn gián đoạn',lambda:w.send('customer.settle',{'job_id':w.settle_id.text()}),row)
    interrupted_box.addLayout(row)
    customer_box.addWidget(interrupted)
    tabs.addTab(customers,'Quản lý khách hàng')

    sepay=QWidget()
    sepay_box=QVBoxLayout(sepay)
    sepay_box.setContentsMargins(18,18,18,18)
    sepay_box.setSpacing(14)
    sepay_box.addWidget(_label('Thanh toán SePay',size=20,bold=True))
    sepay_box.addWidget(_label('Nạp tối thiểu 10.000 VND, tối đa 100.000 VND mỗi giao dịch. Nội dung chuyển khoản '
        'chỉ gồm Chat ID khách hàng. VPS tự đồng bộ giao dịch khi được bật.',muted=True))
    settings,settings_box=_card('Cấu hình tài khoản nhận tiền')
    form=QFormLayout()
    form.setHorizontalSpacing(18)
    w.sepay_fields={}
    for key,caption in (('token','API Token SePay v2'),('bank_id','Bank Account ID (UUID)'),
            ('account_number','Số tài khoản nhận'),('bank_name','Tên ngân hàng'),('account_name','Tên chủ tài khoản')):
        field=QLineEdit()
        if key=='token':
            field.setEchoMode(QLineEdit.EchoMode.Password)
            field.setPlaceholderText('Để trống giữ token đã lưu trên VPS')
        w.sepay_fields[key]=field
        form.addRow(caption,field)
    settings_box.addLayout(form)
    w.sepay_auto=QCheckBox('Tự bật đồng bộ SePay khi VPS khởi động')
    settings_box.addWidget(w.sepay_auto)
    actions=QHBoxLayout()
    w.button('Lưu cấu hình',w.save_sepay,actions)
    _primary(w.button('Bật đồng bộ',lambda:w.send('sepay.start'),actions))
    w.button('Tắt đồng bộ',lambda:w.send('sepay.stop'),actions)
    actions.addStretch()
    settings_box.addLayout(actions)
    w.sepay_status=_label('SePay chưa bật.')
    w.sepay_status.setStyleSheet('background:transparent; color:#60a5fa; font-size:12px;')
    settings_box.addWidget(w.sepay_status)
    sepay_box.addWidget(settings)
    w.transactions=table(['ID giao dịch','Chat ID','Số tiền','Nội dung','Trạng thái','Lý do cần kiểm tra'])
    w.transactions.setMinimumHeight(260)
    sepay_box.addWidget(w.transactions,1)
    row=QHBoxLayout()
    w.review_id=QLineEdit()
    w.review_id.setPlaceholderText('Chat ID nhận giao dịch cần đối soát')
    row.addWidget(w.review_id,1)
    w.button('Cộng giao dịch đã chọn vào Chat ID',w.reconcile_sepay,row)
    sepay_box.addLayout(row)
    sepay_box.addWidget(_label('Đối soát chỉ áp dụng tiền vào đúng ngân hàng và giới hạn nạp, đang chờ khớp Chat ID. '
        'Kiểm tra người chuyển và số tiền trước khi bấm cộng.',muted=True,size=11))
    tabs.addTab(sepay,'Thanh toán SePay')
    return page
