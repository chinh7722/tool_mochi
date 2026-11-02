"""Own-account invite/profile lookup confirmed against Mochichat 1.3.2 XAPK."""
from contextlib import contextmanager
import csv
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from core.browser import browser_manager
from core.job_schedule import VIETNAM_TIME
from core.platforms.mochichat.device import DeviceInfo
from core.platforms.mochichat.fetcher import MochiChatFetcher,MochiAccountReadError
from core.proxy.protocol import is_request_transport_error

LOOKUP_APP_VERSION='1.3.2'
LOOKUP_APP_BUILD='38'
LOOKUP_CERT_SHA1='706f0ea1a5195f1a0cb244a891445c209d97584d'
PROFILE_FIELDS=('email','user_id','display_name','invite_code','beans','updated_at')


class AccountLookupError(RuntimeError):
    def __init__(self,message,*,transport_error=False):
        self.transport_error=bool(transport_error)
        super().__init__(message)


@dataclass(frozen=True)
class AccountProfile:
    email: str
    user_id: str
    display_name: str
    invite_code: str
    beans: str
    updated_at: str


def _email(value):
    if not isinstance(value,str) or len(value)>320:
        raise AccountLookupError('Email Google không hợp lệ.')
    value=value.strip()
    if (re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',value) is None
            or any(ord(char)<32 or ord(char)==127 for char in value)):
        raise AccountLookupError('Nhập đầy đủ địa chỉ email Google hợp lệ.')
    return value


def _quota(value):
    if isinstance(value,bool) or value is None or not isinstance(value,(str,int,float,Decimal)):
        raise AccountLookupError('Số dư beans chưa có hoặc không hợp lệ trong phản hồi Mochi.')
    raw=str(value).strip()
    if (not raw or len(raw)>256
            or re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?',raw) is None):
        raise AccountLookupError('Số dư beans chưa có hoặc không hợp lệ trong phản hồi Mochi.')
    try:
        number=Decimal(raw)
    except (InvalidOperation,ValueError):
        raise AccountLookupError('Số dư beans không phải số hợp lệ trong phản hồi Mochi.') from None
    if not number.is_finite() or abs(number.adjusted())>128 or len(number.as_tuple().digits)>128:
        raise AccountLookupError('Số dư beans không phải số hữu hạn hợp lệ.')
    if not number:
        return '0'
    formatted=format(number,'f')
    return formatted.rstrip('0').rstrip('.') if '.' in formatted else formatted


def _timestamp(value=None):
    try:
        stamp=datetime.now(VIETNAM_TIME) if value is None else datetime.fromisoformat(value)
        if stamp.tzinfo is None:
            stamp=stamp.replace(tzinfo=VIETNAM_TIME)
        return stamp.astimezone(VIETNAM_TIME).isoformat()
    except (TypeError,ValueError,OverflowError):
        raise AccountLookupError('Thời gian cập nhật thông tin tài khoản không hợp lệ.') from None


def parse_account_profile(email,me,invite,*,updated_at=None):
    """Parse only evidenced getMe and getMyInviteCode data, not guessed wallet fields."""
    email=_email(email)
    if not isinstance(me,dict) or not isinstance(invite,dict):
        raise AccountLookupError('Cấu trúc thông tin tài khoản Mochi không hợp lệ.')
    if 'remainingQuota' not in me:
        raise AccountLookupError('Phản hồi Mochi chưa có trường số dư beans remainingQuota.')
    beans=_quota(me['remainingQuota'])
    name=me.get('nickname')
    if name is None:
        name=''
    if not isinstance(name,str) or len(name)>2048 or '\x00' in name:
        raise AccountLookupError('Tên hiển thị trong phản hồi Mochi không hợp lệ.')
    name=name.strip() or 'Mochi User'
    code=invite.get('code')
    if not isinstance(code,str) or re.fullmatch(r'[A-Za-z0-9_-]{1,128}',code.strip()) is None:
        raise AccountLookupError('Phản hồi Mochi chưa có mã mời hợp lệ trong trường code.')
    user_id=me.get('id')
    if user_id is None:
        user_id=''
    elif type(user_id) is int:
        user_id=str(user_id)
    if not isinstance(user_id,str) or len(user_id)>256 or '\x00' in user_id:
        raise AccountLookupError('ID người dùng trong phản hồi Mochi không hợp lệ.')
    return AccountProfile(email,user_id.strip(),name,code.strip(),beans,_timestamp(updated_at))


def _validated_profile(profile):
    if not isinstance(profile,AccountProfile):
        raise AccountLookupError('Dữ liệu thông tin tài khoản không hợp lệ để lưu.')
    return parse_account_profile(profile.email,{'id':profile.user_id,'nickname':profile.display_name,
        'remainingQuota':profile.beans},{'code':profile.invite_code},updated_at=profile.updated_at)


def _csv_text(value):
    # Quoting CSV fields alone does not stop spreadsheet formula evaluation.
    if value and (value[0] in '\t\r\n' or value.lstrip().startswith(('=','+','-','@'))):
        return "'"+value
    return value


def export_profiles_csv(path,profiles):
    """Export in-memory public profiles without requiring a working SQLite store."""
    rows=tuple(_validated_profile(row) for row in profiles)
    temp_path=None
    try:
        target=Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8-sig',newline='',dir=target.parent,
                prefix='.'+target.name+'.',suffix='.tmp',delete=False) as stream:
            temp_path=Path(stream.name)
            writer=csv.writer(stream)
            writer.writerow(PROFILE_FIELDS)
            for row in rows:
                writer.writerow([getattr(row,name) if name=='beans' else _csv_text(getattr(row,name))
                    for name in PROFILE_FIELDS])
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path,target)
    except Exception:
        raise AccountLookupError('Không xuất được CSV; kiểm tra quyền ghi và đóng file CSV nếu đang mở.') from None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


class AccountProfileStore:
    def __init__(self,path):
        try:
            self.path=Path(path).expanduser().resolve()
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with self._connection() as db:
                db.execute('''CREATE TABLE IF NOT EXISTS account_profiles (
                    email_key TEXT PRIMARY KEY,email TEXT NOT NULL,user_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,invite_code TEXT NOT NULL,beans TEXT NOT NULL,
                    updated_at TEXT NOT NULL)''')
        except AccountLookupError:
            raise
        except Exception:
            raise AccountLookupError('Không mở được kho thông tin tài khoản đã lưu.') from None

    @contextmanager
    def _connection(self):
        db=None
        try:
            db=sqlite3.connect(self.path,timeout=10)
            db.row_factory=sqlite3.Row
            yield db
            db.commit()
        except Exception:
            if db is not None:
                db.rollback()
            raise
        finally:
            if db is not None:
                db.close()

    def upsert(self,profile):
        profile=_validated_profile(profile)
        try:
            with self._connection() as db:
                db.execute('''INSERT INTO account_profiles VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(email_key) DO UPDATE SET email=excluded.email,user_id=excluded.user_id,
                        display_name=excluded.display_name,invite_code=excluded.invite_code,
                        beans=excluded.beans,updated_at=excluded.updated_at''',
                    (profile.email.casefold(),*(getattr(profile,name) for name in PROFILE_FIELDS)))
        except Exception:
            raise AccountLookupError('Không lưu được thông tin tài khoản; dữ liệu trước đó vẫn được giữ.') from None

    def list_profiles(self):
        try:
            with self._connection() as db:
                rows=db.execute('''SELECT email,user_id,display_name,invite_code,beans,updated_at
                    FROM account_profiles ORDER BY updated_at DESC,email_key''').fetchall()
            return tuple(_validated_profile(AccountProfile(**dict(row))) for row in rows)
        except Exception:
            raise AccountLookupError('Không đọc được kho thông tin tài khoản đã lưu.') from None

    def export_csv(self,path,profiles=None):
        export_profiles_csv(path,self.list_profiles() if profiles is None else profiles)


async def lookup_account(email,password,*,owner_id,proxy=None,google_via_proxy=False,
        app_via_proxy=False,headless=True,on_progress=None):
    """Authenticate provided credentials and read only own profile + own invite code."""
    email=_email(email)
    if not isinstance(password,str) or not password:
        raise AccountLookupError('Nhập mật khẩu Google để lấy thông tin tài khoản.')
    if not isinstance(owner_id,str) or not owner_id.strip():
        raise AccountLookupError('Chưa có mã phiên đọc thông tin tài khoản.')
    owner_id=owner_id.strip()
    if (google_via_proxy or app_via_proxy) and not proxy:
        raise AccountLookupError('Đã chọn proxy nhưng chưa được cấp proxy để đọc tài khoản.')

    def progress(message):
        if on_progress is not None:
            try:
                on_progress(message)
            except Exception:
                pass

    # Raw Google progress can contain URLs or exception text: emit phase labels only.
    phases={1}
    def login_step(step,_raw):
        phase=1 if step==1 else 2
        if phase not in phases:
            phases.add(phase)
            progress('Đang đăng nhập Google…' if phase==1 else 'Đang đăng nhập Mochi Chat…')

    fetcher=None
    try:
        fetcher=MochiChatFetcher(device=DeviceInfo(app_version=LOOKUP_APP_VERSION,app_build=LOOKUP_APP_BUILD),
            proxy=proxy,app_login_via_proxy=app_via_proxy,redeem_via_proxy=False,auth_cert_sha1=LOOKUP_CERT_SHA1)
        progress('Đang đăng nhập Google…')
        result=await fetcher.login(owner_id=owner_id,email=email,password=password,headless=headless,
            google_login_via_proxy=google_via_proxy,on_step=login_step)
        if not isinstance(result,dict) or result.get('ok') is not True:
            transport=isinstance(result,dict) and bool(result.get('transport_error'))
            raise AccountLookupError('Không đăng nhập được Google/Mochi; kiểm tra tài khoản và kết nối.',
                transport_error=transport)
        progress('Đang lấy tên hiển thị và số dư beans…')
        me=await fetcher.get_own_user_me()
        progress('Đang lấy mã mời của tài khoản…')
        invite=await fetcher.get_own_invite_code()
        profile=parse_account_profile(email,me,invite)
        progress('Đã lấy mã mời và thông tin tài khoản.')
        return profile
    except AccountLookupError:
        raise
    except MochiAccountReadError as exc:
        raise AccountLookupError(str(exc),transport_error=exc.transport_error) from None
    except Exception as exc:
        raise AccountLookupError('Không lấy được thông tin tài khoản; kiểm tra đăng nhập Google và kết nối Mochi.',
            transport_error=is_request_transport_error(exc)) from None
    finally:
        try:
            if fetcher is not None:
                try:
                    await fetcher.close()
                except Exception:
                    pass
        finally:
            try:
                try:
                    await browser_manager.release_by_owner(owner_id)
                except Exception:
                    pass
            finally:
                try:
                    await browser_manager.shutdown_for_loop()
                except Exception:
                    pass
