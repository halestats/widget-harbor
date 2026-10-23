"""Run locally using the project's .env. No shell evaluation of config values."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
env = root / '.env'
if not env.exists():
    raise SystemExit('请先运行 python3 scripts/init_env.py')
for line in env.read_text().splitlines():
    if line.strip() and not line.lstrip().startswith('#'):
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip().strip('\"\''))
os.environ.setdefault('DATA_DIR', str(root / 'data'))
os.environ.setdefault('HOST', os.environ.get('BIND_ADDRESS', '127.0.0.1'))
os.environ.setdefault('PORT', os.environ.get('HOST_PORT', '8765'))
from hub.server import main
main()
