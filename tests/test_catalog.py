import base64
import json
import unittest
from unittest.mock import patch
from pathlib import Path
from test_hub import ApiTests
from hub.core import Store, Problem

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j6xkAAAAASUVORK5CYII=')

class CatalogTests(unittest.TestCase):
    setUp=ApiTests.setUp
    tearDown=ApiTests.tearDown
    request=ApiTests.request
    login=ApiTests.login
    upload=ApiTests.upload
    def call(self,path,method='GET',body=None):
        status,_,data=self.request(path,method,body)
        self.assertLess(status,300,data)
        return json.loads(data)

    def test_image_upload_legacy_migration_and_public_cache(self):
        payload={'data':base64.b64encode(PNG).decode()}
        self.assertEqual(self.request('/api/images','POST',payload)[0],401)
        self.login()
        url=self.call('/api/images','POST',payload)['url']
        self.assertTrue((Path(self.directory.name)/'images'/url.split('/')[-1]).is_file())
        status,headers,data=self.request(url,auth=False)
        self.assertEqual((status,data),(200,PNG));self.assertIn('immutable',headers['Cache-Control'])
        self.assertEqual(self.call('/api/state')['modules'],[])
        bad={'data':base64.b64encode(b'<svg onload="bad()"/>').decode()}
        self.assertEqual(self.request('/api/images','POST',bad)[0],400)
        ident=self.upload(data=PNG,kind='asset',filename='old.png')
        source=f'https://widgets.example.com/api/modules/{ident}/raw'
        with patch.object(self.server.store.catalog,'cache',return_value=url):
            self.call('/api/collections','POST',{'slug':'legacy','icon':source,'module_ids':[]})
        state=self.call('/api/state')
        self.assertEqual(state['modules'],[])
        self.assertEqual(state['collections'][0]['icon_display'],url)
        self.assertEqual(self.request(f'/api/modules/{ident}/raw',auth=False)[2],PNG)
        self.assertTrue(self.call('/api/collections/legacy.rex')['icon'].endswith(url))

    def test_remote_cache_ttl_failed_refresh_keeps_bytes(self):
        catalog=self.server.store.catalog;source='https://example.com/icon.png'
        with patch('hub.network.fetch',return_value=(PNG,source)) as fetch:
            first=catalog.cache(source);self.assertEqual(catalog.cache(source),first);self.assertEqual(fetch.call_count,1)
        with patch('hub.network.fetch',side_effect=Problem('offline')) as fetch:
            self.assertEqual(catalog.cache(source,force=True),first)
            self.assertEqual(catalog.cache(source),first);self.assertEqual(fetch.call_count,1)
        with patch('hub.network.fetch',side_effect=Problem('private destination')) as fetch:
            self.assertEqual(catalog.cache('http://127.0.0.1/icon'),'http://127.0.0.1/icon')
            catalog.cache('http://127.0.0.1/icon');self.assertEqual(fetch.call_count,1)

    def test_folders_move_delete_and_persisted_order(self):
        self.login()
        one=self.upload(data=b'a',kind='asset',filename='a.txt')
        two=self.upload(data=b'b',kind='asset',filename='b.txt')
        folder=self.call('/api/folders','POST',{'name':'文档'})['id']
        self.call('/api/assets/move','POST',{'ids':[one,two],'folder_id':folder})
        self.call('/api/folders/'+folder,'PUT',{'name':'资料'})
        self.call('/api/order','PUT',{'scope':'assets','ids':[two,one]})
        state=self.call('/api/state');self.assertEqual(state['orders']['assets'],[two,one]);self.assertEqual(state['folders'][0]['name'],'资料')
        self.assertEqual(state['locations'][one],folder)
        self.assertEqual(self.request('/api/order','PUT',{'scope':'assets','ids':[one,one]})[0],400)
        self.call('/api/folders/'+folder,'DELETE')
        self.assertEqual(self.call('/api/state')['locations'][one],'')
        self.assertEqual(self.request('/api/modules/'+one+'/raw')[2],b'a')
        again=Store(self.directory.name)
        with again.connect() as db:self.assertEqual(again.catalog.state(db)['orders']['assets'],[two,one])

    def test_catalog_backup_restore_and_rollback(self):
        self.login();ident=self.upload(data=b'x',kind='asset',filename='x.txt')
        url=self.call('/api/images','POST',{'data':base64.b64encode(PNG).decode()})['url']
        folder=self.call('/api/folders','POST',{'name':'图片','icon':url})['id']
        self.call('/api/assets/move','POST',{'ids':[ident],'folder_id':folder})
        self.call('/api/order','PUT',{'scope':'assets','ids':[ident]})
        backup=self.server.store.backup()
        self.server.store.restore(backup);self.assertEqual(self.server.store.backup(),backup)
        broken=json.loads(json.dumps(backup));broken['catalog']['images'][0]['data']='!'
        with self.assertRaises(Problem):self.server.store.restore(broken)
        self.assertEqual(self.server.store.backup(),backup)
        old={k:v for k,v in backup.items() if k!='catalog'};old['schema']=1
        self.server.store.restore(old)
        self.assertEqual(self.call('/api/state')['folders'],[])
