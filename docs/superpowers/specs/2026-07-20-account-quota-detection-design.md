# 账号实际额度检测设计

## 背景

账号池容量计算目前使用站点级人工额度配置，例如 Plus、K12 和 Pro 的 5h、7d 美金额度。Sub2API PostgreSQL 已提供账号官方窗口使用率、重置时间和同窗口的 `account_stats_cost` 聚合，因此可以在账号首次达到 100% 时记录实测美元等价额度，用于评估人工配置是否合理。

检测不得重新引入高频账号快照。系统只记录每个账号、每个独立额度窗口首次满额时的一条紧凑样本。

## 目标

- 使用 `account_stats_cost` 作为实测美元等价额度。
- 5h 和 7d 分别检测，互不替代。
- 同一账号在同一窗口只记录首次 `<100% -> 100%`。
- 同一窗口持续保持 100% 的后续采样全部忽略。
- 额度重置进入新窗口后，同一账号可以再次贡献样本。
- 按站点、账号类型和窗口类型独立统计平均值、最高值、最低值和有效样本总数。
- `free`、`plus`、`team`、`bug_team`、`k12`、`pro` 分别检测，禁止跨类型混算。
- 检测结果只展示，不自动修改账号额度估计。
- 对延迟、重复、异常跳变、账号类型变化和真实额度制度变化提供容错。

## 非目标

- 不主动消耗账号额度进行压测。
- 不根据长期保持 100% 的重复采样增加样本数。
- 不把分钟级账号 usage 快照复制到 MongoDB。
- 不自动覆盖站点的人工额度配置。
- 不把检测结果直接用于容量告警；后续采用检测结果需要单独确认。

## 数据口径

### 窗口

检测器使用现有标准化字段：

- 5h：`codex_5h_used_percent`、`codex_5h_reset_at`、`codex_5h_actual_cost`
- 7d：`codex_7d_used_percent`、`codex_7d_reset_at`、`codex_7d_actual_cost`

其中 `*_actual_cost` 已由 PostgreSQL `usage_logs` 按对应窗口聚合，成本口径为：

```text
COALESCE(account_stats_cost, total_cost) * COALESCE(account_rate_multiplier, 1)
```

界面名称使用“实测美元等价额度”，避免将其误解为供应商公开的固定美元限额。不同模型组合可能产生不同的美元等价结果，统计分布本身就是检测输出的一部分。

### 账号类型

账号类型复用容量计算的标准化逻辑：

- Bug Team 必须先通过 `is_bug_team_account` 判定，不能归入普通 Team。
- 非 Bug Team 使用标准化后的远端 `plan_type`。
- 远端类型暂时为空时沿用现有历史类型；没有历史类型时使用现有 K12 回退规则。
- 类型不在已知集合时归入 `unknown`，只在有样本时单独显示，不进入任何已知类型统计。
- 窗口开始和首次满额时类型不一致，该窗口样本无效。

## 检测流程

检测挂接在账号池数据库刷新完成、账号窗口用量已聚合之后，复用同一批 PostgreSQL 查询结果，不增加逐账号 HTTP 请求。

对每个账号分别处理 5h 和 7d：

1. 规范化百分比、重置时间、窗口分钟数、成本、本次数据库同步时间和账号类型。
2. 使用 `site_id + remote_account_id + window_type` 读取检测状态。
3. 使用标准化 UTC `reset_at` 作为窗口身份；两次 reset 时间相差不超过 2 分钟时视为同一窗口，消除上游时间抖动。
4. 新窗口首次观察只建立基线，不生成样本。
5. 同一窗口内保存最后一个有效的 `<100%` 百分比和成本。
6. 当最新有效观察为 100%，且同窗口此前存在 `<100%` 基线时，生成首次满额候选样本。
7. 候选样本使用当前窗口累计 `account_stats_cost` 作为 `observed_limit_usd`。
8. 将当前窗口标记为已命中；该窗口后续 100% 采样直接忽略。
9. reset 变化后清空命中标记并进入下一窗口。

如果系统首次看到某个窗口时账号已经是 100%，该窗口不能证明发生过首次跨越，因此不生成样本。

## 有效性规则

候选样本必须同时满足：

- 前一次和当前观察属于同一账号、同一窗口类型和同一 reset 窗口。
- 前一次有效百分比小于 100，当前百分比等于 100。
- 前一次和当前 `account_stats_cost` 均存在、非负且当前值不小于前值。
- 当前成本大于 0。
- `codex_usage_synced_at` 距离检测时间不超过 5 分钟，成本和百分比来自同一次数据库刷新。
- reset 时间存在且尚未过期。
- 窗口类型复用现有主/次窗口归一化结果；缺失或无法归类的窗口无效。长窗口允许 Bug Team 等账号不是严格 10,080 分钟，但仍按标准化后的 7d 维度独立统计。
- 账号类型在窗口内保持一致。
- 账号没有 401、凭证失效或缺失数据错误。

无效观察不得把状态推进到 100%，以便下一次新鲜数据到达时仍能完成有效跨越检测。

## 去重与并发

候选样本 ID 使用以下字段确定性生成：

```text
site_id + remote_account_id + window_type + normalized_reset_at
```

同一 ID 使用 upsert，刷新重试、进程重启和并发任务不会重复计数。检测状态更新必须拒绝比 `last_observed_at` 更旧的观察，避免迟到数据倒退状态。

## 异常值与制度变化

每个 `site_id + account_type + window_type` 独立维护当前统计代次。

### 初始阶段

- 前 5 个结构有效样本建立第一代基线。
- 初始样本仍保存质量字段，明显不完整的数据已在有效性规则阶段拒绝。

### 稳定阶段

- 使用当前代最近最多 100 个有效样本计算中位数和 MAD。
- 候选值偏离中位数超过 `max(25%, 3 * MAD / median)` 时标记为异常候选。
- 异常候选不进入平均、最高、最低和有效样本总数。
- 异常候选保留 30 天，记录拒绝原因和基线统计，方便排查。

### 新额度制度识别

真实额度可能发生变化，不能永久把新制度当作异常值。满足以下条件时建立新统计代次：

- 连续出现至少 5 个偏向同一方向的异常候选；
- 候选来自至少 3 个不同账号；
- 候选中位数之间的相对离散不超过 10%；
- 5 个候选均通过结构有效性检查。

新代次建立后，这批候选转为有效样本。旧代次保留用于审计，界面默认显示当前代次统计，并显示代次开始时间。

## 存储模型

### `sub2api_quota_detection_states`

每个站点、远端账号和窗口类型一个小文档：

```json
{
  "_id": "us06-5001:953:five_hour",
  "site_id": "us06-5001",
  "remote_account_id": 953,
  "window_type": "five_hour",
  "window_reset_at": "2026-07-20T10:00:00Z",
  "last_under_limit_percent": 94,
  "last_under_limit_cost_usd": 107.2,
  "last_observed_at": "2026-07-20T09:58:00Z",
  "account_type": "plus",
  "hit_recorded": false
}
```

状态只保存检测所需字段，不保存完整账号 JSON。每次有效观察将 `expires_at` 延长到 30 天后，账号长期消失后由 TTL 清理。

### `sub2api_quota_limit_samples`

每个账号窗口最多一个文档，保存有效样本或异常候选：

```json
{
  "_id": "us06-5001:953:five_hour:2026-07-20T10:00:00Z",
  "site_id": "us06-5001",
  "remote_account_id": 953,
  "account_type": "plus",
  "window_type": "five_hour",
  "window_reset_at": "2026-07-20T10:00:00Z",
  "hit_at": "2026-07-20T09:59:00Z",
  "previous_percent": 94,
  "previous_cost_usd": 107.2,
  "observed_limit_usd": 113.6,
  "classification": "accepted",
  "generation": 1,
  "quality_reason": "first_full_transition"
}
```

有效样本保留 90 天用于容错计算和排查；异常候选保留 30 天。

### `sub2api_quota_limit_daily_rollups`

按站点、账号类型、窗口类型、统计代次和上海自然日保存：

```json
{
  "_id": "us06-5001:plus:five_hour:1:2026-07-20",
  "site_id": "us06-5001",
  "account_type": "plus",
  "window_type": "five_hour",
  "generation": 1,
  "local_date": "2026-07-20",
  "sample_count": 12,
  "sample_sum_usd": 1321.4,
  "sample_min_usd": 106.8,
  "sample_max_usd": 114.9
}
```

当某天样本变化时，从该日样本确定性重算并 replace 当日汇总，避免重试导致 `$inc` 重复。长期统计通过聚合每日 `count/sum/min/max` 得到，不需要永久保存全部账号窗口样本。

## 查询接口

新增只读接口：

```text
GET /api-pools/quota-detection?site_id=<site_id>
```

返回每种账号类型的 5h、7d 当前代次统计：

```json
{
  "site_id": "us06-5001",
  "items": [
    {
      "account_type": "plus",
      "five_hour": {
        "average_usd": 110.12,
        "maximum_usd": 114.9,
        "minimum_usd": 106.8,
        "sample_count": 38,
        "generation": 1,
        "generation_started_at": "2026-07-20T09:59:00Z"
      },
      "seven_day": {
        "average_usd": 139.7,
        "maximum_usd": 145.2,
        "minimum_usd": 135.1,
        "sample_count": 9,
        "generation": 1,
        "generation_started_at": "2026-07-20T11:20:00Z"
      }
    }
  ],
  "last_evaluated_at": "2026-07-20T12:00:00Z"
}
```

已知类型即使尚无样本也返回空统计，保证前端顺序稳定。`unknown` 仅在存在样本时返回。

## 前端

在“站点配置 > 账号额度估计”下方增加“实际额度检测”。切换站点时跟随当前站点重新读取。

按账号类型分行，固定顺序为：

```text
Free, Plus, Team 子号, Bug Team, K12, Pro 20x
```

每行分别显示 5h 和 7d：

- 平均实测额度
- 最高实测额度
- 最低实测额度
- 有效样本总数

无样本显示 `-` 和 `0 个样本`。结果只读，不提供自动覆盖额度配置的按钮。异常样本数和最后检测时间可以放在标题提示中，不占用主要数据位置。

## 索引与空间控制

- detection state：唯一 `_id`，`site_id + updated_at`，`expires_at` TTL。
- limit sample：唯一 `_id`，`site_id + account_type + window_type + hit_at`，`classification + hit_at`，`expires_at` TTL。
- daily rollup：唯一 `_id`，`site_id + account_type + window_type + generation + local_date`。
- 不保存完整 credentials、extra、usage snapshot 或账号名称。
- 不恢复 `remote_account_change_batches`。

## 故障处理

- PostgreSQL读取失败：本轮检测跳过，不修改检测状态。
- 单账号字段异常：只拒绝该账号窗口，不影响同批其他账号。
- 样本写入成功但日汇总失败：样本保留，下一轮重新计算受影响日期。
- 日汇总重复执行：replace 同一确定性 `_id`，结果不重复。
- 站点刷新重叠：沿用站点级任务合并，检测器仍只消费一次结果。
- 进程重启：检测状态和样本均持久化，不会把持续 100% 误判为新命中。

## 测试要求

- `<100 -> 100` 产生一个样本，`100 -> 100` 不产生样本。
- 首次观察即为 100 不产生样本。
- reset 变化后同一账号可以再次产生样本。
- 5h 和 7d 分别检测且可在同次刷新中各自产生样本。
- free、plus、team、bug_team、k12、pro 分组互不混算。
- Bug Team 不进入普通 Team。
- 空 plan 按现有历史/K12回退规则处理。
- stale、cost 倒退、401、类型变化和 reset 异常被拒绝。
- 确定性样本 ID 防止重试重复计数。
- 前 5 个样本建立基线，MAD 异常值不进入正式统计。
- 5 个稳定同向异常候选建立新代次。
- 日汇总重算具有幂等性。
- API 正确返回平均、最高、最低、样本总数和空统计。
- 前端切换站点后读取对应检测结果，生产构建通过。
