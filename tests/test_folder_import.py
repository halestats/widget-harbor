import json
import unittest
from unittest.mock import patch
import test_hub

class FolderImportTests(unittest.TestCase):
    setUp=test_hub.ApiTests.setUp
    tearDown=test_hub.ApiTests.tearDown
    request=test_hub.ApiTests.request
    login=test_hub.ApiTests.login

    def test_async_import_into_folder_and_root(self):
        self.login()
        status, _, raw=self.request('/api/folders','POST',{'name':'Icons'})
        self.assertEqual(status,200)
        folder=json.loads(raw)['id']
        for target in [folder,'']:
            with patch('hub.server.fetch',return_value=(b'{"icons":[]}', 'https://example.com/icons.json')):
                self.assertEqual(self.request('/api/import/start','POST',{'url':'https://example.com/icons.json','kind':'asset','folder_id':target})[0],202)
                self.assertTrue(self.server.import_lock.acquire(timeout=3))
                self.server.import_lock.release()
            self.assertEqual(self.server.job['status'],'complete')
            ident=self.server.job['result']['id']
            state=json.loads(self.request('/api/state')[2])
            self.assertEqual(state['locations'][ident],target)
            self.assertEqual(next(m for m in state['modules'] if m['id']==ident)['kind'],'asset')

    def test_deleted_folder_rolls_back_file(self):
        self.login()
        before=json.loads(self.request('/api/state')[2])['modules']
        with patch('hub.server.fetch',return_value=(b'file','https://example.com/file.txt')):
            status,_,_=self.request('/api/import','POST',{'url':'https://example.com/file.txt','kind':'asset','folder_id':'0'*32})
        self.assertEqual(status,404)
        self.assertEqual(json.loads(self.request('/api/state')[2])['modules'],before)
