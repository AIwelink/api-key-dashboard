# Frontend Design

## 信息架构

```text
Login
Dashboard
Accounts
  - List
  - Detail
  - Create / Edit
  - Import Preview
API Account Pools
Sync Center
Audit Logs
Settings
  - Users
  - Team
  - sub2api
  - Sync Policy
```

## 页面设计

### Dashboard

展示系统整体状态：

- 账号总数。
- active / paused / expired / invalid 数量。
- 最近同步状态。
- 最近失败任务。
- 即将过期账号。

### Accounts List

表格字段：

- 名称。
- 平台。
- 类型。
- 邮箱。
- 状态。
- 计划类型。
- 过期时间。
- 并发。
- 优先级。
- sub2api 状态。
- 最近检查时间。
- 操作按钮。

筛选条件：

- 状态。
- 平台。
- 类型。
- 标签。
- 是否过期。
- 是否同步异常。

### Account Detail

分区：

- 基础信息。
- 期望状态。
- sub2api 观测状态。
- 账号 JSON。
- 最近同步事件。
- 最近审计记录。

MVP 阶段账号 JSON 明文保存。前端是否完整展示凭据，可以后续按角色权限细化。

### Create / Edit Account

表单字段：

- name
- platform
- type
- email
- plan_type
- concurrency
- priority
- auto_pause_on_expired
- credentials
- account_json.extra
- metadata
- tags

`account_json.extra` 是 sub2api 原始 JSON 的一部分；根级 `metadata` 是本系统管理字段。前端需要清楚区分这两个区域。

### Import Preview

导入不是直接入库，而是分两步：

1. 解析并预览。
2. 确认提交。

预览列表需要标明：

- create
- update
- conflict
- invalid
- skip

### Sync Center

展示：

- 同步任务列表。
- 每次任务摘要。
- 失败详情。
- dry run 预览。
- 手动同步按钮。

### API Account Pools

页面名称：`API 账号池状态`。

目标：

- 查看远程 sub2api 站点的 groups 和账号调度状态。
- 区分远程数据库缓存刷新和前端读取缓存刷新。
- 为后续自动补位、账号池策略和封禁替换提供观测基础。

顶部区域：

- API 站点选择。
- 站点 URL、密钥是否配置、最后刷新时间。
- 自动刷新分钟数配置。
- `测试连接`。
- `同步账号池数据`。

group 区域：

- 分组数、总账号、活跃账号、限流账号。
- 账号池下拉选择。
- 横向 group tab。

账号池详情：

- 当前账号池标题，标题右侧放 `前端数据刷新`。
- 状态筛选。
- 当前页、健康、警告、异常统计。
- `5h 总体容量` 和 `7d 总体容量` 两条整体进度条。
- 账号表格列：名称、平台/类型、容量、状态、调度、分组、用量窗口、最近使用、过期时间、操作。

刷新语义：

- `同步账号池数据`：调用后端远程刷新，把 sub2api groups/accounts 写入统一 MongoDB 缓存，完成后显示分组数和账号数。
- `前端数据刷新`：只读取后端 MongoDB 缓存，不访问远程 sub2api。
- 页面加载、切换页面、切换账号池不触发远程 sub2api 刷新。

缓存和切换：

- 前端缓存账号页，key 为 `siteId:groupId:page:pageSize:statusFilter`。
- 任意页面完成 sub2api 同步后，相关页面缓存失效并重新读取同一份后端缓存。
- 切换账号池时，如果命中缓存则立即显示对应数据。
- 未命中缓存时显示加载态，不显示错误的 `0 个可用账号`。
- 账号表必须匹配当前账号页 key；总体容量读取当前 group 的后端 `capacity_summary`，不能按当前页账号重新计算。

### Settings

后台用户管理：

- 用户列表。
- 添加用户。
- 修改角色。
- 禁用和启用用户。
- 重置密码。
- 查看用户最近登录时间。

系统不展示注册入口。登录页只提供登录和受控的设置密码入口。

sub2api 设置：

- base URL
- auth token
- 测试连接
- 默认同步策略

同步策略：

- 自动同步开关。
- 检查频率。
- 过期自动暂停。
- 删除策略。
- 冲突策略。

## 交互原则

- 危险操作需要确认。
- 批量操作先预览。
- 默认不显示完整密钥。
- 保存成功后显示明确状态。
- 同步失败展示可读错误，而不是只显示 raw response。
- 表格要支持搜索、筛选和分页。
- 账号池页面切换 group 时必须保持标题、后端容量汇总和账号表属于同一个账号池。

## 状态颜色建议

```text
active: green
paused: gray
expired: amber
invalid: red
deleted: muted
syncing: blue
```

颜色只作为辅助，必须同时显示文本状态。

## Account upload fields

账号创建和编辑页面需要支持以下字段：

- 填入模式和解析模式，填入模式在前，解析模式在后。
- 解析模板，参数 `source_template`，默认 `sub2api`，购买账号金幺模板为 `purchased_jinyao`。
- 是否自产，参数 `self_produced`，布尔值：`true` 表示自产，`false` 表示购买。
- 购买来源，参数 `purchase_source`；当 `self_produced = false` 时显示并必填，金幺模板默认填入“金幺”；编辑时即使改回自产也保留历史购买来源。
- 购买时账号类型，参数 `purchase_account_type`；当 `self_produced = false` 时必填，金幺模板默认 `free`；用于区分“购买时 free、后续升级为 plus”的账号轨迹。
- 邮箱和接码 session，参数 `email_session`。
- 账号类型，参数 `account_type`，选项顺序为 plus、free、pro、其他。
- 支付类型，参数 `payment_type`，选项顺序为 PayPal 一卡多号、PayPal 一卡一号、不绑卡、gopay、其他。
- 2FA，参数 `2FA`，选填。
- 是否绑定手机，参数 `phone_bound`，布尔值：`true` 表示是，`false` 表示否。
- 手机号，参数 `phone_number`；当 `phone_bound = true` 时建议填写。
- 备注，参数 `remark`；可补充说明 `phone_bound` 布尔值判断依据。
- 账号 JSON 文件或粘贴 JSON。
- 账户状态标注。

解析模式需要先选择模板、导入 JSON，解析出账号列表，然后逐个账号补充缺失字段并保存。解析区域下方需要展示字段说明，包括字段作用和是否必填。

购买账号：金幺模板需要适配平铺 JSON。前端解析后生成 sub2api 账号对象：token、账号 ID 等写入 `credentials`，原始购买字段写入 `account_json.extra`。其中 `mailbox_connection` 自动作为 `email_session`，手机号自动作为 `phone_number`，并默认 `phone_bound = true`。

系统字段不允许用户编辑：

- 创建时间。
- 更新时间。
- 当前登录用户 ID。

sub2api 观测字段只读展示：

- 账户状态。
- 已使用额度。
- 最后请求时间。
- 最后检查时间。
- 最近错误。

列表页建议增加列：

- 上传人。
- 支付类型。
- 人工状态标注。
- 已使用额度。
- 最后请求时间。

`account_json` 支持上传 JSON 文件或粘贴 JSON 内容。前端只负责提交原始结构，不做字段改名或结构转换。支付类型、人工标注和备注等信息会同时写入根级 `metadata` 和 `account_json.extra`，上传人和修改人由后端自动绑定当前登录用户。
