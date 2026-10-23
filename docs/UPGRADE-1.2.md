# iStoreOS 升级到 v1.2.0

适用于已安装到 `/mnt/nvme/tools/widget-harbor` 的实例。不要重新运行首次安装脚本。

1. 上传 `widget-harbor-v1.2.0.tar.gz` 到 `/mnt/nvme/tools/`。
2. 在 iStoreOS 已登录终端粘贴：

```sh
(
  set -eu
  cd /mnt/nvme/tools
  stage=$(mktemp -d /mnt/nvme/tools/widget-harbor-update.XXXXXX)
  tar -xzf widget-harbor-v1.2.0.tar.gz -C "$stage"
  sh "$stage/widget-harbor/scripts/upgrade-istoreos.sh"
)
```

脚本核对原数据挂载，保存 `.env`、原 Compose、源码、在线一致性 SQLite 备份及已有密码凭据，备份位置为原项目 `backups/upgrade-时间-进程号/`。先构建镜像，成功后才更新源码和重建本服务；模块数据和 `.env` 保留，只会短暂中断 Widget Harbor。其他容器、WireGuard、Docker 服务配置不变。升级期间请勿编辑模块或密码，以便备份对应同一使用状态。

旧 Compose 文件会在成功重建后移除，今后使用：

```sh
cd /mnt/nvme/tools/widget-harbor
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml ps
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml logs --tail=80 hub
```

确认状态 healthy 后刷新网页，仍使用当前密码登录。进入设置与备份 → 密码管理即可修改密码。若使用了网页密码管理，`.env` 中的旧密码不会随之更改。

## 构建或启动失败

构建失败会保留正在运行的旧容器，排除网络/磁盘问题后重新执行同一升级脚本即可。新容器启动失败时先查看上述日志，不要重跑安装器或清理 Docker 数据。

需要回退时，将以下 `备份目录完整路径` 替换成脚本输出路径：

```sh
cd /mnt/nvme/tools/widget-harbor
backup='备份目录完整路径'
test -f "$backup/image-id.txt" && docker tag "$(cat "$backup/image-id.txt")" widget-harbor:local &&
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml up -d --no-build --no-deps hub
```

本版本为模块表增加来源分组字段，原内容保持不变。回退前保留本次备份。旧版本不支持网页修改后的密码，回退旧镜像时使用 `.env` 初始密码。旧镜像 ID、源码和数据备份保留在该备份目录。不要运行全局镜像清理，以免删除回退镜像。

## 本次新增功能

- 设置导航 SVG 图标；右上角头像可打开管理员设置，上传 PNG/JPEG/WebP 头像、修改密码、退出登录。
- 合集支持本地图标上传（最大 1 MiB），自动托管为资源文件并写入图标 URL。
- 列表/卡片切换，按最近更新、名称、大小排序；全选当前筛选结果、批量修改兼容平台、批量删除。
- 新导入模块按原订阅地址分组。旧模块没有保存订阅来源，按远程站点分组；本地文件单独分组。
- 删除合集可勾选同时删除模块，仍被其他合集引用的模块保留。
- 链接导入后台执行，显示已完成数量；关闭弹窗不取消任务，可通过进度栏取消。取消要等待当前下载返回（每文件最多约 40 秒），提交前取消不会写入模块。任务状态在内存中，重启会清除。
- 非空订阅描述优先；空描述回退到脚本元数据。已经导入且描述为空的模块可点击卡片/列表“刷新”重新解析，无需再导入整套合集。若两处都没有描述，则仍为空。

## 内网和 Lucky 同时登录

`SITE_URL` 继续使用 `https://widgets.example.com:5090`，负责生成公开订阅链接。如需内网管理，在 `.env` 的 `ALLOWED_ORIGINS` 中添加实际内网地址，例如 `http://192.168.1.1:8765`（多个地址用英文逗号分隔）。公开发行的升级脚本保留原配置，不猜测局域网地址。不使用任意来源通配符；HTTPS 登录使用 Secure Cookie，明确允许的 HTTP 内网入口使用普通 Cookie。两个地址分别登录。

配置修改后执行：

```sh
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml up -d --no-deps hub
```

保留原目录和数据，只升级，不要删除项目目录。头像存于 SQLite，完整数据库备份包含头像；网页 JSON 备份仍只包含模块与合集。来源分组字段包含在 JSON 备份内。
