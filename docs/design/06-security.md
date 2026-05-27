# Security Design

当前 MVP 按用户要求采用简化方案：账号 JSON 和附加信息直接明文存储在 MongoDB，不拆分密钥表，不做字段级加密。

## Current Storage Policy

```text
MongoDB accounts
  - account_json: 明文保存 sub2api 账号 JSON
  - metadata: 明文保存本系统管理字段
```

这意味着以下内容都会明文存在 MongoDB：

- OpenAI API key
- OAuth access token
- OAuth refresh token
- 2FA secret
- 邮箱和接码 session
- sub2api 账号 JSON 中的 credentials
- sub2api 账号 JSON 中的 extra
- `.env` 中的 sub2api admin key，也就是 `SUB2API_TOKEN`

## Risk Note

明文存储可以显著降低开发复杂度，但 MongoDB 一旦泄露，账号凭据会同时泄露。

因此即使 MVP 明文存储，也至少需要做到：

- MongoDB 不暴露公网。
- 使用强密码和最小权限数据库账号。
- 生产环境开启访问白名单或内网访问。
- 不在日志里打印完整 `account_json`。
- 不在审计日志里复制完整凭据。
- 定期备份，并保护备份文件。

## Login and Registration

系统不提供公开注册入口。

用户创建方式：

1. Owner 或 Admin 在后台创建用户。
2. 系统生成一次性设置密码 token。
3. 用户设置密码后才可以正常登录。
4. 首次登录可强制修改密码。

安全要求：

- 密码使用 Argon2id 或 bcrypt 哈希保存。
- 登录失败需要计数和限速。
- 禁用用户不能登录。
- 重置密码 token 只能使用一次，并设置短有效期。
- 创建用户、重置密码、禁用用户都必须写入审计日志。

## Permission Rules

| 操作 | Owner | Admin | Maintainer | Viewer |
| --- | --- | --- | --- | --- |
| 查看账号列表 | yes | yes | yes | yes |
| 添加后台用户 | yes | yes | no | no |
| 修改用户角色 | yes | yes | no | no |
| 禁用用户 | yes | yes | no | no |
| 添加账号 | yes | yes | yes | no |
| 编辑账号 | yes | yes | yes | no |
| 删除账号 | yes | yes | no | no |
| 执行同步 | yes | yes | yes | no |
| 修改 sub2api 配置 | yes | no | no | no |
| 查看审计日志 | yes | yes | no | no |

## Audit Requirements

必须记录：

- 创建后台用户。
- 修改用户角色。
- 禁用或启用用户。
- 重置密码。
- 创建账号。
- 更新账号。
- 删除账号。
- 批量导入。
- 执行同步。
- 修改 sub2api 配置。
- 登录失败过多。

审计日志建议记录摘要，例如账号 ID、字段名、操作类型和时间，不建议复制完整 `account_json`。

## Future Hardening

如果后续需要提升安全等级，可以在不改变外部 JSON 结构的前提下增加：

- 字段级加密。
- 密钥版本管理。
- KMS 或环境变量主密钥。
- 完整密钥查看审批。
- 导出权限控制。
