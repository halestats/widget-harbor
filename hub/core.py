"""Storage and inert metadata parsing. Uploaded JavaScript is never executed."""
import base64
import hashlib
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

MAX_FILE = 5 * 1024 * 1024
FIELDS = ('id', 'title', 'description', 'version', 'author', 'requiredVersion', 'icon', 'site')
PLATFORMS = ('forward', 'rex', 'both')


class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def text(value, limit=500):
    if not isinstance(value, str) or len(value) > limit or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise Problem('字段必须是文本，且不能超过 %s 个字符' % limit)
    return value


def metadata(data):
    if data.startswith((b'FWENC1', b'REXENC')):
        return {}
    source = data.decode('utf-8-sig', errors='replace')
    start = re.search(r'\bWidgetMetadata\s*(?:=\s*)?\{', source)
    if not start:
        return {}
    # Tokenize strings/comments rather than evaluating source or stripping URL slashes.
    tokens = re.finditer(r'''//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[A-Za-z_$][\w$]*|[{}\[\]:,]|[^\s]''', source[start.end()-1:])
    depth, key, expect, result = 0, None, False, {}
    for match in tokens:
        tok = match.group()
        if tok.startswith(('//', '/*')):
            continue
        if tok in ('{', '['):
            depth += 1
            continue
        if tok in ('}', ']'):
            depth -= 1
            if depth == 0:
                break
            continue
        if depth != 1:
            continue
        if tok == ',':
            key, expect = None, False
        elif tok == ':':
            expect = True
        elif key is None:
            key = tok.strip("\"'")
        elif expect:
            if key in FIELDS and tok[0] in ('"', "'", '`') and not (tok[0] == '`' and '${' in tok):
                raw = tok[1:-1]
                # Only literal strings; computed values remain unset.
                raw = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m[1], 16)), raw)
                raw = raw.encode('utf-16', errors='surrogatepass').decode('utf-16', errors='replace')
                result[key] = re.sub(r'\\([\\\'"/nrt])', lambda m: {'n':'\n','r':'\r','t':'\t'}.get(m[1], m[1]), raw)[:2000]
            expect = False
    return result


def prepare_module(payload, old=None):
    if not isinstance(payload, dict):
        raise Problem('模块数据必须是对象')
    old = old or {}
    filename = text(payload.get('filename', old.get('filename', 'widget.js')), 180)
    if not filename or '/' in filename or '\\' in filename or any(ord(c) < 32 for c in filename):
        raise Problem('文件名无效')
    if 'data' in payload:
        try:
            data = base64.b64decode(payload['data'], validate=True)
        except (ValueError, TypeError):
            raise Problem('文件编码无效') from None
    else:
        data = old.get('content', b'')
    if not data or len(data) > MAX_FILE:
        raise Problem('文件不能为空且不能超过 5 MiB')
    kind = payload.get('kind', old.get('kind', 'module'))
    if kind not in ('module', 'asset'):
        raise Problem('文件类型无效')
    if kind == 'module' and not filename.lower().endswith('.js'):
        raise Problem('模块文件必须使用 .js 扩展名')
    platform = payload.get('platform', old.get('platform', 'both'))
    if platform not in PLATFORMS:
        raise Problem('平台必须为 forward / rex / both')
    meta = metadata(data) if kind == 'module' else {}
    # On content refresh, use new metadata; on information-only edits preserve it.
    if 'data' not in payload:
        meta = {k: old.get(k, '') for k in FIELDS}
        meta['id'] = old.get('widget_id', '')
    result = {k: text(payload.get(k, meta.get(k) or old.get(k, '')), 2000) for k in FIELDS if k != 'id'}
    result.update(id=old.get('id', uuid.uuid4().hex), widget_id=text(payload.get('widget_id', meta.get('id') or old.get('widget_id', '') or uuid.uuid4().hex), 2000),
                  filename=filename, content=data, kind=kind, platform=platform,
                  source_url=text(payload.get('source_url', old.get('source_url', '')), 4000),
                  source_group=text(payload.get('source_group', old.get('source_group', '')), 4000),
                  encrypted=int(data.startswith((b'FWENC1', b'REXENC'))), size=len(data),
                  sha256=hashlib.sha256(data).hexdigest(), updated_at=int(time.time()))
    if 'description' not in payload and not result['description'] and old.get('description'):
        result['description'] = old['description']
    result['title'] = result['title'] or filename
    result['version'] = result['version'] or '1.0.0'
    result['requiredVersion'] = result['requiredVersion'] or '0.0.1'
    return result


class Store:
    def __init__(self, directory):
        Path(directory).mkdir(parents=True, exist_ok=True)
        self.path = str(Path(directory) / 'hub.sqlite3')
        with self.connect() as db:
            if db.execute('PRAGMA user_version').fetchone()[0] not in (0, 1):
                raise ValueError('数据库版本不受支持，请使用匹配版本并按迁移文档操作')
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS modules (
              id TEXT PRIMARY KEY, widget_id TEXT, filename TEXT, title TEXT,
              description TEXT, version TEXT, author TEXT, requiredVersion TEXT,
              icon TEXT, site TEXT, kind TEXT, platform TEXT, source_url TEXT,
              encrypted INTEGER, size INTEGER, sha256 TEXT, updated_at INTEGER, content BLOB);
            CREATE TABLE IF NOT EXISTS collections (
              slug TEXT PRIMARY KEY, title TEXT, description TEXT, icon TEXT);
            CREATE TABLE IF NOT EXISTS members (
              slug TEXT REFERENCES collections(slug) ON DELETE CASCADE,
              module_id TEXT REFERENCES modules(id) ON DELETE CASCADE,
              position INTEGER, PRIMARY KEY(slug, module_id));
            CREATE TABLE IF NOT EXISTS collection_sync (
              slug TEXT PRIMARY KEY REFERENCES collections(slug) ON DELETE CASCADE,
              source_url TEXT NOT NULL DEFAULT '', auto_refresh INTEGER NOT NULL DEFAULT 0,
              last_attempt INTEGER NOT NULL DEFAULT 0, last_success INTEGER NOT NULL DEFAULT 0,
              error TEXT NOT NULL DEFAULT '', origin TEXT NOT NULL DEFAULT 'custom');
            CREATE TABLE IF NOT EXISTS app_migrations (name TEXT PRIMARY KEY);
            PRAGMA user_version=1;
            ''')

            if 'origin' not in [r[1] for r in db.execute('PRAGMA table_info(collection_sync)')]:
                db.execute("ALTER TABLE collection_sync ADD COLUMN origin TEXT NOT NULL DEFAULT 'custom'")
                db.execute("UPDATE collection_sync SET origin='imported' WHERE source_url != ''")
            if 'source_group' not in [r[1] for r in db.execute('PRAGMA table_info(modules)')]:
                db.execute("ALTER TABLE modules ADD COLUMN source_group TEXT NOT NULL DEFAULT ''")

            if not db.execute("SELECT 1 FROM app_migrations WHERE name='metadata-1.3'").fetchone():
                for row in db.execute("SELECT id,content FROM modules WHERE kind='module' AND encrypted=0").fetchall():
                    if row['content'].startswith((b'FWENC1',b'REXENC')):
                        db.execute('UPDATE modules SET encrypted=1 WHERE id=?',(row['id'],))
                # Fill only missing descriptions; never replace authored descriptions.
                for row in db.execute("SELECT id,content FROM modules WHERE kind='module' AND COALESCE(description,'')='' ").fetchall():
                    description = metadata(row['content']).get('description', '')
                    if description:
                        db.execute('UPDATE modules SET description=? WHERE id=?', (description,row['id']))
                db.execute("INSERT INTO app_migrations VALUES ('metadata-1.3')")
            # Earlier imports kept source_group on modules but not on collections.
            # Only infer an unambiguous, remote manifest for an import-created slug.
            from urllib.parse import urlsplit
            for row in db.execute("SELECT slug FROM collections WHERE slug NOT IN (SELECT slug FROM collection_sync)").fetchall():
                groups=[r[0] for r in db.execute('SELECT DISTINCT m.source_group FROM modules m JOIN members x ON x.module_id=m.id WHERE x.slug=?',(row['slug'],))]
                source=''
                if row['slug'].startswith('import-') and len(groups)==1 and groups[0]:
                    u=urlsplit(groups[0])
                    if u.scheme in ('http','https') and u.hostname and not u.username:
                        source=groups[0]
                db.execute('INSERT INTO collection_sync(slug,source_url,auto_refresh,last_attempt,origin) VALUES (?,?,?,?,?)',(row['slug'],source,int(bool(source)),int(time.time()),'imported' if row['slug'].startswith('import-') else 'custom'))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def save_module(self, db, module):
        keys = list(module)
        db.execute('INSERT INTO modules (' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ') ON CONFLICT(id) DO UPDATE SET ' + ','.join(k+'=excluded.'+k for k in keys if k != 'id'), list(module.values()))

    def get_module(self, db, ident):
        row = db.execute('SELECT * FROM modules WHERE id=?', (ident,)).fetchone()
        if not row:
            raise Problem('文件不存在', 404)
        return dict(row)

    def collections(self, db):
        result = []
        for row in db.execute('SELECT * FROM collections ORDER BY rowid DESC'):
            item = dict(row)
            sync = db.execute('SELECT * FROM collection_sync WHERE slug=?', (row['slug'],)).fetchone()
            item.update(dict(sync) if sync else dict(source_url='',auto_refresh=0,last_attempt=0,last_success=0,error='',origin='custom'))
            item['module_ids'] = [r[0] for r in db.execute('SELECT module_id FROM members WHERE slug=? ORDER BY position', (row['slug'],))]
            result.append(item)
        return result

    def save_collection(self, db, obj, update=False):
        if not isinstance(obj, dict):
            raise Problem('合集必须是对象')
        slug = text(obj.get('slug', ''), 64)
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', slug):
            raise Problem('链接标识只允许字母、数字、短横线和下划线')
        ids = obj.get('module_ids', [])
        if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)):
            raise Problem('合集最多包含 100 个不重复模块')
        for ident in ids:
            if self.get_module(db, ident)['kind'] != 'module':
                raise Problem('资源文件不能加入模块合集')
        vals = [slug, text(obj.get('title', slug), 200), text(obj.get('description', ''), 2000), text(obj.get('icon', ''), 2000)]
        exists = db.execute('SELECT 1 FROM collections WHERE slug=?', (slug,)).fetchone()
        if exists and not update:
            raise Problem('链接标识已存在', 409)
        if update and not exists:
            raise Problem('合集不存在', 404)
        db.execute('INSERT INTO collections VALUES (?,?,?,?) ON CONFLICT(slug) DO UPDATE SET title=excluded.title,description=excluded.description,icon=excluded.icon', vals)
        db.execute('DELETE FROM members WHERE slug=?', (slug,))
        db.executemany('INSERT INTO members VALUES (?,?,?)', [(slug, ident, pos) for pos, ident in enumerate(ids)])
        previous = db.execute('SELECT * FROM collection_sync WHERE slug=?', (slug,)).fetchone()
        source = text(obj.get('source_url', previous['source_url'] if previous else ''), 4000)
        if source:
            from urllib.parse import urlsplit
            parsed = urlsplit(source)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username:
                raise Problem('远程来源必须是 HTTP/HTTPS URL')
        auto = int(bool(obj.get('auto_refresh', previous['auto_refresh'] if previous else False)))
        origin = 'imported' if source else obj.get('origin',previous['origin'] if previous else 'custom')
        if origin not in ('imported','custom'):raise Problem('合集类型无效')
        db.execute("INSERT INTO collection_sync(slug,source_url,auto_refresh) VALUES (?,?,?) ON CONFLICT(slug) DO UPDATE SET source_url=excluded.source_url,auto_refresh=excluded.auto_refresh", (slug,source,auto))
        db.execute('UPDATE collection_sync SET origin=? WHERE slug=?',(origin,slug))
        return slug

    def backup(self):
        with self.connect() as db:
            db.execute('BEGIN')
            modules = []
            for row in db.execute('SELECT * FROM modules'):
                item = dict(row)
                item['data'] = base64.b64encode(item.pop('content')).decode()
                modules.append(item)
            return dict(format='widget-harbor', schema=1, modules=modules, collections=self.collections(db))

    def restore(self, obj):
        if not isinstance(obj, dict) or obj.get('format') != 'widget-harbor' or obj.get('schema') != 1:
            raise Problem('仅支持 Widget Harbor v1 备份')
        if not isinstance(obj.get('modules'), list) or not isinstance(obj.get('collections'), list):
            raise Problem('备份结构无效')
        if len(obj['modules']) > 10000 or len(obj['collections']) > 10000:
            raise Problem('备份记录过多')
        with self.connect() as db:
            db.execute('DELETE FROM collections')
            db.execute('DELETE FROM modules')
            seen = set()
            for item in obj['modules']:
                if not isinstance(item, dict) or not re.fullmatch(r'[a-f0-9]{32}', str(item.get('id', ''))):
                    raise Problem('备份模块 ID 无效')
                if item['id'] in seen:
                    raise Problem('备份存在重复模块 ID')
                seen.add(item['id'])
                module = prepare_module(item)
                module['id'] = item['id']
                stamp = item.get('updated_at', module['updated_at'])
                if not isinstance(stamp, int) or isinstance(stamp, bool) or not 0 <= stamp <= 2**53:
                    raise Problem('备份更新时间无效')
                module['updated_at'] = stamp
                self.save_module(db, module)
            for collection in reversed(obj['collections']):
                slug = self.save_collection(db, collection)
                stamps = [collection.get(k,0) for k in ('last_attempt','last_success')]
                if any(not isinstance(v,int) or isinstance(v,bool) or not 0<=v<=2**53 for v in stamps):
                    raise Problem('备份刷新时间无效')
                db.execute('UPDATE collection_sync SET last_attempt=?,last_success=?,error=? WHERE slug=?', (*stamps,text(collection.get('error',''),2000),slug))
