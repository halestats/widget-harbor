# iStoreOS 升级到 v1.1.0

适用于已安装到 `/opt/widget-harbor` 的实例。不要重新运行首次安装脚本。

1. 上传 `widget-harbor-v1.1.0.tar.gz` 到 `/opt/`。
2. 在 iStoreOS 已登录终端粘贴：

```sh
(
  set -eu
  cd /opt
  stage=$(mktemp -d /opt/widget-harbor-update.XXXXXX)
  tar -xzf widget-harbor-v1.1.0.tar.gz -C "$stage"
  sh "$stage/widget-harbor/scripts/upgrade.sh" /opt/widget-harbor
)
```

脚本核对原数据挂载，保存 `.env`、原 Compose、源码、在线一致性 SQLite 备份及已有密码凭据，备份位置为原项目 `backups/upgrade-时间-进程号/`。先构建镜像，成功后才更新源码和重建本服务；模块数据和 `.env` 保留，只会短暂中断 Widget Harbor。其他容器、WireGuard、Docker 服务配置不变。升级期间请勿编辑模块或密码，以便备份对应同一使用状态。

旧 Compose 文件会在成功重建后移除，今后使用：

```sh
cd /opt/widget-harbor
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml ps
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml logs --tail=80 hub
```

确认状态 healthy 后刷新网页，仍使用当前密码登录。进入设置与备份 → 密码管理即可修改密码。若使用了网页密码管理，`.env` 中的旧密码不会随之更改。

## 构建或启动失败

构建失败会保留正在运行的旧容器，排除网络/磁盘问题后重新执行同一升级脚本即可。新容器启动失败时先查看上述日志，不要重跑安装器或清理 Docker 数据。

需要回退时，将以下 `备份目录完整路径` 替换成脚本输出路径：

```sh
cd /opt/widget-harbor
backup='备份目录完整路径'
test -f "$backup/image-id.txt" && docker tag "$(cat "$backup/image-id.txt")" widget-harbor:local &&
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml up -d --no-build --no-deps hub
```

本版本未更改模块数据库结构，镜像回退无需覆盖数据库。旧版本不支持网页修改后的密码，回退旧镜像时使用 `.env` 初始密码。旧镜像 ID、源码和数据备份保留在该备份目录。不要运行全局镜像清理，以免删除回退镜像。
