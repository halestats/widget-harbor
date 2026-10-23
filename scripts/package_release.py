#!/usr/bin/env python3
"""Build clean source archives from tracked files (no runtime data)."""
import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', default='dist')
    args = parser.parse_args()
    version = args.version.removeprefix('v')
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        parser.error('version must be MAJOR.MINOR.PATCH')
    root = Path(__file__).resolve().parent.parent
    names = subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
    files = [root/n for n in names if n]
    for path in files:
        relative = path.relative_to(root)
        if path.is_symlink() or any(part in ('.git','data','backups','__pycache__') for part in relative.parts) or (path.name.startswith('.env') and path.name != '.env.example' and path.name != 'istoreos.env.example') or path.suffix in ('.pyc','.sqlite3'):
            raise SystemExit(f'Unexpected release file: {relative}')
    out = Path(args.output).resolve()
    out.mkdir(parents=True,exist_ok=True)
    stem = f'widget-harbor-v{version}'
    with tarfile.open(out/f'{stem}.tar.gz','w:gz') as archive:
        for path in sorted(files):
            archive.add(path,arcname='widget-harbor/'+path.relative_to(root).as_posix())
    with zipfile.ZipFile(out/f'{stem}.zip','w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path,arcname='widget-harbor/'+path.relative_to(root).as_posix())
    archives=[out/f'{stem}.tar.gz',out/f'{stem}.zip']
    (out/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in archives))
    print(f'Packaged {len(files)} tracked files into {out}')


if __name__ == '__main__':
    main()
