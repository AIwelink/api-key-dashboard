# 客户站点分钟 RPM/TPM 采样设计

## 状态

现行设计，2026-07-19 已确认。

## 目标

为 `client_sites` 中的 NewAPI 和 Sub2API 客户站点建立统一的分钟级 RPM/TPM 时间序列，用于后续客户流量分析、容量判断和告警。

本阶段只采集客户端站点整体指标。客户站点当前一个配置对应一个完整站点，不按 Sub2API 分组拆分。

## 范围边界

本功能只处理客户站点：

```text
配置集合：client_sites
目标类型：client_type=newapi | sub2api
采样集合：client_minute_metrics
状态集合：client_metric_sampler_state
```

账号池后端继续使用 `sub2api_sites`、`sub2api_tpm_samples` 和现有账号池采样任务。本功能不得读取账号池站点作为客户站点，也不得把客户指标写入账号池集合。

已经配置的 MySQL 或 PostgreSQL 原始数据库用于后续历史、模型、用户和其他业务数据读取。RPM/TPM 仍通过站点 HTTP 管理接口采集，本阶段不从原始数据库重复计算。

## 架构

采用协议适配器与统一采样器：

```text
client_sites
  |
  v
ClientMetricSampler
  |-- NewApiMetricAdapter
  |     GET /api/log/stat
  |
  |-- Sub2ApiMetricAdapter
        GET /api/v1/admin/dashboard/snapshot-v2
  |
  v
client_minute_metrics
client_metric_sampler_state
```

建议模块边界：

```text
backend/app/modules/client_metrics/
  adapters/base.py       适配器协议和统一采样结果
  adapters/newapi.py     NewAPI 请求、认证和响应解析
  adapters/sub2api.py    Sub2API 请求、累计值解析和差值计算
  models.py              分钟桶、质量状态和文档构造
  sampler.py             站点枚举、并行、互斥和分钟调度
  queries.py             分钟序列和采样状态查询
```

路由保留在 `backend/app/routers/client_sites.py` 或拆为职责单一的 `client_metrics.py`。业务代码只能依赖 `app.modules.*`，不在 `app.services` 增加新实现。

## 适配器协议

统一采样器不理解具体管理接口。每个适配器接收站点密钥配置、目标分钟和持久化游标，返回统一结果：

```text
rpm
tpm
quality
source
source_updated_at
total_requests
total_tokens
elapsed_seconds
error_code
cursor
```

适配器不得直接写 MongoDB。统一采样器负责构造文档、幂等写入和更新状态。

当前适配器按站点整体采样。接口预留内部 `scope` 扩展点，未来 Sub2API 如需按分组采样，应单独设计存储唯一键和查询口径；本阶段不写 `group_id`。

## NewAPI 采样

请求：

```http
GET <base_url>/api/log/stat?p=1&page_size=1&type=0&start_timestamp=<value>&end_timestamp=<value>
Authorization: Bearer <API Key>
New-Api-User: <Admin User ID>
```

`start_timestamp` 和 `end_timestamp` 是接口必填参数，但不用于定义本系统内 RPM/TPM 的时间窗口语义。采样器传入目标分钟对应的合法值，最终以接口返回的 `data.rpm` 和 `data.tpm` 为准。

要求：

- 先检查 HTTP 状态，再检查响应中的 `success`。
- `rpm` 和 `tpm` 必须是非负数；无效或缺失时该分钟标记为缺失。
- 不保存 `quota`、完整响应或无关兼容字段。
- 不自行使用数据库请求数或 token 数替换接口返回值。

## Sub2API 采样

请求站点整体快照：

```http
GET <base_url>/api/v1/admin/dashboard/snapshot-v2
```

请求使用 `granularity=hour`、`include_stats=true`、`include_trend=true`，不传 `group_id`。适配器从当前小时趋势项读取累计 `requests` 和 `total_tokens`，通过持久化游标计算相邻采样差值：

```text
rpm = request_delta / (elapsed_seconds / 60)
tpm = token_delta / (elapsed_seconds / 60)
```

规则：

- 同一远程小时桶内，使用当前累计值减去上次累计值。
- 远程小时桶变化时，使用新小时当前累计值作为本次增量。
- 第一次采样只建立游标，当前分钟标记为缺失，不假设历史基线为零。
- 计数器回退且不能由整点切换解释时，标记 `counter_reset`，RPM/TPM 留空，并用当前值重建后续游标。
- 上游更新时间或累计值没有推进时，标记 `delayed`，不得写入虚假的零值。
- 明确观察到上游已更新且两个增量均为零时，才允许记录真实的 `rpm=0`、`tpm=0`。

## 分钟调度

采样器按 UTC 自然分钟调度，并在分钟边界后预留短暂延迟供上游落库。不得使用“本轮完成后固定休眠 60 秒”的方式，以免执行耗时造成长期漂移。

每轮流程：

1. 计算目标 UTC 分钟桶。
2. 查询 `client_sites` 中 `status=active` 且 API Key 已配置的站点。
3. 按 `client_type` 选择适配器。
4. 站点之间使用有上限的并行执行。
5. 同一站点使用进程内互斥，避免手动采样与定时采样重叠。
6. 采样任务已执行时，无论适配器返回成功或缺失都写入该分钟的确定性文档。
7. 更新该站点持久化采样状态和游标。
8. 一个站点失败不得终止其他站点或采样主循环。

确定性主键：

```text
<site_id>:<bucket_at UTC ISO minute>
```

重复执行同一分钟时使用 upsert/replace 覆盖，不产生重复记录。系统不自动补采过去的缺失分钟。

## 分钟指标存储

集合：`client_minute_metrics`

```javascript
{
  _id: "<site_id>:<bucket_at>",
  site_id: "client-site-id",
  client_type: "newapi | sub2api",
  bucket_at: ISODate,
  sampled_at: ISODate,
  rpm: Number | null,
  tpm: Number | null,
  quality: "complete | missing | delayed | counter_reset",
  source: "newapi_reported | sub2api_hour_delta",
  source_updated_at: ISODate | null,
  total_requests: Number | null,
  total_tokens: Number | null,
  elapsed_seconds: Number | null,
  error_code: String | null,
  expires_at: ISODate
}
```

只保存当前协议需要的可选字段，空的可选字段可以省略。不得保存 API Key、SQL_DSN、完整远程错误或原始响应。

`expires_at` 使用采样时站点的 `data_retention_days` 计算。MongoDB 在该字段建立 TTL 索引，`expireAfterSeconds=0`。

## 缺失数据语义

采样任务实际执行到的每个目标分钟都应有一条记录。以下情况 RPM/TPM 为 `null`：

- HTTP、认证、超时或响应格式失败：`quality=missing`。
- Sub2API 上游数据尚未推进：`quality=delayed`。
- Sub2API 累计计数器异常回退：`quality=counter_reset`。
- Sub2API 首次建立游标：`quality=missing`。

缺失数据不回填、不插值，不使用后续累计增量拆分过去分钟。后端进程停止期间没有执行采样，因此不会补写空文档；查询时应把时间范围内未出现的分钟桶识别为缺失。后续查询和分析忽略空 RPM/TPM，同时必须能够计算完整分钟数、空指标分钟数、缺口分钟数和数据完整率。

## 采样状态

集合：`client_metric_sampler_state`

每个站点一条：

```text
site_id
client_type
last_attempt_at
last_success_at
last_bucket_at
last_quality
last_rpm
last_tpm
consecutive_failures
last_error
cursor_hour
cursor_requests
cursor_tokens
cursor_sampled_at
source_updated_at
updated_at
```

`last_error` 只保存脱敏后的简短信息。采样状态用于区分真实零流量、上游延迟和采集任务停止。

## API

首期提供：

```http
GET  /api/client-sites/{site_id}/metrics/minutes
GET  /api/client-sites/{site_id}/metrics/status
POST /api/client-sites/{site_id}/metrics/sample
```

分钟序列接口接受明确的 UTC 开始、结束时间和受限条数，按 `bucket_at` 返回。响应附带：

```text
total_minutes
complete_minutes
missing_minutes
gap_minutes
completeness_ratio
```

手动采样遵守同一站点互斥和幂等规则，不创建独立存储格式。读取允许 `owner`、`admin`、`maintainer`、`viewer`；手动采样允许 `owner`、`admin`、`maintainer`。

## 前端

客户站点页面只增加轻量采样状态，不在本阶段建设趋势分析页面：

- 最近采样时间。
- 最近 RPM 和 TPM。
- 最近数据质量。
- 连续失败次数和脱敏错误。
- 手动采样按钮。

缺失值显示为无数据，不显示为 `0`。

## 日志与安全

- 正常的每分钟 HTTP 请求不打印 INFO 级响应日志。
- 单站点失败记录站点 ID、客户端类型和脱敏错误，不记录 API Key、Admin User ID、SQL_DSN 或认证头。
- 连续失败状态持久化，服务重启后不丢失。
- 远程响应正文只参与当前解析，不持久化。

## 测试

后端单元和集成测试至少覆盖：

- NewAPI 请求路径、认证头、接口失败和数值解析。
- NewAPI 直接保存上游 RPM/TPM，不用时间参数重新计算。
- Sub2API 站点整体请求不传 `group_id`。
- Sub2API 同小时差值、跨整点、首次游标、上游延迟和计数器回退。
- 自然分钟对齐和确定性文档 ID。
- 失败分钟写空指标，后续成功不回填过去分钟。
- 站点并行隔离和同站点互斥。
- 每站点 `data_retention_days` 与 TTL 索引。
- 查询范围、数据完整率和角色权限。
- 客户采样只读 `client_sites`，不写账号池采样集合。
- 响应、日志和持久化状态不泄露密钥。

前端测试覆盖最近状态、缺失值、错误状态和手动采样反馈；生产构建必须通过。

## 非目标

本阶段不实现：

- 按 Sub2API group 采样。
- 从客户 MySQL/PostgreSQL 回溯模型、用户和历史用量。
- 缺失分钟补采、插值或重分配。
- RPM/TPM 趋势图、容量算法或通知规则。
- 修改账号池现有 TPM/容量采样逻辑。
- 对远程客户数据库执行写操作。
