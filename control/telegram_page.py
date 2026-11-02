"""Telegram settings and user dashboard, matching the desktop controller theme."""
from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from telegram_bot import TelegramClient, TelegramPoller, parse_job_command


def number(value):
    return f"{int(value):,}"


class TelegramPage(QWidget):
    job_requested = pyqtSignal(dict)
    access_revoked = pyqtSignal(str)
    log_signal = pyqtSignal(str)

    def __init__(self, store, parent=None, auto_start=True):
        super().__init__(parent)
        self.store = store
        self.poller = None
        self.accepting = False
        self._shutting_down = False
        self.bot_id, self.username = "", ""
        self._signature = None
        self._dialogs = []
        self._build_ui()
        try:
            self.edit_token.setText(store.token())
        except Exception as error:
            self.status.setText(str(error))
        self.auto_start.setChecked(bool(store.config("auto_start", False)))
        self.edit_rate.setText(str(store.config("rate", 1000)))
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)
        if auto_start and self.auto_start.isChecked() and self.edit_token.text():
            QTimer.singleShot(0, self.start_bot)

    @staticmethod
    def _card(title=None):
        frame = QFrame()
        frame.setObjectName("telegramCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        if title:
            label = QLabel(title)
            label.setStyleSheet("font-size:14px;font-weight:700;background:transparent;")
            layout.addWidget(label)
        return frame, layout

    @staticmethod
    def _button(text, callback, primary=False):
        button = QPushButton(text)
        button.setProperty("class", "actionBlueBtn" if primary else "secondaryBtn")
        button.clicked.connect(callback)
        return button

    def _build_ui(self):
        self.setStyleSheet("""
            QFrame#telegramCard {background:#222225;border:1px solid #343438;border-radius:10px;}
            QFrame#telegramCard QLabel {background:transparent;}
            QLineEdit, QComboBox {background:#202023;border:1px solid #3a3a42;border-radius:5px;padding:8px;color:#f0f3f6;}
            QTableWidget {background:#202023;border:1px solid #38383e;gridline-color:#34343a;}
            QHeaderView::section {background:#27272d;color:#b5c9e8;border:1px solid #3b3b42;padding:9px;}
            QCheckBox {background:transparent;padding:5px;}
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(14)
        title = QLabel("Telegram")
        title.setStyleSheet("font-size:26px;font-weight:700;")
        layout.addWidget(title)
        subtitle = QLabel("Kết nối bot, quản lý người dùng và theo dõi số konpeito đã giao thành công.")
        subtitle.setStyleSheet("color:#adc5e8;")
        layout.addWidget(subtitle)

        config, config_layout = self._card("Cấu hình bot")
        token_row = QHBoxLayout()
        token_row.addWidget(QLabel("Bot Token"))
        self.edit_token = QLineEdit()
        self.edit_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_token.setPlaceholderText("Nhập Bot Token từ @BotFather")
        token_row.addWidget(self.edit_token, 1)
        config_layout.addLayout(token_row)
        buttons = QHBoxLayout()
        self.auto_start = QCheckBox("Tự bật bot khi mở tool")
        buttons.addWidget(self.auto_start)
        buttons.addStretch()
        self.btn_save = self._button("Lưu cấu hình", self.save_config)
        self.btn_start = self._button("Bật bot", self.start_bot, True)
        self.btn_stop = self._button("Tắt bot", self.stop_bot)
        self.btn_stop.setEnabled(False)
        for button in (self.btn_save, self.btn_start, self.btn_stop):
            buttons.addWidget(button)
        config_layout.addLayout(buttons)
        self.status = QLabel("Bot chưa bật. Nhập token, lưu cấu hình rồi bấm Bật bot.")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#58a6ff;")
        config_layout.addWidget(self.status)
        help_text = QLabel(
            "/id — lấy Chat ID    ·    /job MÃ_MỜI KONPEITO [TÊN_KHÁCH] — thêm job    ·    /stats — thống kê cá nhân\n"
            "Ví dụ: /job npxMgy 2000 Khách A. Có thể gửi nhiều dòng /job trong một tin nhắn.\n"
            "Chỉ nhận chat riêng từ người được cấp quyền khi bot đang bật. Lệnh gửi lúc bot tắt được bỏ qua. Mã nhận trước chạy trước trong hàng chờ chung.")
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color:#adc5e8;font-size:12px;")
        config_layout.addWidget(help_text)
        layout.addWidget(config)

        price, price_layout = self._card()
        price_row = QHBoxLayout()
        price_row.addWidget(QLabel("Giá bán (VND / 100 konpeito)"))
        self.edit_rate = QLineEdit("1000")
        price_row.addWidget(self.edit_rate, 1)
        price_row.addWidget(self._button("Lưu đơn giá", self.save_rate))
        price_row.addWidget(self._button("Giao dịch / đối soát", lambda: self.show_jobs()))
        price_layout.addLayout(price_row)
        note = QLabel("Giá được chốt khi thêm job Telegram. Doanh thu ghi nhận theo konpeito đã giao khi job hoàn thành. Thống kê các job Controller ghi nhận từ lúc bật tính năng này.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#adc5e8;font-size:12px;")
        price_layout.addWidget(note)
        layout.addWidget(price)

        stats = QGridLayout()
        self.stat_labels = {}
        for col, (key, label, color) in enumerate((
            ("users", "Người dùng được cấp quyền", "#4287ff"),
            ("completed", "Job hoàn thành", "#20c66a"),
            ("failed", "Job thất bại / đã hủy", "#f16b79"),
            ("konpeito", "Tổng konpeito đã bán", "#b67cff"),
        )):
            card, card_layout = self._card()
            card.setStyleSheet(f"QFrame#telegramCard {{border-top:2px solid {color};}}")
            heading = QLabel(label)
            heading.setWordWrap(True)
            heading.setStyleSheet("color:#adc5e8;font-size:12px;")
            value = QLabel("0")
            value.setStyleSheet("font-size:23px;font-weight:700;")
            card_layout.addWidget(heading)
            card_layout.addWidget(value)
            self.stat_labels[key] = value
            stats.addWidget(card, 0, col)
            stats.setColumnStretch(col, 1)
        layout.addLayout(stats)
        revenue, revenue_layout = self._card()
        revenue_layout.addWidget(QLabel("Doanh thu (VND)"))
        self.stat_labels["revenue"] = QLabel("0")
        self.stat_labels["revenue"].setStyleSheet("color:#58a6ff;font-size:24px;font-weight:700;")
        revenue_layout.addWidget(self.stat_labels["revenue"])
        layout.addWidget(revenue)

        users, users_layout = self._card()
        user_header = QHBoxLayout()
        heading = QLabel("Quản lý người dùng")
        heading.setStyleSheet("font-weight:700;font-size:14px;")
        user_header.addWidget(heading)
        user_header.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm tên, Chat ID hoặc vai trò...")
        self.search.setMinimumWidth(280)
        self.search.textChanged.connect(lambda: self.refresh(force=True))
        user_header.addWidget(self.search, 1)
        users_layout.addLayout(user_header)
        permission_hint = QLabel("Admin / partner chỉ dùng phân loại. Mọi Chat ID được cấp quyền đều tạo job và xem số liệu của chính mình.")
        permission_hint.setStyleSheet("color:#adc5e8;font-size:12px;")
        permission_hint.setWordWrap(True)
        users_layout.addWidget(permission_hint)
        form = QGridLayout()
        self.edit_chat = QLineEdit()
        self.edit_chat.setPlaceholderText("Chat ID lấy bằng /id")
        self.edit_user_name = QLineEdit()
        self.edit_user_name.setPlaceholderText("Tên người dùng")
        self.role = QComboBox()
        self.role.addItems(["partner", "admin"])
        for col, (label, widget) in enumerate((("Chat ID", self.edit_chat), ("Tên người dùng", self.edit_user_name), ("Vai trò", self.role))):
            form.addWidget(QLabel(label), 0, col)
            form.addWidget(widget, 1, col)
        form.setColumnStretch(0, 2)
        form.setColumnStretch(1, 3)
        form.setColumnStretch(2, 1)
        users_layout.addLayout(form)
        actions = QHBoxLayout()
        actions.addWidget(self._button("+ Thêm người dùng", lambda: self.save_user(create=True), True))
        actions.addWidget(self._button("Lưu tên / vai trò", self.save_user))
        actions.addWidget(self._button("Thu hồi quyền", lambda: self.change_access(False)))
        actions.addWidget(self._button("Khôi phục quyền", lambda: self.change_access(True)))
        actions.addWidget(self._button("Xem job", self.show_user_jobs))
        actions.addStretch()
        users_layout.addLayout(actions)
        self.user_message = QLabel("")
        self.user_message.setWordWrap(True)
        self.user_message.setStyleSheet("color:#58a6ff;")
        users_layout.addWidget(self.user_message)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Chat ID", "Tên", "Vai trò", "Job\nhoàn thành", "Job\nthất bại / hủy", "Konpeito\nđã bán", "Quyền truy cập", "Doanh thu\n(VND)"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(260)
        self.table.itemSelectionChanged.connect(self._select_user)
        users_layout.addWidget(self.table)
        layout.addWidget(users, 1)
        scroll.setWidget(body)
        outer.addWidget(scroll)

    def save_config(self):
        try:
            token = self.edit_token.text().strip()
            TelegramClient(token)  # Validate locally; never make a network call here.
            if self.poller and self.poller.isRunning() and token != self.poller.client._token:
                raise ValueError("Tắt bot trước khi đổi Bot Token.")
            self.store.save_bot(token, self.auto_start.isChecked())
            self.status.setText("Đã lưu cấu hình. Bot Token được bảo vệ theo tài khoản Windows hiện tại.")
            return True
        except Exception as error:
            self.status.setText(str(error))
            return False

    def save_rate(self):
        try:
            rate = int(self.edit_rate.text().strip())
            if not 0 <= rate <= 1_000_000_000:
                raise ValueError()
            self.store.set_config(rate=rate)
            self.status.setText(f"Đã lưu đơn giá {number(rate)} VND / 100 konpeito. Job đã thêm giữ nguyên giá cũ.")
        except ValueError:
            self.status.setText("Đơn giá phải là số nguyên từ 0 đến 1.000.000.000 VND / 100 konpeito.")

    def start_bot(self):
        if self._shutting_down or self.poller is not None or not self.save_config():
            return
        self.poller = TelegramPoller(self.edit_token.text(), self.store)
        self.poller.connected.connect(self._connected)
        self.poller.update_received.connect(self.handle_update)
        self.poller.status_changed.connect(self.status.setText)
        self.poller.log_signal.connect(self.log_signal.emit)
        self.poller.finished.connect(self._stopped)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.edit_token.setEnabled(False)
        self.status.setText("Đang kết nối Telegram...")
        self.poller.start()

    def _connected(self, me):
        self.bot_id, self.username = str(me["id"]), me.get("username", "")
        self.accepting = bool(self.poller and not self.poller._stop_event.is_set())

    def stop_bot(self):
        self.accepting = False
        if self.poller:
            self.poller.stop()
            self.status.setText("Đang tắt bot... Job đã vào hàng chờ vẫn tiếp tục.")

    def _stopped(self):
        old = self.poller
        self.poller = None
        self.accepting = False
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.edit_token.setEnabled(True)
        if self.status.text().startswith("Đang tắt"):
            self.status.setText("Bot đã tắt. Các job đã nhận vẫn nằm trong hàng chờ.")
        if old:
            old.deleteLater()

    def shutdown(self):
        self._shutting_down = True
        self.timer.stop()
        self.accepting = False
        if self.poller:
            self.poller.stop()
        return bool(self.poller and self.poller.isRunning())

    def save_user(self, create=False):
        try:
            self.store.save_user(self.edit_chat.text(), self.edit_user_name.text(), self.role.currentText(), create=create)
            self.user_message.setText("Đã thêm người dùng." if create else "Đã lưu tên / vai trò.")
            self.refresh(force=True)
        except ValueError as error:
            self.user_message.setText(str(error))

    def change_access(self, active):
        chat_id = self.edit_chat.text().strip()
        if not self.store.user(chat_id):
            self.user_message.setText("Chọn một Chat ID trong danh sách trước.")
            return
        self.store.set_access(chat_id, active)
        if not active:
            self.access_revoked.emit(chat_id)
        self.user_message.setText("Đã khôi phục quyền." if active else "Đã thu hồi quyền; các mã còn chờ của người dùng đã được hủy.")
        self.refresh(force=True)

    def _select_user(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        chat_id = self.table.item(row, 0).text()
        self.edit_chat.setText(chat_id)
        self.edit_user_name.setText(self.table.item(row, 1).text())
        self.role.setCurrentText(self.table.item(row, 2).text())

    def refresh(self, force=False):
        totals = self.store.stats()
        for key, label in self.stat_labels.items():
            label.setText(number(totals[key]))
        users = [{"chat_id": "LOCAL", "name": "Local", "role": "local", "active": 1}, *self.store.users()]
        query = self.search.text().strip().casefold()
        visible = []
        for user in users:
            if query and query not in " ".join(str(user[key]) for key in ("chat_id", "name", "role")).casefold():
                continue
            stats = self.store.stats(user["chat_id"])
            visible.append((user["chat_id"], user["name"], user["role"], str(stats["completed"]),
                            str(stats["failed"]), number(stats["konpeito"]),
                            "Máy tính" if user["chat_id"] == "LOCAL" else ("Được phép" if user["active"] else "Đã thu hồi"), number(stats["revenue"])))
        signature = tuple(visible)
        if not force and signature == self._signature:
            return
        self._signature = signature
        selected = self.edit_chat.text()
        self.table.blockSignals(True)
        self.table.setRowCount(len(visible))
        for row, values in enumerate(visible):
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
            if values[0] == selected:
                self.table.selectRow(row)
        self.table.blockSignals(False)

    def show_user_jobs(self):
        chat_id = self.edit_chat.text().strip()
        if chat_id != "LOCAL" and not self.store.user(chat_id):
            self.user_message.setText("Chọn người dùng để xem job.")
            return
        self.show_jobs(chat_id)

    def show_jobs(self, chat_id=None):
        dialog = QDialog(self)
        dialog.setWindowTitle("Giao dịch / đối soát" + (f" — {chat_id}" if chat_id else ""))
        dialog.resize(1080, 520)
        layout = QVBoxLayout(dialog)
        hint = QLabel("Dữ liệu được lưu trên máy. Doanh thu chỉ ghi nhận cho job hoàn thành; giá đã chốt không đổi khi sửa đơn giá.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        rows = self.store.jobs(chat_id)
        table = QTableWidget(len(rows), 9)
        table.setHorizontalHeaderLabels(["Thời gian (VN)", "Chat ID", "Mã mời", "Tên job", "Konpeito mục tiêu", "Konpeito đã giao", "Trạng thái", "Giá chốt (VND)", "Doanh thu (VND)"])
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for row, entry in enumerate(rows):
            created = datetime.fromisoformat(entry["created_at"]).astimezone(timezone(timedelta(hours=7))).strftime("%d/%m/%Y %H:%M:%S")
            status = {"queued": "Đang chờ", "submitting": "Đang gửi", "running": "Đang chạy", "completed": "Hoàn thành",
                      "failed": "Thất bại", "cancelled": "Đã hủy", "invalid_code": "Mã không hợp lệ",
                      "error": "Cần kiểm tra", "interrupted": "Gián đoạn — kiểm tra bản local"}.get(entry["status"], entry["status"])
            values = (created, entry["chat_id"], entry["code"], entry["name"], number(entry["points"]), number(entry["achieved"]), status, number(entry["money"]), number(entry["revenue"]))
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(entry["error"] or (entry["backend_id"] or ""))
                table.setItem(row, col, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table)
        layout.addWidget(self._button("Đóng", dialog.close))
        self._dialogs.append(dialog)
        dialog.finished.connect(lambda: self._dialogs.remove(dialog) if dialog in self._dialogs else None)
        dialog.show()

    def handle_update(self, update):
        if not self.accepting:
            return
        message = update.get("message") or {}
        chat, sender = message.get("chat") or {}, message.get("from") or {}
        if chat.get("type") != "private" or chat.get("id") != sender.get("id") or sender.get("is_bot"):
            return
        chat_id = str(chat["id"])
        bot_id = str(update.get("bot_id", self.bot_id))
        text = message.get("text", "").strip()
        for line_number, line in enumerate(text.splitlines()):
            if not line.strip():
                continue
            command = line.split(maxsplit=1)[0].lower()
            if "@" in command:
                command, username = command.split("@", 1)
                if username != self.username.lower():
                    continue
            if command == "/id":
                self.store.notify(bot_id, chat_id, f"Chat ID của bạn: {chat_id}")
                continue
            user = self.store.user(chat_id)
            if not user or not user["active"]:
                self.store.notify(bot_id, chat_id, f"Chat ID {chat_id} chưa được cấp quyền. Liên hệ người quản lý tool để được thêm/khôi phục quyền.")
                continue
            if command == "/stats":
                stats = self.store.stats(chat_id)
                self.store.notify(bot_id, chat_id,
                    f"Thống kê của {user['name']}\nJob hoàn thành: {stats['completed']}\nJob thất bại / hủy: {stats['failed']}\nKonpeito đã bán: {number(stats['konpeito'])}\nDoanh thu: {number(stats['revenue'])} VND")
            elif command == "/job":
                try:
                    code, points, name = parse_job_command(line)
                    rate = int(self.store.config("rate", 1000))
                    self.job_requested.emit({"chat_id": chat_id, "bot_id": bot_id, "code": code,
                        "points": points, "name": name, "rate": rate, "money": (points * rate + 50) // 100,
                        "external_key": f"tg:{bot_id}:{update['update_id']}:{line_number}"})
                except (ValueError, KeyError) as error:
                    self.store.notify(bot_id, chat_id, str(error))
            else:
                self.store.notify(bot_id, chat_id,
                    "/id — lấy Chat ID\n/job MÃ_MỜI KONPEITO [TÊN_KHÁCH] — thêm job\n/stats — thống kê cá nhân\n"
                    "Ví dụ: /job npxMgy 2000 Khách A\nMỗi dòng một lệnh /job. Mã thêm sau nằm cuối hàng chờ.")
