"""Durable terminal job snapshots with an atomic, Excel-readable CSV."""
from contextlib import contextmanager
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time

JOB_TIMEZONE = timezone(timedelta(hours=7))
STATUS_LABELS = {'completed':'Hoàn thành','failed':'Thất bại','cancelled':'Đã hủy',
    'invalid_code':'Mã không hợp lệ','invite_limit_reached':'Đạt giới hạn 24h'}
COLUMNS = (('job_id','Mã job'),('order_id','Mã đơn'),('creator_name','Người tạo job'),
    ('creator_chat_id','Chat ID người tạo'),('customer_name','Khách hàng / Job'),('invite_code','Mã mời'),
    ('created_at','Thời gian tạo'),('finished_at','Thời gian kết thúc'),('status','Trạng thái'),
    ('target_count','Số tài khoản mục tiêu'),('attempted_count','Số tài khoản đã thử'),
    ('success_count','Số tài khoản thành công'),('failure_count','Số tài khoản lỗi'),
    ('target_beans','Bean mục tiêu'),('achieved_beans','Bean thực tế'))
_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class JobHistoryEntry:
    job_id: str
    order_id: str
    creator_name: str
    creator_chat_id: int | None
    customer_name: str
    invite_code: str
    created_at: str
    finished_at: str
    status: str
    target_count: int
    attempted_count: int
    success_count: int
    failure_count: int
    target_beans: int
    achieved_beans: int

    def __post_init__(self):
        for key in ('job_id','order_id','creator_name','customer_name','invite_code','created_at','finished_at','status'):
            if not isinstance(getattr(self,key),str):
                raise ValueError('Trường văn bản lịch sử job không hợp lệ.')
        if not self.job_id or len(self.job_id)>120 or not self.status or self.status in ('running','pending'):
            raise ValueError('Chỉ lưu lịch sử job đã kết thúc có mã job hợp lệ.')
        if self.creator_chat_id is not None and (type(self.creator_chat_id) is not int or not 0<self.creator_chat_id<2**63):
            raise ValueError('Chat ID trong lịch sử không hợp lệ.')
        for key in ('target_count','attempted_count','success_count','failure_count','target_beans','achieved_beans'):
            if type(getattr(self,key)) is not int or not 0<=getattr(self,key)<2**63:
                raise ValueError('Số lượng trong lịch sử job không hợp lệ.')
        for key in ('created_at','finished_at'):
            if datetime.fromisoformat(getattr(self,key)).tzinfo is None:
                raise ValueError('Thời gian lịch sử job phải có múi giờ.')


def _dangerous(text):
    return text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r'))


def _csv_text(value):
    text='' if value is None else str(value)
    return "'"+text if text.startswith("'") or _dangerous(text) else text


def _original_text(text):
    if text.startswith("'") and (text[1:].startswith("'") or _dangerous(text[1:])):
        return text[1:]
    return text


def _atomic_bytes(path,data):
    descriptor,temporary=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(descriptor,'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class JobHistoryStore:
    def __init__(self,path):
        self.path=Path(path).expanduser().resolve()
        self.pending_directory=self.path.parent/(self.path.stem+'_pending')
        with _LOCKS_GUARD:
            self._thread_lock=_LOCKS.setdefault(str(self.path).casefold(),threading.RLock())

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._thread_lock:
            with self.path.with_suffix(self.path.suffix+'.lock').open('a+b') as stream:
                stream.seek(0,os.SEEK_END)
                if stream.tell()==0:
                    stream.write(b'\0')
                    stream.flush()
                if os.name=='nt':
                    import msvcrt
                    deadline=time.monotonic()+3
                    while True:
                        try:
                            stream.seek(0)
                            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                            break
                        except OSError:
                            if time.monotonic()>=deadline:
                                raise OSError('Lịch sử đang được tiến trình khác ghi; thử Làm mới sau.') from None
                            time.sleep(0.05)
                    try:
                        yield
                    finally:
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(stream.fileno(),fcntl.LOCK_UN)

    def _csv_rows(self):
        if not self.path.exists():
            return []
        with self.path.open('r',encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream)
            if reader.fieldnames!=[label for _,label in COLUMNS]:
                raise ValueError('CSV lịch sử không đúng cấu trúc; file hiện tại được giữ nguyên.')
            rows=[]
            for raw in reader:
                if None in raw or any(value is None for value in raw.values()):
                    raise ValueError('CSV lịch sử có dòng chưa đầy đủ; file được giữ nguyên.')
                data={key:_original_text(raw[label]) for key,label in COLUMNS}
                for key in ('target_count','attempted_count','success_count','failure_count','target_beans','achieved_beans'):
                    data[key]=int(data[key])
                data['creator_chat_id']=int(data['creator_chat_id']) if data['creator_chat_id'] else None
                data['status']={label:key for key,label in STATUS_LABELS.items()}.get(data['status'],data['status'])
                rows.append(JobHistoryEntry(**data))
            if len({row.job_id for row in rows})!=len(rows):
                raise ValueError('CSV có mã job trùng; kiểm tra file trước khi ghi tiếp.')
            return rows

    def _pending(self):
        result=[]
        for path in sorted(self.pending_directory.glob('*.json')):
            entry=JobHistoryEntry(**json.loads(path.read_text(encoding='utf-8')))
            if path.stem!=hashlib.sha256(entry.job_id.encode('utf-8')).hexdigest():
                raise ValueError('Bản ghi lịch sử đang chờ có mã job không khớp.')
            result.append((path,entry))
        return result

    def _combined(self):
        rows=self._csv_rows()
        known={row.job_id for row in rows}
        pending=self._pending()
        for _,entry in pending:
            if entry.job_id not in known:
                rows.append(entry)
                known.add(entry.job_id)
        return rows,pending

    def _flush(self):
        rows,pending=self._combined()
        if not pending:
            return
        output=io.StringIO(newline='')
        writer=csv.writer(output)
        writer.writerow([label for _,label in COLUMNS])
        for entry in rows:
            data=asdict(entry)
            data['status']=STATUS_LABELS.get(entry.status,entry.status)
            writer.writerow([_csv_text(data[key]) for key,_ in COLUMNS])
        _atomic_bytes(self.path,output.getvalue().encode('utf-8-sig'))
        for path,_ in pending:
            path.unlink(missing_ok=True)

    def record(self,entry):
        self.record_many((entry,))

    def record_many(self,entries):
        entries=tuple(entries)
        if not all(isinstance(entry,JobHistoryEntry) for entry in entries):
            raise ValueError('Bản ghi lịch sử không hợp lệ.')
        # Save the receipt before waiting on the shared CSV writer. A busy writer
        # or a file held by Excel must not discard this job's terminal result.
        if entries:
            self.pending_directory.mkdir(parents=True,exist_ok=True)
        for entry in entries:
            path=self.pending_directory/(hashlib.sha256(entry.job_id.encode('utf-8')).hexdigest()+'.json')
            if not path.exists():
                _atomic_bytes(path,json.dumps(asdict(entry),ensure_ascii=False).encode('utf-8'))
        with self._locked():
            self._flush()

    def sync(self):
        with self._locked():
            self._flush()

    def read(self):
        with self._locked():
            rows,_=self._combined()
            return tuple(sorted(rows,key=lambda row:(datetime.fromisoformat(row.finished_at),row.job_id),reverse=True))
