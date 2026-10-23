#!/bin/sh
# First install only. Run on the target Linux Docker host after extracting the package.
set -eu
umask 077
fail() { printf '\n安装停止：%s\n' "$*" >&2; exit 1; }
PROJECT_DIR=$(CDPATH= cd "$(dirname "$0")/.." && pwd -P)
[ "$(id -u)" = 0 ] || fail '请使用 sudo sh scripts/install.sh 运行，以设置数据目录权限'
SITE_URL=${SITE_URL:-http://localhost:8765}
BIND_ADDRESS=${BIND_ADDRESS:-127.0.0.1}
HOST_PORT=${HOST_PORT:-8765}
case "$SITE_URL$BIND_ADDRESS$HOST_PORT" in *'
'*) fail '配置不能包含换行' ;; esac
command -v docker >/dev/null 2>&1 || fail '未找到 Docker'
docker info >/dev/null 2>&1 || fail 'Docker 服务不可用'
docker compose version >/dev/null 2>&1 || fail '未找到 Docker Compose v2 插件'
[ ! -e "$PROJECT_DIR/.env" ] && [ ! -L "$PROJECT_DIR/.env" ] || fail '.env 已存在，未覆盖；已有安装请使用 Compose 命令管理'
[ ! -e "$PROJECT_DIR/data" ] && [ ! -L "$PROJECT_DIR/data" ] || fail 'data 目录已存在，未更改其权限或内容'
EXISTING=$(docker ps -a --filter label=com.docker.compose.project=widget-harbor --format '{{.ID}}')
[ -z "$EXISTING" ] || fail '发现已有 widget-harbor 容器，请先核对原部署'
if command -v ss >/dev/null 2>&1; then
    LISTENERS=$(ss -lnt) || fail '无法检查监听端口'
elif command -v netstat >/dev/null 2>&1; then
    LISTENERS=$(netstat -lnt) || fail '无法检查监听端口'
else
    fail '缺少 ss/netstat，无法确认端口是否空闲'
fi
if printf '%s\n' "$LISTENERS" | awk -v port="$HOST_PORT" 'NR>1 && $4 ~ (":" port "$") {found=1} END {exit !found}'; then
    fail '8765 端口已被占用；未启动任何服务'
fi
PUBLISHED_PORTS=$(docker ps --format '{{.Ports}}')
if printf '%s\n' "$PUBLISHED_PORTS" | grep -Fq ":$HOST_PORT->"; then
    fail 'Docker 已发布 8765 端口；未启动任何服务'
fi
cd "$PROJECT_DIR"
# Linux kernel random UUIDs avoid optional od/openssl packages on OpenWrt.
[ -r /proc/sys/kernel/random/uuid ] || fail '无法读取内核随机源，未生成密码'
ADMIN_SECRET=$(sh scripts/random-secret.sh) || fail '随机密码生成失败'
case "$ADMIN_SECRET" in ''|*[!0-9a-f]*) fail '随机密码格式无效' ;; esac
[ "${#ADMIN_SECRET}" = 48 ] || fail '随机密码生成失败'
# Validate configuration before creating application state. Do not print secrets.
ADMIN_PASSWORD="$ADMIN_SECRET" SITE_URL=$SITE_URL BIND_ADDRESS=$BIND_ADDRESS HOST_PORT=$HOST_PORT WIDGET_DATA_DIR="$PROJECT_DIR/data" \
    docker compose -f docker-compose.yml -f docker-compose.storage.yml config --quiet
(
    set -C
    cat > .env <<EOF
ADMIN_PASSWORD=$ADMIN_SECRET
SITE_URL=$SITE_URL
ALLOWED_ORIGINS=$SITE_URL
BIND_ADDRESS=$BIND_ADDRESS
HOST_PORT=$HOST_PORT
WIDGET_DATA_DIR=$PROJECT_DIR/data
EOF
)
unset ADMIN_SECRET
mkdir data
chown 10001:10001 data
chmod 750 data
printf '\n配置已保存；开始构建并启动 Widget Harbor。不会重启 Docker、网络或其他容器。\n'
if ! docker compose -f docker-compose.yml -f docker-compose.storage.yml up -d --build hub; then
    printf '\n构建或启动未完成，配置和数据目录已保留。修复报错后在本目录重试：\n'
    printf 'docker compose -f docker-compose.yml -f docker-compose.storage.yml up -d --build hub\n'
    exit 1
fi
CONTAINER_ID=$(docker compose -f docker-compose.yml -f docker-compose.storage.yml ps -q hub)
[ -n "$CONTAINER_ID" ] || fail '未找到启动后的容器，请检查本项目日志'
i=0
while [ "$i" -lt 30 ]; do
    HEALTH=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER_ID")
    if [ "$HEALTH" = healthy ]; then
        printf '\n安装完成，容器健康检查通过。\n访问：%s\n' "$SITE_URL"
        printf '在本机查看登录密码（不要将输出发送给他人）：\n'
        printf "sed -n 's/^ADMIN_PASSWORD=//p' %s/.env\n" "$PROJECT_DIR"
        exit 0
    fi
    i=$((i+1))
    sleep 2
done
printf '\n容器已创建，但健康检查尚未通过；不能认定安装成功。\n'
docker compose -f docker-compose.yml -f docker-compose.storage.yml ps
printf '请查看日志：docker compose -f docker-compose.yml -f docker-compose.storage.yml logs --tail=80 hub\n'
exit 1
