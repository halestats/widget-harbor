"""Create a private .env without overwriting existing configuration."""
import os
from pathlib import Path
import secrets

root = Path(__file__).resolve().parent.parent
path = root / '.env'
try:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
except FileExistsError:
    raise SystemExit('.env 已存在，未覆盖。请直接编辑已有配置。')
password = secrets.token_urlsafe(24)
with os.fdopen(fd, 'w') as stream:
    stream.write(f'ADMIN_PASSWORD={password}\nSITE_URL=http://localhost:8765\nBIND_ADDRESS=127.0.0.1\nHOST_PORT=8765\n')
print('已生成 .env（仅当前用户可读写）。请打开 .env 查看管理员密码，并设置 SITE_URL。')
