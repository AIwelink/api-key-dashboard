# Data Model

> **文档状态：基础模型。** 本文前半部分的 `account_json + metadata` 仍是当前核心结构；后续新增集合只记录主要字段，不保证覆盖全部运行时索引。sub2api 缓存、容量采样和前台在线集合以 [15-api-pool-status-cache.md](./15-api-pool-status-cache.md) 与 [30-api-pool-realtime-capacity-and-presence.md](./30-api-pool-realtime-capacity-and-presence.md) 为准。

当前采用简化存储结构：MongoDB 中一个账号对应一个文档，一个账号保存一个 JSON，不再拆分独立密钥表，也不做加密字段表。

## Core Rule

账号文档只保留两块核心数据：

```js
{
  _id,
  account_json: {},
  metadata: {}
}
```

- `account_json` 保存 sub2api 需要的原始账号 JSON。
- `metadata` 保存本系统需要的所有管理信息。
- 上传页人工填写字段同时写入 `metadata` 和 `account_json.extra`；`metadata` 方便后台查询，`account_json.extra` 随账号 JSON 保留。
- 凭据、token、邮箱和接码 session 等 MVP 阶段直接明文保存在 MongoDB。
- 导出和推送 sub2api 时，只使用 `account_json` 组装最终 JSON，不修改结构。

## Collections

```text
users
teams
team_members
accounts
sync_jobs
sync_events
audit_logs
app_settings
sub2api_sites
sub2api_groups_cache
sub2api_accounts_cache
sub2api_cache_meta
```

## accounts

一个账号一条文档。

```js
{
  _id,
  account_json: {
    name,
    platform,
    type,
    expires_at,
    auto_pause_on_expired,
    concurrency,
    priority,
    credentials: {},
    extra: {}
  },
  metadata: {
    created_at,
    updated_at,
    uploader_name,
    uploaded_by_user_id,
    email,
    email_session,
    account_type: "plus" | "free" | "pro" | "other",
    payment_type: "paypal_multi" | "paypal_single" | "no_card" | "gopay" | "other",
    "2FA",
    phone_bound: true | false,
    phone_number,
    payment_type_note,
    remark,
    account_status,
    manual_status_label,
    used_quota,
    last_request_at,
    last_checked_at,
    last_error,
    tags: [],
    source: "manual" | "upload" | "import" | "sync",
    file_name,
    sha256
  }
}
```

## Field Source

### System generated

```js
metadata.created_at
metadata.updated_at
metadata.uploaded_by_user_id
```

### User filled

```js
metadata.uploader_name
metadata.email_session
metadata.account_type
metadata.payment_type
metadata["2FA"]
metadata.phone_bound
metadata.phone_number
metadata.payment_type_note
metadata.remark
metadata.manual_status_label
```

### Parsed from account_json

```js
metadata.email
```

邮箱解析优先级：

1. `account_json.credentials.email`
2. `account_json.extra.email`
3. `account_json.name`

### Fetched from sub2api

```js
metadata.account_status
metadata.used_quota
metadata.last_request_at
metadata.last_checked_at
metadata.last_error
```

## users

系统不开放公开注册，用户由后台创建。

```js
{
  _id,
  email,
  name,
  password_hash,
  status: "active" | "disabled" | "pending_password_reset",
  must_change_password,
  last_login_at,
  last_login_ip,
  created_by,
  updated_by,
  created_at,
  updated_at
}
```

## teams

```js
{
  _id,
  name,
  status: "active" | "disabled",
  created_at,
  updated_at
}
```

## team_members

```js
{
  _id,
  team_id,
  user_id,
  role: "owner" | "admin" | "maintainer" | "viewer",
  status: "active" | "disabled",
  created_by,
  created_at,
  updated_at
}
```

## sync_jobs

```js
{
  _id,
  type: "manual" | "scheduled" | "import_after_sync",
  scope: "account" | "all" | "selection",
  account_ids: [],
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled",
  dry_run,
  summary: {
    created: 0,
    updated: 0,
    paused: 0,
    deleted: 0,
    failed: 0,
    skipped: 0
  },
  started_at,
  finished_at,
  created_by,
  created_at
}
```

## sync_events

```js
{
  _id,
  sync_job_id,
  account_id,
  action: "create" | "update" | "pause" | "delete" | "skip" | "error",
  status: "planned" | "succeeded" | "failed" | "skipped",
  diff: {},
  error_message,
  created_at
}
```

## audit_logs

```js
{
  _id,
  actor_type: "user" | "system",
  actor_id,
  action,
  resource_type: "account" | "sync_job" | "setting" | "user",
  resource_id,
  before,
  after,
  ip,
  user_agent,
  created_at
}
```

MVP 阶段账号内容明文存储，但审计日志仍建议只记录字段变更摘要，不复制完整 `account_json`。

## sub2api_sites

sub2api 站点当前由数据库管理，支持多个站点。生产 URL、站点 ID 和密钥不是固定常量。

```js
{
  _id: "<site_id>",
  name,
  base_url,
  site_type: "sub2api",
  token,
  status: "active" | "disabled" | "deleted",
  refresh_interval_minutes,
  auto_remove_abnormal_accounts,
  uptime_kuma_url,
  uptime_kuma_api_key,
  source: "database",
  created_at,
  updated_at
}
```

公开 API 会移除 `token` 和 `uptime_kuma_api_key`，改为返回 `token_configured` 与 `uptime_kuma_api_key_configured`。`newapi` 等客户站点保存在独立的 `client_sites` 集合，并通过 `/api/client-sites` 管理。

## sub2api_groups_cache

保存远程 sub2api group 的观测缓存。

```js
{
  _id: "{site_id}:{group_id}",
  site_id,
  group_id,
  group: {},
  fetched_at
}
```

## sub2api_accounts_cache

保存远程 sub2api account 的观测缓存，供 API 账号池状态页面读取。

```js
{
  _id: "{site_id}:{sub2api_account_id}",
  site_id,
  sub2api_account_id,
  group_ids: [],
  status,
  schedulable,
  account: {},
  fetched_at
}
```

`account` 是 sub2api Admin API 返回的远程账号对象，只作为远程状态快照，不替代本地 `accounts.account_json`。

## sub2api_cache_meta

保存每个站点最近一次缓存刷新状态。

```js
{
  _id: site_id,
  site_id,
  status: "scheduled" | "refreshing" | "succeeded" | "failed",
  requested_at,
  started_at,
  finished_at,
  last_refreshed_at,
  groups,
  accounts,
  updated_at
}
```
