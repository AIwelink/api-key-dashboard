import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import {
  MetricDefinition,
  type MetricDefinitionDetails,
} from "../components/dataWorkspace/MetricDefinition";
import { WorkspaceRail } from "../components/dataWorkspace/WorkspaceRail";
import { GrowthCreateModal } from "../components/GrowthCreateModal";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import type { OperationsSiteId } from "../types";
import { errorMessage } from "../utils/format";
import {
  RedemptionCodeTable,
  type RedemptionCodeListResponse,
  type RedemptionCodeRow,
} from "./operations/RedemptionCodeTable";
import "./OperationsManagementPage.css";

type OperationsTab = "overview" | "internal-users" | "credits" | "classification";
type OperationsRange = "today" | "7d" | "30d" | "custom";
type UserSegment = "all" | "ordinary" | "internal";
type Purpose = "sale" | "promotion" | "internal" | "compensation" | "other";
type Toast = (message: string, isError?: boolean) => void;

export const DEFAULT_OPERATIONS_SEGMENT: UserSegment = "ordinary";

export function shouldAutoRefreshOperationsOverview({
  tab,
  hasSiteAccess,
  queryIsValid,
  busy,
}: {
  tab: OperationsTab;
  hasSiteAccess: boolean;
  queryIsValid: boolean;
  busy: boolean;
}) {
  return tab === "overview" && hasSiteAccess && queryIsValid && !busy;
}

export function shouldApplyOperationsOverviewResponse(
  requestId: number,
  latestRequestId: number,
  requestQuery: string,
  currentQuery: string,
) {
  return requestId === latestRequestId && requestQuery === currentQuery;
}

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

type LifecycleSummary = {
  scope?: "all" | "site";
  site_id?: string | null;
  billing_mode?: "mixed" | "cash_and_subscription" | "usage_postpaid";
  activation_24h_numerator?: number;
  activation_24h_denominator?: number;
  activation_24h_rate?: number | null;
  activation_7d_numerator?: number;
  activation_7d_denominator?: number;
  activation_7d_rate?: number | null;
  payer_7d_numerator?: number;
  payer_7d_denominator?: number;
  payer_7d_rate?: number | null;
  payer_30d_numerator?: number;
  payer_30d_denominator?: number;
  payer_30d_rate?: number | null;
  active_payer_numerator?: number;
  active_payer_denominator?: number;
  active_payer_rate?: number | null;
  period_payer_numerator?: number;
  period_payer_denominator?: number;
  period_payer_rate?: number | null;
  active_user_count?: number;
  cumulative_payer_count?: number;
  effective_payer_count?: number;
  active_cash_payer_count?: number;
  period_payer_count?: number;
  unknown_payer_count?: number;
  churn_warning_user_count?: number;
  churned_user_count?: number;
  returned_user_count?: number;
  cash_income_cny?: number;
  subscription_cash_income_cny?: number;
  subscription_amortized_income_cny?: number;
  recharge_event_count?: number;
  recharge_balance_units?: number;
  usage_billed_income_cny?: number;
};

type RetentionValue = {
  numerator?: number | null;
  denominator?: number | null;
  rate?: number | null;
};

type RetentionRow = {
  site_id: string;
  cohort_date: string;
  cohort_size: number;
  d1_numerator?: number | null;
  d1_denominator?: number | null;
  d1_rate?: number | null;
  d3_numerator?: number | null;
  d3_denominator?: number | null;
  d3_rate?: number | null;
  d7_numerator?: number | null;
  d7_denominator?: number | null;
  d7_rate?: number | null;
  d14_numerator?: number | null;
  d14_denominator?: number | null;
  d14_rate?: number | null;
  d30_numerator?: number | null;
  d30_denominator?: number | null;
  d30_rate?: number | null;
};

type ModelBreakdownItem = {
  model_name: string;
  successful_call_count: number;
  token_count: number;
  billed_amount_cny: number;
  revenue_share?: number | null;
};

type CustomerBreakdownItem = {
  site_id: string;
  external_user_id: string;
  account_label?: string;
  successful_call_count: number;
  token_count: number;
  billed_amount_cny: number;
};

type LifecycleResponse = {
  summary: LifecycleSummary;
  retention: RetentionRow[];
  site_breakdown: LifecycleSummary[];
  model_breakdown: ModelBreakdownItem[];
  customer_breakdown: CustomerBreakdownItem[];
  generated_at: string;
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
export const supportsSafeRedemptionDeletion = false;

export function shouldApplyRedemptionResponse(
  requestId: number,
  latestRequestId: number,
  requestSiteId: string,
  currentSiteId: string,
) {
  return requestId === latestRequestId && requestSiteId === currentSiteId;
}

export function shouldApplyRedemptionReveal(
  requestId: number,
  latestRequestId: number,
  requestSiteId: string,
  currentSiteId: string,
  currentTab: OperationsTab,
  canWrite: boolean,
) {
  return requestId === latestRequestId
    && requestSiteId === currentSiteId
    && currentTab === "credits"
    && canWrite;
}

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

type RedemptionResultPanelProps = {
  codes: string[];
  onClose: () => void;
  onCopy: () => void;
  onDownload: () => void;
};

type RevealedRedemptionCode = {
  code_id: number;
  code: string;
  code_mask: string;
  fetched_at: string;
};

export function RedemptionResultPanel({
  codes,
  onClose,
  onCopy,
  onDownload,
}: RedemptionResultPanelProps) {
  return (
    <div className="operations-redemption-result" role="dialog" aria-modal="true" aria-labelledby="operations-redemption-result-title">
      <div className="operations-redemption-result-header">
        <div>
          <span className="operations-eyebrow">一次性展示</span>
          <h3 id="operations-redemption-result-title">兑换码已生成</h3>
        </div>
        <button className="ghost icon-button" type="button" aria-label="关闭兑换码结果" onClick={onClose}>×</button>
      </div>
      <p className="operations-redemption-result-note">兑换码只在本次响应中显示，Growth 数据库不会保存明文。请立即复制或下载。</p>
      <textarea className="operations-redemption-result-codes" readOnly value={codes.join("\n")} aria-label="生成的兑换码" />
      <div className="operations-redemption-result-actions">
        <button type="button" onClick={onCopy}>复制全部</button>
        <button className="ghost" type="button" onClick={onDownload}>下载兑换码</button>
        <button className="ghost" type="button" onClick={onClose}>完成</button>
      </div>
    </div>
  );
}

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

export function sortNewestFirst<T>(items: T[], timestamp: (item: T) => string) {
  return [...items].sort((left, right) => (
    Date.parse(timestamp(right)) - Date.parse(timestamp(left))
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

export function redemptionSubmitDisabled(form: RedemptionForm) {
  return !form.site_id || Number(form.code_count) <= 0 || Number(form.balance_units_per_code) <= 0;
}

export function adjustmentSubmitDisabled(form: AdjustmentForm) {
  return !form.site_id || !form.external_user_id.trim() || Number(form.balance_units) === 0;
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

export function formatLifecycleRate(value: number | null | undefined) {
  return value == null ? "--" : `${(Number(value) * 100).toFixed(1)}%`;
}

export function formatRetentionRate(value: RetentionValue) {
  return value.denominator == null || Number(value.denominator) === 0
    ? "--"
    : formatLifecycleRate(value.rate);
}

export function retentionHeatTone(rate: number | null | undefined) {
  if (rate == null) return "pending";
  if (rate < 0.3) return "low";
  if (rate < 0.45) return "medium-low";
  if (rate < 0.6) return "medium";
  if (rate < 0.75) return "high";
  return "very-high";
}

export function operationsIncomeLabel(siteId: string) {
  if (siteId === "aiwelink") return "现金收入";
  if (siteId === "aigclink") return "调用计费收入";
  return "收入";
}

function weightedRetentionRate(rows: RetentionRow[], day: 1 | 3 | 7 | 14 | 30) {
  const numeratorKey = `d${day}_numerator` as keyof RetentionRow;
  const denominatorKey = `d${day}_denominator` as keyof RetentionRow;
  let numerator = 0;
  let denominator = 0;
  for (const row of rows) {
    const rowDenominator = row[denominatorKey];
    if (rowDenominator == null) continue;
    numerator += Number(row[numeratorKey] || 0);
    denominator += Number(rowDenominator);
  }
  return denominator > 0 ? numerator / denominator : null;
}

function formatDateTime(value?: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

export function operationsDataWatermark(
  statuses: Array<Pick<SyncStatus, "site_id" | "last_success_at">>,
  expectedSiteIds?: readonly string[],
) {
  const selectedStatuses = expectedSiteIds
    ? expectedSiteIds.map((siteId) => statuses.find((item) => item.site_id === siteId))
    : statuses;
  if (!selectedStatuses.length) return undefined;

  let earliestValue: string | undefined;
  let earliestTime = Number.POSITIVE_INFINITY;
  for (const status of selectedStatuses) {
    const value = status?.last_success_at;
    if (!value) return undefined;
    const time = new Date(value).getTime();
    if (Number.isNaN(time)) return undefined;
    if (time >= earliestTime) continue;
    earliestTime = time;
    earliestValue = value;
  }
  return earliestValue;
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

export function internalUserDeleteDetails(item: InternalUser): Array<[string, string]> {
  return [
    ["站点", siteLabel(item.site_id)],
    ["邮箱", item.email || "-"],
    ["业务用户 ID", item.external_user_id || "-"],
  ];
}

export function InternalUserActionButtons({
  item,
  onDelete,
  onEdit,
}: {
  item: InternalUser;
  onDelete: (item: InternalUser) => void;
  onEdit: (item: InternalUser) => void;
}) {
  return (
    <div className="operations-row-actions">
      <button className="ghost operations-row-button" type="button" onClick={() => onEdit(item)}>
        编辑
      </button>
      <button
        aria-label={`删除内部人员 ${item.email || item.external_user_id || item.internal_user_id}`}
        className="ghost operations-row-button danger-button"
        type="button"
        onClick={() => onDelete(item)}
      >
        删除
      </button>
    </div>
  );
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
  const isLoading = text.startsWith("正在加载");
  return (
    <tr className={isLoading ? "operations-loading-row" : undefined}>
      <td className="operations-empty-cell" colSpan={columns}>
        {isLoading ? (
          <div className="operations-table-loading" role="status">
            <span className="data-loading-mark" aria-hidden="true" />
            <span>{text}</span>
            <div className="data-loading-lines" aria-hidden="true"><i /><i /><i /></div>
          </div>
        ) : text}
      </td>
    </tr>
  );
}

const operationsMetricDefinitions: Record<string, MetricDefinitionDetails> = {
  "注册用户": {
    definition: "当前查询周期内完成注册的去重用户数。",
    formula: "COUNT(DISTINCT user_id WHERE registered_at IN window)",
    included: "当前站点与用户群体中，注册时间落在查询周期内的账号",
    excluded: "不属于当前站点或用户群体的账号；重复注册事实",
    source: "public_users + user_facts",
    freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
  },
  "活跃用户": {
    definition: "当前查询周期内至少产生一次成功调用的去重用户数。",
    formula: "COUNT(DISTINCT user_id WHERE successful_calls > 0)",
    included: "当前站点与用户群体的成功调用用户",
    excluded: "失败调用、取消调用，以及不属于当前用户群体的账号",
    source: "usage_records",
    freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
  },
  "成功调用": {
    definition: "当前查询周期内由上游确认成功的模型调用次数。",
    formula: "SUM(successful_call_count)",
    included: "当前站点与用户群体的成功调用记录",
    excluded: "失败、取消、超时且未计费的调用",
    source: "usage_records",
    freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
  },
  "付费 / 计费用户": {
    definition: "AIWeLink 可核验付款用户 ∪ AIGCLink 有标价调用客户。",
    formula: "COUNT(DISTINCT verified_cash_payer OR priced_usage_customer)",
    included: "AIWeLink 已核验订单、销售型额度或销售型兑换码；AIGCLink 有标价成功调用客户",
    excluded: "赠送、推广、内部、补偿额度；未记录历史兑换码；无标价调用",
    source: "orders + balance_adjustments + usage_records",
    freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
  },
  "现金收入": {
    definition: "AIWeLink 当前周期内可核验的实际收款金额。",
    formula: "SUM(order_cash + sale_adjustment_cash + sale_redemption_cash)",
    included: "已支付订单、销售型手工加款、销售型兑换码的实收金额",
    excluded: "赠送、推广、内部、补偿额度；未记录历史兑换码；订阅摊销",
    source: "orders + balance_adjustments + redemption_batches",
    freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
  },
  "调用计费收入": {
    definition: "AIGCLink 当前周期内按数据库模型标价计算的后付费调用收入。",
    formula: "SUM(successful_usage × effective_model_price)",
    included: "存在有效模型标价的成功调用",
    excluded: "失败调用、无标价调用、未进入当前周期的调用",
    source: "usage_records + model_prices",
    freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
  },
  "收入": {
    definition: "按站点商业模式合并的可核验现金收入与调用计费收入。",
    formula: "AIWeLink cash_income + AIGCLink usage_billed_income",
    included: "AIWeLink 可核验收款；AIGCLink 有标价成功调用",
    excluded: "赠送额度、未记录历史兑换码、无标价调用",
    source: "orders + balance_adjustments + usage_records",
    freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
  },
  "退款": {
    definition: "当前查询周期内已确认完成的退款金额。",
    formula: "SUM(completed_refund_amount)",
    included: "已完成并可关联业务交易的退款",
    excluded: "申请中、失败、取消的退款",
    source: "orders + refunds",
    freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
  },
  "24 小时激活率": {
    definition: "注册后 24 小时内产生成功调用的新用户比例。",
    formula: "24h 内成功调用注册用户 / 已满 24h 的注册用户",
    included: "已完成 24 小时观察窗口的注册用户",
    excluded: "观察窗口尚未成熟的注册用户",
    source: "public_users + usage_records",
    freshness: "随 15 分钟源数据同步滚动重算",
  },
  "7 日激活率": {
    definition: "注册后 7 天内产生成功调用的新用户比例。",
    formula: "7d 内成功调用注册用户 / 已满 7d 的注册用户",
    included: "已完成 7 天观察窗口的注册用户",
    excluded: "观察窗口尚未成熟的注册用户",
    source: "public_users + usage_records",
    freshness: "随 15 分钟源数据同步滚动重算",
  },
  "D7 留存": {
    definition: "注册后第 7 个上海自然日仍有成功调用的用户比例。",
    formula: "Σ D7 成功调用人数 / Σ 已成熟 D7 cohort 人数",
    included: "已经过第 7 个上海自然日的注册 cohort",
    excluded: "未成熟 cohort；失败调用；非当前用户群体",
    source: "public_users + usage_records（Asia/Shanghai）",
    freshness: "随 15 分钟源数据同步按自然日补算",
  },
  "活跃用户付费率": {
    definition: "当前周期活跃用户中，属于已付款或已计费客户的比例。",
    formula: "活跃付费/计费用户 / 活跃用户",
    included: "AIWeLink 可核验付款活跃用户；AIGCLink 有标价调用活跃客户",
    excluded: "仅获赠额度但无可核验付款的 AIWeLink 用户",
    source: "orders + balance_adjustments + usage_records",
    freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
  },
  "本期付款率": {
    definition: "当前周期内新增实际付款用户占活跃用户的比例，仅适用于现金收款站点。",
    formula: "本期实际付款用户 / 本期活跃用户",
    included: "当前周期完成可核验付款的 AIWeLink 用户",
    excluded: "历史付款但本期未付款用户；AIGCLink 后付费客户",
    source: "orders + sale balance_adjustments + usage_records",
    freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
  },
  "流失预警": {
    definition: "最近一次成功调用距今 14 至 30 天的历史活跃用户数。",
    formula: "COUNT(DISTINCT user_id WHERE inactivity_days IN [14, 30))",
    included: "余额与订阅用户统一按成功调用活跃度判断",
    excluded: "从未成功调用的账号；已达到 30 天使用流失的账号",
    source: "usage_records",
    freshness: "随 15 分钟源数据同步滚动重算",
  },
  "使用流失": {
    definition: "连续 30 天及以上没有成功调用的历史活跃用户数。",
    formula: "COUNT(DISTINCT user_id WHERE inactivity_days >= 30)",
    included: "余额与订阅用户统一计算，不区分付费方式",
    excluded: "从未成功调用的账号；30 天内仍有成功调用的账号",
    source: "usage_records",
    freshness: "随 15 分钟源数据同步滚动重算",
  },
  "回流用户": {
    definition: "曾连续 30 天未调用，之后在当前周期重新产生成功调用的用户数。",
    formula: "COUNT(DISTINCT user_id WHERE prior_gap >= 30d AND current_success)",
    included: "余额与订阅用户统一按成功调用回流判断",
    excluded: "间隔不足 30 天的普通复访；失败调用",
    source: "usage_records",
    freshness: "随 15 分钟源数据同步滚动重算",
  },
};

export function operationsMetricDefinition(label: string, siteId: string): MetricDefinitionDetails {
  if (siteId === "aiwelink") {
    if (label === "付费 / 计费用户") return {
      definition: "AIWeLink 可核验付款用户。",
      formula: "COUNT(DISTINCT verified_cash_payer)",
      included: "已核验订单、销售型手工加款或销售型兑换码用户",
      excluded: "赠送、推广、内部、补偿额度用户；未记录历史兑换码",
      source: "orders + balance_adjustments + redemption_batches",
      freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
    };
    if (label === "活跃用户付费率") return {
      definition: "当前周期活跃用户中，属于可核验付款用户的比例。",
      formula: "活跃可核验付款用户 / 活跃用户",
      included: "存在已核验订单、销售型手工加款或销售型兑换码的活跃用户",
      excluded: "仅获赠额度但无可核验付款的用户",
      source: "orders + balance_adjustments + usage_records",
      freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
    };
    if (label === "本期付款率") return {
      definition: "当前周期内新增实际付款用户占活跃用户的比例。",
      formula: "本期实际付款用户 / 本期活跃用户",
      included: "当前周期完成可核验付款的用户",
      excluded: "历史付款但本期未付款用户；赠送与非销售额度",
      source: "orders + sale balance_adjustments + usage_records",
      freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
    };
  }

  if (siteId === "aigclink") {
    if (label === "付费 / 计费用户") return {
      definition: "AIGCLink 有标价成功调用客户。",
      formula: "COUNT(DISTINCT priced_usage_customer)",
      included: "当前周期存在有效模型标价成功调用的企业客户",
      excluded: "失败调用、无标价调用、未进入当前周期的调用",
      source: "usage_records + model_prices",
      freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
    };
    if (label === "活跃用户付费率") return {
      definition: "当前周期活跃客户中，存在有标价成功调用的客户比例。",
      formula: "有标价调用客户 / 活跃客户",
      included: "当前周期存在有效模型标价成功调用的活跃客户",
      excluded: "仅有失败调用或无标价调用的客户",
      source: "usage_records + model_prices",
      freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
    };
    if (label === "本期付款率") return {
      definition: "企业后付费站点不计算本期现金付款率。",
      formula: "N/A（按有标价调用确认计费客户）",
      included: "无",
      excluded: "全部后付费调用客户",
      source: "usage_records + model_prices",
      freshness: "页面缓存 60 秒；源数据每 15 分钟同步",
    };
  }

  return operationsMetricDefinitions[label];
}

function Metric({ label, value, previous, siteId }: { label: string; value: string; previous?: string; siteId: string }) {
  return (
    <div className="operations-metric">
      <span>{label}</span>
      <MetricDefinition label={label} details={operationsMetricDefinition(label, siteId)} showLabel={false} />
      <strong>{value}</strong>
      <small>{previous || "当前查询周期"}</small>
    </div>
  );
}

function RetentionCell({ value }: { value: RetentionValue }) {
  const matureRate = value.denominator == null || Number(value.denominator) === 0
    ? null
    : value.rate;

  return (
    <td className={`operations-retention-cell ${retentionHeatTone(matureRate)}`}>
      {formatRetentionRate(value)}
    </td>
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
  const hasAigclinkAccess = allowedSiteSet.has("aigclink");
  const normalizeSiteFilter = (siteId: string) => !siteId
    ? ""
    : allowedSiteSet.has(siteId as OperationsSiteId) ? siteId : defaultSiteFilter;
  const [tab, setTab] = useState<OperationsTab>(initialTab);
  const [query, setQuery] = useState<OperationsQueryState>({ siteId: defaultSiteFilter, segment: DEFAULT_OPERATIONS_SEGMENT, range: "7d" });
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [lifecycle, setLifecycle] = useState<LifecycleResponse | null>(null);
  const [trends, setTrends] = useState<TrendItem[]>([]);
  const [syncStatuses, setSyncStatuses] = useState<SyncStatus[]>([]);
  const [internalUsers, setInternalUsers] = useState<InternalUser[]>([]);
  const [rates, setRates] = useState<ConversionRate[]>([]);
  const [classificationTasks, setClassificationTasks] = useState<ClassificationTask[]>([]);
  const [redemptionList, setRedemptionList] = useState<RedemptionCodeListResponse>({ items: [], total: 0, page: 1, page_size: 20, pages: 1, truncated: false });
  const [redemptionStatus, setRedemptionStatus] = useState("");
  const [redemptionOrigin, setRedemptionOrigin] = useState("");
  const [redemptionSearchDraft, setRedemptionSearchDraft] = useState("");
  const [redemptionSearch, setRedemptionSearch] = useState("");
  const [redemptionPage, setRedemptionPage] = useState(1);
  const [revealedRedemption, setRevealedRedemption] = useState<RevealedRedemptionCode | null>(null);
  const [redemptionLoading, setRedemptionLoading] = useState(false);
  const overviewRequestId = useRef(0);
  const operationsQueryRef = useRef("");
  const redemptionRequestId = useRef(0);
  const redemptionRevealRequestId = useRef(0);
  const redemptionSiteId = useRef(defaultSiteFilter);
  const operationsTab = useRef<OperationsTab>(initialTab);
  const redemptionCanWrite = useRef(false);
  const [internalSearch, setInternalSearch] = useState("");
  const [internalSite, setInternalSite] = useState(defaultSiteFilter);
  const [creditSite, setCreditSite] = useState(defaultSiteFilter);
  const [classificationSite, setClassificationSite] = useState(defaultSiteFilter);
  const [classificationStatus, setClassificationStatus] = useState("pending");
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [modal, setModal] = useState<ModalState>(null);
  const [internalDeleteTarget, setInternalDeleteTarget] = useState<InternalUser | null>(null);
  const [saving, setSaving] = useState(false);
  const [redemptionCodes, setRedemptionCodes] = useState<string[] | null>(null);
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
  operationsTab.current = tab;
  redemptionCanWrite.current = canWrite;

  const queryIsValid = query.range !== "custom" || Boolean(query.startAt && query.endAt && new Date(query.startAt) < new Date(query.endAt));
  const effectiveQuery = useMemo(
    () => ({ ...query, siteId: normalizeSiteFilter(query.siteId) }),
    [query, allowedSiteKey],
  );
  const effectiveInternalSite = normalizeSiteFilter(internalSite);
  const effectiveCreditSite = normalizeSiteFilter(creditSite);
  redemptionSiteId.current = effectiveCreditSite;
  const effectiveClassificationSite = normalizeSiteFilter(classificationSite);
  const operationsQuery = useMemo(
    () => queryIsValid ? buildOperationsQuery(effectiveQuery) : "",
    [effectiveQuery, queryIsValid],
  );
  operationsQueryRef.current = operationsQuery;

  useEffect(() => {
    setQuery({ siteId: defaultSiteFilter, segment: DEFAULT_OPERATIONS_SEGMENT, range: "7d" });
    setInternalSite(defaultSiteFilter);
    setCreditSite(defaultSiteFilter);
    setClassificationSite(defaultSiteFilter);
    setClassificationStatus("pending");
    setInternalSearch("");
    setOverview(null);
    setLifecycle(null);
    setTrends([]);
    setSyncStatuses([]);
    setInternalUsers([]);
    setRates([]);
    setClassificationTasks([]);
    setRedemptionList({ items: [], total: 0, page: 1, page_size: 20, pages: 1, truncated: false });
    setRedemptionStatus("");
    setRedemptionOrigin("");
    setRedemptionSearchDraft("");
    setRedemptionSearch("");
    setRedemptionPage(1);
    setRevealedRedemption(null);
    setLoadError("");
    setModal(null);
    setRedemptionCodes(null);
    setInternalDeleteTarget(null);
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
    const requestQuery = operationsQuery;
    const requestId = ++overviewRequestId.current;
    const isCurrentRequest = () => shouldApplyOperationsOverviewResponse(
      requestId,
      overviewRequestId.current,
      requestQuery,
      operationsQueryRef.current,
    );
    if (!background) setLoading(true);
    setLoadError("");
    try {
      const [summary, trendData, lifecycleData, syncData] = await Promise.all([
        api<OverviewResponse>(`/operations/summary${operationsQuery}`, token),
        api<ListResponse<TrendItem>>(`/operations/trends${operationsQuery}`, token),
        api<LifecycleResponse>(`/operations/lifecycle${operationsQuery}`, token),
        api<ListResponse<SyncStatus>>("/operations/sync-status", token),
      ]);
      if (!isCurrentRequest()) return;
      setOverview(summary);
      setTrends(trendData.items);
      setLifecycle(lifecycleData);
      setSyncStatuses(syncData.items);
    } catch (error) {
      if (!isCurrentRequest()) return;
      const message = errorMessage(error);
      setLoadError(message);
      if (background) showToast(message, true);
    } finally {
      if (!background && requestId === overviewRequestId.current) setLoading(false);
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

  async function loadRedemptionCodes(background = false) {
    if (!hasSiteAccess || !effectiveCreditSite) return;
    const requestSiteId = effectiveCreditSite;
    const requestId = ++redemptionRequestId.current;
    if (!background) setRedemptionLoading(true);
    const request: Record<string, string | number> = {
      site_id: effectiveCreditSite,
      page: redemptionPage,
      page_size: 20,
    };
    if (redemptionStatus) request.status = redemptionStatus;
    if (redemptionOrigin) request.origin = redemptionOrigin;
    if (redemptionSearch) request.search = redemptionSearch;
    try {
      const data = await api<RedemptionCodeListResponse>("/operations/redemption-codes/query", token, {
        method: "POST",
        body: JSON.stringify(request),
      });
      if (!shouldApplyRedemptionResponse(
        requestId,
        redemptionRequestId.current,
        requestSiteId,
        redemptionSiteId.current,
      )) return;
      setRedemptionList(data);
    } catch (error) {
      if (!shouldApplyRedemptionResponse(
        requestId,
        redemptionRequestId.current,
        requestSiteId,
        redemptionSiteId.current,
      )) return;
      const message = errorMessage(error);
      setLoadError(message);
      if (background) showToast(message, true);
    } finally {
      if (!background && requestId === redemptionRequestId.current) setRedemptionLoading(false);
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

  const autoRefreshBusy = loading
    || refreshing
    || saving
    || modal !== null
    || internalDeleteTarget !== null
    || redemptionCodes !== null
    || revealedRedemption !== null;

  usePageAutoRefresh(
    () => loadOverview(true),
    {
      enabled: shouldAutoRefreshOperationsOverview({
        tab,
        hasSiteAccess,
        queryIsValid,
        busy: autoRefreshBusy,
      }),
    },
  );

  useEffect(() => {
    if (!hasSiteAccess) return;
    if (tab === "overview") void loadOverview();
    if (tab === "internal-users") void loadInternalUsers();
    if (tab === "credits") {
      void loadRates();
      void loadRedemptionCodes();
    }
    if (tab === "classification") void loadClassification();
  }, [tab, operationsQuery, effectiveInternalSite, effectiveCreditSite, effectiveClassificationSite, classificationStatus, redemptionStatus, redemptionOrigin, redemptionSearch, redemptionPage, token, hasSiteAccess]);

  useEffect(() => {
    if (tab === "credits") return;
    redemptionRevealRequestId.current += 1;
    setRevealedRedemption(null);
  }, [tab]);

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

  async function deleteInternalUser() {
    const target = internalDeleteTarget;
    if (!canWrite || !target || !allowedSiteSet.has(target.site_id as OperationsSiteId)) return;
    try {
      await api(`/operations/internal-users/${target.internal_user_id}`, token, {
        method: "DELETE",
      });
      setInternalDeleteTarget(null);
      showToast("内部人员配置已删除，历史运营数据已重新计算");
      await loadInternalUsers(true);
    } catch (error) {
      showToast(errorMessage(error), true);
    }
  }

  async function saveRedemption() {
    if (!canWrite || !allowedSiteSet.has(redemptionForm.site_id as OperationsSiteId)) return;
    setSaving(true);
    try {
      const result = await api<{ codes?: string[] }>("/operations/redemption-batches", token, { method: "POST", body: JSON.stringify(buildRedemptionPayload(redemptionForm, idempotencyKey("redemption"))) });
      setModal(null);
      if (result.codes?.length) {
        setRedemptionCodes(result.codes);
        await loadRedemptionCodes(true);
      } else {
        showToast("该幂等批次已处理，明文兑换码不会再次显示", true);
      }
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  }

  async function revealRedemptionCode(row: RedemptionCodeRow) {
    if (!canWrite || !allowedSiteSet.has(row.site_id as OperationsSiteId)) return;
    const requestId = ++redemptionRevealRequestId.current;
    const requestSiteId = row.site_id;
    try {
      const result = await api<RevealedRedemptionCode>(
        `/operations/redemption-codes/${encodeURIComponent(row.site_id)}/${row.id}/reveal`,
        token,
      );
      if (!shouldApplyRedemptionReveal(
        requestId,
        redemptionRevealRequestId.current,
        requestSiteId,
        redemptionSiteId.current,
        operationsTab.current,
        redemptionCanWrite.current,
      )) return;
      setRevealedRedemption(result);
    } catch (error) {
      if (requestId !== redemptionRevealRequestId.current) return;
      showToast(errorMessage(error), true);
    }
  }

  function copyRedemptionCodes() {
    if (!redemptionCodes?.length) return;
    void navigator.clipboard?.writeText(redemptionCodes.join("\n"));
    showToast("兑换码已复制");
  }

  function downloadRedemptionCodes() {
    if (!redemptionCodes?.length) return;
    const blob = new Blob([`${redemptionCodes.join("\n")}\n`], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `redemption-codes-${new Date().toISOString().slice(0, 10)}.txt`;
    anchor.click();
    URL.revokeObjectURL(url);
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
  const lifecycleSummary = lifecycle?.summary || {};
  const visibleRetention = sortNewestFirst(
    (lifecycle?.retention || []).filter((item) => (
      allowedSiteSet.has(item.site_id as OperationsSiteId)
    )),
    (item) => item.cohort_date,
  );
  const visibleLifecycleSites = (lifecycle?.site_breakdown || []).filter((item) => (
    item.site_id && allowedSiteSet.has(item.site_id as OperationsSiteId)
  ));
  const visibleModels = lifecycle?.model_breakdown || [];
  const visibleCustomers = (lifecycle?.customer_breakdown || []).filter((item) => (
    allowedSiteSet.has(item.site_id as OperationsSiteId)
  ));
  const d7RetentionRate = weightedRetentionRate(visibleRetention, 7);
  const visibleSyncStatuses = syncStatuses.filter((item) => (
    allowedSiteSet.has(item.site_id as OperationsSiteId)
    && (!effectiveQuery.siteId || item.site_id === effectiveQuery.siteId)
  ));
  const selectedSyncSiteIds = effectiveQuery.siteId
    ? [effectiveQuery.siteId]
    : allowedSites.map((item) => item.value);
  const latestOperationsAt = operationsDataWatermark(visibleSyncStatuses, selectedSyncSiteIds);
  const operationsWatermarkLabel = latestOperationsAt
    ? formatDateTime(latestOperationsAt)
    : "等待源数据同步";
  const visibleTrends = sortNewestFirst(
    trends.filter((item) => allowedSiteSet.has(item.site_id as OperationsSiteId)),
    (item) => item.bucket,
  );
  const visibleSiteBreakdown = (overview?.site_breakdown || []).filter((item) => (
    allowedSiteSet.has(item.site_id as OperationsSiteId)
  ));
  const visibleInternalUsers = internalUsers.filter((item) => allowedSiteSet.has(item.site_id as OperationsSiteId));
  const visibleRates = rates.filter((item) => allowedSiteSet.has(item.site_id as OperationsSiteId));
  const visibleClassificationTasks = classificationTasks.filter((item) => allowedSiteSet.has(item.site_id as OperationsSiteId));
  const syncHealth = visibleSyncStatuses.some((item) => item.health === "delayed" || item.health === "never")
    ? "delayed"
    : !latestOperationsAt || visibleSyncStatuses.some((item) => item.health === "running")
      ? "running"
      : "healthy";
  const syncErrorDetails = visibleSyncStatuses.filter((item) => item.error_message).map((item) => `${siteLabel(item.site_id)}：${item.error_message}`).join(" · ");
  const pageDescription = allowedSites.length === 1
    ? `查看 ${allowedSites[0].label} 的收入、消耗和用户构成`
    : "统一查看 AIWeLink 与 AIGCLink 的收入、消耗和用户构成";
  const billingDescription = allowedSites.length > 1
    ? "AIWeLink 按可核验现金，AIGCLink 按数据库调用标价"
    : hasAigclinkAccess
      ? "按数据库调用标价确认计费客户与收入"
      : "按可核验现金确认付费身份，订阅收入单独摊销";
  const showValueRankings = hasAigclinkAccess && effectiveQuery.siteId !== "aiwelink";
  const metricDefinitionSiteId = effectiveQuery.siteId;
  const pageBusy = loading || refreshing || redemptionLoading;

  return (
    <section
      aria-busy={pageBusy}
      className={`view operations-workspace-page ${pageBusy ? "is-loading" : "is-ready"}`}
    >
      <div aria-hidden="true" className={`data-sync-rail ${pageBusy ? "is-active" : ""}`} />
      <div className="topbar operations-page-head motion-section motion-delay-1">
        <div>
          <h2>运营管理</h2>
          <p>{pageDescription}</p>
          {tab === "overview" && <small className="operations-page-freshness">数据截至 {operationsWatermarkLabel}</small>}
        </div>
        {tab === "overview" && <button className="ghost" type="button" disabled={refreshing} onClick={refreshSources}>{refreshing ? "提交中..." : "刷新源数据"}</button>}
      </div>

      <div className="growth-workspace-tabs operations-tabs motion-section motion-delay-2" role="tablist" aria-label="运营管理页面">
        {([
          ["overview", "运营概览"],
          ["internal-users", "内部人员"],
          ["credits", "额度与兑换码"],
          ["classification", "待分类"],
        ] as const).map(([value, label]) => <button className={tab === value ? "active" : ""} role="tab" aria-selected={tab === value} type="button" onClick={() => setTab(value)} key={value}>{label}</button>)}
      </div>

      {loadError && <div className="operations-inline-error" role="alert"><strong>数据更新失败</strong><span>{loadError}</span><small>已加载的数据不会被清空</small></div>}

      <div className="operations-tab-stage" key={tab}>
      {tab === "overview" && (
        <div className="operations-overview-workspace">
          <WorkspaceRail
            label="运营概览页面索引"
            items={[
              { id: "operations-summary", label: "经营总览", count: "01" },
              { id: "operations-lifecycle", label: "生命周期", count: "02" },
              { id: "operations-billing", label: "付费分层", count: "03" },
              { id: "operations-cohort", label: "留存 Cohort", count: "04" },
              ...(showValueRankings ? [{ id: "operations-ranking", label: "价值排行", count: "05" }] : []),
              { id: "operations-trend", label: "运营趋势", count: showValueRankings ? "06" : "05" },
              { id: "operations-sites", label: "站点对比", count: showValueRankings ? "07" : "06" },
            ]}
            status={{
              title: syncHealth === "healthy" ? "数据同步正常" : syncHealth === "running" ? "正在同步" : "数据同步延迟",
              detail: `数据截至 ${operationsWatermarkLabel}`,
              tone: syncHealth === "healthy" ? "healthy" : syncHealth === "running" ? "muted" : "warning",
            }}
          />
          <div className="operations-tab-content operations-overview-main">
          <div className="operations-query-bar">
            <label><span>站点</span><SiteSelect sites={allowedSites} includeAll={showAllSites} value={effectiveQuery.siteId} onChange={(siteId) => setQuery({ ...query, siteId })} /></label>
            <label><span>用户群体</span><select value={query.segment} onChange={(event) => setQuery({ ...query, segment: event.target.value as UserSegment })}>{Object.entries(segmentLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label><span>统计周期</span><select value={query.range} onChange={(event) => setQuery({ ...query, range: event.target.value as OperationsRange })}>{Object.entries(rangeLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            {query.range === "custom" && <><label><span>开始时间</span><input type="datetime-local" value={query.startAt || ""} onChange={(event) => setQuery({ ...query, startAt: event.target.value })} /></label><label><span>结束时间</span><input type="datetime-local" value={query.endAt || ""} onChange={(event) => setQuery({ ...query, endAt: event.target.value })} /></label></>}
            <div className="operations-query-summary"><span>{rangeLabels[query.range]}</span><strong>{segmentLabels[query.segment]}</strong></div>
          </div>

          <div className={`operations-freshness-banner ${syncHealth}`}>
            <div><span className="operations-freshness-dot" /><strong>{syncHealth === "healthy" ? "数据同步正常" : syncHealth === "running" ? "正在同步" : "数据同步延迟"}</strong></div>
            <span>{syncErrorDetails || (!latestOperationsAt ? "等待所选站点完成源数据同步" : visibleSyncStatuses.map((item) => `${siteLabel(item.site_id)} ${formatDateTime(item.last_success_at)}`).join(" · "))}</span>
            <small>页面查询缓存 60 秒，源数据每 15 分钟同步</small>
          </div>

          <div id="operations-summary" className="operations-metric-grid" aria-busy={loading}>
            <Metric label="注册用户" value={formatNumber(summary.registered_user_count, 0)} previous={comparison(summary.registered_user_count, previous.registered_user_count)} siteId={metricDefinitionSiteId} />
            <Metric label="活跃用户" value={formatNumber(summary.active_user_count, 0)} previous={comparison(summary.active_user_count, previous.active_user_count)} siteId={metricDefinitionSiteId} />
            <Metric label="成功调用" value={formatNumber(summary.successful_call_count, 0)} previous={comparison(summary.successful_call_count, previous.successful_call_count)} siteId={metricDefinitionSiteId} />
            <Metric label="付费 / 计费用户" value={formatNumber(summary.payer_count, 0)} previous={comparison(summary.payer_count, previous.payer_count)} siteId={metricDefinitionSiteId} />
            <Metric label={operationsIncomeLabel(effectiveQuery.siteId)} value={formatCurrency(summary.gross_income_cny)} previous={comparison(summary.gross_income_cny, previous.gross_income_cny)} siteId={metricDefinitionSiteId} />
            <Metric label="退款" value={formatCurrency(summary.refund_cny)} previous={comparison(summary.refund_cny, previous.refund_cny)} siteId={metricDefinitionSiteId} />
          </div>

          <section id="operations-lifecycle" className="operations-lifecycle-band" aria-busy={loading}>
            <div className="operations-section-head"><div><h3>生命周期指标</h3><span>成熟用户按完整观察窗口计算</span></div></div>
            <div className="operations-lifecycle-grid">
              <Metric label="24 小时激活率" value={formatLifecycleRate(lifecycleSummary.activation_24h_rate)} previous={`${formatNumber(lifecycleSummary.activation_24h_numerator, 0)} / ${formatNumber(lifecycleSummary.activation_24h_denominator, 0)}`} siteId={metricDefinitionSiteId} />
              <Metric label="7 日激活率" value={formatLifecycleRate(lifecycleSummary.activation_7d_rate)} previous={`${formatNumber(lifecycleSummary.activation_7d_numerator, 0)} / ${formatNumber(lifecycleSummary.activation_7d_denominator, 0)}`} siteId={metricDefinitionSiteId} />
              <Metric label="D7 留存" value={formatLifecycleRate(d7RetentionRate)} previous="成熟注册 cohort" siteId={metricDefinitionSiteId} />
              <Metric label="活跃用户付费率" value={formatLifecycleRate(lifecycleSummary.active_payer_rate)} previous={`${formatNumber(lifecycleSummary.active_payer_numerator, 0)} / ${formatNumber(lifecycleSummary.active_payer_denominator, 0)}`} siteId={metricDefinitionSiteId} />
              <Metric label="本期付款率" value={formatLifecycleRate(lifecycleSummary.period_payer_rate)} previous={effectiveQuery.siteId === "aigclink" ? "不适用" : `${formatNumber(lifecycleSummary.period_payer_numerator, 0)} / ${formatNumber(lifecycleSummary.period_payer_denominator, 0)}`} siteId={metricDefinitionSiteId} />
              <Metric label="流失预警" value={formatNumber(lifecycleSummary.churn_warning_user_count, 0)} previous="14 至 30 天未调用" siteId={metricDefinitionSiteId} />
              <Metric label="使用流失" value={formatNumber(lifecycleSummary.churned_user_count, 0)} previous="30 天以上未调用" siteId={metricDefinitionSiteId} />
              <Metric label="回流用户" value={formatNumber(lifecycleSummary.returned_user_count, 0)} previous="间隔 30 天后再次调用" siteId={metricDefinitionSiteId} />
            </div>
          </section>

          <section id="operations-billing" className="operations-data-section operations-billing-section">
            <div className="operations-section-head"><div><h3>付费与计费分层</h3><span>{billingDescription}</span></div><span>{visibleLifecycleSites.length} 个站点</span></div>
            <div className="operations-table-scroll"><table><thead><tr><th>站点</th><th>商业模式</th><th>付费 / 计费客户</th><th>有效付费</th><th>活跃付费</th><th>本期付款</th><th>付费状态未知</th><th>充值次数</th><th>充值额度</th><th>现金收入</th><th>订阅现金收入</th><th>订阅摊销收入</th><th>调用计费收入</th></tr></thead><tbody>{visibleLifecycleSites.length ? visibleLifecycleSites.map((item) => <tr key={item.site_id || "unknown"}><td><strong>{siteLabel(item.site_id || "")}</strong></td><td><span className={`operations-status-tag ${item.site_id === "aigclink" ? "usage-billed" : "cash-billed"}`}>{item.site_id === "aigclink" ? "企业后付费" : "余额与订阅"}</span></td><td>{formatNumber(item.cumulative_payer_count, 0)}</td><td>{item.site_id === "aigclink" ? "--" : formatNumber(item.effective_payer_count, 0)}</td><td>{formatNumber(item.site_id === "aigclink" ? item.active_payer_numerator : item.active_cash_payer_count, 0)}</td><td>{item.site_id === "aigclink" ? "--" : formatNumber(item.period_payer_count, 0)}</td><td>{item.site_id === "aigclink" ? "--" : formatNumber(item.unknown_payer_count, 0)}</td><td>{item.site_id === "aigclink" ? "--" : formatNumber(item.recharge_event_count, 0)}</td><td>{item.site_id === "aigclink" ? "--" : formatNumber(item.recharge_balance_units, 2)}</td><td>{item.site_id === "aigclink" ? "--" : formatCurrency(item.cash_income_cny)}</td><td>{item.site_id === "aigclink" ? "--" : formatCurrency(item.subscription_cash_income_cny)}</td><td>{item.site_id === "aigclink" ? "--" : formatCurrency(item.subscription_amortized_income_cny)}</td><td>{item.site_id === "aigclink" ? formatCurrency(item.usage_billed_income_cny) : "--"}</td></tr>) : <EmptyRow columns={13} text={loading ? "正在加载付费分层..." : "当前周期暂无付费分层数据"} />}</tbody></table></div>
          </section>

          <section id="operations-cohort" className="operations-data-section operations-retention-section">
            <div className="operations-section-head"><div><h3>留存 Cohort</h3><span>按上海自然日统计，未成熟观察日显示 --</span></div><span>{visibleRetention.length} 个 cohort</span></div>
            <div className="operations-table-scroll"><table><thead><tr><th>注册日期</th><th>站点</th><th>注册人数</th><th>D1</th><th>D3</th><th>D7</th><th>D14</th><th>D30</th></tr></thead><tbody>{visibleRetention.length ? visibleRetention.map((item) => <tr key={`${item.site_id}-${item.cohort_date}`}><td>{item.cohort_date}</td><td>{siteLabel(item.site_id)}</td><td>{formatNumber(item.cohort_size, 0)}</td><RetentionCell value={{ numerator: item.d1_numerator, denominator: item.d1_denominator, rate: item.d1_rate }} /><RetentionCell value={{ numerator: item.d3_numerator, denominator: item.d3_denominator, rate: item.d3_rate }} /><RetentionCell value={{ numerator: item.d7_numerator, denominator: item.d7_denominator, rate: item.d7_rate }} /><RetentionCell value={{ numerator: item.d14_numerator, denominator: item.d14_denominator, rate: item.d14_rate }} /><RetentionCell value={{ numerator: item.d30_numerator, denominator: item.d30_denominator, rate: item.d30_rate }} /></tr>) : <EmptyRow columns={8} text={loading ? "正在加载留存数据..." : "当前周期暂无成熟 cohort"} />}</tbody></table></div>
          </section>

          {showValueRankings && <div id="operations-ranking" className="operations-value-grid">
            <section className="operations-data-section operations-model-ranking">
              <div className="operations-section-head"><div><h3>模型计费排行</h3><span>AIGCLink 数据库调用标价</span></div><span>前 {visibleModels.length} 项</span></div>
              <div className="operations-table-scroll"><table><thead><tr><th>模型</th><th>成功调用</th><th>Token</th><th>调用计费收入</th><th>收入占比</th></tr></thead><tbody>{visibleModels.length ? visibleModels.map((item) => <tr key={item.model_name}><td><strong>{item.model_name}</strong></td><td>{formatNumber(item.successful_call_count, 0)}</td><td>{formatNumber(item.token_count, 0)}</td><td>{formatCurrency(item.billed_amount_cny)}</td><td>{formatLifecycleRate(item.revenue_share)}</td></tr>) : <EmptyRow columns={5} text={loading ? "正在加载模型排行..." : "当前周期暂无模型计费数据"} />}</tbody></table></div>
            </section>
            <section className="operations-data-section operations-customer-ranking">
              <div className="operations-section-head"><div><h3>企业客户排行</h3><span>AIGCLink 高价值计费客户</span></div><span>前 {visibleCustomers.length} 项</span></div>
              <div className="operations-table-scroll"><table><thead><tr><th>客户</th><th>用户 ID</th><th>成功调用</th><th>Token</th><th>调用计费收入</th></tr></thead><tbody>{visibleCustomers.length ? visibleCustomers.map((item) => <tr key={`${item.site_id}-${item.external_user_id}`}><td><strong>{item.account_label || item.external_user_id}</strong></td><td>{item.external_user_id}</td><td>{formatNumber(item.successful_call_count, 0)}</td><td>{formatNumber(item.token_count, 0)}</td><td>{formatCurrency(item.billed_amount_cny)}</td></tr>) : <EmptyRow columns={5} text={loading ? "正在加载客户排行..." : "当前周期暂无企业计费客户"} />}</tbody></table></div>
            </section>
          </div>}

          <div className="operations-overview-table-stack">
            <section id="operations-trend" className="operations-data-section operations-trend-section">
              <div className="operations-section-head"><div><h3>运营趋势</h3><span>48 小时内按小时，更长周期按天汇总</span></div></div>
              <div className="operations-table-scroll"><table><thead><tr><th>时间</th><th>站点</th><th>注册</th><th>活跃</th><th>成功调用</th><th>消耗额度</th><th>付费用户</th><th>{operationsIncomeLabel(effectiveQuery.siteId)}</th><th>退款</th></tr></thead><tbody>{visibleTrends.length ? visibleTrends.map((item, index) => <tr key={`${item.site_id}-${item.bucket}-${index}`}><td>{formatBucket(item.bucket)}</td><td>{siteLabel(item.site_id)}</td><td>{formatNumber(item.registered_user_count, 0)}</td><td>{formatNumber(item.active_user_count, 0)}</td><td>{formatNumber(item.successful_call_count, 0)}</td><td>{formatNumber(item.consumed_balance_units, 2)}</td><td>{formatNumber(item.payer_count, 0)}</td><td>{formatCurrency(item.gross_income_cny)}<small className="operations-cell-subtext">{operationsIncomeLabel(item.site_id)}</small></td><td>{formatCurrency(item.refund_cny)}</td></tr>) : <EmptyRow columns={9} text={loading ? "正在加载趋势..." : "当前周期暂无趋势数据"} />}</tbody></table></div>
            </section>

            <section id="operations-sites" className="operations-data-section operations-site-comparison">
              <div className="operations-section-head"><div><h3>站点运营对比</h3><span>按当前查询周期和用户群体汇总</span></div><span>{visibleSiteBreakdown.length} 个站点</span></div>
              <div className="operations-table-scroll"><table><thead><tr><th>站点</th><th>注册用户</th><th>活跃用户</th><th>成功调用</th><th>消耗额度</th><th>付费用户</th><th>{operationsIncomeLabel(effectiveQuery.siteId)}</th><th>退款</th><th>人均消耗</th><th>付费率</th></tr></thead><tbody>{visibleSiteBreakdown.length ? visibleSiteBreakdown.map((item) => <tr key={item.site_id}><td><strong>{siteLabel(item.site_id)}</strong></td><td>{formatNumber(item.registered_user_count, 0)}</td><td>{formatNumber(item.active_user_count, 0)}</td><td>{formatNumber(item.successful_call_count, 0)}</td><td>{formatNumber(item.consumed_balance_units, 2)}</td><td>{formatNumber(item.payer_count, 0)}</td><td>{formatCurrency(item.gross_income_cny)}<small className="operations-cell-subtext">{operationsIncomeLabel(item.site_id)}</small></td><td>{formatCurrency(item.refund_cny)}</td><td>{formatNumber(averageConsumption(item), 2)}</td><td>{paymentRate(item).toFixed(1)}%</td></tr>) : <EmptyRow columns={10} text={loading ? "正在加载站点对比..." : "当前周期暂无站点汇总"} />}</tbody></table></div>
            </section>
          </div>
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
            <div className="operations-table-scroll"><table><thead><tr><th>站点</th><th>邮箱</th><th>识别状态</th><th>业务用户 ID</th><th>识别时间</th><th>标记原因</th><th>生效时间</th><th>失效时间</th>{canWrite && <th>操作</th>}</tr></thead><tbody>{visibleInternalUsers.length ? visibleInternalUsers.map((item) => <tr key={item.internal_user_id}><td>{siteLabel(item.site_id)}</td><td><strong>{item.email}</strong></td><td><span className={`operations-status-tag ${item.recognition_status}`}>{recognitionStatusLabel(item.recognition_status)}</span></td><td>{item.external_user_id || "-"}</td><td>{formatDateTime(item.recognized_at)}</td><td>{item.reason || "-"}</td><td>{formatDateTime(item.active_from)}</td><td>{formatDateTime(item.active_until)}</td>{canWrite && <td><InternalUserActionButtons item={item} onEdit={openInternal} onDelete={setInternalDeleteTarget} /></td>}</tr>) : <EmptyRow columns={canWrite ? 9 : 8} text={loading ? "正在加载..." : "暂无内部人员配置"} />}</tbody></table></div>
          </section>
        </div>
      )}

      {tab === "credits" && (
        <div className="operations-tab-content">
          <div className="operations-query-bar operations-list-query">
            <label><span>站点</span><SiteSelect sites={allowedSites} includeAll={false} value={effectiveCreditSite} onChange={(value) => { setCreditSite(value); setRedemptionPage(1); setRevealedRedemption(null); }} /></label>
            <label><span>兑换码状态</span><select value={redemptionStatus} onChange={(event) => { setRedemptionStatus(event.target.value); setRedemptionPage(1); }}><option value="">全部状态</option><option value="unused">未使用</option><option value="used">已使用</option><option value="expired">已过期</option><option value="disabled">已禁用</option></select></label>
            <label><span>创建来源</span><select value={redemptionOrigin} onChange={(event) => { setRedemptionOrigin(event.target.value); setRedemptionPage(1); }}><option value="">全部来源</option><option value="management_panel">管理面板创建</option><option value="api_site">API站点创建</option></select></label>
            <label className="operations-search-field"><span>兑换码或使用账号</span><input value={redemptionSearchDraft} onChange={(event) => setRedemptionSearchDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { setRedemptionSearch(redemptionSearchDraft.trim()); setRedemptionPage(1); } }} placeholder="掩码、兑换码或账号" /></label>
            <button className="ghost" type="button" onClick={() => { setRedemptionSearch(redemptionSearchDraft.trim()); setRedemptionPage(1); }} disabled={redemptionLoading}>查询</button>
            {canWrite && <div className="operations-command-group"><button type="button" onClick={() => { setRedemptionForm({ ...emptyRedemptionForm, site_id: effectiveCreditSite }); setModal({ kind: "redemption" }); }}>生成兑换码</button><button className="ghost" type="button" onClick={() => { setAdjustmentForm({ ...emptyAdjustmentForm, site_id: effectiveCreditSite }); setModal({ kind: "adjustment" }); }}>调整余额</button><button className="ghost" type="button" onClick={() => { setConversionForm({ ...emptyConversionForm, site_id: effectiveCreditSite, balance_units_per_cny: effectiveCreditSite === "aigclink" ? "1" : "10" }); setModal({ kind: "rate" }); }}>新增换算比例</button></div>}
          </div>
          <section className="operations-data-section">
            <div className="operations-section-head"><div><h3>兑换码列表</h3><span>当前账号创建的兑换码优先，其余按创建时间从新到旧排列</span></div></div>
            {redemptionList.truncated && <div className="operations-readonly-note">API 站点兑换码超过 10000 条，当前查询仅展示最新数据。请增加状态或关键词筛选。</div>}
            <RedemptionCodeTable canDelete={supportsSafeRedemptionDeletion} canWrite={canWrite} loading={redemptionLoading} onDelete={() => undefined} onPageChange={setRedemptionPage} onReveal={revealRedemptionCode} onSelectionChange={() => undefined} page={redemptionList.page || redemptionPage} pages={redemptionList.pages || 1} rows={redemptionList.items} selectedIds={new Set()} total={redemptionList.total} />
          </section>
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
      </div>

      {modal?.kind === "internal" && <GrowthCreateModal title={modal.item ? "编辑内部人员" : "添加内部人员"} submitLabel={modal.item ? "保存修改" : "确认添加"} saving={saving} submitDisabled={!internalForm.site_id || !internalForm.email.trim()} onClose={() => setModal(null)} onSubmit={saveInternal}><div className="growth-form-grid operations-modal-grid"><label><span className="field-label"><strong>站点</strong></span><SiteSelect sites={allowedSites} includeAll={false} value={internalForm.site_id} onChange={(site_id) => setInternalForm({ ...internalForm, site_id })} /></label><label><span className="field-label"><strong>邮箱</strong></span><input type="email" autoComplete="off" value={internalForm.email} onChange={(event) => setInternalForm({ ...internalForm, email: event.target.value })} required /></label><label className="span-2"><span className="field-label"><strong>标记原因</strong><span>（可选）</span></span><input value={internalForm.reason} onChange={(event) => setInternalForm({ ...internalForm, reason: event.target.value })} /></label><label><span className="field-label"><strong>生效时间</strong></span><input type="datetime-local" value={internalForm.active_from} onChange={(event) => setInternalForm({ ...internalForm, active_from: event.target.value })} /></label><label><span className="field-label"><strong>失效时间</strong><span>（可选）</span></span><input type="datetime-local" value={internalForm.active_until} onChange={(event) => setInternalForm({ ...internalForm, active_until: event.target.value })} /></label></div></GrowthCreateModal>}

      {modal?.kind === "redemption" && <GrowthCreateModal title="生成兑换码" submitLabel="生成兑换码" saving={saving} submitDisabled={redemptionSubmitDisabled(redemptionForm)} onClose={() => setModal(null)} onSubmit={saveRedemption}><div className="growth-form-grid operations-modal-grid"><label><span className="field-label"><strong>站点</strong></span><SiteSelect sites={allowedSites} includeAll={false} value={redemptionForm.site_id} onChange={(site_id) => setRedemptionForm({ ...redemptionForm, site_id })} /></label><PurposeFields purpose={redemptionForm.purpose} cash={redemptionForm.cash_amount_cny} onPurpose={(purpose) => setRedemptionForm({ ...redemptionForm, purpose, cash_amount_cny: purpose === "sale" ? redemptionForm.cash_amount_cny : "0" })} onCash={(cash_amount_cny) => setRedemptionForm({ ...redemptionForm, cash_amount_cny })} /><label><span className="field-label"><strong>兑换码数量</strong></span><input type="number" min="1" max="10000" value={redemptionForm.code_count} onChange={(event) => setRedemptionForm({ ...redemptionForm, code_count: event.target.value })} /></label><label><span className="field-label"><strong>每个兑换码额度</strong></span><input type="number" min="0" step="any" value={redemptionForm.balance_units_per_code} onChange={(event) => setRedemptionForm({ ...redemptionForm, balance_units_per_code: event.target.value })} /></label><label className="span-2"><span className="field-label"><strong>备注</strong><span>（可选）</span></span><textarea value={redemptionForm.note} onChange={(event) => setRedemptionForm({ ...redemptionForm, note: event.target.value })} /></label></div></GrowthCreateModal>}

      {redemptionCodes && <div className="operations-redemption-result-backdrop"><RedemptionResultPanel codes={redemptionCodes} onClose={() => setRedemptionCodes(null)} onCopy={copyRedemptionCodes} onDownload={downloadRedemptionCodes} /></div>}

      {revealedRedemption && <div className="operations-redemption-result-backdrop"><div className="operations-redemption-result" role="dialog" aria-modal="true" aria-labelledby="operations-reveal-title"><div className="operations-redemption-result-header"><div><span className="operations-eyebrow">临时查看</span><h3 id="operations-reveal-title">兑换码明文</h3></div><button className="ghost icon-button" type="button" aria-label="关闭兑换码明文" onClick={() => setRevealedRedemption(null)}>×</button></div><p className="operations-redemption-result-note">关闭后将立即清除本次明文，不会保存到管理面板数据库。</p><input aria-label="兑换码明文" className="operations-revealed-code" readOnly value={revealedRedemption.code} /><div className="operations-redemption-result-actions"><button type="button" onClick={() => { void navigator.clipboard?.writeText(revealedRedemption.code); showToast("兑换码已复制"); }}>复制</button><button className="ghost" type="button" onClick={() => setRevealedRedemption(null)}>关闭</button></div></div></div>}

      {modal?.kind === "adjustment" && <GrowthCreateModal title="调整余额" submitLabel="提交调整" saving={saving} submitDisabled={adjustmentSubmitDisabled(adjustmentForm)} onClose={() => setModal(null)} onSubmit={saveAdjustment}><div className="growth-form-grid operations-modal-grid"><label><span className="field-label"><strong>站点</strong></span><SiteSelect sites={allowedSites} includeAll={false} value={adjustmentForm.site_id} onChange={(site_id) => setAdjustmentForm({ ...adjustmentForm, site_id })} /></label><label><span className="field-label"><strong>业务用户 ID</strong></span><input value={adjustmentForm.external_user_id} onChange={(event) => setAdjustmentForm({ ...adjustmentForm, external_user_id: event.target.value })} /></label><PurposeFields purpose={adjustmentForm.purpose} cash={adjustmentForm.cash_amount_cny} onPurpose={(purpose) => setAdjustmentForm({ ...adjustmentForm, purpose, cash_amount_cny: purpose === "sale" ? adjustmentForm.cash_amount_cny : "0" })} onCash={(cash_amount_cny) => setAdjustmentForm({ ...adjustmentForm, cash_amount_cny })} /><label><span className="field-label"><strong>调整额度</strong></span><input type="number" step="any" value={adjustmentForm.balance_units} onChange={(event) => setAdjustmentForm({ ...adjustmentForm, balance_units: event.target.value })} /><span className="growth-field-message is-muted">增加填正数，扣减填负数</span></label><label className="span-2"><span className="field-label"><strong>备注</strong><span>（可选）</span></span><textarea value={adjustmentForm.note} onChange={(event) => setAdjustmentForm({ ...adjustmentForm, note: event.target.value })} /></label></div></GrowthCreateModal>}

      {modal?.kind === "rate" && <GrowthCreateModal title="新增换算比例" submitLabel="确认生效" saving={saving} submitDisabled={!conversionForm.site_id || Number(conversionForm.balance_units_per_cny) <= 0} onClose={() => setModal(null)} onSubmit={saveRate}><div className="growth-form-grid operations-modal-grid"><label><span className="field-label"><strong>站点</strong></span><SiteSelect sites={allowedSites} includeAll={false} value={conversionForm.site_id} onChange={(site_id) => setConversionForm({ ...conversionForm, site_id, balance_units_per_cny: site_id === "aigclink" ? "1" : "10" })} /></label><label><span className="field-label"><strong>每 1 CNY 对应余额</strong></span><input type="number" min="0" step="any" value={conversionForm.balance_units_per_cny} onChange={(event) => setConversionForm({ ...conversionForm, balance_units_per_cny: event.target.value })} /></label><label><span className="field-label"><strong>生效时间</strong><span>（可选）</span></span><input type="datetime-local" value={conversionForm.effective_from} onChange={(event) => setConversionForm({ ...conversionForm, effective_from: event.target.value })} /><span className="growth-field-message is-muted">{conversionRateEffectiveHint}</span></label><label className="span-2"><span className="field-label"><strong>备注</strong><span>（可选）</span></span><textarea value={conversionForm.note} onChange={(event) => setConversionForm({ ...conversionForm, note: event.target.value })} /></label></div></GrowthCreateModal>}

      {modal?.kind === "classification" && <GrowthCreateModal title="补录额度用途" submitLabel={classificationForm.status === "ignored" ? "确认忽略" : "完成分类"} saving={saving} submitDisabled={false} onClose={() => setModal(null)} onSubmit={saveClassification}><div className="operations-classification-context"><div><span>站点</span><strong>{siteLabel(modal.item.site_id)}</strong></div><div><span>业务用户 ID</span><strong>{modal.item.external_user_id}</strong></div><div><span>来源类型</span><strong>{modal.item.source_type}</strong></div><div><span>额度</span><strong>{formatNumber(modal.item.balance_units, 10)}</strong></div></div><div className="growth-form-grid operations-modal-grid"><label><span className="field-label"><strong>处理方式</strong></span><select value={classificationForm.status} onChange={(event) => setClassificationForm({ ...classificationForm, status: event.target.value as "resolved" | "ignored" })}><option value="resolved">完成分类</option><option value="ignored">忽略记录</option></select></label>{classificationForm.status === "resolved" && <PurposeFields purpose={classificationForm.purpose} cash={classificationForm.cash_amount_cny} onPurpose={(purpose) => setClassificationForm({ ...classificationForm, purpose, cash_amount_cny: purpose === "sale" ? classificationForm.cash_amount_cny : "0" })} onCash={(cash_amount_cny) => setClassificationForm({ ...classificationForm, cash_amount_cny })} />}<label className="span-2"><span className="field-label"><strong>补录说明</strong><span>（可选）</span></span><textarea value={classificationForm.note} onChange={(event) => setClassificationForm({ ...classificationForm, note: event.target.value })} /></label></div></GrowthCreateModal>}

      <ConfirmDialog
        confirmText="删除内部人员"
        details={internalDeleteTarget ? internalUserDeleteDetails(internalDeleteTarget) : []}
        message="删除将撤销该账号的内部身份，并重新计算全部历史运营数据。该操作仅用于纠正误添加。"
        onCancel={() => setInternalDeleteTarget(null)}
        onConfirm={deleteInternalUser}
        open={Boolean(internalDeleteTarget)}
        title="确认删除内部人员"
        tone="danger"
      />
    </section>
  );
}

export default OperationsManagementPage;
