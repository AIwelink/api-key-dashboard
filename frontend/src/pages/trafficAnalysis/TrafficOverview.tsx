import { useEffect, useState } from "react";

import { api } from "../../api/client";
import { errorMessage } from "../../utils/format";


export type TrafficRange = "24h" | "7d" | "30d" | "90d";
export type TrafficUserSegment = "ordinary" | "internal" | "all";
export type TrafficSourceKind = "" | "promotion" | "direct" | "organic_search" | "referral";
export type TrafficMilestone = "registered" | "called" | "paid" | "second_paid" | "continued" | "refunded";

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
  homepage_pv: number;
  homepage_uv: number;
  link_pv: number;
  link_uv: number;
  registered_accounts: number;
  called_accounts: number;
  paid_accounts: number;
  second_paid_accounts: number;
  continued_accounts: number;
  refunded_accounts: number;
};

export type TrafficRates = {
  homepage_registration_rate: number | null;
  link_registration_rate: number | null;
  call_rate: number | null;
  payment_rate: number | null;
  second_payment_rate: number | null;
  continued_rate: number | null;
};

export type TrafficTrend = {
  bucket_at: string;
  homepage_pv: number;
  homepage_uv: number;
  link_pv: number;
  link_uv: number;
  registered_accounts: number;
  called_accounts: number;
  paid_accounts: number;
};

export type TrafficSourceBreakdown = {
  source_kind: Exclude<TrafficSourceKind, "">;
  entry_pv: number;
  entry_uv: number;
  registered_accounts: number;
  called_accounts: number;
  paid_accounts: number;
};

export type TrafficLinkPerformance = {
  tracking_link_id: string;
  site_id: string;
  code: string;
  source_name: string;
  campaign_id: string;
  campaign_name: string;
  channel_id: string;
  channel_name: string;
  link_pv: number;
  link_uv: number;
  registered_accounts: number;
  called_accounts: number;
  paid_accounts: number;
  second_paid_accounts: number;
  continued_accounts: number;
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
  summary: TrafficSummary;
  rates: TrafficRates;
  amounts: Array<{
    currency: string;
    payment_total_minor: number;
    refund_total_minor: number;
  }>;
  trends: TrafficTrend[];
  source_breakdown: TrafficSourceBreakdown[];
  link_performance: TrafficLinkPerformance[];
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
  first_successful_call_at: string | null;
  last_successful_call_at: string | null;
  first_payment_at: string | null;
  second_payment_at: string | null;
  first_refund_at: string | null;
  last_refund_at: string | null;
  has_continued_call: boolean;
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

type QueryOptions = {
  milestone?: TrafficMilestone;
  limit?: number;
  offset?: number;
};

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
  if (options.milestone) query.set("milestone", options.milestone);
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
  const [milestone, setMilestone] = useState<TrafficMilestone>("registered");
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
    ).then((result) => {
      setOverview(result);
    }).catch((requestError) => {
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
        milestone,
        limit: 50,
        offset: usersOffset,
      })}`,
      props.token,
      { signal: controller.signal },
    ).then((result) => {
      setUsers(result);
    }).catch((requestError) => {
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
    milestone,
    usersOffset,
    reloadVersion,
  ]);

  const beginUsersLoad = () => {
    setUsers(null);
    setUsersLoading(true);
    setUsersError("");
  };

  const updateFilters = (next: TrafficOverviewFilters) => {
    const update = decideTrafficOverviewFilterUpdate(filters, next, usersOffset);
    if (update === "none") return;
    if (update === "users-page") {
      beginUsersLoad();
      setUsersOffset(0);
      return;
    }
    setOverview(null);
    setUsers(null);
    setLoading(true);
    setUsersLoading(true);
    setError("");
    setUsersError("");
    setFilters(next);
    setUsersOffset(0);
  };

  return (
    <TrafficOverviewView
      {...props}
      overview={overview}
      users={users}
      filters={filters}
      milestone={milestone}
      loading={loading}
      usersLoading={usersLoading}
      error={error}
      usersError={usersError}
      onFiltersChange={updateFilters}
      onSelectMilestone={(nextMilestone) => {
        if (nextMilestone === milestone && usersOffset === 0) return;
        beginUsersLoad();
        setMilestone(nextMilestone);
        setUsersOffset(0);
      }}
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
        beginUsersLoad();
        setUsersOffset(offset);
      }}
    />
  );
}

export type TrafficOverviewViewProps = Omit<TrafficOverviewProps, "token" | "showToast"> & {
  overview: TrafficOverviewResponse | null;
  users: TrafficUsersResponse | null;
  filters: TrafficOverviewFilters;
  milestone: TrafficMilestone;
  loading: boolean;
  usersLoading: boolean;
  error: string;
  usersError: string;
  onFiltersChange: (filters: TrafficOverviewFilters) => void;
  onSelectMilestone: (milestone: TrafficMilestone) => void;
  onRetry: () => void;
  onUsersPage: (offset: number) => void;
};

const milestoneLabels: Record<TrafficMilestone, string> = {
  registered: "注册账号",
  called: "成功调用",
  paid: "付费账号",
  second_paid: "二次付费",
  continued: "继续调用",
  refunded: "退款账号",
};

const milestoneCommands: Record<TrafficMilestone, string> = {
  registered: "查看注册账号",
  called: "查看成功调用账号",
  paid: "查看付费账号",
  second_paid: "查看二次付费账号",
  continued: "查看继续调用账号",
  refunded: "查看退款账号",
};

const sourceLabels: Record<Exclude<TrafficSourceKind, "">, string> = {
  promotion: "推广链接",
  direct: "直接访问",
  organic_search: "自然搜索",
  referral: "引荐流量",
};

export function TrafficOverviewView({
  overview,
  users,
  filters,
  milestone,
  loading,
  usersLoading,
  error,
  usersError,
  sites,
  channels,
  campaigns,
  trackingLinks,
  onFiltersChange,
  onSelectMilestone,
  onRetry,
  onUsersPage,
}: TrafficOverviewViewProps) {
  const summary = overview?.summary;
  const rates = overview?.rates;
  const visibleUsers = usersLoading || usersError ? null : users;
  const visibleCampaigns = campaigns.filter((item) =>
    (!filters.siteId || item.site_id === filters.siteId)
    && (!filters.channelId || item.channel_id === filters.channelId));
  const visibleLinks = trackingLinks.filter((item) =>
    (!filters.siteId || item.site_id === filters.siteId)
    && (!filters.channelId || item.channel_id === filters.channelId)
    && (!filters.campaignId || item.campaign_id === filters.campaignId));
  const homepageIsFiltered = Boolean(
    filters.siteId
    || filters.sourceKind
    || filters.channelId
    || filters.campaignId
    || filters.trackingLinkId,
  );
  const homepageScope = homepageIsFiltered ? "当前筛选" : "全站";
  const funnel = [
    { key: "registered", value: summary?.registered_accounts, rate: null },
    { key: "called", value: summary?.called_accounts, rate: rates?.call_rate },
    { key: "paid", value: summary?.paid_accounts, rate: rates?.payment_rate },
    { key: "second_paid", value: summary?.second_paid_accounts, rate: rates?.second_payment_rate },
    { key: "continued", value: summary?.continued_accounts, rate: rates?.continued_rate },
    { key: "refunded", value: summary?.refunded_accounts, rate: null },
  ] as const;

  const change = (values: Partial<TrafficOverviewFilters>) => onFiltersChange({ ...filters, ...values });

  return (
    <div className="traffic-overview" aria-busy={loading}>
      <div className="traffic-overview-query" aria-label="流量概览查询">
        <label><span>时间范围</span><select value={filters.range} onChange={(event) => change({ range: event.target.value as TrafficRange })}><option value="24h">最近 24 小时</option><option value="7d">最近 7 天</option><option value="30d">最近 30 天</option><option value="90d">最近 90 天</option></select></label>
        <label><span>用户群体</span><select value={filters.segment} onChange={(event) => change({ segment: event.target.value as TrafficUserSegment })}><option value="ordinary">普通用户</option><option value="internal">内部人员</option><option value="all">全部用户</option></select></label>
        <label><span>站点</span><select value={filters.siteId} onChange={(event) => change({ siteId: event.target.value, campaignId: "", trackingLinkId: "" })}><option value="">全部站点</option>{sites.map((item) => <option key={item.site_id} value={item.site_id}>{item.site_name}</option>)}</select></label>
        <label><span>来源</span><select value={filters.sourceKind} onChange={(event) => change({ sourceKind: event.target.value as TrafficSourceKind, channelId: event.target.value === "promotion" ? filters.channelId : "", campaignId: event.target.value === "promotion" ? filters.campaignId : "", trackingLinkId: event.target.value === "promotion" ? filters.trackingLinkId : "" })}><option value="">全部来源</option><option value="promotion">推广链接</option><option value="direct">直接访问</option><option value="organic_search">自然搜索</option><option value="referral">引荐流量</option></select></label>
        <label><span>渠道</span><select value={filters.channelId} onChange={(event) => change({ sourceKind: event.target.value ? "promotion" : filters.sourceKind, channelId: event.target.value, campaignId: "", trackingLinkId: "" })}><option value="">全部渠道</option>{channels.map((item) => <option key={item.channel_id} value={item.channel_id}>{item.name}</option>)}</select></label>
        <label><span>活动</span><select value={filters.campaignId} onChange={(event) => change({ sourceKind: event.target.value ? "promotion" : filters.sourceKind, campaignId: event.target.value, trackingLinkId: "" })}><option value="">全部活动</option>{visibleCampaigns.map((item) => <option key={item.campaign_id} value={item.campaign_id}>{item.name}</option>)}</select></label>
        <label><span>推广链接</span><select value={filters.trackingLinkId} onChange={(event) => change({ sourceKind: event.target.value ? "promotion" : filters.sourceKind, trackingLinkId: event.target.value })}><option value="">全部链接</option>{visibleLinks.map((item) => <option key={item.tracking_link_id} value={item.tracking_link_id}>{item.source_name} · {item.code}</option>)}</select></label>
        <button className="ghost" type="button" onClick={() => onFiltersChange(defaultTrafficOverviewFilters)}>重置</button>
      </div>

      {loading && !error ? (
        <div className="traffic-overview-loading" role="status">正在加载流量概览</div>
      ) : null}

      {error ? (
        <div className="traffic-overview-error" role="alert"><div><strong>流量概览加载失败</strong><span>{error}</span></div><button type="button" onClick={onRetry}>重新加载</button></div>
      ) : loading ? null : (
        <>
          <section className="traffic-overview-section traffic-overview-metrics" aria-label="访问指标">
            <TrafficMetric label={`主页 PV（${homepageScope}）`} value={summary?.homepage_pv} detail={homepageIsFiltered ? "符合当前筛选的主页访问" : "全部有效主页访问"} />
            <TrafficMetric label={`主页 UV（${homepageScope}）`} value={summary?.homepage_uv} detail={`注册率 ${formatRate(rates?.homepage_registration_rate)}`} />
            <TrafficMetric label="推广链接 PV" value={summary?.link_pv} detail="有效 /r/ 链接访问" />
            <TrafficMetric label="推广链接 UV" value={summary?.link_uv} detail={`注册率 ${formatRate(rates?.link_registration_rate)}`} />
          </section>

          <section className="traffic-overview-section">
            <SectionHeader title="注册转化漏斗" detail="注册 Cohort · 末次触发归因" aside={overview ? `生成于 ${formatDateTime(overview.generated_at, overview.window.timezone)}` : loading ? "正在加载" : "--"} />
            <div className="traffic-overview-funnel">
              {funnel.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  data-milestone={item.key}
                  className={`traffic-overview-funnel-stage${milestone === item.key ? " is-active" : ""}`}
                  aria-pressed={milestone === item.key}
                  onClick={() => onSelectMilestone(item.key)}
                >
                  <span>{milestoneCommands[item.key]}</span>
                  <strong>{formatCount(item.value)}</strong>
                  <small>{item.key === "registered" ? "所选周期 Cohort" : item.rate == null ? "--" : formatRate(item.rate)}</small>
                </button>
              ))}
            </div>
            <div className="traffic-overview-amounts">
              <span>充值与退款</span>
              {overview?.amounts.length ? overview.amounts.map((item) => <strong key={item.currency}>{formatMinor(item.payment_total_minor, item.currency)} <small>充值</small> / {formatMinor(item.refund_total_minor, item.currency)} <small>退款</small></strong>) : <strong>--</strong>}
            </div>
          </section>

          <section className="traffic-overview-section">
            <SectionHeader title="访问与转化趋势" detail={`${overview?.window.bucket === "hour" ? "按小时汇总" : "按天汇总"} · ${overview?.window.timezone || "UTC"}`} />
            <TableScroll variant="trends" label="访问与转化趋势表"><table><thead><tr><th>时间</th><th>主页 PV</th><th>主页 UV</th><th>链接 PV</th><th>链接 UV</th><th>注册</th><th>调用</th><th>付费</th></tr></thead><tbody>{overview?.trends.length ? overview.trends.map((item) => <tr key={item.bucket_at}><td>{formatBucket(item.bucket_at, overview.window.bucket, overview.window.timezone)}</td><td>{formatCount(item.homepage_pv)}</td><td>{formatCount(item.homepage_uv)}</td><td>{formatCount(item.link_pv)}</td><td>{formatCount(item.link_uv)}</td><td>{formatCount(item.registered_accounts)}</td><td>{formatCount(item.called_accounts)}</td><td>{formatCount(item.paid_accounts)}</td></tr>) : <EmptyRow columns={8} text={loading ? "正在加载趋势..." : "当前范围暂无趋势数据"} />}</tbody></table></TableScroll>
          </section>

          <section className="traffic-overview-section">
            <SectionHeader title="来源构成" detail="按访问时识别的来源与注册末次触发来源汇总" />
            <TableScroll variant="source" label="来源构成表"><table><thead><tr><th>来源</th><th>访问 PV</th><th>访问 UV</th><th>注册账号</th><th>成功调用</th><th>付费账号</th><th>注册率</th></tr></thead><tbody>{overview?.source_breakdown.length ? overview.source_breakdown.map((item) => <tr key={item.source_kind}><td><strong>{sourceLabels[item.source_kind]}</strong></td><td>{formatCount(item.entry_pv)}</td><td>{formatCount(item.entry_uv)}</td><td>{formatCount(item.registered_accounts)}</td><td>{formatCount(item.called_accounts)}</td><td>{formatCount(item.paid_accounts)}</td><td>{formatRate(item.entry_uv ? item.registered_accounts / item.entry_uv : null)}</td></tr>) : <EmptyRow columns={7} text={loading ? "正在加载来源..." : "当前筛选下暂无来源数据"} />}</tbody></table></TableScroll>
          </section>

          <section className="traffic-overview-section">
            <SectionHeader title="推广链接表现" detail="最多显示 50 条，按注册账号和访问量排序" />
            <TableScroll variant="links" label="推广链接表现表"><table><thead><tr><th>具体来源</th><th>渠道 / 活动</th><th>站点</th><th>PV</th><th>UV</th><th>注册</th><th>调用</th><th>付费</th><th>二次付费</th><th>继续调用</th></tr></thead><tbody>{overview?.link_performance.length ? overview.link_performance.map((item) => <tr key={item.tracking_link_id}><td><strong>{item.source_name}</strong><small className="traffic-overview-cell-subtext">/r/{item.code}</small></td><td>{item.channel_name}<small className="traffic-overview-cell-subtext">{item.campaign_name}</small></td><td>{siteName(sites, item.site_id)}</td><td>{formatCount(item.link_pv)}</td><td>{formatCount(item.link_uv)}</td><td>{formatCount(item.registered_accounts)}</td><td>{formatCount(item.called_accounts)}</td><td>{formatCount(item.paid_accounts)}</td><td>{formatCount(item.second_paid_accounts)}</td><td>{formatCount(item.continued_accounts)}</td></tr>) : <EmptyRow columns={10} text={loading ? "正在加载链接表现..." : "当前筛选下暂无推广链接表现"} />}</tbody></table></TableScroll>
          </section>
        </>
      )}

      <section className="traffic-overview-section">
        <SectionHeader title={`${milestoneLabels[milestone]}名单`} detail="账号来源在注册时锁定，后续点击不会改写" aside={visibleUsers ? `共 ${formatCount(visibleUsers.total)} 个账号` : "--"} />
        {usersError ? <div className="traffic-overview-inline-error" role="alert">{usersError}</div> : null}
        <TableScroll variant="users" label="里程碑账号名单"><table><thead><tr><th>账号</th><th>用户群体</th><th>站点</th><th>来源</th><th>渠道 / 活动</th><th>注册时间</th><th>首次调用</th><th>首次付费</th><th>二次付费</th><th>最近调用</th><th>退款时间</th></tr></thead><tbody>{visibleUsers?.items.length ? visibleUsers.items.map((item) => <tr key={item.public_user_id}><td><strong>{formatTrafficAccountIdentifier(item.account_label || item.external_user_id)}</strong>{item.account_label ? <small className="traffic-overview-cell-subtext">{formatTrafficAccountIdentifier(item.external_user_id)}</small> : null}</td><td><span className={`traffic-overview-segment ${item.is_internal ? "internal" : "ordinary"}`}>{item.is_internal ? "内部人员" : "普通用户"}</span></td><td>{siteName(sites, item.site_id)}</td><td>{sourceLabels[item.source_kind]}<small className="traffic-overview-cell-subtext">{item.source_name || "--"}</small></td><td>{item.channel_name || "--"}<small className="traffic-overview-cell-subtext">{item.campaign_name || "--"}</small></td><td>{formatDateTime(item.registered_at, overview?.window.timezone)}</td><td>{formatDateTime(item.first_successful_call_at, overview?.window.timezone)}</td><td>{formatDateTime(item.first_payment_at, overview?.window.timezone)}</td><td>{formatDateTime(item.second_payment_at, overview?.window.timezone)}</td><td>{formatDateTime(item.last_successful_call_at, overview?.window.timezone)}</td><td>{formatDateTime(item.last_refund_at || item.first_refund_at, overview?.window.timezone)}</td></tr>) : <EmptyRow columns={11} text={usersLoading ? "正在加载账号..." : "当前里程碑暂无账号"} />}</tbody></table></TableScroll>
        {visibleUsers && visibleUsers.total > visibleUsers.limit ? <div className="traffic-overview-pagination"><button className="ghost" type="button" disabled={visibleUsers.offset === 0} onClick={() => onUsersPage(Math.max(0, visibleUsers.offset - visibleUsers.limit))}>上一页</button><span>{visibleUsers.offset + 1}-{Math.min(visibleUsers.total, visibleUsers.offset + visibleUsers.limit)} / {visibleUsers.total}</span><button className="ghost" type="button" disabled={visibleUsers.offset + visibleUsers.limit >= visibleUsers.total} onClick={() => onUsersPage(visibleUsers.offset + visibleUsers.limit)}>下一页</button></div> : null}
      </section>
    </div>
  );
}

function TrafficMetric({ label, value, detail }: { label: string; value?: number; detail: string }) {
  return <div className="traffic-overview-metric"><span>{label}</span><strong>{formatCount(value)}</strong><small>{detail}</small></div>;
}

function SectionHeader({ title, detail, aside }: { title: string; detail: string; aside?: string }) {
  return <div className="traffic-overview-section-head"><div><h3>{title}</h3><span>{detail}</span></div>{aside ? <small>{aside}</small> : null}</div>;
}

function TableScroll({
  children,
  label,
  variant,
}: {
  children: React.ReactNode;
  label: string;
  variant: "trends" | "source" | "links" | "users";
}) {
  return (
    <div
      className={`traffic-overview-table-scroll traffic-overview-table-${variant}`}
      role="region"
      tabIndex={0}
      aria-label={label}
    >
      {children}
    </div>
  );
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

function formatMinor(value: number, currency: string) {
  return `${currency} ${(value / 100).toFixed(2)}`;
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

function siteName(sites: TrafficSiteOption[], siteId: string) {
  return sites.find((item) => item.site_id === siteId)?.site_name || siteId;
}

export default TrafficOverview;
