# 阶段七：Agent 自启动 Loop 与任务调度设计

本文承接：

```text
docs/agent/账号池运营Agent总体架构.md
docs/agent/阶段一-Agent-LLM配置与调用层设计.md
docs/agent/阶段二-Agent持久化与前端缓存任务.md
docs/agent/阶段三-Context-Pack与LLM主决策设计.md
docs/agent/阶段四-数据理解与分层事件记忆设计.md
docs/agent/阶段五-Agent控制中枢与多步循环设计.md
docs/agent/阶段六-Agent任务状态闭环设计.md
```

前六个阶段已经完成了 Agent 的“被动智能体”基础：

- LLM 配置与 LangChain 调用层。
- Agent run / message / decision / step / task / memory 持久化。
- Context Pack v2。
- LLM 主决策。
- 分层事件窗口。
- 长期记忆读写基础。
- Intent Router。
- Step Loop。
- `agent_tasks` 状态闭环。
- 手动 task transition / feedback / review 路由。
- 告警草稿能力。

阶段七的目标是让 Agent 从“用户触发后运行”升级为“能自己醒来、自己发现该跟进的任务、自己复盘、自己生成运营记忆”的持续运行系统。

## 1. 阶段目标

阶段七主要解决：

- 自己定时醒来巡检。
- 根据事件突增自动启动。
- 自动发现某个 task 到了 `next_check_at`。
- 自动把 `review_after` 到期的任务推进复盘。
- 自动生成每日 / 每周长期记忆。
- 根据 task 状态持续追踪一个问题直到关闭。
- 和钉钉告警流程打通，但仍保留人工确认策略。

阶段七完成后，Agent 的运行方式应从：

```text
用户点击分析
-> Agent 运行一次
-> 保存结果
```

升级为：

```text
Agent 定时醒来
-> 扫描需要处理的 task / 事件 / 记忆总结
-> 选择要运行的账号池或任务
-> 创建 scheduler run
-> 进入 controller / step loop
-> 更新 task 状态
-> 必要时生成告警草稿或等待人工
-> 到点后继续观察或复盘
-> 风险解除后关闭 task
```

## 2. 不做范围

阶段七仍然不做：

- 自动推号。
- 自动买号。
- 自动删号。
- 自动修改账号池业务配置。
- 自动刷新 sub2api 缓存。
- 自动启动账号探测。
- 绕过人工确认发送高影响通知。
- 把完整 task 看板、run trace、记忆详情全部塞进主页面。

阶段七可以做：

- 后端自启动 Agent scheduler。
- 后端定时扫描 `agent_tasks`。
- 后端事件突增检测并触发 Agent run。
- 自动推进 `next_check_at` 和 `review_after` 到期任务。
- 自动生成长期记忆摘要。
- 在策略允许时发送钉钉通知；策略不允许时只生成待确认草稿。
- 写入 Agent 自己的运行、任务、记忆、通知审计记录。

## 3. 核心原则

### 3.1 Scheduler 是唤醒器，不是业务决策者

Scheduler 只决定：

- 什么时候醒来。
- 哪些 task 到期。
- 哪些事件值得触发 Agent。
- 哪些池需要日常巡检。
- 哪些长期记忆需要生成。

Scheduler 不决定：

- 要不要补号。
- 补多少号。
- 当前风险等级。
- 是否关闭 task。
- 是否需要告警。

这些仍由 LLM 主决策和 task 状态机共同完成。

### 3.2 继续保持只写 Agent 自己的集合

阶段七允许写：

- `agent_runs`
- `agent_messages`
- `agent_decisions`
- `agent_run_steps`
- `agent_tasks`
- `agent_memory_summaries`
- 可选新增的 Agent 调度记录集合

阶段七不允许写：

- `accounts`
- `api_pools`
- `sub2api_*`
- 其他账号池业务集合

阶段七不触发：

- sub2api 刷新。
- 账号探测启动。
- 推号。
- 买号。
- 删除账号。

### 3.3 自动运行必须可停、可控、可审计

自启动 loop 必须满足：

- 可以在系统管理页面关闭。
- 可以限制站点 / 账号池范围。
- 可以限制并发数量。
- 可以限制每轮最多处理多少 task。
- 可以限制每个 task 的最小运行间隔。
- 每次自动触发都写入 `agent_runs.trigger`。
- 每次自动状态变化都写入 `state_history`。
- 每次通知草稿或发送都写入审计。

### 3.4 不让定时任务堆积

Agent 自启动 loop 要避免：

- 上一轮未结束，下一轮重复启动。
- 同一个 task 被多个 worker 同时处理。
- 同一个事件突增重复触发多次。
- LLM 异常导致无限重试。
- 告警通知被重复发送。

因此需要：

- 全局 scheduler 锁。
- task 级运行锁。
- event trigger dedupe key。
- 每轮 max runtime。
- 每轮 max tasks。
- 每类触发 cooldown。

### 3.5 开发前置红线

阶段七正式开发前，需要把以上原则当成硬性红线，而不是普通建议。

每个新增模块、函数、路由和后台任务都必须能回答：

- 它是否只是负责唤醒、调度、记录或通知，而不是替 LLM 做业务判断。
- 它是否只写 Agent 自己的集合，或者只写通知模块自己的发送记录。
- 它是否完全不写账号池业务表。
- 它是否完全不触发 sub2api 刷新。
- 它是否完全不启动账号探测。
- 它是否不会自动推号、买号、删号。
- 它是否可以通过系统管理配置关闭。
- 它是否有并发锁、去重键、cooldown 或任务级锁。
- 它是否会把自动触发原因写入 `agent_runs.trigger`。
- 它是否会把 task 状态变化原因写入 `state_history`。
- 它是否会把告警草稿、告警发送或发送失败写入审计。

如果某个实现无法满足这些问题，应先停下来调整设计，而不是继续开发。

阶段七的正确实现边界是：

```text
Scheduler 负责 Wake / Select / Lock / Dispatch / Record。
Controller + LLM 负责 Observe / Decide。
Task 状态机负责 Validate / Transition。
Notification 模块负责 Send。
Agent 只在策略允许和审计完整时触发通知发送。
```

阶段七绝不能把 Scheduler 写成：

```text
定时脚本
-> 自己算补多少号
-> 自己决定风险等级
-> 自己关闭任务
-> 自己发通知
-> 自己改账号池数据
```

## 4. 总体架构

阶段七新增 Agent Scheduler 层：

```text
Backend Startup
        |
        v
Agent Scheduler Loop
        |
        +--> Periodic Pool Patrol
        +--> Due Task Scanner
        +--> Review Due Scanner
        +--> Event Spike Detector
        +--> Memory Summary Scheduler
        +--> Alert Draft / Notification Dispatcher
        |
        v
Agent Controller
        |
        v
Intent Router / Task State Resolver / Step Loop
        |
        v
Context Pack v2 / LLM Decision / Validator
        |
        v
Persist run / decision / step / task / memory / notification audit
```

阶段七不替换阶段五、阶段六的 controller / step loop / tasks，而是在它们外层加调度器。

### 4.1 当前确认口径

第七阶段总体架构先按本节设计推进。

开发时必须保持以下分层：

- `scheduler.py` 只做启动、唤醒、扫描、加锁、分发和记录。
- `task_scheduler.py` 只负责把到期 task 转成一次 Agent run。
- `event_triggers.py` 只负责判断事件是否值得唤醒 Agent，不做最终风险判断。
- `long_term_memory.py` 只负责长期记忆生成和读取，不替代当前决策。
- `notification_dispatcher.py` 只负责在策略允许时处理告警草稿到通知模块的流转。
- `controller.py` 仍然是 Agent 单次运行入口。
- `step_loop.py` 仍然负责一次 run 内的多步循环。
- `tasks.py` 仍然负责 task 状态机、状态转移校验和 state history。
- `decision_core.py` / LLM 仍然负责业务主决策。
- `decision_validator.py` 仍然负责结构、安全和禁止动作校验。

因此第七阶段不是重构 Agent 大脑，而是给现有 Agent 大脑加上持续唤醒和任务调度层。

## 5. 触发来源

阶段七新增或强化以下 trigger：

```text
scheduler_patrol
scheduler_task_due
scheduler_review_due
event_spike
memory_daily_summary
memory_weekly_summary
notification_dispatch
```

### 5.1 scheduler_patrol

定时巡检账号池。

用途：

- 即使没有用户提问，也周期性检查关键账号池。
- 发现容量下降、事件异常、备用池不足等问题。
- 必要时创建或更新 task。

### 5.2 scheduler_task_due

扫描 `observing` task 的 `next_check_at`。

当：

```text
status=observing
next_check_at <= now
```

Agent 应自动创建一次 run，继续跟进这个持续问题。

### 5.3 scheduler_review_due

扫描 task 的 `review_after`。

当：

```text
review_after <= now
status in observing / waiting_human / alert_drafted / review_due
```

Agent 应把 task 推进 `review_due`，并调用复盘流程。

### 5.4 event_spike

根据事件流自动触发。

典型事件：

- 最近 10 分钟内出现大量 401。
- 最近 1h 出现集中封号。
- 某个池 active 数突然下降。
- 5h limit reached 集中爆发。
- 错误类别突变。
- 突发 1h 消耗趋势显著上涨。

event_spike 不是代替 Context Pack，而是决定“现在该不该唤醒 Agent”。

### 5.5 memory_daily_summary

每日生成 `pool_daily_summary`。

用途：

- 总结某个池当天容量变化。
- 总结当天消耗速度。
- 总结当天掉号和恢复。
- 总结当天最大风险时段。
- 总结当天 Agent 建议和人工反馈。

### 5.6 memory_weekly_summary

每周生成 `pool_weekly_summary` 和可选 `survival_pattern`。

用途：

- 总结账号存活质量。
- 总结固定异常时段。
- 总结高风险事件组合。
- 总结 Agent 建议是否偏保守或偏激进。
- 总结某池近期账号质量是否变差。

### 5.7 notification_dispatch

处理告警草稿到通知流程。

阶段七目标不是无脑自动发送，而是接入通知策略：

```text
alert_drafted task
-> 读取 Agent 通知策略
-> 白天通常等待人工确认
-> 夜间 critical 可按策略允许自动发送
-> 所有发送必须写入通知事件和 Agent 审计
```

### 5.8 Trigger 处理契约

阶段七所有 trigger 都必须统一进入可审计流程。

不同 trigger 的职责如下：

| trigger | 是否创建 agent_run | 是否调用 LLM 主决策 | 是否更新 task | 是否写长期记忆 | 是否允许通知发送 |
| --- | --- | --- | --- | --- | --- |
| `scheduler_patrol` | 是 | 是 | 可能 | 否 | 否 |
| `scheduler_task_due` | 是 | 是 | 是 | 否 | 否 |
| `scheduler_review_due` | 是 | 可选 | 是 | 是，写 `decision_review` | 否 |
| `event_spike` | 是 | 是 | 是 | 否 | 否 |
| `memory_daily_summary` | 可选 | 是 | 否 | 是，写 `pool_daily_summary` | 否 |
| `memory_weekly_summary` | 可选 | 是 | 否 | 是，写 `pool_weekly_summary` / `survival_pattern` | 否 |
| `notification_dispatch` | 是或写 dispatch 记录 | 否 | 是 | 否 | 策略允许时是 |

说明：

- `scheduler_patrol` 是日常巡检，LLM 需要基于 Context Pack 判断是否创建或更新 task。
- `scheduler_task_due` 是持续任务跟进，必须绑定已有 task，并更新 task 状态。
- `scheduler_review_due` 以复盘为主，优先调用 `review_agent_task(...)`；只有复盘认为需要重新判断当前状态时，才进入 LLM 主决策。
- `event_spike` 只表示“值得唤醒 Agent”，不直接判断风险等级和补号数量。
- `memory_daily_summary` / `memory_weekly_summary` 是经验总结，不是当前运营决策。
- `notification_dispatch` 只处理已有 `alert_drafted` task，不重新生成补号建议。

### 5.9 agent_runs.trigger 约定

每次自动触发都必须写入 `agent_runs.trigger`。

建议 run 元数据：

```json
{
  "trigger": "scheduler_task_due",
  "trigger_source": "agent_scheduler",
  "trigger_reason": "observing task next_check_at reached",
  "site_id": "...",
  "pool_id": "...",
  "task_id": "...",
  "event_trigger_id": null,
  "memory_type": null,
  "scheduler_tick_id": "...",
  "auto_started": true
}
```

不同 trigger 的必填字段：

- `scheduler_patrol`：必须有 `site_id` / `pool_id` / `scheduler_tick_id`。
- `scheduler_task_due`：必须有 `task_id` / `pool_id` / `scheduler_tick_id`。
- `scheduler_review_due`：必须有 `task_id` / `current_decision_id` / `scheduler_tick_id`。
- `event_spike`：必须有 `event_trigger_id` / `signal` / `pool_id` / `scheduler_tick_id`。
- `memory_daily_summary`：必须有 `memory_type=pool_daily_summary` / `period_start` / `period_end`。
- `memory_weekly_summary`：必须有 `memory_type=pool_weekly_summary` 或 `survival_pattern` / `period_start` / `period_end`。
- `notification_dispatch`：必须有 `task_id` / `alert_draft.source_decision_id` / `notification_event_id` 或发送失败原因。

### 5.10 Trigger 安全边界

所有 trigger 都必须遵守：

- 不写账号池业务表。
- 不触发 sub2api 刷新。
- 不启动账号探测。
- 不推号、买号、删号。
- 不绕过 LLM 主决策修改补号建议。
- 不绕过 task 状态机关闭 task。
- 不绕过通知策略发送钉钉。

如果某个 trigger 发现数据不足，只能：

- 写入 `data_gaps`。
- 创建或更新 `waiting_human` / `observing` task。
- 设置下一次观察时间。
- 记录失败或跳过原因。

不能为了补齐数据去触发主系统刷新或探测。

### 5.11 当前实现落点

第七阶段第五部分已经落到代码基础层：

```text
backend/app/modules/agent/triggers.py
backend/app/modules/agent/memory.py
backend/app/modules/system/bootstrap.py
```

`triggers.py` 提供：

- `TRIGGER_SCHEDULER_PATROL`
- `TRIGGER_SCHEDULER_TASK_DUE`
- `TRIGGER_SCHEDULER_REVIEW_DUE`
- `TRIGGER_EVENT_SPIKE`
- `TRIGGER_MEMORY_DAILY_SUMMARY`
- `TRIGGER_MEMORY_WEEKLY_SUMMARY`
- `TRIGGER_NOTIFICATION_DISPATCH`
- `TRIGGER_CONTRACTS`
- `TRIGGER_SAFETY_BOUNDARY`
- `build_trigger_metadata(...)`
- `validate_trigger_metadata(...)`
- `is_scheduler_trigger(...)`

当前 trigger 契约会记录：

- 是否创建 `agent_run`。
- 是否调用 LLM 主决策。
- 是否更新 task。
- 是否写长期记忆。
- 是否允许通知发送。
- 是否需要审计。
- 安全边界。
- 必填 metadata。
- 二选一 metadata，例如 `notification_dispatch` 的 `notification_event_id` 或 `dispatch_error`。

`memory.py` 中的 `create_agent_run(...)` 已接入 trigger metadata：

- 每次 run 会保存标准化后的 `trigger`。
- 每次 run 会保存 `metadata.trigger_contract`。
- 每次 run 会保存 `trigger_metadata`。
- scheduler trigger 会自动标记 `auto_started=true`。
- 如果自动 trigger 缺少必填 metadata，会写入 `trigger_contract_valid=false` 和 `trigger_contract_missing`，便于后续 scheduler 调试和审计。

`bootstrap.py` 已预留 `agent_event_triggers` 索引：

- `dedupe_key unique`
- `site_id + pool_id + created_at desc`
- `signal + created_at desc`
- `status + created_at desc`

当前实现只建立触发来源的代码契约和记录基础，还没有启动 scheduler，也没有触发任何自动运行。

## 6. Scheduler 配置

阶段七建议扩展系统管理 / Agent 配置。

### 6.1 全局配置

建议字段：

```json
{
  "agent_loop_enabled": true,
  "scheduler_interval_seconds": 300,
  "max_tasks_per_tick": 10,
  "max_pool_patrols_per_tick": 5,
  "max_event_triggers_per_tick": 5,
  "max_concurrent_runs": 2,
  "task_cooldown_minutes": 10,
  "event_trigger_cooldown_minutes": 15,
  "daily_memory_enabled": true,
  "weekly_memory_enabled": true,
  "notification_dispatch_enabled": false
}
```

默认建议：

```text
agent_loop_enabled=false
scheduler_interval_seconds=300
max_tasks_per_tick=5
max_pool_patrols_per_tick=3
max_event_triggers_per_tick=3
max_concurrent_runs=1
notification_dispatch_enabled=false
```

初期建议默认关闭 loop，由管理员在系统管理页开启。

### 6.2 池级策略

后续建议支持池级策略：

```json
{
  "pool_id": "...",
  "agent_enabled": true,
  "patrol_enabled": true,
  "patrol_interval_minutes": 30,
  "event_spike_enabled": true,
  "notification_policy": {
    "daytime": "draft_only",
    "night": "critical_auto_send_with_audit",
    "requires_human_confirm_for_add_accounts": true
  }
}
```

阶段七第一版可以先只做全局配置，池级策略作为数据结构预留。

### 6.3 当前实现落点

第七阶段 Scheduler 配置已经复用阶段一的 Agent LLM 配置入口。

后端落点：

```text
backend/app/schemas.py
backend/app/modules/agent/settings.py
backend/app/routers/settings.py
```

配置仍保存到：

```text
agent_llm_settings
```

这样做是为了复用已有系统管理页、权限、敏感字段处理和审计日志，不额外拆出第二套 Agent 配置入口。

当前后端已支持全局 Scheduler 字段：

```json
{
  "agent_loop_enabled": false,
  "scheduler_interval_seconds": 300,
  "max_tasks_per_tick": 5,
  "max_pool_patrols_per_tick": 3,
  "max_event_triggers_per_tick": 3,
  "max_concurrent_runs": 1,
  "task_cooldown_minutes": 10,
  "event_trigger_cooldown_minutes": 15,
  "daily_memory_enabled": true,
  "weekly_memory_enabled": true,
  "notification_dispatch_enabled": false,
  "pool_strategies": []
}
```

兼容字段：

- 旧字段 `loop_enabled` 会和 `agent_loop_enabled` 同步。
- 旧字段 `loop_interval_seconds` 会继续保留，避免前面阶段代码或前端状态直接断裂。
- 新 Scheduler 后续应优先读取 `agent_loop_enabled` 和 `scheduler_interval_seconds`。

新增 helper：

```python
get_agent_scheduler_runtime_settings(...)
```

后续 `scheduler.py` 应通过这个函数读取调度配置。

前端落点：

```text
frontend/src/pages/ApiTokensPage.tsx
frontend/styles.css
```

系统管理页的 `Agent LLM` tab 已扩展为同时配置：

- Agent LLM 调用。
- Agent Scheduler loop 开关。
- Scheduler tick 间隔。
- 每轮 task / patrol / event trigger 上限。
- 最大并发 run 数。
- task cooldown。
- event trigger cooldown。
- 每日 / 每周长期记忆开关。
- notification dispatch 开关。

池级策略当前只做数据结构预留：

```json
{
  "pool_strategies": []
}
```

当前阶段不做池级策略 UI，也不让 Scheduler 自动读取池级策略执行差异化调度。

## 7. Scheduler Loop 流程

每次 tick 建议流程：

```text
1. 读取 Agent 配置
2. 如果 loop disabled，退出
3. 获取 scheduler lock
4. 标记 review_after 到期任务为 review_due
5. 处理 review_due task
6. 处理 next_check_at 到期 task
7. 检测事件突增并触发 event_spike run
8. 执行少量常规巡检 patrol
9. 生成到期每日 / 每周记忆
10. 处理可发送或待确认的告警草稿
11. 写入 scheduler tick 记录
12. 释放 lock
```

伪代码：

```python
async def agent_scheduler_tick(db):
    settings = await read_agent_scheduler_settings(db)
    if not settings.enabled:
        return

    async with acquire_agent_scheduler_lock(db):
        await process_review_due_tasks(db, settings=settings)
        await process_due_observing_tasks(db, settings=settings)
        await process_event_spikes(db, settings=settings)
        await process_pool_patrols(db, settings=settings)
        await process_memory_summaries(db, settings=settings)
        await process_alert_drafts(db, settings=settings)
```

### 7.1 当前实现落点

第七部分 Scheduler Loop 骨架已经落到：

```text
backend/app/modules/agent/scheduler.py
backend/app/modules/system/bootstrap.py
```

已实现函数：

```python
async def run_agent_scheduler_tick(db, *, reason="timer", actor=None) -> dict: ...

async def acquire_agent_scheduler_lock(db, *, ttl_seconds: int, owner: str | None = None) -> dict: ...

async def release_agent_scheduler_lock(db, *, owner: str) -> bool: ...

async def process_mark_review_due_tasks(db, *, settings, actor=None) -> dict: ...

async def process_review_due_tasks(db, *, settings, actor=None) -> dict: ...

async def process_due_observing_tasks(db, *, settings, actor=None) -> dict: ...

async def process_event_spikes(db, *, settings, actor=None) -> dict: ...

async def process_pool_patrols(db, *, settings, actor=None) -> dict: ...

async def process_memory_summaries(db, *, settings, actor=None) -> dict: ...

async def process_alert_drafts(db, *, settings, actor=None) -> dict: ...
```

当前 tick 行为：

1. 读取 `get_agent_scheduler_runtime_settings(...)`。
2. 如果 `agent_loop_enabled=false`，写入一条 `agent_scheduler_ticks.status=skipped`，`skip_reason=agent_loop_disabled`。
3. 如果 loop 启用，尝试获取全局 scheduler lock。
4. 如果锁忙，写入 `status=skipped`，`skip_reason=scheduler_lock_busy`。
5. 获取锁后按顺序执行：
   - `process_mark_review_due_tasks(...)`
   - `process_review_due_tasks(...)`
   - `process_due_observing_tasks(...)`
   - `process_event_spikes(...)`
   - `process_pool_patrols(...)`
   - `process_memory_summaries(...)`
   - `process_alert_drafts(...)`
6. 每个 processor 独立捕获异常，单个 processor 失败不会阻断后续 processor。
7. tick 完成后写入 `agent_scheduler_ticks`。
8. 最后释放 scheduler lock。

当前已接真实逻辑：

- `process_mark_review_due_tasks(...)` 会复用阶段六的 `mark_due_agent_tasks_review_due(...)`，把 `review_after <= now` 的任务推进 `review_due`。
- `process_review_due_tasks(...)` 会读取 `status=review_due` 的 task，并调用 `review_agent_task(...)` 完成复盘闭环。

当前仍是安全 no-op，后续章节逐步补齐：

- `process_due_observing_tasks(...)`
- `process_event_spikes(...)`
- `process_pool_patrols(...)`
- `process_memory_summaries(...)`
- `process_alert_drafts(...)`

这些 no-op 会返回：

```json
{
  "ok": true,
  "implemented": false,
  "reason": "stage7_xxx_processor_pending"
}
```

这样做是为了先把 Scheduler Loop 的可停、可控、可审计骨架跑通，同时不提前误触发巡检、事件分析、长期记忆或告警发送。

`bootstrap.py` 已新增索引：

```text
agent_scheduler_ticks: started_at desc
agent_scheduler_ticks: status + started_at desc
agent_scheduler_ticks: reason + started_at desc
agent_scheduler_locks: expires_at
```

## 8. Task 调度

### 8.1 observing task 到期

查询条件：

```json
{
  "owner_scope": "agent",
  "status": "observing",
  "next_check_at": {"$lte": now},
  "scheduler_lock": {"$exists": false}
}
```

处理流程：

```text
锁定 task
-> 创建 run(trigger=scheduler_task_due)
-> 调用 controller
-> 构建 Context Pack v2
-> LLM 判断当前是否继续观察 / 等待人工 / 告警 / 关闭
-> 更新 task
-> 释放锁
```

### 8.2 review_after 到期

阶段六已有服务函数：

```python
mark_due_agent_tasks_review_due(...)
review_agent_task(...)
```

阶段七 scheduler 应复用它们：

```text
mark due task -> review_due
-> review_agent_task(...)
-> 写入 decision_review memory
-> 根据复盘结果 closed 或 observing / waiting_human
```

### 8.3 waiting_human task

`waiting_human` 不应被 scheduler 反复催促 LLM 决策。

可以做：

- 如果等待时间过长，生成提醒草稿。
- 如果关联风险仍在，保持等待人工。
- 如果人工反馈已处理，转入 `review_due` 或 `observing`。

不应该做：

- 自动假设人工同意。
- 自动关闭。
- 自动执行补号。

### 8.4 alert_drafted task

`alert_drafted` 的调度逻辑：

```text
读取通知策略
-> 如果策略要求人工确认，保持 alert_drafted / waiting_human
-> 如果策略允许自动发送，调用 notification dispatcher
-> 发送成功后更新 task.alert_status=sent
-> 设置 review_after
```

阶段七第一版建议：

- 默认 `draft_only`。
- 只有明确配置允许时，才自动发送 critical 夜间告警。

### 8.5 closed / failed task

Scheduler 默认不处理：

- `closed`
- `failed`

除非后续有手动重开或故障恢复入口。

### 8.6 当前实现落点

第八部分 Task 调度已经落到：

```text
backend/app/modules/agent/scheduler.py
backend/app/modules/agent/controller.py
backend/app/modules/agent/intent_router.py
backend/app/modules/system/bootstrap.py
```

#### observing task 到期

`process_due_observing_tasks(...)` 已实现真实调度。

查询范围：

```json
{
  "owner_scope": "agent",
  "status": "observing",
  "next_check_at": {"$lte": "now"},
  "scheduler_lock": "not_exists_or_expired"
}
```

处理流程：

```text
查询到期 observing task
-> 尝试写入 task.scheduler_lock
-> 调用 run_agent_controller(trigger=scheduler_task_due)
-> controller 构建 Context Pack v2
-> LLM 输出当前决策
-> task 状态机更新 task
-> 释放 task.scheduler_lock
-> 写入 scheduler tick processed.due_observing_tasks
```

本轮实现新增：

- `acquire_agent_task_scheduler_lock(...)`
- `release_agent_task_scheduler_lock(...)`
- `process_due_observing_tasks(...)`

controller 已支持 scheduler 传入：

- `task_id`
- `metadata`

这样 scheduler 可以明确绑定某个持续任务，而不是只按 pool 模糊匹配 task。

`intent_router.py` 已把以下 trigger 直接路由为 `pool_operation_decision`：

- `scheduler_patrol`
- `scheduler_task_due`
- `event_spike`

#### review_after 到期

第七部分已经接入：

- `mark_due_agent_tasks_review_due(...)`
- `review_agent_task(...)`

第八部分补齐了 `scheduler_review_due` 的 run 审计：

```text
review_due task
-> create_agent_run(trigger=scheduler_review_due)
-> review_agent_task(...)
-> finish_agent_run(...)
```

如果复盘失败：

```text
fail_agent_run(...)
```

这样复盘不再只是 task 内部状态变化，也会形成一条可审计的 Agent run。

#### waiting_human task

当前 Scheduler 不会自动重新决策 `waiting_human` task。

阶段七当前实现保持：

- 不自动假设人工同意。
- 不自动关闭。
- 不自动执行补号。

后续可以在通知或 task 看板阶段增加“等待过久提醒草稿”。

#### alert_drafted task

当前 Scheduler 不会自动发送告警。

告警草稿处理已由 `notification_dispatcher.py` 接入，默认仍保持 `draft_only` 和人工确认策略。

当前默认：

- `notification_dispatch_enabled=false`
- 不调用钉钉发送接口。
- 不改变 `alert_drafted` task 为 sent。

#### closed / failed task

当前 Scheduler 查询条件不包含：

- `closed`
- `failed`

因此不会自动处理已关闭或失败任务。

## 9. 事件突增触发设计

阶段四已有分层事件窗口：

```text
detail_24h
summary_1h
summary_6h
summary_24h
summary_7d
notable_patterns
```

阶段七 event_spike detector 可以复用事件窗口能力，但它只做触发判断。

### 9.1 触发信号

建议第一版支持：

```text
401_burst
ban_burst
limit_burst
capacity_drop
error_category_shift
burst_usage_rising
```

### 9.2 触发阈值

初始阈值建议保守：

```text
最近 10 分钟同池 401 >= 5
最近 30 分钟同池 401 >= 10
最近 1h 同池封禁 / disabled >= 5
最近 1h 5h limit reached >= 10
最近 1h active 下降 >= 20%
突发趋势 rising 且 strength in strong / extreme
```

这些阈值只是唤醒条件，不是补号算法。

### 9.3 去重

每个事件触发必须有 dedupe key：

```text
agent_event_spike:{site_id}:{pool_id}:{signal}:{time_bucket}
```

同一个 dedupe key 在 cooldown 内只能触发一次。

建议写入：

```json
{
  "trigger_id": "...",
  "trigger_type": "event_spike",
  "site_id": "...",
  "pool_id": "...",
  "signal": "401_burst",
  "dedupe_key": "...",
  "status": "processed",
  "run_id": "...",
  "created_at": "..."
}
```

### 9.4 触发后的 Agent run

event_spike 触发 run 时：

```text
trigger=event_spike
user_message=null
pool_id=触发事件所属池
conversation_id=null 或 task conversation
```

Context Pack v2 必须包含事件窗口，让 LLM 自己判断这是：

- 短时集中封号。
- 持续恶化。
- 正常批量任务。
- 数据不足。
- 需要人工确认。
- 需要告警。

### 9.5 当前实现落点

第九部分事件突增触发已经落到：

```text
backend/app/modules/agent/event_triggers.py
backend/app/modules/agent/scheduler.py
backend/app/modules/system/bootstrap.py
```

`event_triggers.py` 已支持第一版信号：

- `401_burst`
- `ban_burst`
- `limit_burst`
- `capacity_drop`
- `error_category_shift`
- `burst_usage_rising`

检测数据来源：

- `read_agent_event_windows(...)`
- `read_pool_capacity(...)`
- `list_agent_pools(...)`

这些数据都来自已有缓存和事件记录，不刷新 sub2api，不启动账号探测。

当前阈值实现：

- 最近 10 分钟同池 401 >= 5。
- 最近 30 分钟同池 401 >= 10。
- 最近 1h 同池 ban / disabled / invalid / remote_removed / missing_suspected >= 5。
- 最近 1h limit / quota / rate limit / 429 类事件 >= 10。
- 最近 1h 事件推导的掉量数量 >= 5，且相对当前 active + 掉量数量达到 20%。
- 最近 1h 某个错误类别占比 >= 60%，且数量 >= 5。
- 突发趋势为 `rising`，且 `burst_1h_trend_strength` 为 `strong` 或 `extreme`。

去重实现：

```text
agent_event_spike:{site_id}:{pool_id}:{signal}:{time_bucket}
```

`time_bucket` 按 `event_trigger_cooldown_minutes` 切分。

写入集合：

```text
agent_event_triggers
```

触发记录字段包括：

- `trigger_id`
- `trigger_type=event_spike`
- `site_id`
- `pool_id`
- `signal`
- `dedupe_key`
- `status`
- `evidence`
- `scheduler_tick_id`
- `run_id`
- `error`
- `created_at`
- `updated_at`

`scheduler.py` 中的 `process_event_spikes(...)` 已接入：

```text
detect_agent_event_spikes(...)
-> 对新建 trigger 调用 run_agent_controller(trigger=event_spike)
-> 成功后更新 agent_event_triggers.status=processed，并写入 run_id
-> 失败后更新 status=failed 和 error
```

安全边界：

- event_spike 只决定是否唤醒 Agent。
- event_spike 不计算补号数量。
- event_spike 不判定最终风险等级。
- event_spike 不关闭 task。
- event_spike 不发送钉钉。
- event_spike 不修改账号池业务表。

## 10. 长期记忆自动总结

阶段四已经设计长期记忆，阶段七开始让它自动运行。

### 10.1 每日总结

函数建议：

```python
async def generate_pool_daily_memory_summary(
    db,
    *,
    site_id: str | None,
    pool_id: str,
    date: str,
) -> dict:
    ...
```

输入：

- 当天容量快照摘要。
- 当天事件窗口摘要。
- 当天 Agent decisions。
- 当天 task 状态变化。
- 当天人工反馈。
- 当天通知草稿或通知发送记录。

输出写入：

```text
agent_memory_summaries.memory_type = pool_daily_summary
```

### 10.2 每周总结

函数建议：

```python
async def generate_pool_weekly_memory_summary(
    db,
    *,
    site_id: str | None,
    pool_id: str,
    week_start: str,
    week_end: str,
) -> dict:
    ...
```

输出写入：

```text
agent_memory_summaries.memory_type = pool_weekly_summary
```

### 10.3 存活规律总结

如果事件和账号探测数据足够，可以生成：

```text
memory_type = survival_pattern
```

内容包括：

- 新号导入后常见失效窗口。
- 最近一周中位存活时间变化。
- 某批账号是否容易 24h 内失效。
- 某类事件组合是否经常导致后续掉号。

### 10.4 幂等与去重

每日 / 每周总结必须幂等。

建议唯一键：

```text
site_id + pool_id + memory_type + period_start + period_end
```

如果重复生成：

- 可以跳过。
- 或更新同一条 memory summary。

不要每天重复写多条相同总结。

### 10.5 LLM 使用方式

长期记忆总结可以调用 Level 1 模型。

Prompt 重点：

```text
你不是在做当前补号决策。
你是在总结过去一个时间窗口的运营经验。
不要编造事件。
如果数据不足，写明不足。
输出 facts / patterns / lessons / risk_baselines。
```

### 10.6 当前实现落点

本部分已经落到后端自动调度链路：

```text
backend/app/modules/agent/long_term_memory.py
backend/app/modules/agent/scheduler.py
backend/app/modules/system/bootstrap.py
```

`long_term_memory.py` 当前新增或强化：

- `generate_pool_daily_memory_summary(...)`
- `generate_pool_weekly_memory_summary(...)`
- `generate_survival_pattern_summary(...)`
- `process_due_memory_summaries(...)`

每日总结读取：

- 当天 `agent_runs`。
- 当天 `agent_decisions`。
- 当天用户消息中的人工反馈。
- 当天相关 `agent_tasks` 状态变化。
- 分层事件窗口摘要。

每周总结读取：

- 过去完整周的 Agent runs。
- 过去完整周的 decisions。
- 过去完整周的人工反馈。
- 过去完整周的 task 状态变化。
- 事件窗口摘要和存活规律线索。

Scheduler 接入方式：

```text
run_agent_scheduler_tick(...)
-> process_memory_summaries(...)
-> process_due_memory_summaries(...)
-> generate_pool_daily_memory_summary(...)
-> generate_pool_weekly_memory_summary(...)
-> generate_survival_pattern_summary(...)
```

当前自动总结策略：

- 每日总结默认生成“昨天”的完整日窗口。
- 每周总结默认生成“上一个完整自然周”的窗口。
- 每轮最多处理 `max_pool_patrols_per_tick` 个池，避免一次 tick 处理过多。
- `daily_memory_enabled=false` 时跳过每日总结。
- `weekly_memory_enabled=false` 时跳过每周总结和存活规律总结。

幂等策略：

- 自动生成前会按 `site_id + pool_id + memory_type + period_start + period_end` 查询是否已存在。
- 新写入的 memory 使用稳定 `memory_id`。
- 重复 tick 不会持续写入重复长期记忆。
- `bootstrap.py` 已补充对应查询索引，但不强制唯一索引，避免历史重复数据导致初始化失败。

LLM 使用方式：

- 先由后端确定性生成基础摘要。
- 再可选调用 Level 1 模型，把基础摘要整理为更适合长期记忆的 `summary / facts / patterns / lessons / risk_baselines`。
- LLM prompt 明确要求：不是当前补号决策，不要编造事件，数据不足必须说明。
- 如果 LLM 未配置、关闭或调用失败，不阻断总结写入，会回退到确定性摘要，并在 `metadata.llm_error` 中记录原因。

安全边界：

- 只写 `agent_memory_summaries`。
- 只读已有 Agent 运行记录、任务记录、消息、决策和事件窗口。
- 不写账号池业务表。
- 不触发 sub2api 刷新。
- 不启动账号探测。
- 不推号、买号、删号。

## 11. 告警草稿到钉钉流程

阶段六只生成告警草稿。阶段七开始接入通知流程，但必须保留人工确认策略。

### 11.1 通知配置来源

复用现有模块：

```text
backend/app/modules/notifications/
```

复用系统管理 / 通知页面中的钉钉配置。

Agent 不直接保存钉钉 webhook。

### 11.2 通知策略

建议策略：

```text
daytime:
  draft_only
  manual_confirm_required

night:
  warning/danger -> draft_only
  critical -> configurable_auto_send
```

初始建议：

```text
notification_dispatch_enabled=false
```

上线前先只展示草稿和审计，不自动发送。

### 11.3 自动发送前置条件

只有同时满足以下条件，才允许自动发送：

- 全局 `notification_dispatch_enabled=true`。
- 通知通道已配置且启用。
- task.status=`alert_drafted`。
- task.alert_status=`drafted`。
- decision.should_alert=true。
- severity=`critical` 或策略明确允许。
- 当前时间段策略允许自动发送。
- task 没有 `requires_human_confirm=true` 或策略允许夜间 critical 先通知。
- dedupe key 在 cooldown 内未发送过。

### 11.4 发送后写回

发送成功后只写 Agent 和 notification 相关集合：

```json
{
  "alert_status": "sent",
  "alert_sent_at": "...",
  "alert_notification_event_id": "...",
  "alert_notification_delivery": {},
  "state_history": [...]
}
```

发送失败：

```json
{
  "alert_status": "failed",
  "alert_error": "...",
  "next_check_at": "...",
  "state_history": [...]
}
```

不因为通知失败而让 Agent run 崩溃。

### 11.5 当前实现落点

本部分已经落到后端通知派发链路：

```text
backend/app/modules/agent/notification_dispatcher.py
backend/app/modules/agent/scheduler.py
backend/app/routers/agent.py
backend/app/modules/system/bootstrap.py
```

`notification_dispatcher.py` 职责：

- 只处理已有 `alert_drafted` task。
- 校验 task 是否仍为 `status=alert_drafted`。
- 校验 `alert_status=drafted`。
- 校验 `alert_draft` 存在。
- 校验关联 decision 存在且 `decision.should_alert=true`。
- 读取现有 `notification_channels` 中已启用的钉钉通道。
- 根据策略判断是否允许自动发送。
- 根据 dedupe key 和 cooldown 避免重复发送。
- 调用 `notifications.service.send_notification_event(...)`。
- 成功后写回 `agent_tasks.alert_status=sent`。
- 失败后写回 `agent_tasks.alert_status=failed` 和 `alert_error`。

Scheduler 接入：

```text
run_agent_scheduler_tick(...)
-> process_alert_drafts(...)
-> process_agent_alert_drafts(...)
-> dispatch_agent_alert_draft(...)
```

自动发送仍然默认关闭：

- `notification_dispatch_enabled=false` 时 scheduler 只记录跳过。
- 开启后也只允许符合策略的草稿发送。
- 白天默认 `draft_only`。
- 夜间默认只有 `critical` 且策略允许时才自动发送。
- 如果 task 或草稿仍要求人工确认，默认不自动发送。

手动发送入口：

```text
POST /api/agent/tasks/{task_id}/dispatch-alert
```

该入口表示人工已经确认发送草稿：

- 仍然要求 task 和 alert draft 合法。
- 仍然复用系统管理 / 通知里的钉钉通道。
- 仍然写 `notification_events` / `notification_deliveries`。
- 仍然写 Agent 审计日志。
- 默认仍受 dedupe cooldown 保护，除非请求 `force=true`。

写回字段：

成功：

```json
{
  "alert_status": "sent",
  "alert_sent_at": "...",
  "alert_notification_event_id": "...",
  "alert_notification_delivery": {},
  "state_history": []
}
```

失败：

```json
{
  "alert_status": "failed",
  "alert_error": "...",
  "alert_notification_delivery": {},
  "next_check_at": "...",
  "state_history": []
}
```

安全边界：

- Agent 不保存钉钉 webhook。
- Agent 不直接调用钉钉 webhook。
- Agent 只调用现有通知模块。
- 不写账号池业务表。
- 不触发 sub2api 刷新。
- 不启动账号探测。
- 不推号、买号、删号。

## 12. 后端模块设计

### 12.1 新增 scheduler.py

文件：

```text
backend/app/modules/agent/scheduler.py
```

职责：

- 读取 Agent loop 配置。
- 获取 scheduler lock。
- 每个 tick 调用不同 processor。
- 控制并发、超时和限流。
- 写入 scheduler tick 记录。

建议函数：

```python
async def start_agent_scheduler(app) -> None: ...

async def stop_agent_scheduler(app) -> None: ...

async def run_agent_scheduler_tick(db, *, reason: str = "timer") -> dict: ...

async def acquire_agent_scheduler_lock(db, *, ttl_seconds: int) -> bool: ...
```

### 12.2 新增 task_scheduler.py

文件：

```text
backend/app/modules/agent/task_scheduler.py
```

职责：

- 查找 `next_check_at` 到期 task。
- 查找 `review_after` 到期 task。
- 锁定单个 task。
- 创建 scheduler run。
- 调用 controller。
- 释放 task lock。

建议函数：

```python
async def process_due_observing_tasks(db, *, settings: dict) -> dict: ...

async def process_due_review_tasks(db, *, settings: dict) -> dict: ...

async def run_task_followup(db, *, task: dict, trigger: str) -> dict: ...
```

### 12.3 新增 event_triggers.py

文件：

```text
backend/app/modules/agent/event_triggers.py
```

职责：

- 读取近期事件摘要。
- 判断是否达到 event_spike 唤醒条件。
- 写入 trigger dedupe 记录。
- 触发 Agent run。

建议函数：

```python
async def detect_agent_event_spikes(db, *, settings: dict) -> list[dict]: ...

async def process_event_spikes(db, *, settings: dict) -> dict: ...
```

### 12.4 扩展 long_term_memory.py

文件：

```text
backend/app/modules/agent/long_term_memory.py
```

新增或强化：

```python
async def generate_pool_daily_memory_summary(...): ...

async def generate_pool_weekly_memory_summary(...): ...

async def generate_survival_pattern_summary(...): ...

async def process_due_memory_summaries(db, *, settings: dict) -> dict: ...
```

### 12.5 新增 notification_dispatcher.py

文件：

```text
backend/app/modules/agent/notification_dispatcher.py
```

职责：

- 读取 `alert_drafted` task。
- 根据 Agent 通知策略判断是否可发送。
- 调用 `app.modules.notifications.service`。
- 写回 task 的 alert 状态。
- 写审计。

建议函数：

```python
async def process_agent_alert_drafts(db, *, settings: dict) -> dict: ...

async def dispatch_agent_alert_draft(db, *, task_id: str, actor: dict | None = None) -> dict: ...
```

### 12.6 修改 controller.py

文件：

```text
backend/app/modules/agent/controller.py
```

修改目标：

- 支持 scheduler trigger。
- 支持 task follow-up 入口。
- run 创建时写入 `trigger=scheduler_task_due / event_spike / scheduler_patrol`。
- 对 scheduler 运行不写用户消息。
- 仍然保存 assistant summary、decision 和 task 更新。

### 12.7 修改 app startup

需要在 FastAPI startup 中按配置启动 scheduler。

建议：

```text
app.state.agent_scheduler_task = asyncio.create_task(...)
```

注意：

- reload / 多进程场景要避免重复启动。
- 开发环境 uvicorn reload 可能启动两个进程，需要锁兜底。
- scheduler loop 异常不能导致主服务退出。

### 12.8 当前实现落点

第十二部分已经按模块边界落地：

```text
backend/app/modules/agent/scheduler.py
backend/app/modules/agent/task_scheduler.py
backend/app/modules/agent/event_triggers.py
backend/app/modules/agent/long_term_memory.py
backend/app/modules/agent/notification_dispatcher.py
backend/app/modules/agent/controller.py
backend/app/main.py
```

`scheduler.py` 当前职责：

- `start_agent_scheduler(app)`：在 FastAPI lifespan 中启动后台 scheduler loop。
- `stop_agent_scheduler(app)`：应用停止时取消后台 scheduler loop。
- `_agent_scheduler_loop(app)`：周期性读取 Agent scheduler 配置。
- `run_agent_scheduler_tick(...)`：执行单次 tick。
- `acquire_agent_scheduler_lock(...)` / `release_agent_scheduler_lock(...)`：全局 scheduler 锁。
- 每个 processor 独立捕获异常，单个 processor 失败不会让整个 tick 崩溃。
- 每次 tick 写入 `agent_scheduler_ticks`。
- 使用 `asyncio.wait_for(...)` 对单次 tick 做最大运行时间保护。

`task_scheduler.py` 当前职责：

- `process_mark_review_due_tasks(...)`：把到期 task 标记为 `review_due`。
- `process_due_review_tasks(...)`：处理 `review_due` task，并调用复盘流程。
- `process_due_observing_tasks(...)`：处理 `next_check_at` 到期的 observing task。
- `run_task_followup(...)`：锁定单个 task，创建 scheduler run，调用 controller。
- `acquire_agent_task_scheduler_lock(...)` / `release_agent_task_scheduler_lock(...)`：task 级运行锁。

`event_triggers.py` 当前职责：

- 读取事件窗口和容量缓存。
- 判断是否达到 `event_spike` 唤醒条件。
- 写入 `agent_event_triggers` 去重记录。
- 对新触发信号调用 `run_agent_controller(trigger=event_spike)`。

`long_term_memory.py` 当前职责：

- 生成每日长期记忆。
- 生成每周长期记忆。
- 生成存活规律长期记忆。
- `process_due_memory_summaries(...)` 供 scheduler tick 调用。
- 写入 `agent_memory_summaries`，并保持幂等。

`notification_dispatcher.py` 当前职责：

- 读取 `alert_drafted` task。
- 根据通知策略判断是否可发送。
- 调用现有 `app.modules.notifications.service`。
- 写回 task 的 alert 状态。
- 手动发送入口由路由层写审计。

`controller.py` 当前已支持：

- scheduler trigger。
- `task_id` follow-up 入口。
- scheduler 运行不写用户消息。
- 仍然保存 assistant summary、decision 和 task 更新。

`main.py` 当前已接入：

- lifespan 启动时设置 `app.state.agent_scheduler_db`。
- 调用 `start_agent_scheduler(app)`。
- shutdown 时调用 `stop_agent_scheduler(app)`。
- 默认配置关闭时 loop 只轮询配置，不执行 Agent tick。
- reload / 多进程场景下仍依赖数据库全局 scheduler lock 兜底。

安全边界保持不变：

- Scheduler 只唤醒、扫描、加锁、分发和记录。
- Task scheduler 只把到期 task 转为 Agent run。
- Event trigger 只决定是否唤醒 Agent。
- Notification dispatcher 只处理已有告警草稿。
- 不写账号池业务表。
- 不触发 sub2api 刷新。
- 不启动账号探测。
- 不推号、买号、删号。

## 13. 数据库设计

阶段七建议新增两个 Agent 独立集合。

### 13.1 agent_scheduler_ticks

保存每次调度 tick：

```json
{
  "_id": "...",
  "tick_id": "...",
  "status": "success | failed | skipped",
  "reason": "timer | manual",
  "started_at": "...",
  "finished_at": "...",
  "duration_ms": 1234,
  "processed": {
    "due_tasks": 2,
    "review_tasks": 1,
    "event_spikes": 1,
    "pool_patrols": 3,
    "memory_summaries": 2,
    "alert_drafts": 0
  },
  "errors": []
}
```

索引：

```text
started_at desc
status + started_at desc
reason + started_at desc
```

### 13.2 agent_event_triggers

保存事件突增触发记录：

```json
{
  "_id": "...",
  "trigger_id": "...",
  "trigger_type": "event_spike",
  "signal": "401_burst",
  "site_id": "...",
  "pool_id": "...",
  "dedupe_key": "...",
  "window_start": "...",
  "window_end": "...",
  "evidence": {},
  "status": "created | processed | skipped | failed",
  "run_id": "...",
  "created_at": "..."
}
```

索引：

```text
dedupe_key unique
site_id + pool_id + created_at desc
signal + created_at desc
status + created_at desc
```

### 13.3 继续只写 Agent 集合

阶段七允许写：

- `agent_scheduler_ticks`
- `agent_event_triggers`
- `agent_tasks`
- `agent_runs`
- `agent_messages`
- `agent_decisions`
- `agent_run_steps`
- `agent_memory_summaries`

通知发送时可以写通知模块自己的集合：

- `notification_events`
- `notification_deliveries`

仍然不写账号池业务表。

### 13.4 当前实现落点

本部分已经落到：

```text
backend/app/modules/system/bootstrap.py
backend/app/modules/agent/scheduler.py
backend/app/modules/agent/event_triggers.py
backend/app/modules/agent/notification_dispatcher.py
```

`agent_scheduler_ticks` 当前写入位置：

```text
backend/app/modules/agent/scheduler.py
```

当前字段包括：

```json
{
  "_id": "...",
  "schema_version": "agent_scheduler_tick.v1",
  "tick_id": "...",
  "reason": "timer | manual",
  "status": "success | partial | failed | skipped",
  "skip_reason": "...",
  "started_at": "...",
  "finished_at": "...",
  "duration_ms": 1234,
  "settings": {},
  "processed": {},
  "errors": [],
  "created_by": "...",
  "created_at": "...",
  "updated_at": "..."
}
```

当前索引已在 `bootstrap.py` 中创建：

```text
started_at desc
status + started_at desc
reason + started_at desc
```

`agent_event_triggers` 当前写入位置：

```text
backend/app/modules/agent/event_triggers.py
```

当前字段包括：

```json
{
  "_id": "...",
  "trigger_id": "...",
  "trigger_type": "event_spike",
  "site_id": "...",
  "pool_id": "...",
  "signal": "401_burst",
  "dedupe_key": "...",
  "status": "created | processed | failed",
  "evidence": {},
  "scheduler_tick_id": "...",
  "run_id": "...",
  "error": null,
  "created_at": "...",
  "updated_at": "..."
}
```

当前索引已在 `bootstrap.py` 中创建：

```text
dedupe_key unique
site_id + pool_id + created_at desc
signal + created_at desc
status + created_at desc
```

另外，当前实现为了避免多进程 / reload / 多 worker 重复执行 scheduler，还使用了一个 Agent 自己的锁集合：

```text
agent_scheduler_locks
```

当前字段包括：

```json
{
  "_id": "agent_scheduler_loop",
  "owner": "...",
  "locked_at": "...",
  "expires_at": "...",
  "updated_at": "..."
}
```

对应索引：

```text
expires_at asc
```

`agent_scheduler_locks` 只用于调度互斥，不保存业务判断，不写账号池业务表。

通知发送仍然只写通知模块自己的集合：

```text
notification_events
notification_deliveries
```

Agent 侧只在 `agent_tasks` 中写回：

```text
alert_status
alert_sent_at
alert_notification_event_id
alert_notification_delivery
alert_error
state_history
```

阶段七仍然不写：

```text
accounts
api_pools
sub2api_*
其他账号池业务集合
```

## 14. 路由设计

阶段七建议新增管理路由：

```text
GET  /api/agent/scheduler/status
POST /api/agent/scheduler/tick
POST /api/agent/scheduler/pause
POST /api/agent/scheduler/resume
GET  /api/agent/scheduler/ticks

POST /api/agent/tasks/{task_id}/run-followup
POST /api/agent/tasks/{task_id}/dispatch-alert

POST /api/agent/memory/daily
POST /api/agent/memory/weekly
```

### 14.1 scheduler/status

返回：

- loop 是否启用。
- 最近一次 tick。
- 是否正在运行。
- 当前锁状态。
- 最近错误。

### 14.2 scheduler/tick

手动触发一次 scheduler tick。

用途：

- 本地测试。
- 管理员手动巡检。
- 验证任务调度。

### 14.3 tasks/{task_id}/run-followup

手动对某个 task 执行一次 follow-up run。

用于调试 `next_check_at` 逻辑。

### 14.4 tasks/{task_id}/dispatch-alert

手动发送某个 task 的告警草稿。

要求：

- task 必须有 `alert_draft`。
- 权限必须是 admin / maintainer。
- 发送后写审计。

### 14.5 memory/daily 和 memory/weekly

手动生成指定池的每日 / 每周总结。

用于上线前验证长期记忆质量。

### 14.6 当前实现落点

本部分已经落到：

```text
backend/app/routers/agent.py
backend/app/modules/agent/scheduler.py
backend/app/modules/agent/task_scheduler.py
backend/app/modules/agent/long_term_memory.py
backend/app/modules/agent/notification_dispatcher.py
```

当前已实现路由：

```text
GET  /api/agent/scheduler/status
GET  /api/agent/scheduler/ticks
POST /api/agent/scheduler/tick
POST /api/agent/scheduler/pause
POST /api/agent/scheduler/resume

POST /api/agent/tasks/{task_id}/run-followup
POST /api/agent/tasks/{task_id}/dispatch-alert

POST /api/agent/memory/daily
POST /api/agent/memory/weekly
```

`GET /api/agent/scheduler/status` 返回：

- `enabled`：Agent loop 是否启用。
- `settings`：当前 scheduler 配置快照。
- `running`：当前全局 scheduler lock 是否仍有效。
- `lock`：当前锁记录。
- `latest_tick`：最近一次 tick。
- `latest_error_tick`：最近一次 failed / partial tick。

`GET /api/agent/scheduler/ticks` 支持查询参数：

```text
status
reason
limit
```

`POST /api/agent/scheduler/tick`：

- 手动触发一次 scheduler tick。
- `reason=manual`。
- 写入 `agent_scheduler_ticks`。
- 写入审计日志 `agent.scheduler.tick`。

`POST /api/agent/scheduler/pause`：

- 设置 `agent_loop_enabled=false`。
- 同步兼容字段 `loop_enabled=false`。
- 写入审计日志 `agent.scheduler.pause`。

`POST /api/agent/scheduler/resume`：

- 设置 `agent_loop_enabled=true`。
- 同步兼容字段 `loop_enabled=true`。
- 写入审计日志 `agent.scheduler.resume`。

`POST /api/agent/tasks/{task_id}/run-followup` 请求体：

```json
{
  "trigger": "scheduler_task_due",
  "lock_ttl_seconds": 300
}
```

说明：

- 用于手动调试某个 task 的 follow-up。
- 复用 `task_scheduler.run_task_followup(...)`。
- 会获取 task 级 scheduler lock。
- 会创建 Agent run。
- 不写用户消息。
- 写入审计日志 `agent.task.run_followup`。

`POST /api/agent/tasks/{task_id}/dispatch-alert` 请求体：

```json
{
  "force": false
}
```

说明：

- 用于人工确认后发送告警草稿。
- 复用 `notification_dispatcher.dispatch_agent_alert_draft(...)`。
- 仍然复用系统管理 / 通知里的钉钉通道。
- 发送后写 `notification_events` / `notification_deliveries`。
- 写入审计日志 `agent.task.dispatch_alert`。

`POST /api/agent/memory/daily` 请求体：

```json
{
  "site_id": "...",
  "pool_id": "...",
  "date": "2026-07-01"
}
```

`POST /api/agent/memory/weekly` 请求体：

```json
{
  "site_id": "...",
  "pool_id": "...",
  "week_start": "2026-06-24",
  "week_end": "2026-07-01"
}
```

说明：

- 用于手动生成长期记忆摘要。
- 写入 `agent_memory_summaries`。
- 使用稳定 `memory_id` 保持幂等。
- 写入审计日志 `agent.memory.daily` / `agent.memory.weekly`。

权限：

- 当前上述路由均复用 `AGENT_ROLES = owner / admin / maintainer`。
- 后续如果要更严格区分 pause / resume，可再把 scheduler 开关限制为 owner / admin。

## 15. 前端展示原则

阶段七前端可以先不做完整调度控制台，但至少要能看到：

- Agent loop 是否启用。
- 最近一次 scheduler tick 时间。
- 最近一次 tick 是否成功。
- 当前是否有到期 task。
- 当前是否有 waiting_human task。
- 当前是否有 alert_drafted task。
- 最近一次自动触发来源。
- 最近一次自动复盘结果。

主页面可以继续保持简洁，只展示：

- 最新 Agent 决策。
- task 摘要。
- scheduler 简短状态。

完整页面后续再做：

- Agent scheduler 详情页。
- task 看板。
- event trigger 详情页。
- memory summary 历史页。
- notification dispatch 历史页。

### 15.1 当前实现落点

本部分已经落到：

```text
backend/app/modules/agent/scheduler.py
frontend/src/pages/AgentAnalysisPage.tsx
frontend/styles.css
```

`GET /api/agent/scheduler/status` 已扩展返回轻量前端摘要：

```json
{
  "enabled": true,
  "running": false,
  "latest_tick": {},
  "latest_error_tick": {},
  "task_summary": {
    "due_observing_count": 0,
    "due_review_count": 0,
    "waiting_human_count": 0,
    "alert_drafted_count": 0
  },
  "latest_auto_trigger": {},
  "latest_review_result": {}
}
```

主页面当前新增 `Scheduler 状态` 简短面板，展示：

- Agent loop 是否启用。
- 当前 scheduler 是否运行中。
- 最近一次 tick 时间。
- 最近一次 tick 状态。
- 到期 task 数量。
- `waiting_human` task 数量。
- `alert_drafted` task 数量。
- 最近一次自动触发来源。
- 最近一次自动复盘结果。

前端实现原则：

- 不展示完整 tick 列表。
- 不展示完整 task 看板。
- 不展示 event trigger 明细。
- 不展示 memory summary 历史。
- 不在主页面提供复杂调度控制台。

当前主页面仍只保留三块核心信息：

```text
最新 Agent 决策
当前 task 摘要
scheduler 简短状态
```

这样既能看见 Agent 是否已经具备“自己醒来”的能力，又不会把运营主页面重新变得拥挤。

## 16. 安全与权限

### 16.1 权限

建议：

- 查看 scheduler 状态：owner / admin / maintainer。
- 手动触发 tick：admin / maintainer。
- pause / resume scheduler：admin。
- 手动发送告警草稿：admin / maintainer。
- 修改 loop 配置：admin。

### 16.2 安全边界

必须保持：

- 不写账号池业务表。
- 不触发 sub2api 刷新。
- 不启动账号探测。
- 不自动推号、买号、删号。
- 不绕过人工确认策略。
- 通知发送必须使用现有通知模块。
- 所有自动触发、自动复盘、自动通知都必须可审计。

### 16.3 失败处理

LLM 调用失败：

- run 标记 failed。
- task 不应被误关闭。
- 可以设置较短 `next_check_at` 重试，但必须有重试上限。

通知发送失败：

- task.alert_status=`failed`。
- 保存错误。
- 不影响主系统。

scheduler tick 失败：

- 写入 `agent_scheduler_ticks.status=failed`。
- 释放 lock。
- 下一轮可继续运行。

### 16.4 当前实现落点

本部分已经落到：

```text
backend/app/routers/agent.py
backend/app/routers/settings.py
backend/app/modules/agent/scheduler.py
backend/app/modules/agent/task_scheduler.py
backend/app/modules/agent/notification_dispatcher.py
```

当前权限分层：

```text
AGENT_VIEW_ROLES     = owner / admin / maintainer
AGENT_OPERATOR_ROLES = owner / admin / maintainer
AGENT_ADMIN_ROLES    = owner / admin
```

路由权限：

- `GET /api/agent/scheduler/status`：`owner / admin / maintainer`。
- `GET /api/agent/scheduler/ticks`：`owner / admin / maintainer`。
- `POST /api/agent/scheduler/tick`：`owner / admin / maintainer`。
- `POST /api/agent/scheduler/pause`：`owner / admin`。
- `POST /api/agent/scheduler/resume`：`owner / admin`。
- `POST /api/agent/tasks/{task_id}/dispatch-alert`：`owner / admin / maintainer`。
- `PUT /api/settings/agent-llm`：`owner / admin`，用于修改 loop 配置。

说明：

- 文档中的 `admin` 级操作在当前系统里包含 `owner`，因为 `owner` 是最高权限。
- `maintainer` 可以做手动 tick 和人工确认后的告警草稿发送，但不能 pause / resume scheduler，也不能修改 loop 配置。

安全边界当前通过模块边界保持：

- Scheduler 只调用 Agent controller、task scheduler、event trigger、long-term memory 和 notification dispatcher。
- Scheduler 不调用账号池写入接口。
- Scheduler 不调用 sub2api refresh。
- Scheduler 不启动账号探测。
- Agent task 状态机只写 `agent_tasks`。
- 告警发送只通过 `app.modules.notifications.service.send_notification_event(...)`，不直接保存或使用钉钉 webhook。
- 自动 tick、手动 tick、pause / resume、task follow-up、告警发送、长期记忆生成均写审计或调度记录。

失败处理当前实现：

- LLM / controller 失败时，`fail_agent_run(...)` 会把 run 标记为 `failed`。
- `run_task_followup(...)` 捕获单个 task 失败，不让整个 scheduler tick 崩溃。
- task follow-up 失败会写入：
  - `scheduler_failure_count`
  - `last_scheduler_error`
  - `last_scheduler_failed_at`
  - `state_history`
- follow-up 默认最多短重试 3 次。
- 未超过上限时，task 保持 `observing`，并设置新的 `next_check_at`。
- 超过上限时，task 转为 `waiting_human`，并记录需要人工检查，不会误关闭 task。
- follow-up 成功后会清理 `last_scheduler_error`，并重置 `scheduler_failure_count=0`。
- 通知发送失败时，`notification_dispatcher.py` 会设置 `task.alert_status=failed`、保存 `alert_error`，并设置后续观察时间，不影响主系统。
- scheduler tick 失败时，`scheduler.py` 写入 `agent_scheduler_ticks.status=failed`，并在 `finally` 中释放全局 lock。

## 17. 分阶段落地建议

阶段七建议拆成四个小步实施。

### 17.1 第一步：Scheduler 基础框架

完成：

- `scheduler.py`。
- 读取 Agent loop 配置。
- 手动 `POST /api/agent/scheduler/tick`。
- 写入 `agent_scheduler_ticks`。
- 不自动启动。

### 17.2 第二步：Task due 与 review due

完成：

- 自动处理 `next_check_at <= now`。
- 自动处理 `review_after <= now`。
- 调用现有 controller 和 reviewer。
- 继续保持只写 Agent 集合。

这是第七阶段最核心的一步。

### 17.3 第三步：长期记忆自动总结

完成：

- 手动 daily summary。
- 手动 weekly summary。
- scheduler 自动生成到期 summary。
- 幂等写入 `agent_memory_summaries`。

### 17.4 第四步：事件触发与告警调度

完成：

- event_spike detector。
- trigger dedupe。
- alert_drafted task 的通知策略判断。
- 手动 dispatch alert。
- 策略允许后再自动 dispatch。

## 18. 验收标准

### 18.1 自启动巡检

- 开启 loop 后，scheduler 能按配置间隔运行。
- 关闭 loop 后，不会自动运行。
- 每次 tick 写入 `agent_scheduler_ticks`。
- 同一时间不会并发执行多个 scheduler tick。

### 18.2 task due

- `observing.next_check_at <= now` 的 task 会自动触发 follow-up run。
- follow-up run 会写入 `agent_runs.trigger=scheduler_task_due`。
- follow-up 后 task 状态会根据 LLM 和状态机更新。

### 18.3 review due

- `review_after <= now` 的 task 会进入复盘。
- 复盘结果写入 `agent_memory_summaries.memory_type=decision_review`。
- 复盘后 task 能转 `closed` 或继续 `observing / waiting_human`。

### 18.4 event spike

- 同一池事件突增会创建 `agent_event_triggers`。
- dedupe key 生效，不会重复触发。
- event_spike run 的 Context Pack 包含事件窗口。

### 18.5 长期记忆

- 每日总结能写入 `pool_daily_summary`。
- 每周总结能写入 `pool_weekly_summary`。
- 重复执行不会生成重复总结。
- 下一轮 Context Pack 能读取这些总结。

### 18.6 钉钉告警

- 默认不自动发送。
- 人工手动 dispatch 可以发送草稿。
- 策略允许后，critical 夜间告警可以自动发送。
- 发送记录进入通知模块和 Agent task。

### 18.7 安全

- 不写账号池业务表。
- 不触发 sub2api 刷新。
- 不启动账号探测。
- 不自动推号、买号、删号。
- 所有自动动作可审计。

## 19. 阶段七完成后的形态

阶段七完成后，Agent 将从：

```text
有任务状态，但需要人手动触发下一步
```

升级为：

```text
有持续任务
有调度器
能自动醒来
能自动跟进 observing
能自动复盘 review_due
能自动沉淀长期记忆
能根据事件突增启动分析
能把告警草稿纳入通知流程
```

这时 Agent 才开始接近负责人期望的高级智能体形态：

```text
Observe
-> Decide
-> Record
-> Wait / Notify / Ask Human
-> Wake Again
-> Review
-> Learn
-> Close or Continue
```

完成阶段七后，后续阶段可以继续推进：

```text
阶段八：完整 Agent Task 看板与调试页面
阶段九：更精细的通知策略与人工确认工作流
阶段十：评测集、回归测试与 Agent 决策质量评估
```
