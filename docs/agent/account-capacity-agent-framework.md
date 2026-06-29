# OpenAI Pro 账号额度管理 Agent 框架

本文档根据 `docs/需求` 下的账号池、容量进度条、健康度逻辑和上传字段需求整理，用于指导后续开发一个账号额度管理 Agent。

Agent 的第一阶段定位是：

```text
只分析、预警、建议和创建待办，不直接自动操作 sub2api。
```

等本地状态、容量计算、待办闭环稳定后，再逐步开放自动补号、自动验证和自动推送。

## 1. 目标

系统用于管理 OpenAI Pro 等账号。每个账号有两类核心调用额度：

```text
5h 额度
7d 额度
```

Agent 需要基于系统内已有账号数据、sub2api 缓存数据、账号池配置、账号错误和封号记录，持续回答这些问题：

1. 当前账号池还能撑多久。
2. 什么时候需要补账号。
3. 需要补多少账号。
4. 是否需要提前预警。
5. 当前应该优先制作新账号、更新旧账号，还是处理问题账号。
6. 如果最近封号很多，下一批账号预计能存活多久。
7. 用户使用速度变化后，账号池是否还能满足未来需求。
8. 哪些账号类型、支付类型、上传批次、制作方式风险更高。

## 2. 不做什么

第一阶段 Agent 不直接做这些事：

```text
自动购买账号
自动制作账号
自动推送账号到 sub2api
自动删除远端账号
自动修改账号 JSON
绕过人工确认执行高风险动作
```

Agent 第一阶段只输出：

```text
容量分析
风险分析
补号建议
告警建议
待办建议
账号处理优先级
```

## 3. 数据输入

Agent 需要读取这些数据源。

### 3.1 本地账号库

来源：

```text
accounts
```

关键字段：

```js
{
  account_json: {
    credentials: {},
    extra: {}
  },
  metadata: {
    created_at,
    updated_at,
    email,
    email_session,
    account_type,
    payment_type,
    phone_bound,
    phone_number,
    manual_status_label,
    pool_status,
    pool_id,
    priority,
    used_quota,
    last_request_at,
    last_checked_at,
    last_error,
    problem_snapshot,
    analysis,
    batch_id,
    upload_intent
  }
}
```

Agent 需要重点使用：

```text
account_type: plus / free / pro / team / k12 / other
payment_type: paypal_multi / paypal_single / no_card / gopay / other
pool_status: library / available / reserve / active / problem / discarded
upload_intent: new / renew / purchase / historical / known_error
```

### 3.2 sub2api 缓存

来源：

```text
sub2api_groups_cache
sub2api_accounts_cache
sub2api_cache_meta
```

用途：

```text
读取远端实际使用账号
读取账号当前状态
读取 5h / 7d 已用额度
读取最后请求时间
读取账号是否可调度
读取远端错误信息
```

规则：

```text
Agent 不直接请求远端 sub2api。
Agent 默认只读 MongoDB 缓存。
只有同步任务和人工刷新任务负责访问远端。
```

### 3.3 API 账号池配置

来源：

```text
api_pools
```

关键字段：

```js
{
  name,
  account_type,
  site_id,
  active_group_id,
  verification_group_id,
  min_active,
  target_active,
  min_reserve,
  max_avg_5h_used,
  max_avg_7d_used,
  status
}
```

用途：

```text
判断每个实际使用池是否缺账号
判断备用池是否足够
判断补号建议属于哪个池
判断是否需要进入验证分组
```

### 3.4 操作日志和待办

来源：

```text
pool_actions
todo_items
audit_logs
```

用途：

```text
分析历史补号频率
分析历史封号频率
避免重复生成待办
追踪一次告警后是否已经有人处理
```

### 3.5 批次和来源

来源：

```text
import_batches
accounts.metadata.batch_id
```

用途：

```text
按上传批次统计存活率
按账号来源统计风险
按制作时间统计寿命
分析下一批账号预计可用多久
```

## 4. 账号额度默认值

第一版可以使用需求文档里的估算值作为默认额度。

| 账号类型 | 5h 总额度 | 7d 总额度 |
| --- | ---: | ---: |
| free | 2 | 10 |
| plus | 28 | 140 |
| team 子号 | 15 | 75 |
| k12 | 20 | 100 |
| pro | 500 | 2500 |

单位先统一按美元额度估算。后续如果 sub2api 返回 token、点数或其他单位，需要在服务层归一化成统一容量指标。

## 5. 核心指标

### 5.1 当前容量

对每个账号池分别计算：

```js
{
  active_account_count,
  reserve_account_count,
  problem_account_count,
  available_account_count,
  total_5h_capacity,
  used_5h,
  remaining_5h,
  total_7d_capacity,
  used_7d,
  remaining_7d
}
```

计算规则：

```text
total_5h_capacity = active 账号数 * 单账号 5h 默认额度
total_7d_capacity = active 账号数 * 单账号 7d 默认额度
remaining_5h = total_5h_capacity - used_5h
remaining_7d = total_7d_capacity - used_7d
```

如果远端账号缺失 5h / 7d 用量，不按 0 计算，而是计入缺失数据比例。

### 5.2 峰值容量倍数

需求文档里有两个峰值视角：

```text
最近一天 5h 峰值容量
7 天最高 24h 峰值容量
```

第一版建议定义：

```text
最近一天 5h 峰值倍数 = 当前 5h 总容量 / 最近 24h 内最高 5h 消耗
7 天最高 24h 峰值倍数 = 当前 7d 总容量 / 最近 7d 内最高 24h 消耗
```

用于回答：

```text
如果流量回到最近峰值，当前账号池能不能撑住。
```

### 5.3 当前速度可用天数

按照用户当前消耗速度估算：

```text
当前速度可用天数 = 当前可用总额度 / 最近速度折算出来的日消耗
```

速度建议同时计算三种：

```text
最近 1h 速度
最近 5h 速度
最近 24h 速度
```

第一版告警以较保守值为准：

```text
effective_daily_burn = max(最近 5h 折算日消耗, 最近 24h 日消耗)
```

### 5.4 账号存活时间

Agent 需要按维度统计账号寿命：

```text
账号寿命 = problem_at 或 discarded_at 或 banned_at - created_at
```

如果账号仍然 active / reserve：

```text
当前存活时间 = now - created_at
```

需要按这些维度聚合：

```text
account_type
payment_type
phone_bound
upload_intent
batch_id
pool_id
uploader
manual_status_label
```

输出：

```js
{
  median_survival_hours,
  p25_survival_hours,
  p75_survival_hours,
  fail_rate_24h,
  fail_rate_3d,
  fail_rate_7d
}
```

用于预测：

```text
下一批账号预计能活多久。
```

## 6. 健康度分级

第一版直接沿用需求文档里的健康度逻辑。

### 6.1 等待数据

```text
7 天最高 5h 峰值倍数没有数据
且 当前速度倍数没有数据
```

状态：

```text
waiting_data
```

### 6.2 耗尽

任一满足：

```text
可用账号 <= 2
最近一天 5h 峰值 < 0.2x
当前速度可用天数 < 6 小时
```

状态：

```text
exhausted
```

### 6.3 危险

任一满足：

```text
最近一天 5h 峰值 < 1x
当前速度可用天数 < 1 天
```

状态：

```text
danger
```

### 6.4 紧张

任一满足：

```text
最近一天 5h 峰值 < 1.5x
当前速度可用天数 < 3 天
```

状态：

```text
tight
```

### 6.5 触发补号建议

任一满足：

```text
最近一天 5h 峰值 < 1.5x
当前速度可用天数 < 2 天
```

Agent 应生成：

```text
need_more_accounts
```

### 6.6 健康

不满足危险或紧张条件。

状态：

```text
healthy
```

### 6.7 充裕

同时满足：

```text
最近一天 5h 峰值倍数 >= 3x
当前速度可用天数 >= 5 天
```

状态：

```text
abundant
```

### 6.8 十分充裕

同时满足：

```text
7 天最高 5h 峰值倍数 >= 5x
7 天最高 24h 最高消耗下，可用天数 >= 10 天
```

状态：

```text
very_abundant
```

## 7. 补号数量计算

Agent 需要分别计算三种补号数量。

### 7.1 保底补号

用于满足池配置：

```text
base_needed = max(0, target_active - healthy_active_count)
```

### 7.2 容量补号

用于把池恢复到目标健康度。

第一版目标：

```text
最近一天 5h 峰值倍数 >= 1.5x
当前速度可用天数 >= 3 天
```

计算：

```text
required_5h_capacity = recent_24h_peak_5h_usage * 1.5
required_7d_capacity = effective_daily_burn * 3
capacity_gap = max(required_5h_capacity - total_5h_capacity, required_7d_capacity - remaining_7d)
capacity_needed = ceil(capacity_gap / single_account_effective_capacity)
```

`single_account_effective_capacity` 第一版可按账号类型默认额度乘以折损系数：

```text
plus: min(28, 140 / 7) * 0.8
pro: min(500, 2500 / 7) * 0.8
free/team/k12 同理
```

### 7.3 风险补号

当最近封号或错误增多时，需要额外加 buffer。

```text
recent_failure_rate = 最近 24h 问题账号数 / 最近 24h active 账号数
risk_buffer = ceil(target_active * recent_failure_rate * risk_multiplier)
```

第一版：

```text
risk_multiplier = 1.5
```

### 7.4 最终建议数量

```text
suggested_add_count = max(base_needed, capacity_needed) + risk_buffer
```

同时需要限制极端值：

```text
suggested_add_count <= max(target_active, active_account_count) * 2
```

如果计算结果很大，Agent 应标记为：

```text
manual_review_required = true
```

## 8. 下一批账号存活时间预测

当系统告警、封号很多或用户准备补号时，Agent 需要输出下一批账号预计存活时间。

### 8.1 预测输入

```text
同账号类型的历史寿命
同支付类型的历史寿命
同上传意图的历史寿命
最近 24h / 3d / 7d 封号率
当前用户消耗速度
当前池压力
最近错误类型分布
```

### 8.2 第一版预测方法

不用复杂机器学习，先使用规则 + 历史分位数：

```text
base_survival = 同 account_type + payment_type 的历史 median_survival_hours
recent_risk_factor = 最近 24h 封号率 / 最近 7d 平均封号率
load_pressure_factor = max(1, 最近 5h 消耗 / 最近 7d 平均 5h 消耗)
predicted_survival = base_survival / max(1, recent_risk_factor, load_pressure_factor)
```

输出时同时给保守值：

```text
conservative_survival = p25_survival_hours / max(1, recent_risk_factor)
```

### 8.3 输出示例

```js
{
  account_type: "plus",
  payment_type: "paypal_multi",
  predicted_survival_hours: 72,
  conservative_survival_hours: 36,
  confidence: "medium",
  reasons: [
    "最近 24h 问题账号比例高于 7d 均值",
    "当前 5h 消耗速度高于历史均值",
    "同类型账号历史中位寿命为 96h"
  ]
}
```

## 9. Agent 输出

每次分析一个池，Agent 应生成标准结果。

```js
{
  pool_id,
  site_id,
  generated_at,
  health_status,
  severity,
  metrics: {
    active_account_count,
    reserve_account_count,
    problem_account_count,
    total_5h_capacity,
    used_5h,
    remaining_5h,
    total_7d_capacity,
    used_7d,
    remaining_7d,
    recent_24h_peak_5h_usage,
    seven_day_peak_24h_usage,
    peak_5h_multiplier,
    days_remaining_by_current_speed,
    missing_usage_ratio
  },
  recommendation: {
    action,
    suggested_add_count,
    suggested_reserve_push_count,
    suggested_make_new_count,
    suggested_recover_old_count,
    manual_review_required,
    reason
  },
  survival_prediction: {
    predicted_survival_hours,
    conservative_survival_hours,
    confidence,
    reasons
  },
  todos: []
}
```

`recommendation.action` 可选：

```text
no_action
watch
push_from_reserve
make_new_accounts
recover_old_accounts
handle_problem_accounts
manual_review
```

## 10. 待办类型

Agent 第一版可以创建或更新这些待办。

```text
agent_capacity_warning
agent_need_more_accounts
agent_reserve_low
agent_failure_spike
agent_usage_spike
agent_survival_drop
agent_data_incomplete
agent_manual_review_required
```

去重键建议：

```text
dedupe_key = "{todo_type}:{pool_id}:{severity}"
```

如果同类待办已经打开，不重复创建，只更新：

```text
occurrence_count
summary
suggested_action
updated_at
```

## 11. 告警策略

### 11.1 普通告警

触发：

```text
health_status = tight
```

动作：

```text
创建 todo
前端展示黄色状态
建议补充备用池
```

### 11.2 危险告警

触发：

```text
health_status = danger
```

动作：

```text
创建高优先级 todo
建议立即从备用池推送账号
如果备用池不足，建议制作新账号
```

### 11.3 耗尽告警

触发：

```text
health_status = exhausted
```

动作：

```text
创建最高优先级 todo
建议立即人工处理
允许后续接入通知渠道
标记 manual_review_required
```

### 11.4 封号激增告警

触发：

```text
最近 24h 问题账号数 >= max(3, 最近 7d 日均问题账号数 * 2)
```

动作：

```text
分析问题账号共同特征
预测下一批账号寿命下降
建议提高补号数量
建议优先检查同批次/同支付类型账号
```

## 12. Agent 服务模块建议

后端建议新增：

```text
backend/app/services/agent_capacity.py
backend/app/services/agent_survival.py
backend/app/services/agent_recommendations.py
backend/app/routers/agent.py
```

### 12.1 agent_capacity.py

负责：

```text
读取池数据
计算 5h / 7d 容量
计算峰值倍数
计算可用天数
计算健康度
```

### 12.2 agent_survival.py

负责：

```text
统计账号寿命
统计封号率
按维度聚合风险
预测下一批账号存活时间
```

### 12.3 agent_recommendations.py

负责：

```text
计算补号数量
判断优先制作新账号还是更新旧账号
生成建议动作
生成或更新 todo_items
```

### 12.4 routers/agent.py

第一版接口：

```text
POST /api/agent/pools/{pool_id}/analyze
GET  /api/agent/pools/{pool_id}/latest
GET  /api/agent/pools
```

后续接口：

```text
POST /api/agent/run
GET  /api/agent/reports
GET  /api/agent/risk-factors
```

## 13. Agent 结果存储

建议新增集合：

```text
agent_runs
agent_pool_reports
```

### 13.1 agent_runs

```js
{
  _id,
  run_type: "manual" | "scheduled",
  status: "running" | "succeeded" | "failed",
  started_at,
  finished_at,
  summary: {},
  error_message
}
```

### 13.2 agent_pool_reports

```js
{
  _id,
  run_id,
  pool_id,
  site_id,
  generated_at,
  health_status,
  severity,
  metrics: {},
  recommendation: {},
  survival_prediction: {},
  created_todo_ids: []
}
```

用途：

```text
前端展示最近一次分析结果
对比历史趋势
追踪一次告警前后的变化
```

## 14. 前端页面建议

可以在现有 `Agent分析` 页面基础上开发。

第一版页面展示：

```text
每个账号池健康状态
5h / 7d 容量进度条
最近一天峰值倍数
7 天最高 24h 峰值倍数
当前速度可用天数
建议补号数量
备用池可推送数量
预计下一批账号存活时间
主要风险原因
一键生成/刷新分析
```

颜色沿用需求：

```text
等待数据：灰色
耗尽：红色闪烁
危险：红色
紧张：黄色
健康：绿色
充裕：蓝色
十分充裕：紫色特殊
```

## 15. 迭代路线

### Milestone 1：只读分析

完成：

```text
读取账号池和 sub2api 缓存
计算容量
计算健康度
计算补号建议
生成分析报告
```

不写入：

```text
账号状态
sub2api
待办
```

### Milestone 2：待办闭环

完成：

```text
生成 agent_* 待办
告警去重
前端展示 Agent 建议
```

### Milestone 3：风险和寿命预测

完成：

```text
按批次/支付方式/账号类型统计寿命
识别封号激增
预测下一批账号存活时间
动态调整补号数量
```

### Milestone 4：半自动执行

完成：

```text
Agent 推荐从备用池推送账号
人工确认后执行 push-to-active
人工确认后执行验证
```

### Milestone 5：受控自动化

完成：

```text
仅在低风险场景自动从备用池补位
高风险场景仍要求人工确认
所有自动动作写入 pool_actions 和 audit_logs
```

## 16. 第一版验收标准

第一版 Agent 完成后，系统至少能回答：

```text
当前每个账号池是什么健康状态？
当前 5h / 7d 额度还剩多少？
按当前用户使用速度还能撑多久？
是否需要补号？
建议补多少号？
备用池够不够？
如果最近封号很多，建议额外补多少号？
下一批同类型账号预计能活多久？
为什么给出这个建议？
```

第一版建议不要追求自动执行，先把判断是否准确跑通。
