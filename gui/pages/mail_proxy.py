"""Separate rotating proxies used only by mail purchase transactions."""
from pathlib import Path
import json
from PySide6.QtCore import QSettings
from core.mail.proxy_pool import MailProxyPool,mail_proxy_snapshot,shutdown_mail_rotations,acknowledge_mail_proxy_check
from gui.pages.proxy import ProxyPage


class MailProxyPage(ProxyPage):
    def __init__(self,settings=None,parent=None):
        super().__init__(settings,parent,settings_prefix='mail_proxy',title='Proxy mua mail',
            subtitle='Mỗi lần mua xoay IP; dùng proxy sẵn sàng trước, chỉ chờ khi tất cả đều đang xoay. Cần endpoint|key_xoay.',
            usage_hint='Mỗi proxy xử lý 1 lượt mua tại một thời điểm; mua xong xoay IP.')
        self.btn_free_proxy.hide()

    def _parse_proxy_line(self,raw,provider_id=None):
        item=super()._parse_proxy_line(raw,provider_id)
        return item if item and item.get('key_xoay') else None

    def create_pool(self,on_wait=None):
        base=Path(__file__).resolve().parents[2]
        if base.name=='src':
            base=base.parent
        directory=(Path(self.settings.fileName()).parent if self.settings.format()==QSettings.Format.IniFormat
            else base/'data')
        return MailProxyPool(self.get_active_proxies(),state_directory=directory/'mail_proxy_state',on_wait=on_wait)

    def _refresh_runtime_status(self):
        before={pid:item['endpoint'] for pid,item in self._proxies.items()}
        super()._refresh_runtime_status()
        for item in self._proxies.values():
            state=mail_proxy_snapshot(self._item_key_reference(item))
            if state is None:
                continue
            item['endpoint']=state['proxy'].to_url()
            item['in_use']=state['in_use']
            if state['failed']:
                item.update(is_failed=True,status='FAILED',error_reason='Nhà cung cấp từ chối xoay IP; kiểm tra key/gói proxy.')
            elif state['in_use']:
                item['status']='RUNNING'
            elif state['rotating'] or state['required']:
                item['status']='COOLING'
                item['speed']='Chờ IP mới'
            else:
                item['status']='READY'
        self._update_metrics_and_render()
        if any(before[pid]!=item['endpoint'] for pid,item in self._proxies.items()):
            self.settings.setValue('mail_proxy/managed_list',json.dumps(list(self._proxies.values()),ensure_ascii=False))

    def shutdown_workers(self):
        gui_busy=super().shutdown_workers()
        rotations_busy=shutdown_mail_rotations()
        return gui_busy or rotations_busy

    def _on_rotate_finished(self,pid,result,latency_ms):
        actual_rotation=pid in self._active_rotations
        super()._on_rotate_finished(pid,result,latency_ms)
        item=self._proxies.get(pid)
        if item and result.success and result.proxy and not result.is_cooling:
            try:
                acknowledge_mail_proxy_check(self._item_key_reference(item),result.proxy,rotated=actual_rotation)
            except OSError:
                item['error_reason']='Chưa lưu được trạng thái IP mới; kiểm tra quyền ghi dữ liệu.'
