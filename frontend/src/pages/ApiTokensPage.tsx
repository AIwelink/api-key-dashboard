import { FormEvent, ReactNode, useEffect, useId, useState } from "react";
import { api } from "../api/client";
import type { ApiPool } from "../types";
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

type SystemTab = "tokens" | "notifications" | "agent-llm";

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

type AgentSettingHelpDetail = {
  purpose: string;
  note?: string;
};

const AGENT_SETTING_HELP: Record<string, AgentSettingHelpDetail> = {
  "Base URL": {
    purpose: "Agent 调用的 OpenAI 兼容接口地址，通常以 /v1 结尾。",
    note: "修改后建议先保存，再执行连接测试。",
  },
  "API Key": {
    purpose: "调用模型接口使用的密钥。编辑已有配置时留空表示保留当前密钥。",
  },
  "主决策模型": {
    purpose: "Level 1 模型负责意图判断、账号池分析、决策和总结，是当前 Agent 的主要模型。",
  },
  "主决策温度": {
    purpose: "控制主模型输出的随机性。数值越低越稳定，运营决策通常建议使用较低值。",
    note: "一般建议 0 到 0.3。",
  },
  "增强模型": {
    purpose: "预留的 Level 2 模型，可用于更复杂的复盘或判断；留空时不使用。",
  },
  "增强模型温度": {
    purpose: "控制 Level 2 模型输出随机性，仅在配置了增强模型时生效。",
  },
  "请求超时": {
    purpose: "单次模型请求最多等待的秒数，超时后本次 Agent run 会按失败处理。",
  },
  "Scheduler Loop": {
    purpose: "开启后，后台会按调度间隔醒来，检查到期任务、事件、巡检、记忆和通知。",
    note: "Loop 只是唤醒器；是否主动分析账号池还受巡检和事件触发配置控制。",
  },
  "调度间隔": {
    purpose: "Scheduler 两次醒来之间的时间。300 秒表示每 5 分钟检查一次。",
  },
  "账号池巡检": {
    purpose: "开启后，Loop 会按巡检规则选择账号池并触发 Agent 分析。关闭时仍可处理已有到期任务和事件触发。",
  },
  "每轮最多处理任务": {
    purpose: "每次调度 tick 最多跟进多少个到期 Agent task，防止单轮积压过多。",
  },
  "每轮最多巡检池": {
    purpose: "每次调度 tick 最多主动巡检多少个账号池。必巡池会优先占用名额。",
  },
  "巡检间隔": {
    purpose: "同一账号池正常情况下两次巡检之间至少间隔多少分钟。",
  },
  "巡检冷却": {
    purpose: "账号池刚完成巡检后，在冷却时间内不会再次被巡检触发。",
  },
  "每轮最多事件触发": {
    purpose: "每次 tick 最多处理多少个事件突增信号，避免异常集中时同时创建过多 Agent run。",
  },
  "最大并发运行数": {
    purpose: "同时允许执行的 Agent run 数量，用于限制 LLM 请求和后台负载。",
  },
  "任务冷却": {
    purpose: "同一个持续任务两次自动运行之间的最小间隔。",
  },
  "事件冷却": {
    purpose: "同类事件突增在冷却时间内只触发一次，避免相同信号重复唤醒 Agent。",
  },
  "每轮最多记忆总结": {
    purpose: "每个 tick 最多为多少个账号池生成每日或每周长期记忆。",
  },
  "决策通知冷却": {
    purpose: "同一账号池的自动决策通知最小发送间隔，用于避免群内重复刷屏。",
  },
  "最低通知风险等级": {
    purpose: "只有达到该风险等级及以上的 Agent 决策才允许发送通知。",
  },
  "每日记忆总结": {
    purpose: "自动总结前一天的容量、事件、决策、任务变化和人工反馈。",
  },
  "每周记忆总结": {
    purpose: "自动总结上一周的账号质量、风险时段、决策效果和经验。",
  },
  "记忆补生成": {
    purpose: "发现历史周期缺少每日或每周总结时，允许后续 tick 分批补齐。",
  },
  "告警草稿派发": {
    purpose: "允许 Scheduler 按通知策略处理 alert_drafted 任务。默认关闭时只保留草稿。",
  },
  "自动决策通知": {
    purpose: "把选定自动触发来源产生的 Agent 决策摘要发送到已配置的钉钉机器人。",
  },
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
    Promise.all([loadTokens(), loadNotificationChannels(), loadAgentLlmSettings(), loadAgentPools()]).catch((error) => showToast(errorMessage(error), true));
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
      showToast("Agent LLM 配置已保存");
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
      showToast(result.message || "Agent LLM 连接测试通过");
    } catch (error) {
      await loadAgentLlmSettings().catch(() => undefined);
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const refreshCurrentTab = () => {
    const loader = activeTab === "tokens" ? loadTokens : activeTab === "notifications" ? loadNotificationChannels : loadAgentLlmSettings;
    loader().catch((error) => showToast(errorMessage(error), true));
  };

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
                <p>配置账号池运营 Agent 使用的模型、调度、巡检、记忆和通知策略。</p>
              </div>
              <span className={`status-pill ${agentLlmSettings?.enabled ? "success" : ""}`}>{agentLlmSettings?.enabled ? "已启用" : "未启用"}</span>
            </div>
            <form className="form-grid single" onSubmit={submitAgentLlmSettings}>
              <label>
                启用 Agent LLM 调用
                <select value={agentLlmForm.enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, enabled: event.target.value === "yes" }))}>
                  <option value="yes">是</option>
                  <option value="no">否</option>
                </select>
              </label>
              <label>
                <AgentSettingHelp helpKey="Base URL">Base URL</AgentSettingHelp>
                <input
                  value={agentLlmForm.base_url}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, base_url: event.target.value }))}
                  placeholder="https://example.com/v1"
                />
              </label>
              <label>
                <AgentSettingHelp helpKey="API Key">API Key</AgentSettingHelp>
                <input
                  type="password"
                  value={agentLlmForm.api_key}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, api_key: event.target.value }))}
                  placeholder={agentLlmSettings?.api_key_configured ? "留空以保留当前密钥" : "sk-..."}
                />
              </label>
              <div className="muted">当前密钥：{agentLlmSettings?.api_key_configured ? agentLlmSettings.api_key_preview || "已配置" : "未配置"}</div>
              <label>
                <AgentSettingHelp helpKey="主决策模型">主决策模型（Level 1）</AgentSettingHelp>
                <input
                  value={agentLlmForm.level1_model}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, level1_model: event.target.value }))}
                  placeholder="gpt-4.1-mini"
                />
              </label>
              <label>
                <AgentSettingHelp helpKey="主决策温度">主决策温度</AgentSettingHelp>
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
                <AgentSettingHelp helpKey="增强模型">增强模型（Level 2，可选）</AgentSettingHelp>
                <input
                  value={agentLlmForm.level2_model}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, level2_model: event.target.value }))}
                  placeholder="可选"
                />
              </label>
              <label>
                <AgentSettingHelp helpKey="增强模型温度">增强模型温度</AgentSettingHelp>
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
                <AgentSettingHelp helpKey="请求超时">请求超时（秒）</AgentSettingHelp>
                <input
                  min="5"
                  max="300"
                  type="number"
                  value={agentLlmForm.timeout_seconds}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, timeout_seconds: event.target.value }))}
                />
              </label>
              <label>
                <AgentSettingHelp helpKey="Scheduler Loop">启用 Scheduler Loop</AgentSettingHelp>
                <select value={agentLlmForm.loop_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, loop_enabled: event.target.value === "yes" }))}>
                  <option value="yes">是</option>
                  <option value="no">否</option>
                </select>
              </label>
              <label>
                <AgentSettingHelp helpKey="调度间隔">调度间隔（秒）</AgentSettingHelp>
                <input
                  min="60"
                  max="86400"
                  type="number"
                  value={agentLlmForm.scheduler_interval_seconds}
                  onChange={(event) => setAgentLlmForm((current) => ({ ...current, scheduler_interval_seconds: event.target.value }))}
                />
              </label>
              <label>
                <AgentSettingHelp helpKey="账号池巡检">启用账号池巡检</AgentSettingHelp>
                <select value={agentLlmForm.patrol_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, patrol_enabled: event.target.value === "yes" }))}>
                  <option value="yes">是</option>
                  <option value="no">否</option>
                </select>
              </label>
              <div className="agent-scheduler-grid">
                <label>
                  <AgentSettingHelp helpKey="每轮最多处理任务">每轮最多处理任务</AgentSettingHelp>
                  <input
                    min="1"
                    max="100"
                    type="number"
                    value={agentLlmForm.max_tasks_per_tick}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, max_tasks_per_tick: event.target.value }))}
                  />
                </label>
                <label>
                  <AgentSettingHelp helpKey="每轮最多巡检池">每轮最多巡检池</AgentSettingHelp>
                  <input
                    min="0"
                    max="100"
                    type="number"
                    value={agentLlmForm.max_pool_patrols_per_tick}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, max_pool_patrols_per_tick: event.target.value }))}
                  />
                </label>
                <label>
                  <AgentSettingHelp helpKey="巡检间隔">巡检间隔（分钟）</AgentSettingHelp>
                  <input
                    min="5"
                    max="1440"
                    type="number"
                    value={agentLlmForm.pool_patrol_interval_minutes}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, pool_patrol_interval_minutes: event.target.value }))}
                  />
                </label>
                <label>
                  <AgentSettingHelp helpKey="巡检冷却">巡检冷却（分钟）</AgentSettingHelp>
                  <input
                    min="0"
                    max="1440"
                    type="number"
                    value={agentLlmForm.pool_patrol_cooldown_minutes}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, pool_patrol_cooldown_minutes: event.target.value }))}
                  />
                </label>
                <div className="agent-required-patrol-field">
                  <strong>必巡账号池</strong>
                  <div className="muted">必巡池会优先选择，剩余巡检名额再按普通优先级分配。</div>
                  <div className="agent-required-patrol-list">
                    {agentPools.length ? (
                      agentPools.map((pool) => (
                        <label className="checkbox-row" key={pool.id}>
                          <input
                            type="checkbox"
                            checked={agentLlmForm.required_patrol_pool_ids.includes(pool.id)}
                            onChange={() => toggleRequiredPatrolPool(pool.id)}
                          />
                          <span>{pool.name} / {pool.account_type} / 分组 #{pool.active_group_id}</span>
                        </label>
                      ))
                    ) : (
                      <div className="muted">暂无可用账号池。</div>
                    )}
                  </div>
                </div>
                <div className="agent-required-patrol-field">
                  <strong>排除的 Agent 账号池</strong>
                  <div className="muted">排除后，巡检和事件突增自动运行都会忽略这些池。</div>
                  <div className="agent-required-patrol-list">
                    {agentPools.length ? (
                      agentPools.map((pool) => (
                        <label className="checkbox-row" key={pool.id}>
                          <input
                            type="checkbox"
                            checked={agentLlmForm.excluded_agent_pool_ids.includes(pool.id)}
                            onChange={() => toggleExcludedAgentPool(pool.id)}
                          />
                          <span>{pool.name} / {pool.account_type} / 分组 #{pool.active_group_id}</span>
                        </label>
                      ))
                    ) : (
                      <div className="muted">暂无可用账号池。</div>
                    )}
                  </div>
                </div>
                <label>
                  <AgentSettingHelp helpKey="每轮最多事件触发">每轮最多事件触发</AgentSettingHelp>
                  <input
                    min="0"
                    max="100"
                    type="number"
                    value={agentLlmForm.max_event_triggers_per_tick}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, max_event_triggers_per_tick: event.target.value }))}
                  />
                </label>
                <label>
                  <AgentSettingHelp helpKey="最大并发运行数">最大并发运行数</AgentSettingHelp>
                  <input
                    min="1"
                    max="20"
                    type="number"
                    value={agentLlmForm.max_concurrent_runs}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, max_concurrent_runs: event.target.value }))}
                  />
                </label>
                <label>
                  <AgentSettingHelp helpKey="任务冷却">任务冷却（分钟）</AgentSettingHelp>
                  <input
                    min="0"
                    max="1440"
                    type="number"
                    value={agentLlmForm.task_cooldown_minutes}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, task_cooldown_minutes: event.target.value }))}
                  />
                </label>
                <label>
                  <AgentSettingHelp helpKey="事件冷却">事件冷却（分钟）</AgentSettingHelp>
                  <input
                    min="0"
                    max="1440"
                    type="number"
                    value={agentLlmForm.event_trigger_cooldown_minutes}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, event_trigger_cooldown_minutes: event.target.value }))}
                  />
                </label>
                <label>
                  <AgentSettingHelp helpKey="每轮最多记忆总结">每轮最多记忆总结</AgentSettingHelp>
                  <input
                    min="0"
                    max="100"
                    type="number"
                    value={agentLlmForm.max_memory_summaries_per_tick}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, max_memory_summaries_per_tick: event.target.value }))}
                  />
                </label>
                <label>
                  <AgentSettingHelp helpKey="决策通知冷却">决策通知冷却（分钟）</AgentSettingHelp>
                  <input
                    min="0"
                    max="1440"
                    type="number"
                    value={agentLlmForm.decision_notification_cooldown_minutes}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, decision_notification_cooldown_minutes: event.target.value }))}
                  />
                </label>
                <label>
                  <AgentSettingHelp helpKey="最低通知风险等级">最低通知风险等级</AgentSettingHelp>
                  <select
                    value={agentLlmForm.decision_notification_min_severity}
                    onChange={(event) => setAgentLlmForm((current) => ({ ...current, decision_notification_min_severity: event.target.value }))}
                  >
                    <option value="watch">观察（watch）</option>
                    <option value="warning">预警（warning）</option>
                    <option value="danger">紧张（danger）</option>
                    <option value="critical">危险（critical）</option>
                  </select>
                </label>
              </div>
              <div className="agent-scheduler-switches">
                <label>
                  <AgentSettingHelp helpKey="每日记忆总结">每日记忆总结</AgentSettingHelp>
                  <select value={agentLlmForm.daily_memory_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, daily_memory_enabled: event.target.value === "yes" }))}>
                    <option value="yes">是</option>
                    <option value="no">否</option>
                  </select>
                </label>
                <label>
                  <AgentSettingHelp helpKey="每周记忆总结">每周记忆总结</AgentSettingHelp>
                  <select value={agentLlmForm.weekly_memory_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, weekly_memory_enabled: event.target.value === "yes" }))}>
                    <option value="yes">是</option>
                    <option value="no">否</option>
                  </select>
                </label>
                <label>
                  <AgentSettingHelp helpKey="记忆补生成">记忆补生成</AgentSettingHelp>
                  <select value={agentLlmForm.memory_summary_catchup_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, memory_summary_catchup_enabled: event.target.value === "yes" }))}>
                    <option value="yes">是</option>
                    <option value="no">否</option>
                  </select>
                </label>
                <label>
                  <AgentSettingHelp helpKey="告警草稿派发">告警草稿派发</AgentSettingHelp>
                  <select value={agentLlmForm.notification_dispatch_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, notification_dispatch_enabled: event.target.value === "yes" }))}>
                    <option value="yes">是</option>
                    <option value="no">否</option>
                  </select>
                </label>
                <label>
                  <AgentSettingHelp helpKey="自动决策通知">自动决策通知</AgentSettingHelp>
                  <select value={agentLlmForm.decision_notification_enabled ? "yes" : "no"} onChange={(event) => setAgentLlmForm((current) => ({ ...current, decision_notification_enabled: event.target.value === "yes" }))}>
                    <option value="yes">是</option>
                    <option value="no">否</option>
                  </select>
                </label>
              </div>
              <div className="agent-required-patrol-field">
                <strong>决策通知触发来源</strong>
                <div className="muted">为勾选的自动 Agent 运行发送钉钉摘要，复用“通知”页面中已配置的钉钉机器人。</div>
                <div className="agent-required-patrol-list">
                  {decisionNotificationTriggerOptions.map((trigger) => (
                    <label className="checkbox-row" key={trigger}>
                      <input
                        type="checkbox"
                        checked={agentLlmForm.decision_notification_triggers.includes(trigger)}
                        onChange={() => toggleDecisionNotificationTrigger(trigger)}
                      />
                      <span>{agentTriggerLabel(trigger)}</span>
                    </label>
                  ))}
                </div>
              </div>
              <div className="muted">当前保存的是全局调度配置，池级 Agent 策略仍为后续扩展项。</div>
              <div className="button-row">
                <button className="success-button" disabled={busy} type="submit">
                  保存 Agent LLM 配置
                </button>
                <button className="ghost" disabled={busy} onClick={testAgentLlmSettings} type="button">
                  测试已保存配置
                </button>
              </div>
            </form>
          </section>

          <section className="panel">
            <h3>连接状态</h3>
            <div className="list">
              <div className="list-item">
                <div>
                  <strong>已保存配置</strong>
                  <div className="muted">Base URL：{agentLlmSettings?.base_url ? "已配置" : "未配置"}</div>
                  <div className="muted">主决策模型：{agentLlmSettings?.level1_model || "-"}</div>
                  <div className="muted">增强模型：{agentLlmSettings?.level2_model || "-"}</div>
                  <div className="muted">请求超时：{agentLlmSettings?.timeout_seconds ?? "-"} 秒</div>
                  <div className="muted">Scheduler Loop：{(agentLlmSettings?.agent_loop_enabled ?? agentLlmSettings?.loop_enabled) ? "已启用" : "未启用"}</div>
                  <div className="muted">账号池巡检：{agentLlmSettings?.patrol_enabled ? "已启用" : "未启用"}</div>
                  <div className="muted">调度间隔：{agentLlmSettings?.scheduler_interval_seconds ?? "-"} 秒</div>
                  <div className="muted">每轮任务 / 巡检 / 事件：{agentLlmSettings?.max_tasks_per_tick ?? "-"} / {agentLlmSettings?.max_pool_patrols_per_tick ?? "-"} / {agentLlmSettings?.max_event_triggers_per_tick ?? "-"}</div>
                  <div className="muted">必巡账号池：{agentLlmSettings?.required_patrol_pool_ids?.length ?? 0} 个</div>
                  <div className="muted">排除账号池：{agentLlmSettings?.excluded_agent_pool_ids?.length ?? 0} 个</div>
                  <div className="muted">每日 / 每周记忆：{agentLlmSettings?.daily_memory_enabled ? "开" : "关"} / {agentLlmSettings?.weekly_memory_enabled ? "开" : "关"} · 每轮最多 {agentLlmSettings?.max_memory_summaries_per_tick ?? "-"}</div>
                  <div className="muted">告警草稿派发：{agentLlmSettings?.notification_dispatch_enabled ? "已启用" : "未启用"}</div>
                  <div className="muted">自动决策通知：{agentLlmSettings?.decision_notification_enabled ? "已启用" : "未启用"} · 最低 {agentSeverityLabel(agentLlmSettings?.decision_notification_min_severity)} · 冷却 {agentLlmSettings?.decision_notification_cooldown_minutes ?? "-"} 分钟</div>
                  <div className="muted">通知触发来源：{(agentLlmSettings?.decision_notification_triggers || []).map(agentTriggerLabel).join("、") || "-"}</div>
                </div>
              </div>
              <div className="list-item">
                <div>
                  <strong>最近测试</strong>
                  <div className="muted">时间：{formatDateTime(agentLlmSettings?.last_test_at)}</div>
                  <div className={agentLlmSettings?.last_test_status === "success" ? "success-text" : "warning-text"}>
                    状态：{deliveryStatusLabel(agentLlmSettings?.last_test_status)}
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

function AgentSettingHelp({ helpKey, children }: { helpKey: string; children: ReactNode }) {
  const tooltipId = useId();
  const help = AGENT_SETTING_HELP[helpKey];
  if (!help) return <>{children}</>;
  return (
    <span className="agent-setting-help" tabIndex={0} aria-describedby={tooltipId}>
      <span className="agent-setting-help-trigger">{children}</span>
      <span className="agent-setting-help-tooltip" id={tooltipId} role="tooltip">
        <strong>{children}</strong>
        <span>{help.purpose}</span>
        {help.note ? <em>{help.note}</em> : null}
      </span>
    </span>
  );
}

function agentTriggerLabel(value?: string | null) {
  const labels: Record<string, string> = {
    event_spike: "事件突增",
    scheduler_task_due: "任务到期",
    scheduler_review_due: "复盘到期",
    scheduler_patrol: "定时巡检",
  };
  return value ? labels[value] || value : "-";
}

function agentSeverityLabel(value?: string | null) {
  const labels: Record<string, string> = {
    watch: "观察",
    warning: "预警",
    danger: "紧张",
    critical: "危险",
  };
  return value ? labels[value] || value : "-";
}

function deliveryStatusLabel(value?: string | null) {
  if (value === "success") return "成功";
  if (value === "failed") return "失败";
  if (value === "partial") return "部分成功";
  if (value === "skipped") return "跳过";
  return value || "-";
}
