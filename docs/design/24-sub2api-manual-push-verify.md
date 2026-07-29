# sub2api 手动推送与账号可用性测试设计

> **文档状态：已落地流程的早期设计。** 幂等、锁、快照和回写原则仍有参考价值；具体目录、接口和推送后行为以当前 `backend/app/modules/sub2api/*`、router 和测试为准。远端匹配只能使用明确绑定或 `credentials.email`，不能按 `name` 猜测。

本文设计下一阶段的最小闭环：从本地账号库选择一个账号，手动推送到 sub2api 指定分组，然后测试该账号是否可用，最后把远端结果和测试结果写回 MongoDB。

当前目标不是自动补位，而是先做一个安全、可观察、可回滚的手动流程。

## 背景

当前系统已经具备：

- 本地账号库 `accounts`。
- 本地状态 `metadata.pool_status`。
- sub2api groups/accounts MongoDB 缓存。
- API 账号池状态页面。
- 可用池、使用备选池的本地手动流转。
- `pool_actions` 动作记录。

下一步需要补上：

```text
本地账号 -> sub2api 指定 group -> 真实可用性测试 -> 本地数据库标记
```

## 第一版目标

第一版只做手动单账号操作：

1. 在 `可用池` 选择 sub2api 目标分组，把账号加入 `使用备选池`。
2. 系统把目标分组写入账号 `metadata.sub2api_group_id` / `metadata.sub2api_group_name`。
3. 在 `使用备选池` 点击单账号手动推送。
4. 后端只使用账号已经保存的目标 group 创建 sub2api 远端账号。
5. 后端刷新 sub2api 缓存。
6. 后端执行一次可用性测试。
7. 将推送结果、远端账号 ID、group、测试结果写入本地 `accounts.metadata`。
8. 写入 `pool_actions` 和 `audit_logs`。

第一版不做：

- 自动补位。
- 批量推送。
- 自动删除远端账号。
- 自动从问题池重试。
- Agent 判断。

## 状态口径

当前简化生命周期继续保留：

```text
library
available
reserve
active
problem
discarded
```

推送成功后：

```js
metadata.pool_status = "active"
```

这里的 `active` 含义升级为：

```text
本地已确认该账号被创建或绑定到 sub2api 目标实际使用 group。
```

第一版 UI 不再提供从 `reserve` 直接本地标记 `active` 的按钮，避免本地状态和远端 sub2api 状态不一致。已有远端账号的手动绑定后续单独设计。

## 目标分组口径

目标分组必须跟账号走，不能跟页面当前选择走：

```text
available -> reserve 时选择分组 -> 写入账号 metadata -> push-to-sub2api 读取账号 metadata
```

关键规则：

- `可用池` 页面加入 `使用备选池` 前，必须手动选择目标 sub2api 分组。
- 前端不自动默认选择第一个分组，避免误写入 `free01` 等默认分组。
- `使用备选池` 页面每个账号显示自己的目标分组。
- `推送并测试` 只使用该账号已保存的 `metadata.sub2api_group_id`，不会使用页面顶部当前选择。
- 后端允许请求体带 `group_id`，但如果请求 `group_id` 和账号已保存 `metadata.sub2api_group_id` 不一致，直接返回 `409`。
- 如果账号没有保存目标分组，后端拒绝推送，前端提示先从 `可用池` 重新加入 `使用备选池` 并选择分组。
- 推送成功后继续写回 `metadata.pool_id = String(target_group_id)`，保证本地池归属和 sub2api 目标分组一致。

## 推荐新增 metadata 字段

在 `accounts.metadata` 中新增或规范这些字段：

```js
{
  pool_status,
  push_lock,

  sub2api_site_id,
  sub2api_account_id,
  sub2api_group_ids,
  sub2api_group_name,

  sub2api_push_status,
  sub2api_pushed_at,
  sub2api_last_sync_at,
  sub2api_last_error,

  verification_status,
  verification_model,
  verification_prompt,
  verification_response_preview,
  verification_latency_ms,
  verification_checked_at,
  verification_error,

  problem_snapshot,
  analysis
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `push_lock` | 推送锁，防止重复点击或并发推送 |
| `sub2api_site_id` | 当前默认 `default` |
| `sub2api_account_id` | sub2api 创建后的远端账号 ID |
| `sub2api_group_ids` | 远端绑定 group 列表 |
| `sub2api_push_status` | `pending` / `pushing` / `succeeded` / `failed` / `uncertain` |
| `sub2api_pushed_at` | 最近成功推送时间 |
| `sub2api_last_error` | 最近 sub2api 写入错误 |
| `verification_status` | `not_tested` / `testing` / `passed` / `failed` / `skipped` |
| `verification_model` | 例如 `gpt-5.4-mini` |
| `verification_prompt` | 第一版固定为空字符串 `""` |
| `verification_response_preview` | 响应摘要，不保存过长内容 |
| `verification_latency_ms` | 测试请求耗时 |
| `verification_checked_at` | 测试时间 |
| `verification_error` | 测试失败摘要 |
| `problem_snapshot` | 失败时保存远端账号快照 |
| `analysis.remote_uncertain` | sub2api 写入结果不确定时置为 `true` |

## 动作记录

`pool_actions.action_type` 新增：

```text
push_to_sub2api_group
push_to_sub2api_group_failed
verify_sub2api_account
verify_sub2api_account_failed
remote_duplicate_bound
remote_state_uncertain
```

每次推送至少写两段信息：

```js
before: {
  pool_status,
  sub2api_account_id,
  sub2api_group_ids,
  sha256,
  email
}

after: {
  pool_status,
  sub2api_account_id,
  sub2api_group_ids,
  push_status,
  verification_status
}
```

失败时：

```js
status = "failed"
error = error_summary
remote_snapshot = optional_remote_account
```

## sub2api 创建账号 payload

本地 `account_json` 不直接修改。发送给 sub2api 时临时构造 payload：

```js
{
  ...account_json,
  group_id: target_group_id,
  group_ids: [target_group_id],
  concurrency: 10,
  load_factor: 10,
  priority: 100,
  status: "active",
  schedulable: true,
  confirm_mixed_channel_risk: true
}
```

从本地 JSON 保留：

```text
name
platform
type
credentials
extra
concurrency
load_factor
priority
rate_multiplier
auto_pause_on_expired
expires_at
```

发送前移除不应复制的远端字段：

```text
id
created_at
updated_at
last_used_at
error_message
credentials_status
current_concurrency
rate_limited_at
rate_limit_reset_at
overload_until
temp_unschedulable_until
temp_unschedulable_reason
session_window_start
session_window_end
session_window_status
account_groups
groups
group
group_id
group_ids
```

说明：

- 发送前必须清理账号 JSON 中可能残留的旧 `group_id` / `group_ids`。
- 创建 payload 同时写入 `group_id` 和 `group_ids`，避免 sub2api 读取单值字段时落到旧分组。
- 第一版推送参数默认：`concurrency=10`、`load_factor=10`、`priority=100`。
- 远端创建后必须刷新缓存并校验远端账号确实属于目标 group；如果不属于目标 group，不能把本地账号标记为 `active`。

第一版接口：

```text
POST /api/v1/admin/accounts
```

成功后保存返回的远端账号 ID。

## 推送前校验

后端接口只允许这些来源：

```text
available
reserve
problem
```

建议第一版默认允许：

```text
reserve -> active
available -> active
problem -> active
```

但 UI 上建议优先从 `使用备选池` 页面推送。`problem -> active` 必须二次确认或单独按钮。

禁止：

```text
discarded -> active
active -> active 重复创建
library -> active
```

如果需要从 `library` 直接推送，应先人工移入 `available` 或 `reserve`。

推送前必须检查：

1. 本地账号未删除。
2. 本地账号存在 `credentials`。
3. 账号已保存目标分组，或请求体提供目标分组。
4. 如果账号已保存目标分组，请求体 `group_id` 必须为空或与保存值一致。
5. 目标 sub2api group 存在于缓存。
6. 账号没有未释放的 `push_lock`。
7. 如果已有 `metadata.sub2api_account_id`，先检查远端状态。
8. 如果缓存中同一规范化 `credentials.email` 已在目标 group，优先绑定已有远端账号，而不是重复创建。

## 幂等与重复处理

重复判断只允许：

1. 同一站点下已保存的 `metadata.sub2api_account_id`。
2. 规范化后的 `account_json.credentials.email`。

`name`、`chatgpt_account_id`、`account_json.extra.email` 和上传批次摘要都不能作为自动绑定依据。缺少 `credentials.email` 且没有明确远端绑定时，转人工处理。

处理规则：

### 情况 1：本地已有 `sub2api_account_id`

如果远端账号仍存在，并且已经在目标 group：

```text
不重复创建。
只刷新本地 metadata。
可继续执行可用性测试。
```

如果远端账号存在，但不在目标 group：

```text
第一版不自动更新 group。
返回错误：远端账号存在但未绑定目标 group。
后续再设计 PUT /accounts/{id} 绑定 group。
```

### 情况 2：缓存中发现同账号已在目标 group

```text
不重复创建。
绑定远端账号 ID。
写 pool_actions.remote_duplicate_bound。
继续执行可用性测试。
```

### 情况 3：缓存中发现同账号在其他 active group

```text
不自动创建。
标记 remote_conflict。
写 last_error。
提示人工确认。
```

### 情况 4：没有重复

```text
正常 POST /admin/accounts 创建。
```

## 推送锁

必须用 MongoDB 条件更新抢锁：

```js
findOneAndUpdate(
  {
    _id: account_id,
    "metadata.deleted_at": { "$exists": false },
    "metadata.pool_status": { "$in": ["available", "reserve", "problem"] },
    "metadata.push_lock": { "$exists": false }
  },
  {
    "$set": {
      "metadata.push_lock": {
        action_id,
        locked_at,
        locked_by_user_id,
        target_group_id
      },
      "metadata.sub2api_push_status": "pushing"
    }
  }
)
```

成功或失败后都要释放：

```js
"$unset": { "metadata.push_lock": "" }
```

如果 sub2api 请求超时或连接断开，不能直接当失败重试，应标记：

```js
metadata.sub2api_push_status = "uncertain"
metadata.analysis.remote_uncertain = true
metadata.sub2api_last_error = "remote state uncertain"
```

然后刷新缓存，尝试按明确远端绑定或规范化后的 `credentials.email` 查找远端账号。

## 可用性测试设计

第一版目标是最简单地确认：

```text
这个账号被 sub2api 调度后，能否完成一次模型请求。
```

测试参数：

```text
model = gpt-5.4-mini
prompt = ""
timeout = 60s
```

已确认 sub2api Admin API 提供账号测试接口：

```text
POST /api/v1/admin/accounts/{remote_account_id}/test
```

请求体：

```json
{
  "model_id": "gpt-5.4-mini",
  "prompt": ""
}
```

响应是 SSE 风格文本流：

```text
data: {"type":"test_start","model":"gpt-5.4-mini"}
data: {"type":"content","text":"Hi"}
data: {"type":"content","text":"!"}
data: {"type":"test_complete","success":true}
```

后端解析规则：

- 只处理以 `data:` 开头的行。
- JSON `type=content` 的 `text` 拼接为 `verification_response_preview`。
- 最后出现 `type=test_complete` 且 `success=true`，视为测试通过。
- 测试接口 HTTP 错误或 `success=false`，视为测试失败。

当前不需要额外 `SUB2API_TEST_API_KEY`，复用 Admin API 的 `x-api-key`。

注意：用户给出的测试 URL 示例端口是 `5001`，当前项目仍以 `.env` 的 `SUB2API_BASE_URL` 为准，不在代码里硬编码端口。

## 测试结果标记

成功：

```js
metadata.pool_status = "active"
metadata.sub2api_push_status = "succeeded"
metadata.verification_status = "passed"
metadata.verification_checked_at = now
metadata.verification_model = "gpt-5.4-mini"
metadata.verification_prompt = ""
metadata.verification_response_preview = "<截断响应>"
metadata.verification_latency_ms = 1234
metadata.last_error = null
metadata.sub2api_last_error = null
```

模型请求失败：

```js
metadata.pool_status = "problem"
metadata.sub2api_push_status = "succeeded"
metadata.verification_status = "failed"
metadata.verification_error = error_summary
metadata.last_error = error_summary
metadata.problem_snapshot = remote_account_snapshot
```

手动跳过测试：

```js
metadata.pool_status = "active"
metadata.sub2api_push_status = "succeeded"
metadata.verification_status = "skipped"
metadata.verification_error = null
```

sub2api 创建失败：

```js
metadata.pool_status = 原状态
metadata.sub2api_push_status = "failed"
metadata.sub2api_last_error = error_summary
metadata.last_error = error_summary
```

sub2api 创建结果不确定：

```js
metadata.pool_status = 原状态
metadata.sub2api_push_status = "uncertain"
metadata.analysis.remote_uncertain = true
metadata.last_error = "push remote state unknown"
```

## 后端接口设计

### 手动推送并测试

```text
POST /api/accounts/{account_id}/push-to-sub2api
```

请求：

```json
{
  "site_id": "default",
  "group_id": 3,
  "run_verification": true,
  "reason": "manual push",
  "concurrency": 10,
  "load_factor": 10,
  "priority": 100
}
```

`group_id` 第一版由前端从账号 `metadata.sub2api_group_id` 读取后传入；后端仍以账号保存的目标分组为权威值。若两者不一致，拒绝推送，防止页面当前选择把账号推到错误分组。

返回：

```json
{
  "account": {},
  "remote_account": {},
  "push_action": {},
  "verification": {
    "status": "passed",
    "model": "gpt-5.4-mini",
    "latency_ms": 1234,
    "response_preview": "Hi!"
  }
}
```

### 单独测试已推送账号

```text
POST /api/accounts/{account_id}/verify-sub2api
```

请求：

```json
{
  "site_id": "default",
  "group_id": 3,
  "model": "gpt-5.4-mini",
  "prompt": ""
}
```

用途：

- 推送后测试失败，可以调整配置后重新测试。
- 已经存在远端账号时，只做可用性测试，不重复创建。

### 刷新并绑定远端状态

```text
POST /api/accounts/{account_id}/refresh-sub2api-binding
```

用途：

- 推送结果不确定时刷新缓存。
- 从缓存中按明确绑定或规范化后的 `credentials.email` 查找远端账号。
- 找到后补写 `sub2api_account_id` 和 group。

第一版可以先只做 `push-to-sub2api`，后两个作为后续补充。

## 后端服务拆分

建议新增：

```text
backend/app/modules/sub2api/push.py
```

职责：

- 构造 sub2api account payload。
- 远端重复检查。
- 抢占和释放 `push_lock`。
- POST `/admin/accounts` 创建账号。
- 刷新 sub2api 缓存。
- 写回 `accounts.metadata`。
- 写 `pool_actions`。

建议扩展：

```text
backend/app/modules/sub2api/client.py
```

新增方法：

```python
create_account(payload)
get_account(account_id)
```

建议新增：

```text
backend/app/modules/sub2api/verify.py
```

职责：

- 调用 sub2api 模型接口或 Admin 测试接口。
- 记录耗时。
- 截断响应。
- 规范化错误。

路由：

```text
backend/app/routers/accounts.py
```

新增：

```text
POST /accounts/{account_id}/push-to-sub2api
POST /accounts/{account_id}/verify-sub2api
```

## 前端入口设计

第一版优先放在 `使用备选池` 页面：

- 每行新增按钮：`推送并测试`。
- 批量推送先不做。
- `使用备选池` 页面不复用顶部 group 选择器决定推送目标。
- 推送目标来自账号在 `可用池 -> 使用备选池` 时保存的目标分组。
- 如果账号没有目标分组，按钮禁用或点击后提示先退回可用池重新选择分组。
- 点击后弹窗确认：

```text
将账号推送到 sub2api 分组：{group_name} #{group_id}
并发：10
负载因子：10
优先级：100
推送成功后会执行 gpt-5.4-mini 测试，并写入本地 active 状态。
```

账号列表页可以后续再加入口。

按钮可见规则：

| 本地状态 | 操作 |
| --- | --- |
| `reserve` | 有目标分组时显示 `推送并测试` |
| `available` | 可显示，但需要二次确认 |
| `problem` | 可显示 `重新推送`，需要二次确认 |
| `active` | 显示 `重新测试`，不显示推送 |
| `library` | 不显示 |
| `discarded` | 不显示 |

## 最小开发顺序

1. 扩展 `Sub2ApiClient.create_account()`。
2. 新增 `sub2api_push.py`。
3. 实现单账号 `push-to-sub2api`，先不测试，只确认远端创建和本地标记。
4. 推送成功后刷新 sub2api 缓存。
5. 从缓存或返回值保存：
   - `sub2api_account_id`
   - `sub2api_group_ids`
   - `sub2api_group_name`
   - `sub2api_pushed_at`
6. 写 `pool_actions.push_to_sub2api_group`。
7. 在推送接口后半段调用 `POST /admin/accounts/{id}/test`。
8. 解析 SSE 风格 `data:` 响应。
9. 测试成功标记 `verification_status = passed`。
10. 测试失败标记 `problem`，保存错误和快照。
11. 前端 `使用备选池` 加单账号按钮。
12. 前端显示推送结果和测试结果。

## 验收场景

### 场景 1：reserve 账号推送成功并测试通过

预期：

- sub2api 目标 group 中出现新账号。
- 本地写入 `metadata.sub2api_account_id`。
- 本地 `pool_status = active`。
- `verification_status = passed`。
- `pool_actions` 有推送和测试记录。

### 场景 2：重复点击推送

预期：

- 第二个请求拿不到 `push_lock` 或发现已有远端绑定。
- 不重复创建远端账号。

### 场景 3：远端已存在同账号

预期：

- 如果在目标 group，绑定远端账号，不重复创建。
- 如果在其他 active group，阻止推送，标记冲突。

### 场景 4：sub2api 创建成功但测试失败

预期：

- 本地记录远端账号 ID。
- `verification_status = failed`。
- `pool_status = problem`。
- 保存 `problem_snapshot`。

### 场景 5：sub2api 请求超时

预期：

- `sub2api_push_status = uncertain`。
- `analysis.remote_uncertain = true`。
- 不允许立即重复创建。

### 场景 6：手动跳过测试

预期：

- 推送仍可成功。
- `verification_status = skipped`。
- UI 明确提示测试未执行。

## 必须确认的问题

剩余需要确认：

1. 推送目标 group 是实际使用池，还是先推到 verification group 测试，通过后再更新到实际使用 group？
2. 测试失败后，远端账号是否需要立刻删除、禁用，还是只标记本地 problem？

建议第一版先采用保守策略：

```text
先推送到用户选择的目标 group。
测试失败不自动删除远端账号。
本地标记 problem，并提示人工处理远端。
```
