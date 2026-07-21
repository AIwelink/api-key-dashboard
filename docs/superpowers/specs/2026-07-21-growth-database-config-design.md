# 访问流量分析 PostgreSQL 配置设计

## 状态

2026-07-21 已确认设计。本阶段只实现全局 PostgreSQL 连接配置，不创建增长业务表，不实现推广链接、`/r/*`、点击记录、注册归因或运营数据同步。

## 目标

在管理面板“客户站点”导航项下方增加“访问流量分析配置”页面，为全系统唯一的增长 PostgreSQL 提供安全的配置保存、脱敏状态展示和真实连接测试能力。

## 配置边界

- 全系统只配置一套增长 PostgreSQL。
- 配置不属于任何 `client_site`，不保存 `client_site_id`。
- 完整 DSN 保存在现有 MongoDB `app_settings` 集合的单例文档中。
- 未来推广链接、点击和归因数据写入该 PostgreSQL；本阶段不创建这些表。
- Sub2API 数据库连接保持独立，不在本配置中保存或修改。

单例标识固定为：

```text
app_settings._id = "growth_database"
```

## 导航与权限

导航顺序：

```text
客户站点
访问流量分析配置
```

页面契约：

- 视图标识：`traffic-analysis-config`
- 路径：`/traffic-analysis-config`
- 折叠导航短标签：`配`
- 仅 `owner` 和 `admin` 显示导航并可进入页面。
- `maintainer` 和 `viewer` 直接访问该路径时跳回默认的 API 账号池状态页。
- 后端读取、保存和测试接口均使用 `require_roles("owner", "admin")`，前端隐藏不能替代后端授权。

## 页面设计

页面标题为“访问流量分析配置”，主体使用现有客户站点数据库连接区域的表单和状态样式。

表单包含：

- 数据库类型：只读，固定显示 `PostgreSQL`。
- `SQL_DSN`：多行密码配置输入区，关闭拼写检查并使用 `autocomplete="new-password"`。
- 保存配置按钮。
- 测试数据库连接按钮。
- 已配置状态和脱敏后的 `host:port/database` endpoint。
- 最近测试结果：成功或失败、测试时间、延迟、PostgreSQL 版本或脱敏错误。

页面首次加载读取公开配置。完整 DSN 永远不回填到浏览器；已配置时输入框保持空白，并显示“已配置，留空不修改”。

交互规则：

- 未配置时，空 DSN 不能保存。
- 已配置时，空 DSN 保存表示保留原连接串，不清空密码。
- 保存配置不自动测试连接。
- 未配置连接时禁用测试按钮。
- 保存或测试进行中时禁用相关按钮，避免重复请求。
- 请求失败通过现有 toast 展示，页面保留最后一次成功读取到的公开状态。

## DSN 格式

只支持项目现有 PostgreSQL DSN 解析器已经支持的两种输入：

```text
host=postgres.example.com port=5432 user=growth_app password=secret dbname=aiwelink_growth sslmode=require
```

或：

```text
DATABASE_HOST=postgres.example.com
DATABASE_PORT=5432
DATABASE_DBNAME=aiwelink_growth
DATABASE_USER=growth_app
DATABASE_PASSWORD=secret
DATABASE_SSLMODE=require
```

本阶段不新增 `postgresql://` URL 格式。端口默认 `5432`，`sslmode` 沿用现有允许值和默认行为。

## 后端接口

### 读取公开配置

```http
GET /api/settings/growth-database
```

返回：

```json
{
  "database_type": "postgresql",
  "sql_dsn_configured": true,
  "database_endpoint": "postgres.example.com:5432/aiwelink_growth",
  "last_database_test_at": "2026-07-21T00:00:00Z",
  "last_database_test_ok": true,
  "last_database_test_error": "",
  "last_database_latency_ms": 18.4,
  "last_database_version": "PostgreSQL 17.5"
}
```

未配置时使用相同字段结构，`sql_dsn_configured=false`，endpoint 和测试字段为空。响应不得包含 `sql_dsn`、数据库用户名或密码。

### 保存配置

```http
PUT /api/settings/growth-database
Content-Type: application/json

{
  "sql_dsn": "host=..."
}
```

行为：

1. 去除输入首尾空白。
2. 非空输入必须通过现有 `parse_sql_dsn(..., "postgresql")` 校验。
3. 未配置且输入为空时返回 `400`。
4. 已配置且输入为空时保留原 DSN。
5. 非空输入更新 DSN、脱敏 endpoint、`updated_at` 和 `updated_by`。
6. 返回公开配置对象。
7. 审计记录只保存更新前后的公开配置，不保存完整 DSN。

### 测试连接

```http
POST /api/settings/growth-database/test
```

行为：

1. 未配置时返回 `400`。
2. 使用独立 SQLAlchemy AsyncEngine、`NullPool` 和 10 秒超时。
3. 执行 `SELECT 1` 和 `SELECT VERSION()`。
4. 无论成功失败都立即释放 Engine。
5. 成功或连接失败均持久化最近测试结果。
6. 连接失败返回 `200` 和 `ok=false`，使页面能够稳定展示诊断结果；无效或缺失配置返回 `400`。
7. 错误在持久化、API 响应和审计前使用现有 `redact_sql_error` 脱敏。

成功响应：

```json
{
  "ok": true,
  "database_type": "postgresql",
  "database_endpoint": "postgres.example.com:5432/aiwelink_growth",
  "latency_ms": 18.4,
  "server_version": "PostgreSQL 17.5",
  "tested_at": "2026-07-21T00:00:00Z",
  "settings": {
    "database_type": "postgresql",
    "sql_dsn_configured": true,
    "database_endpoint": "postgres.example.com:5432/aiwelink_growth",
    "last_database_test_at": "2026-07-21T00:00:00Z",
    "last_database_test_ok": true,
    "last_database_test_error": "",
    "last_database_latency_ms": 18.4,
    "last_database_version": "PostgreSQL 17.5"
  }
}
```

失败响应保留相同顶层结构，以 `ok=false` 和脱敏后的 `error` 代替 `server_version`。

## 模块边界

新增独立的增长数据库设置模块，负责：

- 读取公开和私有配置；
- 校验并保存 DSN；
- 将私有文档转换为公开对象；
- 持久化连接测试结果。

现有 SQL DSN 模块继续负责解析、endpoint 构造和错误脱敏。现有客户站点连接探测逻辑抽取为接受 `sql_dsn` 与 `database_type` 的通用探测函数，客户站点测试与增长数据库测试共同调用，外部行为保持不变。

前端新增独立 `TrafficAnalysisConfigPage`，只依赖上述三个设置接口，不读取客户站点列表，也不承担增长数据展示职责。

## 存储字段

MongoDB 私有单例文档保存：

```text
_id
database_type
sql_dsn
database_endpoint
updated_at
updated_by
last_database_test_at
last_database_test_ok
last_database_test_error
last_database_latency_ms
last_database_version
```

`database_type` 固定为 `postgresql`。`database_endpoint` 可以从 DSN 重算，但同时保存用于受控展示和诊断。

## 安全要求

- 完整 DSN 只存在于后端私有配置和建立数据库连接的内存中。
- API、日志、异常、toast、审计和前端状态不得出现完整 DSN、数据库用户名或密码。
- 更新审计使用公开配置作为 `before` 和 `after`。
- 测试审计只记录 `ok`、脱敏 endpoint、延迟、版本或脱敏错误。
- 本阶段沿用现有客户站点 DSN 的 MongoDB 后端存储语义，不新增字段级加密；字段级加密属于后续独立安全增强。

## 错误处理

- DSN 格式错误：`400`，返回解析器的非敏感校验信息。
- 未配置即测试：`400`。
- 连接超时、认证失败或网络错误：测试响应 `200`、`ok=false`，错误必须脱敏。
- MongoDB 读写失败：保持现有全局错误处理，不返回私有配置。
- 前端加载失败：显示错误 toast，不展示虚构的连接状态。

## 测试

后端测试覆盖：

- 未配置公开响应；
- 保存有效 PostgreSQL DSN；
- 拒绝 MySQL 或无效 DSN；
- 已配置后空输入保留原 secret；
- 公开响应和审计不泄露 DSN、用户名或密码；
- owner/admin 权限与 maintainer/viewer 拒绝；
- 测试成功持久化 endpoint、延迟和版本；
- 测试失败持久化脱敏错误并返回 `ok=false`；
- 未配置连接不能测试；
- 现有客户站点数据库测试在抽取通用探测函数后保持通过。

前端测试覆盖：

- owner/admin 导航在“客户站点”下方显示配置页；
- maintainer/viewer 不显示该导航；
- `/traffic-analysis-config` 路由映射；
- 已配置状态不回填 DSN；
- 未配置时测试按钮禁用；
- 保存和测试请求使用正确接口并展示公开结果。

最终运行完整后端测试、完整前端测试和生产构建，并在桌面与移动端检查表单无重叠或溢出。
