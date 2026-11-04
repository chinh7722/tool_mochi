"""Standalone desktop SSH tunnel manager backed by the system OpenSSH client."""
import os
from pathlib import Path
import shutil
import sys

from PySide6.QtCore import QProcess,QTimer
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QSpinBox,
    QComboBox,QPushButton,QLabel,QPlainTextEdit,QFileDialog,QMessageBox,QInputDialog)
from core.ssh_connections import SSHProfiles,validate_profile


class SSHManager(QWidget):
    def __init__(self,directory,root):
        super().__init__();self.setWindowTitle('Mochi · Quản lý kết nối VPS / SSH key');self.resize(860,760)
        self.setObjectName('sshConnectionManager')
        self.setStyleSheet('QWidget#sshConnectionManager {background:#18181b;color:#edf2fc;} '
            'QLabel {color:#c5d0e3;} QPlainTextEdit {background:#1d1d20;color:#c5d0e3;border:1px solid #343d50;}')
        self.store=SSHProfiles(directory);self.root=Path(root);self.process=None;self.helper=None;self._closing=False
        self.profiles=self.store.load()
        layout=QVBoxLayout(self);layout.addWidget(QLabel('Kết nối SSH tới VPS · Tunnel chỉ mở trên 127.0.0.1'))
        row=QHBoxLayout();self.selector=QComboBox();self.selector.addItem('Kết nối mới',-1)
        for i,p in enumerate(self.profiles):self.selector.addItem(p['name'],i)
        row.addWidget(self.selector,1);self.button('Lưu kết nối',self.save,row);layout.addLayout(row)
        form=QFormLayout();self.fields={}
        for key,title,default in (('name','Tên kết nối','VPS Mochi'),('host','Host / IP','sv50.pcrender.net'),
                ('user','Tài khoản SSH','root1'),('identity','SSH private key','')):
            self.fields[key]=QLineEdit(default);form.addRow(title,self.fields[key])
        for key,title,default in (('port','Cổng SSH',20052),('local_port','Cổng API trên máy này',8765),('remote_port','Cổng API trên VPS',8765)):
            spin=QSpinBox();spin.setRange(1,65535);spin.setValue(default);self.fields[key]=spin;form.addRow(title,spin)
        layout.addLayout(form)
        row=QHBoxLayout();self.button('Chọn SSH key',self.choose_key,row);self.button('Lưu bản sao key',self.import_key,row)
        self.button('Tạo key ed25519',self.generate_key,row);self.button('Xem / Sao chép public key',self.show_public,row);layout.addLayout(row)
        note=QLabel('Key được giữ trên máy này. Cài public key vào ~/.ssh/authorized_keys của tài khoản VPS. '
            'Key có passphrase cần được nạp vào ssh-agent. Không lưu mật khẩu SSH trong cấu hình.')
        note.setWordWrap(True);layout.addWidget(note)
        row=QHBoxLayout();self.button('Kiểm tra / Tin cậy máy chủ',self.scan_host,row)
        self.connect_button=self.button('Kết nối VPS',self.connect_server,row);self.button('Ngắt kết nối',self.disconnect,row)
        self.button('Mở tool quản lý VPS',self.open_manager,row);layout.addLayout(row)
        self.status=QLabel('Chọn hoặc tạo SSH key, lưu kết nối rồi kết nối VPS.');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.logs=QPlainTextEdit();self.logs.setReadOnly(True);self.logs.setMaximumBlockCount(300);layout.addWidget(self.logs,1)
        self.selector.currentIndexChanged.connect(self.load_profile)
        if self.profiles:self.selector.setCurrentIndex(1)

    def button(self,title,callback,layout):
        button=QPushButton(title);button.clicked.connect(lambda _checked=False:self.action(callback));layout.addWidget(button);return button

    def action(self,callback):
        try:callback()
        except Exception as exc:QMessageBox.warning(self,'Kết nối VPS',str(exc) if isinstance(exc,ValueError) else 'Không xử lý được kết nối. Kiểm tra file key và quyền truy cập.')

    def values(self):
        return validate_profile({k:v.value() if isinstance(v,QSpinBox) else v.text() for k,v in self.fields.items()})

    def load_profile(self):
        index=self.selector.currentData()
        if index is None or index<0:return
        for key,value in self.profiles[index].items():
            field=self.fields[key];field.setValue(value) if isinstance(field,QSpinBox) else field.setText(value)

    def save(self):
        profile=self.values();index=self.selector.currentData()
        if index is None or index<0:
            self.profiles.append(profile);index=len(self.profiles)-1;self.selector.addItem(profile['name'],index)
        else:self.profiles[index]=profile;self.selector.setItemText(self.selector.currentIndex(),profile['name'])
        self.store.save(self.profiles);self.selector.setCurrentIndex(index+1);self.status.setText('Đã lưu kết nối và đường dẫn SSH key.')

    def choose_key(self):
        path,_=QFileDialog.getOpenFileName(self,'Chọn private key OpenSSH','', 'SSH private key (*)')
        if path:self.fields['identity'].setText(path)

    def protect_key(self,path,callback):
        if os.name!='nt':Path(path).chmod(0o600);callback();return
        # OpenSSH on Windows rejects inherited read permissions on a private key.
        user=os.environ.get('USERDOMAIN','')+'\\'+os.environ.get('USERNAME','')
        self.run_helper('icacls',[str(path),'/inheritance:r','/grant:r',user+':(F)'],lambda _out:callback())

    def import_key(self):
        target=self.store.import_key(self.fields['identity'].text())
        self.protect_key(target,lambda:self.fields['identity'].setText(str(target)))

    def generate_key(self):
        target=self.store.key_target()
        def generated(_out):
            self.protect_key(target,lambda:(self.fields['identity'].setText(str(target)),self.status.setText('Đã tạo key. Cài public key lên VPS trước khi kết nối.')))
        self.run_helper('ssh-keygen',['-t','ed25519','-N','','-C','mochi-vps-manager','-f',str(target)],generated)

    def show_public(self):
        identity=Path(self.fields['identity'].text()).expanduser()
        public=identity.with_name(identity.name+'.pub')
        def display(text):
            QInputDialog.getMultiLineText(self,'Public SSH key — sao chép lên VPS','Public key (không phải private key):',text.strip())
        if public.is_file():display(public.read_text(encoding='utf-8')[:65536])
        elif identity.is_file():self.run_helper('ssh-keygen',['-y','-P','','-f',str(identity)],display)
        else:raise ValueError('Chọn hoặc tạo SSH key trước.')

    def run_helper(self,program,args,callback):
        if self.helper is not None:raise ValueError('Đang xử lý SSH key/máy chủ, chờ hoàn tất.')
        executable=shutil.which(program)
        if not executable:raise ValueError('Máy chưa có '+program+'. Cài OpenSSH Client trong Windows Optional Features.')
        process=QProcess(self);self.helper=process
        def finished(code,_status):
            output=bytes(process.readAllStandardOutput()).decode('utf-8','replace')
            self.helper=None;process.deleteLater()
            if code==0:self.action(lambda:callback(output))
            else:self.status.setText('Không hoàn tất '+program+'. Kiểm tra host/cổng, key hoặc passphrase.')
        process.finished.connect(finished)
        def error(_e):
            self.status.setText('Không khởi chạy được '+program)
            if process.error()==QProcess.ProcessError.FailedToStart:
                self.helper=None;process.deleteLater()
        process.errorOccurred.connect(error)
        process.start(executable,args)

    def scan_host(self):
        profile=self.values()
        def found(output):
            line,fingerprint=self.store.scanned_key(profile,output)
            question='VPS '+profile['host']+':'+str(profile['port'])+'\n'+fingerprint+'\nĐối chiếu fingerprint với VPS, rồi chọn Có để tin cậy máy chủ này.'
            if QMessageBox.question(self,'Xác nhận host key',question,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
                self.store.trust_key(profile,line);self.status.setText('Đã lưu host key đã xác nhận.')
        self.run_helper('ssh-keyscan',['-T','8','-p',str(profile['port']),'-t','ed25519',profile['host']],found)

    def connect_server(self):
        if self.process is not None:raise ValueError('Đã có kết nối hoặc đang kết nối. Ngắt trước khi đổi VPS.')
        profile=self.values();args=self.store.ssh_arguments(profile)
        executable=shutil.which('ssh')
        if not executable:raise ValueError('Máy chưa có OpenSSH Client.')
        process=QProcess(self);self.process=process;self.connect_button.setEnabled(False)
        self.store.update_manager_url(profile);self.status.setText('Đang xác thực SSH và mở tunnel…')
        recent=['']
        def output():
            text=bytes(process.readAllStandardError()).decode('utf-8','replace')
            self.logs.appendPlainText(text.strip())
            recent[0]=(recent[0]+text)[-4000:]
            if 'Local forwarding listening' in recent[0]:self.status.setText('Đã mở tunnel http://127.0.0.1:'+str(profile['local_port']))
        process.readyReadStandardError.connect(output)
        def finished(_code,_status):
            self.process=None;self.connect_button.setEnabled(True);self.status.setText('Đã ngắt SSH. Job trên VPS vẫn chạy.');process.deleteLater()
            if self._closing:self.close()
        process.finished.connect(finished)
        def error(_e):
            self.status.setText('Không khởi chạy được SSH. Kiểm tra OpenSSH Client.')
            if process.error()==QProcess.ProcessError.FailedToStart:
                self.process=None;self.connect_button.setEnabled(True);process.deleteLater()
        process.errorOccurred.connect(error)
        process.start(executable,args)

    def disconnect(self):
        if self.process is not None:
            process=self.process;process.terminate()
            QTimer.singleShot(3000,lambda:process.kill() if self.process is process and process.state()!=QProcess.ProcessState.NotRunning else None)

    def open_manager(self):
        QProcess.startDetached(sys.executable,['-I',str(self.root/'main_vps_manager.py')],str(self.root))

    def closeEvent(self,event):
        self._closing=True
        if self.helper is not None:self.helper.kill();event.ignore();QTimer.singleShot(100,self.close);return
        if self.process is not None:self.disconnect();event.ignore();return
        super().closeEvent(event)
