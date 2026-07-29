# 账号池运营 Agent 总体架构

本文档用于重新定义账号池运营 Agent 的总体方向。

后续 Agent 开发以本文档为主线。已有代码中关于页面、LLM 接入、只读能力、分析展示的部分可以作为参考或可复用组件，但不再把“点击分析后调用几个能力并返回一次答案”作为 Agent 的核心形态。

## 1. 核心定位

账号池运营 Agent 不是普通聊天助手，也不是一组 tools 的包装层。

它的定位是：

> 持续观察账号池运行状态，结合账号池容量、账号探测、告警事件、历史决策和人工反馈，由大模型主导判断当前风险、补号需求、告警优先级和下一步运营动作的决策系统。

Agent 的重点不是“用户问一句才执行一次”，而是具备持续运行能力：

```text
周期启动
-> 收集账号池上下文
-> 大模型判断当前态势
-> 生成决策
-> 保存运行记录和对话
-> 必要时通知人或创建待办
-> 下一轮继续观察
```

人工提问是 Agent 的交互入口之一，但不是唯一入口，也不应该是主运行方式。

## 2. 设计原则

### 2.1 上下文先行

账号池核心数据不应全部依赖模型临时决定调用能力获取。

每轮 Agent 运行时，系统应先组装完整的 `Agent Context Pack`，直接提供给大模型。

这类数据应该优先进入上下文：

- API 账号池状态。
- sub2api 已有缓存。
- 账号池 active / reserve / rate limited 数量。
- 5h 容量、7d 容量、当前剩余额度。
- 最近 24h 峰值、7d 峰值、突发 1h 预估。
- 突发趋势、消耗速度变化。
- 账号探测摘要。
- 401、恢复、重复邮箱、异常事件。
- 最近几轮 Agent 决策。
- 最近人工反馈。
- 当前告警状态。
- 当前对话上下文。

大模型应该基于完整态势做判断，而不是先靠 tools 一步步拼出基础数据。

### 2.2 大模型主导业务决策

补多少号、是否需要告警、下一步优先做什么，应由大模型结合上下文决定。

后端不应使用固定规则直接替代大模型做最终业务结论。

后端可以做：

- 数据收集。
- 上下文整理。
- 安全边界控制。
- 输出格式校验。
- 权限校验。
- 人工确认流程。
- 审计记录。
- 兜底提示。

后端不应该长期作为“补号数量算法”的主决策者。

### 2.3 可调用能力是辅助，不是主体

可调用能力仍然需要存在，但它们的定位是辅助 Agent 完成任务。

可调用能力适合做：

- 查询更细粒度的账号明细。
- 查询历史趋势。
- 查询某个告警批次。
- 生成通知草稿。
- 创建待办。
- 写入 Agent 运行记录。
- 发送已确认的通知。
- 执行人工确认后的动作。

可调用能力不应该承担全部上下文构造职责，也不应该让 Agent 变成“模型决定调用哪个工具”的简单工具调度器。

### 2.4 运行必须可追踪

Agent 的每次运行都必须落库。

如果 Agent 没有运行记录、没有对话记录、没有决策记录，就没有记忆、没有审计、没有复盘，也无法形成持续运营系统。

### 2.5 前端展示必须有缓存

Agent 页面不能只是一次请求一次展示。

前端应该读取后端保存的最新 Agent 状态、最近决策、历史对话和执行记录。刷新页面后，用户仍然能看到最近一次 Agent 的判断和上下文摘要。

### 2.6 目标形态应对齐高级智能体

本项目中的 Agent 目标形态应对齐 Codex 这类高级智能体，而不是简单聊天机器人。

这里的“高级智能体”不是指必须复制 Codex 的具体产品形态，而是指 Agent 应具备类似的工作方式：

- 能理解一个较高层级目标，而不是只回答单轮问题。
- 能自己组织上下文，而不是等待用户把所有数据喂给它。
- 能持续跟踪任务状态，而不是执行一次就结束。
- 能形成计划、执行、观察、修正的循环。
- 能记住历史运行和用户反馈。
- 能把复杂任务拆成多步，但不会把自己降级成 tools 调度器。
- 能在不确定时提出数据缺口和澄清问题。
- 能在安全边界内主动推进下一步。
- 能在执行一轮后根据结果继续判断是否需要下一轮决策，而不是运行一次就结束。

因此后续设计中，Agent 应更像“账号池运营负责人助理”：

```text
它有持续任务
它有上下文记忆
它有运行记录
它能定期自启动
它能根据状态变化提出决策
它能请求人工确认
它能复盘上一次判断是否正确
```

而不是：

```text
用户问一句
-> 调用几个 tools
-> 回答一句
-> 结束
```

高级智能体的关键不在于调用了多少 tools，而在于它是否围绕目标持续感知、判断、记录和推进。

负责人对高级智能体的参考方向是：Agent 接收到命令或被定时唤醒后，不应只执行一遍就结束，而应具备类似 ReAct 的多轮循环能力：

```text
Observe
-> Think
-> Decide
-> Act / Record
-> Observe result
-> Decide whether to continue
```

如果一轮执行后仍有必要继续观察或补充决策，Agent 应能继续进入下一轮，而不是把一次 LLM 调用当作完整 Agent。

### 2.7 技术边界：LangChain 负责 Agent 编排，不负责系统工程

当前阶段采用 LangChain 作为 Agent LLM 编排框架。

技术分工应明确为：

```text
项目后端负责系统工程。
LangChain 负责 Agent 大脑编排。
LLM 负责业务判断。
```

具体边界如下。

FastAPI / MongoDB / 前端负责：

- 系统管理配置。
- 权限控制。
- 数据读取。
- Agent Context Pack 组装。
- Agent 定时 loop。
- Agent 运行记录。
- 对话记录。
- 决策记录。
- 动作记录。
- 钉钉通知。
- 人工确认流程。
- 前端缓存展示。
- 审计记录。

LangChain 负责：

- 调用 LLM。
- 组织 prompt。
- 管理模型输入输出。
- 结构化决策输出。
- ReAct 风格的多轮推理。
- 必要时编排少量可调用能力。

LLM 负责：

- 判断是否需要补号。
- 判断建议补多少号。
- 判断是否需要告警。
- 判断下一步做什么。
- 判断是否需要人工确认。
- 判断是否需要继续下一轮决策。

因此，不应使用 LangChain 实现数据库、权限、配置页面、前端缓存、定时任务和通知系统。这些属于现有项目的系统工程能力。

后续如果多轮 loop、状态流转、可恢复执行和分支控制变得复杂，可以在 LangChain 生态内考虑升级到 LangGraph。但当前阶段不把 LangGraph 作为必选项，先用 LangChain 完成 LLM 调用、Prompt 编排、结构化输出和 ReAct 风格决策链路。

### 2.8 当前代码结构约定

主项目已经重构后端业务代码位置。

当前后端目录约定：

```text
backend/app/modules/*
```

用于承载新的业务实现。

早期文档或历史提交中可能出现 `backend/app/services/*`，当前仓库不把它作为业务目录；新功能不得新增 `app.services` 依赖。

Agent 后续新增代码应优先放在：

```text
backend/app/modules/agent/
```

系统配置、审计等共用能力放在：

```text
backend/app/modules/system/
```

通知能力放在：

```text
backend/app/modules/notifications/
```

路由层仍保留在：

```text
backend/app/routers/
```

因此后续文档和开发中的路径口径应以 `app.modules.*` 为准。

## 3. 总体架构

推荐架构如下：

```text
Agent Scheduler / Manual Chat / Alert Event
        |
        v
Agent Run Manager
        |
        v
Agent Context Builder
        |
        v
LLM Decision Core
        |
        v
Decision Validator / Safety Guard
        |
        v
Agent Memory & Audit Storage
        |
        v
Frontend Cache / Notification / Todo / Human Confirm
```

### 3.1 Agent Scheduler

Agent 应支持自启动循环。

循环可以像 sub2api 缓存刷新、账号探测一样由后端启动：

```text
backend startup
-> start agent_scheduler_loop
-> every N minutes run agent cycle
```

初始建议：

- 默认关闭自动 loop，通过环境变量启用。
- 支持配置执行间隔。
- 支持只对指定站点或指定账号池运行。
- 支持避免并发重复运行。

示例环境变量：

```text
AGENT_LOOP_ENABLED=false
AGENT_LOOP_INTERVAL_SECONDS=900
AGENT_LOOP_SITE_IDS=
AGENT_LOOP_POOL_IDS=
```

### 3.2 Agent Run Manager

Run Manager 负责创建和管理一次 Agent 运行。

每次运行都应该有唯一 `run_id`。

触发来源包括：

- `scheduler`：定时循环。
- `manual_chat`：用户自然语言提问。
- `manual_analyze`：用户点击分析。
- `alert_event`：主系统出现告警事件后触发。
- `retry`：失败后重试。

Run Manager 要记录：

- 运行开始时间。
- 运行结束时间。
- 当前状态。
- 使用的模型。
- 输入上下文版本。
- LLM 输出。
- 决策结果。
- 执行动作。
- 错误信息。

### 3.3 Agent Context Builder

Context Builder 是新架构的核心组件之一。

它负责把数据库、缓存、事件、历史决策、人工反馈整理成一份清晰的上下文包。

大模型每轮优先读取这份上下文，而不是自己临时决定基础数据怎么查。

### 3.4 LLM Decision Core

LLM Decision Core 是业务判断中心。

它负责判断：

- 当前账号池是否健康。
- 是否需要补号。
- 建议补多少号。
- 为什么补这个数量。
- 当前主要风险是什么。
- 是否需要告警。
- 告警发给谁。
- 是否需要人工确认。
- 下一轮应该重点观察什么。
- 是否需要调用额外能力获取补充信息。

### 3.5 Decision Validator / Safety Guard

Validator 不替代模型决策，但必须检查输出是否安全、完整、可执行。

它负责：

- 校验 JSON 结构。
- 校验补号数量是否为合理非负整数。
- 校验风险等级是否在允许枚举内。
- 校验动作是否越权。
- 判断高风险动作是否需要人工确认。
- 阻止删除账号、推号、购买账号等未授权动作直接执行。
- 在模型输出缺失时要求重新生成或进入失败状态。

### 3.6 Agent Memory & Audit Storage

Agent 必须保存记忆。

记忆包括：

- 每次运行记录。
- 每次输入上下文摘要。
- 每次 LLM 输出。
- 每次结构化决策。
- 每次用户对话。
- 每次计划动作和执行结果。
- 人工确认和人工反馈。

这些数据既用于前端展示，也用于下一轮 Agent 判断。

### 3.7 Frontend Cache

前端不应只依赖当前请求返回。

前端应读取：

- 最新 Agent 状态。
- 最近一次 Agent 决策。
- 当前运行是否正在执行。
- 历史对话。
- 历史运行记录。
- 当前待确认动作。

刷新页面后，最近一次结果仍应可见。

### 3.8 LLM Configuration Center

Agent 的 LLM 调用层需要优先完善，并且配置入口应放在项目的系统管理页面。

后续不应把 Agent 调用的 URL、API Key、模型名长期写死在环境变量中。环境变量最多作为本地开发或首次启动兜底，不应该作为正式运营配置主入口。

系统管理页面应提供 Agent LLM 配置模块：

- OpenAI-compatible Base URL。
- API Key。
- Level 1 模型名。
- Level 1 temperature。
- Level 2 模型名。
- Level 2 temperature。
- 请求超时时间。
- 是否启用 Agent。
- 是否启用 Agent loop。
- loop 执行间隔。
- 测试连接按钮。
- 最近一次测试结果。

配置保存后应进入后端数据库配置集合，例如：

```text
system_settings
或
agent_settings
```

推荐结构：

```json
{
  "key": "agent_llm",
  "value": {
    "enabled": true,
    "base_url": "https://...",
    "api_key_encrypted": "...",
    "level1_model": "...",
    "level1_temperature": 0.2,
    "level2_model": "...",
    "level2_temperature": 0.2,
    "timeout_seconds": 60,
    "updated_at": "...",
    "updated_by": "..."
  }
}
```

安全要求：

- API Key 不应明文返回给前端。
- 前端只展示是否已配置、末尾掩码和更新时间。
- 后端保存时应尽量加密或至少进行受控存储。
- 修改配置需要管理员权限。
- 每次修改配置需要记录审计。
- 测试连接不能泄露完整错误中的密钥。

LLM 调用层读取配置的优先级建议：

```text
数据库中的系统管理配置
-> 环境变量兜底
-> 未配置状态
```

这样做的原因：

- 运营人员可以在前端调整 Agent 模型配置。
- 不需要每次改模型都重启服务。
- 可以在系统管理页验证连接是否可用。
- 方便后续多模型、多站点、多策略扩展。

当前阶段暂不设计多套模型配置，例如测试模型和生产模型并存。先保持一套 Agent LLM 配置，后续如负责人需要再扩展为多 profile。

### 3.9 Agent Strategy Configuration

Agent 的运行策略也应放到系统管理页面或账号池配置页面中，由人工配置。

已确认口径：

- Agent loop 间隔由人工在配置页面设置。
- 不同账号池需要支持不同 Agent 策略。
- 是否启用 Agent loop 由配置控制。
- 钉钉通知配置复用负责人已编写的系统管理 / 通知配置。

不同账号池的策略可以包括：

- 是否启用 Agent 自动观察。
- loop 间隔。
- 白天处理策略。
- 夜间处理策略。
- 是否允许夜间由 Agent 自主决定部分动作。
- 是否允许钉钉通知。
- 是否需要人工确认。
- 风险等级到通知通道的映射。
- 是否对该池启用更保守或更激进的补号建议。

策略示例：

```json
{
  "pool_id": "...",
  "agent_enabled": true,
  "loop_interval_seconds": 900,
  "daytime_policy": {
    "mode": "human_confirm_first",
    "notify_channels": ["frontend", "dingtalk"]
  },
  "night_policy": {
    "mode": "agent_can_decide_under_constraints",
    "notify_channels": ["dingtalk"],
    "requires_confirm_for_high_risk": true
  }
}
```

这里的重点是“灵活”，而不是所有高风险动作永远只能人工确认。白天可以更偏人工确认，夜间遇到超并发、严重容量风险或无人值守场景时，可以允许 Agent 在安全边界内更主动地决策。

## 4. Agent Loop

Agent Loop 是系统主流程。

每轮 loop 可以分为 6 步：

```text
1. Wake
2. Observe
3. Think
4. Decide
5. Record
6. Notify / Wait / Request Confirm
```

### 4.1 Wake

Agent 被唤醒。

触发方式：

- 定时器。
- 用户提问。
- 用户点击分析。
- 告警事件。
- 人工要求重新分析。

### 4.2 Observe

系统组装上下文包。

上下文包中包括当前账号池状态、近期消耗、探测摘要、告警事件、历史决策和对话。

### 4.3 Think

大模型阅读上下文，判断当前运营态势。

这一阶段可以要求模型输出简短思考摘要，但不应该把完整 chain-of-thought 展示给用户或落入普通业务字段。

建议保存的是：

- `reasoning_summary`
- `key_observations`
- `uncertainties`
- `data_gaps`

### 4.4 Decide

大模型输出结构化决策。

决策应包含：

- 风险等级。
- 是否需要补号。
- 建议补号数量。
- 是否需要告警。
- 是否需要人工确认。
- 建议动作。
- 判断依据。
- 下一轮观察重点。

### 4.5 Record

后端保存本轮运行。

即使模型失败，也要保存失败记录。

### 4.6 Notify / Wait / Request Confirm

根据决策结果进入不同分支：

- 无风险：仅记录。
- 需要关注：记录并展示。
- 需要告警：发送通知或生成通知草稿。
- 需要人工确认：创建待确认动作。
- 高风险动作：必须等待人工确认。

## 5. Agent Context Pack

`Agent Context Pack` 是每轮 Agent 思考的主要输入。

建议结构：

```json
{
  "run": {
    "trigger": "scheduler",
    "started_at": "...",
    "site_id": "...",
    "pool_id": "..."
  },
  "pool": {},
  "capacity": {},
  "probe": {},
  "alerts": [],
  "recent_usage": {},
  "recent_agent_decisions": [],
  "conversation": [],
  "human_feedback": [],
  "system_constraints": {}
}
```

### 5.1 pool

账号池基础信息：

- pool_id。
- site_id。
- group_id。
- name。
- account_type。
- active 数。
- reserve 数。
- rate limited 数。

### 5.2 capacity

容量信息：

- 5h 容量。
- 5h 已用。
- 5h 剩余。
- 7d 容量。
- 7d 已用。
- 7d 剩余。
- 当前速度可支撑时间。
- 最近 24h 峰值。
- 7d 峰值。
- 突发 1h 预估。
- 突发趋势。

### 5.3 probe

账号探测信息：

- 最近探测时间。
- 探测新鲜度。
- 1h / 24h / 7d 401。
- 恢复数量。
- 重复邮箱。
- 生存时间统计。

### 5.4 alerts

告警信息：

- 最近告警批次。
- 钉钉或其他通道告警。
- 401 告警。
- 大批量封号事件。
- 重复邮箱事件。

### 5.5 recent_agent_decisions

最近几轮 Agent 决策：

- 上次建议补多少。
- 上次风险等级。
- 上次是否告警。
- 上次要求观察什么。
- 上次是否被人工确认。
- 后续实际结果如何。

### 5.6 conversation

用户和 Agent 的近期对话。

用于让 Agent 记住用户刚刚问过什么、人工做过什么判断。

### 5.7 human_feedback

人工反馈。

例如：

- “这次不用补号。”
- “今天有活动，消耗会升高。”
- “这批号质量不好。”
- “某个站点暂时不要告警。”

这些反馈应该进入下一轮上下文。

### 5.8 system_constraints

系统约束。

例如：

- 当前阶段不能自动推号。
- 当前阶段不能删除账号。
- 当前阶段不能购买账号。
- 高风险动作必须人工确认。
- 通知频率限制。

## 6. LLM 决策输出

LLM 输出必须是结构化数据。

建议输出：

```json
{
  "decision_type": "pool_operation_decision",
  "severity": "healthy | watch | warning | danger | critical",
  "summary": "自然语言总结",
  "should_add_accounts": true,
  "suggested_add_count": 12,
  "confidence": "low | medium | high",
  "main_reasons": [],
  "risk_factors": [],
  "data_gaps": [],
  "should_alert": true,
  "alert_channels": ["frontend", "dingtalk"],
  "requires_human_confirm": true,
  "recommended_actions": [],
  "next_observation_focus": [],
  "follow_up_questions": []
}
```

### 6.1 suggested_add_count

`suggested_add_count` 应由大模型根据上下文判断。

后端可以校验：

- 是否为数字。
- 是否小于 0。
- 是否超过安全阈值。
- 是否需要人工确认。

后端不应长期用固定 `target_active` 或固定公式覆盖模型判断。

### 6.2 confidence

模型需要表达置信度。

低置信度时，应优先要求人工确认或补充数据，而不是直接给强动作建议。

### 6.3 data_gaps

如果模型认为数据不足，必须明确指出缺什么。

例如：

- 缺少最近 1h 消耗。
- 探测数据过期。
- 没有最近 Agent 历史。
- 账号池缓存太旧。

## 7. 可调用能力边界

可调用能力不是 Agent 的主体。

在新架构中，可调用能力分为四类。

### 7.1 Context 能力

用于构造上下文。

这些能力主要由后端 Context Builder 调用，不一定由 LLM 自己决定。

例如：

- 读取 API 账号池状态。
- 读取账号探测摘要。
- 读取告警事件。
- 读取最近 Agent 决策。
- 读取对话历史。

### 7.2 Investigation 能力

用于模型发现缺信息后进一步查询。

例如：

- 查询某批 401 账号明细。
- 查询某个账号历史。
- 查询某个时间段消耗趋势。
- 查询某次通知对应的账号池。

### 7.3 Communication 能力

用于沟通和通知。

例如：

- 生成钉钉通知草稿。
- 发送通知。
- 创建前端待办。
- 请求人工确认。

### 7.4 Action 能力

用于执行动作。

这一类风险最高，必须后置。

例如：

- 推号。
- 标记账号。
- 调整池配置。
- 执行补号流程。

当前阶段不应开放自动执行高风险动作。

## 8. 数据库存储设计

Agent 必须持久化运行记录、对话和决策。

建议新增以下集合。

### 8.1 agent_runs

保存每次 Agent 运行。

```json
{
  "_id": "...",
  "run_id": "...",
  "trigger": "scheduler | manual_chat | manual_analyze | alert_event",
  "status": "running | success | failed",
  "site_id": "...",
  "pool_id": "...",
  "started_at": "...",
  "finished_at": "...",
  "model": "...",
  "context_snapshot": {},
  "llm_input": {},
  "llm_output": {},
  "decision_id": "...",
  "error": null
}
```

### 8.2 agent_decisions

保存结构化决策。

```json
{
  "_id": "...",
  "decision_id": "...",
  "run_id": "...",
  "site_id": "...",
  "pool_id": "...",
  "severity": "warning",
  "suggested_add_count": 12,
  "should_alert": true,
  "requires_human_confirm": true,
  "summary": "...",
  "main_reasons": [],
  "risk_factors": [],
  "data_gaps": [],
  "recommended_actions": [],
  "next_observation_focus": [],
  "created_at": "..."
}
```

### 8.3 agent_messages

保存对话。

```json
{
  "_id": "...",
  "conversation_id": "...",
  "run_id": "...",
  "site_id": "...",
  "pool_id": "...",
  "role": "user | assistant | system",
  "content": "...",
  "metadata": {},
  "created_at": "..."
}
```

### 8.4 agent_actions

保存 Agent 计划或执行的动作。

```json
{
  "_id": "...",
  "action_id": "...",
  "run_id": "...",
  "decision_id": "...",
  "action_type": "notify | create_todo | request_human_confirm | execute_confirmed_action",
  "status": "planned | waiting_confirm | executed | skipped | failed",
  "payload": {},
  "result": {},
  "created_at": "...",
  "updated_at": "..."
}
```

### 8.5 agent_feedback

保存人工反馈。

```json
{
  "_id": "...",
  "feedback_id": "...",
  "run_id": "...",
  "decision_id": "...",
  "user_id": "...",
  "feedback_type": "approve | reject | correct | note",
  "content": "...",
  "created_at": "..."
}
```

## 9. 前端缓存与页面形态

Agent 前端不应只是一个临时分析结果页。

建议前端展示以下模块：

### 9.1 当前 Agent 状态

- 是否正在运行。
- 最近一次运行时间。
- 最近一次运行状态。
- 最近一次风险等级。
- 最近一次建议补号数。
- 最近一次是否需要人工确认。

### 9.2 最新决策

展示最近一次 Agent 决策：

- 总结。
- 风险等级。
- 建议补号数量。
- 判断依据。
- 数据缺口。
- 下一轮观察重点。

### 9.3 对话区

用户可以继续追问。

但对话必须落库，刷新页面后仍可看到。

### 9.4 运行历史

展示最近 N 次运行：

- 时间。
- 触发来源。
- 状态。
- 风险等级。
- 建议动作。

### 9.5 待确认动作

如果 Agent 判断需要人工确认，前端应该展示：

- 动作内容。
- 触发原因。
- 风险说明。
- 确认按钮。
- 拒绝按钮。
- 备注输入。

## 10. 人工确认边界

Agent 可以提出建议，也可以在配置允许的范围内执行部分低风险或应急动作。

人工确认边界不应写死为“所有高风险动作永远必须人工确认”，而应支持按时间段、风险等级、账号池策略灵活配置。

需要人工确认的动作包括：

- 推号。
- 删除账号。
- 修改账号池配置。
- 执行批量补号。
- 发送高影响通知。
- 改变告警策略。

默认情况下，Agent 可以自动执行的动作应限制在低风险范围：

- 保存运行记录。
- 保存决策。
- 生成通知草稿。
- 创建待办。
- 前端展示提醒。

已确认：允许 Agent 发送钉钉通知。钉钉通知通道和相关配置复用负责人已经编写的系统管理 / 通知配置。

人工确认策略建议：

```text
白天：优先人工确认，Agent 负责分析、提醒、生成待办和通知。
夜间：如果出现超并发、容量严重不足、批量封号等紧急情况，可以允许 Agent 在配置约束内更主动地决策和通知。
```

即使夜间允许 Agent 更主动，也仍需要安全边界：

- 不允许绕过权限执行未授权动作。
- 不允许删除账号。
- 不允许直接购买账号。
- 不允许执行未实现审计的高风险动作。
- 所有通知、决策和动作必须写入 Agent 运行记录。

## 11. 与现有实现的关系

已有实现可以保留为后续组件来源，但需要重新定位。

### 11.1 可以保留的部分

- LLM 配置。
- LangChain 接入。
- 账号池状态读取逻辑。
- 账号探测摘要读取逻辑。
- Agent 页面入口。
- 前端展示组件。
- 只读安全边界。

### 11.2 需要调整的部分

- 不再让规则引擎主导最终补号数量。
- 不再把点击分析作为 Agent 主流程。
- 不再把基础数据读取全部设计成 LLM 临时 tools 调用。
- 不再让对话和分析结果只存在于前端内存。
- 不再忽略 Agent run、decision、message 的数据库持久化。

### 11.3 需要新增的部分

- Agent Scheduler。
- Agent Context Builder。
- Agent Run Manager。
- Agent Memory 存储。
- Agent Decision 存储。
- Agent Message 存储。
- Agent Action 存储。
- 前端最新状态缓存读取。
- 前端历史决策展示。

## 12. 建设路线

### 阶段一：完善 LLM 调用层和系统管理配置

目标：

- 在系统管理页面增加 Agent LLM 配置。
- 支持配置 Base URL、API Key、Level 1 模型、Level 2 模型、temperature、timeout。
- 支持测试连接。
- 后端优先读取数据库中的 Agent LLM 配置。
- 环境变量仅作为开发或兜底配置。
- 前端不展示完整 API Key。
- 配置修改写入审计记录。
- 使用 LangChain 完成模型调用、Prompt 编排和结构化输出。
- 定时 loop、数据库持久化、权限、通知和前端缓存继续由现有 FastAPI 项目承担。

交付物：

- 系统管理页 Agent LLM 配置模块。
- 后端 Agent LLM settings 读取服务。
- Agent LLM 测试连接接口。
- 配置保存和掩码展示逻辑。
- LangChain LLM 调用适配层。

### 阶段二：统一架构和数据模型

目标：

- 明确 Agent 是持续运行的决策系统。
- 明确 Context Pack 的结构。
- 明确 LLM 决策输出结构。
- 明确数据库集合。
- 明确前端缓存展示要求。

交付物：

- 本总体架构文档。
- 数据结构草案。
- Prompt 草案。
- 接口草案。

### 阶段三：持久化和前端缓存

目标：

- 保存 Agent runs。
- 保存 Agent messages。
- 保存 Agent decisions。
- 前端进入页面可读取最近一次 Agent 状态。
- 用户对话刷新后不丢失。

### 阶段四：Context Pack 和 LLM 主决策

目标：

- 后端组装完整上下文。
- LLM 基于上下文直接输出决策。
- 补号数量由模型输出。
- 后端只做结构和安全校验。

### 阶段五：Agent Loop

目标：

- 增加可配置定时 loop。
- 每隔一段时间自动启动 Agent run。
- 自动保存结果。
- 前端展示最新 loop 状态。

### 阶段六：告警事件接入

目标：

- 主项目出现告警后触发 Agent run。
- Agent 基于告警事件补充分析。
- Agent 决定是否需要通知、创建待办或请求人工确认。

### 阶段七：人工确认后的动作

目标：

- 支持待确认动作。
- 支持人工 approve / reject。
- 支持执行已确认动作。
- 完整记录审计链路。

## 13. 负责人已确认口径

本节记录当前根据负责人意志整理出的架构口径。后续如负责人调整，以最新确认结果为准。

### 13.1 已确认

1. Agent 以定时 loop 为主，人工提问为辅。
2. 补号数量由 LLM 输出，后端只做结构校验、安全校验和审计。
3. Agent 允许发送钉钉通知，通知配置复用系统管理 / 通知中负责人已编写的配置。
4. 人工确认策略需要灵活配置：白天偏人工确认，夜间遇到超并发或紧急风险时，可允许 Agent 在约束内自主决定。
5. 前端不需要展示完整上下文摘要，避免页面过重。
6. Agent 决策需要支持人工纠错，并把纠错结果进入下一轮上下文。
7. Agent loop 间隔由人工在配置页面配置。
8. 不同账号池需要支持不同 Agent 策略。
9. Agent LLM 的 URL、API Key、模型名统一放到系统管理页面配置。
10. API Key 使用现有系统配置存储方式。该系统本身已有管理员权限边界，当前不额外引入复杂密钥管理。
11. Agent LLM 配置修改权限归系统 admin，目前是负责人和开发者。
12. 当前阶段暂不支持多套模型配置，例如测试模型和生产模型。
13. 高级智能体方向参考 Codex / ReAct：Agent 不应运行一遍就结束，而应支持多轮循环决策，执行完一轮后根据结果继续判断是否需要下一轮。

### 13.2 待定

1. 第一阶段是否允许写入 `agent_runs`、`agent_messages`、`agent_decisions` 仍需明确。

   说明：这个问题不是指写账号池业务数据，也不是指执行补号、删号、推号等动作，而是指 Agent 是否可以把自己的运行记录、对话和决策写入数据库。若不允许写入这些集合，Agent 就无法具备记忆、前端缓存、审计和历史复盘能力。

2. 告警事件触发 Agent 后，是否需要立即通知群聊，暂时待定。

3. “像 Codex 一样的高级智能体”的完整能力边界仍需继续收敛。

   当前已确认的最低理解是：多轮循环、持续观察、根据结果继续决策、类似 ReAct 的 Observe / Think / Decide / Act / Observe result 流程。

4. 是否在前端展示 Agent 的计划、执行过程、记忆引用和下一步任务，暂时不确定。

   当前倾向：不在主界面展示完整过程，避免前端冗余；可以后续考虑放到折叠详情、调试面板或运行历史详情页。

## 14. 当前结论

新的 Agent 主线应从“单次分析工具”调整为“持续运行的账号池运营决策系统”。

核心变化是：

```text
从：用户触发 -> 调用能力 -> 规则计算 -> LLM解释 -> 前端临时展示

改为：Agent loop / 用户 / 告警触发
   -> 组装完整上下文
   -> LLM 主导决策
   -> 后端安全校验
   -> 保存运行、对话、决策、动作
   -> 前端缓存展示
   -> 必要时通知或请求人工确认
```

后续开发应先围绕 Context Pack、Agent Memory、Agent Loop 和前端缓存展开，再考虑更复杂的可调用能力和自动动作。

技术落地上，当前口径是：

```text
FastAPI / MongoDB / 前端
负责系统工程、数据、权限、持久化、配置、通知和页面。

LangChain
负责 Agent 的 LLM 调用、Prompt 编排、结构化输出和 ReAct 风格推理。

LLM
负责账号池运营判断，包括补号数量、告警、下一步动作和是否继续下一轮决策。
```

不要把 LangChain 当成系统工程框架，也不要把现有后端写成一个只会调用 LangChain 的薄壳。正确方向是：现有项目提供稳定的工程底座，LangChain 承担 Agent 决策编排。
