# Linux / Docker 安装

适用于安装了 Docker Engine 与 Compose v2 的 Linux 主机，包括服务器、NAS 和软路由。以下 `/opt/widget-harbor` 只是示例，源码可以放在任何有写权限的目录；数据建议使用本地 Linux 文件系统。无需使用特定磁盘名或发行版。

## 从发行包安装

从 [Releases](https://github.com/halestats/widget-harbor/releases/latest) 下载 `widget-harbor-v1.4.2.tar.gz` 到服务器。例如：

```sh
sudo mkdir -p /opt
sudo tar -xzf widget-harbor-v1.4.2.tar.gz -C /opt
cd /opt/widget-harbor
sudo sh scripts/install.sh
```

已有同名项目请使用升级指南，不要覆盖 `.env` 或 `data/`。安装器只初始化新目录，默认监听 `127.0.0.1:8765`，适合本机反向代理。需要局域网直连时，在首次安装命令中明确传入主机地址（替换示例 IP）：

```sh
sudo env SITE_URL=http://192.168.1.100:8765 BIND_ADDRESS=192.168.1.100 sh scripts/install.sh
```

本机访问 `http://localhost:8765`；局域网部署访问设置的 SITE_URL。在服务器本地查看 `.env` 的 `ADMIN_PASSWORD` 登录，随后可在界面修改密码。

安装器使用 `docker-compose.yml` 和 `docker-compose.storage.yml`，将项目内 `data/` 挂载为容器 `/data`，不更改其他服务。

## 数据目录与配置

```text
/opt/widget-harbor/             示例源码目录，可更改
/opt/widget-harbor/.env         配置及初始密码
/opt/widget-harbor/data/         数据库、凭据及 images/ 图片缓存
/opt/widget-harbor/backups/      升级备份
```

可以在首次启动前将 `.env` 的 `WIDGET_DATA_DIR` 改为其他已创建的绝对路径，如 `/srv/widget-harbor/data`。该目录需由 UID/GID 10001 可写。不要在启动后直接修改路径指向空目录。

```sh
docker compose -f docker-compose.yml -f docker-compose.storage.yml ps
docker compose -f docker-compose.yml -f docker-compose.storage.yml logs --tail=80 hub
```

只使用基础 `docker-compose.yml` 时会使用 Docker 命名卷，这也是受支持的方式，见 README。后续命令保持相同的 Compose 文件组合，避免切换数据存储。

## 反向代理与多入口

反向代理指向实际可达的 `http://主机地址:8765`。`SITE_URL` 设置为客户端访问的公开 HTTPS 地址；`ALLOWED_ORIGINS` 添加额外的管理入口，例如 `http://192.168.1.100:8765`。修改 `.env` 后用同样的 Compose 文件执行 `up -d hub`。

桥接网络容器里的 127.0.0.1 指容器自身，不是宿主机。请根据代理所在网络选择目标地址。配置中不要求某个代理产品，也不要求公网端口映射。

[升级指南](UPGRADE-1.4.md) · [部署与备份细节](DEPLOYMENT.md)
