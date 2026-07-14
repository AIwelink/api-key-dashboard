# 阶段五：Agent 控制中枢、意图路由与多步循环设计

本文承接：

```text
docs/agent/账号池运营Agent总体架构.md
docs/agent/阶段一-Agent-LLM配置与调用层设计.md
docs/agent/阶段二-Agent持久化与前端缓存任务.md
docs/agent/阶段三-Context-Pack与LLM主决策设计.md
docs/agent/阶段四-数据理解与分层事件记忆设计.md
```

前四个阶段已经完成 Agent 的基础能力：

- LLM 配置和 LangChain 调用层。
- Agent run / message / decision 持久化。
- Context Pack v2。
- LLM 主决策。
- 分层事件窗口。
- 长期记忆摘要。
- 前端只展示最近主决策。

但当前 Agent 仍然偏线性链路。阶段五的目标是增加 Agent Controller，让 Agent 从“一次性决策链路”升级为“能判断任务类型、能多步观察、能维护任务状态、能复盘历史判断”的控制系统。

## 1. 阶段目标

阶段五主要解决四个问题：

1. 缺少意图路由。
2. 缺少多步循环。
3. 缺少自我复盘。
4. 缺少任务状态机。

完成后，Agent 的核心链路应升级为：

```text
触发入口
-> Agent Controller
-> Intent Router
-> Task State Resolver
-> Step Loop
-> Context Pack v2 / 只读能力 / 长期记忆 / 主决策 / 人工确认
-> 保存 run、step、decision、task、message、memory
-> 返回前端主决策或直接回复
```

阶段五不是让 Agent 自动推号、买号、删号，也不是立即接入定时 loop 和钉钉正式通知。阶段五先完成“控制中枢”，为后续自启动 loop、告警触发、人机确认打底。

## 2. 不做范围

阶段五暂不做：

- 自动补号。
- 自动推号。
- 自动买号。
- 自动删除或禁用账号。
- 自动修改账号池业务配置。
- 自动触发 sub2api 刷新。
- 自动触发账号探测。
- 自动发送钉钉正式通知。
- LangGraph 重构。
- 多站点批量巡检调度。

阶段五可以生成：

- 运营决策。
- 数据查询回答。
- 复盘结论。
- 告警草稿。
- 人工确认请求。
- 下一轮观察计划。
- Agent 自己的任务状态变更。

所有写入仍然只写 Agent 自己的集合，不写账号池业务表。

## 3. 总体架构

新增 Agent Controller 层：

```text
Manual Analyze / Manual Chat / Future Scheduler / Future Alert Event
        |
        v
backend/app/modules/agent/controller.py
        |
        v
Intent Router
        |
        +--> smalltalk_or_out_of_scope
        +--> agent_usage_question
        +--> operator_feedback
        +--> pool_data_question
        +--> pool_operation_decision
        +--> decision_review
        +--> unauthorized_action_request
        |
        v
Task State Resolver
        |
        v
Step Loop
        |
        +--> build Context Pack v2
        +--> call LLM controller step
        +--> optional read allowed capability
        +--> optional write memory
        +--> optional produce decision
        +--> optional ask human
        |
        v
Persist run / steps / decision / task / message / memory
```

代码落点：

- `backend/app/modules/agent/controller.py`：阶段五主入口。
- `backend/app/modules/agent/intent_router.py`：意图识别。
- `backend/app/modules/agent/step_loop.py`：一次 run 内的多步循环。
- `backend/app/modules/agent/tasks.py`：Agent 任务状态机。
- `backend/app/modules/agent/reviewer.py`：历史决策复盘入口。
- `backend/app/modules/agent/memory.py`：新增 Agent step 持久化。
- `backend/app/modules/system/bootstrap.py`：新增 Agent task / step 索引。

`orchestrator.py` 后续可以逐步瘦身，保留兼容入口。

## 4. 意图路由设计

### 4.1 Intent 类型

先支持以下 intent：

```text
pool_operation_decision
pool_data_question
decision_review
operator_feedback
agent_usage_question
smalltalk_or_out_of_scope
unauthorized_action_request
unknown
```

### 4.2 pool_operation_decision

用户要 Agent 判断账号池运营动作。

示例：

```text
今天这个池子要不要补号
现在风险高不高
这个池子还能撑多久
要不要告警
今天应该准备多少 pro 账号
```

处理方式：

- 构建 Context Pack v2。
- 进入多步循环。
- 产出 `agent_decision`。
- 必要时创建或更新任务状态。

### 4.3 pool_data_question

用户只是问数据，不一定要决策。

示例：

```text
这个池子最近 24h 有多少 401
最近 1h 有没有集中封号
当前 5h 容量是多少
上次 Agent 为什么建议补号
```

处理方式：

- 构建必要 Context Pack 或读取已有 state。
- 输出自然语言数据解释。
- 不强制生成补号 decision。
- 保存 assistant message。

### 4.4 decision_review

用户要求复盘历史判断。

示例：

```text
复盘一下昨天中午那次判断
上次建议补 26 个号后来准不准
最近几次 Agent 判断有没有偏保守
```

处理方式：

- 读取历史 run、decision、event windows、long-term memory。
- 调用 reviewer。
- 写入 `decision_review` 类型长期记忆。
- 可更新相关 task 状态。

### 4.5 operator_feedback

用户在纠正或补充运营事实。

示例：

```text
这次不是异常流量，是我们中午批量任务导致的
负责人说晚上超并发时要更积极告警
这批账号质量不好，以后不要太信任它们
```

处理方式：

- 写入 `operator_feedback_summary`。
- 保存 user message。
- 返回确认。
- 可根据内容决定是否重新分析，但不默认强制跑补号决策。

### 4.6 agent_usage_question

用户询问 Agent 自身能力、数据来源、流程。

示例：

```text
你现在会做什么
你怎么判断补多少号
你读取了哪些数据
你会不会自动推号
```

处理方式：

- 直接回答 Agent 工作方式。
- 不构建完整账号池决策。
- 不生成运营 decision。

### 4.7 smalltalk_or_out_of_scope

闲聊或无关问题。

处理方式：

- 简短自然语言回复。
- 不构建 Context Pack。
- 不生成 decision。
- 不更新任务状态。
- 仍可保存 message，便于审计。

### 4.8 unauthorized_action_request

用户要求越权动作。

示例：

```text
直接帮我推号
自动买 50 个账号
删掉这些异常账号
直接发钉钉
刷新 sub2api 缓存
```

处理方式：

- 明确拒绝直接执行。
- 说明当前阶段只能建议或生成草稿。
- 记录审计。
- 如请求合理，可转成 `human_review_request` 或 `notify_draft`。

### 4.9 Intent Router 输出结构

```json
{
  "schema_version": "agent_intent.v1",
  "intent": "pool_operation_decision",
  "confidence": "high",
  "target_pool_id": "sub2api:us06-5001:5",
  "requires_pool_context": true,
  "should_create_decision": true,
  "should_update_task": true,
  "is_operator_feedback": false,
  "is_unauthorized_action": false,
  "reason": "用户询问今天是否需要补号，属于账号池运营决策。",
  "reply_directly": false,
  "direct_reply": null,
  "safety_notes": []
}
```

实现落点：

- `backend/app/modules/agent/intent_router.py`
- `manual_analyze` 直接路由为 `pool_operation_decision`。
- `manual_chat` 使用 LangChain + Level 1 做完整意图路由。
- 明显越权请求会被硬规则兜底，不允许 LLM 覆盖成普通决策。

## 5. 多步循环设计

### 5.1 Loop 目标

阶段五的 loop 不是无限自动运行，而是在一次 run 内允许多步推理。

目标：

- 让 Agent 判断当前是否已经有足够上下文。
- 让 Agent 可以选择读取补充能力。
- 让 Agent 可以先复盘再决策。
- 让 Agent 可以发现需要人工确认后停止。
- 让 Agent 可以在一次 run 中形成“观察 -> 判断 -> 再观察 -> 最终输出”。

### 5.2 Loop 基本流程

```text
step 1: route intent
step 2: resolve or create task
step 3: build context pack if needed
step 4: call LLM controller step
step 5: validate step output
step 6: execute allowed step action
step 7: append observation
step 8: continue or stop
step 9: produce final response / decision / task update
```

当前实现中，`controller.py` 负责创建 run 和解析 task，`step_loop.py` 负责从 step 1 的路由观察开始记录，并在需要时构建 Context Pack v2。

### 5.3 Step 类型

支持：

```text
observe_context
answer_directly
build_decision
read_more
write_memory
review_previous_decision
update_task_state
ask_human
stop
```

阶段五第一版重点实现：

- `observe_context`：记录意图路由、Context Pack 构建结果。
- `answer_directly`：用于闲聊、能力说明、数据问答、越权拒绝。
- `build_decision`：进入阶段三/四已有的 Context Pack v2 + LLM 主决策。
- `review_previous_decision`：进入 reviewer 复盘入口。
- `write_memory`：允许写 Agent 长期记忆。
- `update_task_state`：根据 LLM step 输出写入或更新 Agent task 状态。
- `ask_human`：缺少目标池或需要人工确认时停止。
- `stop`：安全停止。

`read_more` 暂时只允许读取 Agent 已有只读能力，不触发主系统刷新。

当前只读能力白名单：

```text
api_pool_status.get
account_probe.get
```

注意：阶段五不再让 `refill_decision.calculate` 进入 Controller Step 白名单，避免模型回到规则引擎补号逻辑。

### 5.4 Step 输出结构

LLM 每一步必须输出：

```json
{
  "schema_version": "agent_step.v1",
  "step_type": "build_decision",
  "thought_summary": "当前上下文已经足够，需要生成主决策。",
  "needs_context_pack": true,
  "requested_capability": null,
  "memory_to_write": null,
  "task_update": null,
  "final_decision_ready": true,
  "requires_human_confirm": false,
  "continue_loop": false,
  "stop_reason": "decision_ready"
}
```

`thought_summary` 只保存简短摘要，不保存完整隐藏推理链。

实现落点：

- `backend/app/modules/agent/step_loop.py`
- `STEP_SCHEMA_VERSION = "agent_step.v1"`
- `_controller_step_prompt()` 约束 LLM 只能输出 JSON object。
- `_validate_step_output(...)` 会把非法 step_type 降级为当前 intent 对应的安全 step。
- `_fallback_step_output_for_intent(...)` 会在 LLM 不可用时退回安全流程。

### 5.5 Loop 限制

硬限制：

```text
max_steps = 4
max_llm_calls = 4
max_runtime_seconds = 60
max_capability_calls = 3
```

停止条件：

- 已生成最终决策。
- 已直接回答用户问题。
- 需要人工确认。
- 识别为闲聊或无关问题。
- 识别为越权请求。
- 达到最大步数。
- LLM 输出无效。
- 数据缺口无法继续补齐。

当前代码常量：

```python
MAX_STAGE5_STEPS = 4
MAX_STAGE5_LLM_CALLS = 4
MAX_STAGE5_RUNTIME_SECONDS = 60
MAX_STAGE5_CAPABILITY_CALLS = 3
```

### 5.6 Loop 安全边界

Loop 内禁止：

- 写账号池业务表。
- 触发 sub2api 刷新。
- 启动账号探测。
- 推号。
- 买号。
- 删除账号。
- 发送正式钉钉通知。

Loop 内允许：

- 写 `agent_runs`。
- 写 `agent_messages`。
- 写 `agent_decisions`。
- 写 `agent_memory_summaries`。
- 写 Agent task / step 集合。
- 读取现有缓存、事件、探测摘要、长期记忆。
- 生成告警草稿或人工确认请求。

当前实现中，`step_loop.py` 的 `READ_ONLY_CAPABILITIES` 只允许：

```text
api_pool_status.get
account_probe.get
```

并且 `controller_step_prompt` 明确禁止：

```text
refresh_sub2api
start_account_probe
push_accounts
buy_accounts
delete_accounts
send_dingtalk
write_business_tables
```

## 6. 任务状态机设计

### 6.1 为什么需要任务状态

`agent_runs` 表示一次运行，不表示一个持续运营问题。

任务状态用于表示：

- 某个池当前是否有持续风险。
- Agent 是否正在观察某个风险。
- 是否已经要求人工确认。
- 是否已经生成告警草稿。
- 是否需要复盘。
- 是否已经关闭。

### 6.2 agent_tasks 集合

新增 Agent 独立集合：

```text
agent_tasks
```

示例结构：

```json
{
  "_id": "...",
  "task_id": "...",
  "site_id": "us06-5001",
  "pool_id": "sub2api:us06-5001:5",
  "task_type": "pool_risk_monitoring",
  "status": "observing",
  "severity": "danger",
  "title": "pro 池近期容量不足并出现集中 401",
  "summary": "最近 24h 容量只能支撑约 1 天，且中午出现集中 401。",
  "current_decision_id": "...",
  "current_run_id": "...",
  "conversation_id": "...",
  "opened_at": "...",
  "updated_at": "...",
  "closed_at": null,
  "next_check_at": "...",
  "review_after": "...",
  "requires_human_confirm": true,
  "human_confirm_status": "pending",
  "alert_status": "drafted",
  "decision_history": [],
  "state_history": []
}
```

### 6.3 task_type

建议先支持：

```text
pool_risk_monitoring
capacity_shortage
ban_burst_review
operator_feedback_followup
decision_review
```

### 6.4 status

状态：

```text
open
observing
waiting_human
alert_drafted
review_due
closed
failed
```

含义：

- `open`：任务刚创建，还未进入明确观察或人工状态。
- `observing`：Agent 建议继续观察。
- `waiting_human`：需要人工确认或处理。
- `alert_drafted`：已生成告警草稿，但未发送。
- `review_due`：到了复盘时间。
- `closed`：风险已解除或任务结束。
- `failed`：任务处理异常。

实现落点：

- `backend/app/modules/agent/tasks.py`
- `resolve_agent_task(...)`
- `create_or_update_agent_task(...)`
- `close_agent_task(...)`

## 7. 自我复盘设计

复盘不是重新判断当前是否补号，而是评估历史判断是否有效。

复盘要回答：

- 上次 Agent 说风险高，后面是否继续恶化。
- 上次 Agent 建议补号，后面容量是否改善。
- 上次 Agent 判断为集中封号，后面事件流是否支持。
- 上次 Agent 是否问了上下文已经有的问题。
- 上次 Agent 是否低估或高估风险。
- 哪些经验应该写入长期记忆。

阶段五支持三类触发：

```text
用户手动要求复盘
任务进入 review_due
新一轮决策前发现存在未复盘的高风险历史任务
```

当前实现先完成“用户手动要求复盘”入口，另外为后两类触发预留数据结构和任务状态。

### 7.1 复盘输入

`review_agent_decision(...)` 会构建 `agent_decision_review_pack.v1`，包含：

- 原始 Agent decision。
- 原始容量快照。
- 当前容量快照。
- 容量变化 delta。
- 复盘窗口内后续 Agent decisions。
- 当前事件窗口摘要。
- 当前池相关人工反馈长期记忆。
- 复盘问题清单。
- 安全约束。

复盘读取现有数据库缓存和 Agent 自己的集合，不刷新 sub2api，不启动账号探测，不写账号池业务表。

### 7.2 复盘输出

复盘输出结构：

```json
{
  "schema_version": "agent_decision_review.v1",
  "review_target_decision_id": "...",
  "review_result": "useful | too_conservative | too_aggressive | insufficient_data | wrong_interpretation",
  "summary": "...",
  "what_happened_after": [],
  "accuracy_assessment": [],
  "lessons": [],
  "memory_summary_payload": {},
  "should_update_task": false,
  "next_status": null,
  "data_gaps": []
}
```

`review_result` 含义：

- `useful`：后续证据基本支持原判断。
- `too_conservative`：后续风险高于原判断，原判断可能低估。
- `too_aggressive`：后续风险低于原判断，原判断可能高估。
- `wrong_interpretation`：原判断对事件或原因的解释可能错误。
- `insufficient_data`：后续证据不足，不能强行判断准确性。

### 7.3 LLM 复盘边界

LLM 复盘模型只评估历史判断，不重新给当前池子计算补号数。

Prompt 明确要求：

- 不要重新判断当前是否补号。
- 不要给当前池子重新算补号数。
- 不要编造未给出的账号、事件、人工操作或补号结果。
- 如果后续容量、事件或人工反馈不足，必须输出 `insufficient_data`。
- 如果事件窗口已经证明 401 集中发生，不要再要求人工确认“401 是否集中”。

### 7.4 长期记忆写入

复盘完成后写入：

```text
agent_memory_summaries.memory_type = decision_review
```

写入内容来自 `memory_summary_payload`，包括：

- `summary`
- `facts`
- `patterns`
- `lessons`
- `risk_baselines`
- `source_run_ids`
- `source_decision_ids`

这些记忆会在后续 Context Pack v2 的 `long_term_memory.decision_reviews` 中被加载，帮助 Agent 避免重复犯同类判断错误。

### 7.5 当前实现落点

- `backend/app/modules/agent/reviewer.py`
- `review_agent_decision(...)`
- `_build_review_pack(...)`
- `_review_prompt()`
- `_validate_review(...)`
- `_fallback_review(...)`
- 写入 `agent_memory_summaries.memory_type = decision_review`

LLM 不可用时，reviewer 会使用保守 fallback，根据容量变化、事件窗口和后续 Agent decisions 生成可审计复盘，不会让复盘入口直接失败。

## 8. Agent Step 持久化

新增集合：

```text
agent_run_steps
```

示例结构：

```json
{
  "_id": "...",
  "schema_version": "agent_run_step.v1",
  "step_id": "...",
  "run_id": "...",
  "conversation_id": "...",
  "task_id": "...",
  "step_index": 1,
  "step_type": "observe_context",
  "status": "success",
  "intent": "pool_operation_decision",
  "input_summary": {},
  "output_summary": {},
  "llm": {
    "model": "...",
    "framework": "langchain",
    "raw_text": "..."
  },
  "capability_calls": [],
  "started_at": "...",
  "finished_at": "...",
  "error": null
}
```

字段说明：

- `schema_version`：当前为 `agent_run_step.v1`，用于后续兼容 step 结构演进。
- `step_index`：同一个 run 内的 step 顺序，从 1 开始。
- `step_type`：对应 `agent_step.v1` 的 step 类型。
- `input_summary`：只保存输入摘要，不保存完整 Context Pack。
- `output_summary`：保存本 step 的结构化输出摘要。
- `llm`：保存本 step 的 LLM 调用元信息和原始文本，便于审计。
- `capability_calls`：保存本 step 实际调用过的只读 Agent 可调用能力，例如 `api_pool_status.get`、`account_probe.get`。

实现落点：

- `backend/app/modules/agent/memory.py`
- `create_agent_step(...)`
- `finish_agent_step(...)`
- `fail_agent_step(...)`
- `list_agent_steps(...)`

当前实现说明：

- `create_agent_step(...)` 创建 running step，要求必须有 `run_id`。
- `finish_agent_step(...)` 将 step 标记为 success，写入 `output_summary`、`llm`、`capability_calls`、`finished_at` 和 `duration_ms`。
- `fail_agent_step(...)` 将 step 标记为 failed，写入错误和失败时的输出摘要。
- `list_agent_steps(...)` 按 `step_index` 升序返回某个 run 的 step 列表。
- `step_loop.py` 中每一次路由观察、Context Pack 构建、LLM controller step、只读能力调用、写记忆、更新 task、人工确认或停止，都会通过这些函数形成可审计 step。
- `read_more` 真实调用只读能力时，会把调用名称、类型、状态和结果摘要写入 `capability_calls`。

## 9. 数据库设计

阶段五新增两个 Agent 独立集合：

```text
agent_tasks
agent_run_steps
```

继续只写 Agent 自己的集合：

- `agent_runs`
- `agent_messages`
- `agent_decisions`
- `agent_memory_summaries`
- `agent_tasks`
- `agent_run_steps`

### 9.1 agent_tasks 索引

```text
pool_id + status + updated_at desc
site_id + status + updated_at desc
task_type + status + updated_at desc
next_check_at asc
review_after asc
created_at desc
```

### 9.2 agent_run_steps 索引

```text
run_id + step_index asc
conversation_id + created_at desc
task_id + created_at desc
step_type + created_at desc
created_at desc
```

实现落点：

- `backend/app/modules/system/bootstrap.py`

当前实现说明：

- `ensure_indexes(...)` 已创建 `agent_tasks` 索引：
  - `[("pool_id", 1), ("status", 1), ("updated_at", -1)]`
  - `[("site_id", 1), ("status", 1), ("updated_at", -1)]`
  - `[("task_type", 1), ("status", 1), ("updated_at", -1)]`
  - `[("next_check_at", 1)]`
  - `[("review_after", 1)]`
  - `[("created_at", -1)]`
- `ensure_indexes(...)` 已创建 `agent_run_steps` 索引：
  - `[("run_id", 1), ("step_index", 1)]`
  - `[("conversation_id", 1), ("created_at", -1)]`
  - `[("task_id", 1), ("created_at", -1)]`
  - `[("step_type", 1), ("created_at", -1)]`
  - `[("created_at", -1)]`
- 阶段五新增集合仍然只属于 Agent：
  - `agent_tasks` 用于持续任务状态机。
  - `agent_run_steps` 用于一次 run 内的多步循环审计。
- Agent 仍然只写自己的集合，不写 `accounts`、`api_pools`、`sub2api_*` 等账号池业务集合。

## 10. 路由设计

现有路由保持：

```text
POST /api/agent/pools/{pool_id}/analyze
POST /api/agent/chat
GET  /api/agent/state
GET  /api/agent/runs
GET  /api/agent/conversations/{conversation_id}/messages
```

阶段五新增：

```text
GET  /api/agent/tasks
GET  /api/agent/tasks/{task_id}
GET  /api/agent/runs/{run_id}/steps
POST /api/agent/decisions/{decision_id}/review
```

当前主页面可以先不展示完整 steps，只在 run/decision 的 `agent.step_loop` 中保留摘要，后续调试页再展开。

当前实现落点：

- `backend/app/routers/agent.py`
- `GET /api/agent/tasks`
  - 查询 Agent 自己的任务状态。
  - 支持 `pool_id`、`status`、`limit`。
- `GET /api/agent/tasks/{task_id}`
  - 查询单个 Agent task 详情。
  - 找不到时返回 404。
- `GET /api/agent/runs/{run_id}/steps`
  - 查询某次 run 的 step trace。
  - 按 `step_index` 升序返回。
  - 用于后续调试页或 run 详情页，当前主页面不强制展示。
- `POST /api/agent/decisions/{decision_id}/review`
  - 手动触发历史 decision 复盘。
  - 请求体可传 `review_window_hours`，范围 1 到 168，默认 24。
  - 复盘只写 Agent 长期记忆 `agent_memory_summaries`，不写账号池业务表。
  - 写入审计日志 `agent.decision.review`。

## 11. 前端展示原则

阶段五前端仍然保持克制。

主页面展示：

- 最新主决策。
- 风险等级。
- 是否建议补号。
- 建议补多少。
- 核心依据。
- 事件判断摘要。
- 数据缺口。
- 下一步观察重点。
- 当前任务状态。

暂不在主页面展示：

- 完整 step trace。
- 完整历史对话。
- 完整 Context Pack。
- 80 条事件明细。
- 全量长期记忆。

后续可新增：

- Agent 任务页。
- Run 详情页。
- Step 调试页。
- 决策复盘页。

## 12. 与 LangChain 的关系

阶段五仍使用 LangChain，但不把系统工程放进 LangChain。

LangChain 负责：

- Intent Router prompt。
- Controller Step prompt。
- Decision prompt。
- Review prompt。
- 结构化输出。
- 少量只读能力编排。

FastAPI / MongoDB 负责：

- run 创建。
- task 状态机。
- step 持久化。
- Context Pack 构建。
- 权限和审计。
- 前端状态恢复。

如果后续 step loop 分支越来越复杂，可以评估 LangGraph。阶段五先用普通 LangChain + 后端显式状态机完成。

## 13. 当前实现落点

本阶段已实现：

- `chat.py` 的 analyze/chat 入口进入 `run_agent_controller(...)`。
- `controller.py` 统一创建 run、写 user message、解析 task、调用 step loop、保存最终 report。
- `intent_router.py` 支持八类 intent。
- `step_loop.py` 支持有界多步循环和 `agent_step.v1`。
- `memory.py` 支持 `agent_run_steps` 创建、完成、失败、查询。
- `tasks.py` 支持 task resolve、create/update、close、list。
- `reviewer.py` 支持历史 decision 复盘入口，并写入长期记忆。
- `bootstrap.py` 增加 `agent_tasks` 和 `agent_run_steps` 索引。

需要继续完善：

- 暴露 `GET /api/agent/runs/{run_id}/steps`。
- 暴露 Agent task 列表和详情路由。
- 细化 `update_task_state` 的业务状态规则，例如 review_due、closed、alert_drafted 的转移条件。
- 让 reviewer 从“基础复盘”升级为结合后续事件和容量变化的 LLM 复盘。
- 前端如有需要，再加调试折叠区或独立 run 详情页。

## 14. 验收标准

### 14.1 意图路由验收

- 问“今天这个池子要不要补号”，进入 `pool_operation_decision`。
- 问“最近 24h 有多少 401”，进入 `pool_data_question`。
- 问“复盘昨天那次判断”，进入 `decision_review`。
- 说“这次不是异常流量，是批量任务”，进入 `operator_feedback`。
- 问“你现在会做什么”，进入 `agent_usage_question`。
- 问“你好”，进入 `smalltalk_or_out_of_scope`。
- 说“直接帮我推号”，进入 `unauthorized_action_request`。

### 14.2 多步循环验收

- 一次 run 可以保存多个 step。
- step 有顺序、状态、输入摘要、输出摘要。
- 达到最终决策后停止。
- 需要人工确认时停止。
- 闲聊和越权请求不构建完整运营 decision。
- 达到最大步数时安全停止。

### 14.3 任务状态验收

- 高风险决策可以创建或更新 task。
- task 状态可以进入 `observing`、`waiting_human`、`alert_drafted`。
- 状态变化写入 `state_history`。

### 14.4 安全验收

- 不写账号池业务表。
- 不触发 sub2api 刷新。
- 不启动账号探测。
- 不自动推号、买号、删号。
- 不自动发送钉钉正式通知。
- 越权请求有明确拒绝和审计记录。

### 14.5 构建验收

```text
python -m compileall backend\app\modules\agent
npm run build
```

## 15. 阶段五完成后的形态

阶段五完成后，Agent 不再是简单的：

```text
用户说一句 -> LLM 决策一次 -> 保存结果
```

而是变成：

```text
用户或系统触发
-> Agent 先判断这是什么任务
-> 如果是运营问题，进入多步观察和决策
-> 如果是反馈，沉淀记忆
-> 如果是复盘，评估历史判断
-> 如果是闲聊或越权，直接回答或拒绝
-> 如果存在持续风险，更新任务状态
-> 如果需要继续观察，留下下一轮观察计划
```

这一步是后续“自启动 loop”和“告警触发”的前置条件。只有当 Agent 能正确分流、多步循环、维护任务状态、复盘自己的判断之后，才适合进入真正的周期自启动和事件触发阶段。
