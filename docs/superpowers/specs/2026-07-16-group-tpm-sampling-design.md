# 分组 TPM 分钟采样设计

## 目标

为每个 sub2api 分组按一分钟频率记录 TPM，提供比小时费用趋势更及时的消耗速度信号。采样只按 `group_id` 保存，不额外保存站点汇总，也不请求每个账号的 `/usage`。

## 数据来源

每个活动站点的每个有效缓存分组，每分钟调用一次：

```text
GET /api/v1/admin/dashboard/snapshot-v2
```

请求参数固定为轻量统计模式：

```text
start_date=<Asia/Shanghai 当天日期>
end_date=<Asia/Shanghai 当天日期>
granularity=hour
group_id=<分组 ID>
include_stats=true
include_trend=false
include_model_stats=false
include_group_stats=false
include_users_trend=false
timezone=Asia/Shanghai
```

主要读取 `data.stats.tpm`、`rpm`、`total_tokens`、各类累计 Token 和 `stats_updated_at`。服务端 `tpm` 是主值；当它缺失或无法解析时，使用同一站点、同一分组相邻两次累计 `total_tokens` 的差值，按实际间隔分钟数计算备用 TPM。累计值回退时不产生负数，计数器重置后的首个样本标记为不可计算。

## 存储模型

新增 `sub2api_tpm_samples` 集合，每个文档代表一个分组的一分钟采样：

- `_id`: `<site_id>:<group_id>:<UTC 分钟>`
- `site_id`
- `group_id`
- `bucket_at`: 向下取整到 UTC 分钟
- `sampled_at`
- `stats_updated_at`
- `tpm`: 最终采用的 TPM
- `reported_tpm`: sub2api 返回的 TPM
- `calculated_tpm`: 累计 Token 差值计算的 TPM
- `rpm`
- `total_tokens`
- `input_tokens`
- `output_tokens`
- `cache_creation_tokens`
- `cache_read_tokens`
- `token_delta`
- `elapsed_seconds`
- `source`: `reported`、`calculated` 或 `unavailable`
- `expires_at`: 采样时间后 14 天

唯一索引覆盖 `site_id + group_id + bucket_at`，同一分钟重复执行时覆盖该分钟样本。`expires_at` 使用 TTL 索引自动清理。

## 调度行为

应用启动后创建独立 TPM 采样循环，按 60 秒周期运行。每轮读取活动站点及其缓存分组；同一站点内的分组请求并行执行，不同站点也可以并行执行。

采样任务与账号缓存刷新、账号探测和 dashboard 小时趋势刷新完全独立。进程内锁阻止同一站点采样轮次重叠；数据库唯一键处理偶发重复写入。单个分组失败只记录警告和失败计数，不影响其他分组，也不影响主服务。

## 后续容量信号

本次先稳定记录分钟数据。容量模型后续按当前选中分组读取：

- 当前一分钟 TPM
- 最近 5 分钟 TPM 均值
- 最近 15 分钟 TPM 均值
- 最近 60 分钟 TPM P90
- 连续上涨分钟数、趋势斜率和加速度

这些指标不跨分组汇总，避免其他分组的流量污染当前账号池判断。

## 测试范围

- 每分钟采样只携带对应 `group_id`
- 服务端 TPM 正常时优先使用 `reported_tpm`
- TPM 缺失时按累计 Token 差值和真实时间间隔计算
- 累计计数器下降时不产生负 TPM
- 不同站点和分组的历史样本互不影响
- 同一分钟重复采样执行幂等覆盖
- 同一站点重叠执行被跳过
- 单个分组请求失败不阻断同轮其他分组
- 调度任务在应用退出时正确取消

## 不在本次范围

- 不逐账号采集 TPM
- 不保存无 `group_id` 的站点汇总样本
- 不立即替换现有美元容量预估公式
- 不在本次增加新的前端图表
