# 远程数据库结构扫描设计

## 状态

现行设计，2026-07-19 已确认执行。

## 目标

使用系统中已经配置的 SQL_DSN，对账号池 Sub2API PostgreSQL、客户 Sub2API PostgreSQL 和客户 NewAPI MySQL 执行只读结构扫描。扫描结果用于设计后续数据库读仓储，不读取或导出账号、凭证、请求日志等业务数据。

## 数据来源

扫描三类站点配置：

```text
sub2api_sites  账号池 Sub2API PostgreSQL
client_sites   客户 Sub2API PostgreSQL
client_sites   客户 NewAPI MySQL
```

只处理 `status=active` 且已配置 `sql_dsn` 的站点。客户站点和账号池站点继续保持独立，不合并配置集合。

## 扫描边界

MySQL 只读取当前数据库对应的 `information_schema`：

- tables
- columns
- statistics
- table_constraints
- key_column_usage

PostgreSQL 只读取 `information_schema` 与 `pg_catalog`：

- schemata
- tables
- columns
- table_constraints
- key_column_usage
- constraint_column_usage
- pg_indexes

扫描器不得执行：

- 对业务表的 `SELECT`。
- `INSERT`、`UPDATE`、`DELETE`、DDL 或锁表。
- 行数统计和样例数据读取。
- 数据库用户、密码、SQL_DSN 或 API Key 输出。

## 实现边界

新增可复用模块：

```text
backend/app/modules/system/database_schema.py
```

职责：

1. 根据站点类型解析 SQL_DSN。
2. 使用 SQLAlchemy AsyncEngine 和 `NullPool` 建立短连接。
3. 按 MySQL/PostgreSQL 执行参数化系统目录查询。
4. 标准化为统一的 schema/table/column/index/constraint 结构。
5. 无论成功失败都释放 Engine。
6. 对异常使用现有 `redact_sql_error` 脱敏。

新增一次性命令：

```text
backend/scripts/inspect_remote_database_schemas.py
```

命令连接本系统 MongoDB，枚举已配置站点，执行扫描并把脱敏 JSON 输出到标准输出。命令本身不写 MongoDB，也不修改远程数据库。

## 输出结构

每个站点输出：

```text
site_id
site_scope          account_pool | client
client_type         sub2api | newapi
database_type       postgresql | mysql
database_endpoint   host:port/database
scanned_at
schemas[]
  name
  tables[]
    name
    type
    columns[]
      name
      ordinal_position
      data_type
      native_type
      nullable
    primary_key[]
    foreign_keys[]
    indexes[]
```

字段默认值、业务注释和表数据不进入输出，降低凭证或业务内容意外出现在报告中的风险。

## 文档产物

实际扫描完成后生成：

```text
docs/database/remote-schema-scan-2026-07-19.md
```

文档按站点列出表结构，并增加跨站点对比：

- 相同产品不同站点的表结构是否一致。
- Sub2API 中 groups、accounts、账号分组、usage/限流对应的候选表。
- NewAPI 中 users、logs、models、channels/token 对应的候选表。
- 后续可替换 HTTP API 的数据，以及仍必须保留 HTTP 的能力。

## 测试

- MySQL 系统目录结果可以正确归并为表结构。
- PostgreSQL 多 schema、索引、主外键可以正确归并。
- 扫描 SQL 只引用系统目录。
- Engine 始终释放。
- 输出不包含 SQL_DSN、用户名或密码。
- 一个站点失败不阻断其他站点扫描。

## 非目标

本阶段不编写 Sub2API/NewAPI 业务表查询，不切换现有 API 缓存，不读取业务样例行，也不修改远程数据库。
