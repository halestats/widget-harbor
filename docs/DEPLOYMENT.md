# Docker 部署指南

## 1. 准备目录与配置

把仓库下载并解压，进入含 `docker-compose.yml` 的目录。先确认端口 8765 未被其他服务使用；如冲突只修改本项目的 `HOST_PORT`，并同步修改 `SITE_URL`。

有 Python：执行 `python3 scripts/init_env.py`。没有 Python：复制 `.env.example` 为 `.env`，在编辑器中替换管理员密码。不要把真实 `.env` 上传到 GitHub。

### 仅本机使用

```dotenv
ADMIN_PASSWORD=请替换成至少12个字符的强密码
SITE_URL=http://localhost:8765
BIND_ADDRESS=127.0.0.1
HOST_PORT=8765
```

### 局域网 NAS 使用

下面的 `192.168.1.20` 是示例，改为自己的 NAS IP：

```dotenv
ADMIN_PASSWORD=请替换成至少12个字符的强密码
SITE_URL=http://192.168.1.20:8765
BIND_ADDRESS=192.168.1.20
HOST_PORT=8765
```

也可以使用 `BIND_ADDRESS=0.0.0.0` 监听宿主机所有 IPv4 接口；访问边界还取决于路由、防火墙和端口转发。不要假定“局域网可访问”就代表公网可访问。

## 2. 启动

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 hub
```

服务应显示 `healthy`。浏览器打开 `SITE_URL`，输入 `.env` 中的密码。

该 Compose 只管理 `widget-harbor` 项目。已有宿主机上的其他服务不需要停止、更新或重启。不要使用全局容器清理命令。

## 3. 使用 HTTPS 域名

设置 `SITE_URL=https://widgets.example.com`。浏览器也必须通过该域名访问；登录写操作会拒绝其他 Origin。HTTPS 配置会使用 Secure Cookie。

### Nginx 与应用在同一宿主机

应用保持 `BIND_ADDRESS=127.0.0.1`。将以下片段合入自己的 HTTPS 配置，替换域名和证书路径：

```nginx
server {
    listen 443 ssl;
    server_name widgets.example.com;
    ssl_certificate /your/cert/fullchain.pem;
    ssl_certificate_key /your/cert/privkey.pem;

    client_max_body_size 64m;
    client_body_timeout 30s;
    location / {
        proxy_pass http://127.0.0.1:8765;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 180s;
    }
}
```

本平台使用固定 `SITE_URL`，不会信任客户端提供的 Host / 转发头来生成链接。

### Lucky / Nginx 在另一个容器

普通 bridge 容器里的 `127.0.0.1` 指该容器自身，不能直接指向本平台。选择以下一种连接方式：

- 已有代理使用 host 网络：目标可用 `http://127.0.0.1:8765`，先核实其网络模式。
- 代理与本平台处于同一个用户自定义 Docker 网络：目标可用 `http://hub:8765`；请按已有网络结构配置唯一别名，避免同名服务冲突。
- 代理通过宿主机局域网 IP 连接：将本平台绑定到对应宿主机 IP，并用该 IP 作为代理目标。

不需要为了部署本平台更改现有代理的网络模式。域名、证书和访问路径需要在你的实际环境中验证。使用手机移动网络验证公网入口；同一 Wi-Fi 下的域名访问不能替代公网验收。

## 4. 数据与备份

模块元数据、原始文件字节、合集关系全在 `/data/hub.sqlite3`，使用 SQLite WAL 事务存储。命名卷通常为 `widget-harbor_harbor-data`；实际名称以 `docker volume ls` 为准。

### 管理页面备份

设置与备份 → 导出完整备份。JSON 包含所有文件和合集，不包含管理员密码、登录会话。恢复仅支持本平台 v1 格式，覆盖现有数据且在事务中完成。网页恢复文件限 60 MiB，更大数据使用数据库备份。

### 在线数据库备份（推荐）

```bash
mkdir -p backups
docker compose exec -T hub python -c "import sqlite3; s=sqlite3.connect('/data/hub.sqlite3'); d=sqlite3.connect('/tmp/harbor-backup.sqlite3'); s.backup(d); d.close(); s.close()"
docker compose cp hub:/tmp/harbor-backup.sqlite3 ./backups/harbor-backup.sqlite3
```

SQLite backup API 能产生一致快照；不要在服务写入时只复制 `.sqlite3` 而遗漏 WAL。将备份另存到其他设备，并安全保存 `.env`。

若备份来源是本地开发环境，也可运行 `python3 scripts/backup_db.py ./data/hub.sqlite3 ./backups/harbor-backup.sqlite3`。

### 数据库恢复

先停止**本项目**并备份当前数据。下列操作将覆盖数据库，输入 `RESTORE` 才会执行；源文件必须来自可信的本平台备份：

```bash
docker compose stop hub
docker compose run --rm --no-deps -v "$(pwd)/backups:/backups:ro" -v "$(pwd)/scripts:/tools:ro" hub python /tools/restore_db.py /backups/harbor-backup.sqlite3 /data/hub.sqlite3
docker compose up -d hub
```

脚本校验 SQLite 完整性和 schema，再用 SQLite backup API 恢复。不要使用 `docker compose down -v`，它会删除持久卷。

## 5. 更新、回退和密码修改

更新前先备份数据库和配置，再获取你选择的发布版本：

```bash
git pull --ff-only
docker compose up -d --build hub
docker compose ps
```

这只重建本平台服务。修改站点地址后使用 `docker compose up -d hub`；仅 `restart` 不会应用新的环境变量。进程重启会使所有登录会话失效。

回退：检出原版本标签并重建镜像；如果未来版本有数据库迁移，按对应版本说明恢复升级前备份。不要把新 schema 数据库交给未知兼容性的旧版本。

## 6. 常见问题

- **登录提示 Origin 不一致：**浏览器地址必须与 `SITE_URL` 的协议、域名及端口一致。
- **手机无法导入：**检查订阅内的模块 URL 是否含 `localhost`；使用手机能访问的 IP 或域名。
- **容器立即退出：**查看日志，检查密码至少 12 位、已修改占位密码、SITE_URL 不包含路径。
- **数据目录权限错误：**命名卷无需手工设置；若改成 bind mount，目录应可由 UID/GID 10001 写入。
- **远程导入失败：**仅允许公开 HTTP/HTTPS 80/443 端口，不访问内网；GitHub 必须使用 Raw 文件地址。
- **大合集超时：**一次最多 50 个远程模块、30 MiB 总文件，整体检查期限 120 秒（正在下载的单文件最多另需 40 秒），拆分导入。
- **模块在客户端报错：**托管成功只代表文件和订阅可获取，需检查脚本是否适合对应客户端版本。

## 管理员密码（v1.1.0）

登录 → 设置与备份 → 密码管理 → 修改管理员密码，输入当前密码、新密码及确认密码。新密码为 12–1024 个字符，修改成功后所有设备重新登录。

加盐 scrypt 哈希独立保存于数据目录的 `admin-auth.json`（权限 600），不保存新密码明文。存在此文件时优先使用它，`.env` 的 `ADMIN_PASSWORD` 仅作为初始/恢复密码，仍须保留有效配置。容器重启、重建及 JSON 内容恢复不会重置新密码。JSON 和 SQLite 备份均不包含此凭据文件；迁移时另行安全备份该文件和 `.env`。

忘记新密码时，拥有主机管理权限的管理员可停止本服务，将数据目录中的 `admin-auth.json` 移到安全备份位置，再启动本服务，使用 `.env` 中的初始密码登录并立即在网页设置新密码。iStoreOS 操作始终同时指定两个 Compose 文件。不要上传 `.env`、凭据文件或数据备份至 GitHub。
