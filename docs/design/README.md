# API Key Backend Admin Panel - Design Docs

这个目录用于沉淀项目设计。当前阶段先讨论架构和边界，不急着写业务代码。

## 项目目标

团队内部需要协作管理 OpenAI API key / ChatGPT OAuth 账号凭据，并把这些账号同步到 sub2api。系统需要支持添加、更新、删除、批量导入、状态检查、自动暂停、并发和优先级调整，同时保留审计记录。

## 文档索引

- [01-product-scope.md](./01-product-scope.md): 产品范围、角色、核心流程。
- [02-system-architecture.md](./02-system-architecture.md): 系统架构、后端模块、后台任务。
- [03-data-model.md](./03-data-model.md): MongoDB 集合设计和字段约定。
- [04-api-design.md](./04-api-design.md): 后端 API 草案。
- [05-sub2api-sync.md](./05-sub2api-sync.md): sub2api 同步模型和差异计算。
- [06-security.md](./06-security.md): 明文存储风险、权限和审计。
- [07-frontend-design.md](./07-frontend-design.md): 前端页面、交互和状态展示。
- [08-roadmap.md](./08-roadmap.md): MVP 到后续版本的开发路线。
- [09-open-questions.md](./09-open-questions.md): 暂未确定的问题。
- [10-json-contract.md](./10-json-contract.md): sub2api JSON 结构不可变契约。
- [11-user-management.md](./11-user-management.md): 后台用户管理、登录和不开放注册策略。
- [12-account-fields.md](./12-account-fields.md): 每个上传账号需要保存和展示的字段规范。
- [13-sub2api-admin-api-integration.md](./13-sub2api-admin-api-integration.md): sub2api 管理接口集成记录。
- [14-development-guide.md](./14-development-guide.md): 当前前后端架构、代码分层和新功能开发约定。
- [15-api-pool-status-cache.md](./15-api-pool-status-cache.md): API 账号池状态页面、MongoDB 缓存、前端缓存和刷新语义。
- [16-account-pool-lifecycle-backend.md](./16-account-pool-lifecycle-backend.md): 账号总库、验证、备用池、实际使用池、问题退回、容量计划和待办的后端设计。
- [17-account-pool-lifecycle-simple.md](./17-account-pool-lifecycle-simple.md): 账号池生命周期的最小可落地版本，作为近期开发优先参考。
- [18-account-pool-simple-logic-analysis.md](./18-account-pool-simple-logic-analysis.md): 简化版账号池设计的场景推演、逻辑风险和修正建议。
- [19-account-pool-final-simple-design.md](./19-account-pool-final-simple-design.md): 合并简化版和风险修正后的账号池近期实现主设计。
- [20-account-pool-implementation-priority.md](./20-account-pool-implementation-priority.md): 账号池功能开发优先级、任务顺序和里程碑。
- [21-logging-system.md](./21-logging-system.md): 开发期详细日志、生产期精简日志、请求追踪、日志轮转和自动清理设计。
- [22-todo-free-to-plus.md](./22-todo-free-to-plus.md): free 升 plus 待办与处理、候选规则、分布式锁和完成升级写入规则。
- [23-initial-release-deployment.md](./23-initial-release-deployment.md): 初版上线部署汇总、服务器环境变量、构建启动、Nginx、检查和回滚清单。
- [24-sub2api-manual-push-verify.md](./24-sub2api-manual-push-verify.md): 手动推送本地账号到 sub2api 分组、执行可用性测试并写回数据库的设计。
- [25-sub2api-account-return.md](./25-sub2api-account-return.md): 从 sub2api 手动删除远端账号、回退到本地可用池或总库、保留快照和审计的设计。
- [26-remote-ui-and-verification-group.md](./26-remote-ui-and-verification-group.md): 远端 sub2api 测试控制台 UI、专用验证分组、手动/自动验证流程设计。
- [27-release-0.2.0-project-check.md](./27-release-0.2.0-project-check.md): `0.2.0` 版本标记、整体项目检查结果、已知风险和上线前动作。
- [30-api-pool-realtime-capacity-and-presence.md](./30-api-pool-realtime-capacity-and-presence.md): 当前 API 账号池实时容量、并发总覆盖、前端分级、悬浮说明和前台在线接口实现约定。
- [菜单.md](./菜单.md): 左侧菜单、页面职责、当前功能和对应接口。
- [账号上传界面.md](./账号上传界面.md): 上传账号页面模式、字段顺序和参数名。

## 当前设计原则

1. MVP 阶段采用简化存储：一个账号一个 MongoDB 文档，包含 `account_json` 和 `metadata`。
2. sub2api 同步使用“期望状态 vs 观测状态”的 reconciliation 模型。
3. MVP 阶段优先使用 MongoDB，Redis 作为后续扩展选项。
4. 所有批量操作先预览 diff，再确认执行。
5. 所有敏感操作必须进入审计日志。
6. sub2api JSON 是导出和推送的外部契约，不能修改字段结构；`account_json` 必须原样保存和导出。
7. 系统需要登录后使用，不开放公开注册；用户只能由后台管理员创建或后续通过受控邀请创建。
8. 每个上传账号需要区分 `account_json.extra` 和根级 `metadata`：前者属于 sub2api 原始 JSON，后者属于本系统管理信息。
9. 当前实现采用 Python FastAPI 后端 + Vite React TypeScript 前端；新增功能应沿用 router/service/schema 和 page/api/utils/types 的分层。
10. API 账号池状态和账号池逻辑管理默认读取同一份 MongoDB 缓存；只有“同步账号池数据”和后台定时刷新会访问远程 sub2api。
11. 后续账号池自动化以本地生命周期为准：新账号先进入总库，老账号/问题账号可验证后进入备用池，实际使用池和 sub2api group 通过本地池配置映射。
12. 近期账号池开发优先采用简化版：核心状态只保留 `library`、`reserve`、`active`、`problem`、`discarded`，复杂信号先进入 `metadata.analysis`。
13. 账号池第一版实现以最终简化设计为准：`pool_status` 是当前状态唯一来源，推送必须加锁，容量检查必须读取完整 sub2api 缓存，待办必须去重。
14. `0.2.0` 版本以后，sub2api 相关动作默认仍是人工触发：手动推送、手动测试、手动删除、手动回退；自动调度和 agent 决策后续再启用。
