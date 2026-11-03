"""Local web security boundary and lossless forwarding; no operational VPS calls."""
import json
from pathlib import Path
import sys
import unittest
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from _local_web_fixtures import WebFixture,TOKEN


class LocalWebServerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=WebFixture()
        self.addCleanup(self.fixture.close)
        self.server=self.fixture.server
        self.cookie=f'{self.server.cookie_name}={self.server.session}'
        self.opener=build_opener(ProxyHandler({}))

    def request(self,path='/',payload=None,*,authenticated=True,csrf=True,headers=None,method=None):
        fields={'Cookie':self.cookie} if authenticated else {}
        if payload is not None:
            fields['Content-Type']='application/json'
            if csrf: fields['X-Web-CSRF']=self.server.csrf
        fields.update(headers or {})
        req=Request(self.fixture.base+path,data=None if payload is None else json.dumps(payload).encode(),headers=fields,method=method)
        try:
            response=self.opener.open(req,timeout=3)
        except HTTPError as exc:
            response=exc
        with response:
            raw=response.read()
            return response.status,response.headers,raw

    def post(self,payload):
        return self.request('/api/command',{'request_id':str(uuid.uuid4()),**payload})

    def test_listener_and_static_assets_are_local_and_self_contained(self):
        self.assertEqual(self.server.server_address[0],'127.0.0.1')
        for path in ('/','/web/app.js','/web/style.css','/branding/logo.png'):
            with self.subTest(path=path):
                status,headers,raw=self.request(path,authenticated=False)
                self.assertEqual(status,200)
                self.assertTrue(raw)
                self.assertIn("connect-src 'self'",headers['Content-Security-Policy'])
                self.assertIn('no-store',headers['Cache-Control'])
                self.assertNotIn(TOKEN.encode(),raw)

    def test_bootstrap_sets_http_only_cookie_without_exposing_vps_token(self):
        code,headers,body=self.request('/api/session',{},authenticated=False,csrf=False,
            headers={'X-Web-Bootstrap':self.server.bootstrap,'Origin':self.fixture.base})
        self.assertEqual(code,200)
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        self.assertNotIn(TOKEN.encode(),body)

    def test_wrong_bootstrap_and_unauthenticated_api_cannot_read_or_write(self):
        self.assertEqual(self.request('/api/session',{},authenticated=False,headers={'X-Web-Bootstrap':'wrong'})[0],403)
        self.assertEqual(self.request('/api/state',authenticated=False)[0],401)
        self.assertEqual(self.request('/api/command',{},authenticated=False)[0],401)
        self.assertEqual(self.fixture.fake.commands,[])

    def test_csrf_origin_host_and_preflight_are_enforced(self):
        payload=dict(action='job.stop',payload={'job_id':'test'},request_id=str(uuid.uuid4()))
        for headers in ({'Origin':'https://unrelated.invalid'},{'Host':'attacker.invalid'},
                {'Origin':'null'},{'Sec-Fetch-Site':'cross-site'}):
            with self.subTest(headers=headers): self.assertEqual(self.request('/api/command',payload,headers=headers)[0],403)
        self.assertEqual(self.request('/api/command',payload,csrf=False)[0],403)
        self.assertEqual(self.request('/api/command',method='OPTIONS')[0],405)
        self.assertFalse(self.fixture.fake.commands)

    def test_config_and_state_never_send_the_management_token_to_browser(self):
        for route in ('/api/config','/api/state'):
            code,_,body=self.request(route)
            self.assertEqual(code,200)
            self.assertNotIn(TOKEN.encode(),body)
        config=json.loads(self.request('/api/config')[2])
        self.assertTrue(config['has_token'])
        self.assertNotIn('token',config)

    def test_command_identity_and_selected_recipients_forward_unchanged(self):
        identifier=str(uuid.uuid4())
        payload=dict(action='telegram.broadcast',payload={'text':'Notice','recipients':[202]},request_id=identifier)
        self.assertEqual(self.request('/api/command',payload)[0],200)
        self.assertEqual(self.request('/api/command',payload)[0],200)
        self.assertEqual(self.fixture.fake.commands,[('telegram.broadcast',{'text':'Notice','recipients':[202]},identifier)])

    def test_large_chat_ids_and_wallet_cas_are_lossless(self):
        large=2**63-2
        self.fixture.fake.value['customers'][0]['chat_id']=large
        self.fixture.fake.value['customers'][0]['balance_units']=large
        state=json.loads(self.request('/api/state')[2])['state']
        self.assertEqual(state['customers'][0]['chat_id'],str(large))
        self.assertEqual(state['customers'][0]['balance_units'],str(large))
        self.assertEqual(self.post(dict(action='customer.balance',payload={'chat_id':str(large),'expected_units':str(large),'amount':'5','reason':'test'}))[0],200)
        self.assertEqual(self.fixture.fake.commands[-1][1]['expected_units'],large)
        self.assertEqual(self.fixture.fake.commands[-1][1]['chat_id'],large)

    def test_forbidden_actions_and_invalid_command_identity_do_not_forward(self):
        for payload in ({'action':'shell.exec','payload':{}},{'action':['job.stop'],'payload':{}},
                {'action':'job.stop','payload':[]},{'action':'job.stop','request_id':'invalid','payload':{}}):
            with self.subTest(payload=payload): self.assertEqual(self.post(payload)[0],400)
        self.assertFalse(self.fixture.fake.commands)

    def test_filesystem_traversal_and_runtime_files_are_not_served(self):
        for path in ('/data/remote_connection.json','/web/../data/remote_connection.json',
                '/web/%2e%2e/core/remote/local_web.py','/icons/../../data/remote_connection.json','/?token=secret'):
            with self.subTest(path=path): self.assertEqual(self.request(path)[0],404)

    def test_connection_reuses_saved_token_and_rejects_non_loopback(self):
        self.assertEqual(self.request('/api/connect',{'url':'http://127.0.0.1:9876','token':''})[0],200)
        saved=json.loads(self.fixture.connection.read_text())
        self.assertEqual(saved['token'],TOKEN)
        for url in ('https://example.com','http://127.0.0.1.evil','http://u:p@localhost','http://localhost/other'):
            with self.subTest(url=url): self.assertEqual(self.request('/api/connect',{'url':url,'token':''})[0],400)
        self.assertEqual(json.loads(self.fixture.connection.read_text()),saved)

    def test_errors_hide_private_exception_and_active_mutation_is_not_duplicated(self):
        self.fixture.fake.offline=True
        code,_,raw=self.request('/api/state')
        self.assertEqual(code,500)
        self.assertNotIn(TOKEN.encode(),raw)
        self.server.mutation.acquire()
        try: self.assertEqual(self.post(dict(action='job.stop',payload={'job_id':'x'}))[0],409)
        finally: self.server.mutation.release()
        self.assertFalse(self.fixture.fake.commands)

    def test_browser_management_does_not_import_runtime_or_stop_vps_jobs(self):
        self.assertNotIn('core.remote.runtime',sys.modules)
        self.assertNotIn('PySide6.QtWidgets',sys.modules)
        self.assertFalse(self.fixture.fake.commands)


if __name__=='__main__': unittest.main()
