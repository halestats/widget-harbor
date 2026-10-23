"""Image storage, flat resource folders and persistent library order."""
import base64
import hashlib
import os
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from .core import Problem, text

SCHEMA = '''
CREATE TABLE IF NOT EXISTS images (id TEXT PRIMARY KEY, mime TEXT NOT NULL, content BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS image_sources (source TEXT PRIMARY KEY, image_id TEXT REFERENCES images(id), checked INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS asset_folders (id TEXT PRIMARY KEY, name TEXT NOT NULL, icon TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS asset_locations (module_id TEXT PRIMARY KEY REFERENCES modules(id) ON DELETE CASCADE, folder_id TEXT REFERENCES asset_folders(id) ON DELETE SET NULL);
CREATE TABLE IF NOT EXISTS library_order (scope TEXT NOT NULL, item_id TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(scope,item_id));
CREATE TABLE IF NOT EXISTS legacy_icons (module_id TEXT PRIMARY KEY REFERENCES modules(id) ON DELETE CASCADE);
'''


def image_type(data):
    if not data or len(data)>1024*1024:raise Problem('图片不能为空且不能超过 1 MiB')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):return 'image/png'
    if data.startswith(b'\xff\xd8\xff'):return 'image/jpeg'
    if data.startswith(b'RIFF') and data[8:12]==b'WEBP':return 'image/webp'
    if data.startswith((b'GIF87a',b'GIF89a')):return 'image/gif'
    raise Problem('仅支持 PNG、JPEG、WebP、GIF 图片')


class Catalog:
    def __init__(self, store):
        self.store=store
        self.directory=Path(store.path).parent/'images'
        self.directory.mkdir(exist_ok=True)

    def put(self, db, data, source=''):
        mime=image_type(data);ident=hashlib.sha256(data).hexdigest()
        db.execute('INSERT OR IGNORE INTO images VALUES (?,?,?)',(ident,mime,data))
        if source:db.execute('INSERT OR REPLACE INTO image_sources VALUES (?,?,?)',(source,ident,int(time.time())))
        self.materialize(ident,data)
        return '/images/'+ident

    def materialize(self, ident, data):
        path=self.directory/ident
        if not path.exists():
            tmp=self.directory/(ident+'.'+uuid.uuid4().hex+'.tmp')
            try:tmp.write_bytes(data);os.replace(tmp,path)
            finally:tmp.unlink(missing_ok=True)
        return path

    def cache(self, source, force=False):
        if not source or source.startswith('/images/'):return source
        with self.store.connect() as db:
            old=db.execute('SELECT * FROM image_sources WHERE source=?',(source,)).fetchone()
            if old and not force and old['checked']>time.time()-86400:return '/images/'+old['image_id'] if old['image_id'] else source
        try:
            from .network import fetch
            data,_=fetch(source)
            with self.store.connect() as db:url=self.put(db,data,source)
            self.materialize(url.rsplit('/',1)[1],data)
            return url
        except Problem:
            with self.store.connect() as db:
                db.execute('INSERT OR REPLACE INTO image_sources VALUES (?,?,?)',(source,old['image_id'] if old else None,int(time.time())))
            return '/images/'+old['image_id'] if old and old['image_id'] else source

    def display(self, db, source):
        row=db.execute('SELECT image_id FROM image_sources WHERE source=?',(source,)).fetchone()
        return '/images/'+row[0] if row and row[0] else source

    def migrate_icons(self, db):
        # Preserve legacy public file links while removing collection icons from assets.
        for c in db.execute('SELECT icon FROM collections').fetchall():
            match=re.fullmatch(r'/api/modules/([a-f0-9]{32})/raw',urlsplit(c['icon'] or '').path)
            if not match:continue
            m=db.execute("SELECT * FROM modules WHERE id=? AND kind='asset'",(match[1],)).fetchone()
            if not m:continue
            try:self.put(db,m['content'],c['icon'])
            except Problem:continue
            db.execute('INSERT OR IGNORE INTO legacy_icons VALUES (?)',(m['id'],))

    def state(self, db):
        folders=[dict(r) for r in db.execute('SELECT * FROM asset_folders ORDER BY rowid')]
        for f in folders:f['icon_display']=self.display(db,f['icon'])
        orders={}
        for row in db.execute('SELECT * FROM library_order ORDER BY position'):orders.setdefault(row['scope'],[]).append(row['item_id'])
        return dict(folders=folders,orders=orders,locations={r['module_id']:r['folder_id'] or '' for r in db.execute('SELECT * FROM asset_locations')})

    def folder(self, db, obj, ident=None):
        name=text(obj.get('name',''),100).strip();icon=text(obj.get('icon',''),4000)
        if not name:raise Problem('文件夹名称不能为空')
        if ident and not db.execute('SELECT 1 FROM asset_folders WHERE id=?',(ident,)).fetchone():raise Problem('文件夹不存在',404)
        ident=ident or uuid.uuid4().hex
        db.execute('INSERT INTO asset_folders VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,icon=excluded.icon',(ident,name,icon))
        return ident

    def move(self, db, ids, folder):
        if not isinstance(ids,list) or not 1<=len(ids)<=500 or not all(isinstance(i,str) for i in ids):raise Problem('请选择 1–500 个资源文件')
        if folder and not db.execute('SELECT 1 FROM asset_folders WHERE id=?',(folder,)).fetchone():raise Problem('文件夹不存在',404)
        for ident in ids:
            if self.store.get_module(db,ident)['kind']!='asset':raise Problem('只有资源文件可移入文件夹')
            db.execute('INSERT OR REPLACE INTO asset_locations VALUES (?,?)',(ident,folder or None))

    def reorder(self, db, scope, ids):
        if scope not in ('modules','assets','collections','folders'):raise Problem('排序范围无效')
        if not isinstance(ids,list) or len(ids)>10000 or not all(isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):raise Problem('排序列表无效')
        if scope=='collections':valid={r[0] for r in db.execute('SELECT slug FROM collections')}
        elif scope=='folders':valid={r[0] for r in db.execute('SELECT id FROM asset_folders')}
        else:valid={r[0] for r in db.execute('SELECT id FROM modules WHERE kind=?',('module' if scope=='modules' else 'asset',))}
        if set(ids)-valid:raise Problem('排序对象已变化，请刷新重试',409)
        # Partial reorder preserves other positions, useful for filtered/folder views.
        old=[r[0] for r in db.execute('SELECT item_id FROM library_order WHERE scope=? ORDER BY position',(scope,)) if r[0] in valid]
        old+=sorted(valid-set(old))
        wanted=set(ids);iterator=iter(ids)
        merged=[next(iterator) if i in wanted else i for i in old]
        db.execute('DELETE FROM library_order WHERE scope=?',(scope,))
        db.executemany('INSERT INTO library_order VALUES (?,?,?)',[(scope,i,n) for n,i in enumerate(merged)])

    def export(self, db):
        return dict(images=[dict(id=r['id'],mime=r['mime'],data=base64.b64encode(r['content']).decode()) for r in db.execute('SELECT * FROM images')],image_sources=[dict(r) for r in db.execute('SELECT * FROM image_sources')],folders=[dict(r) for r in db.execute('SELECT * FROM asset_folders')],locations=[dict(r) for r in db.execute('SELECT * FROM asset_locations')],orders=[dict(r) for r in db.execute('SELECT * FROM library_order')],legacy_icons=[r[0] for r in db.execute('SELECT * FROM legacy_icons')])

    def restore(self, db, obj):
        if not isinstance(obj,dict):raise Problem('图库备份结构无效')
        for key in ('images','image_sources','folders','locations','orders','legacy_icons'):
            if not isinstance(obj.get(key,[]),list) or len(obj.get(key,[]))>10000:raise Problem('图库备份结构无效')
        for table in ('image_sources','images','asset_locations','asset_folders','library_order','legacy_icons'):db.execute('DELETE FROM '+table)
        for item in obj.get('images',[]):
            try:data=base64.b64decode(item['data'],validate=True)
            except (KeyError,ValueError,TypeError):raise Problem('图片备份无效') from None
            if self.put(db,data)!='/images/'+str(item.get('id')):raise Problem('图片摘要不匹配')
        for row in obj.get('image_sources',[]):db.execute('INSERT INTO image_sources VALUES (?,?,?)',(text(row.get('source'),4000),text(row['image_id'],64) if row.get('image_id') else None,row.get('checked',0)))
        for row in obj.get('folders',[]):
            if not re.fullmatch('[a-f0-9]{32}',str(row.get('id',''))):raise Problem('文件夹 ID 无效')
            name=text(row.get('name'),100).strip()
            if not name:raise Problem('文件夹名称不能为空')
            db.execute('INSERT INTO asset_folders VALUES (?,?,?)',(row['id'],name,text(row.get('icon',''),4000)))
        for row in obj.get('locations',[]):self.move(db,[row.get('module_id')],row.get('folder_id'))
        groups={}
        for row in obj.get('orders',[]):groups.setdefault(row['scope'],[]).append(row['item_id'])
        for scope,ids in groups.items():self.reorder(db,scope,ids)
        for ident in obj.get('legacy_icons',[]):db.execute('INSERT INTO legacy_icons VALUES (?)',(ident,))
