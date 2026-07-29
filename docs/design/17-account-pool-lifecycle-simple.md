# 账号池生命周期简化版设计

> **文档状态：历史方案。** 本文是生命周期简化过程中的中间稿，不再作为当前开发主线。字段和状态可参考 [19-account-pool-final-simple-design.md](./19-account-pool-final-simple-design.md)，容量、并发和补号逻辑以 [30-api-pool-realtime-capacity-and-presence.md](./30-api-pool-realtime-capacity-and-presence.md) 为准。

本文档是 `16-account-pool-lifecycle-backend.md` 的最小可落地版本。复杂版保留作为远期参考；实际开发优先按本文档推进。

简化原则：

- 先让系统能解释清楚、跑通闭环，再扩展复杂字段。
- 能从现有数据或 sub2api 缓存重新计算的字段，不急着单独存。
- 只保留会直接影响页面展示、人工操作、补位判断的字段。
- 复杂信号可以先放入 `metadata.analysis`，后续做 agent 或置信度聚合时再使用。

## 目标

我们需要一个简单但能工作的账号池流程：

```text
上传批次
  -> 账号总库
  -> 备用池
  -> 实际使用池 sub2api group
  -> 出错退回
  -> 人工处理或后续重新进入备用池
```

第一版重点解决：

- 上传账号有批次记录，能追溯来源。
- 所有账号先进入总库，不自动进入 sub2api。
- 手动或规则把账号放入备用池。
- 当实际使用池容量不足时，从备用池挑账号生成补位建议。
- 当实际使用池账号出错时，退回本地并记录问题快照。
- 当系统不知道该制作新账号还是更新旧账号时，生成整体待办。

## 最小集合

第一版只新增 4 个集合：

```text
import_batches
api_pools
pool_actions
todo_items
```

继续使用现有：

```text
accounts
sub2api_groups_cache
sub2api_accounts_cache
sub2api_cache_meta
```

暂不新增：

```text
account_versions
account_events
pool_memberships
pool_plans
pool_plan_items
```

这些复杂集合后续如果确实需要，再从 `pool_actions` 拆分出来。

## 核心状态

账号只保留一个核心状态字段：

```js
metadata.pool_status
```

枚举：

```text
library
reserve
active
problem
discarded
```

状态说明：

| 状态 | 含义 |
| --- | --- |
| `library` | 总库账号，默认状态。上传后先在这里 |
| `reserve` | 备用池账号，可以被补位逻辑选中 |
| `active` | 本地认为已进入实际使用池 |
| `problem` | 从实际使用池退回或验证失败的问题账号 |
| `discarded` | 人工弃用，不再参与自动判断 |

为什么不拆更多状态：

- `needs_verification`、`verifying` 可以先用 `metadata.verification` 表示。
- `active_pending` 可以先用 `pool_actions` 记录正在推送或推送失败。
- `blocked_conflict` 可以先写入 `metadata.last_error` 和 `pool_actions`。
- 后续如果页面需要更细状态，再扩展枚举。

## accounts 最小字段

现有账号结构不变：

```js
{
  account_json: {},
  metadata: {}
}
```

第一版新增或重点使用这些字段：

```js
metadata: {
  batch_id,
  pool_status,
  pool_id,
  upload_intent,
  priority,
  sub2api_site_id,
  sub2api_account_id,
  sub2api_group_ids,
  last_error,
  problem_snapshot,
  analysis
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `batch_id` | 来源批次 |
| `pool_status` | 核心池状态：`library` / `reserve` / `active` / `problem` / `discarded` |
| `pool_id` | 当前所属本地池。没有入池则为空 |
| `upload_intent` | `new` / `renew` / `purchase` / `historical` / `known_error` |
| `priority` | 人工或系统优先级，数字越高越优先 |
| `sub2api_site_id` | 已推送到哪个 sub2api 站点 |
| `sub2api_account_id` | 对应远端账号 ID |
| `sub2api_group_ids` | 对应远端 group |
| `last_error` | 最近一次错误摘要 |
| `problem_snapshot` | 问题退回时保存的远端快照 |
| `analysis` | 复杂分析字段，后续 agent 或置信度聚合使用 |

`metadata.analysis` 可以容纳暂时不参与主逻辑的复杂信息：

```js
analysis: {
  risk_score,
  risk_flags,
  confidence,
  agent_notes,
  verification_history,
  scoring_detail
}
```

规则：

- 主流程不要依赖 `analysis`。
- `analysis` 只用于展示、辅助判断和后续 agent。
- 后续确认某个字段稳定有用后，再提升为一等字段。

## import_batches

`import_batches` 是总文件库的简化版本。

```js
{
  _id,
  name,
  upload_intent,
  uploaded_by_user_id,
  uploader_name,
  created_at,
  raw_sha256,
  total_count,
  created_count,
  updated_count,
  error_count,
  status,
  remark
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `name` | 批次名称 |
| `upload_intent` | `new` / `renew` / `purchase` / `historical` / `known_error` |
| `uploaded_by_user_id` | 上传人 ID |
| `uploader_name` | 上传人名称 |
| `raw_sha256` | 上传内容 hash |
| `total_count` | 解析账号总数 |
| `created_count` | 新增数量 |
| `updated_count` | 更新数量 |
| `error_count` | 错误数量 |
| `status` | `ok` / `has_error` / `needs_review` |
| `remark` | 批次备注 |

不在第一版保存完整原始文件内容。原因：

- 原始内容可能非常大。
- 原始内容包含敏感 token。
- 账号已经逐条进入 `accounts.account_json`。

如果后续确实需要追溯完整文件，再增加 `raw_payload` 或文件存储。

## api_pools

`api_pools` 保存本地池配置。一个本地池通常对应一个实际使用 sub2api group。

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

默认阈值：

```js
{
  min_active: 20,
  target_active: 30,
  max_avg_5h_used: 70,
  max_avg_7d_used: 80,
  min_reserve: 10
}
```

说明：

- `active_group_id` 是实际使用池。
- `verification_group_id` 是老账号验证用的临时 group。
- 备用池不需要 sub2api group，本地通过 `accounts.metadata.pool_status = reserve` 表示。

## pool_actions

`pool_actions` 是第一版的统一动作日志，用来替代复杂版里的多个集合。

```js
{
  _id,
  action_type,
  account_id,
  pool_id,
  status,
  reason,
  before,
  after,
  remote_snapshot,
  error,
  created_by,
  created_at,
  finished_at
}
```

动作类型：

```text
import_account
update_account
enter_reserve
push_to_active
push_failed
verify_account
verify_failed
return_problem
discard_account
remote_conflict
capacity_check
```

好处：

- 所有关键动作都有记录。
- 不需要一开始设计复杂事件表、计划表、成员表。
- 后续如果某类动作变复杂，可以从这里拆分成独立集合。

## todo_items

`todo_items` 用于整体待办，不分配具体处理人。

```js
{
  _id,
  title,
  todo_type,
  status,
  pool_id,
  summary,
  suggested_action,
  created_at,
  resolved_at
}
```

待办类型：

```text
need_more_accounts
reserve_low
problem_accounts
batch_needs_review
remote_conflict
```

第一版只做整体待办：

- 哪个池缺账号。
- 缺多少。
- 为什么缺。
- 有多少问题账号。
- 建议制作新账号还是更新旧账号。

## 最小流程

### 1. 上传新账号

流程：

1. 创建 `import_batches`。
2. 解析账号 JSON。
3. 每个账号写入 `accounts`。
4. 设置：

```js
metadata.pool_status = "library"
metadata.upload_intent = "new"
metadata.batch_id = batch_id
```

规则：

- 不自动写入 sub2api。
- 不自动进入备用池。
- 不自动验证。

### 2. 上传旧账号或问题账号

流程：

1. 创建 `import_batches`。
2. 账号写入或更新 `accounts`。
3. 设置：

```js
metadata.pool_status = "library"
metadata.upload_intent = "renew" | "purchase" | "historical" | "known_error"
```

后续可以人工触发验证。

### 3. 手动进入备用池

从总库选择账号，执行“加入备用池”：

```js
metadata.pool_status = "reserve"
metadata.pool_id = pool_id
metadata.priority = 用户设置或默认值
```

同时写 `pool_actions.enter_reserve`。

### 4. 老账号验证

验证只针对旧账号、错误账号、复查账号。

流程：

1. 临时写入 `api_pools.verification_group_id`。
2. 通过 sub2api 使用 `gpt-5.4-mini` 发送 `hi`。
3. 保存结果。
4. 清理远端验证账号。
5. 成功则进入备用池：

```js
metadata.pool_status = "reserve"
metadata.priority = 高优先级
```

6. 失败则进入问题状态：

```js
metadata.pool_status = "problem"
metadata.last_error = 错误摘要
```

同时写 `pool_actions.verify_account` 或 `pool_actions.verify_failed`。

### 5. 容量检查

容量检查读取 MongoDB 中的 sub2api 缓存，不直接请求远端。

健康账号判断：

```text
status == active
schedulable == true
无 error_message
无 rate_limited_at
无 temp_unschedulable_until
```

检查指标：

```js
{
  healthy_active_count,
  avg_5h_used,
  avg_7d_used,
  reserve_count
}
```

触发待办：

- `healthy_active_count < min_active`
- `avg_5h_used >= max_avg_5h_used`
- `avg_7d_used >= max_avg_7d_used`
- `reserve_count < min_reserve`

第一版不需要生成复杂 plan，只写：

```text
pool_actions.capacity_check
todo_items.need_more_accounts 或 todo_items.reserve_low
```

### 6. 补位建议

当实际使用池不足时，从备用池挑账号。

第一版排序规则：

1. `pool_id` 匹配。
2. `pool_status = reserve`。
3. `account_type` 匹配。
4. `priority` 高的优先。
5. 没有 `last_error` 的优先。
6. 更新时间更早的优先。

输出只是建议列表，不自动推送：

```js
{
  pool_id,
  need_count,
  suggested_account_ids
}
```

后续再做“确认推送到 sub2api”。

### 7. 推送到实际使用池

第一版可以先手动触发，不做自动。

流程：

1. 选择备用账号。
2. 写入 sub2api `active_group_id`。
3. 成功后设置：

```js
metadata.pool_status = "active"
metadata.sub2api_site_id = site_id
metadata.sub2api_account_id = remote_id
metadata.sub2api_group_ids = [active_group_id]
```

4. 失败后不改变账号状态，写：

```text
pool_actions.push_failed
metadata.last_error
```

### 8. 问题账号退回

当 sub2api 缓存发现 active 账号异常：

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

1. 保存远端快照到 `metadata.problem_snapshot`。
2. 设置：

```js
metadata.pool_status = "problem"
metadata.last_error = 远端错误摘要
```

3. 写 `pool_actions.return_problem`。
4. 生成 `todo_items.problem_accounts`。
5. 不默认硬删远端账号。

## 决策与置信度

复杂信息不删除，但不进入主流程。

可以统一放在：

```js
metadata.analysis
```

示例：

```js
analysis: {
  confidence: 0.72,
  risk_score: 35,
  risk_flags: ["old_account", "paypal_single"],
  signals: {
    payment_type_weight: 0.2,
    phone_bound_weight: 0.1,
    verification_weight: 0.4,
    error_history_weight: 0.3
  },
  agent_notes: "建议更新旧账号后重新验证"
}
```

原则：

- 主流程只看 `pool_status`、`pool_id`、`priority`、`last_error`。
- `analysis` 只做辅助展示和后续 agent 判断。
- 如果某个分析字段连续被使用，再提升为正式字段。

## 第一版接口

建议先做这些接口：

```text
POST /api/import-batches
GET  /api/import-batches
GET  /api/import-batches/{id}

GET  /api/api-pools
POST /api/api-pools
PATCH /api/api-pools/{id}

POST /api/accounts/{id}/enter-reserve
POST /api/accounts/{id}/verify
POST /api/accounts/{id}/push-to-active

POST /api/api-pools/{id}/capacity-check
GET  /api/todo-items
```

暂不做：

```text
复杂 plan 审批流
自动补位定时任务
按人分配待办
agent 自动执行动作
```

## 第一版开发顺序

1. 给 `accounts.metadata` 增加 `pool_status`、`pool_id`、`priority`、`problem_snapshot`、`analysis` 的使用约定。
2. 新增 `import_batches`。
3. 新增 `api_pools`。
4. 新增 `pool_actions`。
5. 新增 `todo_items`。
6. 实现“加入备用池”。
7. 实现“容量检查”和“补位建议”。
8. 实现“问题账号退回”。
9. 实现“老账号验证”。
10. 最后再实现“推送到实际使用池”。

## 测试场景

- 新账号上传后，账号状态为 `library`。
- 手动加入备用池后，账号状态为 `reserve`。
- 容量不足时，生成 `todo_items.need_more_accounts`。
- 备用池不足时，生成 `todo_items.reserve_low`。
- 老账号验证成功后，账号进入 `reserve` 且优先级较高。
- 老账号验证失败后，账号进入 `problem`。
- 推送 sub2api 成功后，账号进入 `active` 并记录远端 ID。
- 推送失败后，账号仍留在 `reserve`，记录 `last_error`。
- active 账号远端出错后，账号进入 `problem`，保存 `problem_snapshot`。

## 与复杂版的关系

复杂版中的字段不是废弃，而是降级为后续扩展。

暂时不做：

- 独立 `account_versions`。
- 独立 `account_events`。
- 独立 `pool_memberships`。
- 独立 `pool_plans` 和 `pool_plan_items`。
- 细分生命周期状态。
- 自动按人分配待办。

如果以后出现真实需求：

- `pool_actions` 太大，再拆成 `account_events`。
- 推送审批复杂，再拆出 `pool_plans`。
- 一个账号同时属于多个池，再拆出 `pool_memberships`。
- 重复账号更新历史变重要，再拆出 `account_versions`。

## 当前结论

第一版只需要记住一句话：

```text
账号先进入总库，需要时手动进入备用池；实际池不够时从备用池推荐补位；实际池出错时退回问题状态；复杂分析先放 analysis，不参与主流程。
```
