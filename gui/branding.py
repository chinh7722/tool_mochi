"""Optional user artwork, shared by the source and src launch paths."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QFrame


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if PROJECT_ROOT.name == 'src':
    PROJECT_ROOT = PROJECT_ROOT.parent
ASSET_DIR = PROJECT_ROOT / 'gui' / 'assets' / 'branding'
FILES = {
    'logo': ('logo.png', 'logo.svg'),
    'app_icon': ('app.ico', 'logo.png', 'logo.svg'),
    'sidebar_background': ('sidebar-background.png', 'sidebar-background.jpg'),
    'dashboard_banner': ('dashboard-banner.png', 'dashboard-banner.jpg'),
    'nav_dashboard': ('nav-dashboard.svg', 'nav-dashboard.png'),
    'nav_mochi': ('nav-mochi.svg', 'nav-mochi.png'),
    'nav_proxy': ('nav-proxy.svg', 'nav-proxy.png'),
    'nav_telegram': ('nav-telegram.svg', 'nav-telegram.png'),
    'nav_settings': ('nav-settings.svg', 'nav-settings.png'),
}


def asset_path(name: str) -> str:
    """Empty means use the built-in text/color fallback; never needs an image."""
    for filename in FILES.get(name, (name,)):
        path = ASSET_DIR / filename
        if path.is_file():
            return str(path)
    return ''


def icon(name: str, fallback: str = '') -> QIcon:
    path = asset_path(name)
    if not path and fallback:
        candidate = PROJECT_ROOT / 'gui' / 'assets' / fallback
        path = str(candidate) if candidate.is_file() else ''
    return QIcon(path) if path else QIcon()


class BrandSidebar(QFrame):
    """Cover an optional sidebar image without stretching its aspect ratio."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('sidebar')
        self._artwork = QPixmap(asset_path('sidebar_background'))

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._artwork.isNull():
            return
        painter = QPainter(self)
        scaled = self._artwork.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((self.width() - scaled.width()) // 2,
            (self.height() - scaled.height()) // 2, scaled)
        painter.fillRect(self.rect(), Qt.GlobalColor.transparent)


WORKSPACE_STYLE = '''
    QTabBar::tab { background: #121824; color: #9aa8c4; border: 1px solid transparent;
        padding: 11px 22px; font-weight: 600; }
    QTabBar::tab:selected { background: #192744; color: #60a5fa;
        border: 1px solid #35558a; border-radius: 7px; }
    QTabBar::tab:hover:!selected { color: #f8fafc; background: #262b35; }
    QTabBar::base { border: none; }
    QScrollArea { border: none; background: transparent; }
    QScrollArea > QWidget > QWidget { background: transparent; }
    QLineEdit, QComboBox, QSpinBox { background: #1d1d20; color: #edf2fc;
        border: 1px solid #3a3a42; border-radius: 7px; padding: 9px 12px; min-height: 18px; }
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #3b82f6; }
    QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { color: #747d90; }
    QPushButton { background: #2a2b30; color: #b9d4ff; border: 1px solid #374968;
        border-radius: 7px; padding: 9px 15px; font-weight: 600; }
    QPushButton:hover { background: #303849; border-color: #4b80d8; }
    QPushButton:disabled { background: #24262b; border-color: #34363d; color: #667085; }
    QPushButton[primary="true"] { background: #2563eb; border-color: #2563eb; color: white; }
    QPushButton[primary="true"]:hover { background: #3475f5; }
    QPushButton[primary="true"]:disabled { background: #203d70; color: #829ac2; border-color: #203d70; }
    QCheckBox, QRadioButton { color: #bcc8de; spacing: 8px; }
    QCheckBox::indicator, QRadioButton::indicator { width: 15px; height: 15px;
        border: 1px solid #606878; background: #1d1d20; border-radius: 3px; }
    QRadioButton::indicator { border-radius: 8px; }
    QCheckBox::indicator:checked { background: #2563eb; border-color: #3b82f6;
        image: url(CHECK_IMAGE); }
    QRadioButton::indicator:checked { background: #60a5fa; border: 4px solid #253652; }
    QCheckBox::indicator:disabled { border-color: #4a4b55; background: #292b32; }
    QComboBox::drop-down { border: none; width: 24px; }
    QComboBox::down-arrow { image: url(DOWN_IMAGE); width: 12px; height: 12px; }
    QComboBox QAbstractItemView { background: #232326; color: #edf2fc;
        selection-background-color: #254a85; }
    QSpinBox::up-button, QSpinBox::down-button { subcontrol-origin: border;
        width: 20px; border: none; background: transparent; }
    QSpinBox::up-arrow { image: url(UP_IMAGE); width: 9px; height: 9px; }
    QSpinBox::down-arrow { image: url(DOWN_IMAGE); width: 9px; height: 9px; }
'''
for placeholder, filename in (('CHECK_IMAGE', 'checkbox_check_white.svg'),
        ('DOWN_IMAGE', 'chevron_down.svg'), ('UP_IMAGE', 'spin_up.svg')):
    WORKSPACE_STYLE = WORKSPACE_STYLE.replace(placeholder,
        '"' + (PROJECT_ROOT / 'gui' / 'assets' / filename).as_posix() + '"')
