import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ApiPool } from "../types";
import { errorMessage, formatDateTime } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type PoolsResponse = {
  items: ApiPool[];
  total: number;
};

type AgentDecision = {
  severity: string;
  headline: string;
  suggested_add_count: number;
  suggested_push_from_reserve_count?: number;
  suggested_make_new_count?: number;
  should_add_accounts?: boolean;
  confidence?: string;
  main_reasons?: string[];
  risk_factors?: string[];
  data_gaps?: string[];
  should_alert?: boolean;
  alert_channels?: string[];
  requires_human_confirm?: boolean;
  manual_review_required: boolean;
  recommended_actions?: AgentRecommendedAction[];
  suggested_actions: string[];
  next_observation_focus?: string[];
  follow_up_questions?: string[];
  inputs?: Record<string, unknown>;
};

type AgentRecommendedAction = {
  action_type?: string;
  title?: string;
  reason?: string;
  risk_level?: string;
  requires_human_confirm?: boolean;
};

type AgentAnalysisResponse = {
  run_id?: string;
  conversation_id?: string;
  decision_id?: string;
  read_only: boolean;
  pool: ApiPool;
  severity: string;
  headline: string;
  decision: AgentDecision;
  reasons: string[];
  suggested_actions: string[];
  capacity: Record<string, unknown>;
  probe: Record<string, unknown>;
  llm?: {
    enabled?: boolean;
    configured?: boolean;
    level?: string;
    framework?: string;
    model?: string;
    message?: unknown;
    summary?: unknown;
    risk_assessment?: unknown;
    operator_message?: unknown;
    questions?: unknown[];
    error?: unknown;
  };
  agent?: {
    mode?: string;
    planned_by?: string;
    intent?: string;
    thought?: string;
    fallback_reason?: string;
    decision_mode?: string;
    validator?: Record<string, unknown>;
    context_pack?: Record<string, unknown>;
  };
  created_at: string;
};

type AgentRun = {
  run_id?: string;
  conversation_id?: string;
  pool_id?: string | null;
  status?: string;
  trigger?: string;
  severity?: string | null;
  summary?: string | null;
  started_at?: string;
  finished_at?: string | null;
  created_at?: string;
};

type AgentMessage = {
  message_id?: string;
  id?: string;
  conversation_id: string;
  run_id?: string | null;
  role: "user" | "assistant" | "system";
  content: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
};

type AgentPersistedDecision = {
  decision_id?: string;
  run_id?: string;
  conversation_id?: string;
  pool_id?: string | null;
  site_id?: string | null;
  severity?: string;
  headline?: string;
  summary?: string;
  decision?: Partial<AgentDecision> & Record<string, unknown>;
  reasons?: string[];
  suggested_actions?: string[];
  capacity_snapshot?: Record<string, unknown>;
  probe_snapshot?: Record<string, unknown>;
  llm_output?: AgentAnalysisResponse["llm"];
  agent?: AgentAnalysisResponse["agent"];
  chat?: Record<string, unknown>;
  read_only?: boolean;
  trigger?: string;
  created_at?: string;
};

type AgentStateResponse = {
  latest_run?: AgentRun | null;
  latest_decision?: AgentPersistedDecision | null;
  messages?: AgentMessage[];
  running?: boolean;
  running_count?: number;
};

type AgentMessagesResponse = {
  items: AgentMessage[];
  total: number;
};

const severityLabels: Record<string, string> = {
  healthy: "健康",
  watch: "观察",
  warning: "预警",
  danger: "紧张",
  critical: "危险",
};

export function AgentAnalysisPage({ token, showToast }: Props) {
  const [pools, setPools] = useState<ApiPool[]>([]);
  const [selectedPoolId, setSelectedPoolId] = useState("");
  const [loadingPools, setLoadingPools] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [chatMessage, setChatMessage] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [report, setReport] = useState<AgentAnalysisResponse | null>(null);
  const [conversationId, setConversationId] = useState("");
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [showFullHistory, setShowFullHistory] = useState(false);
  const [loadingState, setLoadingState] = useState(false);

  const selectedPool = useMemo(() => pools.find((pool) => pool.id === selectedPoolId) || null, [pools, selectedPoolId]);

  const loadPools = async () => {
    setLoadingPools(true);
    try {
      const data = await api<PoolsResponse>("/agent/pools", token);
      const activePools = (data.items || []).filter((pool) => pool.status !== "disabled");
      setPools(activePools);
      setSelectedPoolId((current) => (activePools.some((pool) => pool.id === current) ? current : activePools[0]?.id || ""));
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setLoadingPools(false);
    }
  };

  const loadAgentState = async () => {
    setLoadingState(true);
    try {
      const state = await api<AgentStateResponse>("/agent/state", token);
      const restoredReport = restoreReportFromState(state);
      setReport(restoredReport);
      setMessages(state.messages || []);
      const restoredConversationId = state.latest_run?.conversation_id || state.latest_decision?.conversation_id || "";
      setConversationId(restoredConversationId);
      const restoredPoolId = restoredReport?.pool?.id || state.latest_run?.pool_id || state.latest_decision?.pool_id || "";
      if (restoredPoolId) {
        setSelectedPoolId(String(restoredPoolId));
      }
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setLoadingState(false);
    }
  };

  const loadConversationMessages = async (nextConversationId: string) => {
    if (!nextConversationId) {
      setMessages([]);
      return;
    }
    const data = await api<AgentMessagesResponse>(`/agent/conversations/${encodeURIComponent(nextConversationId)}/messages`, token);
    setMessages(data.items || []);
  };

  const analyzePool = async () => {
    if (!selectedPoolId) {
      showToast("请先选择账号池", true);
      return;
    }
    setAnalyzing(true);
    try {
      const data = await api<AgentAnalysisResponse>(`/agent/pools/${selectedPoolId}/analyze`, token, { method: "POST" });
      setReport(data);
      const nextConversationId = data.conversation_id || "";
      setConversationId(nextConversationId);
      await loadConversationMessages(nextConversationId);
      showToast("Agent 只读分析完成");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setAnalyzing(false);
    }
  };

  const submitChat = async () => {
    const message = chatMessage.trim();
    if (!message) {
      showToast("请输入要问 Agent 的问题", true);
      return;
    }
    setChatBusy(true);
    try {
      const data = await api<AgentAnalysisResponse>("/agent/chat", token, {
        method: "POST",
        body: JSON.stringify({ message, pool_id: selectedPoolId || undefined, conversation_id: conversationId || undefined }),
      });
      setReport(data);
      const nextConversationId = data.conversation_id || conversationId;
      setConversationId(nextConversationId);
      await loadConversationMessages(nextConversationId);
      setChatMessage("");
      if (data.pool?.id) {
        setSelectedPoolId(String(data.pool.id));
      }
      showToast("Agent 已根据你的问题完成分析");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setChatBusy(false);
    }
  };

  useEffect(() => {
    loadPools();
    loadAgentState();
  }, []);

  return (
    <section className="view agent-analysis-page">
      <div className="topbar">
        <div>
          <h2>Agent分析</h2>
          <p>基于 Context Pack 和 Level 1 模型生成账号池运营决策，当前阶段只读，不直接执行账号操作。</p>
        </div>
        <div className="button-row">
          <button className="ghost" type="button" onClick={loadPools} disabled={loadingPools}>
            {loadingPools ? "加载中..." : "刷新账号池"}
          </button>
          <button type="button" onClick={analyzePool} disabled={!selectedPoolId || analyzing}>
            {analyzing ? "分析中..." : "分析"}
          </button>
        </div>
      </div>

      <section className="panel agent-control-panel">
        <div className="panel-header">
          <div>
            <h3>分析目标</h3>
            <p>选择账号池后，Agent 会结合容量、探测、历史决策和最近对话进行判断。</p>
          </div>
          <span className="status-pill accent">LLM decision</span>
        </div>
        <div className="agent-control-grid">
          <label>
            <span className="field-label">
              <strong>账号池</strong>
            </span>
            <select value={selectedPoolId} onChange={(event) => setSelectedPoolId(event.target.value)}>
              {!pools.length && <option value="">暂无账号池</option>}
              {pools.map((pool) => (
                <option key={pool.id} value={pool.id}>
                  {pool.name} / {pool.account_type} / group #{pool.active_group_id}
                </option>
              ))}
            </select>
          </label>
          <Metric label="类型" value={selectedPool?.account_type || "-"} />
          <Metric label="分组" value={selectedPool?.active_group_id ? `group #${selectedPool.active_group_id}` : "-"} />
        </div>
        <div className="agent-chat-box">
          <label>
            <span className="field-label">
              <strong>向 Agent 提问</strong>
            </span>
            <textarea
              rows={3}
              value={chatMessage}
              onChange={(event) => setChatMessage(event.target.value)}
              placeholder="例如：今天这个池要不要补号？如果要补，先准备多少个比较稳？"
            />
          </label>
          <div className="button-row">
            <button type="button" onClick={submitChat} disabled={chatBusy || analyzing || !chatMessage.trim()}>
              {chatBusy ? "分析中..." : "发送问题"}
            </button>
          </div>
        </div>
        <AgentMessageList messages={messages} loading={loadingState} expanded={showFullHistory} onToggle={() => setShowFullHistory((value) => !value)} />
      </section>

      {report ? <AgentReport report={report} /> : <div className="empty-state">选择账号池后点击“分析”，这里会显示 Agent 决策结果。</div>}
    </section>
  );
}

function AgentMessageList({
  messages,
  loading,
  expanded,
  onToggle,
}: {
  messages: AgentMessage[];
  loading: boolean;
  expanded: boolean;
  onToggle: () => void;
}) {
  if (loading && !messages.length) {
    return <div className="agent-message-empty">正在恢复最近一次 Agent 对话...</div>;
  }
  if (!messages.length) {
    return <div className="agent-message-empty">暂无持久化对话消息。</div>;
  }
  const visibleMessages = expanded ? messages : messages.slice(-4);
  const hiddenCount = Math.max(0, messages.length - visibleMessages.length);
  return (
    <div className="agent-history-panel">
      <div className="agent-history-head">
        <strong>最近对话</strong>
        <button className="ghost compact-button" type="button" onClick={onToggle}>
          {expanded ? "收起" : hiddenCount > 0 ? `展开 ${messages.length} 条` : "展开"}
        </button>
      </div>
      {!expanded && hiddenCount > 0 && <div className="agent-message-empty">已折叠较早的 {hiddenCount} 条消息。</div>}
      <div className="agent-message-list">
        {visibleMessages.map((message, index) => (
          <div className={`agent-message-item ${message.role}`} key={message.message_id || message.id || `${message.role}-${index}`}>
            <div className="agent-message-meta">
              <strong>{message.role === "user" ? "你" : message.role === "assistant" ? "Agent" : "System"}</strong>
              <span>{formatDateTime(message.created_at)}</span>
            </div>
            <p>{message.content}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function AgentReport({ report }: { report: AgentAnalysisResponse }) {
  const decision = report.decision;
  const llmError = Boolean(report.llm?.error);
  const llmMessage =
    displayText(report.llm?.message) ||
    composeFallbackLlmMessage(report.llm?.operator_message, report.llm?.summary, report.llm?.risk_assessment, report.llm?.questions);
  return (
    <>
      <section className={`agent-result agent-result-${severityTone(report.severity)}`}>
        <div>
          <span className={`status-pill ${severityTone(report.severity)}`}>{severityLabels[report.severity] || report.severity}</span>
          <h3>{report.headline}</h3>
          <p>生成时间：{formatDateTime(report.created_at)} · 只读分析：{report.read_only ? "是" : "否"}</p>
        </div>
        <div className="agent-result-count">
          <span>建议补号</span>
          <strong>{decision.suggested_add_count}</strong>
        </div>
      </section>

      {report.llm?.configured && (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>LLM 主决策</h3>
              <p>{report.llm.model ? `${report.llm.model} / ${report.llm.framework || "http_fallback"}` : "OpenAI-compatible decision"}</p>
            </div>
            <span className={`status-pill ${llmError ? "warning" : "accent"}`}>
              {llmError ? "failed" : "enabled"}
            </span>
          </div>
          {llmError ? (
            <div className="empty-state">{displayText(report.llm.error)}</div>
          ) : (
            <div className="agent-llm-message">{llmMessage || "模型未返回可展示的分析内容。"}</div>
          )}
        </section>
      )}

      <section className="grid two">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>决策摘要</h3>
              <p>由 Level 1 模型基于完整上下文输出，后端只做结构与安全校验。</p>
            </div>
          </div>
          <div className="compact-stats agent-action-stats">
            <Metric label="置信度" value={confidenceLabel(decision.confidence)} />
            <Metric label="需要补号" value={(decision.should_add_accounts ?? decision.suggested_add_count > 0) ? "是" : "否"} />
            <Metric label="需要告警" value={decision.should_alert ? "是" : "否"} />
            <Metric label="人工复核" value={decision.manual_review_required ? "需要" : "不需要"} />
          </div>
          <div className="list">
            {decisionReasons(report).map((reason) => (
              <div className="list-item" key={reason}>
                {reason}
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>建议动作</h3>
              <p>当前阶段只给建议，不直接执行动作。</p>
            </div>
          </div>
          <div className="list">
            {decisionActions(report).map((action) => (
              <div className="list-item" key={action}>
                {action}
              </div>
            ))}
          </div>
        </section>
      </section>

      {(decision.data_gaps?.length || decision.next_observation_focus?.length || decision.follow_up_questions?.length) && (
        <section className="grid two">
          <section className="panel">
            <div className="panel-header">
              <div>
                <h3>数据不足</h3>
                <p>模型认为会影响判断质量的数据缺口。</p>
              </div>
            </div>
            <div className="list">
              {(decision.data_gaps?.length ? decision.data_gaps : ["暂无明显数据缺口。"]).map((item) => (
                <div className="list-item" key={item}>
                  {item}
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <h3>下一步观察</h3>
                <p>供人工继续追踪的问题和指标。</p>
              </div>
            </div>
            <div className="list">
              {[...(decision.next_observation_focus || []), ...(decision.follow_up_questions || [])].map((item) => (
                <div className="list-item" key={item}>
                  {item}
                </div>
              ))}
            </div>
          </section>
        </section>
      )}

      <section className="grid two">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>API账号池状态数据</h3>
              <p>读取现有缓存，不重新计算、不写回缓存。</p>
            </div>
          </div>
          <div className="agent-metric-grid">
            <Metric label="可用账号" value={numberText(report.capacity.available_accounts)} />
            <Metric label="active" value={numberText(report.capacity.active_account_count)} />
            <Metric label="备用" value={numberText(report.capacity.reserve_account_count)} />
            <Metric label="可用天数" value={daysText(report.capacity.current_speed_days)} />
            <Metric label="24h 5h峰值" value={multipleText(report.capacity.recent_day_five_hour_peak_multiple)} />
            <Metric label="突发1h峰值" value={multipleText(report.capacity.burst_1h_five_hour_multiple)} />
            <Metric label="突发趋势" value={burstTrendText(report.capacity)} />
            <Metric label="突发1h折算" value={usdText(report.capacity.burst_1h_cost)} />
            <Metric label="突发5h折算" value={usdText(report.capacity.burst_1h_five_hour_estimated_cost)} />
            <Metric label="趋势变化" value={percentChangeText(report.capacity.burst_1h_trend_change_percent)} />
            <Metric label="5h可用" value={usdText(report.capacity.five_hour_remaining_usd)} />
            <Metric label="7d可用" value={usdText(report.capacity.seven_day_remaining_usd)} />
            <Metric label="缓存时间" value={formatDateTime(report.capacity.last_refreshed_at)} />
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>账号探测数据</h3>
              <p>读取事件记录中的 401、恢复、重复邮箱和探测新鲜度。</p>
            </div>
          </div>
          <div className="agent-metric-grid">
            <Metric label="探测新鲜" value={report.probe.probe_fresh ? "是" : "否"} />
            <Metric label="最后探测" value={formatDateTime(report.probe.last_probe_at)} />
            <Metric label="1h 401" value={numberText(report.probe.detected_401_1h ?? report.probe.pro_401_1h)} />
            <Metric label="24h 401" value={numberText(report.probe.detected_401_24h ?? report.probe.pro_401_24h)} />
            <Metric label="7d 401" value={numberText(report.probe.detected_401_7d ?? report.probe.pro_401_7d)} />
            <Metric label="24h恢复" value={numberText(report.probe.recovered_24h)} />
            <Metric label="重复邮箱" value={numberText(report.probe.duplicate_email_alert_count)} />
            <Metric label="7d中位寿命" value={hoursText(report.probe.median_survival_hours_7d)} />
          </div>
        </section>
      </section>
    </>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="compact-stat agent-metric">
      <span>{label}</span>
      <strong>{String(value ?? "-")}</strong>
    </div>
  );
}

function severityTone(value: string): "success" | "warning" | "danger" | "muted" | "accent" {
  if (value === "healthy") return "success";
  if (value === "warning" || value === "watch") return "warning";
  if (value === "danger" || value === "critical") return "danger";
  return "accent";
}

function numberText(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? String(Math.round(number)) : "-";
}

function daysText(value: unknown) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  return number < 1 ? `${(number * 24).toFixed(1)}小时` : `${number.toFixed(1)}天`;
}

function multipleText(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(2)}x` : "-";
}

function percentChangeText(value: unknown) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(0)}%`;
}

function burstTrendText(capacity: Record<string, unknown>) {
  const label = displayText(capacity.burst_1h_trend_label);
  const strength = displayText(capacity.burst_1h_trend_strength_label);
  if (!label) return "-";
  if (!strength || strength === "等待数据") return label;
  return `${label} · ${strength}`;
}

function usdText(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? `$${number.toFixed(2)}` : "$-";
}

function hoursText(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(1)}h` : "-";
}

function confidenceLabel(value: unknown) {
  const text = displayText(value).toLowerCase();
  if (text === "high") return "高";
  if (text === "medium") return "中";
  if (text === "low") return "低";
  return "-";
}

function decisionReasons(report: AgentAnalysisResponse): string[] {
  const decision = report.decision;
  const items = [
    ...(decision.main_reasons || []),
    ...(decision.risk_factors || []),
    ...report.reasons,
  ]
    .map(displayText)
    .filter(Boolean);
  return dedupe(items).length ? dedupe(items) : ["模型未返回明确原因。"];
}

function decisionActions(report: AgentAnalysisResponse): string[] {
  const decision = report.decision;
  const structured = (decision.recommended_actions || [])
    .map((action) => {
      const title = displayText(action.title || action.action_type);
      const reason = displayText(action.reason);
      const confirm = action.requires_human_confirm ? "需要人工确认" : "";
      return [title, reason, confirm].filter(Boolean).join("：");
    })
    .filter(Boolean);
  const fallback = [...(decision.suggested_actions || []), ...report.suggested_actions].map(displayText).filter(Boolean);
  return dedupe(structured.length ? structured : fallback).length ? dedupe(structured.length ? structured : fallback) : ["模型未返回建议动作。"];
}

function displayText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function composeFallbackLlmMessage(operatorMessage: unknown, summary: unknown, riskAssessment: unknown, questions: unknown): string {
  const parts = [displayText(operatorMessage), displayText(summary), displayText(riskAssessment)].filter(Boolean);
  if (Array.isArray(questions) && questions.length) {
    const questionText = questions.map(displayText).filter(Boolean).join("；");
    if (questionText) parts.push(`需要人工确认：${questionText}。`);
  }
  return Array.from(new Set(parts)).join("\n\n");
}

function dedupe(values: string[]): string[] {
  return Array.from(new Set(values.map((value) => value.trim()).filter(Boolean)));
}

function restoreReportFromState(state: AgentStateResponse): AgentAnalysisResponse | null {
  const decisionDoc = state.latest_decision;
  if (!decisionDoc) return null;
  const capacity = asRecord(decisionDoc.capacity_snapshot);
  const probe = asRecord(decisionDoc.probe_snapshot);
  const pool = restorePool(decisionDoc, capacity);
  const decision = normalizeDecision(decisionDoc);
  return {
    run_id: decisionDoc.run_id || state.latest_run?.run_id,
    conversation_id: decisionDoc.conversation_id || state.latest_run?.conversation_id,
    decision_id: decisionDoc.decision_id,
    read_only: decisionDoc.read_only ?? true,
    pool,
    severity: decisionDoc.severity || decision.severity || "healthy",
    headline: decisionDoc.headline || decision.headline || decisionDoc.summary || "Agent 最近一次分析",
    decision,
    reasons: Array.isArray(decisionDoc.reasons) ? decisionDoc.reasons : [],
    suggested_actions: Array.isArray(decisionDoc.suggested_actions) ? decisionDoc.suggested_actions : decision.suggested_actions || [],
    capacity,
    probe,
    llm: decisionDoc.llm_output,
    agent: decisionDoc.agent,
    created_at: decisionDoc.created_at || state.latest_run?.finished_at || state.latest_run?.created_at || "",
  };
}

function normalizeDecision(decisionDoc: AgentPersistedDecision): AgentDecision {
  const raw = asRecord(decisionDoc.decision);
  return {
    severity: textOr(raw.severity, decisionDoc.severity || "healthy"),
    headline: textOr(raw.headline, decisionDoc.headline || decisionDoc.summary || "Agent 最近一次分析"),
    suggested_add_count: numberOr(raw.suggested_add_count, 0),
    suggested_push_from_reserve_count: numberOr(raw.suggested_push_from_reserve_count, 0),
    suggested_make_new_count: numberOr(raw.suggested_make_new_count, 0),
    should_add_accounts: booleanOr(raw.should_add_accounts, numberOr(raw.suggested_add_count, 0) > 0),
    confidence: textOr(raw.confidence, ""),
    main_reasons: stringList(raw.main_reasons),
    risk_factors: stringList(raw.risk_factors),
    data_gaps: stringList(raw.data_gaps),
    should_alert: booleanOr(raw.should_alert, false),
    alert_channels: stringList(raw.alert_channels),
    requires_human_confirm: booleanOr(raw.requires_human_confirm, Boolean(raw.manual_review_required)),
    manual_review_required: Boolean(raw.manual_review_required),
    recommended_actions: Array.isArray(raw.recommended_actions)
      ? raw.recommended_actions.map((item) => asRecord(item) as AgentRecommendedAction)
      : [],
    suggested_actions: Array.isArray(raw.suggested_actions)
      ? raw.suggested_actions.map(displayText).filter(Boolean)
      : Array.isArray(decisionDoc.suggested_actions)
        ? decisionDoc.suggested_actions
        : [],
    next_observation_focus: stringList(raw.next_observation_focus),
    follow_up_questions: stringList(raw.follow_up_questions),
    inputs: asRecord(raw.inputs),
  };
}

function restorePool(decisionDoc: AgentPersistedDecision, capacity: Record<string, unknown>): ApiPool {
  const rawPool = asRecord(capacity.pool);
  const poolId = textOr(rawPool.id, decisionDoc.pool_id || "unknown");
  return {
    id: poolId,
    name: textOr(rawPool.name, poolId),
    account_type: (textOr(rawPool.account_type, "other") as ApiPool["account_type"]) || "other",
    site_id: textOr(rawPool.site_id, decisionDoc.site_id || "default"),
    active_group_id: numberOr(rawPool.active_group_id, numberOr(capacity.group_id, 0)),
    verification_group_id: rawPool.verification_group_id === null || rawPool.verification_group_id === undefined ? null : numberOr(rawPool.verification_group_id, 0),
    min_active: numberOr(rawPool.min_active, 20),
    target_active: numberOr(rawPool.target_active, 30),
    max_avg_5h_used: numberOr(rawPool.max_avg_5h_used, 70),
    max_avg_7d_used: numberOr(rawPool.max_avg_7d_used, 80),
    min_reserve: numberOr(rawPool.min_reserve, 10),
    status: rawPool.status === "disabled" ? "disabled" : "active",
    created_at: displayText(rawPool.created_at),
    updated_at: displayText(rawPool.updated_at),
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function textOr(value: unknown, fallback: string): string {
  const text = displayText(value).trim();
  return text || fallback;
}

function numberOr(value: unknown, fallback: number): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function booleanOr(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(displayText).filter(Boolean) : [];
}
