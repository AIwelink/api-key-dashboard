# System Architecture

本文档记录当前已经落地的真实架构，作为后续开发新功能时的总览。

## 当前技术栈

### 后端

- 语言：Python 3.12+
- Web 框架：FastAPI
- ASGI Server：Uvicorn
- 数据库：MongoDB
- MongoDB Driver：Motor
- 配置：Pydantic Settings，从项目根目录 `.env` 读取
- HTTP Client：HTTPX，用于后续连接 sub2api
- 依赖管理：uv

### 前端

- 语言：TypeScript
- 框架：React
- 构建工具：Vite
- 包管理：npm
- 样式：原生 CSS，集中在 `frontend/styles.css`

### 当前不作为硬依赖

Redis 暂时不需要。后续出现以下需求时再引入：

- 多实例部署，需要分布式锁。
- 同步任务数量变多，需要可靠队列。
- 需要 WebSocket / SSE 推送任务状态。
- 需要更严格的限流、去重、缓存。

当前 API 账号池状态功能使用 MongoDB 做持久缓存，并在单进程后端内使用 3 秒刷新防抖锁。该实现适合当前单实例部署；多实例部署时再把刷新锁和任务队列迁移到 Redis。

## 总体结构

```text
Browser
  |
  v
Vite React Frontend
  |
  v
FastAPI Backend
  |
  |-- Auth / RBAC
  |-- User Management
  |-- Account CRUD
  |-- Import / Export
  |-- Sync / sub2api Client
  |-- sub2api Sites / Cache
  |-- Audit Log
  |
  v
MongoDB
```

## 后端目录

```text
backend/app/
  main.py                  FastAPI app 入口，注册 CORS、lifespan、routers
  run.py                   本地启动入口，读取 BACKEND_HOST / BACKEND_PORT
  config.py                读取项目根目录 .env
  database.py              MongoDB 连接生命周期
  schemas.py               Pydantic 请求/响应模型
  security.py              登录、JWT、当前用户、角色权限
  utils.py                 通用序列化、时间、ObjectId、邮箱解析
  routers/
    auth.py                登录和当前用户
    users.py               后台用户管理，不开放注册
    accounts.py            账号 CRUD、筛选、排序、软删除
    imports.py             JSON 预览、批量导入、sub2api JSON 导出
    sync.py                同步预览和执行
    settings.py            sub2api 配置和同步策略
    sub2api_sites.py       API 账号池站点、groups、accounts 缓存读取和刷新
    audit.py               审计日志查询
  services/
    accounts.py            账号业务逻辑、metadata 规范化、extra 同步
    json_parser.py         后端 JSON 容错解析
    sub2api.py             sub2api 调用封装
    sub2api_cache.py       sub2api groups/accounts 的 MongoDB 缓存、刷新锁和后台刷新
    audit.py               审计日志写入
    bootstrap.py           索引和初始 owner 创建
```

## 前端目录

```text
frontend/
  index.html
  styles.css               全局样式
  vite.config.ts           从根目录 .env 读取 Vite 配置
  src/
    main.tsx               React 入口
    App.tsx                登录态、侧边栏、页面分配
    types.ts               前端共享类型
    api/client.ts          fetch 封装、Bearer Token、错误处理
    utils/
      format.ts            显示格式化
      jsonParser.ts        前端 JSON 容错解析
    pages/
      LoginPage.tsx        登录
      UploadPage.tsx       上传账号，合并添加和导入
      AccountsPage.tsx     账号列表、筛选、排序、导出、编辑
      ApiPoolStatusPage.tsx API 账号池状态，展示 sub2api groups/accounts 调度状态
      SyncPage.tsx         同步操作
      UsersPage.tsx        后台用户管理
      AuditPage.tsx        审计日志
```

## 数据核心原则

MongoDB 中一个账号对应一个文档：

```js
{
  account_json: {},
  metadata: {}
}
```

- `account_json` 是 sub2api 账号对象，导出和推送时必须保持结构。
- `metadata` 是本系统管理字段，用于筛选、排序、审计和状态展示。
- 用户填写的账号字段同时写入 `metadata` 和 `account_json.extra`。
- 上传人、修改人、创建时间、更新时间由后端根据当前登录用户自动写入。
- 账号 JSON、token、2FA 等当前阶段明文存储在 MongoDB。

API 账号池状态另有 sub2api 缓存集合：

```text
sub2api_sites
sub2api_groups_cache
sub2api_accounts_cache
sub2api_cache_meta
```

这些集合保存远程 sub2api 的观测结果，不替代本系统 `accounts` 中的本地账号管理数据。`API 账号池状态` 和 `账号池逻辑管理` 读取同一份缓存，不拆分同步链路。页面加载和切换账号池只读 MongoDB 缓存，不访问远程 sub2api；远程刷新只由“同步账号池数据”或后台定时任务触发。

## 主要业务流

### 登录

1. 前端调用 `POST /api/auth/login`。
2. 后端校验用户和密码。
3. 前端保存 `access_token` 和当前用户到 `localStorage`。
4. 后续请求由 `api/client.ts` 自动加 `Authorization: Bearer ...`。

### 上传账号

1. 前端 `UploadPage` 支持填入模式和解析模式。
2. 前端可先进行 JSON 解析和预览。
3. 保存时调用 `POST /api/imports/commit`。
4. 后端从 payload 中抽取包含 `credentials` 的账号对象。
5. 每个账号生成一条 MongoDB 文档。
6. 后端把用户填写字段写入 `metadata`，并同步到 `account_json.extra`。

### 编辑账号

1. 前端 `AccountsPage` 点击编辑打开右侧编辑面板。
2. 表单从 `metadata`、`account_json.extra`、`credentials` 回填字段。
3. 保存时调用 `PATCH /api/accounts/{account_id}`。
4. 后端更新 `account_json` 和 `metadata`。
5. 编辑来源为 `source = "edit"` 时，清空字段会同步移除 `account_json.extra` 中对应旧值。

### 导出 sub2api JSON

1. 前端账号列表页调用 `GET /api/exports/sub2api`。
2. 后端读取未软删除账号。
3. 只使用每条文档的 `account_json` 组装：

```js
{
  exported_at,
  proxies: [],
  accounts: []
}
```

## 权限模型

当前角色：

- `owner`
- `admin`
- `maintainer`
- `viewer`

系统不开放注册。用户只能由后台创建。账号创建、编辑、同步等写操作由后端 `require_roles(...)` 控制。

## 配置模型

项目使用根目录 `.env` 作为统一配置源。

后端读取：

- `BACKEND_HOST`
- `BACKEND_PORT`
- `FRONTEND_ORIGIN`
- `MONGODB_HOST`
- `MONGODB_PORT`
- `MONGODB_USER`
- `MONGODB_PASSWORD`
- `MONGODB_DB`
- `INITIAL_OWNER_EMAIL`
- `INITIAL_OWNER_PASSWORD`
- `SUB2API_BASE_URL`
- `SUB2API_TOKEN`

前端读取：

- `VITE_FRONTEND_HOST`
- `VITE_FRONTEND_PORT`
- `VITE_API_BASE_URL`

## 启动与验证

后端：

```powershell
cd backend
python3 -m uv sync
python3 -m uv run python3 -m app.run
```

前端：

```powershell
npm.cmd --prefix frontend install
npm.cmd --prefix frontend run dev
```

验证：

```powershell
python3 -m compileall backend\app
npm.cmd --prefix frontend run build
```

## 扩展方向

后续功能优先沿用当前分层：

- 新 API：先加 `schemas.py` 请求模型，再加 `routers/xxx.py`，业务逻辑放 `services/xxx.py`。
- 新页面：加 `frontend/src/pages/XxxPage.tsx`，再在 `App.tsx` 分配导航和页面。
- 新账号字段：同步更新 `docs/design/12-account-fields.md`、前端类型、上传/编辑表单、后端 `EXTRA_METADATA_KEYS`。
- 新 sub2api 能力：先封装在 `services/sub2api.py`，业务层不要直接拼请求。
