# 飞书姓名自动合并实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 飞书扫码登录只按飞书姓名与本地用户名自动识别，并把已有的同名待授权飞书账户安全合并到唯一的原有账户。

**Architecture:** 在 `resolve_feishu_user` 的登录解析阶段新增唯一姓名候选查询。未绑定的新身份直接绑定唯一候选；已存在且仍为待授权状态的飞书身份通过现有代理合并模型软停用源用户并关联到唯一目标用户。邮箱不再参与登录候选选择，同名歧义保持待授权状态。

**Tech Stack:** FastAPI, Motor/MongoDB, Python `unittest`, `AsyncMock`

---

### Task 1: 新身份按唯一用户名绑定

**Files:**
- Modify: `backend/tests/test_feishu_auth.py`
- Modify: `backend/app/modules/auth/feishu.py`

- [x] **Step 1: 写失败测试**

增加三个用例：唯一同名用户自动绑定且 `bound_via=feishu_name`；邮箱相同但姓名不同不绑定；多个同名候选不自动绑定。

- [x] **Step 2: 验证测试因缺少姓名解析而失败**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_feishu_auth.FeishuIdentityResolutionTests -v`
Expected: 新增的唯一姓名绑定用例失败，现有用例仍保持原行为。

- [x] **Step 3: 实现最小姓名候选解析**

新增内部函数查询最多两个活跃、已授权、未合并、未绑定的同名用户。登录身份未命中时只使用该函数，不再按邮箱查询；仅一个候选时进入原子绑定逻辑。

- [x] **Step 4: 运行目标测试**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_feishu_auth.FeishuIdentityResolutionTests -v`
Expected: 全部通过。

### Task 2: 已有待授权飞书账户按姓名自动合并

**Files:**
- Modify: `backend/tests/test_feishu_auth.py`
- Modify: `backend/app/modules/auth/feishu.py`
- Modify: `backend/app/modules/system/bootstrap.py`

- [x] **Step 1: 写失败测试**

构造已存在的“张可真”待授权飞书源用户和唯一同名原有用户，断言登录后返回原有用户、源用户被软停用、目标用户保存代理关系，并记录姓名合并审计。

- [x] **Step 2: 验证测试因当前直接登录待授权用户而失败**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_feishu_auth.FeishuIdentityResolutionTests.test_pending_identity_login_merges_into_unique_same_name_user -v`
Expected: 返回待授权源用户而不是原有用户。

- [x] **Step 3: 复用代理恢复实现姓名合并**

当已命中的身份属于可恢复待授权源用户且登录目的为 `login` 时，查找唯一同名目标并调用恢复逻辑；先原子占用目标，再软停用源用户，并支持下次登录续完中断状态。为 `feishu_identity.source_user_id` 建立部分唯一索引，防止同一个飞书临时身份占用两个本地用户。设置 `bound_via=feishu_name_recovery`，保留源用户历史；没有唯一目标时维持当前待授权登录。

- [x] **Step 4: 运行飞书认证与路由回归测试**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_feishu_auth tests.test_auth_routes -v`
Expected: 全部通过。

### Task 3: 全量验证与发布分支

**Files:**
- Verify: `backend/app/modules/auth/feishu.py`
- Verify: `backend/tests/test_feishu_auth.py`
- Verify: `docs/superpowers/specs/2026-08-19-feishu-name-user-merge-design.md`

- [x] **Step 1: 运行后端全量测试**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`
Expected: 0 failures, 0 errors。

- [x] **Step 2: 检查差异格式**

Run: `git diff --check`
Expected: 无输出，退出码 0。

- [x] **Step 3: 提交并推送飞书功能分支**

提交代码到 `codex/mandatory-feishu-binding` 并推送，新建目标为 `achernar/dev` 的 PR（原 PR #54 已合并）。
