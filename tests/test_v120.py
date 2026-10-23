import json,time,os
from unittest.mock import patch
import test_hub
from test_hub import SOURCE

class NewFeatures(test_hub.ApiTests):
    def test_dual_origin_cookies(self):
        self.server.allowed_origins.update({'http://192.168.1.1:8765','https://public.example'})
        for origin,secure in [('http://192.168.1.1:8765',False),('https://public.example',True)]:
            status,h,_=self.request('/api/login','POST',{'password':'test-password-long'},headers={'Origin':origin})
            self.assertEqual(status,200);self.assertEqual('; Secure' in h['Set-Cookie'],secure)
        self.assertEqual(self.request('/api/login','POST',{'password':'test-password-long'},headers={'Origin':'https://evil.example'})[0],403)

    def test_background_import_description_group(self):
        self.login()
        manifest={'title':'Test','widgets':[{'url':'https://example.com/a.js','description':''}]}
        source=SOURCE.replace(b"version:'2.0'",b"description:'From source',version:'2.0'")
        with patch('hub.server.fetch',side_effect=[(json.dumps(manifest).encode(),'https://example.com/sub.rex'),(source,'https://example.com/a.js')]):
            self.assertEqual(self.request('/api/import/start','POST',{'url':'https://example.com/sub.rex'})[0],202)
            for _ in range(100):
                job=json.loads(self.request('/api/import/status')[2])
                if job['status']!='running':break
                time.sleep(.01)
            self.assertEqual(job['status'],'complete')
        m=json.loads(self.request('/api/state')[2])['modules'][0]
        self.assertEqual(m['description'],'From source');self.assertEqual(m['source_group'],'https://example.com/sub.rex')

    def test_cascade_shared_and_batch_profile(self):
        self.login();a=self.upload();b=self.upload()
        for slug,ids in [('first',[a,b]),('second',[b])]:self.request('/api/collections','POST',{'slug':slug,'module_ids':ids})
        self.assertEqual(self.request('/api/collections/first?delete_modules=1','DELETE')[0],200)
        ids=[m['id'] for m in json.loads(self.request('/api/state')[2])['modules']]
        self.assertEqual(ids,[b])
        self.assertEqual(self.request('/api/modules/batch','POST',{'ids':[b],'action':'platform','platform':'rex'})[0],200)
        self.assertEqual(self.request('/api/profile','PUT',{'avatar':'javascript:bad'})[0],400)
        self.assertEqual(self.request('/api/profile','PUT',{'avatar':''})[0],200)
        self.assertEqual(self.request('/api/modules/batch','POST',{'ids':[b],'action':'delete'})[0],200)

    def test_cancel_import_does_not_commit(self):
        import threading
        self.login();entered=threading.Event();release=threading.Event()
        def slow(url):
            entered.set();release.wait(3)
            return SOURCE,url
        with patch('hub.server.fetch',side_effect=slow):
            self.assertEqual(self.request('/api/import/start','POST',{'url':'https://example.com/a.js'})[0],202)
            self.assertTrue(entered.wait(1))
            self.request('/api/import/cancel','POST',{})
            release.set()
            for _ in range(100):
                job=json.loads(self.request('/api/import/status')[2])
                if job['status']!='running':break
                time.sleep(.01)
            self.assertEqual(job['status'],'cancelled')
        self.assertEqual(json.loads(self.request('/api/state')[2])['modules'],[])
