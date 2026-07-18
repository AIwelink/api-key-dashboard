# Roadmap

本文只维护方向级状态，不重复接口和公式。具体实现以 [14-development-guide.md](./14-development-guide.md)、[29-agent-ops-observability-and-notifications.md](./29-agent-ops-observability-and-notifications.md) 和 [30-api-pool-realtime-capacity-and-presence.md](./30-api-pool-realtime-capacity-and-presence.md) 为准。

## 已落地基础

- FastAPI、MongoDB、React/TypeScript 管理端。
- 登录、角色权限、用户管理、系统 Token 和审计。
- 账号 CRUD、上传批次、导入预览、JSON 更新和账号操作记录。
- 本地账号生命周期、问题处理、free 升级 plus 和账号复活。
- 多 sub2api 站点、groups/accounts/usage 缓存和站点级刷新配置。
- API 账号池状态、完整 group 统计、5h/7d 容量和并发容量。
- group TPM/RPM 分钟采样、实时风险、容量 5 分钟样本和补号建议。
- 分组探测、事件、异常告警、Uptime Kuma 与通知通道。
- 钉钉、Telegram、飞书通知配置和测试。
- newapi/sub2api 客户站点配置。
- 页面级 60 秒静默刷新、移动端 API 状态首页和前台在线统计。
- Agent 分析、任务闭环、事件触发、巡检、记忆和评测工作台。

## 当前维护重点

- 用 5 分钟容量样本回测实时风险、补号建议和通知阈值。
- 校验不同账号类型的额度配置、缺失 `plan_type` 回退和 Bug Team 排除口径。
- 提升远端大账号池刷新稳定性、失败隔离和同步可观测性。
- 完善账号变更历史、旧数据迁移和本地/远端身份一致性。
- 收敛历史文档，保证接口、公式、权限和测试只有一个现行来源。
- 对移动端 API 账号池状态页持续做信息密度和性能验证。

## 后续候选

- 凭证字段加密、密钥轮换和导出审批。
- 多实例部署所需的分布式锁、任务队列和 leader election。
- 更完整的 RBAC、速率限制和敏感操作二次确认。
- 备份恢复演练、容量样本归档和长期数据仓库。
- 基于回测结果调整容量目标、通知冷却和 Agent 决策边界。

候选项不是承诺排期。新增实施计划前应先确认现有代码、样本数据和运维目标，避免重新引入已取消的备用池容量或旧峰值告警模型。
