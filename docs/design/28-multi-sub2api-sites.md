# 多 sub2api 站点契约

本文记录当前多 sub2api 站点的数据归属、接口和账号身份边界。缓存刷新细节见 [15-api-pool-status-cache.md](./15-api-pool-status-cache.md)，客户侧 newapi/sub2api 站点不属于本集合，见本文第 7 节。

## 1. 站点来源

sub2api 账号池站点保存在 MongoDB `sub2api_sites`，通过站点配置页面维护。系统不再用 `SUB2API_SITES_JSON` 作为运行时多站点来源，也不存在固定默认生产站点。

```js
{
  _id: "<site_id>",
  name,
  base_url,
  site_type: "sub2api",
  token,
  status: "active" | "disabled" | "deleted",
  refresh_interval_minutes,
  auto_remove_abnormal_accounts,
  uptime_kuma_url,
  uptime_kuma_api_key,
  source: "database",
  created_at,
  updated_at
}
```

约束：

- `site_id` 是本系统稳定标识，创建后不通过改名迁移。
- `base_url` 只保存站点根地址，不包含 `/api/v1/admin`。
- Admin API 使用 `x-api-key`。
- API 响应删除明文 `token` 和 `uptime_kuma_api_key`，只返回 `*_configured`。
- `status=disabled` 的站点可以保留配置和历史缓存，但不参与定时远端刷新。
- 删除站点为软删除；历史账号、事件和采样仍保留 `site_id` 归属。

## 2. 管理接口

```http
GET    /api/sub2api-sites?site_type=sub2api
POST   /api/sub2api-sites
PATCH  /api/sub2api-sites/{site_id}
DELETE /api/sub2api-sites/{site_id}
POST   /api/sub2api-sites/{site_id}/test
POST   /api/sub2api-sites/{site_id}/refresh
GET    /api/sub2api-sites/{site_id}/groups
GET    /api/sub2api-sites/{site_id}/groups/{group_id}/accounts
```

- 新增和删除站点：`owner` / `admin`。
- 读取、连接测试和同步：`owner` / `admin` / `maintainer`。
- 所有写操作进入审计，审计不得包含明文密钥。

## 3. 缓存标识

不同站点可能拥有相同的 group ID 和 remote account ID。任何缓存、操作、事件和回写必须使用复合身份：

```text
group:   site_id + group_id
account: site_id + sub2api_account_id
```

MongoDB 缓存 ID：

```text
sub2api_groups_cache._id   = "{site_id}:{group_id}"
sub2api_accounts_cache._id = "{site_id}:{sub2api_account_id}"
```

禁止：

- 只按 `group_id` 查询多个站点的 group。
- 只按 remote account ID 回写本地账号。
- 从当前 UI 选择的站点猜测历史记录所属站点。
- 把一个站点的缓存刷新结果写入另一个站点的 group 汇总。

## 4. 本地账号绑定

本地账号当前使用 metadata 保存单个有效远端绑定：

```text
metadata.sub2api_site_id
metadata.sub2api_account_id
metadata.sub2api_group_id
metadata.sub2api_group_name
metadata.pool_ref_type = sub2api_group
```

所有推送、测试、删除、退回、复活和凭证应用必须从请求路径或本地绑定获得 `site_id`。如果账号绑定站点与请求站点冲突，后端应拒绝或执行明确的重新绑定流程，不能静默覆盖。

如果未来允许一个本地账号同时绑定多个远端站点，应新增独立绑定集合：

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
  created_at,
  updated_at
}
```

在该迁移完成前，不要向 metadata 追加第二组平行站点字段。

## 5. 账号身份与重复检测

业务匹配只允许以下两种依据：

1. 已保存且一致的 `site_id + sub2api_account_id` 绑定。
2. 规范化后的 `credentials.email`。

邮箱规范化至少执行去除首尾空白和转小写。`name` 只是方便查看的命名，同一批账号可以完全同名，不能用于本地恢复、覆盖、绑定、删除退回或跨站重复判断。

以下字段也不能在缺少明确迁移规则时替代 `credentials.email`：

- `account_json.extra.email`。
- 上传批次名称。
- 远端显示名称。
- 单独的 `chatgpt_account_id`。

如果远端账号缺失 `credentials.email` 且没有本地绑定，系统应标记为无法自动匹配，由人工处理，不能退化为按名称猜测。

## 6. 跨站行为

| 场景 | 约定 |
| --- | --- |
| API 账号池状态 | 当前站点决定 groups、accounts、dashboard、容量和账号操作路径 |
| 后台缓存刷新 | scheduler 遍历所有 active sub2api 站点，不同站点独立刷新 |
| 账号推送 | 请求和本地 metadata 均保存目标 `site_id + group_id` |
| 远端测试 | URL 带 `site_id + account_id`；本地结果按绑定或邮箱回写 |
| 远端删除/退回 | 删除、使用快照、本地恢复和事件记录全部限定同一 `site_id` |
| OAuth 复活 | generate/exchange/apply/schedulable/recover-state 全程使用同一站点 client |
| 分组通知 | 通知配置键包含 `site_id + group_id`，同 ID 分组不会串站 |
| 容量采样 | 样本键和查询都包含 `site_id + group_id` |

同一邮箱出现在多个站点是允许观察到的远端状态。系统可以发出跨站重复提示，但不自动删除；人工操作必须明确选择站点和远端账号。

## 7. 客户站点与账号池站点

系统有两类“站点”，不要混用：

- **账号池 sub2api 站点**：本系统管理的远端账号池，存储于 `sub2api_sites`，接口 `/api/sub2api-sites`。
- **客户站点**：对接本系统的 newapi 或 sub2api 客户端，存储于 `client_sites`，接口 `/api/client-sites`；newapi 可以包含 `admin_user_id`。

`site_type=newapi` 不能写入 `sub2api_sites`。旧数据由启动迁移转入 `client_sites`，后续只通过客户站点页面维护。

## 8. 自动移除异常账号

`auto_remove_abnormal_accounts` 是站点级开关。开启后仍必须区分异常与临时限流：

- 401、token revoked、token invalidated、authentication failed、banned 等明确凭证/封禁错误可进入异常处理。
- 5h/7d 429、529 和仍有恢复时间的限流不是凭证异常。
- `schedulable=false` 本身不是异常；必须结合 `status` 和错误信息。

自动移除需要：

1. 保存远端账号、usage、最后使用时间和错误快照。
2. 按绑定或 `credentials.email` 恢复/更新本地账号。
3. 写入问题状态和系统操作人。
4. 删除同一 `site_id` 下的远端账号。
5. 记录账号操作、审计和刷新摘要。

任何步骤失败都应保留已获取快照并记录具体阶段，不能因为远端删除成功而丢失本地恢复线索。

## 9. 修改检查表

1. 新查询是否同时包含 `site_id`。
2. 账号身份是否只使用明确绑定或 `credentials.email`。
3. 前端切换站点后，旧请求是否可能覆盖当前 group。
4. 密钥是否只在写接口和服务端内存中出现。
5. 站点删除是否保留历史事件和采样可解释性。
6. 客户站点是否错误写入 `sub2api_sites`。
7. 自动异常处理是否把 429 或单独的 `schedulable=false` 误判为凭证错误。
