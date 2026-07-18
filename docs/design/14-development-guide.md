# 开发与架构约定

本文面向后续功能开发，记录当前仓库的代码分层、关键不变量、常用改动路径和验证命令。本文描述的是现行结构；早期文档中的 `backend/app/services/*` 已不是主要业务目录。

## 1. 技术栈与启动入口

- 后端：Python 3.12+、FastAPI、Motor/MongoDB、httpx、Pydantic。
- 前端：React 19、TypeScript、Vite、Vitest。
- 后端应用入口：`backend/app/main.py`。
- 后端本地启动入口：`backend/app/run.py`。
- 前端入口：`frontend/src/App.tsx`。
- 根目录 `.env` 同时为前后端提供本地配置；密钥和生产站点信息不得写入文档或提交到 Git。

```powershell
cd backend
python -m uv sync
python -m uv run python -m app.run
```

```powershell
cd frontend
npm install
npm run dev
```

## 2. 后端分层

```text
backend/app/
  main.py                 FastAPI 生命周期、router 注册、后台任务启动
  routers/                HTTP 参数、权限、状态码和响应边界
  modules/                按领域组织的业务逻辑与数据访问
    accounts/             账号、导入、生命周期、操作记录
    api_pools/            账号池配置、额度配置、状态偏好
    sub2api/               远端客户端、缓存、探测、容量、推送与退回
    notifications/        钉钉、Telegram、飞书等通知通道
    system/               Token、用户在线、审计、客户端站点、启动迁移
    events/               事件记录与查询
    todo/                 待办流程
    agent/                Agent 决策、任务、记忆、巡检与评测
  schemas.py              跨 router 的 Pydantic 请求/响应模型
  security.py             登录用户、API Token actor 和角色权限
  database.py             MongoDB 连接与依赖
```

### 2.1 Router 与领域模块

Router 只负责：

- 解析 HTTP 参数和 Pydantic payload。
- 调用 `Depends(...)` 做认证和角色检查。
- 把领域异常转换为明确的 HTTP 状态码。
- 调用领域模块，并在需要时写审计日志。

领域模块负责：

- 业务规则、状态转换和幂等性。
- MongoDB 查询、更新、索引依赖和事务边界。
- 调用远端服务客户端。
- 返回可序列化的领域结果。

不要在页面对应的 router 中重新实现 sub2api URL、容量公式、通知签名或账号身份匹配。

### 2.2 领域目录选择

| 改动 | 首选目录 |
| --- | --- |
| 账号 CRUD、JSON 更新、上传批次 | `modules/accounts` |
| 本地账号池状态流转 | `modules/accounts/pool_lifecycle.py` |
| sub2api Admin API | `modules/sub2api/client.py` |
| 远端 groups/accounts 缓存 | `modules/sub2api/cache.py` |
| TPM/RPM、容量采样与风险计算 | `modules/sub2api/tpm_sampler.py`、`capacity_sampler.py`、`capacity_risk.py` |
| 账号探测与分组告警 | `modules/sub2api/account_probe.py`、`capacity_notifications.py` |
| 推送、删除、退回、复活 | `modules/sub2api/push.py`、`return_flow.py`、相关 router |
| 额度配置、池配置 | `modules/api_pools` |
| 通知通道 | `modules/notifications` |
| 系统 Token、在线探测、客户端站点 | `modules/system` |
| Agent 能力 | `modules/agent` |

### 2.3 后台任务

`backend/app/main.py` 当前启动：

- dashboard 快照刷新。
- sub2api 账号缓存启动刷新和定时刷新。
- 账号探测。
- TPM/RPM 分钟采样。
- 账号池容量采样。
- Agent scheduler。
- 日志清理。

新增后台任务必须具备取消处理、异常日志和重复启动保护。多实例部署前，不得假设进程内锁可以提供分布式互斥。

## 3. 核心数据不变量

### 3.1 本地账号

```js
{
  account_json: {},
  metadata: {}
}
```

- `account_json` 保留 sub2api 外部结构；导出和推送不能由 `metadata` 重组凭证。
- `metadata` 保存本系统的上传人、生命周期、站点归属、操作人、备注和分析字段。
- 列表接口返回轻量投影；需要完整凭证时使用账号详情接口。
- 用户填写的管理字段需要按 [12-account-fields.md](./12-account-fields.md) 的映射同步到 `metadata` 和 `account_json.extra`。

### 3.2 账号身份

- 远端账号与本地账号匹配只允许使用 `credentials.email` 的规范化邮箱或明确的远端 ID 绑定。
- `name` 是展示命名，不是唯一标识；同一批账号可以同名。
- 账号已绑定远端时，更新字段不能因为同名覆盖其他邮箱账号。
- `plan_type` 暂时缺失时应优先保留缓存中最近一次有效类型；没有历史值时才使用业务约定的回退类型。

### 3.3 编辑人与操作人

- 修改账号内容字段才更新编辑人和编辑时间。
- 推送、删除远端、池移动、调度、测试等操作记录操作人，不应伪造为账号内容编辑。
- 自动任务的 actor 应明确标记为系统或 Agent，不能写成未知用户。

## 4. sub2api 缓存与容量边界

- 站点配置来自 MongoDB，支持多个 sub2api 站点；不要在代码或文档中固定 `default`、端口或生产域名。
- `POST /api/sub2api-sites/{site_id}/refresh` 才会从远端同步 groups/accounts。
- groups/accounts 查询默认只读 MongoDB 缓存。
- 页面级 60 秒静默刷新只重新读取后端数据，不等同于远端同步。
- 分组统计和 `capacity_summary` 必须基于完整 group 缓存，不受当前页、每页数量或前端筛选影响。
- 账号列表数据库排序必须在 `skip/limit` 前执行；当前远端账号列表按 `created_at DESC, sub2api_account_id DESC`。
- 容量、实时可用时间、并发覆盖和前端阈值以 [30-api-pool-realtime-capacity-and-presence.md](./30-api-pool-realtime-capacity-and-presence.md) 为唯一现行说明。

## 5. 权限与审计

读接口通常使用：

```py
Depends(require_roles("owner", "admin", "maintainer"))
```

写接口按风险限制：

- 普通运维操作：`owner` / `admin` / `maintainer`。
- 用户、站点删除、通知通道和高风险系统配置：通常仅 `owner` / `admin`。
- 前台在线列表和历史：仅 `owner`。
- Presence 心跳只接受登录用户；API Token actor 不代表浏览器用户。

所有敏感操作应调用审计或账号操作记录函数。审计内容只记录必要摘要，不复制 access token、refresh token、邮箱授权 token、Webhook 密钥或完整账号 JSON。

## 6. 前端结构与路由

```text
frontend/src/
  App.tsx                 登录状态、菜单分组、英文路径和页面装配
  api/client.ts           同源 API、Bearer Token 和错误解析
  pages/                  页面与页面级测试
  components/             可复用控件
  hooks/                  自动刷新、前台在线等生命周期逻辑
  utils/                  纯计算、格式化和对应测试
  types.ts                跨页面共享类型
```

`App.tsx` 的 `viewPaths` 是当前英文路径来源。新增页面时同步修改：

1. `frontend/src/types.ts` 的 `ViewName`。
2. `App.tsx` 的菜单、短标签、`viewPaths` 和页面渲染。
3. 新建 `frontend/src/pages/XxxPage.tsx`。
4. 必要的页面级权限与移动端默认行为。

移动端根路径默认进入 `/api-pool-status`，桌面端默认进入上传页。页面数据请求统一通过 `frontend/src/api/client.ts`，不能在页面中复制 token 和错误解析逻辑。

### 6.1 自动刷新

- 通用页面使用 `usePageAutoRefresh`，默认周期 60 秒。
- 只有页面可见且窗口有焦点时执行。
- 静默刷新保留现有内容，不显示全页 loading，也不为正常轮询弹成功提示。
- 请求完成时必须确认查询 key 仍匹配当前站点、group、分页和筛选条件，防止旧响应覆盖新选择。
- 数字变化动画只用于值确实变化时；不得让布局尺寸随数字闪动。

### 6.2 容量展示

- 后端返回公式结果，前端只做格式化、分级和说明。
- 实时可用时间与安全并发覆盖使用独立比例函数，不能复用峰值倍数进度条。
- 修改阈值时同步更新 hover/focus 帮助、纯函数测试和页面测试。
- 紫色顶级进度条的性能和无障碍约束见文档 `30`。

## 7. 常见开发路径

### 新增 API

1. 在现有领域模块中实现纯业务函数；只有形成新领域时才新建模块。
2. 在 `schemas.py` 定义需要验证的请求/响应模型。
3. 在 `routers/` 暴露接口和权限。
4. 在 `main.py` 注册新 router，仅当新增 router 文件时需要。
5. 添加领域测试、router 集成测试和审计断言。
6. 更新对应现行设计文档中的接口契约。

### 新增账号字段

1. 明确字段属于外部 JSON、`account_json.extra` 还是本地 `metadata`。
2. 更新后端规范化、轻量投影和详情返回。
3. 更新上传/编辑表单及共享类型。
4. 检查是否应更新编辑人，还是只写操作人。
5. 更新 [12-account-fields.md](./12-account-fields.md) 和导入测试。

### 修改容量或告警

1. 先修改纯计算模块及边界测试。
2. 明确使用实际额度、动态额度、总并发覆盖还是内部余量覆盖。
3. 保持“等待数据”不产生假危险告警。
4. 更新通知文本、补号建议和前端帮助。
5. 同步更新文档 `30`，不要只改历史规格。

## 8. 验证命令

后端完整测试：

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

后端语法检查：

```powershell
cd backend
python -m compileall app
```

前端测试与生产构建：

```powershell
cd frontend
npm test
npm run build
```

文档和差异检查：

```powershell
git diff --check
```

## 9. 当前边界

- MongoDB 是当前持久层；尚未引入 Redis 分布式锁或任务队列。
- 账号凭证仍可能明文存储，日志、审计和异常文本必须主动脱敏。
- 前端生产构建由 FastAPI 或反向代理提供，部署方式以当前服务器配置为准。
- 可用池和使用备选池入口暂时保留，但现行容量、并发和补号建议只计算当前 sub2api group。
