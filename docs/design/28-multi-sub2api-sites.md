# 多 sub2api 站点接入设计

## 当前目标

系统支持同时接入多个 sub2api Admin API 站点。测试环境新增站点：

```text
id: api-5003
name: sub2api 5003
base_url: http://216.167.70.204:5003
auth: x-api-key
```

密钥只写入根目录 `.env`，不写入文档和代码仓库。

## 环境变量

默认站点仍使用旧配置：

```env
SUB2API_BASE_URL=http://216.167.70.204:5002
SUB2API_TOKEN=<sub2api-admin-api-key>
```

额外站点使用 JSON 数组：

```env
SUB2API_SITES_JSON='[{"id":"api-5003","name":"sub2api 5003","base_url":"http://216.167.70.204:5003","token":"<sub2api-admin-api-key>"}]'
```

`SUB2API_SITES_JSON` 中的每个站点字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 系统内部站点 ID，必须唯一 |
| `name` | 前端显示名称 |
| `base_url` | sub2api 根地址，不包含 `/api/v1/admin` |
| `token` | Admin API key，发送为 `x-api-key` |
| `status` | 可选，默认 `active` |
| `auto_remove_abnormal_accounts` | 可选，是否自动从该站点移除异常账号，默认 `false` |

## 后端行为

- `GET /api/sub2api-sites` 返回默认站点和所有 `SUB2API_SITES_JSON` 站点，不返回 token。
- `POST /api/sub2api-sites/{site_id}/test` 使用对应站点的 URL/token 测试连接。
- `POST /api/sub2api-sites/{site_id}/refresh` 只刷新该站点的 groups/accounts 缓存。
- 缓存主键带 `site_id`，避免不同站点的 group/account id 冲突。
- 推送、验证、远端账号删除、远端账号测试都必须显式使用 `site_id`。
- 账号进入使用备选池时，前端会把选中的 `site_id` 和 `group_id` 一起传给后端，后端写入：

```text
metadata.sub2api_site_id
metadata.sub2api_group_id
metadata.sub2api_group_name
metadata.pool_ref_type = sub2api_group
```

## 前端行为

- API 账号池状态页、账号池逻辑管理页、可用池/使用备选池页读取 `/api/sub2api-sites`。
- 用户可以切换站点后刷新 groups。
- 加入使用备选池时，目标站点和目标分组一起固定到账号 metadata。
- 使用备选池推送时优先使用账号 metadata 中保存的 `sub2api_site_id` 和 `sub2api_group_id`。

## 切换点检查

| 场景 | 当前行为 |
| --- | --- |
| API 账号池状态 | 可切换站点；测试连接、远端刷新、groups、group accounts、远端测试、远端删除都使用当前 `site_id` |
| 账号池逻辑管理 | 可切换站点；groups 和当前备选账号汇总按当前 `site_id` 读取 |
| 可用池加入使用备选池 | 选择站点和 group 后写入 `metadata.sub2api_site_id`、`metadata.sub2api_group_id` |
| 使用备选池 | 按当前 `site_id` 过滤 reserve 账号；推送时优先使用账号上保存的 `sub2api_site_id` |
| 手动推送 sub2api | 请求体带 `site_id`；后端按对应站点 token 创建账号 |
| 账号验证 | 请求体带 `site_id`；临时推送、测试、清理都在同一站点执行 |
| 远端账号测试 | URL 中带 `site_id`；回写本地测试结果时同时匹配 `sub2api_site_id` 和 `sub2api_account_id` |
| 远端删除并退回本地 | URL 中带 `site_id`；删除、缓存刷新、本地账号匹配都限定在同一站点 |
| 后台缓存刷新 | scheduler 遍历所有 active 站点，每个站点独立刷新 |
| 旧 settings/sub2api | 只代表默认站点；后续不作为多站点入口 |

## 自动移除异常账号

每个 sub2api 站点有独立开关：

```text
sub2api_sites.auto_remove_abnormal_accounts: boolean
```

开启后，站点同步流程为：

```text
1. 拉取远端 groups/accounts/usage
2. 写入 MongoDB 缓存
3. 识别明确异常账号
4. 把远端账号快照写入本地 accounts
5. 本地账号进入 problem 状态，也就是异常账号库
6. 从对应 sub2api 站点删除远端账号
7. 记录自动移除摘要到 sub2api_cache_meta
```

异常账号不会包含临时限流账号。以下情况才会自动移除：

```text
status in error / failed / banned / disabled / invalid
schedulable == false 且 status 不是 active / warning
error_message 存在且不是 429 / 529 / rate limit 类临时限流
```

本地账号会记录这些 metadata：

```text
pool_status = problem
sub2api_delete_mode = auto_abnormal
abnormal_auto_removed = true
abnormal_auto_removed_at
abnormal_auto_remove_reason
abnormal_detected_at
abnormal_status
abnormal_schedulable
abnormal_error_message
abnormal_reason
abnormal_usage_snapshot
abnormal_remote_snapshot
sub2api_return_snapshot
```

`abnormal_usage_snapshot` 保存同步时可见的用量字段，包括 5h/7d 使用比例、调用次数、token、成本、最后使用时间、并发、状态和错误信息。完整远端账号快照保存在 `abnormal_remote_snapshot` 与 `sub2api_return_snapshot`。

## 跨站重复账号处理

跨站重复账号是允许出现的远端现实状态，但本地系统必须明确处理策略：

1. 远端缓存层允许重复  
   `sub2api_accounts_cache` 主键是 `{site_id}:{sub2api_account_id}`，所以不同站点的同一个账号不会互相覆盖。

2. 远端账号 ID 不能单独作为身份  
   所有测试、删除、回写都必须同时带 `site_id` 和 `sub2api_account_id`。不能只用远端 ID 查本地账号。

3. 本地业务账号当前是一对一远端绑定  
   现阶段 `metadata.sub2api_site_id`、`metadata.sub2api_account_id`、`metadata.sub2api_group_id` 表示当前唯一有效绑定。已经绑定到某站点且未删除的账号，不应再直接推送到另一个站点。

4. 推荐后续升级为绑定表  
   如果业务上需要“同一个本地账号同时存在于多个 sub2api 站点”，应新增独立集合，而不是继续往 metadata 里塞更多字段：

```js
sub2api_account_bindings
{
  _id: "{local_account_id}:{site_id}:{remote_account_id}",
  local_account_id,
  site_id,
  remote_account_id,
  group_ids,
  status,
  schedulable,
  last_seen_at,
  last_test_status,
  created_at,
  updated_at
}
```

5. 重复账号识别键  
   跨站重复扫描按以下优先级生成 identity：

```text
credentials.chatgpt_account_id
credentials.email
account_json.extra.email
name
```

6. 操作策略  
   - 如果只是远端已有重复：先在远端状态页观察，不自动删除。
   - 如果准备把本地账号推到某站点：先查该站点是否已有相同 identity；有则绑定或提示重复，不重复创建。
   - 如果同一 identity 出现在多个站点：页面应标注“跨站重复”，由人工决定保留哪个站点、是否删除多余远端账号、是否退回总库。

## 注意事项

- 不同 sub2api 站点可能有相同的远端账号 id 或 group id，所有查询和回写都必须带 `site_id`。
- `.env.example` 只能放占位 token。
- 旧的 `/api/settings/sub2api` 只代表默认站点；多站点操作以 `/api/sub2api-sites` 为准。
## 生产站点 5001

生产环境 sub2api 站点已按额外站点接入：

```text
id: api-5001
name: sub2api 5001
base_url: http://216.167.70.204:5001
token: 由运维在根目录 .env 中填写
```

注意：5001 是生产环境，`.env.example` 只保留占位 token；本地 `.env` 中 `api-5001.token` 先留空，填写后需要重启后端。
