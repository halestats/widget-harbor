import json
import unittest

import test_hub


class OrganizerTests(unittest.TestCase):
    setUp = test_hub.ApiTests.setUp
    tearDown = test_hub.ApiTests.tearDown
    request = test_hub.ApiTests.request
    login = test_hub.ApiTests.login
    upload = test_hub.ApiTests.upload

    def api(self, path, method='GET', body=None):
        status,_,data=self.request(path,method,body)
        self.assertIn(status,(200,201),data)
        return json.loads(data)

    def apply(self, tool, obj):
        path='/api/organizer/'+tool
        preview=self.api(path,'POST',obj)
        self.api(path,'POST',{**obj,'ids':[r['id'] for r in preview['rows']],'token':preview['token']})
        return preview['rows']

    def test_classification_and_restore(self):
        self.login()
        a=self.upload(filename='match-one.js')
        self.api('/api/collections','POST',{'slug':'favorites'})
        self.apply('rules',dict(keywords='MATCH',label='测试',slug='favorites',auto=True))
        b=self.upload(filename='match-two.js')
        expected=self.api('/api/state')
        self.assertEqual(expected['labels'],{a:'测试',b:'测试'})
        self.assertEqual(expected['collections'][0]['module_ids'],[a,b])
        backup=self.server.store.backup()
        self.server.store.restore(backup)
        self.assertEqual(self.server.store.backup(),backup)
        self.assertEqual(self.api('/api/state')['module_rules'],expected['module_rules'])
        self.assertEqual(self.apply('rules',dict(keywords='match',label='不同分组',slug='favorites')),[])

    def test_old_link_and_filename_cleanup_preserve_public_identity(self):
        self.login()
        a=self.upload(filename='Rex-demo.js',data=b'REXENC-data')
        self.upload(filename='demo.js')
        url='https://example.com/a.js'
        self.api('/api/modules/'+a,'PUT',{'source_url':url,'auto_refresh':False})
        self.api('/api/collections','POST',{'slug':'stable','module_ids':[a]})
        former=self.api('/api/state')['modules']
        old=next(m for m in former if m['id']==a)
        changes=self.apply('filenames',{})
        self.assertNotIn('rex',changes[0]['new'].lower());self.assertNotEqual(changes[0]['new'],'demo.js')
        self.assertEqual(self.request('/widgets/'+a+'/Rex-demo.js',auth=False)[2],b'REXENC-data')
        self.apply('legacy',{})
        state=self.api('/api/state');m=next(m for m in state['modules'] if m['id']==a)
        for field in ('id','widget_id','sha256','title','version','source_url'):
            self.assertEqual(m[field],old[field])
        self.assertEqual(state['collections'][0]['module_ids'],[a]);self.assertEqual(state['managed_links'],[a])
        self.assertEqual(m['refresh']['auto_refresh'],0)
        self.assertFalse(self.apply('legacy',{}))
        self.assertEqual(self.request('/api/organizer/legacy','POST',{},auth=False)[0],401)

    def test_changed_preview_refused(self):
        self.login()
        a=self.upload(filename='rex-one.js')
        p=self.api('/api/organizer/filenames','POST',{})
        self.upload(filename='one.js')
        self.assertEqual(self.request('/api/organizer/filenames','POST',{'ids':[a],'token':p['token']})[0],409)

    def test_manual_groups_share_rule_labels_and_survive_restore(self):
        self.login()
        a = self.upload(filename='one.js')
        b = self.upload(filename='two.js')
        uri = '/api/module-groups'
        self.api('/api/collections','POST',{'slug':'stable'})
        self.apply('rules',dict(keywords='one',label='旧标签',slug='stable',auto=True))
        self.api(uri,'POST',dict(action='rename',name='旧标签',new_name='媒体'))
        self.api(uri,'POST',dict(action='create',new_name='空组'))
        self.api(uri,'POST',dict(action='assign',ids=[a,b],name='媒体'))
        self.assertEqual(self.request(uri,'POST',dict(action='assign',ids=[b,'missing'],name='空组'))[0],404)
        state = self.api('/api/state')
        self.assertEqual(state['module_groups'],['媒体','空组'])
        self.assertEqual(state['module_rules'][0]['label'],'媒体')
        self.assertEqual(state['labels'],{a:'媒体',b:'媒体'})
        for _ in range(2):
            self.api('/api/collections/stable/members/'+b,'POST',{})
        saved = self.server.store.backup()
        self.server.store.restore(saved)
        self.assertEqual(self.server.store.backup(),saved)
        self.api(uri,'POST',dict(action='delete',name='媒体'))
        state = self.api('/api/state')
        self.assertEqual(state['module_groups'],['空组'])
        self.assertFalse(state['labels'])
        self.assertEqual(state['module_rules'][0]['slug'],'stable')
        self.assertEqual(state['collections'][0]['module_ids'],[a,b])
