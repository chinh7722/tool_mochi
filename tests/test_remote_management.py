"""Offline authorization, replay protection, source packaging and Qt integration."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

from core.remote.client import RemoteClient
from core.remote.protocol import MutationJournal,RemoteError,encode
from core.remote.server import ManagementServer


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'ops.sqlite3'
        self.journal=MutationJournal(self.path)
        self.request_id=str(uuid.uuid4())

    def tearDown(self):
        self.temp.cleanup()

    def test_replay_after_reopen_does_not_repeat_purchase_or_job(self):
        calls=[]
        result=self.journal.execute(self.request_id,'job.create',{'code':'TEST'},lambda:(calls.append(1) or {'job_id':'one'}))
        replay=MutationJournal(self.path).execute(self.request_id,'job.create',{'code':'TEST'},lambda:calls.append(2))
        self.assertEqual(result,replay)
        self.assertEqual(calls,[1])

    def test_collision_rejected(self):
        self.journal.execute(self.request_id,'a',{},lambda:{})
        with self.assertRaises(RemoteError):
            self.journal.execute(self.request_id,'b',{},lambda:{})

    def test_interrupted_mutation_is_never_automatically_replayed(self):
        def interrupted():
            raise OSError('simulated crash after side effect')
        with self.assertRaises(OSError):
            self.journal.execute(self.request_id,'job.create',{},interrupted)
        with self.assertRaises(RemoteError):
            MutationJournal(self.path).execute(self.request_id,'job.create',{},lambda:self.fail('must not replay'))

    def test_no_password_or_api_key_body_saved(self):
        self.journal.execute(self.request_id,'lookup.start',{'password':'private-test-password','token':'private-test-key'},lambda:{'ok':True})
        raw=self.path.read_bytes()
        self.assertNotIn(b'private-test-password',raw)
        self.assertNotIn(b'private-test-key',raw)

    def test_invalid_identity_rejected_before_callback(self):
        for bad in ('',None,'../x','other'):
            with self.subTest(bad=bad),self.assertRaises(RemoteError):
                self.journal.execute(bad,'job.create',{},lambda:self.fail('must not execute'))


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.commands=[]
        def submit(command):
            self.commands.append(command)
            command.result={'ok':True,'state':{'version':1}} if command.action=='state' else {'ok':True,'result':{'done':True}}
            command.done.set()
        self.server=ManagementServer(('127.0.0.1',0),'t'*48,submit)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.client=RemoteClient(self.url,'t'*48)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def test_authenticated_read_and_command(self):
        self.assertEqual(self.client.state()['state']['version'],1)
        self.assertTrue(self.client.command('job.stop',{'job_id':'test'})['result']['done'])
        self.assertEqual(len(self.commands),2)
        uuid.UUID(self.commands[-1].request_id)

    def test_wrong_token_has_no_side_effect(self):
        with self.assertRaises(RemoteError):
            RemoteClient(self.url,'x'*48).command('job.create',{})
        self.assertEqual(self.commands,[])

    def test_environment_proxies_cannot_redirect_local_token(self):
        with patch.dict(os.environ,{'HTTP_PROXY':'http://127.0.0.1:1','NO_PROXY':''}):
            self.assertTrue(RemoteClient(self.url,'t'*48).state()['ok'])

    def test_unknown_endpoint_rejected(self):
        with self.assertRaises(RemoteError):
            self.client.request('/v1/state?secret=bad')
        self.assertEqual(self.commands,[])

    def test_non_loopback_urls_rejected(self):
        for url in ('http://example.com','http://127.0.0.1.evil','http://u:p@localhost','http://localhost/other'):
            with self.subTest(url=url),self.assertRaises(RemoteError):
                RemoteClient(url,'t'*48)

    def test_browser_origin_rejected(self):
        from urllib.request import Request,build_opener,ProxyHandler
        from urllib.error import HTTPError
        req=Request(self.url+'/v1/state',headers={'Authorization':'Bearer '+'t'*48,'Origin':'https://example.com'})
        try:
            build_opener(ProxyHandler({})).open(req)
            self.fail('Origin must be rejected')
        except HTTPError as exc:
            self.assertEqual(exc.code,400)
            exc.close()
        self.assertEqual(self.commands,[])


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        from core.remote.runtime import Runtime
        self.temp=tempfile.TemporaryDirectory()
        self.environment=patch.dict(os.environ,{'HOSHINO_DATA_DIR':self.temp.name})
        self.environment.start()
        self.runtime=Runtime(self.temp.name)

    def tearDown(self):
        self.runtime.shutdown()
        self.app.processEvents()
        for page in (self.runtime.telegram,self.runtime.mochi,self.runtime.proxy,self.runtime.mail_proxy):
            page.deleteLater()
        self.app.processEvents()
        self.environment.stop(); self.temp.cleanup()

    def test_empty_state_is_serializable_and_has_local_user(self):
        state=self.runtime.state()
        self.assertTrue(encode(state))
        self.assertEqual(state['version'],1)
        self.assertEqual(state['users'][0]['chat_id'],-1)

    def test_safe_settings_and_secrets_survive_restart(self):
        r=self.runtime
        r.command('settings.save',dict(provider='multi_shop',gmailvip_key='private-gmail-key',
            one_trieu_key='private-trieu-key',product_ids='gmailvip:123,1trieumail:456',
            concurrency=3,proxy_slots=2,job_limit=4,use_proxy=True,google_proxy=False,
            mail_proxy=True,app_proxy=True,redeem_proxy=True,sale_rate='1500'))
        state=r.state()
        encoded=encode(state)
        self.assertNotIn(b'private-gmail-key',encoded)
        self.assertNotIn(b'private-trieu-key',encoded)
        self.assertEqual(state['job_limit'],4)
        self.assertTrue(state['settings']['has_gmailvip_key'])
        self.assertEqual(r.settings.value('runtime/mochi_chat/mail_provider_api_key_1trieumail'),'private-trieu-key')

    def test_invalid_settings_do_not_change_configuration(self):
        before=self.runtime.configuration()
        with self.assertRaises(RemoteError):
            self.runtime.command('settings.save',{'provider':'multi_shop','job_limit':0})
        self.assertEqual(before,self.runtime.configuration())

    def test_missing_api_job_is_rejected_without_workers(self):
        with self.assertRaises(RemoteError):
            self.runtime.command('job.create',{'code':'TEST','points':100})
        self.assertFalse(self.runtime.mochi._workers)

    def test_schedule_and_cancel_persist_without_purchase(self):
        from datetime import datetime,timedelta,timezone
        when=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        result=self.runtime.command('job.create',{'code':'TEST','points':100,'scheduled_at':when})
        jid=result['job_id']
        self.assertEqual(self.runtime.telegram.store.get_job(jid).dispatch_state,'scheduled')
        self.assertFalse(self.runtime.mochi._workers)
        self.runtime.command('job.stop',{'job_id':jid})
        self.assertEqual(self.runtime.telegram.store.get_job(jid).status,'cancelled')
        csv_text=self.runtime.command('history.export',{})['text']
        self.assertIn(jid,csv_text)

    def test_combo_option_requires_real_boolean_before_creating_job(self):
        for value in ('true',1,None,[],{}):
            with self.subTest(value=value),patch.object(self.runtime.mochi,'_on_start_clicked') as create:
                with self.assertRaises(RemoteError):
                    self.runtime.command('job.create',{'code':'TEST','points':5500,'combo':value})
                create.assert_not_called()

    def test_combo_mode_reaches_controller_without_changing_target(self):
        with patch.object(self.runtime.mochi,'_on_start_clicked',return_value='combo-test') as create:
            result=self.runtime.command('job.create',{'code':'TEST','points':5500,'combo':True})
        self.assertEqual(result['job_id'],'combo-test')
        create.assert_called_once_with(code='TEST',points=5500,customer_name='',scheduled_at=None,combo=True)

    def test_scheduled_combo_survives_runtime_reconstruction(self):
        from datetime import datetime,timedelta,timezone
        from core.remote.runtime import Runtime
        when=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        jid=self.runtime.command('job.create',dict(code='COMBO',points=5500,combo=True,scheduled_at=when))['job_id']
        self.assertFalse(self.runtime.mochi._workers)
        self.runtime.shutdown()
        restored=Runtime(self.temp.name)
        try:
            record=next(row for row in restored.state()['jobs'] if row['job_id']==jid)
            self.assertTrue(record['combo'])
            self.assertEqual(record['target_count'],55)
            self.assertIsNotNone(record['scheduled_at'])
            self.assertFalse(restored.mochi._workers)
            restored.command('job.stop',{'job_id':jid})
            self.assertEqual(restored.telegram.store.get_job(jid).status,'cancelled')
        finally:
            restored.shutdown()
            for page in (restored.telegram,restored.mochi,restored.proxy,restored.mail_proxy):
                page.deleteLater()
            self.app.processEvents()

    def test_proxy_import_is_independent_and_hides_credentials(self):
        r=self.runtime
        r.command('proxy.import',dict(pool='account',text='127.0.0.1:8888:user:private-proxy-pass|test-key'))
        self.assertEqual(len(r.state()['proxies']),1)
        self.assertEqual(len(r.state()['mail_proxies']),0)
        self.assertNotIn(b'private-proxy-pass',encode(r.state()))
        exported=r.command('proxy.export',{'pool':'account'})['text']
        self.assertIn('private-proxy-pass',exported)
        self.assertIn('test-key',exported)

    def test_customer_balance_uses_existing_concurrent_update_guard(self):
        r=self.runtime
        r.command('customer.save',{'chat_id':777})
        r.command('customer.balance',{'chat_id':777,'amount':'10000','reason':'test adjustment','expected_units':0})
        with self.assertRaises(ValueError):
            r.command('customer.balance',{'chat_id':777,'amount':'20000','reason':'stale edit','expected_units':0})
        self.assertEqual(r.telegram.store.get_customer(777)['balance_units'],1000000)

    def test_user_keys_do_not_enter_snapshot(self):
        r=self.runtime
        r.command('user.save',{'chat_id':444,'name':'Partner','role':'partner'})
        r.command('user.mail_keys',{'chat_id':444,'gmailvip_key':'private-gmail-key','one_trieu_key':'private-trieu-key'})
        self.assertNotIn(b'private-gmail-key',encode(r.state()))
        self.assertEqual(r.telegram.store.get_user_mail_keys(444)['gmailvip_vn'],'private-gmail-key')

    def test_editing_revoked_user_does_not_restore_access(self):
        r=self.runtime
        r.command('user.save',{'chat_id':444,'name':'Partner','role':'partner'})
        r.command('user.active',{'chat_id':444,'active':False})
        r.command('user.save',{'chat_id':444,'name':'Renamed','role':'admin'})
        self.assertFalse(r.telegram.store.get_user(444).active)

    def test_uploaded_batch_has_safe_server_path_and_is_reusable(self):
        r=self.runtime
        response=r.command('batch.upload',{'accounts':[{'email':'test@example.com','password':'private-batch-pass'}]})
        path=Path(self.temp.name)/'mail_batches'/response['batch']
        self.assertTrue(path.exists())
        self.assertEqual(r.command('batch.list',{})['batches'],[path.name])
        self.assertNotIn(b'private-batch-pass',encode(r.state()))
        with self.assertRaises(RemoteError):
            r.command('job.create',{'code':'TEST','points':100,'batch':'../../file.json'})

    def test_batch_default_directory_obeys_service_configuration(self):
        from core.mail.batch_inventory import _new_batch_path
        self.assertEqual(_new_batch_path('x').parent,Path(self.temp.name)/'mail_batches')

    def test_local_user_cannot_be_revoked(self):
        with self.assertRaises((ValueError,RemoteError)):
            self.runtime.command('user.active',{'chat_id':-1,'active':False})

    def test_unknown_command_rejected(self):
        with self.assertRaises(RemoteError):
            self.runtime.command('__import__',{})

    def test_remote_window_only_creates_management_client(self):
        from gui.remote_window import RemoteMainWindow
        with patch('core.remote.runtime.Runtime',side_effect=AssertionError('No local workers')):
            window=RemoteMainWindow()
            self.assertEqual(window.pages.count(),6)
            window.render({'state':self.runtime.state()})
            self.assertEqual(window.users.rowCount(),1)
            self.assertIsNone(window.client)
            window.close()


class ServiceProcessTests(unittest.TestCase):
    def test_service_survives_closed_manager_and_restores_schedule_after_restart(self):
        import socket
        import subprocess
        import time
        from datetime import datetime,timedelta,timezone
        from PySide6.QtWidgets import QApplication
        from gui.remote_window import RemoteMainWindow
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as listener:
                listener.bind(('127.0.0.1',0))
                port=listener.getsockname()[1]
            environment={**os.environ,'HOSHINO_API_TOKEN':'offline-integration-token-'+'t'*40,
                         'HOSHINO_DATA_DIR':directory,'QT_QPA_PLATFORM':'offscreen'}
            client=RemoteClient(f'http://127.0.0.1:{port}',environment['HOSHINO_API_TOKEN'],timeout=2)
            def stop(process):
                if process.poll() is None:
                    if os.name=='nt':
                        # Windows venv launchers may spawn the actual interpreter.
                        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,check=False)
                    else:
                        process.terminate()
                    process.wait(timeout=15)
            def start():
                process=subprocess.Popen([sys.executable,'-I',str(ROOT/'main_mochi_server.py'),
                    '--data-dir',directory,'--port',str(port)],env=environment,
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    if process.poll() is not None:
                        self.fail(process.stdout.read().decode('utf-8','replace'))
                    try:
                        client.state()
                        return process
                    except RemoteError:
                        time.sleep(0.1)
                stop(process)
                self.fail('Runtime startup timeout')
            process=start()
            try:
                request_id=str(uuid.uuid4())
                payload={'code':'OFFLINE','points':100,'scheduled_at':
                    (datetime.now(timezone.utc)+timedelta(days=10)).isoformat()}
                result=client.command('job.create',payload,request_id=request_id)
                jid=result['result']['job_id']
                window=RemoteMainWindow()
                window.render(client.state()); window.close(); app.processEvents()
                self.assertIsNone(process.poll())
                self.assertEqual(client.state()['state']['scheduled_jobs'],1)
                duplicate=subprocess.run([sys.executable,'-I',str(ROOT/'main_mochi_server.py'),
                    '--data-dir',directory,'--port',str(port+1)],env=environment,
                    stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=10)
                self.assertNotEqual(duplicate.returncode,0)
                stop(process); process.stdout.close()
                process=start()
                state=client.state()['state']
                self.assertEqual(state['scheduled_jobs'],1)
                self.assertEqual(client.command('job.create',payload,request_id=request_id)['result']['job_id'],jid)
                self.assertEqual(client.state()['state']['scheduled_jobs'],1)
                client.command('job.stop',{'job_id':jid})
                self.assertEqual(client.state()['state']['scheduled_jobs'],0)
            finally:
                stop(process)
                process.stdout.close()


if __name__=='__main__':
    unittest.main()
