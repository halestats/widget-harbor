import json
import threading
import unittest
from unittest.mock import patch
import test_hub

class FileApiTests(unittest.TestCase):
    setUp=test_hub.ApiTests.setUp
    tearDown=test_hub.ApiTests.tearDown
    request=test_hub.ApiTests.request
    login=test_hub.ApiTests.login
    upload=test_hub.ApiTests.upload

    def test_scheduling_and_refresh_endpoint(self):
        self.login()
        remote=self.upload(kind='asset',filename='file.txt',data=b'old')
        self.assertEqual(self.request('/api/modules/'+remote,'PUT',{'source_url':'https://example.com:5090/file.txt'})[0],200)
        local=self.upload(kind='asset',filename='local.txt',data=b'local')
        status,_,_=self.request('/api/modules/batch','POST',{'ids':[remote,local],'action':'auto_refresh','enabled':True})
        self.assertEqual(status,200)
        status,_,raw=self.request('/api/state');files={m['id']:m for m in json.loads(raw)['modules']}
        self.assertEqual(files[remote]['refresh']['auto_refresh'],1)
        self.assertFalse(files[local]['refresh'].get('auto_refresh'))
        status,_,_=self.request('/api/modules/'+remote,'PUT',{'auto_refresh':False})
        self.assertEqual(status,200)
        started=threading.Event();finish=threading.Event()
        def download(url):
            started.set();finish.wait(3);return b'new',url
        with patch('hub.sync.fetch',side_effect=download):
            self.assertEqual(self.request('/api/modules/batch','POST',{'ids':[remote,local],'action':'refresh'})[0],202)
            self.assertTrue(started.wait(2))
            self.assertEqual(self.request('/api/modules/batch','POST',{'ids':[remote],'action':'refresh'})[0],409)
            finish.set()
            # Synchronize with the worker releasing the single shared job lock.
            self.assertTrue(self.server.import_lock.acquire(timeout=3));self.server.import_lock.release()
        self.assertEqual(self.server.job['result']['success'],1)
        self.assertEqual(self.server.job['result']['skipped'],1)
