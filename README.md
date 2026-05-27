# API Key Backend Admin Panel

当前版本：`0.2.0`。

当前仓库包含：

- `docs/design`: 设计文档。
- `backend`: Python/FastAPI 后端初版。
- `frontend`: Vite + React + TypeScript 前端。

后端采用 MongoDB，账号数据结构为：

```js
{
  account_json: {},
  metadata: {}
}
```

项目使用根目录 `.env` 统一配置前后端端口、MongoDB、初始用户和 sub2api：

```powershell
Copy-Item .env.example .env
```

后端使用 `uv` 管理依赖：

```powershell
cd backend
python3 -m uv sync
python3 -m uv run python3 -m app.run
```

详见 [backend/README.md](./backend/README.md)。

前端使用 Vite 启动，构建后默认通过同源 `/api` 连接后端：

```powershell
npm.cmd --prefix frontend install
npm.cmd --prefix frontend run dev
```

默认访问地址为 `http://127.0.0.1:5173`。

端口和 API 地址统一写在根目录 `.env`：

- `BACKEND_HOST`、`BACKEND_PORT`、`FRONTEND_ORIGIN`
- `VITE_FRONTEND_HOST`、`VITE_FRONTEND_PORT`、`VITE_API_BASE_URL`

账号上传页同时承担添加和导入功能，支持标准 sub2api export JSON、账号数组、单个账号对象，以及 `{...} {...}` 这种连续 JSON 对象；账号列表页提供导出。

## 当前已完成能力

- 登录、后台用户管理、角色权限。
- 账号上传、导入预览、保存、列表、编辑和 sub2api JSON 导出。
- 审计日志和同步中心初版。
- sub2api Admin API 接入，使用 `x-api-key`。
- API 账号池状态页面，可查看 sub2api groups、账号调度状态、5h/7d 用量窗口和总体容量。
- MongoDB 持久缓存 sub2api groups/accounts，默认 5 分钟后台刷新。
- `数据库手动刷新` 会从远程 sub2api 同步到 MongoDB；`前端数据刷新` 只读取 MongoDB 缓存。
- 前端账号池缓存已处理切换闪烁，标题、总体容量和账号表始终对应同一个账号池查询。
- 可用池和使用备选池支持纯手动状态流转。
- 使用备选池支持手动推送账号到指定 sub2api 分组，并可立即执行账号测试。
- API 账号池状态支持远端账号测试、手动删除并回退到本地可用池或总库。

详见 [API 账号池状态与缓存设计](./docs/design/15-api-pool-status-cache.md)。

## 初版上线部署

当前初版准备部署到服务器时，建议先阅读：

- [初版上线部署汇总](./docs/design/23-initial-release-deployment.md)
- [开发与架构约定](./docs/design/14-development-guide.md)
- [菜单与页面职责](./docs/design/菜单.md)

服务器运行建议：

- 后端使用 `APP_ENV=production`、`LOG_PROFILE=production`。
- 前端执行 `npm run build` 后用 Nginx 托管 `frontend/dist`。
- Nginx 将 `/api` 反向代理到 FastAPI 后端。
- 如果 1Panel/Nginx 配置根路径冲突，也可以让 Nginx 整站反代到 FastAPI；后端会在存在 `frontend/dist/index.html` 时托管前端页面。
- MongoDB 上线前先做备份。
- 当前不会自动调度账号进入 sub2api；推送、测试、删除和回退均为人工触发。
