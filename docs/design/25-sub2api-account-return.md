# sub2api 账号手动删除与回退设计

本文记录第一版账号回退逻辑：在 `API 账号池状态` 页面从 sub2api 删除远端账号，并把账号快照写入本地库。

## 目标

- 支持单个远端账号手动删除。
- 删除前先把远端账号快照写入本地 `accounts`。
- 正常账号手动删除后默认退回 `available`，也就是本地可用池。
- 需要彻底退出使用流程时，可以选择退回 `library`，也就是本地总库。
- 记录是否异常、测试状态、测试时间、删除时间、删除人和远端快照。
- 为后续“异常账号自动删除并退回问题库”复用同一套字段。

## 页面入口

位置：`API 账号池状态` 的远端账号表格。

每行新增两个按钮：

- `手动删除`：从 sub2api 删除，写入本地库，`metadata.pool_status = available`。
- `退回总库`：从 sub2api 删除，写入本地库，`metadata.pool_status = library`。

第一版只做单账号操作，不做批量删除。批量删除风险高，后续需要二次确认、操作摘要和失败回滚提示。

## 后端接口

```text
POST /api/sub2api-sites/{site_id}/accounts/{account_id}/manual-delete
```

请求：

```json
{
  "target_status": "available",
  "reason": "手动删除并退回可用池"
}
```

`target_status` 可选：

```text
available
library
```

## 执行顺序

```text
读取远端缓存/远端详情
-> 转换为本地 account_json
-> 查找或创建本地 accounts 文档
-> 写入删除前状态和远端快照
-> DELETE sub2api /accounts/{id}
-> 刷新 sub2api 缓存
-> 写入删除结果、pool_actions、audit_logs
```

必须先落本地库，再删除远端。这样即使远端删除成功后刷新失败，至少本地不会丢账号。

## 本地匹配规则

优先匹配已有本地账号：

1. `metadata.sub2api_account_id`
2. `account_json.credentials.chatgpt_account_id`
3. `metadata.email`
4. `account_json.credentials.email`
5. `account_json.extra.email`
6. `account_json.name`

如果找不到本地账号，则用远端快照创建一个新的本地账号。

## 写入字段

写入 `accounts.metadata`：

```js
{
  pool_status: "available" | "library",
  source: "sub2api_manual_return",

  sub2api_site_id,
  sub2api_account_id,
  sub2api_group_id,
  sub2api_group_ids,
  sub2api_group_name,

  sub2api_manual_deleted: true,
  sub2api_deleted_at,
  sub2api_deleted_by_user_id,
  sub2api_deleted_by_name,
  sub2api_delete_mode: "manual",
  sub2api_delete_target_status,
  sub2api_delete_reason,
  sub2api_delete_status: "pending" | "succeeded" | "failed",
  sub2api_delete_error,
  sub2api_delete_result,

  sub2api_return_snapshot,
  remote_status_at_return,
  remote_schedulable_at_return,
  remote_error_at_return,
  remote_last_used_at_return,

  return_is_abnormal,
  return_health_status: "normal" | "abnormal",
  return_test_status: "not_tested",
  return_tested_at: null,
  return_checked_at,

  verification_status: "not_tested",
  verification_checked_at: null,
  verification_error: null
}
```

说明：

- 第一版手动删除不主动跑模型测试，所以 `return_test_status = not_tested`。
- `return_checked_at` 是本次删除/回退检查时间。
- 后续如果删除前增加测试，再写 `return_test_status = passed | failed` 和 `return_tested_at`。
- 正常限流类 `429` / `529` 不算异常账号。

## 正常与异常口径

第一版异常判断：

- `status` 为 `error`、`failed`、`banned`、`disabled`、`invalid` 时，视为异常。
- `schedulable = false` 且不是普通 active/warning 状态时，视为异常。
- `error_message` 存在且不是 429/529/rate limit/限流时，视为异常。
- 429/529 限流仍按正常账号处理，可退回可用池。

后续自动异常回退会扩展：

- `return_is_abnormal = true`
- `pool_status = problem`
- `sub2api_delete_mode = auto_error`
- 保存更详细的错误窗口、用量窗口、最后请求时间。

## 需要注意的风险

- 删除远端账号是不可逆动作，必须有确认弹窗和审计日志。
- sub2api `DELETE /accounts/{id}` 的具体行为还需要继续实测：硬删、软删、返回体格式可能不同。
- 如果远端账号原本没有本地记录，必须先落库再删远端。
- 如果远端删除失败，本地账号保留，`sub2api_delete_status = failed`，不能假装已经退回成功。
- 如果匹配到错误本地账号，可能污染状态。后续可以增加 `chatgpt_account_id` 优先和人工确认弹窗。
- 批量删除必须后续单独设计，至少需要预览账号数、目标状态、异常数量和失败处理。

## 第一版实现状态

已实现：

- `Sub2ApiClient.delete_account()`。
- `manual_delete_sub2api_account()` 服务。
- `POST /sub2api-sites/{site_id}/accounts/{account_id}/manual-delete`。
- `API 账号池状态` 每行按钮：`手动删除`、`退回总库`。
- 删除后刷新 sub2api 缓存，并更新页面。

后续建议：

- 增加删除前可选测试。
- 增加删除失败后的“重新确认远端状态”按钮。
- 增加远端-only 账号落库后的明显标签。
- 增加批量删除预览，但默认禁用批量远端删除。
