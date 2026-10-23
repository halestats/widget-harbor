"""Offline restore: stop the hub first. Explicit confirmation is required."""
import sqlite3
import sys
from pathlib import Path

if len(sys.argv) != 3:
    raise SystemExit('用法: python3 scripts/restore_db.py 备份文件 目标数据库')
source, target = map(Path, sys.argv[1:])
if not source.is_file() or source.resolve() == target.resolve():
    raise SystemExit('备份文件不存在，或源和目标相同')
with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as src:
    if src.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
        raise SystemExit('备份完整性校验失败')
    tables={r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'modules','collections','members'} <= tables or src.execute('PRAGMA user_version').fetchone()[0] != 1:
        raise SystemExit('不是受支持的 Widget Harbor 数据库')
    print('请确认本平台服务已停止，恢复将覆盖:', target)
    if input('输入 RESTORE 继续: ') != 'RESTORE':
        raise SystemExit('已取消')
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as dst:
        src.backup(dst)
        dst.execute('PRAGMA wal_checkpoint(TRUNCATE)')
print('恢复完成。请启动本平台并验证文件和订阅。')
