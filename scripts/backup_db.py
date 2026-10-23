"""Create an online, consistent SQLite backup; never overwrite an existing file."""
import sqlite3
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit('用法: python3 scripts/backup_db.py 源数据库 新备份文件')
source, target = map(Path, sys.argv[1:])
if not source.is_file() or target.exists():
    raise SystemExit('源数据库不存在，或目标文件已存在；未修改任何文件。')
target.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src:
    with sqlite3.connect(target) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise SystemExit('备份完整性校验失败')
print('一致性备份已保存:', target)
