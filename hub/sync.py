"""Collection refresh with stable module identities and atomic content updates."""
import base64
import hashlib
import json
import secrets
import threading
import time
from urllib.parse import urljoin, urlsplit
from .core import Problem, prepare_module
from .network import Download, fetch


RETRY_LIFETIME = 7 * 86400
RETRY_STORAGE_LIMIT = 100 * 1024 * 1024


def download_module(store, url, old, conditional):
    validators = None
    if conditional and old:
        with store.connect() as db:
            validators = db.execute(
                'SELECT * FROM fetch_validators WHERE module_id=? AND url=? AND sha256=?',
                (old['id'], url, old['sha256'])).fetchone()
    response = fetch(url, validators=validators) if validators else fetch(url)
    if response[0] is None:
        result = Download(old['content'], response[1], response.etag, response.modified)
        result.transferred = 0
        return result
    return response


def save_validators(db, module, download):
    etag, modified = getattr(download, 'etag', ''), getattr(download, 'modified', '')
    if etag or modified:
        db.execute('''INSERT INTO fetch_validators VALUES (?,?,?,?,?,?)
            ON CONFLICT(module_id) DO UPDATE SET url=excluded.url, final_url=excluded.final_url,
            sha256=excluded.sha256, etag=excluded.etag, modified=excluded.modified
            WHERE (fetch_validators.url, fetch_validators.final_url, fetch_validators.sha256,
                   fetch_validators.etag, fetch_validators.modified) !=
                  (excluded.url, excluded.final_url, excluded.sha256, excluded.etag, excluded.modified)''',
                   (module['id'], module['source_url'], download[1], module['sha256'], etag, modified))
    else:
        db.execute('DELETE FROM fetch_validators WHERE module_id=?', (module['id'],))


def refresh_content(content, old, overrides=None):
    overrides = overrides or {}
    if old and hashlib.sha256(content).hexdigest() == old['sha256']:
        if all(old[key] == value for key, value in overrides.items()):
            return old
        return prepare_module(overrides, old)
    return prepare_module({**overrides, 'data':base64.b64encode(content).decode()}, old)


class Scheduler(threading.Thread):
    def __init__(self, server):
        super().__init__(daemon=True)
        self.server = server
        self.stop = threading.Event()

    def run(self):
        import logging
        while not self.stop.wait(60):
            try:
                with self.server.store.connect() as db:
                    db.execute("DELETE FROM module_previous WHERE expires<=?",(int(time.time()),))
                    db.execute('DELETE FROM collection_retries WHERE saved_at<?', (int(time.time())-RETRY_LIFETIME,))
                self.tick()
            except Exception:logging.getLogger('widget-harbor').exception('Scheduled refresh failed')

    def tick(self):
        with self.server.store.connect() as db:
            rows = db.execute('SELECT slug FROM collection_sync WHERE auto_refresh=1 AND last_attempt < ?', (int(time.time())-86400,)).fetchall()
        with self.server.store.connect() as db:
            icons=[r[0] for r in db.execute("SELECT icon FROM collections UNION SELECT icon FROM asset_folders")]
        for icon in icons:
            if self.stop.is_set():return
            self.server.store.catalog.cache(icon)
        for row in rows:
            if self.stop.is_set():return
            if not self.server.import_lock.acquire(blocking=False):break
            self.server.cancel_import.clear()
            self.run_job(row['slug'], conditional=True)

        with self.server.store.connect() as db:
            ids=[r[0] for r in db.execute("SELECT module_id FROM file_sync JOIN modules ON id=module_id WHERE auto_refresh=1 AND source_url!='' AND last_attempt<? LIMIT 500",(int(time.time())-86400,))]
        if ids and not self.stop.is_set() and self.server.import_lock.acquire(blocking=False):
            self.server.cancel_import.clear()
            self.run_files(ids, conditional=True)

    def refresh_for_player(self, slug, wait_seconds=8):
        """Bounded read-through refresh; retain the atomic cached snapshot on failure."""
        if not self.server.import_lock.acquire(blocking=False):
            return
        handed_off = False
        try:
            with self.server.store.connect() as db:
                collection = next((c for c in self.server.store.collections(db) if c['slug'] == slug), None)
                if not collection:
                    return
                remote = collection['source_url'] or db.execute(
                    "SELECT 1 FROM modules JOIN members ON module_id=id WHERE slug=? AND source_url!='' LIMIT 1",
                    (slug,)).fetchone()
                if not remote or collection['last_attempt'] > int(time.time()) - 60:
                    return
                # Persist the cooldown before starting work, including failed attempts.
                db.execute('UPDATE collection_sync SET last_attempt=? WHERE slug=?', (int(time.time()), slug))
            self.server.cancel_import.clear()
            worker = threading.Thread(target=self.run_job, args=(slug,), kwargs={'conditional':True}, daemon=True)
            worker.start()
            handed_off = True
            worker.join(wait_seconds)
        finally:
            if not handed_off:
                self.server.import_lock.release()

    def refresh_file(self, ident, conditional=False, fetcher=None, remaining=50*1024*1024, cancellable=False):
        store = self.server.store
        with store.connect() as db:
            old = store.get_module(db, ident)
            if not old['source_url']:
                return None, 0
            db.execute('INSERT OR IGNORE INTO file_sync(module_id) VALUES (?)', (ident,))
            db.execute('UPDATE file_sync SET last_attempt=? WHERE module_id=?', (int(time.time()), ident))
        try:
            download = fetcher(old['source_url']) if fetcher else download_module(
                store, old['source_url'], old, conditional)
            transferred = getattr(download, 'transferred', len(download[0]))
            if transferred > remaining:
                raise Problem('批量刷新超过 50 MiB，未处理的文件保持原样')
            updated = refresh_content(download[0], old)
            if cancellable and self.server.cancel_import.is_set():
                raise Problem('刷新已取消')
            with store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if store.get_module(db, ident) != old:
                    raise Problem('文件在刷新期间被修改，请重试', 409)
                if updated is not old:
                    store.save_module(db, updated)
                save_validators(db, updated, download)
                db.execute("UPDATE file_sync SET last_success=?,error='' WHERE module_id=?",
                           (int(time.time()), ident))
        except Exception as exc:
            message = str(exc) if isinstance(exc, Problem) else '刷新失败，请查看服务日志'
            with store.connect() as db:
                # A source edited mid-download should start with a clean status.
                db.execute("""UPDATE file_sync SET error=? WHERE module_id=? AND EXISTS (
                    SELECT 1 FROM modules WHERE id=? AND source_url=?)""",
                           (message, ident, ident, old['source_url']))
            raise
        return updated is not old, transferred

    def run_files(self, ids, conditional=False):
        self.run_sources(ids, [], conditional=conditional)

    def run_sources(self, ids, slugs, *, conditional=True):
        """Refresh selected sources sequentially under the shared job lock."""
        with self.server.job_lock:
            self.server.job={'id':secrets.token_hex(16),'status':'running','done':0,'total':len(ids)+len(slugs)}
        result={'success':0,'skipped':0,'failed':0,'errors':[]}
        deadline=time.monotonic()+300
        total_bytes=0
        tasks=[(slug,True) for slug in slugs]+[(ident,False) for ident in ids]
        try:
            for index,(ident,is_collection) in enumerate(tasks):
                if self.stop.is_set() or self.server.cancel_import.is_set():
                    raise Problem('刷新已取消')
                if time.monotonic()>deadline:
                    raise Problem('批量刷新超时，未处理的来源保持原样')
                try:
                    if is_collection:
                        self.refresh(ident, self.retry_cache(ident), conditional=conditional, report_members=False)
                        changed = True
                    else:
                        changed, transferred = self.refresh_file(
                            ident, conditional, remaining=50*1024*1024-total_bytes, cancellable=True)
                        total_bytes += transferred
                    result['success' if changed else 'skipped']+=1
                except Exception as exc:
                    message=str(exc) if isinstance(exc,Problem) else '刷新失败，请查看服务日志'
                    result['failed']+=1
                    result['errors'].append({'slug' if is_collection else 'id':ident,'message':message})
                    if is_collection:
                        self.collection_error(ident, message)
                    if message.startswith('批量刷新超过'):
                        raise
                finally:
                    with self.server.job_lock:self.server.job.update(done=index+1,result=result)
            if self.stop.is_set() or self.server.cancel_import.is_set():
                raise Problem('刷新已取消')
            with self.server.job_lock:self.server.job.update(status='complete',result=result)
        except Exception as exc:
            with self.server.job_lock:
                self.server.job.update(status='cancelled' if self.server.cancel_import.is_set() else 'failed',
                                       error=str(exc),result=result)
        finally:
            self.server.import_lock.release()

    def collection_error(self, slug, message):
        with self.server.store.connect() as db:
            db.execute('UPDATE collection_sync SET error=? WHERE slug=?', (message, slug))

    def retry_cache(self, slug, job_id=None):
        with self.server.store.connect() as db:
            snapshot = db.execute('SELECT * FROM collection_retries WHERE slug=? AND saved_at>=?',
                                  (slug, int(time.time())-RETRY_LIFETIME)).fetchone()
            if job_id and (not snapshot or snapshot['job_id']!=job_id):
                raise Problem('重试记录已更新，请从链接来源页重试',409)
            if not snapshot:
                return {}
            collection = next((c for c in self.server.store.collections(db) if c['slug']==slug), None)
            if not collection or self.retry_scope(collection)!=snapshot['scope']:
                return {}
            return {tuple(json.loads(r['key'])):Download(r['content'],r['final_url'],r['etag'],r['modified'])
                    for r in db.execute('SELECT * FROM collection_retry_files WHERE slug=?', (slug,))}

    @staticmethod
    def retry_scope(collection):
        return json.dumps([collection['source_url'], collection['module_ids']])

    def save_retry(self, slug, collection, downloaded):
        with self.server.job_lock:
            job_id = self.server.job['id']
        with self.server.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM collections WHERE slug=?', (slug,)).fetchone():
                return
            db.execute('DELETE FROM collection_retries WHERE slug=?', (slug,))
            size = sum(len(r[0]) for r in downloaded.values())
            while db.execute('SELECT COALESCE(sum(length(content)),0) FROM collection_retry_files').fetchone()[0] + size > RETRY_STORAGE_LIMIT:
                db.execute('DELETE FROM collection_retries WHERE slug=(SELECT slug FROM collection_retries ORDER BY saved_at,slug LIMIT 1)')
            db.execute('INSERT INTO collection_retries VALUES (?,?,?,?)',
                       (slug,job_id,self.retry_scope(collection),int(time.time())))
            db.executemany('INSERT INTO collection_retry_files VALUES (?,?,?,?,?,?)',
                           [(slug,json.dumps(key),*value,getattr(value,'etag',''),getattr(value,'modified',''))
                            for key,value in downloaded.items()])

    def run_job(self, slug, retry_job=None, *, conditional=False):
        try:
            with self.server.job_lock:
                self.server.job = {'id':secrets.token_hex(16),'status':'running','done':0,'total':0}
            cache = self.retry_cache(slug,retry_job) if retry_job else {}
            self.refresh(slug,cache,conditional=conditional or bool(retry_job))
            with self.server.job_lock:
                self.server.job.update(status='complete')
        except Exception as exc:
            message = str(exc) if isinstance(exc, Problem) else '刷新失败，请查看服务日志'
            self.collection_error(slug,message)
            with self.server.job_lock:
                self.server.job.update(status='cancelled' if self.server.cancel_import.is_set() else 'failed',error=message)
        finally:
            self.server.import_lock.release()

    def refresh(self, slug, cache=None, *, conditional=False, report_members=True):
        store = self.server.store
        with store.connect() as db:
            collection = next((c for c in store.collections(db) if c['slug']==slug),None)
            if not collection:raise Problem('合集不存在',404)
            originals = {m['id']:dict(m) for m in db.execute("SELECT * FROM modules WHERE id IN (SELECT module_id FROM members WHERE slug=?) OR (? != '' AND source_group=?)", (slug,collection['source_url'],collection['source_url']))}
            db.execute('UPDATE collection_sync SET last_attempt=? WHERE slug=?',(int(time.time()),slug))
        source = collection['source_url']
        deadline = time.monotonic()+300
        pending=[]; ids=[]; total=0; headers={}
        if source:
            data, final = fetch(source)
            try:manifest=json.loads(data)
            except ValueError:raise Problem('远程订阅不是有效 JSON') from None
            widgets=manifest.get('widgets') if isinstance(manifest,dict) else None
            if not isinstance(widgets,list) or not 1<=len(widgets)<=100:raise Problem('远程订阅须包含 1–100 个模块')
            for key in ('title','description','icon'):
                if key in manifest:
                    if not isinstance(manifest[key],str):raise Problem('远程合集资料字段必须是文本')
                    headers[key]=manifest[key]
            if 'title' in headers and not headers['title'].strip():raise Problem('远程合集名称不能为空')
            if headers.get('icon'):headers['icon']=urljoin(final,headers['icon'])
            candidates=[m for m in originals.values() if m['id'] in collection['module_ids'] or m['source_group']==source]
            entries=[]
            for w in widgets:
                if not isinstance(w,dict) or not isinstance(w.get('url'),str):raise Problem('模块缺少来源')
                url=urljoin(final,w['url'])
                old=next((m for m in candidates if m['source_url']==url),None)
                if old is None and w.get('id'):
                    matches=[m for m in candidates if m['widget_id']==w['id']]
                    if len(matches)==1:old=matches[0]
                entries.append((url,w,old))
        else:
            manifests = {}
            entries = []
            for ident in collection['module_ids']:
                if self.stop.is_set() or self.server.cancel_import.is_set():raise Problem('刷新已取消')
                if time.monotonic()>deadline:raise Problem('刷新超时，请减少模块数量')
                old = originals[ident]
                url, meta = old['source_url'], {}
                group = old['source_group']
                if url and group.startswith(('https://','http://')) and group != url:
                    if group not in manifests:
                        data, resolved = fetch(group)
                        try: upstream = json.loads(data)
                        except ValueError: raise Problem('模块来源订阅不是有效 JSON') from None
                        if not isinstance(upstream,dict) or not isinstance(upstream.get('widgets'),list):
                            raise Problem('模块来源订阅缺少 widgets')
                        manifests[group] = (upstream['widgets'],resolved)
                    widgets, resolved = manifests[group]
                    matches = [w for w in widgets if isinstance(w,dict) and isinstance(w.get('url'),str) and urljoin(resolved,w['url'])==url]
                    if not matches:
                        matches = [w for w in widgets if isinstance(w,dict) and w.get('id')==old['widget_id'] and isinstance(w.get('url'),str)]
                    if len(matches)==1:
                        meta = matches[0]
                        url = urljoin(resolved,meta['url'])
                entries.append((url,meta,old))
        failures=[]
        downloaded={}
        for url,meta,old in entries:
            if self.stop.is_set() or self.server.cancel_import.is_set():raise Problem('刷新已取消')
            if time.monotonic()>deadline:raise Problem('刷新超时，请减少模块数量')
            if not url:
                ids.append(old['id']);continue
            key=(url,old['sha256'] if old else None,json.dumps(meta,sort_keys=True))
            try:
                download=cache[key] if cache is not None and key in cache else download_module(store,url,old,conditional)
                content,resolved=download
            except Problem as exc:
                failures.append(dict(id=old['id'] if old else '',title=meta.get('title') or (old['title'] if old else url),url=url,message=str(exc)))
                continue
            transferred = getattr(download,'transferred',len(content))
            total += transferred
            if total>50*1024*1024:raise Problem('本次刷新超过 50 MiB')
            if cache is not None and transferred:
                downloaded[key]=download
            overrides={k:v for k,v in meta.items() if k in ('title','description','author','version','requiredVersion','icon','site') and isinstance(v,str) and v.strip()}
            name=urlsplit(resolved).path.rsplit('/',1)[-1]
            overrides['source_url']=url
            if source:overrides['source_group']=source
            if not old:
                platforms={m['platform'] for m in candidates}
                overrides.update(filename=name if name.endswith('.js') else 'widget.js',
                                 platform=next(iter(platforms)) if len(platforms)==1 else 'both')
                if meta.get('id'):overrides['widget_id']=meta['id']
            updated=refresh_content(content,old,overrides)
            if updated['id'] in ids:raise Problem('远程订阅包含重复模块')
            pending.append((updated,download));ids.append(updated['id'])
            if report_members:
                with self.server.job_lock:self.server.job.update(done=len(ids),total=len(entries))
        if self.stop.is_set() or self.server.cancel_import.is_set():raise Problem('刷新已取消')
        if failures:
            if cache is not None:
                self.save_retry(slug,collection,downloaded)
                if report_members:
                    with self.server.job_lock:
                        self.server.job.update(retry_slug=slug,result=dict(errors=failures,failed=len(failures)))
            raise Problem(failures[0]['message'])
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=next((c for c in store.collections(db) if c['slug']==slug),None)
            if not current or current['source_url']!=source or current['module_ids']!=collection['module_ids'] or any(current[k]!=collection[k] for k in ('title','description','icon')):
                raise Problem('合集在刷新期间被修改，请重试',409)
            for m, download in pending:
                old=originals.get(m['id'])
                if old:
                    live=store.get_module(db,m['id'])
                    if live!=old:raise Problem('模块在刷新期间被修改，请重试',409)
                if m is not old:
                    store.save_module(db,m)
                save_validators(db,m,download)
            if source and (ids!=current['module_ids'] or any(current[k]!=value for k,value in headers.items())):
                # Remote collection membership follows upstream; removed files remain
                # available to custom collections and are never deleted implicitly.
                store.save_collection(db,{**current,**headers,'module_ids':ids},True)
            db.execute("UPDATE collection_sync SET last_success=?,error='' WHERE slug=?",(int(time.time()),slug))
            db.execute('DELETE FROM collection_retries WHERE slug=?', (slug,))

        if source and headers.get('icon') and (not conditional or headers['icon'] != collection['icon']):
            store.catalog.cache(headers['icon'],force=True)
