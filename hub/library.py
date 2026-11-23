"""Library maintenance features. Package inspection never extracts or executes scripts."""
import base64
import fnmatch
import hashlib
import io
import json
import re
import secrets
import time
import zipfile
import zlib

from .core import Problem, metadata, prepare_module, text
from .home_checks import DETAIL_LIMIT, check_item, local_name, reference_host

WEEK = 7 * 86400
HOME_LIMIT = 25 * 1024 * 1024
HOME_AVATAR_LIMIT = 5 * 1024 * 1024
HOME_METADATA_LIMIT = 8 * 1024 * 1024  # The Base64 avatar may use up to 6.67 MiB.
HOME_CHECK_REVISION = 1
META_KEYS = ('title', 'version', 'description', 'author', 'requiredVersion', 'icon',
             'site', 'filename', 'widget_id')
SCHEMA = """
CREATE TABLE IF NOT EXISTS module_previous (
 module_id TEXT PRIMARY KEY REFERENCES modules(id) ON DELETE CASCADE,
 expires INTEGER NOT NULL, info TEXT NOT NULL, content BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS homes (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, summary TEXT NOT NULL,
 content BLOB NOT NULL, share TEXT UNIQUE, sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS home_checks (
 home_id TEXT PRIMARY KEY REFERENCES homes(id) ON DELETE CASCADE,
 sha TEXT NOT NULL, revision INTEGER NOT NULL, stats TEXT NOT NULL, report TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS folder_rules (
 position INTEGER PRIMARY KEY, pattern TEXT NOT NULL,
 folder TEXT NOT NULL REFERENCES asset_folders(id) ON DELETE CASCADE);
"""


def candidates(db, m):
    internal = metadata(m['content']).get('id') if not m['encrypted'] else None
    rows = db.execute("""SELECT id,title,filename,version,sha256,widget_id FROM modules
        WHERE kind='module' AND (sha256=? OR widget_id=? OR filename=? COLLATE NOCASE)""",
                      (m['sha256'], internal, m['filename']))
    matches = []
    for row in rows:
        item = dict(row)
        item['reason'] = ('内容相同' if item['sha256'] == m['sha256'] else
                          '模块 ID 相同' if item['widget_id'] == internal else '文件同名')
        item['collections'] = [r[0] for r in db.execute(
            'SELECT c.title FROM members JOIN collections c USING(slug) WHERE module_id=?', (item['id'],))]
        matches.append(item)
    return matches


def keep_previous(db, module):
    row = db.execute("SELECT * FROM modules WHERE id=? AND kind='module' AND sha256!=?",
                     (module['id'], module['sha256'])).fetchone()
    if row:
        info = {k: row[k] for k in META_KEYS}
        db.execute('INSERT OR REPLACE INTO module_previous VALUES (?,?,?,?)',
                   (row['id'], int(time.time()) + WEEK, json.dumps(info), row['content']))


def rollback(store, db, ident, expected):
    db.execute('BEGIN IMMEDIATE')
    current = store.get_module(db, ident)
    if expected != current['sha256']:
        raise Problem('文件已更新，请重新查看', 409)
    row = db.execute('SELECT * FROM module_previous WHERE module_id=? AND expires>?',
                     (ident, int(time.time()))).fetchone()
    if not row:
        raise Problem('没有可恢复的上一版', 404)
    payload = json.loads(row['info'])
    payload['data'] = base64.b64encode(row['content']).decode()
    store.save_module(db, prepare_module(payload, current))


def read_home_json(archive, name, limit=4 * 1024 * 1024):
    if archive.getinfo(name).file_size > limit:
        raise Problem(f'首页配置 JSON 超过 {limit//(1024*1024)} MiB')
    return json.loads(archive.read(name))


def inspect_home(data, *, report=False):
    summary, details = inspect_home_parts(data, report=report)
    return details if report else summary


def inspect_home_parts(data, *, report):
    if not 0 < len(data) <= HOME_LIMIT:
        raise Problem('首页包须小于等于 25 MiB')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            files = archive.infolist()
            names = {f.filename for f in files if not f.is_dir()}
            if (len(files) > 1024 or len(set(archive.namelist())) != len(files)
                    or sum(f.file_size for f in files) > 64 * 1024 * 1024):
                raise Problem('首页包包含重复条目或解压后过大')
            manifest = read_home_json(archive, 'rex-manifest.json')
            version = manifest.get('version')
            if manifest.get('format') != 'video.fika.rex.home-package' or type(version) is not int or version not in (1, 2):
                raise Problem('不支持的 Rex 首页格式')
            home = read_home_json(archive, 'rex-home.json', HOME_METADATA_LIMIT)
            layout = read_home_json(archive, 'rex-layout.json')
            if not isinstance(layout, list) or len(layout) > 1000:
                raise Problem('首页布局无效')
            refs = set()
            report_refs = set()
            templates, cards, warnings = {}, [], []
            referenced_scripts = set()
            card_count = local_count = warning_count = 0
            count = 0
            for section_number, section in enumerate(layout, 1):
                items = section.get('items', [])
                if not isinstance(items, list):
                    raise Problem('首页卡片无效')
                count += len(items)
                if count > 10000:
                    raise Problem('首页卡片过多')
                for item_number, item in enumerate(items, 1):
                    url = text(item.get('widgetURL', ''), 4000)
                    ident = text(item.get('widgetId') or item.get('widgetBundleId') or '', 2000)
                    if ident or url:
                        refs.add((ident, url))
                    position = f'栏目 {section_number} / 卡片 {item_number}'
                    card, issues, local, script = check_item(archive, names, item, position, templates, url)
                    if card:
                        card_count += 1
                        if report and len(cards) < DETAIL_LIMIT:
                            cards.append(card)
                    local_count += local
                    if script:
                        referenced_scripts.add(script)
                    warning_count += len(issues)
                    if report:
                        warnings.extend(f'{position} · {issue}' for issue in issues[:DETAIL_LIMIT - len(warnings)])
                        if ident or url:
                            host = reference_host(url)
                            location = '本机模块' if local else '内置脚本' if script else host or '无远程地址'
                            report_refs.add((ident, location))
            scripts = sorted(name for name in names if name.startswith('rex-scripts/')
                             and local_name(name[12:]) and name.lower().endswith('.js'))
            package_warnings = []
            if version == 1 and card_count:
                package_warnings.append('此包包含 HTML 卡片，但声明的格式版本为 v1')
            if any(name.startswith('rex-html/') and name not in templates for name in names):
                package_warnings.append('包里有未被布局引用的 HTML 文件')
            if any(name[12:] not in referenced_scripts for name in scripts):
                package_warnings.append('包里有未被布局引用的内置脚本')
            avatar=home_avatar(home.get('avatarData'))
            stats = dict(version=version, html_cards=card_count, scripts=len(scripts), local_items=local_count,
                         warnings=warning_count + len(package_warnings))
            summary = dict(avatar=avatar, title=text(home.get('title') or '未命名首页', 200),
                           sections=len(layout), items=count, refs=[dict(id=i, url=u) for i,u in sorted(refs)], **stats)
            if not report:
                return summary, None
            script_details = []
            for name in scripts[:DETAIL_LIMIT]:
                with archive.open(name) as stream:
                    encrypted = stream.read(6) in (b'REXENC', b'FWENC1')
                script_details.append(dict(name=name[12:], size=archive.getinfo(name).file_size, encrypted=encrypted))
            return summary, dict(version=version, stats=stats, cards=cards, scripts=script_details,
                        references=[dict(id=i, source=s) for i, s in sorted(report_refs)[:DETAIL_LIMIT]],
                        warnings=(package_warnings + warnings)[:DETAIL_LIMIT],
                        truncated=max(card_count, len(scripts), len(report_refs), summary['warnings']) > DETAIL_LIMIT)
    except (zipfile.BadZipFile, KeyError, ValueError, TypeError, AttributeError,
            RuntimeError, OSError, zlib.error, RecursionError, EOFError):
        raise Problem('不是有效的 Rex 首页包，请选择导出的首页 .rex 文件') from None


def home_avatar(encoded):
    if not isinstance(encoded, str):
        return None
    if encoded[:5].lower() == 'data:':
        _, _, encoded = encoded.partition(',')
    if not encoded or len(encoded) > 4 * ((HOME_AVATAR_LIMIT + 2) // 3):
        return None
    from .catalog import image_type
    try:
        raw = base64.b64decode(encoded, validate=True)
        return dict(data=encoded, mime=image_type(raw, size_limit=HOME_AVATAR_LIMIT))
    except (Problem, ValueError):
        return None


def backfill_home_avatars(db):
    migration = db.execute("INSERT OR IGNORE INTO app_migrations(name) VALUES ('home-avatar-5mib')")
    if not migration.rowcount:
        return
    missing = db.execute("SELECT id FROM homes WHERE json_extract(summary, '$.avatar') IS NULL").fetchall()
    for (ident,) in missing:
        content, previous = db.execute('SELECT content,summary FROM homes WHERE id=?', (ident,)).fetchone()
        try:
            avatar = inspect_home(content)['avatar']
        except Problem:
            continue
        if avatar:
            summary = json.loads(previous)
            summary['avatar'] = avatar
            db.execute('UPDATE homes SET summary=? WHERE id=?', (json.dumps(summary), ident))


def save_home(db, obj, ident=None, share=None):
    try:
        data = base64.b64decode(obj['data'], validate=True)
    except (KeyError, ValueError, TypeError):
        raise Problem('首页文件编码无效') from None
    return save_home_data(db, data, obj.get('title'), ident, share)


def save_home_data(db, data, title=None, ident=None, share=None, inspected=None):
    summary, report = inspected if inspected is not None else inspect_home_parts(data, report=True)
    digest = hashlib.sha256(data).hexdigest()
    title = text(title or summary['title'], 200)
    if ident is None:
        ident = secrets.token_hex(16)
    db.execute('''INSERT INTO homes VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        title=excluded.title,summary=excluded.summary,content=excluded.content,sha=excluded.sha''',
               (ident, title, json.dumps(summary), data, share, digest))
    cache_home_check(db, ident, digest, report)
    return ident


def cache_home_check(db, ident, digest, report):
    db.execute('''INSERT INTO home_checks(home_id, sha, revision, stats, report)
        SELECT id, sha, ?, ?, ? FROM homes WHERE id=? AND sha=?
        ON CONFLICT(home_id) DO UPDATE SET sha=excluded.sha, revision=excluded.revision,
            stats=excluded.stats, report=excluded.report''',
        (HOME_CHECK_REVISION, json.dumps(report['stats']), json.dumps(report), ident, digest))


def check_home(store, ident):
    with store.connect() as db:
        cached = db.execute('''SELECT c.report FROM home_checks c JOIN homes h ON h.id=c.home_id AND h.sha=c.sha
            WHERE c.home_id=? AND c.revision=?''', (ident, HOME_CHECK_REVISION)).fetchone()
        if cached:
            return json.loads(cached['report'])
        row = db.execute('SELECT sha, content FROM homes WHERE id=?', (ident,)).fetchone()
    if not row:
        raise Problem('首页不存在', 404)
    report = inspect_home(row['content'], report=True)
    with store.connect() as db:
        cache_home_check(db, ident, row['sha'], report)
    return report


def classify(db, pattern, folder):
    pattern = text(pattern, 180).strip().casefold()
    if not pattern or not db.execute('SELECT 1 FROM asset_folders WHERE id=?',(folder,)).fetchone():
        raise Problem('请填写文件名规则并选择已有文件夹')
    rows = db.execute("""SELECT id,filename FROM modules LEFT JOIN asset_locations ON module_id=id
        WHERE kind='asset' AND folder_id IS NULL AND id NOT IN (SELECT module_id FROM legacy_icons)
        ORDER BY filename,id""")
    return [dict(r) for r in rows if fnmatch.fnmatchcase(r['filename'].casefold(), pattern)]


def classify_new(db, m):
    for r in db.execute('SELECT * FROM folder_rules ORDER BY position'):
        if fnmatch.fnmatchcase(m['filename'].casefold(), r['pattern'].casefold()):
            db.execute('INSERT OR REPLACE INTO asset_locations VALUES (?,?)',(m['id'],r['folder']))
            break


def export(db):
    from . import organizer
    return dict(organizer=organizer.state(db),homes=[dict(id=r['id'],title=r['title'],share=r['share'],
                            data=base64.b64encode(r['content']).decode()) for r in db.execute('SELECT * FROM homes')],
                rules=[dict(r) for r in db.execute('SELECT pattern,folder FROM folder_rules ORDER BY position')],
                previous=[dict(id=r['module_id'],expires=r['expires'],info=json.loads(r['info']),
                               data=base64.b64encode(r['content']).decode()) for r in db.execute(
                                   'SELECT * FROM module_previous WHERE expires>?',(int(time.time()),))])


def restore(store, db, obj):
    from . import organizer
    organizer.restore(db,obj.get('organizer',{}))
    for table in ('homes','folder_rules','module_previous'):
        db.execute('DELETE FROM '+table)
    for row in obj.get('homes', []):
        if not re.fullmatch('[0-9a-f]{32}',row['id']) or (row.get('share') is not None and
                not re.fullmatch('[0-9a-f]{48}',row['share'])):
            raise Problem('首页备份标识无效')
        if db.execute('SELECT 1 FROM homes WHERE id=? OR share=?',(row['id'],row.get('share'))).fetchone():
            raise Problem('首页备份标识重复')
        save_home(db,row,row['id'],row.get('share'))
    for n,row in enumerate(obj.get('rules', [])):
        pattern=text(row['pattern'],180)
        if not pattern or not db.execute('SELECT 1 FROM asset_folders WHERE id=?',(row['folder'],)).fetchone():
            raise Problem('分类规则备份无效')
        db.execute('INSERT INTO folder_rules VALUES (?,?,?)',(n,pattern,row['folder']))
    for row in obj.get('previous', []):
        old=store.get_module(db,row['id'])
        info={k:row['info'][k] for k in META_KEYS}
        prev=prepare_module({**info,'data':row['data']},old)
        expires=row['expires']
        if type(expires) is not int or expires > int(time.time())+WEEK+60:
            raise Problem('历史版本过期时间无效')
        if expires>time.time():
            db.execute('INSERT INTO module_previous VALUES (?,?,?,?)',
                       (old['id'],expires,json.dumps(info),prev['content']))
