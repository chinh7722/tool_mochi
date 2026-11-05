"""Classic hoshino-tool workspace views backed only by remote snapshots."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (QCheckBox,QComboBox,QDateTimeEdit,QFrame,QGridLayout,
    QHBoxLayout,QHeaderView,QLabel,QLineEdit,QPlainTextEdit,QPushButton,QScrollArea,
    QSpinBox,QStackedWidget,QTabBar,QTabWidget,QVBoxLayout,QWidget)
from gui.branding import asset_path, icon
from gui.remote_widgets import ArtworkBanner, card, heading, stat_card

VN = timezone(timedelta(hours=7))


def scroll_content(content):
    scroll=QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(content)
    return scroll


def build_dashboard(window):
    from gui.remote_window import table
    page,box=window.page('Dashboard')
    actions=QHBoxLayout()
    actions.addWidget(heading('Dashboard','Tổng quan hoạt động của hoshino-tool.'),1)
    button=window.button('Tạo job mới',window.open_new_job,actions,mutation=False)
    button.setProperty('primary',True)
    box.addLayout(actions)
    banner=ArtworkBanner()
    banner.set_artwork(QPixmap(asset_path('dashboard_banner')))
    banner.setStyleSheet('QFrame#dashboardCard {background:#202a3c;border:1px solid #344d75;border-radius:10px;}')
    banner_box=QVBoxLayout(banner)
    banner_box.setContentsMargins(18,16,18,16)
    title=QLabel('Không gian làm việc của bạn')
    title.setStyleSheet('font-size:17px;font-weight:700;')
    banner_box.addWidget(title)
    banner_box.addWidget(QLabel('Theo dõi job, quản lý proxy và tiếp nhận yêu cầu từ Telegram.'))
    box.addWidget(banner)
    grid=QGridLayout();grid.setSpacing(14)
    window.dashboard_metrics={}
    for index,(key,title,hint,color) in enumerate((
        ('total','Tổng job','Job trong phiên trên VPS','#65a2ff'),
        ('active','Đang chạy','Bao gồm job chờ và lịch hẹn','#65a2ff'),
        ('completed','Hoàn thành','Job đã đạt mục tiêu','#2dd381'),
        ('failed','Thất bại','Không tính job đã hủy','#ff6978'),
        ('beans','Bean thành công','Bean thực tế trong phiên','#d09aff'),
        ('proxies','Proxy sẵn sàng','Proxy trong danh sách hoạt động','#2dd381'))):
        item,value=stat_card(title,color,hint)
        window.dashboard_metrics[key]=value
        grid.addWidget(item,index//3,index%3)
        grid.setColumnStretch(index%3,1)
    box.addLayout(grid)
    recent,recent_box=card('Job gần đây','10 job mới nhất trong phiên · Nhấp đúp để mở nhật ký.')
    window.dashboard_jobs=table(['Khách hàng / Job','Mã mời','Tiến độ','Bean','Trạng thái','Bắt đầu'])
    window.dashboard_jobs.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    window.dashboard_jobs.setMinimumHeight(190)
    window.dashboard_jobs.cellDoubleClicked.connect(lambda row,_:
        window.open_job_logs(window.dashboard_jobs._rows[row]['job_id'])
        if 0<=row<len(window.dashboard_jobs._rows) else None)
    recent_box.addWidget(window.dashboard_jobs)
    window.dashboard_empty=QLabel('Chưa có job trong phiên. Chọn “Tạo job mới” để bắt đầu.')
    window.dashboard_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
    window.dashboard_empty.setObjectName('muted')
    recent_box.addWidget(window.dashboard_empty)
    box.addWidget(recent)
    footer=QHBoxLayout();footer.setSpacing(14)
    telegram,telegram_box=card('Telegram')
    window.dashboard_telegram=QLabel('Chưa kết nối VPS')
    telegram_box.addWidget(window.dashboard_telegram)
    actions=QHBoxLayout()
    window.button('Quản lý',lambda:window.activate(4),actions,mutation=False)
    telegram_box.addLayout(actions)
    footer.addWidget(telegram,1)
    shortcuts,shortcuts_box=card('Truy cập nhanh')
    actions=QHBoxLayout()
    window.button('Proxy Pool',lambda:window.activate(2),actions,mutation=False)
    window.button('Cài đặt chung',lambda:window.activate(5),actions,mutation=False)
    shortcuts_box.addLayout(actions)
    window.dashboard_summary=QLabel('Chưa kết nối VPS.')
    window.dashboard_summary.setObjectName('smallMuted')
    shortcuts_box.addWidget(window.dashboard_summary)
    footer.addWidget(shortcuts,1)
    box.addLayout(footer)
    box.addStretch()
    return page


def build_mochi(window):
    from gui.remote_window import table
    page,box=window.page('Mochi Chat')
    window.workspace_tabs=QTabBar()
    window.workspace_tabs.setDrawBase(False);window.workspace_tabs.setExpanding(False)
    for label,image in (('Nhiệm vụ','nav_jobs.svg'),('Kho tài khoản','nav_stock.svg'),('Lấy mã mời','nav_jobs.svg')):
        window.workspace_tabs.addTab(icon('',image),label)
    box.addWidget(window.workspace_tabs,0,Qt.AlignmentFlag.AlignLeft)
    window.workspace_stack=QStackedWidget()
    window.workspace_tabs.currentChanged.connect(window.workspace_stack.setCurrentIndex)
    jobs=QWidget(); jobs_box=QVBoxLayout(jobs)
    jobs_box.setContentsMargins(0,8,0,0);jobs_box.setSpacing(12)
    jobs_box.addWidget(heading('Jobs','Tạo job nạp bean cho khách hàng và theo dõi tiến độ thực hiện.'))
    window.mochi_tabs=window.job_tabs=QTabBar()
    window.job_tabs.setDrawBase(False)
    window.job_tabs.setExpanding(True)
    for label,image in (('Thiết lập job','user_add_blue.svg'),('Job đang chạy','pending_tasks_blue.svg'),
                        ('Log job','log_document_blue.svg'),('Lịch sử tạm','log_document_blue.svg')):
        window.job_tabs.addTab(icon('',image),label)
    jobs_box.addWidget(window.job_tabs)
    window.job_stack=QStackedWidget()
    window.job_tabs.currentChanged.connect(window.job_stack.setCurrentIndex)
    window.job_tabs.currentChanged.connect(lambda index:window.refresh_jobs() if index==1 and window.client is not None else None)
    setup,setup_box=card('Thiết lập job')
    choice_row=QHBoxLayout();choice_row.setSpacing(12)
    window._stock_mode=False
    window.stock_type_buttons=[]
    window.stock_type_frames=[]
    for title,subtitle,selected in (('Nhập mã mời để nạp bean','Dùng mã mời khách để nạp bean từ tài khoản mới.',True),
            ('Tạo account có sẵn bean','Mua mail, tạo acc và tự tích bean theo combo.',False)):
        item,item_box=card()
        window.stock_type_frames.append(item)
        item.setStyleSheet('QFrame#remoteCard {background:'+('#202c43;border:1px solid #3b82f6;' if selected else '#202023;border:1px solid #383840;')+'}')
        label=QPushButton(title);label.setCheckable(True);label.setChecked(selected)
        label.clicked.connect(lambda _c=False,mode=not selected:window.select_stock_mode(mode))
        window.stock_type_buttons.append(label)
        label.setStyleSheet('font-size:14px;font-weight:700;')
        item_box.addWidget(label)
        note=QLabel(subtitle);note.setObjectName('smallMuted');note.setWordWrap(True)
        item_box.addWidget(note)
        choice_row.addWidget(item,1)
    setup_box.addLayout(choice_row)
    from gui.stock_widgets import StockCreateForm
    window.stock_create_panel=StockCreateForm(lambda a,p,c=None:window.send(a,p,c),
        lambda:window.snapshot.get('settings',{}).get('product_ids',''),window)
    window.stock_create_panel.hide();setup_box.addWidget(window.stock_create_panel)
    window.job_code=QLineEdit();window.job_code.setPlaceholderText('VD: A75FNX')
    window.job_customer=QLineEdit();window.job_customer.setPlaceholderText('Tùy chọn (để nhận diện job)')
    window.job_points=QSpinBox();window.job_points.setRange(1,1000000);window.job_points.setValue(1000)
    window.job_account_count=QLabel('(= 10 accounts)')
    window.job_account_count.setStyleSheet('color:#60a5fa;font-weight:600;')
    window.job_quote=QLineEdit();window.job_quote.setReadOnly(True)
    window.job_quote.setPlaceholderText('Tính theo đơn giá trên VPS')
    window.job_quote.setToolTip('Ước tính theo đơn giá VPS / 100 bean. Đơn giá thực tế được chốt khi tạo job.')
    grid=QGridLayout();grid.setHorizontalSpacing(14);grid.setVerticalSpacing(10)
    grid.addWidget(QLabel('Tên khách / Ghi chú:'),0,0)
    grid.addWidget(QLabel('Mã mời Mochi (*):'),0,1)
    grid.addWidget(window.job_customer,1,0);grid.addWidget(window.job_code,1,1)
    grid.addWidget(QLabel('Điểm mục tiêu (*):'),2,0)
    grid.addWidget(QLabel('Tổng tiền dự kiến (VND):'),2,1)
    count_row=QHBoxLayout();count_row.addWidget(window.job_points,1);count_row.addWidget(window.job_account_count)
    grid.addLayout(count_row,3,0);grid.addWidget(window.job_quote,3,1)
    grid.setColumnStretch(0,1);grid.setColumnStretch(1,1)
    window.schedule=QCheckBox('Hẹn giờ chạy')
    window.schedule_time=QDateTimeEdit();window.schedule_time.setCalendarPopup(True)
    window.schedule_time.setDisplayFormat('dd/MM/yyyy HH:mm')
    window.schedule_time.setDateTime(QDateTime.fromString((datetime.now(VN)+timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M'),'yyyy-MM-dd HH:mm'))
    window.schedule_time.setEnabled(False)
    window.schedule.toggled.connect(window.schedule_time.setEnabled)
    schedule_row=QHBoxLayout();schedule_row.addWidget(window.schedule);schedule_row.addWidget(window.schedule_time);schedule_row.addStretch()
    grid.addLayout(schedule_row,4,0,1,2)
    window.job_combo=QCheckBox('Nhập mã mời combo — tự tiếp tục sau 24 giờ 1 phút đến đủ bean')
    window.job_combo.setToolTip('Đạt giới hạn 10 lượt / 24 giờ: lưu tiến độ và tự chạy lượt tiếp theo trên VPS. Có thể dừng job trong thời gian chờ.')
    grid.addWidget(window.job_combo,5,0,1,2)
    window.invite_form=QWidget();invite_box=QVBoxLayout(window.invite_form);invite_box.setContentsMargins(0,0,0,0)
    invite_box.addLayout(grid);setup_box.addWidget(window.invite_form)
    note=QLabel('Giờ Việt Nam · Lịch được lưu và thực hiện trên VPS, kể cả khi đóng ứng dụng quản lý.')
    note.setObjectName('smallMuted');note.setWordWrap(True);setup_box.addWidget(note)
    source,source_box=card()
    window.job_source_summary=QLabel('Nguồn mail và proxy dùng cấu hình hiện tại trên VPS.')
    window.job_source_summary.setObjectName('muted');window.job_source_summary.setWordWrap(True)
    source_box.addWidget(window.job_source_summary)
    setup_box.addWidget(source)
    window.stock_invite_notes=(note,source)
    actions=QHBoxLayout()
    window.button('Lưu nháp',window.save_job_draft,actions,mutation=False)
    window.button('Cài đặt',lambda:window.activate(5),actions,mutation=False)
    actions.addStretch()
    window.job_start_button=window.button('Bắt đầu nhập mã',window.create_job,actions)
    window.job_start_button.setProperty('primary',True)
    setup_box.addLayout(actions)
    window.job_stack.addWidget(scroll_content(setup))
    running,running_box=card('DANH SÁCH NHIỆM VỤ TRONG PHIÊN')
    window.job_queue_label=QLabel('Chưa kết nối VPS.');window.job_queue_label.setObjectName('muted')
    running_box.addWidget(window.job_queue_label)
    sync_row=QHBoxLayout()
    window.job_sync_status=QLabel('Chưa có dữ liệu đồng bộ từ VPS.')
    window.job_sync_status.setObjectName('smallMuted')
    window.job_sync_status.setWordWrap(True)
    sync_row.addWidget(window.job_sync_status,1)
    window.job_refresh_button=window.button('Đồng bộ VPS',window.refresh_jobs,sync_row,mutation=False)
    running_box.addLayout(sync_row)
    window.job_search=QLineEdit();window.job_search.setMaxLength(160)
    window.job_search.setPlaceholderText('Tìm theo mã mời, mã job, người tạo hoặc Chat ID…')
    running_box.addWidget(window.job_search)
    window.job_search.textChanged.connect(lambda _:window.render({'state':window.snapshot}) if window.snapshot.get('version')==1 else None)
    window.jobs=table(['Khách hàng / Job','Người tạo job','Mã mời','Tiến độ','Điểm kẹo','Trạng thái','Thời gian','Thao tác'])
    window.jobs.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Interactive)
    window.jobs.setColumnWidth(0,150);window.jobs.setColumnWidth(1,145);window.jobs.setColumnWidth(7,235)
    window.jobs.setMinimumHeight(300)
    window.jobs.cellDoubleClicked.connect(lambda row,_:window.open_job_logs(window.jobs._rows[row]['job_id'])
        if 0<=row<len(window.jobs._rows) else None)
    running_box.addWidget(window.jobs)
    actions=QHBoxLayout()
    window.button('Dừng / Hủy lịch',window.stop_selected_job,actions)
    window.button('Xem log',window.load_logs,actions)
    window.button('Tiếp tục job',window.continue_selected_job,actions)
    actions.addStretch();running_box.addLayout(actions)
    window.job_stack.addWidget(running)
    logs,logs_box=card('NHẬT KÝ THỰC THI (REAL-TIME LOGS)')
    from gui.job_log_picker import JobLogPicker
    window.log_picker=JobLogPicker(window);logs_box.addWidget(window.log_picker)
    window.job_tabs.currentChanged.connect(lambda index:window.log_picker.load() if index==2 else None)
    actions=QHBoxLayout()
    window.job_log_filter=QLabel('(Chọn job để xem nhật ký)');window.job_log_filter.setObjectName('muted')
    actions.addWidget(window.job_log_filter,1)
    window.button('Làm mới',lambda:window.open_job_logs(window._log_job_id) if window._log_job_id else None,actions)
    window.button('Xóa log',lambda:window.logs.clear(),actions,mutation=False)
    logs_box.addLayout(actions)
    window.logs=QPlainTextEdit();window.logs.setReadOnly(True);window.logs.setFont(QFont('Consolas',10))
    logs_box.addWidget(window.logs)
    window.job_stack.addWidget(logs)
    from gui.remote_management_pages import TemporaryHistoryPanel
    window.temporary_panel=TemporaryHistoryPanel(window)
    window.job_stack.addWidget(window.temporary_panel)
    window.job_tabs.currentChanged.connect(lambda index:window.temporary_panel.load() if index==3 else None)
    jobs_box.addWidget(window.job_stack,1)
    window.workspace_stack.addWidget(jobs)
    inventory=QWidget();inventory_box=QVBoxLayout(inventory);inventory_box.setContentsMargins(0,12,0,0)
    inventory_box.addWidget(heading('Kho tài khoản','Chọn lô mail đã mua cho job tiếp theo. Mail được giữ trên VPS.'))
    batch,batch_box=card('Dùng lô mail đã mua','Chỉ xử lý những mail chưa hoàn tất trong lô; không mua thêm khi chọn lô có sẵn.')
    window.job_batch=QComboBox();window.job_batch.addItem('Mua mail theo cấu hình VPS','')
    batch_box.addWidget(window.job_batch)
    actions=QHBoxLayout()
    window.button('Chọn file JSON và đưa lên VPS',window.upload_batch,actions)
    window.button('Làm mới danh sách lô',lambda:window.send('batch.list',callback=window.show_batches),actions)
    batch_box.addLayout(actions)
    actions=QHBoxLayout()
    window.button('Về thiết lập job',window.open_new_job,actions,mutation=False)
    actions.addStretch();batch_box.addLayout(actions)
    inventory_box.addWidget(batch);inventory_box.addStretch()
    batch.hide()
    inventory_box.takeAt(inventory_box.count()-1)
    from gui.stock_widgets import remote_inventory
    window.stock_inventory_panel=remote_inventory(window)
    inventory_box.addWidget(window.stock_inventory_panel,1)
    inventory_box.itemAt(0).widget().hide()
    window.workspace_tabs.currentChanged.connect(lambda i:window.stock_inventory_panel.load() if i==1 else None)
    window.workspace_stack.addWidget(inventory)
    lookup=QWidget();lookup_box=QVBoxLayout(lookup);lookup_box.setContentsMargins(0,12,0,0)
    lookup_box.addWidget(heading('Lấy mã mời tài khoản','Đăng nhập Google để lấy mã mời Mochi, tên hiển thị và số dư beans hiện tại.'))
    login,login_box=card()
    grid=QGridLayout();grid.setHorizontalSpacing(14)
    window.lookup_email=QLineEdit();window.lookup_email.setPlaceholderText('Email tài khoản Google')
    window.lookup_password=QLineEdit();window.lookup_password.setEchoMode(QLineEdit.EchoMode.Password)
    window.lookup_password.setPlaceholderText('Mật khẩu — chỉ dùng cho lần tra cứu này')
    for column,(label,field) in enumerate((('Email Google',window.lookup_email),('Mật khẩu Google',window.lookup_password))):
        grid.addWidget(QLabel(label),0,column);grid.addWidget(field,1,column);grid.setColumnStretch(column,1)
    login_box.addLayout(grid)
    actions=QHBoxLayout()
    lookup_button=window.button('Đăng nhập & lấy mã',window.lookup_start,actions);lookup_button.setProperty('primary',True)
    window.button('Hủy',lambda:window.send('lookup.cancel'),actions)
    window.button('Cài đặt',lambda:window.activate(5),actions,mutation=False)
    actions.addStretch();login_box.addLayout(actions)
    window.lookup_status=QLabel('Chưa kết nối VPS.');window.lookup_status.setObjectName('muted');window.lookup_status.setWordWrap(True)
    login_box.addWidget(window.lookup_status);lookup_box.addWidget(login)
    results,results_box=card('Danh sách tài khoản')
    actions=QHBoxLayout()
    window.button('Sao chép mã',window.copy_invite,actions,mutation=False)
    window.button('Xuất CSV',window.export_profiles,actions,mutation=False)
    actions.addStretch();results_box.addLayout(actions)
    window.profiles=table(['Email','ID tài khoản','Tên hiển thị','Mã mời','Beans','Cập nhật'])
    results_box.addWidget(window.profiles);lookup_box.addWidget(results,1)
    window.workspace_stack.addWidget(lookup)
    box.addWidget(window.workspace_stack,1)
    window.job_points.valueChanged.connect(window.update_job_quote)
    window.job_batch.currentIndexChanged.connect(window.update_job_quote)
    window.schedule.toggled.connect(window.update_job_quote)
    window.job_combo.toggled.connect(window.update_job_quote)
    return page


def build_settings(window):
    page,box=window.page('Cài đặt')
    box.addWidget(heading('Cài đặt','Thiết lập đường mạng, nguồn mail và số luồng cho các job mới.'))
    window.settings_tabs=QTabWidget()
    window.settings_tabs.addTab(QWidget(),'Hạ tầng chung')
    window.settings_tabs.addTab(QWidget(),'Mochi Chat')
    box.addWidget(window.settings_tabs,1)
    window.settings_fields={}
    common=window.settings_tabs.widget(0);common_box=QHBoxLayout(common);common_box.setContentsMargins(0,12,0,0);common_box.setSpacing(16)
    left=QVBoxLayout()
    connection,connection_box=card('Kết nối VPS','Mở SSH tunnel trước khi kết nối. Tác vụ tiếp tục chạy trên VPS khi đóng ứng dụng quản lý.')
    connection_box.addWidget(QLabel('Địa chỉ kết nối'))
    connection_box.addWidget(window.url)
    connection_box.addWidget(QLabel('Token quản lý VPS'))
    connection_box.addWidget(window.token)
    actions=QHBoxLayout();actions.addStretch();actions.addWidget(window.connect_button)
    connection_box.addLayout(actions);left.addWidget(connection)
    proxy,proxy_box=card('Cấu hình proxy theo từng bước')
    for key,caption in (('use_proxy','Sử dụng proxy'),('google_proxy','Đăng nhập Google qua Proxy Pool'),
            ('app_proxy','Đăng nhập Mochi qua Proxy Pool'),('redeem_proxy','Nhập mã mời qua Proxy Pool'),
            ('mail_proxy','Mua mail qua Proxy mua mail')):
        field=QCheckBox(caption);window.settings_fields[key]=field;proxy_box.addWidget(field)
    grid=QGridLayout();grid.addWidget(QLabel('Luồng trên mỗi proxy'),0,0)
    slots=QSpinBox();slots.setRange(1,50);window.settings_fields['proxy_slots']=slots;grid.addWidget(slots,0,1)
    proxy_box.addLayout(grid);left.addWidget(proxy);left.addStretch()
    common_box.addLayout(left,3)
    help_card,help_box=card('Cấu hình đường mạng')
    note=QLabel('Bốn lựa chọn độc lập cho mua mail, Google, đăng nhập Mochi và nhập mã mời.\n\n'
        'Proxy Pool phục vụ tài khoản; Proxy mua mail dùng danh sách riêng.\n\n'
        'Tổng luồng tự tính theo số proxy × luồng/proxy. Ví dụ 5 proxy × 1 luồng/proxy = 5 tài khoản đồng thời.\n\n'
        'Khi request lỗi, VPS thực hiện phục hồi theo cấu hình hiện có.')
    note.setObjectName('muted');note.setWordWrap(True);help_box.addWidget(note);help_box.addStretch()
    common_box.addWidget(help_card,2)
    mail=window.settings_tabs.widget(1);mail_box=QHBoxLayout(mail);mail_box.setContentsMargins(0,12,0,0);mail_box.setSpacing(16)
    config,config_box=card('Mochi Chat')
    grid=QGridLayout();grid.setVerticalSpacing(14);grid.setHorizontalSpacing(14)
    provider=QComboBox()
    for caption,value in (('GmailVip.vn (Tự động mua)','gmailvip_vn'),('1TrieuMail.com (Tự động mua)','1trieumail_com'),('Cả hai shop (theo thứ tự ưu tiên)','multi_shop')):
        provider.addItem(caption,value)
    window.settings_fields['provider']=provider
    entries=[('Nguồn mua Mail',provider)]
    for key,caption in (('gmailvip_key','API GmailVip'),('one_trieu_key','API 1TrieuMail'),('product_ids','Product IDs / Thứ tự ưu tiên'),('sale_rate','Giá bán VND / 100 bean')):
        field=QLineEdit();window.settings_fields[key]=field
        if key.endswith('_key'):
            field.setEchoMode(QLineEdit.EchoMode.Password);field.setPlaceholderText('Để trống giữ key đã lưu trên VPS')
        entries.append((caption,field))
    for key,caption,limit in (('concurrency','Số luồng Direct',50),('job_limit','Số job chạy cùng lúc',100)):
        field=QSpinBox();field.setRange(1,limit);window.settings_fields[key]=field;entries.append((caption,field))
    for row,(caption,field) in enumerate(entries):
        label=QLabel(caption);label.setWordWrap(True);label.setMaximumWidth(145)
        grid.addWidget(label,row,0);grid.addWidget(field,row,1)
    grid.setColumnMinimumWidth(0,145)
    grid.setColumnStretch(1,1);config_box.addLayout(grid);config_box.addStretch()
    mail_box.addWidget(config,3)
    help_card,help_box=card('Mail và tài khoản')
    note=QLabel('Nhập API key và danh sách ID sản phẩm theo nhà cung cấp.\n\n'
        'Chọn Cả hai shop để nhập đồng thời hai API key. Thứ tự ưu tiên có dạng gmailvip:3445, 1trieumail:3423.\n\n'
        'Job mua 1 mail thử mã. Thành công mới mua phần còn thiếu; không mua bù khi tài khoản lỗi.\n\n'
        'Số job chạy cùng lúc áp dụng chung cho job tạo từ máy tính, admin, partner và khách hàng. Job chờ chưa mua mail.')
    note.setObjectName('muted');note.setWordWrap(True);help_box.addWidget(note);help_box.addStretch();mail_box.addWidget(help_card,2)
    footer=QHBoxLayout()
    window.settings_status=QLabel('Cấu hình dùng cho job mới; giới hạn số job áp dụng ngay.')
    window.settings_status.setObjectName('muted');window.settings_status.setWordWrap(True)
    footer.addWidget(window.settings_status,1)
    window.button('Nạp lại cấu hình VPS',lambda:window.load_settings(window.snapshot),footer,mutation=False)
    save=window.button('Lưu thay đổi',window.save_settings,footer);save.setProperty('primary',True)
    box.addLayout(footer)
    return page


def format_time(value):
    if not value:
        return ''
    try:
        return datetime.fromisoformat(str(value)).astimezone(VN).strftime('%d/%m %H:%M')
    except ValueError:
        return str(value)
