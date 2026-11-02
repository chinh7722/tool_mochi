"""Exclusive mail purchase routes with background IP rotation across job loops."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from curl_cffi.requests import AsyncSession
from core.proxy.broker import proxy_maintenance,update_proxy_endpoint,require_proxy_rotation
from core.proxy.models import ProxyModel,curl_proxy_options
from core.proxy.rotators import make_rotating_provider,provider_shared_identity,is_real_proxy_error

_LOCK=threading.RLock()
_STATES={}


@dataclass
class _State:
    reference:str
    proxy:ProxyModel
    factory:object
    marker:Path|None=None
    configured:str=''
    in_use:bool=False
    rotating:bool=False
    required:bool=False
    failed:bool=False
    retry_at:float=0
    thread:object=None
    loop:object=None
    task:object=None
    stop:threading.Event=field(default_factory=threading.Event)


def _marker(state,required=True,proxy=None):
    if state.marker is None:
        return
    state.marker.parent.mkdir(parents=True,exist_ok=True)
    descriptor,path=tempfile.mkstemp(dir=state.marker.parent,suffix='.tmp')
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            data={'requires_rotation':required,'config_hash':hashlib.sha256(state.configured.encode()).hexdigest()}
            if proxy is not None:
                data['server']=proxy.server
            json.dump(data,stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path,state.marker)
    finally:
        if os.path.exists(path):
            os.unlink(path)


def _start_rotation(state,maintenance=None):
    if state.rotating or state.in_use:
        return
    if maintenance is None:
        maintenance=proxy_maintenance(state.reference)
        try:
            maintenance.__enter__()
        except Exception:
            state.retry_at=time.monotonic()+0.1
            return
    state.rotating=True
    state.stop.clear()
    def run():
        async def rotate():
            with _LOCK:
                state.loop=asyncio.get_running_loop()
                state.task=asyncio.current_task()
            if state.stop.is_set():
                return
            provider=state.factory(state.reference,current_endpoint=state.proxy.to_url())
            result=await provider.get_current_or_rotate(force_rotate=True,wait_until_rotated=True,max_wait_seconds=300)
            with _LOCK:
                if result.success and result.proxy and not result.is_cooling:
                    _marker(state,False,result.proxy)
                    state.proxy=result.proxy
                    state.required=False
                    state.failed=False
                    update_proxy_endpoint(state.reference,result.proxy)
                else:
                    state.retry_at=time.monotonic()+max(5,result.wait_seconds or 0)
                    state.failed=is_real_proxy_error(result.error or '')
        try:
            asyncio.run(rotate())
        except BaseException:
            with _LOCK:
                state.retry_at=time.monotonic()+5
        finally:
            with _LOCK:
                state.rotating=False
                state.loop=state.task=None
                maintenance.__exit__(None,None,None)
    state.thread=threading.Thread(target=run,name='mail-proxy-rotation',daemon=True)
    try:
        state.thread.start()
    except Exception:
        state.rotating=False
        state.retry_at=time.monotonic()+5
        maintenance.__exit__(None,None,None)


def mail_proxy_snapshot(reference):
    identity=provider_shared_identity(make_rotating_provider(reference))
    with _LOCK:
        state=_STATES.get(identity)
        return None if state is None else dict(in_use=state.in_use,rotating=state.rotating,
            required=state.required,failed=state.failed,proxy=state.proxy)


def acknowledge_mail_proxy_check(reference,proxy,*,rotated=False):
    identity=provider_shared_identity(make_rotating_provider(reference))
    with _LOCK:
        state=_STATES.get(identity)
        if state is None or state.in_use or state.rotating:
            return
        if rotated:
            _marker(state,False,proxy)
            state.proxy=proxy
            state.required=False
        state.failed=False
        state.retry_at=0


def shutdown_mail_rotations():
    with _LOCK:
        states=list(_STATES.values())
        for state in states:
            state.stop.set()
            if state.loop and state.task:
                try:
                    state.loop.call_soon_threadsafe(state.task.cancel)
                except RuntimeError:
                    pass
        return any(state.thread and state.thread.is_alive() for state in states)


@dataclass
class _Lease:
    state:_State
    maintenance:object
    consumed:bool=False


class _PurchaseSession:
    def __init__(self,session,lease):
        self._session,self._lease=session,lease
    def __getattr__(self,name):
        return getattr(self._session,name)
    async def post(self,*args,**kwargs):
        _marker(self._lease.state)  # Durable before POST, including lost responses.
        self._lease.consumed=True
        require_proxy_rotation(self._lease.state.reference)
        return await self._session.post(*args,**kwargs)


class MailProxyPool:
    def __init__(self,entries,*,state_directory=None,provider_factory=None,on_wait=None,timeout=300):
        self.states=[]
        self.cursor=0
        self.on_wait=on_wait
        self.timeout=timeout
        factory=provider_factory or make_rotating_provider
        seen=set()
        for endpoint,reference in entries:
            if not reference:
                raise ValueError('Proxy mua mail cần key xoay: ip:port:user:pass|key_xoay.')
            model=ProxyModel.from_string(endpoint)
            identity=provider_shared_identity(factory(reference,current_endpoint=model.to_url()))
            if identity in seen:
                continue
            seen.add(identity)
            with _LOCK:
                is_new=identity not in _STATES
                if is_new:
                    marker=(Path(state_directory)/f'{hashlib.sha256(identity.encode()).hexdigest()}.json'
                        if state_directory is not None else None)
                    required=False
                    original_url=model.to_url()
                    if marker is not None and marker.exists():
                        try:
                            saved=json.loads(marker.read_text(encoding='utf-8'))
                            required=bool(saved.get('requires_rotation',True))
                            if not required and saved.get('config_hash')==hashlib.sha256(original_url.encode()).hexdigest() and saved.get('server'):
                                cached=ProxyModel.from_string(saved['server'])
                                model.host,model.port=cached.host,cached.port
                        except Exception:
                            required=True
                    _STATES[identity]=_State(reference,model,factory,marker=marker,configured=original_url,required=required)
                state=_STATES[identity]
                if not state.in_use and not state.rotating:
                    if not is_new and state.configured!=model.to_url():
                        state.proxy=model
                        state.configured=model.to_url()
                    state.failed=False
                    state.stop.clear()
                self.states.append(state)

    def _notify(self,message):
        if self.on_wait:
            try:
                self.on_wait(message)
            except Exception:
                pass

    async def acquire(self):
        if not self.states:
            raise ValueError('Tab Proxy mua mail chưa có proxy xoay khả dụng.')
        deadline=time.monotonic()+self.timeout
        announced=False
        while True:
            with _LOCK:
                for offset in range(len(self.states)):
                    index=(self.cursor+offset)%len(self.states)
                    state=self.states[index]
                    if state.in_use or state.rotating or state.failed or state.required:
                        continue
                    context=proxy_maintenance(state.reference)
                    try:
                        context.__enter__()
                    except Exception:
                        continue
                    state.in_use=True
                    self.cursor=(index+1)%len(self.states)
                    self._notify(f'Proxy mua mail: {state.proxy.server}; sẽ xoay IP sau yêu cầu mua.')
                    return _Lease(state,context)
                for state in self.states:
                    if state.required and not state.failed and not state.in_use and not state.rotating and state.retry_at<=time.monotonic():
                        _start_rotation(state)
                if all(state.failed for state in self.states):
                    raise ValueError('Tất cả proxy mua mail đều bị nhà cung cấp từ chối. Kiểm tra key xoay.')
            if not announced:
                self._notify('Các proxy mua mail đều đang bận/xoay IP; chờ proxy đầu tiên sẵn sàng.')
                announced=True
            if time.monotonic()>=deadline:
                raise TimeoutError('Hết thời gian chờ proxy mua mail xoay IP.')
            await asyncio.sleep(0.1)

    def release(self,lease,*,broken=False):
        with _LOCK:
            state=lease.state
            state.in_use=False
            if lease.consumed or broken:
                state.required=True
                _start_rotation(state,lease.maintenance)
                self._notify('Yêu cầu mua mail đã kết thúc; proxy được đưa vào vòng xoay IP.')
            else:
                lease.maintenance.__exit__(None,None,None)

    @asynccontextmanager
    async def session(self):
        lease=await self.acquire()
        broken=False
        connection=None
        try:
            connection=AsyncSession(impersonate='chrome_android',timeout=30,
                proxies=curl_proxy_options(lease.state.proxy),trust_env=False,verify=True)
            active=await connection.__aenter__()
            yield _PurchaseSession(active,lease)
        except BaseException:
            broken=True
            raise
        finally:
            try:
                if connection is not None:
                    try:
                        await connection.__aexit__(None,None,None)
                    except Exception:
                        self._notify('Không đóng được phiên HTTP mua mail; giao dịch đã nhận vẫn được giữ.')
            finally:
                self.release(lease,broken=broken)
