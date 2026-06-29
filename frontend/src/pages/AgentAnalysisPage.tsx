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
  suggested_push_from_reserve_count: number;
  suggested_make_new_count: number;
  manual_review_required: boolean;
  suggested_actions: string[];
  inputs?: Record<string, unknown>;
};

type AgentCapabilityStep = {
  index?: number;
  capability: string;
  reason?: string;
  status?: string;
  arguments?: Record<string, unknown>;
  summary?: Record<string, unknown>;
};

type AgentAnalysisResponse = {
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
    capability_plan?: AgentCapabilityStep[];
    capability_trace?: AgentCapabilityStep[];
  };
  created_at: string;
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

  const analyzePool = async () => {
    if (!selectedPoolId) {
      showToast("请先选择账号池", true);
      return;
    }
    setAnalyzing(true);
    try {
      const data = await api<AgentAnalysisResponse>(`/agent/pools/${selectedPoolId}/analyze`, token, { method: "POST" });
      setReport(data);
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
        body: JSON.stringify({ message, pool_id: selectedPoolId || undefined }),
      });
      setReport(data);
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
  }, []);

  return (
    <section className="view agent-analysis-page">
      <div className="topbar">
        <div>
          <h2>Agent分析</h2>
          <p>最小 MVP：只读分析 API 账号池状态和账号探测数据，不写数据库、不操作 sub2api、不发送机器人通知。</p>
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
            <p>第一版建议先选择 Pro 池验证容量与 401 风险判断。</p>
          </div>
          <span className="status-pill accent">LangChain-ready</span>
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
          <Metric label="目标 active" value={selectedPool?.target_active ?? "-"} />
          <Metric label="最小备用" value={selectedPool?.min_reserve ?? "-"} />
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

      {report ? <AgentReport report={report} /> : <div className="empty-state">选择账号池后点击“分析”，这里会显示只读决策结果。</div>}
    </section>
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
              <h3>Level 1 LLM</h3>
              <p>{report.llm.model ? `model: ${report.llm.model} / ${report.llm.framework || "http_fallback"}` : "OpenAI-compatible explanation"}</p>
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

      {report.agent && (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>Agent 编排过程</h3>
              <p>
                {displayText(report.agent.intent) || "analyze_pool"} / {displayText(report.agent.mode) || "read_only"}
              </p>
            </div>
            <span className={`status-pill ${report.agent.planned_by === "level1" ? "accent" : "warning"}`}>
              {report.agent.planned_by === "level1" ? "Level 1 planned" : "fallback"}
            </span>
          </div>
          {report.agent.thought && <div className="agent-thought">{displayText(report.agent.thought)}</div>}
          {report.agent.fallback_reason && <div className="empty-state">{displayText(report.agent.fallback_reason)}</div>}
          <CapabilityTimeline title="计划调用能力" steps={report.agent.capability_plan || []} />
          <CapabilityTimeline title="实际调用记录" steps={report.agent.capability_trace || []} showSummary />
        </section>
      )}

      <section className="grid two">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>建议动作</h3>
              <p>当前版本只给建议，不执行动作。</p>
            </div>
          </div>
          <div className="compact-stats agent-action-stats">
            <Metric label="备用池推送" value={decision.suggested_push_from_reserve_count} />
            <Metric label="制作新号" value={decision.suggested_make_new_count} />
            <Metric label="人工复核" value={decision.manual_review_required ? "需要" : "不需要"} />
          </div>
          <div className="list">
            {report.suggested_actions.map((action) => (
              <div className="list-item" key={action}>
                {action}
              </div>
            ))}
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>原因</h3>
              <p>由容量缓存和账号探测数据共同生成。</p>
            </div>
          </div>
          <div className="list">
            {report.reasons.map((reason) => (
              <div className="list-item" key={reason}>
                {reason}
              </div>
            ))}
          </div>
        </section>
      </section>

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

function CapabilityTimeline({ title, steps, showSummary = false }: { title: string; steps: AgentCapabilityStep[]; showSummary?: boolean }) {
  if (!steps.length) return null;
  return (
    <div className="agent-capability-block">
      <h4>{title}</h4>
      <div className="agent-capability-list">
        {steps.map((step, index) => (
          <div className="agent-capability-item" key={`${step.capability}-${step.index ?? index}`}>
            <div className="agent-capability-head">
              <span>{step.index ?? index + 1}</span>
              <strong>{step.capability}</strong>
              {step.status && <em>{step.status}</em>}
            </div>
            {step.reason && <p>{step.reason}</p>}
            {showSummary && step.summary && <code>{compactJson(step.summary)}</code>}
          </div>
        ))}
      </div>
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

function displayText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function compactJson(value: unknown): string {
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
