# Free 升 Plus 待办与处理

本文档记录待办与处理页面第一个大任务类型：`free_to_plus`。

## 定位

待办与处理是人工任务池和执行台，不等同于账号列表，也不等同于使用备选池。

账号只有处在以下本地状态时，才可以进入待办候选：

```text
library
available
```

账号一旦进入 `reserve` 或 `active`，就不再出现在待办里。需要处理时，必须先由人工流转回 `available` 或 `library`。

## 候选条件

`free_to_plus` 第一版候选账号必须满足：

| 条件 | 说明 |
| --- | --- |
| `metadata.account_type = "free"` | 当前账号类型是 free |
| `metadata.email_session` 或 `account_json.extra.email_session` 非空 | 必须能拿到邮箱和接码 session |
| `metadata.pool_status` 不存在、`library` 或 `available` | 总库和可用池可进入待办 |
| `metadata.deleted_at` 不存在 | 已删除账号不出现 |

自产 free 和购买 free 都可以进入待办。购买账号保留 `purchase_account_type = free`，升级完成后当前 `account_type` 改成 `plus`。

## 分布式锁

用户点击“开始处理”时，后端使用 MongoDB 原子 `find_one_and_update` 同时完成：

1. 检查账号仍满足候选条件。
2. 检查账号没有被其他人锁定，或锁已过期。
3. 写入处理状态和锁信息。

锁字段：

```js
metadata.upgrade_lock = {
  task_type: "free_to_plus",
  locked_by_user_id,
  locked_by_name,
  locked_at,
  expires_at
}
```

第一版锁有效期为 2 小时。锁过期后其他人可以重新开始处理，避免账号长期卡死。

## 处理信息

待办与处理列表默认只展示任务摘要，不在列表里直接暴露登录信息。用户点击“开始处理”并获得当前账号锁后，页面才在该账号下方展开处理面板，展示账号处理所需的登录信息：

| 信息 | 来源优先级 |
| --- | --- |
| 邮箱 | `metadata.email`、`credentials.email`、`account_json.extra.email`、`account_json.name` |
| 邮箱和接码 session | `metadata.email_session`、`account_json.extra.email_session`、`account_json.extra.mailbox_connection` |
| 2FA | `metadata["2FA"]`、`account_json.extra["2FA"]` |
| 密码 | `account_json.extra.password` |

待办与处理页面也需要提供账号编辑入口，保存逻辑复用账号总库的 `/api/accounts/{account_id}` 更新接口。编辑保存后继续同步写入 `metadata` 和 `account_json.extra`。

编辑入口只在处理面板中显示，也就是任务处于 `processing` 且锁属于当前用户时显示。`pending` 或 `failed` 状态可以显示历史处理人，但历史处理人只是追踪信息，不参与是否可开始处理、是否可编辑的判断。

## 状态字段

```js
metadata.upgrade_task_type = "free_to_plus"
metadata.upgrade_status = "pending" | "processing" | "completed" | "failed"
metadata.upgrade_from = "free"
metadata.upgrade_to = "plus"
metadata.upgrade_assignee_user_id
metadata.upgrade_assignee_name
metadata.upgrade_started_at
metadata.upgrade_completed_at
metadata.upgrade_failed_at
metadata.upgrade_error
metadata.upgrade_note
```

## 完成升级

处理人点击“完成升级”时必须选择 `payment_type`。

后端写入：

```js
metadata.account_type = "plus"
metadata.payment_type = selected_payment_type
metadata.pool_status = "available"
metadata.upgrade_status = "completed"
account_json.extra.account_type = "plus"
account_json.extra.payment_type = selected_payment_type
account_json.credentials.plan_type = "plus"
```

完成后自动清除 `metadata.upgrade_lock`。

## 退回处理

已完成任务允许人工点击“退回处理”，用于处理误点完成或需要复核的账号。

退回处理规则：

- 只允许 `metadata.upgrade_status = "completed"` 的任务退回。
- 账号仍必须位于 `library` 或 `available`。
- 点击后重新进入 `processing`，并把锁分配给当前用户。
- 不自动把 `account_type` 从 `plus` 改回 `free`，避免误伤已经升级成功的数据。
- 如果退回后点击“取消处理”，任务也统一回到 `pending`。

## 接口

```text
GET  /api/todo-items/free-to-plus/accounts
POST /api/todo-items/free-to-plus/accounts/{account_id}/start
POST /api/todo-items/free-to-plus/accounts/{account_id}/release
POST /api/todo-items/free-to-plus/accounts/{account_id}/return-processing
POST /api/todo-items/free-to-plus/accounts/{account_id}/complete
POST /api/todo-items/free-to-plus/accounts/{account_id}/fail
```

`complete` 请求：

```json
{
  "payment_type": "paypal_multi",
  "note": "optional"
}
```

`fail` 请求：

```json
{
  "error": "支付失败",
  "note": "optional"
}
```
