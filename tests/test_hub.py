import base64
import http.client
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from hub.core import Store, Problem, metadata, prepare_module
from hub.server import HubServer
from hub.network import destination, fetch


def encoded(data):
    return base64.b64encode(data).decode()


SOURCE = b'''var WidgetMetadata = {id:'demo.id', title:'Hello', site:'https://example.com/a', version:'2.0', modules:[{title:'Nested'}]};'''


class CoreTests(unittest.TestCase):
    def test_literals_and_comments_not_evaluated(self):
        parsed = metadata(SOURCE)
        self.assertEqual(parsed['site'], 'https://example.com/a')
        self.assertEqual(parsed['title'], 'Hello')
        self.assertEqual(metadata(b'/* WidgetMetadata {"title":"Comment","id":"x"} */')['title'], 'Comment')
        self.assertEqual(metadata(b'''WidgetMetadata={title:'hi } {', // comment
          version:'3.0', modules:[{title:'bad'}], author:'A'}''')['author'], 'A')
        self.assertNotIn('title', metadata(b'WidgetMetadata={title:dangerousCall()}'))
        self.assertEqual(metadata(br'WidgetMetadata={title:"\uD83D\uDE00"}')["title"], '😀')
        self.assertEqual(metadata(b'FWENC1\x00\xff'), {})

    def test_restore_rolls_back_and_preserves_identity(self):
        with tempfile.TemporaryDirectory() as path:
            store = Store(path)
            m=prepare_module({'filename':'hello.js','data':encoded(SOURCE)})
            with store.connect() as db:
                store.save_module(db,m)
                store.save_collection(db,{'slug':'demo','module_ids':[m['id']]})
            with store.connect() as db:
                store.save_collection(db,{'slug':'second','module_ids':[m['id']]})
            backup=store.backup()
            backup['modules'][0]['updated_at']=1234
            store.restore(backup)
            self.assertEqual(store.backup(),backup)
            backup['collections'][0]['module_ids']=['missing']
            with self.assertRaises(Problem):store.restore(backup)
            with store.connect() as db:
                self.assertEqual(store.get_module(db,m['id'])['content'],SOURCE)
                self.assertEqual(store.collections(db)[0]['module_ids'],[m['id']])

    def test_public_network_only(self):
        for host in ('127.0.0.1','10.0.0.1','169.254.169.254','::1','::ffff:127.0.0.1'):
            with patch('hub.network.socket.getaddrinfo', return_value=[(2,1,6,'',(host,80))]):
                with self.assertRaises(Problem):destination('http://example.com')
        for url in ('file:///etc/passwd','http://user:pass@example.com','https://example.com:0','https://example.com:65536'):
            with self.assertRaises(Problem):destination(url)

    def test_redirect_is_revalidated(self):
        class Response:
            status=302
            def getheader(self, key):return 'http://127.0.0.1/secret'
        with patch('hub.network.socket.getaddrinfo', side_effect=[[(2,1,6,'',('8.8.8.8',80))],[(2,1,6,'',('127.0.0.1',80))]]), patch('hub.network.socket.socket'), patch('hub.network.http.client.HTTPConnection') as connection:
            connection.return_value.getresponse.return_value=Response()
            with self.assertRaises(Problem):fetch('http://example.com')
            self.assertEqual(connection.call_count,1)


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.server=HubServer(('127.0.0.1',0),self.directory.name,'test-password-long','http://localhost:8765')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.cookie=''

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.directory.cleanup()

    def request(self,path,method='GET',body=None,auth=True,headers=None):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        h={'Content-Type':'application/json','X-Hub-Request':'1'}
        if auth:h['Cookie']=self.cookie
        h.update(headers or {})
        conn.request(method,path,json.dumps(body) if body is not None else None,h)
        r=conn.getresponse();data=r.read();status=r.status;hs=dict(r.getheaders());conn.close()
        return status,hs,data

    def login(self):
        status,headers,_=self.request('/api/login','POST',{'password':'test-password-long'})
        self.assertEqual(status,200);self.cookie=headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly',headers['Set-Cookie'])

    def upload(self,platform='both',data=SOURCE,kind='module',filename='demo.js'):
        status,_,body=self.request('/api/modules','POST',{'filename':filename,'data':encoded(data),'platform':platform,'kind':kind})
        self.assertEqual(status,201,body)
        return json.loads(body)['id']

    def test_auth_csrf_and_logout(self):
        self.assertEqual(self.request('/api/state',auth=False)[0],401)
        self.assertEqual(self.request('/api/login','POST',{'password':'wrong'})[0],401)
        self.login()
        self.assertEqual(self.request('/api/modules','POST',{},headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/modules','POST',{},headers={'X-Hub-Request':''})[0],403)
        self.assertEqual(self.request('/api/state')[0],200)
        self.request('/api/logout','POST',{})
        self.assertEqual(self.request('/api/state')[0],401)

    def test_password_rotation_persistence_and_sessions(self):
        payload={'current_password':'test-password-long','new_password':'updated-password-long'}
        self.assertEqual(self.request('/api/password','POST',payload)[0],401)
        self.login();first=self.cookie; self.login();second=self.cookie
        self.assertEqual(self.request('/api/password','POST',payload,headers={'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.request('/api/password','POST',{**payload,'current_password':'incorrect'})[0],400)
        self.assertEqual(self.request('/api/password','POST',{**payload,'new_password':'short'})[0],400)
        with patch('hub.server.os.replace',side_effect=OSError('simulated write failure')):
            self.assertEqual(self.request('/api/password','POST',payload)[0],500)
        self.assertEqual(self.request('/api/state')[0],200)
        self.assertEqual(self.request('/api/password','POST',payload)[0],200)
        for cookie in (first,second):
            self.cookie=cookie
            self.assertEqual(self.request('/api/state')[0],401)
        self.assertEqual(self.request('/api/login','POST',{'password':'test-password-long'})[0],401)
        self.assertEqual(self.request('/api/login','POST',{'password':'updated-password-long'})[0],200)
        from pathlib import Path
        saved=Path(self.directory.name)/'admin-auth.json'
        self.assertNotIn('updated-password-long',saved.read_text())
        self.assertEqual(saved.stat().st_mode & 0o777,0o600)
        self.server.shutdown();self.server.server_close();self.thread.join()
        self.server=HubServer(('127.0.0.1',0),self.directory.name,'test-password-long','http://localhost:8765')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.assertEqual(self.request('/api/login','POST',{'password':'test-password-long'})[0],401)
        status,headers,_=self.request('/api/login','POST',{'password':'updated-password-long'})
        self.assertEqual(status,200);self.cookie=headers['Set-Cookie'].split(';')[0]
        self.assertNotIn('admin-auth',self.request('/api/backup')[2].decode())

    def test_subscriptions_filter_bytes_and_delete(self):
        self.login()
        common=self.upload();rex=self.upload('rex',b'FWENC1\x00\xff\x80')
        self.assertEqual(self.request('/api/collections','POST',{'slug':'demo','title':'Collection','module_ids':[common,rex]})[0],200)
        for ext,n in [('fwd',1),('rex',2)]:
            status,_,data=self.request('/api/collections/demo.'+ext,auth=False)
            self.assertEqual(status,200);manifest=json.loads(data);self.assertEqual(len(manifest['widgets']),n)
            self.assertTrue(manifest['widgets'][0]['url'].startswith('http://localhost:8765/'))
            self.assertEqual(manifest['widgets'][0]['id'],'demo.id')
        self.assertEqual(self.request(f'/api/modules/{rex}/raw',auth=False)[2],b'FWENC1\x00\xff\x80')
        self.request(f'/api/modules/{rex}','DELETE')
        self.assertEqual(len(json.loads(self.request('/api/collections/demo.rex')[2])['widgets']),1)

    def test_refresh_preserves_id_and_failure_preserves_content(self):
        self.login();ident=self.upload()
        self.request(f'/api/modules/{ident}','PUT',{'source_url':'https://example.com/a.js'})
        with patch('hub.server.fetch',side_effect=Problem('failure',502)):
            self.assertEqual(self.request(f'/api/modules/{ident}/refresh','POST',{})[0],502)
        self.assertEqual(self.request(f'/api/modules/{ident}/raw')[2],SOURCE)
        with patch('hub.server.fetch',return_value=(SOURCE.replace(b'2.0',b'3.0'),'https://example.com/a.js')):
            self.assertEqual(self.request(f'/api/modules/{ident}/refresh','POST',{})[0],200)
        state=json.loads(self.request('/api/state')[2]);self.assertEqual(state['modules'][0]['id'],ident);self.assertEqual(state['modules'][0]['version'],'3.0')

    def test_manifest_import_is_atomic_and_resolves_relative_urls(self):
        self.login()
        manifest={'title':'Imported','widgets':[{'url':'./a.js'},{'url':'./b.js'}]}
        with patch('hub.server.fetch',side_effect=[(json.dumps(manifest).encode(),'https://example.com/sub/demo.fwd'),(SOURCE,'https://example.com/sub/a.js'),Problem('failed')]):
            self.assertEqual(self.request('/api/import','POST',{'url':'https://example.com/sub/demo.fwd'})[0],400)
        self.assertEqual(json.loads(self.request('/api/state')[2])['modules'],[])
        with patch('hub.server.fetch',side_effect=[(json.dumps(manifest).encode(),'https://example.com/sub/demo.fwd'),(SOURCE,'https://example.com/sub/a.js'),(SOURCE,'https://example.com/sub/b.js')]) as mocked:
            status,_,data=self.request('/api/import','POST',{'url':'https://example.com/sub/demo.fwd'})
            self.assertEqual(status,201,data);self.assertEqual(mocked.call_args_list[1].args[0],'https://example.com/sub/a.js')
        state=json.loads(self.request('/api/state')[2]);self.assertEqual(len(state['modules']),2);self.assertEqual(state['modules'][0]['widget_id'],'demo.id')

    def test_raw_endpoint_import_and_json_asset(self):
        self.login()
        with patch('hub.server.fetch',return_value=(SOURCE,'https://example.com/api/modules/abc/raw')):
            self.assertEqual(self.request('/api/import','POST',{'url':'https://example.com/api/modules/abc/raw'})[0],201)
        with patch('hub.server.fetch',return_value=(b'{"ok":true}','https://example.com/config.json')):
            self.assertEqual(self.request('/api/import','POST',{'url':'https://example.com/config.json','kind':'asset'})[0],201)

    def test_backup_restore_and_active_content(self):
        self.login();ident=self.upload(kind='asset',filename='page.html',data=b'<script>alert(1)</script>')
        status,headers,data=self.request(f'/widgets/{ident}/page.html',auth=False)
        self.assertEqual(status,200);self.assertIn('attachment',headers['Content-Disposition']);self.assertIn('sandbox',headers['Content-Security-Policy'])
        backup=json.loads(self.request('/api/backup')[2]);self.request(f'/api/modules/{ident}','DELETE')
        self.assertEqual(self.request('/api/restore','POST',{'backup':backup,'confirm':'RESTORE'})[0],200)
        self.assertEqual(self.request(f'/widgets/{ident}/page.html')[2],data)
        self.assertEqual(self.request('/api/modules','POST',{'filename':'../escape.js','data':encoded(SOURCE)})[0],400)

    def test_health_static_and_head(self):
        self.assertEqual(self.request('/healthz',auth=False)[0],200)
        self.assertEqual(self.request('/',auth=False)[0],200)
        self.assertEqual(self.request('/style.css','HEAD',auth=False)[2],b'')
        self.assertEqual(self.request('/../hub/server.py',auth=False)[0],401)


if __name__=='__main__':unittest.main()
