# Development Guide

本文档面向后续新功能开发，说明当前项目的代码组织、设计约定和常见改动路径。

## 基本原则

1. 后端负责权限、数据规范化、审计和数据库写入。
2. 前端负责页面交互、表单状态、展示格式化和调用 API。
3. `account_json` 是外部契约，尽量保持 sub2api 账号对象结构。
4. `metadata` 是内部管理层，所有筛选、排序、状态、人工标注优先查它。
5. 用户填写的账号管理字段要同时写入 `metadata` 和 `account_json.extra`。
6. 上传人、修改人、创建时间、更新时间不由前端填写，由后端根据登录用户生成。
7. 不开放注册；用户管理只能通过后台页面和对应 API。

## 后端开发约定

### 新增接口

新增接口通常按这个顺序改：

1. 在 `backend/app/schemas.py` 定义请求模型。
2. 在 `backend/app/routers/` 增加或修改 router。
3. 在 `backend/app/services/` 增加业务函数。
4. 在 `backend/app/main.py` 注册新 router。
5. 需要审计时调用 `write_audit_log(...)`。

示例结构：

```text
routers/accounts.py
  接收 HTTP 请求
  做 Depends 权限控制
  调用 service

services/accounts.py
  做业务规则
  做 MongoDB 查询/更新
  返回 serialize_doc 后的数据
```

### 权限

读接口通常使用：

```py
Depends(get_current_user)
```

写接口根据风险使用：

```py
Depends(require_roles("owner", "admin", "maintainer"))
```

用户管理、删除等高风险操作只给 `owner` / `admin`。

### MongoDB 文档

账号文档核心结构固定：

```js
{
  account_json: {},
  metadata: {}
}
```

新增账号管理字段时，通常要改：

- `backend/app/services/accounts.py` 的 `EXTRA_METADATA_KEYS`
- `normalize_metadata(...)`
- 前端上传和编辑表单
- `docs/design/12-account-fields.md`

### JSON 处理

前端和后端都支持容错 JSON：

- 标准 JSON
- 顶层 `{ accounts: [...] }`
- 账号数组
- 单个账号对象
- 连续对象：`{} {}`

导入时只接受能解析到 `credentials` 的账号对象。

### sub2api

sub2api 相关调用统一放在：

```text
backend/app/services/sub2api.py
```

业务层不要直接拼 URL 或 token。后续要新增 sub2api 接口时，先在 `sub2api.py` 增加方法，再由 `sync.py` 或其他 service 调用。

API 账号池状态相关缓存放在：

```text
backend/app/services/sub2api_cache.py
backend/app/routers/sub2api_sites.py
```

约定：

- 页面读 groups/accounts 时只读 MongoDB 缓存。
- 只有 `POST /api/sub2api-sites/{site_id}/refresh` 和后台 scheduler 访问远程 sub2api。
- 当前不使用 Redis；单实例刷新防抖锁在进程内实现。
- 后续推送账号、自动补位等写操作，执行前后都应调用刷新流程，但要复用现有防抖锁。

## 前端开发约定

### 页面分配

主页面入口是：

```text
frontend/src/App.tsx
```

导航顺序当前为：

1. 上传账号
2. 账号列表
3. API 账号池状态
4. 同步
5. 用户
6. 审计

新增页面时：

1. 新建 `frontend/src/pages/XxxPage.tsx`
2. 在 `frontend/src/types.ts` 扩展 `ViewName`
3. 在 `App.tsx` 的 `navItems` 添加入口
4. 在 `App.tsx` 中分配渲染

### API 调用

统一使用：

```text
frontend/src/api/client.ts
```

它负责：

- 拼接 `VITE_API_BASE_URL`
- 设置 `Content-Type: application/json`
- 自动附加 Bearer Token
- 解析后端错误信息

页面里不要重复写 fetch 封装。

### 类型

共享类型放在：

```text
frontend/src/types.ts
```

当前核心类型：

```ts
AccountDocument = {
  id: string;
  account_json: Record<string, unknown>;
  metadata: Record<string, unknown>;
}
```

账号字段目前保持较宽松的 `Record<string, unknown>`，因为 sub2api JSON 的字段可能继续变化。

### 样式

样式集中在：

```text
frontend/styles.css
```

当前 UI 方向：

- 管理后台风格
- 紧凑、可扫描
- 侧边栏导航
- 表格和筛选优先
- 不做营销页，不做大 hero
- 字段标签中字段名加粗，必填用浅色辅助文本

## 账号字段写入规则

用户填写字段：

```js
email_session
account_type
payment_type
"2FA"
self_produced
purchase_source
purchase_account_type
phone_bound
phone_number
remark
manual_status_label
```

保存时写入：

```js
metadata[field] = value
account_json.extra[field] = value
```

`phone_bound` 必须是布尔值：

```js
true | false
```

前端表单里可用字符串 `"true"` / `"false"` 做 select 状态，但提交给后端时要转换成 boolean。

`self_produced` 也必须是布尔值：

```js
true | false
```

当 `self_produced = false` 时，`purchase_source` 和 `purchase_account_type` 必填。购买账号金幺模板默认填入 `purchase_source = 金幺`、`purchase_account_type = free`。

`purchase_source` 是历史来源字段，编辑账号时即使 `self_produced` 后续改为 `true`，也应该继续保留，除非用户明确清空。`purchase_account_type` 用于记录购买时账号类型，常见场景是购买时为 `free`，后续升级后当前 `account_type` 改为 `plus`。

解析模板通过 `source_template` 标识：

```text
sub2api
purchased_jinyao
```

新增导入模板时，前后端都要更新：

1. `frontend/src/types.ts` 的 `UploadTemplate`。
2. `frontend/src/utils/jsonParser.ts` 的模板转换逻辑。
3. `backend/app/schemas.py` 的 `UploadSourceTemplate`。
4. `backend/app/services/json_parser.py` 的模板转换逻辑。
5. `docs/design/12-account-fields.md` 的字段映射。

## 常见新功能路径

### 增加一个账号筛选条件

1. 后端 `routers/accounts.py` 增加 Query 参数。
2. 后端 `services/accounts.py` 的 `list_accounts(...)` 增加 MongoDB query 条件。
3. 前端 `AccountsPage.tsx` 的 `Filters` 增加字段。
4. 筛选表单增加 input/select。
5. 确认 URLSearchParams 会传入新字段。

### 增加一个账号填写字段

1. 更新字段文档。
2. 后端 `EXTRA_METADATA_KEYS` 增加字段。
3. 后端 metadata 规范化时处理类型。
4. 前端 `UploadFields` 增加字段。
5. 上传页和编辑页同时增加控件。
6. 导入解析模式需要补充缺失信息时也显示该字段。

### 增加一个后台页面

1. 新建 page 组件。
2. 增加 `ViewName`。
3. `App.tsx` 加导航和渲染。
4. 需要后端数据时先补 API，再接 `api/client.ts`。

### 增加同步能力

1. 在 `services/sub2api.py` 封装 sub2api API。
2. 在 `services` 中写业务 reconciliation。
3. 在 `routers/sync.py` 暴露预览和执行接口。
4. 同步结果写回 `metadata.account_status`、`metadata.used_quota`、`metadata.last_request_at`、`metadata.last_checked_at`。
5. 前端 `SyncPage` 展示预览结果和执行结果。

### 增加 API 账号池状态能力

1. 远程 sub2api 接口先封装到 `services/sub2api.py`。
2. 需要缓存的远程观测状态写入 `services/sub2api_cache.py`。
3. 前端读取接口放在 `routers/sub2api_sites.py`，默认只读 MongoDB 缓存。
4. 前端页面在 `ApiPoolStatusPage.tsx` 中维护 `siteId:groupId:page:pageSize:statusFilter` 数据 key。
5. 表格和当前页统计只能渲染与当前 key 匹配的数据；总体容量读取当前 group 的后端 `capacity_summary`。
6. 账号池数据同步完成后清理该站点前端账号页缓存，再重新读取当前账号池。

## 列表性能约定

账号列表、可用池、使用备选池、待办与处理等列表接口默认返回轻量字段，不直接返回完整 `account_json.credentials`。列表只保留表格、筛选、处理面板需要的字段，例如 `metadata`、`account_json.name`、`credentials.email`、`credentials.plan_type`、`extra.email_session`、`extra["2FA"]`、`extra.password`。

需要编辑账号时，前端必须先通过 `GET /api/accounts/{account_id}` 获取完整账号，再打开编辑抽屉。这样可以避免每页列表携带大量 access token、refresh token、id token，减少加载时间。

## 验证命令

每次改后端：

```powershell
python -m compileall backend\app
```

每次改前端：

```powershell
npm.cmd --prefix frontend run build
```

本地运行：

```powershell
python -m uv run python -m app.run
npm.cmd --prefix frontend run dev
```

## 当前边界

- 没有引入 Redis。
- 没有公开注册。
- 没有加密账号凭据。
- 没有把 sub2api JSON 拆成复杂关系表。
- 导出 sub2api 时以 `account_json` 为准，不用 `metadata` 重新拼账号结构。
