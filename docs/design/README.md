# API Key Dashboard 设计文档索引

本目录记录系统设计、已经落地的实现约定和历史方案。后续开发应先确认文档状态，不能把历史计划中的站点、阈值或目录结构直接当作当前实现。

## 文档状态规则

- **现行约定**：描述当前代码接口、字段、公式和维护边界，代码变更时必须同步更新。
- **基础设计**：仍可作为领域背景参考；如果与现行约定冲突，以现行约定和代码为准。
- **历史方案**：保留设计演进和迁移背景，不再作为新功能实现依据。
- `docs/superpowers/specs` 和 `docs/superpowers/plans` 是特定时间点的规格与实施计划。完成后应标记归档，不能覆盖现行实现文档。
- 文档中不要固定生产站点 ID、域名、账号数或密钥。站点和额度配置以 MongoDB 与管理页面为准。

## 现行约定

- [14-development-guide.md](./14-development-guide.md)：当前代码分层、页面路由、开发流程与验证命令。
- [15-api-pool-status-cache.md](./15-api-pool-status-cache.md)：多 sub2api 站点、MongoDB 缓存和刷新语义。
- [21-logging-system.md](./21-logging-system.md)：日志级别、请求追踪、生产日志和清理策略。
- [28-multi-sub2api-sites.md](./28-multi-sub2api-sites.md)：多 sub2api 站点及账号站点归属迁移约定。
- [29-agent-ops-observability-and-notifications.md](./29-agent-ops-observability-and-notifications.md)：Agent、事件、探测、通知和运维可观测性。
- [30-api-pool-realtime-capacity-and-presence.md](./30-api-pool-realtime-capacity-and-presence.md)：当前容量字段、实时风险公式、并发总覆盖、前端分级、账号排序和前台在线接口。容量与在线相关改动以本文为准。

## 基础设计

- [01-product-scope.md](./01-product-scope.md)：产品范围、角色和核心流程。
- [02-system-architecture.md](./02-system-architecture.md)：系统架构和后台任务边界。
- [03-data-model.md](./03-data-model.md)：MongoDB 核心集合与字段模型。
- [04-api-design.md](./04-api-design.md)：后端 API 基础约定。
- [05-sub2api-sync.md](./05-sub2api-sync.md)：sub2api 同步与 reconciliation 模型。
- [06-security.md](./06-security.md)：敏感数据、权限和审计要求。
- [07-frontend-design.md](./07-frontend-design.md)：管理端页面和交互基础约定。
- [08-roadmap.md](./08-roadmap.md)：当前能力、在建方向和长期事项。
- [09-open-questions.md](./09-open-questions.md)：仍需确认的产品和技术问题。
- [10-json-contract.md](./10-json-contract.md)：sub2api JSON 外部契约。
- [11-user-management.md](./11-user-management.md)：登录、角色和后台用户管理。
- [12-account-fields.md](./12-account-fields.md)：账号上传与管理字段。
- [13-sub2api-admin-api-integration.md](./13-sub2api-admin-api-integration.md)：sub2api Admin API 实测记录和本地封装入口。
- [31-newapi-data-api-integration.md](./31-newapi-data-api-integration.md)：NewAPI 模型用量、RPM/TPM 与用户统计接口的实测标准和待确认边界。

## 生命周期与功能记录

- [16-account-pool-lifecycle-backend.md](./16-account-pool-lifecycle-backend.md)：早期完整生命周期目标模型，已标记历史边界。
- [17-account-pool-lifecycle-simple.md](./17-account-pool-lifecycle-simple.md)：早期简化生命周期方案。
- [18-account-pool-simple-logic-analysis.md](./18-account-pool-simple-logic-analysis.md)：生命周期并发、状态和容量风险分析。
- [19-account-pool-final-simple-design.md](./19-account-pool-final-simple-design.md)：已落地生命周期字段的重要来源；容量与自动补号部分不再代表现状。
- [20-account-pool-implementation-priority.md](./20-account-pool-implementation-priority.md)：历史实施优先级。
- [22-todo-free-to-plus.md](./22-todo-free-to-plus.md)：free 升级 plus 待办流程。
- [23-initial-release-deployment.md](./23-initial-release-deployment.md)：`0.2.0` 初版部署记录，生产部署前还需核对当前 README 和环境配置。
- [24-sub2api-manual-push-verify.md](./24-sub2api-manual-push-verify.md)：手动推送与验证流程。
- [25-sub2api-account-return.md](./25-sub2api-account-return.md)：远端删除、快照和本地退回流程。
- [26-remote-ui-and-verification-group.md](./26-remote-ui-and-verification-group.md)：远端验证 UI 与验证分组设计。
- [27-release-0.2.0-project-check.md](./27-release-0.2.0-project-check.md)：`0.2.0` 版本检查记录。

## 其他入口

- [菜单.md](./菜单.md)：页面职责的历史汇总；当前菜单和英文路径以 `frontend/src/App.tsx` 为准。
- [账号上传界面.md](../需求/账号上传界面.md)：上传页字段、模式和交互需求。
- [../server-update-command.md](../server-update-command.md)：服务器更新命令。

## 维护要求

1. 新接口要记录请求方法、路径、权限、关键请求体和响应字段。
2. 新计算要记录输入口径、公式、等待数据语义、前端阈值和告警使用的是哪个字段。
3. 分页列表的统计必须说明是当前页还是完整集合；API 账号池容量始终来自完整 group 缓存。
4. 历史计划与现行实现冲突时，在历史文档顶部增加归档说明，不静默改写当时的设计背景。
5. 修改文档后运行 `git diff --check`，并检查相对链接存在。
