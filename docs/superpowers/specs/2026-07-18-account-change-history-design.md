# 账号字段变化历史存储设计

## 背景

当前 `remote_account_probe_samples` 在每次账号探测时，为每个账号重复保存状态、分组、usage 和累计 usage。生产数据库中该集合约 391 万条、逻辑体积约 5.99 GiB，占数据库逻辑数据的 98.57%。

账号探测仍需要保持约 3 分钟的时间精度，但存储目标应从“定时完整快照”改为“当前状态 + 字段级变化日志”。账号未变化时不产生历史数据；账号变化时只保存发生变化字段的新值。

## 目标

- 保留探测原始时间精度，不按小时降采样。
- 账号静态信息只保存一次。
- usage、订阅和凭证有效期只在字段变化时写入。
- 能重建任意保留期内时间点的账号动态状态。
- 细粒度变化保留 30 天，每日检查点保留 1 年。
- 401、删除、恢复和状态异常等业务事件继续长期保留。
- 旧探测快照停止写入，并依靠现有 TTL 自然淘汰。

## 非目标

- 不迁移 MongoDB 到其他数据库。
- 不在上线时批量更新或删除数百万条旧样本。
- 不把完整远端账号 credentials 写入变化日志或每日检查点。
- 不替代现有 `remote_account_status_events` 业务事件。

## 数据模型

### 当前账号状态

`remote_account_identities` 继续作为每个邮箱身份的当前状态物化表。

静态或低频字段只保留在 identity：

- `site_id`
- `normalized_email`、`email`
- `name`
- 当前和历史远端账号 ID
- `plan_type` 与来源
- 首次发现时间、最后出现时间
- 当前分组、状态、调度状态、错误信息

动态字段在 identity 中只保存当前值：

- `last_usage_snapshot`
- `current_subscription_snapshot`
- `cumulative_usage_totals`
- `cumulative_usage_snapshot`
- `history_baseline_snapshot`
- `history_baseline_hash`

`history_baseline_snapshot` 表示最后一次已确认写入变化历史的动态状态。它与当前状态分开保存，用于在历史批次写入失败后重新生成缺失变化。

### 字段变化批次

新增 `remote_account_change_batches`。每个站点的一次探测产生零到多个批次文档；没有账号动态字段变化时不写文档。

```json
{
  "_id": "site-id:probe-run-id:0",
  "schema_version": 1,
  "site_id": "us06-5001",
  "probe_run_id": "probe-run-id",
  "chunk_index": 0,
  "observed_at": "2026-07-18T06:30:00Z",
  "entries": [
    {
      "event_id": "sha256(identity + baseline + new-state)",
      "identity_id": "us06-5001:user@example.com",
      "remote_account_id": 953,
      "changes": {
        "usage.codex_5h_used_percent": 42,
        "usage.codex_5h_actual_cost": 32.7,
        "subscription.subscription_expires_at": "2026-08-01T00:00:00Z"
      },
      "unset": [],
      "previous_state_hash": "...",
      "new_state_hash": "..."
    }
  ],
  "entry_count": 1,
  "expires_at": "2026-08-17T06:30:00Z"
}
```

批次规则：

- `changes` 只保存变化字段的新值，不保存数值差额。
- 远端字段消失时，将字段路径写入 `unset`。
- 不重复保存邮箱、名称、plan、分组等 identity 字段。
- 每批最多 500 个 entry。
- 编码后接近 8 MiB 时提前分片，始终低于 MongoDB 16 MiB 文档上限。
- `_id` 和 `event_id` 都是确定性的，重试使用 upsert。
- 读取时按 `event_id` 去重。
- `expires_at` 为 `observed_at + 30 天`。

### 每日检查点

新增 `remote_account_daily_checkpoints`。每个站点每天生成零到多个检查点分片。

```json
{
  "_id": "site-id:2026-07-18:0",
  "schema_version": 1,
  "site_id": "us06-5001",
  "local_date": "2026-07-18",
  "checkpoint_at": "2026-07-18T16:00:00Z",
  "chunk_index": 0,
  "entries": [
    {
      "identity_id": "us06-5001:user@example.com",
      "usage": {},
      "subscription": {},
      "cumulative_usage": {}
    }
  ],
  "entry_count": 1,
  "expires_at": "2027-07-18T16:00:00Z"
}
```

检查点不保存账号静态信息，保留 365 天。单片同样受 500 个账号和 8 MiB 双重限制。

检查点按 `Asia/Shanghai` 自然日生成。当天第一次成功探测负责创建或补齐该日检查点，使用确定性 `_id` 保证重试幂等。

## 动态字段范围

### Usage

沿用现有 `_usage_snapshot` 提取的 5h、7d 和累计使用字段，包括：

- 使用百分比
- reset 时间和剩余秒数
- 请求数、token 数
- actual cost、standard cost、user cost
- usage 更新时间和同步时间

### 订阅与凭证

从账号顶层、`credentials` 和 `extra` 统一提取：

- `subscription_expires_at`
- `chatgpt_subscription_active_start`
- `chatgpt_subscription_active_until`
- `chatgpt_subscription_last_checked`
- `credential_expires_at` 或 credentials `expires_at`
- 远端返回的订阅状态字段

所有时间统一规范化为 UTC `datetime` 后比较，避免字符串格式不同产生虚假变化。

### 业务状态

状态、调度、错误、401、分组变化、远端删除和恢复继续由 `remote_account_status_events` 记录，不同时写入 change batch。identity 仍保留这些字段的当前值。

## 写入流程

1. 拉取远端账号并按邮箱归并。
2. 规范化 usage 与订阅快照。
3. 使用 identity 的 `history_baseline_snapshot` 计算 `changes` 和 `unset`。
4. 为每个变化生成确定性 `event_id`、previous hash 和 new hash。
5. 按站点、探测批次和大小分片，upsert change batch。
6. 批次写入成功后，条件更新对应 identity 的 `history_baseline_snapshot` 和 hash。
7. 独立更新 identity 当前状态、累计 usage、session 和现有业务事件。
8. 整个探测没有动态变化时，不写 change batch。

基线更新使用 previous hash 作为条件。批次成功但基线更新失败时，下次探测可能重新生成相同 `event_id`；读取端会去重，不会形成错误的重复变化。

## 状态重建

重建账号在时间 `T` 的动态状态：

1. 查询 `T` 之前最近一个每日检查点中的该 identity。
2. 若没有检查点，从空动态状态开始。
3. 查询检查点之后至 `T` 的 change batches。
4. 过滤目标 identity，按 `observed_at` 排序并按 `event_id` 去重。
5. 顺序应用 `unset`，再应用 `changes`。

当前状态页面不执行历史重建，直接读取 identity 当前值。

## 查询与索引

`remote_account_change_batches`：

- 唯一 `_id`
- `{site_id: 1, observed_at: -1}`
- `expires_at` TTL

不为 `entries.identity_id` 建多键索引，避免索引重新增长到每账号每变化一项。账号详情只需扫描指定站点 30 天内的批次文档，并在聚合管道中 `$filter` entry；按 3 分钟频率每站点最多约 14,400 个批次。

`remote_account_daily_checkpoints`：

- 唯一 `_id`
- `{site_id: 1, local_date: -1}`
- `expires_at` TTL

## 兼容迁移

- 停止向 `remote_account_probe_samples` 写新文档。
- Change batch 是站点级文档，TTL 固定为 30 天，不再使用各分组不同的 `sample_retention_days`。前端移除该保留天数输入，避免配置无效。
- 现有分组 `record_usage_samples` 开关改名为“记录动态变化”；账号命中关闭该开关的分组设置时，不写 usage 和订阅变化，但 identity 当前状态仍正常更新。
- 保留该集合及现有读取兼容，旧数据按 14 天 TTL 自然删除。
- 账号详情接口同时返回旧 `samples` 和新 `changes`，直到旧样本归零。
- 旧数据归零后再删除旧读取路径和无用索引，单独执行，不与本次上线绑定。
- 首次运行时，identity 没有 history baseline，则以当前远端动态状态建立 baseline，不产生“所有字段初始化”变化事件。
- 首个每日检查点提供后续历史重建起点。

## 故障处理

- Change batch 写入失败：不推进 history baseline，下次探测重新计算。
- Identity 当前状态更新失败：探测任务失败并记录日志，change batch 仍可通过 hash 判断是否已经写入。
- 每日检查点失败：不影响账号探测，下次调度重试当日相同确定性 `_id`。
- 单个账号动态数据非法：记录账号级错误，跳过该 entry，不阻断同批其他账号。
- 批次部分失败：成功分片保持幂等，失败分片重试。

## 测试要求

- 无变化时不写 change batch。
- 单字段变化只记录该字段新值。
- 字段消失时产生 `unset`。
- usage reset 到零被记录为新值，不计算成负差额。
- 订阅和凭证时间格式变化但实际时间相同时不产生事件。
- 一次探测多账号变化被合并到批次。
- 超过 500 项或 8 MiB 自动分片。
- 写入失败不推进 baseline。
- 重试产生相同 event ID，读取重建结果无重复。
- 检查点加后续变化可准确重建目标时间状态。
- 旧 sample 与新 change 在账号详情接口并存。

## 预期效果

当前约每 3 分钟、每账号一条完整文档。新结构变为每站点、每次探测最多少量批次文档，且 entry 只包含真实变化字段。文档数量由“账号数乘探测次数”降为“站点数乘探测次数”，账号静态字段和未变化 usage 不再重复进入备份。
