# 远端 sub2api UI 与账号验证分组设计

本文定义 `API 账号池状态` 页面的完整远端 UI 设计，以及专用账号验证分组的后端逻辑。当前环境是专门测试环境，可以开放更完整的远端操作，但所有远端写操作仍必须保存本地快照、动作记录和审计日志。

## 核心结论

- `API 账号池状态` 不再只是观测页面，而是“远端 sub2api 测试控制台”。
- 远端 UI 允许手动测试、删除、回退、本地落库、刷新缓存等操作。
- 账号验证必须使用一个专用 sub2api 分组，称为 `verification group`。
- `verification group` 只用于测试账号可用性，不作为实际使用池，不参与容量补位。
- 后续自动测试、批量验证、问题账号复测都复用同一个 `verification group`。
- 实际使用分组和验证分组必须分离，避免测试账号误进入生产/实际调用池。

## 页面定位

菜单：`API 账号池状态`

页面职责升级为：

```text
远端状态观察 + 远端测试操作 + 远端账号回退入口
```

它仍然读取统一 MongoDB 缓存：

```text
sub2api -> refresh_site_cache -> sub2api_groups_cache / sub2api_accounts_cache / sub2api_cache_meta
```

但允许通过明确按钮触发远端写操作：

- 刷新远端缓存。
- 测试 sub2api 连接。
- 单账号远端测试。
- 单账号从 sub2api 删除并回退本地。
- 单账号写入本地总库。
- 后续：远端账号参数更新、启停调度、分组迁移、批量预览。

## 远端 UI 总体布局

建议页面分为 5 个区域：

```text
站点工具栏
分组列表 / 分组角色
分组容量摘要
远端账号表格
操作结果 / 审计提示
```

### 1. 站点工具栏

字段和操作：

- 当前站点。
- 远端 base URL。
- token 是否配置。
- 后台刷新间隔。
- 最后同步时间。
- `测试连接`。
- `同步账号池数据`。
- `前端数据刷新`。

说明：

- `同步账号池数据` 会访问远端 sub2api 并写入 MongoDB 缓存。
- `前端数据刷新` 只重新读取本地缓存，不访问远端。
- 任意远端写操作成功后，应自动触发一次缓存刷新。

### 2. 分组列表 / 分组角色

分组来自 `sub2api_groups_cache`。

每个分组展示：

- group ID。
- group name。
- status。
- account_count。
- active_account_count。
- rate_limited_account_count。
- 5h 总体容量。
- 7d 总体容量。
- 本地角色标记。

本地角色建议：

```text
active_pool      实际使用分组
verification     专用验证分组
reserve_view      仅展示/临时观察
ignore            不参与本系统策略
unknown           未标记
```

第一版可以先不做角色编辑，只在设计上预留。后续建议新增集合：

```js
sub2api_group_configs {
  _id: "{site_id}:{group_id}",
  site_id,
  group_id,
  group_name,
  role: "active_pool" | "verification" | "reserve_view" | "ignore" | "unknown",
  account_type: "plus" | "free" | "pro" | "other",
  remark,
  created_at,
  updated_at,
  updated_by_user_id,
  updated_by_name
}
```

如果一个分组被标记为 `verification`：

- 前端显示醒目的“验证分组”标签。
- 容量摘要可以展示，但不进入实际容量补位判断。
- 自动化测试任务只允许写入这个分组。

### 3. 分组容量摘要

继续使用后端写入的 `sub2api_groups_cache.capacity_summary`。

注意：

- 当前页面分页账号不能用于总体容量计算。
- `verification group` 的容量只用于观察，不用于“实际可用容量”。
- 429/529 限流账号仍可按正常账号计入总体容量，但用量窗口要如实展示。

### 4. 远端账号表格

建议列：

| 列 | 说明 |
| --- | --- |
| 选择 | 后续批量预览用，第一版远端删除不批量执行 |
| 名称 | 远端 name、email、远端 ID |
| 平台/标签 | OpenAI、Plus/Free/Pro、Private 等标签，不显示 OAuth |
| 容量 | current_concurrency / concurrency、load_factor |
| 状态 | active/error/disabled/paused、error_message、health |
| 调度 | schedulable、priority |
| 分组 | 当前远端 group |
| 用量窗口 | 5h、7d |
| 最近使用 | last_used_at、限流时间 |
| 过期时间 | expires_at |
| 操作 | 测试、手动删除、退回总库、后续更多 |

当前已实现：

- `手动删除`：删除远端账号，写入本地库，退回 `available`。
- `退回总库`：删除远端账号，写入本地库，退回 `library`。
- `测试`：调用 sub2api account test API，只测试远端账号，不改变分组。
- `POST /api/accounts/{account_id}/verify-via-sub2api`：本地账号写入 verification group 测试并清理远端临时账号。

建议继续补：

- `写入本地`：只把远端账号快照写入本地库，不删除远端。
- `查看快照`：查看缓存中的完整远端 JSON。
- `更新参数`：并发、负载因子、priority、schedulable，先做弹窗确认。

## 专用验证分组

账号验证分组是一个独立的 sub2api group，例如：

```text
账号验证 / verification / test
```

它的唯一职责：

```text
临时接收待验证账号 -> 发起模型测试 -> 记录结果 -> 清理远端验证账号
```

它不是：

- 实际使用池。
- 备用池。
- 容量补位目标。
- 长期存放账号的地方。

## 账号验证流程

### 手动验证

适用来源：

```text
library
available
reserve
problem
```

推荐入口：

- 账号列表：验证单个本地账号。
- 可用池：验证后再加入使用备选池。
- 问题账号/待办：复测旧账号或错误账号。
- API 账号池状态：测试已经在远端的账号。

流程：

```text
选择本地账号
-> 读取 verification group
-> 构造 sub2api payload
-> 写入 verification group
-> 调用 /accounts/{remote_id}/test
-> 保存测试结果
-> 删除 verification group 中的临时远端账号
-> 更新本地账号 metadata
```

成功：

```js
metadata.verification_status = "passed"
metadata.verification_checked_at = now
metadata.verification_group_id = verification_group_id
metadata.verification_remote_account_id = remote_id
metadata.verification_response_preview = "Hi!"
metadata.verification_latency_ms = 1234
metadata.verification_error = null
metadata.last_error = null
```

失败：

```js
metadata.verification_status = "failed"
metadata.verification_checked_at = now
metadata.verification_group_id = verification_group_id
metadata.verification_remote_account_id = remote_id
metadata.verification_error = error_summary
metadata.last_error = error_summary
metadata.problem_snapshot = remote_snapshot
```

清理失败：

```js
metadata.verification_cleanup_status = "failed"
metadata.verification_cleanup_error = error_summary
metadata.analysis.verification_remote_leftover = true
```

### 自动验证

后续自动测试任务复用同一条逻辑：

```text
筛选待验证账号
-> 加 verification_lock
-> 写入 verification group
-> test
-> 删除临时远端账号
-> 释放锁
-> 写 account_events / pool_actions
```

自动测试只做判断，不自动进入实际使用池。进入使用池仍由手动确认或后续明确自动补位规则执行。

## 验证与实际推送的关系

必须区分两个动作：

### 1. 验证账号

目标 group：

```text
verification group
```

结果：

```text
只更新本地验证状态，不代表账号已经进入实际使用池。
```

### 2. 推送到实际使用池

目标 group：

```text
账号在可用池 -> 使用备选池时保存的目标分组
```

结果：

```text
创建/绑定到实际 sub2api group，成功后 metadata.pool_status = active。
```

推荐规则：

- 新账号上传后先在本地总库。
- 人工确认后进入可用池。
- 可选：先走 verification group 验证。
- 验证通过后进入使用备选池。
- 最后手动推送到实际使用分组。

## 状态字段

建议在 `accounts.metadata` 统一增加/规范：

```js
{
  verification_status: "not_tested" | "testing" | "passed" | "failed" | "skipped",
  verification_checked_at,
  verification_model,
  verification_prompt,
  verification_response_preview,
  verification_latency_ms,
  verification_error,

  verification_group_id,
  verification_group_name,
  verification_remote_account_id,
  verification_remote_snapshot,
  verification_cleanup_status: "not_needed" | "pending" | "succeeded" | "failed",
  verification_cleanup_error,

  verification_lock: {
    action_id,
    locked_at,
    locked_by_user_id,
    expires_at
  }
}
```

动作记录：

```text
verify_account_started
verify_account_pushed_to_verification_group
verify_account_test_passed
verify_account_test_failed
verify_account_cleanup_succeeded
verify_account_cleanup_failed
```

## 后端接口设计

第一阶段建议新增：

```text
POST /api/accounts/{account_id}/verify-via-sub2api
```

请求：

```json
{
  "site_id": "default",
  "verification_group_id": 12,
  "model_id": "gpt-5.4-mini",
  "prompt": "",
  "cleanup_remote": true,
  "reason": "manual verification"
}
```

返回：

```json
{
  "account": {},
  "remote_account": {},
  "verification": {
    "status": "passed",
    "model": "gpt-5.4-mini",
    "latency_ms": 1234,
    "response_preview": "Hi!"
  },
  "cleanup": {
    "status": "succeeded"
  }
}
```

远端账号表格的直接测试：

```text
POST /api/sub2api-sites/{site_id}/accounts/{remote_account_id}/test
```

这只测试已有远端账号，不创建临时账号，不删除账号。

## 安全与防误操作

即使当前是测试环境，也保留以下规则：

- 所有远端写操作必须有确认弹窗。
- 所有远端写操作必须进入 `audit_logs`。
- 所有远端写操作必须进入 `pool_actions` 或对应事件表。
- 删除远端账号前必须先写本地快照。
- 验证临时账号必须尽量清理；清理失败要显式标记。
- 批量远端删除必须先做预览，不在第一版直接开放。

## 你可能忽略的点

1. **验证分组容量不应进入实际容量判断。** 否则测试账号会污染容量统计。
2. **验证成功不等于可长期使用。** 它只证明当前 JSON 能完成一次模型请求。
3. **验证临时远端账号可能清理失败。** 必须记录 leftover，后续页面提供清理入口。
4. **远端-only 账号需要先落本地。** 不然删除后会丢失账号 JSON。
5. **重复账号匹配要谨慎。** `chatgpt_account_id` 优先，其次 email/name，避免误覆盖。
6. **测试会消耗额度和用量窗口。** 自动测试需要频控和并发上限。
7. **429/529 是容量/限流信号，不一定是账号坏。** 不应直接标记异常。
8. **实际推送和验证推送要用不同接口。** 避免把测试 group 当成实际使用 group。

## 实施顺序

1. 已完成远端 UI 设计文档和菜单口径同步。
2. 已增加远端账号 `测试` 按钮，只测试已有远端账号。
3. 已增加本地账号 `verify-via-sub2api` 接口，写入 verification group 测试并清理。
4. 下一步增加 verification group 本地配置。
5. 在账号列表/可用池/问题账号页增加 `验证账号` 按钮。
6. 增加自动验证任务，但只做建议和状态更新，不自动推送实际使用池。
7. 增加清理 verification leftover 的页面操作。
