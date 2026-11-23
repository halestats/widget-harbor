"""Administrator tools for grouping modules and adopting standalone remote sources."""
import hashlib
import json
import re
from collections import Counter

from .core import Problem, text

SCHEMA = """
CREATE TABLE IF NOT EXISTS module_labels (
 module_id TEXT PRIMARY KEY REFERENCES modules(id) ON DELETE CASCADE, label TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS module_groups (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS module_rules (
 position INTEGER PRIMARY KEY AUTOINCREMENT, keywords TEXT NOT NULL, label TEXT NOT NULL,
 slug TEXT REFERENCES collections(slug) ON DELETE SET NULL);
INSERT OR IGNORE INTO module_groups SELECT label FROM module_labels WHERE label != '';
CREATE TABLE IF NOT EXISTS managed_links (
 module_id TEXT PRIMARY KEY REFERENCES modules(id) ON DELETE CASCADE);
"""


def rules(db):
    return [dict(r) for r in db.execute('SELECT * FROM module_rules ORDER BY position')]


def group_names(db):
    rows = db.execute("""SELECT name FROM module_groups UNION SELECT label FROM module_labels
        UNION SELECT label FROM module_rules WHERE label != '' ORDER BY 1""")
    return [r[0] for r in rows]


def change_group(db, obj):
    db.execute('BEGIN IMMEDIATE')
    action = obj.get('action')
    name = text(obj.get('name', ''), 100).strip()
    groups = group_names(db)
    if action in ('create', 'rename'):
        new = text(obj.get('new_name', ''), 100).strip()
        if not new or new in ('全部', '未分类') or any(ord(c) < 32 for c in new):
            raise Problem('请输入有效的分组名称')
        if new in groups and (action == 'create' or new != name):
            raise Problem('分组名称已存在', 409)
    if action == 'create':
        db.execute('INSERT INTO module_groups VALUES (?)', (new,))
        return
    if action == 'assign':
        if name and name not in groups:
            raise Problem('分组不存在', 404)
        ids = obj.get('ids')
        if not isinstance(ids, list) or not ids:
            raise Problem('请选择模块')
        for ident in ids:
            if not isinstance(ident, str) or not db.execute(
                    "SELECT 1 FROM modules WHERE kind='module' AND id=?", (ident,)).fetchone():
                raise Problem('所选模块不存在', 404)
            db.execute('DELETE FROM module_labels WHERE module_id=?', (ident,))
            if name:
                db.execute('INSERT INTO module_labels VALUES (?,?)', (ident, name))
        return
    if action not in ('rename', 'delete') or name not in groups:
        raise Problem('请选择已有分组')
    if action == 'rename':
        if name == new:
            return
        db.execute('INSERT INTO module_groups VALUES (?)', (new,))
        db.execute('UPDATE module_labels SET label=? WHERE label=?', (new, name))
        db.execute('UPDATE module_rules SET label=? WHERE label=?', (new, name))
    else:
        db.execute('DELETE FROM module_labels WHERE label=?', (name,))
        db.execute("DELETE FROM module_rules WHERE label=? AND slug IS NULL", (name,))
        db.execute("UPDATE module_rules SET label='' WHERE label=?", (name,))
    db.execute('DELETE FROM module_groups WHERE name=?', (name,))


def add_to_collection(db, slug, ident):
    db.execute('BEGIN IMMEDIATE')
    if not db.execute("SELECT 1 FROM modules WHERE id=? AND kind='module'", (ident,)).fetchone():
        raise Problem('模块不存在', 404)
    if not db.execute("""SELECT 1 FROM collections LEFT JOIN collection_sync USING(slug)
            WHERE slug=? AND coalesce(source_url,'')=''""", (slug,)).fetchone():
        raise Problem('请选择自建合集')
    if db.execute('SELECT 1 FROM members WHERE slug=? AND module_id=?', (slug, ident)).fetchone():
        return
    count, pos = db.execute('SELECT count(*),coalesce(max(position),-1)+1 FROM members WHERE slug=?',
                            (slug,)).fetchone()
    if count >= 100:
        raise Problem('目标合集最多 100 个模块')
    db.execute('INSERT INTO members(slug,module_id,position) VALUES (?,?,?)', (slug, ident, pos))


def state(db):
    return dict(module_groups=group_names(db), module_rules=rules(db), labels=dict(db.execute('SELECT module_id,label FROM module_labels')),
                managed_links=[r[0] for r in db.execute('SELECT module_id FROM managed_links ORDER BY module_id')])


def rule_input(db, obj):
    words=text(obj.get('keywords',''),300).strip()
    label=text(obj.get('label',''),100).strip()
    slug=text(obj.get('slug') or '',64)
    if not any(w.strip() for w in re.split('[,，]',words)) or not (slug or label):
        raise Problem('请填写关键词，并指定分组或自建合集')
    if slug and not db.execute("""SELECT 1 FROM collections c LEFT JOIN collection_sync s USING(slug)
        WHERE c.slug=? AND coalesce(s.source_url,'')=''""",(slug,)).fetchone():
        raise Problem('请选择已有的自建合集')
    return dict(keywords=words,label=label,slug=slug or None)


def match_rule(module, rule):
    fields='\n'.join(module[k] or '' for k in ('title','filename','author')).casefold()
    return any(w.strip().casefold() in fields for w in re.split('[,，]',rule['keywords']) if w.strip())


def apply_row(db, ident, label, slug):
    if label:
        db.execute('INSERT OR IGNORE INTO module_groups VALUES (?)', (label,))
        db.execute('INSERT OR IGNORE INTO module_labels VALUES (?,?)',(ident,label))
    if slug:
        db.execute("""INSERT OR IGNORE INTO members(slug,module_id,position)
          SELECT ?,?,coalesce(max(position),-1)+1 FROM members WHERE slug=?
          HAVING count(*)<100 AND NOT EXISTS(SELECT 1 FROM collection_sync WHERE slug=? AND source_url!='')""",
                   (slug,ident,slug,slug))


def on_create(db, module):
    for rule in rules(db):
        if match_rule(module,rule):
            apply_row(db,module['id'],rule['label'],rule['slug'])
            break


def preview(db, tool, obj):
    if tool=='legacy':
        rows=db.execute("""SELECT id,title,source_url FROM modules m WHERE kind='module'
          AND (source_url LIKE 'https://%' OR source_url LIKE 'http://%')
          AND NOT (coalesce(source_group,'') != source_url AND (source_group LIKE 'https://%' OR source_group LIKE 'http://%'))
          AND NOT EXISTS(SELECT 1 FROM managed_links WHERE module_id=m.id)
          AND NOT EXISTS(SELECT 1 FROM members JOIN collection_sync USING(slug)
                         WHERE module_id=m.id AND collection_sync.source_url!='')
          ORDER BY id""")
        return [dict(r) for r in rows]
    rows=db.execute("SELECT id,title,filename,author FROM modules WHERE kind='module' ORDER BY id").fetchall()
    if tool=='filenames':
        occupied=Counter(r['filename'].casefold() for r in rows)
        changes=[]
        for r in rows:
            name=r['filename']
            if 'rex' not in name[:-3].casefold():continue
            stem=re.sub(r'[\s_.-]+','-',re.sub('rex','',name[:-3],flags=re.I)).strip('-') or 'module'
            proposed=stem+'.js'
            index=1
            while proposed.casefold() in occupied:
                proposed=f"{stem[:145]}-{r['id'][:8]}"+(f'-{index}' if index>1 else '')+'.js'
                index+=1
            occupied[proposed.casefold()]+=1
            changes.append(dict(id=r['id'],filename=name,new=proposed,title=r['title']))
        return changes
    rule=rule_input(db,obj)
    slug=rule['slug']
    members={r[0] for r in db.execute('SELECT module_id FROM members WHERE slug=?',(slug,))}
    labels=dict(db.execute('SELECT module_id,label FROM module_labels'))
    result=[]
    for r in rows:
        if match_rule(r,rule):
            add=bool(slug and r['id'] not in members)
            label=rule['label'] if r['id'] not in labels else ''
            if label or add:
                result.append(dict(id=r['id'],title=r['title'],label=label,slug=slug if add else ''))
    return result


def operate(db, tool, obj):
    db.execute('BEGIN IMMEDIATE')
    if tool=='remove-rule':
        db.execute('DELETE FROM module_rules WHERE position=?',(obj.get('position'),))
        return {'ok':True}
    rows=preview(db,tool,obj)
    config=rule_input(db,obj) if tool=='rules' else {}
    token=hashlib.sha256(json.dumps([rows,config],sort_keys=True).encode()).hexdigest()
    if 'token' not in obj:return dict(rows=rows,token=token)
    if obj['token']!=token:raise Problem('预览内容已变化，请重新预览',409)
    ids=obj.get('ids')
    if not isinstance(ids,list) or not all(isinstance(i,str) for i in ids) or set(ids)-{r['id'] for r in rows}:
        raise Problem('请选择预览中的模块')
    chosen=set(ids)
    if tool=='rules':
        slug=config['slug']
        additions=sum(r['id'] in chosen and bool(r['slug']) for r in rows)
        if additions + db.execute('SELECT count(*) FROM members WHERE slug=?',(slug,)).fetchone()[0]>100:
            raise Problem('目标合集最多 100 个模块，请减少选择')
        if obj.get('auto') is True:
            if len(rules(db))>=100:raise Problem('最多保存 100 条规则')
            if not db.execute('SELECT 1 FROM module_rules WHERE keywords=? AND label=? AND slug IS ?',
                              tuple(config.values())).fetchone():
                db.execute('INSERT INTO module_rules(keywords,label,slug) VALUES (:keywords,:label,:slug)',config)
    for r in rows:
        if r['id'] not in chosen:continue
        if tool=='rules':apply_row(db,r['id'],r['label'],r['slug'])
        elif tool=='filenames':db.execute('UPDATE modules SET filename=? WHERE id=?',(r['new'],r['id']))
        else:
            db.execute('INSERT INTO managed_links VALUES (?)',(r['id'],))
            db.execute('INSERT OR IGNORE INTO file_sync(module_id) VALUES (?)',(r['id'],))
    return {'count':len(chosen)}


def restore(db,obj):
    for key in ('module_rules','module_labels','module_groups','managed_links'):
        db.execute('DELETE FROM '+key)
    for name in obj.get('module_groups', []):
        db.execute('INSERT INTO module_groups VALUES (?)', (text(name, 100),))
    for r in obj.get('module_rules',[]):
        if not r.get('label') and not r.get('slug'):continue
        # A target may have acquired an upstream since the rule was saved.
        slug=r.get('slug')
        if slug and not db.execute('SELECT 1 FROM collections WHERE slug=?',(slug,)).fetchone():
            raise Problem('规则引用的合集不存在')
        db.execute('INSERT INTO module_rules(position,keywords,label,slug) VALUES (?,?,?,?)',
                   (int(r['position']),text(r['keywords'],300),text(r['label'],100),slug))
    for ident,label in obj.get('labels',{}).items():
        db.execute('INSERT INTO module_labels VALUES (?,?)',(ident,text(label,100)))
    db.execute("INSERT OR IGNORE INTO module_groups SELECT label FROM module_labels WHERE label != '';")
    db.executemany('INSERT INTO managed_links VALUES (?)',[(i,) for i in obj.get('managed_links',[])])
