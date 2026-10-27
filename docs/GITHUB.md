# 发行维护

源码仓库：[halestats/widget-harbor](https://github.com/halestats/widget-harbor)。GitHub 托管代码和发行包，GitHub Pages 不能运行 Python 后端；服务部署在自己的 Docker 主机。

## 测试与发布

1. 在分支完成修改，更新 CHANGELOG 和界面版本，提交 PR。
2. CI 在 main 分支推送和 PR 时运行 Python 3.11 / 3.13 / 3.14 测试、前端逻辑回归及 Docker 健康检查。
3. 完成目标设备验证后，在 Releases 创建对应 `vMAJOR.MINOR.PATCH` 标签的 Release，填写变更与升级说明并发布。
4. `Release assets and container` 工作流运行回归测试，然后从 Git 跟踪文件生成带 `widget-harbor/` 根目录的 tar.gz、zip 和 SHA256SUMS.txt，并上传到该 Release。
5. 测试和文件上传成功后，构建 amd64 / arm64 镜像推送到 GHCR。Actions 实际成功才说明发行资产或镜像已就绪。

源码压缩包不包含 `.env`、数据库、备份、缓存或导入的第三方模块。不要将这些运行数据加入 Git。发行附件应使用工作流生成的 `widget-harbor-v*.tar.gz`，其目录名适配安装脚本；GitHub 自动的 Source code 压缩包根目录名不同。

## 本地打包

在干净的 Git 检出目录执行：

```sh
python3 scripts/package_release.py --version v1.4.5 --output dist
```

脚本只收集 Git 跟踪文件。打包前提交所需文件，并检查 `git status`。

## GHCR 镜像

工作流使用仓库自带的 `GITHUB_TOKEN`，不需要额外上传访问令牌。镜像地址：

```text
ghcr.io/halestats/widget-harbor:1.4.5
```

镜像构建成功后，维护者需在 Packages 中确认包可见性。源码仓库公开不代表镜像已可匿名拉取。用户始终可以按 README 从源码构建，不依赖 GHCR。

使用预构建镜像时，将 Compose 中 `build: .` 删除，`image:` 改为实际版本镜像，然后执行 pull 和 up。使用两个 Compose 文件时，所有命令都要带上两个 `-f` 参数，避免误切换数据卷。
