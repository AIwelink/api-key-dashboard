# AIWeLink Growth PostgreSQL 数据库需求文档

## 1. 文档状态

- 状态：V1 需求基线，待产品与技术评审。
- 日期：2026-07-21。
- 归因规则修订：2026-07-25；第 8 节及相关统计口径统一采用“注册前 30 天全局末次有效触发”，替代本文早期版本中的旧归因规则。
- 范围：增长数据的存储边界、逻辑表结构、同步投影、统计口径和验收要求。
- 前置条件：全局 Growth PostgreSQL 连接配置已经完成，配置仍保存在 MongoDB `app_settings._id = "growth_database"` 中。
- 本文不包含：页面视觉设计、具体 SQL migration、Sub2API/NewAPI 实际字段映射和部署脚本。

## 2. 背景与目标

AIWeLink 需要在多个客户站点、多个业务系统之间统一回答以下问题：

> 哪个具体渠道、活动、帖子、群或推荐人带来了某个账号，这个账号后来是否注册、成功调用、充值、二次充值、继续调用和退款。

Growth PostgreSQL 是独立的增长分析数据库。它不替代 Sub2API、NewAPI 等业务数据库，也不接管账号、认证、API 调用、订单或余额业务。

V1 目标：

1. 保存渠道、推广活动、具体来源和唯一推广链接。
2. 保存匿名访问以及注册后不可变的账号归因。
3. 从各业务库同步最小调用和资金事实。
4. 生成每个归因账号的统一统计结果，供运营看板查询。
5. 支持多站点、多业务系统，不因不同系统的用户 ID 重复而串号。
6. 所有同步数据可核验、可幂等重放、可从业务事实源重建。

## 3. 非目标

V1 不做以下事项：

- 不整库复制 Sub2API 或 NewAPI。
- 不将 Growth PostgreSQL 作为账号、调用、支付或退款的业务事实源。
- 不复制密码、密码哈希、Session、API Key、访问令牌或支付密钥。
- 不复制提示词、模型响应、完整请求体、完整支付回调或每一笔模型调用明细。
- 不要求修改 Sub2API 上游核心代码或长期维护大幅分叉。
- 不建设 CDC、Kafka、数据湖或通用 BI 平台。
- 不在 V1 中将“第二次成功调用”包装为 D1、D7、D30 留存。

## 4. 数据所有权与事实源

| 数据 | 权威事实源 | Growth PostgreSQL 的角色 |
| --- | --- | --- |
| 客户站点身份、系统类型、业务库 DSN | 现有 MongoDB `client_sites` | 保存可建立外键的 Growth 站点目录和分析配置，不保存 DSN |
| 渠道、活动、具体来源、推广链接 | Growth PostgreSQL | 权威事实源 |
| 匿名访问和末次有效触发 | Growth PostgreSQL | 权威事实源 |
| 账号来源分类与推广链接归因 | Growth PostgreSQL | 权威事实源，注册成功后不可改写 |
| 注册时间、成功调用、充值和退款 | 各站点业务数据库 | 保存最小标准化投影 |
| 账号汇总指标 | Growth PostgreSQL | 派生结果，可删除后重建 |
| 后台操作者与权限 | 现有管理后台 MongoDB | Growth 只保存操作者 ID 快照，不管理登录和权限 |
| Growth PostgreSQL DSN | MongoDB `app_settings` | 不得写入 Growth PostgreSQL 自身 |

业务库连接继续使用现有 `client_sites` 中已经配置的 SQL DSN。Growth 功能不得要求运营人员重复录入同一站点的数据库连接。

## 5. 总体数据模型

Growth PostgreSQL 使用独立 schema：

```text
growth
├── sites                    站点目录与增长接入状态
├── channels                 推广渠道
├── campaigns                渠道下的推广活动
├── tracking_links           具体帖子、群、推荐人对应的唯一链接
├── link_visits              外链访问事件
├── attribution_sessions     匿名归因 Session（由 traffic-analysis 迁移管理）
├── session_attributions     Session 当前全局末次邀请（由 traffic-analysis 迁移管理）
├── homepage_visits          主页访问与直接/搜索/引荐来源（由 traffic-analysis 迁移管理）
├── user_attributions        站点账号的唯一归因
├── user_exclusions          内部、测试账号排除名单
├── user_usage_daily         按账号、按 UTC 日的成功调用投影
├── billing_facts            充值和退款最小交易事实
├── user_facts               每个归因账号的派生统计结果
├── sync_cursors             增量同步游标
└── sync_runs                同步运行记录
```

关系：

```text
channel
  └── campaign + site
        └── tracking_link + concrete source
              └── link_visit + anonymous visitor
site account + promotion/direct/search/referral source
  └── user_attribution
        ├── user_usage_daily
        ├── billing_facts
        └── user_facts
```

## 6. 全局建模规则

### 6.1 多站点账号标识

所有账号数据必须使用复合业务键：

```text
(site_id, external_user_id)
```

- `site_id` 对应现有 `client_sites._id`，在 PostgreSQL 中使用 `TEXT`。
- `external_user_id` 是该站点业务库中的稳定用户 ID，统一转成 `TEXT` 保存。
- 禁止只按 `external_user_id` 跨站点查询或建立唯一约束。
- 同一个字符串用户 ID 出现在两个站点时，必须被视为两个不同账号。

### 6.2 主键

- Growth 自有实体使用应用生成的 UUID，推荐 UUIDv7；不得依赖自增 ID 在系统间传递。
- 高频访问表的 `visit_id` 同样使用应用生成 UUID。
- 同步投影除内部 UUID 外，还必须保存并唯一约束来源记录 ID。

### 6.3 时间

- 所有事件时间使用 PostgreSQL `TIMESTAMPTZ`，写入和传输统一使用 UTC。
- 展示时按 `growth.sites.timezone` 转换，时区值使用 IANA 名称，例如 `Asia/Shanghai`。
- `user_usage_daily.usage_date_utc` 固定按 UTC 切日，避免站点时区变更导致历史主键变化。
- 所有表至少有 `created_at`，可变记录另有 `updated_at`。
- 不允许使用无时区的 `TIMESTAMP` 保存业务事件。

### 6.4 金额

- 金额使用 `BIGINT amount_minor` 保存最小货币单位，不使用浮点数。
- 币种使用大写 ISO 4217 `CHAR(3)`，例如 `CNY`、`USD`。
- 充值和退款金额必须为正数；方向由 `fact_type` 判断。
- API 返回金额时同时返回 `amount_minor` 和 `currency`，格式化由前端完成。
- V1 每个站点只允许一个统计币种，必须与 `growth.sites.currency` 一致；不同币种不得直接相加。
- 若来源系统使用余额点数而非真实货币，适配器必须先定义明确的换算规则；没有可靠换算时该站点的金额能力标记为“未接入”，不得猜测。

### 6.5 状态与删除

- 状态字段使用受约束的短文本或 PostgreSQL enum；同一语义不得混用多套值。
- 推广链接、归因和交易事实不做物理删除。
- 停用推广链接只阻止新访问，不清除历史访问和既有归因。
- 来源业务记录被撤销时，通过状态更新保留审计轨迹，不直接删除投影。

## 7. 表结构需求

### 7.1 `growth.sites`

用途：为 Growth 数据提供站点外键，并保存站点的增长接入配置。它不保存数据库 DSN。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `site_id` | `TEXT` | 主键；对应 `client_sites._id` |
| `site_name` | `TEXT` | 非空；客户站点名称快照 |
| `system_type` | `TEXT` | 非空；V1 允许 `sub2api`、`newapi` |
| `public_origin` | `TEXT` | 非空；只允许 HTTPS origin，不含路径、查询和片段 |
| `default_landing_path` | `TEXT` | 非空；以单个 `/` 开头，不得以 `//` 开头 |
| `timezone` | `TEXT` | 非空；IANA 时区 |
| `currency` | `CHAR(3)` | 非空；默认结算币种 |
| `binding_mode` | `TEXT` | `shared_parent_cookie`、`signed_handoff` 或 `disabled` |
| `adapter_name` | `TEXT` | 当前数据适配器名称 |
| `adapter_version` | `TEXT` | 已验证的适配器版本 |
| `registration_capability` | `TEXT` | `pending`、`available`、`unsupported`、`error` |
| `usage_capability` | `TEXT` | 同上 |
| `payment_capability` | `TEXT` | 同上 |
| `refund_capability` | `TEXT` | 同上 |
| `sync_interval_seconds` | `INTEGER` | 60 至 3600；V1 默认 300 |
| `initial_sync_from` | `TIMESTAMPTZ` | 首次业务数据回溯起点 |
| `status` | `TEXT` | `active`、`disabled`、`archived` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | 非空 |

规则：

- 创建站点行前，管理后端必须确认 MongoDB 中存在对应 `client_sites`。
- `site_name` 和 `system_type` 是受控快照；客户站点变更后由管理后端同步更新。
- 删除客户站点时，已有 Growth 数据必须保留，站点改为 `archived`。
- 不支持的能力在 API 中返回 `unsupported`，前端显示“未接入”，不得显示数值零。

索引：

- `INDEX (status)`。
- `INDEX (system_type, status)`。

### 7.2 `growth.channels`

用途：保存平台或一级渠道，例如小红书、V2EX、Telegram、微信和熟人推荐。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `channel_id` | `UUID` | 主键 |
| `code` | `VARCHAR(40)` | 非空；机器可读编码，只允许小写字母、数字、`-` |
| `name` | `VARCHAR(100)` | 非空；展示名称 |
| `description` | `TEXT` | 可空 |
| `status` | `TEXT` | `active`、`disabled`、`archived` |
| `created_by` / `updated_by` | `TEXT` | 管理后台用户 ID |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | 非空 |

约束与索引：

- `UNIQUE (code)`，编码创建后不可修改和复用。
- `INDEX (status, name)`。

### 7.3 `growth.campaigns`

用途：表示某个站点、某个渠道下的一次推广活动。活动不是具体帖子或群，具体来源属于推广链接。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `campaign_id` | `UUID` | 主键 |
| `site_id` | `TEXT` | 外键到 `sites.site_id` |
| `channel_id` | `UUID` | 外键到 `channels.channel_id` |
| `code` | `VARCHAR(60)` | 站点内机器可读编码 |
| `name` | `VARCHAR(160)` | 非空 |
| `description` | `TEXT` | 可空 |
| `starts_at` / `ends_at` | `TIMESTAMPTZ` | 可空；结束时间必须晚于开始时间 |
| `status` | `TEXT` | `draft`、`active`、`paused`、`archived` |
| `created_by` / `updated_by` | `TEXT` | 管理后台用户 ID |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | 非空 |

约束与索引：

- `FOREIGN KEY (site_id) REFERENCES sites(site_id)`。
- `FOREIGN KEY (channel_id) REFERENCES channels(channel_id)`。
- `UNIQUE (site_id, code)`。
- `UNIQUE (campaign_id, site_id)`，供推广链接建立复合外键。
- `INDEX (site_id, channel_id, status)`。
- 渠道和站点归属创建后不得变更；需要变更时新建活动。

### 7.4 `growth.tracking_links`

用途：表示一条可公开使用的 `https://aiwelink.cc/r/{code}`。每条链接只对应一个站点和一个具体来源。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `tracking_link_id` | `UUID` | 主键 |
| `site_id` | `TEXT` | 非空 |
| `campaign_id` | `UUID` | 非空；与 `site_id` 组成复合外键到活动 |
| `code` | `CHAR(8)` | 全局唯一、无业务含义、不可修改 |
| `source_type` | `TEXT` | `post`、`group`、`referrer`、`profile`、`other` |
| `source_name` | `VARCHAR(240)` | 非空；具体帖子、群或推荐人名称 |
| `source_url` | `TEXT` | 可空；原始推广页面，仅作运营元数据 |
| `audience_group` | `VARCHAR(160)` | 可空 |
| `promoter` | `VARCHAR(160)` | 可空；推广负责人 |
| `landing_path` | `TEXT` | 可空；为空时使用站点默认路径 |
| `extra_dimensions` | `JSONB` | 最多 3 个字符串键值对，供预留维度使用 |
| `valid_from` / `valid_until` | `TIMESTAMPTZ` | 可空；有效期 |
| `status` | `TEXT` | `active`、`paused`、`archived` |
| `created_by` / `updated_by` | `TEXT` | 管理后台用户 ID |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | 非空 |

约束：

- `UNIQUE (code)`。
- `UNIQUE (tracking_link_id, site_id)`。
- `FOREIGN KEY (campaign_id, site_id) REFERENCES campaigns(campaign_id, site_id)`。
- `code` 使用排除 `0/o/1/i/l` 的小写随机字符集，服务端用密码学安全随机数生成，冲突时重试。
- 停用或归档后的 `code` 永不重新分配。
- `landing_path` 必须是站内相对路径；禁止保存任意外部跳转 URL，避免开放重定向。
- `source_url` 与跳转目标无关，不得被 `/r/*` 直接使用。
- `extra_dimensions` 的键和值均限制长度，禁止嵌套对象、数组和敏感信息。

索引：

- `INDEX (site_id, status, created_at DESC)`。
- `INDEX (campaign_id, status, created_at DESC)`。
- `GIN (extra_dimensions)`，只有确认后台需要按预留维度过滤时再创建。

### 7.5 `growth.link_visits`

用途：保存每次有效或被排除的 `/r/{code}` 请求，用于点击人数、风控和故障追踪。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `visit_id` | `UUID` | 主键 |
| `request_id` | `UUID` | 非空；单次 HTTP 请求幂等 ID |
| `tracking_link_id` | `UUID` | 非空 |
| `site_id` | `TEXT` | 非空；与链接组成复合外键 |
| `anonymous_visitor_key` | `CHAR(64)` | 非空；服务端 HMAC 后的匿名访客键，不保存原始 Cookie token |
| `visited_at` | `TIMESTAMPTZ` | 非空 |
| `is_first_touch` | `BOOLEAN` | 已弃用的旧迁移兼容列；末次触发逻辑不得读取该字段，运行时可固定写入 `false` |
| `is_bot` | `BOOLEAN` | 机器人判断结果 |
| `is_counted` | `BOOLEAN` | 是否进入正式点击统计 |
| `exclusion_reason` | `TEXT` | 可空；机器人、内部探测、无效请求等原因 |
| `referer_origin` | `TEXT` | 可空；只保存 scheme + host，不保存完整 URL 查询参数 |
| `user_agent_family` | `VARCHAR(80)` | 可空；解析后的浏览器族，不保存完整 UA 为长期字段 |
| `device_type` | `VARCHAR(24)` | 可空 |
| `country_code` | `CHAR(2)` | 可空；来自可信边缘层时才保存 |
| `ip_hash` | `CHAR(64)` | 可空；带轮换盐的短期哈希，不保存原始 IP |
| `redirect_result` | `TEXT` | `redirected`、`fallback_redirected`、`failed` |
| `http_status` | `SMALLINT` | 返回给客户端的状态码 |
| `created_at` | `TIMESTAMPTZ` | 非空 |

约束与索引：

- `UNIQUE (request_id)`，重复处理同一请求不得重复计数。
- `FOREIGN KEY (tracking_link_id, site_id) REFERENCES tracking_links(tracking_link_id, site_id)`。
- `INDEX (tracking_link_id, visited_at DESC)`。
- `INDEX (site_id, visited_at DESC)`。
- `INDEX (anonymous_visitor_key, visited_at DESC)`。
- 大数据量时按 `visited_at` 月分区，并增加 BRIN 时间索引。

“点击人数”定义为指定范围内：

```text
COUNT(DISTINCT anonymous_visitor_key) WHERE is_counted = true
```

原始 HTTP 请求次数不能直接作为点击人数。

### 7.6 `growth.user_attributions`

用途：保存注册成功站点账号的唯一、不可变来源；推广来源绑定到具体链接，非推广来源保存直接访问、自然搜索或引荐分类。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `site_id` | `TEXT` | 复合主键 |
| `external_user_id` | `TEXT` | 复合主键 |
| `source_kind` | `TEXT` | 非空；`promotion`、`direct`、`organic_search` 或 `referral`，是分析查询的权威来源类型 |
| `tracking_link_id` | `UUID` | 可空；`source_kind='promotion'` 时非空并与站点组成复合外键，其他来源必须为空 |
| `anonymous_visitor_key` | `CHAR(64)` | 可空；存在 Session 事件证据时保存服务端摘要，无证据的直接注册为空 |
| `source_link_visit_id` | `UUID` | 可空；推广来源对应的末次有效访问事件；由流量服务迁移从旧列 `first_visit_id` 重命名 |
| `source_homepage_visit_id` | `UUID` | 可空；自然搜索、引荐或有事件证据的直接访问对应主页访问事件 |
| `source_registration_id` | `TEXT` | 可空；来源系统注册事件或用户创建记录 ID |
| `registered_at` | `TIMESTAMPTZ` | 非空；业务库注册成功时间 |
| `attributed_at` | `TIMESTAMPTZ` | 非空；Growth 完成绑定时间 |
| `attribution_method` | `TEXT` | `shared_cookie`、`service_reported_direct`、`signed_handoff` 或 `reconciled` |
| `evidence_hash` | `CHAR(64)` | 可空；归因凭据摘要，不保存原始 token |
| `created_at` | `TIMESTAMPTZ` | 非空 |

主键和唯一规则：

```text
PRIMARY KEY (site_id, external_user_id)
```

该主键同时实现“一个站点账号只能拥有一个来源”；当来源为 `promotion` 时只能归入一条推广链接。

其他约束与索引：

- `FOREIGN KEY (tracking_link_id, site_id) REFERENCES tracking_links(tracking_link_id, site_id)`，仅对非空值生效。
- `FOREIGN KEY (source_link_visit_id) REFERENCES link_visits(visit_id)`，仅对非空值生效。
- `FOREIGN KEY (source_homepage_visit_id) REFERENCES homepage_visits(page_view_id)`，仅对非空值生效。
- `UNIQUE (site_id, source_registration_id)`，仅对非空值生效。
- `INDEX (tracking_link_id, registered_at DESC)`。
- `INDEX (site_id, registered_at DESC)`。
- `source_kind='promotion'` 时 `tracking_link_id` 非空且 `source_homepage_visit_id` 为空；非推广来源的 `tracking_link_id` 和 `source_link_visit_id` 均为空。

不可变规则：

- 首次成功插入后，不允许通过普通 API 修改 `source_kind`、`tracking_link_id`、`site_id` 或 `external_user_id`。
- 用户后来再次点击其他推广链接、登录或在其他设备访问，不改写既有归因。
- 重复注册回调使用 `INSERT ... ON CONFLICT DO NOTHING`，返回现有归因，不生成第二条记录。
- 只有有证据的管理员纠错流程可以通过受审计的离线修复执行；V1 管理页面不提供“改归因”按钮。

### 7.7 `growth.user_exclusions`

用途：排除团队内部账号、自动化测试账号和确认的异常账号，同时保留可审计的原因。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `site_id` | `TEXT` | 复合主键 |
| `external_user_id` | `TEXT` | 复合主键 |
| `reason` | `TEXT` | 非空 |
| `source` | `TEXT` | `manual`、`site_tag`、`rule` |
| `is_active` | `BOOLEAN` | 非空 |
| `created_by` / `updated_by` | `TEXT` | 管理后台用户 ID 或同步任务 ID |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | 非空 |

规则：

- `FOREIGN KEY (site_id) REFERENCES sites(site_id)`。
- 排除只影响正式统计，不删除访问、归因和业务投影。
- 排除状态变化后必须重算对应账号和链接的汇总。
- 管理员手工排除与恢复均写入现有后台审计日志。

### 7.8 `growth.user_usage_daily`

用途：保存业务库成功调用明细的最小按日投影。失败请求不进入成功调用字段。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `site_id` | `TEXT` | 复合主键 |
| `external_user_id` | `TEXT` | 复合主键 |
| `usage_date_utc` | `DATE` | 复合主键 |
| `successful_call_count` | `BIGINT` | 非负 |
| `first_successful_call_at` | `TIMESTAMPTZ` | 当日首次成功调用 |
| `last_successful_call_at` | `TIMESTAMPTZ` | 当日最后成功调用 |
| `input_tokens` | `BIGINT` | 可空、非负 |
| `output_tokens` | `BIGINT` | 可空、非负 |
| `source_updated_at` | `TIMESTAMPTZ` | 来源数据水位 |
| `synced_at` | `TIMESTAMPTZ` | 最近同步时间 |

主键和索引：

```text
PRIMARY KEY (site_id, external_user_id, usage_date_utc)
INDEX (site_id, usage_date_utc)
INDEX (site_id, source_updated_at)
```

规则：

- `FOREIGN KEY (site_id, external_user_id) REFERENCES user_attributions(site_id, external_user_id)`。
- 只持久化已有 `user_attributions` 的账号，包括推广、直接、自然搜索和引荐来源；尚无注册来源记录的账号不得写入本投影。
- 只同步能确认“成功获得模型响应”的调用。
- 不保存提示词、响应内容、完整请求体、API Key 或单次调用明细。
- 同步采用覆盖式 upsert；来源日汇总变化时，用新快照替换旧快照，不做累加写入。
- 如果来源只能提供调用明细，适配器在同步进程中聚合后再写入本表。

### 7.9 `growth.billing_facts`

用途：保存判断首次充值、二次充值、充值金额和退款金额所需的最小交易级事实。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `billing_fact_id` | `UUID` | 主键 |
| `site_id` | `TEXT` | 非空 |
| `external_user_id` | `TEXT` | 非空 |
| `fact_type` | `TEXT` | `payment` 或 `refund` |
| `source_fact_id` | `TEXT` | 来源系统内稳定且唯一的交易/退款 ID |
| `related_payment_id` | `TEXT` | 退款时关联的来源充值 ID，可空 |
| `amount_minor` | `BIGINT` | 大于 0 |
| `currency` | `CHAR(3)` | 非空 |
| `effective_status` | `TEXT` | `settled` 或 `reversed` |
| `occurred_at` | `TIMESTAMPTZ` | 实际到账或退款成功时间 |
| `source_updated_at` | `TIMESTAMPTZ` | 来源记录更新时间 |
| `synced_at` | `TIMESTAMPTZ` | 最近同步时间 |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | 非空 |

约束与索引：

- `FOREIGN KEY (site_id, external_user_id) REFERENCES user_attributions(site_id, external_user_id)`。
- `UNIQUE (site_id, fact_type, source_fact_id)`。
- `INDEX (site_id, external_user_id, occurred_at)`。
- `INDEX (site_id, source_updated_at)`。
- `INDEX (site_id, fact_type, effective_status, occurred_at)`。

规则：

- 只持久化已有 `user_attributions` 账号的资金事实，包括推广和非推广来源，不建设全站订单副本。
- 只有独立、真实、成功到账且状态为 `settled` 的 `payment` 才参与充值次数和金额。
- `currency` 必须与站点统计币种一致；不一致的事实进入同步拒绝计数并使资金能力显示错误，不能静默换算。
- 同一订单的重复回调或状态更新不得形成第二笔充值。
- 成功退款保存为独立 `refund` 事实；订单取消不能推断为退款。
- 后续退款不抹除“这个账号曾成功充值”的历史里程碑，退款金额单独统计。
- 完整订单、支付渠道密钥、支付回调和付款人敏感信息不得写入本表。

### 7.10 `growth.user_facts`

用途：为看板提供每个归因账号的当前统计结果。该表完全派生，可从归因、调用和资金事实重建。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `site_id` | `TEXT` | 复合主键 |
| `external_user_id` | `TEXT` | 复合主键 |
| `source_kind` | `TEXT` | 非空；复制账号归因的权威来源类型 |
| `tracking_link_id` | `UUID` | 可空；推广来源非空，非推广来源为空 |
| `account_label` | `TEXT` | 可空；非敏感用户名或脱敏邮箱，供名单识别 |
| `registered_at` | `TIMESTAMPTZ` | 非空 |
| `successful_call_count` | `BIGINT` | 非负 |
| `first_successful_call_at` | `TIMESTAMPTZ` | 可空 |
| `last_successful_call_at` | `TIMESTAMPTZ` | 可空 |
| `has_continued_call` | `BOOLEAN` | 成功调用次数是否至少为 2 |
| `settled_payment_count` | `INTEGER` | 非负 |
| `first_payment_at` | `TIMESTAMPTZ` | 可空 |
| `first_payment_amount_minor` | `BIGINT` | 可空 |
| `second_payment_at` | `TIMESTAMPTZ` | 可空 |
| `second_payment_amount_minor` | `BIGINT` | 可空 |
| `payment_total_minor` | `BIGINT` | 非负 |
| `settled_refund_count` | `INTEGER` | 非负 |
| `first_refund_at` | `TIMESTAMPTZ` | 可空 |
| `last_refund_at` | `TIMESTAMPTZ` | 可空 |
| `refund_total_minor` | `BIGINT` | 非负 |
| `currency` | `CHAR(3)` | 非空 |
| `is_excluded` | `BOOLEAN` | 是否排除正式统计 |
| `exclusion_reason` | `TEXT` | 可空 |
| `source_data_fresh_at` | `TIMESTAMPTZ` | 所依赖投影的最旧水位 |
| `computed_at` | `TIMESTAMPTZ` | 最近计算时间 |

约束与索引：

```text
PRIMARY KEY (site_id, external_user_id)
INDEX (tracking_link_id, is_excluded, registered_at)
INDEX (tracking_link_id, is_excluded, first_successful_call_at)
INDEX (tracking_link_id, is_excluded, first_payment_at)
```

规则：

- `FOREIGN KEY (site_id, external_user_id) REFERENCES user_attributions(site_id, external_user_id)`。
- `FOREIGN KEY (tracking_link_id, site_id) REFERENCES tracking_links(tracking_link_id, site_id)`，仅对非空值生效。
- 仅为已有 `user_attributions` 的账号生成记录。
- `has_continued_call = successful_call_count >= 2`。
- 第一、第二笔充值按 `occurred_at`、再按稳定来源 ID 排序，且必须是两笔不同的 `settled payment`。
- `payment_total_minor` 和 `refund_total_minor` 分开保存，不用净额替代原始两个指标。
- `account_label` 不得保存明文密码、Token、API Key 或完整敏感身份信息。
- 账号详情时间线由本表里程碑和 `billing_facts` 组合生成，不需要复制完整调用明细。

### 7.11 `growth.sync_cursors`

用途：保存每个站点、适配器和数据流的增量同步位置。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `site_id` | `TEXT` | 复合主键 |
| `adapter_name` | `TEXT` | 复合主键 |
| `stream_name` | `TEXT` | `registration`、`usage`、`billing`、`exclusion` |
| `cursor_value` | `JSONB` | 受适配器版本管理的游标，例如 `updated_at + id` |
| `watermark_at` | `TIMESTAMPTZ` | 已确认同步到的来源时间 |
| `last_success_at` | `TIMESTAMPTZ` | 最近成功时间 |
| `last_run_id` | `UUID` | 可空 |
| `updated_at` | `TIMESTAMPTZ` | 非空 |

主键：

```text
PRIMARY KEY (site_id, adapter_name, stream_name)
```

约束：

- `FOREIGN KEY (site_id) REFERENCES sites(site_id)`。

### 7.12 `growth.sync_runs`

用途：记录同步运行结果、数据新鲜度和可诊断错误。

主要字段：

| 字段 | 类型 | 约束与说明 |
| --- | --- | --- |
| `run_id` | `UUID` | 主键 |
| `site_id` | `TEXT` | 非空 |
| `adapter_name` | `TEXT` | 非空 |
| `stream_name` | `TEXT` | 非空 |
| `trigger_type` | `TEXT` | `schedule`、`manual`、`backfill`、`reconcile` |
| `status` | `TEXT` | `running`、`succeeded`、`failed`、`partial` |
| `cursor_before` / `cursor_after` | `JSONB` | 不得包含凭据或业务敏感内容 |
| `rows_scanned` | `BIGINT` | 非负 |
| `rows_upserted` | `BIGINT` | 非负 |
| `rows_rejected` | `BIGINT` | 非负 |
| `started_at` / `finished_at` | `TIMESTAMPTZ` | 非空/可空 |
| `error_code` | `TEXT` | 可空 |
| `error_message` | `TEXT` | 可空；必须脱敏并限制长度 |
| `created_at` | `TIMESTAMPTZ` | 非空 |

约束与索引：

- `FOREIGN KEY (site_id) REFERENCES sites(site_id)`。
- `INDEX (site_id, stream_name, started_at DESC)`。
- `INDEX (status, started_at DESC)`。

## 8. 归因规则

### 8.1 归因窗口

- 匿名归因 Session 和父域 Cookie 的有效期为 30 天。
- 每次有效 `/r/{code}` 访问都写入独立 `link_visits` 事件，并原子覆盖该 Session 当前的全局末次邀请，同时把 30 天窗口刷新为本次 `visited_at + 30 天`。
- 注册绑定必须以业务事实 `registered_at` 重建来源：在该时间此前 30 天内，从同一匿名访客全部已计数访问中选择最后一条，排序为 `visited_at DESC, created_at DESC, visit_id DESC`。
- 末次访问的 `site_id` 必须与注册站点一致；不一致时不回退到该站点更早的访问，该账号保持无推广归因。
- 候选链接只要求在点击发生时有效；之后普通暂停或归档不影响已经记录且仍处于原 30 天窗口内的候选访问。
- 安全事件导致链接被明确吊销时，可以拒绝尚未完成的归因，但必须保留历史数据和审计记录。

### 8.2 注册绑定

- 只有业务系统确认注册成功，并取得稳定 `external_user_id` 后才能写入归因。
- 只有访问、登录失败、注册失败或仅提交注册表单都不算注册。
- 已存在账号后来登录，不得被当作新注册归因。
- 没有有效归因凭据的注册保持“无推广归因”，不得根据 IP、邮箱、时间接近度猜测来源。

### 8.3 唯一归属

数据库通过以下主键强制保证唯一归属：

```text
UNIQUE (site_id, external_user_id)
```

同一账号后续访问任何推广链接均不改变已有记录。

## 9. 增量同步与幂等

### 9.1 V1 同步拓扑

```text
Sub2API / NewAPI 业务数据库
→ 只读系统适配器
→ user_usage_daily / billing_facts / user_exclusions
→ user_facts
→ 运营看板
```

- 适配器放在当前管理后端的独立 Growth 模块或独立 worker，不放进 Sub2API 上游代码。
- 适配器使用 `client_sites` 已配置的数据库连接，数据库账号应为只读权限。
- 默认每 5 分钟运行一次，正常情况下看板数据延迟不超过 5 分钟。
- 每种系统类型和版本必须有明确适配器；不得仅根据可能的表名猜测状态。

### 9.2 增量游标

- 优先使用单调递增主键或 `(updated_at, id)` 复合游标。
- 对可能迟到或被更新的数据，每次同步使用可配置重叠窗口，V1 默认回看 10 分钟。
- 游标只在目标数据事务提交成功后推进。
- 单次运行失败不得跳过未提交的数据。

### 9.3 幂等键

- 注册：`(site_id, external_user_id)`。
- 访问：`request_id`。
- 调用日汇总：`(site_id, external_user_id, usage_date_utc)`，覆盖式 upsert。
- 资金事实：`(site_id, fact_type, source_fact_id)`。
- 同一批数据重复同步任意次数，最终结果必须相同。

### 9.4 重算与对账

- 每次投影 upsert 后，只重算受影响账号的 `user_facts`。
- 每天至少执行一次滚动对账，重新读取最近 7 天来源数据并修复差异。
- 支持按站点、数据流和时间范围手工回填。
- 支持删除某个站点的派生 `user_facts` 后完整重建。
- 对账只能更正同步投影和派生结果，不得自动改写已经锁定的账号来源。

## 10. 统一统计口径

普通用户正式指标必须排除 `growth.internal_users` 在注册时间生效的账号和其他 `user_facts.is_excluded = true` 的账号。内部人员视图只包含注册时间命中 `growth.internal_users` 的账号，即使同时存在通用排除记录也保留其调用和消耗；全部用户视图为普通用户与内部人员之和。

| 指标 | 定义 |
| --- | --- |
| 点击人数 | 链接下 `is_counted=true` 的匿名访客键去重数 |
| 注册账号数 | 链接下未排除的唯一 `(site_id, external_user_id)` 数 |
| 成功调用账号数 | `successful_call_count >= 1` 的账号数 |
| 充值账号数 | `settled_payment_count >= 1` 的账号数 |
| 二次充值账号数 | `settled_payment_count >= 2` 的账号数 |
| 继续调用账号数 | `successful_call_count >= 2` 的账号数 |
| 充值金额 | `settled payment` 的 `amount_minor` 合计 |
| 退款金额 | `settled refund` 的 `amount_minor` 合计 |
| 注册率 | 注册账号数 / 点击人数 |
| 成功调用率 | 成功调用账号数 / 注册账号数 |
| 付费率 | 充值账号数 / 注册账号数 |
| 二次付费率 | 二次充值账号数 / 注册账号数 |
| 继续调用率 | 继续调用账号数 / 注册账号数 |

分母为 0 时，API 返回 `null`，前端显示 `--`，不得返回虚假的 `0%`。

点击人数按匿名浏览器标识计算，注册按账号计算。同一浏览器创建多个真实账号时注册率理论上可能超过 100%，这属于两种统计单位不同的结果，不能通过错误去重隐藏。

带日期范围的转化看板默认使用“注册 Cohort”：`registered_at` 位于所选区间的归因账号进入 Cohort，后续里程碑统计截至查询时刻。访问 PV/UV 仍按访问事件时间独立筛选。若未来增加“触达 Cohort”或“事件发生时间”口径，必须使用不同参数和清晰标签，不能混在同一指标中。

## 11. 性能与容量

- `/r/{code}` 按 `tracking_links.code` 查询必须走唯一索引。
- 单条推广链接的摘要查询不得扫描全部业务投影；以 `user_facts.tracking_link_id` 索引聚合。
- 访问事件表预计增长最快，必须预留按月分区能力。
- 首版容量目标由压测确定，最低要求 `/r/*` 数据库写入 P95 小于 100 ms，不包含公网网络时间。
- 列表 API 必须使用游标分页，不允许无上限返回账号或交易事实。
- 分析查询设置语句超时，不能影响 `/r/*` 写入和注册绑定。

## 12. 隐私、安全与保留

### 12.1 数据最小化

Growth PostgreSQL 禁止保存：

- 密码、密码哈希、Session、JWT、API Key、访问令牌；
- 完整 IP 地址和长期完整 User-Agent；
- 提示词、模型响应和完整 API 请求体；
- 完整订单、支付回调、银行卡或付款凭证；
- 与增长统计无关的用户资料。

### 12.2 匿名标识

- 浏览器 Cookie 使用至少 128 bit 的随机 token，并由服务端签名。
- 数据库只保存带服务端密钥 HMAC 后的 `anonymous_visitor_key`。
- 签名密钥和 HMAC 密钥从密钥管理或环境配置读取，不写入数据库和日志。
- `ip_hash` 使用定期轮换的独立盐，只用于短期风控，不参与账号归因。

### 12.3 保留策略

V1 默认建议：

- 归因 Cookie：30 天。
- 原始 `ip_hash`：最多 7 天。
- 解析前的完整 User-Agent：不落库；日志最多 7 天。
- `link_visits`：至少 24 个月，最终期限需在生产前经过隐私与业务确认。
- `sync_runs` 成功记录：180 天；失败记录：365 天。
- 归因、调用投影、资金事实和账号汇总：账号生命周期加法定/业务留存期；账号删除请求处理后应匿名化可识别标签，同时保留合法的聚合财务事实。

任何清理任务必须按分区或受控批次执行，并记录删除范围与数量。

## 13. 权限与审计

- Growth 业务表只允许后端服务账号读写，不直接暴露给浏览器。
- 来源业务库连接使用只读账号；同步 worker 不得修改来源数据。
- 渠道、活动、链接、站点分析配置和排除名单的查看、保存与测试仅允许 `owner`、`admin`。
- 所有管理写操作使用现有管理后台审计能力，记录操作者、动作、对象 ID、变更前后公开字段和时间。
- 审计日志不得包含数据库 DSN、归因 Cookie、签名 token、账号凭据或完整敏感错误。

## 14. Migration 与版本管理

- 使用项目统一 migration 工具管理 `growth` schema，禁止人工在生产库直接改表。
- 每个 migration 必须可在空库执行，也必须能从上一生产版本升级。
- 大表新增非空字段采用“先可空、回填、校验、再加约束”的分阶段方式。
- 索引创建应评估使用并发模式，避免长时间阻塞访问写入。
- 适配器版本与 schema 版本分开管理；适配器升级不得隐式改变历史统计口径。
- 指标口径变化必须记录版本、生效时间和是否需要历史重算。

## 15. 数据库验收标准

### 15.1 结构验收

1. 所有 Growth 表位于独立 `growth` schema。
2. 所有账号表均以 `(site_id, external_user_id)` 识别用户。
3. `user_attributions` 能在数据库层阻止同一站点账号拥有两条来源；推广来源只能归入一条链接。
4. 两个站点可以同时存在相同 `external_user_id`，且数据互不影响。
5. 推广链接 `code` 全局唯一、不可修改、停用后不可复用。
6. 所有金额为整数最小货币单位，所有业务事件时间为 `TIMESTAMPTZ`。
7. Growth 数据库中不存在 DSN、密码、Session、API Key、提示词和模型响应。

### 15.2 幂等验收

1. 同一访问请求重放两次只产生一条访问事件。
2. 同一注册成功回调重放两次只产生一条账号归因。
3. 同一调用同步批次重跑后调用次数不翻倍。
4. 同一充值回调或来源记录同步多次只产生一笔充值事实。
5. 同一订单两次状态更新不能被判为二次充值。
6. 删除并重建 `user_facts` 后，各项指标与重建前一致。

### 15.3 业务链路验收

创建一条“某篇小红书帖子”链接，用非内部测试账号执行：

```text
点击链接
→ 注册成功
→ 第一次成功调用
→ 第一笔成功充值
→ 第二笔独立成功充值
→ 再次成功调用
→ 一笔成功退款（扩展验证）
```

数据库必须满足：

- 链接下存在计数访问和唯一账号归因。
- `successful_call_count >= 2` 且 `has_continued_call = true`。
- 存在两条不同 `source_fact_id` 的 settled payment。
- `second_payment_at` 和第二笔金额正确。
- 退款是独立 refund 事实，充值总额与退款总额分开。
- 账号详情所需的来源、注册、首次调用、首次/二次充值、最近调用和退款时间均可查询。
- 把该账号加入排除名单后，原始事实仍在，但正式链接指标不再包含它。

## 16. 实施前待确认项

以下事项不阻塞需求基线，但必须在实现对应适配器或上线前确认：

1. 当前 Sub2API 与 NewAPI 版本中用户、成功调用、充值和退款的真实表、字段、索引及状态机。
2. 各系统注册成功后取得稳定 `external_user_id` 的可靠方式。
3. 各站点是否能提供明确的成功调用判定，而非仅有 HTTP 请求记录。
4. 充值金额的来源单位、币种以及历史数据是否需要换算。
5. 内部和测试账号由来源标签自动识别还是先维护显式名单。
6. 生产数据量、访问峰值和 `link_visits` 最终保留期限。
7. V1 首批启用哪些站点和能力；未验证能力必须保持“未接入”。

这些结论应写入各系统适配器的字段映射文档，不应通过修改本需求中的统一业务口径来迁就某个来源系统。
