# 远程数据库结构扫描报告

## 扫描信息

- 扫描时间：2026-07-19 07:52 UTC。
- 扫描方式：使用现有 SQL_DSN 和完整异步驱动，只读 `information_schema` / `pg_catalog`。
- 扫描范围：表、字段、主键、外键和索引。
- 未执行：业务表查询、行数统计、样例数据读取和任何写操作。
- 未保存：SQL_DSN、数据库用户名、密码、API Key、业务字段值和原始响应。

## 扫描结果

| 站点 | 范围 | 类型 | 数据库 | Endpoint | Schema | 表 | 结果 |
| --- | --- | --- | --- | --- | ---: | ---: | --- |
| `us06-5001` | 账号池 | Sub2API | PostgreSQL | `104.238.221.47:5433/sub2api-5001` | 1 | 82 | 成功 |
| `aigclink` | 客户站点 | NewAPI | MySQL | `104.238.221.47:3307/new-api-4001` | 1 | 31 | 成功 |
| `aiwelink` | 客户站点 | Sub2API | PostgreSQL | `216.167.70.204:5433/sub2api-8081` | 1 | 82 | 成功 |

全部 3 个站点扫描成功，没有连接或权限错误。

## Sub2API PostgreSQL

### 结构兼容性

`us06-5001` 和 `aiwelink` 均为 `public` schema、82 张表。对标准化后的表、字段、主外键和索引计算结构摘要，两站点结果完全相同：

```text
DC1C69EC7FE21901
```

因此可以实现一套参数化 `Sub2ApiPostgresRepository`，同时服务账号池站点和客户 Sub2API 站点。两类站点仍分别从 `sub2api_sites` 和 `client_sites` 读取连接配置，不合并站点模型。

### 全部表

```text
account_groups
accounts
announcement_reads
announcements
api_keys
atlas_schema_revisions
audit_logs
auth_cache_invalidation_outbox
auth_identities
auth_identity_channels
auth_identity_migration_reports
batch_image_events
batch_image_items
batch_image_jobs
billing_usage_entries
channel_account_stats_model_pricing
channel_account_stats_pricing_intervals
channel_account_stats_pricing_rules
channel_groups
channel_model_pricing
channel_monitor_aggregation_watermark
channel_monitor_daily_rollups
channel_monitor_histories
channel_monitor_request_templates
channel_monitors
channel_pricing_intervals
channels
content_moderation_logs
deleted_api_key_audits
error_passthrough_rules
groups
idempotency_records
identity_adoption_decisions
ops_alert_events
ops_alert_rules
ops_error_logs
ops_ingress_reject_aggregates
ops_job_heartbeats
ops_metrics_daily
ops_metrics_hourly
ops_system_log_cleanup_audits
ops_system_logs
ops_system_metrics
orphan_allowed_groups_audit
payment_audit_logs
payment_orders
payment_provider_instances
pending_auth_sessions
promo_code_usages
promo_codes
prompt_audit_events
prompt_audit_jobs
proxies
redeem_codes
scheduled_test_plans
scheduled_test_results
scheduler_outbox
schema_migrations
security_secrets
settings
subscription_plans
tls_fingerprint_profiles
usage_billing_dedup
usage_billing_dedup_archive
usage_cleanup_tasks
usage_dashboard_aggregation_watermark
usage_dashboard_daily
usage_dashboard_daily_users
usage_dashboard_hourly
usage_dashboard_hourly_users
usage_logs
user_affiliate_ledger
user_affiliates
user_allowed_groups
user_attribute_definitions
user_attribute_values
user_avatars
user_group_rate_multipliers
user_platform_quotas
user_provider_default_grants
user_subscriptions
users
```

### 账号池核心表

#### `accounts`

主键：`id`

核心字段：

```text
id, name, platform, type, status, schedulable, priority
credentials jsonb, extra jsonb
concurrency, load_factor, quota_dimension, rate_multiplier
last_used_at, rate_limited_at, rate_limit_reset_at, overload_until
temp_unschedulable_reason, temp_unschedulable_until
session_window_start, session_window_end, session_window_status
expires_at, error_message, created_at, updated_at, deleted_at
```

已存在账号状态、调度、限流时间、并发和最近使用时间索引。账号列表可以直接从该表读取。`credentials` 和 `extra` 包含敏感或半结构化数据，仓储必须在 SQL 结果进入 MongoDB 前做字段白名单映射，不能把完整 JSON 直接暴露给前端或日志。

#### `groups`

主键：`id`，活动组名称有唯一索引。

核心字段：

```text
id, name, platform, status, sort_order
subscription_type, rpm_limit
daily_limit_usd, weekly_limit_usd
rate_multiplier, peak_rate_enabled, peak_rate_multiplier
model_routing, models_list_config, supported_model_scopes
fallback_group_id, fallback_group_id_on_invalid_request
created_at, updated_at, deleted_at
```

现有 `GET /groups` 的读取部分可以直接由该表替代。

#### `account_groups`

复合主键：`account_id, group_id`。

```text
account_id -> accounts.id
group_id   -> groups.id
priority
created_at
```

该表是账号和分组的唯一关系来源。账号池按 group 查询应使用 `groups -> account_groups -> accounts`，而不是从账号名称或 JSON email 推断分组。

### 用量核心表

#### `usage_logs`

主键：`id`。已具备以下组合索引：

```text
account_id, created_at
group_id, created_at
user_id, created_at
api_key_id, created_at
model, created_at
created_at, model, upstream_model
```

核心维度和指标：

```text
created_at
account_id, group_id, user_id, api_key_id, channel_id
model, requested_model, upstream_model
input_tokens, output_tokens, cache_creation_tokens, cache_read_tokens
input_cost, output_cost, cache_creation_cost, cache_read_cost
total_cost, actual_cost, account_stats_cost, account_rate_multiplier
duration_ms, first_token_ms, stream, request_type
```

按账号、分组、用户和模型的精确历史统计可以直接从该表聚合。分组容量分析应使用 `group_id + created_at` 索引。

账号窗口统计已按 Sub2API 服务端实现确认：

- 5h/7d 官方使用率和重置时间来自 OpenAI/Codex 响应头，持久化在 `accounts.extra.codex_5h_*`、`accounts.extra.codex_7d_*`。
- `primary`/`secondary` 按 `window_minutes` 归一化，较短窗口为 5h、较长窗口为 7d；单窗口 `<= 360` 分钟视为 5h。
- 窗口未过期时，统计起点为 `reset_at - window_duration`；已过期或没有重置时间时，统计起点为当前时间减去窗口时长。
- 请求和 Token 来自 `usage_logs`；账号口径成本为 `COALESCE(account_stats_cost, total_cost) * COALESCE(account_rate_multiplier, 1)`，标准成本为 `total_cost`，用户成本为 `actual_cost`。
- Redis 仅保存调度账号快照副本；管理用量接口的持久化来源仍是 PostgreSQL，主动探测节流使用 Go 进程内缓存。

#### `usage_dashboard_hourly` / `usage_dashboard_daily`

站点整体预聚合表，分别以 `bucket_start` 和 `bucket_date` 为主键：

```text
total_requests
input_tokens, output_tokens
cache_creation_tokens, cache_read_tokens
total_cost, actual_cost, account_cost
total_duration_ms, active_users, computed_at
```

这两张表没有 `group_id`。站点整体趋势可以直接读取；按分组趋势仍需聚合 `usage_logs`。

### Sub2API HTTP 替换判断

| 当前能力 | 数据库替换 | 依据 |
| --- | --- | --- |
| groups 列表 | 可以 | `groups` |
| accounts 列表 | 可以 | `accounts + account_groups + groups` |
| 账号状态、调度、限流时间、并发 | 可以 | `accounts` |
| 站点整体小时/日趋势 | 可以 | `usage_dashboard_hourly/daily` |
| 分组历史趋势 | 可以，但需聚合 | `usage_logs(group_id, created_at)` |
| 账号历史消耗 | 可以，但需聚合 | `usage_logs(account_id, created_at)` |
| 5h/7d usage 窗口 | 可以；缺失快照时回退 HTTP | 官方比例和重置时间读取 `accounts.extra`，窗口请求/Token/成本聚合 `usage_logs(account_id, created_at)` |
| 账号测试、OAuth、recover-state | 不可以 | 属于远程动作，不是数据库读模型 |
| 创建、更新、删除、调度开关 | 保留 HTTP | 写操作继续使用管理 API，避免绕过业务校验和事件处理 |

## NewAPI MySQL

### 全部表

```text
abilities
authz_roles
casbin_rule
channels
checkins
custom_oauth_providers
logs
midjourneys
models
options
passkey_credentials
perf_metrics
prefill_groups
quota_data
redemptions
setups
subscription_orders
subscription_plans
subscription_pre_consume_records
system_instances
system_task_locks
system_tasks
tasks
tokens
top_ups
two_fa_backup_codes
two_fas
user_oauth_bindings
user_subscriptions
users
vendors
```

### 分析核心表

#### `quota_data`

字段与已实测 `/api/data`、`/api/data/users` 响应直接对应：

```text
id, created_at
model_name, username, user_id
token_used, count, quota
channel_id, token_id
use_group, node_name
```

索引覆盖 `created_at`、`model_name + username`、`user_id`、`channel_id`、`token_id`、`use_group` 和 `node_name`。模型小时统计和用户统计应优先直接查询该表，不再定时调用两个 HTTP 聚合接口。

#### `logs`

原始请求日志：

```text
created_at, type
user_id, username
token_id, token_name
channel_id, channel_name
model_name, group
prompt_tokens, completion_tokens, quota
request_id, upstream_request_id
use_time, is_stream, ip
```

具备时间、用户、模型、渠道、Token 和请求 ID 索引。后续详细审计和异常分析可以读取该表，但不应把原始 IP、请求内容或渠道密钥复制到本地分析集合。

#### `perf_metrics`

```text
bucket_ts, model_name, group
request_count, success_count
output_tokens, total_latency_ms
ttft_count, ttft_sum_ms, generation_ms
```

存在 `model_name + group + bucket_ts` 唯一索引。该表适合后续性能趋势、成功率、TTFT 和延迟分析，不替代当前已经实现的上游 RPM/TPM 原值分钟采样。

### 管理核心表

- `users`：用户、额度、已用额度、请求次数和状态。查询时必须排除 `password`、`access_token` 等敏感字段。
- `channels`：渠道状态、余额、模型和路由配置。查询时必须排除 `key`、Header 覆盖和可能包含密钥的 `other/settings`。
- `models`：模型目录、状态、厂商和标签。
- `tokens`：用户 Token 的额度、状态和分组。查询时不得读取或保存 `key`。

### NewAPI HTTP 替换判断

| 当前能力 | 数据库替换 | 依据 |
| --- | --- | --- |
| `/api/data` 模型用量 | 可以 | `quota_data` 按 `created_at + model_name` 聚合 |
| `/api/data/users` 用户用量 | 可以 | `quota_data` 按 `created_at + username/user_id` 聚合 |
| 用户、模型、渠道状态 | 可以 | `users`、`models`、`channels` |
| 详细请求分析 | 可以 | `logs` |
| 性能趋势 | 可以 | `perf_metrics` |
| RPM/TPM 分钟原值 | 暂时保留 HTTP | 已实现 `/api/log/stat` 原值采样，数据库等价公式尚未确认 |
| NewAPI 写操作 | 保留 HTTP | 避免绕过 NewAPI 的权限、校验和缓存刷新 |

## 后续重构顺序

1. 已完成：建立只读 PostgreSQL/MySQL 仓储边界，业务模块不直接拼接外部输入 SQL。
2. 已完成：Sub2API `groups/accounts/account_groups` 读链路切到 PostgreSQL，Mongo 和前端契约保持不变。
3. 已完成：站点整体 dashboard 趋势读取 `usage_dashboard_hourly/daily`。
4. 已完成：账号 5h/7d 窗口统计批量聚合 `usage_logs`；数据库失败或官方窗口缺失时回退 HTTP。
5. 待完成：按分组聚合 `usage_logs(group_id, created_at)`，替换分组 dashboard HTTP。
6. 待完成：NewAPI 模型/用户小时数据读取 `quota_data`；RPM/TPM 继续使用分钟采样器。

## 明确风险

- Sub2API `credentials`、`extra` 和 NewAPI 多个表包含密钥或认证信息。SQL 必须使用字段白名单；不得使用 `SELECT *`。
- 数据库读路径绕过远程 API 的响应规范化，仓储层必须保持现有 Mongo 缓存字段契约，前端不应感知数据源变化。
- 数据库版本升级可能改变表结构。上线前应比较结构摘要，不匹配时自动回退 HTTP 或阻止切换。
- 5h/7d 窗口已通过 Sub2API 源码核对和真实站点双读验证；RPM/TPM 与后续分组/模型聚合仍需分别校验。
