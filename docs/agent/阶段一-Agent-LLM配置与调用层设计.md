# 阶段一：Agent LLM 配置与调用层设计

本文档是 `账号池运营Agent总体架构.md` 的阶段一落地设计。

阶段一只做 Agent LLM 配置与调用层，不进入 Agent 主业务闭环。

## 1. 阶段目标

阶段一目标是先把 Agent 调用大模型的基础设施打牢。

具体目标：

- 在系统管理页面增加 Agent LLM 配置入口。
- 支持配置 OpenAI-compatible Base URL、API Key、Level 1 模型、Level 2 模型、temperature、timeout。
- 支持配置是否启用 Agent、是否启用 Agent loop、loop 间隔。
- 支持测试连接。
- 后端 LLM 调用层优先读取数据库配置。
- 环境变量只作为开发和兜底配置。
- 使用 LangChain 完成模型调用、Prompt 编排和结构化输出。
- 保持系统工程能力仍由 FastAPI / MongoDB / 前端承担。

阶段一不做：

- 不做 Agent 自动 loop 实际运行。
- 不做补号决策。
- 不做 Agent Context Pack。
- 不做 agent_runs / agent_messages / agent_decisions 入库。
- 不做钉钉自动通知。
- 不做复杂 ReAct 多轮循环。
- 不改账号池业务数据。

## 2. 当前项目可复用模式

### 2.1 系统管理页面

当前系统管理页面位于：

```text
frontend/src/pages/ApiTokensPage.tsx
```

当前已有两个 tab：

- 系统 Token。
- 通知。

阶段一建议在该页面新增第三个 tab：

```text
Agent LLM
```

不要新建独立一级菜单，避免系统管理入口分散。

### 2.2 后端 settings 路由

当前设置路由位于：

```text
backend/app/routers/settings.py
```

现有模式：

```text
GET   /api/settings/sync-policy
PATCH /api/settings/sync-policy
```

数据保存到：

```text
app_settings
```

阶段一沿用 settings 路由入口和审计模式，但 Agent LLM 配置不写入 app_settings，改写入独立集合：

```text
agent_llm_settings
```

新增：

```text
GET  /api/settings/agent-llm
PUT  /api/settings/agent-llm
POST /api/settings/agent-llm/test
```

### 2.3 通知配置的敏感字段处理

通知配置当前使用：

```text
backend/app/routers/notifications.py
backend/app/modules/notifications/service.py
frontend/src/pages/ApiTokensPage.tsx
```

可复用模式：

- 敏感字段保存到数据库。
- 返回前端时不返回明文。
- 前端只显示 `configured` 和 `preview`。
- 编辑时密码输入框留空表示不修改。
- 测试连接结果写回配置。
- 修改和测试写 audit log。

Agent LLM 配置应沿用该模式。

### 2.4 审计日志

当前审计方法：

```text
backend/app/modules/system/audit.py
write_audit_log(...)
```

阶段一新增配置保存和测试连接时都应写审计：

```text
settings.agent_llm.update
settings.agent_llm.test
```

## 3. 技术边界

阶段一必须保持边界清晰。

FastAPI / MongoDB / 前端负责：

- 配置页面。
- 权限。
- 配置保存。
- 配置读取。
- 配置测试接口。
- 审计日志。
- 敏感字段掩码展示。

LangChain 负责：

- 根据配置创建模型调用对象。
- 组织 Prompt。
- 调用 OpenAI-compatible 模型。
- 返回文本或结构化 JSON。

LLM 负责：

- 按 Prompt 返回结果。

阶段一不让 LangChain 负责：

- 数据库。
- 定时任务。
- 权限。
- 前端页面。
- 通知。
- Agent 运行记录。

## 4. 数据设计

### 4.1 集合

使用 Agent 独立集合，避免与现有系统配置集合冲突：

```text
agent_llm_settings
```

### 4.2 文档 ID

建议使用：

```text
_id = "agent_llm"
```

### 4.3 数据结构

建议保存结构：

```json
{
  "_id": "agent_llm",
  "enabled": true,
  "base_url": "https://example.com/v1",
  "api_key": "sk-...",
  "level1_model": "gpt-5.5",
  "level1_temperature": 0.2,
  "level2_model": "gpt-5.3-mini",
  "level2_temperature": 0.2,
  "timeout_seconds": 60,
  "loop_enabled": false,
  "loop_interval_seconds": 900,
  "last_test_at": "2026-06-30T00:00:00Z",
  "last_test_status": "success",
  "last_test_message": "ok",
  "updated_by": "...",
  "updated_at": "2026-06-30T00:00:00Z"
}
```

说明：

- 当前项目已有管理员权限边界，API Key 可以沿用现有系统配置存储方式。
- 不额外引入复杂密钥管理。
- 但前端响应中不得返回完整 API Key。
- `loop_enabled` 和 `loop_interval_seconds` 只作为配置项保存，阶段一不启动 loop。

### 4.4 前端返回结构

后端返回给前端时：

```json
{
  "enabled": true,
  "base_url": "https://example.com/v1",
  "api_key_configured": true,
  "api_key_preview": "sk-abc...xyz",
  "level1_model": "gpt-5.5",
  "level1_temperature": 0.2,
  "level2_model": "gpt-5.3-mini",
  "level2_temperature": 0.2,
  "timeout_seconds": 60,
  "loop_enabled": false,
  "loop_interval_seconds": 900,
  "last_test_at": "...",
  "last_test_status": "success",
  "last_test_message": "ok",
  "updated_at": "..."
}
```

不得返回：

```text
api_key
```

### 4.5 更新规则

前端编辑已有配置时：

- `api_key` 留空表示不修改。
- `api_key` 非空表示覆盖保存。
- `base_url`、模型名、temperature、timeout 等字段按提交值更新。

这与通知配置里 webhook / signing_secret 的更新体验保持一致。

## 5. 后端接口设计

### 5.1 获取 Agent LLM 配置

```text
GET /api/settings/agent-llm
```

权限：

```text
owner, admin
```

返回：

```json
{
  "enabled": false,
  "base_url": null,
  "api_key_configured": false,
  "api_key_preview": null,
  "level1_model": null,
  "level1_temperature": 0.2,
  "level2_model": null,
  "level2_temperature": 0.2,
  "timeout_seconds": 60,
  "loop_enabled": false,
  "loop_interval_seconds": 900,
  "last_test_at": null,
  "last_test_status": null,
  "last_test_message": null,
  "updated_at": null
}
```

如果数据库没有配置，应返回默认值。

默认值可以参考：

```text
enabled = false
level1_temperature = 0.2
level2_temperature = 0.2
timeout_seconds = 60
loop_enabled = false
loop_interval_seconds = 900
```

### 5.2 保存 Agent LLM 配置

```text
PUT /api/settings/agent-llm
```

权限：

```text
owner, admin
```

请求：

```json
{
  "enabled": true,
  "base_url": "https://example.com/v1",
  "api_key": "sk-...",
  "level1_model": "gpt-5.5",
  "level1_temperature": 0.2,
  "level2_model": "gpt-5.3-mini",
  "level2_temperature": 0.2,
  "timeout_seconds": 60,
  "loop_enabled": false,
  "loop_interval_seconds": 900
}
```

校验：

- `base_url` 最大长度建议 1000。
- `api_key` 最大长度建议 2000。
- `level1_model` 最大长度建议 200。
- `level2_model` 最大长度建议 200。
- `temperature` 范围建议 0 到 2。
- `timeout_seconds` 范围建议 5 到 300。
- `loop_interval_seconds` 范围建议 60 到 86400。
- 当 `enabled=true` 时，`base_url`、`api_key`、`level1_model` 至少应已配置。

审计：

```text
action = settings.agent_llm.update
resource_type = setting
resource_id = agent_llm
```

审计日志中不要写完整 API Key。

### 5.3 测试 Agent LLM 连接

```text
POST /api/settings/agent-llm/test
```

权限：

```text
owner, admin
```

行为：

- 读取数据库中的 Agent LLM 配置。
- 如果配置不完整，返回 400。
- 使用 LangChain 调用配置的 Level 1 模型。
- 发送一个极小测试 Prompt。
- 要求模型返回 JSON。
- 测试成功后更新 `last_test_at`、`last_test_status=success`、`last_test_message`。
- 测试失败后更新 `last_test_at`、`last_test_status=failed`、`last_test_message`。

测试 Prompt 示例：

```text
你是 AIwelink Agent LLM 连接测试。请只返回 JSON：{"ok": true, "message": "agent llm ready"}
```

成功响应：

```json
{
  "ok": true,
  "message": "agent llm ready",
  "settings": {}
}
```

失败响应：

```json
{
  "ok": false,
  "message": "HTTP 401"
}
```

注意：

- 错误信息要清洗，不能泄露 API Key。
- 测试失败可以返回 502。
- 测试失败也要写入配置的 last_test 字段。

审计：

```text
action = settings.agent_llm.test
resource_type = setting
resource_id = agent_llm
after = {"ok": true, "message": "..."}
```

## 6. 后端服务设计

### 6.0 当前后端目录约定

主分支已经重构后端业务代码位置。

当前约定：

```text
backend/app/modules/*
```

是新的业务代码目录。

早期设计中的 `backend/app/services/*` 已不是当前业务目录。新功能不应新增 `app.services` 依赖。

阶段一新增 Agent 配置和 LLM 调用代码应放在：

```text
backend/app/modules/agent/
```

router 仍然保留在：

```text
backend/app/routers/
```

### 6.1 建议新增文件

```text
backend/app/modules/agent/settings.py
```

职责：

- 读取 Agent LLM 配置。
- 保存 Agent LLM 配置。
- 返回前端安全视图。
- 合并数据库配置和环境变量兜底。
- 提供测试连接所需配置。

建议函数：

```python
async def get_agent_llm_settings(db) -> dict: ...
async def update_agent_llm_settings(db, payload, actor) -> dict: ...
async def test_agent_llm_settings(db, actor) -> dict: ...
async def get_agent_llm_runtime_settings(db) -> object: ...
def public_agent_llm_settings(document) -> dict: ...
def redact_api_key(value: str | None) -> str | None: ...
```

### 6.2 建议新增或调整 schema

在：

```text
backend/app/schemas.py
```

新增：

```python
class AgentLlmSettingsUpdate(BaseModel):
    enabled: bool = False
    base_url: str | None = Field(default=None, max_length=1000)
    api_key: str | None = Field(default=None, max_length=2000)
    level1_model: str | None = Field(default=None, max_length=200)
    level1_temperature: float = Field(default=0.2, ge=0, le=2)
    level2_model: str | None = Field(default=None, max_length=200)
    level2_temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    loop_enabled: bool = False
    loop_interval_seconds: int = Field(default=900, ge=60, le=86400)
```

### 6.3 调整 settings router

在：

```text
backend/app/routers/settings.py
```

新增：

```python
@router.get("/agent-llm")
@router.put("/agent-llm")
@router.post("/agent-llm/test")
```

权限沿用：

```python
require_roles("owner", "admin")
```

### 6.4 LangChain 调用适配层

建议新增：

```text
backend/app/modules/agent/llm_client.py
```

职责：

- 根据 Agent LLM settings 创建 OpenAI-compatible 调用客户端。
- 使用 LangChain Runnable / ChatPromptTemplate 编排调用。
- 提供通用 `invoke_json` 或 `ainvoke_json`。

阶段一可以只实现最小通用调用：

```python
async def test_agent_llm_connection(settings: dict) -> dict: ...
async def invoke_agent_level1_json(db, system_prompt: str, payload: dict) -> dict: ...
```

要求：

- 用 LangChain 做 Prompt 组装和调用链。
- 底层可以继续通过 OpenAI-compatible HTTP 调用。
- 如果项目暂时没有 `langchain_openai`，可以使用 `RunnableLambda` 包装现有 HTTP 调用，但调用链形态要保持 LangChain。

## 7. 配置读取优先级

Agent LLM 调用层读取配置时，优先级为：

```text
数据库 agent_llm_settings.agent_llm
-> 环境变量兜底
-> 未配置
```

环境变量只保留用于开发兜底：

```text
AGENT_LLM_BASE_URL
AGENT_LLM_API_KEY
AGENT_LEVEL1_MODEL
AGENT_LEVEL1_TEMPERATURE
AGENT_LEVEL2_MODEL
AGENT_LEVEL2_TEMPERATURE
AGENT_REQUEST_TIMEOUT_SECONDS
```

如果数据库中已配置字段，则优先使用数据库字段。

如果数据库没有配置，才读环境变量。

现有 Agent 分析链路已经接入该优先级：

```text
backend/app/modules/agent/orchestrator.py
-> backend/app/modules/agent/llm.py
-> backend/app/modules/agent/settings.py:get_agent_llm_runtime_settings(db)
```

也就是说，前端点击 Agent 分析或发送自然语言问题时，Level 1 规划与解释都会优先读取 `agent_llm_settings` 中的配置。

如果两者都没有，Agent LLM 状态为：

```json
{
  "configured": false
}
```

## 8. 前端设计

### 8.1 页面位置

在：

```text
frontend/src/pages/ApiTokensPage.tsx
```

新增 tab：

```text
Agent LLM
```

当前系统管理 tab 变为：

- 系统 Token。
- 通知。
- Agent LLM。

### 8.2 前端状态类型

新增类型：

```ts
type AgentLlmSettings = {
  enabled: boolean;
  base_url?: string | null;
  api_key_configured?: boolean;
  api_key_preview?: string | null;
  level1_model?: string | null;
  level1_temperature: number;
  level2_model?: string | null;
  level2_temperature: number;
  timeout_seconds: number;
  loop_enabled: boolean;
  loop_interval_seconds: number;
  last_test_at?: string | null;
  last_test_status?: string | null;
  last_test_message?: string | null;
  updated_at?: string | null;
};
```

表单类型：

```ts
type AgentLlmForm = {
  enabled: boolean;
  base_url: string;
  api_key: string;
  level1_model: string;
  level1_temperature: string;
  level2_model: string;
  level2_temperature: string;
  timeout_seconds: string;
  loop_enabled: boolean;
  loop_interval_seconds: string;
};
```

### 8.3 前端交互

页面应展示：

- 是否启用 Agent。
- Base URL。
- API Key 密码输入框。
- 如果已配置，展示 `api_key_preview`。
- Level 1 模型名。
- Level 1 temperature。
- Level 2 模型名。
- Level 2 temperature。
- timeout。
- 是否启用 Agent loop。
- loop 间隔。
- 最近测试时间。
- 最近测试状态。
- 最近测试消息。
- 保存按钮。
- 测试连接按钮。

编辑已有配置时：

- API Key 输入框 placeholder 显示“留空则不修改”。
- 保存时如果 API Key 为空，不提交或提交 `null`，后端保持原值。

### 8.4 API 调用

加载：

```ts
api<AgentLlmSettings>("/settings/agent-llm", token)
```

保存：

```ts
api<AgentLlmSettings>("/settings/agent-llm", token, {
  method: "PUT",
  body: JSON.stringify(payload),
})
```

测试：

```ts
api<{ ok: boolean; message?: string; settings?: AgentLlmSettings }>("/settings/agent-llm/test", token, {
  method: "POST",
})
```

## 9. 与现有 Agent 代码的关系

当前已有：

```text
backend/app/modules/agent/llm.py
backend/app/modules/agent/langchain_adapter.py
backend/app/modules/agent/orchestrator.py
```

阶段一不继续扩展当前 Agent 分析业务逻辑。

阶段一只做：

- 让 LLM 调用层可以从系统管理配置读取模型参数。
- 为后续 Context Pack / LLM 决策提供稳定调用入口。
- 保留环境变量兜底。

后续阶段再决定是否重构现有 `backend/app/modules/agent/llm.py`。

推荐方向：

```text
backend/app/modules/agent/settings.py
-> 负责配置读取

backend/app/modules/agent/llm_client.py
-> 负责 LangChain 调用适配

backend/app/modules/agent/llm.py
-> 后续逐步变成业务 prompt / decision output 处理层
```

## 10. 测试与验收

### 10.1 后端验收

- `GET /api/settings/agent-llm` 未配置时返回默认值。
- `PUT /api/settings/agent-llm` 可以保存配置。
- 保存配置后不会向前端返回完整 API Key。
- API Key 留空更新时不会覆盖旧值。
- 修改配置写入 audit log。
- `POST /api/settings/agent-llm/test` 可以调用配置中的模型。
- 测试成功更新 `last_test_status=success`。
- 测试失败更新 `last_test_status=failed`。
- 测试失败不泄露 API Key。

### 10.2 前端验收

- 系统管理页面出现 Agent LLM tab。
- 能看到当前配置状态。
- 能保存配置。
- API Key 只显示已配置和掩码，不显示明文。
- 编辑时 API Key 留空不修改。
- 点击测试连接能看到成功或失败提示。
- 刷新页面后配置仍存在。

### 10.3 构建验收

需要通过：

```text
python -m compileall backend\app
npm run build
```

## 11. 开发顺序建议

建议按以下顺序开发：

1. 新增 schema：`AgentLlmSettingsUpdate`。
2. 新增 Agent 配置服务：`backend/app/modules/agent/settings.py`。
3. 新增 LangChain 调用适配：`backend/app/modules/agent/llm_client.py`。
4. 扩展 router：`settings.py` 增加 agent-llm 三个接口。
5. 扩展前端类型和状态。
6. 在 `ApiTokensPage.tsx` 新增 Agent LLM tab。
7. 接入保存和测试连接。
8. 运行后端编译和前端构建。

## 12. 风险与注意事项

### 12.1 不要提前做 Agent 业务闭环

阶段一只做 LLM 配置和调用层。

不要顺手接入：

- 自动 loop。
- 补号判断。
- Agent run 入库。
- 钉钉通知。
- ReAct 多轮业务流程。

### 12.2 不要把 API Key 返回前端

任何 GET 或 PUT 响应都不能包含完整 API Key。

测试连接失败时，也不能把包含 API Key 的 URL 或 header 写入错误信息。

### 12.3 不要破坏现有通知配置

Agent LLM 配置只是系统管理页新增 tab。

不要改现有通知配置的数据结构。

### 12.4 不要把系统工程交给 LangChain

LangChain 只做模型调用、Prompt 编排和结构化输出。

配置、权限、审计、数据库和前端仍由现有项目负责。

## 13. 阶段一完成后的下一步

阶段一完成后，再进入下一份设计：

```text
阶段二：Agent 持久化与前端缓存设计
```

对应任务文档：

```text
docs/agent/阶段二-Agent持久化与前端缓存任务.md
```

阶段二才讨论：

- `agent_runs`。
- `agent_messages`。
- `agent_decisions`。
- 前端读取最近一次 Agent 状态。
- 用户对话刷新后不丢失。

不要在阶段一提前实现这些内容。
