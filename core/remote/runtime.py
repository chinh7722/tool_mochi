"""Compatibility host for the existing, tested Qt job/billing controllers.

No window is shown. Qt's event loop owns the controllers on the VPS, and HTTP
commands are marshalled onto that thread. Clients never own running workers.
"""
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
import html
import json
from pathlib import Path
import re
import uuid

from PySide6.QtCore import QSettings
from core.remote.protocol import RemoteError, integer, text_field
from core.finance import money_units
from core.mail.batch_inventory import load_batch
from core.proxy.models import ProxyModel
from core.job_schedule import schedule_time


class Runtime:
    def __init__(self, directory):
        from gui.pages.proxy import ProxyPage
        from gui.pages.mail_proxy import MailProxyPage
        from gui.pages.mochi_chat import MochiChatPage
        from gui.pages.telegram import TelegramPage
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.settings = QSettings(str(self.directory / 'settings.ini'), QSettings.Format.IniFormat)
        self.proxy = ProxyPage(settings=self.settings)
        self.mail_proxy = MailProxyPage(settings=self.settings)
        self.mochi = MochiChatPage(settings=self.settings, proxy_page=self.proxy)
        self.mochi.mail_proxy_page = self.mail_proxy
        self.telegram = TelegramPage(settings=self.settings, mochi_page=self.mochi)
        self.proxy.proxies_updated.connect(self.mochi._update_proxy_tab_status)
        self.closing = False
        self.log_epoch = uuid.uuid4().hex
        self.mochi.chk_headless.setChecked(True)

    def shutdown(self):
        self.closing = True
        # Evaluate every shutdown, even when the first controller is still busy.
        states = [self.telegram.shutdown_workers(), self.mochi.shutdown_workers(),
                  self.proxy.shutdown_workers(), self.mail_proxy.shutdown_workers()]
        self.settings.sync()
        return any(states)

    def state(self):
        m, t = self.mochi, self.telegram
        jobs = []
        with t.store._connection() as db:
            continued = {r[0] for r in db.execute('SELECT source_id FROM job_continuations')}
        for record in list(m._session_jobs.values())[-500:]:
            jobs.append({key: getattr(record, key, None) for key in (
                'job_id', 'invite_code', 'customer_name', 'creator_name', 'creator_chat_id',
                'status', 'target_count', 'success_count', 'failure_count', 'attempted_count',
                'created_at', 'scheduled_at', 'combo')})
            jobs[-1]['can_continue'] = (record.is_terminal and record.success_count < record.target_count
                and record.job_id not in m._workers and record.job_id not in continued)
        def proxies(page):
            rows = []
            for item in list(page._proxies.values())[:2000]:
                try:
                    endpoint = ProxyModel.from_string(item['endpoint']).server
                except ValueError:
                    endpoint = '(không hợp lệ)'
                rows.append(dict(id=item['id'], endpoint=endpoint, status=item['status'],
                    provider=item['provider'], speed=item.get('speed', ''),
                    error=item.get('error_reason', ''), has_key=bool(item.get('key_xoay'))))
            return rows
        lookup = m.account_lookup_page
        history = m.job_history_store.read()[:500]
        result = dict(version=1, closing=self.closing, jobs=jobs,
            active_jobs=len(m._active_job_ids), queued_jobs=len(m._pending_jobs),
            scheduled_jobs=len(m._scheduled_jobs), job_limit=m.spin_job_concurrency.value(),
            proxies=proxies(self.proxy), mail_proxies=proxies(self.mail_proxy),
            users=[asdict(user) for user in t.store.list_users(include_local=True)],
            customers=t.store.list_customers()[:1000], history=[asdict(row) for row in history],
            telegram=dict(running=t.worker is not None, ready=t._accepting_requests,
                status=t.lbl_status.text(), has_token=bool(t.input_token.text()),
                auto_start=t.chk_auto_start.isChecked(), broadcast=t.lbl_broadcast_status.text(),
                broadcast_running=t._broadcast_id is not None, broadcast_recipient_selection=True),
            sepay=dict(running=t.sepay_page.worker is not None, status=t.sepay_page.status.text(),
                has_token=bool(t.sepay_page.fields['token'].text()),
                enabled=t.sepay_page.auto.isChecked(),
                fields={k:f.text() for k,f in t.sepay_page.fields.items() if k!='token'},
                transactions=t.store.list_sepay_transactions()[:500]),
            lookup=dict(busy=lookup._worker is not None, status=lookup.lbl_status.text(),
                profiles=[asdict(p) for p in lookup._profiles]), settings=self.configuration())
        return result

    def configuration(self):
        m = self.mochi
        return dict(provider=m.combo_provider.currentData(), product_ids=m.input_pids.text(),
            concurrency=m._manual_concurrency, proxy_slots=m.spin_proxy_account_concurrency.value(),
            job_limit=m.spin_job_concurrency.value(), use_proxy=m.chk_use_proxy.isChecked(),
            google_proxy=m.chk_google_via_proxy.isChecked(), mail_proxy=m.chk_mail_via_proxy.isChecked(),
            app_proxy=m.chk_app_login_via_proxy.isChecked(), redeem_proxy=m.chk_redeem_via_proxy.isChecked(),
            has_gmailvip_key=bool(m.input_gmailvip_key.text()),
            has_one_trieu_key=bool(m.input_1trieumail_key.text()),
            sale_rate=str(self.settings.value('finance/sale_rate_per_100', '') or ''))

    def command(self, action, data):
        if self.closing:
            raise RemoteError('VPS đang dừng dịch vụ; không nhận lệnh mới.')
        actions = {name: getattr(self, 'do_' + name.replace('.', '_')) for name in (
            'job.create', 'job.stop', 'job.continue', 'job.logs', 'statistics.get', 'proxy.import', 'proxy.check', 'proxy.rotate',
            'proxy.delete', 'settings.save', 'telegram.save', 'telegram.start', 'telegram.stop',
            'telegram.broadcast', 'telegram.cancel_broadcast', 'user.save', 'user.active',
            'user.mail_keys', 'user.jobs', 'user.report', 'customer.save', 'customer.delete', 'customer.balance',
            'customer.history', 'customer.settle', 'finance.ledger', 'finance.reconcile',
            'finance.price', 'sepay.save', 'sepay.start', 'sepay.stop', 'sepay.reconcile',
            'lookup.start', 'lookup.cancel', 'batch.upload', 'batch.list', 'history.export', 'proxy.export')}
        if action not in actions or not isinstance(data, dict):
            raise RemoteError('Lệnh quản lý không được hỗ trợ.')
        return actions[action](data) or {'message': 'Đã nhận lệnh.'}

    def do_job_create(self, data):
        code = text_field(data, 'code', maximum=64).strip().upper()
        if not re.fullmatch('[A-Z0-9_-]{1,64}', code):
            raise RemoteError('Mã mời không hợp lệ.')
        points = integer(data, 'points', maximum=1_000_000)
        customer = text_field(data, 'customer', maximum=120)
        combo = data.get('combo', False)
        if type(combo) is not bool:
            raise RemoteError('Chế độ combo phải là giá trị bật hoặc tắt.')
        when = data.get('scheduled_at')
        if when:
            when = schedule_time(text_field(data, 'scheduled_at', maximum=80), future=True)
        m = self.mochi
        batch = text_field(data, 'batch', maximum=150)
        if batch:
            if Path(batch).name != batch or not batch.endswith('.json'):
                raise RemoteError('Tên lô mail không hợp lệ.')
            path = self.directory / 'mail_batches' / batch
            if not path.is_file():
                raise RemoteError('Lô mail không có trên VPS.')
            m.input_mail_batch.setText(str(path))
        m.chk_use_mail_batch.setChecked(bool(batch))
        jid = m._on_start_clicked(code=code, points=points, customer_name=customer, scheduled_at=when, combo=combo)
        if not jid:
            raise RemoteError(m.validation_label.text() or 'Chưa tạo được job; kiểm tra cấu hình VPS.')
        self.settings.sync()
        return {'job_id': jid, 'message': 'Đã tạo job ' + jid}

    def do_job_stop(self, data):
        jid = text_field(data, 'job_id', maximum=80)
        if jid not in self.mochi._session_jobs:
            raise RemoteError('Không tìm thấy job trong phiên VPS.')
        self.mochi._stop_job(jid)

    def do_job_logs(self, data):
        jid = text_field(data, 'job_id', maximum=80)
        lines = self.mochi._job_logs.get(jid, [])
        cursor = data.get('cursor')
        if cursor is not None and (type(cursor) is not int or cursor<0):
            raise RemoteError('Vị trí log không hợp lệ.')
        reset = cursor is None or data.get('epoch')!=self.log_epoch or cursor>len(lines)
        start = max(0,len(lines)-1000) if reset else cursor
        end = min(len(lines), start+1000)
        return {'lines': [html.unescape(re.sub('<[^>]+>', '', line)) for line in lines[start:end]],
            'cursor':end,'epoch':self.log_epoch,'reset':reset,'job_id':jid}

    def do_job_continue(self, data):
        jid = text_field(data,'job_id',maximum=80)
        m, store = self.mochi, self.telegram.store
        source = store.get_job(jid)
        if source is None or source.finalized_at is None or jid in m._workers:
            raise RemoteError('Chờ job kết thúc trước khi tiếp tục.')
        child = store.continuation(jid)
        if child:
            return dict(job_id=child,message='Job đã có lần tiếp tục: '+child)
        owner = store.get_user(source.chat_id)
        keys = self.telegram._job_mail_keys(jid)
        remaining = source.target_count-source.success_count
        if remaining<=0 or source.status=='completed':
            raise RemoteError('Job đã đủ bean.')
        saved = json.loads(source.schedule_data or '{}')
        record = m._session_jobs.get(jid)
        customer = record.customer_name if record else saved.get('customer_name','')
        def register(new_id, _code, _points, _target, **options):
            return store.create_continuation(jid,new_id,**options)
        new_id = m._on_start_clicked(code=source.code,points=remaining*100,customer_name=customer,
            creator_name=owner.name,creator_chat_id=source.chat_id if source.chat_id>0 else None,
            before_start=register,mail_api_keys=keys,combo=saved.get('combo') is True,
            scheduled_at=datetime.now().astimezone()+timedelta(seconds=3))
        if not new_id:
            raise RemoteError(m.validation_label.text() or 'Chưa tạo được phần còn thiếu.')
        if source.chat_id>0:
            self.telegram._job_chats[new_id]=(source.bot_fingerprint,source.chat_id)
        return dict(job_id=new_id,message=f'Đã tiếp tục {jid}: tạo {new_id} cho {remaining*100} bean còn thiếu.')

    def do_statistics_get(self, data):
        month = text_field(data,'month',maximum=7)
        owner = data.get('owner_id')
        if owner is not None:
            owner = integer(data,'owner_id',minimum=-1)
        return self.telegram.store.statistics(month,day=data.get('day'),owner_id=owner,
            offset=data.get('offset',0),limit=data.get('limit',100))

    def pool(self, data):
        pool = data.get('pool', 'account')
        if pool not in ('account', 'mail'):
            raise RemoteError('Danh sách proxy không hợp lệ.')
        return self.proxy if pool == 'account' else self.mail_proxy

    def do_proxy_import(self, data):
        lines = text_field(data, 'text', maximum=500000).splitlines()
        provider = data.get('provider', 'proxyvn')
        if provider not in ('proxyvn', 'muaproxyxoay') or len(lines) > 2000:
            raise RemoteError('Nhà cung cấp hoặc số proxy không hợp lệ.')
        ids, duplicates, invalid = self.pool(data)._import_proxy_lines(lines, provider)
        self.settings.sync()
        return dict(message=f'Đã thêm {len(ids)} proxy, trùng {duplicates}, lỗi {len(invalid)} dòng.', invalid=invalid)

    def do_proxy_export(self, data):
        return {'text':'\n'.join(self.pool(data)._serialize_proxy_lines())+'\n'}

    def do_history_export(self, data):
        path=self.mochi.job_history_store.path
        self.mochi.job_history_store.read()
        return {'text':path.read_text(encoding='utf-8-sig') if path.exists() else ''}

    def proxy_item(self, data):
        page = self.pool(data)
        pid = text_field(data, 'id', maximum=80)
        if pid not in page._proxies:
            raise RemoteError('Không tìm thấy proxy.')
        return page, pid

    def do_proxy_check(self, data):
        page = self.pool(data)
        if data.get('id'):
            page, pid = self.proxy_item(data)
            page._check_single_proxy(pid)
        else:
            page._check_proxy_batch(list(page._proxies))

    def do_proxy_rotate(self, data):
        page, pid = self.proxy_item(data)
        page._rotate_single_proxy(pid)

    def do_proxy_delete(self, data):
        page, pid = self.proxy_item(data)
        item = page._proxies[pid]
        if item.get('in_use') or pid in page._active_rotations:
            raise RemoteError('Proxy đang được sử dụng; thử lại khi rảnh.')
        page._delete_single_proxy(pid)

    def do_settings_save(self, data):
        m = self.mochi
        provider = data.get('provider', m.combo_provider.currentData())
        if provider not in ('gmailvip_vn', '1trieumail_com', 'multi_shop'):
            raise RemoteError('Nguồn mail không hợp lệ.')
        # Validate all input before touching live controls.
        numbers = {name:integer(data, name, maximum=limit) for name,limit in
                   (('concurrency',50), ('proxy_slots',50), ('job_limit',100)) if name in data}
        fields = {k:text_field(data,k,maximum=2048) for k in
                  ('gmailvip_key','one_trieu_key','product_ids','sale_rate') if k in data}
        if fields.get('sale_rate'):
            money_units(fields['sale_rate'])
        switches = ('use_proxy','google_proxy','mail_proxy','app_proxy','redeem_proxy')
        if any(type(data[k]) is not bool for k in switches if k in data):
            raise RemoteError('Cấu hình proxy phải là bật/tắt.')
        m.combo_provider.setCurrentIndex(m.combo_provider.findData(provider))
        for key, field in (('gmailvip_key',m.input_gmailvip_key), ('one_trieu_key',m.input_1trieumail_key)):
            if fields.get(key):
                field.setText(fields[key])
        if provider != 'multi_shop':
            chosen = m.input_gmailvip_key if provider=='gmailvip_vn' else m.input_1trieumail_key
            m.input_api_key.setText(chosen.text())
        if 'product_ids' in fields:
            m.input_pids.setText(fields['product_ids'])
        if 'concurrency' in numbers:
            m._manual_concurrency = numbers['concurrency']
        for key,widget in (('proxy_slots',m.spin_proxy_account_concurrency),('job_limit',m.spin_job_concurrency)):
            if key in numbers:
                widget.setValue(numbers[key])
        for key,widget in zip(switches,(m.chk_use_proxy,m.chk_google_via_proxy,m.chk_mail_via_proxy,
                                      m.chk_app_login_via_proxy,m.chk_redeem_via_proxy)):
            if key in data:
                widget.setChecked(data[key])
        m.radio_proxy_tab.setChecked(True)
        m.chk_headless.setChecked(True)
        m._refresh_concurrency_display()
        m._save_workspace_settings()
        # Persist both independent keys, including while a single shop is selected.
        for suffix,widget in (('gmailvip',m.input_gmailvip_key),('1trieumail',m.input_1trieumail_key)):
            self.settings.setValue('runtime/mochi_chat/mail_provider_api_key_'+suffix, widget.text())
        if fields.get('sale_rate'):
            self.telegram.input_sale_rate.setText(fields['sale_rate'])
            self.telegram._save_finance_settings()
        self.settings.sync()
        return {'message': 'Đã lưu cấu hình trên VPS.'}

    def do_telegram_save(self, data):
        t = self.telegram
        token = text_field(data,'token',maximum=256)
        if token:
            from core.telegram_bot import validate_bot_token
            validate_bot_token(token)
            if t.worker is not None:
                raise RemoteError('Tắt bot trước khi đổi token.')
            t.input_token.setText(token)
        if 'auto_start' in data:
            if type(data['auto_start']) is not bool:
                raise RemoteError('auto_start phải là bật/tắt.')
            t.chk_auto_start.setChecked(data['auto_start'])
        if not t._save_config():
            raise RemoteError(t.lbl_status.text())
        self.settings.sync()

    def do_telegram_start(self, data):
        self.telegram._start_bot()
        return {'message': self.telegram.lbl_status.text()}

    def do_telegram_stop(self, data):
        self.telegram._stop_bot()

    def do_telegram_broadcast(self, data):
        from core.telegram_broadcast import validate_announcement
        t = self.telegram
        if t._broadcast_id is not None:
            raise RemoteError('Đang gửi một thông báo khác; chờ hoàn tất hoặc dừng lượt hiện tại.')
        if t._closing or t.worker is None or not t._accepting_requests:
            raise RemoteError('Bật bot và chờ kết nối sẵn sàng trước khi gửi thông báo.')
        recipients = data.get('recipients')
        if 'recipients' in data and not isinstance(recipients,list):
            raise RemoteError('Danh sách người nhận phải là danh sách Chat ID.')
        # Validate the entire selection before changing the editor or creating a
        # campaign. Empty/null/stale selections must never fall back to everyone.
        try:
            t._resolve_broadcast_recipients(recipients)
            text = validate_announcement(text_field(data,'text',maximum=4000))
        except ValueError as exc:
            raise RemoteError(str(exc)) from None
        t.input_broadcast.setPlainText(text)
        if not t._send_broadcast(recipients=recipients):
            raise RemoteError(t.lbl_broadcast_status.text())
        return {'message': t.lbl_broadcast_status.text()}

    def do_telegram_cancel_broadcast(self, data):
        self.telegram._cancel_broadcast()

    def refresh_users(self):
        self.telegram._render_users()
        self.telegram._sync_access()
        self.telegram.customer_page.refresh()
        self.telegram._save_config()

    def do_user_save(self, data):
        chat_id = integer(data,'chat_id')
        role = text_field(data,'role',maximum=20,default='partner')
        if role not in ('admin','partner'):
            raise RemoteError('Vai trò không hợp lệ.')
        existing=self.telegram.store.get_user(chat_id)
        self.telegram.store.upsert_user(chat_id,text_field(data,'name',maximum=120),role,
            active=existing.active if existing is not None else True)
        self.refresh_users()

    def do_user_active(self, data):
        if type(data.get('active')) is not bool:
            raise RemoteError('active phải là bật/tắt.')
        self.telegram.store.set_active(integer(data,'chat_id'),data['active'])
        self.refresh_users()

    def do_user_mail_keys(self, data):
        self.telegram.store.set_user_mail_keys(integer(data,'chat_id'),
            text_field(data,'gmailvip_key',maximum=2048),text_field(data,'one_trieu_key',maximum=2048))

    def do_user_jobs(self, data):
        owner = integer(data,'chat_id',minimum=-1)
        if owner == 0:
            raise RemoteError('Chat ID không hợp lệ.')
        return {'rows': [dict(row) for row in self.telegram.store.list_jobs(owner)][-500:]}

    def do_user_report(self, data):
        owner = integer(data, 'chat_id', minimum=-1)
        if owner == 0:
            raise RemoteError('Chat ID không hợp lệ.')
        kind = text_field(data, 'kind', maximum=20, default='all')
        try:
            return self.telegram.store.user_report(owner, kind=kind)
        except (ValueError, PermissionError) as exc:
            raise RemoteError(str(exc)) from None

    def do_customer_save(self, data):
        self.telegram.store.ensure_customer(integer(data,'chat_id'),text_field(data,'name',maximum=120),restore=True)
        self.telegram.customer_page.refresh()

    def do_customer_delete(self, data):
        self.telegram.store.delete_customer(integer(data,'chat_id'))
        self.telegram.customer_page.refresh()

    def do_customer_balance(self, data):
        self.telegram.store.set_customer_balance(integer(data,'chat_id'),
            money_units(text_field(data,'amount',maximum=40)),text_field(data,'reason',maximum=500),
            expected_units=integer(data,'expected_units',minimum=0))
        self.telegram.customer_page.refresh()

    def do_customer_history(self, data):
        chat_id = integer(data,'chat_id')
        return {'history':[dict(row) for row in self.telegram.store.customer_history(chat_id)],
                'orders':[dict(row) for row in self.telegram.store.customer_orders(chat_id)]}

    def do_customer_settle(self, data):
        jid = text_field(data,'job_id',maximum=80)
        if jid in self.mochi._workers:
            raise RemoteError('Job vẫn có worker trên VPS.')
        job = self.telegram.store.get_job(jid)
        if not job or not self.telegram.store.get_customer_order_for_job(jid):
            raise RemoteError('Không tìm thấy đơn hàng.')
        self.telegram.store.finish_job(jid,'failed',job.success_count)

    def do_finance_ledger(self, data):
        return {'purchases':[dict(row) for row in self.telegram.store.list_purchases()],
                'unpriced':[dict(row) for row in self.telegram.store.list_unpriced_jobs()]}

    def do_finance_reconcile(self, data):
        self.telegram.store.reconcile_purchase(text_field(data,'purchase_id',maximum=150),
            money_units(text_field(data,'amount',maximum=40)))

    def do_finance_price(self, data):
        self.telegram.store.price_job(text_field(data,'job_id',maximum=80),
            money_units(text_field(data,'amount',maximum=40)))

    def do_sepay_save(self, data):
        panel = self.telegram.sepay_page
        if panel.worker is not None:
            raise RemoteError('Tắt đồng bộ trước khi sửa SePay.')
        values = {key:text_field(data,key,maximum=2048) for key in panel.fields if key in data}
        if 'enabled' in data and type(data['enabled']) is not bool:
            raise RemoteError('enabled phải là bật/tắt.')
        for key,value in values.items():
            if key!='token' or value:
                panel.fields[key].setText(value)
        if 'enabled' in data:
            panel.auto.setChecked(data['enabled'])
        if not panel.save():
            raise RemoteError(panel.status.text())

    def do_sepay_start(self, data):
        self.telegram.sepay_page.start()
        return {'message': self.telegram.sepay_page.status.text()}

    def do_sepay_stop(self, data):
        self.telegram.sepay_page.stop()

    def do_sepay_reconcile(self, data):
        self.telegram.store.reconcile_sepay_credit(text_field(data,'transaction_id',maximum=150),
                                                   integer(data,'chat_id'))
        self.telegram.sepay_page.refresh()

    def do_lookup_start(self, data):
        page = self.mochi.account_lookup_page
        if page._worker is not None:
            raise RemoteError('VPS đang lấy mã mời; chờ hoặc hủy lần hiện tại.')
        page.input_email.setText(text_field(data,'email',maximum=320))
        page.input_password.setText(text_field(data,'password',maximum=2048))
        try:
            page._start_lookup()
        finally:
            page.input_password.clear()
        return {'message': page.lbl_status.text()}

    def do_lookup_cancel(self, data):
        self.mochi.account_lookup_page._cancel_lookup()

    def do_batch_upload(self, data):
        # Never accept a filesystem path from a remote machine.
        accounts = data.get('accounts')
        if not isinstance(accounts,list) or not 1<=len(accounts)<=10000:
            raise RemoteError('Lô mail không hợp lệ.')
        directory = self.directory/'mail_batches'
        directory.mkdir(parents=True,exist_ok=True)
        path = directory / ('import_'+uuid.uuid4().hex+'.json')
        try:
            path.write_text(json.dumps({'accounts':accounts},ensure_ascii=False),encoding='utf-8')
            load_batch(path)
        except Exception:
            path.unlink(missing_ok=True)
            raise RemoteError('Không đọc được lô mail hoặc lô không còn tài khoản chưa xử lý.') from None
        return {'batch':path.name,'message':'Đã đưa lô mail lên VPS.'}

    def do_batch_list(self, data):
        return {'batches':[p.name for p in sorted((self.directory/'mail_batches').glob('*.json'))][-1000:]}
