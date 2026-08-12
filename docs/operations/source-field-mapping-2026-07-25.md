# 运营分析来源字段映射审计

审计日期：2026-07-25

本文记录 AIWeLink（Sub2API/PostgreSQL）与 AIGCLink（NewAPI/MySQL）进入 Growth PostgreSQL 运营分析域的只读字段映射。检查过程只读取 `information_schema` 的表名与列名，没有读取业务行、密码、Token、IP 或请求内容。

## 统一口径

- 账号唯一键：`(site_id, external_user_id)`。
- 调用：仅同步可确认已产生消耗的成功调用记录。
- 销售收入：仅同步已完成的支付或订阅订单，使用实际收款 CNY。
- 兑换码：来源系统无法提供可信用途时进入 `pending`，由运营人员补录。
- 内部人员：同步后按 `growth.internal_users` 标记，消耗计入成本，但不进入普通用户收入和付费指标。
- 增量同步：游标回扫 48 小时，事实表按来源记录键 UPSERT；每 15 分钟同步一次。
- 安全边界：适配器只读业务数据库；生成兑换码和调整余额必须走已验证的业务 API，不允许直写业务库。

## AIWeLink / Sub2API

站点标识：`aiwelink`

| 业务事实 | 来源表 | 白名单字段 | Growth 映射 |
| --- | --- | --- | --- |
| 用户 | `users` | `id`, `email`, `username`, `status`, `balance`, `created_at`, `updated_at` | `ops_user_snapshots` |
| 成功调用 | `usage_logs` | `id`, `user_id`, `actual_cost`, `created_at` | `usage_facts` |
| 支付/退款 | `payment_orders` | `id`, `user_id`, `amount`, `pay_amount`, `status`, `paid_at`, `completed_at`, `updated_at`, `refund_amount`, `refund_at`, `order_type` | `credit_events` |
| 兑换码使用 | `redeem_codes` | `id`, `used_by`, `value`, `type`, `used_at`, `notes` | `credit_events` + `classification_tasks` |

映射规则：

- `users.id` 转为字符串业务用户 ID；账号展示名依次使用 `email`、`username`、`id`。
- 每条 `usage_logs` 记录计为一次成功调用，`actual_cost` 作为消耗余额单位。
- 仅 `status=COMPLETED` 且存在 `completed_at` 的订单按 `purpose=sale` 写入；`pay_amount` 是实际 CNY 收入，`amount` 是到账余额单位。
- `refund_amount > 0` 且存在 `refund_at` 时，生成独立的退款 debit 事件。
- 已使用兑换码默认 `classification_status=pending`，禁止从备注文本猜测用途。
- 当前换算比例：`1 CNY = 10` 个 AIWeLink 余额单位，按生效时间版本化保存。

## AIGCLink / NewAPI

站点标识：`aigclink`

| 业务事实 | 来源表 | 白名单字段 | Growth 映射 |
| --- | --- | --- | --- |
| 用户 | `users` | `id`, `username`, `email`, `display_name`, `status`, `quota`, `created_at`, `last_login_at` | `ops_user_snapshots` |
| 成功调用 | `quota_data` | `id`, `user_id`, `count`, `quota`, `created_at` | `usage_facts` |
| 充值 | `top_ups` | `id`, `user_id`, `amount`, `money`, `complete_time`, `create_time` | `credit_events` |
| 订阅订单 | `subscription_orders` | `id`, `user_id`, `money`, `complete_time`, `create_time` | `credit_events` |
| 兑换码使用 | `redemptions` | `id`, `used_user_id`, `quota`, `redeemed_time`, `name` | `credit_events` + `classification_tasks` |

映射规则：

- NewAPI 原始 quota 按 `QuotaPerUnit=500000` 转为展示余额单位。
- `quota_data.count` 是成功调用次数，`quota / QuotaPerUnit` 是消耗余额。
- 已完成的 `top_ups` 和 `subscription_orders` 按 `purpose=sale` 写入，`money` 是实际 CNY 收入。
- 已使用兑换码默认进入待分类，不根据兑换码名称猜测销售、推广或内部使用。
- 当前展示换算比例：`1 CNY = 1` 个 AIGCLink 展示余额单位，按生效时间版本化保存。

## 未映射与后续验证

- NewAPI 的 `logs` 表已在结构扫描中确认存在，但当前成功调用统计使用聚合口径更明确的 `quota_data`，不会读取日志请求内容。
- 当前 NewAPI 版本没有已验证的独立退款字段映射。未得到表结构和业务状态语义证据前，不把支付撤销或管理员日志推断为退款。
- 管理员调额或无法识别来源的额度记录进入 `classification_tasks`，由 owner/admin 补录用途和实际收款。
- 每次业务系统升级后应重新执行只读结构检查；字段变化必须先更新匿名测试夹具并通过适配器回归测试，再调整生产查询。

## 验证结果

- 适配器查询不包含密码、访问 Token、TOTP 密钥、IP 或请求内容字段。
- AIWeLink 和 AIGCLink 映射测试覆盖用户、调用、支付、退款（AIWeLink）与兑换码待分类。
- 2026-07-25 后端全量回归：681 项通过。
- Growth 数据库当前版本：`0002_operations_analytics`，无待执行迁移，业务表 22 张。
