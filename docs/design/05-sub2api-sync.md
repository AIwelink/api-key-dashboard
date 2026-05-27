# Sub2Api Sync Design

## 设计目标

同步不是简单覆盖，而是比较本地 `account_json` 和 sub2api 当前状态，然后生成同步计划。

重要约束：推送给 sub2api 的 JSON 必须保持 sub2api 原始结构。当前内部模型为 `account_json + metadata`，不能改变最终导出的 JSON 形状。

```text
account_json
  +
metadata 中的管理字段
  +
sub2api 当前观测字段
  =
sync plan
```

## 状态分层

### account_json

sub2api 所需的原始账号对象，明文保存，导出和推送时直接使用。

### metadata

本系统管理字段，例如上传人、支付类型、人工状态标注、备注、sub2api 观测字段。

### sub2api observed fields

从 sub2api 查询到的实际状态，例如：

- 账户状态。
- 已使用额度。
- 最后请求时间。
- 最近错误。
- 最近检查时间。

这些字段写入根级 `metadata`，不写入 `account_json.extra`。

## 同步动作

```text
create: 本地存在，sub2api 不存在。
update: 本地和 sub2api 都存在，但 account_json 中的并发、优先级等配置不同。
pause: 本地策略要求暂停，或账号已过期且策略要求自动暂停。
delete: 本地软删除，且策略要求从 sub2api 删除。
skip: 无差异或当前策略不允许自动操作。
error: 同步失败。
```

## 差异计算

示例 diff：

```json
{
  "account_id": "local_id",
  "action": "update",
  "changes": {
    "account_json.concurrency": {
      "from": 5,
      "to": 10
    },
    "account_json.priority": {
      "from": 3,
      "to": 1
    }
  }
}
```

## 导入 sub2api export JSON

给定 sub2api 导出的 JSON：

```json
{
  "exported_at": "2026-05-23T06:10:08.106Z",
  "proxies": [],
  "accounts": []
}
```

导入时：

- 顶层 `accounts[]` 中每个对象生成一条账号文档。
- 每个 `accounts[]` 元素原样保存为 `account_json`。
- 上传人、支付类型、人工标注、备注等本系统字段保存到根级 `metadata`。
- 不拆分 `credentials`。
- 不拆分 `account_json.extra`。
- 不改字段名。

导出和推送时重新组装顶层结构：

```json
{
  "exported_at": "2026-05-25T00:00:00.000Z",
  "proxies": [],
  "accounts": []
}
```

其中 `accounts[]` 直接来自每条账号文档的 `account_json`。

## 明文存储说明

当前 MVP 不做敏感字段拆分。`credentials`、`account_json.extra`、`2FA` 等字段都随 `account_json` 明文保存。

字段名仍然必须保留。比如导入是 `"2FA"`，导出仍然必须是 `"2FA"`。

## 自动暂停规则

如果满足以下条件，系统生成 pause 动作：

- `account_json.auto_pause_on_expired = true`
- 当前时间大于 `account_json.expires_at`
- sub2api 当前仍处于启用状态

## sub2api 观测字段

每次连接 sub2api 检查账号时，需要尽量回填以下字段：

```js
metadata: {
  account_status,
  used_quota,
  last_request_at,
  last_checked_at,
  last_error
}
```

这些字段只表示 sub2api 当前观测结果，不等于人工标注。人工状态标注保存到 `metadata.manual_status_label`。

当前 API 账号池状态页面已经可以从 sub2api Admin API 拉取 groups/accounts，并把远程观测状态写入 MongoDB 缓存集合。该缓存用于页面展示和后续补位策略判断，不直接写回本地 `accounts.metadata`。后续真正执行本地账号同步时，再按 reconciliation 结果写入 `metadata.account_status` 等本地观测字段。

账号邮箱优先从以下路径获取：

1. `account_json.credentials.email`
2. `account_json.extra.email`
3. `account_json.name`

## 冲突处理

冲突场景：

- 同一个 email 已存在，但 `account_json` 不同。
- 同一个 sub2api account id 绑定到多个本地账号。
- 本地账号已软删除，但 sub2api 仍存在。
- sub2api 中账号被手动修改，与本地 `account_json` 不同。

MVP 策略：

- 批量导入时冲突不自动覆盖，进入预览列表。
- 手动同步可以允许覆盖 sub2api 配置。
- 删除动作默认只软删除本地，不自动删除 sub2api，除非用户显式勾选。

## 同步频率

MVP：

- 手动同步单账号。
- 手动同步全部账号。
- API 账号池远程观测缓存默认每 5 分钟刷新一次，可按站点配置。
- 本地账号同步仍保留手动触发，后续再扩展定时检查过期和状态。

后续：

- 针对不同标签配置不同同步频率。
- 失败后指数退避重试。
- 引入 Redis 队列。
