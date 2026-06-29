# Agent 运营观测、异常告警与通知系统设计

## 1. 目标

最近新增的功能围绕后续 agent 系统展开：系统不只展示当前 API 账号池状态，还要持续收集 sub2api 中账号的状态、寿命、异常、删除重加、重复邮箱、额度消耗和通知配置，给后续自动分析、自动补号、风控预警和运营报表提供稳定接口。

第一阶段已经落地的能力：

- 系统 Token：外部系统可以用 `akd_` 开头的 Bearer Token 调用后台接口。
- 通知机器人：通知列表支持钉钉自定义机器人和 Telegram 机器人，支持测试发送，也提供正式通用通知入口。
- 账号探测：后台 scheduler 轻量探测 active sub2api site 的账号状态。
- 分组探测配置：每个 site/group 可以单独设置是否探测、是否记录详细样本、样本保留时间和记录内容。
- 异常告警中心：独立菜单展示探测发现的告警，当前第一类是同邮箱多个 sub2 remote id；401 封号已接入正式通知。
- 告警已读：已读状态写回后端，用于后续通知机器人停止重复提醒。
- 容量估计参数：free/plus/team/k12/pro 每账号 5h 和 7d 额度可配置。
- API 池状态偏好：可以置顶默认站点和默认分组。

后续 agent 应以这些数据作为读取基础，避免直接依赖页面 DOM 或人工展示文案。

## 2. 关键原则

### 2.1 身份只按邮箱归一

账号长期身份必须使用 `site_id + normalized_email`。

原因：

- sub2api 的 remote account id 会因为手动删除、重新添加、远端迁移而变化。
- `name` 只是方便查看的命名，可能重复，不能用于账号匹配。
- 同一邮箱在 sub2 里出现多个 remote id 时，容量估计按一个账号处理，用量按多个 remote id 加和。

相关集合：

- `remote_account_identities._id = {site_id}:{normalized_email}`
- `remote_account_sessions.remote_account_id` 记录一次进入 sub2 的 remote 实例。
- `remote_account_status_events.remote_account_id` 记录事件发生时对应的 remote 实例。

### 2.2 当前状态和历史时间线分开

`sub2api_accounts_cache` 是当前快照；运营分析要读探测系统的历史集合：

- 当前判断：`remote_account_identities`
- 每次进入 sub2 的生命周期：`remote_account_sessions`
- 状态变化时间线：`remote_account_status_events`
- 短期原始样本：`remote_account_probe_samples`
- 每次探测摘要：`remote_account_probe_runs`

### 2.3 原始样本短期保留，事件长期保留

账号探测按分组配置的间隔运行，默认 3 分钟。短期样本通过 `expires_at` TTL 删除；状态变化事件、identity 和 session 用于长期分析，默认长期保留。

### 2.4 已读告警是通知去重依据

异常告警的已读状态不是前端本地状态，而是写入后端。后续通知模块发送告警时应该只发送未读告警；人工标记已读后停止重复通知。

重复邮箱告警的已读状态绑定当前 remote id 签名。如果同邮箱 remote id 集合变化，会自动重新变为未读。

## 3. 系统管理配置

### 3.1 系统 Token

页面：`系统管理 -> 系统 Token`

接口：

- `GET /api/api-tokens`
- `POST /api/api-tokens`
- `POST /api/api-tokens/{token_id}/revoke`

创建参数：

- `name`: Token 名称。
- `role`: `owner` / `admin` / `maintainer` / `viewer`。
- `expires_in_days`: 可选过期天数。
- `note`: 备注。

返回的明文 token 只在创建时返回一次。后续外部系统调用接口时使用：

```http
Authorization: Bearer akd_xxx
```

后端会把 API Token 映射成虚拟 actor：

- `_id = api_token:{token_id}`
- `actor_type = api_token`
- `role = token.role`
- 每次使用会更新 `last_used_at` 和 `usage_count`。

Agent 对接建议：

- 自动化 agent 使用最小所需角色，通常 `maintainer` 足够读取和执行维护操作。
- 涉及配置修改、通知配置、Token 管理时需要 `admin` 或 `owner`。

### 3.2 通知机器人

页面：`系统管理 -> 通知页`

接口：

- `GET /api/notification-channels`
- `POST /api/notification-channels`
- `PUT /api/notification-channels/{channel_id}`
- `DELETE /api/notification-channels/{channel_id}`
- `POST /api/notification-channels/{channel_id}/test`

集合：`notification_channels`

公共返回不会暴露敏感配置，只返回配置状态和预览：

- `webhook_configured`
- `signing_secret_configured`
- `webhook_preview`
- `telegram_bot_token_configured`
- `telegram_bot_token_preview`
- `telegram_chat_id`
- `last_delivery_at`
- `last_delivery_status`
- `last_delivery_message`

正式通知统一入口：

```python
send_notification_event(
    db,
    event_type="sub2api.account.401_detected",
    severity="critical",
    source="sub2api_account_probe",
    resource_type="notification_batch",
    resource_id=batch_id,
    title="AIwelink Pro 401 封号告警",
    text="...",
    markdown_text="...",
    payload={...},
    channel_ids=dingtalk_channel_ids,
)
```

调用方只负责描述业务事件，不直接处理钉钉/TG 发送细节。默认底层会查找所有 `status=active` 的通知渠道，逐个投递，并记录结果；如果调用方传入 `channel_ids`，则只投递指定渠道。

正式通知记录集合：

- `notification_events`: 一条业务通知事件。
- `notification_batches`: 需要聚合后再发出的业务通知批次。
- `notification_deliveries`: 每个渠道的一次投递结果。

`notification_events` 关键字段：

- `_id`
- `event_type`
- `severity`: `info` / `warning` / `critical`
- `source`
- `resource_type`
- `resource_id`
- `dedupe_key`
- `title`
- `text`
- `markdown_text`
- `payload`
- `status`: `success` / `partial` / `failed` / `skipped`
- `channel_count`
- `success_count`
- `failed_count`
- `created_at`
- `finished_at`

`notification_batches` 关键字段：

- `_id`
- `event_type`
- `source`
- `status`: `pending` / `sending` / `sent` / `failed` / `skipped`
- `window_start_at`
- `window_end_at`
- `first_event_at`
- `last_event_at`
- `event_count`
- `status_event_ids`
- `status_event_db_ids`: 回写 `remote_account_status_events` 用的数据库 `_id` 列表。
- `site_ids`
- `group_ids`
- `pro_group_ids`
- `account_names`
- `events`: 批次内事件 payload 快照。
- `notification_event_id`
- `notification_status`
- `notification_channel_count`
- `notification_success_count`
- `notification_failed_count`
- `created_at`
- `updated_at`

`notification_deliveries` 关键字段：

- `_id`
- `notification_event_id`
- `event_type`
- `severity`
- `channel_id`
- `channel_name`
- `channel_type`
- `status`: `success` / `failed`
- `message`
- `attempted_at`
- `created_at`

#### 钉钉自定义机器人

创建/更新字段：

- `channel_type = dingtalk`
- `webhook_url`: 钉钉自定义机器人 Webhook 地址。
- `signing_secret`: 钉钉自定义机器人加签密钥。
- `status`: `active` / `disabled`
- `note`: 用途说明。

发送方式：

- 后端按钉钉加签规则生成 `timestamp` 和 `sign`。
- 当前测试消息使用 markdown。
- 钉钉返回 `errcode != 0` 时视为失败。

#### Telegram 机器人

创建/更新字段：

- `channel_type = telegram`
- `telegram_bot_token`
- `telegram_chat_id`
- `status`: `active` / `disabled`
- `note`

发送方式：

- 后端调用 `https://api.telegram.org/bot{token}/sendMessage`。
- Telegram 返回 `ok != true` 时视为失败。

后续扩展建议：

- 不要把通知场景直接写死在 channel 上。
- 新增 `notification_rules` 或类似集合，把“容量预警 / 重复邮箱 / 401 风控 / 自动补号失败”等事件类型映射到一个或多个 channel。
- 告警通知应先读取未读告警，发送成功后可以保持未读，也可以按规则自动标记为“已通知但未读”。人工已读才停止提醒。

## 4. 账号探测配置

页面：`账号池管理 -> 账号探测配置`

接口：

- `GET /api/api-pools/observability/groups?site_id={site_id}`
- `PATCH /api/api-pools/observability/groups/{group_id}?site_id={site_id}`
- `POST /api/api-pools/observability/probe?site_id={site_id}`

集合：`group_observability_settings`

主键：

```text
_id = {site_id}:{group_id}
```

字段：

- `site_id`: sub2api 站点 ID。
- `group_id`: sub2api 分组 ID。
- `group_name`: 分组名称快照。
- `enabled`: 是否启用探测。关闭后该分组账号不会进入本次探测过滤。
- `detailed_enabled`: 是否启用详细记录。关闭后不写高频样本。
- `probe_interval_seconds`: 探测间隔，当前接口允许 60 到 3600 秒，默认 180 秒；前端按“分钟”配置并保存为秒。
- `sample_retention_days`: 样本保留天数，当前接口允许 1 到 90 天。free 分组默认较短，核心池默认较长。
- `record_usage_samples`: 是否记录用量样本。
- `record_status_events`: 是否记录状态变化事件。
- `record_duplicate_email_warning`: 是否记录重复邮箱告警事件。
- `status`: 配置状态。
- `created_at`
- `updated_at`
- `updated_by`

UI 中“记录内容”的含义：

- `事件`: 记录状态变化时间线，例如新发现、401、恢复、分组变化、远端消失、重新出现。
- `样本`: 记录每次探测的短期快照，用于后续聚合分析；最占存储。
- `重复邮箱`: 记录同邮箱多个 sub2 remote id 的异常告警，影响容量估计和用量加和。

默认策略：

- 含 `free` 字样的分组默认 `detailed_enabled=false`，`record_usage_samples=false`，样本保留较短。
- plus/team/k12/pro 或核心使用池建议开启详细记录。
- 即使关闭样本，也建议保留事件和重复邮箱记录，保证关键异常能进入告警中心。

## 5. 账号探测运行逻辑

后台启动时在 `main.py` 创建 `probe_scheduler_loop(db)`。

默认常量：

- `DEFAULT_PROBE_INTERVAL_SECONDS = 180`
- `PROBE_LOOP_SLEEP_SECONDS = 30`
- `DEFAULT_SAMPLE_RETENTION_DAYS = 14`
- `DEFAULT_MISSING_CONFIRM_COUNT = 3`
- `ACCOUNT_LIST_PAGE_SIZE = 200`
- `MAX_ACCOUNT_LIST_PAGES = 100`

流程：

1. 读取 active 的 sub2api site。
2. 根据该 site 的 `group_observability_settings` 计算最小探测间隔。
3. 到期后创建 `remote_account_probe_runs` 运行记录。
4. 调用 sub2api `/admin/accounts` 分页拉取账号列表，timezone 固定 `Asia/Shanghai`。
5. 按开启探测的 group 过滤账号。
6. 标准化账号字段：remote id、email、status、schedulable、error_message、group_ids、plan_type、last_used_at、usage snapshot。
7. 按 `normalized_email` 归一同邮箱账号。
8. 同邮箱多 remote id 时：
   - `duplicate_remote_count` 记录数量。
   - `current_remote_account_ids` 保存 remote id 列表。
   - 用量字段按加和处理。
   - 5h/7d 使用百分比按加和并封顶 100。
   - reset 时间取最小值。
   - 写入 `duplicate_email_detected` 事件。
9. 更新或创建 `remote_account_identities`。
   - 如果 `codex_7d_*` 或 total usage 字段相对上次探测回落，视为窗口清零，把清零前的窗口值结转到 `cumulative_usage_totals`。
   - 容量预估继续使用远端当前窗口字段；运营分析、删除/归档快照优先使用累计字段。
10. 远端 id 变化或重新出现时开启新的 `remote_account_sessions`。
11. 状态、错误、调度开关、分组变化时写入 `remote_account_status_events`。
12. 检测 401 和 401 恢复。
13. 写入短期 `remote_account_probe_samples`。
14. 对上次存在但本次没看到的 identity 增加 missing count。
15. 连续缺失达到阈值后标记 `current_presence=removed`，关闭当前 session。
16. 更新 `remote_account_probe_runs` 和 `remote_account_probe_meta`。

401 判断：

- 错误信息包含 `401`
- `token_invalidated`
- `token revoked`
- `authentication failed`
- `invalid_request_error`

401 正式通知：

- `401_detected` 事件写入后先判断是否属于 Pro 账号池。
- Pro 判断优先看账号 `plan_type=pro`、分组容量类型 `account_type=pro`，其次看分组名是否包含 `pro` 或 `20x`。
- 非 Pro 账号池只记录事件，`notification_status=skipped_non_pro`，不发正式封号通知。
- 新账号第一次被探测到时如果已经是 Pro 401，也会写入 `401_detected` 并进入通知批次。
- 已存在账号只有从非 401 变为 401 时才进入通知批次，避免每轮探测重复提醒。
- 如果账号恢复后再次 401，会重新写事件并进入新的通知批次。
- 3 分钟内出现多个 Pro 401 时聚合为一条钉钉通知，防止消息刷屏；每个封号事件仍然单独写入 `remote_account_status_events`。
- 封号通知只投递到 `channel_type=dingtalk` 且 `status=active` 的渠道，不进入“异常告警”的已读确认流。
- 关闭该分组的 `record_status_events` 后不会写事件，也不会发 401 通知。
- 通知失败只写入事件或批次的 `notification_status=failed` 和 `notification_error`，不会中断探测。

Pro 401 钉钉通知正文：

- 本次新增封号数。
- 账号：只显示 sub2api 账号 `name`，不在正文展示站点、分组、邮箱、Remote ID、错误详情。
- 1h 内封号总数。
- 今日封号总数，按 Asia/Shanghai 自然日计算。
- 剩余账号数。
- 5h 动态可用、5h 实际可用。
- 7d 动态可用、7d 实际可用。
- 峰值容量或预估天数只有进入红色危险状态时才显示；非红色状态不显示。

正常状态判断：

- `active`
- `ok`
- `healthy`
- `normal`
- `available`

异常状态判断：

- `abnormal`
- `error`
- `failed`
- `disabled`
- `inactive`
- `invalid`
- `revoked`
- 或存在 `error_message`

## 6. 账号运营数据模型

### 6.1 `remote_account_identities`

账号长期身份表，按邮箱归一。

关键字段：

- `_id`: `{site_id}:{normalized_email}`
- `site_id`
- `normalized_email`
- `email`
- `first_seen_at`
- `last_seen_at`
- `last_present_at`
- `current_presence`: `present` / `missing_suspected` / `removed`
- `missing_count`
- `current_remote_account_id`
- `current_remote_account_ids`
- `duplicate_remote_count`
- `current_session_id`
- `current_status`
- `current_schedulable`
- `current_error_message`
- `current_is_401`
- `current_group_ids`
- `plan_type`
- `last_usage_snapshot`
- `cumulative_usage_totals`: 周期窗口清零前会把上一轮窗口值结转到这里，保留账号生命周期累计用量。
- `cumulative_usage_snapshot`: 当前窗口值加累计基数后的快照，累计字段以 `*_cumulative` 结尾。
- `last_usage_rollover_at`
- `missing_confirm_count`
- `last_event_at`
- `total_sessions`
- `total_401_count`
- `total_recovery_count`
- `total_removed_count`
- `first_401_at`
- `last_401_at`
- `first_recovered_at`
- `last_recovered_at`
- `first_removed_at`
- `last_removed_at`
- `duplicate_email_alert_read_at`
- `duplicate_email_alert_read_by`
- `duplicate_email_alert_read_by_name`
- `duplicate_email_alert_read_signature`
- `duplicate_email_alert_read_note`
- `created_at`
- `updated_at`

Agent 用途：

- 判断账号是否当前仍在 sub2。
- 判断某邮箱账号出现过几次 401。
- 判断是否存在重复 remote id。
- 判断同一账号删除后是否重新加入。
- 作为账号长期运营分析的主表。

### 6.2 `remote_account_sessions`

账号每次进入 sub2 的生命周期。

关键字段：

- `_id`: `{site_id}:{normalized_email}:{session_index}`
- `site_id`
- `identity_id`
- `normalized_email`
- `email`
- `remote_account_id`
- `session_index`
- `started_at`
- `ended_at`
- `end_reason`
- `status`: `open` / `closed`
- `first_active_at`
- `last_active_at`
- `first_abnormal_at`
- `last_abnormal_at`
- `first_401_at`
- `last_401_at`
- `group_ids_first`
- `group_ids_last`
- `plan_type_first`
- `plan_type_last`
- `error_message_first`
- `error_message_last`
- `last_usage_snapshot`
- `cumulative_usage_totals`: 本次进入 sub2 session 内的累计用量。
- `cumulative_usage_snapshot`
- `last_usage_rollover_at`
- `created_at`
- `updated_at`

Agent 用途：

- 计算账号本次正常使用时长。
- 判断账号被手动删除后重新加入的次数。
- 对比不同来源、类型账号的寿命。

### 6.3 `remote_account_status_events`

状态变化事件时间线。

事件类型：

- `remote_account_seen_first`
- `remote_account_reappeared`
- `status_changed`
- `error_changed`
- `schedulable_changed`
- `group_changed`
- `401_detected`
- `401_recovered`
- `missing_suspected`
- `remote_removed_confirmed`
- `duplicate_email_detected`
- `usage_rollover`

关键字段：

- `site_id`
- `identity_id`
- `session_id`
- `normalized_email`
- `email`
- `remote_account_id`
- `event_type`
- `severity`: `info` / `warning` / `critical`
- `occurred_at`
- `detected_at`
- `previous_status`
- `current_status`
- `previous_schedulable`
- `current_schedulable`
- `previous_error_message`
- `current_error_message`
- `previous_group_ids`
- `current_group_ids`
- `is_401`
- `error_category`
- `usage_snapshot`
- `details`: `usage_rollover` 事件会记录 `rollover_fields`、清零前后的 usage snapshot 和累计 totals。
- `raw_excerpt`
- `notification_status`
- `notification_event_id`
- `notification_channel_count`
- `notification_success_count`
- `notification_failed_count`
- `notification_error`
- `created_at`

Agent 用途：

- 最近 1h/6h/24h/7d 401 统计。
- 风控是否收紧。
- 哪些时间段容易封号。
- 账号恢复率和重复封禁情况。

### 6.4 `remote_account_probe_samples`

短期原始样本。

关键字段：

- `_id = {run_id}:{remote_account_id}`
- `site_id`
- `probe_run_id`
- `identity_id`
- `session_id`
- `normalized_email`
- `remote_account_id`
- `sampled_at`
- `status`
- `schedulable`
- `error_message`
- `group_ids`
- `plan_type`
- `last_used_at`
- `updated_at`
- `codex_5h_used_percent`
- `codex_7d_used_percent`
- `codex_5h_actual_cost`
- `codex_7d_actual_cost`
- `codex_5h_total_cost`
- `codex_7d_total_cost`
- `codex_total_cost`
- `codex_total_actual_cost`
- `codex_5h_request_count`
- `codex_7d_request_count`
- `codex_total_request_count`
- `codex_5h_token_count`
- `codex_7d_token_count`
- `codex_total_token_count`
- `codex_usage_updated_at`
- `codex_usage_synced_at`
- `usage_snapshot`
- `cumulative_usage_snapshot`
- `raw_hash`
- `created_at`
- `expires_at`

Agent 用途：

- 短期回溯和聚合。
- 样本有 TTL，长期 agent 不应依赖它作为唯一数据源。

### 6.5 `remote_account_probe_runs`

每次探测运行摘要。

字段：

- `_id`
- `site_id`
- `started_at`
- `finished_at`
- `status`: `running` / `succeeded` / `failed`
- `duration_ms`
- `accounts_seen`
- `accounts_new`
- `accounts_changed`
- `accounts_401`
- `accounts_missing_suspected`
- `accounts_removed_confirmed`
- `duplicate_email_count`
- `error_message`
- `created_at`

Agent 用途：

- 判断探测是否正常运行。
- 判断当前数据新鲜度。
- 统计一段时间内账号变化量。

### 6.6 `remote_account_probe_meta`

每个 site 的最近探测状态。

字段：

- `_id = site_id`
- `site_id`
- `last_probe_at`
- `last_run_id`
- `status`
- `message`
- `updated_at`

Agent 用途：

- 判断 site 是否探测异常。
- 如果 `last_probe_at` 过旧，agent 应先提示数据不新鲜。

## 7. 异常告警中心

页面：`异常告警`

接口：

- `GET /api/api-pools/observability/alerts?include_read=false&limit=100`
- `GET /api/api-pools/observability/alerts?site_id=api-5001&group_id=3&include_read=true`
- `POST /api/api-pools/observability/alerts/{alert_id}/read`

当前第一类告警：重复邮箱。

重复邮箱告警来源：

- 数据存储在 `remote_account_identities`。
- 条件是 `duplicate_remote_count > 1` 且 `current_presence = present`。
- 告警 id 使用 identity id，即 `{site_id}:{normalized_email}`。

返回结构包含通用告警字段：

- `id`
- `alert_type`: 当前为 `duplicate_email`
- `alert_category`: 当前为 `账号`
- `alert_label`
- `alert_severity`: 当前为 `warning`
- `alert_at`
- `message`
- `site_id`
- `site_name`
- `site_base_url`
- `group_names`
- `is_read`
- `read_at`
- `read_by_name`
- `read_note`
- `alert_signature`
- `read_signature`

以及重复邮箱相关字段：

- `normalized_email`
- `email`
- `current_remote_account_id`
- `current_remote_account_ids`
- `duplicate_remote_count`
- `current_group_ids`
- `current_status`
- `current_error_message`
- `last_seen_at`
- `updated_at`

排序规则：

- 未读优先。
- 同读状态内按 `alert_at` / `last_seen_at` / `updated_at` 倒序。

已读接口：

```http
POST /api/api-pools/observability/alerts/{alert_id}/read
Content-Type: application/json

{"note":"manual read from alert center"}
```

后端写入：

- `duplicate_email_alert_read_at`
- `duplicate_email_alert_read_by`
- `duplicate_email_alert_read_by_name`
- `duplicate_email_alert_read_signature`
- `duplicate_email_alert_read_note`

后续扩展建议：

- 如果告警类型增多，建议新增统一 `observability_alerts` 集合。
- 保留当前返回字段：`alert_type`、`alert_category`、`alert_severity`、`alert_at`、`is_read`。
- 通知机器人只依赖通用字段，不依赖重复邮箱特有字段。

## 8. 容量估计配置

页面：`账号池管理 -> 额度估计`

接口：

- `GET /api/api-pools/capacity-limits`
- `PATCH /api/api-pools/capacity-limits`

集合：`app_settings`

主键：

```text
_id = capacity_account_limits
```

默认值：

```json
{
  "free": {"five_hour_usd": 2, "seven_day_usd": 10},
  "plus": {"five_hour_usd": 28, "seven_day_usd": 140},
  "team": {"five_hour_usd": 15, "seven_day_usd": 75},
  "k12": {"five_hour_usd": 20, "seven_day_usd": 100},
  "pro": {"five_hour_usd": 360, "seven_day_usd": 2100}
}
```

说明：

- sub2api 里的 K12 字段是 `plan_type = k12`。
- 容量预估和账号数折算应优先使用数据库配置，不要写死旧默认值。
- 同邮箱多个 remote id 在容量上按一个账号计算，在用量上按 remote id 加和。

## 9. API 池状态置顶偏好

页面：`API 账号池状态`

接口：

- `GET /api/api-pools/status-preferences`
- `PATCH /api/api-pools/status-preferences`

集合：`app_settings`

主键：

```text
_id = api_pool_status_preferences
```

字段：

- `pinned_site_id`
- `pinned_group_id`
- `updated_at`
- `updated_by_user_id`
- `updated_by_name`

用途：

- 前端默认显示置顶 site。
- 在站点下默认优先显示置顶 group。
- agent 生成状态摘要时也可优先读取置顶对象。

## 10. 问题账号、复活和归档相关接口

这些接口不是观测模块本身，但会影响运营分析。

### 10.1 问题账号信息修正

接口：

- `POST /api/accounts/{account_id}/resolve-problem-info-correction`

用途：

- 操作人修正账号信息后，移除问题状态，账号回到总库。
- 记录操作类型 `account.problem_info_corrected`。
- 本地 metadata 写入 `problem_resolution = info_corrected`。

### 10.2 更新凭证 JSON

接口：

- `POST /api/accounts/{account_id}/refresh-credentials-json`

用途：

- 只更新 access token、refresh token、id token、expires_at、exported_at 等重新授权获得的 credentials 参数。
- 不用于修改上传页面里的业务字段。

### 10.3 账号复活 OAuth 流程

前端流程：

1. 用户在“代办与错误账号处理 -> 账号复活”打开账号。
2. 前端展示邮箱、2FA、手机接码等辅助信息。
3. 获取授权链接。
4. 用户完成授权后粘贴 callback URL。
5. 前端提交 callback，后端交换 OAuth 凭证。
6. 后端把凭证应用到 sub2api 账号。
7. 后端打开调度并重置远端状态。
8. 成功后从待办列表移除。

相关接口：

- `POST /api/sub2api-sites/{site_id}/openai/generate-auth-url`
- `POST /api/sub2api-sites/{site_id}/openai/exchange-code`
- `POST /api/sub2api-sites/{site_id}/accounts/{account_id}/apply-oauth-credentials`

远端 sub2api 目标路径：

- `/api/v1/admin/openai/generate-auth-url`
- `/api/v1/admin/openai/exchange-code`
- `/api/v1/admin/accounts/{account_id}/apply-oauth-credentials`
- `/api/v1/admin/accounts/{account_id}/schedulable`
- `/api/v1/admin/accounts/{account_id}/recover-state`

注意：

- 复活的 `account_id` 是 sub2api remote id，不是本地 Mongo account id。
- 如果 `apply-oauth-credentials` 或 `recover-state` 远端不存在，后端会尝试 fallback 或跳过，并返回提示。
- 成功结果会包含 `apply`、`schedulable`、`recover_state`、`account`。

### 10.4 复活失败处理

接口：

- `POST /api/sub2api-sites/{site_id}/accounts/{account_id}/resurrection-failed`

逻辑：

- 从 sub2api 远端删除该账号。
- 本地根据用户选择进入：
  - `problem_pool`: 错误账号池，等待后续处理。
  - `banned_archive`: 封禁归档。
- 写入 `problem_source = resurrection`、失败原因、远端快照、最后测试信息等 metadata。

Agent 用途：

- `problem_pool` 表示仍可能人工处理。
- `banned_archive` 表示账号封禁或无继续运营价值，应计入损耗分析。

## 11. 本地账号池操作与更新时间规则

近期规则：

- 推送、删除远端、转移池、置顶备选池等本地操作，不刷新账号“编辑更新时间”。
- 只有编辑账号信息、更新 JSON、更新 credentials JSON 等确实修改账号资料的动作，才刷新账号修改人/修改时间。
- 池移动、推送、删除、自动化动作写入“最后操作人”字段，供账号列表按操作时间排序。

相关 metadata：

- `last_operation_name`
- `last_operation_at`
- `last_operation_by_user_id`
- `last_operation_by_name`

相关集合：

- `pool_actions`: 池动作、推送、删除、远端刷新等操作记录。
- `account_operations`: 账号层面的操作记录。
- `audit_logs`: 后台接口审计。

Agent 分析时：

- 判断账号资料是否被改过，看 `metadata.updated_at` 和修改人。
- 判断最近发生了什么运营动作，看 `metadata.last_operation_at` 和 `pool_actions`。

## 12. Agent 推荐读取接口

### 12.1 当前池状态

- `GET /api/sub2api-sites`
- `GET /api/sub2api-sites/{site_id}/cache`
- `GET /api/api-pools/status-preferences`
- `GET /api/api-pools/capacity-limits`

用于回答：

- 当前哪个站点/分组优先关注。
- 当前容量、可用、限流、异常数量。
- 额度估计使用什么参数。

### 12.2 探测配置和新鲜度

- `GET /api/api-pools/observability/groups?site_id={site_id}`
- 直接读 `remote_account_probe_meta`
- 直接读最近 `remote_account_probe_runs`

用于回答：

- 哪些分组开启了详细记录。
- 最近一次探测是否成功。
- 数据是否过期。

### 12.3 异常告警

- `GET /api/api-pools/observability/alerts?include_read=false&limit=300`
- `POST /api/api-pools/observability/alerts/{alert_id}/read`

用于回答：

- 当前未处理异常。
- 是否需要通知。
- 人工确认后停止重复通知。

### 12.4 账号长期分析与事件记录

页面：`事件记录`

前端路由：`/event-records`

正式只读接口：

- `GET /api/event-records/events`
- `GET /api/event-records/accounts`
- `GET /api/event-records/accounts/{identity_id}`
- `GET /api/event-records/summary`

这些接口基于以下集合聚合，后续 agent 优先使用接口，不需要直接扫页面 DOM：

- `remote_account_identities`
- `remote_account_sessions`
- `remote_account_status_events`
- `remote_account_probe_samples`，仅短期回溯。

`/api/event-records/events` 用于事件流，默认按 `detected_at desc` 返回最近 24h。支持筛选：

- `range`: `1h` / `6h` / `24h` / `today` / `7d` / `all`
- `site_id`
- `group_id`
- `event_type`
- `severity`
- `account_type`
- `q`: 邮箱、name、remote id、错误内容模糊搜索
- `only_401`
- `only_abnormal`
- `only_pro`
- `only_cumulative`
- `only_delete_archive`
- `skip`
- `limit`

事件返回重点字段：

- `event_type`
- `severity`
- `detected_at`
- `site_id` / `site_name`
- `identity_id`
- `session_id`
- `remote_account_id`
- `remote_account_ids`
- `name`
- `normalized_email`
- `plan_type`
- `group_ids` / `group_names`
- `current_status`
- `current_schedulable`
- `current_error_message`
- `is_401`
- `usage_snapshot`
- `cumulative_usage_snapshot`
- `usage_duration_seconds`
- `normal_use_seconds`
- `notification_status`
- `uploader_name`
- `last_operation_by_name`
- `details`

`/api/event-records/accounts` 用于账号视图，按 `site_id + normalized_email` 聚合长期身份，支持：

- `site_id`
- `group_id`
- `account_type`
- `presence`
- `q`
- `only_401`
- `only_abnormal`
- `only_pro`
- `only_cumulative`
- `skip`
- `limit`

账号返回重点字段：

- `identity_id`
- `normalized_email`
- `site_name`
- `group_names`
- `current_presence`
- `current_status`
- `current_schedulable`
- `current_error_message`
- `current_remote_account_id`
- `current_remote_account_ids`
- `duplicate_remote_count`
- `first_seen_at`
- `last_seen_at`
- `first_401_at`
- `last_401_at`
- `last_removed_at`
- `total_sessions`
- `total_401_count`
- `total_recovery_count`
- `total_removed_count`
- `last_usage_snapshot`
- `cumulative_usage_snapshot`
- `cumulative_usage_totals`
- `lifetime_seconds`
- `uploader_name`
- `last_operation_name`

`/api/event-records/accounts/{identity_id}` 返回详情：

- `identity`
- `sessions`
- `events`
- `samples`
- `raw`

典型问题：

- 最近 24h 401 数量是否上升。
- 某分组平均存活时间是否下降。
- 哪个上传人、购买来源、账号类型近期异常率高。
- 哪个时间段更容易封号。
- 账号通常用了多少额度后开始异常，注意使用 `cumulative_usage_snapshot`，不要只读清零后的周用量。
- 当前需要提前补多少号。

### 12.5 通知配置

- `GET /api/notification-channels`
- `POST /api/notification-channels/{channel_id}/test`
- 直接读 `notification_events`
- 直接读 `notification_deliveries`

用于回答：

- 可以通过哪些机器人发送提醒。
- 某通知配置最近测试是否成功。
- 最近正式通知是否投递成功。

## 13. 后续 Agent 接口建议

为了减少 agent 直接扫 Mongo 的复杂度，建议后续增加这些只读聚合 API：

### 13.1 风控摘要

```http
GET /api/ops/summary?site_id=api-5001&group_id=3&window=24h
```

建议返回：

- `probe_fresh`
- `last_probe_at`
- `accounts_present`
- `accounts_401`
- `new_401_count`
- `recovered_count`
- `removed_count`
- `duplicate_email_count`
- `risk_level`
- `risk_reasons`
- `recommended_refill_count`

### 13.2 账号寿命分布

```http
GET /api/ops/survival?site_id=api-5001&group_id=3&days=7
```

建议返回：

- 平均存活时间。
- 中位数存活时间。
- P90 存活时间。
- 按账号类型、购买来源、上传人分组。

### 13.3 401 事件列表

```http
GET /api/event-records/events?site_id=api-5001&event_type=401_detected&range=24h
```

建议返回：

- 邮箱。
- remote id。
- 分组。
- 错误类别。
- 发生时间。
- 使用快照。
- 是否已恢复。

### 13.4 告警统一接口

当前告警由重复邮箱逻辑直接从 identity 生成。后续可以抽象成：

```http
GET /api/observability/alerts
POST /api/observability/alerts/{alert_id}/read
POST /api/observability/alerts/{alert_id}/notify
```

建议统一字段：

- `id`
- `alert_type`
- `alert_category`
- `alert_severity`
- `alert_at`
- `site_id`
- `group_id`
- `title`
- `message`
- `dedupe_key`
- `signature`
- `is_read`
- `read_at`
- `read_by_name`
- `notified_at`
- `notification_channel_ids`
- `payload`

## 14. 已知边界和注意事项

- 账号探测是轻量列表探测，不应按探测间隔对所有账号拉重 usage 接口；重 usage 统计继续走低频缓存/聚合逻辑。
- `schedulable` 是调度开关，不是账号健康状态；状态判断看 `status` 和 `error_message`。
- 所有前端时间应显示上海时间；后端请求 sub2api 时已尽量带 `timezone=Asia/Shanghai`。
- 同邮箱重复 remote id 是数据异常，但容量估计要容错：账号数按 1 个，用量按全部 remote id 加和。
- `name` 不可作为唯一身份；命名、同步、恢复、重复检测都必须按 credentials/extra/account 中的 email 归一。
- 已归档账号在账号列表中通过 `account_scope=archived` 查看，本地状态为 `metadata.pool_status=discarded`。
- 通知系统已经有通用正式发送入口，当前第一类正式事件是 `sub2api.account.401_detected`；更细的通知规则和按事件选择机器人仍属于后续扩展。
- 长期运营统计表如 `account_ops_hourly_stats`、`account_ops_daily_stats` 仍属于后续阶段。
