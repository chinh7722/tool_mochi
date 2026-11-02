"""Jobs workspace: setup, session jobs, persisted logs and searchable history."""
from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import Qt, QDate, QDateTime, QTime, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDateTimeEdit, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QProgressBar, QPushButton, QStackedWidget, QTabBar,
    QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

from desktop_pages import action, card, muted
from job_history import VN_TIME, creator_text, export_history, status_text, vn_time


class JobHistoryPage(QWidget):
    log_requested = pyqtSignal(str)

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.setStyleSheet("QComboBox,QDateTimeEdit {background:#202123;border:1px solid #3a3a42;border-radius:5px;padding:8px;color:#e6edf7;}")
        self.csv_path = store.path.parent / "job_history.csv"
        self._signature = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        frame, body = card("Lịch sử job")
        body.addWidget(muted("Tự lưu khi job kết thúc, giữ qua lần mở lại tool và khi xóa khỏi phiên. Nhấp đúp một dòng để xem log."))
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm mã job, mã đơn, người tạo, Chat ID, khách hàng hoặc mã mời...")
        filters.addWidget(self.search, 1)
        self.result_filter = QComboBox()
        for text, value in (("Tất cả kết quả", "all"), ("Hoàn thành", "completed"), ("Thất bại", "failed"),
                            ("Đạt giới hạn 24h", "invite_limit_reached"), ("Mã không hợp lệ", "invalid_code"),
                            ("Đã hủy", "cancelled"), ("Cần kiểm tra", "error"), ("Bị gián đoạn", "interrupted")):
            self.result_filter.addItem(text, value)
        filters.addWidget(self.result_filter)
        filters.addWidget(action("Làm mới", self.reload))
        body.addLayout(filters)
        self.summary = QLabel()
        body.addWidget(self.summary)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["Kết thúc (VN)", "Người tạo job", "Khách hàng / Job", "Mã mời", "Kết quả",
                                             "Thành công /\nTổng", "Konpeito\nthực tế", "Mã job", "Log"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(31)
        for col, width in enumerate((160, 170, 145, 85, 135, 120, 125, 180, 60)):
            self.table.setColumnWidth(col, width)
        self.table.cellDoubleClicked.connect(self._open_row_log)
        body.addWidget(self.table, 1)
        self.csv_note = muted("")
        body.addWidget(self.csv_note)
        buttons = QHBoxLayout()
        buttons.addWidget(action("Mở CSV", self.open_csv))
        buttons.addWidget(action("Mở thư mục", self.open_folder))
        buttons.addStretch()
        body.addLayout(buttons)
        layout.addWidget(frame)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda: self.debounce.start(150))
        self.result_filter.currentIndexChanged.connect(lambda: self.refresh())
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh() if self.isVisible() else None)
        self.timer.start(2000)
        self.reload()

    def refresh(self):
        query, result = self.search.text(), self.result_filter.currentData()
        rows = self.store.history(query, result)
        summary = self.store.history_summary(query, result)
        self.summary.setText(f"Hiển thị {len(rows)}/{summary['total']} job · Hoàn thành: {summary['completed']} · Konpeito thực tế: {summary['konpeito']:,}")
        signature = [tuple(row.items()) for row in rows]
        if signature == self._signature:
            return
        self._signature = signature
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            values = (vn_time(row["completed_at"] or row["updated_at"]), creator_text(row), row["name"], row["code"],
                      status_text(row["status"], row["error"]), f"{row['success_count']} / {row['target_count']}",
                      f"{row['achieved']:,}", row["backend_id"] or row["id"])
            for col, text in enumerate(values):
                entry = QTableWidgetItem(text)
                entry.setData(Qt.ItemDataRole.UserRole, row["id"])
                entry.setToolTip(row["error"] or row["external_key"])
                if col == 4:
                    entry.setForeground(QColor("#25dc96" if row["status"] == "completed" else "#ffb84d" if row["status"] == "cancelled" else "#ff6481"))
                self.table.setItem(index, col, entry)
            button = QPushButton("Log")
            button.clicked.connect(lambda checked=False, rid=row["id"]: self.log_requested.emit(rid))
            self.table.setCellWidget(index, 8, button)

    def _open_row_log(self, row, col):
        entry = self.table.item(row, 0)
        if entry:
            self.log_requested.emit(entry.data(Qt.ItemDataRole.UserRole))

    def reload(self):
        self.refresh()
        self.save_csv()

    def save_csv(self):
        try:
            export_history(self.store, self.csv_path)
            self.csv_note.setText(f"CSV: {self.csv_path}")
            return True
        except OSError:
            self.csv_note.setText("Lịch sử đã lưu trong tool. Chưa cập nhật được CSV; đóng file CSV đang mở rồi bấm Làm mới để thử lại.")
            return False

    def open_csv(self):
        if self.save_csv():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.csv_path)))

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.csv_path.parent)))

    def shutdown(self):
        self.timer.stop()
        self.debounce.stop()


class JobLogPage(QWidget):
    def __init__(self, store):
        super().__init__()
        self.store = store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        frame, body = card()
        header = QHBoxLayout()
        heading = muted("NHẬT KÝ THỰC THI (REAL-TIME LOGS)")
        heading.setWordWrap(False)
        header.addWidget(heading)
        self.filter = QComboBox()
        self.filter.setMinimumWidth(230)
        self.filter.addItem("Tất cả", None)
        self.filter.currentIndexChanged.connect(self.load)
        header.addWidget(self.filter, 1)
        header.addWidget(action("Xóa màn hình", self.clear_screen))
        body.addLayout(header)
        self.box = QTextEdit()
        self.box.setObjectName("logBox")
        self.box.setReadOnly(True)
        self.box.document().setMaximumBlockCount(2000)
        body.addWidget(self.box, 1)
        body.addWidget(muted("Hiển thị 2.000 dòng gần nhất; log gốc được lưu cùng lịch sử. Nhấp Log để xem riêng; xóa màn hình không xóa dữ liệu đã lưu."))
        layout.addWidget(frame)
        self.load()

    def ensure_job(self, record_id):
        if not record_id:
            return
        if self.filter.findData(record_id) >= 0:
            return
        row = self.store.job(record_id)
        if row:
            self.filter.addItem(f"{row['code']} — {row['name']}", record_id)

    def select_job(self, record_id):
        self.ensure_job(record_id)
        self.filter.setCurrentIndex(max(0, self.filter.findData(record_id)))
        self.load()

    def clear_screen(self):
        self.box.clear()

    def load(self):
        self.box.setUpdatesEnabled(False)
        self.box.clear()
        for row in self.store.logs(self.filter.currentData()):
            self.append(row)
        self.box.setUpdatesEnabled(True)

    def append(self, row):
        selected = self.filter.currentData()
        if selected and row["record_id"] != selected:
            return
        scrollbar = self.box.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 2
        color = {"error": "#ff6481", "warning": "#ffc54d", "success": "#25dc96"}.get(row["level"], "#58a6ff")
        cursor = QTextCursor(self.box.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.box.document().isEmpty():
            cursor.insertBlock()
        style = QTextCharFormat()
        style.setForeground(QColor(color))
        cursor.insertText(f"[{vn_time(row['created_at'], short=True)}] {row['message']}", style)
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())


class JobsPage(QWidget):
    start_requested = pyqtSignal(bool)
    pause_requested = pyqtSignal()
    remove_requested = pyqtSignal(int)
    settings_requested = pyqtSignal()

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.setStyleSheet("QComboBox,QDateTimeEdit {background:#202123;border:1px solid #3a3a42;border-radius:5px;padding:8px;color:#e6edf7;}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 24, 20)
        layout.setSpacing(12)
        section = QLabel("Nhiệm vụ")
        section.setStyleSheet("color:#58a6ff;background:#1c2b46;border:1px solid #365582;border-radius:6px;padding:10px 24px;")
        row = QHBoxLayout()
        row.addWidget(section)
        row.addStretch()
        layout.addLayout(row)
        title = QLabel("Jobs")
        title.setProperty("class", "pageTitle")
        layout.addWidget(title)
        layout.addWidget(muted("Tạo job nạp konpeito cho khách hàng và theo dõi tiến độ thực hiện."))
        self.tabs = QTabBar()
        self.tabs.setObjectName("jobsNavigation")
        self.tabs.setExpanding(True)
        self.tabs.setDrawBase(False)
        for title in ("Thiết lập job", "Job đang chạy", "Log job", "Lịch sử job"):
            self.tabs.addTab(title)
        self.tabs.setStyleSheet("QTabBar::tab {background:#121b2a;color:#adc5e8;padding:12px;border:1px solid transparent;border-radius:6px;} QTabBar::tab:selected {background:#1c2b46;color:#58a6ff;border-color:#365582;}")
        layout.addWidget(self.tabs)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self._build_setup()
        self._build_running()
        self.logs = JobLogPage(store)
        self.history = JobHistoryPage(store)
        self.history.log_requested.connect(self.show_log)
        self.stack.addWidget(self.logs)
        self.stack.addWidget(self.history)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.setCurrentIndex(0)

    def _build_setup(self):
        frame, body = card("Thiết lập job")
        types = QHBoxLayout()
        invite, invite_layout = card("Nhập mã mời để nạp konpeito")
        invite.setStyleSheet("QFrame {background:#22314a;border:1px solid #4384ff;border-radius:8px;} QLabel {border:none;background:transparent;}")
        invite_layout.addWidget(muted("Dùng mã mời khách để nạp konpeito từ tài khoản mới."))
        types.addWidget(invite, 1)
        stock, stock_layout = card("Tạo account có sẵn konpeito")
        stock_layout.addWidget(muted("Chưa hỗ trợ — hiện dùng chức năng nhập mã mời."))
        stock.setEnabled(False)
        types.addWidget(stock, 1)
        body.addLayout(types)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        self.edit_name = QLineEdit()
        self.edit_name.setPlaceholderText("Tùy chọn (để nhận diện job)")
        self.edit_code = QTextEdit()
        self.edit_code.setAcceptRichText(False)
        self.edit_code.setPlaceholderText("VD: npxMgy. Nhiều mã: mỗi dòng một mã.")
        self.edit_code.setFixedHeight(75)
        self.edit_code.setStyleSheet("QTextEdit {background:#202123;border:1px solid #3a3a42;border-radius:6px;padding:8px;}")
        grid.addWidget(QLabel("Tên khách / Ghi chú:"), 0, 0)
        grid.addWidget(QLabel("Mã mời (*) — mỗi dòng một mã:"), 0, 1)
        grid.addWidget(self.edit_name, 1, 0, alignment=Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.edit_code, 1, 1)
        self.edit_points = QLineEdit("2000")
        self.account_hint = QLabel("= 10 accounts")
        self.account_hint.setStyleSheet("color:#58a6ff;")
        points_row = QHBoxLayout()
        points_row.addWidget(self.edit_points, 1)
        points_row.addWidget(self.account_hint)
        self.edit_points.textChanged.connect(self._update_accounts)
        self.edit_money = QLineEdit("0")
        grid.addWidget(QLabel("Konpeito mục tiêu / mã (*):"), 2, 0)
        grid.addWidget(QLabel("Tổng tiền / mã (VND):"), 2, 1)
        grid.addLayout(points_row, 3, 0)
        grid.addWidget(self.edit_money, 3, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        body.addLayout(grid)
        schedule = QHBoxLayout()
        self.schedule_check = QCheckBox("Hẹn giờ chạy")
        self.schedule_date = QDateTimeEdit()
        self.schedule_date.setCalendarPopup(True)
        self.schedule_date.setDisplayFormat("dd/MM/yyyy HH:mm")
        self._set_schedule_date(datetime.now(VN_TIME) + timedelta(minutes=5))
        self.schedule_date.setEnabled(False)
        self.schedule_check.toggled.connect(self.schedule_date.setEnabled)
        schedule.addWidget(self.schedule_check)
        schedule.addWidget(self.schedule_date)
        schedule.addStretch()
        body.addLayout(schedule)
        body.addWidget(muted("Giờ Việt Nam · Lịch chạy một lần, được giữ khi đóng/mở lại. Đến giờ mới nhận lượt; nếu có job đang chạy sẽ chờ."))
        body.addWidget(muted("Thao tác trên giao diện bản local ở nền, không chiếm chuột/bàn phím. Nhiều mã sẽ được xử lý lần lượt."))
        body.addWidget(action("Cấu hình job và kết nối", self.settings_requested.emit), alignment=Qt.AlignmentFlag.AlignLeft)
        self.form_message = muted("")
        body.addWidget(self.form_message)
        body.addStretch()
        footer = QHBoxLayout()
        footer.addWidget(action("Lưu nháp", self.save_draft))
        footer.addStretch()
        self.setup_pause = action("Tạm dừng hàng chờ", self.pause_requested.emit)
        self.setup_pause.setToolTip("Tạm dừng nhận lượt tiếp theo. Job đang chạy vẫn tiếp tục.")
        footer.addWidget(self.setup_pause)
        self.btn_api_run = action("Thêm qua backend", lambda: self.start_requested.emit(False))
        footer.addWidget(self.btn_api_run)
        self.btn_ui_run = action("Tạo, chạy job", lambda: self.start_requested.emit(True), True)
        footer.addWidget(self.btn_ui_run)
        body.addLayout(footer)
        self.stack.addWidget(frame)

    def _build_running(self):
        frame, body = card()
        body.addWidget(muted("DANH SÁCH NHIỆM VỤ TRONG PHIÊN"))
        controls = QHBoxLayout()
        self.lbl_queue = muted("Đang xử lý: 0 job · Đang chờ: 0 job · Hẹn giờ: 0 job")
        controls.addWidget(self.lbl_queue, 1)
        self.btn_pause_queue = action("Tạm dừng hàng chờ", self.pause_requested.emit)
        controls.addWidget(self.btn_pause_queue)
        body.addLayout(controls)
        stats = QHBoxLayout()
        self.lbl_status = QLabel("Chưa chạy")
        self.lbl_acc_count = QLabel("0 / 0 account")
        self.lbl_points = QLabel("+0 konpeito")
        self.lbl_pct = QLabel("0%")
        for widget in (self.lbl_status, self.lbl_acc_count, self.lbl_points, self.lbl_pct):
            stats.addWidget(widget)
        self.prog_bar = QProgressBar()
        self.prog_bar.setRange(0, 100)
        stats.addWidget(self.prog_bar, 1)
        body.addLayout(stats)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Khách hàng / Job", "Người tạo job", "Mã mời", "Tiến độ", "Konpeito", "Trạng thái", "Thời gian", "Thao tác"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(34)
        for col, width in enumerate((140, 170, 80, 80, 115, 125, 80, 115)):
            self.table.setColumnWidth(col, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        body.addWidget(self.table, 1)
        body.addWidget(muted("Xóa mã chờ sẽ hủy trước khi chạy. Xóa job đã kết thúc chỉ ẩn khỏi phiên; lịch sử và log vẫn được giữ."))
        self.stack.addWidget(frame)

    def _tab_changed(self, index):
        self.stack.setCurrentIndex(index)
        if index == 3:
            self.history.refresh()

    def show_log(self, record_id):
        self.logs.select_job(record_id)
        self.tabs.setCurrentIndex(2)

    def _update_accounts(self):
        try:
            self.account_hint.setText(f"= {max(1, int(self.edit_points.text()) // 200)} accounts")
        except ValueError:
            self.account_hint.setText("= — accounts")

    def _set_schedule_date(self, value):
        value = value.astimezone(VN_TIME)
        self.schedule_date.setDateTime(QDateTime(QDate(value.year, value.month, value.day), QTime(value.hour, value.minute)))

    def schedule_value(self):
        if not self.schedule_check.isChecked():
            return None
        date, time = self.schedule_date.date(), self.schedule_date.time()
        chosen = datetime(date.year(), date.month(), date.day(), time.hour(), time.minute(), tzinfo=VN_TIME)
        if chosen <= datetime.now(VN_TIME):
            raise ValueError("Giờ hẹn phải nằm trong tương lai (giờ Việt Nam).")
        return chosen.astimezone(timezone.utc).isoformat()

    def save_draft(self):
        self.store.set_config(job_draft={"codes": self.edit_code.toPlainText(), "name": self.edit_name.text(),
            "points": self.edit_points.text(), "money": self.edit_money.text(), "scheduled": self.schedule_check.isChecked(),
            "date": self.schedule_date.dateTime().toString("yyyy-MM-dd HH:mm")})
        self.form_message.setText("Đã lưu nháp. Nháp được khôi phục khi mở lại tool.")

    def restore_draft(self):
        draft = self.store.config("job_draft")
        if not isinstance(draft, dict):
            return
        self.edit_code.setPlainText(draft.get("codes", ""))
        for field in ("name", "points", "money"):
            getattr(self, "edit_" + field).setText(draft.get(field, ""))
        self.schedule_check.setChecked(bool(draft.get("scheduled")))
        value = QDateTime.fromString(draft.get("date", ""), "yyyy-MM-dd HH:mm")
        if value.isValid():
            self.schedule_date.setDateTime(value)
        self.form_message.setText("Đã khôi phục nháp đã lưu.")

    def render_queue(self, items, active, paused, progress=None):
        active_count = sum(item.status == "running" for item in items)
        scheduled = sum(item.status == "queued" and not item.ready() for item in items)
        waiting = sum(item.status == "queued" and item.ready() for item in items)
        self.lbl_queue.setText(f"Đang xử lý: {active_count} job · Đang chờ: {waiting} job · Hẹn giờ: {scheduled} job" + (" · ĐÃ TẠM DỪNG" if paused else ""))
        for button in (self.btn_pause_queue, self.setup_pause):
            button.setText("Tiếp tục hàng chờ" if paused else "Tạm dừng hàng chờ")
        self.table.setRowCount(len(items))
        for row_index, item in enumerate(items):
            row = self.store.job(item.record_id) or {}
            self.logs.ensure_job(item.record_id)
            current, total = row.get("success_count", 0), row.get("target_count", max(1, item.target_points // 200))
            points = row.get("achieved", 0)
            if active is item and item.status == "running" and progress:
                current, total, points = progress[:3]
            status = ("Chờ theo dõi job cũ" if item.status == "queued" and item.resume_job_id else
                      "Hẹn giờ" if item.status == "queued" and not item.ready() else status_text(item.status, row.get("error", "")))
            created = creator_text(row) if row else "Local (máy tính)"
            values = (item.job_name, created, item.invitation_code, f"{current} / {total}", f"{points:,} / {item.target_points:,}",
                      status, vn_time(item.scheduled_at if status == "Hẹn giờ" else row.get("started_at") or row.get("created_at"), short=True))
            for col, text in enumerate(values):
                entry = QTableWidgetItem(text)
                entry.setData(Qt.ItemDataRole.UserRole, item.number)
                entry.setToolTip(row.get("error", "") or item.progress or item.job_name)
                if col == 5:
                    entry.setForeground(QColor("#25dc96" if item.status == "completed" else "#ff6481" if item.status in ("failed", "error", "invalid_code", "invite_limit_reached") else "#adc5e8"))
                self.table.setItem(row_index, col, entry)
            actions = QWidget()
            buttons = QHBoxLayout(actions)
            buttons.setContentsMargins(2, 1, 2, 1)
            buttons.setSpacing(4)
            remove = QPushButton("Xóa")
            remove.setFixedSize(44, 26)
            remove.setStyleSheet("QPushButton {background:#30343b;border:1px solid #414956;border-radius:4px;padding:2px;}")
            remove.setEnabled(item.status != "running" and not item.resume_job_id)
            remove.clicked.connect(lambda checked=False, number=item.number: self.remove_requested.emit(number))
            buttons.addWidget(remove)
            log = QPushButton("Log")
            log.setFixedSize(44, 26)
            log.setStyleSheet("QPushButton {background:#008cc4;color:white;border:none;border-radius:4px;padding:2px;}")
            log.clicked.connect(lambda checked=False, rid=item.record_id: self.show_log(rid))
            buttons.addWidget(log)
            self.table.setCellWidget(row_index, 7, actions)

    def update_progress(self, number, current, total, points, target, status):
        for row in range(self.table.rowCount()):
            entry = self.table.item(row, 0)
            if entry and entry.data(Qt.ItemDataRole.UserRole) == number:
                self.table.item(row, 3).setText(f"{current} / {total}")
                self.table.item(row, 4).setText(f"{points:,} / {target:,}")
                self.table.item(row, 5).setText(status_text(status))
                break

    def shutdown(self):
        self.history.shutdown()
