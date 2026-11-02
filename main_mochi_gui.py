"""hoshino-tool desktop launcher."""
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

# Keep the bundled root DLLs out of the Python module search path.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
while PROJECT_ROOT in sys.path:
    sys.path.remove(PROJECT_ROOT)
while '' in sys.path:
    sys.path.remove('')
SRC_DIR = os.path.join(PROJECT_ROOT, 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

try:
    import PySide6.QtSvg
except ImportError:
    pass

from PySide6.QtCore import QSettings, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)
from gui.branding import ASSET_DIR, BrandSidebar, asset_path, icon
from gui.pages.dashboard import DashboardPage
from gui.pages.mochi_chat import MochiChatPage
from gui.pages.proxy import ProxyPage
from gui.pages.mail_proxy import MailProxyPage
from gui.pages.telegram import TelegramPage


class MochiMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('hoshino-tool')
        self.resize(1280, 900)
        self.setMinimumSize(1120, 760)
        self.setWindowIcon(icon('app_icon'))
        self.setStyleSheet('''
            QMainWindow { background: #18181b; }
            QWidget { color: #edf2fc; font-family: 'Segoe UI'; font-size: 13px; }
            QFrame#sidebar { background: #202023; border-right: 1px solid #303037; }
            QStackedWidget#mainPages { background: #18181b; }
            QPushButton[nav="true"] { text-align: left; padding: 12px 14px;
                color: #adbcd6; background: transparent; border: none;
                border-radius: 8px; font-weight: 500; }
            QPushButton[nav="true"]:hover { background: #2b303b; color: white; }
            QPushButton[nav="true"]:checked { background: #2563eb; color: white; font-weight: 600; }
            QScrollBar:vertical { background: #202023; width: 9px; border: none; }
            QScrollBar::handle:vertical { background: #4a4b55; min-height: 25px; border-radius: 4px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar:horizontal { background: #202023; height: 9px; border: none; }
            QScrollBar::handle:horizontal { background: #4a4b55; min-width: 25px; border-radius: 4px; }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
        ''')
        # Preserve the original settings namespace and saved credentials/proxies.
        self.settings = QSettings('Hermit Coder', 'TTM Tool')
        central = QWidget(self)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = self._create_sidebar()
        self.header_bar = self.sidebar
        root.addWidget(self.sidebar)
        self.stacked = QStackedWidget(self)
        self.stacked.setObjectName('mainPages')
        self.proxy_page = ProxyPage(settings=self.settings, parent=self)
        self.mail_proxy_page=MailProxyPage(settings=self.settings,parent=self)
        self.mochi_page = MochiChatPage(settings=self.settings, parent=self, proxy_page=self.proxy_page)
        self.mochi_page.mail_proxy_page=self.mail_proxy_page
        self.telegram_page = TelegramPage(settings=self.settings, mochi_page=self.mochi_page, parent=self)
        self.dashboard_page = DashboardPage(self.mochi_page, self.proxy_page, self.telegram_page, self)
        # Keep established page indices for existing integrations.
        for page in (self.mochi_page, self.proxy_page, self.telegram_page,
                self.dashboard_page, self.mochi_page.settings_page,self.mail_proxy_page):
            self.stacked.addWidget(page)
        root.addWidget(self.stacked, 1)
        self.setCentralWidget(central)
        self.proxy_page.proxies_updated.connect(self._on_proxies_updated)
        self._on_proxies_updated()
        self.switch_to_mochi_tab()

    def _create_sidebar(self):
        sidebar = BrandSidebar(self)
        sidebar.setFixedWidth(220)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(15, 24, 15, 16)
        layout.setSpacing(6)
        logo = QPixmap(asset_path('logo'))
        if not logo.isNull():
            image = QLabel(sidebar)
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setPixmap(logo.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            layout.addWidget(image)
        brand = QLabel('hoshino-tool', sidebar)
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setStyleSheet('font-size: 24px; font-weight: 800; color: #eff6ff; letter-spacing: 0.3px;')
        layout.addWidget(brand)
        caption = QLabel('MOCHI · PROXY · TELEGRAM', sidebar)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        caption.setStyleSheet('font-size: 9px; color: #93a5c4; margin-top: 2px; margin-bottom: 24px;')
        layout.addWidget(caption)
        self._nav_buttons = {}

        def section(text):
            label = QLabel(text, sidebar)
            label.setStyleSheet('font-size: 10px; font-weight: 700; color: #69758b; margin: 15px 12px 8px;')
            layout.addWidget(label)

        def button(text, index, callback, image_name, fallback):
            btn = QPushButton(text, sidebar)
            btn.setProperty('nav', True)
            btn.setCheckable(True)
            btn.setMinimumHeight(43)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setIcon(icon(image_name, fallback))
            btn.setIconSize(QSize(20, 20))
            btn.clicked.connect(callback)
            self._nav_buttons[index] = btn
            layout.addWidget(btn)
            return btn

        section('TỔNG QUAN')
        self.btn_tab_dashboard = button('Dashboard', 3, self.switch_to_dashboard_tab,
            'nav_dashboard', 'nav_dashboard.svg')
        section('NỀN TẢNG')
        self.btn_tab_mochi = button('Mochi Chat', 0, self.switch_to_mochi_tab,
            'nav_mochi', 'nav_mochi_chat.svg')
        section('HỆ THỐNG')
        self.btn_tab_proxy = button('Proxy Pool', 1, self.switch_to_proxy_tab,
            'nav_proxy', 'nav_proxy.svg')
        self.btn_tab_mail_proxy=button('Proxy mua mail',5,self.switch_to_mail_proxy_tab,'nav_proxy','nav_proxy.svg')
        self.btn_tab_telegram = button('Telegram', 2, self.switch_to_telegram_tab,
            'nav_telegram', 'nav_telegram.svg')
        self.btn_tab_settings = button('Cài đặt chung', 4, self.switch_to_settings_tab,
            'nav_settings', 'nav_settings.svg')
        layout.addStretch()
        self.btn_proxy_pill = QPushButton('Chưa có proxy', sidebar)
        self.btn_proxy_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_proxy_pill.setStyleSheet('text-align: left; padding: 12px; border: 1px solid #3a3a42; '
            'border-radius: 8px; background: #29292e; color: #9aa8c4; font-size: 11px;')
        self.btn_proxy_pill.clicked.connect(self.switch_to_proxy_tab)
        layout.addWidget(self.btn_proxy_pill)
        self.btn_assets = QPushButton('Mở thư mục hình ảnh', sidebar)
        self.btn_assets.setToolTip(str(ASSET_DIR))
        self.btn_assets.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_assets.setStyleSheet('text-align: left; background: transparent; border: none; '
            'color: #9aa8c4; padding: 8px 5px; font-size: 10px;')
        self.btn_assets.clicked.connect(self._open_asset_folder)
        layout.addWidget(self.btn_assets)
        local = QLabel('Local edition  ·  v2.1', sidebar)
        local.setStyleSheet('color: #6f7e96; font-size: 10px; margin-left: 5px;')
        layout.addWidget(local)
        return sidebar

    def _open_asset_folder(self):
        ASSET_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(ASSET_DIR)))

    def _activate_page(self, index):
        self.stacked.setCurrentIndex(index)
        self._update_tab_styles(index)

    def switch_to_mochi_tab(self):
        self._activate_page(0)

    def switch_to_proxy_tab(self):
        self._activate_page(1)

    def switch_to_mail_proxy_tab(self):
        self._activate_page(5)

    def switch_to_telegram_tab(self):
        self._activate_page(2)

    def switch_to_dashboard_tab(self):
        self.dashboard_page.refresh()
        self._activate_page(3)

    def switch_to_settings_tab(self):
        self._activate_page(4)

    def _update_tab_styles(self, active_index):
        for index, button in self._nav_buttons.items():
            button.setChecked(index == active_index)

    def _on_proxies_updated(self):
        count = len(self.proxy_page.get_active_proxies())
        self.btn_proxy_pill.setText(f'●  {count} proxy sẵn sàng' if count else '○  Chưa có proxy')
        self.mochi_page._update_proxy_tab_status()

    def closeEvent(self, event):
        telegram_busy = self.telegram_page.shutdown_workers()
        mochi_busy = self.mochi_page.shutdown_workers()
        proxy_busy = self.proxy_page.shutdown_workers()
        mail_proxy_busy=self.mail_proxy_page.shutdown_workers()
        if telegram_busy or mochi_busy or proxy_busy or mail_proxy_busy:
            event.ignore()
            QTimer.singleShot(100, self.close)
        else:
            super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationDisplayName('hoshino-tool')
    app.setFont(QFont('Segoe UI', 10))
    if '--local' not in sys.argv:
        from gui.remote_window import RemoteMainWindow
        window = RemoteMainWindow()
    else:
        window = MochiMainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
