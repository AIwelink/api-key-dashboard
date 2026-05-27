# Sub2Api JSON Contract

这个文档定义项目最重要的外部契约：sub2api JSON 结构不能被修改。

## Current Rule

MongoDB 中每个账号文档保存：

```js
{
  account_json: {},
  metadata: {}
}
```

- `account_json` 是 sub2api 的账号对象。
- `metadata` 是本系统的管理字段。
- 导出和推送 sub2api 时，只读取 `account_json`。
- `metadata` 不进入 sub2api 导出文件，除非后续明确需要。

## account_json Contract

`account_json` 必须保持 sub2api 账号对象结构：

```js
{
  name,
  platform,
  type,
  expires_at,
  auto_pause_on_expired,
  concurrency,
  priority,
  credentials,
  extra
}
```

必须保持：

- `credentials` 的嵌套位置。
- `extra` 的嵌套位置。
- 未知字段的字段名和值。
- 类似 `extra["2FA"]` 这样的原始字段名。

不允许：

- 导出时改字段名。
- 导出时改变嵌套层级。
- 把 `credentials` 展平到账号根字段。
- 把 `account_json.extra` 拆成系统级 `metadata`。
- 丢弃未知字段。
- 把 `"2FA"` 改成 `two_factor` 后导出。

## Export Shape

导出 sub2api 文件时生成顶层结构：

```json
{
  "exported_at": "2026-05-25T00:00:00.000Z",
  "proxies": [],
  "accounts": [
    {
      "name": "user@example.com",
      "platform": "openai",
      "type": "oauth",
      "expires_at": 1780380391,
      "auto_pause_on_expired": true,
      "concurrency": 10,
      "priority": 1,
      "credentials": {},
      "extra": {}
    }
  ]
}
```

其中 `accounts[]` 直接来自 MongoDB 的 `account_json`。

## Naming Note

这里有两个容易混淆的概念：

```js
{
  account_json: {
    extra: {}
  },
  metadata: {}
}
```

- `account_json.extra` 是 sub2api 原始 JSON 的一部分，必须原样保留。
- 根级 `metadata` 是本系统管理信息，例如上传人、支付类型、备注和 sub2api 观测状态。
