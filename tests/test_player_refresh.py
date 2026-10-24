import json
import threading
import unittest
from unittest.mock import patch
import test_sync
import test_hub
from hub.core import Problem


class PlayerRefreshTests(unittest.TestCase):
    setUp = test_sync.SyncTests.setUp
    tearDown = test_sync.SyncTests.tearDown
    module = test_sync.SyncTests.module
    collection = test_sync.SyncTests.collection

    def test_latest_and_cooldown(self):
        m = self.module('https://example.com/a.js')
        self.collection('mixed', [m, self.module()])
        updated = test_sync.SOURCE.replace(b'version:"2"', b'version:"3"')
        with patch('hub.sync.fetch', return_value=(updated, m['source_url'])) as fetched:
            self.server.scheduler.refresh_for_player('mixed')
            self.server.scheduler.refresh_for_player('mixed')
            self.assertEqual(fetched.call_count, 1)
        with self.store.connect() as db:
            self.assertEqual(self.store.get_module(db, m['id'])['version'], '3')

    def test_failure_keeps_cache_and_throttles(self):
        m = self.module('https://example.com/a.js')
        self.collection('mixed', [m])
        with patch('hub.sync.fetch', side_effect=Problem('offline')) as fetched:
            self.server.scheduler.refresh_for_player('mixed')
            self.server.scheduler.refresh_for_player('mixed')
            self.assertEqual(fetched.call_count, 1)
        with self.store.connect() as db:
            self.assertEqual(self.store.get_module(db, m['id']), m)
            self.assertEqual(self.store.collections(db)[0]['error'], 'offline')

    def test_timeout_busy_local_and_missing(self):
        local = self.module()
        self.collection('local', [local])
        with patch('hub.sync.fetch') as fetched:
            self.server.scheduler.refresh_for_player('local')
            self.server.scheduler.refresh_for_player('missing')
            fetched.assert_not_called()
        m = self.module('https://example.com/a.js')
        self.collection('mixed', [m])
        started, finish = threading.Event(), threading.Event()
        def download(url):
            started.set()
            finish.wait(3)
            return test_sync.SOURCE.replace(b'version:"2"', b'version:"3"'), url
        with patch('hub.sync.fetch', side_effect=download) as fetched:
            try:
                self.server.scheduler.refresh_for_player('mixed', wait_seconds=0.01)
                self.assertTrue(started.wait(1))
                with self.store.connect() as db:
                    self.assertEqual(self.store.get_module(db, m['id'])['version'], '2')
                self.server.scheduler.refresh_for_player('mixed')
                self.assertEqual(fetched.call_count, 1)
            finally:
                finish.set()
                self.assertTrue(self.server.import_lock.acquire(timeout=3))
                self.server.import_lock.release()
        with self.store.connect() as db:
            self.assertEqual(self.store.get_module(db, m['id'])['version'], '3')


class PlayerApiTests(unittest.TestCase):
    setUp = test_hub.ApiTests.setUp
    tearDown = test_hub.ApiTests.tearDown
    request = test_hub.ApiTests.request
    login = test_hub.ApiTests.login
    upload = test_hub.ApiTests.upload

    def test_subscription_get_refreshes_but_head_does_not(self):
        self.login()
        ident = self.upload()
        self.request('/api/collections', 'POST', {'slug':'player','title':'Player','module_ids':[ident]})
        with patch.object(self.server.scheduler, 'refresh_for_player') as refresh:
            for extension in ('rex', 'fwd'):
                status, _, body = self.request('/api/collections/player.'+extension)
                self.assertEqual(status, 200)
                self.assertEqual(len(json.loads(body)['widgets']), 1)
            self.assertEqual(refresh.call_count, 2)
            self.assertEqual(self.request('/api/collections/player.rex', 'HEAD')[0], 200)
            self.assertEqual(refresh.call_count, 2)
