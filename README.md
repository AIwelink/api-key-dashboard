# API Key Dashboard

团队内部的 API/OAuth 账号、sub2api 账号池、容量风险、通知、事件和 Agent 运维管理系统。

仓库：

- SSH：`git@github.com:AIwelink/api-key-dashboard.git`
- HTTPS：`https://github.com/AIwelink/api-key-dashboard.git`
- 当前协作分支：`achernar/dev`

## 项目结构

```text
backend/     FastAPI + MongoDB 后端
frontend/    React + TypeScript + Vite 管理端
docs/design/ 现行设计、接口约定和历史方案
docs/agent/  Agent 分阶段设计
```

账号核心结构：

```js
{
  account_json: {},
  metadata: {}
}
```

`account_json` 保留 sub2api 外部结构，`metadata` 保存本系统的上传、生命周期、操作、站点和分析信息。

## 本地启动

从模板创建根目录配置，并填写 MongoDB、初始 Owner 和需要的站点参数：

```powershell
Copy-Item .env.example .env
```

后端：

```powershell
cd backend
python -m uv sync
python -m uv run python -m app.run
```

前端：

```powershell
cd frontend
npm install
npm run dev
```

默认前端地址为 `http://127.0.0.1:5173`。前端通过 `VITE_API_BASE_URL` 或同源 `/api` 调用后端。

## 验证

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

```powershell
cd frontend
npm test
npm run build
```

## 当前主要能力

- 登录、角色权限、后台用户和系统 Token。
- 账号上传、批量导入、账号列表、字段修正、凭证 JSON 更新和审计。
- 多 sub2api 站点配置、groups/accounts/usage 缓存、探测和手动同步。
- API 账号池概览、5h/7d 容量、TPM/RPM、实时可用时间、并发覆盖和 5 分钟回测样本。
- 远端账号推送、测试、删除退回、问题处理和 OAuth 复活流程。
- 分组额度、探测、通知、Uptime Kuma 和容量告警配置。
- 钉钉、Telegram、飞书通知通道及测试。
- newapi/sub2api 客户站点配置。
- 事件记录、异常告警、前台在线和 Agent 运维工作台。
- 页面级 60 秒静默刷新；移动端根路径优先进入 API 账号池状态页。

## 关键约定

- 账号唯一匹配依据是规范化邮箱或已保存的远端 ID，不使用展示 `name`。
- `status` 表示远端账号状态，`schedulable` 只是调度开关。
- 页面读取 MongoDB 缓存；只有明确的同步动作和后台任务访问远端 sub2api。
- 容量、并发和补号建议只计算当前 sub2api group，不再叠加本地备选池。
- 敏感凭证不得进入日志、审计详情或文档。

## 文档入口

- [设计文档索引](./docs/design/README.md)
- [开发与架构约定](./docs/design/14-development-guide.md)
- [API 账号池缓存设计](./docs/design/15-api-pool-status-cache.md)
- [实时容量与前台在线契约](./docs/design/30-api-pool-realtime-capacity-and-presence.md)
- [服务器更新命令](./docs/server-update-command.md)

生产环境更新前先确认当前分支和工作区状态；不要用 `git pull --ff-only` 覆盖存在本地提交且远端发生过强制更新的分支。
