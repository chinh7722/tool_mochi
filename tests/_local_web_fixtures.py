"""Offline VPS API substitute shared by local web transport/browser checks."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from core.remote.client import RemoteClient
from core.remote.local_web import LocalWebServer

TOKEN='offline-web-vps-token-'+'x'*40


def user(chat_id,name,role='partner',active=True):
    return dict(chat_id=chat_id,name=name,role=role,active=active,completed_jobs=1,failed_jobs=0,
        requested_beans=1000,sold_beans=1000,revenue_units=100000,cost_units=10000,
        pending_costs=0,unpriced_jobs=0)


def snapshot():
    return dict(version=1,closing=False,jobs=[dict(job_id='old-job',invite_code='OLDER',customer_name='Khách cũ',
        creator_name='Local',creator_chat_id=None,status='Hoàn thành',target_count=10,success_count=10,
        failure_count=0,attempted_count=10,created_at='2026-11-01T10:00:00+07:00',scheduled_at=None,combo=False)],
        active_jobs=0,queued_jobs=0,scheduled_jobs=0,job_limit=2,proxies=[dict(id='proxy-1',endpoint='http://127.0.0.1:8001',
        status='READY',provider='Proxy.vn',speed='50 ms',error='',has_key=True)],mail_proxies=[],history=[],
        users=[user(-1,'Local'),user(101,'Admin','admin'),user(202,'Partner A'),user(303,'Partner B'),user(404,'Đã thu hồi',active=False)],
        customers=[dict(chat_id=505,name='Khách A',balance_units=123450,total_orders=1,completed_orders=0,failed_orders=0)],
        telegram=dict(running=True,ready=True,status='Bot đang nhận lệnh.',has_token=True,auto_start=True,
            broadcast='Chưa gửi thông báo.',broadcast_running=False,broadcast_recipient_selection=True),
        sepay=dict(running=False,status='SePay chưa bật.',has_token=False,enabled=False,
            fields=dict(bank_id='',account_number='',bank_name='',account_name=''),transactions=[]),
        lookup=dict(busy=False,status='Chưa tra cứu.',profiles=[]),settings=dict(provider='gmailvip_vn',product_ids='3445',
            concurrency=3,proxy_slots=1,job_limit=2,use_proxy=False,google_proxy=True,mail_proxy=True,
            app_proxy=True,redeem_proxy=True,has_gmailvip_key=True,has_one_trieu_key=True,sale_rate='1500'))


class FakeVPS:
    def __init__(self):
        self.value=snapshot()
        self.commands=[]
        self.seen={}
        self.offline=False
        self.log_lines=['Đang xử lý job fixture-job','<log is plain text>']
        self.temporary_rows=[dict(job_id='temp-failed',code='TEMP',name='Partner A',chat_id=202,
            status='failed',target_count=10,success_count=2,can_continue=True,finalized_at='2026-11-04T09:00:00+07:00')]

    def factory(self,url,token):
        RemoteClient(url,token)  # Validate actual connection constraints without connecting.
        return self

    def state(self):
        if self.offline:
            raise RuntimeError('offline injected; never display private token '+TOKEN)
        return {'ok':True,'state':deepcopy(self.value)}

    def command(self,action,payload,*,request_id):
        if request_id in self.seen:
            return self.seen[request_id]
        self.commands.append((action,deepcopy(payload),request_id))
        result={'message':'Đã thực hiện trên VPS.'}
        if action=='job.create':
            jid='web-job-'+str(len(self.commands))
            self.value['jobs'].append(dict(job_id=jid,invite_code=payload['code'],customer_name=payload.get('customer',''),
                creator_name='Local',creator_chat_id=None,status='Chờ lượt combo' if payload.get('combo') else 'Đang chờ',
                target_count=(payload['points']+99)//100,success_count=0,failure_count=0,attempted_count=0,
                created_at=datetime.now(timezone.utc).isoformat(),scheduled_at=payload.get('scheduled_at'),combo=payload.get('combo',False)))
            result['job_id']=jid
        elif action=='job.logs':
            cursor=payload.get('cursor')
            reset=cursor is None or payload.get('epoch')!='fixture-epoch'
            start=0 if reset else cursor
            result.update(lines=self.log_lines[start:],cursor=len(self.log_lines),epoch='fixture-epoch',reset=reset)
        elif action=='statistics.get':
            from calendar import monthrange
            month=payload['month'];year,number=map(int,month.split('-'))
            days=[dict(day=f'{month}-{d:02}',jobs=1 if d==4 else 0,customers=2 if d==4 else 0,unique_customers=1 if d==4 else 0,
                revenue_units=300000,cost_units=20000,profit_units=280000,pending_jobs=0) for d in range(1,monthrange(year,number)[1]+1)]
            selected=payload.get('owner_id')
            owners=[dict(chat_id=202,name='Partner A'),dict(chat_id=303,name='Partner B')]
            owners=[o for o in owners if selected is None or str(o['chat_id'])==str(selected)]
            rows=[dict(job_id='statistics-'+str(o['chat_id']),chat_id=o['chat_id'],name=o['name'],code='CODE'+str(o['chat_id']),
                status='completed',success_count=3,revenue_units=300000,cost_units=20000,profit_units=280000,
                finalized_at=f'{month}-04T09:00:00+07:00',finished_at=f'{month}-04T09:00:00+07:00') for o in owners]
            result=dict(month=month,days=days,owners=[dict(chat_id=202,name='Partner A'),dict(chat_id=303,name='Partner B')],
                customer_counts=dict(today=2,week=3,month=4),reasons=[dict(reason='Google lỗi',count=1)],
                users=[dict(**o,jobs=1,revenue_units=300000,cost_units=20000,profit_units=280000,pending_jobs=0) for o in owners],
                history=rows,customer_history=rows,total=len(rows),customer_total=len(rows),note='Offline statistics fixture',
                code_history=[dict(day=month+'-04',code='ONECODE',runs=2,owners=owners)],code_total=1)
        elif action=='history.temporary':result=dict(rows=deepcopy(self.temporary_rows),total=len(self.temporary_rows))
        elif action=='history.temporary.clear':self.temporary_rows=[]
        elif action=='pricing.save':
            self.value['settings']['prices']=deepcopy(payload['prices'])
            result=dict(prices=deepcopy(payload['prices']),message='Đã lưu bảng giá.')
        elif action=='job.continue':result['job_id']='continued-temp'
        elif action=='telegram.broadcast': self.value['telegram']['broadcast_running']=True
        elif action=='telegram.cancel_broadcast': self.value['telegram']['broadcast_running']=False
        elif action=='batch.list': result['batches']=['paid_mail.json']
        elif action=='batch.upload': result['batch']='import_mail.json'
        elif action in ('proxy.export','history.export'): result['text']='offline export\n'
        elif action=='user.save': self.value['users'].append(user(payload['chat_id'],payload['name'],payload['role']))
        elif action=='user.jobs': result['rows']=[dict(job_id='owned-job',code='ABC',status='completed',success_count=3)]
        elif action=='user.report':
            owner=next(r for r in self.value['users'] if str(r['chat_id'])==str(payload['chat_id']))
            result=deepcopy(getattr(self,'report_override',None)) or dict(owner=deepcopy(owner),
                generated_at='2026-11-03T09:30:00+07:00',files=[
                    dict(filename='hoshino_'+str(owner['chat_id'])+'_jobs.csv',kind='jobs',row_count=1,
                        text='\ufeffChat ID,Mã job,Bean\r\n'+str(owner['chat_id'])+',offline-job,300\r\n'),
                    dict(filename='hoshino_'+str(owner['chat_id'])+'_purchases.csv',kind='purchases',row_count=1,
                        text='\ufeffChat ID,Mã giao dịch,Số mail\r\n'+str(owner['chat_id'])+',offline-purchase,3\r\n')])
        elif action=='customer.history': result.update(history=[],orders=[])
        elif action=='finance.ledger': result.update(purchases=[],unpriced=[])
        elif action=='lookup.start': self.value['lookup']['busy']=True
        response={'ok':True,'result':result}
        self.seen[request_id]=response
        return response


class WebFixture:
    def __init__(self):
        self.temp=tempfile.TemporaryDirectory()
        self.connection=Path(self.temp.name)/'connection.json'
        self.connection.write_text(json.dumps(dict(url='http://127.0.0.1:8765',token=TOKEN)),encoding='utf-8')
        self.fake=FakeVPS()
        self.server=LocalWebServer(ROOT,port=0,client_factory=self.fake.factory,connection_path=self.connection)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()
