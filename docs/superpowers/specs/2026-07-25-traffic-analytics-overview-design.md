# 访问流量分析概览与末次归因设计

## 1. 文档状态

- 日期：2026-07-25。
- 状态：V1 设计已确认，可进入实施。
- 采集与归因仓库：`AIwelink/traffic-analysis`。
- 管理页面：当前管理后台的“访问流量分析”。
- 分析数据库：现有 Growth PostgreSQL。
- 归因规则：注册前 30 天全局末次有效触发。

## 2. 目标

访问流量分析页面需要回答：

1. 最近有多少主页访问和推广链接访问，分别有多少独立访客。
2. 流量来自推广、直接访问、自然搜索还是外部引用。
3. 哪个渠道、活动和具体推广链接带来注册、调用和付费用户。
4. 注册用户后续是否成功调用、付费、二次付费、继续调用或退款。
5. 普通用户和内部人员分别产生了多少转化和消耗，不把内部额度当真实付费。
6. 每项转化指标对应哪些业务账号。

## 3. 系统边界

### 3.1 `traffic-analysis`

独立流量服务继续负责运行时写入：

- `GET /r/{code}`；
- `POST /api/public/homepage-visits`；
- `GET /api/public/growth-context`；
- `POST /internal/growth/registrations/bind`；
- 归因 Session、主页访问、链接访问和用户来源写入；
- 归因运行时 Schema 迁移。

它不提供 owner/admin 管理页面，也不暴露管理看板查询接口。

### 3.2 当前管理后台

当前管理后台负责：

- 站点、渠道、活动和推广链接配置；
- 读取 Growth PostgreSQL 生成访问与转化聚合；
- 使用现有 `traffic-analysis` 权限保护查询接口；
- 在“访问流量分析”页面展示概览、趋势、来源排行和账号名单；
- 使用运营管理配置的 `growth.internal_users` 进行普通/内部人员分段。

管理页面只读取 Growth PostgreSQL，不直接请求 AIWeLink/AIGCLink 业务库，也不调用流量服务公网接口拼装报表。

### 3.3 运营管理

运营管理继续负责用户、额度、收入、成本和内部人员配置。访问流量分析只复用内部人员身份，不重复建设内部人员表单。

```text
traffic-analysis 写访问与归因
              ↓
       Growth PostgreSQL
          ↙          ↘
访问流量分析查询       运营分析同步与查询
```

## 4. 末次有效触发

1. 每次有效 `/r/{code}` 点击都写入独立 `link_visits`。
2. 正式访问无条件覆盖该 Session 当前邀请，并刷新 30 天有效期。
3. 同一浏览器跨站点点击时全局只保留最后一次邀请。
4. 注册绑定按 `registered_at` 查询此前 30 天最后一条已计数链接访问。
5. 全局末次邀请与注册站点不一致时，不回退到该站点更早的邀请。
6. 注册成功后 `(site_id, external_user_id)` 来源不可覆盖。
7. 注册后的新点击不能改变历史账号来源。

页面统一显示“末次触发归因”，不再出现“首次触达”口径。

## 5. 内部人员与正式统计

`growth.internal_users` 是内部人员唯一配置来源，使用 `(site_id, external_user_id)` 关联。

用户分段：

```text
ordinary：注册时间命中不到内部人员，且不是其他正式排除账号
internal：注册时间命中有效内部人员配置
all：ordinary + internal
```

规则：

- 匿名主页 PV/UV、链接 PV/UV 无法在注册前识别人员身份，因此不按用户分段拆分。
- 注册及后续漏斗默认显示 `ordinary`。
- 页面可切换 `ordinary`、`internal` 和 `all`。
- 内部人员的成功调用、继续调用和消耗保留。
- 兑换码和管理员调额不计为支付；支付、二次支付只认业务系统成功到账订单形成的 `user_facts`。
- 内部人员即使同时存在于通用排除表，选择 `internal` 时仍可查询；非内部测试账号继续从正式统计排除。
- 原始访问、归因、调用和账单事实不删除，只在聚合层分段。

## 6. 时间与过滤

默认最近 7 天，支持：

- 最近 24 小时；
- 最近 7 天；
- 最近 30 天；
- 最近 90 天。

过滤条件：

- 站点；
- 用户分段；
- 来源类型；
- 渠道；
- 活动；
- 推广链接。

主页 PV/UV 是 `aiwelink.cc` 全站访问，站点过滤不作用于无法预先确定业务站点的直接、搜索和引用访问。站点、渠道、活动和链接过滤作用于推广访问、注册 Cohort 和后续转化。页面指标明确标记“主页全站”或“当前筛选”。

## 7. 指标口径

### 7.1 访问

- 主页 PV：时间范围内 `homepage_visits.is_counted=true` 的记录数。
- 主页 UV：上述记录按 `anonymous_visitor_key` 去重。
- 推广链接 PV：时间范围内符合过滤条件的 `link_visits.is_counted=true` 记录数。
- 推广链接 UV：上述记录按 `anonymous_visitor_key` 去重。
- 机器人和内部健康探测保留原始记录，但 `is_counted=false`，不进入正式指标。

### 7.2 注册 Cohort

转化漏斗以所选时间范围内完成注册的 `user_attributions` 为 Cohort：

- 注册账号：Cohort 账号数；
- 成功调用账号：Cohort 中 `first_successful_call_at` 非空；
- 付费账号：Cohort 中 `first_payment_at` 非空；
- 二次付费账号：Cohort 中 `second_payment_at` 非空；
- 继续调用账号：Cohort 中 `has_continued_call=true`；
- 退款账号：Cohort 中退款次数大于 0。

里程碑读取查询时最新的 `user_facts`，因此历史 Cohort 的后续转化会随着同步更新。页面显示数据生成时间。

### 7.3 金额

- 充值金额和退款金额使用 `user_facts` 中成功到账与成功退款汇总。
- 内部兑换码、推广额度和管理员调额不属于支付事实。
- 金额按站点币种展示；跨币种时禁止直接求和，V1 返回分币种金额列表。

### 7.4 比率

- 主页注册率：注册账号数 / 主页 UV。
- 链接注册率：推广来源注册账号数 / 推广链接 UV。
- 调用率、付费率、二次付费率、继续调用率：对应账号数 / 注册账号数。
- 分母为 0 时返回 `null`，前端显示 `--`，不伪造 0%。

## 8. 管理查询接口

### 8.1 概览

```http
GET /api/growth/analytics/overview
  ?range=7d
  &segment=ordinary
  &site_id=
  &source_kind=
  &channel_id=
  &campaign_id=
  &tracking_link_id=
```

响应结构：

```json
{
  "generated_at": "2026-07-25T08:00:00Z",
  "window": {"range": "7d", "start_at": "...", "end_at": "...", "bucket": "day"},
  "summary": {
    "homepage_pv": 0,
    "homepage_uv": 0,
    "link_pv": 0,
    "link_uv": 0,
    "registered_accounts": 0,
    "called_accounts": 0,
    "paid_accounts": 0,
    "second_paid_accounts": 0,
    "continued_accounts": 0,
    "refunded_accounts": 0
  },
  "rates": {
    "homepage_registration_rate": null,
    "link_registration_rate": null,
    "call_rate": null,
    "payment_rate": null,
    "second_payment_rate": null,
    "continued_rate": null
  },
  "amounts": [{"currency": "CNY", "payment_total_minor": 0, "refund_total_minor": 0}],
  "trends": [],
  "source_breakdown": [],
  "link_performance": []
}
```

`link_performance` 最多返回 50 条，按注册账号、链接 UV、链接 PV依次降序。

### 8.2 账号名单

```http
GET /api/growth/analytics/users
  ?range=7d
  &segment=ordinary
  &milestone=registered
  &limit=50
  &offset=0
```

`milestone` 支持：

- `registered`；
- `called`；
- `paid`；
- `second_paid`；
- `continued`；
- `refunded`。

每行返回站点、业务用户 ID、显示标签、普通/内部身份、来源类型、渠道、活动、推广链接、注册时间以及相关里程碑时间。列表不返回邮箱、密码、Cookie、API Key 或支付密钥。

所有接口使用现有 `traffic-analysis` 页面权限；查询接口只读，owner/admin/operator 均可访问。

## 9. 页面结构

顶部页签改为：

```text
流量概览 / 推广链接 / 渠道管理 / 活动管理 / 站点接入
```

默认进入“流量概览”。页面保持从上到下的查询型布局，不使用左右主分栏：

1. 查询条件；
2. 主页和推广访问指标带；
3. 注册转化漏斗；
4. 时间趋势表；
5. 来源构成表；
6. 推广链接表现表；
7. 点击漏斗指标后展示对应账号名单。

普通用户为默认分段。切换内部人员时，匿名访问指标保持不变，注册后漏斗和账号名单切换到内部人员。

## 10. 数据缺失与错误

- `0002_attribution_sessions` 未应用：接口返回 `503`，概览显示“流量采集库未初始化”，配置页签继续可用。
- 查询失败：只影响概览页签，不阻断推广链接、渠道、活动和站点配置。
- 空数据：显示 0、`--` 和空表，不展示模拟数据。
- `user_facts` 尚未同步：注册数仍显示，后续里程碑为 0，并显示生成时间。
- `internal_users` 表未就绪：正式用户分段查询返回 `503`，不得把全部用户误当普通用户。
- 单一账号缺少显示标签：显示业务用户 ID。
- 跨币种：按币种分行，不合并金额。

## 11. 性能

- 单次概览查询目标 P95 小于 500 ms。
- 查询只读 Growth PostgreSQL，不访问业务站数据库。
- 时间范围最大 90 天，链接排行最多 50 条，账号名单分页最大 100 条。
- 使用已有访问、来源、注册和内部人员索引；若生产 `EXPLAIN` 显示全表扫描，再增加针对查询条件的组合索引。
- V1 不新增第二套聚合表；数据量达到单次查询目标上限后再设计小时/日流量汇总。

## 12. 测试

### 12.1 后端

- 时间范围和分母为零的比率；
- 末次触发来源读取；
- 主页 PV/UV 和链接 PV/UV；
- 普通、内部、全部三种 Cohort；
- 内部人员调用保留但不伪造付费；
- 通用排除账号不进入普通统计；
- 来源、站点、渠道、活动和链接过滤；
- 跨币种金额分组；
- 账号里程碑过滤和分页；
- 缺少运行时表时返回 `503`；
- 所有接口要求 `traffic-analysis` 权限。

### 12.2 前端

- 五个页签且默认流量概览；
- 默认最近 7 天和普通用户；
- 指标、漏斗、趋势、来源和链接排行；
- 点击指标切换账号名单；
- 内部人员切换不改变匿名 PV/UV；
- 空数据、加载、失败和重试状态；
- 配置数据成功但概览失败时仍可切换配置页签；
- 桌面和移动视口无重叠，表格允许水平滚动。

## 13. V1 验收

1. 用户依次点击推广链接 A、推广链接 B，并通过 B 对应站点注册。
2. 两次点击均进入 `link_visits`，账号来源锁定为 B。
3. 注册后再次点击 A，账号来源仍为 B。
4. 流量概览准确显示主页和推广 PV/UV。
5. B 对应渠道、活动和链接的注册账号数增加 1。
6. 账号完成调用、首次付费、二次付费和继续调用后，漏斗与名单正确更新。
7. 配置一个内部人员并产生调用；内部分段显示该调用，普通分段不包含该账号。
8. 给内部人员发放兑换码或管理员额度，不增加付费账号和充值金额。
9. 点击各漏斗指标可找到对应账号。
10. 流量服务暂时不可用或表未迁移时，配置页签仍可正常使用。
