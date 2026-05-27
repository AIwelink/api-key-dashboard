# Roadmap

## Phase 0 - Design

- 明确账号类型：OpenAI API key、ChatGPT OAuth，或两者都支持。
- 明确 sub2api 接口能力。
- 明确权限模型。
- 明确同步策略。
- 明确 Redis 是否进入 MVP。

## Phase 1 - Backend MVP

- 已完成：初始化后端项目。
- 已完成：接入 MongoDB。
- 已完成：用户登录和角色权限。
- 已完成：后台用户管理，不开放公开注册。
- 已完成：accounts CRUD。
- 已完成：accounts 单文档存储：`account_json + metadata`。
- 已完成：audit_logs。
- 已完成：JSON 导入 preview / commit。
- 已完成：sub2api client 初版。
- 已完成：sub2api sites/groups/accounts 统一缓存读取和账号池数据同步。
- 手动同步单账号和全部账号。

## Phase 2 - Frontend MVP

- 已完成：初始化前端项目。
- 已完成：登录页。
- 已完成：后台用户管理页。
- 已完成：账号列表。
- 账号详情。
- 已完成：添加和编辑账号。
- 已完成：批量导入预览。
- 已完成：同步中心初版。
- 已完成：API 账号池状态页面。
- 设置页。
- 已完成：审计日志页。

## Phase 3 - Automation

- 已完成：API 账号池缓存默认 5 分钟定时刷新，可按站点配置刷新间隔。
- 定时同步本地账号到 sub2api。
- 过期自动暂停。
- 失败重试。
- 同步任务历史。
- 通知和告警。

## Phase 4 - Hardening

- 更完整的 RBAC。
- 密钥轮换。
- 导出控制。
- 审计日志筛选。
- 操作速率限制。
- 备份恢复方案。

## Phase 5 - Scale

- 引入 Redis。
- BullMQ 后台队列。
- 多实例部署。
- WebSocket 实时任务状态。
- 多 sub2api 实例。
