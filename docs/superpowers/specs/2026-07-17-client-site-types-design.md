# 客户端站点类型设计

## 目标

将“账号池管理”收敛为“站点配置”，统一保存两类客户端连接：现有 sub2api 客户端和新增 newapi 客户端。现有站点无须迁移，缺少类型字段时按 `sub2api` 处理。

## 数据模型

站点记录新增：

- `site_type`: `sub2api` 或 `newapi`，默认 `sub2api`
- `admin_user_id`: newapi 必填，sub2api 不使用

两类站点继续共用 `id`、`name`、`base_url`、`token` 和 `status`。API Key 仍只返回 `token_configured`，不向前端返回密钥正文。

## 协议隔离

sub2api 的缓存刷新、Dashboard、TPM/RPM/并发采样、账号探测和 API 账号池页面只能读取 `sub2api` 类型。newapi 第一阶段只完成连接配置持久化，不调用 sub2api 管理接口。直接对 newapi 站点调用 sub2api 操作时返回明确错误。

## 页面

菜单和页面标题改为“站点配置”，主路径改为 `/site-configuration`，旧 `/pool-lifecycle` 继续映射到该页面。站点类型使用分段选择；选择 newapi 时显示必填 Admin User ID，隐藏 sub2api 专属刷新间隔、异常自动移除、额度估计和分组监控。

删除页面中的 sub2api 目标分组、备选池统计、当前推送目标和本地池历史配置区域。sub2api 站点仍保留同步、额度估计和分组监控配置。

## 验证

- 旧站点序列化为 `sub2api`
- newapi 缺少 `admin_user_id` 时拒绝保存
- newapi 配置可创建、读取和更新
- 后台 sub2api 作业排除 newapi
- 前端生产构建通过

