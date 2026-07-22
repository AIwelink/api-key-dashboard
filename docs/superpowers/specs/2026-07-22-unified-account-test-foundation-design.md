# 统一账号测试基座设计

## 背景

系统当前存在多条独立账号测试路径：长期 7d 限流账号探测、账号复活、5002 自产 Plus 探测等。不同路径分别实现模型请求、错误分类、测试时间记录和调度处理，容易出现重复请求和判断口径不一致。

新的测试基座需要覆盖所有启用的 Sub2API 站点和全部远端账号，包括 `schedulable=false` 的账号。测试结果必须先保存到数据库，再通知调度和类型判断程序读取结果并执行各自业务。机器人通知不属于本次“通知”的含义。

## 目标

- 使用统一服务执行单账号模型测试。
- 固定使用 `gpt-5.4`、空 prompt 和 `default` 模式。
- 所有启用 Sub2API 站点的每个远端账号至少每 24 小时测试一次。
- 新发现或从未测试的账号立即进入待测队列。
- 包括调度关闭账号，已从远端删除的账号不再测试。
- 全局严格串行发起远端测试，避免并行探测冲击上游。
- 测试结果先持久化，再立即触发内部判断。
- 判断程序只读取已保存结果，不重新请求远端测试。
- 调度判断、Free 到 Plus 纠错及后续判断业务复用统一结果模型。
- 判断过程可幂等重放，进程中断不能丢失已保存但尚未判断的结果。

## 非目标

- 不发送飞书、钉钉、Telegram 或邮件通知。
- 不修改账号名称或所属分组。
- 不改造 5002 自产 Plus 探测和转移流程；该流程本期保持原样。
- 不在本期增加前端配置页或结果列表。
- 不并行测试账号。
- 不保存 access token、refresh token、id token 或完整 credentials。

## 总体架构

系统分为四层：

1. `account_test_service`：执行一次远端测试、标准化结果并落库。
2. `account_test_dispatcher`：在结果提交后立即分发给注册的判断器，并重放未完成事件。判断器通过显式 registry 注册，新增业务不修改测试执行器。
3. 判断器：读取保存结果，分别处理调度状态和账号类型纠错。
4. `account_test_scheduler`：持续寻找从未测试或超过 24 小时的账号，严格串行调用测试基座。

数据流：

```text
定时任务选出 due 账号
  -> 测试基座调用 Sub2API /accounts/{id}/test
  -> 保存测试事件和账号最新测试状态
  -> 提交成功后调用内部 dispatcher
  -> 调度判断器 / Free-Plus 判断器读取事件
  -> 幂等执行判断并保存 handler 状态
```

判断失败不能回滚或删除测试结果。dispatcher 后台轮询 `pending` 和可重试的 `failed` handler，保证进程中断后能够继续处理。

## 统一测试请求

每次请求固定为：

```json
{
  "model_id": "gpt-5.4",
  "prompt": "",
  "mode": "default"
}
```

管理 API Key 优先从站点 PostgreSQL 读取，与 5002 现有稳定路径一致；没有 SQL 配置时才使用站点保存的管理密钥。管理密钥认证失败属于站点级配置错误，本轮暂停该站点，不能把所有账号误判为 401。

测试器一次只处理一个账号。站点之间也不并行，保证全局同时最多一个账号测试请求。调度器复用 5002 的 MongoDB 租约模式并定时续租，避免多进程或多实例同时运行测试队列。

## 标准结果模型

测试结果至少归一化为以下 outcome：

- `passed`：上游明确返回成功。
- `rate_limited`：账号返回 429，说明凭证和模型资格有效，但当前额度不可用。
- `unauthorized`：401 或明确 token invalidated/revoked。
- `payment_required`：402，例如 deactivated workspace。
- `inactive_owner`：已确认的失效类 403，例如 personal access token owner inactive 或 biscuit baker credential error。
- `forbidden_other`：其他 403，不足以证明账号失效。
- `model_not_supported`：账号不支持 `gpt-5.4`。
- `failed`：其他业务失败。
- `transport_error`：超时、连接错误或无法解析的上游响应。

Sub2API 管理密钥被拒绝不属于账号 outcome。执行器必须抛出站点级 `admin_auth_error`，由调度器记录站点退避状态，不插入账号测试事件。

结果保存上游 HTTP 状态、错误代码、经过截断和脱敏的响应摘要、延迟和模型，不保存认证凭证。

## 存储模型

### `sub2api_account_test_states`

每个站点远端账号一条最新状态：

```json
{
  "_id": "US06-5001:4072",
  "site_id": "US06-5001",
  "remote_account_id": 4072,
  "normalized_email": "user@example.com",
  "last_event_id": "...",
  "last_outcome": "passed",
  "last_tested_at": "2026-07-22T12:00:00Z",
  "next_test_at": "2026-07-23T12:00:00Z",
  "model": "gpt-5.4",
  "verified_plan_type": "plus",
  "verified_plan_type_source": "gpt-5.4",
  "updated_at": "2026-07-22T12:00:00Z"
}
```

`site_id + remote_account_id` 唯一。成功和失败都将 `next_test_at` 设置为测试完成后 24 小时。远端账号不存在后不主动删除历史，但不会再次进入待测队列。

### `sub2api_account_test_events`

每次测试保存一条事件。测试结果载荷写入后不可修改，只有 `dispatch` handler 状态允许更新：

```json
{
  "_id": "generated-event-id",
  "site_id": "US06-5001",
  "remote_account_id": 4072,
  "normalized_email": "user@example.com",
  "model": "gpt-5.4",
  "outcome": "passed",
  "success": true,
  "http_status": 200,
  "error_code": null,
  "response_preview": "...",
  "latency_ms": 820,
  "tested_at": "2026-07-22T12:00:00Z",
  "next_test_at": "2026-07-23T12:00:00Z",
  "dispatch": {
    "scheduling": {"status": "pending"},
    "plan_correction": {"status": "pending"}
  },
  "expires_at": "2026-10-20T12:00:00Z"
}
```

测试事件保留 90 天并使用 TTL 清理。最新状态长期保留。

### `sub2api_account_test_site_meta`

每个站点保存最近一次队列执行时间、管理密钥错误和退避截止时间。站点级错误不得复制到账号事件。

## 内部分发与幂等

测试基座必须按以下顺序执行：

1. 调用远端测试并得到标准 outcome。
2. 插入测试结果载荷不可变的测试事件。
3. upsert 最新测试状态。
4. 两次数据库写入成功后调用 dispatcher。

MongoDB 当前未启用跨集合事务，因此事件 ID 和 latest state 更新必须幂等。若事件写入成功但 latest state 更新失败，调度器不得立即重复远端测试；恢复任务应从事件重建 latest state。

每个判断器以 `event_id + handler_name` 为幂等键。handler 状态包括 `pending`、`processing`、`completed`、`failed`，并保存 attempt、last_error、next_retry_at。dispatcher 即时调用后仍由后台重放循环兜底。

## 调度判断器

调度判断器只根据保存 outcome 执行动作：

- `passed`：账号当前 `schedulable=false` 时调用 `/accounts/{id}/schedulable` 设置为 `true`。
- `unauthorized`、`payment_required`、`inactive_owner`：账号当前调度开启时设置为 `false`。
- `rate_limited`、`forbidden_other`、`model_not_supported`、`failed`、`transport_error`：不修改调度。

调度接口返回成功后更新本地账号缓存。接口失败只将 scheduling handler 标记为 failed，测试事件仍然有效并可重放。

该规则意味着人工关闭的账号只要 `gpt-5.4` 测试明确成功，也会重新打开调度，这是已确认的业务要求。

## Free 到 Plus 判断器

候选签名必须同时满足：

- 远端 `credentials.plan_type=free`。
- `extra.source=sub_bundle_input`。
- 所属分组名称包含独立 `plus` 标记。
- `codex_5h_window_minutes=0`。
- `codex_7d_window_minutes=10080`。

判断规则：

- `passed` 或 `rate_limited`：在 latest state 写入 `verified_plan_type=plus`。
- `model_not_supported`：清除先前由该验证器写入的 Plus 纠正。
- 其他 outcome：保留已有纠正，不根据认证失败推断订阅类型。

纠正只影响本地类型归一化、容量统计、账号探测身份和后续判断，不调用远端更新接口，不改名称，不改分组。缓存刷新和账号探测在批量归一化前读取 verified plan type；只有已保存的验证结果可以覆盖远端误报 Free，候选签名本身不再直接等同于 Plus。

测试基座同时把最新 outcome、测试时间、模型和脱敏摘要同步到现有账号缓存的 `remote_test_*` 字段，账号列表继续显示最近一次测试信息。该同步失败不影响数据库中的测试事件和判断分发，后续刷新可从 latest state 修复缓存。

## 24 小时调度任务

调度任务默认启用，没有额外前端开关。每轮：

1. 列出所有启用的 Sub2API 站点。
2. 从当前账号缓存读取远端仍存在的账号，包括调度关闭账号。
3. 关联 latest test state。
4. 优先选择从未测试账号，其次选择 `next_test_at <= now` 中最早到期账号。
5. 全局只测试一个账号，保存和分发完成后再选下一个。
6. 没有 due 账号时短暂休眠。

单账号异常不能停止整个循环。站点管理密钥错误时对该站点增加退避并继续其他站点。服务关闭时正确响应 cancellation，不启动新请求。

## 与现有任务关系

- `long_7d_probe_scheduler_loop` 从应用启动任务中移除，其健康判断由统一 24 小时测试覆盖。
- 旧 `long_7d_account_probes` 数据和站点配置暂时保留，只停止产生新记录，避免破坏历史页面或回滚能力。
- 5002 `plus_self_produced` 模块、调度、集合和转组规则完全保持不变。
- 现有账号状态快照探测继续负责 PostgreSQL 字段变化和历史事件，不重复发起模型测试。

## 索引

新增：

```text
sub2api_account_test_states: unique(site_id, remote_account_id)
sub2api_account_test_states: next_test_at
sub2api_account_test_events: (site_id, remote_account_id, tested_at desc)
sub2api_account_test_events: dispatch.*.status + dispatch.*.next_retry_at
sub2api_account_test_events: TTL(expires_at)
sub2api_account_test_site_meta: unique(site_id)
operation_locks: unified account test scheduler lease id
```

## 失败恢复

- 远端测试失败：仍保存标准失败结果，24 小时后重试。
- 测试事件保存失败：不执行判断；短退避后允许重新测试。
- latest state 保存失败：事件保留，恢复器重建状态，避免重复测试。
- 即时 dispatcher 失败：事件保持 pending/failed，由重放循环继续。
- 判断动作成功但 handler 完成标记失败：下一次重放先读取当前远端/本地状态，幂等跳过已完成动作。
- 管理密钥 401：标记站点级配置错误，不写账号 unauthorized。

## 测试要求

- 单元测试覆盖所有标准 outcome，尤其区分管理密钥 401 与账号 401、确认失效 403 与普通 403。
- 验证测试结果先落库、后调用 dispatcher。
- 验证 dispatcher pending/failed 重放和 handler 幂等。
- 验证调度成功自动打开，401/402/确认失效 403 自动关闭，429 不改变调度。
- 验证所有账号包括 `schedulable=false` 均进入 24 小时队列。
- 验证从未测试账号优先、已测试账号 24 小时内不重复、到期后重测。
- 验证全局最大并发为 1。
- 验证 Free 候选只有 `passed`/`rate_limited` 才纠正为 Plus，`model_not_supported` 撤销纠正。
- 验证普通 Free 分组、缺少完整签名的账号和已有有效非 Free 历史类型不会误判。
- 验证测试和判断全程不修改名称与分组。
- 验证 5002 现有测试不需要修改并继续通过。
- 运行完整后端测试套件。

## 发布行为

部署后统一测试调度器默认启动。从未测试的账号数量可能较多，调度器持续串行消化队列，不集中并发。随着结果产生，判断器即时更新调度状态和本地类型纠正；容量页在后续正常缓存刷新中读取最新确认类型。
