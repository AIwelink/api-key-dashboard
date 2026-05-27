# 整体日志系统设计

## 目标

当前处于开发阶段，日志系统优先帮助定位问题：

- 记录应用启动、关闭、后台任务、请求开始、请求结束、异常栈。
- 每个请求生成 `request_id`，并通过响应头 `x-request-id` 返回，方便前后端和服务端日志对齐。
- 开发阶段默认详细日志；后续并发规模上来后，可通过环境变量切换到精简日志。
- 日志自动轮转和清理，避免长期运行后堆积过多文件。

## 安全边界

本项目会处理 `account_json`、OpenAI OAuth token、sub2api token、密码等敏感数据。

因此即使在开发阶段，也不默认记录请求正文，不记录完整 `account_json`，不记录 `credentials`、`access_token`、`refresh_token`、`id_token`、`password`、`authorization` 等字段。

开发时如果确实要定位某个具体 JSON 解析问题，应优先：

- 记录数量、哈希、账号邮箱、批次 ID、错误位置。
- 不把 token 原文写入日志。
- 必要时只在本地临时调试，调试完成后删除日志。

## 日志文件

默认目录：

```text
logs/
```

默认文件：

```text
logs/app.log
logs/access.log
logs/error.log
```

含义：

| 文件 | 内容 |
| --- | --- |
| `app.log` | 应用生命周期、业务服务日志、后台任务日志 |
| `access.log` | 请求开始、请求结束、状态码、耗时、慢请求标记 |
| `error.log` | ERROR 级别异常和错误栈 |

## 环境变量

```env
LOG_PROFILE=development
LOG_LEVEL=DEBUG
LOG_DIR=logs
LOG_RETENTION_DAYS=14
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=10
LOG_REQUEST_BODY=false
LOG_SLOW_REQUEST_MS=1000
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `LOG_PROFILE` | `development` 更详细；后续可改为 `production` 降低控制台和访问日志噪音 |
| `LOG_LEVEL` | 应用日志级别，开发默认 `DEBUG` |
| `LOG_DIR` | 日志目录，相对路径从项目根目录计算 |
| `LOG_RETENTION_DAYS` | 超过多少天的日志文件自动删除 |
| `LOG_MAX_BYTES` | 单个日志文件最大体积，超过后轮转 |
| `LOG_BACKUP_COUNT` | 每类日志最多保留多少个轮转文件 |
| `LOG_REQUEST_BODY` | 保留开关，但当前为了 token 安全不捕获正文 |
| `LOG_SLOW_REQUEST_MS` | 超过该耗时的请求标记为慢请求并提升为 WARNING |

## 开发期策略

开发期默认：

```env
LOG_PROFILE=development
LOG_LEVEL=DEBUG
```

记录：

- 请求开始：method、path、query、client、content_length、user_agent。
- 请求结束：method、path、status_code、elapsed_ms、slow。
- 异常：完整 Python stack trace。
- 生命周期：启动、索引初始化前后、关闭。
- 后台任务：后续新增任务应使用 `logging.getLogger("app")` 记录关键步骤。

## 并发期策略

后续功能稳定、并发上来后，建议切换：

```env
LOG_PROFILE=production
LOG_LEVEL=INFO
LOG_RETENTION_DAYS=7
LOG_MAX_BYTES=52428800
LOG_BACKUP_COUNT=5
LOG_SLOW_REQUEST_MS=2000
```

并发期原则：

- access 日志保留请求结束，不必记录请求开始。
- 业务日志只记录关键状态变化、失败和慢操作。
- 高频循环任务只记录汇总，不逐账号刷屏。
- 保留 `request_id`，便于用户反馈问题时快速检索。

## 自动清理

应用启动时会立即清理一次旧日志。

应用运行中会启动一个后台任务，每 24 小时清理一次 `LOG_DIR` 下超过 `LOG_RETENTION_DAYS` 的 `*.log*` 文件。

单个日志文件通过 `RotatingFileHandler` 控制大小，超过 `LOG_MAX_BYTES` 后自动轮转，最多保留 `LOG_BACKUP_COUNT` 个备份。

## 后续扩展

后续可以继续增加：

- 前端错误上报接口：捕获页面运行错误并写入后端 `frontend_error.log` 或 MongoDB。
- 操作链路日志：将 `pool_actions` 和普通日志通过 `request_id` 关联。
- 结构化 JSON 日志：生产环境如需接入 ELK、Loki、Grafana，可把 formatter 切换为 JSON。
- 按模块开关日志：例如 sub2api 同步、容量计算、验证流程单独设置 log level。
