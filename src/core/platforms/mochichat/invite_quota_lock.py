"""Cancellable per-code ownership across threads, loops and service processes."""
import asyncio
from contextlib import asynccontextmanager
import hashlib
import os
from pathlib import Path
import threading

from core.platforms.mochichat.invite_quota import normalize_invite_code

_GUARD=threading.Lock()
_LOCKS={}


@asynccontextmanager
async def invite_code_guard(store,code,*,cancelled=lambda:False,on_wait=lambda:None):
    code=normalize_invite_code(code)
    directory=Path(store.path).resolve().parent/'invite_quota_locks'
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/(hashlib.sha256(code.encode()).hexdigest()+'.lock')
    key=str(path).casefold() if os.name=='nt' else str(path)
    with _GUARD:
        lock=_LOCKS.setdefault(key,threading.Lock())
    waiting=False
    owned=False
    stream=None
    file_owned=False
    try:
        while not owned:
            if cancelled():
                raise asyncio.CancelledError()
            owned=lock.acquire(blocking=False)
            if not owned:
                if not waiting:
                    waiting=True
                    on_wait()
                await asyncio.sleep(0.1)
        stream=path.open('a+b')
        if os.name=='nt':
            import msvcrt
            stream.seek(0,os.SEEK_END)
            if stream.tell()==0:
                stream.write(b'0');stream.flush()
        while not file_owned:
            if cancelled():
                raise asyncio.CancelledError()
            try:
                if os.name=='nt':
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                file_owned=True
            except BlockingIOError:
                pass
            except OSError as exc:
                # Windows reports an overlapping locked range as access denied.
                if os.name!='nt' or getattr(exc,'winerror',None) not in (33,36) and exc.errno not in (13,11):
                    raise
            if not file_owned:
                if not waiting:
                    waiting=True
                    on_wait()
                await asyncio.sleep(0.1)
        yield
    finally:
        try:
            if stream is not None:
                if file_owned:
                    if os.name=='nt':
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
                    else:
                        fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
                stream.close()
        finally:
            if owned:
                lock.release()
