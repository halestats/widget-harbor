import json
import unittest
import test_hub

class RexExportTests(unittest.TestCase):
    setUp=test_hub.ApiTests.setUp
    tearDown=test_hub.ApiTests.tearDown
    request=test_hub.ApiTests.request
    login=test_hub.ApiTests.login
    upload=test_hub.ApiTests.upload

    def test_rex_preserves_required_text_omits_empty_urls_forward_unchanged(self):
        self.login()
        ident=self.upload()
        self.request('/api/modules/'+ident,'PUT',{'icon':'','site':'','author':'','description':''})
        self.request('/api/collections','POST',{'slug':'optional','title':'Optional','icon':'','description':'','module_ids':[ident]})
        status,_,raw=self.request('/api/collections/optional.rex')
        self.assertEqual(status,200)
        result=json.loads(raw)
        self.assertNotIn('icon',result)
        self.assertEqual(result['description'],'')
        widget=result['widgets'][0]
        for name in ('icon','site'):
            self.assertNotIn(name,widget)
        for name in ('author','description'):
            self.assertEqual(widget[name],'')
        for name in ('id','title','version','requiredVersion','url'):
            self.assertTrue(widget[name])
        forward=json.loads(self.request('/api/collections/optional.fwd')[2])
        self.assertEqual(forward['icon'],'')
        self.assertEqual(forward['widgets'][0]['icon'],'')
        self.assertEqual(forward['widgets'][0]['url'],widget['url'])

    def test_nonempty_metadata_and_local_icon_are_preserved(self):
        self.login()
        ident=self.upload()
        self.request('/api/modules/'+ident,'PUT',{'icon':'https://example.com/icon.png','site':'https://example.com','description':'说明'})
        self.request('/api/collections','POST',{'slug':'populated','title':'Populated','icon':'https://example.com/collection.png','description':'合集说明','module_ids':[ident]})
        result=json.loads(self.request('/api/collections/populated.rex')[2])
        self.assertEqual(result['icon'],'https://example.com/collection.png')
        self.assertEqual(result['description'],'合集说明')
        self.assertEqual(result['widgets'][0]['icon'],'https://example.com/icon.png')
        self.assertEqual(result['widgets'][0]['site'],'https://example.com')
        self.assertEqual(result['widgets'][0]['description'],'说明')

    def test_encrypted_identity_correction_preserves_file(self):
        import base64
        self.login()
        content=b'REXENC\nopaque-test-payload'
        status,_,raw=self.request('/api/modules','POST',{'filename':'encrypted.js','data':base64.b64encode(content).decode(),'kind':'module','platform':'rex'})
        self.assertEqual(status,201)
        ident=json.loads(raw)['id']
        status,_,_=self.request('/api/modules/'+ident,'PUT',{'widget_id':'confirmed.widget','title':'Confirmed','version':'2.1.3'})
        self.assertEqual(status,200)
        self.request('/api/collections','POST',{'slug':'encrypted-test','title':'Encrypted test','module_ids':[ident]})
        widget=json.loads(self.request('/api/collections/encrypted-test.rex')[2])['widgets'][0]
        self.assertEqual(widget['id'],'confirmed.widget')
        self.assertEqual(widget['version'],'2.1.3')
        self.assertEqual(self.request('/api/modules/'+ident+'/raw')[2],content)
