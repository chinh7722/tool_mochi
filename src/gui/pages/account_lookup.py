"""Google sign-in and read-only collection of a Mochi account's own profile."""
import asyncio
from datetime import datetime
from pathlib import Path
import uuid

try:
    from PySide6.QtCore import Qt,Signal
    from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QFrame,QLabel,QLineEdit,
        QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QFileDialog,QApplication)
except ImportError:
    from PyQt6.QtCore import Qt,pyqtSignal as Signal
    from PyQt6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QFrame,QLabel,QLineEdit,
        QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QFileDialog,QApplication)

from core.job_schedule import VIETNAM_TIME
from core.platforms.mochichat.account_lookup import (AccountProfile,AccountProfileStore,AccountLookupError,
    lookup_account,export_profiles_csv)
from core.proxy.protocol import ProxyReleaseOutcome
from gui.background import AsyncTaskThread
from gui.branding import WORKSPACE_STYLE


class AccountLookupPage(QWidget):
    progress_changed=Signal(str)
    cleanup_failed=Signal(str)

    def __init__(self,database_path,route_provider,parent=None):
        super().__init__(parent)
        self.setStyleSheet(WORKSPACE_STYLE)
        self.route_provider=route_provider
        self.database_path=Path(database_path)
        self.store=None
        self._worker=None
        self._closing=False
        self._received_result=False
        self._profiles=()
        self._pending_profiles={}
        self._cleanup_warning=''
        self._build_ui()
        self.progress_changed.connect(self._on_progress)
        self.cleanup_failed.connect(self._remember_cleanup_warning)
        try:
            self.store=AccountProfileStore(self.database_path)
            self._refresh_profiles()
        except Exception:
            self._status('Chưa mở được dữ liệu tài khoản. Vẫn có thể tra cứu và sao chép kết quả trong phiên này.',error=True)

    def _build_ui(self):
        root=QVBoxLayout(self)
        root.setContentsMargins(0,8,0,0)
        root.setSpacing(12)
        title=QLabel('Lấy mã mời tài khoản',self)
        title.setStyleSheet('font-size: 25px; font-weight: 700; color: #f8fafc;')
        root.addWidget(title)
        hint=QLabel('Đăng nhập Google để lấy mã mời Mochi, tên hiển thị và số dư beans hiện tại.',self)
        hint.setWordWrap(True)
        hint.setStyleSheet('color: #9aa8c4;')
        root.addWidget(hint)
        card=QFrame(self)
        card.setObjectName('lookupCard')
        card.setStyleSheet('QFrame#lookupCard {background: #232326; border: 1px solid #323238; border-radius: 10px;}')
        box=QVBoxLayout(card)
        box.setContentsMargins(18,16,18,16)
        fields=QGridLayout()
        self.input_email=QLineEdit(card)
        self.input_email.setMaxLength(320)
        self.input_email.setPlaceholderText('Email tài khoản Google')
        self.input_password=QLineEdit(card)
        self.input_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_password.setPlaceholderText('Mật khẩu Google — chỉ dùng cho lần tra cứu này')
        for column,(caption,entry) in enumerate((('Email Google',self.input_email),('Mật khẩu Google',self.input_password))):
            fields.addWidget(QLabel(caption,card),0,column)
            fields.addWidget(entry,1,column)
            fields.setColumnStretch(column,1)
        box.addLayout(fields)
        note=QLabel('Google, API Mochi và chế độ trình duyệt dùng cấu hình hiện tại trong Cài đặt chung. '
            'Kết quả được lưu để xem lại; mật khẩu và token không được lưu.',card)
        note.setWordWrap(True)
        note.setStyleSheet('color: #9aa8c4; font-size: 11px;')
        box.addWidget(note)
        actions=QHBoxLayout()
        self.btn_settings=QPushButton('Cài đặt đường mạng',card)
        self.btn_settings.clicked.connect(self._open_settings)
        actions.addWidget(self.btn_settings)
        actions.addStretch()
        self.btn_cancel=QPushButton('Hủy tra cứu',card)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_lookup)
        actions.addWidget(self.btn_cancel)
        self.btn_lookup=QPushButton('Đăng nhập & lấy mã mời',card)
        self.btn_lookup.setProperty('primary',True)
        self.btn_lookup.clicked.connect(self._start_lookup)
        self.input_password.returnPressed.connect(self._start_lookup)
        actions.addWidget(self.btn_lookup)
        box.addLayout(actions)
        self.lbl_status=QLabel('Nhập email và mật khẩu Google để bắt đầu.',card)
        self.lbl_status.setTextFormat(Qt.TextFormat.PlainText)
        self.lbl_status.setWordWrap(True)
        box.addWidget(self.lbl_status)
        root.addWidget(card)
        results=QFrame(self)
        results.setObjectName('lookupResults')
        results.setStyleSheet('QFrame#lookupResults {background: #232326; border: 1px solid #323238; border-radius: 10px;}')
        result_box=QVBoxLayout(results)
        result_box.setContentsMargins(14,12,14,12)
        result_actions=QHBoxLayout()
        result_actions.addWidget(QLabel('Tài khoản đã thu thập',results))
        result_actions.addStretch()
        self.btn_copy=QPushButton('Sao chép mã mời',results)
        self.btn_copy.clicked.connect(self._copy_invite)
        self.btn_export=QPushButton('Xuất CSV',results)
        self.btn_export.clicked.connect(self._export_csv)
        self.btn_refresh=QPushButton('Làm mới danh sách',results)
        self.btn_refresh.clicked.connect(self._refresh_profiles)
        for button in (self.btn_copy,self.btn_export,self.btn_refresh):result_actions.addWidget(button)
        result_box.addLayout(result_actions)
        self.table=QTableWidget(0,6,results)
        self.table.setStyleSheet(
            'QTableWidget {background: #1d1d20; color: #edf2fc; gridline-color: #36363d; '
            'border: 1px solid #36363d; border-radius: 6px;}'
            'QHeaderView::section {background: #29292f; color: #afbfda; border: 0; padding: 9px 6px; font-weight: 600;}'
            'QTableWidget::item:selected {background: #253c60; color: #f8fafc;}'
            'QTableCornerButton::section {background: #29292f; border: 0;}')
        self.table.setHorizontalHeaderLabels(['Email','Tên hiển thị','Mã mời','Beans','ID Mochi','Cập nhật (VN)'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setMinimumSectionSize(90)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0,250)
        self.table.itemSelectionChanged.connect(self._update_controls)
        result_box.addWidget(self.table,1)
        self.lbl_count=QLabel('0 tài khoản',results)
        self.lbl_count.setStyleSheet('color: #9aa8c4; font-size: 11px;')
        result_box.addWidget(self.lbl_count)
        root.addWidget(results,1)
        self._update_controls()

    def _status(self,message,*,error=False):
        self.lbl_status.setText(message)
        self.lbl_status.setStyleSheet('color: #f87171;' if error else 'color: #60a5fa;')

    def _open_settings(self):
        window=self.window()
        if hasattr(window,'switch_to_settings_tab'):window.switch_to_settings_tab()

    def _update_controls(self):
        busy=self._worker is not None
        self.btn_lookup.setEnabled(not busy and not self._closing)
        self.btn_cancel.setEnabled(busy and not self._closing)
        self.input_email.setEnabled(not busy and not self._closing)
        self.input_password.setEnabled(not busy and not self._closing)
        self.btn_copy.setEnabled(0<=self.table.currentRow()<len(self._profiles))
        self.btn_export.setEnabled(bool(self._profiles))

    def _start_lookup(self):
        if self._worker is not None or self._closing:return
        email=self.input_email.text().strip()
        password=self.input_password.text()
        if not email or '@' not in email or any(char.isspace() for char in email) or not password:
            self._status('Nhập đúng email và mật khẩu tài khoản Google.',error=True)
            return
        owner_id='account_lookup_'+uuid.uuid4().hex
        try:
            options=dict(self.route_provider(owner_id))
        except Exception:
            self._status('Chưa cấp được đường mạng. Kiểm tra Proxy Pool và cấu hình Google/API Mochi trong Cài đặt chung.',error=True)
            return
        client=options.pop('proxy_client',None)
        self._received_result=False
        self._cleanup_warning=''
        self._status('Đang chuẩn bị đăng nhập Google…')

        async def execute():
            lease=None
            outcome=ProxyReleaseOutcome.SUCCESS
            try:
                if client is not None:
                    self.progress_changed.emit('Đang chờ cấp proxy từ Proxy Pool…')
                    try:
                        lease=await client.lease(owner_id)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        raise AccountLookupError('Không cấp được proxy cho lần tra cứu. Kiểm tra Proxy Pool.') from None
                return await lookup_account(email,password,owner_id=owner_id,
                    proxy=lease.proxy if lease else None,on_progress=self.progress_changed.emit,**options)
            except asyncio.CancelledError:
                outcome=ProxyReleaseOutcome.ABORTED
                raise
            except AccountLookupError as exc:
                outcome=(ProxyReleaseOutcome.NETWORK_ERROR if getattr(exc,'transport_error',False)
                    else ProxyReleaseOutcome.BUSINESS_FAILURE)
                raise
            except Exception:
                outcome=ProxyReleaseOutcome.BUSINESS_FAILURE
                raise AccountLookupError('Không hoàn tất được lần tra cứu. Hãy kiểm tra đường mạng hoặc đăng nhập lại.') from None
            finally:
                if lease is not None:
                    try:
                        await client.release(lease,outcome=outcome,success=outcome==ProxyReleaseOutcome.SUCCESS)
                    except Exception:
                        try:
                            await client.release_job()
                        except Exception:
                            self.cleanup_failed.emit('Chưa xác nhận trả được proxy; kiểm tra Proxy Pool trước khi chạy tiếp.')
        worker=AsyncTaskThread(execute,self)
        self._worker=worker
        worker.result.connect(self._on_result)
        worker.failed.connect(self._on_error)
        worker.finished.connect(self._worker_stopped)
        self._update_controls()
        try:
            worker.start()
            self.input_password.clear()
        except Exception:
            worker.factory=None
            self._worker=None
            worker.deleteLater()
            self._received_result=True
            self._status('Không khởi chạy được tác vụ tra cứu. Hãy thử lại.',error=True)
            self._update_controls()

    def _on_progress(self,message):
        if self._worker is not None:self._status(message)

    def _remember_cleanup_warning(self,message):
        self._cleanup_warning=message

    def _on_result(self,profile):
        self._received_result=True
        if not isinstance(profile,AccountProfile):
            self._status('API trả về kết quả không đúng cấu trúc tài khoản.',error=True)
            return
        self._profiles=tuple([profile]+[item for item in self._profiles if item.email.casefold()!=profile.email.casefold()])
        self._pending_profiles[profile.email.casefold()]=profile
        saved=False
        try:
            if self.store is not None:
                self.store.upsert(profile)
                saved=True
                self._pending_profiles.pop(profile.email.casefold(),None)
        except Exception:
            pass
        self._render_profiles()
        self.table.selectRow(0)
        message=('Đã lấy mã mời và cập nhật thông tin tài khoản.' if saved
            else 'Đã lấy thông tin nhưng chưa lưu được vào dữ liệu. Hãy sao chép mã hoặc xuất CSV trước khi đóng tool.')
        if self._cleanup_warning:message+='\n'+self._cleanup_warning
        self._status(message,error=not saved or bool(self._cleanup_warning))

    def _on_error(self,message):
        self._received_result=True
        self._status(message+('\n'+self._cleanup_warning if self._cleanup_warning else ''),error=True)

    def _worker_stopped(self):
        worker,self._worker=self._worker,None
        if worker is not None:
            worker.factory=None
            worker.deleteLater()
        if not self._received_result:
            self._status('Đã hủy tra cứu. Chưa cập nhật thông tin tài khoản.'
                + ('\n'+self._cleanup_warning if self._cleanup_warning else ''),error=bool(self._cleanup_warning))
        self._update_controls()

    def _cancel_lookup(self):
        if self._worker is not None:
            self._worker.request_cancel()
            self.btn_cancel.setEnabled(False)
            self._status('Đang hủy và đóng phiên đăng nhập…')

    def _refresh_profiles(self):
        try:
            if self.store is None:self.store=AccountProfileStore(self.database_path)
            for key,profile in list(self._pending_profiles.items()):
                try:
                    self.store.upsert(profile)
                    self._pending_profiles.pop(key,None)
                except Exception:
                    pass
            saved={item.email.casefold():item for item in self.store.list_profiles()}
            saved.update(self._pending_profiles)
            self._profiles=tuple(sorted(saved.values(),key=lambda item:item.updated_at,reverse=True))
            self._render_profiles()
            if self._pending_profiles:
                self._status('Có kết quả chưa lưu được; kết quả vẫn hiển thị để sao chép hoặc xuất CSV.',error=True)
        except Exception:
            self._status('Không đọc được danh sách tài khoản đã lưu; kết quả đang hiển thị được giữ.',error=True)

    def _render_profiles(self):
        self.table.setRowCount(len(self._profiles))
        for row,profile in enumerate(self._profiles):
            try:
                updated=datetime.fromisoformat(profile.updated_at).astimezone(VIETNAM_TIME).strftime('%d/%m/%Y %H:%M')
            except (ValueError,TypeError):updated=profile.updated_at
            values=(profile.email,profile.display_name,profile.invite_code,profile.beans,profile.user_id,updated)
            for column,value in enumerate(values):
                item=QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                self.table.setItem(row,column,item)
        self.lbl_count.setText(f'{len(self._profiles)} tài khoản · Chỉ lưu kết quả tra cứu, không lưu mật khẩu/token.')
        self._update_controls()

    def _copy_invite(self):
        row=self.table.currentRow()
        if 0<=row<len(self._profiles):
            QApplication.clipboard().setText(self._profiles[row].invite_code)
            self._status('Đã sao chép mã mời.')

    def _export_csv(self):
        if not self._profiles:return
        path,_=QFileDialog.getSaveFileName(self,'Xuất thông tin tài khoản',str(self.database_path.parent/'mochi_accounts.csv'),
            'CSV (*.csv);;Tất cả tệp (*)')
        if not path:return
        try:
            export_profiles_csv(path,self._profiles)
            self._status('Đã xuất danh sách tài khoản ra CSV.')
        except Exception:
            self._status('Không xuất được CSV. Kiểm tra quyền ghi hoặc đóng tệp đang mở rồi thử lại.',error=True)

    def shutdown_workers(self):
        self._closing=True
        if self._worker is not None:
            self._worker.request_cancel()
            self._update_controls()
            return self._worker.isRunning()
        return False
