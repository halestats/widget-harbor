import base64
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sqlite3
import struct
import time
import unittest
import zipfile
import zlib
from unittest.mock import patch
import test_hub
from hub.core import Problem, Store
from hub.server import HubServer

def pack(home=None, *, version=1, layout=None, files=()):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name, value in [('manifest',{'format':'video.fika.rex.home-package','version':version}),
                            ('home',home or {'title':'首页'}),('layout',layout if layout is not None else [{'items':[{'widgetId':'test','widgetURL':'file:///m.js'}]}])]:
            z.writestr('rex-'+name+'.json',json.dumps(value))
        for name, content in files:
            z.writestr(name, content)
    return out.getvalue()


def home_item(**fields):
    return dict({'style':'cover','count':0,'gridSize':[2,2],'id':'row-item','title':'卡片',
                 'subtitle':'','iconUrl':'','functionId':'load','params':[],'widgetBundleId':'',
                 'widgetId':'','widgetURL':'','name':''}, **fields)

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

    def test_state_and_subscription_list_metadata_without_reading_module_bodies(self):
        self.login()
        first = self.upload(filename='first.js')
        second = self.upload(platform='forward', filename='second.js')
        self.call('/api/collections', {'slug': 'metadata-only', 'module_ids': [first, second]})
        connect = self.server.store.connect
        with connect() as db:
            db.execute('UPDATE modules SET updated_at=100')

        def authorize(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and (table, column) == ('modules', 'content'):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        @contextmanager
        def metadata_only_connection():
            with connect() as db:
                db.set_authorizer(authorize)
                yield db

        with patch.object(self.server.store, 'connect', metadata_only_connection):
            modules = self.call('/api/state', method='GET')['modules']
            rex = self.call('/api/collections/metadata-only.rex', method='GET')['widgets']
            forward = self.call('/api/collections/metadata-only.fwd', method='GET')['widgets']

        self.assertEqual([module['id'] for module in modules], [second, first])
        self.assertTrue(all(module['size'] > 0 and 'content' not in module for module in modules))
        self.assertEqual([row['url'].split('/')[5] for row in rex], [first])
        self.assertEqual([row['url'].split('/')[5] for row in forward], [first, second])

    def test_state_alerts_on_third_failure_until_a_success(self):
        self.login()
        with self.assertLogs('widget-harbor', level='ERROR') as logs:
            for _ in range(2):
                with patch.object(self.server.store, 'connect', side_effect=sqlite3.OperationalError):
                    for _ in range(4):
                        self.assertEqual(self.request('/api/state')[0], 500)
                self.assertEqual(self.request('/api/state')[0], 200)

        alerts = [entry for entry in logs.output if 'ALERT endpoint=/api/state' in entry]
        self.assertEqual(len(alerts), 2)
        for alert in alerts:
            self.assertIn('status=500 error_type=OperationalError failures=3', alert)

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

    def test_home_v2_upload_check_and_rotate_preserve_archive_bytes(self):
        self.login()
        card = '<meta name="rex-widget" content="type=card; width=x2; height=x2">'
        layout = [{'items': [
            home_item(widgetId='file-card', htmlTemplate='cover.html', widgetURL='file:///m.js'),
            home_item(widgetId='local-card', htmlTemplate=card, widgetURL='file://ABCD.js'),
            home_item(widgetId='remote', widgetURL='https://example.org/m.js?key=private'),
        ]}]
        raw = pack(version=2, layout=layout, files=[('rex-html/cover.html', card), ('rex-scripts/m.js', b'REXENC\n')])
        ident = self.call('/api/homes', {'data': base64.b64encode(raw).decode()})['id']
        url = '/api/homes/' + ident
        home = self.call('/api/state', method='GET')['homes'][0]
        self.assertEqual((home['summary']['version'], home['summary']['html_cards'], home['summary']['warnings']), (2, 2, 1))
        result = self.call(url + '/check', method='GET')
        self.assertEqual([c['source'] for c in result['cards']], ['包内文件', '内联'])
        self.assertTrue(result['scripts'][0]['encrypted'])
        self.assertEqual(result['references'][-1]['source'], 'example.org')
        self.assertEqual(self.request(url + '/check', auth=False)[0], 401)
        self.assertNotIn('private', json.dumps(result))

        self.call(url + '/share', {'expected': home['sha'], 'enabled': True})
        old = self.call('/api/state', method='GET')['homes'][0]['share']
        params = {'expected': home['sha'], 'rotate': True, 'expected_share': old}
        self.call(url + '/share', params)
        updated = self.call('/api/state', method='GET')['homes'][0]
        self.assertEqual(updated['sha'], home['sha'])
        self.assertEqual(self.request(url + '/share', 'POST', params)[0], 409)
        self.assertEqual(self.request(f'/homes/{old}.rex', auth=False)[0], 404)
        self.assertEqual(self.request(f"/homes/{updated['share']}.rex", auth=False)[2], raw)
        self.assertEqual(self.request(url + '/file')[2], raw)

    def test_home_reports_missing_or_unsafe_templates_before_save(self):
        self.login()
        cases = [('not-in-archive.html', '找不到 HTML 模板'), ('../outside.html', '文件名不安全')]
        for template, error in cases:
            with self.subTest(template=template):
                raw = pack(version=2, layout=[{'items': [{'htmlTemplate': template}]}])
                code, _, data = self.request('/api/homes', 'POST', {'data': base64.b64encode(raw).decode()})
                self.assertEqual(code, 400)
                self.assertIn(error, json.loads(data)['error'])

        self.assertEqual(self.call('/api/state', method='GET')['homes'], [])

    def test_home_check_cache_tracks_current_file(self):
        self.login()
        original = pack(layout=[{'items': [{'widgetId': 'test'}]}])
        url = '/api/homes/' + self.call('/api/homes', {'data': base64.b64encode(original).decode()})['id']
        with self.server.store.connect() as db:
            db.execute('DELETE FROM home_checks')  # Simulate a home saved before the check cache existed.
        report = self.call(url + '/check', method='GET')
        self.assertIn('布局缺少字段', report['warnings'][0])
        with patch('hub.library.inspect_home', side_effect=AssertionError('cache miss')):
            self.assertEqual(self.call(url + '/check', method='GET'), report)
        expected = self.call(url + '/tools', method='GET')['expected']
        replacement = pack(layout=[{'items': [home_item()]}])
        self.call(url, {'expected': expected, 'data': base64.b64encode(replacement).decode()}, 'PUT')
        with patch('hub.library.inspect_home', side_effect=AssertionError('cache miss')):
            self.assertEqual(self.call(url + '/check', method='GET')['warnings'], [])

    def test_home_relink_copies_or_overwrites_and_removes_only_unused_scripts(self):
        self.login()
        ident = self.upload()  # Widget ID demo.id.
        layout = [{'items': [home_item(widgetId='demo.id', widgetURL='file:///used.js'),
                             home_item(widgetId='missing', widgetURL='file:///keep.js'),
                             home_item(widgetId='demo.id', widgetURL='file://device.js')]}]
        raw = pack(layout=layout, files=[('rex-scripts/used.js', b'one'), ('rex-scripts/keep.js', b'two'),
                                       ('rex-scripts/unrelated.js', b'three')])
        root = '/api/homes'
        original = self.call(root, {'data': base64.b64encode(raw).decode()})['id']
        url = root + '/' + original
        plan = self.call(url + '/tools', method='GET')
        self.assertEqual([(link['widget_id'], link['count']) for link in plan['links']], [('demo.id', 2), ('missing', 1)])
        self.assertIsNone(plan['links'][1]['target'])
        payload = {'action': 'relink', 'expected': plan['expected'], 'selected': {'demo.id': ident}}
        self.assertEqual(self.request(url + '/tools', 'POST', {**payload, 'origin':'https://other.example'})[0], 400)
        copy_result = self.call(url + '/tools', payload)
        self.assertEqual(copy_result['changed'], 2)
        self.assertNotEqual(copy_result['id'], original)
        self.assertEqual(self.request(url + '/file')[2], raw)
        copy_file = self.request(root + '/' + copy_result['id'] + '/file')[2]
        with zipfile.ZipFile(io.BytesIO(copy_file)) as archive:
            items = json.loads(archive.read('rex-layout.json'))[0]['items']
            self.assertEqual(items[0]['widgetURL'], f'http://localhost:8765/api/modules/{ident}/raw')
            self.assertEqual(items[2]['widgetURL'], items[0]['widgetURL'])
            self.assertEqual(items[1], layout[0]['items'][1])
            self.assertNotIn('rex-scripts/used.js', archive.namelist())
            self.assertEqual(archive.read('rex-scripts/keep.js'), b'two')
            self.assertEqual(archive.read('rex-scripts/unrelated.js'), b'three')
        self.call(url + '/share', {'expected': plan['expected'], 'enabled':True})
        old_share = next(h['share'] for h in self.call('/api/state', method='GET')['homes'] if h['id'] == original)
        payload['mode'] = 'replace'
        self.assertEqual(self.call(url + '/tools', payload)['id'], original)
        self.assertEqual(self.request(f'/homes/{old_share}.rex', auth=False)[0], 200)
        self.assertEqual(self.request(url + '/tools', 'POST', payload)[0], 409)

    def test_home_html_attach_replace_remove_and_row_validation(self):
        self.login()
        layout = [{'title':'单项目', 'items':[home_item()]}, {'title':'两项目', 'items':[home_item(), home_item()]}]
        raw = pack(layout=layout)
        root = '/api/homes'
        original = self.call(root, {'data': base64.b64encode(raw).decode()})['id']
        url = root + '/' + original
        plan = self.call(url + '/tools', method='GET')
        html = b'<meta name="rex-widget" content="type=rail; height=288; item-ratio=9:8">'
        payload = {'action':'html','expected':plan['expected'],'item':0,'section':0,
                   'filename':'example.html','html':base64.b64encode(html).decode()}
        self.assertEqual(self.request(url + '/tools','POST',{**payload,'section':1})[0],400)
        self.assertEqual(self.request(url + '/tools','POST',{**payload,'html':'YQ=='})[0],400)
        copied = root + '/' + self.call(url + '/tools',payload)['id']
        self.assertEqual(self.request(url + '/file')[2],raw)
        with zipfile.ZipFile(io.BytesIO(self.request(copied + '/file')[2])) as archive:
            self.assertEqual(json.loads(archive.read('rex-manifest.json'))['version'],2)
            item = json.loads(archive.read('rex-layout.json'))[0]['items'][0]
            self.assertEqual((item['style'],item['gridSize']),('fullWidthPoster',[-1,2.3]))
            self.assertEqual(archive.read('rex-html/' + item['htmlTemplate']),html)
        expected = self.call(copied + '/tools', method='GET')['expected']
        self.call(copied + '/tools', {'action':'html','mode':'replace','expected':expected,'section':0,'item':0,'remove':True})
        with zipfile.ZipFile(io.BytesIO(self.request(copied + '/file')[2])) as archive:
            self.assertNotIn('rex-html/example.html', archive.namelist())
            self.assertNotIn('htmlTemplate', json.loads(archive.read('rex-layout.json'))[0]['items'][0])

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
