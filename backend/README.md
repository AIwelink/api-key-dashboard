# API Key Admin Backend

Python/FastAPI 后端初版，实现当前设计里的简化存储模型：

```js
{
  account_json: {},
  metadata: {}
}
```

`account_json` 原样保存 sub2api 账号 JSON，`metadata` 保存后台管理字段。

## Setup

```powershell
cd backend
python -m uv sync
python -m uv run uvicorn app.main:app --reload
```

这里使用 `python -m uv`，避免 VS Code 终端没有刷新 PATH 时找不到 `uv.exe`。

后端读取项目根目录的 `.env`。如果还没有根目录 `.env`，可以从根目录 `.env.example` 复制一份再填写。当前项目不会自动覆盖已有 `.env`。

本地服务端口写在根目录 `.env`：

```text
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
FRONTEND_ORIGIN=http://127.0.0.1:5173
```

推荐用项目启动入口，直接读取 `.env`：

```powershell
python -m uv run python -m app.run
```

也可以手动传给 uvicorn：

```powershell
python -m uv run uvicorn app.main:app --reload --host $env:BACKEND_HOST --port $env:BACKEND_PORT
```

需要本地或远程 MongoDB：

```text
MONGODB_URI=
MONGODB_HOST=localhost
MONGODB_PORT=27017
MONGODB_USER=
MONGODB_PASSWORD=
MONGODB_DB=api_key_admin
```

如果你已经有完整连接串，可以只填 `MONGODB_URI`。如果没有，就分别填写 host、port、user、password 和 database name。

检查 MongoDB 连接：

```powershell
python -m uv run python -m app.check_mongo
```

如果出现 `Authentication failed`，优先检查：

- `MONGODB_USER` 是否是 MongoDB 用户名，不是系统登录名。
- `MONGODB_PASSWORD` 是否正确，特殊字符建议改用 `MONGODB_URI` 并 URL encode。
- 分开填写时，后端会使用 `MONGODB_DB` 作为认证数据库和业务数据库。
- 如果使用完整 `MONGODB_URI`，它会优先于分开的 host/user/password 配置。

系统不开放注册。首次启动时，如果 `users` 集合为空，会根据 `.env` 创建初始 Owner：

```text
INITIAL_OWNER_EMAIL=admin@example.com
INITIAL_OWNER_PASSWORD=change-me
```

sub2api 测试站点配置也放在根目录 `.env`：

```text
SUB2API_BASE_URL=http://216.167.70.204:5002
SUB2API_TOKEN=<sub2api-admin-api-key>
```

后端调用 sub2api Admin API 时使用请求头 `x-api-key`。

## Main APIs

- `POST /api/auth/login`
- `GET /api/auth/me`
- `GET /api/users`
- `POST /api/users`
- `GET /api/accounts`
- `POST /api/accounts`
- `PATCH /api/accounts/{id}`
- `DELETE /api/accounts/{id}`
- `POST /api/imports/preview`
- `POST /api/imports/commit`
- `GET /api/exports/sub2api`
- `POST /api/sync/run`
- `GET /api/sub2api-sites`
- `PATCH /api/sub2api-sites/{site_id}`
- `POST /api/sub2api-sites/{site_id}/test`
- `POST /api/sub2api-sites/{site_id}/refresh`
- `GET /api/sub2api-sites/{site_id}/groups`
- `GET /api/sub2api-sites/{site_id}/groups/{group_id}/accounts`
- `GET /api/audit-logs`

## sub2api Cache

API 账号池状态功能通过 MongoDB 缓存远程 sub2api 的 groups/accounts：

```text
sub2api_sites
sub2api_groups_cache
sub2api_accounts_cache
sub2api_cache_meta
```

刷新语义：

- `POST /api/sub2api-sites/{site_id}/refresh`：访问远程 sub2api，写入 MongoDB 缓存。
- `GET /api/sub2api-sites/{site_id}/groups`：只读 MongoDB 缓存。
- `GET /api/sub2api-sites/{site_id}/groups/{group_id}/accounts`：只读 MongoDB 缓存。
- 后台 scheduler 默认每 5 分钟刷新一次，可通过 `refresh_interval_minutes` 调整。
- 当前不使用 Redis；单实例内使用 3 秒刷新防抖锁。

## Notes

- 当前 MVP 明文保存账号 JSON。
- `metadata` 是后台管理字段，不会进入 sub2api 导出。
- `account_json.extra` 是 sub2api 原始字段，不要和 `metadata` 混用。
