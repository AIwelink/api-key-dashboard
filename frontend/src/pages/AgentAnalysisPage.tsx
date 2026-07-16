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
  evidence_summary?: AgentEvidenceSummary;
  event_assessment?: AgentEventAssessment;
  memory_used?: AgentMemoryReference[];
  inputs?: Record<string, unknown>;
};

type AgentEvidenceSummary = {
  capacity?: string[];
  events?: string[];
  probe?: string[];
  memory?: string[];
};

type AgentEventAssessment = {
  has_recent_ban_burst?: boolean;
  ban_burst_window?: string | null;
  is_continuous_degradation?: boolean;
  interpretation?: string;
};

type AgentMemoryReference = {
  memory_id?: string;
  reason?: string;
};

type AgentRecommendedAction = {
  action_type?: string;
  title?: string;
  reason?: string;
  risk_level?: string;
  requires_human_confirm?: boolean;
};

type AgentTaskSummary = {
  task_id?: string;
  task_type?: string;
  status?: string;
  severity?: string;
  title?: string;
  requires_human_confirm?: boolean;
  alert_status?: string | null;
  next_check_at?: string | null;
  review_after?: string | null;
  current_decision_id?: string | null;
  latest_state_reason?: string | null;
  latest_state_changed_at?: string | null;
  updated_at?: string | null;
  state_history?: AgentTaskStateHistory[];
  last_review?: Record<string, unknown>;
  last_human_feedback?: Record<string, unknown>;
};

type AgentTaskStateHistory = {
  from_status?: string | null;
  to_status?: string | null;
  reason?: string | null;
  changed_at?: string | null;
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
    task?: AgentTaskSummary | null;
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

type AgentMessage = {
  message_id?: string;
  id?: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
};

type AgentSchedulerStatus = {
  enabled?: boolean;
  settings?: AgentSchedulerSettings | null;
  running?: boolean;
  latest_tick?: AgentSchedulerTick | null;
  latest_error_tick?: AgentSchedulerTick | null;
  task_summary?: AgentSchedulerTaskSummary | null;
  patrol_summary?: AgentSchedulerPatrolSummary | null;
  latest_auto_trigger?: AgentSchedulerAutoTrigger | null;
  latest_review_result?: AgentSchedulerReviewResult | null;
  latest_eval_run?: AgentEvalRunSummary | null;
};

type AgentSchedulerSettings = {
  patrol_enabled?: boolean;
  max_pool_patrols_per_tick?: number;
  required_patrol_pool_ids?: string[];
  excluded_agent_pool_ids?: string[];
  max_memory_summaries_per_tick?: number;
};

type AgentSchedulerTick = {
  tick_id?: string;
  status?: string;
  reason?: string;
  started_at?: string;
  finished_at?: string;
  skip_reason?: string | null;
  errors?: unknown[];
};

type AgentSchedulerTaskSummary = {
  due_observing_count?: number;
  due_review_count?: number;
  waiting_human_count?: number;
  alert_drafted_count?: number;
};

type AgentSchedulerPatrolSummary = {
  enabled?: boolean;
  implemented?: boolean;
  selected?: number;
  processed?: number;
  skipped?: number;
  errors?: number;
  pending?: number;
  latest_tick_at?: string | null;
  reason?: string | null;
};

type AgentSchedulerAutoTrigger = {
  run_id?: string;
  trigger?: string;
  status?: string;
  pool_id?: string | null;
  task_id?: string | null;
  signal?: string | null;
  started_at?: string;
  finished_at?: string | null;
  summary?: string | null;
};

type AgentSchedulerReviewResult = {
  task_id?: string;
  pool_id?: string | null;
  review_result?: string | null;
  next_status?: string | null;
  summary?: string | null;
  reviewed_at?: string | null;
  memory_id?: string | null;
};

type AgentEvalRunSummary = {
  eval_run_id?: string;
  suite?: string | null;
  mode?: string | null;
  status?: string | null;
  finished_at?: string | null;
  started_at?: string | null;
  total?: number;
  passed?: number;
  failed?: number;
  score?: number;
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
  const [schedulerStatus, setSchedulerStatus] = useState<AgentSchedulerStatus | null>(null);
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
    }
  };

  const loadAgentSchedulerStatus = async () => {
    try {
      const status = await api<AgentSchedulerStatus>("/agent/scheduler/status", token);
      setSchedulerStatus(status);
    } catch {
      setSchedulerStatus(null);
    }
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
      setMessages([]);
      const nextConversationId = data.conversation_id || "";
      setConversationId(nextConversationId);
      void loadAgentSchedulerStatus();
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
      setMessages([]);
      const nextConversationId = data.conversation_id || conversationId;
      setConversationId(nextConversationId);
      setChatMessage("");
      if (data.pool?.id) {
        setSelectedPoolId(String(data.pool.id));
      }
      void loadAgentSchedulerStatus();
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
    loadAgentSchedulerStatus();
  }, []);

  return (
    <section className="view agent-analysis-page">
      <div className="topbar">
        <div>
          <h2>Agent分析</h2>
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
      </section>

      <AgentSchedulerSummaryPanel status={schedulerStatus} />

      <AgentRecentConversation messages={messages} />

      {report ? <AgentReport report={report} /> : <div className="empty-state">选择账号池后点击“分析”，这里会显示 Agent 决策结果。</div>}
    </section>
  );
}

function AgentRecentConversation({ messages }: { messages: AgentMessage[] }) {
  const visibleMessages = messages.filter((item) => item.role !== "system").slice(-2);
  if (!visibleMessages.length) return null;
  return (
    <section className="panel agent-recent-conversation">
      <div className="panel-header">
        <div>
          <h3>最近对话</h3>
          <p>只保留最后两条，完整历史后续放到 run 详情页。</p>
        </div>
      </div>
      <div className="agent-recent-message-list">
        {visibleMessages.map((message, index) => (
          <div className={`agent-recent-message ${message.role}`} key={message.message_id || message.id || `${message.role}-${index}`}>
            <span>{message.role === "user" ? "你" : "Agent"} · {formatDateTime(message.created_at)}</span>
            <strong>{message.content}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}

function AgentSchedulerSummaryPanel({ status }: { status: AgentSchedulerStatus | null }) {
  const latestTick = status?.latest_tick || null;
  const taskSummary = status?.task_summary || {};
  const dueTaskCount = (taskSummary.due_observing_count || 0) + (taskSummary.due_review_count || 0);
  const latestAutoTrigger = status?.latest_auto_trigger || null;
  const latestReview = status?.latest_review_result || null;
  const patrolSummary = status?.patrol_summary || null;
  const latestEval = status?.latest_eval_run || null;
  return (
    <section className="panel agent-scheduler-summary-panel">
      <div className="panel-header">
        <div>
          <h3>Scheduler 状态</h3>
          <p>Agent 自启动 loop 的简短运行状态。</p>
        </div>
        <span className={`status-pill ${status?.enabled ? "success" : "muted"}`}>{status?.enabled ? "loop on" : "loop off"}</span>
      </div>
      <div className="compact-stats agent-scheduler-summary-stats">
        <Metric label="巡检开关" value={(patrolSummary?.enabled ?? status?.settings?.patrol_enabled) ? "已启用" : "未启用"} />
        <Metric label="最近巡检" value={patrolProcessedText(patrolSummary)} />
        <Metric label="评测状态" value={evalStatusText(latestEval)} />
        <Metric label="评测通过率" value={evalScoreText(latestEval)} />
        <Metric label="Loop 开关" value={status?.enabled ? "已启用" : "未启用"} />
        <Metric label="运行中" value={status?.running ? "是" : "否"} />
        <Metric label="最近调度时间" value={formatOptionalDate(latestTick?.finished_at || latestTick?.started_at)} />
        <Metric label="调度状态" value={schedulerTickStatusText(latestTick)} />
        <Metric label="到期任务" value={dueTaskCount ? `${dueTaskCount} 个` : "无"} />
        <Metric label="等待人工" value={taskSummary.waiting_human_count ? `${taskSummary.waiting_human_count} 个` : "无"} />
        <Metric label="告警草稿" value={taskSummary.alert_drafted_count ? `${taskSummary.alert_drafted_count} 个` : "无"} />
        <Metric label="自动触发" value={triggerLabel(latestAutoTrigger?.trigger)} />
        <Metric label="最近复盘" value={reviewResultLabel(latestReview?.review_result)} />
      </div>
      <div className="agent-task-reason">
        <span>最近自动动作</span>
        <strong>{schedulerActivityText(latestAutoTrigger, latestReview)}</strong>
      </div>
    </section>
  );
}

function AgentReport({ report }: { report: AgentAnalysisResponse }) {
  const decision = report.decision;
  const llmError = Boolean(report.llm?.error);
  const llmMessage =
    displayText(report.llm?.message) ||
    composeFallbackLlmMessage(report.llm?.operator_message, report.llm?.summary, report.llm?.risk_assessment, report.llm?.questions);
  const evidenceItems = decisionEvidenceItems(report);
  const eventItems = eventAssessmentItems(decision);
  const observationItems = dedupe([...(decision.next_observation_focus || []), ...(decision.follow_up_questions || [])].map(displayText).filter(Boolean));
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

      <AgentTaskSummaryPanel task={report.agent?.task || null} />

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
              <h3>核心依据</h3>
              <p>来自模型整理后的容量、事件、探测和记忆证据。</p>
            </div>
          </div>
          <div className="list">
            {(evidenceItems.length ? evidenceItems : ["模型未返回结构化核心依据。"]).map((item) => (
              <div className="list-item" key={item}>
                {item}
              </div>
            ))}
          </div>
        </section>
      </section>

      <section className="grid two">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>事件判断摘要</h3>
              <p>只展示事件流结论，不展开 24h 明细或完整窗口。</p>
            </div>
          </div>
          <div className="list">
            {(eventItems.length ? eventItems : ["模型未返回明确事件判断。"]).map((item) => (
              <div className="list-item" key={item}>
                {item}
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>数据缺口</h3>
              <p>模型认为会影响判断质量的数据不足。</p>
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
      </section>

      <section className="grid two">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>下一步观察</h3>
              <p>供人工继续追踪的问题和指标。</p>
            </div>
          </div>
          <div className="list">
            {(observationItems.length ? observationItems : ["暂无额外观察项。"]).map((item) => (
              <div className="list-item" key={item}>
                {item}
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>审计与详情</h3>
              <p>完整 Context Pack、事件窗口和长期记忆暂不在主页面展开。</p>
            </div>
          </div>
          <div className="list">
            <div className="list-item">run：{report.run_id || "-"}</div>
            <div className="list-item">decision：{report.decision_id || "-"}</div>
            <div className="list-item">conversation：{report.conversation_id || "-"}</div>
          </div>
        </section>
      </section>
    </>
  );
}

function AgentTaskSummaryPanel({ task }: { task: AgentTaskSummary | null }) {
  const latestReason = taskLatestReason(task);
  const status = task?.status || "none";
  return (
    <section className="panel agent-task-summary-panel">
      <div className="panel-header">
        <div>
          <h3>任务状态</h3>
          <p>当前 Agent 对这个运营问题的持续跟进状态。</p>
        </div>
        <span className={`status-pill ${taskStatusTone(status)}`}>{taskStatusLabel(status)}</span>
      </div>
      <div className="compact-stats agent-task-stats">
        <Metric label="当前状态" value={taskStatusLabel(status)} />
        <Metric label="等待人工" value={task?.requires_human_confirm ? "是" : "否"} />
        <Metric label="告警草稿" value={task?.alert_status === "drafted" ? "有" : "无"} />
        <Metric label="下次观察" value={formatOptionalDate(task?.next_check_at)} />
        <Metric label="复盘时间" value={formatOptionalDate(task?.review_after)} />
        <Metric label="更新时间" value={formatOptionalDate(task?.updated_at)} />
      </div>
      <div className="agent-task-reason">
        <span>最近变化</span>
        <strong>{latestReason || (task ? "暂无状态变化原因。" : "暂无持续任务。")}</strong>
      </div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  const displayValue = String(value ?? "-");
  return (
    <div className="compact-stat agent-metric" title={`${label}：${displayValue}`}>
      <span>{label}</span>
      <strong>{displayValue}</strong>
    </div>
  );
}

function severityTone(value: string): "success" | "warning" | "danger" | "muted" | "accent" {
  if (value === "healthy") return "success";
  if (value === "warning" || value === "watch") return "warning";
  if (value === "danger" || value === "critical") return "danger";
  return "accent";
}

function taskStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    open: "打开",
    observing: "观察中",
    waiting_human: "等待人工",
    alert_drafted: "告警草稿",
    review_due: "待复盘",
    closed: "已关闭",
    failed: "失败",
    none: "暂无任务",
  };
  return labels[value] || value || "-";
}

function taskStatusTone(value: string): "success" | "warning" | "danger" | "muted" | "accent" {
  if (value === "closed") return "success";
  if (value === "failed") return "danger";
  if (value === "waiting_human" || value === "alert_drafted" || value === "review_due") return "warning";
  if (value === "none") return "muted";
  return "accent";
}

function formatOptionalDate(value?: string | null): string {
  return value ? formatDateTime(value) : "-";
}

function schedulerTickStatusText(tick?: AgentSchedulerTick | null): string {
  if (!tick) return "-";
  const errorCount = Array.isArray(tick.errors) ? tick.errors.length : 0;
  if (tick.status === "success") return "成功";
  if (tick.status === "partial") return errorCount ? `部分成功 · ${errorCount} 个错误` : "部分成功";
  if (tick.status === "failed") return errorCount ? `失败 · ${errorCount} 个错误` : "失败";
  if (tick.status === "skipped") return tick.skip_reason === "agent_loop_disabled" ? "已跳过：loop 关闭" : "已跳过";
  return tick.status || "-";
}

function triggerLabel(value?: string | null): string {
  const labels: Record<string, string> = {
    scheduler_patrol: "定时巡检",
    scheduler_task_due: "任务到期",
    scheduler_review_due: "复盘到期",
    event_spike: "事件突增",
    memory_daily_summary: "每日记忆",
    memory_weekly_summary: "每周记忆",
    notification_dispatch: "通知派发",
  };
  return value ? labels[value] || value : "-";
}

function reviewResultLabel(value?: string | null): string {
  const labels: Record<string, string> = {
    useful: "有效",
    too_conservative: "偏保守",
    too_aggressive: "偏激进",
    wrong_interpretation: "判断偏差",
    insufficient_data: "证据不足",
  };
  return value ? labels[value] || value : "-";
}

function patrolProcessedText(summary?: AgentSchedulerPatrolSummary | null): string {
  if (!summary) return "-";
  const selected = Number(summary.selected || 0);
  const processed = Number(summary.processed || 0);
  const skipped = Number(summary.skipped || 0);
  const errors = Number(summary.errors || 0);
  const pending = Number(summary.pending || 0);
  if (!selected && !processed && !skipped && !errors && !pending) {
    return summary.enabled ? "本轮暂无处理" : "未启用";
  }
  const parts = [`选中 ${selected}`, `处理 ${processed}`];
  if (skipped) parts.push(`跳过 ${skipped}`);
  if (pending) parts.push(`待处理 ${pending}`);
  if (errors) parts.push(`错误 ${errors}`);
  return parts.join(" / ");
}

function evalStatusText(run?: AgentEvalRunSummary | null): string {
  if (!run) return "-";
  const labels: Record<string, string> = {
    success: "通过",
    failed: "未通过",
    partial: "部分通过",
    running: "运行中",
  };
  return labels[String(run.status || "")] || String(run.status || "-");
}

function evalScoreText(run?: AgentEvalRunSummary | null): string {
  if (!run) return "-";
  const score = Number(run.score);
  const total = Number(run.total || 0);
  const passed = Number(run.passed || 0);
  if (!Number.isFinite(score)) return total ? `${passed}/${total}` : "-";
  return total ? `${Math.round(score * 100)}% (${passed}/${total})` : `${Math.round(score * 100)}%`;
}

function schedulerActivityText(autoTrigger?: AgentSchedulerAutoTrigger | null, review?: AgentSchedulerReviewResult | null): string {
  const triggerTime = autoTrigger?.finished_at || autoTrigger?.started_at || "";
  const reviewTime = review?.reviewed_at || "";
  if (reviewTime && (!triggerTime || reviewTime >= triggerTime)) {
    const result = reviewResultLabel(review?.review_result);
    const summary = displayText(review?.summary);
    return [`复盘：${result}`, summary, formatOptionalDate(reviewTime)].filter(Boolean).join(" · ");
  }
  if (autoTrigger) {
    const trigger = triggerLabel(autoTrigger.trigger);
    const signal = displayText(autoTrigger.signal);
    const summary = displayText(autoTrigger.summary);
    return [trigger, signal ? `信号：${signal}` : "", summary, formatOptionalDate(triggerTime)].filter(Boolean).join(" · ");
  }
  return "暂无自动触发记录。";
}

function taskLatestReason(task: AgentTaskSummary | null): string {
  if (!task) return "";
  const directReason = displayText(task.latest_state_reason).trim();
  if (directReason) return directReason;
  const history = Array.isArray(task.state_history) ? task.state_history : [];
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const reason = displayText(history[index]?.reason).trim();
    if (reason) return reason;
  }
  return "";
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

function decisionEvidenceItems(report: AgentAnalysisResponse): string[] {
  const evidence = report.decision.evidence_summary;
  if (!evidence) return [];
  return dedupe([
    ...(evidence.capacity || []).map((item) => `容量：${item}`),
    ...(evidence.events || []).map((item) => `事件：${item}`),
    ...(evidence.probe || []).map((item) => `探测：${item}`),
    ...(evidence.memory || []).map((item) => `记忆：${item}`),
  ]);
}

function eventAssessmentItems(decision: AgentDecision): string[] {
  const assessment = decision.event_assessment;
  if (!assessment) return [];
  const items: string[] = [];
  if (assessment.interpretation) {
    items.push(assessment.interpretation);
  }
  if (assessment.ban_burst_window) {
    items.push(`集中异常窗口：${assessment.ban_burst_window}`);
  }
  items.push(`近期集中封号：${assessment.has_recent_ban_burst ? "是" : "否"}`);
  items.push(`持续恶化：${assessment.is_continuous_degradation ? "是" : "否"}`);
  return dedupe(items);
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
    evidence_summary: normalizeEvidenceSummary(raw.evidence_summary),
    event_assessment: normalizeEventAssessment(raw.event_assessment),
    memory_used: normalizeMemoryUsed(raw.memory_used),
    inputs: asRecord(raw.inputs),
  };
}

function normalizeEvidenceSummary(value: unknown): AgentEvidenceSummary {
  const raw = asRecord(value);
  return {
    capacity: stringList(raw.capacity),
    events: stringList(raw.events),
    probe: stringList(raw.probe),
    memory: stringList(raw.memory),
  };
}

function normalizeEventAssessment(value: unknown): AgentEventAssessment | undefined {
  const raw = asRecord(value);
  if (!Object.keys(raw).length) return undefined;
  return {
    has_recent_ban_burst: booleanOr(raw.has_recent_ban_burst, false),
    ban_burst_window: raw.ban_burst_window === null ? null : textOr(raw.ban_burst_window, ""),
    is_continuous_degradation: booleanOr(raw.is_continuous_degradation, false),
    interpretation: textOr(raw.interpretation, ""),
  };
}

function normalizeMemoryUsed(value: unknown): AgentMemoryReference[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      const raw = asRecord(item);
      return {
        memory_id: textOr(raw.memory_id, ""),
        reason: textOr(raw.reason, ""),
      };
    })
    .filter((item) => item.memory_id || item.reason);
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
    min_active: numberOr(rawPool.min_active, 0),
    target_active: numberOr(rawPool.target_active, 0),
    max_avg_5h_used: numberOr(rawPool.max_avg_5h_used, 0),
    max_avg_7d_used: numberOr(rawPool.max_avg_7d_used, 0),
    min_reserve: numberOr(rawPool.min_reserve, 0),
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
