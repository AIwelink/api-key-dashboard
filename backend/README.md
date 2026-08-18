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

### 飞书扫码登录

飞书登录必须使用当前企业租户下的自建应用，机器人 Webhook 不能用于 OAuth 登录。先在飞书开放平台启用网页登录，并为应用开通用户基本信息与组织邮箱读取权限（`contact:user.base:readonly`、`contact:user.email:readonly`）。

回调地址必须与服务端地址完全一致，例如：

```text
https://account.example.com/api/auth/feishu/callback
```

生产环境配置：

```text
FRONTEND_ORIGIN=https://account.example.com
FEISHU_AUTH_ENABLED=false
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_REDIRECT_URI=https://account.example.com/api/auth/feishu/callback
FEISHU_ALLOWED_TENANT_KEYS=tenant_key_a
FEISHU_AUTHORIZE_BASE_URL=https://accounts.feishu.cn
FEISHU_OPEN_API_BASE_URL=https://open.feishu.cn
FEISHU_REQUEST_TIMEOUT_SECONDS=8
```

`FEISHU_ALLOWED_TENANT_KEYS` 支持逗号分隔多个租户；生产环境不得留空。`FEISHU_AUTHORIZE_BASE_URL` 只用于浏览器授权，`FEISHU_OPEN_API_BASE_URL` 只用于服务端换取用户身份，不要互换。

上线时先保持 `FEISHU_AUTH_ENABLED=false` 完成后端迁移和前端部署，再填写 App ID、App Secret、固定回调地址及租户白名单，最后开启认证。开启后，未绑定用户使用密码验证成功也必须继续完成飞书绑定；已经绑定的用户仍可使用密码作为应急入口。

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

## Growth PostgreSQL

Growth PostgreSQL 的 DSN 在管理面板“客户站点 > 访问流量分析配置”中保存，只有 `owner/admin` 可以查看和操作。保存配置只会更新管理配置，不会自动修改 Growth 数据库。

需要建库时，可以在页面中显式点击初始化，也可以在 `backend` 目录执行：

```powershell
.\.venv\Scripts\python.exe -m scripts.init_growth_database
```

初始化会创建 `growth.schema_migrations` 和 12 张业务表；迁移可幂等执行，重复初始化不会重复创建已存在的结构。

Growth API 基础路径：

```text
/api/settings/growth-database
/api/growth
```

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
sub2api_hourly_forecasts
sub2api_forecast_evaluations
sub2api_forecast_accuracy_summaries
```

刷新语义：

- `POST /api/sub2api-sites/{site_id}/refresh`：访问远端并更新 groups/accounts/usage 缓存。
- groups/accounts GET：只读 MongoDB。
- 新站点默认刷新间隔 30 分钟，可按站点修改。
- scheduler 每 30 秒检查到期站点；同站点刷新使用 3 秒任务防抖。
- 前端 60 秒静默刷新只读缓存，不触发远端同步。

预测准确性结算：

- 后台每 10 分钟结算一次逐小时预测和 5 分钟 Nowcast 快照。
- 目标自然小时结束 15 分钟后写入 `provisional`，结束 90 分钟后重新读取 PostgreSQL 并覆写为 `final`。
- `sub2api_forecast_evaluations` 使用确定性 `_id`，重复执行不会产生重复样本；记录保留 180 天。
- `sub2api_forecast_accuracy_summaries` 按站点和分组保存当前模型版本的 24h、7d、28d WAPE、Bias、P90 Coverage、Pinball Loss、MAE 和预测步长分段。
- 状态页只把 `final` 样本计入准确性，预测缓存过期后评估记录仍可用于回测。

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
- 飞书 App Secret、授权码、OAuth access token、本地登录票据和 JWT 不得出现在 URL、日志或审计详情中。
