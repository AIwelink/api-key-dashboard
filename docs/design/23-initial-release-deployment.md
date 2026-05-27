# 初版上线部署汇总

本文用于把当前初版打包到服务器运行。当前版本目标是团队内部试用，重点保证账号上传、账号列表、手动池流转、sub2api 状态查看、手动推送/测试/回退和日志审计可用。

## 当前版本范围

已完成：

- 登录、后台用户管理，不开放公开注册。
- 账号上传、批量解析、导入批次、账号列表、账号编辑。
- sub2api JSON 导出，保持 `account_json` 原始结构。
- 手动状态流转：`library`、`available`、`reserve`、`active`、`problem`、`discarded`。
- 待办与处理：当前已有 free 升 plus 待办页。
- API 账号池状态：从 sub2api 同步 groups/accounts 到 MongoDB 缓存，再由前端读取。
- 账号池逻辑管理：当前展示统一 sub2api 分组缓存和本地 reserve 汇总，自动化策略暂不启用。
- 手动推送账号到指定 sub2api 分组，支持并发、负载因子、优先级参数。
- 手动测试 sub2api 远端账号，记录测试状态、模型、响应摘要和测试时间。
- 手动删除 sub2api 远端账号，并回退到本地可用池或总库，保留远端快照和删除审计。
- 用户管理、审计日志、开发期详细日志和自动清理。

暂不包含：

- 自动调度账号进入 sub2api。
- 自动验证账号。
- 自动从 sub2api 问题账号写回本地库。
- Agent 自动决策。
- 多 sub2api 站点管理。
- Redis。

## 技术栈

- 后端：Python 3.12+、FastAPI、Motor、MongoDB、uv。
- 前端：Vite、React、TypeScript。
- 数据库：MongoDB。
- 运行配置：项目根目录 `.env`。
- 日志：后端 `logs/app.log`、`logs/access.log`、`logs/error.log`，支持轮转和按天清理。

## 目录建议

服务器上可以按下面结构放置：

```text
/opt/api-key-admin/
  .env
  backend/
  frontend/
  docs/
```

不建议上传：

- `.env`
- `backend/.venv`
- `frontend/node_modules`
- `frontend/dist` 以外的临时构建缓存
- `logs`
- `*.log`

如果是源码部署，服务器上重新执行依赖安装和前端构建。

## 环境变量

从根目录复制：

```bash
cp .env.example .env
```

生产或服务器初版建议：

```text
APP_ENV=production
APP_SECRET_KEY=<换成强随机字符串>

BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
FRONTEND_ORIGIN=https://your-domain.example

VITE_FRONTEND_HOST=127.0.0.1
VITE_FRONTEND_PORT=5173
VITE_API_BASE_URL=https://your-domain.example/api

MONGODB_URI=mongodb://user:password@host:27017/api_key_admin
# 或者使用拆分配置：
# MONGODB_HOST=localhost
# MONGODB_PORT=27017
# MONGODB_USER=
# MONGODB_PASSWORD=
# MONGODB_DB=api_key_admin

ACCESS_TOKEN_EXPIRE_MINUTES=10080

INITIAL_OWNER_EMAIL=<初始管理员邮箱>
INITIAL_OWNER_NAME=<初始管理员名称>
INITIAL_OWNER_PASSWORD=<初始管理员密码>

SUB2API_BASE_URL=http://<sub2api-host>:5002
SUB2API_TOKEN=<sub2api-admin-api-key>

LOG_PROFILE=production
LOG_LEVEL=INFO
LOG_DIR=logs
LOG_RETENTION_DAYS=14
LOG_MAX_BYTES=10485760
LOG_BACKUP_COUNT=10
LOG_REQUEST_BODY=false
LOG_SLOW_REQUEST_MS=1000
```

注意：

- `APP_SECRET_KEY` 不能使用默认值。
- `FRONTEND_ORIGIN` 必须和浏览器访问的前端域名一致，否则 CORS 会失败。
- `VITE_API_BASE_URL` 是前端构建时写入的 API 地址，改动后需要重新 `npm run build`。
- 初始 Owner 只会在 `users` 集合为空时创建。已经有用户后，修改 `INITIAL_OWNER_*` 不会覆盖现有用户。

## 后端部署

进入后端目录：

```bash
cd /opt/api-key-admin/backend
python -m uv sync
python -m uv run python -m app.check_mongo
python -m uv run python -m compileall app
```

启动：

```bash
python -m uv run python -m app.run
```

`app.run` 会读取根目录 `.env`：

- `APP_ENV=development` 时启用 reload。
- `APP_ENV=production` 时不启用 reload。
- 监听地址和端口来自 `BACKEND_HOST`、`BACKEND_PORT`。

建议用 systemd 或进程管理器托管。systemd 示例：

```ini
[Unit]
Description=API Key Admin Backend
After=network.target

[Service]
WorkingDirectory=/opt/api-key-admin/backend
ExecStart=/usr/bin/python3 -m uv run python -m app.run
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

实际服务器上如果 `python` 或 `uv` 路径不同，需要按服务器路径调整。

## 前端部署

进入前端目录：

```bash
cd /opt/api-key-admin/frontend
npm install
npm run build
```

构建产物：

```text
frontend/dist
```

推荐方式是用 Nginx 托管静态文件，并把 `/api` 反向代理到后端。

Nginx 示例：

```nginx
server {
    listen 80;
    server_name your-domain.example;

    root /opt/api-key-admin/frontend/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /health {
        proxy_pass http://127.0.0.1:8000/health;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

如果暂时不用 Nginx，也可以用 Vite preview 验证构建产物：

```bash
npm run preview
```

但正式运行更建议使用 Nginx。

如果 1Panel 的站点配置里根路径代理和静态 `location /` 冲突，也可以采用整站反代方式：

```nginx
server {
    listen 80;
    server_name your-domain.example;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

这种方式要求服务器上已经执行过：

```bash
cd /opt/api-key-admin/frontend
npm install
npm run build
```

后端会读取项目根目录下的 `frontend/dist/index.html` 并托管前端页面；`/api/*` 仍然保持后端 API 语义。

## 启动后检查

后端健康检查：

```bash
curl http://127.0.0.1:8000/health
```

预期：

```json
{"status":"ok"}
```

登录检查：

- 打开前端域名。
- 使用 `.env` 中的 `INITIAL_OWNER_EMAIL` 和 `INITIAL_OWNER_PASSWORD` 登录。
- 如果已有用户库，则使用数据库中已有 Owner/Admin 登录。

MongoDB 检查：

- 后端启动日志应出现 `app_started`。
- 首次启动且用户表为空，应自动创建初始 Owner。
- `users`、`accounts`、`audit_logs`、`import_batches`、`sub2api_*` 集合会按使用逐步创建。

sub2api 检查：

- 进入 `API 账号池状态`。
- 点击 `测试连接`。
- 点击 `同步账号池数据`。
- 确认 groups、账号列表、5h/7d 窗口和最后同步时间更新。

上传检查：

- 进入 `上传账号`。
- 使用解析模式导入一个测试 JSON。
- 保存后到 `账号列表` 检查是否进入 `library`。
- 从账号列表或可用池页面做一次手动状态流转。

日志检查：

```bash
ls -lh /opt/api-key-admin/logs
tail -f /opt/api-key-admin/logs/app.log
tail -f /opt/api-key-admin/logs/error.log
```

## 当前手动流程说明

当前池操作以人工确认为主：

```text
上传账号 -> library
账号列表手动移入 -> available
可用池手动加入 -> reserve
使用备选池手动推送 sub2api -> active
人工发现问题 -> problem
人工决定不用 -> discarded
```

重要限制：

- `active` 应尽量由“手动推送 sub2api 成功”写入，避免本地状态和远端状态不一致。
- API 账号池状态页展示的是 sub2api 远端缓存，不等同于本地 `accounts`。
- sub2api 远端独有的问题账号，还不会自动进入本地待办。
- 批量操作当前逐条执行，可能出现部分成功；上线初版操作时建议先小批量执行。

## 数据备份

初版上线前后建议备份 MongoDB：

```bash
mongodump --uri="mongodb://user:password@host:27017/api_key_admin" --out /backup/api-key-admin-$(date +%F)
```

恢复：

```bash
mongorestore --uri="mongodb://user:password@host:27017/api_key_admin" /backup/api-key-admin-YYYY-MM-DD/api_key_admin
```

账号 JSON 当前明文保存，备份文件必须按敏感数据处理。

## 更新发布流程

推荐顺序：

1. 停止后端服务。
2. 备份 MongoDB。
3. 更新代码。
4. 检查 `.env` 是否仍正确。
5. 后端执行：

```bash
cd backend
python -m uv sync
python -m uv run python -m compileall app
```

6. 前端执行：

```bash
cd frontend
npm install
npm run build
```

7. 启动后端。
8. 重载 Nginx。
9. 执行启动后检查。

## 回滚思路

如果新版本启动失败：

- 保留当前 `.env`。
- 回退代码到上一个可运行版本。
- 重新 `uv sync` 和 `npm run build`。
- 如果数据已经被错误写入，再用 MongoDB 备份恢复。

当前版本没有数据库迁移脚本，大部分集合和索引由后端启动时自动确保。正式引入破坏性数据变更前，需要单独补迁移文档和备份流程。

## 初版上线清单

- `.env` 已填写真实生产值。
- `APP_SECRET_KEY` 已替换。
- `APP_ENV=production`。
- `LOG_PROFILE=production`。
- MongoDB 可连接，且已完成备份策略。
- Nginx `/api` 反向代理可用。
- 前端 `VITE_API_BASE_URL` 指向服务器 API。
- 后端 `/health` 正常。
- 初始管理员能登录。
- sub2api 测试连接成功。
- sub2api 同步账号池数据成功。
- 上传、列表、编辑、手动流转能完成一次闭环。
- 明确团队成员：当前没有自动调度，sub2api 推送、测试、删除和回退都需要人工触发。
