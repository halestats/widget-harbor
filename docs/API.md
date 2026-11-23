# API 与订阅格式

管理操作使用登录后 HttpOnly Cookie。写请求需 `Content-Type: application/json` 和 `X-Hub-Request: 1`；浏览器 Origin 必须与 SITE_URL 一致。登录有效期 12 小时，退出及服务重启后失效。无 CORS 放行。

## 公共只读接口

| 请求 | 作用 |
| --- | --- |
| `GET /healthz` | 健康检查，含数据库读取 |
| `GET /api/collections/{slug}.fwd` | Forward 订阅，只包含 forward/both 模块 |
| `GET /api/collections/{slug}.rex` | Rex 订阅，只包含 rex/both 模块 |
| `GET /api/modules/{id}/raw` | 模块原始字节，JS MIME |
| `GET /widgets/{id}/{filename}` | 资源或模块访问链接，ID 为实际索引 |
| `GET /download/{id}/{filename}` | 强制下载 |
| `GET /api/session` | 是否已登录、站点地址 |

文件名用于可读性，不作为数据库主键。无目录遍历或任意路径文件服务。支持 HEAD；没有分块上传、Range 下载、客户端 UA 限制。

订阅返回示例：

```json
{
  "title": "我的合集",
  "description": "常用模块",
  "icon": "",
  "widgets": [{
    "id": "org.example.widget",
    "title": "示例",
    "description": "",
    "requiredVersion": "0.0.1",
    "version": "1.0.0",
    "author": "Author",
    "icon": "",
    "site": "",
    "url": "https://widgets.example.com/api/modules/32位文件ID/raw?v=内容摘要"
  }]
}
```

Rex 唤起格式：`rex://widget?url=` 加 URL 编码后的完整 `.rex` 订阅链接。Forward 使用复制订阅链接导入，不推测其未核实的 URL Scheme。

## 管理接口

| 请求 | JSON 请求体 / 作用 |
| --- | --- |
| `POST /api/login` | `{ "password": "..." }` |
| `POST /api/logout` | `{}`，撤销当前会话 |
| `GET /api/state` | 文件元数据和全部合集，不返回文件正文 |
| `POST /api/modules` | 上传文件，见下例 |
| `PUT /api/modules/{id}` | 修改信息，或传入 data 替换字节 |
| `DELETE /api/modules/{id}` | 删除文件并移除所有合集引用 |
| `POST /api/modules/{id}/refresh` | `{}`，重新下载 source_url |
| `POST /api/import` | 远程模块或完整订阅转存 |
| `POST /api/collections` | 创建合集；slug 冲突返回 409 |
| `PUT /api/collections` | 按 slug 更新合集及成员 |
| `DELETE /api/collections/{slug}` | 删除合集，保留模块文件 |
| `GET /api/backup` | 导出完整 JSON 备份 |
| `POST /api/restore` | `{ "confirm":"RESTORE", "backup":备份对象 }` |

上传示例：

```json
{
  "filename": "demo.js",
  "data": "文件原始字节的Base64",
  "kind": "module",
  "platform": "both",
  "source_url": "https://example.com/demo.js"
}
```

- kind 为 `module` 或 `asset`；platform 为 `forward`、`rex` 或 `both`。
- 可显式覆盖 `title/description/version/author/requiredVersion/icon/site/widget_id`。
- 上传及内容替换会解析新元数据；仅改信息时保留其他元数据。
- 加密模块替换需额外传 `replace: true`。上传新文件、远程刷新不执行解密。
- `widget_id` 是脚本内部的模块身份，不是本站文件记录的 `id`。明文文件会读取 `WidgetMetadata.id`；加密文件无法自动读取，未提供身份时使用临时生成的 ID。将首页自带的加密模块迁移到订阅合集时，应在模块编辑页填写作者或首页配置明确提供的真实 ID，不能凭文件名猜测。此操作只修改订阅元数据，不修改加密字节或下载地址。
- 更正 ID 后仍需在播放器刷新订阅并核对首页来源。本站无法修改播放器中既有的 `file://` 引用或环境关联；不要承诺删除原模块后会自动迁移所有首页条目。
- `POST /api/import`：传 `{ "url":"https://...", "platform":"both" }`；无扩展名订阅加 `manifest:true`；资源加 `kind:"asset"`。
- 本地订阅文件：传 `filename` 与 Base64 `data`；其中模块 URL 应为绝对公开 URL。远程订阅支持相对模块 URL。

合集示例：

```json
{"slug":"favorites","title":"收藏","description":"","icon":"","module_ids":["文件ID1","文件ID2"]}
```

module_ids 按数组顺序输出。最多 100 个，不得重复；只能引用模块，不能引用资源。

常见状态：400 校验失败、401 未登录、403 请求来源/保护头无效、404 不存在、409 冲突、413 请求过大、429 登录频率限制、502/504 上游失败/超时。

## 修改管理员密码

`POST /api/password`：需有效会话及 `X-Hub-Request: 1`；请求 JSON 为 `current_password`、`new_password`。校验当前密码，新密码限制 12–1024 字符；成功返回 `{ "ok": true }` 并使所有会话失效。错误当前密码返回 400，限流返回 429，跨站请求返回 403，未登录返回 401。凭据原子写入失败时旧密码仍有效。

## 合集同步（v1.3.0）

创建/更新合集支持 `source_url` 和布尔 `auto_refresh`。`GET /api/state` 的合集附带 `origin`、`last_attempt`、`last_success`、`error`，时间为 Unix 秒。`POST /api/collections/{slug}/refresh` 启动后台同步，返回 202；与导入共用任务锁（冲突返回 409），通过 `/api/import/status` 查看进度、`POST /api/import/cancel` 请求取消。需要会话及来源校验。

### v1.3.1

后台任务状态增加 `id`，每次任务唯一，用于关闭终态提示。远程合集刷新同时同步上游 title、description、icon（缺失时保留，description/icon 空字符串时清空）；slug 不变。自建合集资料不变。

## v1.4 图库与分类

以下写入接口均要求管理员会话与来源校验。

- `POST /api/images`：`{data: base64}`，返回独立图片 URL；PNG/JPEG/WebP/GIF，最大 1 MiB。
- `GET /images/{sha256}`：公开图片，内容摘要 URL 使用不可变缓存。
- `POST /api/folders`、`PUT /api/folders/{id}`：`{name, icon}`。
- `DELETE /api/folders/{id}`：删除分类，文件回到根目录。
- `POST /api/assets/move`：`{ids, folder_id}`，空字符串表示根目录。
- `PUT /api/order`：`{scope, ids}`，scope 为 modules/assets/collections/folders，合集用 slug，其他用 ID。
- `GET /api/state` 额外返回 folders、locations、orders；合集的 icon_display 是缓存地址。

图片保存在数据库及 data/images/，JSON 备份含图片原始数据。资源 URL 不受移动文件夹或排序影响。

## v1.4.1 文件刷新

`POST /api/modules/batch` 支持 `{ids, action:"refresh"}`（202，后台任务）和 `{ids, action:"auto_refresh", enabled:true/false}`。沿用 `/api/import/status` 与 `/api/import/cancel` 查看/取消任务，返回成功、跳过、失败数量与错误列表。`PUT /api/modules/{id}` 可传布尔值 auto_refresh。状态中的每个文件带 refresh，记录自动刷新设置和最近尝试/成功/错误。JSON 备份增加可选 file_sync 字段。

### 播放器刷新联动（v1.4.2）

播放器重新请求 `.fwd` / `.rex` 订阅时，会触发后台刷新对应合集。导入合集同步上游资料和成员；自建合集刷新其中有远程来源的模块，本地文件保留。服务最多等待 8 秒：完成则返回新订阅，超时或失败则返回已保存内容，后台任务继续；慢任务完成后再次刷新播放器即可读取新版本。

同一合集距上次刷新尝试不足 60 秒时复用缓存；已有导入或同步任务运行时也直接返回缓存。此联动不依赖“每天自动刷新”开关。HEAD 探测和单个文件下载不会触发同步。服务无法区分播放器点击刷新和普通订阅 GET 请求，因此订阅访问均适用上述规则；播放器若使用本地缓存、没有请求服务器，则不会触发。

## Rex 导出兼容性

`.rex` 响应仅省略空的可选 URL 字段（合集 `icon`，模块 `icon` / `site`），保留 `description`、`author` 等文本字段，即使值为空字符串。v1.4.4 错误地省略所有空字符串，可能导致 Rex 报“无法识别的模块数据”；修复后恢复这些文本字段。有值的字段保持原样。Swift `JSONDecoder` 对可选 URL 字段的空字符串会报 `Invalid URL string`，而字段缺失可正常解码。不要用空字符串表示未填写的可选 URL。`.fwd` 格式保持不变。

## 模块与首页管理

- 管理员调用 `POST /api/maintenance/preview`，传入上传数据或远程链接，查看文件类型及重复模块所在的合集。单模块按内容摘要、已知模块 ID 或文件名比对，是否原位更新由管理员决定。旧版上传和订阅导入 API 仍可直接使用。
- 模块内容变化后保留上一版七天；`POST /api/modules/{id}/rollback` 传当前内容摘要 `expected`。原记录、直链、订阅关系、远程来源和刷新设置保留。已启用的定时刷新仍可能再次更新文件。
- Rex 首页包在「首页配置」中管理。仅开启分享后生成公开下载地址。替换首页文件沿用分享地址，关闭再开启会生成新地址。模块引用取自包内布局，显示的是导出时的记录。

### Rex 首页包检查

- 首页配置接收 v1、v2 `.rex` 首页包，保留原文件。v2 支持内联 HTML 和 `rex-html/` 内的模板文件。引用不存在或路径不安全的 HTML 文件会返回 400，并指明栏目和卡片位置。
- 管理员可通过 `GET /api/homes/{id}/check` 查看静态报告。报告按原包 SHA-256 和检查规则版本缓存，列出 HTML 卡片、缺字段、内置脚本、本机依赖和模块来源。每类最多 200 条明细。检查不执行 HTML 或脚本，也不请求远程地址。
- `GET /api/homes/{id}/tools` 列出可编辑卡片和同 ID 的本站 Rex 模块。`POST` 提交 `expected`（原包摘要）和 `action`，可用 `relink` 更换所选本机／内置脚本引用，或用 `html` 给所选卡片添加、替换或移除 HTML。`mode` 默认 `copy`，新副本默认私有。`replace` 保留首页 ID 和原分享地址。
- 已公开的首页可通过 `POST /api/homes/{id}/share` 提交 `expected`（当前文件摘要）、`rotate: true` 和 `expected_share`（当前分享标识）更换公开地址。旧链接立即失效，首页 ID 和包内容保持不变。

### 刷新、归类与备份
- 合集成员下载失败时继续保留原快照。「仅重试失败项」会重新核对上游清单，十分钟内可复用上次成功下载且来源和元数据仍匹配的成员。批量文件刷新也可按错误列表重试。
- 在「资源文件 → 自动归类」输入文件名通配符，预览后应用到根目录。可选保存规则供以后上传或链接导入时使用。指定文件夹的导入优先使用指定位置。
- 完整备份包含首页包、分享状态、有效的上一版模块和分类规则；可以恢复旧版没有这些字段的备份。

### 模块整理

在「设置 → 模块整理」可按关键词分组、向自建合集追加成员、预览清理文件名、接管旧链接或查找重复模块。「订阅合集」下的「模块合集 / 首页配置 / 链接来源」分别管理三种内容。接管旧链接沿用原来的手动或定时刷新设置。

登录后向 `/api/organizer/rules`、`filenames` 或 `legacy` 发送 POST 获取候选记录及预览 token；提交同一请求并附上 token 和用户勾选的 ids，才会写入。规则参数为 keywords（逗号分隔）、label（分组名）和 slug（自建合集），至少指定一个目标。auto 为 true 时保存为新增模块规则，按创建顺序使用首个匹配。预览期间候选发生变化会返回 409。文件名清理仅作用于本站下载文件名，已有公开链接继续按记录 ID 读取文件。


模块库支持手动分组，分组名与自动分类规则共用。管理员可以向 `POST /api/module-groups` 提交以下操作。

| action | 字段 | 效果 |
| --- | --- | --- |
| create | new_name | 创建空分组 |
| rename | name、new_name | 同步更新模块和规则的分组名 |
| assign | ids、name | 批量移动模块，空名称表示移回未分类 |
| delete | name | 将模块移回未分类，同时移除规则的分组目标 |

`GET /api/state` 返回 `module_groups` 和按模块 ID 索引的 `labels`。分组会随完整备份导出和恢复。横条卡片的「＋ 合集」调用 `POST /api/collections/{slug}/members/{id}`，以空 JSON 对象为请求体，将模块追加到自建合集。重复提交会保留原有顺序。


### 自动刷新与失败重试

- 定时刷新及播放器触发的刷新会对已保存的模块文件发送上游提供的 `If-None-Match` 和 `If-Modified-Since`。上游返回的 `304` 不会改写模块正文。没有响应验证信息时，通过 SHA-256 判断内容是否变化。手动刷新仍可重新下载原文件。
- 管理员可调用 `POST /api/sources/retry`，提交 `{}` 重试失败来源，或提交 `{ "id":"文件 ID" }` / `{ "slug":"合集标识" }` 重试单个来源。接口返回 `queued`，进度沿用 `/api/import/status`。
- 合集更新仍在全部成员验证通过后提交。符合当前来源、模块内容及上游元数据的成功下载可在重试时复用。临时重试文件最多保留 7 天，全站合计上限 100 MiB。
