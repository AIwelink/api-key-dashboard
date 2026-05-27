# Account Fields

本文档定义每个上传账号需要保存、展示和同步的字段。当前采用简化模型：一个账号文档保存一个 `account_json`，所有管理字段都放进 `metadata`。

## Account Document Shape

```js
{
  _id,
  account_json: {},
  metadata: {}
}
```

## account_json

`account_json` 保存 sub2api 的原始账号对象，例如：

```js
{
  name,
  platform,
  type,
  expires_at,
  auto_pause_on_expired,
  concurrency,
  priority,
  credentials: {},
  extra: {}
}
```

规则：

- 原样保存。
- 明文保存。
- 不改字段名。
- 不改嵌套结构。
- 用户在上传页填写的管理字段会同步写入 `account_json.extra`，例如 `payment_type`、`phone_bound`、`remark`。
- 导出和推送 sub2api 时直接使用。

## metadata

所有系统管理字段都放进 `metadata`。

```js
{
  created_at,
  updated_at,
  uploader_name,
  uploaded_by_user_id,
  email,
  email_session,
  account_type,
  payment_type,
  "2FA",
  self_produced,
  purchase_source,
  purchase_account_type,
  phone_bound,
  phone_number,
  payment_type_note,
  account_status,
  remark,
  manual_status_label,
  used_quota,
  last_request_at,
  last_checked_at,
  last_error,
  tags: []
}
```

## System Generated

| 字段 | 说明 |
| --- | --- |
| `metadata.created_at` | 创建时间，系统自动生成 |
| `metadata.updated_at` | 更新时间，系统自动更新 |
| `metadata.uploaded_by_user_id` | 当前登录用户，系统自动记录 |

## User Filled

| 字段 | 说明 |
| --- | --- |
| `metadata.email_session` / `account_json.extra.email_session` | 邮箱和接码 session，英文参数 `email_session`，必填 |
| `metadata.account_type` / `account_json.extra.account_type` | 账号类型，英文参数 `account_type`，选项为 `plus`、`free`、`pro`、`other` |
| `metadata.payment_type` / `account_json.extra.payment_type` | 支付类型，英文参数 `payment_type`，选项为 `paypal_multi`、`paypal_single`、`no_card`、`gopay`、`other` |
| `metadata["2FA"]` / `account_json.extra["2FA"]` | 2FA，英文参数 `2FA`，选填 |
| `metadata.self_produced` / `account_json.extra.self_produced` | 是否自产，英文参数 `self_produced`，布尔值，`true` 表示自产，`false` 表示购买，必填 |
| `metadata.purchase_source` / `account_json.extra.purchase_source` | 购买来源，英文参数 `purchase_source`，当 `self_produced = false` 时必填；金幺模板默认值为 `金幺`；账号后续改为自产时保留该字段作为历史来源 |
| `metadata.purchase_account_type` / `account_json.extra.purchase_account_type` | 购买时账号类型，英文参数 `purchase_account_type`，选项为 `plus`、`free`、`pro`、`other`；购买账号必填，金幺模板默认 `free`；用于标注“购买时 free，后续升级 plus”等场景，和当前 `account_type` 分开保存 |
| `metadata.phone_bound` / `account_json.extra.phone_bound` | 是否绑定手机，英文参数 `phone_bound`，布尔值，`true` 表示已绑定手机，`false` 表示未绑定手机，必填 |
| `metadata.phone_number` / `account_json.extra.phone_number` | 手机号，英文参数 `phone_number`，选填；当 `phone_bound = true` 时建议填写 |
| `metadata.payment_type_note` | 支付类型补充说明 |
| `account_json` | 账号 JSON 文件或粘贴内容 |
| `metadata.remark` / `account_json.extra.remark` | 备注，选填；可补充说明 `phone_bound` 布尔值判断依据 |
| `metadata.manual_status_label` / `account_json.extra.manual_status_label` | 账户状态标注，人工填写 |

`payment_type` 初始枚举：

```text
paypal_multi
paypal_single
no_card
gopay
other
```

前端显示：

```text
PayPal 一卡多号
PayPal 一卡一号
不绑卡
gopay
其他
```

## Parsed From account_json

| 字段 | 说明 |
| --- | --- |
| `metadata.email` | 账号邮箱，优先从 JSON 中获取 |

邮箱解析优先级：

1. `account_json.credentials.email`
2. `account_json.extra.email`
3. `account_json.name`

如果无法从 JSON 中获取，允许用户手动补充。

## Import Templates

解析模式支持选择 `source_template`：

| 模板 | 参数 | 说明 |
| --- | --- | --- |
| sub2api 账号 JSON | `sub2api` | 默认模板，要求账号对象包含 `credentials`，保持 sub2api 账号结构 |
| 购买账号：金幺 | `purchased_jinyao` | 适配购买账号平铺 JSON，将 token 字段组装进 `credentials`，并将原始购买字段保存到 `account_json.extra` |

金幺模板字段映射：

| 来源字段 | 写入位置 | 说明 |
| --- | --- | --- |
| `email` / `login_identity` / `account_claims_email` | `account_json.name`、`credentials.email`、`metadata.email` | 账号邮箱 |
| `access_token`、`refresh_token`、`id_token`、`session_token`、`client_id` | `account_json.credentials` | API / OAuth 凭据 |
| `chatgpt_account_id`、`chatgpt_user_id`、`organization_id`、`project_id`、`workspace_id` | `account_json.credentials` | OpenAI / ChatGPT 账号关联 ID |
| `mailbox_connection` | `account_json.extra.email_session`、上传页 `email_session` | 邮箱和接码 session，必填 |
| `phone` | 上传页 `phone_number`，并默认 `phone_bound = true` | 购买账号手机号 |
| 其他原始字段 | `account_json.extra` | 包括 `db_id`、`password`、`mailbox`、`mailbox_url` 等原始购买信息 |

## Fetched From sub2api

| 字段 | 说明 |
| --- | --- |
| `metadata.account_status` | 账户状态，连接 sub2api 获取 |
| `metadata.used_quota` | 已使用额度，连接 sub2api 获取 |
| `metadata.last_request_at` | 最后请求时间，连接 sub2api 获取 |
| `metadata.last_checked_at` | 最后检查时间，系统自动记录 |
| `metadata.last_error` | 最近一次获取失败原因 |

## Status Separation

账户状态和人工标注分开保存：

```js
{
  metadata: {
    account_status,
    manual_status_label
  }
}
```

- `account_status` 来自 sub2api。
- `manual_status_label` 由人工填写。
