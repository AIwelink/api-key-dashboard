# 0.2.0 版本标记与项目检查

本文记录 `0.2.0` 的版本范围、项目检查结果、已知风险和部署前动作。当前版本是团队内部测试版，目标是把“账号上传 -> 本地池流转 -> 手动推送 sub2api -> 测试 -> 手动删除回退”跑成闭环。

## 版本标记

- 后端版本：`backend/pyproject.toml` -> `0.2.0`。
- 前端版本：`frontend/package.json` -> `0.2.0`。
- 前端锁文件：`frontend/package-lock.json` -> `0.2.0`。
- 当前没有创建 git tag。原因是工作区仍有大量未提交文件，直接 tag 当前 HEAD 不能准确代表本次 `0.2.0` 代码状态。

## 0.2.0 范围

已完成：

- 登录、登录过期自动回到登录页、后台用户管理。
- 账号上传、批量解析、导入批次、账号列表、账号编辑、sub2api JSON 导出。
- 本地手动池状态：`library`、`available`、`reserve`、`active`、`problem`、`discarded`。
- 可用池和使用备选池页面，多选、分页、本页全选和批量操作。
- API 账号池状态，从 sub2api 同步 groups/accounts 到 MongoDB 缓存，前端读取统一缓存。
- 账号池逻辑管理，展示同步到本地的 sub2api 分组和容量摘要，暂不启用自动策略。
- 手动把本地账号推送到指定 sub2api 分组，支持 `concurrency=10`、`load_factor=10`、`priority=100` 默认参数。
- 手动测试本地推送后的远端账号，使用 sub2api test 接口并写回测试状态。
- API 账号池状态页可对远端账号执行手动测试。
- API 账号池状态页可手动删除远端账号，并退回本地 `available` 或 `library`。
- 推送、验证、远端删除均有动作锁，避免连续点击造成重复远端账号或重复删除。
- 后端日志系统支持开发期详细日志、生产期精简日志、轮转和自动清理。
- 审计日志记录关键账号操作、远端推送、远端测试和远端删除。
- 后端可托管 `frontend/dist`，支持 Nginx 整站反代到 FastAPI，方便 1Panel 部署。

暂不包含：

- 自动容量调度。
- 自动把可用账号推送到 sub2api。
- 自动发现 sub2api 问题账号并回写本地问题池。
- agent 自动决策。
- 多 sub2api 站点。
- Redis。

## 项目结构检查

后端：

- 语言与框架：Python 3.12+、FastAPI、Motor、MongoDB、uv。
- 入口：`backend/app/run.py`。
- Web 应用：`backend/app/main.py`。
- API 分层：`backend/app/routers/*`。
- 业务服务：`backend/app/services/*`。
- 配置来源：项目根目录 `.env`。

前端：

- 语言与框架：Vite、React、TypeScript。
- 入口：`frontend/src/main.tsx`。
- 主布局与菜单：`frontend/src/App.tsx`。
- 页面：`frontend/src/pages/*`。
- 通用组件：`frontend/src/components/*`。
- API 客户端：`frontend/src/api/client.ts`。

文档：

- 设计索引：`docs/design/README.md`。
- 部署文档：`docs/design/23-initial-release-deployment.md`。
- 手动推送与验证：`docs/design/24-sub2api-manual-push-verify.md`。
- 手动删除与回退：`docs/design/25-sub2api-account-return.md`。
- 远端 UI 与验证分组：`docs/design/26-remote-ui-and-verification-group.md`。

## 检查结果

已执行：

```powershell
cd frontend
npm run build
```

结果：通过。TypeScript 编译和 Vite 生产构建成功。

```powershell
cd backend
python3 -m uv run python3 -m compileall app
```

结果：通过。后端 `app` 包基础语法编译成功。

版本一致性：

- `frontend/package.json`：已是 `0.2.0`。
- `frontend/package-lock.json`：已同步为 `0.2.0`。
- `backend/pyproject.toml`：已是 `0.2.0`。

代码残留扫描：

- 未发现 `console.log`、`debugger`、`FIXME`。
- `TODO` 命中的是业务常量名，例如 `TODO_OPEN`，不是待修复注释。

## 关键逻辑检查

账号上传：

- 保持 `account_json` 原始结构，系统字段写入 `metadata`，同时必要字段也写入账号 JSON 内的 `extra`。
- 支持批量粘贴和宽容解析。
- 上传人和修改人由登录用户自动绑定。

本地池流转：

- `pool_status` 是本地账号当前状态的主字段。
- 可用池读取 `available`。
- 使用备选池读取 `reserve`。
- 问题账号读取 `problem`。
- 弃用账号读取 `discarded`。

sub2api 推送：

- 允许从 `available`、`reserve`、`problem` 手动推送。
- 不允许从 `library`、`active`、`discarded` 直接推送。
- 推送前会解析目标分组，优先使用用户选择的 group，其次使用账号 metadata 里已绑定的目标 group。
- 推送成功后刷新 sub2api 缓存，并校验远端账号确实进入目标 group。
- 如果发现远端重复账号已经在目标 group，会绑定现有远端账号。
- 如果远端重复账号在其他 group，会阻止推送，避免错误分组或重复账号。

sub2api 测试：

- 支持对本地推送账号执行测试，也支持 API 账号池状态页直接测试远端账号。
- 测试结果写入本地账号 metadata 或 sub2api 缓存。
- 当前模型默认由前端/请求传入，现阶段测试环境使用 `gpt-5.4-mini`。

sub2api 删除回退：

- 远端账号删除前会加远端删除锁。
- 本地账号回退前会加本地回退锁。
- 正常删除可退回 `available`。
- 退回总库可写入 `library`。
- 删除时保留远端快照、远端状态、是否异常、删除原因、操作者和时间。

容量与同步：

- API 账号池状态和账号池逻辑管理读取同一份 MongoDB 缓存。
- 5h/7d 总体容量应由后端基于当前 group 的完整健康账号缓存计算，不按当前分页计算。
- `health: warning` 中属于 429/529 限流的账号，按当前设计可计入总体容量。

## 已知风险

1. 当前没有自动化测试套件。
   影响：只能通过构建、编译和人工流程验证发现问题。
   建议：0.2.x 增加后端服务层单元测试和前端关键页面 smoke test。

2. 当前工作区未提交文件很多。
   影响：不能安全创建 git tag；release zip 和源码状态可能不完全等价。
   建议：部署前先清理不需要纳入版本的文件，再提交并 tag。

3. 根目录已有 `releases/api-key-admin-initial-20260526-155857.zip`。
   影响：这是旧打包产物，可能不包含 `0.2.0` 最新修改。
   建议：完成本次检查后重新打包，命名为 `api-key-admin-0.2.0-YYYYMMDD-HHMMSS.zip`。

4. sub2api 接口依赖测试环境。
   影响：本地构建不能证明远端推送、测试、删除一定成功。
   建议：部署前在测试环境按“推送 -> 测试 -> 删除回退”完整跑 1 个账号。

5. 账号 JSON 明文存储。
   影响：MongoDB、备份文件、日志和导出文件都按敏感数据处理。
   建议：服务器权限最小化，备份加密，禁止把真实 `.env` 和数据库备份放入仓库。

6. 当前仍是单 sub2api 站点模型。
   影响：后续多站点需要扩展站点配置、缓存隔离和前端选择逻辑。
   建议：继续保留 `site_id` 字段，不要写死到业务账号 metadata 之外。

## 上线前清单

- 确认 `.env` 使用生产值，尤其是 `APP_SECRET_KEY`、MongoDB、sub2api token。
- 确认 `APP_ENV=production`。
- 确认 `LOG_PROFILE=production`。
- 执行 MongoDB 备份。
- 执行 `python3 -m uv sync`。
- 执行 `python3 -m uv run python3 -m compileall app`。
- 执行 `npm install`。
- 执行 `npm run build`。
- 用 Nginx 托管 `frontend/dist`，并把 `/api` 反向代理到 FastAPI。
- 如果 1Panel 根路径配置冲突，可改为 Nginx 整站反代到 FastAPI，由后端托管 `frontend/dist`。
- 启动后检查 `/health`。
- 登录后检查用户管理、账号上传、账号列表。
- 同步 sub2api 缓存，检查 API 账号池状态和账号池逻辑管理是否读取同一批更新时间。
- 选 1 个测试账号完整执行：进入可用池 -> 进入使用备选池 -> 推送指定 group -> 测试 -> 删除退回可用池。

## 后续建议

近期优先级：

1. 增加后端测试：账号状态流转、推送锁、删除锁、重复远端账号阻止。
2. 增加前端 smoke test：登录过期、列表分页、多选批量操作、确认弹窗。
3. 重新打包 `0.2.0`，排除 `.env`、日志、`node_modules`、`.venv`、旧 release 目录和 `__pycache__`。
4. 服务器部署后记录一次真实测试结果，补充到本文件或新增 `0.2.0-deployment-record.md`。
