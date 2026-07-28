# 容量状态变化飞书通知设计

## 目标

API 账号池分组的容量健康状态发生变化时，确保所有启用的飞书机器人立即收到一条通知。状态未变化时不发送状态变化通知。现有容量阈值告警、危险状态冷却重复提醒和恢复通知继续工作。

## 生效范围

- 受分组现有 `capacity_notification_enabled` 开关控制。
- 只处理有效容量状态：`very_abundant`、`abundant`、`healthy`、`tight`、`danger`、`exhausted`。
- `pending` 只表示等待数据，不作为状态变化消息的起点或终点。
- 系统首次观察到有效状态时只建立状态变化基线，不发送专用状态变化通知。
- 上述基线规则不抑制现有的低容量阈值告警；首次观察即处于危险阈值时，原有告警仍正常发送。

## 状态判断

沿用 `sub2api_capacity_notification_meta.last_observed_status` 作为上一次观察状态。每次评估当前分组时：

1. 读取当前 `capacity_summary.health_status`。
2. 当前状态为 `pending` 时，不产生状态变化。
3. 上一次状态缺失或为 `pending` 时，将当前有效状态作为基线。
4. 上一次和当前均为有效状态且不同，产生一次状态变化。
5. 无论通知渠道是否配置或投递是否成功，本轮结束后都更新观察状态，避免同一个变化在每次采样时重复发送。

状态变化判断不使用健康等级大小，因此改善和恶化都发送，例如：

- `abundant -> healthy`
- `danger -> tight`
- `healthy -> very_abundant`
- `tight -> exhausted`

## 与现有告警去重

原有 `capacity_notification_decision()` 继续决定阈值告警、状态恶化、冷却重复和恢复消息。

- 如果本轮状态变化同时使原有状态机发送告警或恢复消息，不再发送第二条专用状态变化消息。原有消息默认投递到所有启用渠道，其中包含启用的飞书机器人。
- 如果本轮发生状态变化，但原有状态机决定不发送，则创建 `sub2api.capacity.status_changed` 事件，并仅投递到所有 `status=active`、`channel_type=feishu` 的渠道。
- 状态未变化时，原有危险状态冷却重复通知保持不变。

这样每次状态变化对飞书最多产生一条消息，不会出现“容量预警”和“状态变化”同时刷屏。

## 通知内容

专用状态变化事件：

```text
账号池容量状态变化：Plus 池 充裕 -> 健康

站点：api-5001
分组：Plus 池（#3）
状态变化：充裕 -> 健康
压力阶段：稳定
实际 / 动态可用：4.0小时 / 6.0小时
并发覆盖：3.20x
判断原因：当前容量处于健康范围
变化时间：2026-07-28 12:30
```

事件字段：

- `event_type`: `sub2api.capacity.status_changed`
- `source`: `sub2api_capacity`
- `resource_type`: `sub2api_group`
- `resource_id`: `{site_id}:{group_id}`
- `payload.previous_health_status`
- `payload.health_status`
- `payload.capacity_summary`
- `payload.notification_type`: `status_change`

严重度按当前状态映射：`exhausted=critical`、`danger=danger`、`tight=warning`、`healthy=success`、`abundant/very_abundant=info`。

## 元数据

专用状态变化尝试后，在 `sub2api_capacity_notification_meta` 记录：

- `last_state_change_at`
- `last_state_change_from`
- `last_state_change_to`
- `last_state_change_event_id`
- `last_state_change_delivery_status`

这些字段只用于审计状态迁移，不替代现有 `last_attempt_at`、`last_notified_status`、`active_alert` 和冷却逻辑。

## 异常处理

- 没有启用的飞书渠道时，事件可记录为 `skipped`，状态基线仍前移。
- 单个飞书渠道失败由现有通知服务隔离和记录，不阻塞容量缓存刷新。
- 状态变化通知失败后不按每次采样重试；后续新的状态变化仍会正常触发。
- 分组通知关闭时只维护观察状态，不发送；重新开启不会补发关闭期间的历史变化。

## 测试范围

- 首次有效状态只建立状态变化基线。
- 相同状态不发送。
- 任意有效状态改善或恶化都识别为变化。
- `pending` 不发送，`pending -> 有效状态` 只建立基线。
- 分组通知关闭时不发送。
- 原有告警/恢复已发送时不重复发送专用事件。
- 原状态机不发送时，仅选择启用的飞书渠道。
- 专用消息包含前后状态、站点、分组和核心容量字段。
- 专用投递后写入状态变化审计字段且不破坏 `active_alert`。
- 现有危险冷却重复通知行为保持不变。
