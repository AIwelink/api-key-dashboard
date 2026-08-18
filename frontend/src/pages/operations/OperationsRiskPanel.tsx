import { useEffect, useMemo, useRef, useState } from "react";
import {
  Ban,
  BadgeCheck,
  ChevronLeft,
  ChevronRight,
  Eye,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldAlert,
  Unlock,
  X,
} from "lucide-react";

import { api } from "../../api/client";
import {
  MetricDefinition,
  type MetricDefinitionDetails,
} from "../../components/dataWorkspace/MetricDefinition";
import { GrowthCreateModal } from "../../components/GrowthCreateModal";
import { usePageAutoRefresh } from "../../hooks/usePageAutoRefresh";
import { errorMessage } from "../../utils/format";
import "./OperationsRiskPanel.css";

type Toast = (message: string, isError?: boolean) => void;
type RiskView = "accounts" | "ip-clusters" | "events";
type RiskActionKind = "ban" | "release" | "false-positive" | "override-remove";

type RiskSettings = {
  detector_enabled?: boolean;
  auto_ban_enabled?: boolean;
  poll_interval_seconds?: number;
  ip_window_days?: number;
  shared_ip_min_accounts?: number;
};

type SourceHealth = {
  source_stream: string;
  status: "current" | "delayed" | "stale" | "empty";
  latest_observed_at?: string | null;
  last_success_at?: string | null;
  last_error_code?: string;
  last_error_message?: string;
};

type RiskOverview = {
  banned_count?: number;
  high_risk_count?: number;
  shared_ip_cluster_count?: number;
  failed_action_count?: number;
  settings: RiskSettings;
  source_health: SourceHealth[];
};

type RiskReasons = {
  email_rules?: string[];
  shared_ips?: Array<Record<string, unknown>>;
  protection_reasons?: string[];
};

type RiskAccount = {
  risk_account_id: string;
  external_user_id: string;
  email: string;
  risk_status: string;
  risk_reasons?: RiskReasons;
  shared_ip_count?: number;
  max_linked_account_count?: number;
  manual_override_active?: boolean;
  manual_override_reason?: string;
  is_stats_excluded?: boolean;
  first_detected_at?: string | null;
  last_detected_at?: string | null;
  banned_at?: string | null;
  released_at?: string | null;
};

type RiskAccountDetail = RiskAccount & {
  ip_evidence?: RiskIpEvidence[];
  actions?: RiskAction[];
  events?: RiskEvent[];
};

type RiskIpEvidence = {
  ip_address: string;
  source_type?: string;
  linked_account_count?: number;
  linked_external_user_ids?: string[];
  first_seen_at?: string | null;
  last_seen_at?: string | null;
};

type RiskIpCluster = {
  ip_address: string;
  account_count: number;
  external_user_ids?: string[];
  sources?: string[];
  first_seen_at?: string | null;
  last_seen_at?: string | null;
};

type RiskAction = {
  risk_action_id: string;
  action_type: string;
  action_status: string;
  decision_reason?: string;
  requested_by?: string;
  requested_at?: string | null;
  completed_at?: string | null;
  source_user_status_before?: string | null;
  source_user_updated_at_before?: string | null;
  source_api_key_count_before?: number;
  result_summary?: {
    user_status?: string | null;
    user_updated_at?: string | null;
    api_key_count?: number;
    user_restored?: boolean | null;
    restored_key_count?: number;
    conflicted_key_count?: number;
    partial?: boolean | null;
    protected_reason?: string | null;
  };
  error_code?: string;
  error_message?: string;
};

type RiskEvent = {
  risk_event_id: string;
  risk_account_id?: string;
  external_user_id?: string;
  email?: string;
  event_type: string;
  decision_reason?: string;
  actor_name?: string;
  created_at?: string | null;
  error_message?: string;
};

type AccountResponse = { items: RiskAccount[]; total: number; limit: number; offset: number };
type ListResponse<T> = { items: T[]; total: number; limit: number; offset: number };

type OperationsRiskPanelProps = {
  active: boolean;
  token: string;
  role: string;
  showToast: Toast;
};

const EMPTY_OVERVIEW: RiskOverview = {
  banned_count: 0,
  high_risk_count: 0,
  shared_ip_cluster_count: 0,
  failed_action_count: 0,
  settings: {},
  source_health: [],
};

const PAGE_SIZE = 25;

const metricDefinitions: Record<string, MetricDefinitionDetails> = {
  "已封禁账号": {
    definition: "已由运营人员确认封禁，且已从普通运营统计中排除的账号。",
    formula: "COUNT(risk_status = banned)",
    included: "源库用户已禁用且风控动作成功的账号",
    excluded: "仅标记高危、动作失败、已解除或误报账号",
    source: "growth.risk_accounts + risk_actions",
    freshness: "检测器每 60 秒执行；页面每 60 秒刷新",
  },
  "待人工审核": {
    definition: "存在异常邮箱或共享 IP 风险，但尚未执行封禁的账号。",
    formula: "COUNT(risk_status IN [high_risk, ban_pending])",
    included: "单信号账号、历史付费保护账号和双信号待审批账号",
    excluded: "已确认封禁、已解除、已标记误报账号",
    source: "risk_accounts + verified payment facts",
    freshness: "检测器每 60 秒执行；页面每 60 秒刷新",
  },
  "共享 IP 集群": {
    definition: "7 天内同一 IP 至少关联 3 个账号，账号关系来自操作日志或调用日志。",
    formula: "COUNT(IP WHERE DISTINCT account_id >= 3 WITHIN 7d)",
    included: "操作日志与调用日志去重后的账号-IP 关系",
    excluded: "不足三个账号、超过七天窗口或无效 IP",
    source: "audit_logs.client_ip + usage_logs.ip_address",
    freshness: "检测器每 60 秒增量读取，证据保留 30 天",
  },
  "异常动作": {
    definition: "人工处置中失败、并发冲突，需要运营人员确认的动作。",
    formula: "COUNT(action_status IN [failed, conflicted])",
    included: "源库写入失败、状态变化冲突和恢复冲突",
    excluded: "待执行或已成功动作",
    source: "growth.risk_actions",
    freshness: "动作完成后即时更新",
  },
  "人工审批规则": {
    definition: "风险检测只标记待审批账号，确认封禁后才修改用户和 API Key 状态。",
    formula: "风险信号 -> high_risk；人工确认 -> banned",
    included: "异常邮箱、共享 IP、历史付款保护和人工复核证据",
    excluded: "人工误报例外和已解除账号",
    source: "用户、订单、操作日志、调用日志与 Growth 销售事实",
    freshness: "检测器启用后每 60 秒评估一次",
  },
};

const statusLabels: Record<string, string> = {
  high_risk: "高风险待审批",
  ban_pending: "高风险待审批",
  banned: "已封禁",
  ban_failed: "封禁失败",
  released: "已解除",
  cleared: "已排除误报",
};

const eventLabels: Record<string, string> = {
  high_risk_detected: "识别为高危",
  auto_ban_succeeded: "自动封禁成功",
  auto_ban_failed: "自动封禁失败",
  auto_ban_conflicted: "自动封禁冲突",
  manual_ban_succeeded: "人工封禁成功",
  manual_release_succeeded: "人工解除成功",
  manual_release_partial: "人工部分解除",
  manual_override_set: "标记为误报",
  manual_override_removed: "撤销误报例外",
  risk_cleared: "风险已清除",
};

const riskActionLabels: Record<string, string> = {
  auto_ban: "历史自动封禁",
  manual_ban: "人工封禁",
  manual_release: "人工解除",
};

const riskActionStatusLabels: Record<string, string> = {
  pending: "等待恢复",
  running: "执行中",
  succeeded: "已完成",
  failed: "失败",
  conflicted: "状态冲突",
  cancelled: "自动封禁已取消",
};

const actionLabels: Record<RiskActionKind, { title: string; submit: string; endpoint: string }> = {
  ban: { title: "确认封禁", submit: "确认封禁", endpoint: "ban" },
  release: { title: "确认解除封禁", submit: "解除封禁", endpoint: "release" },
  "false-positive": { title: "确认标记误报", submit: "标记误报", endpoint: "false-positive" },
  "override-remove": { title: "确认撤销误报例外", submit: "撤销例外", endpoint: "override/remove" },
};

export function OperationsRiskPanel({
  active,
  token,
  role,
  showToast,
}: OperationsRiskPanelProps) {
  const [overview, setOverview] = useState<RiskOverview>(EMPTY_OVERVIEW);
  const [accounts, setAccounts] = useState<RiskAccount[]>([]);
  const [accountTotal, setAccountTotal] = useState(0);
  const [accountOffset, setAccountOffset] = useState(0);
  const [clusters, setClusters] = useState<RiskIpCluster[]>([]);
  const [clusterTotal, setClusterTotal] = useState(0);
  const [clusterOffset, setClusterOffset] = useState(0);
  const [clusterSearchDraft, setClusterSearchDraft] = useState("");
  const [clusterSearch, setClusterSearch] = useState("");
  const [events, setEvents] = useState<RiskEvent[]>([]);
  const [eventTotal, setEventTotal] = useState(0);
  const [eventOffset, setEventOffset] = useState(0);
  const [eventType, setEventType] = useState("");
  const [eventStartDate, setEventStartDate] = useState("");
  const [eventEndDate, setEventEndDate] = useState("");
  const [view, setView] = useState<RiskView>("accounts");
  const [status, setStatus] = useState("");
  const [rule, setRule] = useState("");
  const [searchDraft, setSearchDraft] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [detail, setDetail] = useState<RiskAccountDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [action, setAction] = useState<{ kind: RiskActionKind; account: RiskAccount } | null>(null);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const requestId = useRef(0);
  const canWrite = role === "owner" || role === "admin" || role === "operator";

  const accountQuery = useMemo(() => {
    const params = new URLSearchParams({
      limit: String(PAGE_SIZE),
      offset: String(accountOffset),
    });
    if (status) params.set("risk_status", status);
    if (rule) params.set("rule", rule);
    if (search) params.set("search", search);
    return params.toString();
  }, [accountOffset, status, rule, search]);
  const clusterQuery = useMemo(
    () => {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(clusterOffset),
      });
      if (clusterSearch) params.set("search", clusterSearch);
      return params.toString();
    },
    [clusterOffset, clusterSearch],
  );
  const eventQuery = useMemo(
    () => {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(eventOffset),
      });
      if (eventType) params.set("event_type", eventType);
      if (eventStartDate) params.set("start_date", eventStartDate);
      if (eventEndDate) params.set("end_date", eventEndDate);
      return params.toString();
    },
    [eventEndDate, eventStartDate, eventOffset, eventType],
  );

  async function loadAll(background = false) {
    if (!active) return;
    const currentRequest = ++requestId.current;
    if (!background) setLoading(true);
    setError("");
    try {
      const [overviewData, accountData, clusterData, eventData] = await Promise.all([
        api<RiskOverview>("/operations/risk/overview", token),
        api<AccountResponse>(`/operations/risk/accounts?${accountQuery}`, token),
        api<ListResponse<RiskIpCluster>>(`/operations/risk/ip-clusters?${clusterQuery}`, token),
        api<ListResponse<RiskEvent>>(`/operations/risk/events?${eventQuery}`, token),
      ]);
      if (currentRequest !== requestId.current) return;
      setOverview(overviewData);
      setAccounts(accountData.items);
      setAccountTotal(accountData.total);
      setClusters(clusterData.items);
      setClusterTotal(clusterData.total);
      setEvents(eventData.items);
      setEventTotal(eventData.total);
    } catch (loadError) {
      if (currentRequest !== requestId.current) return;
      const message = errorMessage(loadError);
      setError(message);
      if (background) showToast(message, true);
    } finally {
      if (!background && currentRequest === requestId.current) setLoading(false);
    }
  }

  useEffect(() => {
    if (active) void loadAll();
    else requestId.current += 1;
  }, [active, accountQuery, clusterQuery, eventQuery, token]);

  usePageAutoRefresh(() => loadAll(true), {
    enabled: active,
    paused: loading || saving || Boolean(action),
  });

  async function loadDetail(account: RiskAccount) {
    setDetail({ ...account, ip_evidence: [], actions: [], events: [] });
    setDetailLoading(true);
    try {
      const result = await api<RiskAccountDetail>(
        `/operations/risk/accounts/${account.risk_account_id}`,
        token,
      );
      setDetail(result);
    } catch (loadError) {
      showToast(errorMessage(loadError), true);
    } finally {
      setDetailLoading(false);
    }
  }

  async function updateSetting(key: "detector_enabled", value: boolean) {
    if (!canWrite || saving) return;
    setSaving(true);
    try {
      const result = await api<RiskSettings>("/operations/risk/settings", token, {
        method: "PATCH",
        body: JSON.stringify({ [key]: value }),
      });
      setOverview((current) => ({ ...current, settings: { ...current.settings, ...result } }));
      showToast(value ? "风控设置已启用" : "风控设置已暂停");
    } catch (updateError) {
      showToast(errorMessage(updateError), true);
    } finally {
      setSaving(false);
    }
  }

  function openAction(kind: RiskActionKind, account: RiskAccount) {
    if (!canWrite) return;
    setReason("");
    setAction({ kind, account });
  }

  async function submitAction() {
    if (!action || !reason.trim() || saving) return;
    setSaving(true);
    try {
      const definition = actionLabels[action.kind];
      await api(
        `/operations/risk/accounts/${action.account.risk_account_id}/${definition.endpoint}`,
        token,
        { method: "POST", body: JSON.stringify({ reason: reason.trim() }) },
      );
      showToast(`${definition.submit}已完成`);
      setAction(null);
      setReason("");
      setDetail(null);
      await loadAll(true);
    } catch (submitError) {
      showToast(errorMessage(submitError), true);
    } finally {
      setSaving(false);
    }
  }

  if (!active) return null;

  const usageHealth = overview.source_health.find((item) => item.source_stream === "usage_logs");
  const staleUsage = usageHealth?.status === "stale" || usageHealth?.status === "empty";

  return (
    <div className="risk-workspace" aria-busy={loading}>
      <section className="risk-control-band" aria-label="风控状态与设置">
        <div className="risk-rule-summary">
          <div>
            <ShieldAlert aria-hidden="true" size={19} strokeWidth={1.8} />
            <strong>AIWeLink 实时风控</strong>
            <MetricDefinition
              details={metricDefinitions["人工审批规则"]}
              label="人工审批规则"
              showLabel={false}
            />
          </div>
          <p>所有风险账号先进入人工审批；只有确认封禁后才停用账号并从运营统计中排除。</p>
        </div>
        <div className="risk-setting-list">
          <RiskToggle
            checked={Boolean(overview.settings.detector_enabled)}
            disabled={!canWrite || saving}
            label="检测器"
            note="每 60 秒读取新增日志"
            onChange={(value) => void updateSetting("detector_enabled", value)}
          />
          <div aria-label="封禁模式：人工审批" className="risk-approval-mode">
            <BadgeCheck aria-hidden="true" size={18} strokeWidth={1.8} />
            <span><strong>人工审批</strong><small>确认后才执行封禁</small></span>
          </div>
        </div>
      </section>

      <section className="risk-source-strip" aria-label="风控数据源状态">
        {(["audit_logs", "usage_logs"] as const).map((stream) => (
          <SourceState
            health={overview.source_health.find((item) => item.source_stream === stream)}
            key={stream}
            stream={stream}
          />
        ))}
      </section>

      {staleUsage ? (
        <div className="risk-source-warning" role="alert">
          <strong>调用日志已过期</strong>
          <span>当前共享 IP 仍会联合操作日志判断，但调用侧证据可能不完整，请先检查源数据同步。</span>
        </div>
      ) : null}

      <section className="risk-metric-band" aria-label="风控概览">
        <RiskMetric label="已封禁账号" value={overview.banned_count} />
        <RiskMetric label="待人工审核" value={overview.high_risk_count} />
        <RiskMetric label="共享 IP 集群" value={overview.shared_ip_cluster_count} />
        <RiskMetric label="异常动作" value={overview.failed_action_count} />
      </section>

      <div className="risk-view-toolbar">
        <div className="risk-view-tabs" role="tablist" aria-label="风控数据视图">
          <button className={view === "accounts" ? "active" : ""} onClick={() => setView("accounts")} role="tab" aria-selected={view === "accounts"} type="button">风险账号</button>
          <button className={view === "ip-clusters" ? "active" : ""} onClick={() => setView("ip-clusters")} role="tab" aria-selected={view === "ip-clusters"} type="button">共享 IP</button>
          <button className={view === "events" ? "active" : ""} onClick={() => setView("events")} role="tab" aria-selected={view === "events"} type="button">处置记录</button>
        </div>
        <button aria-label="刷新风控数据" className="ghost icon-button" disabled={loading} onClick={() => void loadAll()} title="刷新风控数据" type="button">
          <RefreshCw aria-hidden="true" size={16} />
        </button>
      </div>

      {error ? <div className="risk-inline-error" role="alert"><strong>风控数据加载失败</strong><span>{error}</span></div> : null}

      {view === "accounts" ? (
        <AccountsView
          accounts={accounts}
          canWrite={canWrite}
          loading={loading}
          onAction={openAction}
          onDetail={(account) => void loadDetail(account)}
          onRule={(value) => {
            setAccountOffset(0);
            setRule(value);
          }}
          onSearch={() => {
            setAccountOffset(0);
            setSearch(searchDraft.trim());
          }}
          onSearchDraft={setSearchDraft}
          onStatus={(value) => {
            setAccountOffset(0);
            setStatus(value);
          }}
          onPage={setAccountOffset}
          rule={rule}
          searchDraft={searchDraft}
          status={status}
          total={accountTotal}
          offset={accountOffset}
        />
      ) : null}
      {view === "ip-clusters" ? <IpClustersView clusters={clusters} loading={loading} offset={clusterOffset} onPage={setClusterOffset} onSearch={(value) => { setClusterOffset(0); setClusterSearch((value ?? clusterSearchDraft).trim()); }} onSearchDraft={setClusterSearchDraft} searchDraft={clusterSearchDraft} total={clusterTotal} /> : null}
      {view === "events" ? <EventsView endDate={eventEndDate} eventType={eventType} events={events} loading={loading} offset={eventOffset} onEndDate={(value) => { setEventOffset(0); setEventEndDate(value); }} onEventType={(value) => { setEventOffset(0); setEventType(value); }} onPage={setEventOffset} onStartDate={(value) => { setEventOffset(0); setEventStartDate(value); }} startDate={eventStartDate} total={eventTotal} /> : null}

      {!canWrite ? <div className="risk-readonly-note">当前角色为只读权限。风控操作只能由 owner/admin/operator 执行。</div> : null}

      {detail ? (
        <RiskDetailDrawer
          detail={detail}
          loading={detailLoading}
          onAction={openAction}
          onClose={() => setDetail(null)}
          writable={canWrite}
        />
      ) : null}

      {action ? (
        <GrowthCreateModal
          onClose={() => setAction(null)}
          onSubmit={() => void submitAction()}
          saving={saving}
          submitDisabled={!reason.trim()}
          submitLabel={actionLabels[action.kind].submit}
          title={actionLabels[action.kind].title}
        >
          <div className="risk-action-context">
            <div><span>账号</span><strong>{action.account.email}</strong></div>
            <div><span>业务用户 ID</span><strong>{action.account.external_user_id}</strong></div>
          </div>
          <label className="risk-action-reason">
            <span className="field-label"><strong>处置说明</strong><span>（必填）</span></span>
            <textarea autoFocus maxLength={500} onChange={(event) => setReason(event.target.value)} value={reason} />
          </label>
        </GrowthCreateModal>
      ) : null}
    </div>
  );
}

function RiskToggle({ checked, disabled, label, note, onChange }: { checked: boolean; disabled: boolean; label: string; note: string; onChange: (value: boolean) => void }) {
  return (
    <label className="risk-toggle">
      <span><strong>{label}</strong><small>{note}</small></span>
      <input checked={checked} disabled={disabled} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
      <i aria-hidden="true" />
    </label>
  );
}

function SourceState({ health, stream }: { health?: SourceHealth; stream: "audit_logs" | "usage_logs" }) {
  const label = stream === "audit_logs" ? "操作日志" : "调用日志";
  const status = health?.status || "empty";
  const statusLabel = status === "current" ? "正常" : status === "delayed" ? "延迟" : status === "stale" ? "已过期" : "无数据";
  return (
    <div className={`risk-source-state ${status}`}>
      <span>{label}</span>
      <strong>{statusLabel}</strong>
      <small>{formatDateTime(health?.latest_observed_at)}</small>
      {health?.last_error_message ? <small className="risk-source-error">{health.last_error_code ? `${health.last_error_code}: ` : ""}{health.last_error_message}</small> : null}
    </div>
  );
}

function RiskMetric({ label, value }: { label: string; value?: number }) {
  return (
    <div className="risk-metric">
      <div><span>{label}</span><MetricDefinition details={metricDefinitions[label]} label={label} showLabel={false} /></div>
      <strong>{Number(value || 0).toLocaleString("zh-CN")}</strong>
    </div>
  );
}

function AccountsView({ accounts, canWrite, loading, offset, onAction, onDetail, onPage, onRule, onSearch, onSearchDraft, onStatus, rule, searchDraft, status, total }: { accounts: RiskAccount[]; canWrite: boolean; loading: boolean; offset: number; onAction: (kind: RiskActionKind, account: RiskAccount) => void; onDetail: (account: RiskAccount) => void; onPage: (offset: number) => void; onRule: (value: string) => void; onSearch: () => void; onSearchDraft: (value: string) => void; onStatus: (value: string) => void; rule: string; searchDraft: string; status: string; total: number }) {
  return (
    <section className="risk-data-section">
      <div className="risk-query-bar">
        <label><span>状态</span><select onChange={(event) => onStatus(event.target.value)} value={status}><option value="">全部状态</option><option value="high_risk">高风险待审批</option><option value="ban_pending">高风险待审批</option><option value="banned">已封禁</option><option value="ban_failed">封禁失败</option><option value="released">已解除</option><option value="cleared">已排除误报</option></select></label>
        <label><span>邮箱规则</span><select onChange={(event) => onRule(event.target.value)} value={rule}><option value="">全部规则</option><option value="email_local_part_dot">邮箱点号</option><option value="email_plus_tag">+ 标签</option></select></label>
        <form className="risk-search" onSubmit={(event) => { event.preventDefault(); onSearch(); }}><label><span>账号搜索</span><input onChange={(event) => onSearchDraft(event.target.value)} placeholder="邮箱或业务用户 ID" value={searchDraft} /></label><button aria-label="查询风险账号" title="查询" type="submit"><Search aria-hidden="true" size={15} /></button></form>
        <span className="risk-result-count">{total.toLocaleString("zh-CN")} 个账号</span>
      </div>
      <div className="risk-table-scroll">
        <table>
          <thead><tr><th>状态</th><th>账号</th><th>命中规则</th><th>共享 IP</th><th>保护/例外</th><th>最近识别</th><th aria-label="操作" /></tr></thead>
          <tbody>
            {accounts.length ? accounts.map((account) => (
              <tr key={account.risk_account_id}>
                <td><RiskStatus status={account.risk_status} /></td>
                <td><strong>{account.email || "-"}</strong><small>{account.external_user_id}</small></td>
                <td><RuleList reasons={account.risk_reasons} /></td>
                <td><strong>{Number(account.max_linked_account_count || 0)}</strong><small>{Number(account.shared_ip_count || 0)} 个集群</small></td>
                <td><ProtectionLabel account={account} /></td>
                <td>{formatDateTime(account.last_detected_at)}</td>
                <td><AccountActions account={account} canWrite={canWrite} onAction={onAction} onDetail={onDetail} /></td>
              </tr>
            )) : <EmptyTable colSpan={7} loading={loading} text="当前筛选下暂无风险账号" />}
          </tbody>
        </table>
      </div>
      <RiskPagination label="风险账号" loading={loading} offset={offset} onPage={onPage} total={total} />
    </section>
  );
}

function IpClustersView({ clusters, loading, offset, onPage, onSearch, onSearchDraft, searchDraft, total }: { clusters: RiskIpCluster[]; loading: boolean; offset: number; onPage: (offset: number) => void; onSearch: (value?: string) => void; onSearchDraft: (value: string) => void; searchDraft: string; total: number }) {
  return (
    <section className="risk-data-section">
      <div className="risk-query-bar">
        <form className="risk-search" onSubmit={(event) => { event.preventDefault(); onSearch((event.currentTarget.elements.namedItem("ip-search") as HTMLInputElement | null)?.value); }}>
          <label><span>搜索共享 IP</span><input aria-label="搜索共享 IP" name="ip-search" onChange={(event) => onSearchDraft(event.target.value)} placeholder="IPv4 或 IPv6 地址" value={searchDraft} /></label>
          <button aria-label="查询共享 IP" title="查询共享 IP" type="submit"><Search aria-hidden="true" size={15} /></button>
        </form>
        <span className="risk-result-count">{total.toLocaleString("zh-CN")} 个集群</span>
      </div>
      <div className="risk-section-head"><div><h3>共享 IP 集群</h3><span>操作日志与调用日志联合查询，滚动窗口 7 天</span></div><strong>{total} 个集群</strong></div>
      <div className="risk-table-scroll"><table><thead><tr><th>IP 地址</th><th>关联账号</th><th>证据来源</th><th>首次发现</th><th>最近发现</th></tr></thead><tbody>{clusters.length ? clusters.map((cluster) => <tr key={cluster.ip_address}><td><strong className="risk-mono">{cluster.ip_address}</strong></td><td><strong>{cluster.account_count}</strong><small>{(cluster.external_user_ids || []).slice(0, 4).join(" · ") || "-"}</small></td><td>{(cluster.sources || []).map(sourceLabel).join(" + ") || "-"}</td><td>{formatDateTime(cluster.first_seen_at)}</td><td>{formatDateTime(cluster.last_seen_at)}</td></tr>) : <EmptyTable colSpan={5} loading={loading} text="当前没有达到阈值的共享 IP 集群" />}</tbody></table></div>
      <RiskPagination label="共享 IP" loading={loading} offset={offset} onPage={onPage} total={total} />
    </section>
  );
}

function EventsView({ endDate, eventType, events, loading, offset, onEndDate, onEventType, onPage, onStartDate, startDate, total }: { endDate: string; eventType: string; events: RiskEvent[]; loading: boolean; offset: number; onEndDate: (value: string) => void; onEventType: (value: string) => void; onPage: (offset: number) => void; onStartDate: (value: string) => void; startDate: string; total: number }) {
  return (
    <section className="risk-data-section">
      <div className="risk-query-bar">
        <label><span>事件类型</span><select aria-label="事件类型" onChange={(event) => onEventType(event.target.value)} value={eventType}><option value="">全部事件</option>{Object.entries(eventLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <DateFilterInput ariaLabel="开始日期" label="开始日期" onChange={onStartDate} value={startDate} />
        <DateFilterInput ariaLabel="结束日期" label="结束日期" onChange={onEndDate} value={endDate} />
        <span className="risk-result-count">{total.toLocaleString("zh-CN")} 条记录</span>
      </div>
      <div className="risk-section-head"><div><h3>处置记录</h3><span>检测、封禁、解除和人工例外均保留不可变事件记录</span></div><strong>{total} 条记录</strong></div>
      <div className="risk-table-scroll"><table><thead><tr><th>时间</th><th>事件</th><th>账号</th><th>归因</th><th>操作人</th></tr></thead><tbody>{events.length ? events.map((event) => <tr key={event.risk_event_id}><td>{formatDateTime(event.created_at)}</td><td><strong>{eventLabels[event.event_type] || event.event_type}</strong>{event.error_message ? <small className="risk-error-text">{event.error_message}</small> : null}</td><td>{event.email || event.external_user_id || "-"}</td><td>{reasonLabel(event.decision_reason)}</td><td>{event.actor_name || "系统"}</td></tr>) : <EmptyTable colSpan={5} loading={loading} text="暂无处置记录" />}</tbody></table></div>
      <RiskPagination label="处置记录" loading={loading} offset={offset} onPage={onPage} total={total} />
    </section>
  );
}

function DateFilterInput({ ariaLabel, label, onChange, value }: { ariaLabel: string; label: string; onChange: (value: string) => void; value: string }) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const input = inputRef.current;
    if (!input) return undefined;
    const handleNativeChange = () => onChange(input.value);
    input.addEventListener("change", handleNativeChange);
    return () => input.removeEventListener("change", handleNativeChange);
  }, [onChange]);

  return <label><span>{label}</span><input aria-label={ariaLabel} onChange={(event) => onChange(event.target.value)} ref={inputRef} type="date" value={value} /></label>;
}

function RiskDetailDrawer({ detail, loading, onAction, onClose, writable }: { detail: RiskAccountDetail; loading: boolean; onAction: (kind: RiskActionKind, account: RiskAccount) => void; onClose: () => void; writable: boolean }) {
  return (
    <div className="risk-drawer-backdrop" onMouseDown={onClose} role="presentation">
      <aside aria-label={`风险账号详情 ${detail.email}`} aria-modal="true" className="risk-drawer" onMouseDown={(event) => event.stopPropagation()} role="dialog">
        <header><div><RiskStatus status={detail.risk_status} /><h3>{detail.email}</h3><span>{detail.external_user_id}</span></div><button aria-label="关闭风险账号详情" className="ghost icon-button" onClick={onClose} title="关闭" type="button"><X aria-hidden="true" size={18} /></button></header>
        {loading ? <div className="risk-drawer-loading">正在加载详情...</div> : (
          <>
            <section><h4>判断归因</h4><RuleList reasons={detail.risk_reasons} /><ProtectionLabel account={detail} /></section>
            <section><h4>IP 证据</h4>{detail.ip_evidence?.length ? detail.ip_evidence.map((item) => <div className="risk-evidence-row" key={`${item.ip_address}-${item.source_type}`}><strong className="risk-mono">{item.ip_address}</strong><span>{sourceLabel(item.source_type)} · {item.linked_account_count || 0} 个账号</span><small>{formatDateTime(item.last_seen_at)}</small></div>) : <p>暂无 IP 证据</p>}</section>
            <section><h4>动作记录</h4>{detail.actions?.length ? detail.actions.map((item) => <RiskActionRecord action={item} key={item.risk_action_id} />) : <p>暂无动作记录</p>}</section>
            <section><h4>处置时间线</h4>{detail.events?.length ? detail.events.map((event) => <div className="risk-timeline-row" key={event.risk_event_id}><i aria-hidden="true" /><div><strong>{eventLabels[event.event_type] || event.event_type}</strong><span>{reasonLabel(event.decision_reason)}</span><small>{formatDateTime(event.created_at)} · {event.actor_name || "系统"}</small></div></div>) : <p>暂无处置记录</p>}</section>
            {writable ? <div className="risk-drawer-actions"><AccountActionCommands account={detail} onAction={onAction} /></div> : null}
          </>
        )}
      </aside>
    </div>
  );
}

function RiskPagination({ label, loading, offset, onPage, total }: { label: string; loading: boolean; offset: number; onPage: (offset: number) => void; total: number }) {
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.min(pageCount, Math.floor(offset / PAGE_SIZE) + 1);
  return (
    <nav aria-label={`${label}分页`} className="risk-pagination">
      <span aria-live="polite">第 {currentPage} / {pageCount} 页</span>
      <div>
        <button aria-label={`上一页${label}`} className="ghost icon-button" disabled={loading || offset === 0} onClick={() => onPage(Math.max(0, offset - PAGE_SIZE))} title="上一页" type="button"><ChevronLeft aria-hidden="true" size={15} /></button>
        <button aria-label={`下一页${label}`} className="ghost icon-button" disabled={loading || offset + PAGE_SIZE >= total} onClick={() => onPage(offset + PAGE_SIZE)} title="下一页" type="button"><ChevronRight aria-hidden="true" size={15} /></button>
      </div>
    </nav>
  );
}

function RiskActionRecord({ action }: { action: RiskAction }) {
  const result = action.result_summary || {};
  return (
    <div className="risk-action-record">
      <div>
        <strong>{riskActionLabels[action.action_type] || action.action_type}</strong>
        <span className={`risk-action-state ${action.action_status}`}>{riskActionStatusLabels[action.action_status] || action.action_status}</span>
      </div>
      <small>{formatDateTime(action.requested_at)} · {action.requested_by || "系统"}</small>
      <span>封禁前 {action.source_user_status_before || "-"} · {Number(action.source_api_key_count_before || 0)} 个 API Key</span>
      {action.action_status === "cancelled"
        ? <span>未执行封禁</span>
        : <ActionResultSummary actionType={action.action_type} result={result} />}
      {action.error_message ? <p className="risk-action-error"><strong>{action.error_code || "ActionError"}</strong><span>{action.error_message}</span></p> : null}
    </div>
  );
}

function ActionResultSummary({ actionType, result }: { actionType: string; result: NonNullable<RiskAction["result_summary"]> }) {
  if (actionType === "manual_release") {
    return (
      <span>
        {result.partial ? "部分解除" : "解除完成"} · 已恢复 {Number(result.restored_key_count || 0)} · 冲突 {Number(result.conflicted_key_count || 0)}
      </span>
    );
  }
  if (result.protected_reason) return <span>历史付费保护，未执行封禁</span>;
  return <span>结果 {result.user_status || "-"} · {Number(result.api_key_count || 0)} 个 API Key 已停用</span>;
}

function AccountActions({ account, canWrite, onAction, onDetail }: { account: RiskAccount; canWrite: boolean; onAction: (kind: RiskActionKind, account: RiskAccount) => void; onDetail: (account: RiskAccount) => void }) {
  return (
    <div className="risk-row-actions">
      <button aria-label={`查看 ${account.email}`} className="ghost icon-button" onClick={() => onDetail(account)} title="查看详情" type="button"><Eye aria-hidden="true" size={15} /></button>
      {canWrite ? <AccountActionCommands account={account} onAction={onAction} /> : null}
    </div>
  );
}

function AccountActionCommands({ account, onAction }: { account: RiskAccount; onAction: (kind: RiskActionKind, account: RiskAccount) => void }) {
  if (account.risk_status === "banned") {
    return <button aria-label={`解除封禁 ${account.email}`} className="ghost icon-button" onClick={() => onAction("release", account)} title="解除封禁" type="button"><Unlock aria-hidden="true" size={15} /></button>;
  }
  return (
    <>
      <button aria-label={`确认封禁 ${account.email}`} className="ghost icon-button danger-button" onClick={() => onAction("ban", account)} title="确认封禁" type="button"><Ban aria-hidden="true" size={15} /></button>
      {account.manual_override_active ? <button aria-label={`撤销误报例外 ${account.email}`} className="ghost icon-button" onClick={() => onAction("override-remove", account)} title="撤销误报例外" type="button"><RotateCcw aria-hidden="true" size={15} /></button> : <button aria-label={`标记误报 ${account.email}`} className="ghost icon-button" onClick={() => onAction("false-positive", account)} title="标记误报" type="button"><BadgeCheck aria-hidden="true" size={15} /></button>}
    </>
  );
}

function RiskStatus({ status }: { status: string }) {
  return <span className={`risk-status ${status}`}>{statusLabels[status] || status}</span>;
}

function RuleList({ reasons }: { reasons?: RiskReasons }) {
  const labels = (reasons?.email_rules || []).map((rule) => rule === "email_local_part_dot" ? "邮箱点号" : rule === "email_plus_tag" ? "+ 标签" : rule);
  const hasSharedIp = Boolean(reasons?.shared_ips?.length);
  return <span className="risk-rule-list">{[...labels, ...(hasSharedIp ? ["共享 IP"] : [])].join(" + ") || "仅共享 IP"}</span>;
}

function ProtectionLabel({ account }: { account: RiskAccount }) {
  const protection = account.risk_reasons?.protection_reasons || [];
  if (protection.includes("verified_payment_history")) return <span className="risk-protection paid">历史付费，仅人工审核</span>;
  if (protection.includes("source_state_conflict")) return <span className="risk-protection conflict">源状态冲突，需人工审核</span>;
  if (account.manual_override_active) return <span className="risk-protection override">人工误报例外</span>;
  return <span className="risk-protection none">无</span>;
}

function EmptyTable({ colSpan, loading, text }: { colSpan: number; loading: boolean; text: string }) {
  return <tr><td className="risk-empty" colSpan={colSpan}>{loading ? "正在加载风控数据..." : text}</td></tr>;
}

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function sourceLabel(value?: string) {
  if (value === "user_audit" || value === "registration_audit" || value === "audit_logs") return "操作日志";
  if (value === "usage_log" || value === "usage_logs") return "调用日志";
  return value || "未知来源";
}

function reasonLabel(value?: string) {
  if (value === "verified_payment_review") return "历史付费保护";
  if (value === "email_and_shared_ip") return "异常邮箱 + 共享 IP";
  if (value === "email_or_shared_ip") return "单一风险信号";
  if (value === "source_state_conflict") return "源状态冲突";
  return value || "-";
}

export default OperationsRiskPanel;
