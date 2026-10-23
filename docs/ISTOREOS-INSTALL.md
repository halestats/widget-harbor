# 安装到 /mnt/nvme/tools

适用于 x86_64 / aarch64 iStoreOS，要求 Docker Compose v2。专用安装脚本使用 `/mnt/nvme` 数据盘及以下目录；其他磁盘路径请按 [通用 iStoreOS 指南](ISTOREOS.md) 手动部署。示例 IP 为 `192.168.1.1`，安装时必须传入实际局域网 IPv4 地址。

固定目录：

```text
/mnt/nvme/tools/widget-harbor/       源码与 Compose 文件
/mnt/nvme/tools/widget-harbor/.env   随机管理员密码及站点配置，权限 600
/mnt/nvme/tools/widget-harbor/data/  SQLite 数据目录
```

## 1. 上传安装包

从 [Release 页面](https://github.com/halestats/widget-harbor/releases/latest) 下载带版本号的 `widget-harbor-v1.3.1.tar.gz`。不要使用 GitHub 自动生成的 Source code 包，其根目录名称不同。

早期文档中的 `widget-harbor-istoreos.tar.gz` 是同类安装包的别名；如照旧文档执行，可重命名下载文件，或将命令中的包名改为实际文件名。

在已经登录的 iStoreOS 终端执行：

```sh
mkdir -p /mnt/nvme/tools
```

用电脑上的 SFTP/SCP 或已有文件管理工具，把 `widget-harbor-v1.3.1.tar.gz` 上传到 `/mnt/nvme/tools/`。电脑如使用 scp，OpenWrt 系统未安装 SFTP 子系统时可使用 `scp -O` 的旧 SCP 协议。密码在你自己的终端输入，不要发送到聊天中。

## 2. 解压并安装

在 iStoreOS 终端执行。已有同名目录时会停止，不覆盖原文件：

```sh
cd /mnt/nvme/tools
if [ -e widget-harbor ] || [ -L widget-harbor ]; then
    echo 'widget-harbor 目录已存在，请先核对内容，不要直接覆盖。'
else
    tar -xzf widget-harbor-v1.3.1.tar.gz && (
        cd widget-harbor && ISTOREOS_IP=192.168.1.1 sh scripts/install-istoreos.sh
    )
fi
```

脚本检查数据盘挂载、至少 2 GiB 可用空间、现有项目、端口和 Compose。只创建本项目配置和数据目录，不覆盖已有 `.env` 或 data，不更改 WireGuard、Lucky、防火墙、Docker 服务或其他容器。

首次构建需要设备访问 Docker Hub；根据网络状态可能耗时。如果失败，配置会保留。修复原因后，在项目目录执行下面的 Compose 命令继续，不要反复运行初始化脚本：

```sh
cd /mnt/nvme/tools/widget-harbor
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml up -d --build hub
```

## 3. 登录

容器健康检查通过后，浏览器打开 `http://192.168.1.1:8765`。

在 iStoreOS 终端本地查看管理员密码：

```sh
sed -n 's/^ADMIN_PASSWORD=//p' /mnt/nvme/tools/widget-harbor/.env
```

不要把这条命令的输出发送给他人。手机在外面使用这个私网地址的订阅时，也要连接能到达家中网段的 WireGuard。

## 4. 查看状态

```sh
cd /mnt/nvme/tools/widget-harbor
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml ps
docker compose -f docker-compose.yml -f docker-compose.istoreos.yml logs --tail=80 hub
```

可提供状态和经过检查的报错日志排障，不要发送 `.env`。容器 healthy 仅证明容器内服务可用，仍需通过 WireGuard 从电脑或手机打开实际页面。

`/rom` 是只读系统镜像，其 100% 使用率不等于数据盘故障。多个 overlay 挂载可能共享同一数据盘，不应把各行容量相加。

完整网络、备份和反向代理说明见 [ISTOREOS.md](ISTOREOS.md) 和 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 精简系统缺少 od 的修复

2026-10-23 更新的安装脚本从 Linux 内核随机 UUID 接口生成密码，使用已安装的 awk 校验并处理两个独立 UUID，拼接后截取 48 个十六进制字符，不依赖 od、openssl、tr 字符类、cut 或宿主机 Python。生成失败会在写入配置之前停止。

早期安装包出现 `od: not found` 时，说明停在密码生成阶段；配置与数据目录尚未由该次运行创建。可替换安装脚本后重新运行，无需安装系统软件包或重启 Docker。

同日兼容性补充：早期替换命令中的 `tr -d "[:space:]-"` 仍依赖字符类支持；[OpenWrt 默认 BusyBox 配置](https://github.com/openwrt/openwrt/blob/master/package/utils/busybox/Config-defaults.in) 将 `BUSYBOX_DEFAULT_FEATURE_TR_CLASSES` 设为关闭。当前版本使用 `scripts/random-secret.sh`（仅依赖安装器已经使用的 awk），并验证完整输入长度与十六进制字符，再生成密码。设备实际编译选项仍应以实机为准。
