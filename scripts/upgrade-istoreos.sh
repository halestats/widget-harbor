#!/bin/sh
# Run from a separately extracted release directory. Never run the first-install script to upgrade.
set -eu
umask 077
release=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
target=/mnt/nvme/tools/widget-harbor
[ "$release" != "$target" ] || { echo '请从单独解压的新版本目录运行此脚本'; exit 1; }
[ -f "$release/docker-compose.yml" ] && [ -f "$target/.env" ] && [ -d "$target/data" ] || { echo '安装目录或更新文件不完整'; exit 1; }
cd "$target"
base=docker-compose.yml
override=docker-compose.istoreos.yml
if [ ! -f "$base" ]; then base=compose.yaml; override=compose.istoreos.yaml; fi
compose() { docker compose -f "$base" -f "$override" "$@"; }
compose config --quiet
# Confirm the existing service actually mounts the expected data directory.
cid=$(compose ps -q hub)
[ -n "$cid" ] || { echo '现有服务未运行，请先排查'; exit 1; }
mounts=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$cid")
[ "$mounts" = "$target/data" ] || { echo '数据挂载路径不符，停止更新'; exit 1; }
backup="$target/backups/upgrade-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$backup"
cp -p .env "$base" "$override" "$backup/"
tar -czf "$backup/source.tar.gz" hub static Dockerfile scripts
# SQLite online backup; no interruption of the existing service during image build.
compose exec -T hub python -c "import sqlite3; s=sqlite3.connect('/data/hub.sqlite3'); d=sqlite3.connect('/tmp/upgrade.sqlite3'); s.backup(d); d.close(); s.close()"
compose exec -T hub cat /tmp/upgrade.sqlite3 > "$backup/hub.sqlite3.tmp"
[ -s "$backup/hub.sqlite3.tmp" ] || { echo "数据库备份为空，停止更新"; exit 1; }
mv "$backup/hub.sqlite3.tmp" "$backup/hub.sqlite3"
if [ -f data/admin-auth.json ]; then cp -p data/admin-auth.json "$backup/"; fi
old_image=$(docker inspect --format '{{.Image}}' "$cid")
printf '%s\n' "$old_image" > "$backup/image-id.txt"
# Build before replacing source files or recreating the service.
docker build -t widget-harbor:local "$release"
# Copy release content only; runtime secrets and data never come from the bundle.
for item in hub static scripts docs deploy examples tests .github Dockerfile docker-compose.yml docker-compose.istoreos.yml .dockerignore .gitignore .env.example README.md CHANGELOG.md LICENSE SECURITY.md CONTRIBUTING.md THIRD_PARTY_NOTICES.md; do
    if [ -e "$release/$item" ]; then cp -R "$release/$item" "$target/"; fi
done
base=docker-compose.yml
override=docker-compose.istoreos.yml
# Preserve all existing origins. Older deployments may add trusted LAN origins
# explicitly in .env; a public release cannot assume the user's LAN address.
compose config --quiet
compose up -d --no-build --no-deps hub
# Remove obsolete filenames only after successful recreation; backups retain originals.
rm -f compose.yaml compose.istoreos.yaml
printf '更新已启动；备份目录：%s\n' "$backup"
i=0
while [ "$i" -lt 30 ]; do
    cid=$(compose ps -q hub)
    health=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$cid")
    if [ "$health" = healthy ]; then
        compose ps
        echo '更新完成，健康检查通过。请刷新网页。'
        exit 0
    fi
    i=$((i + 1))
    sleep 2
done
compose ps
echo '健康检查尚未通过，请查看本服务日志；备份已保留。'
exit 1
