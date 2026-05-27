# Sub2API Admin API Integration Notes

本文记录 2026-05-25 对测试 sub2api 站点的实测结果，用于后续开发多站点配置、账号池同步和自动补位功能。

## Test Instance

```text
base_url: http://216.167.70.204:5002
admin_page_groups:   http://216.167.70.204:5002/admin/groups
admin_page_accounts: http://216.167.70.204:5002/admin/accounts
api_prefix: http://216.167.70.204:5002/api/v1/admin
```

认证方式：

```http
x-api-key: <sub2api-admin-api-key>
```

本项目 `.env` 中暂时使用：

```text
SUB2API_BASE_URL=http://216.167.70.204:5002
SUB2API_TOKEN=<sub2api-admin-api-key>
```

后续多站点接入时，不应继续只依赖全局 `.env`，应改为数据库配置。

## Response Shape

sub2api Admin API 的典型响应：

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

列表接口的 `data` 通常包含：

```json
{
  "items": [],
  "total": 0,
  "page": 1,
  "page_size": 50,
  "pages": 1
}
```

## Groups API

已实测接口：

```text
GET  /api/v1/admin/groups?page=1&page_size=50
POST /api/v1/admin/groups
PUT  /api/v1/admin/groups/{id}
```

测试站点原有分组：

```text
id=2, name=free 账户池 01
id=3, name=plus 账号池 01
```

本次创建的测试分组：

```text
id=4, name=plus 账号池 02
```

创建 `plus 账号池 02` 时，以 `plus 账号池 01` 为源，复制全部业务字段，排除服务端生成和统计字段：

```text
id
created_at
updated_at
account_count
active_account_count
rate_limited_account_count
```

字段对比结果：

```json
{
  "source_id": 3,
  "created_id": 4,
  "created_name": "plus 账号池 02",
  "business_field_diff": {}
}
```

### Group Payload Pattern

创建 group 时可沿用列表返回的大部分业务字段：

```json
{
  "name": "plus 账号池 02",
  "description": "",
  "platform": "openai",
  "rate_multiplier": 1,
  "is_exclusive": false,
  "status": "active",
  "subscription_type": "standard",
  "daily_limit_usd": 0,
  "weekly_limit_usd": 0,
  "monthly_limit_usd": 0,
  "allow_image_generation": true,
  "image_rate_independent": true,
  "image_rate_multiplier": 1,
  "image_price_1k": 2,
  "image_price_2k": 3,
  "image_price_4k": 4,
  "claude_code_only": false,
  "fallback_group_id": null,
  "fallback_group_id_on_invalid_request": null,
  "allow_messages_dispatch": true,
  "require_oauth_only": false,
  "require_privacy_set": false,
  "rpm_limit": 0,
  "model_routing": {},
  "model_routing_enabled": false,
  "mcp_xml_inject": true,
  "default_mapped_model": "",
  "messages_dispatch_model_config": {
    "opus_mapped_model": "gpt-5.5",
    "sonnet_mapped_model": "gpt-5.4",
    "haiku_mapped_model": "gpt-5.4-mini"
  },
  "supported_model_scopes": [],
  "sort_order": 0
}
```

## Accounts API

已实测接口：

```text
GET  /api/v1/admin/accounts?page=1&page_size=3
GET  /api/v1/admin/accounts?group_id=3
GET  /api/v1/admin/accounts?group_id=3&status=active
GET  /api/v1/admin/accounts/{id}
POST /api/v1/admin/accounts
```

账号可以通过 `group_id` 查询：

```text
GET /api/v1/admin/accounts?group_id=3
```

账号创建到指定分组时，在请求 body 中传：

```json
{
  "group_ids": [4]
}
```

本次测试从 `plus 账号池 01` 复制了一个正常账号到 `plus 账号池 02`。

源账号：

```text
id=833
name=0521-自产plus一卡一号-x10
status=active
schedulable=true
group_ids=[3]
```

新账号：

```text
id=852
name=0521-自产plus一卡一号-x10 - plus02 copy test
status=active
schedulable=true
group_ids=[4]
group=plus 账号池 02
```

验证：

```text
GET /api/v1/admin/accounts/852
返回 group_ids=[4]

GET /api/v1/admin/accounts?group_id=4
可以查到 id=852
```

## Account Payload Pattern

从已有账号复制创建时，可以使用这些字段作为基础：

```json
{
  "name": "account name",
  "platform": "openai",
  "type": "oauth",
  "credentials": {},
  "extra": {},
  "proxy_id": null,
  "concurrency": 10,
  "load_factor": 10,
  "priority": 100,
  "rate_multiplier": 1,
  "status": "active",
  "expires_at": null,
  "auto_pause_on_expired": true,
  "schedulable": true,
  "notes": null,
  "group_ids": [4],
  "confirm_mixed_channel_risk": true
}
```

不要从源账号复制这些字段：

```text
id
created_at
updated_at
last_used_at
error_message
credentials_status
current_concurrency
rate_limited_at
rate_limit_reset_at
overload_until
temp_unschedulable_until
temp_unschedulable_reason
session_window_start
session_window_end
session_window_status
account_groups
groups
```

实际创建成功后，sub2api 会重新生成：

```text
id
created_at
updated_at
last_used_at
current_concurrency
```

## Encoding Note

PowerShell 直接发送中文 JSON 时，曾出现分组名称被写成 `plus ??? 02` 的情况。后续开发应避免依赖 shell 默认编码。

建议在后端代码中使用 `httpx` 并明确发送 UTF-8 JSON：

```python
import json
import httpx

payload = {"name": "plus 账号池 02"}
headers = {
    "x-api-key": api_key,
    "Content-Type": "application/json; charset=utf-8",
}

response = await client.post(
    f"{base_url}/api/v1/admin/groups",
    headers=headers,
    content=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
)
```

如果使用 `httpx` 的 `json=payload` 参数，通常也可以正常工作；但涉及中文名称的关键路径，建议加集成测试确认。

## Development Recommendations

### 1. Multi-Site Config

后续应新增 `sub2api_sites` 配置，而不是继续只用 `.env`。

建议字段：

```js
{
  _id,
  name,
  base_url,
  api_key_encrypted,
  status: "active" | "disabled",
  last_tested_at,
  last_test_result,
  created_at,
  updated_at
}
```

### 2. Local Pool Mapping

本系统的账号池应映射到 sub2api site + group：

```js
{
  _id,
  name,
  site_id,
  sub2api_group_id,
  sub2api_group_name,
  account_type: "free" | "plus" | "pro" | "other",
  replacement_strategy,
  status
}
```

### 3. Account Mapping

本地账号同步到 sub2api 后，需要保存远端账号 id 和 group：

```js
metadata: {
  sub2api_site_id,
  sub2api_account_id,
  sub2api_group_ids: [4],
  sub2api_status,
  sub2api_schedulable,
  last_checked_at,
  last_sync_at,
  last_sync_error
}
```

### 4. Sync Flow

推荐同步流程：

```text
1. GET /groups 拉取远端分组，刷新本地站点分组缓存。
2. GET /accounts 拉取远端账号状态，本项目按 `group_ids` / `groups` / `account_groups` 在后端过滤 group。
3. 用 sub2api_account_id 优先匹配本地账号。
4. 无远端 id 时，用 email / account hash 做辅助匹配。
5. 生成 diff：create / update / bind_group / unbind_group / skip / error。
6. 预览 diff 后再执行。
7. 执行结果写入 sync_jobs / sync_events / audit_logs。
```

### 5. Replacement Flow

账号封禁或不可调度时：

```text
1. 从 sub2api 读取 active group 的账号状态。
2. 发现 status!=active 或 schedulable=false。
3. 按本地账号池策略选择 reserve 账号。
4. POST /accounts 创建到目标 group_ids。
5. 记录旧账号和新账号的替换关系。
6. 必要时调用 sub2api 的状态恢复、清错、禁用调度接口处理旧账号。
```

### 6. Idempotency

创建账号前应先检查：

```text
sub2api_account_id 是否已存在
email 是否已在目标 group 中存在
account_json hash 是否已经推送过
```

避免自动补位任务重复创建账号。

## Confirmed Facts

```text
Admin API 使用 x-api-key。
/api/v1/admin/groups 可以创建和更新 group。
/api/v1/admin/accounts 返回账号列表和 group 归属字段；本项目不依赖远端 `group_id` 参数，统一在后端按账号归属字段过滤。
创建账号时传 group_ids 可以绑定到指定 group。
账号必须绑定 group 才能被该 group 调用。
```

## Current Project Implementation

当前项目已经基于上述实测完成 API 账号池状态功能：

```text
backend/app/services/sub2api.py        sub2api Admin API 封装，使用 x-api-key
backend/app/services/sub2api_cache.py  groups/accounts MongoDB 缓存、刷新防抖锁、后台刷新
backend/app/routers/sub2api_sites.py   前端读取站点、groups、accounts 和触发刷新
frontend/src/pages/ApiPoolStatusPage.tsx API 账号池状态页面
```

当前刷新语义：

```text
同步账号池数据：远程 sub2api -> MongoDB cache -> 前端重新读取当前账号池
前端数据刷新：MongoDB cache -> 前端，不访问远程 sub2api
页面加载/切换账号池：只读 MongoDB cache，不触发远程刷新
```

缓存刷新：

```text
默认 5 分钟后台刷新
站点级 refresh_interval_minutes 可配置
同站点刷新请求有 3 秒防抖锁
当前不使用 Redis
```

前端切换账号池时，账号表使用 `siteId:groupId:page:pageSize:statusFilter` key 避免旧请求覆盖当前账号池；总体容量读取当前 group 的后端 `capacity_summary`，不能按当前页账号重新计算。

## Open Items

后续还需要继续实测：

```text
PUT /api/v1/admin/accounts/{id} 的更新字段
DELETE /api/v1/admin/accounts/{id} 的行为是硬删还是软删
POST /api/v1/admin/accounts/{id}/schedulable 的请求体格式
POST /api/v1/admin/accounts/data 的批量导入格式
GET /api/v1/admin/ops/account-availability 的返回结构
```
