# 推广链接双域名复制设计

## 目标

在流量分析的“推广链接”列表中，为同一个推广码提供两个可复制入口：

- 主页链接：`https://aiwelink.cc/r/{code}`
- API 站链接：`https://api.aiwelink.cc/r/{code}`

两个入口使用同一个推广码，因此渠道、活动、来源归因和运营数据保持不变。

## 交互设计

每条推广链接仍显示现有主页链接。在该链接文本后紧跟一个纵向操作组：

1. 第一行保留主页链接复制按钮，文案为“复制主页链接”。
2. 第二行新增 API 链接复制按钮，文案为“复制 API 链接”。

两个按钮上下排列，不移入右侧的编辑、启停操作区。复制成功提示分别为“主页推广链接已复制”和“API 推广链接已复制”；复制失败继续使用现有错误提示。

## 数据与实现边界

后端继续返回现有 `public_url` 和 `code`，数据库结构不变。前端使用 `code` 生成 API 链接：

```text
https://api.aiwelink.cc/r/{code}
```

该变化不创建第二条推广记录，不改变推广链接 ID，也不影响现有统计归因。

## 验收标准

- 推广链接列表中的每条记录显示两个上下排列的复制按钮。
- 两个按钮紧跟在链接文本后，不与编辑和启停按钮混排。
- “复制主页链接”写入现有 `https://aiwelink.cc/r/{code}`。
- “复制 API 链接”写入 `https://api.aiwelink.cc/r/{code}`。
- 两种复制操作具有可区分的成功提示。
- 现有推广链接创建、编辑、筛选和启停行为不变。
Follow-up layout decision (2026-08-12): render the homepage and API URLs as two separate clickable rows. Each row places its matching copy button immediately after the URL; the existing edit and enable/disable actions remain in the right-side action area.
