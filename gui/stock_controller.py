"""Qt-side durable orchestration shared by the local tool, VPS API and admin bot."""
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import asyncio
import re

from PySide6.QtCore import QObject, QTimer, Signal
from core.job_schedule import VIETNAM_TIME
from core.stock_accounts import StockAccountStore, creation_options, selected_ids
from core.platforms.mochichat.account_lookup import lookup_account
from core.platforms.mochichat.stock_creation import purchase_stock_batch
from core.mail.providers.priority import MULTI_SHOP, GMAILVIP, ONE_TRIEU, parse_mail_priority
from core.proxy.protocol import ProxyReleaseOutcome
from gui.background import AsyncTaskThread


class StockController(QObject):
    changed = Signal()
    log_line = Signal(str, str)

    def __init__(self, mochi, telegram, directory):
        super().__init__(mochi)
        self.m, self.t = mochi, telegram
        self.store = StockAccountStore(Path(directory)/'stock_accounts.sqlite3')
        self.store.recover()
        self.worker = None
        self.closing = False
        self.last_error = ''
        self.log_line.connect(lambda jid, line:self.m._append_log_line(line, job_id=jid))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(2000)

    def create(self, count, beans, infinite, product_ids, *, owner=-1, request_key=None, mail_keys=None):
        creation_options(count, beans, infinite, product_ids)
        provider, keys = self.mail_settings(product_ids,mail_keys)
        # Validate routing now, before accepting a billable task.
        self.m._account_lookup_route('stock_validate')
        if self.m.chk_use_proxy.isChecked() and self.m.chk_mail_via_proxy.isChecked():
            if self.m.mail_proxy_page is None or not self.m.mail_proxy_page.get_active_proxies():
                raise ValueError('Thêm proxy mua mail hoặc bỏ chọn mua mail qua proxy trong Cài đặt.')
        bid = self.store.create_batch(count, beans, infinite, product_ids, owner, request_key,mail_keys=keys,provider=provider)
        self.changed.emit()
        return {'batch_id':bid, 'message':'Đã lưu lô tạo acc '+bid+'. VPS sẽ mua một lô mail và tạo từng acc.'}

    def mail_settings(self, product_ids, keys, *, provider=None):
        m = self.m
        provider = provider or m.combo_provider.currentData()
        from core.stock_accounts import creation_mail_keys
        keys=creation_mail_keys(keys or {})
        if provider not in (GMAILVIP,ONE_TRIEU,MULTI_SHOP):raise ValueError('Shop mua mail tạo acc không hợp lệ.')
        if provider == MULTI_SHOP:
            choices = parse_mail_priority(product_ids)
            if any(not keys.get(c.provider) for c in choices):
                raise ValueError('Nhập API mua mail tạo acc riêng cho các shop đã chọn.')
        else:
            from core.mail.providers.gmailvip_vn import parse_gmailvip_product_ids
            from core.mail.providers.one_trieu_mail import parse_1trieumail_product_ids
            if not re.fullmatch(r'[1-9][0-9]*(?:[\s,;]+[1-9][0-9]*)*',product_ids.strip()):
                raise ValueError('Product ID phải là số dương hoặc danh sách số, phân cách bằng dấu phẩy.')
            (parse_1trieumail_product_ids if provider == ONE_TRIEU else parse_gmailvip_product_ids)(product_ids)
            if not keys.get(provider):
                raise ValueError('Nhập API mua mail tạo acc riêng cho shop đang chọn; không dùng API nhập mã mời để thay thế.')
        return provider, dict(keys)

    def _start(self, factory, error, on_result=None):
        worker = AsyncTaskThread(factory, self)
        self.worker = worker
        worker.failed.connect(lambda _raw:error())
        if on_result is not None:worker.result.connect(on_result)
        def finished():
            self.worker = None
            worker.factory = None
            worker.deleteLater()
            self.changed.emit()
        worker.finished.connect(finished)
        worker.start()

    def tick(self):
        if self.closing or self.m._closing_jobs:
            return
        try:
            self._sync_jobs()
            if self.worker is not None:
                return
            # Account preparation shares the VPS job capacity, and waits for queued invite work.
            if len(self.m._active_job_ids)+len(self.m._pending_jobs) >= self.m.spin_job_concurrency.value():
                return
            rows = self.store.all(internal=True)
            due = next((r for r in rows if r['status']=='waiting' and not r['job_id']
                and (not r['next_at'] or datetime.fromisoformat(r['next_at'])<=datetime.now(VIETNAM_TIME))),None)
            if due:
                self._dispatch(due)
                return
            for batch in reversed(self.store.batches()):
                if batch['state'] == 'queued':
                    try:self._buy(batch)
                    except (ValueError,PermissionError):
                        self.store.batch_update(batch['id'],'failed','Chưa mua mail: cấu hình hoặc quyền người tạo đã thay đổi. Sửa cấu hình rồi tạo lô mới.')
                    return
            pending = next((r for r in rows if r['status']=='creating'), None)
            if pending:
                self._login(pending)
                return
        except Exception:
            self.last_error = 'Chưa xử lý được kho acc; kiểm tra cấu hình và dữ liệu VPS.'

    def _buy(self, batch):
        owner=self.t.store.get_user(batch['owner'])
        if owner is None or not owner.active or (batch['owner']>0 and owner.role!='admin'):
            raise PermissionError('Người tạo lô không còn quyền quản lý kho.')
        provider,keys=self.store.batch_mail_settings(batch['id'])
        provider,keys=self.mail_settings(batch['product_ids'],keys,provider=provider)
        pool = (self.m.mail_proxy_page.create_pool() if self.m.chk_use_proxy.isChecked()
            and self.m.chk_mail_via_proxy.isChecked() else None)
        if self.t.store.get_job(batch['id']) is None:
            # This ledger row owns purchase costs; account creation earns no referral beans/revenue.
            # Legacy jobs schema requires requested_beans > 0. This internal marker
            # has target/success 0 and is excluded from requested-bean/customer totals.
            self.t.store.record_job(batch['id'], batch['owner'], 'STOCK_CREATE', 1,
                0, sale_rate_units=0)
        async def execute():
            await purchase_stock_batch(self.store, batch, provider=provider, keys=keys,
                purchase_sink=self.t.store.record_purchase, mail_pool=pool,
                directory=self.store.path.parent/'mail_batches',
                on_wait=lambda _msg:self.log_line.emit(batch['id'], 'Đang chờ shop mua mail cho lô tạo acc…'))
        def failed():
            self.store.batch_update(batch['id'], 'uncertain',
                'Không xác nhận được giao dịch. Đối soát shop/lô đã lưu; không tự mua lại.')
            self.t.store.finish_job(batch['id'],'failed',0)
        self._start(execute,failed)

    def _login(self, row):
        owner = 'stock_create_'+str(row['id'])
        options = dict(self.m._account_lookup_route(owner))
        client = options.pop('proxy_client', None)
        self.store.update(row['id'], status='logging_in', message='Đang tạo/đăng nhập Mochi và lấy mã mời.')
        async def execute():
            lease = None
            outcome = ProxyReleaseOutcome.BUSINESS_FAILURE
            try:
                if client is not None:
                    lease = await client.lease(owner)
                profile = await lookup_account(row['email'], row['password'], owner_id=owner,
                    proxy=lease.proxy if lease else None, **options)
                self.store.update(row['id'], invite_code=profile.invite_code, beans=profile.beans,
                    status='waiting' if row['infinite'] or Decimal(profile.beans)<row['target'] else 'ready',
                    message='Đã tạo acc, lấy mã mời và số dư bean.')
                outcome = ProxyReleaseOutcome.SUCCESS
            except asyncio.CancelledError:
                self.store.update(row['id'], status='creating', message='Chờ tạo tiếp sau khi khởi động lại VPS.')
                raise
            finally:
                if client is not None:
                    if lease is not None:
                        await client.release(lease, outcome=outcome, success=outcome==ProxyReleaseOutcome.SUCCESS)
                    await client.release_job()
        def failed():
            self.store.update(row['id'], status='creation_failed', message='Không đăng nhập/lấy mã được. Sửa kết nối rồi bấm Thử tạo lại lô; không mua mail mới.')
            self._finish_batch(row['batch_id'])
        self._start(execute, failed,lambda _r:self._finish_batch(row['batch_id']))

    def _finish_batch(self, bid):
        rows = [r for r in self.store.all(internal=True) if r['batch_id']==bid]
        if any(r['status'] in ('creating','logging_in') for r in rows):
            return
        batch = next(b for b in self.store.batches() if b['id']==bid)
        success = sum(bool(r['invite_code']) for r in rows)
        state = 'completed' if success==batch['count'] else 'partial'
        self.store.batch_update(bid, state, f'Đã tạo {success}/{batch["count"]} acc. Mail lỗi được giữ nội bộ để thử tạo lại, không mua lại.')
        self.t.store.finish_job(bid, 'completed' if state=='completed' else 'failed', 0,
            failure_count=max(0,len(rows)-success), attempted_count=len(rows))

    def retry_batch(self, bid):
        batch = next((b for b in self.store.batches() if b['id']==bid), None)
        if not batch or batch['state'] not in ('partial','completed'):
            raise ValueError('Chỉ thử lại mail đã giao và đăng nhập lỗi; lô mua chưa rõ phải đối soát trước.')
        rows = [r for r in self.store.all(internal=True) if r['batch_id']==bid and r['status']=='creation_failed']
        if not rows:
            raise ValueError('Lô này không có mail đăng nhập lỗi để thử lại.')
        for r in rows:
            self.store.update(r['id'], status='creating', message='Chờ thử tạo lại từ mail đã mua.')
        self.store.batch_update(bid, 'creating', 'Đang thử lại mail đã mua; không mua thêm.')
        return {'message':'Đã xếp lại '+str(len(rows))+' mail để đăng nhập/tạo acc.'}

    def _dispatch(self, row):
        missing = max(0, row['target']-int(Decimal(row['beans'])))
        points = 1000 if row['infinite'] else ((missing+99)//100)*100
        if not points:
            self.store.update(row['id'], status='ready')
            return
        m = self.m
        def register(jid, code, beans, target, **kwargs):
            kwargs.setdefault('schedule_data',{})['stock_account_id']=row['id']
            self.store.update(row['id'], status='accumulating', job_id=jid, job_base=row['beans'], message='Đang nhập mã theo combo.')
            try:
                created=self.t.store.record_job(jid, row['owner'], code, beans, target,
                    sale_rate_units=0, **kwargs)
                if not created:self.store.update(row['id'],status='failed',job_id=None,message='Mã job bị trùng; chưa chạy hoặc mua mail.')
                return created
            except Exception:
                self.store.update(row['id'], status='failed', job_id=None, message='Chưa ghi được job tích bean.')
                raise
        prior = m.chk_use_mail_batch.isChecked()
        m.chk_use_mail_batch.setChecked(False)
        try:
            jid = m._on_start_clicked(code=row['invite_code'], points=points,
                customer_name='Kho acc #'+str(row['id']), before_start=register,
                creator_name='Kho tài khoản', creator_chat_id=row['owner'] if row['owner']>0 else None,
                combo=True, mail_product_ids=row['product_ids'])
            if not jid:
                self.store.update(row['id'], status='failed', message=m.validation_label.text() or 'Chưa tạo được job tích bean.')
        finally:
            m.chk_use_mail_batch.setChecked(prior)

    def _sync_jobs(self):
        for row in self.store.all():
            if not row['job_id'] or row['status'] in ('exporting','deleted','exported'):
                continue
            job = self.t.store.get_job(row['job_id'])
            if job is None:
                self.store.update(row['id'], status='failed', job_id=None, message='Chưa tìm thấy job tích bean. Kiểm tra trước khi tiếp tục.')
                continue
            # Quota ledger survives a crash before Qt progress/financial callbacks.
            confirmed = self.m.invite_quota_store.job_success_count(row['invite_code'], row['job_id'])
            beans = str(Decimal(row['job_base'])+max(job.success_count,confirmed)*100)
            if job.finalized_at:
                if row['job_id'] in self.m._workers:
                    continue
                if row['status']=='stopping' or job.status=='cancelled':
                    status, next_at = 'paused', None
                elif job.status=='completed' and row['infinite']:
                    status = 'waiting'
                    next_at = (datetime.fromisoformat(job.finalized_at)+timedelta(hours=24,minutes=1)).isoformat()
                else:
                    status, next_at = ('ready' if job.status=='completed' else 'failed'), None
                self.store.update(row['id'], beans=beans, status=status, next_at=next_at, job_id=None,
                    message='Job tích bean: '+job.status)
            elif beans != row['beans']:
                self.store.update(row['id'], beans=beans)

    def stop(self, ids):
        selected_ids(ids)
        rows = {r['id']:r for r in self.store.all()}
        if any(i not in rows or rows[i]['status']=='exporting' for i in ids):
            raise ValueError('Acc không còn trong kho hoặc đang xuất.')
        for i in ids:
            row = rows[i]
            self.store.update(i, status='stopping' if row['job_id'] else 'paused', next_at=None,
                message='Đã yêu cầu dừng nhập mã cho acc.')
            if row['job_id']:
                self.m._stop_job(row['job_id'])
        self._sync_jobs()
        return {'message':'Đã dừng/xếp yêu cầu dừng cho '+str(len(ids))+' acc. Chờ trạng thái Đã dừng trước khi xuất/xóa.'}

    def resume(self, ids):
        selected_ids(ids)
        rows = {r['id']:r for r in self.store.all()}
        if any(i not in rows or rows[i]['status'] not in ('paused','failed','ready') or rows[i]['job_id'] for i in ids):
            raise ValueError('Chỉ tiếp tục acc đã dừng/lỗi và không còn job đang chạy.')
        for i in ids:
            self.store.update(i, status='waiting', message='Chờ tiếp tục tích bean.')
        return {'message':'Đã xếp tiếp tục tích bean cho các acc đã chọn.'}

    def list(self, offset=0):
        rows = self.store.all()
        return dict(self.store.summary(), rows=rows[offset:offset+100], batches=self.store.batches(),
            exports=self.store.exports(), offset=offset, busy=self.worker is not None, error=self.last_error)

    def shutdown(self):
        self.closing = True
        self.timer.stop()
        if self.worker is not None:
            self.worker.request_cancel()
            return True
        return False
