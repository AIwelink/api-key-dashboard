# 内部人员删除与运营重分类实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成内部人员新增、编辑、删除的前后端闭环，并让运营概览默认排除内部人员、历史聚合与缓存保持一致。

**Architecture:** 保留现有 Growth PostgreSQL 事实表和聚合表。删除在同一站点锁定事务中解除快照绑定、删除配置并重算完整历史聚合；路由负责权限与审计，React 页面负责二次确认和刷新。

**Tech Stack:** FastAPI, SQLAlchemy async, PostgreSQL, React 19, TypeScript, Vitest, Python unittest。

---

### Task 1: 为删除闭环补充失败测试

**Files:**
- Modify: `backend/tests/test_operations_repository.py`
- Modify: `backend/tests/test_operations_routes.py`
- Modify: `frontend/src/pages/OperationsManagementPage.test.tsx`

- [ ] **Step 1: 写后端 repository 删除测试**

断言删除 SQL 先清除 `growth.ops_user_snapshots` 绑定，再删除 `growth.internal_users`，并返回删除前记录。

- [ ] **Step 2: 写 service/route 权限与审计测试**

断言删除服务获取站点锁并重算完整历史；路由只允许 owner/admin，成功写入 `operations.internal_user.delete`，越权返回 403。

- [ ] **Step 3: 写前端删除渲染测试**

断言 owner/admin 的内部人员表包含删除入口、历史重算提示和确认文本；operator 不包含删除入口。

- [ ] **Step 4: 运行测试确认失败**

```text
backend/.venv/Scripts/python.exe -m unittest tests.test_operations_repository tests.test_operations_routes
frontend: npm test -- --run src/pages/OperationsManagementPage.test.tsx
```

预期：新增删除相关断言失败，因为删除 API、删除 repository 方法和前端操作尚未存在。

### Task 2: 实现后端删除事务

**Files:**
- Modify: `backend/app/modules/operations/repository.py`
- Modify: `backend/app/modules/operations/service.py`
- Modify: `backend/app/routers/operations.py`

- [ ] **Step 1: 添加 repository 删除函数**

在一个 SQL 语句中使用 CTE：读取目标记录，清除快照的 `is_internal`/`internal_user_id`，删除配置并返回删除前安全字段。

- [ ] **Step 2: 添加 service 删除函数**

复用 `growth_connection(write=True)`、站点权限校验和 `acquire_operations_sync_lock`；调用删除后以 `HISTORICAL_CONVERSION_RATE_START` 到当前时间重算聚合，再清理站点缓存。

- [ ] **Step 3: 添加 DELETE 路由**

使用 `DELETE /operations/internal-users/{internal_user_id}`，调用 `_require_operations_writer`，将不存在映射为 404，成功写入删除审计日志。

### Task 3: 完成前端删除交互

**Files:**
- Modify: `frontend/src/pages/OperationsManagementPage.tsx`
- Modify: `frontend/src/pages/OperationsManagementPage.test.tsx`

- [ ] **Step 1: 增加删除状态和处理函数**

删除前打开现有 `ConfirmDialog`，展示站点、邮箱、业务用户 ID 和历史数据重算警告；确认后调用 DELETE API，刷新内部人员列表并提示结果。

- [ ] **Step 2: 增加表格删除入口**

owner/admin 显示危险操作按钮，operator 不渲染；按钮必须有 `aria-label`，不改变现有编辑入口。

### Task 4: 验证与交付

**Files:**
- No new source files.

- [ ] **Step 1: 运行后端完整测试**

```text
backend/.venv/Scripts/python.exe -B -m unittest discover -s tests
```

- [ ] **Step 2: 运行前端测试和构建**

```text
npm test -- --run
npm run build
```

- [ ] **Step 3: 检查 diff 和工作树**

```text
git diff --check
git status --short
```

- [ ] **Step 4: 提交并推送分支**

在当前 `achernar/dev` 分支提交全部相关改动并推送到 `origin`，然后创建 PR，描述默认普通用户、缓存/聚合一致性和内部人员删除行为。

