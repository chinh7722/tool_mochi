"""Classic desktop shell and branding for the remote-only VPS manager."""
import json
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QFrame,QHBoxLayout,QLabel,QLineEdit,QPushButton,
    QScrollArea,QStackedWidget,QVBoxLayout,QWidget)
from gui.branding import ASSET_DIR, BrandSidebar, WORKSPACE_STYLE, asset_path, icon
from gui.remote_widgets import STYLE


def initialize(self):
    self.setWindowTitle('hoshino-tool · Quản lý VPS')
    self.setWindowIcon(icon('app_icon'))
    self.resize(1280,900)
    self.setMinimumSize(1120,760)
    self.setStyleSheet(STYLE+WORKSPACE_STYLE)
    self.preferences=QSettings('Hermit Coder','Hoshino Remote')
    self.client=None
    self.snapshot={}
    self._workers=set()
    self._polling=False
    self._mutating=False
    self._closing=False
    self._connected=False
    self._mutation_buttons=[]
    self._job_row_buttons=[]
    self._config_loaded=False
    self._log_job_id=None
    self.proxy_views={}
    self.url=QLineEdit(str(self.preferences.value('url','http://127.0.0.1:8765')))
    self.url.setPlaceholderText('http://127.0.0.1:8765')
    self.token=QLineEdit()
    self.token.setEchoMode(QLineEdit.EchoMode.Password)
    self.token.setPlaceholderText('Token quản lý VPS')
    self.connect_button=QPushButton('Kết nối VPS')
    self.connect_button.setProperty('primary',True)
    self.connect_button.clicked.connect(self.connect_server)
    self.status=QLabel('Mở SSH tunnel rồi kết nối VPS tại Cài đặt chung.')
    self.status.setObjectName('connectionStatus')
    self.status.setTextFormat(Qt.TextFormat.PlainText)
    self.status.setWordWrap(True)
    central=QWidget()
    root=QHBoxLayout(central)
    root.setContentsMargins(0,0,0,0)
    root.setSpacing(0)
    self.nav=[]
    self.sidebar=build_sidebar(self)
    root.addWidget(self.sidebar)
    content=QVBoxLayout()
    content.setContentsMargins(0,0,0,0);content.setSpacing(0)
    self.pages=self.stacked=QStackedWidget()
    self.pages.setObjectName('mainPages')
    self.page_contents={}
    for index,builder in enumerate((self.build_dashboard,self.build_mochi,
            lambda:self.build_proxy('account'),lambda:self.build_proxy('mail'),
            self.build_telegram,self.build_settings)):
        page=builder()
        self.page_contents[index]=page
        if index in (0,5):
            scroll=QScrollArea()
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
        else:
            self.pages.addWidget(page)
    content.addWidget(self.pages,1)
    content.addWidget(self.status)
    root.addLayout(content,1)
    self.setCentralWidget(central)
    self.activate(1)
    self.timer=QTimer(self)
    self.timer.timeout.connect(self.poll)
    self.timer.start(2500)
    root_path=Path(__file__).resolve().parents[1]
    if root_path.name=='src':
        root_path=root_path.parent
    self.connection_path=root_path/'data'/'remote_connection.json'
    try:
        config=json.loads(self.connection_path.read_text(encoding='utf-8'))
        self.url.setText(config.get('url','http://127.0.0.1:8765'))
        self.token.setText(config.get('token',''))
    except (OSError,ValueError,TypeError):
        pass
    self.restore_job_draft()
    self.enable_actions(False)


def build_sidebar(self):
    sidebar=BrandSidebar(self)
    sidebar.setFixedWidth(220)
    box=QVBoxLayout(sidebar)
    box.setContentsMargins(15,24,15,16)
    box.setSpacing(6)
    logo=QPixmap(asset_path('logo'))
    if not logo.isNull():
        picture=QLabel()
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        picture.setPixmap(logo.scaled(64,64,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        box.addWidget(picture)
    brand=QLabel('hoshino-tool')
    brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
    brand.setStyleSheet('font-size:24px;font-weight:800;color:#eff6ff;')
    box.addWidget(brand)
    caption=QLabel('MOCHI · PROXY · TELEGRAM')
    caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
    caption.setStyleSheet('font-size:9px;color:#93a5c4;margin-top:2px;margin-bottom:24px;')
    box.addWidget(caption)
    definitions=(('TỔNG QUAN',(('Dashboard',0,'nav_dashboard','nav_dashboard.svg'),)),
        ('NỀN TẢNG',(('Mochi Chat',1,'nav_mochi','nav_mochi_chat.svg'),)),
        ('HỆ THỐNG',(('Proxy Pool',2,'nav_proxy','nav_proxy.svg'),('Proxy mua mail',3,'nav_proxy','nav_proxy.svg'),
                    ('Telegram',4,'nav_telegram','nav_telegram.svg'),('Cài đặt chung',5,'nav_settings','nav_settings.svg'))))
    self.nav=[None]*6
    for title,buttons in definitions:
        label=QLabel(title)
        label.setStyleSheet('font-size:10px;font-weight:700;color:#69758b;margin:15px 12px 8px;')
        box.addWidget(label)
        for caption,index,asset,fallback in buttons:
            button=QPushButton(caption)
            button.setProperty('nav',True)
            button.setCheckable(True)
            button.setMinimumHeight(43)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setIcon(icon(asset,fallback));button.setIconSize(QSize(20,20))
            button.clicked.connect(lambda _=False,i=index:self.activate(i))
            self.nav[index]=button
            box.addWidget(button)
    box.addStretch()
    self.btn_proxy_pill=QPushButton('○  Chưa có proxy')
    self.btn_proxy_pill.setStyleSheet('text-align:left;padding:12px;border:1px solid #3a3a42;border-radius:8px;'
        'background:#29292e;color:#9aa8c4;font-size:11px;')
    self.btn_proxy_pill.clicked.connect(lambda:self.activate(2))
    box.addWidget(self.btn_proxy_pill)
    self.btn_connection=QPushButton('○  Chưa kết nối VPS')
    self.btn_connection.setStyleSheet('text-align:left;background:transparent;border:none;color:#93a5c4;padding:8px 5px;font-size:11px;')
    self.btn_connection.clicked.connect(self.open_connection_settings)
    box.addWidget(self.btn_connection)
    self.btn_assets=QPushButton('Mở thư mục hình ảnh')
    self.btn_assets.setToolTip(str(ASSET_DIR))
    self.btn_assets.setStyleSheet('text-align:left;background:transparent;border:none;color:#9aa8c4;padding:8px 5px;font-size:10px;')
    self.btn_assets.clicked.connect(open_assets)
    box.addWidget(self.btn_assets)
    edition=QLabel('Quản lý VPS  ·  v2.1')
    edition.setStyleSheet('color:#6f7e96;font-size:10px;margin-left:5px;')
    box.addWidget(edition)
    return sidebar


def open_assets():
    ASSET_DIR.mkdir(parents=True,exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(ASSET_DIR)))
