# 阶段二：Agent 持久化与前端缓存任务

本文档承接 `账号池运营Agent总体架构.md` 和 `阶段一-Agent-LLM配置与调用层设计.md`。

阶段一已经完成 Agent LLM 配置、测试连接、数据库优先读取和 LangChain 调用入口。阶段二的目标是让 Agent 从“一次请求一次展示”升级为“每次运行可追踪、对话可恢复、前端刷新不丢失”。

## 1. 阶段目标

阶段二只做 Agent 自身数据的持久化和前端恢复，不改账号池业务数据，不执行补号、推号、删号等动作。

本阶段完成后应具备：

- 每次点击分析或发送自然语言问题都会生成 `agent_runs` 运行记录。
- 用户输入和 Agent 回复会写入 `agent_messages`。
- Agent 的最终分析结果会写入 `agent_decisions`。
- Agent 能把能力调用过程、LLM 信息和错误记录进 run。
- 前端进入 Agent 分析页时能读取最近一次结果。
- 前端刷新页面后，对话和最近分析结果不丢失。
- 后续 Agent loop 可以复用这些历史记录作为记忆来源。

## 2. 不做范围

阶段二暂不做：

- 自动定时 loop。
- 告警事件触发 Agent。
- 钉钉自动通知。
- 人工确认后的动作执行。
- 购买账号、删除账号、推送账号等高风险动作。
- 完整 Context Pack 重构。
- 让 LLM 完全替代当前规则 fallback 的业务判断。

这些放到后续阶段处理。

## 3. 数据库集合

所有集合都使用现有 MongoDB 连接，但新建 Agent 独立集合，不与账号池业务集合混用。

### 3.1 agent_runs

保存每次 Agent 运行。

```json
{
  "_id": "...",
  "run_id": "...",
  "trigger": "manual_analyze | manual_chat | scheduler | alert_event",
  "status": "running | success | failed",
  "conversation_id": "...",
  "pool_id": "...",
  "site_id": "...",
  "user_message": "...",
  "started_at": "...",
  "finished_at": "...",
  "duration_ms": 1234,
  "llm": {
    "enabled": true,
    "configured": true,
    "model": "...",
    "source": "database | environment",
    "framework": "langchain | http_fallback"
  },
  "agent": {
    "mode": "...",
    "planned_by": "...",
    "intent": "...",
    "thought": "...",
    "capability_plan": [],
    "capability_trace": []
  },
  "summary": "...",
  "severity": "healthy | watch | warning | danger | critical",
  "decision_id": "...",
  "error": null,
  "created_by": "...",
  "created_at": "...",
  "updated_at": "..."
}
```

### 3.2 agent_messages

保存对话消息。

```json
{
  "_id": "...",
  "message_id": "...",
  "conversation_id": "...",
  "run_id": "...",
  "pool_id": "...",
  "site_id": "...",
  "role": "user | assistant | system",
  "content": "...",
  "metadata": {},
  "created_by": "...",
  "created_at": "..."
}
```

### 3.3 agent_decisions

保存 Agent 最终分析/决策结果。

```json
{
  "_id": "...",
  "decision_id": "...",
  "run_id": "...",
  "conversation_id": "...",
  "pool_id": "...",
  "site_id": "...",
  "severity": "...",
  "headline": "...",
  "summary": "...",
  "decision": {},
  "reasons": [],
  "suggested_actions": [],
  "capacity_snapshot": {},
  "probe_snapshot": {},
  "llm_output": {},
  "requires_human_confirm": false,
  "created_by": "...",
  "created_at": "..."
}
```

## 4. 后端任务

### 4.1 新增持久化模块

新增：

```text
backend/app/modules/agent/memory.py
```

职责：

- 创建 run。
- 完成 run。
- 标记 run 失败。
- 写入 user / assistant message。
- 写入 decision。
- 查询最近一次 Agent 状态。
- 查询某个 conversation 的消息。
- 查询最近 N 次 runs。

建议函数：

```python
async def create_agent_run(db, *, trigger, actor, pool_id=None, user_message=None, conversation_id=None) -> dict: ...
async def finish_agent_run(db, *, run_id, report, decision_id=None) -> dict: ...
async def fail_agent_run(db, *, run_id, error) -> dict: ...
async def append_agent_message(db, *, conversation_id, role, content, run_id=None, actor=None, metadata=None) -> dict: ...
async def save_agent_decision(db, *, run_id, report, actor=None) -> dict: ...
async def get_agent_latest_state(db, *, pool_id=None) -> dict: ...
async def list_agent_messages(db, *, conversation_id, limit=50) -> dict: ...
async def list_agent_runs(db, *, pool_id=None, limit=20) -> dict: ...
```

### 4.2 改造 Agent 运行入口

当前入口：

```text
backend/app/modules/agent/orchestrator.py
```

阶段二改造：

- `run_agent_analysis` 开始时创建 run。
- `manual_chat` 触发时写入 user message。
- `_execute_plan` 成功后保存 decision。
- 保存 assistant message。
- run 成功时写入 summary、severity、decision_id、agent trace。
- 任意异常时标记 run failed，并保存 error。

注意：

- 仍然不改账号池业务数据。
- 当前能力调用仍为只读。
- 当前 deterministic fallback 可以保留，但结果必须落库。

### 4.3 扩展 Agent 路由

在：

```text
backend/app/routers/agent.py
```

新增接口：

```text
GET /api/agent/state
GET /api/agent/runs
GET /api/agent/conversations/{conversation_id}/messages
```

建议返回：

```json
{
  "latest_run": {},
  "latest_decision": {},
  "messages": [],
  "running": false
}
```

现有接口保持不变：

```text
POST /api/agent/pools/{pool_id}/analyze
POST /api/agent/chat
```

但响应中应增加：

```json
{
  "run_id": "...",
  "conversation_id": "...",
  "decision_id": "..."
}
```

### 4.4 审计与权限

权限沿用现有 Agent 分析接口：

```text
owner, admin, maintainer
```

阶段二不要求每条 message 都写系统 audit log，但 run 创建和失败可以考虑写入 Agent 自身 run 记录即可。系统审计仍主要用于配置变更等管理动作。

## 5. 前端任务

当前页面：

```text
frontend/src/pages/AgentAnalysisPage.tsx
```

阶段二改造目标：

- 页面加载时请求 `GET /api/agent/state`。
- 如果存在最近 run，恢复最近分析结果。
- 如果存在 conversation，恢复最近消息。
- 点击分析后使用返回的 `run_id` / `conversation_id` 更新页面状态。
- 用户发送自然语言问题后，消息不只存在 React state，而是以后端返回为准。
- 刷新页面后仍能看到最近一次 Agent 回复。

建议前端状态：

```ts
type AgentRun = {
  run_id: string;
  status: string;
  trigger: string;
  pool_id?: string | null;
  severity?: string | null;
  summary?: string | null;
  started_at?: string;
  finished_at?: string | null;
};

type AgentMessage = {
  message_id: string;
  conversation_id: string;
  run_id?: string | null;
  role: "user" | "assistant" | "system";
  content: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
};
```

## 6. 开发顺序

建议按以下顺序执行：

1. 新增 `backend/app/modules/agent/memory.py`。
2. 实现 `agent_runs / agent_messages / agent_decisions` 写入函数。
3. 改造 `run_agent_analysis`，让现有分析流程创建 run 并保存结果。
4. 扩展 `POST /agent/chat` 和 `POST /agent/pools/{pool_id}/analyze` 响应中的 `run_id / conversation_id / decision_id`。
5. 新增 `GET /agent/state`。
6. 新增 `GET /agent/runs`。
7. 新增 `GET /agent/conversations/{conversation_id}/messages`。
8. 前端页面加载时恢复最近 state。
9. 前端发送消息后以后端持久化结果刷新消息列表。
10. 运行后端编译和前端构建。

## 7. 验收标准

后端验收：

- 点击分析会写入一条 `agent_runs`。
- 点击分析会写入一条 `agent_decisions`。
- 自然语言提问会写入 user 和 assistant 两条 `agent_messages`。
- 运行失败时 `agent_runs.status=failed`，并有 error。
- `GET /api/agent/state` 能返回最近一次 run 和 decision。
- `GET /api/agent/conversations/{conversation_id}/messages` 能返回历史对话。

前端验收：

- 刷新 Agent 分析页后，最近一次分析结果仍显示。
- 刷新后，最近对话仍显示。
- 新问题发送后，页面显示的消息来自后端返回或后端重新拉取。
- 不显示完整底层上下文，避免页面过重。

构建验收：

```text
python -m compileall backend\app
npm run build
```

## 8. 阶段完成后的下一步

阶段二完成后，再进入：

```text
阶段三：Context Pack 与 LLM 主决策
```

阶段三重点是让后端先组装完整上下文，再让 LLM 输出结构化业务决策，并逐步减少规则引擎对最终补号数量的主导。
