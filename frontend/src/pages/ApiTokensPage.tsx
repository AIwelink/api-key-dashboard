import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import { allNavigationItems, viewLabel } from "../navigation";
import type { ApiPool, UserRole, ViewName } from "../types";
import { errorMessage, formatDateTime } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type ApiToken = {
  id: string;
  name: string;
  role: string;
  status: string;
  token_prefix?: string;
  note?: string | null;
  expires_at?: string | null;
  last_used_at?: string | null;
  usage_count?: number;
  created_at?: string;
  revoked_at?: string | null;
  token?: string;
};

type NotificationChannel = {
  id: string;
  name: string;
  channel_type: "dingtalk" | "telegram" | "feishu";
  status: "active" | "disabled";
  note?: string | null;
  webhook_configured?: boolean;
  webhook_preview?: string | null;
  signing_secret_configured?: boolean;
  telegram_bot_token_configured?: boolean;
  telegram_bot_token_preview?: string | null;
  telegram_chat_id?: string | null;
  last_test_at?: string | null;
  last_test_status?: string | null;
  last_test_message?: string | null;
  last_delivery_at?: string | null;
  last_delivery_status?: string | null;
  last_delivery_message?: string | null;
  created_at?: string;
  updated_at?: string;
};

type NotificationForm = {
  name: string;
  channel_type: "dingtalk" | "telegram" | "feishu";
  status: "active" | "disabled";
  webhook_url: string;
  signing_secret: string;
  telegram_bot_token: string;
  telegram_chat_id: string;
  note: string;
};

type SystemTab = "tokens" | "notifications" | "permissions" | "agent-llm";

export type RolePermissionEntry = {
  allowed_views: ViewName[];
  default_view: ViewName | null;
};

export type RolePermissionsSettings = {
  available_views: ViewName[];
  roles: Record<UserRole, RolePermissionEntry>;
  updated_at?: string;
  updated_by?: string;
};

type AgentLlmSettings = {
  id?: string;
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
  agent_loop_enabled?: boolean;
  scheduler_interval_seconds: number;
  max_tasks_per_tick: number;
  max_pool_patrols_per_tick: number;
  patrol_enabled: boolean;
  pool_patrol_interval_minutes: number;
  pool_patrol_cooldown_minutes: number;
  required_patrol_pool_ids?: string[];
  excluded_agent_pool_ids?: string[];
  max_event_triggers_per_tick: number;
  max_concurrent_runs: number;
  task_cooldown_minutes: number;
  event_trigger_cooldown_minutes: number;
  daily_memory_enabled: boolean;
  weekly_memory_enabled: boolean;
  max_memory_summaries_per_tick: number;
  memory_summary_catchup_enabled: boolean;
  notification_dispatch_enabled: boolean;
  decision_notification_enabled: boolean;
  decision_notification_min_severity?: string | null;
  decision_notification_triggers?: string[];
  decision_notification_cooldown_minutes: number;
  pool_strategies?: Record<string, unknown>[];
  last_test_at?: string | null;
  last_test_status?: string | null;
  last_test_message?: string | null;
};

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
  scheduler_interval_seconds: string;
  max_tasks_per_tick: string;
  max_pool_patrols_per_tick: string;
  patrol_enabled: boolean;
  pool_patrol_interval_minutes: string;
  pool_patrol_cooldown_minutes: string;
  required_patrol_pool_ids: string[];
  excluded_agent_pool_ids: string[];
  max_event_triggers_per_tick: string;
  max_concurrent_runs: string;
  task_cooldown_minutes: string;
  event_trigger_cooldown_minutes: string;
  daily_memory_enabled: boolean;
  weekly_memory_enabled: boolean;
  max_memory_summaries_per_tick: string;
  memory_summary_catchup_enabled: boolean;
  notification_dispatch_enabled: boolean;
  decision_notification_enabled: boolean;
  decision_notification_min_severity: string;
  decision_notification_triggers: string[];
  decision_notification_cooldown_minutes: string;
};

const decisionNotificationTriggerOptions = ["event_spike", "scheduler_task_due", "scheduler_review_due", "scheduler_patrol"];

const roleOrder: UserRole[] = ["owner", "admin", "maintainer", "operator", "viewer"];

const roleLabels: Record<UserRole, string> = {
  owner: "owner",
  admin: "admin",
  maintainer: "maintainer",
  operator: "运营",
  viewer: "viewer",
};

const emptyNotificationForm: NotificationForm = {
  name: "",
  channel_type: "dingtalk",
  status: "active",
  webhook_url: "",
  signing_secret: "",
  telegram_bot_token: "",
  telegram_chat_id: "",
  note: "",
};

const emptyAgentLlmForm: AgentLlmForm = {
  enabled: false,
  base_url: "",
  api_key: "",
  level1_model: "",
  level1_temperature: "0.2",
  level2_model: "",
  level2_temperature: "0.2",
  timeout_seconds: "60",
  loop_enabled: false,
  loop_interval_seconds: "900",
  scheduler_interval_seconds: "300",
  max_tasks_per_tick: "5",
  max_pool_patrols_per_tick: "3",
  patrol_enabled: false,
  pool_patrol_interval_minutes: "30",
  pool_patrol_cooldown_minutes: "30",
  required_patrol_pool_ids: [],
  excluded_agent_pool_ids: [],
  max_event_triggers_per_tick: "3",
  max_concurrent_runs: "1",
  task_cooldown_minutes: "10",
  event_trigger_cooldown_minutes: "15",
  daily_memory_enabled: true,
  weekly_memory_enabled: true,
  max_memory_summaries_per_tick: "3",
  memory_summary_catchup_enabled: true,
  notification_dispatch_enabled: false,
  decision_notification_enabled: false,
  decision_notification_min_severity: "warning",
  decision_notification_triggers: [...decisionNotificationTriggerOptions],
  decision_notification_cooldown_minutes: "30",
};

export function ApiTokensPage({ token, showToast }: Props) {
  const [activeTab, setActiveTab] = useState<SystemTab>("tokens");
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [createdToken, setCreatedToken] = useState<ApiToken | null>(null);
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [notificationForm, setNotificationForm] = useState<NotificationForm>(emptyNotificationForm);
  const [editingChannelId, setEditingChannelId] = useState<string | null>(null);
  const [agentLlmSettings, setAgentLlmSettings] = useState<AgentLlmSettings | null>(null);
  const [agentLlmForm, setAgentLlmForm] = useState<AgentLlmForm>(emptyAgentLlmForm);
  const [agentPools, setAgentPools] = useState<ApiPool[]>([]);
  const [rolePermissionsSettings, setRolePermissionsSettings] = useState<RolePermissionsSettings | null>(null);
  const [busy, setBusy] = useState(false);

  const loadTokens = async () => {
    const data = await api<{ items: ApiToken[] }>("/api-tokens", token);
    setTokens(data.items);
  };

  const loadNotificationChannels = async () => {
    const data = await api<{ items: NotificationChannel[] }>("/notification-channels", token);
    setChannels(data.items);
  };

  const loadAgentPools = async () => {
    const data = await api<{ items: ApiPool[] }>("/agent/pools", token);
    setAgentPools((data.items || []).filter((pool) => pool.status !== "disabled"));
  };

  const loadRolePermissionsSettings = async () => {
    const data = await api<RolePermissionsSettings>("/settings/role-permissions", token);
    setRolePermissionsSettings(data);
  };

  const loadAgentLlmSettings = async () => {
    const data = await api<AgentLlmSettings>("/settings/agent-llm", token);
    setAgentLlmSettings(data);
    setAgentLlmForm({
      enabled: !!data.enabled,
      base_url: data.base_url || "",
      api_key: "",
      level1_model: data.level1_model || "",
      level1_temperature: String(data.level1_temperature ?? 0.2),
      level2_model: data.level2_model || "",
      level2_temperature: String(data.level2_temperature ?? 0.2),
      timeout_seconds: String(data.timeout_seconds ?? 60),
      loop_enabled: !!(data.agent_loop_enabled ?? data.loop_enabled),
      loop_interval_seconds: String(data.loop_interval_seconds ?? 900),
      scheduler_interval_seconds: String(data.scheduler_interval_seconds ?? 300),
      max_tasks_per_tick: String(data.max_tasks_per_tick ?? 5),
      max_pool_patrols_per_tick: String(data.max_pool_patrols_per_tick ?? 3),
      patrol_enabled: !!data.patrol_enabled,
      pool_patrol_interval_minutes: String(data.pool_patrol_interval_minutes ?? 30),
      pool_patrol_cooldown_minutes: String(data.pool_patrol_cooldown_minutes ?? 30),
      required_patrol_pool_ids: data.required_patrol_pool_ids || [],
      excluded_agent_pool_ids: data.excluded_agent_pool_ids || [],
      max_event_triggers_per_tick: String(data.max_event_triggers_per_tick ?? 3),
      max_concurrent_runs: String(data.max_concurrent_runs ?? 1),
      task_cooldown_minutes: String(data.task_cooldown_minutes ?? 10),
      event_trigger_cooldown_minutes: String(data.event_trigger_cooldown_minutes ?? 15),
      daily_memory_enabled: data.daily_memory_enabled ?? true,
      weekly_memory_enabled: data.weekly_memory_enabled ?? true,
      max_memory_summaries_per_tick: String(data.max_memory_summaries_per_tick ?? 3),
      memory_summary_catchup_enabled: data.memory_summary_catchup_enabled ?? true,
      notification_dispatch_enabled: !!data.notification_dispatch_enabled,
      decision_notification_enabled: !!data.decision_notification_enabled,
      decision_notification_min_severity: data.decision_notification_min_severity || "warning",
      decision_notification_triggers: data.decision_notification_triggers?.length ? data.decision_notification_triggers : [...decisionNotificationTriggerOptions],
      decision_notification_cooldown_minutes: String(data.decision_notification_cooldown_minutes ?? 30),
    });
  };

  useEffect(() => {
    Promise.all([loadTokens(), loadNotificationChannels(), loadAgentLlmSettings(), loadAgentPools(), loadRolePermissionsSettings()]).catch((error) => showToast(errorMessage(error), true));
  }, []);

  const submitToken = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form).entries());
    const expiresRaw = String(values.expires_in_days || "").trim();
    const payload: Record<string, unknown> = {
      name: values.name,
      role: values.role,
      note: values.note,
    };
    if (expiresRaw) payload.expires_in_days = Number(expiresRaw);

    setBusy(true);
    try {
      const created = await api<ApiToken>("/api-tokens", token, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setCreatedToken(created);
      await loadTokens();
      form.reset();
      showToast("系统 Token 已创建，只显示这一次");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (item: ApiToken) => {
    if (!window.confirm(`确定停用 ${item.name} 吗？停用后对接系统会立即失效。`)) return;
    setBusy(true);
    try {
      await api(`/api-tokens/${item.id}/revoke`, token, { method: "POST" });
      await loadTokens();
      showToast("系统 Token 已停用");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const copyCreatedToken = async () => {
    if (!createdToken?.token) return;
    await navigator.clipboard.writeText(createdToken.token);
    showToast("Token 已复制");
  };

  const resetNotificationForm = () => {
    setNotificationForm(emptyNotificationForm);
    setEditingChannelId(null);
  };

  const editNotificationChannel = (item: NotificationChannel) => {
    setEditingChannelId(item.id);
    setNotificationForm({
      name: item.name,
      channel_type: item.channel_type,
      status: item.status,
      webhook_url: "",
      signing_secret: "",
      telegram_bot_token: "",
      telegram_chat_id: item.telegram_chat_id || "",
      note: item.note || "",
    });
  };

  const submitNotificationChannel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const webhookUrl = notificationForm.webhook_url.trim();
    const signingSecret = notificationForm.signing_secret.trim();
    const telegramBotToken = notificationForm.telegram_bot_token.trim();
    const telegramChatId = notificationForm.telegram_chat_id.trim();
    const payload: Record<string, unknown> = {
      name: notificationForm.name.trim(),
      channel_type: notificationForm.channel_type,
      status: notificationForm.status,
      note: notificationForm.note.trim(),
    };
    if (notificationForm.channel_type === "dingtalk") {
      if (!editingChannelId || webhookUrl) payload.webhook_url = webhookUrl;
      if (!editingChannelId || signingSecret) payload.signing_secret = signingSecret;
    }
    if (notificationForm.channel_type === "feishu") {
      if (!editingChannelId || webhookUrl) payload.webhook_url = webhookUrl;
      if (signingSecret) payload.signing_secret = signingSecret;
    }
    if (notificationForm.channel_type === "telegram") {
      if (!editingChannelId || telegramBotToken) payload.telegram_bot_token = telegramBotToken;
      if (!editingChannelId || telegramChatId) payload.telegram_chat_id = telegramChatId;
    }

    setBusy(true);
    try {
      await api<NotificationChannel>(editingChannelId ? `/notification-channels/${editingChannelId}` : "/notification-channels", token, {
        method: editingChannelId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      await loadNotificationChannels();
      resetNotificationForm();
      showToast(editingChannelId ? "通知配置已保存" : "通知配置已创建");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const testNotificationChannel = async (item: NotificationChannel) => {
    setBusy(true);
    try {
      const result = await api<{ message?: string; channel?: NotificationChannel }>(`/notification-channels/${item.id}/test`, token, { method: "POST" });
      await loadNotificationChannels();
      showToast(result.message || "测试通知已发送");
    } catch (error) {
      await loadNotificationChannels().catch(() => undefined);
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const deleteNotificationChannel = async (item: NotificationChannel) => {
    if (!window.confirm(`确定删除通知配置 ${item.name} 吗？`)) return;
    setBusy(true);
    try {
      await api<null>(`/notification-channels/${item.id}`, token, { method: "DELETE" });
      await loadNotificationChannels();
      if (editingChannelId === item.id) resetNotificationForm();
      showToast("通知配置已删除");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const submitAgentLlmSettings = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const apiKey = agentLlmForm.api_key.trim();
    const payload: Record<string, unknown> = {
      enabled: agentLlmForm.enabled,
      base_url: agentLlmForm.base_url.trim() || null,
      level1_model: agentLlmForm.level1_model.trim() || null,
      level1_temperature: Number(agentLlmForm.level1_temperature || 0.2),
      level2_model: agentLlmForm.level2_model.trim() || null,
      level2_temperature: Number(agentLlmForm.level2_temperature || 0.2),
      timeout_seconds: Number(agentLlmForm.timeout_seconds || 60),
      loop_enabled: agentLlmForm.loop_enabled,
      agent_loop_enabled: agentLlmForm.loop_enabled,
      loop_interval_seconds: Number(agentLlmForm.scheduler_interval_seconds || 300),
      scheduler_interval_seconds: Number(agentLlmForm.scheduler_interval_seconds || 300),
      max_tasks_per_tick: Number(agentLlmForm.max_tasks_per_tick || 5),
      max_pool_patrols_per_tick: Number(agentLlmForm.max_pool_patrols_per_tick || 3),
      patrol_enabled: agentLlmForm.patrol_enabled,
      pool_patrol_interval_minutes: Number(agentLlmForm.pool_patrol_interval_minutes || 30),
      pool_patrol_cooldown_minutes: Number(agentLlmForm.pool_patrol_cooldown_minutes || 30),
      required_patrol_pool_ids: agentLlmForm.required_patrol_pool_ids,
      excluded_agent_pool_ids: agentLlmForm.excluded_agent_pool_ids,
      max_event_triggers_per_tick: Number(agentLlmForm.max_event_triggers_per_tick || 3),
      max_concurrent_runs: Number(agentLlmForm.max_concurrent_runs || 1),
      task_cooldown_minutes: Number(agentLlmForm.task_cooldown_minutes || 10),
      event_trigger_cooldown_minutes: Number(agentLlmForm.event_trigger_cooldown_minutes || 15),
      daily_memory_enabled: agentLlmForm.daily_memory_enabled,
      weekly_memory_enabled: agentLlmForm.weekly_memory_enabled,
      max_memory_summaries_per_tick: Number(agentLlmForm.max_memory_summaries_per_tick || 3),
      memory_summary_catchup_enabled: agentLlmForm.memory_summary_catchup_enabled,
      notification_dispatch_enabled: agentLlmForm.notification_dispatch_enabled,
      decision_notification_enabled: agentLlmForm.decision_notification_enabled,
      decision_notification_min_severity: agentLlmForm.decision_notification_min_severity,
      decision_notification_triggers: agentLlmForm.decision_notification_triggers,
      decision_notification_cooldown_minutes: Number(agentLlmForm.decision_notification_cooldown_minutes || 30),
      pool_strategies: agentLlmSettings?.pool_strategies || [],
    };
    if (apiKey) payload.api_key = apiKey;

    setBusy(true);
    try {
      const updated = await api<AgentLlmSettings>("/settings/agent-llm", token, {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      setAgentLlmSettings(updated);
      setAgentLlmForm((current) => ({ ...current, api_key: "" }));
      showToast("Agent LLM settings saved");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const toggleRequiredPatrolPool = (poolId: string) => {
    setAgentLlmForm((current) => {
      const exists = current.required_patrol_pool_ids.includes(poolId);
      return {
        ...current,
        required_patrol_pool_ids: exists
          ? current.required_patrol_pool_ids.filter((item) => item !== poolId)
          : [...current.required_patrol_pool_ids, poolId],
        excluded_agent_pool_ids: current.excluded_agent_pool_ids.filter((item) => item !== poolId),
      };
    });
  };

  const toggleExcludedAgentPool = (poolId: string) => {
    setAgentLlmForm((current) => {
      const exists = current.excluded_agent_pool_ids.includes(poolId);
      return {
        ...current,
        excluded_agent_pool_ids: exists
          ? current.excluded_agent_pool_ids.filter((item) => item !== poolId)
          : [...current.excluded_agent_pool_ids, poolId],
        required_patrol_pool_ids: current.required_patrol_pool_ids.filter((item) => item !== poolId),
      };
    });
  };

  const toggleDecisionNotificationTrigger = (trigger: string) => {
    setAgentLlmForm((current) => {
      const exists = current.decision_notification_triggers.includes(trigger);
      return {
        ...current,
        decision_notification_triggers: exists
          ? current.decision_notification_triggers.filter((item) => item !== trigger)
          : [...current.decision_notification_triggers, trigger],
      };
    });
  };

  const testAgentLlmSettings = async () => {
    setBusy(true);
    try {
      const result = await api<{ message?: string; settings?: AgentLlmSettings }>("/settings/agent-llm/test", token, { method: "POST" });
      if (result.settings) {
        setAgentLlmSettings(result.settings);
      } else {
        await loadAgentLlmSettings();
      }
      showToast(result.message || "Agent LLM connection test passed");
    } catch (error) {
      await loadAgentLlmSettings().catch(() => undefined);
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const saveRolePermissionsSettings = async () => {
    if (!rolePermissionsSettings) return;
    setBusy(true);
    try {
      const updated = await api<RolePermissionsSettings>("/settings/role-permissions", token, {
        method: "PUT",
        body: JSON.stringify({ roles: rolePermissionsSettings.roles }),
      });
      setRolePermissionsSettings(updated);
      showToast("权限配置已保存");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const refreshCurrentTab = () => {
    const loader = activeTab === "tokens"
      ? loadTokens
      : activeTab === "notifications"
        ? loadNotificationChannels
        : activeTab === "permissions"
          ? loadRolePermissionsSettings
          : loadAgentLlmSettings;
    loader().catch((error) => showToast(errorMessage(error), true));
  };

  usePageAutoRefresh(
    async () => {
      if (activeTab === "tokens") await loadTokens();
      if (activeTab === "notifications") await loadNotificationChannels();
      if (activeTab === "permissions") await loadRolePermissionsSettings();
      if (activeTab === "agent-llm") await loadAgentPools();
    },
    { paused: Boolean(busy || editingChannelId) },
  );

  const channelLabel = (item: NotificationChannel) => {
    if (item.channel_type === "telegram") return "TG机器人";
    if (item.channel_type === "feishu") return "飞书";
    return "钉钉";
  };

  const channelConfigSummary = (item: NotificationChannel) => {
    if (item.channel_type === "telegram") {
      return `Bot Token ${item.telegram_bot_token_configured ? item.telegram_bot_token_preview || "已配置" : "未配置"} · Chat ID ${item.telegram_chat_id || "未配置"}`;
    }
    if (item.channel_type === "feishu") {
      return `Webhook ${item.webhook_configured ? item.webhook_preview || "已配置" : "未配置"} · 加签 ${item.signing_secret_configured ? "已配置" : "未配置（可选）"}`;
    }
    return `Webhook ${item.webhook_configured ? item.webhook_preview || "已配置" : "未配置"} · 加签 ${item.signing_secret_configured ? "已配置" : "未配置"}`;
  };

  return (
    <section className="view">
      <div className="topbar">
        <div>
          <h2>系统管理</h2>
          <p>管理外部系统 Token 和通知机器人配置。</p>
        </div>
        <button onClick={refreshCurrentTab} type="button">
          刷新
        </button>
      </div>

      <div className="system-tabs">
        <button className={activeTab === "tokens" ? "active" : ""} onClick={() => setActiveTab("tokens")} type="button">
          系统 Token
        </button>
        <button className={activeTab === "notifications" ? "active" : ""} onClick={() => setActiveTab("notifications")} type="button">
          通知
        </button>
        <button className={activeTab === "permissions" ? "active" : ""} onClick={() => setActiveTab("permissions")} type="button">
          权限
        </button>
        <button className={activeTab === "agent-llm" ? "active" : ""} onClick={() => setActiveTab("agent-llm")} type="button">
          Agent LLM
        </button>
      </div>

      {activeTab === "tokens" && createdToken?.token && (
        <section className="panel token-created-panel">
          <div className="panel-header">
            <div>
              <h3>新 Token</h3>
              <p>请现在保存到另一个系统里，关闭后无法再次查看明文。</p>
            </div>
            <button onClick={copyCreatedToken} type="button">
              复制
            </button>
          </div>
          <textarea readOnly rows={3} value={createdToken.token} />
        </section>
      )}

      {activeTab === "tokens" && (
      <div className="grid two">
        <section className="panel">
          <h3>Token 列表</h3>
          <div className="list">
            {tokens.map((item) => (
              <div className="list-item token-list-item" key={item.id}>
                <div>
                  <strong>{item.name}</strong>
                  <div className="muted">
                    {item.token_prefix} · {item.role} · {item.status}
                  </div>
                  <div className="muted">
                    创建 {formatDateTime(item.created_at)} · 最近使用 {formatDateTime(item.last_used_at)} · 使用 {item.usage_count || 0} 次
                  </div>
                  {item.expires_at && <div className="muted">过期时间 {formatDateTime(item.expires_at)}</div>}
                  {item.note && <div>{item.note}</div>}
                </div>
                <button className="ghost danger-button" disabled={busy || item.status !== "active"} onClick={() => revoke(item)} type="button">
                  停用
                </button>
              </div>
            ))}
            {!tokens.length && <div className="muted">还没有系统 Token。</div>}
          </div>
        </section>

        <section className="panel">
          <h3>创建 Token</h3>
          <form className="form-grid single" onSubmit={submitToken}>
            <label>
              名称 <input name="name" placeholder="例如 billing-sync" required />
            </label>
            <label>
              角色
              <select name="role" defaultValue="maintainer">
                <option value="viewer">viewer</option>
                <option value="maintainer">maintainer</option>
                <option value="operator">运营</option>
                <option value="admin">admin</option>
              </select>
            </label>
            <label>
              有效天数 <input min="1" max="3650" name="expires_in_days" placeholder="留空表示长期有效" type="number" />
            </label>
            <label>
              备注 <textarea name="note" rows={4} placeholder="用途、对接系统、联系人等" />
            </label>
            <button disabled={busy} type="submit">
              创建 Token
            </button>
          </form>
        </section>
      </div>
      )}

      {activeTab === "permissions" && (
        <RolePermissionsPanel
          settings={rolePermissionsSettings}
          busy={busy}
          onChange={setRolePermissionsSettings}
          onSave={saveRolePermissionsSettings}
        />
      )}

      {activeTab === "notifications" && (
        <div className="grid two">
          <section className="panel notification-list-panel">
            <div className="panel-header">
              <div>
                <h3>通知列表</h3>
                <p>这里是总通知备选。后续不同通知可以选择不同机器人。</p>
              </div>
            </div>
            <div className="list">
              {channels.map((item) => (
                <div className="list-item notification-channel-item" key={item.id}>
                  <div>
                    <div className="notification-channel-title">
                      <strong>{item.name}</strong>
                      <span className={`status-pill ${item.status === "active" ? "success" : ""}`}>{item.status === "active" ? "启用" : "停用"}</span>
                      <span className="status-pill accent">{channelLabel(item)}</span>
                    </div>
                    <div className="muted">{channelConfigSummary(item)}</div>
                    <div className="muted">创建 {formatDateTime(item.created_at)} · 最近测试 {formatDateTime(item.last_test_at)}</div>
                    {item.last_delivery_at && (
                      <div className="muted">
                        最近投递 {formatDateTime(item.last_delivery_at)} · {deliveryStatusLabel(item.last_delivery_status)}
                      </div>
                    )}
                    {item.last_test_status && (
                      <div className={item.last_test_status === "success" ? "success-text" : "warning-text"}>
                        测试 {item.last_test_status === "success" ? "成功" : "失败"}：{item.last_test_message || "-"}
                      </div>
                    )}
                    {item.last_delivery_status && item.last_delivery_status !== "success" && (
                      <div className="warning-text">投递失败：{item.last_delivery_message || "-"}</div>
                    )}
                    {item.note && <div>{item.note}</div>}
                  </div>
                  <div className="notification-channel-actions">
                    <button className="ghost success-button" disabled={busy || item.status !== "active"} onClick={() => testNotificationChannel(item)} type="button">
                      测试
                    </button>
                    <button className="ghost" disabled={busy} onClick={() => editNotificationChannel(item)} type="button">
                      编辑
                    </button>
                    <button className="ghost danger-button" disabled={busy} onClick={() => deleteNotificationChannel(item)} type="button">
                      删除
                    </button>
                  </div>
                </div>
              ))}
              {!channels.length && <div className="muted">还没有通知配置。</div>}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <h3>{editingChannelId ? "编辑通知" : "新增通知"}</h3>
                <p>当前支持钉钉、飞书自定义机器人和 Telegram 机器人。</p>
              </div>
              {editingChannelId && (
                <button className="ghost" onClick={resetNotificationForm} type="button">
                  新增
                </button>
              )}
            </div>
            <form className="form-grid single" onSubmit={submitNotificationChannel}>
              <label>
                通知名称 <input required value={notificationForm.name} onChange={(event) => setNotificationForm((current) => ({ ...current, name: event.target.value }))} placeholder="例如 容量预警钉钉群" />
              </label>
              <label>
                通知选型
                <select
                  disabled={!!editingChannelId}
                  value={notificationForm.channel_type}
                  onChange={(event) => setNotificationForm((current) => ({ ...current, channel_type: event.target.value as "dingtalk" | "telegram" | "feishu" }))}
                >
                  <option value="dingtalk">钉钉自定义机器人</option>
                  <option value="feishu">飞书自定义机器人</option>
                  <option value="telegram">TG 机器人</option>
                </select>
              </label>
              <label>
                状态
                <select value={notificationForm.status} onChange={(event) => setNotificationForm((current) => ({ ...current, status: event.target.value as "active" | "disabled" }))}>
                  <option value="active">启用</option>
                  <option value="disabled">停用</option>
                </select>
              </label>
              {notificationForm.channel_type === "dingtalk" && (
                <>
                  <label>
                    钉钉自定义机器人 Webhook 地址 *
                    <textarea
                      required={!editingChannelId}
                      rows={3}
                      value={notificationForm.webhook_url}
                      onChange={(event) => setNotificationForm((current) => ({ ...current, webhook_url: event.target.value }))}
                      placeholder={editingChannelId ? "留空不修改" : "https://oapi.dingtalk.com/robot/send?access_token=..."}
                    />
                  </label>
                  <label>
                    钉钉自定义机器人加签密钥 *
                    <input
                      required={!editingChannelId}
                      type="password"
                      value={notificationForm.signing_secret}
                      onChange={(event) => setNotificationForm((current) => ({ ...current, signing_secret: event.target.value }))}
                      placeholder={editingChannelId ? "留空不修改" : "SEC..."}
                    />
                  </label>
                </>
              )}
              {notificationForm.channel_type === "feishu" && (
                <>
                  <label>
                    飞书自定义机器人 Webhook 地址 *
                    <textarea
                      required={!editingChannelId}
                      rows={3}
                      value={notificationForm.webhook_url}
                      onChange={(event) => setNotificationForm((current) => ({ ...current, webhook_url: event.target.value }))}
                      placeholder={editingChannelId ? "留空不修改" : "https://open.feishu.cn/open-apis/bot/v2/hook/..."}
                    />
                  </label>
                  <label>
                    飞书自定义机器人加签密钥（可选）
                    <input
                      type="password"
                      value={notificationForm.signing_secret}
                      onChange={(event) => setNotificationForm((current) => ({ ...current, signing_secret: event.target.value }))}
                      placeholder={editingChannelId ? "留空不修改" : "飞书机器人安全设置中的签名校验密钥"}
                    />
                  </label>
                </>
              )}
              {notificationForm.channel_type === "telegram" && (
                <>
                  <label>
                    Telegram Bot Token *
                    <input
                      required={!editingChannelId}
                      type="password"
                      value={notificationForm.telegram_bot_token}
                      onChange={(event) => setNotificationForm((current) => ({ ...current, telegram_bot_token: event.target.value }))}
                      placeholder={editingChannelId ? "留空不修改" : "1234567890:AA..."}
                    />
                  </label>
                  <label>
                    Telegram Chat ID *
                    <input
                      required={!editingChannelId}
                      value={notificationForm.telegram_chat_id}
                      onChange={(event) => setNotificationForm((current) => ({ ...current, telegram_chat_id: event.target.value }))}
                      placeholder="-1001234567890 或用户 ID"
                    />
                  </label>
                </>
              )}
              <label>
                备注 <textarea rows={4} value={notificationForm.note} onChange={(event) => setNotificationForm((current) => ({ ...current, note: event.target.value }))} placeholder="用途、触发场景、群名等" />
              </label>
              <button className="success-button" disabled={busy} type="submit">
                保存通知配置
              </button>
            </form>
          </section>
        </div>
      )}

      {activeTab === "agent-llm" && (
        <div className="grid two">
          <section className="panel">
            <div className="panel-header">
              <div>
                <h3>Agent LLM</h3>
                <p>OpenAI-compatible endpoint for the account-pool Agent.</p>
              </div>
              <span className={`status-pill ${agentLlmSettings?.enabled ? "success" : ""}`}>{agentLlmSettings?.enabled ? "Enabled" : "Disabled"}</span>
            </div>
            <form className="form-grid single" onSubmit={submitAgentLlmSettings}>
              <label>
                Enable Agent LLM calls
                <select value={agentLlmForm.enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, enabled: event.target.value === "yes" }))}>
                  <option value="yes">是</option>
                  <option value="no">否</option>
                </select>
              </label>
              <label>
                Base URL
                <input
                  value={agentLlmForm.base_url}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, base_url: event.target.value }))}
                  placeholder="https://example.com/v1"
                />
              </label>
              <label>
                API Key
                <input
                  type="password"
                  value={agentLlmForm.api_key}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, api_key: event.target.value }))}
                  placeholder={agentLlmSettings?.api_key_configured ? "Leave blank to keep current key" : "sk-..."}
                />
              </label>
              <div className="muted">Current key: {agentLlmSettings?.api_key_configured ? agentLlmSettings.api_key_preview || "configured" : "not configured"}</div>
              <label>
                Level 1 Model
                <input
                  value={agentLlmForm.level1_model}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, level1_model: event.target.value }))}
                  placeholder="gpt-4.1-mini"
                />
              </label>
              <label>
                Level 1 Temperature
                <input
                  min="0"
                  max="2"
                  step="0.1"
                  type="number"
                  value={agentLlmForm.level1_temperature}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, level1_temperature: event.target.value }))}
                />
              </label>
              <label>
                Level 2 Model
                <input
                  value={agentLlmForm.level2_model}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, level2_model: event.target.value }))}
                  placeholder="optional"
                />
              </label>
              <label>
                Level 2 Temperature
                <input
                  min="0"
                  max="2"
                  step="0.1"
                  type="number"
                  value={agentLlmForm.level2_temperature}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, level2_temperature: event.target.value }))}
                />
              </label>
              <label>
                Timeout Seconds
                <input
                  min="5"
                  max="300"
                  type="number"
                  value={agentLlmForm.timeout_seconds}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, timeout_seconds: event.target.value }))}
                />
              </label>
              <label>
                Enable Agent scheduler loop
                <select value={agentLlmForm.loop_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, loop_enabled: event.target.value === "yes" }))}>
                  <option value="yes">是</option>
                  <option value="no">否</option>
                </select>
              </label>
              <label>
                Scheduler Interval Seconds
                <input
                  min="60"
                  max="86400"
                  type="number"
                  value={agentLlmForm.scheduler_interval_seconds}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, scheduler_interval_seconds: event.target.value }))}
                />
              </label>
              <label>
                Enable pool patrol
                <select value={agentLlmForm.patrol_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, patrol_enabled: event.target.value === "yes" }))}>
                  <option value="yes">Yes</option>
                  <option value="no">No</option>
                </select>
              </label>
              <div className="agent-scheduler-grid">
                <label>
                  Max tasks / tick
                  <input
                    min="1"
                    max="100"
                    type="number"
                    value={agentLlmForm.max_tasks_per_tick}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, max_tasks_per_tick: event.target.value }))}
                  />
                </label>
                <label>
                  Max patrols / tick
                  <input
                    min="0"
                    max="100"
                    type="number"
                    value={agentLlmForm.max_pool_patrols_per_tick}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, max_pool_patrols_per_tick: event.target.value }))}
                  />
                </label>
                <label>
                  Patrol interval minutes
                  <input
                    min="5"
                    max="1440"
                    type="number"
                    value={agentLlmForm.pool_patrol_interval_minutes}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, pool_patrol_interval_minutes: event.target.value }))}
                  />
                </label>
                <label>
                  Patrol cooldown minutes
                  <input
                    min="0"
                    max="1440"
                    type="number"
                    value={agentLlmForm.pool_patrol_cooldown_minutes}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, pool_patrol_cooldown_minutes: event.target.value }))}
                  />
                </label>
                <div className="agent-required-patrol-field">
                  <strong>Required patrol pools</strong>
                  <div className="muted">Required pools are selected first; the remaining patrol slots use normal priority.</div>
                  <div className="agent-required-patrol-list">
                    {agentPools.length ? (
                      agentPools.map((pool) => (
                        <label className="checkbox-row" key={pool.id}>
                          <input
                            type="checkbox"
                            checked={agentLlmForm.required_patrol_pool_ids.includes(pool.id)}
                            onChange={() => toggleRequiredPatrolPool(pool.id)}
                          />
                          <span>{pool.name} / {pool.account_type} / group #{pool.active_group_id}</span>
                        </label>
                      ))
                    ) : (
                      <div className="muted">No active pools available.</div>
                    )}
                  </div>
                </div>
                <div className="agent-required-patrol-field">
                  <strong>Excluded Agent pools</strong>
                  <div className="muted">Excluded pools are ignored by patrol and event-spike auto runs.</div>
                  <div className="agent-required-patrol-list">
                    {agentPools.length ? (
                      agentPools.map((pool) => (
                        <label className="checkbox-row" key={pool.id}>
                          <input
                            type="checkbox"
                            checked={agentLlmForm.excluded_agent_pool_ids.includes(pool.id)}
                            onChange={() => toggleExcludedAgentPool(pool.id)}
                          />
                          <span>{pool.name} / {pool.account_type} / group #{pool.active_group_id}</span>
                        </label>
                      ))
                    ) : (
                      <div className="muted">No active pools available.</div>
                    )}
                  </div>
                </div>
                <label>
                  Max event triggers / tick
                  <input
                    min="0"
                    max="100"
                    type="number"
                    value={agentLlmForm.max_event_triggers_per_tick}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, max_event_triggers_per_tick: event.target.value }))}
                  />
                </label>
                <label>
                  Max concurrent runs
                  <input
                    min="1"
                    max="20"
                    type="number"
                    value={agentLlmForm.max_concurrent_runs}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, max_concurrent_runs: event.target.value }))}
                  />
                </label>
                <label>
                  Task cooldown minutes
                  <input
                    min="0"
                    max="1440"
                    type="number"
                    value={agentLlmForm.task_cooldown_minutes}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, task_cooldown_minutes: event.target.value }))}
                  />
                </label>
                <label>
                  Event cooldown minutes
                  <input
                    min="0"
                    max="1440"
                    type="number"
                    value={agentLlmForm.event_trigger_cooldown_minutes}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, event_trigger_cooldown_minutes: event.target.value }))}
                  />
                </label>
                <label>
                  Max memory summaries / tick
                  <input
                    min="0"
                    max="100"
                    type="number"
                    value={agentLlmForm.max_memory_summaries_per_tick}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, max_memory_summaries_per_tick: event.target.value }))}
                  />
                </label>
                <label>
                  Decision notify cooldown minutes
                  <input
                    min="0"
                    max="1440"
                    type="number"
                    value={agentLlmForm.decision_notification_cooldown_minutes}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, decision_notification_cooldown_minutes: event.target.value }))}
                  />
                </label>
                <label>
                  Decision notify min severity
                  <select
                    value={agentLlmForm.decision_notification_min_severity}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, decision_notification_min_severity: event.target.value }))}
                  >
                    <option value="watch">watch</option>
                    <option value="warning">warning</option>
                    <option value="danger">danger</option>
                    <option value="critical">critical</option>
                  </select>
                </label>
              </div>
              <div className="agent-scheduler-switches">
                <label>
                  Daily memory summary
                  <select value={agentLlmForm.daily_memory_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, daily_memory_enabled: event.target.value === "yes" }))}>
                    <option value="yes">是</option>
                    <option value="no">否</option>
                  </select>
                </label>
                <label>
                  Weekly memory summary
                  <select value={agentLlmForm.weekly_memory_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, weekly_memory_enabled: event.target.value === "yes" }))}>
                    <option value="yes">是</option>
                    <option value="no">否</option>
                  </select>
                </label>
                <label>
                  Memory catch-up
                  <select value={agentLlmForm.memory_summary_catchup_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, memory_summary_catchup_enabled: event.target.value === "yes" }))}>
                    <option value="yes">是</option>
                    <option value="no">否</option>
                  </select>
                </label>
                <label>
                  Notification dispatch
                  <select value={agentLlmForm.notification_dispatch_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, notification_dispatch_enabled: event.target.value === "yes" }))}>
                    <option value="yes">是</option>
                    <option value="no">否</option>
                  </select>
                </label>
                <label>
                  Decision notifications
                  <select value={agentLlmForm.decision_notification_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, decision_notification_enabled: event.target.value === "yes" }))}>
                    <option value="yes">是</option>
                    <option value="no">否</option>
                  </select>
                </label>
              </div>
              <div className="agent-required-patrol-field">
                <strong>Decision notification triggers</strong>
                <div className="muted">Send DingTalk summaries for selected automatic Agent runs. DingTalk webhook is reused from Notifications.</div>
                <div className="agent-required-patrol-list">
                  {decisionNotificationTriggerOptions.map((trigger) => (
                    <label className="checkbox-row" key={trigger}>
                      <input
                        type="checkbox"
                        checked={agentLlmForm.decision_notification_triggers.includes(trigger)}
                        onChange={() => toggleDecisionNotificationTrigger(trigger)}
                      />
                      <span>{trigger}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div className="muted">Pool-level Agent strategies are reserved for a later task; this stage only saves global scheduler configuration.</div>
              <div className="button-row">
                <button className="success-button" disabled={busy} type="submit">
                  Save Agent LLM
                </button>
                <button className="ghost" disabled={busy} onClick={testAgentLlmSettings} type="button">
                  Test saved config
                </button>
              </div>
            </form>
          </section>

          <section className="panel">
            <h3>Connection Status</h3>
            <div className="list">
              <div className="list-item">
                <div>
                  <strong>Saved configuration</strong>
                  <div className="muted">Base URL {agentLlmSettings?.base_url ? "configured" : "not configured"}</div>
                  <div className="muted">Level 1 model {agentLlmSettings?.level1_model || "-"}</div>
                  <div className="muted">Level 2 model {agentLlmSettings?.level2_model || "-"}</div>
                  <div className="muted">Timeout {agentLlmSettings?.timeout_seconds ?? "-"}s</div>
                  <div className="muted">Scheduler loop {(agentLlmSettings?.agent_loop_enabled ?? agentLlmSettings?.loop_enabled) ? "enabled" : "disabled"}</div>
                  <div className="muted">Pool patrol {agentLlmSettings?.patrol_enabled ? "enabled" : "disabled"}</div>
                  <div className="muted">Scheduler interval {agentLlmSettings?.scheduler_interval_seconds ?? "-"}s</div>
                  <div className="muted">Max tasks / patrols / events {agentLlmSettings?.max_tasks_per_tick ?? "-"} / {agentLlmSettings?.max_pool_patrols_per_tick ?? "-"} / {agentLlmSettings?.max_event_triggers_per_tick ?? "-"}</div>
                  <div className="muted">Required patrol pools {agentLlmSettings?.required_patrol_pool_ids?.length ?? 0}</div>
                  <div className="muted">Excluded Agent pools {agentLlmSettings?.excluded_agent_pool_ids?.length ?? 0}</div>
                  <div className="muted">Memory daily / weekly {agentLlmSettings?.daily_memory_enabled ? "on" : "off"} / {agentLlmSettings?.weekly_memory_enabled ? "on" : "off"} · max {agentLlmSettings?.max_memory_summaries_per_tick ?? "-"}</div>
                  <div className="muted">Notification dispatch {agentLlmSettings?.notification_dispatch_enabled ? "enabled" : "disabled"}</div>
                  <div className="muted">Decision notifications {agentLlmSettings?.decision_notification_enabled ? "enabled" : "disabled"} · min {agentLlmSettings?.decision_notification_min_severity || "warning"} · cooldown {agentLlmSettings?.decision_notification_cooldown_minutes ?? "-"}m</div>
                  <div className="muted">Decision notify triggers {(agentLlmSettings?.decision_notification_triggers || []).join(", ") || "-"}</div>
                </div>
              </div>
              <div className="list-item">
                <div>
                  <strong>Last test</strong>
                  <div className="muted">Time {formatDateTime(agentLlmSettings?.last_test_at)}</div>
                  <div className={agentLlmSettings?.last_test_status === "success" ? "success-text" : "warning-text"}>
                    Status {agentLlmSettings?.last_test_status || "-"}
                  </div>
                  {agentLlmSettings?.last_test_message && <div className="agent-test-message">{agentLlmSettings.last_test_message}</div>}
                </div>
              </div>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}

export function toggleRoleViewPermission(settings: RolePermissionsSettings, role: UserRole, view: ViewName): RolePermissionsSettings {
  const entry = settings.roles[role];
  const exists = entry.allowed_views.includes(view);
  const allowedViews = exists
    ? entry.allowed_views.filter((item) => item !== view)
    : [...entry.allowed_views, view];
  const defaultView = entry.default_view && allowedViews.includes(entry.default_view)
    ? entry.default_view
    : allowedViews[0] || null;
  return {
    ...settings,
    roles: {
      ...settings.roles,
      [role]: {
        allowed_views: allowedViews,
        default_view: defaultView,
      },
    },
  };
}

function setRoleDefaultView(settings: RolePermissionsSettings, role: UserRole, view: ViewName): RolePermissionsSettings {
  const entry = settings.roles[role];
  if (!entry.allowed_views.includes(view)) return settings;
  return {
    ...settings,
    roles: {
      ...settings.roles,
      [role]: {
        ...entry,
        default_view: view,
      },
    },
  };
}

export function RolePermissionsPanel({
  settings,
  busy,
  onChange,
  onSave,
}: {
  settings: RolePermissionsSettings | null;
  busy: boolean;
  onChange: (settings: RolePermissionsSettings) => void;
  onSave: () => void;
}) {
  if (!settings) {
    return (
      <section className="panel">
        <div className="muted">正在加载权限配置...</div>
      </section>
    );
  }
  const availableViews = settings.available_views.length ? settings.available_views : allNavigationItems.map(([view]) => view);
  return (
    <section className="panel role-permissions-panel">
      <div className="panel-header">
        <div>
          <h3>权限管理</h3>
          <p>角色可访问页面由后端配置保存到数据库。</p>
        </div>
        <button className="success-button" disabled={busy} onClick={onSave} type="button">
          {busy ? "保存中..." : "保存权限"}
        </button>
      </div>
      <div className="role-permission-grid">
        {roleOrder.map((role) => {
          const entry = settings.roles[role];
          return (
            <article className="role-permission-card" key={role}>
              <div className="role-permission-head">
                <strong>{roleLabels[role]}</strong>
                <span>{entry.allowed_views.length} 个页面</span>
              </div>
              <label>
                默认页面
                <select
                  value={entry.default_view || ""}
                  disabled={busy || entry.allowed_views.length === 0}
                  onChange={(event) => onChange(setRoleDefaultView(settings, role, event.target.value as ViewName))}
                >
                  {entry.allowed_views.length ? (
                    entry.allowed_views.map((view) => (
                      <option value={view} key={view}>{viewLabel(view)}</option>
                    ))
                  ) : (
                    <option value="">未设置</option>
                  )}
                </select>
              </label>
              <div className="role-permission-options">
                {availableViews.map((view) => (
                  <label className="checkbox-row" key={`${role}-${view}`}>
                    <input
                      type="checkbox"
                      value={view}
                      checked={entry.allowed_views.includes(view)}
                      disabled={busy}
                      onChange={() => onChange(toggleRoleViewPermission(settings, role, view))}
                    />
                    <span>{viewLabel(view)}</span>
                  </label>
                ))}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function deliveryStatusLabel(value?: string | null) {
  if (value === "success") return "成功";
  if (value === "failed") return "失败";
  if (value === "partial") return "部分成功";
  if (value === "skipped") return "跳过";
  return value || "-";
}
