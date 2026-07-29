# Frontend

前端已升级为 Vite + React + TypeScript。

## 使用方式

安装依赖：

```powershell
npm.cmd --prefix frontend install
```

前端读取项目根目录的 `.env`。如果还没有根目录 `.env`，可以从根目录 `.env.example` 复制：

```powershell
Copy-Item .env.example .env
```

启动开发服务器：

```powershell
npm.cmd --prefix frontend run dev
```

默认地址由根目录 `.env` 控制：

```text
VITE_FRONTEND_HOST=127.0.0.1
VITE_FRONTEND_PORT=5173
```

默认 API 地址也由根目录 `.env` 控制：

```text
VITE_API_BASE_URL=http://127.0.0.1:8000/api
```

也可以在浏览器控制台临时覆盖 API 地址：

```js
localStorage.setItem("apiBase", "http://127.0.0.1:8000/api")
```

然后刷新页面。

## 构建

```powershell
npm.cmd --prefix frontend run build
```

构建产物输出到 `frontend/dist`。

## 账号上传

账号上传页同时承担添加和导入功能，支持：

- 标准 sub2api export JSON：`{ "accounts": [...] }`
- 单个账号对象：`{ "name": "...", "credentials": {...} }`
- 账号数组：`[{...}, {...}]`
- 多个连续 JSON 对象：`{...} {...}`

后端只要求每个账号对象里存在 `credentials`。

账号列表页提供 sub2api JSON 导出。

## API 账号池状态

侧边栏入口：`API 账号池状态`。

页面能力：

- 选择 API 站点，当前默认站点为 `sub2api 5002`。
- 查看 groups 汇总：分组数、总账号、活跃账号、限流账号。
- 查看账号池账号表：名称、邮箱、平台/类型、容量、状态、调度、分组、用量窗口、最近使用、过期时间。
- 展示 `5h 总体容量` 和 `7d 总体容量`，只计算健康且可调度账号。
- `同步账号池数据`：触发后端从远程 sub2api 写入统一 MongoDB 缓存。
- `前端数据刷新`：只重新读取后端 MongoDB 缓存。

缓存行为：

- 页面切换会保留前端缓存。
- 切换不同账号池时会优先使用账号页缓存。
- 任意页面完成 sub2api 同步后，相关页面缓存会失效并重新读取统一 MongoDB 缓存。
- 未命中缓存时显示加载态，不显示错误的 `0 个可用账号`。
- 账号表和总体容量必须匹配同一个账号池查询 key。

## 流量分析

流量分析功能仅对 `owner/admin` 开放：

- `/traffic-analysis-config`：配置 Growth PostgreSQL 数据库、测试连接、查看 Schema 状态，并在确认后显式初始化数据库。保存配置不会自动初始化或修改数据库结构。
- `/traffic-analysis`：管理站点、渠道、活动和追踪链接。

页面只展示后端返回的真实配置数据，不展示尚未采集的点击、注册或付费指标。
