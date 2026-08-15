import { useEffect, useState } from "react";

import { api } from "../../api/client";
import {
  MetricDefinition,
  type MetricDefinitionDetails,
} from "../../components/dataWorkspace/MetricDefinition";
import { WorkspaceRail } from "../../components/dataWorkspace/WorkspaceRail";
import { errorMessage } from "../../utils/format";


export type TrafficRange = "24h" | "7d" | "30d" | "90d";
export type TrafficUserSegment = "ordinary" | "internal" | "all";
export type TrafficSourceKind = "" | "promotion" | "direct" | "organic_search" | "referral";
export type TrafficCapability = "available" | "unavailable" | "delayed" | "error";
export type TrafficFactState = "normal" | "excluded" | "facts_pending";

export type TrafficOverviewFilters = {
  range: TrafficRange;
  segment: TrafficUserSegment;
  siteId: string;
  sourceKind: TrafficSourceKind;
  channelId: string;
  campaignId: string;
  trackingLinkId: string;
};

export type TrafficSummary = {
  recorded_visits: number;
  counted_pv: number;
  session_uv: number;
  excluded_visits: number;
  latest_event_at: string | null;
};

export type TrafficSourceBreakdown = {
  source_kind: Exclude<TrafficSourceKind, "">;
  counted_pv: number;
  session_uv: number;
};

export type TrafficTrend = {
  bucket_at: string;
  homepage_pv: number;
  homepage_uv: number;
  link_pv: number;
  link_uv: number;
};

export type TrafficLinkPerformance = {
  tracking_link_id: string;
  site_id: string;
  code: string;
  source_name: string;
  status: "active" | "paused" | "archived";
  valid_from: string | null;
  valid_until: string | null;
  campaign_id: string;
  campaign_name: string;
  channel_id: string;
  channel_name: string;
  recorded_visits: number;
  counted_pv: number;
  session_uv: number;
  excluded_visits: number;
  attribution_updates: number;
  registered_accounts: number;
};

export type TrafficOverviewResponse = {
  generated_at: string;
  window: {
    range: TrafficRange;
    start_at: string;
    end_at: string;
    bucket: "hour" | "day";
    timezone: string;
  };
  capabilities: {
    homepage_traffic: TrafficCapability;
    link_traffic: TrafficCapability;
    registration_attribution: TrafficCapability;
    downstream_facts: TrafficCapability;
  };
  homepage_summary: TrafficSummary & { valid_rate: number | null };
  link_summary: TrafficSummary & { attribution_updates: number };
  registration_summary: {
    attributed_accounts: number;
    excluded_accounts: number;
    facts_pending_accounts: number;
  };
  traffic_trends: TrafficTrend[];
  active_source_breakdown: TrafficSourceBreakdown[];
  classified_source_breakdown: TrafficSourceBreakdown[];
  link_performance: TrafficLinkPerformance[];
  quality: {
    exclusion_reasons: Array<{
      event_scope: "homepage" | "link";
      reason: string;
      event_count: number;
    }>;
    homepage_bot_visits: number;
    link_bot_visits: number;
    redirect_results: Array<{ redirect_result: string; event_count: number }>;
    http_statuses: Array<{ http_status: number; event_count: number }>;
    facts_pending_accounts: number;
    latest_source_data_fresh_at: string | null;
    latest_computed_at: string | null;
    facts_delay_seconds: number | null;
  };
};

export type TrafficUser = {
  public_user_id: string;
  site_id: string;
  external_user_id: string;
  account_label: string | null;
  is_internal: boolean;
  source_kind: Exclude<TrafficSourceKind, "">;
  tracking_link_id: string | null;
  source_name: string | null;
  campaign_id: string | null;
  campaign_name: string | null;
  channel_id: string | null;
  channel_name: string | null;
  registered_at: string;
  attributed_at: string;
  attribution_method: string;
  fact_state: TrafficFactState;
  source_touch_at: string | null;
};

export type TrafficUsersResponse = {
  generated_at: string;
  items: TrafficUser[];
  total: number;
  limit: number;
  offset: number;
};

export type TrafficSiteOption = { site_id: string; site_name: string };
export type TrafficChannelOption = { channel_id: string; name: string };
export type TrafficCampaignOption = {
  campaign_id: string;
  site_id: string;
  channel_id: string;
  name: string;
};
export type TrafficLinkOption = {
  tracking_link_id: string;
  site_id: string;
  campaign_id: string;
  channel_id: string;
  code: string;
  source_name: string;
};

type QueryOptions = { limit?: number; offset?: number };

export const defaultTrafficOverviewFilters: TrafficOverviewFilters = {
  range: "7d",
  segment: "ordinary",
  siteId: "",
  sourceKind: "",
  channelId: "",
  campaignId: "",
  trackingLinkId: "",
};

export function buildTrafficAnalyticsQuery(
  filters: TrafficOverviewFilters,
  options: QueryOptions = {},
) {
  const query = new URLSearchParams();
  query.set("range", filters.range);
  query.set("segment", filters.segment);
  if (filters.siteId) query.set("site_id", filters.siteId);
  if (filters.sourceKind) query.set("source_kind", filters.sourceKind);
  if (filters.channelId) query.set("channel_id", filters.channelId);
  if (filters.campaignId) query.set("campaign_id", filters.campaignId);
  if (filters.trackingLinkId) query.set("tracking_link_id", filters.trackingLinkId);
  if (options.limit !== undefined) query.set("limit", String(options.limit));
  if (options.offset !== undefined) query.set("offset", String(options.offset));
  return `?${query.toString()}`;
}

export type TrafficOverviewFilterUpdate = "none" | "users-page" | "filters";

export function decideTrafficOverviewFilterUpdate(
  current: TrafficOverviewFilters,
  next: TrafficOverviewFilters,
  usersOffset: number,
): TrafficOverviewFilterUpdate {
  if (buildTrafficAnalyticsQuery(next) !== buildTrafficAnalyticsQuery(current)) return "filters";
  return usersOffset > 0 ? "users-page" : "none";
}

type TrafficOverviewProps = {
  token: string;
  sites: TrafficSiteOption[];
  channels: TrafficChannelOption[];
  campaigns: TrafficCampaignOption[];
  trackingLinks: TrafficLinkOption[];
  showToast: (message: string, isError?: boolean) => void;
};

export function TrafficOverview(props: TrafficOverviewProps) {
  const [filters, setFilters] = useState<TrafficOverviewFilters>(defaultTrafficOverviewFilters);
  const [overview, setOverview] = useState<TrafficOverviewResponse | null>(null);
  const [users, setUsers] = useState<TrafficUsersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [usersLoading, setUsersLoading] = useState(true);
  const [error, setError] = useState("");
  const [usersError, setUsersError] = useState("");
  const [usersOffset, setUsersOffset] = useState(0);
  const [reloadVersion, setReloadVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setOverview(null);
    setLoading(true);
    setError("");
    void api<TrafficOverviewResponse>(
      `/growth/analytics/overview${buildTrafficAnalyticsQuery(filters)}`,
      props.token,
      { signal: controller.signal },
    ).then(setOverview).catch((requestError) => {
      if (!controller.signal.aborted) setError(errorMessage(requestError));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [
    props.token,
    filters.range,
    filters.segment,
    filters.siteId,
    filters.sourceKind,
    filters.channelId,
    filters.campaignId,
    filters.trackingLinkId,
    reloadVersion,
  ]);

  useEffect(() => {
    const controller = new AbortController();
    setUsers(null);
    setUsersLoading(true);
    setUsersError("");
    void api<TrafficUsersResponse>(
      `/growth/analytics/users${buildTrafficAnalyticsQuery(filters, {
        limit: 50,
        offset: usersOffset,
      })}`,
      props.token,
      { signal: controller.signal },
    ).then(setUsers).catch((requestError) => {
      if (!controller.signal.aborted) setUsersError(errorMessage(requestError));
    }).finally(() => {
      if (!controller.signal.aborted) setUsersLoading(false);
    });
    return () => controller.abort();
  }, [
    props.token,
    filters.range,
    filters.segment,
    filters.siteId,
    filters.sourceKind,
    filters.channelId,
    filters.campaignId,
    filters.trackingLinkId,
    usersOffset,
    reloadVersion,
  ]);

  const updateFilters = (next: TrafficOverviewFilters) => {
    const update = decideTrafficOverviewFilterUpdate(filters, next, usersOffset);
    if (update === "none") return;
    setUsers(null);
    setUsersLoading(true);
    setUsersError("");
    setUsersOffset(0);
    if (update === "users-page") return;
    setOverview(null);
    setLoading(true);
    setError("");
    setFilters(next);
  };

  return (
    <TrafficOverviewView
      {...props}
      overview={overview}
      users={users}
      filters={filters}
      loading={loading}
      usersLoading={usersLoading}
      error={error}
      usersError={usersError}
      onFiltersChange={updateFilters}
      onRetry={() => {
        setOverview(null);
        setUsers(null);
        setLoading(true);
        setUsersLoading(true);
        setError("");
        setUsersError("");
        setReloadVersion((version) => version + 1);
      }}
      onUsersPage={(offset) => {
        if (offset === usersOffset) return;
        setUsers(null);
        setUsersLoading(true);
        setUsersError("");
        setUsersOffset(offset);
      }}
    />
  );
}

export type TrafficOverviewViewProps = Omit<TrafficOverviewProps, "token" | "showToast"> & {
  overview: TrafficOverviewResponse | null;
  users: TrafficUsersResponse | null;
  filters: TrafficOverviewFilters;
  loading: boolean;
  usersLoading: boolean;
  error: string;
  usersError: string;
  onFiltersChange: (filters: TrafficOverviewFilters) => void;
  onRetry: () => void;
  onUsersPage: (offset: number) => void;
};

const sourceLabels: Record<Exclude<TrafficSourceKind, "">, string> = {
  promotion: "推广链接",
  direct: "直接访问",
  organic_search: "自然搜索",
  referral: "外站引荐",
};

const factStateLabels: Record<TrafficFactState, string> = {
  normal: "事实正常",
  excluded: "已排除",
  facts_pending: "同步待确认",
};

const linkStatusLabels: Record<TrafficLinkPerformance["status"], string> = {
  active: "启用",
  paused: "暂停",
  archived: "归档",
};

const trafficMetricDefinitions: Record<string, MetricDefinitionDetails> = {
  "有效主页 PV": {
    definition: "所选窗口内通过有效性校验的主页浏览事件总数。",
    formula: "主页访问事件 - 机器人 - 内部测试 - 无效事件",
    included: "普通账号与匿名 Session 的有效主页事件",
    excluded: "机器人、内部测试、重复或无效上报",
    source: "traffic_homepage_events",
    freshness: "源数据 15 分钟同步，页面缓存 60 秒",
  },
  "有效 Session UV": {
    definition: "至少产生一次有效主页访问的匿名浏览器 Session 数。",
    formula: "COUNT(DISTINCT valid_session_id)",
    included: "有效主页访问对应的浏览器 Session",
    excluded: "无 Session、机器人与已排除事件",
    source: "traffic_homepage_events",
    freshness: "源数据 15 分钟同步，页面缓存 60 秒",
  },
  "有效率": {
    definition: "主页记录中通过有效性校验的事件比例。",
    formula: "有效主页 PV ÷ 主页记录访问 × 100%",
    included: "所选窗口内全部主页访问记录",
    excluded: "无；排除结果体现在分子",
    source: "traffic_homepage_events",
    freshness: "源数据 15 分钟同步，页面缓存 60 秒",
  },
  "推广链接": {
    definition: "推广跳转链接产生的有效访问量和去重 Session 数。",
    formula: "有效链接事件 PV；UV = COUNT(DISTINCT session_id)",
    included: "状态与有效期允许的推广链接访问",
    excluded: "机器人、无效跳转与明确排除事件",
    source: "traffic_link_events",
    freshness: "源数据 15 分钟同步，页面缓存 60 秒",
  },
  "归因注册": {
    definition: "注册成功时已锁定有效来源的普通账号数。",
    formula: "COUNT(DISTINCT account_id WHERE fact_state = normal)",
    included: "正式归因且事实状态正常的注册账号",
    excluded: "内部账号、明确排除与事实待确认账号",
    source: "traffic_registration_attribution",
    freshness: "源数据 15 分钟同步，页面缓存 60 秒",
  },
  "注册转化率": {
    definition: "所选周期内，由有效主页访问产生的正式归因注册比例。",
    formula: "归因注册账号 ÷ 有效 Session UV × 100%",
    included: "正式归因普通账号与有效主页 Session",
    excluded: "内部账号、待确认事实、重复注册与无效 Session",
    source: "traffic_homepage_events + traffic_registration_attribution",
    freshness: "源数据 15 分钟同步，页面缓存 60 秒",
  },
};

export function trafficMetricDefinition(
  label: string,
  segment: TrafficUserSegment,
): MetricDefinitionDetails {
  const details = trafficMetricDefinitions[label];
  if (label !== "归因注册" && label !== "注册转化率") return details;

  const subject = segment === "ordinary" ? "普通账号" : segment === "internal" ? "内部账号" : "全部账号";
  const outsideSegment = segment === "ordinary" ? "内部账号、" : segment === "internal" ? "普通账号、" : "";
  if (label === "归因注册") return {
    ...details,
    definition: `注册成功时已锁定有效来源的${subject}数。`,
    included: `正式归因且事实状态正常的${subject}`,
    excluded: `${outsideSegment}明确排除与事实待确认账号`,
  };
  return {
    ...details,
    definition: `所选周期内，由有效主页访问产生的${subject}正式归因注册比例。`,
    included: `正式归因${subject}与有效主页 Session`,
    excluded: `${outsideSegment}待确认事实、重复注册与无效 Session`,
  };
}

const trafficRailItems = [
  { id: "traffic-summary", label: "总览与趋势", count: "01" },
  { id: "traffic-sources", label: "有效归因来源", count: "02" },
  { id: "traffic-diagnostic", label: "自然入口诊断", count: "03" },
  { id: "traffic-links", label: "推广链接表现", count: "04" },
  { id: "traffic-registration", label: "注册归因", count: "05" },
  { id: "traffic-quality", label: "数据质量", count: "06" },
];

export function buildTrafficTrendPoints(
  values: number[],
  width: number,
  height: number,
  domain?: { minimum: number; maximum: number },
) {
  if (!values.length) return "";
  const minimum = domain?.minimum ?? Math.min(...values);
  const maximum = domain?.maximum ?? Math.max(...values);
  const range = maximum - minimum;
  const denominator = Math.max(1, values.length - 1);

  return values.map((value, index) => {
    const x = values.length === 1 ? width / 2 : (index / denominator) * width;
    const y = range === 0
      ? domain ? height : height / 2
      : height - ((value - minimum) / range) * height;
    return `${formatTrendCoordinate(x)},${formatTrendCoordinate(y)}`;
  }).join(" ");
}

export function confirmedRegistrationCount(
  summary: TrafficOverviewResponse["registration_summary"],
) {
  return Math.max(0, summary.attributed_accounts - summary.facts_pending_accounts);
}

export function registrationConversionRate(
  overview: Pick<TrafficOverviewResponse, "homepage_summary" | "registration_summary">,
) {
  if (overview.homepage_summary.session_uv <= 0) return null;
  return confirmedRegistrationCount(overview.registration_summary) / overview.homepage_summary.session_uv;
}

function formatTrendCoordinate(value: number) {
  return String(Math.round(value * 100) / 100);
}

export function TrafficOverviewView({
  overview,
  users,
  filters,
  loading,
  usersLoading,
  error,
  usersError,
  sites,
  channels,
  campaigns,
  trackingLinks,
  onFiltersChange,
  onRetry,
  onUsersPage,
}: TrafficOverviewViewProps) {
  const visibleUsers = usersLoading || usersError ? null : users;
  const visibleCampaigns = campaigns.filter((item) =>
    (!filters.siteId || item.site_id === filters.siteId)
    && (!filters.channelId || item.channel_id === filters.channelId));
  const visibleLinks = trackingLinks.filter((item) =>
    (!filters.siteId || item.site_id === filters.siteId)
    && (!filters.channelId || item.channel_id === filters.channelId)
    && (!filters.campaignId || item.campaign_id === filters.campaignId));
  const activeUvTotal = overview?.active_source_breakdown.reduce(
    (total, item) => total + item.session_uv,
    0,
  ) || 0;
  const trendMax = overview?.traffic_trends.reduce(
    (maximum, item) => Math.max(maximum, item.homepage_pv, item.homepage_uv, item.link_pv, item.link_uv),
    0,
  ) || 0;
  const timezone = overview?.window.timezone || "UTC";
  const confirmedRegistrations = overview
    ? confirmedRegistrationCount(overview.registration_summary)
    : 0;
  const registrationConversion = overview ? registrationConversionRate(overview) : null;
  const latestTrafficAt = overview?.homepage_summary.latest_event_at || overview?.generated_at || null;

  const change = (values: Partial<TrafficOverviewFilters>) => {
    onFiltersChange({ ...filters, ...values });
  };

  return (
    <div className="traffic-overview" aria-busy={loading}>
      <div className="traffic-overview-workspace">
        <WorkspaceRail
          label="访问流量页面索引"
          items={trafficRailItems}
          status={overview ? {
            title: "数据正常",
            detail: `截至 ${formatDateTime(latestTrafficAt, timezone)} · ${timezone}`,
            tone: "healthy",
          } : undefined}
        />
        <div className="traffic-overview-main">
      <div className="traffic-overview-query" aria-label="流量概览查询">
        <label><span>时间范围</span><select value={filters.range} onChange={(event) => change({ range: event.target.value as TrafficRange })}><option value="24h">最近 24 小时</option><option value="7d">最近 7 天</option><option value="30d">最近 30 天</option><option value="90d">最近 90 天</option></select></label>
        <label><span>注册账号范围</span><select value={filters.segment} onChange={(event) => change({ segment: event.target.value as TrafficUserSegment })}><option value="ordinary">普通账号</option><option value="internal">内部账号</option><option value="all">全部账号</option></select></label>
        <label><span>站点</span><select value={filters.siteId} onChange={(event) => change({ siteId: event.target.value, channelId: "", campaignId: "", trackingLinkId: "" })}><option value="">全部站点</option>{sites.map((item) => <option key={item.site_id} value={item.site_id}>{item.site_name}</option>)}</select></label>
        <label><span>有效来源</span><select value={filters.sourceKind} onChange={(event) => change({ sourceKind: event.target.value as TrafficSourceKind, channelId: "", campaignId: "", trackingLinkId: "" })}><option value="">全部来源</option><option value="promotion">推广链接</option><option value="direct">直接访问</option><option value="organic_search">自然搜索</option><option value="referral">外站引荐</option></select></label>
        <label><span>渠道</span><select value={filters.channelId} onChange={(event) => change({ sourceKind: event.target.value ? "promotion" : filters.sourceKind, channelId: event.target.value, campaignId: "", trackingLinkId: "" })}><option value="">全部渠道</option>{channels.map((item) => <option key={item.channel_id} value={item.channel_id}>{item.name}</option>)}</select></label>
        <label><span>活动</span><select value={filters.campaignId} onChange={(event) => change({ sourceKind: event.target.value ? "promotion" : filters.sourceKind, campaignId: event.target.value, trackingLinkId: "" })}><option value="">全部活动</option>{visibleCampaigns.map((item) => <option key={item.campaign_id} value={item.campaign_id}>{item.name}</option>)}</select></label>
        <label><span>推广链接</span><select value={filters.trackingLinkId} onChange={(event) => change({ sourceKind: event.target.value ? "promotion" : filters.sourceKind, trackingLinkId: event.target.value })}><option value="">全部链接</option>{visibleLinks.map((item) => <option key={item.tracking_link_id} value={item.tracking_link_id}>{item.source_name} · {item.code}</option>)}</select></label>
        <button className="ghost" type="button" onClick={() => onFiltersChange(defaultTrafficOverviewFilters)}>重置</button>
      </div>

      {loading ? <div className="traffic-overview-loading" role="status">正在加载流量概览</div> : null}
      {error ? <div className="traffic-overview-error" role="alert"><div><strong>流量概览加载失败</strong><span>{error}</span></div><button type="button" onClick={onRetry}>重新加载</button></div> : null}

      {!error && overview ? (
        <>
          <section id="traffic-summary" className="traffic-overview-summary" aria-label="有效主页流量">
            <div className="traffic-overview-kpi-strip">
              <TrafficMetric
                label="有效主页 PV"
                value={formatCount(overview.homepage_summary.counted_pv)}
                detail={`记录访问 ${formatCount(overview.homepage_summary.recorded_visits)} · 排除 ${formatCount(overview.homepage_summary.excluded_visits)}`}
                segment={filters.segment}
              />
              <TrafficMetric
                label="有效 Session UV"
                value={formatCount(overview.homepage_summary.session_uv)}
                detail="匿名浏览器 Session"
                segment={filters.segment}
              />
              <TrafficMetric
                label="有效率"
                value={formatRate(overview.homepage_summary.valid_rate)}
                detail="主页记录通过有效性校验"
                segment={filters.segment}
              />
              <TrafficMetric
                label="推广链接"
                value={formatCount(overview.link_summary.counted_pv)}
                detail={`${formatCount(overview.link_summary.session_uv)} Session UV`}
                segment={filters.segment}
              />
              <TrafficMetric
                label="归因注册"
                value={formatCount(confirmedRegistrations)}
                detail="来源在注册时锁定"
                segment={filters.segment}
              />
              <TrafficMetric
                label="注册转化率"
                value={formatRate(registrationConversion)}
                detail={`较窗口内 ${formatCount(overview.homepage_summary.session_uv)} Session UV`}
                align="end"
                segment={filters.segment}
              />
            </div>
            <dl className="traffic-overview-context traffic-overview-audit-context" aria-label="流量审计摘要">
              <div><dt>有效率</dt><dd>{formatRate(overview.homepage_summary.valid_rate)}</dd></div>
              <div><dt>推广链接</dt><dd>{formatCount(overview.link_summary.counted_pv)} PV / {formatCount(overview.link_summary.session_uv)} UV</dd></div>
              <div><dt>归因注册</dt><dd>{formatCount(confirmedRegistrations)}</dd></div>
              <div><dt>数据最新</dt><dd>{formatDateTime(overview.homepage_summary.latest_event_at, timezone)}</dd></div>
            </dl>
          </section>

          <div className="traffic-overview-capability" data-status={overview.capabilities.downstream_facts}>
            <div><strong>调用、充值、二次充值、继续调用、退款</strong><span>下游业务事实同步完成生产验收后开放</span></div>
            <b>{capabilityLabel(overview.capabilities.downstream_facts)}</b>
          </div>

          <section id="traffic-trend" className="traffic-overview-section">
            <SectionHeader title="有效流量趋势" detail={`${overview.window.bucket === "hour" ? "按小时" : "按天"}汇总 · ${timezone}`} aside={`生成于 ${formatDateTime(overview.generated_at, timezone)}`} />
            <TrafficTrendChart trends={overview.traffic_trends} />
            <TableScroll variant="trends" label="有效流量趋势表"><table><thead><tr><th>时间</th><th>主页 PV</th><th>主页 UV</th><th>链接 PV</th><th>链接 UV</th></tr></thead><tbody>{overview.traffic_trends.length ? overview.traffic_trends.map((item) => <tr key={item.bucket_at}><td>{formatBucket(item.bucket_at, overview.window.bucket, timezone)}</td><td><TrendValue value={item.homepage_pv} max={trendMax} tone="strong" /></td><td><TrendValue value={item.homepage_uv} max={trendMax} tone="quiet" /></td><td>{formatCount(item.link_pv)}</td><td>{formatCount(item.link_uv)}</td></tr>) : <EmptyRow columns={5} text="当前范围暂无有效流量" />}</tbody></table></TableScroll>
          </section>

          <div className="traffic-overview-split">
          <section id="traffic-sources" className="traffic-overview-section">
            <SectionHeader title="有效归因来源" detail="按窗口内最后一次有效主页事件互斥归类" aside={`合计 ${formatCount(activeUvTotal)} Session UV`} />
            <TableScroll variant="active-sources" label="有效归因来源表"><table><thead><tr><th>来源</th><th>有效主页 PV</th><th>互斥 Session UV</th><th>UV 构成</th></tr></thead><tbody>{overview.active_source_breakdown.length ? overview.active_source_breakdown.map((item) => <tr key={item.source_kind}><td><strong>{sourceLabels[item.source_kind]}</strong></td><td>{formatCount(item.counted_pv)}</td><td>{formatCount(item.session_uv)}</td><td><ShareValue value={item.session_uv} total={activeUvTotal} /></td></tr>) : <EmptyRow columns={4} text="当前筛选下暂无有效来源" />}</tbody></table></TableScroll>
          </section>

          <section id="traffic-diagnostic" className="traffic-overview-section traffic-overview-diagnostic-section">
            <SectionHeader title="自然入口诊断" detail="本次主页如何进入；Session 可能触达多个入口" aside="来源间不可相加" />
            <TableScroll variant="classified-sources" label="自然入口诊断表"><table><thead><tr><th>自然入口</th><th>有效主页 PV</th><th>触达 Session UV</th></tr></thead><tbody>{overview.classified_source_breakdown.length ? overview.classified_source_breakdown.map((item) => <tr key={item.source_kind}><td><strong>{sourceLabels[item.source_kind]}</strong></td><td>{formatCount(item.counted_pv)}</td><td>{formatCount(item.session_uv)}</td></tr>) : <EmptyRow columns={3} text="当前筛选下暂无自然入口数据" />}</tbody></table></TableScroll>
          </section>
          </div>

          <section id="traffic-links" className="traffic-overview-section">
            <SectionHeader title="推广链接表现" detail="有效访问、归因更新和注册分别按权威事实统计" aside={`有效链接 ${formatCount(overview.link_summary.counted_pv)} PV`} />
            <TableScroll variant="links" label="推广链接表现表"><table><thead><tr><th>具体来源</th><th>渠道 / 活动</th><th>站点</th><th>链接状态 / 有效期</th><th>有效 PV</th><th>有效 UV</th><th>排除</th><th>归因更新</th><th>推广注册</th></tr></thead><tbody>{overview.link_performance.length ? overview.link_performance.map((item) => <tr key={item.tracking_link_id}><td><strong>{item.source_name}</strong><small className="traffic-overview-cell-subtext">/r/{item.code}</small></td><td>{item.channel_name}<small className="traffic-overview-cell-subtext">{item.campaign_name}</small></td><td>{siteName(sites, item.site_id)}</td><td><span className={`traffic-overview-status ${item.status}`}>{linkStatusLabels[item.status]}</span><small className="traffic-overview-cell-subtext">{formatLinkValidity(item.valid_from, item.valid_until, timezone)}</small></td><td>{formatCount(item.counted_pv)}</td><td>{formatCount(item.session_uv)}</td><td>{formatCount(item.excluded_visits)}</td><td>{formatCount(item.attribution_updates)}</td><td>{formatCount(item.registered_accounts)}</td></tr>) : <EmptyRow columns={9} text="当前筛选下暂无推广链接表现" />}</tbody></table></TableScroll>
          </section>

          <section id="traffic-registration" className="traffic-overview-section">
            <SectionHeader title="注册归因" detail="来源在注册成功时锁定，后续访问不会改写" aside={`正式归因 ${formatCount(confirmedRegistrations)} 个`} />
            <div className="traffic-overview-registration-summary">
              <SummaryDatum label="归因注册" value={confirmedRegistrations} />
              <SummaryDatum label="明确排除" value={overview.registration_summary.excluded_accounts} tone="muted" />
              <SummaryDatum label="同步待确认" value={overview.registration_summary.facts_pending_accounts} tone="warning" />
            </div>
            {usersError ? <div className="traffic-overview-inline-error" role="alert">{usersError}</div> : null}
            <TableScroll variant="users" label="注册归因账号表"><table><thead><tr><th>账号</th><th>事实状态</th><th>账号范围</th><th>站点</th><th>不可变来源</th><th>渠道 / 活动</th><th>来源触点</th><th>注册时间</th><th>归因写入</th></tr></thead><tbody>{visibleUsers?.items.length ? visibleUsers.items.map((item) => <tr key={item.public_user_id}><td><strong>{formatTrafficAccountIdentifier(item.account_label || item.external_user_id)}</strong>{item.account_label ? <small className="traffic-overview-cell-subtext">{formatTrafficAccountIdentifier(item.external_user_id)}</small> : null}</td><td><span className={`traffic-overview-fact-state ${item.fact_state}`}>{factStateLabels[item.fact_state]}</span></td><td>{item.is_internal ? "内部账号" : "普通账号"}</td><td>{siteName(sites, item.site_id)}</td><td>{sourceLabels[item.source_kind]}<small className="traffic-overview-cell-subtext">{item.source_name || "--"}</small></td><td>{item.channel_name || "--"}<small className="traffic-overview-cell-subtext">{item.campaign_name || "--"}</small></td><td>{formatDateTime(item.source_touch_at, timezone)}</td><td>{formatDateTime(item.registered_at, timezone)}</td><td>{formatDateTime(item.attributed_at, timezone)}<small className="traffic-overview-cell-subtext">{formatAttributionMethod(item.attribution_method)}</small></td></tr>) : <EmptyRow columns={9} text={usersLoading ? "正在加载注册归因..." : "当前筛选下暂无注册归因"} />}</tbody></table></TableScroll>
            {visibleUsers && visibleUsers.total > visibleUsers.limit ? <div className="traffic-overview-pagination"><button className="ghost" type="button" disabled={visibleUsers.offset === 0} onClick={() => onUsersPage(Math.max(0, visibleUsers.offset - visibleUsers.limit))}>上一页</button><span>{visibleUsers.offset + 1}-{Math.min(visibleUsers.total, visibleUsers.offset + visibleUsers.limit)} / {visibleUsers.total}</span><button className="ghost" type="button" disabled={visibleUsers.offset + visibleUsers.limit >= visibleUsers.total} onClick={() => onUsersPage(visibleUsers.offset + visibleUsers.limit)}>下一页</button></div> : null}
          </section>

          <details id="traffic-quality" className="traffic-overview-quality">
            <summary><span><strong>数据质量与新鲜度</strong><small>访问排除、跳转结果与事实同步诊断</small></span><b>{formatCount(overview.homepage_summary.excluded_visits + overview.link_summary.excluded_visits)} 条排除</b></summary>
            <div className="traffic-overview-quality-body">
              <dl className="traffic-overview-quality-stats">
                <div><dt>主页记录 / 有效 / 排除</dt><dd>{formatCount(overview.homepage_summary.recorded_visits)} / {formatCount(overview.homepage_summary.counted_pv)} / {formatCount(overview.homepage_summary.excluded_visits)}</dd></div>
                <div><dt>链接记录 / 有效 / 排除</dt><dd>{formatCount(overview.link_summary.recorded_visits)} / {formatCount(overview.link_summary.counted_pv)} / {formatCount(overview.link_summary.excluded_visits)}</dd></div>
                <div><dt>机器人识别</dt><dd>主页 {formatCount(overview.quality.homepage_bot_visits)} · 链接 {formatCount(overview.quality.link_bot_visits)}</dd></div>
                <div><dt>事实同步待确认</dt><dd>{formatCount(overview.quality.facts_pending_accounts)} 个账号</dd></div>
                <div><dt>来源数据水位</dt><dd>{formatDateTime(overview.quality.latest_source_data_fresh_at, timezone)}</dd></div>
                <div><dt>派生计算时间</dt><dd>{formatDateTime(overview.quality.latest_computed_at, timezone)} · 延迟 {formatDuration(overview.quality.facts_delay_seconds)}</dd></div>
              </dl>
              <div className="traffic-overview-quality-groups">
                <QualityGroup title="排除原因" items={overview.quality.exclusion_reasons.map((item) => ({ label: `${item.event_scope === "homepage" ? "主页" : "链接"} · ${exclusionReasonLabel(item.reason)}`, value: item.event_count }))} />
                <QualityGroup title="跳转结果" items={overview.quality.redirect_results.map((item) => ({ label: item.redirect_result, value: item.event_count }))} />
                <QualityGroup title="HTTP 状态" items={overview.quality.http_statuses.map((item) => ({ label: `HTTP ${item.http_status}`, value: item.event_count }))} />
              </div>
            </div>
          </details>
        </>
      ) : null}
        </div>
      </div>
    </div>
  );
}

function TrafficMetric({
  label,
  value,
  detail,
  align = "start",
  segment,
}: {
  label: string;
  value: string;
  detail: string;
  align?: "start" | "end";
  segment: TrafficUserSegment;
}) {
  return (
    <article className="traffic-overview-metric">
      <span><MetricDefinition label={label} details={trafficMetricDefinition(label, segment)} align={align} /></span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function TrafficTrendChart({ trends }: { trends: TrafficOverviewResponse["traffic_trends"] }) {
  const homepagePv = trends.map((item) => item.homepage_pv);
  const homepageUv = trends.map((item) => item.homepage_uv);
  const linkPv = trends.map((item) => item.link_pv);
  const domain = {
    minimum: 0,
    maximum: trends.reduce(
      (maximum, item) => Math.max(maximum, item.homepage_pv, item.homepage_uv, item.link_pv),
      0,
    ),
  };

  return (
    <div className="traffic-overview-trend-chart" role="img" aria-label="主页 PV、主页 UV 和链接 PV 趋势图">
      <div className="traffic-overview-trend-chart-head">
        <span>趋势预览</span>
        <span className="traffic-overview-trend-legend"><i className="homepage-pv" />主页 PV <i className="homepage-uv" />主页 UV <i className="link-pv" />链接 PV</span>
      </div>
      <svg viewBox="0 0 100 44" preserveAspectRatio="none" aria-hidden="true">
        <polyline className="homepage-pv" points={buildTrafficTrendPoints(homepagePv, 100, 40, domain)} />
        <polyline className="homepage-uv" points={buildTrafficTrendPoints(homepageUv, 100, 40, domain)} />
        <polyline className="link-pv" points={buildTrafficTrendPoints(linkPv, 100, 40, domain)} />
      </svg>
    </div>
  );
}

function SummaryDatum({ label, value, tone = "default" }: { label: string; value: number; tone?: "default" | "muted" | "warning" }) {
  return <div className={`traffic-overview-summary-datum ${tone}`}><span>{label}</span><strong>{formatCount(value)}</strong></div>;
}

function SectionHeader({ title, detail, aside }: { title: string; detail: string; aside?: string }) {
  return <div className="traffic-overview-section-head"><div><h3>{title}</h3><span>{detail}</span></div>{aside ? <small>{aside}</small> : null}</div>;
}

function TrendValue({ value, max, tone }: { value: number; max: number; tone: "strong" | "quiet" }) {
  const width = max > 0 ? Math.max(3, Math.round((value / max) * 100)) : 0;
  return <div className={`traffic-overview-trend-value ${tone}`}><span>{formatCount(value)}</span><i aria-hidden="true"><b style={{ width: `${width}%` }} /></i></div>;
}

function ShareValue({ value, total }: { value: number; total: number }) {
  const rate = total > 0 ? value / total : null;
  return <div className="traffic-overview-share"><span>{formatRate(rate)}</span><i aria-hidden="true"><b style={{ width: `${rate == null ? 0 : rate * 100}%` }} /></i></div>;
}

function QualityGroup({ title, items }: { title: string; items: Array<{ label: string; value: number }> }) {
  return <div className="traffic-overview-quality-group"><strong>{title}</strong>{items.length ? <ul>{items.map((item) => <li key={item.label}><span>{item.label}</span><b>{formatCount(item.value)}</b></li>)}</ul> : <small>暂无记录</small>}</div>;
}

function TableScroll({
  children,
  label,
  variant,
}: {
  children: React.ReactNode;
  label: string;
  variant: "trends" | "active-sources" | "classified-sources" | "links" | "users";
}) {
  return <div className={`traffic-overview-table-scroll traffic-overview-table-${variant}`} role="region" tabIndex={0} aria-label={label}>{children}</div>;
}

function EmptyRow({ columns, text }: { columns: number; text: string }) {
  return <tr><td className="traffic-overview-empty" colSpan={columns}>{text}</td></tr>;
}

export function formatTrafficAccountIdentifier(value: string | null | undefined) {
  const normalized = String(value || "").trim();
  const separator = normalized.indexOf("@");
  if (separator <= 0 || separator === normalized.length - 1) return normalized || "--";
  return `${normalized[0]}***@${normalized[separator + 1]}***`;
}

function formatCount(value: number | undefined) {
  return value === undefined ? "--" : new Intl.NumberFormat("zh-CN").format(value);
}

function formatRate(value: number | null | undefined) {
  return value == null ? "--" : `${(value * 100).toFixed(1)}%`;
}

function formatDateTime(value: string | null | undefined, timeZone = "UTC") {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone,
  }).format(date);
}

function formatBucket(value: string, bucket: "hour" | "day", timeZone: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    ...(bucket === "hour" ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
    timeZone,
  }).format(date);
}

function formatLinkValidity(validFrom: string | null, validUntil: string | null, timeZone: string) {
  if (!validFrom && !validUntil) return "长期有效";
  return `${validFrom ? formatDateTime(validFrom, timeZone) : "即时"} - ${validUntil ? formatDateTime(validUntil, timeZone) : "长期"}`;
}

function formatDuration(value: number | null | undefined) {
  if (value == null) return "--";
  if (value < 60) return `${value} 秒`;
  if (value < 3600) return `${Math.round(value / 60)} 分钟`;
  return `${(value / 3600).toFixed(1)} 小时`;
}

function formatAttributionMethod(value: string) {
  const labels: Record<string, string> = {
    shared_cookie: "共享 Cookie",
    signed_handoff: "签名交接",
    homepage_session: "主页 Session",
    reconciled: "数据对账",
  };
  return labels[value] || value;
}

function exclusionReasonLabel(value: string) {
  if (value === "unclassified") return "未分类";
  if (value === "bot") return "机器人";
  return value;
}

function capabilityLabel(value: TrafficCapability) {
  if (value === "available") return "已接入";
  if (value === "delayed") return "数据延迟";
  if (value === "error") return "查询异常";
  return "未接入";
}

function siteName(sites: TrafficSiteOption[], siteId: string) {
  return sites.find((item) => item.site_id === siteId)?.site_name || siteId;
}

export default TrafficOverview;
