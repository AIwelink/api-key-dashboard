# API Key Dashboard Backend

FastAPI + MongoDB 后端。业务代码按领域放在 `app/modules/*`，HTTP 接口放在 `app/routers/*`。

## 安装与启动

后端读取项目根目录 `.env`：

```powershell
cd backend
python -m uv sync
python -m uv run python -m app.run
```

开发时也可以直接启动 uvicorn：

```powershell
python -m uv run uvicorn app.main:app --reload
```

健康检查：

```http
GET /health
```

检查 MongoDB：

```powershell
python -m uv run python -m app.check_mongo
```

## 配置

常用根目录环境变量：

```text
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
FRONTEND_ORIGIN=http://127.0.0.1:5173

MONGODB_URI=
MONGODB_HOST=localhost
MONGODB_PORT=27017
MONGODB_USER=
MONGODB_PASSWORD=
MONGODB_DB=api_key_admin

INITIAL_OWNER_EMAIL=admin@example.com
INITIAL_OWNER_PASSWORD=change-me
```

如果 `users` 为空且配置了初始 Owner，启动时会创建必须修改密码的 Owner。sub2api 和客户端站点后续通过管理页面写入 MongoDB；不要在代码或文档中固定生产 URL 和 API Key。

## 代码结构

```text
app/main.py              FastAPI 生命周期和 router 注册
app/routers/             HTTP 接口
app/modules/accounts/    本地账号、导入和生命周期
app/modules/api_pools/   池配置、额度和状态偏好
app/modules/sub2api/     远端客户端、缓存、容量、探测和操作
app/modules/system/      Token、在线、审计、站点和启动迁移
app/modules/notifications/ 通知通道
app/modules/events/      事件记录
app/modules/todo/        待办流程
app/modules/agent/       Agent 运维能力
```

详细开发规则见 [开发与架构约定](../docs/design/14-development-guide.md)。

## 主要 API 域

```text
/api/auth
/api/users
/api/accounts
/api/imports
/api/sub2api-sites
/api/api-pools
/api/client-sites
/api/notification-channels
/api/event-records
/api/todo-items
/api/presence
/api/agent
/api/audit-logs
```

具体 method 和 payload 以 router、Pydantic schema 和自动生成的 OpenAPI 为准。开发环境启动后可查看 `/docs`。

## sub2api 缓存

核心集合：

```text
sub2api_sites
sub2api_groups_cache
sub2api_accounts_cache
long_7d_account_probes
sub2api_cache_meta
sub2api_dashboard_trends
sub2api_tpm_samples
sub2api_capacity_samples
```

刷新语义：

- `POST /api/sub2api-sites/{site_id}/refresh`：访问远端并更新 groups/accounts/usage 缓存。
- groups/accounts GET：只读 MongoDB。
- 新站点默认刷新间隔 30 分钟，可按站点修改。
- scheduler 每 30 秒检查到期站点；同站点刷新使用 3 秒任务防抖。
- 前端 60 秒静默刷新只读缓存，不触发远端同步。

参见 [API 账号池状态与缓存设计](../docs/design/15-api-pool-status-cache.md) 和 [实时容量契约](../docs/design/30-api-pool-realtime-capacity-and-presence.md)。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

快速语法检查：

```powershell
python -m compileall app
```

## 安全约定

- 账号凭证目前可能明文存储，异常、日志和审计必须脱敏。
- `account_json` 是外部 JSON，`metadata` 是本地管理层；不要互相替代。
- 账号身份匹配使用规范化邮箱或明确远端绑定，不使用展示名称。
- API Key、Webhook 密钥、access token、refresh token 和邮箱授权 token 不得出现在接口列表响应、审计详情或测试快照中。
