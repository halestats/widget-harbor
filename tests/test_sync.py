import base64
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from hub.core import Store, prepare_module, metadata, Problem
from hub.server import HubServer

SOURCE=b'WidgetMetadata={id:"a", title:"A", description:`Full description\\nSecond line`,version:"2"};'
ENC=b'REXENC\n{"v":2}\nopaque-bytes'

def data(content):return base64.b64encode(content).decode()

class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.server=HubServer(('127.0.0.1',0),self.tmp.name,'test-password-long','http://localhost:8765')
        self.store=self.server.store
    def tearDown(self):
        self.server.server_close();self.server.scheduler.join(2);self.tmp.cleanup()
    def module(self,source='',group='',content=SOURCE,**kw):
        m=prepare_module({'filename':'a.js','data':data(content),'source_url':source,'source_group':group,**kw})
        with self.store.connect() as db:self.store.save_module(db,m)
        return m
    def collection(self,slug,modules,source='',auto=False):
        with self.store.connect() as db:
            self.store.save_collection(db,dict(slug=slug,module_ids=[m['id'] for m in modules],source_url=source,auto_refresh=auto))
    def test_metadata_and_encrypted(self):
        self.assertEqual(metadata(SOURCE)['description'],'Full description\nSecond line')
        self.assertNotIn('description',metadata(b'WidgetMetadata={description:`${run()}`}'))
        m=self.module(content=ENC,title='Encrypted',description='Upstream description')
        updated=prepare_module({'data':data(ENC)},m)
        self.assertEqual(updated['description'],'Upstream description')
        self.assertEqual(updated['title'],'Encrypted')
        self.assertEqual(updated['encrypted'],1)
        self.assertEqual(prepare_module({'description':''},m)['description'],'')
    def test_import_refresh_identity_members_and_custom_references(self):
        url='https://example.com/sub.rex';a=self.module('https://example.com/a.js',url)
        local=self.module();self.collection('remote',[a],url);self.collection('custom',[a,local])
        manifest={'widgets':[{'id':'a','url':'/new-a.js','description':'Latest'},{'id':'b','url':'/b.js'}]}
        with patch('hub.sync.fetch',side_effect=[(json.dumps(manifest).encode(),url),(SOURCE,'https://example.com/new-a.js'),(SOURCE.replace(b'id:"a"',b'id:"b"'),'https://example.com/b.js')]):
            self.server.scheduler.refresh('remote')
        with self.store.connect() as db:
            cs={c['slug']:c for c in self.store.collections(db)}
            self.assertEqual(cs['remote']['module_ids'][0],a['id'])
            self.assertEqual(cs['custom']['module_ids'],[a['id'],local['id']])
            self.assertEqual(self.store.get_module(db,a['id'])['description'],'Latest')
            self.assertGreater(cs['remote']['last_success'],0)
    def test_custom_refresh_only_selected_remote_and_keeps_local(self):
        url='https://example.com/sub.rex';a=self.module('https://example.com/a.js',url);local=self.module()
        self.collection('mixed',[a,local])
        manifest={'widgets':[{'id':'a','url':'/new-a.js','description':'Manifest text'},{'id':'unused','url':'/unused.js'}]}
        with patch('hub.sync.fetch',side_effect=[(json.dumps(manifest).encode(),url),(SOURCE,'https://example.com/new-a.js')]) as fetched:
            self.server.scheduler.refresh('mixed')
            self.assertEqual(fetched.call_count,2)
        with self.store.connect() as db:
            self.assertEqual(self.store.get_module(db,local['id']),local)
            self.assertEqual(self.store.get_module(db,a['id'])['description'],'Manifest text')
            self.assertEqual(self.store.collections(db)[0]['module_ids'],[a['id'],local['id']])
    def test_failed_or_cancelled_refresh_is_atomic(self):
        a=self.module('https://example.com/a.js');b=self.module('https://example.com/b.js')
        self.collection('mixed',[a,b])
        with patch('hub.sync.fetch',side_effect=[(SOURCE.replace(b'version:"2"',b'version:"3"'),a['source_url']),Problem('offline')]):
            with self.assertRaises(Problem):self.server.scheduler.refresh('mixed')
        with self.store.connect() as db:self.assertEqual(self.store.get_module(db,a['id']),a)
        self.server.cancel_import.set()
        with self.assertRaises(Problem):self.server.scheduler.refresh('mixed')
    def test_daily_tick_persists_and_disabled_is_skipped(self):
        a=self.module('https://example.com/a.js');self.collection('daily',[a],auto=True);self.collection('manual',[a])
        with patch('hub.sync.fetch',return_value=(SOURCE,a['source_url'])) as fetched:
            self.server.scheduler.tick();self.server.scheduler.tick()
            self.assertEqual(fetched.call_count,1)
        with self.store.connect() as db:
            db.execute('UPDATE collection_sync SET last_attempt=0 WHERE slug="daily"')
        with patch('hub.sync.fetch',side_effect=Problem('offline')) as fetched:
            self.server.scheduler.tick();self.server.scheduler.tick();self.assertEqual(fetched.call_count,1)
        again=Store(self.tmp.name)
        with again.connect() as db:
            c=next(c for c in again.collections(db) if c['slug']=='daily')
            self.assertEqual(c['error'],'offline');self.assertGreater(c['last_attempt'],0)
    def test_old_database_repair_only_missing_descriptions(self):
        a=self.module(description='');b=self.module(description='Keep my words');url='https://example.com/sub.rex'
        with self.store.connect() as db:
            db.execute('UPDATE modules SET source_group=? WHERE id=?',(url,a['id']))
            self.store.save_collection(db,{'slug':'import-old','module_ids':[a['id']]})
            db.execute('DELETE FROM collection_sync')
            db.execute('DELETE FROM app_migrations')
        again=Store(self.tmp.name)
        with again.connect() as db:
            self.assertEqual(again.get_module(db,a['id'])['description'],'Full description\nSecond line')
            self.assertEqual(again.get_module(db,b['id'])['description'],'Keep my words')
            self.assertEqual(again.collections(db)[0]['source_url'],url)

    def test_sync_metadata_backup_roundtrip_and_edit_conflict(self):
        a=self.module('https://example.com/a.js');self.collection('custom',[a],auto=True)
        with self.store.connect() as db:db.execute("UPDATE collection_sync SET last_attempt=1234,last_success=1230,error='offline'")
        backup=self.store.backup();self.store.restore(backup);self.assertEqual(self.store.backup(),backup)
        def racing_fetch(url):
            with self.store.connect() as db:db.execute('UPDATE modules SET title=? WHERE id=?',('Edited during refresh',a['id']))
            return SOURCE,url
        with patch('hub.sync.fetch',side_effect=racing_fetch):
            with self.assertRaises(Problem):self.server.scheduler.refresh('custom')
        with self.store.connect() as db:self.assertEqual(self.store.get_module(db,a['id'])['title'],'Edited during refresh')

    def test_repair_runs_once_and_explicit_empty_description_survives_restart(self):
        a=self.module(description='')
        Store(self.tmp.name)
        with self.store.connect() as db:self.assertEqual(self.store.get_module(db,a['id'])['description'],'')

    def test_remote_headers_sync_missing_empty_and_stable_slug(self):
        url='https://example.com/sub.rex';a=self.module('https://example.com/a.js',url)
        self.collection('stable',[a],url)
        manifest={'title':'New title','description':'New description','icon':'/icon.png','widgets':[{'url':'/a.js'}]}
        def refresh(m):
            with patch('hub.sync.fetch',side_effect=[(json.dumps(m).encode(),url),(SOURCE,a['source_url'])]):self.server.scheduler.refresh('stable')
        refresh(manifest)
        with self.store.connect() as db:
            c=self.store.collections(db)[0]
            self.assertEqual((c['slug'],c['title'],c['description'],c['icon']),('stable','New title','New description','https://example.com/icon.png'))
        refresh({'widgets':manifest['widgets'],'description':'','icon':''})
        with self.store.connect() as db:
            c=self.store.collections(db)[0]
            self.assertEqual((c['title'],c['description'],c['icon']),('New title','',''))
        with patch('hub.sync.fetch',return_value=(json.dumps({**manifest,'title':42}).encode(),url)):
            with self.assertRaises(Problem):self.server.scheduler.refresh('stable')
        with self.store.connect() as db:self.assertEqual(self.store.collections(db)[0]['title'],'New title')

    def test_remote_headers_failure_and_concurrent_edit(self):
        url='https://example.com/sub.rex';a=self.module('https://example.com/a.js',url)
        self.collection('stable',[a],url)
        manifest={'title':'Upstream','widgets':[{'url':'/a.js'}]}
        with patch('hub.sync.fetch',side_effect=[(json.dumps(manifest).encode(),url),Problem('offline')]):
            with self.assertRaises(Problem):self.server.scheduler.refresh('stable')
        with self.store.connect() as db:self.assertEqual(self.store.collections(db)[0]['title'],'stable')
        def fetch(u):
            if u==url:return json.dumps(manifest).encode(),url
            with self.store.connect() as db:db.execute("UPDATE collections SET title='Local edit'")
            return SOURCE,u
        with patch('hub.sync.fetch',side_effect=fetch):
            with self.assertRaises(Problem):self.server.scheduler.refresh('stable')
        with self.store.connect() as db:self.assertEqual(self.store.collections(db)[0]['title'],'Local edit')
