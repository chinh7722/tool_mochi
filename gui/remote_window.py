"""Windows management workspace. Closing it never stops the VPS runtime."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import csv
import json
from pathlib import Path
import re

from PySide6.QtCore import QDateTime, QSettings, Qt, QThread, QTimer, Signal, QSignalBlocker
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QDateTimeEdit, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox, QStackedWidget,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QMessageBox)
from gui.branding import BrandSidebar, WORKSPACE_STYLE, icon
from core.remote.client import RemoteClient
from core.remote.protocol import RemoteError

TERMINAL_JOB_STATUSES={'Hoàn thành','Thất bại','Đã hủy','Mã không hợp lệ','Mã mời lỗi',
    'Đạt giới hạn 24h','completed','failed','cancelled','invalid_code','invite_limit_reached'}


def ordered_jobs(records):
    """Keep current work visible and show the newest jobs above older results."""
    def key(pair):
        index,record=pair
        try:
            stamp=datetime.fromisoformat(str(record.get('created_at',''))).timestamp()
        except (ValueError,TypeError,OverflowError):
            stamp=0
        return (record.get('status') not in TERMINAL_JOB_STATUSES,stamp,index)
    return [record for _,record in sorted(enumerate(records),key=key,reverse=True)]


class RequestWorker(QThread):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback

    def run(self):
        try:
            self.result.emit(self.callback())
        except RemoteError as exc:
            self.error.emit(str(exc))
        except Exception:
            self.error.emit('Không đọc được phản hồi từ VPS.')
        finally:
            self.callback = None


def table(headers):
    result = QTableWidget(0,len(headers))
    result.setHorizontalHeaderLabels(headers)
    result.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    result.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    result.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    result.horizontalHeader().setStretchLastSection(True)
    result.verticalHeader().hide()
    result.verticalHeader().setDefaultSectionSize(44)
    result._rows = []
    return result


def fill(widget, rows, keys, *, identity_key=None):
    if identity_key is None:
        sample=rows[0] if rows else (widget._rows[0] if widget._rows else {})
        identity_key=next((key for key in ('job_id','chat_id','transaction_id','id','email') if key in sample),keys[0] if keys else '')
    selected = widget.currentRow()
    prior = widget._rows[selected].get(identity_key) if 0<=selected<len(widget._rows) else None
    blocker=QSignalBlocker(widget)
    widget.clearSelection()
    widget.setCurrentCell(-1,-1)
    widget.setRowCount(len(rows))
    widget._rows = rows
    for row,values in enumerate(rows):
        for column,key in enumerate(keys):
            value = values.get(key,'')
            item = QTableWidgetItem('' if value is None else str(value))
            item.setToolTip(item.text())
            widget.setItem(row,column,item)
        if prior is not None and values.get(identity_key)==prior:
            widget.selectRow(row)
    del blocker


class RemoteMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        from gui.remote_shell import initialize
        initialize(self)

    def activate(self,index):
        self.pages.setCurrentIndex(index)
        for number,button in enumerate(self.nav):
            button.setChecked(number==index)
        if index==6 and hasattr(self,'statistics_panel'):
            self.statistics_panel.load()

    def page(self,caption):
        widget=QWidget()
        box=QVBoxLayout(widget)
        box.setContentsMargins(28,24,28,20)
        box.setSpacing(14)
        return widget,box

    def button(self,caption,callback,layout,*,mutation=True):
        button = QPushButton(caption)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(callback)
        layout.addWidget(button)
        if mutation:
            self._mutation_buttons.append(button)
        return button

    def selected(self,widget,key):
        row=widget.currentRow()
        if not 0<=row<len(widget._rows) or widget.isRowHidden(row):
            self.status.setText('Chọn một dòng trước khi thao tác.')
            return None
        return widget._rows[row].get(key)

    def enable_actions(self,enabled):
        active=enabled and not self._mutating and not self._closing and not self.snapshot.get('closing',False)
        for button in self._mutation_buttons:
            button.setEnabled(active)
        for view in self.proxy_views.values():
            view.set_actions_enabled(active)
        for button,allowed in self._job_row_buttons:
            button.setEnabled(active and allowed)
        if hasattr(self,'telegram_presentation'):
            self.telegram_presentation.update_broadcast_controls()

    def task(self,callback,on_result):
        worker = RequestWorker(callback,self)
        self._workers.add(worker)
        worker.result.connect(on_result)
        worker.error.connect(self.failure)
        def done():
            self._workers.discard(worker)
            worker.deleteLater()
            if self._closing and not self._workers:
                self.close()
        worker.finished.connect(done)
        worker.start()
        return worker

    def failure(self,message):
        self.status.setText(message)

    def connect_server(self):
        if self._workers:
            self.status.setText('Chờ yêu cầu hiện tại kết thúc trước khi đổi kết nối.')
            return
        try:
            self.client = RemoteClient(self.url.text().strip(),self.token.text().strip())
        except RemoteError as exc:
            self.failure(str(exc))
            return
        self.preferences.setValue('url',self.url.text().strip())
        self._config_loaded = False
        self._connected=False
        self.enable_actions(False)
        self.poll()

    def poll(self):
        if self.client is None or self._polling or self._mutating or self._closing:
            return
        self._polling = True
        worker = self.task(self.client.state,self.render)
        worker.error.connect(self.connection_lost)
        worker.finished.connect(self._poll_finished)

    def _poll_finished(self):
        self._polling=False
        if self._refresh_requested and not self._closing:
            self._refresh_requested=False
            QTimer.singleShot(0,self.refresh_jobs)

    def refresh_jobs(self,*_):
        if self._closing:
            return
        if self.client is None:
            self.status.setText('Kết nối VPS tại Cài đặt chung để đồng bộ danh sách job.')
            self.open_connection_settings()
            return
        if self._polling or self._mutating:
            self._refresh_requested=True
            return
        self._refresh_requested=False
        self.poll()

    def send(self,action,payload=None,callback=None):
        if self.client is None or not self._connected or self._mutating or self._closing or self.snapshot.get('closing',False):
            return
        self._mutating = True
        self.enable_actions(False)
        client = self.client
        def received(response):
            result = response.get('result',{})
            self.status.setText(result.get('message','Đã thực hiện trên VPS.'))
            if callback:
                callback(result)
        worker = self.task(lambda:client.command(action,payload),received)
        worker.error.connect(self.command_unconfirmed)
        def done():
            self._mutating = False
            self.enable_actions(self._connected)
            self.refresh_jobs()
        worker.finished.connect(done)

    def command_unconfirmed(self,message):
        # A failed POST may already have reached the VPS. Recheck state first.
        self._connected=False
        self.enable_actions(False)

    def connection_lost(self,message):
        self._connected=False
        self.enable_actions(False)
        self.btn_connection.setText('○  Mất kết nối VPS')
        self.btn_connection.setStyleSheet('text-align:left;background:transparent;border:none;color:#fbbf24;padding:8px 5px;font-size:11px;')
        self.job_sync_status.setText('Chưa đồng bộ được VPS · Đang giữ kết quả lần cập nhật trước.')

    def build_dashboard(self):
        from gui.remote_workspace import build_dashboard
        return build_dashboard(self)

    def build_mochi(self):
        from gui.remote_workspace import build_mochi
        return build_mochi(self)

    def create_job(self):
        self.save_job_draft(quiet=True)
        payload = dict(code=self.job_code.text().strip(),points=self.job_points.value(),
            customer=self.job_customer.text(),batch=self.job_batch.currentData() or '')
        if self.job_combo.isChecked():
            payload['combo'] = True
        if self.schedule.isChecked():
            payload['scheduled_at'] = self.schedule_time.dateTime().toString('yyyy-MM-ddTHH:mm:ss')+'+07:00'
        self.send('job.create',payload,self.job_created)

    def job_created(self,result):
        job_id=result.get('job_id')
        if not isinstance(job_id,str) or not job_id:
            return
        self._pending_focus_job_id=job_id
        self.activate(1)
        self.workspace_tabs.setCurrentIndex(0)
        self.mochi_tabs.setCurrentIndex(1)
        self.job_sync_status.setText('Đã tạo '+job_id+' · Đang cập nhật từ VPS…')
        self.refresh_jobs()

    def selected_send(self,widget,key,action,extra=None):
        value = self.selected(widget,key)
        if value is not None:
            self.send(action,{key:value,**(extra or {})})

    def load_logs(self):
        jid=self.selected(self.jobs,'job_id')
        if jid:
            self.open_job_logs(jid)

    def stop_selected_job(self):
        jid=self.selected(self.jobs,'job_id')
        if jid:self.confirm_stop_job(jid)

    def confirm_stop_job(self,jid):
        answer=QMessageBox.question(self,'Xác nhận dừng job',
            f'Bạn có muốn dừng / hủy lịch job {jid}?',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer==QMessageBox.StandardButton.Yes:
            self.send('job.stop',{'job_id':jid})

    def continue_selected_job(self):
        jid=self.selected(self.jobs,'job_id')
        if jid:self.send('job.continue',{'job_id':jid},self.job_created)

    def upload_batch(self):
        path,_ = QFileDialog.getOpenFileName(self,'Chọn lô mail đã mua','','JSON (*.json)')
        if not path:
            return
        try:
            if Path(path).stat().st_size>1500000:
                raise ValueError()
            data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
            if not isinstance(data,dict) or not isinstance(data.get('accounts'),list):
                raise ValueError()
        except (OSError,ValueError):
            self.status.setText('Không đọc được lô mail hoặc file quá lớn.')
            return
        self.send('batch.upload',{'accounts':data['accounts']},
            lambda r:(self.job_batch.addItem(r['batch'],r['batch']),self.job_batch.setCurrentIndex(self.job_batch.count()-1)))

    def show_batches(self,result):
        chosen = self.job_batch.currentData()
        self.job_batch.clear(); self.job_batch.addItem('Mua mail theo cấu hình VPS','')
        for name in result['batches']:
            self.job_batch.addItem(name,name)
        self.job_batch.setCurrentIndex(max(0,self.job_batch.findData(chosen)))

    def lookup_start(self):
        password = self.lookup_password.text()
        self.lookup_password.clear()
        self.send('lookup.start',{'email':self.lookup_email.text(),'password':password})

    def copy_invite(self):
        code = self.selected(self.profiles,'invite_code')
        if code:
            QApplication.clipboard().setText(code)

    def export_profiles(self):
        rows = self.snapshot.get('lookup',{}).get('profiles',[])
        if not rows:
            return
        path,_ = QFileDialog.getSaveFileName(self,'Xuất tài khoản','mochi_accounts.csv','CSV (*.csv)')
        if not path:
            return
        from core.platforms.mochichat.account_lookup import AccountProfile,export_profiles_csv
        try:
            export_profiles_csv(path,[AccountProfile(**row) for row in rows])
            self.status.setText('Đã xuất CSV.')
        except (OSError,ValueError):
            self.status.setText('Chưa ghi được CSV. Kiểm tra file đang mở.')

    def build_proxy(self,pool):
        from gui.remote_proxy import build_proxy_page
        return build_proxy_page(self,pool)

    def save_text(self,result,name,file_filter,*,bom=False):
        path,_=QFileDialog.getSaveFileName(self,'Lưu dữ liệu từ VPS',name,file_filter)
        if not path:
            return
        from PySide6.QtCore import QSaveFile,QIODevice
        target=QSaveFile(path)
        content=result.get('text','').encode('utf-8-sig' if bom else 'utf-8')
        if not target.open(QIODevice.OpenModeFlag.WriteOnly):
            self.status.setText('Chưa mở được file để lưu.'); return
        if target.write(content)!=len(content) or not target.commit():
            target.cancelWriting(); self.status.setText('Chưa lưu được file.'); return
        self.status.setText('Đã lưu file từ VPS.')

    def import_proxy_file(self,pool,provider):
        path,_ = QFileDialog.getOpenFileName(self,'Nhập proxy','','TXT (*.txt)')
        if path:
            try:
                raw = Path(path).read_bytes()
                if len(raw)>500000:
                    raise ValueError()
                encoding = 'utf-32' if raw.startswith((b'\xff\xfe\x00\x00',b'\x00\x00\xfe\xff')) else (
                    'utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
                text = raw.decode(encoding)
            except (OSError,ValueError):
                self.status.setText('Không đọc được TXT hoặc file quá lớn.'); return
            self.send('proxy.import',dict(pool=pool,provider=provider.currentData(),text=text))

    def build_telegram(self):
        from gui.remote_telegram import build_telegram_page
        return build_telegram_page(self)

    def pick_user(self,row,_):
        if 0<=row<len(self.users._rows):
            user = self.users._rows[row]
            self.user_id.setText(str(user['chat_id'])); self.user_name.setText(user['name'])
            self.user_role.setCurrentText(user['role']); self.user_gmail.clear(); self.user_trieu.clear()

    def numeric(self,field):
        try:
            return int(field.text().strip())
        except ValueError:
            self.status.setText('Nhập Chat ID số nguyên.'); return None

    def numeric_send(self,action,key,field,extra=None):
        value = self.numeric(field)
        if value is not None:
            self.send(action,{key:value,**(extra or {})})

    def save_user(self):
        self.numeric_send('user.save','chat_id',self.user_id,{'name':self.user_name.text(),'role':self.user_role.currentText()})

    def save_user_keys(self):
        selected = self.selected(self.users,'chat_id')
        typed = self.numeric(self.user_id)
        if selected is None or selected!=typed or typed<=0:
            self.status.setText('Chọn đúng người dùng và Chat ID trước khi lưu API.'); return
        self.send('user.mail_keys',dict(chat_id=typed,gmailvip_key=self.user_gmail.text(),one_trieu_key=self.user_trieu.text()),
            lambda _:(self.user_gmail.clear(),self.user_trieu.clear()))

    def export_user_report(self):
        owner = self.selected(self.users,'chat_id')
        if owner is not None:
            self.send('user.report',{'chat_id':owner,'kind':'all'},
                lambda result, expected=owner:self.save_user_report(result,expected_owner=expected))

    def save_user_report(self,result,*,expected_owner=None):
        """Save both CSVs into one chosen directory without overwriting older exports."""
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        try:
            owner=result['owner']
            owner_id=owner['chat_id']
            if type(owner_id) is not int or owner_id==0 or owner_id < -1:
                raise ValueError()
            if expected_owner is not None and owner_id!=expected_owner:
                raise ValueError()
            files=result['files']
            if (not isinstance(owner,dict) or not isinstance(files,list) or len(files)!=2
                    or {item.get('kind') for item in files}!={'jobs','purchases'}):
                raise ValueError()
            names=set()
            content=[]
            for item in files:
                name,text=item.get('filename'),item.get('text')
                if (not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,150}\.csv',name)
                        or name.casefold() in names or not isinstance(text,str)):
                    raise ValueError()
                names.add(name.casefold())
                content.append((name,text.lstrip('\ufeff').encode('utf-8-sig')))
            if sum(len(raw) for _,raw in content)>8*1024*1024:
                raise ValueError()
        except (KeyError,ValueError,TypeError,AttributeError):
            self.status.setText('Báo cáo CSV từ VPS không hợp lệ; chưa lưu file.')
            return
        directory=QFileDialog.getExistingDirectory(self,
            f'Lưu báo cáo CSV — {owner.get("name", "")} ({owner_id})')
        if not directory:
            self.status.setText('Đã hủy lưu báo cáo CSV.')
            return
        from PySide6.QtCore import QSaveFile,QIODevice
        prepared=[]
        saved=[]
        try:
            folder=Path(directory).resolve()
            if not folder.is_dir():
                raise OSError()
            occupied={item.name.casefold() for item in folder.iterdir()}
            for name,raw in content:
                candidate=name
                number=2
                while candidate.casefold() in occupied:
                    candidate=f'{Path(name).stem}_{number}.csv'
                    number+=1
                occupied.add(candidate.casefold())
                target=QSaveFile(str(folder/candidate))
                prepared.append((target,candidate))
                if not target.open(QIODevice.OpenModeFlag.WriteOnly) or target.write(raw)!=len(raw):
                    raise OSError()
            for target,name in prepared:
                if not target.commit():
                    raise OSError()
                saved.append(name)
        except Exception:
            for target,name in prepared:
                if name not in saved:
                    target.cancelWriting()
            if saved:
                self.status.setText(f'Đã lưu {len(saved)}/2 file CSV: '+', '.join(saved)+
                    '. Chưa lưu đủ báo cáo; kiểm tra quyền ghi hoặc file đang mở.')
            else:
                self.status.setText('Chưa lưu được báo cáo CSV. Kiểm tra quyền ghi hoặc file đang mở.')
            return
        self.status.setText(f'Đã lưu 2 báo cáo CSV của {owner.get("name", "")} ({owner_id}) tại {folder}.')

    def save_bot(self):
        self.send('telegram.save',{'token':self.bot_token.text(),'auto_start':self.bot_auto.isChecked()},lambda _:self.bot_token.clear())

    def save_sepay(self):
        self.send('sepay.save',{**{k:f.text() for k,f in self.sepay_fields.items()},'enabled':self.sepay_auto.isChecked()},
                  lambda _:self.sepay_fields['token'].clear())

    def reconcile_sepay(self):
        transaction = self.selected(self.transactions,'transaction_id'); chat_id = self.numeric(self.review_id)
        if transaction and chat_id is not None:
            self.send('sepay.reconcile',{'transaction_id':transaction,'chat_id':chat_id})

    def selected_details(self,widget,key,action):
        value = self.selected(widget,key)
        if value is not None:
            self.send(action,{key:value},self.details)

    def details(self,result):
        dialog = QDialog(self); dialog.setWindowTitle('Chi tiết trên VPS'); dialog.resize(900,550)
        box = QVBoxLayout(dialog); tabs = QTabWidget(); box.addWidget(tabs)
        for name,rows in result.items():
            if isinstance(rows,list):
                keys = list(dict.fromkeys(k for row in rows for k in row)) if rows else []
                listing = table(keys); fill(listing,rows,keys); tabs.addTab(listing,name)
        close = QPushButton('Đóng'); close.clicked.connect(dialog.accept); box.addWidget(close)
        dialog.exec()

    def edit_balance(self):
        row = self.customers.currentRow()
        if not 0<=row<len(self.customers._rows):
            self.status.setText('Chọn khách hàng.'); return
        customer = dict(self.customers._rows[row])
        dialog = QDialog(self); dialog.setWindowTitle('Sửa số dư khả dụng'); box = QFormLayout(dialog)
        amount = QLineEdit(str(Decimal(customer['balance_units'])/100)); reason = QLineEdit()
        box.addRow('Số dư mới (VND)',amount); box.addRow('Lý do',reason)
        save = QPushButton('Lưu số dư'); box.addRow(save)
        def commit():
            self.send('customer.balance',{'chat_id':customer['chat_id'],'amount':amount.text(),
                'reason':reason.text(),'expected_units':customer['balance_units']})
            dialog.accept()
        save.clicked.connect(commit); dialog.exec()

    def build_settings(self):
        from gui.remote_workspace import build_settings
        return build_settings(self)

    def switch_to_settings_tab(self):
        self.activate(5)

    def open_connection_settings(self):
        self.activate(5)
        self.settings_tabs.setCurrentIndex(0)
        self.url.setFocus()

    def open_new_job(self):
        self.activate(1)
        self.workspace_tabs.setCurrentIndex(0)
        self.mochi_tabs.setCurrentIndex(0)

    def open_job_logs(self,job_id):
        if not job_id:
            return
        self._log_job_id=job_id
        self._log_cursor=None;self._log_epoch=None
        self.job_log_filter.setText('Job: '+job_id+' · Tự cập nhật mỗi giây')
        self.logs.clear()
        self.activate(1);self.workspace_tabs.setCurrentIndex(0);self.mochi_tabs.setCurrentIndex(2)
        self.poll_logs()

    def poll_logs(self):
        if (not self.client or not self._connected or self._closing or self._log_polling or
                not self._log_job_id or self.pages.currentIndex()!=1 or self.mochi_tabs.currentIndex()!=2):return
        self._log_polling=True
        jid=self._log_job_id;client=self.client
        payload=dict(job_id=jid,cursor=self._log_cursor,epoch=self._log_epoch)
        def display(response):
            if jid!=self._log_job_id or client is not self.client:return
            result=response['result']
            scroll=self.logs.verticalScrollBar();at_end=scroll.value()>=scroll.maximum()-4
            if result.get('reset'):self.logs.clear()
            lines=result.get('lines',[])
            if lines:self.logs.appendPlainText('\n'.join(lines))
            self._log_cursor=result.get('cursor');self._log_epoch=result.get('epoch')
            if at_end:scroll.setValue(scroll.maximum())
        worker=self.task(lambda:client.command('job.logs',payload),display)
        worker.finished.connect(lambda:setattr(self,'_log_polling',False))

    def save_job_draft(self,*,quiet=False):
        draft=dict(code=self.job_code.text(),customer=self.job_customer.text(),points=self.job_points.value(),
            scheduled=self.schedule.isChecked(),when=self.schedule_time.dateTime().toString('yyyy-MM-ddTHH:mm:ss'),
            combo=self.job_combo.isChecked())
        self.preferences.setValue('job/draft',json.dumps(draft,ensure_ascii=False))
        if not quiet:
            self.status.setText('Đã lưu nháp job trên máy tính.')

    def restore_job_draft(self):
        try:
            draft=json.loads(str(self.preferences.value('job/draft','{}')))
            self.job_code.setText(str(draft.get('code','')))
            self.job_customer.setText(str(draft.get('customer','')))
            points=draft.get('points',1000)
            if type(points) is int:
                self.job_points.setValue(points)
            when=QDateTime.fromString(str(draft.get('when','')),'yyyy-MM-ddTHH:mm:ss')
            if when.isValid():
                self.schedule_time.setDateTime(when)
            self.schedule.setChecked(draft.get('scheduled') is True)
            self.job_combo.setChecked(draft.get('combo') is True)
        except (ValueError,TypeError):
            pass
        self.update_job_quote()

    def update_job_quote(self,*_):
        count=(self.job_points.value()+99)//100
        self.job_account_count.setText(f'(= {count:,} accounts)')
        rate=self.snapshot.get('settings',{}).get('sale_rate','')
        try:
            amount=Decimal(str(rate))*count
            if not amount.is_finite():
                raise ValueError()
            self.job_quote.setText(format(amount,',.2f').rstrip('0').rstrip('.'))
        except (ValueError,ArithmeticError):
            self.job_quote.clear()
        self.job_start_button.setText('Tạo lịch combo' if self.schedule.isChecked() and self.job_combo.isChecked()
            else 'Tạo lịch job' if self.schedule.isChecked()
            else 'Bắt đầu combo' if self.job_combo.isChecked() else 'Bắt đầu nhập mã')
        provider=self.snapshot.get('settings',{}).get('provider','')
        names={'gmailvip_vn':'GmailVip.vn','1trieumail_com':'1TrieuMail.com','multi_shop':'Cả hai shop'}
        batch=self.job_batch.currentData()
        source=('Lô mail đã mua: '+str(batch)) if batch else 'Nguồn mua mail: '+names.get(provider,'chưa kết nối VPS')
        self.job_source_summary.setText(source+' · Cấu hình mail, proxy và số luồng được quản lý tại Cài đặt chung.')

    def save_settings(self):
        values = {}
        for key,field in self.settings_fields.items():
            values[key] = (field.currentData() if isinstance(field,QComboBox) else field.value() if isinstance(field,QSpinBox)
                else field.isChecked() if isinstance(field,QCheckBox) else field.text())
        self.send('settings.save',values,lambda _:(self.settings_fields['gmailvip_key'].clear(),self.settings_fields['one_trieu_key'].clear()))

    def load_settings(self,state):
        config = state.get('settings',{})
        for key,field in self.settings_fields.items():
            if key not in config:
                continue
            value = config[key]
            if isinstance(field,QComboBox): field.setCurrentIndex(max(0,field.findData(value)))
            elif isinstance(field,QSpinBox): field.setValue(value)
            elif isinstance(field,QCheckBox): field.setChecked(value)
            else: field.setText(str(value))
        self.bot_auto.setChecked(state.get('telegram',{}).get('auto_start',False))
        payment = state.get('sepay',{})
        self.sepay_auto.setChecked(payment.get('enabled',False))
        for key,value in payment.get('fields',{}).items():
            self.sepay_fields[key].setText(value)
        self.update_job_quote()

    def render(self,response):
        from gui.remote_workspace import format_time
        state=response.get('state',{})
        if state.get('version')!=1:
            self.failure('Phiên bản dịch vụ VPS không tương thích.')
            self.enable_actions(False)
            return
        self.snapshot=state
        self._connected=True
        self.enable_actions(not state.get('closing',False))
        self.btn_connection.setText('●  Đã kết nối VPS')
        self.btn_connection.setStyleSheet('text-align:left;background:transparent;border:none;color:#34d399;padding:8px 5px;font-size:11px;')
        self.status.setText('Đã kết nối VPS · '+datetime.now().strftime('%H:%M:%S'))
        records=state.get('jobs',[])
        failed={'Thất bại','Mã không hợp lệ','Mã mời lỗi','Đạt giới hạn 24h'}
        values=dict(total=len(records),active=state['active_jobs']+state['queued_jobs']+state['scheduled_jobs'],
            completed=sum(r.get('status')=='Hoàn thành' for r in records),
            failed=sum(r.get('status') in failed for r in records),
            beans=sum(max(0,r.get('success_count',0))*100 for r in records),
            proxies=sum(r.get('status')!='FAILED' for r in state['proxies']))
        for key,value in values.items():
            self.dashboard_metrics[key].setText(f'{value:,}')
        self.btn_proxy_pill.setText(f"●  {values['proxies']} proxy sẵn sàng" if values['proxies'] else '○  Chưa có proxy')
        self.dashboard_summary.setText(f"Đang chờ: {state['queued_jobs']} · Hẹn giờ: {state['scheduled_jobs']}")
        self.job_queue_label.setText(f"Đang chạy: {state['active_jobs']} / {state['job_limit']} job · "
            f"Đang chờ: {state['queued_jobs']} · Hẹn giờ: {state['scheduled_jobs']}")
        display=[{**r,'customer_display':(r.get('customer_name') or r['job_id'])+(' · Combo' if r.get('combo') else ''),
            'progress':f"{r.get('success_count',0)} / {r.get('target_count',0)}",
            'beans':f"{r.get('success_count',0)*100:,} / {r.get('target_count',0)*100:,}",
            'beans_actual':f"{r.get('success_count',0)*100:,}",
            'time_display':('Tiếp: ' if r.get('combo') and r.get('status')=='Chờ lượt combo' else '')
                +format_time(r.get('scheduled_at') or r.get('created_at'))}
            for r in records]
        recent=list(reversed(display))[:10]
        fill(self.dashboard_jobs,recent,['customer_display','invite_code','progress','beans_actual','status','time_display'],identity_key='job_id')
        self.dashboard_empty.setVisible(not recent)
        at_top=self.jobs.verticalScrollBar().value()<=1
        display=ordered_jobs(display)
        for old_row in range(self.jobs.rowCount()):
            cell=self.jobs.cellWidget(old_row,7)
            if cell is not None:
                cell.hide()
                self.jobs.removeCellWidget(old_row,7)
        fill(self.jobs,display,['customer_display','creator_name','invite_code','progress','beans','status','time_display','actions'],identity_key='job_id')
        self._job_row_buttons=[]
        terminal=TERMINAL_JOB_STATUSES
        for row,record in enumerate(display):
            for column in range(7):
                item=self.jobs.item(row,column)
                item.setData(Qt.ItemDataRole.UserRole,record['job_id'])
                item.setToolTip('Mã job: '+record['job_id']+'\n'+item.text())
            actions=QWidget()
            actions.setStyleSheet('background:transparent;')
            layout=QHBoxLayout(actions)
            layout.setContentsMargins(4,3,4,3);layout.setSpacing(4)
            for caption,callback,allowed in (
                ('Dừng',lambda _=False,jid=record['job_id']:self.confirm_stop_job(jid),record.get('status') not in terminal),
                ('Log',lambda _=False,jid=record['job_id']:self.open_job_logs(jid),True),
                ('Tiếp tục',lambda _=False,jid=record['job_id']:self.send('job.continue',{'job_id':jid},self.job_created),record.get('can_continue',False))):
                button=QPushButton(caption)
                button.setStyleSheet('padding:4px 8px;font-size:11px;')
                button.clicked.connect(callback)
                button.setEnabled(allowed and not self._mutating and not self._closing and not state.get('closing',False))
                self._job_row_buttons.append((button,allowed))
                layout.addWidget(button)
            self.jobs.setCellWidget(row,7,actions)
        focused=False
        if self._pending_focus_job_id:
            for row,record in enumerate(display):
                if record['job_id']==self._pending_focus_job_id:
                    self.jobs.selectRow(row)
                    self.jobs.scrollToItem(self.jobs.item(row,0))
                    self._pending_focus_job_id=None
                    focused=True
                    break
        if at_top and not focused:
            self.jobs.scrollToTop()
        updated=datetime.now().strftime('%H:%M:%S')
        if self._pending_focus_job_id:
            self.job_sync_status.setText('VPS đã nhận '+self._pending_focus_job_id+
                f' · Đang chờ cập nhật vào danh sách ({len(display)} job đã đồng bộ lúc {updated}).')
        else:
            self.job_sync_status.setText(f'Đã đồng bộ {len(display)} job từ VPS lúc {updated} · '
                'Job đang xử lý và job mới hiển thị trước.')
        self.proxy_views['account'].update_rows(state['proxies'])
        self.proxy_views['mail'].update_rows(state['mail_proxies'])
        self.telegram_presentation.refresh(state)
        active_users=sum(r.get('active',False) and r.get('chat_id',0)>0 for r in state['users'])
        self.dashboard_telegram.setText(f"{active_users} người dùng được cấp quyền · "+
            ('Bot đang nhận yêu cầu' if state['telegram'].get('ready') else 'Bot chưa nhận yêu cầu'))
        self.lookup_status.setText(state['lookup']['status'])
        fill(self.profiles,state['lookup']['profiles'],['email','user_id','display_name','invite_code','beans','updated_at'])
        config=state['settings']
        self.settings_status.setText('API GmailVip: '+('đã lưu' if config['has_gmailvip_key'] else 'chưa có')+
            ' · API 1TrieuMail: '+('đã lưu' if config['has_one_trieu_key'] else 'chưa có'))
        if not self._config_loaded:
            self.load_settings(state)
            self._config_loaded=True
        self.update_job_quote()

    def closeEvent(self,event):
        self._closing = True
        self.timer.stop()
        self.enable_actions(False)
        if self._workers:
            self.status.setText('Đang đóng kết nối quản lý. Job trên VPS vẫn chạy.')
            event.ignore()
        else:
            event.accept()
