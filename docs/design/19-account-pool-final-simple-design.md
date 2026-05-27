# 账号池最终简化设计思路

本文档合并 `17-account-pool-lifecycle-simple.md` 和 `18-account-pool-simple-logic-analysis.md`，作为近期实现账号池后端逻辑的主参考。

目标：保持系统足够简单，同时修掉已发现的主要逻辑风险。

## 总体结论

第一版账号池不做复杂状态机，不做完整自动化，不做复杂审批流。

第一版只做这条主线：

```text
上传账号 -> 总库 library
人工/验证 -> 备用池 reserve
手动推送 -> 实际池 active
远端异常 -> 问题 problem
人工弃用 -> discarded
```

核心原则：

- `accounts.metadata.pool_status` 是账号当前状态唯一来源。
- `pool_actions` 只记录历史动作，不反推当前状态。
- sub2api 缓存是远端观测，不直接覆盖本地状态。
- 第一版一个账号同一时间只属于一个本地 `api_pool`。
- 复杂风险、置信度、agent 信号先放入 `metadata.analysis`，不参与主流程硬判断。

## 最小集合

新增集合：

```text
import_batches
api_pools
pool_actions
todo_items
```

继续使用：

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

后续拆分规则：

- `pool_actions` 太大，再拆 `account_events`。
- 推送审批复杂，再拆 `pool_plans`。
- 一个账号需要同时属于多个池，再拆 `pool_memberships`。
- 账号 JSON 回滚变重要，再拆 `account_versions`。

## accounts.metadata 最小字段

```js
metadata: {
  batch_id,
  upload_intent,
  pool_status,
  pool_id,
  priority,
  sub2api_site_id,
  sub2api_account_id,
  sub2api_group_ids,
  last_error,
  push_lock,
  problem_snapshot,
  analysis
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `batch_id` | 来源批次 |
| `upload_intent` | `new` / `renew` / `purchase` / `historical` / `known_error` |
| `pool_status` | 当前本地状态 |
| `pool_id` | 当前本地池 ID，第一版单账号只允许一个池 |
| `priority` | 备用池补位优先级，数字越高越优先 |
| `sub2api_site_id` | 实际推送站点 |
| `sub2api_account_id` | sub2api 远端账号 ID |
| `sub2api_group_ids` | sub2api 远端 group IDs |
| `last_error` | 最近错误摘要 |
| `push_lock` | 推送并发锁，防止重复推送 |
| `problem_snapshot` | 退回问题池时的远端状态快照 |
| `analysis` | 复杂分析信息和 agent 预留字段 |

`metadata.analysis` 示例：

```js
analysis: {
  confidence: 0.72,
  risk_score: 35,
  risk_flags: ["old_account", "paypal_single"],
  remote_duplicate: false,
  remote_uncertain: false,
  cleanup_warning: false,
  scoring_detail: {},
  agent_notes: ""
}
```

约束：

- 主流程不依赖 `analysis`。
- `analysis` 只用于展示、辅助分析、agent 输入。
- 某个分析字段稳定有用后，再提升成正式字段。

## pool_status

枚举：

```text
library
reserve
active
problem
discarded
```

含义：

| 状态 | 含义 |
| --- | --- |
| `library` | 总库账号，上传后默认状态 |
| `reserve` | 备用池账号，可以被补位逻辑选中 |
| `active` | 本地认为已经进入实际使用池 |
| `problem` | 出错退回或验证失败账号 |
| `discarded` | 人工弃用账号 |

允许流转：

```text
library  -> reserve
library  -> problem
library  -> discarded
reserve  -> active
reserve  -> problem
reserve  -> discarded
active   -> problem
active   -> reserve
problem  -> reserve
problem  -> discarded
```

禁止默认流转：

```text
library -> active
discarded -> reserve
discarded -> active
```

如确实需要从 `library` 强制推送，需要单独接口、二次确认和高风险日志。

## import_batches

简化批次结构：

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

规则：

- 第一版不保存完整原始文件内容。
- `raw_sha256` 用于标记批次来源和去重。
- 每个账号写入 `accounts.metadata.batch_id`。
- 批次只做追溯和统计，不参与账号当前状态判断。

`upload_intent`：

```text
new
renew
purchase
historical
known_error
```

含义：

| 值 | 含义 |
| --- | --- |
| `new` | 新制作账号，默认只入总库 |
| `renew` | 更新/续用旧账号 JSON |
| `purchase` | 购买账号 |
| `historical` | 历史账号，可能需要验证 |
| `known_error` | 已知问题账号，通常需要验证或人工处理 |

## api_pools

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

默认值：

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

- `active_group_id` 是实际使用的 sub2api group。
- `verification_group_id` 是老账号验证用的临时 group。
- 备用池只在本地表达，不需要 sub2api group。

## pool_actions

`pool_actions` 是统一动作日志。

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

关键约束：

- `pool_actions` 只表示历史。
- 当前状态只看 `accounts.metadata.pool_status`。
- 更新账号 JSON 时，第一版至少在 `pool_actions.update_account` 记录 old/new sha256、email、account name 摘要。

## todo_items

```js
{
  _id,
  dedupe_key,
  title,
  todo_type,
  status,
  pool_id,
  summary,
  suggested_action,
  occurrence_count,
  created_at,
  updated_at,
  resolved_at
}
```

待办类型：

```text
need_more_accounts
reserve_low
capacity_data_incomplete
problem_accounts
batch_needs_review
remote_conflict
```

去重规则：

```text
dedupe_key = "{todo_type}:{pool_id}"
```

创建待办时：

- 如果同一 `dedupe_key` 已有 `open` 待办，则更新 `summary`、`updated_at`、`occurrence_count`。
- 不重复创建新待办。

第一版不分配具体处理人。

## 流程 1：上传账号

新账号：

```js
metadata.pool_status = "library"
metadata.upload_intent = "new"
metadata.batch_id = batch_id
```

规则：

- 不写 sub2api。
- 不进入备用池。
- 不自动验证。

历史账号、已知错误账号、renew 账号：

```js
metadata.pool_status = "library"
metadata.upload_intent = "renew" | "purchase" | "historical" | "known_error"
```

后续可人工验证或加入备用池。

远端重复处理：

- 如果本地无重复，但 sub2api 缓存存在同 email/name/chatgpt_account_id 且 active/schedulable，允许入库，但标记：

```js
metadata.last_error = "remote active duplicate"
metadata.analysis.remote_duplicate = true
metadata.pool_status = "library"
```

- 同时写 `pool_actions.remote_conflict` 和去重待办 `todo_items.remote_conflict`。
- 如果是更新本地已有账号，并且远端正在使用，则阻止覆盖。

## 流程 2：加入备用池

允许来源：

```text
library
problem
```

目标：

```js
metadata.pool_status = "reserve"
metadata.pool_id = pool_id
metadata.priority = priority
metadata.last_error = null
```

必须使用条件更新，例如：

```js
{
  _id: account_id,
  "metadata.pool_status": { "$in": ["library", "problem"] }
}
```

成功后写：

```text
pool_actions.enter_reserve
```

## 流程 3：容量检查

容量检查只读 MongoDB 缓存，不直接请求远端。

查询范围：

```js
site_id == api_pool.site_id
group_ids contains api_pool.active_group_id
```

健康账号：

```text
status == active
schedulable == true
无 error_message
无 rate_limited_at
无 temp_unschedulable_until
```

计算指标：

```js
{
  healthy_active_count,
  active_total_count,
  avg_5h_used_observed,
  avg_7d_used_observed,
  observed_5h_count,
  observed_7d_count,
  missing_5h_count,
  missing_7d_count,
  high_5h_count,
  high_7d_count,
  eligible_reserve_count
}
```

缺失值规则：

- 缺失 5h/7d 字段不按 0 计算。
- 平均值只统计有观测值的账号。
- 如果缺失比例超过 30%，生成 `capacity_data_incomplete` 待办。

极端用量规则：

- 平均值超过阈值，生成 `need_more_accounts` 待办。
- 或 `high_5h_count >= max(3, healthy_active_count * 0.3)`，生成 `need_more_accounts` 待办。
- 或 `high_7d_count >= max(3, healthy_active_count * 0.3)`，生成 `need_more_accounts` 待办。

备用池可用账号：

```text
pool_status == reserve
pool_id == 当前池
account_type 匹配
last_error 为空
未 discarded
```

触发条件：

- `healthy_active_count < min_active`
- `avg_5h_used_observed >= max_avg_5h_used`
- `avg_7d_used_observed >= max_avg_7d_used`
- `eligible_reserve_count < min_reserve`
- 容量观测数据缺失比例过高

写入：

```text
pool_actions.capacity_check
todo_items.need_more_accounts / reserve_low / capacity_data_incomplete
```

## 流程 4：补位建议

补位建议只从可用备用账号中选。

过滤条件：

```text
metadata.pool_status == reserve
metadata.pool_id == pool_id
metadata.account_type == api_pool.account_type
metadata.last_error 为空
metadata.push_lock 不存在
```

排序：

```text
priority desc
metadata.updated_at asc
metadata.created_at asc
```

输出：

```js
{
  pool_id,
  need_count,
  suggested_account_ids,
  reason
}
```

第一版只返回建议，不自动推送。

## 流程 5：推送到实际使用池

推送只允许：

```text
pool_status == reserve
pool_id == target_pool_id
```

禁止：

```text
library -> active
discarded -> active
problem -> active
```

并发保护：

1. 创建 `pool_actions.push_to_active`，状态 `running`。
2. 原子锁定账号：

```js
findOneAndUpdate(
  {
    _id: account_id,
    "metadata.pool_status": "reserve",
    "metadata.pool_id": pool_id,
    "metadata.push_lock": { "$exists": false }
  },
  {
    "$set": {
      "metadata.push_lock": action_id
    }
  }
)
```

3. 写入 sub2api `api_pool.active_group_id`。
4. 成功后：

```js
metadata.pool_status = "active"
metadata.sub2api_site_id = api_pool.site_id
metadata.sub2api_account_id = remote_id
metadata.sub2api_group_ids = [api_pool.active_group_id]
metadata.push_lock = null
metadata.last_error = null
```

5. 失败后：

```js
metadata.pool_status = "reserve"
metadata.last_error = error_summary
metadata.push_lock = null
```

远端不确定处理：

如果 sub2api 请求超时、连接断开或响应未知，不要立即允许再次推送。

写入：

```js
metadata.analysis.remote_uncertain = true
metadata.last_error = "push remote state unknown"
```

然后：

1. 触发 sub2api 缓存刷新。
2. 用 email/name/chatgpt_account_id 查找远端账号。
3. 如果找到远端账号，补写 active 状态。
4. 如果确认找不到，才允许再次推送。

## 流程 6：问题账号退回

严重错误直接退回：

```text
error
disabled
banned
invalid
failed
schedulable=false 且无恢复时间
```

临时状态先观察：

```text
rate_limited_at
temp_unschedulable_until
rate_limit_reset_at 在未来
```

观察规则：

- 临时异常只写 `pool_actions.capacity_check` 或待办提示，不立即改 `problem`。
- 如果连续 2 次缓存刷新仍异常，或异常持续超过 30 分钟，再退回 `problem`。

退回时：

```js
metadata.pool_status = "problem"
metadata.last_error = error_summary
metadata.problem_snapshot = remote_snapshot
```

同时：

```text
pool_actions.return_problem
todo_items.problem_accounts
```

不默认硬删远端账号。

## 流程 7：老账号验证

适用：

```text
renew
purchase
historical
known_error
```

流程：

1. 创建 `pool_actions.verify_account`。
2. 临时写入 `api_pool.verification_group_id`。
3. 通过 sub2api 使用 `gpt-5.4-mini` 发送 `hi`。
4. 记录调用结果。
5. 清理远端验证账号。
6. 成功进入 `reserve`，失败进入 `problem`。

验证结果拆成两部分：

```js
verification_call_status
verification_cleanup_status
```

如果调用成功但清理失败：

```js
metadata.pool_status = "reserve"
metadata.analysis.cleanup_warning = true
metadata.last_error = "verification remote cleanup failed"
```

并生成 `todo_items.remote_conflict`。

## 风险解决汇总

| 风险 | 最终解决 |
| --- | --- |
| 新账号绕过备用池直接推送 | 后端强制只有 `reserve` 能 push-to-active |
| 同账号并发推送 | 使用 `metadata.push_lock` 和条件更新 |
| sub2api 成功但后端超时 | 标记 `remote_uncertain`，刷新缓存确认 |
| 容量只看当前页 | 后端读取完整 `sub2api_accounts_cache` |
| 缺失 5h/7d 按 0 | 缺失不参与平均，记录 missing count |
| 平均值掩盖极端账号 | 增加 high_5h/high_7d count |
| 备用池数量虚高 | 使用 `eligible_reserve_count` |
| 临时限流误退回 | 临时状态先观察，连续异常再 problem |
| 本地 active 与远端不一致 | 生成 `remote_conflict` 待办，不自动覆盖 |
| 待办重复生成 | 使用 `dedupe_key` 更新未解决待办 |
| pool_actions 被当当前状态 | 明确当前状态只看 `pool_status` |
| 单账号多池 | 第一版只支持单池，后续用 `pool_memberships` |
| 缺少版本回滚 | 第一版记录 update 摘要，后续再做版本表 |
| 远端重复账号正在使用 | 新账号标记冲突；更新已有账号时阻止覆盖 |
| 验证成功但清理失败 | 验证调用和清理结果分开记录 |

## 第一版接口

```text
POST /api/import-batches
GET  /api/import-batches
GET  /api/import-batches/{id}

GET   /api/api-pools
POST  /api/api-pools
PATCH /api/api-pools/{id}

POST /api/accounts/{id}/enter-reserve
POST /api/accounts/{id}/verify
POST /api/accounts/{id}/push-to-active

POST /api/api-pools/{id}/capacity-check
GET  /api/todo-items
```

接口约束：

- `enter-reserve` 只允许 `library` / `problem`。
- `push-to-active` 只允许 `reserve`。
- `verify` 主要用于 `historical` / `known_error` / `renew`。
- `capacity-check` 不访问远端，只读 MongoDB 缓存。
- `todo-items` 第一版只读整体待办。

## 第一版开发顺序

1. 新增 `pool_status` 等 metadata 使用约定和索引。
2. 新增 `import_batches`。
3. 新增 `api_pools`。
4. 新增 `pool_actions`。
5. 新增 `todo_items` 和 `dedupe_key` upsert。
6. 实现加入备用池。
7. 实现容量检查和补位建议。
8. 实现问题账号退回。
9. 实现推送到实际使用池，包含 `push_lock`。
10. 实现老账号验证。

## 验收场景

- 新账号上传后只进入 `library`。
- `library` 账号不能直接 push-to-active。
- `reserve` 账号可以 push-to-active。
- 两个并发 push-to-active 只有一个成功获得 `push_lock`。
- sub2api 超时后标记 `remote_uncertain`，不会重复推送。
- 容量计算使用完整缓存。
- 5h/7d 缺失值不按 0 计算。
- 备用池不足按 `eligible_reserve_count` 判断。
- 临时限流不会立即进入 `problem`。
- 严重远端错误会进入 `problem` 并保存 `problem_snapshot`。
- 同一池同一类型待办不会重复创建。

## 最终设计口径

第一版只需要记住：

```text
账号状态只看 pool_status。
动作记录只写 pool_actions。
待办必须去重。
推送必须加锁。
容量必须读完整缓存。
复杂判断先放 analysis。
```

这套设计比复杂版少很多集合和字段，但已经解决第一版最容易出现的逻辑错误。
