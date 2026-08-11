import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { GrowthCreateModal } from "../components/GrowthCreateModal";
import type { OperationsSiteId } from "../types";
import { errorMessage } from "../utils/format";
import "./OperationsManagementPage.css";

type OperationsTab = "overview" | "internal-users" | "credits" | "classification";
type OperationsRange = "today" | "7d" | "30d" | "custom";
type UserSegment = "all" | "ordinary" | "internal";
type Purpose = "sale" | "promotion" | "internal" | "compensation" | "other";
type Toast = (message: string, isError?: boolean) => void;

export const DEFAULT_OPERATIONS_SEGMENT: UserSegment = "ordinary";

type OperationsManagementProps = {
  token: string;
  role: string;
  allowedSiteIds: OperationsSiteId[];
  showToast: Toast;
  initialTab?: OperationsTab;
};

type OperationsQueryState = {
  siteId: string;
  segment: UserSegment;
  range: OperationsRange;
  startAt?: string;
  endAt?: string;
};

type OperationsSummary = {
  registered_user_count?: number;
  active_user_count?: number;
  successful_call_count?: number;
  consumed_balance_units?: number;
  cost_cny?: number;
  payer_count?: number;
  sale_event_count?: number;
  gross_income_cny?: number;
  refund_cny?: number;
  net_income_cny?: number;
};

type OverviewResponse = {
  summary: OperationsSummary;
  previous_summary: OperationsSummary;
  site_breakdown: SiteBreakdownItem[];
  generated_at: string;
};

type SiteBreakdownItem = OperationsSummary & {
  site_id: string;
};

type TrendItem = OperationsSummary & {
  bucket: string;
  site_id: string;
  user_segment: UserSegment;
};

type SyncStatus = {
  site_id: string;
  status?: string;
  health?: "healthy" | "running" | "delayed" | "never";
  last_success_at?: string;
  error_message?: string;
};

type InternalUser = {
  internal_user_id: string;
  site_id: string;
  email: string;
  external_user_id: string | null;
  recognition_status: "pending" | "recognized";
  recognized_at?: string | null;
  reason?: string;
  active_from: string;
  active_until?: string | null;
};

type ConversionRate = {
  conversion_rate_id: string;
  site_id: string;
  balance_units_per_cny: number;
  effective_from: string;
  effective_until?: string | null;
  note?: string;
};

type ClassificationTask = {
  classification_task_id: string;
  site_id: string;
  external_user_id: string;
  account_label?: string;
  source_type: string;
  source_record_id?: string;
  balance_units: number;
  occurred_at: string;
  status: string;
};

type ListResponse<T> = { items: T[]; total: number; generated_at?: string };

type RefreshResultItem = {
  site_id: string;
  status: string;
  error?: string;
};

export const conversionRateEffectiveHint = "首次配置留空将覆盖全部历史数据；以后调整留空将从当前时间生效。";

export type RedemptionForm = {
  site_id: string;
  purpose: Purpose;
  code_count: string;
  balance_units_per_code: string;
  cash_amount_cny: string;
  note: string;
};

type InternalUserForm = {
  site_id: string;
  email: string;
  reason: string;
  active_from: string;
  active_until: string;
};

type AdjustmentForm = {
  site_id: string;
  external_user_id: string;
  purpose: Purpose;
  balance_units: string;
  cash_amount_cny: string;
  note: string;
};

type ConversionForm = {
  site_id: string;
  balance_units_per_cny: string;
  effective_from: string;
  note: string;
};

type ClassificationForm = {
  status: "resolved" | "ignored";
  purpose: Purpose;
  cash_amount_cny: string;
  note: string;
};

type ModalState =
  | { kind: "internal"; item?: InternalUser }
  | { kind: "redemption" }
  | { kind: "adjustment" }
  | { kind: "rate" }
  | { kind: "classification"; item: ClassificationTask }
  | null;

const siteOptions: Array<{ value: OperationsSiteId; label: string }> = [
  { value: "aiwelink", label: "AIWeLink" },
  { value: "aigclink", label: "AIGCLink" },
];

const operationsSitePriority: Record<string, number> = {
  aiwelink: 0,
  aigclink: 1,
};

export function orderOperationsSites<T extends { value: string }>(sites: T[]) {
  return [...sites].sort((left, right) => (
    (operationsSitePriority[left.value] ?? Number.MAX_SAFE_INTEGER)
    - (operationsSitePriority[right.value] ?? Number.MAX_SAFE_INTEGER)
  ));
}

export function preferredOperationsSiteId<T extends { value: string }>(sites: T[]) {
  return sites.find((site) => site.value === "aiwelink")?.value || sites[0]?.value || "";
}

const purposeLabels: Record<Purpose, string> = {
  sale: "销售",
  promotion: "推广",
  internal: "内部使用",
  compensation: "补偿",
  other: "其他",
};

const rangeLabels: Record<OperationsRange, string> = {
  today: "今天",
  "7d": "最近 7 天",
  "30d": "最近 30 天",
  custom: "自定义",
};

const segmentLabels: Record<UserSegment, string> = {
  all: "全部用户",
  ordinary: "普通用户",
  internal: "内部人员",
};

const emptyInternalUserForm: InternalUserForm = {
  site_id: "aiwelink",
  email: "",
  reason: "",
  active_from: "",
  active_until: "",
};

export const emptyRedemptionForm: RedemptionForm = {
  site_id: "aiwelink",
  purpose: "promotion",
  code_count: "1",
  balance_units_per_code: "",
  cash_amount_cny: "0",
  note: "",
};

const emptyAdjustmentForm: AdjustmentForm = {
  site_id: "aiwelink",
  external_user_id: "",
  purpose: "compensation",
  balance_units: "",
  cash_amount_cny: "0",
  note: "",
};

const emptyConversionForm: ConversionForm = {
  site_id: "aiwelink",
  balance_units_per_cny: "10",
  effective_from: "",
  note: "",
};

const emptyClassificationForm: ClassificationForm = {
  status: "resolved",
  purpose: "other",
  cash_amount_cny: "0",
  note: "",
};

export function canManageOperations(role: string) {
  return role === "owner" || role === "admin";
}

export function buildOperationsQuery(query: OperationsQueryState) {
  const params = new URLSearchParams();
  if (query.siteId.trim()) params.set("site_id", query.siteId.trim());
  params.set("segment", query.segment);
  params.set("range", query.range);
  if (query.range === "custom") {
    if (query.startAt) params.set("start_at", new Date(query.startAt).toISOString());
    if (query.endAt) params.set("end_at", new Date(query.endAt).toISOString());
  }
  return `?${params.toString()}`;
}

export function buildRedemptionPayload(form: RedemptionForm, idempotencyKey: string) {
  return {
    site_id: form.site_id.trim(),
    purpose: form.purpose,
    code_count: Number(form.code_count),
    balance_units_per_code: Number(form.balance_units_per_code),
    cash_amount_cny: form.purpose === "sale" ? Number(form.cash_amount_cny) : 0,
    note: form.note.trim(),
    idempotency_key: idempotencyKey,
  };
}

function idempotencyKey(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function optionalIso(value: string) {
  return value ? new Date(value).toISOString() : undefined;
}

function formatNumber(value: number | undefined, maximumFractionDigits = 2) {
  return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits });
}

function formatCurrency(value: number | undefined) {
  return `¥${formatNumber(value, 2)}`;
}

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatBucket(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: value.includes("T") ? "2-digit" : undefined,
    minute: value.includes("T") ? "2-digit" : undefined,
    hour12: false,
  });
}

function siteLabel(siteId: string) {
  return siteOptions.find((site) => site.value === siteId)?.label || siteId;
}

export function refreshFailureMessage(items: RefreshResultItem[]) {
  return items
    .filter((item) => item.status === "failed")
    .map((item) => `${siteLabel(item.site_id)}：${item.error || "同步失败"}`)
    .join("；");
}

function comparison(current: number | undefined, previous: number | undefined) {
  const currentValue = Number(current || 0);
  const previousValue = Number(previous || 0);
  if (previousValue === 0) return currentValue === 0 ? "与上期持平" : "上期为 0";
  const percent = ((currentValue - previousValue) / Math.abs(previousValue)) * 100;
  return `较上期 ${percent >= 0 ? "+" : ""}${percent.toFixed(1)}%`;
}

export function averageConsumption(
  item: Pick<OperationsSummary, "consumed_balance_units" | "active_user_count">,
) {
  const activeUsers = Number(item.active_user_count || 0);
  return activeUsers > 0 ? Number(item.consumed_balance_units || 0) / activeUsers : 0;
}

export function paymentRate(
  item: Pick<OperationsSummary, "payer_count" | "active_user_count">,
) {
  const activeUsers = Number(item.active_user_count || 0);
  return activeUsers > 0 ? (Number(item.payer_count || 0) / activeUsers) * 100 : 0;
}

export function recognitionStatusLabel(status: InternalUser["recognition_status"]) {
  return status === "recognized" ? "识别成功" : "待识别";
}

function SiteSelect({ value, onChange, sites, includeAll = true }: { value: string; onChange: (value: string) => void; sites: Array<{ value: OperationsSiteId; label: string }>; includeAll?: boolean }) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      {sites.map((site) => <option value={site.value} key={site.value}>{site.label}</option>)}
      {includeAll && <option value="">全部站点</option>}
    </select>
  );
}

function EmptyRow({ columns, text = "暂无数据" }: { columns: number; text?: string }) {
  return <tr><td className="operations-empty-cell" colSpan={columns}>{text}</td></tr>;
}

function Metric({ label, value, previous }: { label: string; value: string; previous?: string }) {
  return (
    <div className="operations-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{previous || "当前查询周期"}</small>
    </div>
  );
}

function PurposeFields({ purpose, cash, onPurpose, onCash }: { purpose: Purpose; cash: string; onPurpose: (value: Purpose) => void; onCash: (value: string) => void }) {
  return (
    <>
      <label><span className="field-label"><strong>用途</strong></span><select value={purpose} onChange={(event) => onPurpose(event.target.value as Purpose)}>{Object.entries(purposeLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <label><span className="field-label"><strong>实际收款 CNY</strong></span><input type="number" min="0" step="0.01" value={purpose === "sale" ? cash : "0"} disabled={purpose !== "sale"} onChange={(event) => onCash(event.target.value)} /></label>
    </>
  );
}

export function OperationsManagementPage(
  {
    token,
    role,
    allowedSiteIds,
    showToast,
    initialTab = "overview",
  }: OperationsManagementProps,
) {
  const allowedSiteKey = allowedSiteIds.join("|");
  const allowedSites = useMemo(
    () => orderOperationsSites(siteOptions.filter((site) => allowedSiteIds.includes(site.value))),
    [allowedSiteKey],
  );
  const firstAllowedSiteId = preferredOperationsSiteId(allowedSites);
  const defaultSiteFilter = firstAllowedSiteId;
  const hasSiteAccess = allowedSites.length > 0;
  const showAllSites = allowedSites.length > 1;
  const allowedSiteSet = new Set(allowedSites.map((site) => site.value));
  const normalizeSiteFilter = (siteId: string) => !siteId
    ? ""
    : allowedSiteSet.has(siteId as OperationsSiteId) ? siteId : defaultSiteFilter;
  const [tab, setTab] = useState<OperationsTab>(initialTab);
  const [query, setQuery] = useState<OperationsQueryState>({ siteId: defaultSiteFilter, segment: DEFAULT_OPERATIONS_SEGMENT, range: "7d" });
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [trends, setTrends] = useState<TrendItem[]>([]);
  const [syncStatuses, setSyncStatuses] = useState<SyncStatus[]>([]);
  const [internalUsers, setInternalUsers] = useState<InternalUser[]>([]);
  const [rates, setRates] = useState<ConversionRate[]>([]);
  const [classificationTasks, setClassificationTasks] = useState<ClassificationTask[]>([]);
  const [internalSearch, setInternalSearch] = useState("");
  const [internalSite, setInternalSite] = useState(defaultSiteFilter);
  const [creditSite, setCreditSite] = useState(defaultSiteFilter);
  const [classificationSite, setClassificationSite] = useState(defaultSiteFilter);
  const [classificationStatus, setClassificationStatus] = useState("pending");
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [modal, setModal] = useState<ModalState>(null);
  const [saving, setSaving] = useState(false);
  const [internalForm, setInternalForm] = useState<InternalUserForm>(() => ({ ...emptyInternalUserForm, site_id: firstAllowedSiteId }));
  const [redemptionForm, setRedemptionForm] = useState<RedemptionForm>(() => ({ ...emptyRedemptionForm, site_id: firstAllowedSiteId }));
  const [adjustmentForm, setAdjustmentForm] = useState<AdjustmentForm>(() => ({ ...emptyAdjustmentForm, site_id: firstAllowedSiteId }));
  const [conversionForm, setConversionForm] = useState<ConversionForm>(() => ({
    ...emptyConversionForm,
    site_id: firstAllowedSiteId,
    balance_units_per_cny: firstAllowedSiteId === "aigclink" ? "1" : "10",
  }));
  const [classificationForm, setClassificationForm] = useState(emptyClassificationForm);
  const canWrite = canManageOperations(role);

  const queryIsValid = query.range !== "custom" || Boolean(query.startAt && query.endAt && new Date(query.startAt) < new Date(query.endAt));
  const effectiveQuery = useMemo(
    () => ({ ...query, siteId: normalizeSiteFilter(query.siteId) }),
    [query, allowedSiteKey],
  );
  const effectiveInternalSite = normalizeSiteFilter(internalSite);
  const effectiveCreditSite = normalizeSiteFilter(creditSite);
  const effectiveClassificationSite = normalizeSiteFilter(classificationSite);
  const operationsQuery = useMemo(
    () => queryIsValid ? buildOperationsQuery(effectiveQuery) : "",
    [effectiveQuery, queryIsValid],
  );

  useEffect(() => {
    setQuery({ siteId: defaultSiteFilter, segment: DEFAULT_OPERATIONS_SEGMENT, range: "7d" });
    setInternalSite(defaultSiteFilter);
    setCreditSite(defaultSiteFilter);
    setClassificationSite(defaultSiteFilter);
    setClassificationStatus("pending");
    setInternalSearch("");
    setOverview(null);
    setTrends([]);
    setSyncStatuses([]);
    setInternalUsers([]);
    setRates([]);
    setClassificationTasks([]);
    setLoadError("");
    setModal(null);
    setInternalForm({ ...emptyInternalUserForm, site_id: firstAllowedSiteId });
    setRedemptionForm({ ...emptyRedemptionForm, site_id: firstAllowedSiteId });
    setAdjustmentForm({ ...emptyAdjustmentForm, site_id: firstAllowedSiteId });
    setConversionForm({
      ...emptyConversionForm,
      site_id: firstAllowedSiteId,
      balance_units_per_cny: firstAllowedSiteId === "aigclink" ? "1" : "10",
    });
  }, [allowedSiteKey]);

  async function loadOverview(background = false) {
    if (!hasSiteAccess || !operationsQuery) return;
    if (!background) setLoading(true);
    setLoadError("");
    try {
      const [summary, trendData, syncData] = await Promise.all([
        api<OverviewResponse>(`/operations/summary${operationsQuery}`, token),
        api<ListResponse<TrendItem>>(`/operations/trends${operationsQuery}`, token),
        api<ListResponse<SyncStatus>>("/operations/sync-status", token),
      ]);
      setOverview(summary);
      setTrends(trendData.items);
      setSyncStatuses(syncData.items);
    } catch (error) {
      const message = errorMessage(error);
      setLoadError(message);
      if (background) showToast(message, true);
    } finally {
      if (!background) setLoading(false);
    }
  }

  async function loadInternalUsers(background = false) {
    if (!hasSiteAccess) return;
    if (!background) setLoading(true);
    setLoadError("");
    const params = new URLSearchParams();
    if (effectiveInternalSite) params.set("site_id", effectiveInternalSite);
    if (internalSearch.trim()) params.set("query", internalSearch.trim());
    try {
      const data = await api<ListResponse<InternalUser>>(`/operations/internal-users?${params}`, token);
      setInternalUsers(data.items);
    } catch (error) {
      const message = errorMessage(error);
      setLoadError(message);
      if (background) showToast(message, true);
    } finally {
      if (!background) setLoading(false);
    }
  }

  async function loadRates(background = false) {
    if (!hasSiteAccess) return;
    if (!background) setLoading(true);
    setLoadError("");
    const suffix = effectiveCreditSite ? `?site_id=${encodeURIComponent(effectiveCreditSite)}` : "";
    try {
      const data = await api<ListResponse<ConversionRate>>(`/operations/conversion-rates${suffix}`, token);
      setRates(data.items);
    } catch (error) {
      const message = errorMessage(error);
      setLoadError(message);
      if (background) showToast(message, true);
    } finally {
      if (!background) setLoading(false);
    }
  }

  async function loadClassification(background = false) {
    if (!hasSiteAccess) return;
    if (!background) setLoading(true);
    setLoadError("");
    const params = new URLSearchParams({ task_status: classificationStatus });
    if (effectiveClassificationSite) params.set("site_id", effectiveClassificationSite);
    try {
      const data = await api<ListResponse<ClassificationTask>>(`/operations/classification-tasks?${params}`, token);
      setClassificationTasks(data.items);
    } catch (error) {
      const message = errorMessage(error);
      setLoadError(message);
      if (background) showToast(message, true);
    } finally {
      if (!background) setLoading(false);
    }
  }

  useEffect(() => {
    if (!hasSiteAccess) return;
    if (tab === "overview") void loadOverview();
    if (tab === "internal-users") void loadInternalUsers();
    if (tab === "credits") void loadRates();
    if (tab === "classification") void loadClassification();
  }, [tab, operationsQuery, effectiveInternalSite, effectiveCreditSite, effectiveClassificationSite, classificationStatus, token, hasSiteAccess]);

  async function refreshSources() {
    if (!hasSiteAccess) return;
    setRefreshing(true);
    try {
      const siteIds = effectiveQuery.siteId ? [effectiveQuery.siteId] : allowedSites.map((site) => site.value);
      const result = await api<ListResponse<RefreshResultItem>>("/operations/refresh", token, { method: "POST", body: JSON.stringify({ site_ids: siteIds }) });
      const failure = refreshFailureMessage(result.items);
      if (failure) {
        showToast(`源数据同步失败：${failure}`, true);
      } else {
        showToast("源数据同步完成");
      }
      await loadOverview(true);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setRefreshing(false);
    }
  }

  function openInternal(item?: InternalUser) {
    if (!canWrite || (item && !allowedSiteSet.has(item.site_id as OperationsSiteId))) return;
    setInternalForm(item ? {
      site_id: item.site_id,
      email: item.email,
      reason: item.reason || "",
      active_from: item.active_from?.slice(0, 16) || "",
      active_until: item.active_until?.slice(0, 16) || "",
    } : { ...emptyInternalUserForm, site_id: firstAllowedSiteId });
    setModal({ kind: "internal", item });
  }

  async function saveInternal() {
    if (!canWrite || modal?.kind !== "internal" || !allowedSiteSet.has(internalForm.site_id as OperationsSiteId)) return;
    setSaving(true);
    try {
      const payload = modal.item ? {
        email: internalForm.email.trim(),
        reason: internalForm.reason.trim(),
        active_from: optionalIso(internalForm.active_from),
        active_until: optionalIso(internalForm.active_until) || null,
      } : {
        site_id: internalForm.site_id.trim(),
        email: internalForm.email.trim(),
        reason: internalForm.reason.trim(),
        ...(internalForm.active_from ? { active_from: optionalIso(internalForm.active_from) } : {}),
        active_until: optionalIso(internalForm.active_until) || null,
      };
      await api(`/operations/internal-users${modal.item ? `/${modal.item.internal_user_id}` : ""}`, token, {
        method: modal.item ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      setModal(null);
      showToast(modal.item ? "内部人员配置已更新" : "内部人员已添加");
      await loadInternalUsers(true);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  }

  async function saveRedemption() {
    if (!canWrite || !allowedSiteSet.has(redemptionForm.site_id as OperationsSiteId)) return;
    setSaving(true);
    try {
      await api("/operations/redemption-batches", token, { method: "POST", body: JSON.stringify(buildRedemptionPayload(redemptionForm, idempotencyKey("redemption"))) });
      setModal(null);
      showToast("兑换码批次已生成");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  }

  async function saveAdjustment() {
    if (!canWrite || !allowedSiteSet.has(adjustmentForm.site_id as OperationsSiteId)) return;
    setSaving(true);
    try {
      await api("/operations/balance-adjustments", token, {
        method: "POST",
        body: JSON.stringify({
          site_id: adjustmentForm.site_id.trim(),
          external_user_id: adjustmentForm.external_user_id.trim(),
          purpose: adjustmentForm.purpose,
          balance_units: Number(adjustmentForm.balance_units),
          cash_amount_cny: adjustmentForm.purpose === "sale" ? Number(adjustmentForm.cash_amount_cny) : 0,
          note: adjustmentForm.note.trim(),
          idempotency_key: idempotencyKey("adjustment"),
        }),
      });
      setModal(null);
      showToast("余额调整已提交");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  }

  async function saveRate() {
    if (!canWrite || !allowedSiteSet.has(conversionForm.site_id as OperationsSiteId)) return;
    setSaving(true);
    try {
      await api("/operations/conversion-rates", token, {
        method: "POST",
        body: JSON.stringify({
          site_id: conversionForm.site_id.trim(),
          balance_units_per_cny: Number(conversionForm.balance_units_per_cny),
          ...(conversionForm.effective_from ? { effective_from: optionalIso(conversionForm.effective_from) } : {}),
          note: conversionForm.note.trim(),
        }),
      });
      setModal(null);
      showToast("换算比例已生效");
      await loadRates(true);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  }

  async function saveClassification() {
    if (!canWrite || modal?.kind !== "classification" || !allowedSiteSet.has(modal.item.site_id as OperationsSiteId)) return;
    setSaving(true);
    try {
      const ignored = classificationForm.status === "ignored";
      await api(`/operations/classification-tasks/${modal.item.classification_task_id}`, token, {
        method: "PATCH",
        body: JSON.stringify({
          status: classificationForm.status,
          purpose: ignored ? null : classificationForm.purpose,
          cash_amount_cny: ignored || classificationForm.purpose !== "sale" ? 0 : Number(classificationForm.cash_amount_cny),
          note: classificationForm.note.trim(),
        }),
      });
      setModal(null);
      showToast(ignored ? "记录已忽略" : "记录已完成分类");
      await loadClassification(true);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  }

  if (!hasSiteAccess) {
    return (
      <section className="view operations-workspace-page">
        <div className="topbar operations-page-head">
          <div><h2>运营管理</h2><p>查看已授权站点的运营数据、内部人员与额度记录</p></div>
        </div>
        <div className="operations-access-empty" role="status">
          <div>
            <strong>尚未分配运营站点权限</strong>
            <span>请联系 owner 或 admin，在权限管理中分配可访问的运营站点。</span>
          </div>
        </div>
      </section>
    );
  }

  const summary = overview?.summary || {};
  const previous = overview?.previous_summary || {};
  const visibleSyncStatuses = syncStatuses.filter((item) => (
    allowedSiteSet.has(item.site_id as OperationsSiteId)
    && (!effectiveQuery.siteId || item.site_id === effectiveQuery.siteId)
  ));
  const visibleTrends = trends.filter((item) => allowedSiteSet.has(item.site_id as OperationsSiteId));
  const visibleSiteBreakdown = (overview?.site_breakdown || []).filter((item) => (
    allowedSiteSet.has(item.site_id as OperationsSiteId)
  ));
  const visibleInternalUsers = internalUsers.filter((item) => allowedSiteSet.has(item.site_id as OperationsSiteId));
  const visibleRates = rates.filter((item) => allowedSiteSet.has(item.site_id as OperationsSiteId));
  const visibleClassificationTasks = classificationTasks.filter((item) => allowedSiteSet.has(item.site_id as OperationsSiteId));
  const syncHealth = visibleSyncStatuses.some((item) => item.health === "delayed" || item.health === "never") ? "delayed" : visibleSyncStatuses.some((item) => item.health === "running") ? "running" : "healthy";
  const syncErrorDetails = visibleSyncStatuses.filter((item) => item.error_message).map((item) => `${siteLabel(item.site_id)}：${item.error_message}`).join(" · ");
  const pageDescription = allowedSites.length === 1
    ? `查看 ${allowedSites[0].label} 的收入、消耗和用户构成`
    : "统一查看 AIWeLink 与 AIGCLink 的收入、消耗和用户构成";

  return (
    <section className="view operations-workspace-page">
      <div className="topbar operations-page-head">
        <div><h2>运营管理</h2><p>{pageDescription}</p></div>
        {tab === "overview" && <button className="ghost" type="button" disabled={refreshing} onClick={refreshSources}>{refreshing ? "提交中..." : "刷新源数据"}</button>}
      </div>

      <div className="growth-workspace-tabs operations-tabs" role="tablist" aria-label="运营管理页面">
        {([
          ["overview", "运营概览"],
          ["internal-users", "内部人员"],
          ["credits", "额度与兑换码"],
          ["classification", "待分类"],
        ] as const).map(([value, label]) => <button className={tab === value ? "active" : ""} role="tab" aria-selected={tab === value} type="button" onClick={() => setTab(value)} key={value}>{label}</button>)}
      </div>

      {loadError && <div className="operations-inline-error" role="alert"><strong>数据更新失败</strong><span>{loadError}</span><small>已加载的数据不会被清空</small></div>}

      {tab === "overview" && (
        <div className="operations-tab-content">
          <div className="operations-query-bar">
            <label><span>站点</span><SiteSelect sites={allowedSites} includeAll={showAllSites} value={effectiveQuery.siteId} onChange={(siteId) => setQuery({ ...query, siteId })} /></label>
            <label><span>用户群体</span><select value={query.segment} onChange={(event) => setQuery({ ...query, segment: event.target.value as UserSegment })}>{Object.entries(segmentLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label><span>统计周期</span><select value={query.range} onChange={(event) => setQuery({ ...query, range: event.target.value as OperationsRange })}>{Object.entries(rangeLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            {query.range === "custom" && <><label><span>开始时间</span><input type="datetime-local" value={query.startAt || ""} onChange={(event) => setQuery({ ...query, startAt: event.target.value })} /></label><label><span>结束时间</span><input type="datetime-local" value={query.endAt || ""} onChange={(event) => setQuery({ ...query, endAt: event.target.value })} /></label></>}
            <div className="operations-query-summary"><span>{rangeLabels[query.range]}</span><strong>{segmentLabels[query.segment]}</strong></div>
          </div>

          <div className={`operations-freshness-banner ${syncHealth}`}>
            <div><span className="operations-freshness-dot" /><strong>{syncHealth === "healthy" ? "数据同步正常" : syncHealth === "running" ? "正在同步" : "数据同步延迟"}</strong></div>
            <span>{syncErrorDetails || (visibleSyncStatuses.length ? visibleSyncStatuses.map((item) => `${siteLabel(item.site_id)} ${formatDateTime(item.last_success_at)}`).join(" · ") : "等待首次同步记录")}</span>
            <small>页面查询缓存 60 秒，源数据每 15 分钟同步</small>
          </div>

          <div className="operations-metric-grid" aria-busy={loading}>
            <Metric label="注册用户" value={formatNumber(summary.registered_user_count, 0)} previous={comparison(summary.registered_user_count, previous.registered_user_count)} />
            <Metric label="活跃用户" value={formatNumber(summary.active_user_count, 0)} previous={comparison(summary.active_user_count, previous.active_user_count)} />
            <Metric label="成功调用" value={formatNumber(summary.successful_call_count, 0)} previous={comparison(summary.successful_call_count, previous.successful_call_count)} />
            <Metric label="付费用户" value={formatNumber(summary.payer_count, 0)} previous={comparison(summary.payer_count, previous.payer_count)} />
            <Metric label="收入" value={formatCurrency(summary.gross_income_cny)} previous={comparison(summary.gross_income_cny, previous.gross_income_cny)} />
            <Metric label="退款" value={formatCurrency(summary.refund_cny)} previous={comparison(summary.refund_cny, previous.refund_cny)} />
          </div>

          <div className="operations-overview-table-stack">
            <section className="operations-data-section operations-trend-section">
              <div className="operations-section-head"><div><h3>运营趋势</h3><span>48 小时内按小时，更长周期按天汇总</span></div></div>
              <div className="operations-table-scroll"><table><thead><tr><th>时间</th><th>站点</th><th>注册</th><th>活跃</th><th>成功调用</th><th>消耗额度</th><th>付费用户</th><th>收入</th><th>退款</th></tr></thead><tbody>{visibleTrends.length ? visibleTrends.map((item, index) => <tr key={`${item.site_id}-${item.bucket}-${index}`}><td>{formatBucket(item.bucket)}</td><td>{siteLabel(item.site_id)}</td><td>{formatNumber(item.registered_user_count, 0)}</td><td>{formatNumber(item.active_user_count, 0)}</td><td>{formatNumber(item.successful_call_count, 0)}</td><td>{formatNumber(item.consumed_balance_units, 2)}</td><td>{formatNumber(item.payer_count, 0)}</td><td>{formatCurrency(item.gross_income_cny)}</td><td>{formatCurrency(item.refund_cny)}</td></tr>) : <EmptyRow columns={9} text={loading ? "正在加载趋势..." : "当前周期暂无趋势数据"} />}</tbody></table></div>
            </section>

            <section className="operations-data-section operations-site-comparison">
              <div className="operations-section-head"><div><h3>站点运营对比</h3><span>按当前查询周期和用户群体汇总</span></div><span>{visibleSiteBreakdown.length} 个站点</span></div>
              <div className="operations-table-scroll"><table><thead><tr><th>站点</th><th>注册用户</th><th>活跃用户</th><th>成功调用</th><th>消耗额度</th><th>付费用户</th><th>收入</th><th>退款</th><th>人均消耗</th><th>付费率</th></tr></thead><tbody>{visibleSiteBreakdown.length ? visibleSiteBreakdown.map((item) => <tr key={item.site_id}><td><strong>{siteLabel(item.site_id)}</strong></td><td>{formatNumber(item.registered_user_count, 0)}</td><td>{formatNumber(item.active_user_count, 0)}</td><td>{formatNumber(item.successful_call_count, 0)}</td><td>{formatNumber(item.consumed_balance_units, 2)}</td><td>{formatNumber(item.payer_count, 0)}</td><td>{formatCurrency(item.gross_income_cny)}</td><td>{formatCurrency(item.refund_cny)}</td><td>{formatNumber(averageConsumption(item), 2)}</td><td>{paymentRate(item).toFixed(1)}%</td></tr>) : <EmptyRow columns={10} text={loading ? "正在加载站点对比..." : "当前周期暂无站点汇总"} />}</tbody></table></div>
            </section>
          </div>
        </div>
      )}

      {tab === "internal-users" && (
        <div className="operations-tab-content">
          <div className="operations-query-bar operations-list-query">
            <label><span>站点</span><SiteSelect sites={allowedSites} includeAll={showAllSites} value={effectiveInternalSite} onChange={setInternalSite} /></label>
            <label className="operations-search-field"><span>人员查询</span><input value={internalSearch} onChange={(event) => setInternalSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void loadInternalUsers(); }} placeholder="邮箱或业务用户 ID" /></label>
            <button className="ghost" type="button" onClick={() => void loadInternalUsers()} disabled={loading}>查询</button>
          </div>
          <section className="operations-data-section">
            <div className="operations-section-head"><div><h3>内部人员名单</h3><span>内部人员消耗单独统计，不计入普通用户收入</span></div>{canWrite && <button type="button" onClick={() => openInternal()}>添加内部人员</button>}</div>
            <div className="operations-table-scroll"><table><thead><tr><th>站点</th><th>邮箱</th><th>识别状态</th><th>业务用户 ID</th><th>识别时间</th><th>标记原因</th><th>生效时间</th><th>失效时间</th>{canWrite && <th>操作</th>}</tr></thead><tbody>{visibleInternalUsers.length ? visibleInternalUsers.map((item) => <tr key={item.internal_user_id}><td>{siteLabel(item.site_id)}</td><td><strong>{item.email}</strong></td><td><span className={`operations-status-tag ${item.recognition_status}`}>{recognitionStatusLabel(item.recognition_status)}</span></td><td>{item.external_user_id || "-"}</td><td>{formatDateTime(item.recognized_at)}</td><td>{item.reason || "-"}</td><td>{formatDateTime(item.active_from)}</td><td>{formatDateTime(item.active_until)}</td>{canWrite && <td><button className="ghost operations-row-button" type="button" onClick={() => openInternal(item)}>编辑</button></td>}</tr>) : <EmptyRow columns={canWrite ? 9 : 8} text={loading ? "正在加载..." : "暂无内部人员配置"} />}</tbody></table></div>
          </section>
        </div>
      )}

      {tab === "credits" && (
        <div className="operations-tab-content">
          <div className="operations-query-bar operations-list-query">
            <label><span>站点</span><SiteSelect sites={allowedSites} includeAll={showAllSites} value={effectiveCreditSite} onChange={setCreditSite} /></label>
            {canWrite && <div className="operations-command-group"><button type="button" onClick={() => { setRedemptionForm({ ...emptyRedemptionForm, site_id: firstAllowedSiteId }); setModal({ kind: "redemption" }); }}>生成兑换码</button><button className="ghost" type="button" onClick={() => { setAdjustmentForm({ ...emptyAdjustmentForm, site_id: firstAllowedSiteId }); setModal({ kind: "adjustment" }); }}>调整余额</button><button className="ghost" type="button" onClick={() => { setConversionForm({ ...emptyConversionForm, site_id: firstAllowedSiteId, balance_units_per_cny: firstAllowedSiteId === "aigclink" ? "1" : "10" }); setModal({ kind: "rate" }); }}>新增换算比例</button></div>}
          </div>
          <section className="operations-data-section">
            <div className="operations-section-head"><div><h3>余额换算比例</h3><span>用于把不同系统的余额统一换算为 CNY 口径，按生效时间保留历史版本</span></div><span>{visibleRates.length} 条记录</span></div>
            <div className="operations-table-scroll"><table><thead><tr><th>站点</th><th>每 1 CNY 对应余额</th><th>生效时间</th><th>失效时间</th><th>备注</th></tr></thead><tbody>{visibleRates.length ? visibleRates.map((item) => <tr key={item.conversion_rate_id}><td>{siteLabel(item.site_id)}</td><td><strong>{formatNumber(item.balance_units_per_cny, 10)}</strong></td><td>{formatDateTime(item.effective_from)}</td><td>{formatDateTime(item.effective_until)}</td><td>{item.note || "-"}</td></tr>) : <EmptyRow columns={5} text={loading ? "正在加载..." : "暂无换算比例"} />}</tbody></table></div>
          </section>
          {!canWrite && <div className="operations-readonly-note">当前角色为只读权限。兑换码、余额调整和换算比例只能由 owner/admin 操作。</div>}
        </div>
      )}

      {tab === "classification" && (
        <div className="operations-tab-content">
          <div className="operations-query-bar operations-list-query">
            <label><span>站点</span><SiteSelect sites={allowedSites} includeAll={showAllSites} value={effectiveClassificationSite} onChange={setClassificationSite} /></label>
            <label><span>处理状态</span><select value={classificationStatus} onChange={(event) => setClassificationStatus(event.target.value)}><option value="pending">待处理</option><option value="resolved">已分类</option><option value="ignored">已忽略</option></select></label>
          </div>
          <section className="operations-data-section">
            <div className="operations-section-head"><div><h3>待分类额度记录</h3><span>来源用途无法自动识别时，在这里补录真实业务用途</span></div><span>{visibleClassificationTasks.length} 条记录</span></div>
            <div className="operations-table-scroll"><table><thead><tr><th>站点</th><th>业务用户 ID</th><th>来源类型</th><th>来源记录</th><th>额度</th><th>发生时间</th><th>状态</th>{canWrite && <th>操作</th>}</tr></thead><tbody>{visibleClassificationTasks.length ? visibleClassificationTasks.map((item) => <tr key={item.classification_task_id}><td>{siteLabel(item.site_id)}</td><td><strong>{item.account_label || item.external_user_id}</strong>{item.account_label && <small className="operations-cell-subtext">{item.external_user_id}</small>}</td><td>{item.source_type}</td><td>{item.source_record_id || "-"}</td><td>{formatNumber(item.balance_units, 10)}</td><td>{formatDateTime(item.occurred_at)}</td><td><span className={`operations-status-tag ${item.status}`}>{item.status === "pending" ? "待处理" : item.status === "resolved" ? "已分类" : "已忽略"}</span></td>{canWrite && <td>{item.status === "pending" ? <button className="ghost operations-row-button" type="button" onClick={() => { setClassificationForm(emptyClassificationForm); setModal({ kind: "classification", item }); }}>补录</button> : "-"}</td>}</tr>) : <EmptyRow columns={canWrite ? 8 : 7} text={loading ? "正在加载..." : "当前筛选下暂无记录"} />}</tbody></table></div>
          </section>
        </div>
      )}

      {modal?.kind === "internal" && <GrowthCreateModal title={modal.item ? "编辑内部人员" : "添加内部人员"} submitLabel={modal.item ? "保存修改" : "确认添加"} saving={saving} submitDisabled={!internalForm.site_id || !internalForm.email.trim()} onClose={() => setModal(null)} onSubmit={saveInternal}><div className="growth-form-grid operations-modal-grid"><label><span className="field-label"><strong>站点</strong></span><SiteSelect sites={allowedSites} includeAll={false} value={internalForm.site_id} onChange={(site_id) => setInternalForm({ ...internalForm, site_id })} /></label><label><span className="field-label"><strong>邮箱</strong></span><input type="email" autoComplete="off" value={internalForm.email} onChange={(event) => setInternalForm({ ...internalForm, email: event.target.value })} required /></label><label className="span-2"><span className="field-label"><strong>标记原因</strong><span>（可选）</span></span><input value={internalForm.reason} onChange={(event) => setInternalForm({ ...internalForm, reason: event.target.value })} /></label><label><span className="field-label"><strong>生效时间</strong></span><input type="datetime-local" value={internalForm.active_from} onChange={(event) => setInternalForm({ ...internalForm, active_from: event.target.value })} /></label><label><span className="field-label"><strong>失效时间</strong><span>（可选）</span></span><input type="datetime-local" value={internalForm.active_until} onChange={(event) => setInternalForm({ ...internalForm, active_until: event.target.value })} /></label></div></GrowthCreateModal>}

      {modal?.kind === "redemption" && <GrowthCreateModal title="生成兑换码" submitLabel="生成兑换码" saving={saving} submitDisabled={!redemptionForm.site_id || Number(redemptionForm.code_count) <= 0 || Number(redemptionForm.balance_units_per_code) <= 0 || (redemptionForm.purpose === "sale" && Number(redemptionForm.cash_amount_cny) <= 0)} onClose={() => setModal(null)} onSubmit={saveRedemption}><div className="growth-form-grid operations-modal-grid"><label><span className="field-label"><strong>站点</strong></span><SiteSelect sites={allowedSites} includeAll={false} value={redemptionForm.site_id} onChange={(site_id) => setRedemptionForm({ ...redemptionForm, site_id })} /></label><PurposeFields purpose={redemptionForm.purpose} cash={redemptionForm.cash_amount_cny} onPurpose={(purpose) => setRedemptionForm({ ...redemptionForm, purpose, cash_amount_cny: purpose === "sale" ? redemptionForm.cash_amount_cny : "0" })} onCash={(cash_amount_cny) => setRedemptionForm({ ...redemptionForm, cash_amount_cny })} /><label><span className="field-label"><strong>兑换码数量</strong></span><input type="number" min="1" max="10000" value={redemptionForm.code_count} onChange={(event) => setRedemptionForm({ ...redemptionForm, code_count: event.target.value })} /></label><label><span className="field-label"><strong>每个兑换码额度</strong></span><input type="number" min="0" step="any" value={redemptionForm.balance_units_per_code} onChange={(event) => setRedemptionForm({ ...redemptionForm, balance_units_per_code: event.target.value })} /></label><label className="span-2"><span className="field-label"><strong>备注</strong><span>（可选）</span></span><textarea value={redemptionForm.note} onChange={(event) => setRedemptionForm({ ...redemptionForm, note: event.target.value })} /></label></div></GrowthCreateModal>}

      {modal?.kind === "adjustment" && <GrowthCreateModal title="调整余额" submitLabel="提交调整" saving={saving} submitDisabled={!adjustmentForm.site_id || !adjustmentForm.external_user_id.trim() || Number(adjustmentForm.balance_units) === 0 || (adjustmentForm.purpose === "sale" && Number(adjustmentForm.cash_amount_cny) <= 0)} onClose={() => setModal(null)} onSubmit={saveAdjustment}><div className="growth-form-grid operations-modal-grid"><label><span className="field-label"><strong>站点</strong></span><SiteSelect sites={allowedSites} includeAll={false} value={adjustmentForm.site_id} onChange={(site_id) => setAdjustmentForm({ ...adjustmentForm, site_id })} /></label><label><span className="field-label"><strong>业务用户 ID</strong></span><input value={adjustmentForm.external_user_id} onChange={(event) => setAdjustmentForm({ ...adjustmentForm, external_user_id: event.target.value })} /></label><PurposeFields purpose={adjustmentForm.purpose} cash={adjustmentForm.cash_amount_cny} onPurpose={(purpose) => setAdjustmentForm({ ...adjustmentForm, purpose, cash_amount_cny: purpose === "sale" ? adjustmentForm.cash_amount_cny : "0" })} onCash={(cash_amount_cny) => setAdjustmentForm({ ...adjustmentForm, cash_amount_cny })} /><label><span className="field-label"><strong>调整额度</strong></span><input type="number" step="any" value={adjustmentForm.balance_units} onChange={(event) => setAdjustmentForm({ ...adjustmentForm, balance_units: event.target.value })} /><span className="growth-field-message is-muted">增加填正数，扣减填负数</span></label><label className="span-2"><span className="field-label"><strong>备注</strong><span>（可选）</span></span><textarea value={adjustmentForm.note} onChange={(event) => setAdjustmentForm({ ...adjustmentForm, note: event.target.value })} /></label></div></GrowthCreateModal>}

      {modal?.kind === "rate" && <GrowthCreateModal title="新增换算比例" submitLabel="确认生效" saving={saving} submitDisabled={!conversionForm.site_id || Number(conversionForm.balance_units_per_cny) <= 0} onClose={() => setModal(null)} onSubmit={saveRate}><div className="growth-form-grid operations-modal-grid"><label><span className="field-label"><strong>站点</strong></span><SiteSelect sites={allowedSites} includeAll={false} value={conversionForm.site_id} onChange={(site_id) => setConversionForm({ ...conversionForm, site_id, balance_units_per_cny: site_id === "aigclink" ? "1" : "10" })} /></label><label><span className="field-label"><strong>每 1 CNY 对应余额</strong></span><input type="number" min="0" step="any" value={conversionForm.balance_units_per_cny} onChange={(event) => setConversionForm({ ...conversionForm, balance_units_per_cny: event.target.value })} /></label><label><span className="field-label"><strong>生效时间</strong><span>（可选）</span></span><input type="datetime-local" value={conversionForm.effective_from} onChange={(event) => setConversionForm({ ...conversionForm, effective_from: event.target.value })} /><span className="growth-field-message is-muted">{conversionRateEffectiveHint}</span></label><label className="span-2"><span className="field-label"><strong>备注</strong><span>（可选）</span></span><textarea value={conversionForm.note} onChange={(event) => setConversionForm({ ...conversionForm, note: event.target.value })} /></label></div></GrowthCreateModal>}

      {modal?.kind === "classification" && <GrowthCreateModal title="补录额度用途" submitLabel={classificationForm.status === "ignored" ? "确认忽略" : "完成分类"} saving={saving} submitDisabled={classificationForm.status === "resolved" && classificationForm.purpose === "sale" && Number(classificationForm.cash_amount_cny) <= 0} onClose={() => setModal(null)} onSubmit={saveClassification}><div className="operations-classification-context"><div><span>站点</span><strong>{siteLabel(modal.item.site_id)}</strong></div><div><span>业务用户 ID</span><strong>{modal.item.external_user_id}</strong></div><div><span>来源类型</span><strong>{modal.item.source_type}</strong></div><div><span>额度</span><strong>{formatNumber(modal.item.balance_units, 10)}</strong></div></div><div className="growth-form-grid operations-modal-grid"><label><span className="field-label"><strong>处理方式</strong></span><select value={classificationForm.status} onChange={(event) => setClassificationForm({ ...classificationForm, status: event.target.value as "resolved" | "ignored" })}><option value="resolved">完成分类</option><option value="ignored">忽略记录</option></select></label>{classificationForm.status === "resolved" && <PurposeFields purpose={classificationForm.purpose} cash={classificationForm.cash_amount_cny} onPurpose={(purpose) => setClassificationForm({ ...classificationForm, purpose, cash_amount_cny: purpose === "sale" ? classificationForm.cash_amount_cny : "0" })} onCash={(cash_amount_cny) => setClassificationForm({ ...classificationForm, cash_amount_cny })} />}<label className="span-2"><span className="field-label"><strong>补录说明</strong><span>（可选）</span></span><textarea value={classificationForm.note} onChange={(event) => setClassificationForm({ ...classificationForm, note: event.target.value })} /></label></div></GrowthCreateModal>}
    </section>
  );
}

export default OperationsManagementPage;
