# 账号池生命周期与后端逻辑设计

> **文档状态：历史目标模型，已归档。** 本文保留早期生命周期、membership、计划和待办的完整推演，不代表当前页面、集合或自动补号行为。已落地的简化生命周期可参考 [19-account-pool-final-simple-design.md](./19-account-pool-final-simple-design.md)，当前 Agent/通知边界见 [29-agent-ops-observability-and-notifications.md](./29-agent-ops-observability-and-notifications.md)，当前容量与并发计算见 [30-api-pool-realtime-capacity-and-presence.md](./30-api-pool-realtime-capacity-and-presence.md)。旧的备用池容量、固定验证站点和固定模型不得直接用于新开发。

本文档描述后续账号池后端逻辑的目标模型。它建立在现有 `accounts.account_json + metadata`、sub2api MongoDB 缓存和 API 账号池状态页面之上，用于继续设计总文件库、账号总库、验证、备用池、实际使用池、问题退回、容量计划、待办和 agent 预留字段。

## 背景目标

系统最终目标不是只保存账号 JSON，而是支持团队协作制作账号、上传账号、沉淀账号总库，并根据使用池状态自动判断什么时候需要补充账号、更新旧账号或制作新账号。

核心目标：

- 团队成员协作上传和维护账号，所有上传内容都能追溯来源批次。
- 系统保留账号总库，所有账号先进入本地数据库，而不是直接进入 sub2api。
- 系统根据验证结果、风险信息、容量、并发、5h/7d 用量等指标，动态推荐账号进入备用池或实际使用池。
- 实际使用账号出错后，从使用池退回本地，保留尽可能完整的使用快照和错误上下文。
- 当实际使用池容量不足、备用池不足或问题账号变多时，生成整体待办和容量计划。
- 后续 agent 基于结构化字段辅助判断：更新旧账号、制作新账号、弃用问题账号，还是重新验证并放回备用池。我们会输入更多外界信息，然后agent根据库中池中账号情况，做出决策性综合分析。或者指导开发新的逻辑，防止agent停止工作时进行逻辑判断

## 当前基础

现有基础能力：

- `accounts` 集合已经保存一个账号一个文档，核心结构为 `account_json + metadata`。
- `account_json` 必须保持 sub2api 原始账号结构，导出和推送时不改字段结构。
- 上传页已经支持单账号和批量 JSON 粘贴。
- API 账号池状态页面已经读取 sub2api groups/accounts，并写入 MongoDB 缓存集合。
- sub2api 缓存集合只代表远端观测状态，不替代本地账号管理状态。

当前缺失能力：

- 没有上传批次记录，无法追溯一次上传产生了哪些账号和错误。
- 没有明确账号生命周期状态，无法稳定区分总库、待验证、备用池、使用池和问题池。
- 没有本地备用池模型，sub2api group 与本地备用池概念还没有分开。
- 没有容量计划和补位计划，5h/7d 用量目前只在前端展示。
- 没有整体待办，问题账号和账号缺口不能沉淀成后续协作任务。

## 核心概念

### 总文件库 / import_batches

一次上传动作形成一个批次。批次不是账号本身，而是账号来源档案。

批次用于回答：

- 谁在什么时候上传了这批账号。
- 原始输入是什么来源：粘贴、文件、手工录入。
- 解析出了多少账号。
- 新增、更新、冲突、错误分别是多少。
- 这批账号系统自动识别为什么类型。
- 后续是否需要复查、抽样验证或 agent 判断。

系统需要自动聚合账号类型、支付类型、手机号绑定情况、批次状态等信息。人工只做辅助标记，不要求每个批次都手填大量字段。

### 账号总库 / accounts

`accounts` 仍保持现有结构：

```js
{
  account_json: {},
  metadata: {}
}
```

所有账号先进入总库。新账号默认留在总库，不自动验证，不自动进入备用池，不自动写入 sub2api。

老账号、已知错误账号、需要复查账号可以进入验证流程。验证通过后可以进入备用池。

### 备用池

备用池是本地逻辑，不等同于 sub2api group。

备用池保存已经通过验证或人工确认、可在需要时补入实际使用池的账号。它的作用是让系统在实际使用池容量不足时，有一组候选账号可以按规则评分后补位。

老账号验证通过后进入备用池，且使用优先级较高。新账号后续可以通过人工操作、批次判断或 agent 规则从总库进入备用候选。

### 实际使用池

实际使用池对应 sub2api 正在使用的 group，例如 plus/free 账号池。

本地必须记录实际使用池关系，不能完全以 sub2api 当前返回为准。原因：

- 推送账号到 sub2api 可能失败。
- 本地计划可能已经生成，但远端还没有完成同步。
- sub2api 里可能原本就有账号。
- sub2api 可能被人手动修改。
- 远端缓存有刷新延迟。

因此本地需要记录账号进入实际使用池的计划、执行结果、远端账号 ID 和失败原因。

### 问题池 / returned

实际使用账号出现错误后，从使用池退回本地，进入问题状态。

退回时尽可能记录：

- 使用金额或用量相关字段。
- 5h/7d 用量窗口。
- 错误信息。
- 问题出现时间。
- sub2api 远端状态。
- 最后请求时间。
- 最近检查时间。
- 剩余容量或当前容量。
- 远端 group。
- 当前账号 JSON hash。

后续人工或 agent 根据这些信息判断：弃用、更新账号 JSON 后重新验证，还是重新进入备用池。

## 数据模型

### import_batches

`import_batches` 保存每次上传/制作批次。

```js
{
  _id,
  batch_name,
  batch_type,
  uploaded_by_user_id,
  uploader_name,
  created_at,
  source,
  raw_sha256,
  raw_size,
  parsed_count,
  created_count,
  updated_count,
  blocked_conflict_count,
  invalid_count,
  detected_account_types,
  detected_payment_types,
  detected_phone_bound_summary,
  status,
  manual_label,
  remark,
  agent_notes
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `batch_name` | 批次名称，可由前端生成默认名称，也可人工填写 |
| `batch_type` | `new` / `renew` / `purchase` / `historical` / `known_error` |
| `uploaded_by_user_id` | 当前上传用户 ID，系统根据当前账号生成，也可以指定编辑 |
| `uploader_name` | 上传人显示名，系统生成 |
| `source` | `paste` / `file` / `manual` |
| `raw_sha256` | 原始输入内容 hash，用于追溯和去重 |
| `raw_size` | 原始输入大小 |
| `parsed_count` | 成功解析出的账号数量 |
| `created_count` | 新建账号数量 |
| `updated_count` | 更新版本数量 |
| `blocked_conflict_count` | 因远端正在使用而阻止更新的数量 |
| `invalid_count` | 无效账号数量 |
| `detected_account_types` | 系统聚合识别出的账号类型 |
| `detected_payment_types` | 系统聚合识别出的支付类型 |
| `detected_phone_bound_summary` | 绑定手机情况统计 |
| `status` | `parsed` / `committed` / `has_errors` / `needs_review` |
| `manual_label` | 人工批次标记 |
| `remark` | 批次备注 |
| `agent_notes` | 预留给 agent 的结构化判断备注 |

### accounts.metadata 新增字段

账号仍在 `accounts` 集合中保存，新增字段进入 `metadata`：

```js
{
  batch_id,
  upload_intent,
  lifecycle_status,
  reserve_priority,
  risk_score,
  risk_flags,
  verification_status,
  verification_last_at,
  verification_error,
  sub2api_site_id,
  sub2api_account_id,
  sub2api_group_ids,
  last_pool_event_at,
  agent_decision_hint
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `batch_id` | 来源批次 ID |
| `upload_intent` | `new` / `renew` / `purchase` / `historical` / `known_error` |
| `lifecycle_status` | 当前账号生命周期状态 |
| `reserve_priority` | 备用池优先级，数字越高越优先 |
| `risk_score` | 风险分，供系统和 agent 使用 |
| `risk_flags` | 风险标签数组，例如 `remote_active_conflict`、`verification_failed` |
| `verification_status` | `not_required` / `pending` / `running` / `succeeded` / `failed` / `skipped` |
| `verification_last_at` | 最近验证时间 |
| `verification_error` | 最近验证错误 |
| `sub2api_site_id` | 远端站点 ID |
| `sub2api_account_id` | 远端账号 ID |
| `sub2api_group_ids` | 远端 group ID 列表 |
| `last_pool_event_at` | 最近一次池生命周期事件时间 |
| `agent_decision_hint` | agent 预留判断提示 |

`lifecycle_status` 初始枚举：

```text
library
needs_verification
verifying
reserve_ready
active_pending
active
returned
discarded
blocked_conflict
```

语义：

| 状态 | 说明 |
| --- | --- |
| `library` | 总库账号，默认状态 |
| `needs_verification` | 需要验证 |
| `verifying` | 正在验证 |
| `reserve_ready` | 可进入备用池候选 |
| `active_pending` | 计划推送到实际使用池，但尚未确认成功 |
| `active` | 本地确认已在实际使用池 |
| `returned` | 从实际使用池退回的问题账号 |
| `discarded` | 人工弃用 |
| `blocked_conflict` | 发现 sub2api 正在使用同账号，阻止更新 |

### account_versions

`account_versions` 记录重复账号更新历史。

```js
{
  _id,
  account_id,
  batch_id,
  old_sha256,
  new_sha256,
  old_account_json,
  new_account_json,
  changed_fields,
  created_by_user_id,
  created_by_name,
  created_at,
  reason
}
```

原则：

- 重复账号不创建重复主账号，优先更新版本。
- 版本表保存必要快照，符合当前明文存储策略。
- 审计日志不复制完整 token，只记录摘要。
- 如果远端正在实际使用同账号，阻止更新，不写入新版本。

### account_events

`account_events` 记录账号生命周期事件。

```js
{
  _id,
  account_id,
  batch_id,
  pool_id,
  event_type,
  actor_type,
  actor_id,
  status,
  message,
  before,
  after,
  remote_snapshot,
  local_snapshot,
  created_at
}
```

事件类型：

```text
imported
updated_version
remote_active_conflict
verification_started
verification_succeeded
verification_failed
reserve_entered
active_push_planned
active_push_failed
returned
discarded
```

`remote_snapshot` 用于保存 sub2api 远端快照。`local_snapshot` 用于保存本地关键字段快照。两者不建议进入普通审计日志，但可以进入本系统生命周期事件表。

### api_pools

`api_pools` 保存本地池配置。本地池映射到 sub2api site 和 group。

```js
{
  _id,
  name,
  account_type,
  site_id,
  active_group_id,
  verification_group_id,
  min_active,
  target_active,
  max_avg_5h_used,
  max_avg_7d_used,
  min_reserve,
  status,
  created_at,
  updated_at
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `name` | 本地池名称，例如 `plus 主池` |
| `account_type` | `plus` / `free` / `pro` / `other` |
| `site_id` | sub2api 站点 ID |
| `active_group_id` | 实际使用的 sub2api group |
| `verification_group_id` | 专用验证 group |
| `min_active` | 最小健康可调度账号数 |
| `target_active` | 补位目标账号数 |
| `max_avg_5h_used` | 5h 平均用量阈值 |
| `max_avg_7d_used` | 7d 平均用量阈值 |
| `min_reserve` | 最小备用池账号数 |
| `status` | `active` / `disabled` |

默认均衡阈值：

```js
{
  min_active: 20,
  target_active: 30,
  max_avg_5h_used: 70,
  max_avg_7d_used: 80,
  min_reserve: 10
}
```

### pool_memberships

`pool_memberships` 保存账号和本地池的关系。

```js
{
  _id,
  account_id,
  pool_id,
  role,
  status,
  priority,
  entered_at,
  exited_at,
  reason,
  created_at,
  updated_at
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `role` | `reserve` / `active` / `verification` |
| `status` | `ready` / `pending` / `running` / `succeeded` / `failed` / `exited` |
| `priority` | 池内优先级 |
| `entered_at` | 进入池时间 |
| `exited_at` | 退出池时间 |
| `reason` | 进入或退出原因 |

### pool_plans / pool_plan_items

`pool_plans` 保存系统计算出的补位计划。

```js
{
  _id,
  pool_id,
  plan_type,
  status,
  summary,
  metrics_snapshot,
  created_by,
  created_at,
  executed_at,
  finished_at
}
```

`pool_plan_items` 保存计划明细。

```js
{
  _id,
  plan_id,
  pool_id,
  account_id,
  item_type,
  status,
  reason,
  score,
  score_detail,
  remote_action,
  created_at,
  updated_at
}
```

计划项类型：

```text
verify_account
enter_reserve
push_to_active
return_problem_account
create_todo
```

第一版先生成计划，可视化和人工确认后续开发。验证动作例外：老账号验证可以真实写入测试环境 sub2api。

### todo_items

`todo_items` 第一版生成整体待办，不分配具体处理人。

```js
{
  _id,
  title,
  todo_type,
  status,
  pool_id,
  plan_id,
  severity,
  summary,
  suggested_action,
  created_at,
  resolved_at
}
```

用途：

- 哪个池缺多少账号。
- 为什么缺账号。
- 问题账号多少。
- 建议制作新账号还是更新旧账号。
- 是否需要人工复查某个批次。

第一版不把待办分配到具体处理人，只生成整体待办。后续可根据上传人、制作人、账号类型或 agent 判断再分配。

## 关键流程

### 上传新账号批次

流程：

1. 前端提交 JSON 或粘贴内容。
2. 后端解析账号对象。
3. 创建 `import_batches`。
4. 系统自动聚合批次信息。
5. 每个有效账号进入 `accounts`。
6. 新账号设置：

```js
metadata.upload_intent = "new"
metadata.lifecycle_status = "library"
metadata.verification_status = "not_required"
```

规则：

- 不自动写入 sub2api。
- 不自动抽样验证。
- 不自动进入备用池。
- 后续抽样验证单独设计。

### 上传旧账号 / 错误账号

适用类型：

```text
renew
purchase
historical
known_error
```

流程：

1. 创建批次。
2. 账号进入 `accounts`。
3. 根据上传意图设置状态。
4. 可以手动或计划触发验证。
5. 验证通过后进入备用池。

建议初始状态：

```js
metadata.lifecycle_status = "needs_verification"
metadata.verification_status = "pending"
```

如果只是历史归档但暂不验证，可保留：

```js
metadata.lifecycle_status = "library"
```

### 重复账号更新

匹配优先级：

1. `account_json.credentials.chatgpt_account_id`
2. `account_json.credentials.email`
3. `account_json.extra.email`
4. `account_json.name`
5. `metadata.sha256`

流程：

1. 上传时先尝试匹配本地已有账号。
2. 如果没有匹配，创建新账号。
3. 如果匹配到本地账号，先检查 sub2api 缓存。
4. 如果 sub2api 缓存中同账号正在实际使用池 active/schedulable，阻止更新。
5. 阻止时写：

```text
account_events.remote_active_conflict
import_batches.blocked_conflict_count += 1
metadata.lifecycle_status = blocked_conflict
```

6. 如果没有远端 active 冲突，则写 `account_versions` 并更新账号 JSON。

远端 active 冲突判断：

- 账号能通过 email、name、chatgpt_account_id 或远端账号 ID 匹配到 sub2api 缓存。
- 远端账号位于某个 `api_pools.active_group_id`。
- 远端状态满足：

```text
status == active
schedulable == true
```

满足以上条件时，阻止覆盖，避免误改正在使用账号。

### 验证流程

验证主要用于老账号、已知错误账号、需要复查账号。新账号第一版不抽样验证。

流程：

1. 账号进入 `verifying`。
2. 创建 `pool_memberships`，role 为 `verification`。
3. 将账号临时写入专用 `verification_group_id`。
4. 通过 sub2api 使用 `gpt-5.4-mini` 发送 `hi`。
5. 记录响应、错误、模型、耗时、远端账号 ID。
6. 刷新 sub2api 缓存或读取远端账号详情。
7. 记录 5h/7d 用量窗口和远端状态。
8. 测完清理远端验证账号。
9. 写入 `account_events`。

验证成功条件：

- sub2api 调用 `gpt-5.4-mini` 返回正常响应。
- 远端账号没有明显错误状态。
- 清理远端验证账号成功，或清理失败被记录为 warning。

验证成功后：

```js
metadata.lifecycle_status = "reserve_ready"
metadata.verification_status = "succeeded"
metadata.reserve_priority = 高优先级
```

同时创建或更新备用池 membership：

```js
{
  role: "reserve",
  status: "ready",
  priority: 高优先级
}
```

验证失败后：

```js
metadata.lifecycle_status = "returned"
metadata.verification_status = "failed"
metadata.verification_error = 错误摘要
```

并生成整体待办。

### 容量计划

容量计划读取 MongoDB 中的 sub2api 缓存，不直接请求远端。

健康可调度账号判断：

```text
status == active
schedulable == true
无 error_message
无 rate_limited_at
无 temp_unschedulable_until
```

计算指标：

- `active_healthy_count`
- `active_total_count`
- `avg_5h_used`
- `avg_7d_used`
- `reserve_ready_count`
- `problem_account_count`
- `active_gap = target_active - active_healthy_count`
- `reserve_gap = min_reserve - reserve_ready_count`

触发条件：

- `active_healthy_count < min_active`
- `avg_5h_used >= max_avg_5h_used`
- `avg_7d_used >= max_avg_7d_used`
- `reserve_ready_count < min_reserve`
- 问题账号数量超过后续配置阈值

触发后生成 `pool_plans` 和 `pool_plan_items`。

### 备用账号评分

第一版使用规则评分，不交给 agent 做最终决策。agent 后续可以读取评分细节并提出建议。

建议评分因素：

| 因素 | 说明 |
| --- | --- |
| 账号类型匹配 | plus/free/pro 与池配置一致加分 |
| 验证状态 | `verification_status=succeeded` 加分 |
| 备用优先级 | `reserve_priority` 越高越优先 |
| 支付类型 | 不同支付类型可配置风险权重 |
| 是否绑手机 | `phone_bound=true` 可加分或降低风险 |
| 历史错误 | 有 `verification_failed`、`returned` 历史减分 |
| 创建时间 | 可按先进先出或新鲜度加权 |
| 人工标注 | 人工状态标注可影响分数 |
| agent hint | 后续 agent 写入的建议可作为参考 |

输出：

```js
{
  score,
  score_detail: {
    account_type_match,
    verification,
    payment_risk,
    phone_bound,
    history,
    manual_priority
  }
}
```

### 问题账号退回

问题状态来源：

```text
error
disabled
paused
banned
invalid
failed
schedulable=false
rate_limited_at
temp_unschedulable_until
```

流程：

1. 容量计划或状态扫描发现问题账号。
2. 生成 `pool_plan_items.return_problem_account`。
3. 保存远端快照和本地快照。
4. 本地生命周期变为 `returned`。
5. 退出 active membership。
6. 生成整体待办。
7. 不默认硬删远端账号，只生成后续处理计划。

问题快照建议保存：

```js
{
  sub2api_account_id,
  group_ids,
  status,
  schedulable,
  error_message,
  credentials_status,
  current_concurrency,
  concurrency,
  codex_5h_used_percent,
  codex_7d_used_percent,
  codex_5h_reset_after_seconds,
  codex_7d_reset_after_seconds,
  last_used_at,
  rate_limited_at,
  temp_unschedulable_until,
  fetched_at
}
```

## 后端接口设计草案

### Import Batches

```text
POST /api/import-batches
GET  /api/import-batches
GET  /api/import-batches/{batch_id}
```

`POST /api/import-batches`：

- 创建批次并导入账号。
- 替代或增强现有 `/api/imports/commit`。
- 返回批次统计和账号处理结果。

### API Pools

```text
GET  /api/api-pools
POST /api/api-pools
GET  /api/api-pools/{pool_id}
PATCH /api/api-pools/{pool_id}
POST /api/api-pools/{pool_id}/plans
GET  /api/api-pools/{pool_id}/plans/{plan_id}
```

第一版至少需要：

- 查看本地池配置和容量摘要。
- 生成容量/补位/退回计划。
- 查看计划详情。

### Verification

```text
POST /api/accounts/{account_id}/verify
```

触发单账号验证：

- 只允许 `owner` / `admin` / `maintainer`。
- 只对 `historical`、`known_error`、`renew` 或人工指定账号开放。
- 写入 verification group。
- 调用 `gpt-5.4-mini` 发送 `hi`。
- 测完清理远端验证账号。
- 返回验证结果和本地账号状态。

### Todo Items

```text
GET /api/todo-items
```

第一版只读整体待办。后续再扩展：

```text
PATCH /api/todo-items/{todo_id}
POST  /api/todo-items/{todo_id}/resolve
```

## 后端模块建议

新增模块：

```text
backend/app/routers/import_batches.py
backend/app/routers/api_pools.py
backend/app/routers/todo_items.py
backend/app/services/pool_lifecycle.py
backend/app/services/account_verification.py
backend/app/services/pool_planner.py
```

职责：

| 模块 | 职责 |
| --- | --- |
| `import_batches.py` | 批次导入 API |
| `api_pools.py` | 本地池配置、容量摘要、计划生成 API |
| `todo_items.py` | 整体待办 API |
| `pool_lifecycle.py` | 生命周期状态切换、事件写入、membership 管理 |
| `account_verification.py` | 验证 group 写入、`hi` 测试、清理远端验证账号 |
| `pool_planner.py` | 容量计算、缺口判断、备用账号评分、计划生成 |

sub2api 写入能力仍封装在：

```text
backend/app/services/sub2api.py
```

不要在 router 中直接拼 sub2api 请求。

## 实现顺序

1. 写本文档并更新设计文档索引。
2. 新增集合索引设计。
3. 实现 `import_batches` 和账号版本记录。
4. 实现生命周期事件 `account_events`。
5. 实现 `api_pools` 本地池配置。
6. 实现老账号验证：写 verification group、调用 `gpt-5.4-mini hi`、清理远端账号。
7. 实现容量计划生成：读取 sub2api 缓存，计算缺口和推荐项。
8. 实现整体待办。
9. 后续开发前端可视化和手动执行入口。

## 测试场景

### 新账号批次

- 导入新账号批次后，生成 `import_batches`。
- 账号进入 `accounts`。
- `lifecycle_status = library`。
- 不进入备用池。
- 不写 sub2api。
- 不生成验证任务。

### 老账号验证成功

- 账号写入 verification group。
- 成功调用 `gpt-5.4-mini` 发送 `hi`。
- 记录验证响应和远端账号 ID。
- 测完清理远端验证账号。
- 本地账号进入 `reserve_ready`。
- 创建高优先级备用池 membership。

### 老账号验证失败

- 记录失败原因。
- 记录远端清理结果。
- 本地账号进入 `returned` 或 `needs_review`。
- 生成整体待办。

### 重复账号更新

- 同一 `chatgpt_account_id`、email、name 或 hash 匹配到本地账号。
- 没有远端 active 冲突时，写入 `account_versions`。
- 有远端 active/schedulable 冲突时，阻止覆盖。
- 写入 `account_events.remote_active_conflict`。
- 批次错误计数增加。

### 容量计划

- 健康账号数低于 `min_active` 时生成补位计划。
- 平均 5h 用量超过阈值时生成补位计划。
- 平均 7d 用量超过阈值时生成补位计划。
- 备用池数量低于 `min_reserve` 时生成整体待办。
- 计划项包含推荐账号、评分和原因。

### 问题账号退回

- 远端账号出现错误、禁用、暂停、不可调度或临时不可调度。
- 生成退回计划项。
- 保存问题快照。
- 本地状态变为 `returned`。
- 不默认硬删远端账号。

## Agent 预留设计

第一版不让 agent 直接执行最终动作，只为 agent 准备结构化输入。

agent 可读取：

- `import_batches` 的批次质量和错误统计。
- `accounts.metadata.risk_flags`。
- `accounts.metadata.agent_decision_hint`。
- `account_events` 的历史事件。
- `pool_plans` 的容量缺口。
- `todo_items` 的整体需求。

agent 后续可输出：

- 建议制作新账号。
- 建议更新旧账号。
- 建议弃用某类账号。
- 建议调整支付类型或绑手机策略。
- 建议提高某批次账号的备用池优先级。

agent 建议先写入结构化字段，不直接修改账号生命周期。

## 设计时假设（已归档）

- 验证站点在当时按单站点设计；当前必须从账号或池的 `site_id` 解析，不能固定站点。
- 当前不引入 Redis。
- 当前仍采用 MongoDB 明文存储。
- 当前先生成整体待办，不分配具体处理人。
- 新账号批次暂不抽样验证，抽样逻辑后续单独设计。
- 老账号、已知错误账号、需要复查账号可以逐个验证。
- 当时设想固定验证模型和测试消息；当前应由实际探测接口与站点配置决定。
- 验证通过后远端验证账号需要清理，本地进入高优先级备用池。
- agent 后续读取结构化字段和事件，不在第一版直接做最终决策。
