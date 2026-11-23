import base64
import io
import json
from pathlib import Path
import struct
import time
import unittest
import zipfile
import zlib
from unittest.mock import patch
import test_hub
from hub.core import Problem, Store
from hub.server import HubServer

def pack(home=None):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name, value in [('manifest',{'format':'video.fika.rex.home-package','version':1}),
                            ('home',home or {'title':'首页'}),('layout',[{'items':[{'widgetId':'test','widgetURL':'file:///m.js'}]}])]:
            z.writestr('rex-'+name+'.json',json.dumps(value))
    return out.getvalue()

class MaintenanceTests(unittest.TestCase):
    setUp=test_hub.ApiTests.setUp
    tearDown=test_hub.ApiTests.tearDown
    request=test_hub.ApiTests.request
    login=test_hub.ApiTests.login
    upload=test_hub.ApiTests.upload

    def call(self,path,body=None,method='POST'):
        code,_,data=self.request(path,method,body)
        self.assertIn(code,(200,201,202),data)
        return json.loads(data)

    def test_home_share_replace_backup_revoke(self):
        self.login()
        raw=pack()
        payload={'data':base64.b64encode(raw).decode()}
        ident=self.call('/api/homes',payload)['id']
        def home():return self.call('/api/state',method='GET')['homes'][0]
        h=home()
        self.assertEqual(h['summary']['refs'],[{'id':'test','url':'file:///m.js'}])
        self.assertIsNone(h['share'])
        url='/api/homes/'+ident
        self.assertEqual(h['size'],len(raw))
        code,headers,content=self.request(url+'/file')
        self.assertEqual((code,content),(200,raw))
        self.assertIn('attachment',headers['Content-Disposition'])
        self.assertEqual(self.request(url+'/file',auth=False)[0],401)
        self.call(url,{'title':'新名称','expected':h['sha']},'PATCH')
        self.assertEqual(home()['title'],'新名称')
        self.assertEqual(self.request(url+'/file')[2],raw)
        self.call(url+'/share',{'expected':h['sha'],'enabled':True})
        link='/homes/'+home()['share']+'.rex'
        self.call(url,{**payload,'title':'replaced','expected':h['sha']},'PUT')
        self.call(url,{**payload,'expected':h['sha']},'PUT')
        self.assertEqual(home()['title'],'replaced')
        self.assertEqual(self.request(link,auth=False)[2],raw)
        backup=self.server.store.backup()
        self.server.store.restore(backup)
        self.assertEqual(self.server.store.backup(),backup)
        self.call(url+'/share',{'expected':home()['sha'],'enabled':False})
        self.assertEqual(self.request(link,auth=False)[0],404)
        self.assertEqual(self.request('/api/homes','POST',{'data':'AA=='})[0],400)

    def test_home_avatar_five_mib_and_existing_package_backfill(self):
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

        self.login()
        limit = 5 * 1024 * 1024
        png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
        pixels = chunk(b'IDAT', zlib.compress(b'\0\0\xff\0')) + chunk(b'IEND', b'')
        padding = limit - len(png) - len(pixels) - 12
        avatar = png + chunk(b'tEXt', b'A\x00' + b'x' * (padding - 2)) + pixels
        self.assertEqual(len(avatar), limit)
        package = pack({'title':'首页', 'avatarData':'data:image/png;base64,' + base64.b64encode(avatar).decode()})
        ident = self.call('/api/homes', {'data':base64.b64encode(package).decode()})['id']
        url = '/api/homes/' + ident

        def check_avatar():
            home = self.call('/api/state', method='GET')['homes'][0]
            self.assertTrue(home['has_avatar'])
            self.assertNotIn('avatar', home['summary'])
            status, headers, image = self.request(url + '/avatar')
            self.assertEqual((status, headers['Content-Type'], image), (200, 'image/png', avatar))

        check_avatar()
        with self.server.store.connect() as db:
            db.execute("UPDATE homes SET title='手动改名', summary=json_set(summary,'$.avatar',NULL) WHERE id=?", (ident,))
            db.execute("DELETE FROM app_migrations WHERE name='home-avatar-5mib'")
        Store(Path(self.server.store.path).parent)
        check_avatar()
        self.assertEqual(self.call('/api/state', method='GET')['homes'][0]['title'], '手动改名')
        self.assertEqual(self.request(url + '/file')[2], package)

    def test_encrypted_duplicate_rollback_and_membership(self):
        self.login()
        old=b'REXENC-opaque'
        ident=self.upload(filename='one.js',data=old)
        self.call('/api/collections',{'slug':'demo','title':'Example','module_ids':[ident]})
        for filename,content,reason in [('two.js',old,'内容相同'),('one.js',b'REXENC-new','文件同名')]:
            p=self.call('/api/maintenance/preview',{'filename':filename,'data':base64.b64encode(content).decode()})
            self.assertEqual(p['matches'][0]['reason'],reason)
            self.assertEqual(p['matches'][0]['collections'],['Example'])
        self.call('/api/modules/'+ident,{'data':'UkVYRU5DLTI=','replace':True},'PUT')
        before=self.call('/api/state',method='GET')
        self.assertIn(ident,before['previous'])
        backup=self.server.store.backup()
        self.server.store.restore(backup)
        self.call('/api/modules/'+ident+'/rollback',{'expected':before['modules'][0]['sha256']})
        self.assertEqual(self.request('/api/modules/'+ident+'/raw')[2],old)
        self.assertEqual(self.call('/api/state',method='GET')['collections'][0]['module_ids'],[ident])

    def test_only_unfiled_matches_and_rules_survive_restore(self):
        self.login()
        folder=self.call('/api/folders',{'name':'Icons'})['id']
        first=self.upload(kind='asset',filename='one-icon.json',data=b'{}')
        obj={'folder':folder,'pattern':'*-icon.json','auto':True}
        p=self.call('/api/folder-rules/preview',obj)
        self.assertEqual([r['id'] for r in p['rows']],[first])
        self.upload(kind='asset',filename='two-icon.json',data=b'{}')
        self.assertEqual(self.request('/api/folder-rules/apply','POST',{**obj,'token':p['token']})[0],409)
        p=self.call('/api/folder-rules/preview',obj)
        self.call('/api/folder-rules/apply',{**obj,'token':p['token']})
        backup=self.server.store.backup();self.server.store.restore(backup)
        added=self.upload(kind='asset',filename='three-icon.json',data=b'{}')
        self.assertEqual(self.call('/api/state',method='GET')['locations'][added],folder)
        self.assertEqual(self.call('/api/folder-rules/preview',obj)['rows'],[])

    def test_collection_retry_reuses_only_unchanged_downloads(self):
        self.login()
        ids=[]
        for n in ('a','b'):
            ident=self.upload(filename=n+'.js')
            self.call('/api/modules/'+ident,{'source_url':'https://example.com/'+n+'.js'},'PUT')
            ids.append(ident)
        self.call('/api/collections',{'slug':'demo','module_ids':ids})
        scheduler=self.server.scheduler
        with patch('hub.sync.fetch',side_effect=[(b'new','https://example.com/a.js'),Problem('offline')]):
            self.server.import_lock.acquire();scheduler.run_job('demo')
        self.assertEqual(self.server.job['result']['errors'][0]['id'],ids[1])
        self.assertNotEqual(self.request('/api/modules/'+ids[0]+'/raw')[2],b'new')
        restarted = HubServer(('127.0.0.1',0), Path(self.server.store.path).parent,
                              'temporary-test-password', self.server.site_url)
        try:
            with patch('hub.sync.fetch',return_value=(b'next','https://example.com/b.js')) as fetched:
                restarted.import_lock.acquire()
                restarted.scheduler.run_job('demo', self.server.job['id'])
                self.assertEqual(restarted.job['status'], 'complete')
                fetched.assert_called_once_with('https://example.com/b.js')
        finally:
            restarted.server_close()
            restarted.scheduler.join(2)
        self.assertEqual(self.request('/api/modules/'+ids[0]+'/raw')[2],b'new')

    def test_retry_sources_only_runs_failed_entries(self):
        self.login()
        up = self.upload(filename='clear.js')
        bad = self.upload(filename='failed.js')
        for ident, name in ((up,'good'),(bad,'bad')):
            self.call('/api/modules/'+ident, {'source_url':f'https://example.com/{name}.js'}, 'PUT')
        with self.server.store.connect() as db:
            db.execute("INSERT INTO file_sync(module_id,error) VALUES (?,'offline')", (bad,))
        self.assertEqual(self.request('/api/sources/retry','POST',{},auth=False)[0],401)
        with patch('hub.sync.fetch', return_value=(b'new','https://example.com/bad.js')) as fetch:
            self.assertEqual(self.call('/api/sources/retry',{})['queued'],1)
            deadline=time.monotonic()+4
            while self.server.import_lock.locked() and time.monotonic()<deadline:
                time.sleep(.01)
            fetch.assert_called_once_with('https://example.com/bad.js')
        self.assertEqual(self.server.job['result']['success'],1)
        self.assertEqual(self.call('/api/sources/retry',{})['queued'],0)

    def test_extensionless_preview_and_changed_source(self):
        self.login()
        url='https://example.com/feed'
        manifest=json.dumps({'title':'Example','widgets':[{'url':'a.js'}]}).encode()
        with patch('hub.server.fetch',return_value=(manifest,url)):
            p=self.call('/api/maintenance/preview',{'url':url})
        self.assertEqual((p['type'],p['count']),('collection',1))
        with patch('hub.server.fetch',return_value=(b'{}',url)):
            self.assertEqual(self.request('/api/import','POST',{'url':url,'manifest':True,'expected_sha':p['sha256']})[0],409)
        self.assertEqual(self.server.store.backup()['collections'],[])
