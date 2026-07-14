# 阶段三：Context Pack 与 LLM 主决策设计

本文档承接：

```text
docs/agent/账号池运营Agent总体架构.md
docs/agent/阶段一-Agent-LLM配置与调用层设计.md
docs/agent/阶段二-Agent持久化与前端缓存任务.md
```

阶段一已经完成 Agent LLM 配置与 LangChain 调用层。

阶段二已经完成 Agent 自身运行记录、对话消息、决策结果的持久化，以及前端最近状态恢复。

阶段三的目标是把当前 Agent 从“LLM 规划能力调用 + 规则生成补号建议 + LLM 解释”升级为“后端组装完整 Context Pack，LLM 基于上下文输出主决策，后端只做结构校验、安全校验和兜底”。

## 1. 阶段目标

阶段三的核心目标：

- 新增 Agent Context Pack 构建层。
- 每次 Agent 运行前，由后端统一收集账号池态势、容量、探测、历史决策和对话上下文。
- 把 Context Pack 作为 LLM 主决策输入。
- LLM 输出结构化业务决策。
- 补号数量、风险等级、是否告警、下一步动作由 LLM 主导。
- 后端不再用固定 `target_active` 或固定公式覆盖 LLM 的最终补号结论。
- 后端只做结构校验、安全边界校验、范围保护和 fallback。
- 继续写入 `agent_runs / agent_messages / agent_decisions`。
- 继续保持只读，不执行推号、删号、买号等高风险动作。

阶段三完成后，Agent 应具备：

```text
用户点击分析 / 用户提问
-> 创建 run
-> 构建 Context Pack
-> LLM 主决策
-> 校验与安全兜底
-> 保存 decision
-> 保存 assistant message
-> 前端恢复展示
```

## 2. 不做范围

阶段三暂不做：

- 自动定时 loop。
- 告警事件自动触发 Agent。
- 钉钉自动通知。
- 创建待办。
- 人工确认动作流。
- 推号、删号、买号、修改账号池配置。
- 多账号池批量巡检。
- 多轮自启动循环。
- LangGraph 状态机重构。

这些放到后续阶段。

阶段三仍然是手动触发为主，但这次手动触发后的决策方式要改为 LLM 主决策。

## 3. 当前基础

当前已有能力：

### 3.1 LLM 配置

已完成：

- 系统管理页 Agent LLM 配置。
- 数据库优先读取 Agent LLM 配置。
- OpenAI-compatible URL / API Key / Level 1 模型。
- 测试连接。
- LangChain 调用适配。

相关文件：

```text
backend/app/modules/agent/settings.py
backend/app/modules/agent/llm_client.py
backend/app/modules/agent/llm.py
backend/app/modules/agent/langchain_adapter.py
backend/app/routers/settings.py
frontend/src/pages/ApiTokensPage.tsx
```

### 3.2 Agent 持久化

已完成：

- `agent_runs`
- `agent_messages`
- `agent_decisions`

相关文件：

```text
backend/app/modules/agent/memory.py
backend/app/modules/agent/orchestrator.py
backend/app/routers/agent.py
frontend/src/pages/AgentAnalysisPage.tsx
```

### 3.3 只读数据能力

已完成或已有：

- 读取 API 账号池状态。
- 读取账号探测摘要。
- 读取现有 sub2api 缓存。
- 读取最近 Agent state / messages / runs。

相关文件：

```text
backend/app/modules/agent/capacity.py
backend/app/modules/agent/probe.py
backend/app/modules/agent/capabilities.py
backend/app/modules/agent/memory.py
```

## 4. 阶段三总体流程

阶段三建议新流程：

```text
run_agent_analysis
  |
  v
create_agent_run
  |
  v
build_agent_context_pack
  |
  v
invoke_llm_primary_decision
  |
  v
validate_agent_decision
  |
  v
save_agent_decision
  |
  v
append assistant message
  |
  v
finish_agent_run
```

异常流程：

```text
任意步骤异常
  |
  v
fail_agent_run
  |
  v
返回错误或进入 deterministic fallback
```

## 5. 新增 Context Pack 构建层

### 5.1 新增文件

建议新增：

```text
backend/app/modules/agent/context_pack.py
```

职责：

- 根据 `pool_id / user_message / trigger / conversation_id` 构建本轮 Agent 上下文。
- 读取账号池基础信息。
- 读取账号池容量状态。
- 读取账号探测摘要。
- 读取最近 Agent 决策。
- 读取最近对话消息。
- 汇总系统约束。
- 输出稳定结构，供 LLM 主决策使用。

建议函数：

```python
async def build_agent_context_pack(
    db,
    *,
    trigger: str,
    pool_id: str | None,
    user_message: str | None,
    conversation_id: str | None,
    actor: dict | None = None,
) -> dict:
    ...
```

### 5.2 Context Pack 结构

建议输出：

```json
{
  "schema_version": "agent_context_pack.v1",
  "run": {
    "trigger": "manual_chat",
    "user_message": "...",
    "conversation_id": "...",
    "created_by": "..."
  },
  "target_pool": {
    "pool_id": "...",
    "site_id": "...",
    "group_id": 5,
    "name": "pro账号池",
    "account_type": "pro"
  },
  "capacity": {
    "active_account_count": 19,
    "reserve_account_count": 0,
    "available_accounts": 19,
    "current_speed_days": 5.6,
    "recent_day_five_hour_peak_multiple": 2.15,
    "burst_1h_five_hour_multiple": 1.12,
    "burst_1h_trend_label": "上涨",
    "burst_1h_trend_strength_label": "弱",
    "five_hour_remaining_usd": 1234,
    "seven_day_remaining_usd": 5678,
    "cache_fresh": true,
    "last_refreshed_at": "..."
  },
  "probe": {
    "probe_fresh": true,
    "last_probe_at": "...",
    "detected_401_1h": 0,
    "detected_401_24h": 0,
    "detected_401_7d": 0,
    "recovered_24h": 0,
    "duplicate_email_alert_count": 1,
    "median_survival_hours_7d": null
  },
  "recent_agent_decisions": [
    {
      "created_at": "...",
      "severity": "warning",
      "summary": "...",
      "suggested_add_count": 11,
      "requires_human_confirm": true
    }
  ],
  "conversation": [
    {
      "role": "user",
      "content": "今天这个池要不要补号？",
      "created_at": "..."
    },
    {
      "role": "assistant",
      "content": "...",
      "created_at": "..."
    }
  ],
  "system_constraints": {
    "read_only": true,
    "can_send_dingtalk": false,
    "can_create_todo": false,
    "can_push_accounts": false,
    "can_delete_accounts": false,
    "can_buy_accounts": false,
    "high_risk_actions_require_human_confirm": true
  },
  "data_quality": {
    "capacity_available": true,
    "probe_available": true,
    "history_available": true,
    "warnings": []
  }
}
```

### 5.3 数据来源

Context Pack 不新增账号池业务写入，只读取现有数据。

建议来源：

```text
target_pool
-> backend/app/modules/agent/capacity.py:list_agent_pools

capacity
-> backend/app/modules/agent/capacity.py:read_pool_capacity

probe
-> backend/app/modules/agent/probe.py:read_probe_summary

recent_agent_decisions
-> agent_decisions

conversation
-> agent_messages

system_constraints
-> 当前阶段硬编码只读约束
-> 后续阶段接入配置
```

### 5.4 最近历史数量

建议初始限制：

```text
recent_agent_decisions: 最近 5 条
conversation: 最近 20 条
```

原因：

- 控制上下文长度。
- 保留足够近期判断。
- 避免把所有历史消息直接塞给模型。

后续可以做摘要记忆。

## 6. LLM 主决策层

### 6.1 新增或调整文件

建议新增：

```text
backend/app/modules/agent/decision_core.py
```

职责：

- 接收 Context Pack。
- 通过 LangChain 调用 Level 1 模型。
- 要求 LLM 输出结构化 JSON。
- 解析并返回业务决策。

建议函数：

```python
async def decide_with_context_pack(
    db,
    *,
    context_pack: dict,
) -> dict:
    ...
```

### 6.2 Prompt 定位

Prompt 必须明确告诉 LLM：

- 你是账号池运营 Agent。
- 你不是普通聊天助手。
- 你的任务是基于 Context Pack 做运营决策。
- 补多少号由你判断。
- 后端不会用固定公式替代你的最终判断。
- 你必须输出 JSON。
- 当前阶段只读，不允许输出直接执行高风险动作。
- 如果数据不足，要明确指出 data_gaps。

### 6.3 系统提示词草案

```text
你是 AIwelink 账号池运营 Agent。

你的任务是根据账号池 Context Pack 判断当前账号池风险、是否需要补号、建议补多少号、是否需要告警、是否需要人工确认，以及下一步运营动作。

你必须基于上下文做业务判断。不要假设不存在的数据。不要编造账号数量、额度或事件。

当前系统处于只读阶段：
- 你可以提出建议。
- 你可以建议通知或人工确认。
- 你不能直接推号。
- 你不能删除账号。
- 你不能购买账号。
- 你不能修改账号池配置。

补号数量由你根据上下文判断，不要机械等同于 target_active 缺口。你需要综合：
- 当前可用账号数。
- active 与 reserve 情况。
- 5h / 7d 剩余额度。
- 当前速度可支撑时间。
- 24h 峰值、7d 峰值、突发 1h 预估。
- 突发趋势。
- 账号探测异常。
- 最近 Agent 决策。
- 人工对话上下文。
- 数据新鲜度。

如果数据不足，请在 data_gaps 中说明。

必须只输出 JSON，不要输出 Markdown。
```

### 6.4 用户消息结构

用户输入给 LLM 的内容应是 JSON 序列化后的 Context Pack：

```json
{
  "task": "make_pool_operation_decision",
  "context_pack": {}
}
```

## 7. LLM 决策输出结构

阶段三建议使用新的结构化输出：

```json
{
  "decision_type": "pool_operation_decision",
  "schema_version": "agent_decision.v1",
  "severity": "healthy | watch | warning | danger | critical",
  "summary": "自然语言总结",
  "operator_message": "展示给运营人员看的自然语言回答",
  "should_add_accounts": true,
  "suggested_add_count": 12,
  "confidence": "low | medium | high",
  "main_reasons": [],
  "risk_factors": [],
  "data_gaps": [],
  "should_alert": false,
  "alert_channels": [],
  "requires_human_confirm": true,
  "recommended_actions": [
    {
      "action_type": "prepare_accounts",
      "title": "准备 12 个 pro 新号",
      "reason": "...",
      "risk_level": "medium",
      "requires_human_confirm": true
    }
  ],
  "next_observation_focus": [],
  "follow_up_questions": [],
  "continue_decision_loop": false
}
```

### 7.1 severity

允许值：

```text
healthy
watch
warning
danger
critical
```

含义建议：

- `healthy`：无明显风险。
- `watch`：需要观察，但无需立即动作。
- `warning`：需要运营关注，可能需要准备账号。
- `danger`：容量或异常风险明显，需要尽快处理。
- `critical`：严重风险，需要立即通知或人工介入。

### 7.2 suggested_add_count

由 LLM 输出。

后端只校验：

- 必须是整数。
- 不能小于 0。
- 不能超过阶段三安全上限。
- 如果超过安全阈值，则 `requires_human_confirm=true`。

建议阶段三安全上限：

```text
0 <= suggested_add_count <= 200
```

该上限不是业务算法，只是防止模型异常输出。

### 7.3 recommended_actions

阶段三只允许建议类动作，不执行动作。

允许的动作类型：

```text
observe
prepare_accounts
manual_review
notify_draft
investigate_probe
investigate_capacity
```

不允许阶段三输出为可执行动作：

```text
push_accounts
delete_accounts
buy_accounts
modify_pool_config
send_dingtalk
```

如果模型输出这些动作，Validator 应降级为 `manual_review` 或标记为 blocked。

### 7.4 continue_decision_loop

阶段三可以让模型输出是否建议继续一轮决策，但暂不自动执行 loop。

用途：

- 为后续 Agent Loop 阶段预留字段。
- 当前阶段只保存该建议，不自动再次运行。

## 8. 决策校验与安全保护

### 8.1 新增文件

建议新增：

```text
backend/app/modules/agent/decision_validator.py
```

职责：

- 校验 LLM 输出结构。
- 填补默认字段。
- 限制枚举值。
- 限制补号数量范围。
- 阻止高风险动作。
- 生成可保存的标准决策对象。

建议函数：

```python
def validate_agent_decision(raw: dict, *, context_pack: dict) -> dict:
    ...
```

### 8.2 校验规则

必须校验：

- `decision_type == "pool_operation_decision"`。
- `severity` 在允许枚举内。
- `suggested_add_count` 是整数。
- `suggested_add_count >= 0`。
- `suggested_add_count <= 200`。
- `confidence` 在 `low / medium / high` 内。
- `main_reasons` 是 list。
- `risk_factors` 是 list。
- `data_gaps` 是 list。
- `recommended_actions` 是 list。
- 不允许高风险动作进入执行态。

### 8.3 Validator 不做什么

Validator 不应该：

- 用固定公式重算补号数量并覆盖 LLM。
- 因为 `target_active=30` 就强制补到 30。
- 把所有 warning 都改成 danger。
- 替 LLM 编造业务原因。

Validator 的职责是“保安全、保结构”，不是重新做业务决策。

## 9. 与现有规则引擎的关系

当前系统已有 `refill_decision.calculate` 和 deterministic fallback。

阶段三不要求立刻删除它们，但需要重新定位。

### 9.1 保留用途

规则引擎可以作为：

- LLM 未配置时的 fallback。
- LLM 调用失败时的 fallback。
- 校验参考信息。
- 决策对比字段。

### 9.2 不再作为主路径

当 LLM 可用且 Context Pack 构建成功时：

```text
最终 suggested_add_count
最终 severity
最终 recommended_actions
```

应来自 LLM 主决策。

规则结果可以进入 Context Pack 的 `rule_baseline`：

```json
{
  "rule_baseline": {
    "severity": "warning",
    "suggested_add_count": 11,
    "reasons": []
  }
}
```

但 Prompt 必须说明：

```text
rule_baseline 只是参考，不是必须采纳的答案。
```

## 10. 持久化调整

阶段三继续使用阶段二已建立的集合：

```text
agent_runs
agent_messages
agent_decisions
```

### 10.1 agent_runs 增强字段

建议在 run 中增加：

```json
{
  "context_pack": {},
  "context_pack_summary": {},
  "decision_mode": "llm_primary | deterministic_fallback",
  "validator": {
    "status": "passed | adjusted | failed",
    "warnings": []
  }
}
```

注意：

- 初期可以保存完整 Context Pack。
- 如果后续 Context Pack 过大，再改为保存摘要 + 引用。

### 10.2 agent_decisions 增强字段

建议保存：

```json
{
  "decision": {
    "decision_type": "pool_operation_decision",
    "schema_version": "agent_decision.v1",
    "severity": "warning",
    "suggested_add_count": 12,
    "confidence": "medium",
    "main_reasons": [],
    "risk_factors": [],
    "data_gaps": [],
    "recommended_actions": [],
    "next_observation_focus": []
  },
  "context_pack_snapshot": {},
  "validator_warnings": [],
  "decision_mode": "llm_primary"
}
```

### 10.3 agent_messages

assistant message 应优先使用：

```text
decision.operator_message
```

如果没有，则使用：

```text
decision.summary
```

## 11. 后端改造任务

### 11.1 新增 context_pack.py

文件：

```text
backend/app/modules/agent/context_pack.py
```

任务：

- 实现 `build_agent_context_pack`。
- 复用 `read_pool_capacity`。
- 复用 `read_probe_summary`。
- 读取最近 `agent_decisions`。
- 读取最近 `agent_messages`。
- 输出 `agent_context_pack.v1`。

### 11.2 新增 decision_core.py

文件：

```text
backend/app/modules/agent/decision_core.py
```

任务：

- 实现 `decide_with_context_pack`。
- 使用 LangChain 调用 Level 1。
- 输出 JSON。
- 复用现有 LLM 配置读取。

### 11.3 新增 decision_validator.py

文件：

```text
backend/app/modules/agent/decision_validator.py
```

任务：

- 实现结构校验。
- 实现安全动作过滤。
- 实现补号数量范围保护。
- 返回标准化 decision。

### 11.4 改造 orchestrator.py

文件：

```text
backend/app/modules/agent/orchestrator.py
```

改造目标：

- 在 run 创建后构建 Context Pack。
- 优先调用 LLM 主决策。
- LLM 失败时 fallback 到现有 deterministic 流程。
- 保存 decision 时写入 `decision_mode`。
- run 成功时保存 context pack 或摘要。

### 11.5 调整 memory.py

文件：

```text
backend/app/modules/agent/memory.py
```

可能需要：

- `finish_agent_run` 支持保存 `context_pack / decision_mode / validator`。
- `save_agent_decision` 支持保存 `context_pack_snapshot / validator_warnings / decision_mode`。

### 11.6 调整前端展示

文件：

```text
frontend/src/pages/AgentAnalysisPage.tsx
```

阶段三前端不需要大改，只需兼容新字段：

- `confidence`
- `data_gaps`
- `risk_factors`
- `recommended_actions`
- `next_observation_focus`
- `decision_mode`

不展示完整 Context Pack。

## 12. 接口响应兼容

现有接口保持：

```text
POST /api/agent/pools/{pool_id}/analyze
POST /api/agent/chat
GET  /api/agent/state
```

响应可以逐步增加字段：

```json
{
  "run_id": "...",
  "conversation_id": "...",
  "decision_id": "...",
  "decision_mode": "llm_primary",
  "context_pack_version": "agent_context_pack.v1",
  "validator": {
    "status": "passed",
    "warnings": []
  },
  "decision": {},
  "llm": {},
  "agent": {}
}
```

前端已有字段继续保留：

```text
severity
headline
suggested_actions
capacity
probe
llm
agent
```

这样可以避免一次性大改前端。

## 13. 开发顺序

建议按以下顺序开发：

1. 新增 `context_pack.py`，只构建 Context Pack，不接 LLM。
2. 给 `GET /api/agent/state` 或临时调试日志验证 Context Pack 数据完整性。
3. 新增 `decision_validator.py`，定义标准 decision schema。
4. 新增 `decision_core.py`，用 LangChain 调 LLM 输出结构化 JSON。
5. 改造 `orchestrator.py`，让手动分析优先走 LLM 主决策。
6. 保留 deterministic fallback。
7. 扩展 `memory.py` 保存 `context_pack / decision_mode / validator`。
8. 前端兼容展示新 decision 字段。
9. 跑后端编译和前端构建。
10. 本地用真实账号池数据进行人工验收。

## 14. 验收标准

### 14.1 Context Pack 验收

- 点击分析时能构建 Context Pack。
- Context Pack 包含目标账号池。
- Context Pack 包含容量数据。
- Context Pack 包含探测数据。
- Context Pack 包含最近 Agent 决策。
- Context Pack 包含最近对话。
- Context Pack 包含只读系统约束。
- 不触发 sub2api 重新拉取。
- 不写账号池业务表。

### 14.2 LLM 主决策验收

- LLM 输出结构化 JSON。
- `suggested_add_count` 来自 LLM 输出。
- `severity` 来自 LLM 输出。
- `recommended_actions` 来自 LLM 输出。
- 后端不再用 `target_active` 缺口直接覆盖补号数量。
- LLM 未配置或失败时，可以 fallback。

### 14.3 Validator 验收

- 非法 JSON 会失败或重试。
- 非法 severity 会被修正或失败。
- 负数补号会被修正为 0 或失败。
- 超过安全上限会被限制并要求人工确认。
- 高风险动作不会被执行。

### 14.4 持久化验收

- `agent_runs` 保存 decision mode。
- `agent_runs` 保存 context pack 或摘要。
- `agent_decisions` 保存 LLM 主决策结构。
- `agent_messages` 保存自然语言回答。
- 刷新前端后仍能看到最近结果。

### 14.5 构建验收

```text
python -m compileall backend\app
npm run build
```

## 15. 风险与注意事项

### 15.1 不要把 Context Pack 做成 tools 调用集合

Context Pack 是后端主动组装的上下文，不是让 LLM 一步步调用 tools 拼出来。

tools 或可调用能力仍然存在，但它们不是基础上下文构造主路径。

### 15.2 不要让规则引擎继续主导补号数

阶段三最重要的变化是：

```text
补号数量由 LLM 输出。
规则只做 fallback 和参考。
```

如果继续让规则结果覆盖 LLM，就没有完成阶段三。

### 15.3 不要展示完整 Context Pack

Context Pack 可以很大，也可能包含内部字段。

前端只展示：

- 决策结果。
- 关键原因。
- 数据缺口。
- 下一步观察重点。
- 能力调用和 validator 摘要。

### 15.4 不要执行高风险动作

阶段三仍是只读决策阶段。

允许：

- 保存 run。
- 保存 message。
- 保存 decision。
- 展示建议。

不允许：

- 推号。
- 删号。
- 买号。
- 修改账号池配置。
- 自动发送钉钉通知。

### 15.5 需要保留 fallback

LLM 主决策不代表没有 fallback。

如果 LLM 未配置、超时、返回非法 JSON，系统应：

- 记录失败原因。
- 标记 run 中的 LLM 错误。
- 使用 deterministic fallback 维持页面可用。
- 明确告诉用户当前结果来自 fallback。

## 16. 阶段三完成后的下一步

阶段三完成后，进入：

```text
阶段四：Agent Loop 与定时自启动
```

阶段四目标：

- 使用系统管理中的 loop 配置。
- 后端启动 Agent scheduler loop。
- 定时构建 Context Pack。
- 定时调用 LLM 主决策。
- 保存每轮结果。
- 前端展示最近 loop 状态。
- 防止并发重复运行。

阶段三和阶段四的分界：

```text
阶段三解决“Agent 如何基于完整上下文做主决策”。
阶段四解决“Agent 如何自己定时醒来并持续运行”。
```

1. 触发 Agent
   来源仍然是：
   - 点击“分析”
   - 用户自然语言提问

2. 创建 run
   写入 agent_runs：
   - run_id
   - trigger
   - conversation_id
   - pool_id
   - status=running

3. 构建 Context Pack
   后端主动收集完整上下文：
   - 目标账号池信息
   - API 账号池状态
   - 5h / 7d 容量
   - 突发 1h 峰值和趋势
   - 账号探测摘要
   - 最近 Agent 决策
   - 最近对话消息
   - 当前系统安全约束

4. 调用 LLM 主决策
   把 Context Pack 给 LLM。
   让 LLM 决定：
   - 当前风险等级
   - 是否需要补号
   - 建议补多少号
   - 为什么
   - 是否需要人工确认
   - 是否需要告警
   - 下一步建议动作
   - 数据是否不足

5. 决策校验
   后端不替 LLM 重算补号数，只做保护：
   - JSON 格式是否合法
   - severity 是否在枚举内
   - suggested_add_count 是否是非负整数
   - 是否超过安全上限
   - 是否包含阶段三禁止动作
   - 高风险动作是否被降级为人工确认

6. 保存结果
   写入：
   - agent_runs：本次运行、context pack 摘要、LLM 信息、校验结果
   - agent_decisions：LLM 主决策
   - agent_messages：用户消息和 Agent 自然语言回复

7. 前端展示
   前端继续展示：
   - 最近分析结果
   - 对话消息
   - 风险等级
   - 建议补号数
   - 原因
   - 数据缺口
   - 下一步观察重点
   - Agent 决策模式：llm_primary / fallback

8. fallback
   如果 LLM 未配置、超时、返回非法 JSON：
   - run 标记错误信息
   - 使用当前 deterministic fallback 保持页面可用
   - 明确标记 decision_mode=deterministic_fallback