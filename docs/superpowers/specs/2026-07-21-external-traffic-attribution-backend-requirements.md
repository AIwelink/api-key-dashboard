# AIWeLink 外链流量识别与归因后端需求文档

## 1. 文档状态

- 状态：V1 需求基线，待产品与技术评审。
- 日期：2026-07-21。
- 归因规则修订：2026-07-25；V1 统一采用“注册前 30 天全局末次有效触发”。同主域 Session 的最终拓扑以 `2026-07-24-aiwelink-homepage-growth-session-design.md` 为准，本文跨主域 handoff 内容仅作为未来扩展参考。
- 范围：推广链接管理、`/r/{code}` 请求、匿名访客识别、注册归因、业务数据同步和运营查询 API。
- 相关文档：`2026-07-21-growth-data-platform-requirements.md`。
- 本文不包含：管理页面视觉稿、具体框架选型、业务系统实际字段映射、Nginx/Cloudflare 最终配置。

## 2. 核心目标

系统为每个具体推广来源生成唯一链接：

```text
https://aiwelink.cc/r/{code}
```

用户访问后，系统需要持续回答：

```text
哪个站点
→ 哪个渠道
→ 哪个推广活动
→ 哪条帖子、哪个群或哪个推荐人
→ 带来了哪个注册账号
→ 该账号是否成功调用、充值、二次充值、继续调用和退款
```

V1 必须同时满足：

1. 每个帖子、群、推荐人和具体入口使用不同链接。
2. 每条链接只指向一个业务站点。
3. 同一个站点账号最多归入一条链接。
4. 注册后仍能根据业务库中的后续事实更新账号里程碑。
5. 多站点可以使用不同业务系统和不同用户 ID 体系。
6. 不修改 Sub2API 上游核心代码，不阻断其持续升级。

V1 采用全局末次有效触发归因：同一匿名访客每次有效访问推广链接时都覆盖当前邀请并刷新 30 天窗口；注册账号归属于 `registered_at` 之前 30 天内最后一条已计数访问。末次访问与注册站点不一致时不回退到该站点更早的访问；注册绑定成功后永久锁定。

## 3. 非目标

V1 不做以下事项：

- 不使用浏览器指纹追踪用户。
- 不承诺跨设备、无痕模式或清除 Cookie 后仍识别为同一匿名访客。
- 不把登录当作注册，不给已有账号补造推广归因。
- 不根据 IP、邮箱、昵称、注册时间接近度猜测账号归属。
- 不复制或同步登录 Session。
- 不在 `aiwelink.cc` 前端 SPA 中用 JavaScript 实现核心 `/r/*` 跳转。
- 不把 Growth 逻辑写进 Sub2API/NewAPI 上游仓库。
- 不把失败调用算作成功调用，不把订单状态重复更新算作二次充值。
- 不在 V1 实现 D1、D7、D30 留存、跨设备身份图谱或复杂多触点归因模型。

## 4. 代码库与服务边界

### 4.1 独立 Growth 服务

`/r/*`、归因和业务投影应放在独立于 Sub2API 的自有代码库中，建议结构：

```text
aiwelink-growth
├── redirect-service       GET /r/{code}、Cookie、跳转和 handoff
├── attribution-service    注册绑定与不可变归因
├── admin-api              渠道、活动、链接和分析查询
├── sync-worker            Sub2API/NewAPI 只读适配器与同步任务
├── migrations             growth schema 迁移
└── tests                  单元、集成和端到端测试
```

这些目录可以是一个仓库内的模块或进程，不要求拆成多个仓库。

### 4.2 现有管理后台

当前管理后台继续负责：

- `owner`、`admin` 的登录、Session 和权限；
- Growth PostgreSQL DSN 的保存与测试；
- 客户站点及其业务库连接配置；
- “访问流量分析”与“访问流量分析配置”页面；
- 管理操作审计。

管理后台通过私有网络调用 Growth admin API，或由后端受控代理 `/api/growth/*`。浏览器不得直接访问不受管理后台认证保护的私有 Growth API。

### 4.3 业务系统

Sub2API/NewAPI 继续负责：

- 账号注册、登录和 Session；
- API 调用与调用状态；
- 充值订单、到账、退款和余额；
- 业务数据库中的权威记录。

Growth 服务只能通过只读适配器读取业务库。注册绑定所需的网关只观察成功结果或调用官方“当前用户”接口，不修改业务语义。

## 5. 域名与路由

推荐生产路由：

```text
aiwelink.cc/                 品牌主页
aiwelink.cc/r/{code}         Growth redirect-service
api.aiwelink.cc/*            原业务系统或其前置网关
管理后台域名/api/growth/*     受管理后台鉴权的 Growth 管理接口
```

要求：

- CDN、Nginx 或边缘路由在加载主页前截获 `/r/*`。
- `/r/*` 不进入 React/Vue 页面渲染，不依赖 JavaScript 才能记录和跳转。
- `api.aiwelink.cc` 前可以部署轻量归因网关，但普通业务请求和 Session 响应必须透明传递。
- iframe 自定义首页与本系统无关；iframe 不能替代顶层跳转、Cookie 归因或注册绑定。

## 6. 推广层级与链接数据

推广层级固定为：

```text
channel
→ campaign
→ concrete source
→ tracking link
```

示例：

```text
小红书
→ 2026 年 7 月 API 推广
→ “Claude API 入门”第 3 篇帖子
→ https://aiwelink.cc/r/7km4q2xd
```

链接创建时必须填写：

- 目标客户站点；
- 渠道；
- 推广活动；
- 来源类型；
- 具体来源名称；
- 原始来源 URL（如有）；
- 受众群体（如有）；
- 推广负责人（如有）；
- 站内落地路径；
- 最多 3 个预留键值维度；
- 生效和失效时间（如有）。

同一内容同时推广两个站点时，必须创建两条链接。链接创建后不能更换站点、活动或 `code`；需要更换时停用旧链接并新建。

## 7. 链接码规则

公开格式：

```text
https://aiwelink.cc/r/{code}
```

V1 `code` 规则：

- 固定 8 位小写随机字符串；
- 使用密码学安全随机数；
- 排除容易混淆的 `0`、`o`、`1`、`i`、`l`；
- 不包含渠道、用户、站点或时间等可推断信息；
- 数据库全局唯一；
- 创建后永久不变；
- 停用、过期或归档后不重新分配。

服务端不得使用递增 ID、可枚举编码或客户端提交的自定义 code 作为默认生成方式。

## 8. 匿名访客标识

### 8.1 同一主域场景

针对 `aiwelink.cc` 与 `api.aiwelink.cc`，redirect-service 设置第一方 Cookie：

```text
Name: aiw_growth_vid
Value: version.random_token.signature
Domain: aiwelink.cc
Path: /
Max-Age: 2592000
Secure: true
HttpOnly: true
SameSite: Lax
```

规则：

- `random_token` 至少 128 bit，不包含账号信息。
- `signature` 使用带版本号的 HMAC，支持密钥轮换。
- Cookie 只用于匿名流量归因，不具有登录或授权能力。
- 数据库不保存原始 Cookie 值，只保存服务端 HMAC 后的匿名访客键。
- 有效 Cookie 可以在访问时刷新 30 天有效期。
- Cookie 无效、签名错误或版本不支持时，生成新访客 token，不尝试修复旧值。

该父域 Cookie 会被同一主域下的服务接收，因此只能包含无权限的随机标识。若存在不受信任或可能被接管的子域，生产上线前必须重新评估父域 Cookie，改用签名 handoff。

### 8.2 跨主域场景（V1 不启用）

如果目标业务站点不在 `*.aiwelink.cc`，不能依赖父域 Cookie。目标站点必须实现签名 handoff：

```text
GET https://target.example.com/__aiwelink/attribution?token={signed_token}&next={relative_path}
```

handoff 流程：

1. `/r/{code}` 为目标站点生成短期、单站点、单用途签名 token。
2. token 只包含版本、站点 ID、候选链接 ID、匿名访客键摘要、触达时间、过期时间和随机 nonce。
3. 目标站点网关验证签名、站点、有效期和 nonce。
4. 网关设置该站点自己的 30 天 HttpOnly 归因 Cookie。
5. 网关用 302 跳到经过校验的站内 `next`，从地址栏移除 token。
6. 若未来启用，后续有效 handoff 必须遵循同一全局末次触发规则，覆盖当前邀请并刷新 30 天窗口。

`next` 必须是站内相对路径。不得允许 token 或 `next` 形成开放重定向。

## 9. `/r/{code}` 请求流程

### 9.1 标准 GET 流程

```text
浏览器 GET /r/{code}
→ 校验 code 格式
→ 读取链接、活动和站点状态
→ 校验链接有效期和目标站点状态
→ 读取或签发匿名访客 Cookie
→ 判断机器人、内部探测和重复请求
→ 同步写入 link_visits
→ 原子覆盖该 Session 的全局末次邀请并刷新 30 天窗口
→ 返回 HTTP 302
```

响应要求：

```http
Cache-Control: no-store, private
Pragma: no-cache
Referrer-Policy: no-referrer
```

- CDN 必须绕过 `/r/*` 缓存，不能把一个用户的 Cookie 响应发给另一个用户。
- 使用 `302` 或 `303`，不使用会被长期缓存的 `301`。
- 跳转目标由站点 `public_origin` 与受校验的站内路径拼接，不读取请求参数中的任意 URL。
- GET 的数据库读取、访问写入和 Session 末次邀请更新应在返回跳转前完成。
- `HEAD` 可以返回相同可达性状态，但不签发 Cookie、不记录正式点击、不参与归因。

### 9.2 全局末次触发规则

当前邀请按全局归因 Session 计算，不按站点拆分：

```text
session_key_hash → one current invitation
```

每个有效 `/r/{code}` 请求都写入一条 `link_visits`，并按 `session_key_hash` 原子覆盖 `session_attributions`。注册绑定不直接信任回调到达时的当前记录，而是根据 Session 取得 `anonymous_visitor_key`，在 `registered_at` 之前 30 天内选择全局最后一条 `is_counted=true` 访问：

```text
ORDER BY visited_at DESC, created_at DESC, visit_id DESC
LIMIT 1
```

该访问的 `site_id` 必须与注册请求的 `site_id` 一致才能形成 `promotion` 来源；不一致时不得回退到该站点更早的链接。服务改用独立主页访问证据分类为 `organic_search` 或 `referral`，仍无证据时分类为 `direct`。账号绑定成功后保持不可变，注册后的后续点击只影响匿名 Session，不改写历史账号来源。

### 9.3 无效、暂停和过期链接

以下情况不能签发归因凭据，也不能写入正式点击：

- code 不存在或格式错误；
- 链接、活动或站点被暂停、停用或归档；
- 未到生效时间或已经过期；
- 目标站点没有可用安全落地地址。

对外统一 302 到品牌站的“链接不可用”页面，避免泄露链接是否曾经存在。后端记录脱敏错误指标，不把内部状态放进查询参数。

普通暂停或归档只阻止新的有效触发，不删除暂停前已经写入的访问。历史访问只有在注册时仍位于 30 天窗口内、是全局最后一条已计数访问且站点匹配时，才能成为注册候选。

### 9.4 Growth 数据库不可用

推广跳转的可用性优先于统计，但归因正确性优先于错误补记：

- 数据库读取或写入超时后，服务使用固定安全目标完成降级跳转。
- 降级跳转不得生成新的可用于注册绑定的归因凭据。
- 不允许把当前链接猜作末次触发后异步补写。
- 服务记录 `redirect_degraded_total` 和脱敏日志，并触发告警。
- 数据恢复后不能根据访问日志中的 IP 或 UA 自动猜测缺失归因。

该策略可能丢失少量统计，但不会根据不完整证据猜测账号来源。

## 10. 机器人、重复点击和内部流量

### 10.1 机器人

机器人识别可使用：

- 已知爬虫 User-Agent 规则；
- CDN/边缘层可信 bot score；
- 明确的内部健康检查标头和来源；
- 异常频率规则。

机器人访问可以保留一条 `is_counted=false` 的诊断事件，但不能成为末次有效触发、不能覆盖当前邀请、不能进入点击人数，也不能签发跨域 handoff。

### 10.2 重复点击

- 每个 HTTP 请求有独立 `request_id`，同一请求重试只落一条事件。
- 同一匿名访客多次点击同一链接会产生多条访问事件，但“点击人数”只计一个访客。
- 点击次数和点击人数是两个不同指标；V1 主看板展示点击人数。

### 10.3 内部和测试账号

- 内部探测和自动化检查尽量在访问阶段标为 `is_counted=false`。
- 已完成注册的内部或测试账号通过 `(site_id, external_user_id)` 排除名单过滤。
- 排除只影响正式统计，不删除归因、访问、充值或调用事实。
- 测试验收账号可以在专用环境计入，也可以在生产验证后立即标记为排除。

## 11. 注册归因绑定

### 11.1 必要条件

只有同时满足以下条件才能写入 `user_attributions`：

1. 来源业务系统已经确认注册成功。
2. 已取得该站点稳定的 `external_user_id`。
3. 已取得稳定、可幂等重放的 `source_registration_id`。
4. 该账号尚无既有归因。

推广归因还要求注册发生时存在有效归因 Session，并且 `registered_at` 之前 30 天全局最后一条已计数推广访问与注册站点一致。条件不满足时不得回退到旧推广链接，但注册来源仍按独立主页证据分类；没有可验证证据时保存为 `direct`。

### 11.2 内部绑定接口

业务系统网关调用私有接口：

```http
POST /internal/growth/registrations/bind
Content-Type: application/json
Authorization: service credential

{
  "site_id": "aiwelink",
  "external_user_id": "12345",
  "source_registration_id": "12345",
  "registered_at": "2026-07-21T08:00:00Z",
  "growth_session": "opaque-session-value-or-null"
}
```

接口规则：

- 只允许登记过的站点服务身份调用，服务身份只能写自己的 `site_id`。
- `growth_session` 只在内存中校验，不写日志、不写数据库原文。
- 服务端根据证据计算匿名访客键，并按 `registered_at` 查找此前 30 天全局最后一条已计数访问。
- 末次推广访问站点与注册站点不一致时不得回退到本站更早访问；改用独立主页来源证据，没有证据时分类为 `direct`。
- 用 `INSERT ... ON CONFLICT (site_id, external_user_id) DO NOTHING` 写入。
- 重复调用返回 `200` 和原归因；不得返回第二条或覆盖链接。
- 缺失、格式错误或无法解析的 Session 不得产生推广来源；分类为 `direct`，且不保存伪造事件证据。
- 只有可信服务身份提供的 `site_id` 可以作为注册站点事实，浏览器不能自行指定其他站点。

建议响应：

```json
{
  "result": "attributed",
  "source_kind": "promotion",
  "tracking_link_id": "uuid",
  "attribution_method": "shared_cookie"
}
```

`result` 允许：`attributed`、`classified`、`already_attributed`。首次创建返回 `201`，幂等重放返回 `200`。

### 11.3 不修改 Sub2API 的接入方式

在 `api.aiwelink.cc` 前部署外置归因网关：

```text
浏览器携带 aiw_growth_vid
→ 网关转发注册请求给原 Sub2API
→ Sub2API 确认注册成功并设置原有 Session
→ 网关取得稳定用户 ID
→ 网关调用内部归因绑定接口
→ 原注册响应和 Session Cookie 原样返回浏览器
```

稳定用户 ID 的允许来源按优先级为：

1. 注册成功响应中的明确用户 ID。
2. 注册成功后，使用刚建立的原有 Session 调用官方“当前用户”接口。
3. 业务系统提供的可靠注册事件或扩展点。

禁止使用邮箱、昵称、IP、最后插入记录或时间接近度猜测用户 ID。如果当前 Sub2API 版本无法可靠取得 ID，该站点注册归因能力必须标记为“未接入”，直到完成可靠适配。

网关安全要求：

- 不记录注册请求体、密码、验证码、Session Cookie 或完整响应体。
- 不改变 Sub2API 的 CORS、CSRF、Cookie 域和登录语义，除非有独立评审。
- Growth 绑定失败不能把成功注册改成失败；网关应记录待重试的非敏感绑定任务。
- 待重试任务只能包含站点、外部用户 ID、注册时间和加密/受保护的短期归因证据，不得包含密码。

### 11.4 既有账号和多次点击

- 已有账号登录不触发注册绑定。
- 同一账号重复提交注册成功回调只返回原归因。
- 账号归因后再访问其他链接，不改写归因。
- 同一匿名浏览器可以注册多个真实账号；每个账号分别按自己的 `registered_at` 重建当时的全局末次有效触发，不额外限制浏览器只能注册一个账号。

## 12. 多站点与系统适配器

每个站点必须明确选择一种注册归因接入模式：同一可信主域使用 `shared_parent_cookie`，跨主域使用 `signed_handoff`，尚未接入时使用 `disabled`。接入模式按站点独立配置，不能根据请求临时猜测。

### 12.1 适配器契约

每个 `system_type + supported_version` 实现统一适配器接口：

```text
detect_version()
detect_capabilities()
read_registration(user_id or cursor)
read_usage_daily(cursor, overlap_window)
read_billing_facts(cursor, overlap_window)
read_account_exclusions(cursor)
healthcheck()
```

适配器输出统一使用：

```text
(site_id, external_user_id)
```

并负责把来源状态映射成统一业务事实。

### 12.2 能力发现

每个站点分别记录以下能力：

- 注册绑定；
- 成功调用；
- 充值；
- 退款。

能力状态包括：`pending`、`available`、`unsupported`、`error`。

- `unsupported`：该版本没有可验证事实或尚未开发适配器。
- `error`：已接入但当前读取失败。
- 两者在 API 中都不得被转换成数字零。

### 12.3 业务数据库读取

- 使用现有客户站点 SQL DSN，不在 Growth 表单重复保存。
- 连接账号原则上只有 `SELECT` 权限。
- 适配器必须基于实际 schema、字段、索引和状态机开发。
- 适配器版本升级前运行字段契约测试，防止上游升级后静默算错。
- 一个站点的适配器失败不得阻塞其他站点同步。

## 13. 调用、充值和退款同步

V1 拓扑：

```text
业务数据库
→ 每 1 至 5 分钟增量读取
→ 标准化投影
→ 重算受影响账号 user_facts
→ 看板查询
```

### 13.1 成功调用

- 只有业务系统能确认已经成功获得模型响应的请求才计入。
- 失败、超时、限流、鉴权失败和未产生模型响应的请求不计入。
- Growth 只保存按账号、按 UTC 日汇总：成功次数、当日首次和最后成功时间，可选 token 合计。
- 第一次成功调用后又成功调用至少一次，即“继续调用”。不要求跨天。

### 13.2 充值

- 只有独立订单且成功到账的充值计入。
- 二次充值要求同一账号存在两条不同来源交易 ID 的成功到账记录。
- 同一订单的重复 webhook、轮询和状态更新必须幂等 upsert，不能增加次数。
- 第一、第二笔按实际到账时间排序，相同时间再按稳定来源 ID 排序。

### 13.3 退款

- 退款必须来自明确的成功退款事实。
- 取消订单、支付失败、余额调整或订单关闭不能自动当作退款。
- 一笔充值可以关联多笔部分退款，每笔退款必须有独立来源 ID。
- 充值金额和退款金额分开展示，不只展示净额。

### 13.4 数据新鲜度

- 默认同步间隔 5 分钟，正常数据延迟目标不超过 5 分钟。
- API 返回每个站点、每种能力的 `fresh_at`、`last_sync_status` 和脱敏错误摘要。
- 超过两倍同步间隔未成功时标记为 stale，页面显示“数据延迟”，不得继续显示为实时。

## 14. 幂等、重试与对账

### 14.1 幂等

- `/r/*`：`request_id` 唯一。
- 注册归因：`(site_id, external_user_id)` 唯一。
- 调用日汇总：`(site_id, external_user_id, usage_date_utc)` 覆盖式 upsert。
- 充值/退款：`(site_id, fact_type, source_fact_id)` 唯一。
- 管理 API 创建链接：支持 `Idempotency-Key`，客户端网络重试不能生成两条链接。

### 14.2 重试

- 注册后的 Growth 绑定失败可指数退避重试，直到证据过期或成功。
- 同步失败不推进游标。
- 连接、超时等临时错误可重试；字段缺失、未知状态等契约错误立即停止该站点对应数据流并告警。
- 重试日志只能包含 request ID、site ID、外部用户 ID 的受控表示和错误码。

### 14.3 对账

- 每日重读最近 7 天调用、充值和退款数据。
- 支持按站点和时间范围手工回填。
- 对账可以更新业务投影和派生账号事实，不自动改变推广归因。
- 对账差异必须记录数量、来源水位和运行 ID。

## 15. 管理 API

所有以下接口仅允许 `owner`、`admin`，并写管理后台审计日志。

### 15.1 站点增长接入

```http
GET   /api/growth/sites
GET   /api/growth/sites/{site_id}
PUT   /api/growth/sites/{site_id}
POST  /api/growth/sites/{site_id}/test
POST  /api/growth/sites/{site_id}/sync
```

要求：

- 只能选择已有 `client_sites`，不得在这里录入第二份业务库 DSN。
- 测试返回系统版本、适配器版本和四项能力状态。
- 手工同步是受审计操作，并防止同一站点同一数据流并发运行。

### 15.2 渠道

```http
GET   /api/growth/channels
POST  /api/growth/channels
PATCH /api/growth/channels/{channel_id}
```

渠道存在历史引用时不能物理删除，只能停用或归档。

### 15.3 推广活动

```http
GET   /api/growth/campaigns
POST  /api/growth/campaigns
GET   /api/growth/campaigns/{campaign_id}
PATCH /api/growth/campaigns/{campaign_id}
```

活动创建后不能更换站点和渠道。

### 15.4 推广链接

```http
GET   /api/growth/tracking-links
POST  /api/growth/tracking-links
GET   /api/growth/tracking-links/{tracking_link_id}
PATCH /api/growth/tracking-links/{tracking_link_id}
POST  /api/growth/tracking-links/{tracking_link_id}/pause
POST  /api/growth/tracking-links/{tracking_link_id}/activate
POST  /api/growth/tracking-links/{tracking_link_id}/archive
```

创建成功响应必须返回：

```json
{
  "tracking_link_id": "uuid",
  "code": "7km4q2xd",
  "public_url": "https://aiwelink.cc/r/7km4q2xd",
  "status": "active"
}
```

链接没有物理删除接口。更新接口不得修改 `code`、`site_id` 或 `campaign_id`。

## 16. 分析查询 API

所有分析接口仅允许 `owner`、`admin`。所有列表使用游标分页。

### 16.1 链接汇总

```http
GET /api/growth/analytics/links
```

过滤条件：

- 站点；
- 渠道；
- 活动；
- 具体来源类型；
- 推广负责人；
- 链接状态；
- 注册 Cohort 时间范围；
- 预留维度。

每条链接返回：

- 点击人数；
- 注册账号数和注册率；
- 成功调用账号数和成功调用率；
- 充值账号数和付费率；
- 二次充值账号数和二次付费率；
- 继续调用账号数和继续调用率；
- 充值金额和退款金额；
- 能力状态和数据新鲜度。

### 16.2 指标账号名单

```http
GET /api/growth/analytics/links/{tracking_link_id}/accounts?milestone={value}
```

`milestone` 允许：

- `registered`
- `called`
- `paid`
- `paid_again`
- `continued_call`
- `refunded`

每行至少返回 `site_id`、`external_user_id`、脱敏账号标签、相关里程碑时间、排除状态和数据新鲜度。默认不返回被排除账号；管理员可显式选择查看。

### 16.3 单账号详情

```http
GET /api/growth/analytics/accounts/{site_id}/{external_user_id}
```

返回：

- 站点、渠道、活动、具体来源和推广链接；
- 注册状态和时间；
- 首次成功调用、最近成功调用、成功调用总数；
- 是否继续调用；
- 第一笔和第二笔充值时间及金额；
- 充值次数和总额；
- 退款次数、时间和总额；
- 排除状态与原因；
- 按时间排序的里程碑时间线；
- 各数据流能力状态和新鲜度。

账号详情不返回密码、Session、API Key、提示词、模型响应、完整支付记录或归因 Cookie。

### 16.4 未接入和空值

- 能力为 `unsupported` 或 `pending` 时，相应指标值返回 `null` 和能力状态。
- 能力已接入且事实为零时，才返回数字 `0`。
- 比率分母为零时返回 `null`。
- API 不得把“同步失败”“未接入”和“真实为零”合并成同一种结果。

## 17. 权限与安全

### 17.1 公开接口

`GET /r/{code}` 是唯一无需登录的公开业务接口。它必须：

- 限制请求方法；
- 对 code 使用固定格式校验和参数化查询；
- 设置请求超时和数据库语句超时；
- 按 IP 短期哈希、访客键和 code 做分层限流；
- 对健康检查和机器人流量单独处理；
- 不在响应、日志或错误页泄露内部链接 UUID、站点配置或数据库错误。

限流不能依赖永久 IP 标识，也不能让攻击者通过伪造或高频请求污染他人的全局末次邀请。

### 17.2 私有接口

- 管理 API 通过现有用户 Session 和 `owner/admin` 角色鉴权。
- 内部绑定 API 使用 mTLS 或可轮换的服务凭据，并限制网络来源。
- 所有修改型管理 API 使用 CSRF/Origin 防护。
- Growth 服务与管理后台之间的操作者上下文必须签名，不能信任浏览器提交的角色字段。

### 17.3 URL 与 token

- 只允许跳转到站点配置中的 HTTPS origin。
- 所有 `next` 和 `landing_path` 必须是规范化站内相对路径。
- handoff token 短期有效、单站点、签名并带 nonce；不得包含账号或数据库凭据。
- URL 中的 handoff token 不记录在访问日志和分析工具中，消费后立即从地址栏移除。
- Cookie 和 token 的签名密钥必须可轮换，旧密钥只在受控过渡期用于验证。

## 18. 错误处理

| 场景 | 对外行为 | 内部行为 |
| --- | --- | --- |
| code 不存在、停用或过期 | 302 到统一不可用页，不归因 | 分类计数，不泄露状态 |
| Growth DB 暂时不可用 | 降级跳安全页面，不签发新归因 | 告警、指标、脱敏日志 |
| 注册成功但绑定失败 | 注册仍成功 | 受控重试，不记录密码 |
| 找不到有效推广触发，或末次推广站点不匹配 | 返回非推广 `classified` | 不回退旧推广链接；按主页证据分类，否则为 `direct` |
| 账号已有归因 | 返回 `already_attributed` | 保持原记录 |
| 适配器网络错误 | 保留旧数据并标记 stale | 不推进游标，可重试 |
| 适配器字段契约变化 | 对应能力标记 error | 停止该流、告警、要求适配 |
| 分析查询超时 | 返回受控 503/504 | 不影响 redirect 写入 |

所有错误响应带 `request_id`。生产错误不返回 SQL、DSN、表结构、堆栈、Cookie 或 token。

## 19. 可观测性

### 19.1 指标

至少提供：

```text
redirect_requests_total{result}
redirect_latency_seconds
redirect_degraded_total{reason}
visit_write_failures_total
attribution_bind_total{status,site_id}
attribution_bind_latency_seconds
sync_runs_total{site_id,stream,status}
sync_lag_seconds{site_id,stream}
sync_rows_total{site_id,stream,result}
analytics_query_latency_seconds{endpoint}
```

指标标签不得包含外部用户 ID、完整 code、Cookie、token、URL 查询参数或高基数字段。

### 19.2 日志

- 使用结构化日志，包含 `request_id`、受控 `site_id`、服务、动作和错误码。
- 推广链接可记录内部 `tracking_link_id`，但公开 code 只在确有诊断需要时受控记录。
- 不记录 Cookie、handoff token、注册请求体、数据库 DSN、支付回调、Session 和 API Key。
- 错误消息在进入日志、数据库和管理页面前统一脱敏和限长。

### 19.3 告警

至少覆盖：

- `/r/*` 降级率或 5xx 突增；
- 访问写入失败；
- 注册绑定拒绝率异常；
- 任一站点数据流超过两倍同步周期未成功；
- 适配器契约错误；
- 充值或退款同步量相对基线异常为零或突增。

## 20. 性能与可用性

- `/r/*` 不调用 Sub2API/NewAPI 业务接口，只访问 Growth PostgreSQL 和本地配置缓存。
- 链接元数据可以短期缓存，但暂停和归档必须在可接受时间内生效；建议缓存不超过 30 秒并支持主动失效。
- redirect-service 与分析查询使用独立连接池和资源限制，慢查询不能耗尽跳转连接。
- `/r/*` 服务目标可用性不低于品牌主页；具体 SLO 在压测后确定。
- V1 性能目标：redirect-service P95 服务端处理低于 150 ms，P99 低于 300 ms，不含公网和目标站点时间。
- 所有外部调用均有超时；redirect 请求链路不得等待同步 worker 或业务库。

## 21. 测试要求

### 21.1 单元测试

- code 生成、格式、冲突重试和不可复用；
- 链接状态与有效期判断；
- Cookie 签名、过期、篡改和密钥轮换；
- 同站点后一次链接覆盖前一次链接、跨站点全局末次触发，以及站点不匹配时不回退；
- bot、HEAD、内部探测和重复 request ID 排除；
- 站内路径规范化和开放重定向拦截；
- 注册绑定唯一性和重复回调；
- 调用、充值、二次充值、退款和继续调用口径；
- “未接入”、同步错误和真实零值的区分。

### 21.2 集成测试

- redirect-service + PostgreSQL 完整访问写入与 302；
- 父域 Cookie 在 `aiwelink.cc` 与 `api.aiwelink.cc` 网关之间可用；
- 若未来启用跨域 handoff，验证其验签、消费、清理 URL 和全局末次邀请更新；
- 注册成功时取得用户 ID 并绑定，注册失败时不绑定；
- Growth 绑定失败不改变业务注册结果；
- Sub2API/NewAPI 适配器重复同步结果幂等；
- worker 失败不推进游标，恢复后能继续；
- owner/admin 可访问，maintainer/viewer 与未登录用户被后端拒绝。

### 21.3 安全测试

- code 枚举、SQL 注入、开放重定向和 header 注入；
- Cookie 和 handoff token 篡改、重放、跨站点使用；
- CSRF、伪造角色、伪造服务身份；
- 日志、审计、错误响应和前端状态不泄露 DSN、密码、Session、Cookie 或 token；
- 大量机器人请求不会污染全局末次邀请和正式点击人数。

## 22. V1 端到端验收

### 22.1 标准链路

管理员创建：

```text
站点：AIWeLink API
渠道：小红书
活动：2026 年 7 月 API 推广
具体来源：某篇小红书帖子
```

系统生成：

```text
https://aiwelink.cc/r/7km4q2xd
```

使用非内部测试账号执行：

```text
点击链接
→ 注册成功
→ 第一次成功调用
→ 第一笔成功充值
→ 第二笔独立成功充值
→ 再次成功调用
```

在正常同步周期后必须满足：

1. `/r/*` 返回 302，设置合法匿名 Cookie，并记录一名计数访客。
2. 注册成功后账号唯一归属于该小红书帖子链接。
3. 链接汇总中注册、成功调用、充值、二次充值和继续调用均增加 1。
4. 充值总额等于两笔成功到账金额之和。
5. 点击每个里程碑数字都能在名单中找到该账号。
6. 账号详情中的站点、渠道、活动、帖子、注册时间、调用时间和两笔充值时间与业务事实一致。
7. 重放注册、调用和支付同步后，各指标不翻倍。

### 22.2 唯一归因验证

同一浏览器先访问链接 A，再访问同一站点链接 B，然后注册：

- A、B 都必须各自记录访问，B 覆盖 Session 当前邀请。
- 账号必须归因到 `registered_at` 之前 30 天内最后的有效链接 B。
- 注册后再次点击 A，账号归因仍保持 B。

同一浏览器先访问站点一的链接 A，再访问站点二的链接 C：

- 随后注册站点二账号时，可以归因到 C。
- 随后注册站点一账号时，不能回退到 A；按独立主页证据分类，否则来源为 `direct`。
- 两个站点相同字符串的 `external_user_id` 不得串号。

### 22.3 异常验证

- 注册失败：不产生账号归因。
- 已有账号登录：不产生新注册归因。
- 链接停用后新点击：不产生正式点击和归因候选。
- 数据库故障：仍安全跳转，但不错误归因。
- 同一订单状态回调两次：充值次数只增加一次。
- 成功调用后一次失败请求：不构成继续调用；第二次成功调用后才构成。
- 内部账号被排除后：原始数据保留，正式指标不包含该账号。

## 23. 上线前待确认项

1. `aiwelink.cc`、`api.aiwelink.cc` 和管理后台的实际反向代理控制权与部署方式。
2. 是否存在不受信任的 `*.aiwelink.cc` 子域，从而影响父域 Cookie 方案。
3. 当前 Sub2API 注册接口成功响应是否包含稳定用户 ID；若不包含，官方当前用户接口是否可用。
4. NewAPI 注册绑定的可靠扩展点和用户 ID 获取方式。
5. 各业务系统中“成功模型响应”“成功到账”“成功退款”的真实字段与状态机。
6. 首批启用站点、各站点默认落地路径和是否需要跨主域 handoff。
7. 生产同步周期最终取 1 分钟还是 5 分钟，以及预计访问和业务事件峰值。
8. 内部/测试账号的来源标签或首批显式排除名单。
9. 服务到服务认证采用 mTLS 还是可轮换签名凭据。

以上事项确认后，应分别形成部署说明和系统适配器字段映射文档。它们不会改变本需求中的唯一归因、数据最小化和业务指标口径。
