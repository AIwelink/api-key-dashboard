# API Design

本文档是后端 API 初稿。当前数据存储采用简化模型：一个账号对应一个 MongoDB 文档，包含 `account_json` 和 `metadata`。

## Common Rules

- 所有接口返回 JSON。
- 系统不开放公开注册，因此不提供 `POST /api/auth/register`。
- 账号 JSON 明文保存。
- 前端提交的 `account_json` 必须保持 sub2api 原始账号结构。
- 所有额外管理字段统一放进 `metadata`。
- 删除账号默认软删除，标记到 `metadata.deleted_at` 和 `metadata.deleted_by`。

## Error Format

```json
{
  "error": {
    "code": "ACCOUNT_NOT_FOUND",
    "message": "Account not found",
    "details": {}
  }
}
```

## Auth

```text
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
POST /api/auth/change-password
```

## Users

后台用户管理接口。只有 Owner 或具备权限的 Admin 可以访问。

```text
GET    /api/users
POST   /api/users
GET    /api/users/:id
PATCH  /api/users/:id
POST   /api/users/:id/reset-password
POST   /api/users/:id/disable
POST   /api/users/:id/enable
```

### Create User

```json
{
  "email": "member@example.com",
  "name": "Team Member",
  "role": "maintainer",
  "send_setup_link": true
}
```

## Accounts

```text
GET    /api/accounts
POST   /api/accounts
GET    /api/accounts/:id
PATCH  /api/accounts/:id
DELETE /api/accounts/:id
```

### Create Account

```json
{
  "account_json": {
    "name": "user@example.com",
    "platform": "openai",
    "type": "oauth",
    "expires_at": 1780380391,
    "auto_pause_on_expired": true,
    "concurrency": 10,
    "priority": 1,
    "credentials": {},
    "extra": {}
  },
  "metadata": {
    "email": "user@example.com",
    "email_session": "email and sms session text",
    "account_type": "plus",
    "payment_type": "paypal_multi",
    "2FA": "DP7...example",
    "phone_bound": true,
    "phone_number": "+10000000000",
    "payment_type_note": "",
    "remark": "备注示例",
    "manual_status_label": "人工标注示例",
    "tags": []
  }
}
```

后端自动补充：

```js
metadata.created_at
metadata.updated_at
metadata.uploaded_by_user_id
```

如果 `metadata.email` 没有填写，后端从 `account_json.credentials.email`、`account_json.extra.email`、`account_json.name` 中按顺序解析。

### Account Response

```json
{
  "id": "account_id",
  "account_json": {
    "name": "user@example.com",
    "platform": "openai",
    "type": "oauth",
    "expires_at": 1780380391,
    "auto_pause_on_expired": true,
    "concurrency": 10,
    "priority": 1,
    "credentials": {},
    "extra": {}
  },
  "metadata": {
    "created_at": "2026-05-25T00:00:00.000Z",
    "updated_at": "2026-05-25T00:00:00.000Z",
    "uploader_name": "Alice",
    "uploaded_by_user_id": "user_id",
    "updated_by_name": "Alice",
    "updated_by_user_id": "user_id",
    "email": "user@example.com",
    "email_session": "email and sms session text",
    "account_type": "plus",
    "payment_type": "paypal_multi",
    "2FA": "DP7...example",
    "phone_bound": true,
    "phone_number": "+10000000000",
    "payment_type_note": "",
    "account_status": "active",
    "remark": "备注示例",
    "manual_status_label": "人工标注示例",
    "used_quota": 12.34,
    "last_request_at": "2026-05-23T06:10:08.104Z",
    "last_checked_at": "2026-05-25T00:00:00.000Z",
    "tags": []
  }
}
```

## Import

```text
POST /api/imports/preview
POST /api/imports/commit
GET  /api/exports/sub2api
```

导入 sub2api export JSON 时：

- 顶层 `accounts[]` 中每个对象生成一条账号文档。
- 每个 `accounts[]` 元素原样保存到 `account_json`。
- 支付类型、备注等本系统字段同时放入 `metadata` 和 `account_json.extra`。上传人和修改人由当前登录用户自动写入。

## Export

```text
GET /api/exports/sub2api
```

导出结果：

```json
{
  "exported_at": "2026-05-25T00:00:00.000Z",
  "proxies": [],
  "accounts": []
}
```

`accounts` 数组由每条账号文档的 `account_json` 直接组成。

## Sync

```text
POST /api/sync/preview
POST /api/sync/run
GET  /api/sync/jobs
GET  /api/sync/jobs/:id
POST /api/accounts/:id/sync
```

sub2api 返回的状态、已使用额度、最后请求时间写入 `metadata`。

## sub2api Sites / API Account Pools

API 账号池状态页面使用以下接口读取站点、groups 和账号调度状态：

```text
GET   /api/sub2api-sites
PATCH /api/sub2api-sites/{site_id}
POST  /api/sub2api-sites/{site_id}/test
POST  /api/sub2api-sites/{site_id}/refresh
GET   /api/sub2api-sites/{site_id}/groups
GET   /api/sub2api-sites/{site_id}/groups/{group_id}/accounts
```

`PATCH` 当前支持保存 `refresh_interval_minutes`。`POST /refresh` 从远程 sub2api 拉取 groups/accounts 并写入 MongoDB 缓存，带 3 秒防抖锁。`GET /groups` 和 `GET /groups/{group_id}/accounts` 只读 MongoDB 缓存，不触发远程刷新。

刷新成功响应示例：

```json
{
  "ok": true,
  "site_id": "default",
  "status": "succeeded",
  "groups": 3,
  "accounts": 751,
  "started_at": "...",
  "finished_at": "..."
}
```

## Settings

```text
GET   /api/settings/sub2api
PATCH /api/settings/sub2api
POST  /api/settings/sub2api/test
GET   /api/settings/sync-policy
PATCH /api/settings/sync-policy
```

## Audit Logs

```text
GET /api/audit-logs
GET /api/accounts/:id/audit-logs
```

审计日志建议记录字段变更摘要，不复制完整 `account_json`。
