# 阶段六：Agent 任务状态闭环设计

本文承接：

```text
docs/agent/账号池运营Agent总体架构.md
docs/agent/阶段一-Agent-LLM配置与调用层设计.md
docs/agent/阶段二-Agent持久化与前端缓存任务.md
docs/agent/阶段三-Context-Pack与LLM主决策设计.md
docs/agent/阶段四-数据理解与分层事件记忆设计.md
docs/agent/阶段五-Agent控制中枢与多步循环设计.md
```

前五阶段已经完成：

- LLM 配置与 LangChain 调用层。
- Agent run / message / decision / memory 持久化。
- Context Pack v2。
- LLM 主决策。
- 分层事件窗口和长期记忆。
- Intent Router。
- Step Loop。
- `agent_tasks` 集合和基础任务状态机。
- `agent_run_steps` 审计。
- 手动复盘入口。

阶段六的目标不是马上做自启动 loop，也不是马上自动发钉钉，而是先让 `agent_tasks` 真正承担“持续运营问题”的生命周期。

## 1. 阶段目标

阶段六要把任务状态闭环跑起来：

```text
open
-> observing
-> waiting_human
-> alert_drafted
-> review_due
-> closed
```

要完成：

- 每次 Agent 决策后，明确更新 task 状态。
- `observing` 任务必须有 `next_check_at`。
- `waiting_human` 任务等待人工反馈。
- `alert_drafted` 只生成告警草稿，不自动发送。
- `review_due` 可以触发复盘。
- 复盘后能转 `closed` 或继续 `observing`。

这一步完成后，Agent 才有“持续处理一个运营问题”的能力。

## 2. 不做范围

阶段六暂不做：

- 定时自启动巡检。
- 事件流自动触发 Agent。
- 钉钉正式自动发送。
- 自动推号。
- 自动买号。
- 自动删号。
- 自动刷新 sub2api。
- 自动启动账号探测。
- 前端完整任务看板。

阶段六可以做：

- 生成和更新 Agent task。
- 生成告警草稿。
- 保存人工确认请求。
- 保存复盘结果。
- 关闭任务。
- 为下一阶段自启动 loop 留下 `next_check_at`、`review_after`、`status` 等调度字段。

## 3. 核心原则

### 3.1 task 表示持续问题，不表示一次运行

`agent_runs` 表示一次 Agent 执行。

`agent_tasks` 表示一个持续运营问题，例如：

```text
pro 池近期容量不足
中午出现集中 401
备用池为 0，需要观察
上次高风险决策需要复盘
```

一个 task 可以关联多个 run、decision、step 和 message。

### 3.2 task 只属于 Agent

阶段六仍然只写 Agent 自己的集合：

- `agent_tasks`
- `agent_runs`
- `agent_messages`
- `agent_decisions`
- `agent_run_steps`
- `agent_memory_summaries`

不写：

- `accounts`
- `api_pools`
- `sub2api_*`
- 其他账号池业务集合。

### 3.3 状态转移必须有原因

每次状态变化必须写入：

```text
state_history
```

记录：

- from_status
- to_status
- reason
- run_id
- decision_id
- changed_at

这样后续才能审计 Agent 为什么进入观察、为什么等人工、为什么关闭。

### 3.4 当前实现落点

文件：

```text
backend/app/modules/agent/tasks.py
```

当前已落地的约束：

- `AGENT_TASK_SCHEMA_VERSION = "agent_task.v1"`。
- 新建或更新 task 时写入 `owner_scope = "agent"`。
- `resolve_agent_task(...)`、`list_agent_tasks(...)`、`get_agent_task(...)` 只读取 Agent 自己的 task，兼容旧数据中缺少 `owner_scope` 的记录。
- `create_or_update_agent_task(...)` 会维护：
  - `linked_run_ids`
  - `linked_decision_ids`
  - `linked_conversation_ids`
  - `linked_step_ids`
  - `run_history`
  - `decision_history`
  - `state_history`
- 每次状态变化都会写入 `state_history`，并包含：
  - `from_status`
  - `to_status`
  - `reason`
  - `run_id`
  - `decision_id`
  - `changed_at`
- `close_agent_task(...)` 要求必须提供关闭原因。
- `append_agent_task_step_link(...)` 会把一次 run 内创建的 step 回链到 task，支持一个 task 关联多个 step。

这些实现只写 `agent_tasks`，不写账号池业务集合。

## 4. 状态定义

### 4.1 open

任务刚创建，还没有明确进入观察、等待人工或告警草稿状态。

常见来源：

- Agent 第一次发现某个池存在轻微风险。
- 用户提出了一个需要跟进但暂时没有强风险的问题。

### 4.2 observing

Agent 判断需要继续观察。

要求：

- 必须有 `next_check_at`。
- 必须有观察原因。
- 最好有 `next_observation_focus`。

典型情况：

- 风险存在，但不需要立刻人工处理。
- 容量暂时可支撑，但趋势需要跟踪。
- 事件爆发已经停止，但需要观察是否复发。

### 4.3 waiting_human

Agent 判断需要人工确认或处理。

典型情况：

- LLM 判断需要补号，但需要负责人确认执行计划。
- 数据缺口影响关键判断，需要人工补充。
- 用户要求了越权动作，Agent 转成人工确认请求。
- 白天策略要求人工确认。

要求：

- 必须记录 `requires_human_confirm = true`。
- 必须记录需要人工确认的问题或事项。
- 不自动执行账号操作。

### 4.4 alert_drafted

Agent 判断应该告警，但阶段六只生成告警草稿，不自动发送。

典型情况：

- `should_alert = true`。
- 风险等级为 `danger` 或 `critical`。
- 出现集中封号、容量快速下降、备用池耗尽等高风险信号。

要求：

- 保存告警草稿。
- 保存告警原因。
- 不调用钉钉发送接口。
- 后续由人工确认或下一阶段接入通知策略。

### 4.5 review_due

任务进入复盘状态。

典型情况：

- 之前有高风险决策，需要看后续是否改善。
- 之前建议补号，需要看容量是否恢复。
- 之前判断集中封号，需要看事件流是否支持。
- `review_after` 到期。

要求：

- 可以调用 `review_agent_decision(...)`。
- 复盘后要决定转 `closed` 或 `observing`。

### 4.6 closed

任务关闭。

典型情况：

- 风险解除。
- 复盘完成，后续无需观察。
- 人工确认不再处理。
- 长期没有继续恶化。

要求：

- 必须记录关闭原因。
- 必须写入 `closed_at`。

### 4.7 当前实现落点

状态定义已经落到 `backend/app/modules/agent/tasks.py`：

- `open`：保留轻量任务，不强制观察时间；如果 LLM 明确给出 `next_check_at` 或 `review_after`，会保存下来。
- `observing`：由 `_status_fields(...)` 强制补齐 `next_check_at`、`observation_reason` 和 `next_observation_focus`；如果 LLM 没给观察时间，按风险等级默认生成下一次检查时间。
- `waiting_human`：强制设置 `requires_human_confirm=true`、`human_confirm_status=pending`、`human_confirm_questions`，并且不会执行任何账号操作。
- `alert_drafted`：保存 `alert_status=drafted`、`alert_reason`、`alert_draft`，其中 `send_behavior=draft_only`，只生成草稿，不调用钉钉发送。
- `review_due`：保存或生成 `review_after`，用于后续复盘入口扫描或手动复盘。
- `closed`：强制保存 `closed_at` 和 `close_reason`，同时清空 `next_check_at` / `review_after`。

状态字段统一由 `_status_fields(...)` 生成，避免入口代码散落状态规则。每次状态变化仍然写入 `state_history`，记录 `from_status`、`to_status`、`reason`、`run_id`、`decision_id` 和 `changed_at`。

## 5. 状态转移规则

阶段六建议先支持以下转移：

```text
open -> observing
open -> waiting_human
open -> alert_drafted
open -> closed

observing -> observing
observing -> waiting_human
observing -> alert_drafted
observing -> review_due
observing -> closed

waiting_human -> observing
waiting_human -> alert_drafted
waiting_human -> review_due
waiting_human -> closed

alert_drafted -> waiting_human
alert_drafted -> review_due
alert_drafted -> closed

review_due -> observing
review_due -> waiting_human
review_due -> closed

any -> failed
```

暂不建议支持任意无理由跳转。

如果 LLM 给出的 `task_update.next_status` 不在允许转移内，后端应降级为：

```text
observing
```

或者保持原状态，并把问题写入 `task_update_warnings`。

### 5.1 当前实现落点

状态转移规则已经落到 `backend/app/modules/agent/tasks.py`：

- `ALLOWED_TASK_TRANSITIONS` 保存阶段六允许的状态转移表。
- `_resolve_next_status(...)` 先根据 decision / step_result 生成候选状态，再校验当前状态是否允许跳转。
- `task_update.next_status` 或 `step_result.next_status` 可以显式请求状态，但必须在允许转移表内。
- 如果显式请求的状态不合法，后端优先降级为 `observing`；如果当前状态不能转到 `observing`，则保持原状态。
- 如果后端根据 decision 推导出的状态不允许从当前状态进入，则保持原状态。
- 同状态的幂等更新允许存在，用于重复观察、重复等待人工等场景，但仍然必须记录本次更新原因。
- 所有非法状态请求、降级和 validator warning 都会写入 `task_update_warnings`。
- `failed` 保留为特殊失败态，支持 `any -> failed`，用于后续异常闭环。
- `fail_agent_task(...)` 可将任意 task 标记为 `failed`，并写入 `failed_at`、`error` 和 `state_history`。

这一步不让 LLM 任意改写任务生命周期，只允许它提出状态意图，最终由后端状态机守住边界。

## 6. 决策后如何更新 task

每次 `pool_operation_decision` 结束后，都应该根据 LLM decision 和 validator 结果更新 task。

### 6.1 输入来源

用于更新 task 的输入：

- decision.severity
- decision.should_add_accounts
- decision.suggested_add_count
- decision.should_alert
- decision.requires_human_confirm
- decision.manual_review_required
- decision.next_observation_focus
- decision.follow_up_questions
- decision.data_gaps
- decision.recommended_actions
- decision.event_assessment
- validator 降级信息
- loop_result.final_step.task_update

### 6.2 状态选择建议

后端不替 LLM 重算补号数，但可以根据 decision 字段选择任务状态。

建议规则：

```text
requires_human_confirm=true
-> waiting_human

should_alert=true
-> alert_drafted

severity in danger/critical 且没有立即人工确认
-> observing 或 alert_drafted

severity in warning/watch
-> observing

severity=healthy 且没有数据缺口
-> closed 或不创建 task

data_gaps 影响关键判断
-> waiting_human 或 observing
```

注意：

- 如果 `requires_human_confirm=true` 和 `should_alert=true` 同时存在，优先进入 `waiting_human`，同时保留告警草稿字段。
- 如果是 `critical` 且 `should_alert=true`，可以进入 `alert_drafted`，但不自动发送。
- 是否真正发钉钉留到后续阶段。

### 6.3 next_check_at

进入 `observing` 时必须设置 `next_check_at`。

建议默认值：

```text
critical: 15 分钟后
danger: 30 分钟后
warning: 60 分钟后
watch: 120 分钟后
healthy: 不设置，或关闭任务
```

如果 LLM 在 `task_update.next_check_minutes` 中给出明确建议，后端可以在安全范围内采用：

```text
min = 5 分钟
max = 24 小时
```

### 6.4 review_after

如果本次 decision 具备后续验证价值，应设置 `review_after`。

建议：

```text
建议补号: 6-24 小时后复盘
集中封号: 3-12 小时后复盘
告警草稿: 1-6 小时后复盘
人工确认: 等人工反馈后再决定
```

后续自启动 loop 会扫描 `review_after <= now` 的任务，把它们推进 `review_due`。

阶段六先完成字段和手动推进，不做定时扫描。

### 6.5 当前实现落点

决策后更新 task 已经由 `create_or_update_agent_task(...)` 承接：

- 输入来源包含 `decision` 和 `step_result.task_update`。
- `controller.py` 在最终保存运营决策时，会通过 `_task_step_result_from_loop(...)` 合并 `loop_result.final_step.task_update` 和 LLM 主决策字段后再更新 task。
- 如果 step loop 直接进入 `ask_human`，`controller.py` 会通过 `_ask_human_task_step_result(...)` 创建或更新 `waiting_human` task，避免人工确认请求只停留在聊天回复里。
- 状态选择优先级为：
  - 显式 `task_update.next_status`。
  - `requires_human_confirm` / `manual_review_required`。
  - `should_alert`。
  - 关键数据缺口。
  - 补号或人工处理建议。
  - `severity`。
- `requires_human_confirm=true` 优先进入 `waiting_human`。
- `should_alert=true` 进入 `alert_drafted`；如果同时需要人工确认，则状态为 `waiting_human`，但仍保留 `alert_draft`。
- `severity=healthy` 且没有持续问题时，如果已有 task 会转 `closed`；如果没有已有 task，则不创建新的持续任务。
- `observing` 会按风险等级自动生成 `next_check_at`，也会采用 LLM 给出的 `next_check_minutes`，并限制在 5 分钟到 24 小时之间。
- 具备后续验证价值的任务会写入 `review_after`：
  - 告警草稿默认 3 小时后复盘。
  - 集中封号默认 6 小时后复盘。
  - 补号建议默认 12 小时后复盘。
  - LLM 显式给出 `review_after` 或 `review_after_hours` 时优先采用，并限制在安全范围内。

当前阶段只写 Agent 自己的任务状态，不启动定时扫描，不自动发送钉钉，不修改账号池业务表。

## 7. waiting_human 与人工反馈

### 7.1 人工反馈来源

人工反馈可以来自：

- Agent 聊天输入被识别为 `operator_feedback`。
- 用户明确说“这次不是异常流量，是批量任务”。
- 用户明确说“已经补了 20 个号”。
- 用户明确说“不需要补，先观察”。
- 用户明确说“可以发告警”或“不用发告警”。

### 7.2 反馈处理

当 task 处于 `waiting_human` 时，如果收到相关 `operator_feedback`：

- 写入 `operator_feedback_summary` 长期记忆。
- 追加到 task 的 `human_feedback_history`。
- 根据反馈内容转移状态：
  - 已补号或已处理：`review_due` 或 `observing`。
  - 确认先观察：`observing`。
  - 确认不处理：`closed`。
  - 确认需要告警：`alert_drafted`。

### 7.3 不做自然语言强解析的替代方案

阶段六可以先提供显式 task 操作接口：

```text
POST /api/agent/tasks/{task_id}/feedback
POST /api/agent/tasks/{task_id}/transition
```

自然语言反馈仍然走 Agent chat，但显式接口更稳定，便于前端后续做按钮。

### 7.4 当前实现落点

阶段六第七部分已经落到以下位置：

- `backend/app/modules/agent/tasks.py`
  - `append_agent_task_feedback(...)`：写入 `human_feedback_history`，必要时写入 `agent_memory_summaries.memory_type=operator_feedback_summary`，并根据反馈类型更新 task 状态。
  - `transition_agent_task(...)`：提供显式状态转移能力，校验是否符合 `ALLOWED_TASK_TRANSITIONS`，并写入 `state_history`。
- `backend/app/modules/agent/controller.py`
  - 当 Agent chat 被识别为 `operator_feedback` 时，会复用 step loop 已写入的长期记忆，并把反馈追加到当前 task。
  - 自然语言反馈只做轻量识别，不依赖强解析；如果没有明确状态意图，会只写历史和记忆，不强行关闭或升级任务。
- `backend/app/routers/agent.py`
  - `POST /api/agent/tasks/{task_id}/feedback`
  - `POST /api/agent/tasks/{task_id}/transition`

显式 feedback 接口支持：

```json
{
  "feedback": "已经补了 20 个号，先观察恢复情况",
  "feedback_type": "handled",
  "target_status": "review_due",
  "reason": "人工确认已处理，需要后续复盘"
}
```

显式 transition 接口支持：

```json
{
  "target_status": "observing",
  "reason": "负责人确认先观察 1 小时",
  "next_check_minutes": 60
}
```

当前阶段仍然不自动发送钉钉、不推号、不修改账号池业务表。

## 8. alert_drafted 设计

### 8.1 告警草稿字段

建议在 task 中保存：

```json
{
  "alert_status": "drafted",
  "alert_draft": {
    "channel": "dingtalk",
    "title": "...",
    "content": "...",
    "severity": "critical",
    "source_decision_id": "...",
    "created_at": "..."
  }
}
```

### 8.2 告警草稿原则

- 只生成草稿。
- 不自动发送。
- 不调用通知发送接口。
- 草稿要说明风险等级、池子、核心证据、建议动作、是否需要人工确认。

后续接通知阶段时，再决定：

- 白天是否必须人工确认。
- 夜间 critical 是否允许自动发送。
- 是否使用系统管理/通知里已有的钉钉配置。

### 8.3 当前实现落点

阶段六第八部分已经落到 `backend/app/modules/agent/tasks.py`：

- `_alert_draft(...)` 负责从 LLM decision、step_result 和 task 池上下文生成告警草稿。
- `_feedback_alert_draft(...)` 负责人工反馈“可以发告警”时生成同样只读的告警草稿。
- 草稿会写入 task 的：
  - `alert_status = "drafted"`
  - `alert_reason`
  - `alert_draft`

当前 `alert_draft` 除基础字段外，还会补充：

```json
{
  "channel": "dingtalk",
  "status": "drafted",
  "send_behavior": "draft_only",
  "draft_only": true,
  "auto_send": false,
  "severity": "critical",
  "title": "...",
  "content": "...",
  "pool": {
    "pool_id": "...",
    "site_id": "...",
    "name": "...",
    "account_type": "pro"
  },
  "evidence": [],
  "recommended_actions": [],
  "requires_human_confirm": true,
  "notification_policy": {
    "auto_send": false,
    "manual_confirmation_required": true,
    "use_system_notification_config": "future_stage",
    "daytime_policy": "future_stage",
    "night_critical_policy": "future_stage"
  },
  "source_decision_id": "...",
  "created_at": "..."
}
```

`content` 会用自然语言说明：

- 风险等级。
- 账号池。
- LLM 给出的摘要。
- 核心证据。
- 建议动作。
- 是否需要人工确认。
- 该消息只是草稿，未自动发送。

当前阶段明确不做：

- 不调用钉钉发送接口。
- 不读取或使用系统管理/通知配置发送消息。
- 不根据夜间或白天策略自动改变发送行为。
- 不修改账号池业务表。

## 9. review_due 与复盘闭环

### 9.1 进入 review_due

任务可以在以下情况下进入 `review_due`：

- `review_after <= now`。
- 用户手动要求复盘。
- Agent 新一轮分析发现存在未复盘高风险 decision。
- 人工反馈说明某个历史判断需要验证。

### 9.2 复盘动作

进入 `review_due` 后，调用：

```python
review_agent_decision(...)
```

复盘会读取：

- 原始 decision。
- 原始容量快照。
- 当前容量快照。
- 后续 Agent decisions。
- 当前事件窗口。
- 人工反馈长期记忆。

复盘输出：

```text
useful
too_conservative
too_aggressive
wrong_interpretation
insufficient_data
```

### 9.3 复盘后的状态

建议：

```text
review_result=useful 且当前风险已缓解
-> closed

review_result=useful 但仍有风险
-> observing

review_result=too_conservative
-> observing 或 waiting_human

review_result=too_aggressive
-> closed 或 observing

review_result=wrong_interpretation
-> waiting_human 或 observing

review_result=insufficient_data
-> observing
```

阶段六可以先由后端保守决定：

- 缺证据：`observing`
- 风险解除：`closed`
- 风险仍在：`observing`
- 需要人工纠正：`waiting_human`

### 9.4 当前实现落点

阶段六第九部分已经落到以下位置：

- `backend/app/modules/agent/tasks.py`
  - `mark_agent_task_review_due(...)`：手动把单个 task 推进 `review_due`。
  - `mark_due_agent_tasks_review_due(...)`：扫描 `review_after <= now` 的 Agent task，并把符合条件的任务推进 `review_due`；这是给后续自启动 loop 使用的服务函数，当前不会自动调度。
  - `review_agent_task(...)`：以 task 为入口，找到 `current_decision_id`，调用 `review_agent_decision(...)`，写入 `decision_review` 长期记忆，然后根据复盘结果更新 task 状态。
  - `_task_status_after_review(...)`：实现阶段六保守状态策略。
- `backend/app/routers/agent.py`
  - `POST /api/agent/tasks/review-due/mark`
  - `POST /api/agent/tasks/{task_id}/review`

复盘后的后端保守映射：

```text
review_result=insufficient_data -> observing
review_result=wrong_interpretation -> waiting_human
review_result=too_conservative -> observing
review_result=too_aggressive -> closed 或 observing
review_result=useful -> closed 或 observing
```

其中 `useful` 和 `too_aggressive` 是否关闭任务，只看复盘包里的保守信号：

- 容量有改善信号。
- 没有容量恶化信号。
- 24h 高价值事件数为 0。

如果证据不足或风险仍在，则继续 `observing`，并保留 `review_after` 供后续再次复盘。

## 10. 后端模块设计

### 10.1 扩展 tasks.py

文件：

```text
backend/app/modules/agent/tasks.py
```

新增或强化函数：

```python
def resolve_next_task_status(...): ...

def build_task_schedule(...): ...

def build_alert_draft_from_decision(...): ...

async def transition_agent_task(...): ...

async def append_agent_task_feedback(...): ...

async def mark_agent_task_review_due(...): ...
```

职责：

- 根据 decision 和 step_result 计算下一状态。
- 写入 `next_check_at`。
- 写入 `review_after`。
- 写入 `alert_draft`。
- 写入 `state_history`。
- 写入 `human_feedback_history`。
- 提供显式状态转移能力。

### 10.2 修改 controller.py

文件：

```text
backend/app/modules/agent/controller.py
```

修改目标：

- 每次 `pool_operation_decision` 后调用强化版 `create_or_update_agent_task(...)`。
- 把 `decision_id` 写入 task。
- 把 `next_observation_focus` 写入 task。
- 把 `requires_human_confirm` 写入 task。
- 把 `alert_draft` 写入 task。
- 把 `review_after` 写入 task。
- 在 report.agent.task 中返回 task 摘要。

### 10.3 修改 reviewer.py

文件：

```text
backend/app/modules/agent/reviewer.py
```

修改目标：

- 复盘完成后返回建议 task 状态。
- 如果输入了 `task_id`，允许复盘后更新 task。
- 复盘 memory 写入后，把 `memory_id` 关联回 task。

### 10.4 修改 step_loop.py

文件：

```text
backend/app/modules/agent/step_loop.py
```

修改目标：

- `update_task_state` step 可以真正修改 task。
- 对非法状态转移进行降级。
- 将 task 更新写入 `agent_run_steps.output_summary.task_update_result`。

### 10.5 当前实现落点

第十部分的后端模块设计已经落到代码：

#### tasks.py

已提供公开函数：

```python
def resolve_next_task_status(...): ...
def build_task_schedule(...): ...
def build_alert_draft_from_decision(...): ...
async def transition_agent_task(...): ...
async def append_agent_task_feedback(...): ...
async def mark_agent_task_review_due(...): ...
async def mark_due_agent_tasks_review_due(...): ...
async def review_agent_task(...): ...
```

职责覆盖：

- 根据 `decision` 和 `step_result` 计算下一状态。
- 校验 `ALLOWED_TASK_TRANSITIONS`，非法转移会降级或拒绝。
- 写入 `next_check_at`。
- 写入 `review_after`。
- 写入 `alert_draft`。
- 写入 `state_history`。
- 写入 `human_feedback_history`。
- 支持显式状态转移、人工反馈、复盘进入与复盘后闭环。

#### controller.py

`pool_operation_decision` 完成后会调用强化版 `create_or_update_agent_task(...)`。

写入 task 的内容包括：

- `decision_id`
- `next_observation_focus`
- `requires_human_confirm`
- `alert_draft`
- `review_after`
- `current_run_id`
- `conversation_id`

`report.agent.task` 会返回 task 摘要，包括：

- `task_id`
- `status`
- `severity`
- `requires_human_confirm`
- `alert_status`
- `next_check_at`
- `review_after`
- `current_decision_id`

#### reviewer.py

`review_agent_decision(...)` 已支持可选 `task_id`。

当传入 `task_id` 时：

- 复盘完成后写入 `decision_review` 长期记忆。
- 将 `memory_id` 关联回 task 的 `linked_memory_ids`。
- 写入 `last_review_memory_id` 和 `last_review_result`。

task 级复盘闭环由 `tasks.py` 的 `review_agent_task(...)` 调用 reviewer 完成。

#### step_loop.py

`update_task_state` step 已接入真实 task 更新：

- 调用 `create_or_update_agent_task(...)`。
- 非法状态转移由 task 状态机降级或拒绝。
- 更新结果写入 `controller_output["task_update_result"]`。
- step 持久化后进入 `agent_run_steps.output_summary.task_update_result`。

当前阶段仍然不自动启动 scheduler，不发送钉钉，不刷新 sub2api，不修改账号池业务表。

## 11. 路由设计

阶段五已经有：

```text
GET  /api/agent/tasks
GET  /api/agent/tasks/{task_id}
GET  /api/agent/runs/{run_id}/steps
POST /api/agent/decisions/{decision_id}/review
```

阶段六建议新增：

```text
POST /api/agent/tasks/{task_id}/transition
POST /api/agent/tasks/{task_id}/feedback
POST /api/agent/tasks/{task_id}/review
```

### 11.1 transition

用于人工显式修改 task 状态。

请求示例：

```json
{
  "next_status": "observing",
  "reason": "负责人确认先观察 1 小时",
  "next_check_minutes": 60
}
```

### 11.2 feedback

用于人工反馈。

请求示例：

```json
{
  "message": "这次不是异常流量，是中午批量任务导致的。",
  "feedback_type": "operator_correction",
  "next_status": "observing"
}
```

### 11.3 review

用于对某个 task 当前关联 decision 做复盘。

如果 task 没有关联 decision，应返回明确错误。

### 11.4 当前实现落点

阶段六路由已经落到 `backend/app/routers/agent.py`。

已保留阶段五路由：

```text
GET  /api/agent/tasks
GET  /api/agent/tasks/{task_id}
GET  /api/agent/runs/{run_id}/steps
POST /api/agent/decisions/{decision_id}/review
```

阶段六新增路由：

```text
POST /api/agent/tasks/{task_id}/transition
POST /api/agent/tasks/{task_id}/feedback
POST /api/agent/tasks/{task_id}/review
POST /api/agent/tasks/review-due/mark
```

#### transition 实现

接口：

```text
POST /api/agent/tasks/{task_id}/transition
```

请求字段兼容两种状态字段名：

```json
{
  "next_status": "observing",
  "reason": "负责人确认先观察 1 小时",
  "next_check_minutes": 60
}
```

也兼容：

```json
{
  "target_status": "observing",
  "reason": "负责人确认先观察 1 小时",
  "next_check_minutes": 60
}
```

如果缺少 `next_status` / `target_status`，返回 `400`。

如果状态转移不符合 `ALLOWED_TASK_TRANSITIONS`，返回 `400`，不会强行修改 task。

#### feedback 实现

接口：

```text
POST /api/agent/tasks/{task_id}/feedback
```

请求字段兼容两种消息字段名：

```json
{
  "message": "这次不是异常流量，是中午批量任务导致的。",
  "feedback_type": "operator_correction",
  "next_status": "observing"
}
```

也兼容：

```json
{
  "feedback": "这次不是异常流量，是中午批量任务导致的。",
  "feedback_type": "operator_correction",
  "target_status": "observing"
}
```

如果缺少 `message` / `feedback`，返回 `400`。

该接口会：

- 写入 `human_feedback_history`。
- 写入 `operator_feedback_summary` 长期记忆。
- 根据 `next_status` / `target_status` 或反馈类型更新 task。
- 写审计日志 `agent.task.feedback`。

#### review 实现

接口：

```text
POST /api/agent/tasks/{task_id}/review
```

请求示例：

```json
{
  "review_window_hours": 24
}
```

该接口会：

- 从 task 找 `current_decision_id`。
- 调用 `review_agent_decision(...)`。
- 写入 `decision_review` 长期记忆。
- 把 `memory_id` 关联回 task。
- 根据复盘结果更新 task 状态。
- 写审计日志 `agent.task.review`。

如果 task 不存在，返回 `404`。

如果 task 没有关联 decision，返回 `400: task has no decision to review`。

#### review-due mark 实现

接口：

```text
POST /api/agent/tasks/review-due/mark
```

请求示例：

```json
{
  "limit": 50
}
```

该接口只手动扫描并标记 `review_after <= now` 的 Agent task 为 `review_due`，不启动定时任务。

### 11.5 路由安全边界

所有阶段六 task 路由都沿用 Agent 管理角色：

```text
owner / admin / maintainer
```

所有写操作都会写审计日志。

当前阶段仍然只写 Agent 自己的集合：

- `agent_tasks`
- `agent_run_steps`
- `agent_memory_summaries`
- `agent_messages`
- `agent_runs`
- `agent_decisions`

不写账号池业务表，不触发 sub2api 刷新，不发送钉钉。

## 12. 前端展示原则

阶段六前端可以先不做完整任务看板，但至少要让主页面看见 task 摘要：

- 当前 task 状态。
- 是否等待人工。
- 是否有告警草稿。
- 下一次观察时间。
- 复盘时间。
- 最近状态变化原因。

完整页面后续再做：

- Agent task 列表页。
- task 详情页。
- run steps 折叠详情。
- decision review 详情。

### 12.1 当前实现落点

当前阶段不做完整任务看板，只在 Agent 分析主页面展示 `report.agent.task` 的摘要信息。

后端返回的 task 摘要由 `controller.py` 中的 `_task_summary(...)` 生成，前端在 `frontend/src/pages/AgentAnalysisPage.tsx` 中通过 `AgentTaskSummaryPanel` 展示。

主页面展示字段：

- 当前 task 状态：`status`。
- 是否等待人工：`requires_human_confirm`。
- 是否有告警草稿：`alert_status=drafted`。
- 下一次观察时间：`next_check_at`。
- 复盘时间：`review_after`。
- 最近状态变化原因：优先读取 `latest_state_reason`，没有时读取 `state_history` 最后一条 reason。

展示原则：

- 主页面只展示任务摘要，不展示完整 `state_history`、`human_feedback_history`、run steps 或长期记忆。
- 没有 task 时显示“暂无任务”，表示本次分析没有形成持续运营问题。
- `alert_drafted` 只表示存在告警草稿，不代表已经发送钉钉。
- `waiting_human` 只表示 Agent 需要人工确认，不代表已执行任何账号操作。

后续独立页面再展开：

- Agent task 列表页。
- task 详情页。
- run steps 折叠详情。
- decision review 详情。

## 13. 验收标准

### 13.1 决策后 task 状态

- `requires_human_confirm=true` 后进入 `waiting_human`。
- `should_alert=true` 后进入 `alert_drafted`，但不发送钉钉。
- `severity=warning/watch` 后进入 `observing`。
- `severity=healthy` 且无风险后关闭或不创建 task。

### 13.2 observing

- observing task 必须有 `next_check_at`。
- observing task 必须有观察原因。
- observing task 可以被下一次 Agent run 继续更新。

### 13.3 waiting_human

- waiting_human task 必须有 `requires_human_confirm=true`。
- 人工反馈能写入 `human_feedback_history`。
- 人工反馈能转 `observing`、`review_due` 或 `closed`。

### 13.4 alert_drafted

- alert_drafted task 必须有 `alert_draft`。
- 不自动发送正式钉钉通知。
- 审计中能看到告警草稿来源 decision。

### 13.5 review_due

- review_due task 可以触发 `review_agent_decision(...)`。
- 复盘结果写入 `agent_memory_summaries`。
- 复盘后 task 能转 `closed` 或 `observing`。

### 13.6 安全

- 不写账号池业务表。
- 不触发 sub2api 刷新。
- 不启动账号探测。
- 不自动推号、买号、删号。
- 不自动发送钉钉正式通知。

### 13.7 构建

```text
python -m compileall backend\app\modules\agent backend\app\routers
```

## 14. 阶段六完成后的形态

阶段六完成后，Agent 不再只是：

```text
做一次分析 -> 保存一次结果
```

而是：

```text
发现问题
-> 创建或更新 task
-> 根据决策进入 observing / waiting_human / alert_drafted
-> 等待下一次观察、人工反馈或复盘
-> 复盘后关闭或继续观察
```

这时 Agent 才真正具备“持续处理一个账号池运营问题”的能力。

完成阶段六后，再进入下一阶段会更稳：

```text
阶段七：自启动 loop 与任务调度
阶段八：事件触发 Agent
阶段九：告警草稿到钉钉确认/发送流程
阶段十：评测集与准确性回归
```
