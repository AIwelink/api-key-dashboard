# 阶段九：Agent 运维工作台设计

## 1. 阶段目标

阶段九新增一个独立前端入口：

```text
Agent工作台
```

位置：

```text
主导航
-> Agent分析
-> Agent工作台
-> 系统管理
```

它不是替代 `Agent分析`，而是承接完整运维、调试和审计能力。

`Agent分析` 保持轻量：

- 选池。
- 手动分析。
- 向 Agent 提问。
- 查看主决策。
- 查看当前 task 摘要。
- 查看 scheduler 简短状态。

`Agent工作台` 承接完整追踪：

- task 看板。
- run 列表。
- step trace。
- event trigger 历史。
- eval run 和 eval result。
- memory summary 历史。
- notification draft / dispatch 历史。
- 后续 pricing decision。

## 2. 核心原则

### 2.1 单开窗口，不塞回 Agent 分析页

Agent 分析页是日常入口。

Agent 工作台是运维入口。

如果把所有 task、run、step、trigger、eval、memory、notification 都塞进 Agent 分析页，会重新变成拥挤页面，也会打断日常问答体验。

因此阶段九明确：

```text
Agent分析 = 当前决策与对话
Agent工作台 = 历史追踪、调试和审计
```

### 2.2 第一版只读为主

阶段九前端第一版优先做只读展示：

- 不自动推号。
- 不自动买号。
- 不自动删号。
- 不刷新 sub2api。
- 不启动账号探测。
- 不直接改价格。

允许的显式人工操作：

- task feedback。
- task transition。
- task review。
- dispatch alert draft。
- run eval suite。

这些操作必须走后端已有审计接口。

### 2.3 先看清楚，再增加自动化

工作台是后续能力的保护层。

在继续做用户用量归因、价格策略 Agent、Skills / Playbook、多 LLM 编排前，需要先能看清：

- Agent 为什么被唤醒。
- 它读了什么上下文。
- 它生成了什么 decision。
- 它怎么更新 task。
- 它什么时候要求人工。
- 它什么时候复盘。
- 它有没有评测退化。
- 它有没有重复告警。

## 3. 前端入口设计

### 3.1 导航

新增 view：

```text
agent-workbench
```

路径：

```text
/agent-workbench
```

导航位置：

```text
Agent分析
Agent工作台
系统管理
```

当前落点：

```text
frontend/src/types.ts
frontend/src/App.tsx
frontend/src/pages/AgentWorkbenchPage.tsx
frontend/styles.css
```

### 3.2 页面标题

```text
Agent工作台
```

说明：

```text
面向运维和调试的完整工作台，用来追踪 task、run、trace、trigger、eval、memory 和 notification。
```

### 3.3 第一版 Tab

第一版建议 9 个 tab：

```text
Tasks
Runs
Trace
Triggers
Evals
Memory
Notifications
Pricing
Usage Attribution
```

后续扩展：

```text
Playbooks
```

## 4. Tasks

### 4.1 目标

展示持续运营问题，而不是单次运行。

字段：

- task_id。
- pool_id。
- task_type。
- status。
- severity。
- title。
- requires_human_confirm。
- alert_status。
- next_check_at。
- review_after。
- current_decision_id。
- latest_state_reason。
- updated_at。

### 4.2 分组

按状态分组：

```text
open
observing
waiting_human
alert_drafted
review_due
closed
failed
```

### 4.3 操作

第一版可支持：

```text
已补号
先观察
关闭
转复盘
发送告警草稿
```

对应接口：

```text
GET  /api/agent/tasks
GET  /api/agent/tasks/{task_id}
POST /api/agent/tasks/{task_id}/feedback
POST /api/agent/tasks/{task_id}/transition
POST /api/agent/tasks/{task_id}/review
POST /api/agent/tasks/{task_id}/run-followup
POST /api/agent/tasks/{task_id}/dispatch-alert
```

### 4.4 关键验收

- `waiting_human` task 能被人工反馈转出等待状态。
- 待处理数量能随状态变化减少。
- 每个状态变化能看到 `state_history.reason`。
- 不需要到数据库里手动找 task_id。

### 4.5 当前实现落点

Tasks tab 已经接入真实数据：

```text
frontend/src/pages/AgentWorkbenchPage.tsx
frontend/styles.css
```

当前行为：

- 进入 `Agent工作台` 后默认打开 `Tasks`。
- 自动读取：

```text
GET /api/agent/tasks?limit=200
```

- 按状态分组展示：
  - `open`
  - `observing`
  - `waiting_human`
  - `alert_drafted`
  - `review_due`
  - `closed`
  - `failed`
- 每张 task 卡片展示：
  - task_id。
  - pool_id。
  - task_type。
  - status。
  - severity。
  - title。
  - alert_status。
  - next_check_at。
  - review_after。
  - current_decision_id。
  - latest state reason。
  - updated_at。
- task_id 可以直接复制，不需要进数据库查。

当前支持操作：

```text
已补号
先观察
关闭
转复盘
发送告警草稿
```

对应接口：

```text
POST /api/agent/tasks/{task_id}/feedback
POST /api/agent/tasks/{task_id}/transition
POST /api/agent/tasks/{task_id}/dispatch-alert
```

说明：

- `已补号` 会写 operator feedback，并尝试推进到 `review_due`。
- `先观察` 会写 operator feedback，并尝试推进到 `observing`。
- `关闭` 走显式 transition 到 `closed`。
- `转复盘` 走显式 transition 到 `review_due`。
- `发送告警草稿` 只对 `alert_drafted + alert_status=drafted` 的 task 展示。
- 操作成功后会重新加载 task 列表，因此 waiting_human 数量会随状态变化下降。

## 5. Runs

### 5.1 目标

展示每一次 Agent 执行。

字段：

- run_id。
- trigger。
- pool_id。
- task_id。
- conversation_id。
- status。
- severity。
- decision_id。
- started_at。
- finished_at。
- duration_ms。
- error。

### 5.2 trigger 过滤

支持：

```text
manual_analyze
manual_chat
scheduler_patrol
scheduler_task_due
scheduler_review_due
event_spike
memory_daily_summary
memory_weekly_summary
notification_dispatch
```

接口：

```text
GET /api/agent/runs
```

后续可新增：

```text
GET /api/agent/runs/{run_id}
```

### 5.3 当前实现落点

Runs tab 已经接入真实数据：

```text
GET /api/agent/runs?limit=100
GET /api/agent/runs?trigger=scheduler_patrol&limit=100
```

当前展示字段：

- run_id。
- trigger。
- pool_id。
- task_id。
- conversation_id。
- status。
- severity。
- decision_id。
- started_at。
- finished_at。
- duration_ms。
- error。

当前支持 trigger 过滤：

```text
all
manual_analyze
manual_chat
scheduler_patrol
scheduler_task_due
scheduler_review_due
event_spike
memory_daily_summary
memory_weekly_summary
notification_dispatch
```

说明：

- `GET /api/agent/runs` 已支持 `trigger` 查询参数。
- 工作台只展示 run 列表和关键字段。
- run 的 step 详情仍留给后续 `Trace` tab 使用 `GET /api/agent/runs/{run_id}/steps` 展开。

## 6. Trace

### 6.1 目标

展示 step loop 如何一步步完成一次 run。

字段：

- step_index。
- step_type。
- status。
- intent。
- input_summary。
- output_summary。
- llm.model。
- llm.framework。
- capability_calls。
- started_at。
- finished_at。
- error。

接口：

```text
GET /api/agent/runs/{run_id}/steps
```

### 6.2 安全原则

只展示：

- `thought_summary`。
- output summary。
- capability calls。
- task update result。

不展示隐藏推理链。

### 6.3 当前实现落点

Trace tab 已经接入真实数据：

```text
GET /api/agent/runs/{run_id}/steps?limit=200
```

当前入口：

- 可以从最近 100 条 run 下拉选择。
- 可以手动粘贴 `run_id` 查询。
- 查询后按 `step_index` 展示 step loop。

当前展示字段：

- step_index。
- step_type。
- status。
- intent。
- input_summary。
- output_summary。
- llm.model。
- llm.framework。
- capability_calls。
- task_update_result。
- started_at。
- finished_at。
- duration_ms。
- error。

安全处理：

- 页面突出展示 `output_summary.thought_summary`。
- JSON 折叠块只展示 `input_summary`、`output_summary`、`capability_calls`、`task_update_result`。
- 前端会过滤常见隐藏推理字段名，例如 `chain_of_thought`、`hidden_reasoning`。
- 不展示模型隐藏推理链，只展示 step loop 已持久化的安全摘要。

## 7. Triggers

### 7.1 Scheduler ticks

展示每一轮 scheduler 结果：

- tick_id。
- status。
- reason。
- started_at。
- duration_ms。
- due_tasks。
- review_tasks。
- event_spikes。
- pool_patrols。
- memory_summaries。
- alert_drafts。
- errors。

接口：

```text
GET /api/agent/scheduler/ticks
```

### 7.2 Event triggers

展示事件突增：

- trigger_id。
- signal。
- site_id。
- pool_id。
- dedupe_key。
- evidence。
- status。
- run_id。
- created_at。

当前后端已有集合：

```text
agent_event_triggers
```

后续建议补路由：

```text
GET /api/agent/event-triggers
GET /api/agent/event-triggers/{trigger_id}
```

### 7.3 Patrol runs

展示巡检历史：

```text
GET /api/agent/patrol/runs
```

重点展示：

- processed。
- skipped。
- failed。
- skip_reason。
- required_patrol。
- excluded_agent_pool_ids 是否生效。

### 7.4 当前实现落点

Triggers tab 已经接入真实数据：

```text
GET /api/agent/scheduler/ticks?limit=30
GET /api/agent/event-triggers?limit=30
GET /api/agent/patrol/runs?limit=30
```

当前展示三块：

1. Scheduler ticks

展示：

- tick_id。
- status。
- reason。
- started_at。
- duration_ms。
- due_tasks。
- review_tasks。
- event_spikes。
- pool_patrols。
- memory_summaries。
- alert_drafts。
- errors 数量。

2. Event triggers

展示：

- trigger_id。
- signal。
- site_id。
- pool_id。
- dedupe_key。
- evidence。
- status。
- run_id。
- created_at。
- error。

3. Patrol runs

展示：

- patrol_id。
- status。
- pool_id。
- required_patrol。
- reason。
- skip_reason。
- run_id。
- decision_id。
- task_id。
- severity。
- started_at。

后端补充：

- 新增只读接口 `GET /api/agent/event-triggers`。
- `GET /api/agent/patrol/runs` 返回中补充 `required_patrol`，用于确认必巡池配置是否生效。

说明：

- Triggers tab 只负责解释自动唤醒来源。
- event_spike evidence 使用折叠 JSON 展示。
- event_spike 仍只是唤醒信号，不代表最终风险等级。

## 8. Evals

### 8.1 目标

把自动评测集变成可见回归工具。

接口：

```text
GET  /api/agent/evals/cases
POST /api/agent/evals/run
GET  /api/agent/evals/runs
GET  /api/agent/evals/runs/{eval_run_id}
GET  /api/agent/evals/results
```

展示：

- suite。
- mode。
- status。
- score。
- passed。
- failed。
- case_id。
- assertion results。
- failure_reasons。
- output_summary。

### 8.2 验收

- prompt 改动后能从前端跑一次 eval。
- 能看到失败 case。
- 能看到失败原因。
- 能区分 LLM 问题、Context Pack 问题、断言器问题。

### 8.3 当前实现落点

Evals tab 已经接入真实数据和手动运行：

```text
GET  /api/agent/evals/cases
POST /api/agent/evals/run
GET  /api/agent/evals/runs
GET  /api/agent/evals/results?eval_run_id=...
```

当前支持：

- 选择 category：
  - `all`
  - 后端返回的 eval categories。
- 选择 mode：
  - `llm_live`
  - `llm_mock`
- 点击 `运行评测` 后执行：

```json
{
  "suite": "default",
  "mode": "llm_live",
  "category": null,
  "persist": true
}
```

当前展示：

1. Eval runs

- eval_run_id。
- suite。
- mode。
- status。
- score。
- passed。
- failed。
- started_at。
- duration_ms。

2. Eval results

- case_id。
- category。
- status。
- score。
- assertion results。
- failure_reasons。
- output_summary。

3. Eval cases

- case_id。
- category。
- input_mode。
- min_score。
- description。

验收对应：

- prompt 改动后可以从工作台手动跑一次默认评测集。
- failed case 会以红色边框突出。
- failure_reasons 和 failed assertions 可以直接展开查看。
- output_summary 能帮助判断问题来自 intent router、context pack 理解、decision 输出、patrol selection 或 safety boundary。

## 9. Memory

### 9.1 目标

查看长期记忆是否真的沉淀经验。

memory_type：

```text
operator_feedback_summary
decision_review
pool_daily_summary
pool_weekly_summary
survival_pattern
future_playbook
```

当前集合：

```text
agent_memory_summaries
```

后续建议补路由：

```text
GET /api/agent/memory
GET /api/agent/memory/{memory_id}
```

### 9.2 展示字段

- memory_id。
- memory_type。
- pool_id。
- period_start。
- period_end。
- summary。
- facts。
- patterns。
- lessons。
- risk_baselines。
- created_at。

### 9.3 当前实现落点

Memory tab 已经接入真实数据：

```text
GET /api/agent/memory?limit=100
GET /api/agent/memory?memory_type=pool_daily_summary&limit=100
GET /api/agent/memory?pool_id=...&limit=100
GET /api/agent/memory/{memory_id}
```

当前支持过滤：

- `memory_type`。
- `pool_id`。

当前展示：

- memory_id。
- memory_type。
- site_id。
- pool_id。
- period_start。
- period_end。
- summary。
- facts。
- patterns。
- lessons。
- risk_baselines。
- source_run_ids。
- source_decision_ids。
- created_at。

当前可见 memory_type：

```text
operator_feedback_summary
decision_review
pool_daily_summary
pool_weekly_summary
survival_pattern
future_playbook
```

说明：

- Memory tab 只读取 `agent_memory_summaries`。
- 不修改账号池业务表。
- `future_playbook` 作为后续 Skills / Playbook 层预留类型展示。
- risk_baselines 和 sources 使用折叠 JSON 展示，避免主页面过载。

## 10. Notifications

### 10.1 目标

查看告警草稿和实际通知发送。

展示：

- alert_drafted task。
- alert_draft.title。
- alert_draft.content。
- severity。
- source_decision_id。
- alert_status。
- notification_event_id。
- delivery status。
- error。

接口：

```text
GET  /api/agent/tasks?status=alert_drafted
POST /api/agent/tasks/{task_id}/dispatch-alert
```

通知模块集合：

```text
notification_events
notification_deliveries
```

后续建议复用通知模块路由，或新增 Agent 侧聚合视图：

```text
GET /api/agent/notifications
```

### 10.2 当前实现落点

Notifications tab 已经接入真实数据：

```text
GET  /api/agent/notifications?limit=100
GET  /api/agent/notifications?status=drafted&limit=100
POST /api/agent/tasks/{task_id}/dispatch-alert
```

后端新增 Agent 侧聚合视图：

```text
GET /api/agent/notifications
```

聚合来源：

- `agent_tasks.alert_draft`
- `agent_tasks.alert_status`
- `agent_tasks.alert_notification_event_id`
- `notification_events`
- `notification_deliveries`

当前展示：

- task_id。
- pool_id。
- task_status。
- alert_draft.title。
- alert_draft.content。
- severity。
- source_decision_id。
- alert_status。
- notification_event_id。
- delivery_status。
- delivery records。
- error。
- sent_at。

当前操作：

- 对 `alert_status=drafted` 的 task 展示 `发送告警草稿`。
- 发送接口仍走：

```text
POST /api/agent/tasks/{task_id}/dispatch-alert
```

说明：

- 工作台只显示和派发 Agent 已生成的告警草稿。
- 不重新生成补号建议。
- 不绕过后端通知策略、钉钉配置和审计。
- notification event 和 deliveries 使用折叠 JSON 展示。

## 11. Pricing Decisions 预留

价格策略 Agent 不在阶段九实现，但工作台预留入口。

后续 pricing decision 应展示：

- pricing_decision_id。
- pool_id / site_id。
- current_price。
- suggested_price。
- reason。
- evidence。
- user_usage_attribution。
- risk_of_change。
- requires_human_confirm。
- status。

原则：

```text
只建议价格
不自动改价格
需要人工确认
写审计
```

### 11.1 当前预留落点

工作台已新增 reserved tab：

```text
Pricing
```

当前只展示字段和原则，不调用后端接口。

预留字段：

- `pricing_decision_id`
- `pool_id / site_id`
- `current_price`
- `suggested_price`
- `reason`
- `evidence`
- `user_usage_attribution`
- `risk_of_change`
- `requires_human_confirm`
- `status`

安全边界：

- 只生成价格建议。
- 不自动改价格。
- 不写业务价格配置。
- 必须人工确认。
- 后续需要写入 Agent 自己的 pricing decision / audit 记录。

## 12. User Usage Attribution 预留

用户用量归因不在阶段九实现，但工作台后续需要展示：

- top users。
- top user share。
- top 3 share。
- active user count。
- distribution。
- single_user_dominant / multi_user_growth。
- peak hour。
- change_vs_previous_window。

该数据进入 Context Pack 后，也应该能在工作台里被追溯。

### 12.1 当前预留落点

工作台已新增 reserved tab：

```text
Usage Attribution
```

当前只展示字段和用途，不调用后端接口。

预留字段：

- `top_users`
- `top_user_share`
- `top_3_share`
- `active_user_count`
- `distribution`
- `single_user_dominant`
- `multi_user_growth`
- `peak_hour`
- `change_vs_previous_window`

后续接入原则：

- 先作为只读归因输入。
- 进入 Context Pack 后必须能在 Trace / Workbench 中追溯。
- 用于辅助补号、告警、调价判断。
- 不直接限制用户。
- 不直接改价格。

## 13. 当前实现落点

当前已经完成阶段九入口壳：

```text
frontend/src/types.ts
frontend/src/App.tsx
frontend/src/pages/AgentWorkbenchPage.tsx
frontend/styles.css
docs/agent/阶段九-Agent运维工作台设计.md
```

已完成：

- 新增 `agent-workbench` view。
- 新增 `/agent-workbench` 路径。
- 主导航在 `Agent分析` 下方增加 `Agent工作台`。
- 新增 Agent 工作台页面。
- 页面包含 Tasks / Runs / Trace / Triggers / Evals / Memory / Notifications / Pricing / Usage Attribution tabs。
- Tasks / Runs / Trace / Triggers / Evals / Memory / Notifications 已接入真实数据。
- Pricing / Usage Attribution 已完成 reserved 字段预留。

未完成：

- pricing decision 后端模型、路由和审计。
- user_usage_windows 聚合、Context Pack 接入和工作台真实数据。
- Playbooks tab。

## 14. 开发顺序

建议阶段九按以下顺序落地：

```text
1. Tasks 看板
2. Task 操作按钮
3. Runs 列表
4. Step Trace
5. Triggers
6. Evals
7. Memory
8. Notifications
9. Pricing reserved
10. Usage Attribution reserved
```

原因：

- Tasks 是当前最直接的运营闭环。
- Task 操作能解决 waiting_human 无法下降的问题。
- Runs 和 Trace 能解释 Agent 为什么这么判断。
- Triggers 能解释为什么 loop 自动唤醒。
- Evals 能保护后续 prompt / context 改动。
- Memory 和 Notifications 再补完整审计。
- Pricing / Usage Attribution 先预留入口，避免后续调整工作台导航结构。

## 15. 验收标准

### 15.1 导航

- 左侧导航显示 `Agent工作台`。
- 位置在 `Agent分析` 下方。
- 点击进入 `/agent-workbench`。

### 15.2 页面

- 页面标题为 `Agent工作台`。
- 页面包含 9 个 tab：
  - Tasks。
  - Runs。
  - Trace。
  - Triggers。
  - Evals。
  - Memory。
  - Notifications。
  - Pricing。
  - Usage Attribution。
- 切换 tab 不刷新页面。
- 移动端不横向溢出。

### 15.3 后续接入

- Tasks tab 能把 waiting_human task 转出等待状态。
- Trace tab 能按 run_id 展示 step。
- Evals tab 能运行默认评测集。
- Notifications tab 能手动发送告警草稿并看到审计。
- Pricing tab 显示价格策略建议预留字段和“不自动改价”原则。
- Usage Attribution tab 显示用户用量归因预留字段和后续 Context Pack 追溯原则。
