# API 账号池状态与缓存设计

本文记录已经落地的 API 账号池状态功能，包括 sub2api 站点接入、MongoDB 缓存、前端缓存、刷新语义和当前 UI 行为。

## 已完成工作总结

- 已接入 sub2api Admin API，认证方式为请求头 `x-api-key`。
- 已完成测试站点 `sub2api 5002` 的站点配置读取，当前从 `.env` 获取 `SUB2API_BASE_URL` 和 `SUB2API_TOKEN`。
- 已实测并记录 groups/accounts 操作，创建了 `plus 账号池 02`，并从 `plus 账号池 01` 复制了一个账号到 `plus 账号池 02`。
- 已新增后端 `sub2api-sites` API，前端页面可查看远程 groups 和账号调度状态。
- 已新增 MongoDB 持久缓存，不使用 Redis。
- 已实现默认 5 分钟后台刷新间隔，可在前端按站点修改。
- 已实现 3 秒刷新防抖锁，同一站点短时间多次刷新请求会合并。
- 已实现“账号池数据同步”和“前端数据刷新”两个明确动作。
- 已实现前端页面缓存和账号池分页缓存，切换页面或切换账号池时尽量复用已加载数据。
- 已修复账号池切换闪烁问题：账号列表必须匹配当前查询 key 后才渲染，总体容量读取当前 group 的后端汇总。

## 后端接口

本系统对前端暴露的接口前缀为 `/api/sub2api-sites`：

```text
GET   /api/sub2api-sites
PATCH /api/sub2api-sites/{site_id}
POST  /api/sub2api-sites/{site_id}/test
POST  /api/sub2api-sites/{site_id}/refresh
GET   /api/sub2api-sites/{site_id}/groups
GET   /api/sub2api-sites/{site_id}/groups/{group_id}/accounts
```

语义：

- `GET /sub2api-sites` 返回当前可用 sub2api 站点，当前只有 `default`，展示名为 `sub2api 5002`。
- `PATCH /sub2api-sites/{site_id}` 当前用于保存 `refresh_interval_minutes`。
- `POST /test` 只测试 sub2api 连接。
- `POST /refresh` 从 sub2api 拉取 groups/accounts，写入 MongoDB 缓存。
- `GET /groups` 和 `GET /groups/{group_id}/accounts` 只读 MongoDB 缓存，不触发远程 sub2api 刷新。

权限：

- 读取和刷新：`owner` / `admin` / `maintainer`。
- 修改站点配置：`owner` / `admin`。

## MongoDB 缓存

当前缓存集合：

```text
sub2api_sites
sub2api_groups_cache
sub2api_accounts_cache
sub2api_cache_meta
```

缓存写入规则：

- group 缓存主键：`{site_id}:{group_id}`。
- account 缓存主键：`{site_id}:{sub2api_account_id}`。
- account 额外保存 `group_ids`、`status`、`schedulable`，便于按 group/status 查询。
- group 额外保存 `capacity_summary`，由后端按该 group 下完整账号集合计算，不按前端当前页计算。
- 本项目不依赖远端 `group_id` 参数过滤账号，刷新时拉取账号列表后按 `group_ids` / `groups` / `account_groups` 在后端归类。
- 每次刷新完成后删除该站点中已不存在的 group/account 缓存。
- `sub2api_cache_meta` 保存 `status`、`requested_at`、`started_at`、`last_refreshed_at`、`groups`、`accounts` 等刷新摘要。
- `API 账号池状态` 和 `账号池逻辑管理` 读取同一份 `sub2api_groups_cache` / `sub2api_accounts_cache`。后台不要拆出第二套分组同步、第二套账号缓存或第二个刷新任务；前端只决定展示哪些字段。
- “可用池”和“使用备选池”的手动目标分组也读取同一份 `sub2api_groups_cache`，账号流转时记录 `metadata.pool_ref_type = sub2api_group`、`metadata.sub2api_group_id` 和 `metadata.sub2api_group_name`。

刷新策略：

- 默认刷新间隔为 5 分钟。
- 后台 scheduler 每 30 秒检查一次是否到期。
- 手动刷新和后台刷新共用 3 秒防抖锁。
- 当前不引入 Redis；如果后续多实例部署，需要把锁和任务队列迁移到 Redis 或其他分布式机制。

## 前端页面行为

页面入口：`API 账号池状态`。

页面上方展示：

- API 站点选择。
- 站点 URL、密钥是否配置、最后刷新时间。
- 自动刷新分钟数配置。
- `测试连接`。
- `同步账号池数据`。

group 区域展示：

- 分组数、总账号、活跃账号、限流账号。
- group 下拉选择。
- 横向 group tab。

账号池区域展示：

- 当前 group 名称和 `前端数据刷新`。
- 状态筛选。
- 当前页、健康、警告、异常统计。
- `5h 总体容量` 和 `7d 总体容量`。
- 账号表格列：名称、平台/类型、容量、状态、调度、分组、用量窗口、最近使用、过期时间、操作。

刷新按钮语义：

- `同步账号池数据`：调用后端 `/refresh`，从 sub2api 同步 groups/accounts 到同一份 MongoDB 缓存，然后重新读取当前账号池缓存。完成后显示类似 `账号池数据同步完成：3 个分组，751 个账号`。
- `前端数据刷新`：只重新读取后端 MongoDB 缓存，不触发远程 sub2api。
- 页面首次加载、切换页面、切换账号池不会触发远程 sub2api 刷新。

前端缓存规则：

- 页面级缓存保存站点、group、当前 group、当前账号页、每页数量、筛选条件和最后刷新时间。
- 账号页缓存 key 为 `siteId:groupId:page:pageSize:statusFilter`。
- 每次成功调用 `POST /sub2api-sites/{site_id}/refresh` 后，前端写入新的 `sub2apiCacheVersion` 并广播 `sub2api-cache-updated`。`API 账号池状态` 和 `账号池逻辑管理` 都需要响应该事件，丢弃旧页面缓存并重新读取统一 MongoDB 缓存。
- 切换账号池时如果命中缓存，立即展示缓存数据。
- 未命中缓存时显示加载态，不显示错误的 `0 个可用账号`。
- 接口响应回来后，只有响应 key 仍等于当前选择 key，才允许更新表格；总体容量只允许更新到当前 group 的 `capacity_summary`。

## 总体容量计算

`5h 总体容量` 和 `7d 总体容量` 必须由后端计算并存储到 `sub2api_groups_cache.capacity_summary`。前端只读取该汇总，不使用当前页账号重新计算，避免分页数量、状态筛选或页面缓存导致总体容量变化。

后端计算对象是当前 group 下的完整账号集合。容量账号口径：

```text
正常账号：status == active 且 schedulable == true 且无 error_message
临时限流账号：明确 429 / 529，或 `rate_limit_reset_at` / `temp_unschedulable_until` 仍在未来，也计入总体容量
历史 `rate_limited_at` 只作为最近限流时间，不单独触发 warning；如果 reset/until 已过期，后端归一化时会清掉临时限流字段。读取某个 group 账号列表时，后端会重新归一化该 group 的完整缓存账号、写回 `sub2api_accounts_cache`，并重算 `capacity_summary`。
```

汇总字段：

```text
capacity_summary.available_accounts
capacity_summary.used_5h_percent
capacity_summary.available_5h_percent
capacity_summary.used_7d_percent
capacity_summary.available_7d_percent
capacity_summary.total_accounts
capacity_summary.calculated_at
```

账号行用量窗口保持紧凑展示，只显示百分比和剩余时间：

```text
5h  0%   现在
7d  16%  5d 20h
```

后端仍可缓存更完整的 sub2api 用量字段，供后续详情页、悬浮层或分析逻辑使用：

```text
codex_5h_used_percent / codex_7d_used_percent
codex_5h_reset_after_seconds / codex_7d_reset_after_seconds
codex_5h_request_count / codex_7d_request_count
codex_5h_token_count / codex_7d_token_count
codex_5h_actual_cost / codex_7d_actual_cost
codex_5h_total_cost / codex_7d_total_cost
codex_usage_updated_at   # sub2api 用量窗口自身更新时间
codex_usage_synced_at    # 本系统同步 sub2api 缓存的时间
```

其中请求数、token 和成本来自 `/api/v1/admin/usage` 日志按 `account_id` 聚合。`A` 表示 `actual_cost`，`U` 表示 `total_cost`。这些字段暂不在账号列表行直接展示，避免列表过乱。`codex_usage_updated_at` 可能早于本系统同步时间，因为它是 sub2api 用量窗口字段自身的更新时间，不是本系统拉取缓存的时间。

颜色规则按已用百分比：

```text
0% - 49%   蓝色
50% - 74%  绿色
75% - 89%  黄色
90% - 100% 红色
```

账号池切换时，账号表必须使用当前 `accountsDataKey`。容量条读取当前 group 的 `capacity_summary`；如果当前 group 没有后端汇总，则显示加载态，不使用当前页账号临时计算容量。

## 已验证结果

- `npm run build` 通过。
- `python -m compileall app` 曾用于后端验证。
- 对测试站点完成过真实刷新，缓存中 groups 为 3，accounts 为 751。
- 浏览器实测 `同步账号池数据` 会显示同步中、完成反馈，并恢复按钮。
- 浏览器实测切换 `free 账户池 01`、`plus 账号池 01`、`plus 账号池 02` 时，标题、总体容量和账号表不再错配闪烁。
