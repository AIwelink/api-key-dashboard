# User Management

## 核心规则

系统需要登录后才能使用，不开放公开注册。

用户来源只有两种：

1. 后台管理员创建。
2. 后续版本通过受控邀请加入。

MVP 优先实现后台创建用户，不做公开注册页面。

## 用户创建流程

1. Owner 或 Admin 打开后台用户管理页。
2. 创建用户，填写邮箱、姓名和角色。
3. 系统创建 `pending_password_reset` 用户。
4. 系统生成一次性设置密码 token。
5. 用户通过设置密码链接完成初始密码设置。
6. 用户登录系统。

如果 MVP 暂不接邮件服务，可以让管理员复制一次性设置密码链接给用户。

## 登录页面

登录页只包含：

- email
- password
- login

不提供注册入口。

可以提供“设置密码”或“重置密码”入口，但它必须依赖后台生成的一次性 token。

## 角色

```text
owner
admin
maintainer
viewer
```

初始策略：

- Owner 可以管理所有用户和系统配置。
- Admin 可以创建普通用户、维护账号和执行同步。
- Maintainer 可以维护 API key / OAuth 账号，但不能管理用户。
- Viewer 只读。

需要后续确认：Admin 是否可以创建其他 Admin。

## 用户状态

```text
active
disabled
pending_password_reset
```

`disabled` 用户不能登录。

## 安全要求

- 不提供公开注册 API。
- 密码必须哈希保存。
- 一次性设置密码 token 只展示或发送一次。
- token 只保存哈希值，不保存明文。
- token 必须有过期时间。
- 用户创建、禁用、启用、角色变更、重置密码都进入审计日志。

