import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import os
import secrets
import tempfile
import threading
import time
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote, quote, urljoin
from .core import Store, Problem, prepare_module, text, MAX_FILE
from .network import fetch

ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = 64 * 1024 * 1024
LOG = logging.getLogger('widget-harbor')


class HubServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, address, directory, password, site_url):
        site_url = site_url.rstrip('/')
        parsed = urlsplit(site_url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('SITE_URL 必须是完整 HTTP/HTTPS 站点地址，不含路径')
        if len(password) < 12 or password == 'change-me-at-least-12-characters':
            raise ValueError('ADMIN_PASSWORD 至少 12 个字符，请修改示例密码')
        self.store = Store(directory)
        self.site_url = site_url
        self.allowed_origins = {site_url, *[v.strip().rstrip('/') for v in os.environ.get('ALLOWED_ORIGINS', '').split(',') if v.strip()]}
        for origin in self.allowed_origins:
            parsed_origin = urlsplit(origin)
            if parsed_origin.scheme not in ('http', 'https') or not parsed_origin.hostname or parsed_origin.path or parsed_origin.query or parsed_origin.fragment or parsed_origin.username:
                raise ValueError('ALLOWED_ORIGINS 必须是逗号分隔的完整站点地址')
        self.job = {'status':'idle'}
        self.job_lock = threading.Lock()
        self.cancel_import = threading.Event()
        self.salt = secrets.token_bytes(16)
        self.password_hash = hashlib.scrypt(password.encode(), salt=self.salt, n=16384, r=8, p=1)
        self.credentials_path = Path(directory) / 'admin-auth.json'
        if self.credentials_path.exists():
            credentials = json.loads(self.credentials_path.read_text())
            self.salt = bytes.fromhex(credentials['salt'])
            self.password_hash = bytes.fromhex(credentials['hash'])
            if len(self.salt) != 16 or len(self.password_hash) != 64:
                raise ValueError('管理员凭据文件损坏，请从备份恢复')
        self.sessions, self.failures = {}, {}
        self.auth_lock = threading.Lock()
        self.import_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(24)
        super().__init__(address, Handler)
        from .sync import Scheduler
        self.scheduler = Scheduler(self)
        self.scheduler.start()

    def server_close(self):
        self.scheduler.stop.set()
        super().server_close()

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'WidgetHarbor/1.0'

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, fmt, *args):
        # Never log URLs, cookies, credentials or subscription query strings.
        LOG.info('%s %s', self.command, str(args[1]) if len(args) > 1 else '')

    def send(self, status, value, mime='application/json; charset=utf-8', headers=None):
        if not isinstance(value, bytes):
            value = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(value)))
        self.send_header('Cache-Control', (headers or {}).get('Cache-Control','no-store'))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: https: http:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, val in (headers or {}).items():
            if key.lower()!='cache-control':self.send_header(key, val)
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(value)

    def body(self):
        if self.headers.get('Transfer-Encoding'):
            raise Problem('不支持分块请求体', 400)
        if self.headers.get_content_type() != 'application/json':
            raise Problem('请求必须是 application/json', 415)
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            raise Problem('请求长度无效') from None
        if size <= 0 or size > MAX_BODY:
            raise Problem('请求体为空或超过 64 MiB', 413)
        try:
            obj = json.loads(self.rfile.read(size))
        except (ValueError, UnicodeError):
            raise Problem('JSON 数据无效') from None
        if not isinstance(obj, dict):
            raise Problem('请求必须是 JSON 对象')
        return obj

    def session_token(self):
        try:
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            val = cookie.get('hub_session')
            return hashlib.sha256(val.value.encode()).hexdigest() if val else ''
        except CookieError:
            return ''

    def authenticated(self):
        with self.server.auth_lock:
            return self.server.sessions.get(self.session_token(), 0) > time.time()

    def protect_write(self):
        if self.headers.get('X-Hub-Request') != '1':
            raise Problem('缺少请求保护头', 403)
        origin = self.headers.get('Origin')
        if origin and origin not in self.server.allowed_origins:
            raise Problem('请求来源与 SITE_URL 不一致', 403)

    def cookie(self, value, age):
        origin = self.headers.get('Origin', self.server.site_url)
        secure = '; Secure' if origin in self.server.allowed_origins and origin.startswith('https:') else ''
        return f'hub_session={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}'

    def login(self, obj):
        password = text(obj.get('password', ''), 1024)
        now = time.time()
        # Global bucket deliberately independent of spoofable proxy headers.
        with self.server.auth_lock:
            attempts = [t for t in self.server.failures.get('login', []) if t > now - 600]
            self.server.failures['login'] = attempts
            if len(attempts) >= 20:
                raise Problem('登录尝试过多，请 10 分钟后重试', 429)
            attempts.append(now)
        with self.server.auth_lock:
            candidate = hashlib.scrypt(password.encode(), salt=self.server.salt, n=16384, r=8, p=1)
            if not hmac.compare_digest(candidate, self.server.password_hash):
                raise Problem('密码不正确', 401)
            token = secrets.token_urlsafe(32)
            self.server.sessions = {k:v for k,v in self.server.sessions.items() if v > now}
            if len(self.server.sessions) >= 100:
                self.server.sessions.pop(next(iter(self.server.sessions)))
            self.server.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + 43200
            self.server.failures['login'] = []
        self.send(200, {'ok':True}, headers={'Set-Cookie':self.cookie(token, 43200)})

    def change_password(self, obj):
        current = text(obj.get('current_password'), 1024)
        password = text(obj.get('new_password'), 1024)
        if not isinstance(current, str) or not isinstance(password, str):
            raise Problem('请填写当前密码和新密码')
        if not 12 <= len(password) <= 1024 or len(current) > 1024:
            raise Problem('新密码须为 12–1024 个字符')
        if password == current or password == 'change-me-at-least-12-characters':
            raise Problem('请使用不同于当前密码的新密码')
        with self.server.auth_lock:
            if self.server.sessions.get(self.session_token(), 0) <= time.time():
                raise Problem('请重新登录', 401)
            now = time.time()
            attempts = [t for t in self.server.failures.get('password', []) if t > now - 600]
            self.server.failures['password'] = attempts
            if len(attempts) >= 20:
                raise Problem('验证尝试过多，请 10 分钟后重试', 429)
            attempts.append(now)
            candidate = hashlib.scrypt(current.encode(), salt=self.server.salt, n=16384, r=8, p=1)
            if not hmac.compare_digest(candidate, self.server.password_hash):
                raise Problem('当前密码不正确', 400)
            salt = secrets.token_bytes(16)
            hashed = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
            # Atomic replacement: failed writes leave the working password intact.
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', dir=self.server.credentials_path.parent,
                                                 prefix='.admin-auth-', delete=False) as out:
                    temporary = Path(out.name)
                    json.dump({'salt': salt.hex(), 'hash': hashed.hex()}, out)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(temporary, self.server.credentials_path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            self.server.salt, self.server.password_hash = salt, hashed
            self.server.sessions.clear()
            self.server.failures.clear()
        self.send(200, {'ok': True}, headers={'Set-Cookie': self.cookie('', 0)})

    def public_file(self, ident, download=False):
        with self.server.store.connect() as db:
            module = self.server.store.get_module(db, ident)
        mime = 'application/javascript' if module['kind'] == 'module' else mimetypes.guess_type(module['filename'])[0] or 'application/octet-stream'
        # Active documents are always downloads, with a sandbox for defense in depth.
        active = mime in ('text/html', 'image/svg+xml', 'application/xhtml+xml', 'text/xml', 'application/xml')
        headers = {'Content-Security-Policy':"sandbox; default-src 'none'", 'ETag':'"'+module['sha256']+'"'}
        if download or active:
            headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(module['filename'])
        self.send(200, module['content'], mime, headers)

    def subscription(self, slug, platform):
        with self.server.store.connect() as db:
            row = db.execute('SELECT * FROM collections WHERE slug=?', (slug,)).fetchone()
            if not row:
                raise Problem('合集不存在', 404)
            result = {k:row[k] for k in ('title','description','icon')}
            rows = db.execute('SELECT m.* FROM modules m JOIN members x ON x.module_id=m.id WHERE x.slug=? ORDER BY x.position', (slug,))
            result['icon']=self.server.store.catalog.display(db,result['icon'])
            if result['icon'].startswith('/images/'):result['icon']=self.server.site_url+result['icon']
            result['widgets'] = []
            for m in rows:
                if m['platform'] not in (platform, 'both'):
                    continue
                widget = {k:m[k] for k in ('title','description','requiredVersion','version','author','icon','site')}
                widget.update(id=m['widget_id'], url=f'{self.server.site_url}/api/modules/{m["id"]}/raw?v={m["sha256"][:12]}')
                result['widgets'].append(widget)
        self.send(200, result)

    def import_data(self, obj):
        url = text(obj.get('url', ''), 4000)
        if url:
            data, final_url = fetch(url)
            filename = unquote(urlsplit(final_url).path.rsplit('/', 1)[-1]) or 'widget.js'
        else:
            final_url = ''
            filename = text(obj.get('filename', 'collection.fwd'), 180)
            try:
                data = base64.b64decode(obj.get('data', ''), validate=True)
            except (ValueError, TypeError):
                raise Problem('文件编码无效') from None
        if not data or len(data) > MAX_FILE:
            raise Problem('导入文件不能为空且不能超过 5 MiB')
        is_manifest = obj.get('kind') != 'asset' and (filename.lower().endswith(('.fwd', '.rex', '.json')) or obj.get('manifest') is True)
        if not is_manifest:
            if obj.get('kind', 'module') == 'module' and not filename.lower().endswith('.js'):
                filename = 'widget.js'
            module = prepare_module({**obj, 'data':base64.b64encode(data).decode(), 'filename':filename, 'source_url':url})
            if self.server.cancel_import.is_set():
                raise Problem('导入已取消')
            with self.server.store.connect() as db:
                self.server.store.save_module(db, module)
            return {'id':module['id']}
        try:
            manifest = json.loads(data)
        except (ValueError, UnicodeError):
            raise Problem('订阅文件不是有效 JSON') from None
        if not isinstance(manifest, dict) or not isinstance(manifest.get('widgets'), list) or not 1 <= len(manifest['widgets']) <= 50:
            raise Problem('导入订阅须包含 1–50 个 widgets')
        modules, total = [], 0
        deadline = time.monotonic() + 120
        for widget in manifest['widgets']:
            if self.server.cancel_import.is_set():
                raise Problem('导入已取消')
            with self.server.job_lock:
                self.server.job.update(done=len(modules), total=len(manifest['widgets']))
            if time.monotonic() > deadline:
                raise Problem('订阅导入超过 120 秒，请拆分合集', 504)
            if not isinstance(widget, dict):
                raise Problem('订阅中的模块条目无效')
            source = text(widget.get('url', ''), 4000)
            if not source:
                raise Problem('订阅模块缺少 URL')
            source = urljoin(final_url, source)
            content, resolved = fetch(source)
            total += len(content)
            if total > 30 * 1024 * 1024:
                raise Problem('单次合集导入总文件大小超过 30 MiB')
            name = unquote(urlsplit(resolved).path.rsplit('/', 1)[-1])
            if not name.lower().endswith('.js'):
                name = f'widget-{len(modules)+1}.js'
            overrides = {k:v for k,v in widget.items() if k in ('title','description','version','author','requiredVersion','icon','site') and isinstance(v, str) and v.strip()}
            module = prepare_module({**overrides, 'filename':name, 'data':base64.b64encode(content).decode(), 'source_url':source,
                                     'platform':obj.get('platform', 'both'), 'source_group':url or filename, **({'widget_id':widget['id']} if widget.get('id') else {})})
            modules.append(module)
        collection = {'slug':obj.get('slug') or 'import-'+secrets.token_hex(4), 'title':manifest.get('title', '导入的合集'),
                      'source_url':url, 'origin':'imported', 'auto_refresh':bool(url), 'description':manifest.get('description', ''), 'icon':manifest.get('icon', ''), 'module_ids':[m['id'] for m in modules]}
        if self.server.cancel_import.is_set():
            raise Problem('导入已取消')
        # Download and validate everything before a single atomic transaction.
        with self.server.store.connect() as db:
            for module in modules:
                self.server.store.save_module(db, module)
            slug = self.server.store.save_collection(db, collection)
            db.execute('UPDATE collection_sync SET last_attempt=?,last_success=? WHERE slug=?',(int(time.time()),int(time.time()),slug))
        self.server.store.catalog.cache(collection.get('icon',''))
        return {'slug':slug, 'imported':len(modules)}

    def dispatch(self):
        path = unquote(urlsplit(self.path).path)
        method = self.command
        if method in ('GET','HEAD'):
            if path == '/healthz':
                with self.server.store.connect() as db:
                    db.execute('SELECT 1').fetchone()
                return self.send(200, {'status':'ok'})
            if path in ('/', '/app.js', '/style.css', '/favicon.svg'):
                filename = 'index.html' if path == '/' else path[1:]
                mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
                return self.send(200, (ROOT/'static'/filename).read_bytes(), mime+'; charset=utf-8')
            import re
            image_match=re.fullmatch(r'/images/([a-f0-9]{64})',path)
            if image_match:
                with self.server.store.connect() as db:row=db.execute('SELECT * FROM images WHERE id=?',(image_match[1],)).fetchone()
                if not row:raise Problem('图片不存在',404)
                self.server.store.catalog.materialize(row['id'],row['content'])
                return self.send(200,row['content'],row['mime'],{'Cache-Control':'public, max-age=31536000, immutable'})
            match = re.fullmatch(r'/api/collections/([\w-]+)\.(fwd|rex)', path)
            if match:
                return self.subscription(match[1], 'forward' if match[2]=='fwd' else 'rex')
            match = re.fullmatch(r'/api/modules/([a-f0-9]{32})/raw', path)
            if match:
                return self.public_file(match[1])
            match = re.fullmatch(r'/(widgets|download)/([a-f0-9]{32})/(.+)', path)
            if match:
                return self.public_file(match[2], match[1] == 'download')
            if path == '/api/session':
                return self.send(200, {'authenticated':self.authenticated(), 'site_url':self.server.site_url})
        if method not in ('GET','HEAD'):
            self.protect_write()
            if path == '/api/login' and method == 'POST':
                return self.login(self.body())
        if not self.authenticated():
            raise Problem('请先登录', 401)
        if path == '/api/logout' and method == 'POST':
            with self.server.auth_lock:
                self.server.sessions.pop(self.session_token(), None)
            return self.send(200, {'ok':True}, headers={'Set-Cookie':self.cookie('', 0)})
        if path == '/api/password' and method == 'POST':
            return self.change_password(self.body())
        store = self.server.store
        catalog=store.catalog
        if path=='/api/images' and method=='POST':
            obj=self.body()
            try:data=base64.b64decode(obj.get('data',''),validate=True)
            except (ValueError,TypeError):raise Problem('图片编码无效') from None
            with store.connect() as db:url=catalog.put(db,data)
            catalog.materialize(url.rsplit('/',1)[1],data)
            return self.send(201,{'url':url})
        if path=='/api/order' and method=='PUT':
            obj=self.body()
            with store.connect() as db:catalog.reorder(db,obj.get('scope'),obj.get('ids'))
            return self.send(200,{'ok':True})
        if path=='/api/assets/move' and method=='POST':
            obj=self.body()
            with store.connect() as db:catalog.move(db,obj.get('ids'),text(obj.get('folder_id',''),32))
            return self.send(200,{'ok':True})
        import re
        folder_match=re.fullmatch(r'/api/folders/([a-f0-9]{32})',path)
        if (path=='/api/folders' and method=='POST') or (folder_match and method=='PUT'):
            obj=self.body()
            with store.connect() as db:ident=catalog.folder(db,obj,folder_match[1] if folder_match else None)
            catalog.cache(obj.get('icon',''))
            return self.send(200,{'id':ident})
        if folder_match and method=='DELETE':
            with store.connect() as db:db.execute('DELETE FROM asset_folders WHERE id=?',(folder_match[1],))
            return self.send(200,{'ok':True})
        if path == '/api/import/status' and method == 'GET':
            with self.server.job_lock:
                return self.send(200, self.server.job)
        if path == '/api/import/cancel' and method == 'POST':
            self.server.cancel_import.set()
            return self.send(200, {'ok':True})
        if path == '/api/profile' and method in ('GET', 'PUT'):
            with store.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY, avatar TEXT)')
                if method == 'PUT':
                    avatar = text(self.body().get('avatar', ''), 1500000)
                    import re
                    if avatar and not re.fullmatch(r'data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+', avatar):
                        raise Problem('头像须为 PNG/JPEG/WebP 图片')
                    db.execute('INSERT OR REPLACE INTO profile VALUES (1, ?)', (avatar,))
                row = db.execute('SELECT avatar FROM profile WHERE id=1').fetchone()
            return self.send(200, {'avatar':row[0] if row else ''})
        if path == '/api/modules/batch' and method == 'POST':
            obj = self.body()
            ids = obj.get('ids')
            if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or not all(isinstance(i,str) for i in ids):
                raise Problem('请选择 1–500 个文件')
            with store.connect() as db:
                for ident in ids:
                    store.get_module(db, ident)
                for ident in ids:
                    if obj.get('action') == 'delete':
                        db.execute('DELETE FROM modules WHERE id=?', (ident,))
                    elif obj.get('action') == 'platform' and obj.get('platform') in ('both','rex','forward'):
                        db.execute('UPDATE modules SET platform=? WHERE id=? AND kind="module"', (obj['platform'], ident))
                    else:
                        raise Problem('批量操作无效')
            return self.send(200, {'ok':True})
        import re
        refresh_match = re.fullmatch(r'/api/collections/([\w-]+)/refresh', path)
        if refresh_match and method == 'POST':
            slug = refresh_match[1]
            if not self.server.import_lock.acquire(blocking=False):
                raise Problem('已有导入或刷新正在进行，请稍后重试',409)
            with store.connect() as db:
                if not db.execute('SELECT 1 FROM collections WHERE slug=?',(slug,)).fetchone():
                    self.server.import_lock.release()
                    raise Problem('合集不存在',404)
            self.server.cancel_import.clear()
            with self.server.job_lock:
                self.server.job = {'id':secrets.token_hex(16),'status':'running','done':0,'total':0}
            threading.Thread(target=self.server.scheduler.run_job,args=(slug,),daemon=True).start()
            return self.send(202,{'ok':True})
        if path == '/api/state' and method == 'GET':
            with store.connect() as db:
                modules = [{k:v for k,v in dict(r).items() if k != 'content'} for r in db.execute('SELECT * FROM modules WHERE id NOT IN (SELECT module_id FROM legacy_icons) ORDER BY updated_at DESC, rowid DESC')]
                collections = store.collections(db)
                extra=catalog.state(db)
                for c in collections:c['icon_display']=catalog.display(db,c['icon'])
            return self.send(200, {'modules':modules, 'collections':collections, 'site_url':self.server.site_url,**extra})
        if path == '/api/backup' and method == 'GET':
            return self.send(200, store.backup(), headers={'Content-Disposition':'attachment; filename="widget-harbor-backup.json"'})
        if path == '/api/restore' and method == 'POST':
            obj = self.body()
            if obj.get('confirm') != 'RESTORE':
                raise Problem('需要确认覆盖恢复')
            store.restore(obj.get('backup'))
            return self.send(200, {'ok':True})
        if path in ('/api/import', '/api/import/start') and method == 'POST':
            obj = self.body()
            if not self.server.import_lock.acquire(blocking=False):
                raise Problem('已有导入正在进行，请稍后重试', 409)
            self.server.cancel_import.clear()
            if path.endswith('/start'):
                with self.server.job_lock:
                    self.server.job = {'id':secrets.token_hex(16),'status':'running', 'done':0, 'total':0}
                def run():
                    try:
                        result = self.import_data(obj)
                        with self.server.job_lock:
                            self.server.job.update(status='complete', result=result)
                    except Exception as exc:
                        with self.server.job_lock:
                            self.server.job.update(status='cancelled' if self.server.cancel_import.is_set() else 'failed', error=str(exc) if isinstance(exc, Problem) else '导入失败，请查看服务日志')
                    finally:
                        self.server.import_lock.release()
                threading.Thread(target=run, daemon=True).start()
                return self.send(202, {'ok':True})
            try:
                result = self.import_data(obj)
            finally:
                self.server.import_lock.release()
            return self.send(201, result)
        if path == '/api/modules' and method == 'POST':
            payload=self.body()
            module = prepare_module(payload)
            with store.connect() as db:
                store.save_module(db, module)
                if module['kind']=='asset' and payload.get('folder_id'):catalog.move(db,[module['id']],text(payload['folder_id'],32))
            return self.send(201, {'id':module['id']})
        import re
        match = re.fullmatch(r'/api/modules/([a-f0-9]{32})(/refresh)?', path)
        if match:
            ident = match[1]
            with store.connect() as db:
                old = store.get_module(db, ident)
            if method == 'DELETE' and not match[2]:
                with store.connect() as db:
                    db.execute('DELETE FROM modules WHERE id=?', (ident,))
                return self.send(200, {'ok':True})
            if method == 'PUT' and not match[2]:
                obj = self.body()
                if old['encrypted'] and 'data' in obj and not obj.get('replace'):
                    raise Problem('加密模块仅支持替换文件')
                updated = prepare_module(obj, old)
            elif method == 'POST' and match[2]:
                if not old['source_url']:
                    raise Problem('该文件没有远程来源')
                data, _ = fetch(old['source_url'])
                updated = prepare_module({'data':base64.b64encode(data).decode()}, old)
            else:
                raise Problem('不支持该请求', 405)
            with store.connect() as db:
                current = store.get_module(db, ident)
                if current['sha256'] != old['sha256']:
                    raise Problem('文件已被其他操作更新，请刷新页面后重试', 409)
                store.save_module(db, updated)
            return self.send(200, {'ok':True})
        if path == '/api/collections' and method in ('POST','PUT'):
            obj = self.body()
            with store.connect() as db:
                slug = store.save_collection(db, obj, method=='PUT')
                catalog.migrate_icons(db)
            catalog.cache(obj.get('icon',''))
            return self.send(200, {'slug':slug})
        match = re.fullmatch(r'/api/collections/([\w-]+)', path)
        if match and method == 'DELETE':
            with store.connect() as db:
                ids = [r[0] for r in db.execute('SELECT module_id FROM members WHERE slug=?', (match[1],))]
                db.execute('DELETE FROM collections WHERE slug=?', (match[1],))
                if urlsplit(self.path).query == 'delete_modules=1':
                    for ident in ids:
                        db.execute('DELETE FROM modules WHERE id=? AND NOT EXISTS (SELECT 1 FROM members WHERE module_id=?)', (ident, ident))
            return self.send(200, {'ok':True})
        raise Problem('接口不存在', 404)

    def handle_request(self):
        try:
            self.dispatch()
        except Problem as exc:
            self.send(exc.status, {'error':str(exc)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception:
            LOG.exception('Request failed')
            self.send(500, {'error':'服务器错误，请检查服务日志'})

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = handle_request


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    server = HubServer((os.environ.get('HOST', '0.0.0.0'), int(os.environ.get('PORT', '8765'))),
                       os.environ.get('DATA_DIR', '/data'), os.environ.get('ADMIN_PASSWORD', ''),
                       os.environ.get('SITE_URL', 'http://localhost:8765'))
    LOG.info('Widget Harbor listening on port %s', server.server_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
