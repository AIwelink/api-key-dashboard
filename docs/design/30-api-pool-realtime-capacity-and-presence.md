# API 账号池实时容量、并发覆盖与前台在线实现说明

本文记录当前已经落地、对后续开发有约束作用的接口、字段、计算公式和前端展示规则。历史方案中的备用池容量、旧峰值告警和旧并发倍数不作为本文实现依据。

## 1. 代码入口

后端：

- `backend/app/modules/sub2api/cache.py`：sub2api 缓存、分组容量汇总和账号列表。
- `backend/app/modules/sub2api/capacity_risk.py`：分钟压力、可用时间、并发覆盖、补号判断。
- `backend/app/modules/sub2api/capacity_notifications.py`：容量阈值告警、恢复通知和容量健康状态变化飞书通知。
- `backend/app/modules/sub2api/tpm_sampler.py`：每分钟 TPM/RPM/当前并发采样。
- `backend/app/modules/sub2api/capacity_sampler.py`：每 5 分钟保存分组容量摘要，供后续回测。
- `backend/app/routers/sub2api_sites.py`：站点、分组和账号缓存接口。
- `backend/app/modules/system/presence.py`：前台在线心跳、当前在线和历史聚合。
- `backend/app/routers/presence.py`：前台在线接口。

前端：

- `frontend/src/pages/ApiPoolStatusPage.tsx`：API 账号池状态页、容量字段和悬浮说明。
- `frontend/src/utils/capacityScale.ts`：实时可用时间和安全并发覆盖的独立进度比例、颜色分级。
- `frontend/styles.css`：容量条样式和紫色顶级动画。
- `frontend/src/hooks/useForegroundPresence.ts`：浏览器前台心跳。
- `frontend/src/pages/PresencePage.tsx`：owner 前台在线页面。

## 2. API 账号列表与容量响应

### 2.1 分组账号列表

```http
GET /api/sub2api-sites/{site_id}/groups/{group_id}/accounts
Authorization: Bearer <token>
```

查询参数：

| 参数 | 默认值 | 限制 | 说明 |
| --- | --- | --- | --- |
| `page` | `1` | `>= 1` | 页码 |
| `page_size` | `50` | `1..500` | 每页数量 |
| `status` | 空 | 可选 | 远端账号状态过滤 |

权限：`owner`、`admin`、`maintainer`。

响应结构：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 50,
  "pages": 1,
  "cache_meta": {},
  "capacity_summary": {}
}
```

账号分页在 MongoDB 查询阶段排序，不允许只在前端对当前页排序：

```text
created_at DESC, sub2api_account_id DESC
```

`created_at` 使用新版 `sub2api_accounts_cache` 顶层字段。缺少创建时间的旧缓存记录排在有创建时间的记录之后；远端账号 ID 用作同一创建时间下的稳定排序键。

### 2.2 容量字段

`capacity_summary` 由后端使用当前分组完整账号集合计算，不能由前端当前分页重新计算。实时容量相关字段：

容量资格先统一经过 `_is_capacity_account()`：`schedulable=false` 是硬排除条件，无论账号当前是 active、5h 429 还是 7d 429，都不能进入 5h/7d 美元总容量、动态容量或并发总容量。账号池概览的“正常账号”是状态统计口径，不等同于容量资格，调度关闭账号仍可按既有规则出现在概览计数中。

| 字段 | 含义 |
| --- | --- |
| `realtime_risk_ready` | 分钟样本是否满足实时判断条件 |
| `sample_count` | 可用 TPM/RPM 分钟样本数 |
| `concurrency_sample_count` | 含当前并发值的样本数 |
| `pressure_tpm` | 用于额度消耗预测的压力 TPM |
| `pressure_rpm` | 最近短周期 RPM |
| `estimated_concurrency` | 压力并发 |
| `concurrency_safe_available` | 当前安全可用并发余量，不包含已占用并发 |
| `concurrency_coverage` | 对外展示的总安全并发覆盖倍数 |
| `concurrency_target_coverage` | 对外总覆盖目标，当前为 `2.2x` |
| `actual_runway_hours` | 只按当前实际剩余额度计算的可用时间 |
| `dynamic_runway_hours` | 包含短期额度恢复后的动态可用时间 |
| `target_runway_hours` | 动态可用时间目标，当前为 `3h` |
| `current_consumption_rate_usd_per_hour` | 当前分组的自然小时美元消耗速度；无可靠小时桶时为 `null` |
| `current_consumption_rate_source` | `previous_full_hour`、`current_hour_prorated` 或 `unavailable` |
| `current_consumption_rate_elapsed_minutes` | 当前自然小时已过分钟，用于核对 5 分钟切换边界 |
| `current_consumption_rate_hour` | 实际采用的小时桶（UTC ISO 时间）；无可靠数据时为 `null` |
| `pressure_stage` | `waiting_data`、`stable`、`transmission`、`accelerating`、`peak_guard`、`recovering`、`inventory_risk` |
| `replenishment_required` | 是否建议补号 |
| `recommended_refill_options` | 按当前池可补账号类型拆分的建议数量 |

同一份 `capacity_summary` 也存储在 `sub2api_groups_cache.capacity_summary`，分组列表接口会随 group 返回该字段。

动态可用只计入短期恢复：5h 额度恢复等待时间不超过 `1h`，7d 额度恢复等待时间不超过 `1d`。恰好位于边界时仍计入，超过边界后只保留当前实际可用额度，不再提前计算未来恢复额度。

## 3. 实时数据就绪条件

`calculate_capacity_risk()` 只有同时满足以下条件才设置 `ready=true`：

- 至少 15 个分钟样本。
- 最近 15 个样本覆盖时间不超过 20 分钟。
- 最近样本距离当前不超过 3 分钟。
- 最近样本中至少 5 个含 `current_concurrency`。
- `cost_per_token` 是正数。

条件不满足时：

- `realtime_risk_ready=false`。
- 前端实时可用时间和安全并发覆盖显示等待数据/灰色。
- 不得用旧日峰值直接替代实时并发覆盖并触发危险告警。

## 4. 压力和可用时间公式

压力 TPM：

```text
上涨或稳定：
pressure_tpm = max(EMA15, 最近2小时P90, EMA5 * trend_multiplier)

确认回落：
pressure_tpm = max(EMA5, EMA15)
```

其中 `trend_multiplier` 限制在 `1.0..1.5`。

压力并发：

```text
estimated_concurrency = max(当前并发EMA5, 最近1小时当前并发P90)
```

当压力并发处于 `(0, 1)` 时按 `1` 计算；等于 `0` 时并发覆盖返回 `null`。

额度消耗和可用时间：

```text
burn_usd_per_hour = pressure_tpm * 60 * cost_per_token

actual_remaining_usd = min(5h实际剩余, 7d实际剩余)
dynamic_remaining_usd = min(5h动态剩余, 7d动态剩余)

actual_runway_hours = actual_remaining_usd / burn_usd_per_hour
dynamic_runway_hours = dynamic_remaining_usd / burn_usd_per_hour
```

### 4.1 当前消耗速度

“当前消耗速度”只读取当前 sub2api 站点、当前分组的 PostgreSQL 小时趋势，基础值为 `account_cost`，不能汇总其他分组或客户端站点：

```text
Asia/Shanghai 整点后 elapsed < 5 分钟：
rate = 紧邻上一完整自然小时的 account_cost

elapsed >= 5 分钟：
rate = 本小时累计 account_cost / elapsed * 60
```

小时桶必须和目标自然小时精确相等。前 5 分钟缺少紧邻上一小时，或 5 分钟后缺少当前小时，都返回 `source=unavailable`；不能使用更早小时回退。负数、非数值和无法解析的桶同样不可用。

前端只格式化后端结果为 `$ / 小时` 并显示“上一完整小时”“本小时折算”或“等待小时消耗数据”，不得自行按浏览器时间重算。该指标当前只用于人工观察，不参与健康等级、通知阈值或补号公式。

### 4.2 压力阶段

前端“压力阶段”标题必须通过 `MetricHelp` 提供 hover/focus 详情。阶段由 TPM/RPM 的 EMA5、EMA15、EMA60、短期动量、需求倍率、回落判断、实时健康状态和动态可用时间共同决定：

| 阶段 | 判定摘要 |
| --- | --- |
| 等待数据 | 分钟样本、并发样本、新鲜度或单位 Token 成本未满足实时计算条件 |
| 稳定 | 未确认回落，需求倍率低于 `1.2` |
| 压力传导 | 需求倍率达到 `1.2`，但未达到加速上涨条件 |
| 加速上涨 | 需求倍率达到 `1.5`，或 TPM 短期动量达到 `1.2` |
| 峰值保底 | 实时健康状态已经是危险或耗尽 |
| 回落观察 | 流量已确认下降，且未进入库存风险 |
| 库存风险 | 流量下降且动态可用时间超过 `6h` |

阶段用于解释运营态势，不替代 `health_status`。等待数据不得触发容量预警或恢复通知；峰值保底优先于上涨阶段。

### 4.3 一小时硬告警线

分组容量通知启用后，只要 `realtime_risk_ready=true`，并且以下任一值低于 `1h`，必须发送危险告警：

```text
actual_runway_hours < 1
dynamic_runway_hours < 1
```

这条硬告警线优先于分组配置的 `capacity_notification_threshold`，即使阈值选择“仅耗尽”也不能屏蔽。通知总开关、冷却时间、危险状态重复提醒和恢复通知规则仍然有效。`realtime_risk_ready=false` 时不得根据残留 runway 字段报警。

### 4.4 容量健康状态变化飞书通知

状态变化通知受分组现有 `capacity_notification_enabled` 开关控制。有效状态只有：

```text
very_abundant, abundant, healthy, tight, danger, exhausted
```

`pending` 表示等待数据，不是有效状态，不得覆盖 `sub2api_capacity_notification_meta.last_observed_status`。首次观察到有效状态只建立基线，不发送专用状态变化消息；启用通知后，任意两个有效状态之间的变化都发送，包括改善和恶化。

去重和路由规则：

- 本轮状态变化如果同时触发原有 `sub2api.capacity.low` 或 `sub2api.capacity.recovered`，复用该事件，不再发送第二条消息。
- 原状态机本轮不发送时，创建 `sub2api.capacity.status_changed`，只投递到所有 `status=active` 且 `channel_type=feishu` 的通知渠道。
- 专用状态变化事件不写 `last_attempt_at`、`last_notification_type` 或 `last_notified_status`，因此不改变危险告警冷却和恢复状态机。
- 没有飞书渠道、投递失败或投递跳过时，仍前移 `last_observed_status`，避免同一次变化每分钟重复发送。
- 分组通知关闭时继续维护当前有效观察状态，但不发送；重新启用后不补发关闭期间的历史变化。

专用事件 payload 至少包含：

```text
site_id
group_id
group_name
previous_health_status
health_status
notification_type = status_change
capacity_summary
```

当前状态对应严重度：`exhausted=critical`、`danger=danger`、`tight=warning`、`healthy=success`、`abundant/very_abundant=info`。

## 5. 并发覆盖：总覆盖与内部余量必须分开

`concurrency_safe_available` 是尚未使用的安全并发余量。对外展示的“安全并发覆盖”必须包含已经承接的压力并发：

```text
concurrency_spare_coverage = concurrency_safe_available / estimated_concurrency
concurrency_coverage = 1 + concurrency_spare_coverage
```

因此：

- 有压力并发数据时，`concurrency_coverage` 最低为 `1x`。
- `1x` 表示当前压力已占满安全容量，没有安全余量。
- 压力并发为 `0` 时返回 `null`，不能制造超大倍数。

重要兼容规则：

- API 返回 `concurrency_coverage`，它是**总覆盖**。
- 后端健康判断和补号计算继续使用内部 `concurrency_spare_coverage`，它是**余量覆盖**。
- 内部余量目标仍为 `1.2x`；对应对外总覆盖目标为 `2.2x`。
- 不要把 API 返回值再次加 `1`，也不要直接用 API 返回值替换补号公式中的余量覆盖。

补号并发缺口仍按余量计算：

```text
gap = max(0, estimated_concurrency * 1.2 - concurrency_safe_available)
```

## 6. 前端独立分级

### 6.1 实时可用时间

颜色：

```text
< 1h       红色 danger
1h - 3h   黄色 warning
3h - 24h  绿色 success
24h - 48h 蓝色 info
>= 48h    紫色 excellent
```

进度条满刻度为 `48h`。比例分段由 `runwayScalePercent()` 计算，不使用峰值倍数函数。

### 6.2 安全并发覆盖

并发覆盖与历史峰值容量不是同一种指标，必须使用独立分级：

```text
< 1.5x        红色 danger
1.5x - 3x    黄色 warning
3x - 5x      绿色 success
5x - 10x     蓝色 info
>= 10x       紫色 excellent
```

进度条满刻度为 `10x`，分段点为：

```text
0, 1, 1.5, 3, 5, 7.5, 10
```

不要调用峰值容量使用的 `multipleScalePercent()` 或 `multipleScaleTone()`。

### 6.3 悬浮说明

“实时可用时间”和“安全并发覆盖”的标题通过 `MetricHelp` 展示悬浮说明，内容位于 `METRIC_HELP_DETAILS`。提示必须包括：

- 用途。
- 计算公式。
- 颜色阈值和等待数据语义。

悬浮说明同时支持鼠标 hover 和键盘 focus。调整公式或阈值时，需要同步修改帮助文本和 `ApiPoolStatusHelp.test.ts`。

### 6.4 紫色顶级进度动画

`excellent` 进度条使用纯紫底色和单个 `::after` 高光层：

- 动画周期 `3s`。
- 单个椭圆高光从左向右移动。
- 只动画 `transform: translate3d(...)`，不动画整条背景位置。
- 禁止恢复 `repeating-linear-gradient`、多层波纹或外发光循环。
- `prefers-reduced-motion: reduce` 时关闭高光动画。

## 7. 前台在线接口

### 7.1 心跳

```http
POST /api/presence/heartbeat
Authorization: Bearer <user-token>
Content-Type: application/json
```

```json
{
  "client_id": "persistent-browser-id",
  "session_id": "tab-session-id",
  "client_label": "Windows · Chrome",
  "device_type": "desktop",
  "view": "api-pools",
  "path": "/api-pool-status",
  "foreground_since_at": "2026-07-18T12:00:00Z"
}
```

规则：

- 只接受登录用户，API Token actor 返回 403。
- 前端仅在 `document.visibilityState === "visible"` 且窗口有焦点时上报。
- 心跳周期为 15 秒。
- `client_id` 存在 `localStorage`，同一浏览器复用。
- `session_id` 存在 `sessionStorage`，每个标签页独立。
- 当前在线文档 ID 由 `user_id + client_id + session_id` 计算，多个标签页不会互相覆盖。

### 7.2 标签页离开

```http
POST /api/presence/leave
Authorization: Bearer <user-token>
Content-Type: application/json
```

```json
{
  "client_id": "persistent-browser-id",
  "session_id": "tab-session-id"
}
```

只删除当前用户、当前浏览器、当前标签页对应的 presence 文档。

### 7.3 当前在线

```http
GET /api/presence
```

- 权限：仅 `owner`。
- `last_seen_at` 在最近 60 秒内视为在线。
- 最多返回 500 个在线 session，按 `last_seen_at DESC`。

### 7.4 在线历史

```http
GET /api/presence/history
```

- 权限：仅 `owner`。
- 聚合粒度：5 分钟。
- 展示窗口：最多 30 天，且不早于 `2026-07-18 00:00 Asia/Shanghai`。
- 历史 TTL：35 天；当前 session TTL：24 小时。
- 同一用户的多个标签页按客户端聚合，`active_clients` 是去重后的客户端数量，`session_count` 显示该客户端活动标签页数量。

主要响应字段：

```text
items[].is_online
items[].active_clients
items[].active_client_details[]
items[].last_seen_at
items[].online_minutes
items[].online_ratio_percent
items[].common_periods
items[].daily_timeline[]
online_users
bucket_minutes
start_at / end_at / timezone
```

## 8. MongoDB 集合与索引

容量和账号缓存：

```text
sub2api_groups_cache
sub2api_accounts_cache
sub2api_tpm_samples
sub2api_capacity_samples
sub2api_capacity_notification_meta
```

`sub2api_capacity_samples` 每 5 分钟按站点和分组保存一次 schema version 2 文档。`metrics` 会自动保留容量摘要中的标量字段，因此当前消耗速度的四个字段直接进入新样本；这是加法兼容变更，不提升 schema version，也不回填历史样本。

`sub2api_capacity_notification_meta` 使用 `{site_id}:{group_id}` 作为文档 ID。状态变化审计字段为：

```text
last_observed_status
last_observed_at
last_state_change_at
last_state_change_from
last_state_change_to
last_state_change_event_id
last_state_change_delivery_status
```

状态变化审计字段与原有 `last_attempt_at`、`last_notified_status`、`active_alert` 分离。原告警或恢复事件复用时，`last_state_change_event_id` 指向被复用的事件。

前台在线：

```text
frontend_presence
frontend_presence_minutes
```

`frontend_presence` 唯一身份是 `user_id + client_id + session_id`。修改 presence 身份模型时必须迁移或替换旧唯一索引，不能同时保留只含 `client_id` 的旧唯一索引。

## 9. 测试与修改检查表

相关测试：

```text
backend/tests/test_capacity_risk.py
backend/tests/test_capacity_risk_integration.py
backend/tests/test_capacity_sampler.py
backend/tests/test_concurrency_capacity.py
backend/tests/test_capacity_notifications.py
backend/tests/test_frontend_presence.py
backend/tests/test_sub2api_account_list.py
frontend/src/utils/capacityScale.test.ts
frontend/src/pages/ApiPoolStatusHelp.test.ts
frontend/src/components/AnimatedValue.test.ts
frontend/src/hooks/useForegroundPresence.test.ts
frontend/src/pages/presenceTimeline.test.ts
```

建议验证命令：

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

cd ..\frontend
npm.cmd test
npm.cmd run build
```

修改容量逻辑时至少检查：

1. 分组容量仍使用完整缓存账号，不受当前分页影响。
2. 实时样本未就绪时不产生假危险状态。
3. `concurrency_coverage` 总覆盖最低为 `1x`，压力并发为 0 时为 `null`。
4. 补号计算继续使用安全余量，而不是对外总覆盖。
5. 并发覆盖不复用历史峰值倍数的前端比例和颜色函数。
6. 修改颜色阈值时同步更新悬浮说明和测试。
7. 账号列表排序必须在数据库分页前执行。
8. presence leave 必须携带 `session_id`，不能关闭同一浏览器的其他标签页。
9. `schedulable=false` 的账号不能进入美元容量或并发容量，即使账号仍带有 5h/7d 429 状态。
10. 当前消耗速度在整点后前 5 分钟只使用紧邻上一小时，5 分钟后只使用当前小时，且始终保持站点/分组隔离。
11. `pending` 不得覆盖容量状态基线；同一轮阈值告警或恢复已经发送时，不得再发送专用状态变化事件。
12. 专用状态变化事件只能路由到启用的飞书渠道，且不得更新原告警冷却字段。
