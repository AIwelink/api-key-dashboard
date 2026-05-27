# 账号池开发优先级与任务顺序

本文档基于 `19-account-pool-final-simple-design.md`，用于指导账号池后端和前端的实际开发顺序。

核心判断：

```text
先开发本地状态和判断能力，再开发写入 sub2api 的动作。
```

也就是先让系统知道：

- 每个账号现在在哪里。
- 哪些账号可以进入备用池。
- 哪个实际使用池缺账号。
- 备用池是否足够。
- 应该优先推荐哪些账号。
- 哪些问题需要团队处理。

然后再开发：

- 推送到 sub2api 实际使用池。
- 老账号验证。
- 自动化执行。

## 优先级结论

### 第一优先级：本地基础状态

内容：

```text
账号 metadata 基础字段
import_batches
api_pools
pool_actions
todo_items
```

原因：

- 后续所有池化逻辑都依赖这些数据。
- 先把本地状态和日志打稳，可以避免后续反复返工。
- 这些功能没有远端副作用，风险低。

### 第二优先级：判断和推荐

内容：

```text
加入备用池
容量检查
备用池可用数量
补位建议
整体待办
```

原因：

- 这是系统最早能产生价值的部分。
- 即使暂时不自动推送 sub2api，也能指导人工操作。
- 这一步可以验证阈值、筛选和状态设计是否合理。

### 第三优先级：sub2api 写操作

内容：

```text
push-to-active
verify
问题账号退回后的远端处理
```

原因：

- 这些操作有远端副作用。
- 必须先有 `push_lock`、`pool_actions`、`todo_items` 和本地状态保护。
- 否则容易出现重复推送、推送失败后状态错乱、验证账号残留等问题。

### 第四优先级：前端可视化和手动操作

内容：

```text
账号列表扩展
池配置页面
容量检查页面
待办页面
手动推送/验证按钮
```

原因：

- 前端要依赖后端接口稳定。
- 可以先在现有账号列表和 API 账号池状态页上做轻量入口，再做独立页面。

## 推荐第一轮开发范围

第一轮建议只做 1 到 8：

```text
1. metadata 基础字段
2. 基础集合和索引
3. 批次导入
4. api_pools 配置
5. pool_actions 和 todo_items 基础服务
6. 加入备用池
7. 容量检查
8. 补位建议
```

第一轮完成后，系统应能回答：

```text
当前每个池缺不缺账号？
备用池够不够？
应该优先补哪些账号？
哪些问题需要团队处理？
```

第一轮不做：

```text
真实推送 sub2api
老账号验证
自动补位
复杂审批流
按人分配待办
agent 自动判断
```

## 任务顺序清单

### 1. 账号 metadata 基础字段

目标：

统一账号池状态字段，让所有后续逻辑有唯一当前状态来源。

字段：

```js
metadata.pool_status
metadata.pool_id
metadata.priority
metadata.upload_intent
metadata.push_lock
metadata.problem_snapshot
metadata.analysis
```

默认值：

```js
metadata.pool_status = "library"
metadata.priority = 0
metadata.analysis = {}
```

关键规则：

- `pool_status` 是当前状态唯一来源。
- `pool_actions` 只保存历史动作。
- sub2api 缓存只代表远端观测状态，不直接覆盖本地状态。

验收：

- 新上传账号默认 `pool_status = library`。
- 账号列表能按 `pool_status` 查询。
- 不存在 `pool_status` 的旧账号读取时按 `library` 处理。

### 2. 基础集合和索引

新增集合：

```text
import_batches
api_pools
pool_actions
todo_items
```

建议索引：

```text
accounts.metadata.pool_status
accounts.metadata.pool_id
accounts.metadata.priority
accounts.metadata.upload_intent
accounts.metadata.sub2api_account_id
accounts.metadata.push_lock

import_batches.created_at
import_batches.uploaded_by_user_id
import_batches.status

api_pools.status
api_pools.site_id
api_pools.active_group_id

pool_actions.account_id
pool_actions.pool_id
pool_actions.action_type
pool_actions.created_at

todo_items.dedupe_key + status
todo_items.pool_id
todo_items.todo_type
```

验收：

- 后端启动时自动创建索引。
- 重复执行索引初始化不会报错。

### 3. 批次导入

目标：

将上传动作沉淀为批次，让账号来源可追溯。

接口：

```text
POST /api/import-batches
GET  /api/import-batches
GET  /api/import-batches/{id}
```

`upload_intent`：

```text
new
renew
purchase
historical
known_error
```

规则：

- 新账号默认只进入总库。
- 不写 sub2api。
- 不进入备用池。
- 不自动验证。
- 批次只保存摘要和统计，第一版不保存完整原始 payload。

验收：

- 上传新批次后生成 `import_batches`。
- 每个账号写入 `accounts.metadata.batch_id`。
- 每个账号默认 `pool_status = library`。
- 批次统计能显示总数、新增数、更新数、错误数。

### 4. api_pools 配置

目标：

建立本地池和 sub2api group 的映射。

接口：

```text
GET   /api/api-pools
POST  /api/api-pools
PATCH /api/api-pools/{id}
```

核心字段：

```js
{
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
  status
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

验收：

- 能创建 plus/free 本地池。
- 能映射到现有 sub2api group。
- 容量检查可以读取 pool 配置。

### 5. pool_actions 和 todo_items 基础服务

目标：

所有关键动作有记录，所有团队问题有待办。

`pool_actions` 用于记录：

```text
import_account
update_account
enter_reserve
capacity_check
remote_conflict
```

第一轮暂不做：

```text
push_to_active
verify_account
return_problem
```

`todo_items` 必须支持去重。

去重键：

```text
dedupe_key = "{todo_type}:{pool_id}"
```

规则：

- 如果同一 `dedupe_key` 已有 `open` 待办，更新原待办。
- 不重复创建。
- 更新 `occurrence_count` 和 `updated_at`。

验收：

- 连续运行容量检查不会生成重复待办。
- `pool_actions` 可按账号和池查询。

### 6. 加入备用池

目标：

允许人工把总库账号或问题账号放入备用池。

接口：

```text
POST /api/accounts/{id}/enter-reserve
```

允许来源：

```text
library
problem
```

更新：

```js
metadata.pool_status = "reserve"
metadata.pool_id = pool_id
metadata.priority = priority
metadata.last_error = null
```

必须使用条件更新：

```js
{
  _id: account_id,
  "metadata.pool_status": { "$in": ["library", "problem"] }
}
```

验收：

- `library -> reserve` 成功。
- `problem -> reserve` 成功。
- `active -> reserve` 不允许通过这个接口执行。
- 写入 `pool_actions.enter_reserve`。

### 7. 容量检查

目标：

读取完整 sub2api 缓存，判断实际使用池是否健康。

接口：

```text
POST /api/api-pools/{id}/capacity-check
```

只读取：

```text
sub2api_accounts_cache
accounts
api_pools
```

不请求远端 sub2api。

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

关键规则：

- 不能用前端当前页数据。
- 必须读取完整 group 缓存。
- 5h/7d 缺失值不按 0 计算。
- 缺失比例超过 30%，生成 `capacity_data_incomplete`。
- 平均值超过阈值，生成 `need_more_accounts`。
- 高用量账号比例过高，生成 `need_more_accounts`。
- 备用池不足，生成 `reserve_low`。

验收：

- group 有 500 个账号时，容量检查统计 500 个，不是当前页 50 个。
- 缺失 5h/7d 字段不会拉低平均值。
- 待办不会重复生成。

### 8. 补位建议

目标：

根据容量检查结果，从备用池推荐账号。

可用备用账号条件：

```text
pool_status == reserve
pool_id == 当前池
account_type 匹配
last_error 为空
push_lock 不存在
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

规则：

- 第一轮只返回建议。
- 不自动推送 sub2api。
- 不改变账号状态。

验收：

- 推荐账号全部来自当前池备用账号。
- `last_error` 不为空的账号不被推荐。
- 高 priority 账号排在前面。

## 第二轮任务

第二轮开始接触 sub2api 写操作。

### 9. 问题账号退回

目标：

根据 sub2api 缓存发现 active 账号异常，并退回本地问题状态。

先实现严重错误：

```text
error
disabled
banned
invalid
failed
schedulable=false 且无恢复时间
```

暂不立即处理：

```text
rate_limited_at
temp_unschedulable_until
rate_limit_reset_at 在未来
```

更新：

```js
metadata.pool_status = "problem"
metadata.last_error = error_summary
metadata.problem_snapshot = remote_snapshot
```

验收：

- 严重错误进入 `problem`。
- 临时限流只生成观察待办，不立刻 `problem`。
- 保存 `problem_snapshot`。

### 10. 推送到实际使用池

目标：

将备用账号写入 sub2api 实际使用 group。

接口：

```text
POST /api/accounts/{id}/push-to-active
```

只允许：

```text
pool_status == reserve
pool_id == target_pool_id
```

必须使用：

```text
metadata.push_lock
```

成功：

```js
metadata.pool_status = "active"
metadata.sub2api_account_id = remote_id
metadata.sub2api_group_ids = [active_group_id]
metadata.push_lock = null
metadata.last_error = null
```

失败：

```js
metadata.pool_status = "reserve"
metadata.last_error = error_summary
metadata.push_lock = null
```

远端不确定：

```js
metadata.analysis.remote_uncertain = true
```

并触发缓存刷新确认。

验收：

- `library` 账号不能推送。
- 并发推送只有一个请求拿到锁。
- sub2api 超时不会导致重复推送。

### 11. 老账号验证

目标：

验证历史账号、问题账号、renew 账号是否可用。

接口：

```text
POST /api/accounts/{id}/verify
```

流程：

1. 写入 verification group。
2. 用 `gpt-5.4-mini` 发送 `hi`。
3. 记录结果。
4. 清理远端验证账号。
5. 成功进入 `reserve`。
6. 失败进入 `problem`。

验收：

- 验证成功后远端账号被清理。
- 清理失败时记录 `analysis.cleanup_warning = true`。
- 验证失败写 `last_error`。

## 第三轮任务

第三轮主要是前端和自动化。

### 12. 前端账号列表扩展

增加显示：

```text
pool_status
pool_id
priority
last_error
```

增加操作：

```text
加入备用池
容量检查入口
推送到实际池
验证账号
```

### 13. API 池配置页面

管理：

```text
api_pools
阈值
active_group_id
verification_group_id
```

### 14. 容量检查页面

展示：

```text
healthy_active_count
avg_5h_used_observed
avg_7d_used_observed
eligible_reserve_count
补位建议
```

### 15. 待办页面

展示：

```text
need_more_accounts
reserve_low
capacity_data_incomplete
problem_accounts
remote_conflict
```

第一版仍不按人分配。

## 不建议提前做的事情

这些功能先不要做：

```text
自动补位定时任务
复杂审批流
账号多池关系
完整版本回滚
agent 自动执行动作
按人分配待办
Redis 队列
WebSocket 实时推送
```

原因：

- 当前核心问题是先把本地状态和判断跑通。
- 这些功能会引入更多状态和边界。
- 过早开发会掩盖基础逻辑问题。

## 里程碑

### Milestone 1：本地状态可用

完成：

```text
metadata pool fields
import_batches
api_pools
pool_actions
todo_items
enter-reserve
```

结果：

```text
账号可以从总库进入备用池。
批次可追溯。
动作有记录。
待办可去重。
```

### Milestone 2：系统能判断缺口

完成：

```text
capacity-check
eligible reserve count
replacement suggestion
capacity todo
```

结果：

```text
系统能告诉我们哪个池缺账号、缺多少、推荐哪些备用账号。
```

### Milestone 3：系统能安全写 sub2api

完成：

```text
push-to-active
push_lock
remote_uncertain recovery
problem return
```

结果：

```text
备用账号可以安全进入实际使用池。
问题账号可以退回本地。
```

### Milestone 4：老账号验证

完成：

```text
verification group
gpt-5.4-mini hi
remote cleanup
verify success/failure
```

结果：

```text
历史账号和问题账号可以被验证后重新进入备用池。
```

### Milestone 5：前端操作闭环

完成：

```text
账号池配置页面
容量检查页面
待办页面
账号列表操作按钮
```

结果：

```text
团队可以在页面上完成账号池维护闭环。
```

## 最终建议

最推荐先开发：

```text
Milestone 1 + Milestone 2
```

也就是：

```text
本地状态
批次
池配置
动作日志
待办去重
加入备用池
容量检查
补位建议
```

这部分没有远端写入风险，但能立刻验证设计是否有用。等它稳定后，再开发 `push-to-active` 和 `verify`。
