# iStoreOS + WireGuard 部署

以下以局域网地址 `192.168.1.1` 为例，请替换为自己的地址。数据盘、端口和网络连通性需在目标设备确认。远程访问私网地址时，设备需要已连接能访问该网段的 WireGuard 等 VPN。

## 第一步：在 iStoreOS 上做只读检查

浏览器打开 `http://192.168.1.1`，使用现有管理入口进入终端；不同安装版本的终端入口可能不同。也可以在电脑终端执行 `ssh root@192.168.1.1`，前提是你已启用 SSH 并使用该管理员账户。密码只在自己的终端输入，不要发送给他人。

**以下命令应在 iStoreOS 的终端执行，不是在 Mac 本地终端直接执行：**

```sh
uname -m
docker version --format '{{.Server.Version}}'
docker compose version
docker info --format '{{.DockerRootDir}}'
df -h
mount
docker ps --format 'table {{.Names}}\t{{.Ports}}'
```

若 `docker compose version` 不存在，再检查 `docker-compose version`，先反馈结果，不要因此升级或重启现有 Docker 服务。

需要确认：

1. 架构是否为 `x86_64` 或 `aarch64`；分别对应 amd64 和 arm64。其他架构不能直接假定受支持。
2. 项目及数据库放在哪个已挂载、空间充足的本地 Linux 数据盘目录。避免系统 `/overlay`；不建议在 FAT/NTFS/SMB/NFS 上直接运行此 SQLite 数据目录。
3. DockerRootDir 是否位于空间充足的数据盘。即使数据库改为外置目录，镜像层仍使用 DockerRootDir；不要为此自动迁移 Docker 数据。
4. 8765 端口是否空闲。必要时用 `ss -lnt` 或 `netstat -lnt` 进一步确认。

## 第二步：上传源码与配置

将源码包上传并解压到确认好的数据盘项目目录，进入包含 `docker-compose.yml` 的目录。可使用已有的文件管理器或 SFTP 工具。上传方式不要求开放公网服务。

首次部署且不存在 `.env` 时，复制模板：

```sh
cp -i deploy/istoreos.env.example .env
chmod 600 .env
```

用编辑器修改 `.env`：

- `ADMIN_PASSWORD`：替换示例值，至少 12 个字符。建议使用较长的随机字母数字密码，避免手工填写时遇到 Compose 的 `$` 插值问题。
- `SITE_URL`：保持 `http://192.168.1.1:8765`。
- `BIND_ADDRESS`：保持 `192.168.1.1`，无需改为 host 网络。
- `WIDGET_DATA_DIR`：填写已确认的数据盘上、本项目专用数据目录的绝对路径。

如果更换端口，同时修改 `HOST_PORT` 和 `SITE_URL` 中的端口。

确认数据盘已挂载后，创建本项目独立数据目录。将下面路径替换为 `.env` 中的实际路径，不要直接复制占位内容：

```sh
mkdir -p /实际数据盘路径/widget-harbor-data
chown 10001:10001 /实际数据盘路径/widget-harbor-data
chmod 750 /实际数据盘路径/widget-harbor-data
```

只修改本项目新建的数据目录，不要对整块磁盘或现有服务目录递归 chown。挂载源必须提前存在，配置不会自动创建它。磁盘重挂载或设备重启后仍应确认挂载正常；“目录存在”不能证明数据盘已挂载。

## 第三步：启动本项目

硬件、空间和 Compose 确认正常后执行：

```sh
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml up -d --build hub
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml ps
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml logs --tail=80 hub
```

首次构建需要 iStoreOS 本身能访问 Docker Hub，并会占用一定 CPU、内存和磁盘。如果基础镜像拉取失败，保留错误信息，不要使用来源不明的镜像站或一键脚本。也可在 GitHub 发布对应架构的镜像，再改用拉取镜像部署，见 `GITHUB.md`。

本配置使用两个文件：基础配置提供服务与网络设置，iStoreOS 配置仅覆盖 `/data` 的存储挂载。后续操作都带上这两个 `-f` 参数，避免误切换到基础配置的空命名卷。[Compose 合并规则](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/)

## 第四步：通过 WireGuard 验收

在当前电脑或已连接相同 VPN 的设备上打开：

- 管理页面：`http://192.168.1.1:8765`
- 健康检查：`http://192.168.1.1:8765/healthz`

健康检查应返回 `{"status":"ok"}`。然后登录、上传模块、创建合集：

```text
Forward: http://192.168.1.1:8765/api/collections/你的合集标识.fwd
Rex:     http://192.168.1.1:8765/api/collections/你的合集标识.rex
```

手机在外面时也需要连接能访问 `192.168.1.1` 的 WireGuard 隧道，才能拉取这些订阅和模块。HTTP 服务位于私网，远程 VPN 段由 WireGuard 加密；如果需要独立公网访问或端到端 HTTPS，再配置域名与反向代理。

若首页可达而 8765 不可达：先检查容器状态、日志、端口监听及现有防火墙。不要直接关闭防火墙。部署前 HTTP 200 只证明管理入口可达，不证明新服务端口已可达。

## Lucky 和备份

使用 Lucky 时，先确认它能访问应用监听的宿主机地址。

- 若应用仍绑定 `192.168.1.1`，代理目标应按实际网络可达性使用 `http://192.168.1.1:8765`。
- 若准备改为宿主机回环监听，须先核实 Lucky 在宿主机还是容器中运行；bridge 容器中的 `127.0.0.1` 不代表宿主机。
- 配置 HTTPS 域名后，同步修改 `SITE_URL` 并只重建本项目容器。通过域名访问；若还需要内网管理，在 `.env` 设置 `ALLOWED_ORIGINS=http://192.168.1.1:8765` 后重建本应用。两个入口分别登录。

先用页面“设置与备份 → 导出完整备份”保存数据。大数据库备份使用 `DEPLOYMENT.md` 中的 SQLite backup API；所有 Compose 命令增加 `-f docker-compose.yml -f docker-compose.istoreos.yml`，不要只复制正在写入的数据库主文件。
