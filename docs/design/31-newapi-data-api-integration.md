# NewAPI Data API Integration Standard

> **文档状态：接口实测标准，代码待实现。** 本文根据 2026-07-18 提供的真实请求与响应整理。后续 NewAPI 采样、容量分析和客户使用统计必须以本文为基础；尚未由样例确认的认证头、边界包含规则和 RPM/TPM 聚合语义不得自行假设。

## Scope

本文只描述“客户站点”中的 NewAPI 数据接口，不属于账号池后端 Sub2API：

```text
配置来源：client_sites
客户端类型：client_type=newapi
Base URL：读取站点配置，不在代码中固定生产域名
账号池接口：不得使用 /api/sub2api-sites 或 sub2api_sites
```

NewAPI 站点当前需要保存：

```text
base_url
api_key
admin_user_id
status
```

提供的请求样例没有包含请求头。实现前必须再确认 API Key 与 `admin_user_id` 对应的实际认证头名称；文档和日志中不得输出完整密钥。

当前已经确认并保留以下 API 认证头：

```http
Authorization: Bearer <API Key>
New-Api-User: <Admin User ID>
```

## Database Connection

客户站点同时保存 API 连接和数据库连接，两者不能互相替代：

```text
API 连接       RPM/TPM 及只能通过 URL 获取的数据
数据库连接    后续历史用量、模型、用户数据读取与受控写操作
```

固定协议：

```text
NewAPI 客户站点  MySQL       mysql://user:password@host:3306/database
Sub2API 客户站点 PostgreSQL  postgresql://user:password@host:5432/database
```

后端使用 SQLAlchemy AsyncEngine；MySQL 使用 `aiomysql`，PostgreSQL 使用 `asyncpg`。标准 DSN 只在建立连接时转换为驱动 DSN，数据库密码不返回前端、不写入审计日志。

数据库配置与测试接口：

```http
PATCH /api/client-sites/{site_id}
POST  /api/client-sites/{site_id}/database/test
```

站点响应只公开：

```text
database_dsn_configured
database_type
database_endpoint
data_retention_days
last_database_test_at
last_database_test_ok
last_database_test_error
last_database_latency_ms
last_database_version
```

`database_endpoint` 只包含 `host:port/database`。连接测试使用完整驱动执行 `SELECT 1` 和版本查询，成功或失败都会保存脱敏后的最近测试结果。`data_retention_days` 按客户站点配置，默认 90 天；业务数据采集与 TTL 清理将在采样阶段实现。

## Common Rules

- 下列接口均使用 `GET`，没有请求体。
- `start_timestamp` 和 `end_timestamp` 是 Unix 时间戳，单位为秒。
- 调用方必须显式传入开始和结束时间，不能依赖浏览器本地时区拼接日期。
- 金额、token 和 quota 等累计值可能很大，后端使用 Python `int`，数据库使用 64 位整数。
- 先检查 HTTP 状态，再检查响应中的 `success`；不能只判断是否返回 JSON。
- `quota` 是 NewAPI 内部额度单位，未配置换算规则前不能直接当作美元金额。
- 聚合行中的 `id`、`user_id`、`token_id`、`channel_id` 在样例中可能全部为 `0`，不能作为记录唯一标识。
- 推荐使用“接口 + 聚合维度 + `created_at` + 维度值”构造采样唯一键，避免重复写入。

典型响应外壳：

```json
{
  "data": [],
  "message": "",
  "success": true
}
```

## Model Usage Timeline

按模型和时间桶读取用量：

```http
GET <newapi-base-url>/api/data/?start_timestamp=<unix-seconds>&end_timestamp=<unix-seconds>&default_time=<granularity>
```

### Query Parameters

| 参数 | 必填 | 已确认口径 |
| --- | --- | --- |
| `start_timestamp` | 是 | 查询开始 Unix 秒时间戳 |
| `end_timestamp` | 是 | 查询结束 Unix 秒时间戳 |
| `default_time` | 是 | `hour`、`day` 或 `week` |

已实测请求形式：

```text
GET /api/data/?start_timestamp=1784300227&end_timestamp=1784386627&default_time=hour
GET /api/data/?start_timestamp=1784300408&end_timestamp=1784386808&default_time=day
```

响应中的 `data` 是聚合行数组。按模型请求时，`model_name` 有值，`username` 通常为空：

```json
{
  "data": [
    {
      "id": 0,
      "user_id": 0,
      "username": "",
      "model_name": "gpt-5.6-sol",
      "created_at": 1784300400,
      "use_group": "",
      "token_id": 0,
      "channel_id": 0,
      "node_name": "",
      "token_used": 43636746,
      "count": 550,
      "quota": 3424892
    }
  ],
  "message": "",
  "success": true
}
```

### Row Fields

| 字段 | 含义 |
| --- | --- |
| `created_at` | 当前聚合时间桶的 Unix 秒时间戳 |
| `model_name` | 模型维度；按模型统计时使用 |
| `username` | 用户维度；按模型统计时通常为空 |
| `token_used` | 当前时间桶累计 token 使用量 |
| `count` | 当前时间桶请求次数 |
| `quota` | 当前时间桶消耗的 NewAPI 内部额度 |
| `use_group` | NewAPI 使用分组；样例中为空，但字段需要保留 |
| `node_name` | 节点名称；样例中为空，但字段需要保留 |
| `id`、`user_id`、`token_id`、`channel_id` | 聚合兼容字段，不能假设始终包含实体 ID |

实测 `hour` 响应的 `created_at` 按整点递增。`day` 和 `week` 的时间桶对齐时区，以及开始、结束边界是否包含，仍需通过跨日和跨周请求确认。

## RPM And TPM Statistics

读取指定时间范围的 RPM、TPM 和 quota 汇总：

```http
GET <newapi-base-url>/api/log/stat?p=1&page_size=1&type=0&start_timestamp=<unix-seconds>&end_timestamp=<unix-seconds>
```

已实测请求形式：

```text
GET /api/log/stat?p=1&page_size=1&type=0&start_timestamp=1784304000&end_timestamp=1784391217
```

已实测响应：

```json
{
  "data": {
    "quota": 164373249,
    "rpm": 68,
    "tpm": 7065395
  },
  "message": "",
  "success": true
}
```

字段口径：

| 字段 | 当前可用含义 |
| --- | --- |
| `quota` | 查询范围内接口返回的内部额度统计 |
| `rpm` | NewAPI 返回的 RPM 指标 |
| `tpm` | NewAPI 返回的 TPM 指标 |

`p=1`、`page_size=1`、`type=0` 是当前实测调用参数。现有样例不足以确认 `rpm` 和 `tpm` 是区间最大值、平均值、最近值还是其他聚合结果；在完成连续短窗口对照前，不得用自定义公式替换，也不得直接将它们作为告警峰值。

## User Usage Statistics

按用户和时间桶读取用量：

```http
GET <newapi-base-url>/api/data/users?start_timestamp=<unix-seconds>&end_timestamp=<unix-seconds>
```

已实测请求形式：

```text
GET /api/data/users?start_timestamp=1783782932&end_timestamp=1784387732
```

响应仍使用统一外壳。按用户请求时，`username` 有值，`model_name` 通常为空：

```json
{
  "data": [
    {
      "id": 0,
      "user_id": 0,
      "username": "wangzhi10",
      "model_name": "",
      "created_at": 1783785600,
      "use_group": "",
      "token_id": 0,
      "channel_id": 0,
      "node_name": "",
      "token_used": 24887672,
      "count": 419,
      "quota": 977914
    }
  ],
  "message": "",
  "success": true
}
```

用户统计的主要维度键为 `username + created_at`。样例中的 `user_id=0`，因此当前不能依赖 `user_id` 区分用户。后续若 NewAPI 返回稳定非零 `user_id`，需要先验证再调整唯一键和历史数据迁移策略。

## Integration Boundaries

后续代码实现应遵守：

1. NewAPI 数据采样器只读取 `client_sites` 中 `client_type=newapi` 且 `status=active` 的站点。
2. 每个站点独立保存采样状态和错误，不因一个站点失败阻断其他站点。
3. 原始响应和标准化采样分开保存；标准化字段至少包含 `site_id`、时间桶、维度、`token_used`、`count` 和 `quota`。
4. RPM/TPM 原值先按站点和采样时间保存，在聚合语义确认前不进行二次换算。
5. 用户名可能包含邮箱、中文和其他 Unicode 字符，日志与导出必须使用 UTF-8。
6. 页面展示必须区分“模型用量”“用户用量”“RPM/TPM”，不能把三个接口的数据混成同一统计口径。

## Open Items

实现采样器前还需要实测：

- HTTP 401、403、429 和 5xx 的错误响应格式。
- `start_timestamp` / `end_timestamp` 的包含边界。
- `day`、`week` 时间桶使用的时区和周起始日。
- `/api/data/users` 是否支持 `default_time` 或其他粒度参数。
- `/api/log/stat` 中 `type=0` 的准确含义。
- `/api/log/stat` 的 `rpm`、`tpm` 聚合算法。
- 分页参数是否只为接口兼容，是否影响 `/api/log/stat` 的统计结果。
