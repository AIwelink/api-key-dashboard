# 客户站点数据库连接设计

## 状态

现行设计，2026-07-19 已确认开始实现。

## 目标

客户站点同时保留 API 连接和数据库连接。API 继续采集 RPM/TPM 等只能通过 NewAPI URL 获取的数据；数据库连接用于后续读取历史模型、用户和用量数据，并为将来的受控写操作保留完整驱动能力。

账号池后端站点不参与本设计。所有配置继续保存在独立的 `client_sites` 集合中。

## 连接模型

现有 API 字段保持不变：

```text
client_type
base_url
api_key
admin_user_id
```

新增数据库字段：

```text
database_dsn
data_retention_days
last_database_test_at
last_database_test_ok
last_database_test_error
last_database_latency_ms
last_database_version
```

协议固定：

```text
client_type=newapi  -> mysql://
client_type=sub2api -> postgresql://
```

标准输入格式：

```text
mysql://user:password@host:3306/database
postgresql://user:password@host:5432/database
```

`data_retention_days` 按站点配置，默认 90 天。本阶段只保存该配置，不启动业务数据清理。

## 安全边界

- `database_dsn` 只在后端保存，不通过 API 返回。
- 公共站点响应只返回 `database_dsn_configured` 和不含用户名、密码、查询参数的 `database_endpoint`。
- API Key 与 Admin User ID 的现有保存和掩码语义不变。
- 空数据库连接串更新表示保留原值，不会意外清空已配置密钥。
- 审计日志只能记录脱敏后的公共站点对象。
- 连接异常在持久化和返回前清除可能出现的连接串、用户名与密码。

## 驱动

使用 SQLAlchemy AsyncEngine 作为统一异步访问层：

```text
MySQL      aiomysql
PostgreSQL asyncpg
```

保存标准 DSN，建立连接时转换为：

```text
mysql+aiomysql://
postgresql+asyncpg://
```

使用完整数据库驱动而不是 TCP 端口探测，确保认证、数据库选择和 SQL 执行均正常，并支持后续参数化查询、事务与写操作。

## 连接测试

接口：

```http
POST /api/client-sites/{site_id}/database/test
```

权限与客户站点读取一致，允许 `owner`、`admin`、`maintainer` 执行。

流程：

1. 从 `client_sites` 读取站点和完整数据库连接串。
2. 校验连接串协议与 `client_type` 匹配。
3. 使用独立 AsyncEngine、`NullPool` 和 10 秒超时建立连接。
4. 执行 `SELECT 1`，再查询数据库版本。
5. 无论成功失败都立即释放 Engine。
6. 保存最近测试时间、状态、延迟、版本或脱敏错误。
7. 返回公共测试结果，不返回连接串。

成功响应包含：

```text
ok
database_type
database_endpoint
latency_ms
server_version
tested_at
```

失败使用可读的 HTTP 错误，同时最近一次失败结果仍写入站点配置，方便前端持续显示。

## 前端

“客户站点”页面保留现有 API 配置，新增独立的“数据库连接”区域：

- 固定显示 MySQL 或 PostgreSQL 类型。
- 完整连接串使用密码输入框。
- 已配置时输入框留空并显示脱敏 endpoint。
- 提供“测试数据库连接”按钮。
- 显示最近测试成功或失败、延迟、版本和时间。
- 保留天数为数字输入，默认 90。

## 测试

- NewAPI 只接受 MySQL DSN。
- 客户 Sub2API 只接受 PostgreSQL DSN。
- 公共响应不泄露 DSN、用户名或密码。
- 空 DSN 更新保留原连接串。
- 测试接口使用完整驱动执行 SQL 并释放连接。
- 成功和失败均持久化最近测试结果。
- 前端生产构建通过。

## 非目标

本阶段不读取 NewAPI 或 Sub2API 业务表，不启动 90 天回溯，不创建 RPM/TPM 或小时统计集合，也不执行任何数据库写操作。
