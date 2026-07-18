# sub2api 批量更新请求样例

> 本文件只保留 payload 结构示例。站点地址、账号 ID 和 group ID 必须来自当前环境，不要固定生产主机。

```json
{"account_ids":[852,853,854,855,856,857],"concurrency":10,"load_factor":10,"priority":100,"group_ids":[3]}
```

```http
POST <sub2api-base-url>/api/v1/admin/accounts/bulk-update
```
