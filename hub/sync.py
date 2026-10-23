"""Collection refresh with stable module identities and atomic content updates."""
import base64
import json
import secrets
import threading
import time
from urllib.parse import urljoin, urlsplit
from .core import Problem, prepare_module
from .network import fetch


class Scheduler(threading.Thread):
    def __init__(self, server):
        super().__init__(daemon=True)
        self.server = server
        self.stop = threading.Event()

    def run(self):
        import logging
        while not self.stop.wait(60):
            try:self.tick()
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
            self.run_job(row['slug'])

    def run_job(self, slug):
        try:
            with self.server.job_lock:
                self.server.job = {'id':secrets.token_hex(16),'status':'running','done':0,'total':0}
            self.refresh(slug)
            with self.server.job_lock:
                self.server.job.update(status='complete')
        except Exception as exc:
            message = str(exc) if isinstance(exc, Problem) else '刷新失败，请查看服务日志'
            with self.server.store.connect() as db:
                db.execute('UPDATE collection_sync SET error=? WHERE slug=?',(message,slug))
            with self.server.job_lock:
                self.server.job.update(status='cancelled' if self.server.cancel_import.is_set() else 'failed',error=message)
        finally:
            self.server.import_lock.release()

    def refresh(self, slug):
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
        for url,meta,old in entries:
            if self.stop.is_set() or self.server.cancel_import.is_set():raise Problem('刷新已取消')
            if time.monotonic()>deadline:raise Problem('刷新超时，请减少模块数量')
            if not url:
                ids.append(old['id']);continue
            content,resolved=fetch(url);total+=len(content)
            if total>50*1024*1024:raise Problem('本次刷新超过 50 MiB')
            overrides={k:v for k,v in meta.items() if k in ('title','description','author','version','requiredVersion','icon','site') and isinstance(v,str) and v.strip()}
            name=urlsplit(resolved).path.rsplit('/',1)[-1]
            payload={**overrides,'data':base64.b64encode(content).decode(),'source_url':url}
            if not old:
                platforms={m['platform'] for m in candidates}
                payload.update(filename=name if name.endswith('.js') else 'widget.js',platform=next(iter(platforms)) if len(platforms)==1 else 'both',source_group=source)
                if meta.get('id'):payload['widget_id']=meta['id']
            updated=prepare_module(payload,old)
            if source:updated['source_group']=source
            if updated['id'] in ids:raise Problem('远程订阅包含重复模块')
            pending.append(updated);ids.append(updated['id'])
            with self.server.job_lock:self.server.job.update(done=len(ids),total=len(entries))
        if self.stop.is_set() or self.server.cancel_import.is_set():raise Problem('刷新已取消')
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=next((c for c in store.collections(db) if c['slug']==slug),None)
            if not current or current['source_url']!=source or current['module_ids']!=collection['module_ids'] or any(current[k]!=collection[k] for k in ('title','description','icon')):
                raise Problem('合集在刷新期间被修改，请重试',409)
            for m in pending:
                old=originals.get(m['id'])
                if old:
                    live=store.get_module(db,m['id'])
                    if live!=old:raise Problem('模块在刷新期间被修改，请重试',409)
                store.save_module(db,m)
            if source:
                # Remote collection membership follows upstream; removed files remain
                # available to custom collections and are never deleted implicitly.
                store.save_collection(db,{**current,**headers,'module_ids':ids},True)
            db.execute("UPDATE collection_sync SET last_success=?,error='' WHERE slug=?",(int(time.time()),slug))

        if source and headers.get('icon'):store.catalog.cache(headers['icon'],force=True)
