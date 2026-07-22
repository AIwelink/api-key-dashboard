# API 账号池状态与缓存设计

本文只记录当前 sub2api 多站点缓存、刷新和页面读取语义。容量字段、实时风险公式、并发覆盖、前端颜色阈值和账号排序的完整契约见 [30-api-pool-realtime-capacity-and-presence.md](./30-api-pool-realtime-capacity-and-presence.md)。

## 1. 设计边界

- sub2api groups/accounts 是远端观测状态，持久化到 MongoDB 后供多个页面复用。
- 页面读取缓存不应隐式访问远端 sub2api。
- 远端同步、前端静默刷新和 dashboard 刷新是不同动作，不能混用同一个“刷新”语义。
- 系统支持多个数据库站点，不存在固定的 `default` 站点、固定端口或固定生产域名。
- 当前实现不依赖 Redis。进程内防抖只适用于单实例；多实例部署前需要迁移到分布式锁或任务队列。

## 2. 代码入口

```text
backend/app/modules/sub2api/client.py       sub2api Admin API 封装
backend/app/modules/sub2api/cache.py        站点、groups/accounts 缓存和刷新调度
backend/app/modules/sub2api/dashboard.py    dashboard 趋势快照
backend/app/modules/sub2api/tpm_sampler.py  group TPM/RPM 分钟采样
backend/app/modules/sub2api/capacity_sampler.py 5 分钟容量状态采样
backend/app/routers/sub2api_sites.py         前端 API
frontend/src/pages/ApiPoolStatusPage.tsx    API 账号池状态页
frontend/src/hooks/usePageAutoRefresh.ts    页面级静默刷新
```

## 3. 站点接口

所有路径以 `/api` 为应用前缀：

```http
GET    /api/sub2api-sites
POST   /api/sub2api-sites
PATCH  /api/sub2api-sites/{site_id}
DELETE /api/sub2api-sites/{site_id}
POST   /api/sub2api-sites/{site_id}/test
POST   /api/sub2api-sites/{site_id}/refresh
POST   /api/sub2api-sites/{site_id}/dashboard/refresh
GET    /api/sub2api-sites/{site_id}/dashboard
GET    /api/sub2api-sites/{site_id}/groups
GET    /api/sub2api-sites/{site_id}/groups/{group_id}/accounts
```

关键语义：

- `GET /api/sub2api-sites` 返回 MongoDB 中未删除的站点；可用 `site_type=sub2api` 过滤。
- `POST` / `PATCH` 保存站点名称、`base_url`、API Key、状态、刷新间隔和异常处理开关。
- API Key 留空更新时不得清除已配置密钥；接口响应只返回是否配置，不返回明文密钥。
- `POST /test` 只测试连接，不写入完整账号缓存。
- `POST /refresh` 拉取远端 groups/accounts/usage，更新统一缓存并重算 group 汇总。
- `POST /dashboard/refresh` 刷新站点与 group dashboard 快照，不替代账号缓存刷新。
- groups/accounts GET 接口只读 MongoDB，不触发远端同步。

权限：

- 读取、测试、同步：`owner` / `admin` / `maintainer`。
- 新增和删除站点：`owner` / `admin`。
- 修改站点当前允许 `owner` / `admin` / `maintainer`，高风险字段仍应由前端限制并写审计。

## 4. MongoDB 集合

核心缓存：

```text
sub2api_sites
sub2api_groups_cache
sub2api_accounts_cache
sub2api_cache_meta
sub2api_dashboard_trends
sub2api_tpm_samples
sub2api_capacity_samples
```

标识规则：

- group 缓存 ID：`{site_id}:{group_id}`。
- account 缓存 ID：`{site_id}:{sub2api_account_id}`。
- 所有查询必须同时带 `site_id`；远端 account ID 只在站点内唯一。
- 本地账号与远端账号的业务匹配使用规范化邮箱或已保存的 `site_id + remote_account_id`，不能使用 `name`。

### 4.1 Group 缓存

每个 group 文档至少包含：

```text
site_id
group_id
group
fetched_at
capacity_summary
```

`capacity_summary` 基于该 group 的完整账号集合计算，不使用当前页数据。其字段和公式由文档 `30` 维护。

### 4.2 Account 缓存

每个 account 文档保留远端原始字段和归一化查询字段，包括：

```text
site_id
sub2api_account_id
account
group_ids
status
schedulable
error_message
created_at
last_used_at
codex_* usage fields
fetched_at
```

状态解释：

- `status` 和错误信息决定账号是否异常。
- `schedulable` 是调度开关，不能替代 `status`。
- 调度关闭但状态正常的账号仍可计入“正常账号”；401、token revoked、authentication failed 等错误即使调度关闭也必须计入异常。
- 5h/7d 429 必须按各自窗口和恢复时间判断，不能仅凭历史 `rate_limited_at` 永久标记。
- Bug Team 类型识别和容量资格必须分开：显式 `account_type/plan_type=bug_team` 优先级最高；否则仅把 `plan_type=team`、没有有效 5h 窗口且 7d 窗口不少于 28 天的账号识别为 Bug Team。Bug Team 的 7d 使用率低于 100% 时正常计入概览、美元容量和并发；达到 100% 后，恢复时间不超过 2 天才计入动态 5h/7d 容量，超过 2 天或恢复时间未知时排除。临时 403 cooldown 不能替代明确的 7d 100% 判断。
- 新导入的 Plus 可能缺少远端 `plan_type`。标准名称以独立的 `plus` 前缀开头（例如 `plus +手机号---邮箱`）时可将类型标记为 Plus；这条规则只用于容量类型推断，账号身份仍必须按规范化邮箱或明确远端绑定匹配。其余缺少类型且没有有效历史值的账号继续按 K12 回退。
- `sub_bundle_input` 导入的账号可能被远端错误标记为 `free`。账号位于名称含独立 `plus` 标记的分组、没有 5h 窗口且主窗口恰好为 7 天时，只能标记为待验证候选；签名本身不能直接纠正类型。只有统一 `gpt-5.4` 测试已经持久化为 `passed` 或 `rate_limited`，才能写入本地 `verified_plan_type=plus` 并在缓存归一化时纠正。`model_not_supported` 会撤销该验证。已有非 Free、非回退的有效历史类型优先；普通 Free 分组和不满足完整签名的账号不得纠正。

## 5. 远端刷新流程

`refresh_site_cache(db, site_id)` 的主要顺序：

1. 读取站点配置并创建 `Sub2ApiClient`。
2. 并行拉取 groups 和第一页 accounts。
3. 并行拉取剩余账号分页。
4. 拉取需要更新的账号 usage；当前实现并行请求，不设置人为逐请求限速，错误单独记录。
5. 批量读取 `sub2api_account_test_states`，把已持久化的类型验证附加到对应远端账号后再归一化；类型优先使用明确的非 Free 远端 `plan_type` 和有效历史类型，已验证的错误 Free 可纠正为 Plus，再识别标准 Plus 名称前缀，其余缺失类型按 K12 回退。
6. 批量写入 groups/accounts 缓存并删除远端已不存在的缓存文档。
7. 基于完整 group 集合重算 `capacity_summary`。
8. 更新 `sub2api_cache_meta`，供页面显示同步状态和最后完成时间。

远端 accounts 拉取使用分页，不能假设单页包含全部账号。group 归属以账号返回的 `group_ids` / `groups` / `account_groups` 为准，本地不依赖远端 `group_id` 查询参数完成完整归类。

### 5.1 统一账号测试基座

- `account_test_scheduler_loop` 覆盖所有启用 Sub2API 站点中仍存在于账号缓存的全部账号，包括 `schedulable=false`；每个账号每 24 小时测试一次，未测试账号优先。
- 所有站点共用 MongoDB 租约，全局严格串行调用 `/accounts/{id}/test`。请求固定为 `gpt-5.4`、空 prompt、`default` 模式。
- 每次结果先写入 `sub2api_account_test_events` 和 `sub2api_account_test_states`，之后才调用内部 dispatcher。判断程序只读取已保存事件，不得再次请求远端测试接口。
- `passed` 会重新开启关闭的调度。自动禁用开关当前关闭：401、402 和确认账号失效的 403 仍保存标准结果，但暂不调用 `schedulable=false`；429、普通 403、模型不支持和传输错误同样不改变调度。
- handler 状态保存在事件中，支持 `pending` / `processing` / `completed` / `failed` 幂等重放。机器人通知不属于该 dispatcher。
- 管理 API Key 认证失败是站点级故障，只写 `sub2api_account_test_site_meta` 退避，不得生成账号 401 事件。
- 旧 `long_7d_probe_scheduler_loop` 不再随应用启动，但旧模块和历史集合保留。US06-5002 自产 Plus 测试、改名和转组流程继续独立运行，不复用本次类型或调度 handler。

## 6. 调度与防抖

- 新站点默认 `refresh_interval_minutes = 30`。
- 每个站点可在站点配置中保存独立刷新间隔；读取页面时必须显示数据库值，不能重置为前端默认值。
- scheduler 每 30 秒检查一次已到期且启用的 sub2api 站点。
- 手动和定时刷新共用站点级 3 秒防抖任务；同一站点的短时间重复请求等待同一个任务。
- 不同站点可以独立刷新。
- 修改探测间隔不能再创建第二套账号缓存刷新；探测读取统一缓存并按自身配置执行。

## 7. 前端读取与静默刷新

页面动作分为：

| 动作 | 数据路径 | 访问远端 sub2api |
| --- | --- | --- |
| 同步账号池数据 | 远端 -> MongoDB -> 当前页面 | 是 |
| 刷新 dashboard | 远端 dashboard -> MongoDB -> 当前页面 | 是 |
| 页面级静默刷新 | MongoDB -> 当前页面 | 否 |
| 切换站点/group/分页 | MongoDB -> 当前页面 | 否 |

页面级静默刷新默认每 60 秒运行，仅在页面可见且窗口有焦点时执行。静默刷新应保留当前数据，不显示整页 loading，不重置滚动位置，也不弹正常成功提示。

账号页查询 key 至少包含：

```text
siteId:groupId:page:pageSize:statusFilter
```

响应写入前必须再次确认 key 仍与当前选择一致。同步成功后清除该站点的旧页缓存，再读取当前 group；不得通过整页 reload 造成滚动位置丢失。

## 8. 列表与统计约定

- group 账号接口默认每页 50，最大 500。
- 数据库先按 `created_at DESC, sub2api_account_id DESC` 排序，再执行 `skip/limit`。
- 顶部概览、429 数量、异常数量、并发和容量都来自完整 group，不受当前页影响。
- 表格只负责展示账号行；不能在浏览器中根据当前 50 条账号推导总容量。
- 未命中缓存或实时样本不足时显示等待数据，不显示误导性的零容量危险状态。

## 9. 修改检查表

修改缓存或刷新逻辑时至少检查：

1. 多站点查询是否都包含 `site_id`。
2. 账号分页是否完整拉取，排序是否发生在数据库分页前。
3. usage 单账号失败是否只影响该账号，并有错误日志。
4. 同步失败是否返回可解析的 JSON 错误，而不是 HTML `Internal Server Error`。
5. `status` 与 `schedulable` 是否仍保持独立语义。
6. 容量汇总是否使用完整 group 缓存。
7. 静默刷新是否保留滚动、筛选和已显示数据。
8. 修改公式或阈值时是否同步更新文档 `30`、通知和测试。

相关测试重点位于：

```text
backend/tests/test_sub2api_*.py
backend/tests/test_capacity_*.py
backend/tests/test_concurrency_capacity.py
frontend/src/pages/ApiPoolStatusHelp.test.ts
frontend/src/hooks/usePageAutoRefresh.test.ts
```
