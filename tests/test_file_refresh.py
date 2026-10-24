import unittest
from unittest.mock import patch
import test_sync
from hub.core import Problem
from hub.network import destination


class FileRefreshTests(unittest.TestCase):
    setUp=test_sync.SyncTests.setUp
    tearDown=test_sync.SyncTests.tearDown
    module=test_sync.SyncTests.module
    def run_files(self, ids):
        self.assertTrue(self.server.import_lock.acquire(False))
        self.server.scheduler.run_files(ids)
        self.assertFalse(self.server.import_lock.locked())
        return self.server.job

    def test_bulk_partial_failure_and_folder_identity(self):
        good=self.module('https://example.com:5090/a.json',kind='asset',filename='a.json',content=b'old')
        bad=self.module('https://example.com/b.json',kind='asset',filename='b.json',content=b'keep')
        local=self.module(kind='asset',filename='local.txt',content=b'local')
        with self.store.connect() as db:
            folder=self.store.catalog.folder(db,{'name':'Files'})
            self.store.catalog.move(db,[good['id']],folder)
        with patch('hub.sync.fetch',side_effect=[(b'new','https://example.com:5090/a.json'),Problem('offline')]):
            job=self.run_files([good['id'],bad['id'],local['id']])
        self.assertEqual(job['status'],'complete')
        self.assertEqual([job['result'][k] for k in ('success','failed','skipped')],[1,1,1])
        with self.store.connect() as db:
            self.assertEqual(self.store.get_module(db,good['id'])['content'],b'new')
            self.assertEqual(self.store.get_module(db,bad['id'])['content'],b'keep')
            self.assertEqual(self.store.catalog.state(db)['locations'][good['id']],folder)
            self.assertEqual(db.execute('SELECT error FROM file_sync WHERE module_id=?',(bad['id'],)).fetchone()[0],'offline')

    def test_daily_retry_and_backup(self):
        m=self.module('https://example.com/a.json',kind='asset',filename='a.json',content=b'old')
        with self.store.connect() as db:db.execute('INSERT INTO file_sync(module_id,auto_refresh) VALUES (?,1)',(m['id'],))
        with patch('hub.sync.fetch',side_effect=Problem('offline')) as download:
            self.server.scheduler.tick();self.server.scheduler.tick()
            self.assertEqual(download.call_count,1)
        backup=self.store.backup();self.store.restore(backup)
        self.assertEqual(self.store.backup(),backup)

    def test_cancel_and_concurrent_edit(self):
        m=self.module('https://example.com/a.json',kind='asset',filename='a.json',content=b'old')
        self.server.cancel_import.set()
        with patch('hub.sync.fetch') as download:
            self.assertEqual(self.run_files([m['id']])['status'],'cancelled');download.assert_not_called()
        self.server.cancel_import.clear()
        def download(url):
            with self.store.connect() as db:db.execute("UPDATE modules SET title='User edit' WHERE id=?",(m['id'],))
            return b'new',url
        with patch('hub.sync.fetch',side_effect=download):self.assertEqual(self.run_files([m['id']])['result']['failed'],1)
        with self.store.connect() as db:self.assertEqual(self.store.get_module(db,m['id'])['content'],b'old')

    def test_public_custom_port(self):
        with patch('hub.network.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',5090))]):
            self.assertEqual(destination('https://example.com:5090/widget.js')[2],5090)
        with patch('hub.network.socket.getaddrinfo',return_value=[(2,1,6,'',('192.168.1.1',5090))]):
            with self.assertRaises(Problem):destination('https://example.com:5090/widget.js')
