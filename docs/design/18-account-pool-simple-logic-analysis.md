# 账号池简化版逻辑分析报告

本文档用于审查 `17-account-pool-lifecycle-simple.md` 的最小实现逻辑。目标不是重新复杂化，而是通过具体例子找出可能的逻辑错误、边界风险和需要补充的最小约束。

结论：

- 简化版方向正确，可以作为近期开发主线。
- 但需要补充少量“防错规则”，否则容易出现重复推送、容量误判、远端与本地状态不一致、待办重复生成等问题。
- 复杂字段不需要马上恢复，建议继续放入 `metadata.analysis`，只在决策时作为置信度信号参与聚合。

## 一句话版本

简化版可以落地，但第一版实现时必须守住 5 条底线：

1. `accounts.metadata.pool_status` 是本地当前状态的唯一主字段。
2. `pool_actions` 只做动作日志，不能反向当成当前状态。
3. 推送、退回、加入备用池都必须做原子条件更新，避免并发重复操作。
4. 容量检查必须读取完整 group 缓存，不能只看当前页面数据。
5. 待办必须做去重，同一个池同一种未解决问题不要无限生成。

## 状态流转审查

### 设计状态

简化版只有 5 个核心状态：

```text
library -> reserve -> active -> problem -> discarded
```

推荐允许的状态流转：

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

不建议直接允许：

```text
library -> active
discarded -> active
discarded -> reserve
```

原因：

- `library -> active` 绕过备用池和补位选择，容易让未确认账号直接进入 sub2api。
- `discarded` 是人工弃用状态，后续如果要恢复，应该先人工改回 `library` 或 `problem`，不要自动恢复。

## 例子 1：新账号上传后被误推到 sub2api

场景：

```text
批次 A 上传 100 个新账号
账号状态全部为 library
维护人员在列表里批量选择
误点“推送到实际使用池”
```

如果接口只检查账号存在，不检查 `pool_status`，这些新账号会绕过备用池直接进入 sub2api。

风险：

- 新账号没有验证或人工确认。
- 用户之前明确说过：新账号加入 sub2api 但不使用也可能增加封禁风险。
- 如果推送失败，本地还可能误标为 active。

建议修正：

- `push-to-active` 只允许从 `reserve` 状态执行。
- 后端强制条件：

```js
metadata.pool_status == "reserve"
metadata.pool_id == target_pool_id
```

- 如果确实需要从总库直接推送，应另开“强制推送”接口并写高风险日志。

结论：这是必须修正的 P0 逻辑约束。

## 例子 2：两个维护人员同时推送同一个备用账号

场景：

```text
账号 X 状态为 reserve
用户 A 点击推送
用户 B 几乎同时点击推送
两个请求都调用 sub2api 创建账号
```

可能结果：

- sub2api 中重复创建同一个账号。
- 一个请求成功，一个请求失败。
- 本地只保存最后一个远端 ID。

建议修正：

推送前做原子占用，不新增 `active_pending` 状态也可以，用 `pool_actions` 和条件更新解决：

```js
findOneAndUpdate(
  {
    _id: account_id,
    "metadata.pool_status": "reserve",
    "metadata.push_lock": { "$exists": false }
  },
  {
    "$set": {
      "metadata.push_lock": action_id
    }
  }
)
```

推送成功：

```js
metadata.pool_status = "active"
metadata.push_lock = null
```

推送失败：

```js
metadata.pool_status = "reserve"
metadata.last_error = error
metadata.push_lock = null
```

如果不想加 `push_lock` 字段，也至少要在 MongoDB 更新时加条件：

```js
{ _id: account_id, "metadata.pool_status": "reserve" }
```

结论：需要最小并发锁，否则自动补位或多人操作会出错。

## 例子 3：sub2api 创建成功，但后端超时

场景：

```text
后端调用 sub2api 创建账号
sub2api 实际创建成功
网络超时或后端没有拿到响应
本地记录 push_failed
账号仍然是 reserve
```

下一次再推送同一个账号，会重复创建远端账号。

建议修正：

推送失败后不要只写 `last_error`，还要进入“疑似远端不确定”处理：

```js
metadata.last_error = "push timeout, remote state unknown"
metadata.analysis.remote_uncertain = true
```

后端下一步应：

1. 触发 sub2api 账号池数据同步。
2. 用 email / name / chatgpt_account_id / sha256 在缓存中查找远端账号。
3. 如果找到，则补写：

```js
metadata.pool_status = "active"
metadata.sub2api_account_id = remote_id
```

4. 如果找不到，才允许再次推送。

结论：推送失败需要区分“确定失败”和“远端未知”，这是 P1 约束。

## 例子 4：容量检查只看当前页导致误判

场景：

```text
plus group 有 500 个账号
前端当前页只显示 50 个
当前页 50 个账号平均 5h 用量 90%
全量 500 个账号平均 5h 用量 40%
```

如果后端复用前端当前页结果，会错误生成补位待办。

现有设计已经说容量检查读取 MongoDB 缓存，这是正确的。

必须明确：

- 后端容量检查要查完整 `sub2api_accounts_cache`。
- 不能用前端 `ApiPoolStatusPage` 当前页计算结果。
- 查询条件必须包含：

```js
site_id == api_pool.site_id
group_ids contains api_pool.active_group_id
```

结论：设计方向正确，实现时必须避免复用前端分页数据。

## 例子 5：缺失 5h/7d 字段被当作 0

场景：

```text
30 个 active 账号
其中 20 个缺少 extra.codex_5h_used_percent
10 个有值且平均 95%
```

如果缺失字段按 0 计算：

```text
平均值 = (20 * 0 + 10 * 95) / 30 = 31.6%
```

系统会认为容量很健康，但实际可观测账号已经接近耗尽。

建议修正：

容量计算拆成两个指标：

```js
avg_5h_used_observed
missing_5h_count
```

第一版简单规则：

- 缺失值不参与平均值。
- 如果缺失比例超过 30%，生成 `todo_items` 提醒“容量数据不完整”。
- 页面展示时标注 `observed_count / healthy_count`。

示例：

```text
healthy=30
observed_5h=10
missing_5h=20
avg_5h_used=95%
```

结论：缺失值不能按 0 处理，这是 P0 容量判断问题。

## 例子 6：平均用量掩盖极端账号

场景：

```text
10 个健康账号
5h 用量分别是：100, 100, 100, 0, 0, 0, 0, 0, 0, 0
平均 30%
```

如果只看平均，系统不会补位。但 3 个账号已经耗尽，实际并发和稳定性可能下降。

建议修正：

除了平均值，增加简单计数：

```js
high_5h_count = used_5h >= 90 的账号数量
high_7d_count = used_7d >= 90 的账号数量
```

第一版触发规则可以保持简单：

- 平均超过阈值，生成补位待办。
- 或者 `high_5h_count >= max(3, healthy_count * 0.3)`，生成提醒。

结论：不必马上复杂化，但报告中应记录平均值的盲点。

## 例子 7：备用池数量看起来足够，但不可用

场景：

```text
reserve_count = 20
其中 10 个是 free
5 个 last_error 不为空
3 个 account_type 缺失
2 个属于另一个 pool
plus 池实际可用备用账号 = 0
```

如果只数 `pool_status=reserve`，会误判备用池充足。

建议修正：

定义 `eligible_reserve_count`：

```text
pool_status == reserve
pool_id == 当前池
account_type 匹配
last_error 为空
未 discarded
```

容量检查使用：

```text
eligible_reserve_count < min_reserve
```

而不是原始 `reserve_count`。

结论：备用池统计必须是“可用备用账号数”，不是简单状态计数。

## 例子 8：临时限流被直接打成 problem

场景：

```text
账号状态 active
schedulable true
rate_limited_at 有值
rate_limit_reset_at 10 分钟后恢复
```

如果立刻设置 `pool_status=problem`，可能把临时限流账号踢出使用池。

建议修正：

第一版不用新增 `warning` 状态，但退回规则要分等级：

直接进入 `problem`：

```text
error
disabled
banned
invalid
failed
schedulable=false 且无恢复时间
```

只生成观察待办，不立刻退回：

```text
rate_limited_at
temp_unschedulable_until
rate_limit_reset_at 仍在未来
```

如果临时异常持续超过阈值，例如 30 分钟或连续 2 次缓存刷新仍异常，再退回 problem。

结论：简化状态没问题，但问题判定需要避免把临时状态当永久错误。

## 例子 9：active 本地状态和远端 group 不一致

场景：

```text
本地账号 X pool_status=active
metadata.sub2api_group_ids=[3]
sub2api 缓存里账号 X 已不在 group 3
```

可能原因：

- 远端被手动移出。
- 缓存刷新删除了账号。
- 本地保存了错误远端 ID。

建议修正：

容量检查时顺手做轻量一致性扫描：

```text
本地 active 账号在缓存中找不到 -> pool_actions.remote_conflict
本地 active group 与远端 group 不一致 -> todo_items.remote_conflict
```

不要自动覆盖本地，也不要自动恢复远端。先生成待办。

结论：本地 active 不是绝对真相，需要与远端缓存比对。

## 例子 10：待办重复生成

场景：

```text
每 5 分钟容量检查一次
plus 池持续缺 10 个账号
一天生成 288 条 need_more_accounts 待办
```

建议修正：

`todo_items` 增加去重键：

```js
dedupe_key = "{todo_type}:{pool_id}"
```

创建待办时：

- 如果同一 `dedupe_key` 已有 `open` 待办，则更新 `summary` 和 `updated_at`。
- 不创建新待办。

建议字段：

```js
{
  dedupe_key,
  updated_at,
  occurrence_count
}
```

结论：待办必须去重，这是 P0 可用性问题。

## 例子 11：pool_actions 同时承担日志和当前动作

场景：

```text
账号有 enter_reserve 成功日志
后来又被 discarded
查询 pool_actions 看到 enter_reserve
误以为账号仍在 reserve
```

建议修正：

明确：

- 当前状态只看 `accounts.metadata.pool_status`。
- `pool_actions` 只表示历史动作。
- 页面列表不要通过 `pool_actions` 反推当前池状态。

结论：文档需要强调 source of truth，避免实现歧义。

## 例子 12：一个账号只能属于一个 pool 的限制

简化版用：

```js
metadata.pool_id
```

这意味着一个账号同一时间只能属于一个本地池。

可能问题：

```text
一个 plus 账号既想作为 plus 主池备用，也想作为 plus 测试池备用
```

简化版无法表达。

建议：

- 第一版接受这个限制。
- 文档明确：一个账号同一时间只属于一个本地池。
- 如果后续需要多池关系，再引入 `pool_memberships`。

结论：这是可接受的简化，但需要写明约束。

## 例子 13：重复账号更新缺少版本表

简化版暂不做 `account_versions`。

场景：

```text
账号 X 原来在 problem
维护人员上传新 JSON 更新账号
更新后又失败
想回滚旧 JSON
```

没有版本表时无法回滚。

建议：

第一版不引入版本表，但更新账号时至少写 `pool_actions.update_account`：

```js
before: {
  sha256,
  email,
  account_name
},
after: {
  sha256,
  email,
  account_name
}
```

如果需要完整回滚，再实现 `account_versions`。

结论：简化可接受，但更新操作必须有摘要日志。

## 例子 14：远端重复账号正在使用

场景：

```text
上传账号 X
本地没有 X
但 sub2api 缓存中已有同 email 的 active/schedulable 账号
```

如果系统只检查本地重复，会把 X 当新账号导入，并可能后续再次推送。

建议修正：

导入时可先不阻止创建本地账号，但必须标记：

```js
metadata.last_error = "remote active duplicate"
metadata.analysis.remote_duplicate = true
metadata.pool_status = "library"
```

并写：

```text
pool_actions.remote_conflict
todo_items.remote_conflict
```

如果是“更新已有本地账号”，且远端正在使用，则阻止覆盖。

结论：本地重复和远端重复要分开处理。

## 例子 15：验证成功后清理远端失败

场景：

```text
老账号验证成功
写入 verification group
gpt-5.4-mini hi 成功
删除/禁用验证账号失败
```

如果本地直接进入 reserve，但远端验证账号还留着，可能增加封禁风险。

建议修正：

验证结果分两部分：

```js
verification_call_status = succeeded
verification_cleanup_status = failed
```

本地可以进入 `reserve`，但必须写：

```js
metadata.analysis.cleanup_warning = true
metadata.last_error = "verification remote cleanup failed"
```

并生成 `todo_items.remote_conflict` 或 `todo_items.problem_accounts`。

结论：验证成功不等于清理成功，两者要分开记录。

## 推荐的最小修正文档

建议对简化版补充这些最小规则：

### 1. Source of Truth

```text
accounts.metadata.pool_status 是账号当前池状态唯一来源。
pool_actions 只保存历史动作，不参与当前状态判断。
sub2api 缓存是远端观测，不直接覆盖本地状态。
```

### 2. 单池约束

```text
第一版一个账号同一时间只能属于一个本地 api_pool。
如果后续需要多池复用，再引入 pool_memberships。
```

### 3. 允许推送条件

```text
只有 pool_status=reserve 且 pool_id 匹配的账号允许 push-to-active。
```

### 4. 容量统计规则

```text
容量检查必须读取完整 sub2api_accounts_cache。
缺失 5h/7d 字段不能按 0 计算。
备用池数量使用 eligible_reserve_count。
临时限流不立即进入 problem。
```

### 5. 待办去重

```text
todo_items 使用 dedupe_key，未解决的同类待办只更新，不重复创建。
```

### 6. 并发保护

```text
加入备用池、推送实际池、问题退回都必须使用条件更新。
```

## 风险等级汇总

| 风险 | 等级 | 建议 |
| --- | --- | --- |
| library 账号被直接推送 active | P0 | 后端强制只允许 reserve 推送 |
| 缺失容量字段按 0 处理 | P0 | 缺失不参与平均，记录 missing count |
| 待办重复生成 | P0 | 使用 dedupe_key |
| 同账号并发推送 | P0 | 条件更新或 push_lock |
| 只看当前页容量 | P0 | 后端读取完整缓存 |
| sub2api 成功但后端超时 | P1 | 标记 remote_uncertain，刷新缓存确认 |
| 临时限流直接 problem | P1 | 连续异常或无恢复时间再退回 |
| active 本地远端不一致 | P1 | 生成 remote_conflict 待办 |
| 单账号多池需求 | P2 | 第一版接受单池限制 |
| 缺少版本回滚 | P2 | 先写 update 摘要，后续再做版本表 |

## 修正后的简化版核心逻辑

```text
1. 上传账号
   -> accounts.pool_status = library
   -> import_batches 记录批次

2. 人工或验证通过
   -> accounts.pool_status = reserve
   -> pool_id 必须写入

3. 容量检查
   -> 读取完整 sub2api 缓存
   -> 计算 healthy_active_count / avg_5h / avg_7d / eligible_reserve_count
   -> 写 capacity_check action
   -> 更新或创建去重 todo

4. 补位
   -> 只从 reserve 且 pool_id 匹配账号中选
   -> 按 priority、account_type、last_error、updated_at 排序
   -> 生成建议

5. 推送
   -> 条件更新锁定 reserve 账号
   -> 写 sub2api
   -> 成功变 active
   -> 失败保持 reserve，并按错误是否远端不确定决定是否刷新缓存

6. 问题退回
   -> 严重错误直接 problem
   -> 临时限流先观察
   -> 保存 problem_snapshot
   -> 生成去重 todo
```

## 结论

简化版不需要回到复杂状态机，但需要补 6 条实现约束：

1. 明确 `pool_status` 是唯一当前状态。
2. 明确第一版一个账号只能属于一个池。
3. 强制只有 `reserve` 可以推送实际池。
4. 容量计算使用完整缓存、缺失值不按 0、备用池统计只算可用账号。
5. 所有待办用 `dedupe_key` 去重。
6. 推送和状态切换使用条件更新，避免并发重复操作。

这样仍然保持简单，但足够抗住第一版开发中的主要逻辑错误。
