# 精确分组分钟用量与七天回补设计

## 背景

当前 TPM 采样器每分钟读取当小时累计计数器，再使用相邻样本差值估算 TPM、RPM 和账号成本。该方法会受到以下因素影响：

- 调度周期不严格等于 60 秒，导致速率被采样漂移放大或缩小。
- 整点累计值重置，跨小时样本无法稳定计算。
- `usage_logs` 延迟写入时，用量会归属到错误的采样分钟。
- 进程重启或单轮采样失败会造成无法补齐的缺口。

状态判断和后续回测需要准确的自然分钟数据，因此采样器改为直接从 PostgreSQL `usage_logs` 按分钟聚合。

## 目标

- 只统计已经结束的自然分钟，不使用当前尚未结束的分钟。
- 按 `site_id + group_id + UTC minute` 保存精确 TPM、RPM 和账号成本。
- 每分钟重新校准最近 20 个已结束分钟，吸收延迟写入日志。
- 每分钟额外回补一个历史小时，持续覆盖最近七天。
- 在约 2 小时 48 分钟内完成一次完整七天扫描，并持续循环校准。
- 保留现有的远端账号并发采样，不让历史用量回补覆盖并发数据。
- 单站点失败、历史回补失败或部分写入失败不会阻塞其他站点。

## 不在范围内

- 不建立 PostgreSQL 物化视图或新的汇总表。
- 不按 `usage_logs.id` 维护增量消费游标。
- 不改变前端容量算法和告警阈值。
- 不增加新的前端图表或配置项。
- 不回补七天以前的精确分钟数据。

## 方案选择

采用“滑动窗口校准 + 循环历史回补”方案：

1. 每分钟聚合最近 20 个已结束分钟。
2. 每分钟按持久化游标聚合一个历史小时。
3. 两个范围都一次查询全部有效分组，并在 MongoDB 中幂等覆盖对应分钟。

相比只处理新日志 ID，该方案不需要维护复杂的日志消费状态，能自然修正延迟日志。相比 PostgreSQL 汇总表，该方案不引入数据库迁移和额外运维对象，符合当前数据量和每分钟两次查询的负载预算。

## 时间边界

所有存储桶使用 UTC 的自然分钟边界。

```text
closed_end = floor(now_utc, minute)
recent_start = closed_end - 20 minutes
recent_range = [recent_start, closed_end)
```

例如采样器在 `14:07:38Z` 运行时，最近窗口为 `[13:47:00Z, 14:07:00Z)`。`14:07:00Z` 之后的日志属于尚未结束的分钟，不参与本轮统计。

PostgreSQL 查询使用左闭右开边界，避免相邻窗口重复或遗漏日志。MongoDB 的 `bucket_at` 是对应分钟起点。

## PostgreSQL 聚合

新增一个按时间范围读取全部目标分组分钟用量的仓储函数。查询按 `group_id` 和 UTC 分钟分组：

```sql
SELECT
    group_id,
    date_trunc('minute', created_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS bucket_at,
    COUNT(id) AS total_requests,
    COALESCE(SUM(
        COALESCE(input_tokens, 0)
        + COALESCE(output_tokens, 0)
        + COALESCE(cache_creation_tokens, 0)
        + COALESCE(cache_read_tokens, 0)
    ), 0) AS total_tokens,
    COALESCE(SUM(input_tokens), 0) AS input_tokens,
    COALESCE(SUM(output_tokens), 0) AS output_tokens,
    COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
    COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
    COALESCE(SUM(
        COALESCE(account_stats_cost, total_cost)
        * COALESCE(account_rate_multiplier, 1)
    ), 0) AS account_cost,
    MAX(created_at) AS source_updated_at
FROM usage_logs
WHERE group_id = ANY(CAST(:group_ids AS bigint[]))
  AND created_at >= :start_at
  AND created_at < :end_at
GROUP BY group_id, bucket_at
ORDER BY bucket_at ASC, group_id ASC
```

每个分钟桶的指标定义如下：

```text
RPM = total_requests
TPM = input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens
minute account cost = SUM(
  COALESCE(account_stats_cost, total_cost)
  * COALESCE(account_rate_multiplier, 1)
)
```

最近窗口和历史窗口分别查询。最近窗口成功后立即写入；历史查询失败不回滚已经完成的最近窗口。

## MongoDB 样本模型

继续使用 `sub2api_tpm_samples` 集合和现有唯一索引：

```text
site_id + group_id + bucket_at
```

精确分钟文档升级为 `schema_version = 3`，`counter_source = postgresql_usage_logs_minute`。文档主要字段为：

```text
_id: <site_id>:<group_id>:<UTC minute>
schema_version: 3
counter_source: postgresql_usage_logs_minute
site_id
group_id
bucket_at
sampled_at
recorded_at
stats_updated_at
tpm
calculated_tpm
rpm
calculated_rpm
minute_tokens
minute_requests
input_tokens
output_tokens
cache_creation_tokens
cache_read_tokens
minute_account_cost
account_cost_per_minute
account_cost_per_hour
current_concurrency
source: exact_minute
elapsed_seconds: 60
expires_at
```

`sampled_at` 与 `bucket_at` 都表示实际用量所属的自然分钟，供现有容量分析按时间排序和截取窗口；`recorded_at` 表示本次聚合或回补的执行时间。`tpm` 与 `minute_tokens` 相同，`rpm` 与 `minute_requests` 相同。保留 `calculated_tpm`、`calculated_rpm` 和 `account_cost_per_minute`，让现有容量分析消费者无需改变字段接口。样本保留期继续使用 60 天，`expires_at` 从 `recorded_at` 起算。

写入使用按 `_id` 的幂等 upsert。同一分钟重新聚合时覆盖用量字段，因此延迟日志会被后续校准纳入。

## 零值补齐

PostgreSQL 不会返回没有日志的分组分钟。采样器根据请求范围生成完整的“分组 × 分钟”集合，并将缺失组合写为零：

```text
tpm = 0
rpm = 0
minute_account_cost = 0
token fields = 0
stats_updated_at = null
```

明确保存零值可以区分“该分钟没有流量”和“采样器没有采到数据”，并保证趋势、均值和回测窗口连续。

## 并发采样

并发不是历史日志指标，继续通过远端账号列表计算：

- 每轮为站点读取一次远端账号列表。
- 将分组并发总数写入最新的已结束分钟，即 `closed_end - 1 minute`。
- 最近 20 分钟校准只更新用量字段，不清空其他分钟已有的 `current_concurrency`。
- 历史回补不写 `current_concurrency`；新建的历史文档该字段为 `null`。
- 远端账号请求失败时，用量聚合仍然成功，最新分钟并发记为 `null`。

因此容量分析仍能获得实时并发样本，而历史用量重算不会制造伪造的历史并发。

## 七天回补游标

新增 `sub2api_tpm_backfill_state` 集合，每个站点使用一个文档：

```text
_id: <site_id>
site_id
next_window_end
last_window_start
last_window_end
last_completed_at
updated_at
```

首次运行没有游标时：

```text
next_window_end = recent_start
historical_range = [next_window_end - 1 hour, next_window_end)
```

历史查询和对应 MongoDB upsert 全部成功后：

```text
next_window_end = historical_range.start
```

游标从最近数据向更早时间推进。到达 `closed_end - 7 days` 后，下一轮将游标重置为当时的 `recent_start`，重新开始七天扫描。每轮处理一小时，完整扫描最多需要 168 轮，即约 2 小时 48 分钟。

若进程停机导致游标超出当前合法范围，启动后的首轮会将其钳制到 `[closed_end - 7 days, recent_start]`。游标只在查询和样本写入都成功后推进。

## 调度流程

每个活动 sub2api 站点每分钟执行：

1. 获取站点配置和缓存中的有效分组 ID。
2. 计算 `closed_end`、最近 20 分钟范围和历史一小时范围。
3. 查询最近窗口的精确分钟聚合。
4. 补齐零值并幂等写入最近窗口。
5. 查询远端账号并将当前并发写入最新已结束分钟。
6. 查询游标指定的历史一小时。
7. 补齐零值并幂等写入历史窗口。
8. 成功后推进历史游标。

同一站点继续使用进程内锁防止重叠执行。不同站点并行执行。调度周期扣除本轮运行时间；若单轮超过 60 秒，下一轮立即开始，但不会与当前站点的未完成任务重叠。

## 读取与迁移

容量状态读取只接受：

```text
schema_version = 3
counter_source = postgresql_usage_logs_minute
```

部署后首轮立即生成最近 20 分钟的 v3 样本，因此状态页无需等待完整七天回补。旧 v2 文档不批量删除，也不参与新计算；它们根据原有 TTL 自然过期。由于 `_id` 和唯一键保持不变，回补到相同分钟时会把旧文档原位升级为 v3。

## 失败处理

- PostgreSQL 最近窗口查询失败：本轮站点标记失败，不写伪造零值，不推进历史游标。
- 最近窗口 MongoDB 写入失败：不执行游标推进；下轮按相同时间范围幂等重试。
- 远端并发请求失败：记录警告并继续历史回补，不影响精确用量。
- 历史查询或写入失败：保留游标，下轮重试相同历史小时。
- 单个站点失败：不影响其他活动站点。
- 部分 bulk upsert 成功后报错：不推进游标；下轮幂等覆盖相同分钟。
- 没有有效分组：返回完成状态，不执行 PostgreSQL 查询，也不推进无意义的回补游标。

日志不输出 SQL_DSN、API Key 或账号凭证。失败日志只记录站点 ID、阶段、时间范围和异常类型。

## 测试范围

### PostgreSQL 仓储

- 一个查询同时聚合多个分组。
- SQL 使用左闭右开时间边界并排除当前未结束分钟。
- Token、RPM 和账号成本公式正确。
- Decimal 和 datetime 正确转换为应用层类型。
- 查询失败时释放 SQLAlchemy engine。

### 分钟样本写入

- 最近 20 个已结束分钟生成准确边界。
- 数据行映射到 v3 样本字段。
- 缺失的分组分钟写为零。
- 相同 `_id` 重跑时幂等覆盖用量。
- 用量重算不会覆盖已有并发。
- 只有最新已结束分钟写入新并发值。

### 历史回补

- 无游标时从最近窗口之前开始。
- 成功后游标后移一小时。
- 查询或写入失败时游标保持不变。
- 到达七天边界后从最近窗口重新循环。
- 停机后的越界游标会被钳制到合法范围。

### 集成与兼容

- 状态读取只加载 v3 精确分钟样本。
- 部署后最近窗口可立即为容量分析提供数据。
- 并发请求失败不会丢失用量数据。
- 同站点重叠执行被跳过，不同站点失败相互隔离。
- 调度任务在应用退出时正确取消。

## 验收标准

- 任意已结束分钟的 TPM、RPM 和账号成本能与同范围 `usage_logs` 聚合结果一致。
- 延迟不超过 20 分钟写入的日志在下一轮最近窗口校准中自动修正。
- 更早的延迟日志在最多约 2 小时 48 分钟内由循环历史回补修正。
- 当前未结束分钟不会进入容量计算。
- 连续运行约 2 小时 48 分钟后，最近七天每个有效分组的每个分钟都有 v3 文档，包括零流量分钟。
- 任意失败不会错误推进历史游标，也不会把查询失败误写成零流量。
