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
