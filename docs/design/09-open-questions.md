# Open Questions

这些问题需要在进入代码实现前逐步确认。

## 账号类型

1. 项目只管理 OpenAI API key，还是同时管理 ChatGPT OAuth 账号？
2. OAuth 账号是否需要保存 refresh token？
3. 是否需要自动刷新 token，还是只同步已有 token？

## sub2api

已确认：

1. 当前通过 Admin API 调用，认证头为 `x-api-key`。
2. 已实测 `GET /groups`、`POST /groups`、`PUT /groups/{id}`。
3. 已实测 `GET /accounts`、`GET /accounts/{id}`、`POST /accounts`。
4. 已确认 groups/accounts 列表可支持 API 账号池状态页。
5. 已确认账号用量窗口可从 account 的 `extra` 字段读取并展示。

仍待确认：

1. `PUT /accounts/{id}` 的完整可更新字段。
2. `DELETE /accounts/{id}` 是硬删还是软删。
3. `POST /accounts/{id}/schedulable` 的请求体格式。
4. `POST /accounts/data` 的批量导入格式。
5. `GET /ops/account-availability` 的返回结构。
6. 失败响应格式是否在所有接口中稳定。

## 权限

1. 是否需要团队邀请？
2. 是否需要只读角色？
3. 谁可以修改 sub2api 配置？
4. 谁可以执行批量删除？
5. Admin 是否可以添加其他 Admin，还是只有 Owner 可以添加 Admin？
6. MVP 是否接邮件服务发送设置密码链接，还是先由管理员复制一次性链接？

## 同步策略

1. 过期账号是自动暂停，还是只标记异常？
2. 本地删除后，是否默认删除 sub2api 远端账号？
3. sub2api 被人手工改动后，是本地覆盖远端，还是提示冲突？
4. 是否需要 dry run 作为所有批量同步的必经步骤？

## Redis

已确认 MVP 不引入 Redis。当前使用 MongoDB 持久缓存和单进程内 3 秒刷新防抖锁。

后续如果需要多实例部署、分布式锁、可靠任务队列或 WebSocket/SSE 实时推送，再重新评估 Redis。
