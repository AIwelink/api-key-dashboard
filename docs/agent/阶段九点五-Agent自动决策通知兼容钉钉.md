# 阶段九点五：Agent 自动决策通知兼容钉钉

## 1. 目标

阶段九点五不实现“每 2 小时强制生成决策”。本阶段只把现有 Agent loop 已经检测到的事件、巡检结果、到期 task 跟进和复盘结果，通过系统管理里的通知通道发送到钉钉群。

通知配置继续复用：

```text
系统管理 -> 通知 -> 钉钉自定义机器人
```

Agent 不保存钉钉 webhook，不保存加签密钥，不直接调用钉钉接口。

## 2. 通知类型

新增 Agent 通知事件：

```text
agent_loop_decision
```

它表示某次 scheduler 自动触发的 Agent run 已经形成决策摘要，可以通过钉钉发送给运维群。

和阶段七已有的告警草稿派发保持分离：

```text
agent_alert_draft     -> alert_drafted task 的告警草稿派发
agent_loop_decision   -> scheduler 自动决策摘要
```

## 3. 触发范围

默认支持以下自动触发来源：

```text
event_spike
scheduler_task_due
scheduler_review_due
scheduler_patrol
```

不推送：

```text
manual_chat
manual_analyze
memory_daily_summary
memory_weekly_summary
notification_dispatch
```

## 4. 配置

新增 Agent LLM / Scheduler 配置：

```json
{
  "decision_notification_enabled": false,
  "decision_notification_min_severity": "warning",
  "decision_notification_triggers": [
    "event_spike",
    "scheduler_task_due",
    "scheduler_review_due",
    "scheduler_patrol"
  ],
  "decision_notification_cooldown_minutes": 30
}
```

默认关闭，避免上线后刷屏。

## 5. 发送内容

钉钉 markdown 摘要包含：

- 触发来源。
- 账号池。
- 风险等级。
- run_id。
- decision_id。
- task 状态。
- 事件信号。
- 是否建议补号。
- 是否建议告警。
- 是否需要人工确认。
- 核心依据。
- 建议动作。

## 6. 安全边界

本阶段仍然保持：

- 不写账号池业务表。
- 不触发 sub2api 刷新。
- 不启动账号探测。
- 不自动推号、买号、删号。
- 不自动改价格。
- 不绕过 notification 策略保存 webhook。
- 所有发送都写入 `notification_events` / `notification_deliveries`。

## 7. Scheduler 接入

每轮 scheduler tick 在自动处理结束后执行：

```text
process_agent_decision_notifications(...)
```

它扫描本轮 `scheduler_tick_id` 下成功完成、且有 `decision_id` 的自动 run，按配置和冷却策略发送通知。

结果写入：

```text
agent_scheduler_ticks.processed.decision_notifications
```

每个 run 也会记录：

```text
agent_runs.decision_notification
```

## 8. 前端

系统管理 / Agent LLM 增加：

- Decision notifications 开关。
- Decision notify min severity。
- Decision notify cooldown minutes。
- Decision notification triggers。

Agent 工作台 / Notifications 可以看到：

- `agent_alert_draft` 告警草稿通知。
- `agent_loop_decision` 自动决策摘要通知。

