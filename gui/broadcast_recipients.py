"""Recipient choices for an announcement draft, keyed by durable Telegram IDs."""
from PySide6.QtCore import Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget)


class BroadcastRecipients(QWidget):
    selection_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._users = {}
        self._selected = set()
        box = QVBoxLayout(self)
        box.setContentsMargins(0,0,0,0)
        box.setSpacing(8)
        row = QHBoxLayout()
        label = QLabel('Gửi đến:')
        row.addWidget(label)
        self.mode = QComboBox()
        for caption,value in (('Tất cả admin / partner','all'),('Chỉ admin','admin'),
                ('Chỉ partner','partner'),('Chọn từng người','custom')):
            self.mode.addItem(caption,value)
        row.addWidget(self.mode,1)
        box.addLayout(row)
        self.custom = QWidget()
        custom_box = QVBoxLayout(self.custom)
        custom_box.setContentsMargins(0,0,0,0)
        custom_box.setSpacing(6)
        self.people = QListWidget()
        self.people.setMaximumHeight(170)
        self.people.setMinimumHeight(105)
        self.people.setStyleSheet('QListWidget {background:#1e1e21; color:#d4d4d8; '
            'border:1px solid #3b3b43; border-radius:6px; padding:4px;} '
            'QListWidget::item {padding:5px;}')
        custom_box.addWidget(self.people)
        actions = QHBoxLayout()
        self.select_all_button = QPushButton('Chọn tất cả')
        self.clear_button = QPushButton('Bỏ chọn tất cả')
        actions.addWidget(self.select_all_button)
        actions.addWidget(self.clear_button)
        actions.addStretch()
        custom_box.addLayout(actions)
        box.addWidget(self.custom)
        self.count = QLabel()
        self.count.setTextFormat(Qt.TextFormat.PlainText)
        self.count.setWordWrap(True)
        self.count.setStyleSheet('color:#9ba9c0; font-size:11px; background:transparent;')
        box.addWidget(self.count)
        self._last_recipients = ()
        self.mode.currentIndexChanged.connect(self._changed)
        self.people.itemChanged.connect(self._person_changed)
        self.select_all_button.clicked.connect(lambda: self._select(set(self._users)))
        self.clear_button.clicked.connect(lambda: self._select(set()))
        self._changed()

    def recipient_ids(self):
        mode = self.mode.currentData()
        return tuple(sorted(chat_id for chat_id,user in self._users.items()
            if mode=='all' or (mode=='custom' and chat_id in self._selected) or user['role']==mode))

    def set_users(self, users):
        eligible = {}
        for user in users:
            if not isinstance(user,dict):
                continue
            chat_id = user.get('chat_id')
            if (type(chat_id) is not int or not 1<=chat_id<=2**63-1
                    or user.get('active') is not True or user.get('role') not in ('admin','partner')):
                continue
            eligible[chat_id] = dict(chat_id=chat_id,name=str(user.get('name') or chat_id),role=user['role'])
        self._selected.intersection_update(eligible)
        if eligible != self._users:
            self._users = eligible
            blocker = QSignalBlocker(self.people)
            self.people.clear()
            for user in sorted(eligible.values(),key=lambda row:(row['name'].casefold(),row['chat_id'])):
                item = QListWidgetItem(f"{user['name']} · {user['role']} · {user['chat_id']}")
                item.setData(Qt.ItemDataRole.UserRole,user['chat_id'])
                item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if user['chat_id'] in self._selected
                    else Qt.CheckState.Unchecked)
                self.people.addItem(item)
            del blocker
        self._changed()

    def _person_changed(self, item):
        chat_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState()==Qt.CheckState.Checked and chat_id in self._users:
            self._selected.add(chat_id)
        else:
            self._selected.discard(chat_id)
        self._changed()

    def _select(self, selected):
        self._selected = set(selected)&set(self._users)
        blocker = QSignalBlocker(self.people)
        for row in range(self.people.count()):
            item = self.people.item(row)
            item.setCheckState(Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in self._selected
                else Qt.CheckState.Unchecked)
        del blocker
        self._changed()

    def _changed(self, *_):
        self.custom.setVisible(self.mode.currentData()=='custom')
        recipients = self.recipient_ids()
        self.count.setText(f'Đã chọn {len(recipients)} / {len(self._users)} người đang được cấp quyền.')
        if recipients != self._last_recipients:
            self._last_recipients = recipients
            self.selection_changed.emit()
