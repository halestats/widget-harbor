"""Rewrite selected entries in a hosted Rex home package without extracting it."""

import base64
from collections import Counter
from copy import copy
import io
import json
import re
import shutil
import zipfile
import zlib
from urllib.parse import unquote

from .core import Problem
from .home_checks import HTML_LIMIT, check_template, local_name
from .library import HOME_LIMIT, inspect_home_parts, read_home_json, save_home_data

LAYOUT = 'rex-layout.json'
MANIFEST = 'rex-manifest.json'


def all_items(layout):
    return (item for row in layout for item in row.get('items', []))


def widget_id(item):
    return item.get('widgetId') or item.get('widgetBundleId') or ''


def script_entry(url):
    if url.lower().startswith('file:///'):
        name = unquote(url[8:])
        if local_name(name):
            return 'rex-scripts/' + name
    return None


def template_entry(template):
    if isinstance(template, str) and local_name(template) and template.lower().endswith(('.html', '.htm')):
        return 'rex-html/' + template
    return None


def link_plan(db, layout):
    counts = Counter(widget_id(item) for item in all_items(layout)
                     if item.get('widgetURL', '').lower().startswith('file://'))
    modules = {}
    for row in db.execute('''SELECT widget_id, id, title, version FROM modules
            WHERE kind='module' AND platform IN ('both', 'rex')
              AND widget_id IN (SELECT value FROM json_each(?))
              AND id NOT IN (SELECT module_id FROM legacy_icons)
            ORDER BY updated_at DESC, rowid DESC''', (json.dumps([key for key in counts if key]),)):
        modules.setdefault(row['widget_id'], dict(row))
    return [dict(widget_id=key, count=count, target=modules.get(key)) for key, count in counts.items()]


def get_home(db, ident):
    row = db.execute('SELECT sha, content FROM homes WHERE id=?', (ident,)).fetchone()
    if not row:
        raise Problem('首页不存在', 404)
    return row


def preview(store, ident):
    with store.connect() as db:
        row = get_home(db, ident)
        with zipfile.ZipFile(io.BytesIO(row['content'])) as archive:
            layout = read_home_json(archive, LAYOUT)
        links = link_plan(db, layout)
    items = [dict(section=section, item=index, group=group.get('title') or f'栏目 {section + 1}',
                  label=item.get('title') or item.get('name') or f'卡片 {index + 1}',
                  has_html=bool(item.get('htmlTemplate')))
             for section, group in enumerate(layout) for index, item in enumerate(group.get('items', []))]
    return dict(expected=row['sha'], items=items, links=links)


def relink(layout, plan, selected, origin):
    if not isinstance(selected, dict) or not selected:
        raise Problem('请选择要更换的模块')
    targets = {row['widget_id']: row['target']['id'] for row in plan if row['target']}
    if any(targets.get(ident) != target or ident not in targets for ident, target in selected.items()):
        raise Problem('可替换的模块已变化，请刷新后重选', 409)
    removed = set()
    changed = 0
    for item in all_items(layout):
        url = item.get('widgetURL', '')
        target = selected.get(widget_id(item))
        if target and url.lower().startswith('file://'):
            removed.add(script_entry(url))
            item['widgetURL'] = f'{origin}/api/modules/{target}/raw'
            changed += 1
    still_used = {script_entry(item.get('widgetURL', '')) for item in all_items(layout)}
    return {name: None for name in removed - still_used if name}, changed


def add_html(archive, layout, obj):
    section, index = obj.get('section'), obj.get('item')
    if (type(section) is not int or type(index) is not int or not 0 <= section < len(layout)
            or not 0 <= index < len(layout[section].get('items', []))):
        raise Problem('找不到所选栏目或卡片，请刷新后重选')
    items = layout[section]['items']
    target = items[index]
    old_path = template_entry(target.get('htmlTemplate'))
    changes = {}
    if obj.get('remove') is True:
        if not target.pop('htmlTemplate', None):
            raise Problem('这张卡片没有可移除的 HTML')
    else:
        try:
            raw = base64.b64decode(obj.get('html', ''), validate=True)
            raw.decode('utf-8-sig')
        except (TypeError, ValueError):
            raise Problem('请选择有效的 UTF-8 HTML 文件') from None
        if not 0 < len(raw) <= HTML_LIMIT or not raw.strip():
            raise Problem('HTML 卡片须大于 0 且不超过 32 KiB')
        declaration, _ = check_template(raw, len(raw), strict=True)
        shape = declaration['type']
        if shape == 'card':
            grid = [int(declaration['width'][1]), int(declaration['height'][1])]
            if any(i != index and card.get('htmlTemplate') and card.get('gridSize') != grid
                   for i, card in enumerate(items)):
                raise Problem('同一栏目的 HTML 方卡须使用相同的宽高档位')
        else:
            if len(items) != 1:
                raise Problem('全宽和横滑卡片所在栏目只能有一个项目')
            old_grid = target.get('gridSize')
            height = old_grid[1] if (isinstance(old_grid, list) and len(old_grid) == 2 and old_grid[0] == -1
                                     and type(old_grid[1]) in (int, float) and old_grid[1] > 0) else 2.3
            grid = [-1, height]
        filename = obj.get('filename')
        stem = re.sub(r'[^\w. -]+', '-', filename.rsplit('.', 1)[0]) if isinstance(filename, str) else ''
        stem = stem.strip(' .-')[:80] or 'card'
        names = set(archive.namelist())
        name = stem + '.html'
        suffix = 2
        while 'rex-html/' + name in names:
            name = f'{stem}-{suffix}.html'
            suffix += 1
        changes['rex-html/' + name] = raw
        target.update(htmlTemplate=name, style='fullWidthPoster' if shape == 'rail' else 'cover', gridSize=grid)
        manifest = read_home_json(archive, MANIFEST)
        manifest['version'] = 2
        changes[MANIFEST] = json.dumps(manifest).encode()
    target.pop('htmlTemplateSource', None)
    if old_path and all(template_entry(item.get('htmlTemplate')) != old_path for item in all_items(layout)):
        changes[old_path] = None
    return changes, 1


def rebuild(archive, changes):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as result:
        result.comment = archive.comment
        for info in archive.infolist():
            if info.filename in changes:
                continue
            with archive.open(info) as source, result.open(copy(info), 'w') as destination:
                shutil.copyfileobj(source, destination, length=256 * 1024)
        for name, data in changes.items():
            if data is not None:
                result.writestr(name, data)
    if output.tell() > HOME_LIMIT:
        raise Problem('编辑后的首页包超过 25 MiB')
    return output.getvalue()


def apply(store, ident, obj, origin):
    mode = obj.get('mode', 'copy')
    action = obj.get('action')
    if mode not in ('copy', 'replace') or action not in ('relink', 'html'):
        raise Problem('首页编辑操作无效')
    try:
        with store.connect() as db:
            row = get_home(db, ident)
            if obj.get('expected') != row['sha']:
                raise Problem('首页已更新，请刷新后重选', 409)
            with zipfile.ZipFile(io.BytesIO(row['content'])) as archive:
                layout = read_home_json(archive, LAYOUT)
                if action == 'relink':
                    changes, count = relink(layout, link_plan(db, layout), obj.get('selected'), origin)
                else:
                    changes, count = add_html(archive, layout, obj)
                changes[LAYOUT] = json.dumps(layout).encode()
                content = rebuild(archive, changes)
    except (zipfile.BadZipFile, ValueError, RuntimeError, OSError, zlib.error, EOFError):
        raise Problem('无法读取或重建首页包') from None
    inspected = inspect_home_parts(content, report=True)
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT sha, title FROM homes WHERE id=?', (ident,)).fetchone()
        if not current or current['sha'] != obj.get('expected'):
            raise Problem('首页已更新，请刷新后重选', 409)
        if action == 'relink':
            selected = obj['selected']
            available = dict(db.execute('''SELECT widget_id, id FROM modules
                WHERE kind='module' AND platform IN ('both','rex')
                  AND id IN (SELECT value FROM json_each(?))''', (json.dumps(list(selected.values())),)))
            if selected != available:
                raise Problem('可替换的模块已变化，请刷新后重选', 409)
        if mode == 'copy':
            saved = save_home_data(db, content, current['title'][:196] + '（副本）', inspected=inspected)
        else:
            saved = save_home_data(db, content, current['title'], ident, inspected=inspected)
    return dict(id=saved, changed=count)
