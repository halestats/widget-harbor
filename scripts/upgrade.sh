#!/bin/sh
# Run from a separately extracted release directory. Never run the first-install script to upgrade.
set -eu
umask 077
release=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)
[ "$#" = 1 ] || { echo '用法：sh scripts/upgrade.sh /绝对路径/widget-harbor'; exit 1; }
case "$1" in /*) ;; *) echo '安装目录必须为绝对路径'; exit 1;; esac
target=$(CDPATH= cd -- "$1" && pwd -P)
[ "$release" != "$target" ] || { echo '请从单独解压的新版本目录运行此脚本'; exit 1; }
[ -f "$release/docker-compose.yml" ] && [ -f "$target/.env" ] || { echo '安装目录或更新文件不完整'; exit 1; }
cd "$target"
cid=$(docker ps --filter label=com.docker.compose.project=widget-harbor --filter label=com.docker.compose.service=hub --format '{{.ID}}')
[ -n "$cid" ] && [ "$(printf '%s\n' "$cid" | wc -l | tr -d ' ')" = 1 ] || { echo '需要且仅支持一个运行中的 hub 容器'; exit 1; }
working_dir=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$cid")
[ "$working_dir" = "$target" ] || { echo '运行容器的项目目录与目标不符，停止更新'; exit 1; }
config_files=$(docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' "$cid")
base=docker-compose.yml
override=''
case "$config_files" in *compose.istoreos.yaml*) base=compose.yaml; override=compose.istoreos.yaml;; *docker-compose.storage.yml*) override=docker-compose.storage.yml;; *docker-compose.istoreos.yml*) override=docker-compose.istoreos.yml;; esac
compose() { if [ -n "$override" ]; then docker compose -f "$base" -f "$override" "$@"; else docker compose -f "$base" "$@"; fi; }
compose config --quiet
mounts=$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$cid")
[ -n "$mounts" ] || { echo '数据挂载路径不符，停止更新'; exit 1; }
backup="$target/backups/upgrade-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$backup"
cp -p .env "$base" "$backup/"
if [ -n "$override" ]; then cp -p "$override" "$backup/"; fi
tar -czf "$backup/source.tar.gz" hub static Dockerfile scripts
# SQLite online backup; no interruption of the existing service during image build.
compose exec -T hub python -c "import sqlite3; s=sqlite3.connect('/data/hub.sqlite3'); d=sqlite3.connect('/tmp/upgrade.sqlite3'); s.backup(d); d.close(); s.close()"
compose exec -T hub cat /tmp/upgrade.sqlite3 > "$backup/hub.sqlite3.tmp"
[ -s "$backup/hub.sqlite3.tmp" ] || { echo "数据库备份为空，停止更新"; exit 1; }
mv "$backup/hub.sqlite3.tmp" "$backup/hub.sqlite3"
compose exec -T hub python -c "from pathlib import Path; import sys; p=Path('/data/admin-auth.json'); sys.stdout.buffer.write(p.read_bytes() if p.exists() else b'')" > "$backup/admin-auth.json"
old_image=$(docker inspect --format '{{.Image}}' "$cid")
printf '%s\n' "$old_image" > "$backup/image-id.txt"
# Build before replacing source files or recreating the service.
docker build -t widget-harbor:local "$release"
# Copy release content only; runtime secrets and data never come from the bundle.
for item in hub static scripts docs deploy examples tests .github Dockerfile docker-compose.yml docker-compose.istoreos.yml docker-compose.storage.yml .dockerignore .gitignore .env.example README.md CHANGELOG.md LICENSE SECURITY.md CONTRIBUTING.md THIRD_PARTY_NOTICES.md; do
    if [ "$item" = docker-compose.storage.yml ] && [ "$override" != docker-compose.storage.yml ]; then continue; fi
    if [ "$item" = docker-compose.istoreos.yml ] && [ "$override" != docker-compose.istoreos.yml ] && [ "$override" != compose.istoreos.yaml ]; then continue; fi
    case "$item" in docker-compose.storage.yml|docker-compose.istoreos.yml) if [ -e "$target/$item" ]; then continue; fi;; esac
    if [ -e "$release/$item" ]; then cp -R "$release/$item" "$target/"; fi
done
base=docker-compose.yml
if [ "$override" = compose.istoreos.yaml ]; then override=docker-compose.istoreos.yml; fi
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
