"""Presentation primitives shared by the desktop VPS management pages."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class ArtworkBanner(QFrame):
    """Cover optional artwork while keeping dashboard labels readable."""
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setObjectName('dashboardCard')
        self.setMinimumHeight(120)
        self._artwork=QPixmap()

    def set_artwork(self,pixmap):
        self._artwork=pixmap
        self.update()

    def paintEvent(self,event):
        super().paintEvent(event)
        if self._artwork.isNull():
            return
        painter=QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip=QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()),10,10)
        painter.setClipPath(clip)
        scaled=self._artwork.scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        painter.drawPixmap((self.width()-scaled.width())//2,(self.height()-scaled.height())//2,scaled)
        painter.fillRect(self.rect(),QColor(10,17,32,185))


def heading(title, subtitle=''):
    widget = QFrame()
    widget.setObjectName('pageHeading')
    box = QVBoxLayout(widget)
    box.setContentsMargins(0, 0, 0, 0)
    box.setSpacing(5)
    label = QLabel(title)
    label.setObjectName('pageTitle')
    box.addWidget(label)
    if subtitle:
        note = QLabel(subtitle)
        note.setObjectName('muted')
        note.setWordWrap(True)
        box.addWidget(note)
    return widget


def card(title=None, subtitle=None):
    widget = QFrame()
    widget.setObjectName('remoteCard')
    box = QVBoxLayout(widget)
    box.setContentsMargins(18, 16, 18, 16)
    box.setSpacing(12)
    if title:
        label = QLabel(title)
        label.setObjectName('cardTitle')
        box.addWidget(label)
    if subtitle:
        note = QLabel(subtitle)
        note.setObjectName('muted')
        note.setWordWrap(True)
        box.addWidget(note)
    return widget, box


def stat_card(title, accent='#60a5fa', hint=''):
    widget, box = card()
    widget.setMinimumHeight(106)
    widget.setStyleSheet('QFrame#remoteCard {border-top:2px solid '+accent+';}')
    box.setSpacing(5)
    caption = QLabel(title)
    caption.setObjectName('muted')
    box.addWidget(caption)
    value = QLabel('0')
    value.setStyleSheet('font-size:26px;font-weight:700;color:'+accent+';')
    box.addWidget(value)
    if hint:
        note = QLabel(hint)
        note.setObjectName('smallMuted')
        note.setWordWrap(True)
        box.addWidget(note)
    return widget, value


STYLE = '''
    QMainWindow, QWidget {background:#18181b;color:#edf2fc;font-family:'Segoe UI';font-size:13px;}
    QLabel {background:transparent;border:none;}
    QCheckBox,QRadioButton {background:transparent;}
    QFrame#sidebar {background:#202023;border-right:1px solid #303037;}
    QFrame#remoteCard {background:#232326;border:1px solid #323239;border-radius:10px;}
    QFrame#pageHeading {background:transparent;border:none;}
    QLabel#pageTitle {font-size:25px;font-weight:700;color:#f8fafc;}
    QLabel#cardTitle {font-size:15px;font-weight:700;color:#f8fafc;}
    QLabel#muted {color:#9aa8c4;}
    QLabel#smallMuted {color:#8493ac;font-size:11px;}
    QLabel#connectionStatus {color:#9aa8c4;padding:7px 28px;background:#1b1b1f;border-top:1px solid #303037;}
    QPushButton[nav="true"] {text-align:left;padding:12px 14px;color:#adbcd6;background:transparent;
        border:none;border-radius:8px;font-weight:500;}
    QPushButton[nav="true"]:hover {background:#2b303b;color:white;}
    QPushButton[nav="true"]:checked {background:#2563eb;color:white;font-weight:600;}
    QTableWidget {background:#1d1d20;color:#f8fafc;border:1px solid #3a3a42;
        border-radius:7px;gridline-color:#34343b;padding:0;}
    QTableWidget::item {padding:6px;}
    QTableWidget::item:selected {background:#253654;color:#f8fafc;}
    QHeaderView::section {background:#29292e;color:#a7b8d5;padding:10px 7px;border:none;
        border-right:1px solid #3b3b43;border-bottom:1px solid #3b3b43;font-weight:600;}
    QPlainTextEdit,QTextEdit {background:#1b1b1e;color:#f1f5f9;border:1px solid #3a3a42;border-radius:6px;padding:8px;}
    QDateTimeEdit {background:#1d1d20;color:#edf2fc;border:1px solid #3a3a42;border-radius:7px;padding:9px 12px;}
    QTabWidget::pane {border:0;background:transparent;}
    QStackedWidget {background:transparent;border:0;}
    QScrollBar:vertical {background:#202023;width:9px;border:none;}
    QScrollBar::handle:vertical {background:#4a4b55;min-height:25px;border-radius:4px;}
    QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
    QScrollBar:horizontal {background:#202023;height:9px;border:none;}
    QScrollBar::handle:horizontal {background:#4a4b55;min-width:25px;border-radius:4px;}
    QScrollBar::add-line:horizontal,QScrollBar::sub-line:horizontal {width:0;}
'''
