# Agent 运行说明

本文档说明当前 Agent MVP 的实际运行方式、数据流、边界，以及后续向完整 Agent 演进时应坚持的设计原则。

重点原则：

```text
Agent 是账号运营的决策与编排主体。
Agent 可调用能力只是 Agent 的执行手段，不是系统本身。
```

因此后续开发不应把 Agent 简化成一组工具接口的集合，而应让 Agent 根据用户指令或系统事件主动判断目标、选择数据源、调用能力、整合结果并给出决策。

## 1. 当前定位

当前 Agent 处于最小可落地 MVP 阶段。

它的主要职责是：

- 读取 API 账号池状态。
- 读取账号探测摘要。
- 使用规则引擎计算容量风险和补号建议。
- 使用 Level 1 LLM 生成自然语言解释。
- 在前端 Agent 分析页面展示结果。

当前 Agent 明确不做：

- 不写数据库保存分析报告。
- 不修改账号池。
- 不推送账号。
- 不删除账号。
- 不调用 sub2api 写接口。
- 不发送钉钉或 TG 通知。
- 不让 LLM 直接决定补号数量。

当前阶段要保证 Agent 只读、安全、可解释。

## 2. 当前入口

### 2.1 前端入口

页面：

```text
Agent分析
```

当前支持两种操作：

```text
选择账号池后点击“分析”
在输入框里向 Agent 提问
```

用户可以问：

```text
今天这个池要不要补号？
为什么建议补这么多？
如果近期流量上涨，这个池风险大不大？
当前容量还能撑多久？
```

### 2.2 后端接口

当前 Agent 使用的后端接口：

```text
GET  /api/agent/pools
POST /api/agent/pools/{pool_id}/analyze
POST /api/agent/chat
GET  /api/agent/tools
```

其中：

- `/agent/pools`：列出 Agent 可分析的 API 账号池。
- `/agent/pools/{pool_id}/analyze`：对指定账号池做一次只读分析。
- `/agent/chat`：接收用户自然语言问题，并基于当前账号池做分析。
- `/agent/tools`：历史命名接口，当前返回 Agent 可调用能力清单，不代表 Agent 架构要变成工具集合。

## 3. 当前数据流

用户提问后的实际链路：

```text
用户在前端输入问题
  ↓
前端调用 /api/agent/chat
  ↓
后端根据 pool_id 或用户问题确定账号池
  ↓
读取 API账号池状态数据
  ↓
读取账号探测摘要数据
  ↓
规则引擎计算风险等级、补号建议、原因和动作
  ↓
把“用户问题 + 数据摘要 + 规则结果”交给 Level 1 LLM
  ↓
Level 1 LLM 生成自然语言解释
  ↓
前端展示结构化决策和自然语言回答
```

这里有一个关键点：

```text
大模型不是第一步。
```

当前实现是先由后端读取数据并用规则引擎算出确定性结果，再让大模型解释结果。

当前读取的是数据库中已经存在的缓存和探测数据：

```text
不会触发 sub2api 刷新。
不会重新拉取 API 账号池状态。
不会主动启动账号探测。
```

点击“分析”或发送问题时，Agent 只消费主程序已经写入数据库的数据。

这样做的原因：

- 补号数量不能靠模型自由发挥。
- 风险等级必须可复现。
- Agent 早期阶段要降低误判和幻觉风险。
- 后续即使升级成多模型 Agent，关键动作也要有规则或策略约束。

## 4. 数据源边界

### 4.1 API 账号池状态

这是当前 Agent 的核心主数据源。

它用于判断：

- 当前 active 账号数。
- 可用账号数。
- 备用池数量。
- 目标 active 数。
- 最小备用线。
- 5h / 7d 容量。
- 当前使用速度。
- 剩余支撑天数。
- 近期峰值压力。

当前补号建议主要基于这个数据源计算。

### 4.2 账号探测

这是当前 Agent 的辅助风险数据源。

它可用于提示：

- 最近是否出现 401 事件。
- 是否有 401 后恢复。
- 是否存在重复邮箱。
- 探测数据是否新鲜。
- 账号生命周期是否有异常迹象。

注意：

```text
账号探测模块不属于当前 Agent 模块负责范围。
```

因此在主项目账号探测逻辑稳定前，Agent 不应过度依赖探测数据做补号核心决策。当前更适合把它作为风险提示和上下文补充。

### 4.3 Level 1 LLM

Level 1 LLM 当前负责解释，不负责改写规则结果。

它可以：

- 总结当前账号池状态。
- 解释为什么建议补号。
- 把结构化数据转成人可读的话。
- 根据用户问题强调不同风险点。
- 提出需要人工确认的问题。

它不能：

- 修改 `suggested_add_count`。
- 修改 `severity`。
- 覆盖规则引擎给出的补号数量。
- 自行决定推号、删号、买号。
- 直接调用写操作。

## 5. 当前规则引擎职责

规则引擎是当前 MVP 的确定性决策核心。

它负责输出：

```text
severity
headline
suggested_add_count
suggested_push_from_reserve_count
suggested_make_new_count
manual_review_required
reasons
suggested_actions
```

当前设计里：

```text
补多少号由规则引擎计算。
大模型只解释这个结果。
```

这是为了让 Agent 的早期建议可控、可复现、方便排查。

## 6. Agent 可调用能力层

后续系统会有 Agent 可调用能力，但能力不是主体。

正确关系：

```text
用户或系统事件提出目标
  ↓
Agent 理解目标
  ↓
Agent 判断需要哪些信息
  ↓
Agent 调用相应能力
  ↓
Agent 汇总结果
  ↓
Agent 做最终判断
  ↓
Agent 输出建议或发起后续动作
```

不推荐的关系：

```text
用户直接调用能力 A
用户直接调用能力 B
前端把能力结果拼起来
```

也就是说，可调用能力应该藏在 Agent 编排内部。用户面对的是 Agent，不是能力列表。

### 6.1 LangChain 实现规范

代码实现上，Agent 可调用能力按 LangChain 规范使用 `@tool` 装饰器定义。

当前能力层文件：

```text
backend/app/services/agent_capabilities.py
```

当前只读能力：

```text
api_pool_status.get
account_probe.get
refill_decision.calculate
```

这些能力在代码层是 LangChain tool，在产品和架构层统一称为：

```text
Agent 可调用能力
```

### 6.2 能力分级

当前和后续能力分为三类。

只读能力：

```text
读取 API 账号池状态
读取账号探测摘要
读取历史趋势
读取历史 Agent 报告
读取告警批次
读取待办状态
```

刷新能力：

```text
刷新 API 账号池缓存
刷新 dashboard 趋势
触发一次账号探测
```

刷新能力目前不开放给 Agent 自动执行。后续如果开放，也应先由 Agent 提示数据不新鲜，再由用户确认。

执行能力：

```text
发送通知
创建或更新待办
推送备用账号
删除或归档账号
```

执行能力属于后续阶段，必须有权限、审计、幂等和人工确认。

### 6.3 当前点击分析的能力调用

当前点击“分析”后，已经按照 Agent 可调用能力层执行，但仍然是固定只读流程：

```text
Agent 接收分析任务
  ↓
调用 api_pool_status.get 读取现有 API 账号池状态缓存
  ↓
调用 account_probe.get 读取现有账号探测摘要
  ↓
调用 refill_decision.calculate 计算确定性补号建议
  ↓
调用 Level 1 LLM 生成解释
```

这一步的重点是：

```text
Agent 调用的是现成数据读取能力。
不是重新拉取 sub2api。
不是重新执行账号探测。
```

当前能力复用主项目现有只读口径：

```text
api_pool_status.get:
  复用 sub2api_cache.list_cached_groups / get_cache_meta 的缓存读取口径。
  直接消费 sub2api_groups_cache 中已有 capacity_summary。
  不调用 request_debounced_refresh，不触发远端同步。

account_probe.get:
  复用 event_records.event_records_summary / list_event_records 的事件统计口径。
  复用 sub2api_account_probe.list_duplicate_email_alerts 的重复邮箱告警口径。
  不调用 probe_site_accounts，不触发新探测。
```

## 7. 目标 Agent 架构

长期目标采用多 level Agent 架构。

### 7.1 Level 1

Level 1 是决策核心。

职责：

- 理解用户指令。
- 判断当前任务类型。
- 决定需要调用哪些能力。
- 读取 Level 2 或服务返回的数据摘要。
- 综合容量、探测、历史趋势和上下文。
- 做最终运营判断。
- 生成用户可读结论。
- 决定是否需要人工确认、通知或创建待办。

Level 1 不应被设计成简单能力执行器。

### 7.2 Level 2

Level 2 是执行与整理层。

职责：

- 按 Level 1 的计划执行具体能力。
- 获取 API 账号池状态。
- 获取账号探测摘要。
- 整理数据摘要。
- 执行低风险格式转换。
- 后续可负责通知发送、待办创建等受控动作。

Level 2 不做最终业务决策。

### 7.3 可调用能力

后续 Agent 可调用的能力可以包括：

```text
读取 API 账号池状态
读取账号探测数据
读取历史 Agent 报告
读取告警批次
读取待办状态
发送通知
创建或更新待办
生成运营日报
```

这些能力在代码上可以实现为 LangChain `@tool`、service、chain、workflow node 或其他形式，但在产品语义上都只是 Agent 的可调用能力。

## 8. 当前 MVP 与目标架构的差距

当前已经完成：

- Agent 页面入口。
- 账号池选择。
- 用户自然语言输入。
- API 账号池状态读取。
- 账号探测摘要读取。
- 规则引擎补号建议。
- Level 1 LLM 解释。
- LangChain 基础链路。

当前还没有完成：

- Level 1 自主规划调用步骤。
- Level 2 执行层。
- 多轮上下文。
- Agent 长驻后台调度。
- 事件触发分析。
- 钉钉通知下发。
- 分析报告持久化。
- 人工确认后的动作执行。

因此当前系统更准确地说是：

```text
Agent MVP：只读分析 + LLM解释
```

还不是完整的：

```text
自主编排 Agent
```

## 9. 后续开发顺序建议

### 阶段 1：稳固只读 Agent

目标：

- 保持只读。
- 把 API 账号池状态作为主数据源。
- 账号探测只作为辅助风险源。
- 优化前端问答体验。
- 补齐运行说明和边界文档。

当前正在这个阶段。

### 阶段 2：引入 Agent 编排

目标：

- 让 Level 1 先理解用户问题。
- Level 1 决定需要哪些能力。
- Level 2 或服务层执行读取。
- Level 1 汇总并输出。

这一阶段仍然只读。

推荐先支持的问题：

```text
分析当前账号池是否需要补号
解释补号数量来源
对比多个账号池风险
模拟流量上涨后的容量风险
判断数据是否足够新鲜
```

### 阶段 3：事件触发 Agent

目标：

- 捕获容量预警。
- 捕获 401 通知批次。
- 捕获备用池不足。
- 后台自动触发 Agent 分析。
- 把建议发送到前端或钉钉。

这一阶段可以开始写 Agent 报告，但仍不自动执行高风险账号动作。

### 阶段 4：人工确认后的动作

目标：

- Agent 建议推送备用账号。
- 人工确认后执行。
- Agent 创建待办。
- Agent 跟踪待办状态。
- Agent 汇总执行结果。

这一阶段才开始接入写操作，而且必须有权限、审计和回滚策略。

## 10. 当前实现中的重要文件

后端：

```text
backend/app/routers/agent.py
backend/app/services/agent_chat.py
backend/app/services/agent_capabilities.py
backend/app/services/agent_capacity.py
backend/app/services/agent_probe.py
backend/app/services/agent_decision.py
backend/app/services/agent_llm.py
backend/app/services/agent_langchain.py
```

前端：

```text
frontend/src/pages/AgentAnalysisPage.tsx
frontend/styles.css
```

文档：

```text
docs/agent/整体框架需求.md
docs/agent/agent设计框架.md
docs/agent/Agent运行说明.md
```

## 11. 环境变量

当前 Level 1 LLM 使用 OpenAI-compatible 配置：

```text
AGENT_LLM_BASE_URL
AGENT_LLM_API_KEY
AGENT_LEVEL1_MODEL
AGENT_LEVEL1_TEMPERATURE
AGENT_REQUEST_TIMEOUT_SECONDS
```

这些变量只控制模型解释能力。

即使模型未配置，规则引擎仍应能返回基础分析结果。

## 12. 当前安全边界

当前 Agent 必须遵守：

```text
只读数据库
只读 sub2api 缓存
不写账号数据
不执行账号动作
不发送通知
不创建待办
不由 LLM 直接决定高风险操作
```

后续开放写操作时，需要先补：

- 权限模型。
- 操作审计。
- 人工确认流程。
- 幂等策略。
- 失败回滚或补偿策略。
- 通知去重策略。

## 13. 一句话总结

当前 Agent 是一个安全的只读运营分析 MVP。

它现在通过确定性规则算出建议，再让 Level 1 LLM 解释给人看。

后续演进方向不是把它拆成一堆工具，而是让 Agent 成为账号池运营的决策与编排中心，由 Agent 主动调用其他能力完成分析、通知、待办和人工确认后的动作。
