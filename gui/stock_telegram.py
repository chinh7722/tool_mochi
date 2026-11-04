"""Admin-only stock commands, including recoverable text exports."""
import json


def status_label(status):
    return 'Tạm dừng ưu tiên khách' if status=='priority_wait' else status


def stock_control(page, worker, request, actor):
    if actor.role!='admin':
        worker.reply(actor.chat_id,'Chỉ admin đang được cấp quyền mới được quản lý kho acc.',request.update_id)
        return
    controller=page.mochi_page.stock_controller
    store=controller.store
    data=request.stock_data
    action=request.action.removeprefix('stock_')
    try:
        if action=='mailapi':
            if 'mail_keys' in data:store.set_creation_keys(actor.chat_id,data['mail_keys'])
            keys=store.get_creation_keys(actor.chat_id)
            from core.mail.providers.priority import GMAILVIP,ONE_TRIEU
            text=('API riêng mua mail tạo acc: GmailVip '+('đã nhập' if keys[GMAILVIP] else 'chưa nhập')+
                ' · 1TrieuMail '+('đã nhập' if keys[ONE_TRIEU] else 'chưa nhập')+
                '.\nCó thể dùng cùng key với /mailapi. Key ở đây áp dụng cho mua mail tạo acc; mail nhập mã mời dùng /mailapi của bạn.')
        elif action=='create':
            keys=store.get_creation_keys(actor.chat_id)
            if not any(keys.values()):raise ValueError('Dùng /acc_mailapi KEY_GMAILVIP KEY_1TRIEUMAIL trước khi tạo acc; dùng - cho shop không dùng.')
            result=controller.create(**data,owner=actor.chat_id,
                request_key=f'{worker.fingerprint}:{request.update_id}',mail_keys=keys)
            text=result['message']
        elif action=='list':
            rows=store.all();start=(data['page']-1)*10
            text=f"Kho acc · {len(rows)} acc · {store.summary()['beans']} bean · Trang {data['page']}\n"
            text+='\n'.join(f"#{r['id']} · {r['email']} · {r['beans']} bean · {status_label(r['status'])}" for r in rows[start:start+10])
            text+='\n/acc_stop STT · /acc_resume STT · /acc_export STT1,STT2\n/acc_exports: lịch sử xuất'
        elif action in ('stop','resume'):
            text=getattr(controller,action)(data['ids'])['message']
        elif action=='info':
            row=next((r for r in store.all() if r['id']==data['ids'][0]),None)
            if row is None:raise ValueError('Acc không còn trong kho.')
            target='Vô hạn, 10 lượt/ngày' if row['infinite'] else str(row['target'])+' bean'
            text=(f"Acc #{row['id']}\nEmail: {row['email']}\nMật khẩu: {row['password']}\n"
                f"Bean: {row['beans']}\nMục tiêu: {target}\nMã mời: {row['invite_code']}\nTrạng thái: {status_label(row['status'])}")
        elif action=='batches':
            start=(data['page']-1)*10
            text='Tiến độ tạo acc · Trang '+str(data['page'])+'\n'
            text+='\n'.join(f"{r['id']} · {r['count']} acc · {r['state']}\n{r['message']}" for r in store.batches()[start:start+10])
            text+='\n/acc_retry MÃ_LÔ: thử lại mail đã mua nhưng đăng nhập lỗi'
        elif action=='delete':
            text=f"Đã xóa {store.delete(data['ids'])} acc khỏi kho. Lịch sử job và giao dịch được giữ."
        elif action=='retry':
            text=controller.retry_batch(data['id'])['message']
        elif action=='done':
            text=store.finish_export(data['id'])['message']
        elif action=='exports':
            rows=store.exports((data['page']-1)*10,10)
            text='Lịch sử xuất acc · Trang '+str(data['page'])+'\n'
            text+='\n'.join(f"{r['id']} · {r['count']} acc · {r['state']} · {r['created_at']}" for r in rows)
            text+='\n/acc_show MÃ_PHIẾU: tải lại\n/acc_done MÃ_PHIẾU: xác nhận đã nhận, đưa acc khỏi kho'
        elif action in ('export','show'):
            result=store.prepare_export(data['ids'],actor.chat_id) if action=='export' else store.export(data['id'])
            def authorize(actor_id,_owner):
                current=page.store.get_user(actor_id)
                return bool(current and current.active and not current.is_local and current.role=='admin')
            report={'owner':{'chat_id':actor.chat_id},'files':[{'kind':'stock_accounts',
                'filename':'accounts_'+result['id']+'.txt','text':result['content'],
                'row_count':len(json.loads(result['account_ids']))}]}
            if not worker.send_report(actor.chat_id,report,request.update_id,target_id=actor.chat_id,
                    admin_only=True,authorize=authorize):
                raise ValueError('Chưa xếp được file vào hàng đợi. Phiếu đã lưu; dùng /acc_show '+result['id'])
            text='Phiếu xuất '+result['id']+' đã lưu. File mỗi acc một dòng mail|mật khẩu.\n'
            text+='Sau khi nhận file, dùng /acc_done '+result['id']+' để hoàn tất xuất và đưa acc khỏi kho.'
        else:
            raise ValueError('Lệnh kho acc không hợp lệ.')
    except (ValueError,PermissionError) as exc:
        text=str(exc)
    except Exception:
        text='Chưa xử lý được kho acc. Dữ liệu và phiếu xuất đã lưu vẫn được giữ; kiểm tra VPS.'
    worker.reply(actor.chat_id,text,request.update_id,admin_only=True)
