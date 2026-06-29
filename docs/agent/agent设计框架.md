# Agent 设计框架

本文档基于：

- `docs/agent/整体框架需求.md`
- `docs/agent/account-capacity-agent-framework.md`
- `docs/需求/账号探测.md`
- 当前主分支已有的 API 账号池状态、账号探测和通知系统

目标是为后续开发一个高兼容、高扩展的账号运营 Agent 做总设计。本文档作为 Agent 模块后续调整的主入口，后续逻辑重做、可调用能力扩展、模型策略变化，都优先更新本文档。

## 1. Agent 定位

Agent 不是单纯聊天助手，而是账号池运营决策系统。

它需要同时支持两种模式：

```text
人工对话触发：用户在 Agent 分析面板发出指令，Agent 根据指令分析并返回结果。
系统事件触发：系统检测到 401、容量红线、备用池不足等事件后，Agent 自动分析并把建议发到钉钉或前端。
```

第一阶段 Agent 的边界：

```text
可以读取数据
可以分析风险
可以生成建议
可以创建待办
可以通过钉钉/TG/前端发送建议
不直接自动买号
不直接自动制作账号
不绕过人工确认执行高风险操作
```

后续可以逐步开放半自动能力，例如：

```text
Agent 建议从备用池补号
人工确认后执行推送
低风险场景自动创建待办
低风险场景自动发送通知
```

## 2. 总体架构

整体采用 LangChain 或 LangGraph 风格的 Agent 编排。

推荐逻辑上分为四层：

```text
交互层
调度层
LLM 决策层
Agent 可调用能力层
```

### 2.1 交互层

入口包括：

```text
前端 Agent 分析页面
系统后台定时任务
账号探测 401 事件
容量检查告警
钉钉/TG 通知回传或群聊指令，后续阶段
```

第一阶段先做：

```text
前端 Agent 分析页面
后台事件触发分析
钉钉机器人发送 Agent 建议
```

### 2.2 调度层

调度层负责决定 Agent 什么时候运行。

触发类型：

```text
manual_chat
manual_analyze_pool
scheduled_capacity_review
probe_401_detected
notification_batch_sent
capacity_warning
reserve_low
data_stale
```

调度层需要写运行记录：

```text
agent_runs
agent_pool_reports
```

### 2.3 LLM 决策层

采用多 level 模式。

```text
Level 1：决策核心模型
Level 2：能力执行模型
```

Level 1 负责：

```text
理解用户指令
决定需要哪些数据
选择能力调用计划
综合能力结果
判断风险等级
给出最终建议
生成可读解释
决定是否需要通知钉钉或创建待办
```

Level 2 负责：

```text
执行简单能力调用
整理能力返回
做格式转换
生成数据摘要
调用通知能力
调用待办能力
```

注意：

```text
Level 2 不做最终业务决策。
最终建议必须回到 Level 1。
```

### 2.4 Agent 可调用能力层

可调用能力层不直接让 LLM 查数据库，而是把数据读取、规则计算、通知、待办等操作封装成稳定能力。代码实现上遵循 LangChain 规范，使用 `@tool` 装饰器定义；但在产品和架构语义上统一称为 Agent 可调用能力。

第一阶段只读能力：

```text
api_pool_status.get
account_probe.get
refill_decision.calculate
```

建议补充的能力：

```text
notification.send
todo.create_or_update
agent_report.save
account_lifecycle.query
capacity_forecast.compute
risk_trend.compute
```

能力不是用户直接面对的接口。用户面对的是 Agent，Agent 根据任务目标决定是否调用这些能力。

## 3. 模型配置

所有 LLM 请求地址、key、模型名都放到环境变量，不写死在代码里。

建议环境变量：

```text
AGENT_LLM_BASE_URL=
AGENT_LLM_API_KEY=
AGENT_LLM_PROVIDER=openai_compatible

AGENT_LEVEL1_MODEL=
AGENT_LEVEL2_MODEL=

AGENT_LEVEL1_TEMPERATURE=0.2
AGENT_LEVEL2_TEMPERATURE=0
AGENT_REQUEST_TIMEOUT_SECONDS=60
AGENT_MAX_TOOL_ROUNDS=8
```

模型命名原则：

```text
Level 1 使用强推理模型，负责最终决策。
Level 2 使用轻量模型，负责能力执行和结构化整理。
具体模型名通过环境变量配置，不在代码里硬编码。
```

如果后续确实要配置成类似：

```text
Level 1 = 高性能 GPT-5.x / 其他强推理模型
Level 2 = mini / 轻量模型
```

也应该只在 `.env` 中配置。

## 4. 推荐工作流

用户提出的初始工作流是合理的：

```text
用户发出指令
Level 1 判断意图
Level 1 选择可调用能力
Level 2 执行可调用能力
Level 2 返回能力结果摘要
Level 1 综合结果
Level 1 输出决策
```

建议优化为一个可追踪流程：

```text
1. 创建 agent_run
2. Level 1 解析任务
3. Level 1 生成 capability_plan
4. Level 2 执行可调用能力
5. 能力结果写入 agent_run.steps
6. Level 1 汇总分析
7. Level 1 生成 recommendation
8. 保存 agent_pool_report
9. 根据策略决定是否通知/创建待办
10. 返回前端或发送机器人消息
```

### 4.1 人工对话流程

```text
用户在 Agent 分析页面输入：
“看看 Pro 池今天还要不要补号”

Agent：
1. 读取 API 账号池状态。
2. 读取账号探测最近 1h/6h/24h/7d 401 趋势。
3. 读取最近账号 session 寿命。
4. 计算容量缺口和风险 buffer。
5. 返回建议。
```

### 4.2 系统事件触发流程

例如账号探测发现 Pro 401：

```text
sub2api_account_probe 写入 401_detected
notification_batch 3 分钟聚合
触发 agent event
Agent 自动读取当前容量和最近 401 趋势
Agent 生成补号建议
Agent 发送钉钉消息或创建待办
```

第一阶段可以先不直接接入“通知批次发送后自动触发 Agent”，而是新增独立 Agent scheduler 周期性读取未处理事件。

推荐：

```text
Agent scheduler 每 1-3 分钟运行一次
读取最近未处理的 critical 事件
按池聚合事件
生成 Agent 建议
标记事件已被 Agent 消费
```

这样兼容性更好，不会强耦合到钉钉通知代码。

## 5. Agent 常驻运行设计

Agent 可以像 sub2api 缓存刷新、账号探测一样常驻运行。

后端启动时创建：

```python
agent_scheduler_task = asyncio.create_task(agent_scheduler_loop(db))
```

建议新增：

```text
backend/app/services/agent_scheduler.py
```

循环逻辑：

```text
while True:
    扫描需要 Agent 处理的事件
    扫描需要定时分析的账号池
    执行轻量分析或创建 agent_run
    sleep AGENT_SCHEDULER_INTERVAL_SECONDS
```

建议环境变量：

```text
AGENT_ENABLED=false
AGENT_SCHEDULER_INTERVAL_SECONDS=180
AGENT_AUTO_NOTIFY_ENABLED=true
AGENT_AUTO_TODO_ENABLED=true
AGENT_AUTO_EXECUTE_ENABLED=false
```

默认建议：

```text
AGENT_ENABLED=false
```

上线后先手动开启，避免开发期误发通知。

## 6. 数据源分工

Agent 需要把两个主数据源分清楚。

### 6.1 API 账号池状态

定位：

```text
当前容量仪表盘
```

主要回答：

```text
现在够不够
还能撑多久
缺多少账号
备用池够不够
是否低于红线
```

可读取：

```text
api_pools
sub2api_groups_cache
sub2api_accounts_cache
sub2api_cache_meta
capacity_summary
accounts.metadata.pool_status
```

提供给 Agent 的核心指标：

```text
active_account_count
reserve_account_count
available_accounts
5h used / remaining
7d used / remaining
recent_day_five_hour_peak_multiple
seven_day_five_hour_peak_multiple
current_speed_days
seven_day_peak_speed_days
capacity health status
```

### 6.2 账号探测

定位：

```text
长期运营黑匣子
```

主要回答：

```text
为什么会缺
风险是否变高
账号能活多久
哪类账号质量差
是否需要增加补号 buffer
```

可读取：

```text
group_observability_settings
remote_account_identities
remote_account_sessions
remote_account_status_events
remote_account_probe_samples
remote_account_probe_runs
remote_account_probe_meta
notification_events
notification_batches
notification_deliveries
```

提供给 Agent 的核心指标：

```text
最近 1h / 6h / 24h / 7d 401 数量
401 后恢复数量
账号平均存活时长
账号 p25 / p50 / p90 存活时长
重复邮箱告警数量
同邮箱多次进入 sub2 次数
当前探测是否新鲜
探测失败次数
账号删除/重加记录
用量到多少后更容易 401
```

### 6.3 组合方式

Agent 分析时先读容量，再读风险。

```text
API 账号池状态告诉 Agent：缺不缺、缺多少。
账号探测告诉 Agent：为什么缺、会不会继续恶化、要不要多补。
```

最终建议：

```text
建议补号数量 = 容量缺口 + 风险 buffer
```

## 7. Agent 可调用能力设计

### 7.1 api_pool_status.get

用途：

```text
获取指定站点、分组或本地 api_pool 的当前容量状态。
```

输入：

```js
{
  "site_id": "default",
  "pool_id": "...",
  "group_id": 1,
  "include_accounts": false
}
```

输出：

```js
{
  "pool": {},
  "capacity_summary": {},
  "cache_meta": {},
  "reserve_summary": {}
}
```

### 7.2 account_probe.get

用途：

```text
获取账号探测数据、401 趋势、账号寿命和事件。
```

输入：

```js
{
  "site_id": "default",
  "group_id": 1,
  "window": "24h",
  "account_type": "pro",
  "include_events": true
}
```

输出：

```js
{
  "probe_fresh": true,
  "last_probe_at": "...",
  "event_counts": {},
  "survival_stats": {},
  "recent_401_events": [],
  "duplicate_email_alerts": []
}
```

### 7.3 decision.dispatch

用途：

```text
把 Agent 决策下发到某个通道。
```

下发目标：

```text
frontend_chat
dingtalk
telegram
todo
audit_only
```

输入：

```js
{
  "target": "dingtalk",
  "severity": "critical",
  "title": "Pro 池补号建议",
  "message": "...",
  "report_id": "..."
}
```

第一阶段建议：

```text
用户主动对话 -> 默认返回 frontend_chat。
系统自动触发 -> critical/high 发送 dingtalk，同时写 agent_report。
非紧急 -> 只写 todo 或前端报告。
```

### 7.4 todo.create_or_update

用途：

```text
创建或更新 Agent 待办。
```

建议待办类型：

```text
agent_need_more_accounts
agent_capacity_warning
agent_failure_spike
agent_survival_drop
agent_reserve_low
agent_data_stale
agent_manual_review
```

### 7.5 agent_report.save

用途：

```text
保存 Agent 的每次分析结果，方便前端查看和后续追踪。
```

## 8. 决策输出结构

Agent 每次最终输出统一结构。

```js
{
  "run_id": "...",
  "trigger": "manual_chat | scheduled | probe_401_detected | capacity_warning",
  "site_id": "default",
  "pool_id": "...",
  "group_id": 1,
  "severity": "info | warning | danger | critical",
  "decision": {
    "action": "no_action | watch | notify | create_todo | refill_needed | manual_review",
    "suggested_add_count": 0,
    "suggested_push_from_reserve_count": 0,
    "suggested_make_new_count": 0,
    "suggested_recover_old_count": 0,
    "manual_review_required": false
  },
  "capacity": {},
  "risk": {},
  "survival_prediction": {},
  "reasoning_summary": [],
  "dispatch": {
    "frontend": true,
    "dingtalk": false,
    "telegram": false,
    "todo": false
  }
}
```

注意：

```text
reasoning_summary 是可展示的业务原因，不保存模型隐式思考过程。
```

## 9. 决策规则第一版

第一版先使用规则和统计，不依赖复杂模型自由发挥。

### 9.1 容量缺口

```text
如果 current_speed_days < 1 天 -> critical
如果 current_speed_days < 3 天 -> danger
如果 recent_day_five_hour_peak_multiple < 1.5x -> warning/danger
如果 reserve_available_accounts < min_reserve -> reserve_low
```

### 9.2 401 风险

```text
如果 1h Pro 401 >= 3 -> danger
如果 24h Pro 401 高于 7d 日均 2 倍 -> danger
如果 24h Pro 401 高于 7d 日均 3 倍 -> critical
```

### 9.3 存活时间下降

```text
如果最近 24h 中位存活时长 < 最近 7d 中位的 50% -> danger
如果 p25 存活时长持续下降 -> warning
```

### 9.4 补号建议

```text
base_needed = 容量缺口账号数
risk_buffer = ceil(target_active * recent_failure_rate * risk_multiplier)
suggested_add_count = base_needed + risk_buffer
```

第一版：

```text
risk_multiplier = 1.5
```

当 401 激增时：

```text
risk_multiplier = 2.0 到 3.0
```

## 10. Agent 与通知系统关系

当前主分支已有通知系统：

```text
notification_channels
notification_events
notification_batches
notification_deliveries
```

已有 Pro 401 通知逻辑：

```text
账号探测发现 Pro 401
3 分钟内聚合
发送钉钉
写 notification_events / deliveries
```

Agent 不应该替代这套逻辑。

推荐关系：

```text
现有通知系统负责第一时间告警。
Agent 负责在告警后补充运营决策。
```

例如：

```text
钉钉第一条：Pro 401 封号告警，告诉群里封了几个。
Agent 第二条：根据容量和风险分析，建议补几个、优先处理什么。
```

第一阶段可以让 Agent scheduler 读取：

```text
notification_batches
remote_account_status_events
```

找到未分析过的 401 批次，生成 Agent 决策。

## 11. 前端设计

入口放在当前左侧菜单的：

```text
Agent分析
```

页面第一阶段包括：

```text
对话输入框
最近 Agent 分析报告
每个账号池当前建议
系统自动触发记录
数据新鲜度
最近 401 趋势
最近补号建议
```

对话示例：

```text
今天 Pro 池需要补多少号？
最近封号多不多？
哪个分组风险最高？
现在要不要从备用池推账号？
最近账号平均能活多久？
```

页面不应只做聊天框，也要有可扫描的运营面板。

## 12. 后端模块建议

建议新增：

```text
backend/app/services/agent_llm.py
backend/app/services/agent_capabilities.py
backend/app/services/agent_tools.py
backend/app/services/agent_capacity.py
backend/app/services/agent_probe.py
backend/app/services/agent_decision.py
backend/app/services/agent_scheduler.py
backend/app/routers/agent.py
```

职责：

```text
agent_llm.py：封装 Level 1 / Level 2 模型调用。
agent_capabilities.py：按 LangChain `@tool` 规范定义 Agent 可调用能力。
agent_tools.py：历史兼容入口，返回能力清单。
agent_capacity.py：读取 API 账号池状态并生成容量摘要。
agent_probe.py：读取账号探测数据并生成风险摘要。
agent_decision.py：执行补号和风险规则，生成最终 recommendation。
agent_scheduler.py：常驻事件扫描和自动分析。
routers/agent.py：前端对话和分析接口。
```

## 13. 建议新增集合

```text
agent_runs
agent_run_steps
agent_pool_reports
agent_event_consumptions
```

### 13.1 agent_runs

记录一次 Agent 运行。

```js
{
  "_id": "...",
  "trigger": "manual_chat",
  "status": "running | succeeded | failed",
  "user_message": "...",
  "started_at": "...",
  "finished_at": "...",
  "model_level1": "...",
  "model_level2": "...",
  "summary": {},
  "error_message": null
}
```

### 13.2 agent_run_steps

记录 Agent 可调用能力的调用步骤。

```js
{
  "_id": "...",
  "run_id": "...",
  "step_type": "capability_call | llm_summary | dispatch",
  "capability_name": "api_pool_status.get",
  "input": {},
  "output_summary": {},
  "status": "succeeded | failed",
  "created_at": "..."
}
```

### 13.3 agent_pool_reports

保存最终报告。

```js
{
  "_id": "...",
  "run_id": "...",
  "site_id": "...",
  "pool_id": "...",
  "group_id": 1,
  "severity": "warning",
  "decision": {},
  "capacity": {},
  "risk": {},
  "survival_prediction": {},
  "created_todo_ids": [],
  "notification_event_ids": [],
  "created_at": "..."
}
```

### 13.4 agent_event_consumptions

避免自动事件被重复分析。

```js
{
  "_id": "...",
  "source_event_type": "notification_batch | remote_account_status_event",
  "source_event_id": "...",
  "agent_run_id": "...",
  "status": "consumed",
  "created_at": "..."
}
```

## 14. 安全和控制

Agent 必须有开关和边界。

```text
默认不开启自动执行。
自动通知和自动待办可以单独开启。
所有自动分析都写 agent_runs。
所有通知都写 notification_events。
所有待办都去重。
所有高风险动作需要人工确认。
```

权限建议：

```text
owner/admin：配置 Agent、模型、通知策略。
maintainer：运行分析、查看报告、创建待办。
viewer：只读分析结果。
```

## 15. 第一阶段开发范围

第一阶段只做最小闭环：

```text
1. 新增 Agent 文档和接口设计。
2. 新增 Agent 分析页面占位和对话入口。
3. 新增只读能力 api_pool_status.get。
4. 新增只读能力 account_probe.get。
5. 新增规则版 decision engine。
6. 支持人工触发分析。
7. 保存 agent_runs 和 agent_pool_reports。
```

第一阶段不做：

```text
自动推送账号
自动执行补号
钉钉群聊反向对话
复杂多 Agent 协作
训练模型
长期 hourly/daily 聚合
```

## 16. 第二阶段开发范围

```text
1. Agent scheduler 常驻运行。
2. 捕获 Pro 401 notification_batches。
3. 自动生成 Agent 决策。
4. critical 场景通过钉钉发送 Agent 补充建议。
5. 自动创建去重待办。
6. 前端展示自动触发记录。
```

## 17. 第三阶段开发范围

```text
1. 接入 account_ops_hourly_stats / account_ops_daily_stats。
2. 做账号寿命预测。
3. 做购买来源、上传人、支付类型质量分析。
4. 做成本和账号质量分析。
5. 支持更复杂的策略规则配置。
```

## 18. 待确认问题

后续开发前需要逐步确认：

```text
Agent 是否使用 OpenAI 官方 API、兼容 API，还是内部代理。
Level 1 / Level 2 的默认模型名。
是否允许 Agent 自动发送钉钉。
钉钉是否只发 critical，还是 warning 也发。
Agent 报告是否需要用户已读状态。
Agent 是否要对接现有 todo_items。
自动事件扫描频率。
Agent 分析是否按 site、group、api_pool 三个维度都支持。
```

## 19. 当前推荐路线

最稳妥的路线：

```text
先做只读 Agent。
先把 API 账号池状态和账号探测数据接入。
先让 Agent 能解释为什么建议补号。
再接入自动事件触发。
最后考虑半自动执行。
```

也就是：

```text
先让 Agent 会看，会说清楚，再让 Agent 会提醒，最后才让 Agent 会行动。
```

## 20. 最小可落地 MVP 开发计划

MVP 目标只保留一句话：

```text
用户在 Agent分析 页面点击“分析 Pro 池”，系统返回当前缺不缺号、建议补几个、为什么。
```

这一版不追求真正的 LLM Agent，不追求 LangChain 完整编排，不追求自动通知。先把最关键的数据链路、规则决策和页面展示跑通。

### 20.1 MVP 原则

```text
先规则，后 LLM。
先只读，后执行。
先人工触发，后自动触发。
先单池分析，后全局调度。
先页面展示，后钉钉主动推送。
```

MVP 不接触高风险动作：

```text
不自动推送账号到 sub2api。
不自动删除账号。
不自动修改账号状态。
不自动制作新账号。
不自动发送钉钉，除非后续单独打开。
```

### 20.2 MVP 用户路径

第一版用户操作流程：

```text
1. 用户进入 Agent分析 页面。
2. 页面展示可分析的账号池列表。
3. 用户选择一个账号池，例如 Pro 池。
4. 用户点击“分析”。
5. 后端读取 API 账号池状态和账号探测数据。
6. 后端用规则生成建议。
7. 前端展示分析结果。
```

输出示例：

```text
Pro 池当前状态：紧张
建议补号：8 个
建议动作：先从备用池推 3 个，同时制作 5 个新号

原因：
1. 当前速度预计还能撑 1.7 天。
2. 最近 24h 出现 5 个 Pro 401。
3. 备用池只剩 3 个，低于安全线。
4. 最近封号速度高于安全阈值，额外增加 3 个风险 buffer。
```

### 20.3 MVP 后端范围

新增最少模块：

```text
backend/app/services/agent_capacity.py
backend/app/services/agent_probe.py
backend/app/services/agent_decision.py
backend/app/routers/agent.py
```

暂不新增：

```text
agent_llm.py
agent_scheduler.py
agent_capabilities.py
LangChain 编排
后台常驻任务
```

原因：

```text
MVP 先验证业务规则和数据源是否够用。
LLM 和 scheduler 是第二阶段能力。
```

### 20.4 后端任务清单

#### 任务 1：Agent 路由

新增：

```text
backend/app/routers/agent.py
```

接口：

```text
GET  /api/agent/pools
POST /api/agent/pools/{pool_id}/analyze
```

职责：

```text
GET /pools 返回可分析账号池列表。
POST /analyze 执行一次只读分析。
```

权限：

```text
owner / admin / maintainer 可运行分析。
viewer 第一版可只读历史报告，暂不允许触发分析。
```

#### 任务 2：容量读取服务

新增：

```text
backend/app/services/agent_capacity.py
```

输入：

```text
pool_id
```

读取：

```text
api_pools
sub2api_groups_cache
sub2api_accounts_cache
accounts
capacity_summary
```

输出：

```js
{
  "pool_id": "...",
  "site_id": "...",
  "group_id": 1,
  "account_type": "pro",
  "active_account_count": 0,
  "reserve_account_count": 0,
  "available_accounts": 0,
  "current_speed_days": null,
  "recent_day_five_hour_peak_multiple": null,
  "seven_day_five_hour_peak_multiple": null,
  "five_hour_remaining_usd": null,
  "seven_day_remaining_usd": null,
  "cache_fresh": true,
  "last_refreshed_at": "..."
}
```

第一版如果某些字段拿不到：

```text
允许返回 null。
不要阻塞整体分析。
在 reasons 里提示数据不足。
```

#### 任务 3：账号探测读取服务

新增：

```text
backend/app/services/agent_probe.py
```

输入：

```text
site_id
group_id
account_type
```

读取：

```text
remote_account_probe_meta
remote_account_probe_runs
remote_account_status_events
remote_account_identities
remote_account_sessions
```

第一版只需要返回：

```js
{
  "probe_fresh": true,
  "last_probe_at": "...",
  "detected_401_1h": 0,
  "detected_401_24h": 0,
  "detected_401_7d": 0,
  "recovered_24h": 0,
  "duplicate_email_alert_count": 0,
  "median_survival_hours_7d": null,
  "recent_events": []
}
```

兼容说明：

```text
早期 MVP 曾使用 pro_401_1h / pro_401_24h / pro_401_7d 字段名。
后续统一使用 detected_401_*，旧字段只作为兼容映射保留。
```

暂不做复杂统计：

```text
不按购买来源分析。
不按上传人分析。
不按支付方式分析。
不做复杂寿命预测。
```

#### 任务 4：规则版决策服务

新增：

```text
backend/app/services/agent_decision.py
```

输入：

```text
capacity_summary
probe_summary
pool_config
```

输出：

```js
{
  "severity": "healthy | watch | warning | danger | critical",
  "suggested_add_count": 0,
  "suggested_push_from_reserve_count": 0,
  "suggested_make_new_count": 0,
  "manual_review_required": false,
  "reasons": [],
  "suggested_actions": []
}
```

第一版规则：

```text
current_speed_days < 1 -> critical
current_speed_days < 3 -> danger
recent_day_five_hour_peak_multiple < 1 -> danger
recent_day_five_hour_peak_multiple < 1.5 -> warning
reserve_account_count < min_reserve -> warning
detected_401_1h >= 3 -> danger
detected_401_24h >= 5 -> danger
probe_fresh = false -> warning
```

补号计算：

```text
base_needed = max(0, target_active - available_accounts)
risk_buffer = ceil(detected_401_24h * 1.5)
reserve_gap = max(0, min_reserve - reserve_account_count)
suggested_add_count = max(base_needed, reserve_gap) + risk_buffer
```

如果 `suggested_add_count = 0` 但状态是 warning：

```text
建议 watch，不建议立即补号。
```

#### 任务 5：报告存储

MVP 可以先只新增一个集合：

```text
agent_pool_reports
```

暂不新增完整的：

```text
agent_runs
agent_run_steps
agent_event_consumptions
```

`agent_pool_reports` 字段：

```js
{
  "_id": "...",
  "pool_id": "...",
  "site_id": "...",
  "group_id": 1,
  "trigger": "manual",
  "severity": "warning",
  "capacity": {},
  "probe": {},
  "decision": {},
  "created_by": "...",
  "created_at": "..."
}
```

后续接 LLM 和 scheduler 时，再拆出 `agent_runs`。

### 20.5 MVP 前端范围

修改：

```text
frontend/src/pages/IntroPage.tsx 或新增 AgentAnalysisPage.tsx
frontend/src/App.tsx
frontend/src/types.ts
frontend/styles.css
```

建议直接新增：

```text
frontend/src/pages/AgentAnalysisPage.tsx
```

页面内容：

```text
账号池选择器
分析按钮
当前状态摘要
建议补号数量
建议动作
原因列表
容量数据块
账号探测数据块
最近一次分析时间
```

第一版不做：

```text
聊天上下文
流式输出
复杂图表
多轮对话
钉钉发送按钮
自动刷新
```

### 20.6 MVP API 返回结构

`POST /api/agent/pools/{pool_id}/analyze` 返回：

```js
{
  "report_id": "...",
  "pool": {
    "id": "...",
    "name": "Pro 池",
    "account_type": "pro",
    "site_id": "...",
    "active_group_id": 1
  },
  "severity": "danger",
  "headline": "Pro 池当前紧张，建议补 8 个号",
  "decision": {
    "suggested_add_count": 8,
    "suggested_push_from_reserve_count": 3,
    "suggested_make_new_count": 5,
    "manual_review_required": false,
    "suggested_actions": [
      "先从备用池推 3 个账号",
      "同时制作 5 个新 Pro 账号"
    ]
  },
  "reasons": [
    "当前速度预计还能撑 1.7 天",
    "最近 24h 出现 5 个 Pro 401",
    "备用池只剩 3 个，低于安全线"
  ],
  "capacity": {},
  "probe": {},
  "created_at": "..."
}
```

### 20.7 MVP 验收标准

完成后必须能做到：

```text
1. 页面能列出账号池。
2. 用户能选择一个账号池点击分析。
3. 后端能读取容量数据。
4. 后端能读取账号探测 401 数据。
5. 后端能返回建议补号数量。
6. 前端能展示建议动作和原因。
7. 分析结果能保存到 agent_pool_reports。
8. 不会对 sub2api 产生任何写操作。
9. 不会自动发送钉钉。
10. 没有数据时能明确提示“数据不足”，而不是报错。
```

### 20.8 MVP 开发顺序

推荐按这个顺序开发：

```text
1. 后端 agent_capacity.py
2. 后端 agent_probe.py
3. 后端 agent_decision.py
4. 后端 routers/agent.py
5. 后端 main.py 注册 agent router
6. 前端 AgentAnalysisPage.tsx
7. 前端 App.tsx 接入 Agent分析页面
8. 前端样式
9. 本地 build 检查
10. 手动接口测试
```

### 20.9 MVP 之后的下一步

MVP 稳定后，再进入第二步：

```text
引入 LLM，但只用于解释和自然语言理解。
规则引擎仍然负责关键数值决策。
```

第二步再做：

```text
用户自然语言提问
LLM 判断要分析哪个池
LLM 触发 Agent 调用只读能力
规则引擎生成决策
LLM 把决策解释成人话
```

第三步再做：

```text
Agent scheduler
捕获 Pro 401 批次
自动生成补充建议
critical 情况发送钉钉
```
