import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { errorMessage, formatDateTime, pretty, text } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type Site = {
  id: string;
  name: string;
  base_url?: string;
  status?: string;
};

type Group = {
  id: number;
  name: string;
  account_count?: number;
  active_account_count?: number;
};

type EventsResponse = {
  items: EventRecord[];
  total: number;
  skip: number;
  limit: number;
  summary?: EventSummary;
};

type AccountsResponse = {
  items: EventAccount[];
  total: number;
  skip: number;
  limit: number;
  summary?: EventSummary;
};

type EventSummary = {
  total_events?: number;
  critical_events?: number;
  warning_events?: number;
  detected_401?: number;
  recovered_401?: number;
  usage_rollovers?: number;
  official_usage_refreshes?: number;
  duplicate_email_events?: number;
  removed_events?: number;
  today_events?: number;
  today_401?: number;
  one_hour_401?: number;
  current_abnormal_accounts?: number;
  cumulative_actual_cost?: number;
  cumulative_total_cost?: number;
  cumulative_7d_actual_cost?: number;
  cumulative_request_count?: number;
  cumulative_token_count?: number;
  last_event_at?: string | null;
};

type EventRecord = {
  id: string;
  event_type?: string;
  severity?: string;
  occurred_at?: string;
  detected_at?: string;
  site_id?: string;
  site_name?: string;
  identity_id?: string;
  session_id?: string;
  remote_account_id?: number | string;
  remote_account_ids?: Array<number | string>;
  name?: string;
  email?: string;
  normalized_email?: string;
  plan_type?: string;
  group_ids?: number[];
  group_names?: string[];
  previous_status?: string;
  current_status?: string;
  current_schedulable?: boolean | null;
  current_error_message?: string;
  error_category?: string;
  is_401?: boolean;
  usage_snapshot?: Record<string, unknown>;
  cumulative_usage_snapshot?: Record<string, unknown>;
  usage_duration_seconds?: number | null;
  normal_use_seconds?: number | null;
  notification_status?: string;
  uploader_name?: string;
  last_operation_by_name?: string;
  last_operation_name?: string;
  last_operation_at?: string;
  details?: Record<string, unknown>;
  raw_excerpt?: string;
};

type EventAccount = {
  id: string;
  site_id?: string;
  site_name?: string;
  identity_id?: string;
  email?: string;
  normalized_email?: string;
  name?: string;
  plan_type?: string;
  current_presence?: string;
  current_status?: string;
  current_schedulable?: boolean | null;
  current_error_message?: string;
  current_is_401?: boolean;
  current_remote_account_id?: number | string;
  current_remote_account_ids?: Array<number | string>;
  duplicate_remote_count?: number;
  current_group_ids?: number[];
  group_names?: string[];
  first_seen_at?: string;
  last_seen_at?: string;
  first_401_at?: string;
  last_401_at?: string;
  last_removed_at?: string;
  last_event_at?: string;
  total_sessions?: number;
  total_401_count?: number;
  total_recovery_count?: number;
  total_removed_count?: number;
  last_usage_snapshot?: Record<string, unknown>;
  cumulative_usage_snapshot?: Record<string, unknown>;
  cumulative_usage_totals?: Record<string, unknown>;
  last_usage_rollover_at?: string;
  lifetime_seconds?: number | null;
  uploader_name?: string;
  last_operation_by_name?: string;
  last_operation_name?: string;
  last_operation_at?: string;
};

type AccountDetail = {
  identity: EventAccount;
  sessions: Array<Record<string, unknown>>;
  events: EventRecord[];
  samples: Array<Record<string, unknown>>;
  raw?: Record<string, unknown>;
};

type Filters = {
  range: string;
  site_id: string;
  group_id: string;
  event_type: string;
  severity: string;
  account_type: string;
  presence: string;
  q: string;
  only_401: boolean;
  only_abnormal: boolean;
  only_pro: boolean;
  only_cumulative: boolean;
  only_delete_archive: boolean;
  limit: number;
};

type ViewMode = "events" | "accounts";

type CachedEventRecordsState = {
  filters?: Partial<Filters>;
  viewMode?: ViewMode;
};

type EventRecordsDataCache = {
  accounts: EventAccount[];
  cachedAt: number;
  events: EventRecord[];
  groups: Group[];
  key: string;
  sites: Site[];
  skip: number;
  summary: EventSummary;
  total: number;
};

const initialFilters: Filters = {
  range: "24h",
  site_id: "",
  group_id: "",
  event_type: "",
  severity: "",
  account_type: "",
  presence: "",
  q: "",
  only_401: false,
  only_abnormal: false,
  only_pro: false,
  only_cumulative: false,
  only_delete_archive: false,
  limit: 100,
};

const EVENT_RECORDS_STATE_STORAGE_KEY = "eventRecordsPageState";
const allowedLimits = new Set([50, 100, 200, 500]);
const EVENT_RECORDS_DATA_CACHE_LIMIT = 8;
let eventRecordsDataCache = new Map<string, EventRecordsDataCache>();

const eventTypeOptions = [
  ["", "全部事件"],
  ["remote_account_seen_first", "首次发现"],
  ["remote_account_reappeared", "重新出现"],
  ["status_changed", "状态变化"],
  ["error_changed", "错误变化"],
  ["schedulable_changed", "调度变化"],
  ["group_changed", "分组变化"],
  ["401_detected", "401 封号"],
  ["401_recovered", "401 恢复"],
  ["usage_rollover", "用量清零累计"],
  ["official_usage_refresh", "官方额度刷新"],
  ["missing_suspected", "疑似远端删除"],
  ["remote_removed_confirmed", "确认远端删除"],
  ["duplicate_email_detected", "重复邮箱"],
  ["duplicate_email_resolved", "重复邮箱已解除"],
] as const;

export function EventRecordsPage({ token, showToast }: Props) {
  const [initialState] = useState(() => loadCachedEventRecordsState());
  const [initialDataCache] = useState(() => getEventRecordsDataCache(initialState.viewMode, initialState.filters));
  const [viewMode, setViewMode] = useState<ViewMode>(initialState.viewMode);
  const [filters, setFilters] = useState<Filters>(initialState.filters);
  const [draftFilters, setDraftFilters] = useState<Filters>(initialState.filters);
  const [events, setEvents] = useState<EventRecord[]>(() => initialDataCache?.events || []);
  const [accounts, setAccounts] = useState<EventAccount[]>(() => initialDataCache?.accounts || []);
  const [summary, setSummary] = useState<EventSummary>(() => initialDataCache?.summary || {});
  const [total, setTotal] = useState(() => initialDataCache?.total || 0);
  const [skip, setSkip] = useState(() => initialDataCache?.skip || 0);
  const [loading, setLoading] = useState(false);
  const [sites, setSites] = useState<Site[]>(() => initialDataCache?.sites || []);
  const [groups, setGroups] = useState<Group[]>(() => initialDataCache?.groups || []);
  const [filterOpen, setFilterOpen] = useState(false);
  const [displayOpen, setDisplayOpen] = useState(false);
  const [detailIdentityId, setDetailIdentityId] = useState<string | null>(null);
  const [detail, setDetail] = useState<AccountDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const selectedSiteId = filters.site_id;
  const activeFilterCount = useMemo(() => countActiveFilters(filters), [filters]);
  const visibleRowsCount = viewMode === "events" ? events.length : accounts.length;

  const loadSites = async () => {
    try {
      const data = await api<{ items: Site[] }>("/sub2api-sites", token);
      const nextSites = data.items || [];
      setSites(nextSites);
      updateEventRecordsDataCache({ sites: nextSites });
    } catch (error) {
      showToast(errorMessage(error), true);
    }
  };

  const loadGroups = async (siteId: string) => {
    if (!siteId) {
      setGroups([]);
      return;
    }
    try {
      const data = await api<{ items: Group[] }>(`/sub2api-sites/${siteId}/groups?page=1&page_size=500`, token);
      const nextGroups = data.items || [];
      setGroups(nextGroups);
      updateEventRecordsDataCache({ groups: nextGroups });
    } catch (error) {
      showToast(errorMessage(error), true);
    }
  };

  const loadData = async ({ force = false } = {}) => {
    if (!force) {
      const cached = getEventRecordsDataCache(viewMode, filters);
      if (cached && cached.skip === skip) {
        restoreEventRecordsDataCache(cached, {
          setAccounts,
          setEvents,
          setGroups,
          setSites,
          setSummary,
          setTotal,
        });
        return;
      }
    }
    setLoading(true);
    try {
      const params = buildParams(filters, viewMode, skip);
      if (viewMode === "events") {
        const data = await api<EventsResponse>(`/event-records/events?${params.toString()}`, token);
        const nextEvents = data.items || [];
        const nextSummary = data.summary || {};
        const nextTotal = numberValue(data.total);
        setEvents(nextEvents);
        setAccounts([]);
        setSummary(nextSummary);
        setTotal(nextTotal);
        writeEventRecordsDataCache({
          accounts: [],
          cachedAt: Date.now(),
          events: nextEvents,
          groups,
          key: eventRecordsDataCacheKey(viewMode, filters),
          sites,
          skip,
          summary: nextSummary,
          total: nextTotal,
        });
      } else {
        const data = await api<AccountsResponse>(`/event-records/accounts?${params.toString()}`, token);
        const nextAccounts = data.items || [];
        const nextSummary = data.summary || {};
        const nextTotal = numberValue(data.total);
        setAccounts(nextAccounts);
        setEvents([]);
        setSummary(nextSummary);
        setTotal(nextTotal);
        writeEventRecordsDataCache({
          accounts: nextAccounts,
          cachedAt: Date.now(),
          events: [],
          groups,
          key: eventRecordsDataCacheKey(viewMode, filters),
          sites,
          skip,
          summary: nextSummary,
          total: nextTotal,
        });
      }
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setLoading(false);
    }
  };

  const openAccountDetail = async (identityId?: string | null) => {
    if (!identityId) return;
    setDetailIdentityId(identityId);
    setDetailLoading(true);
    try {
      const data = await api<AccountDetail>(`/event-records/accounts/${encodeURIComponent(identityId)}`, token);
      setDetail(data);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setDetailLoading(false);
    }
  };

  const switchViewMode = (nextViewMode: ViewMode) => {
    const cached = getEventRecordsDataCache(nextViewMode, filters);
    setViewMode(nextViewMode);
    setSkip(cached?.skip ?? 0);
  };

  const applyFilters = () => {
    setFilters(draftFilters);
    setSkip(0);
    setFilterOpen(false);
  };

  const resetFilters = () => {
    setDraftFilters(initialFilters);
    setFilters(initialFilters);
    setViewMode("events");
    setSkip(0);
    setFilterOpen(false);
    clearCachedEventRecordsState();
    clearEventRecordsDataCache();
  };

  const setDraft = <K extends keyof Filters>(key: K, value: Filters[K]) => {
    setDraftFilters((current) => {
      const next = { ...current, [key]: value };
      if (key === "site_id") next.group_id = "";
      return next;
    });
  };

  useEffect(() => {
    loadSites();
  }, []);

  useEffect(() => {
    loadGroups(draftFilters.site_id);
  }, [draftFilters.site_id]);

  useEffect(() => {
    loadData();
  }, [viewMode, filters, skip]);

  useEffect(() => {
    setDraftFilters(filters);
  }, [filters]);

  useEffect(() => {
    saveCachedEventRecordsState({ filters, viewMode });
  }, [filters, viewMode]);

  return (
    <section className="view event-records-page">
      <header className="event-records-head">
        <div>
          <h2>事件记录</h2>
          <p>账号运营时间线、封号、恢复、远端删除、用量清零累计和通知投递都集中在这里。</p>
        </div>
        <div className="event-records-head-actions">
          <div className="account-view-menu event-records-tabs">
            <button className={`account-view-menu-item ${viewMode === "events" ? "active" : ""}`} type="button" onClick={() => switchViewMode("events")}>
              事件流
              <span>按时间倒序</span>
            </button>
            <button className={`account-view-menu-item ${viewMode === "accounts" ? "active" : ""}`} type="button" onClick={() => switchViewMode("accounts")}>
              账号视图
              <span>按邮箱聚合</span>
            </button>
          </div>
          <div className="floating-menu-wrap">
            <button className="ghost compact-button" type="button" onClick={() => { setFilterOpen((value) => !value); setDisplayOpen(false); }}>
              筛选{activeFilterCount ? ` · ${activeFilterCount}` : ""}
            </button>
            {filterOpen && (
              <div className="floating-menu event-filter-menu">
                <FilterMenu
                  draft={draftFilters}
                  groups={groups}
                  onApply={applyFilters}
                  onReset={resetFilters}
                  setDraft={setDraft}
                  sites={sites}
                  viewMode={viewMode}
                />
              </div>
            )}
          </div>
          <div className="floating-menu-wrap">
            <button className="ghost compact-button" type="button" onClick={() => { setDisplayOpen((value) => !value); setFilterOpen(false); }}>
              显示
            </button>
            {displayOpen && (
              <div className="floating-menu event-display-menu">
                <label className="inline-select">
                  <span>每页</span>
                  <select value={draftFilters.limit} onChange={(event) => {
                    const limit = Number(event.target.value);
                    setDraft("limit", limit);
                    setFilters((current) => ({ ...current, limit }));
                    setSkip(0);
                  }}>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                    <option value={200}>200</option>
                    <option value={500}>500</option>
                  </select>
                </label>
                <p>列宽会保持紧凑；详细原始数据请点行打开右侧详情。</p>
              </div>
            )}
          </div>
          <button className="compact-button" type="button" disabled={loading} onClick={() => loadData({ force: true })}>
            {loading ? "刷新中..." : "刷新"}
          </button>
        </div>
      </header>

      <section className="event-summary-strip">
        <SummaryItem label="事件" value={summary.total_events ?? total} />
        <SummaryItem label="401" value={summary.detected_401} tone="danger" />
        <SummaryItem label="1h封号" value={summary.one_hour_401} tone="danger" />
        <SummaryItem label="今日封号" value={summary.today_401} tone="warning" />
        <SummaryItem label="异常账号" value={summary.current_abnormal_accounts} tone="warning" />
        <SummaryItem label="清零累计" value={summary.usage_rollovers} />
        <SummaryItem label="官方刷新" value={summary.official_usage_refreshes} />
        <SummaryItem label="累计消耗" value={formatUsd(summary.cumulative_actual_cost)} strong />
        <SummaryItem label="最近事件" value={formatDateTime(summary.last_event_at)} />
      </section>

      <section className="event-active-filters">
        <span>{viewMode === "events" ? "事件流" : "账号视图"}</span>
        {filterBadges(filters, sites, groups).map((item) => (
          <b key={item}>{item}</b>
        ))}
        {!activeFilterCount && <em>默认最近 24h，筛选项在菜单里</em>}
        <strong>{total ? `${skip + 1}-${Math.min(skip + filters.limit, total)} / ${total}` : "0 / 0"}</strong>
      </section>

      {viewMode === "events" ? (
        <EventTable items={events} loading={loading} onOpenDetail={openAccountDetail} />
      ) : (
        <AccountTable items={accounts} loading={loading} onOpenDetail={openAccountDetail} />
      )}

      <div className="pagination event-pagination">
        <button className="ghost" type="button" disabled={skip <= 0 || loading} onClick={() => setSkip(Math.max(0, skip - filters.limit))}>
          上一页
        </button>
        <button className="ghost" type="button" disabled={skip + filters.limit >= total || loading} onClick={() => setSkip(skip + filters.limit)}>
          下一页
        </button>
        <span className="muted">当前 {visibleRowsCount} 条 · 每页 {filters.limit}</span>
      </div>

      {detailIdentityId && (
        <AccountDetailDrawer
          detail={detail}
          loading={detailLoading}
          onClose={() => {
            setDetailIdentityId(null);
            setDetail(null);
          }}
        />
      )}
    </section>
  );
}

function loadCachedEventRecordsState(): { filters: Filters; viewMode: ViewMode } {
  if (typeof window === "undefined") {
    return { filters: initialFilters, viewMode: "events" };
  }
  try {
    const raw = window.localStorage.getItem(EVENT_RECORDS_STATE_STORAGE_KEY);
    if (!raw) return { filters: initialFilters, viewMode: "events" };
    const cached = JSON.parse(raw) as CachedEventRecordsState;
    return {
      filters: normalizeCachedFilters(cached.filters),
      viewMode: cached.viewMode === "accounts" ? "accounts" : "events",
    };
  } catch {
    return { filters: initialFilters, viewMode: "events" };
  }
}

function saveCachedEventRecordsState(state: { filters: Filters; viewMode: ViewMode }) {
  try {
    window.localStorage.setItem(EVENT_RECORDS_STATE_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Local storage can be unavailable in private mode; the page still works without persistence.
  }
}

function clearCachedEventRecordsState() {
  try {
    window.localStorage.removeItem(EVENT_RECORDS_STATE_STORAGE_KEY);
  } catch {
    // Ignore storage failures; resetting in memory is enough for the active session.
  }
}

function eventRecordsDataCacheKey(viewMode: ViewMode, filters: Filters) {
  return JSON.stringify({ viewMode, filters });
}

function getEventRecordsDataCache(viewMode: ViewMode, filters: Filters): EventRecordsDataCache | null {
  return eventRecordsDataCache.get(eventRecordsDataCacheKey(viewMode, filters)) || null;
}

function writeEventRecordsDataCache(value: EventRecordsDataCache) {
  eventRecordsDataCache.set(value.key, value);
  if (eventRecordsDataCache.size <= EVENT_RECORDS_DATA_CACHE_LIMIT) return;
  const oldest = [...eventRecordsDataCache.values()].sort((a, b) => a.cachedAt - b.cachedAt)[0];
  if (oldest) eventRecordsDataCache.delete(oldest.key);
}

function updateEventRecordsDataCache(value: Partial<Pick<EventRecordsDataCache, "groups" | "sites">>) {
  if (!eventRecordsDataCache.size) return;
  eventRecordsDataCache = new Map(
    [...eventRecordsDataCache.entries()].map(([key, cache]) => [
      key,
      {
        ...cache,
        ...value,
        cachedAt: Date.now(),
      },
    ]),
  );
}

function clearEventRecordsDataCache() {
  eventRecordsDataCache.clear();
}

function restoreEventRecordsDataCache(
  cached: EventRecordsDataCache,
  setters: {
    setAccounts: (items: EventAccount[]) => void;
    setEvents: (items: EventRecord[]) => void;
    setGroups: (items: Group[]) => void;
    setSites: (items: Site[]) => void;
    setSummary: (summary: EventSummary) => void;
    setTotal: (total: number) => void;
  },
) {
  setters.setEvents(cached.events);
  setters.setAccounts(cached.accounts);
  setters.setSummary(cached.summary);
  setters.setTotal(cached.total);
  if (cached.sites.length) setters.setSites(cached.sites);
  if (cached.groups.length) setters.setGroups(cached.groups);
}

function normalizeCachedFilters(value?: Partial<Filters>): Filters {
  if (!value || typeof value !== "object") return initialFilters;
  return {
    range: typeof value.range === "string" && value.range ? value.range : initialFilters.range,
    site_id: typeof value.site_id === "string" ? value.site_id : initialFilters.site_id,
    group_id: typeof value.group_id === "string" ? value.group_id : initialFilters.group_id,
    event_type: typeof value.event_type === "string" ? value.event_type : initialFilters.event_type,
    severity: typeof value.severity === "string" ? value.severity : initialFilters.severity,
    account_type: typeof value.account_type === "string" ? value.account_type : initialFilters.account_type,
    presence: typeof value.presence === "string" ? value.presence : initialFilters.presence,
    q: typeof value.q === "string" ? value.q : initialFilters.q,
    only_401: Boolean(value.only_401),
    only_abnormal: Boolean(value.only_abnormal),
    only_pro: Boolean(value.only_pro),
    only_cumulative: Boolean(value.only_cumulative),
    only_delete_archive: Boolean(value.only_delete_archive),
    limit: typeof value.limit === "number" && allowedLimits.has(value.limit) ? value.limit : initialFilters.limit,
  };
}

function FilterMenu({
  draft,
  groups,
  onApply,
  onReset,
  setDraft,
  sites,
  viewMode,
}: {
  draft: Filters;
  groups: Group[];
  onApply: () => void;
  onReset: () => void;
  setDraft: <K extends keyof Filters>(key: K, value: Filters[K]) => void;
  sites: Site[];
  viewMode: ViewMode;
}) {
  return (
    <div className="event-filter-grid">
      <label>
        <span>时间</span>
        <select value={draft.range} onChange={(event) => setDraft("range", event.target.value)}>
          <option value="1h">最近 1h</option>
          <option value="6h">最近 6h</option>
          <option value="24h">最近 24h</option>
          <option value="today">今天</option>
          <option value="7d">最近 7d</option>
          <option value="all">全部</option>
        </select>
      </label>
      <label>
        <span>站点</span>
        <select value={draft.site_id} onChange={(event) => setDraft("site_id", event.target.value)}>
          <option value="">全部站点</option>
          {sites.map((site) => (
            <option key={site.id} value={site.id}>{site.name || site.id}</option>
          ))}
        </select>
      </label>
      <label>
        <span>分组</span>
        <select value={draft.group_id} onChange={(event) => setDraft("group_id", event.target.value)} disabled={!draft.site_id}>
          <option value="">全部分组</option>
          {groups.map((group) => (
            <option key={group.id} value={group.id}>{group.name}</option>
          ))}
        </select>
      </label>
      {viewMode === "events" ? (
        <>
          <label>
            <span>事件</span>
            <select value={draft.event_type} onChange={(event) => setDraft("event_type", event.target.value)}>
              {eventTypeOptions.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label>
            <span>级别</span>
            <select value={draft.severity} onChange={(event) => setDraft("severity", event.target.value)}>
              <option value="">全部级别</option>
              <option value="critical">critical</option>
              <option value="warning">warning</option>
              <option value="info">info</option>
            </select>
          </label>
        </>
      ) : (
        <label>
          <span>存在状态</span>
          <select value={draft.presence} onChange={(event) => setDraft("presence", event.target.value)}>
            <option value="">全部</option>
            <option value="present">仍在 sub2</option>
            <option value="missing_suspected">疑似删除</option>
            <option value="removed">已确认删除</option>
          </select>
        </label>
      )}
      <label>
        <span>账号类型</span>
        <select value={draft.account_type} onChange={(event) => setDraft("account_type", event.target.value)}>
          <option value="">全部类型</option>
          <option value="free">free</option>
          <option value="plus">plus</option>
          <option value="team">team</option>
          <option value="bug_team">bug team</option>
          <option value="k12">k12</option>
          <option value="pro">pro</option>
        </select>
      </label>
      <label className="span-2">
        <span>搜索</span>
        <input value={draft.q} onChange={(event) => setDraft("q", event.target.value)} placeholder="邮箱 / name / remote id / 错误内容" />
      </label>
      <div className="event-filter-checks span-2">
        <label className="inline-check"><input checked={draft.only_401} onChange={(event) => setDraft("only_401", event.target.checked)} type="checkbox" />只看 401</label>
        <label className="inline-check"><input checked={draft.only_abnormal} onChange={(event) => setDraft("only_abnormal", event.target.checked)} type="checkbox" />只看异常</label>
        <label className="inline-check"><input checked={draft.only_pro} onChange={(event) => setDraft("only_pro", event.target.checked)} type="checkbox" />只看 pro</label>
        <label className="inline-check"><input checked={draft.only_cumulative} onChange={(event) => setDraft("only_cumulative", event.target.checked)} type="checkbox" />只看累计用量</label>
        {viewMode === "events" && <label className="inline-check"><input checked={draft.only_delete_archive} onChange={(event) => setDraft("only_delete_archive", event.target.checked)} type="checkbox" />删除/归档相关</label>}
      </div>
      <div className="event-filter-actions span-2">
        <button className="ghost compact-button" type="button" onClick={onReset}>重置</button>
        <button className="compact-button" type="button" onClick={onApply}>应用筛选</button>
      </div>
    </div>
  );
}

function EventTable({ items, loading, onOpenDetail }: { items: EventRecord[]; loading: boolean; onOpenDetail: (identityId?: string | null) => void }) {
  return (
    <div className="table-wrap event-table-wrap">
      <table className="event-record-table">
        <thead>
          <tr>
            <th>时间 / 事件</th>
            <th>账号</th>
            <th>站点 / 分组</th>
            <th>状态</th>
            <th>使用时长</th>
            <th>用量累计</th>
            <th>通知 / 操作</th>
            <th>错误摘要</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} className="clickable-row" onClick={() => onOpenDetail(item.identity_id)}>
              <td>
                <div className="cell-main">{formatDateTime(item.detected_at || item.occurred_at)}</div>
                <div className="event-type-line">
                  <StatusPill value={eventTypeLabel(item.event_type)} tone={eventTone(item.severity, item.is_401)} />
                  <span>{item.severity || "info"}</span>
                </div>
              </td>
              <td>
                <div className="cell-main truncate" title={item.name || item.email || item.normalized_email}>{item.name || item.email || item.normalized_email || "-"}</div>
                <div className="cell-sub">{item.normalized_email || item.email || "-"}</div>
                <div className="cell-sub">remote {remoteIdsText(item.remote_account_ids, item.remote_account_id)}</div>
              </td>
              <td>
                <div>{item.site_name || item.site_id || "-"}</div>
                <div className="cell-sub">{item.group_names?.length ? item.group_names.join(" / ") : "-"}</div>
                <div className="account-tags"><span className={`account-tag ${planTagTone(item.plan_type)}`}>{displayPlan(item.plan_type)}</span></div>
              </td>
              <td>
                <StatusPill value={displayStatus(item.current_status)} tone={statusTone(item.current_status, item.is_401)} />
                <div className="cell-sub">调度 {item.current_schedulable === true ? "开启" : item.current_schedulable === false ? "关闭" : "-"}</div>
              </td>
              <td>
                <div>{formatDuration(item.usage_duration_seconds)}</div>
                <div className="cell-sub">正常 {formatDuration(item.normal_use_seconds)}</div>
              </td>
              <td>
                <UsageBlock usage={item.usage_snapshot} cumulative={item.cumulative_usage_snapshot} />
              </td>
              <td>
                <div>{displayNotification(item.notification_status)}</div>
                <div className="cell-sub">{item.uploader_name ? `上传 ${item.uploader_name}` : "上传 -"}</div>
                <div className="cell-sub">{item.last_operation_name || item.last_operation_by_name || "-"}</div>
              </td>
              <td className="event-error-cell" title={item.current_error_message || item.raw_excerpt || ""}>
                {item.current_error_message || item.raw_excerpt || <span className="muted">-</span>}
              </td>
            </tr>
          ))}
          {!items.length && (
            <tr>
              <td colSpan={8} className="muted">{loading ? "正在加载事件..." : "暂无事件"}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function AccountTable({ items, loading, onOpenDetail }: { items: EventAccount[]; loading: boolean; onOpenDetail: (identityId?: string | null) => void }) {
  return (
    <div className="table-wrap event-table-wrap">
      <table className="event-account-table">
        <thead>
          <tr>
            <th>账号</th>
            <th>站点 / 分组</th>
            <th>当前状态</th>
            <th>生命周期</th>
            <th>封号 / 恢复</th>
            <th>累计用量</th>
            <th>本地信息</th>
            <th>错误摘要</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id} className="clickable-row" onClick={() => onOpenDetail(item.identity_id || item.id)}>
              <td>
                <div className="cell-main truncate" title={item.name || item.email || item.normalized_email}>{item.name || item.email || item.normalized_email || "-"}</div>
                <div className="cell-sub">{item.normalized_email || item.email || "-"}</div>
                <div className="cell-sub">remote {remoteIdsText(item.current_remote_account_ids, item.current_remote_account_id)}</div>
              </td>
              <td>
                <div>{item.site_name || item.site_id || "-"}</div>
                <div className="cell-sub">{item.group_names?.length ? item.group_names.join(" / ") : "-"}</div>
                <div className="account-tags"><span className={`account-tag ${planTagTone(item.plan_type)}`}>{displayPlan(item.plan_type)}</span></div>
              </td>
              <td>
                <StatusPill value={displayStatus(item.current_status)} tone={statusTone(item.current_status, item.current_is_401)} />
                <div className="cell-sub">{presenceLabel(item.current_presence)} · 调度 {item.current_schedulable === true ? "开启" : item.current_schedulable === false ? "关闭" : "-"}</div>
                {numberValue(item.duplicate_remote_count) > 1 && <div className="cell-sub danger">重复 remote id：{item.duplicate_remote_count}</div>}
              </td>
              <td>
                <div>首见 {formatDateTime(item.first_seen_at)}</div>
                <div className="cell-sub">最近 {formatDateTime(item.last_seen_at)}</div>
                <div className="cell-sub">总时长 {formatDuration(item.lifetime_seconds)}</div>
              </td>
              <td>
                <div>401 {numberValue(item.total_401_count)} 次</div>
                <div className="cell-sub">最近 {formatDateTime(item.last_401_at)}</div>
                <div className="cell-sub">恢复 {numberValue(item.total_recovery_count)} 次</div>
              </td>
              <td>
                <UsageBlock usage={item.last_usage_snapshot} cumulative={item.cumulative_usage_snapshot} />
              </td>
              <td>
                <div>{item.uploader_name ? `上传 ${item.uploader_name}` : "上传 -"}</div>
                <div className="cell-sub">{item.last_operation_name || "-"}</div>
                <div className="cell-sub">{formatDateTime(item.last_operation_at)}</div>
              </td>
              <td className="event-error-cell" title={item.current_error_message || ""}>
                {item.current_error_message || <span className="muted">-</span>}
              </td>
            </tr>
          ))}
          {!items.length && (
            <tr>
              <td colSpan={8} className="muted">{loading ? "正在加载账号..." : "暂无账号记录"}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function AccountDetailDrawer({ detail, loading, onClose }: { detail: AccountDetail | null; loading: boolean; onClose: () => void }) {
  const identity = detail?.identity;
  return (
    <div className="drawer-backdrop" role="dialog" aria-modal="true">
      <aside className="drawer-panel event-detail-drawer">
        <div className="drawer-header">
          <div>
            <h3>账号事件详情</h3>
            <p>{identity?.normalized_email || identity?.email || "加载中..."}</p>
          </div>
          <button className="ghost compact-button" type="button" onClick={onClose}>关闭</button>
        </div>
        {loading && !detail ? (
          <div className="empty-state">正在加载详情...</div>
        ) : detail && identity ? (
          <div className="event-detail-content">
            <section className="event-detail-summary">
              <DetailMetric label="站点" value={identity.site_name || identity.site_id || "-"} />
              <DetailMetric label="分组" value={identity.group_names?.join(" / ") || "-"} />
              <DetailMetric label="状态" value={displayStatus(identity.current_status)} />
              <DetailMetric label="生命周期" value={formatDuration(identity.lifetime_seconds)} />
              <DetailMetric label="401次数" value={numberValue(identity.total_401_count)} />
              <DetailMetric label="累计消耗" value={formatUsd(firstUsageNumber(identity.cumulative_usage_snapshot, identity.cumulative_usage_totals, ["codex_total_actual_cost_cumulative", "codex_total_actual_cost"]))} />
            </section>
            <section>
              <h4>事件时间线</h4>
              <div className="event-timeline">
                {detail.events.map((event) => (
                  <div className="event-timeline-item" key={event.id}>
                    <span>{formatDateTime(event.detected_at || event.occurred_at)}</span>
                    <strong>{eventTypeLabel(event.event_type)}</strong>
                    <em>{displayStatus(event.current_status)} · {event.current_error_message || event.raw_excerpt || "-"}</em>
                  </div>
                ))}
              </div>
            </section>
            <section>
              <h4>Sub2 会话</h4>
              <div className="event-session-list">
                {detail.sessions.map((session) => (
                  <div className="event-session-item" key={String(session.id || session._id || session.remote_account_id)}>
                    <strong>remote #{String(session.remote_account_id || "-")}</strong>
                    <span>{formatDateTime(session.started_at)} - {formatDateTime(session.ended_at)}</span>
                    <em>{String(session.status || "-")} · {formatDuration(session.duration_seconds)}</em>
                  </div>
                ))}
                {!detail.sessions.length && <div className="muted">暂无会话记录</div>}
              </div>
            </section>
            <section>
              <h4>原始数据</h4>
              <pre className="event-raw-json">{pretty(detail.raw || detail)}</pre>
            </section>
          </div>
        ) : (
          <div className="empty-state">未找到详情</div>
        )}
      </aside>
    </div>
  );
}

function SummaryItem({ label, value, tone, strong }: { label: string; value: unknown; tone?: "danger" | "warning"; strong?: boolean }) {
  return (
    <div className={`event-summary-item ${tone || ""} ${strong ? "strong" : ""}`}>
      <span>{label}</span>
      <b>{value === null || value === undefined || value === "" ? "-" : String(value)}</b>
    </div>
  );
}

function DetailMetric({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value === null || value === undefined || value === "" ? "-" : String(value)}</strong>
    </div>
  );
}

function UsageBlock({ usage, cumulative }: { usage?: Record<string, unknown>; cumulative?: Record<string, unknown> }) {
  const fiveHour = firstUsageNumber(usage, cumulative, ["codex_5h_actual_cost_cumulative", "codex_5h_actual_cost"]);
  const sevenDay = firstUsageNumber(usage, cumulative, ["codex_7d_actual_cost_cumulative", "codex_7d_actual_cost"]);
  const total = firstUsageNumber(cumulative, usage, ["codex_total_actual_cost_cumulative", "codex_total_actual_cost", "codex_7d_actual_cost_cumulative", "codex_7d_actual_cost"]);
  const requests = firstUsageNumber(cumulative, usage, ["codex_total_request_count_cumulative", "codex_total_request_count", "codex_7d_request_count_cumulative", "codex_7d_request_count"]);
  return (
    <div className="usage-mini-block">
      <span>5h {formatUsd(fiveHour)}</span>
      <span>7d {formatUsd(sevenDay)}</span>
      <b>累计 {formatUsd(total)}</b>
      <em>{formatCompactNumber(requests)} req</em>
    </div>
  );
}

function StatusPill({ value, tone = "muted" }: { value: string; tone?: "accent" | "success" | "warning" | "danger" | "muted" }) {
  return <span className={`status-pill ${tone}`}>{value}</span>;
}

function buildParams(filters: Filters, viewMode: ViewMode, skip: number) {
  const params = new URLSearchParams({
    skip: String(skip),
    limit: String(filters.limit),
  });
  if (viewMode === "events") params.set("range", filters.range);
  for (const key of ["site_id", "group_id", "account_type", "q"] as const) {
    if (filters[key]) params.set(key, String(filters[key]));
  }
  if (viewMode === "events") {
    for (const key of ["event_type", "severity"] as const) {
      if (filters[key]) params.set(key, String(filters[key]));
    }
    if (filters.only_delete_archive) params.set("only_delete_archive", "true");
  } else if (filters.presence) {
    params.set("presence", filters.presence);
  }
  for (const key of ["only_401", "only_abnormal", "only_pro", "only_cumulative"] as const) {
    if (filters[key]) params.set(key, "true");
  }
  return params;
}

function countActiveFilters(filters: Filters) {
  let count = 0;
  if (filters.range !== "24h") count += 1;
  for (const key of ["site_id", "group_id", "event_type", "severity", "account_type", "presence", "q"] as const) {
    if (filters[key]) count += 1;
  }
  for (const key of ["only_401", "only_abnormal", "only_pro", "only_cumulative", "only_delete_archive"] as const) {
    if (filters[key]) count += 1;
  }
  return count;
}

function filterBadges(filters: Filters, sites: Site[], groups: Group[]) {
  const result: string[] = [];
  if (filters.range !== "24h") result.push(rangeLabel(filters.range));
  if (filters.site_id) result.push(sites.find((site) => site.id === filters.site_id)?.name || filters.site_id);
  if (filters.group_id) result.push(groups.find((group) => String(group.id) === filters.group_id)?.name || `group #${filters.group_id}`);
  if (filters.event_type) result.push(eventTypeLabel(filters.event_type));
  if (filters.severity) result.push(filters.severity);
  if (filters.account_type) result.push(filters.account_type);
  if (filters.presence) result.push(presenceLabel(filters.presence));
  if (filters.q) result.push(`搜索：${filters.q}`);
  if (filters.only_401) result.push("只看401");
  if (filters.only_abnormal) result.push("只看异常");
  if (filters.only_pro) result.push("只看pro");
  if (filters.only_cumulative) result.push("累计用量");
  if (filters.only_delete_archive) result.push("删除/归档");
  return result;
}

function eventTypeLabel(value?: string) {
  const labels: Record<string, string> = {
    remote_account_seen_first: "首次发现",
    remote_account_reappeared: "重新出现",
    status_changed: "状态变化",
    error_changed: "错误变化",
    schedulable_changed: "调度变化",
    group_changed: "分组变化",
    "401_detected": "401 封号",
    "401_recovered": "401 恢复",
    usage_rollover: "用量清零累计",
    official_usage_refresh: "官方额度刷新",
    missing_suspected: "疑似远端删除",
    remote_removed_confirmed: "确认远端删除",
    duplicate_email_detected: "重复邮箱",
    duplicate_email_resolved: "重复邮箱已解除",
  };
  return labels[value || ""] || value || "事件";
}

function rangeLabel(value: string) {
  const labels: Record<string, string> = {
    "1h": "最近1h",
    "6h": "最近6h",
    "24h": "最近24h",
    today: "今天",
    "7d": "最近7d",
    all: "全部时间",
  };
  return labels[value] || value;
}

function displayStatus(value?: string) {
  const normalized = (value || "").toLowerCase();
  if (normalized === "active") return "正常";
  if (normalized === "error") return "异常";
  if (normalized === "disabled") return "禁用";
  if (normalized === "paused") return "暂停";
  if (normalized === "failed") return "失败";
  if (!value) return "unknown";
  return value;
}

function statusTone(value?: string, is401?: boolean): "accent" | "success" | "warning" | "danger" | "muted" {
  const normalized = (value || "").toLowerCase();
  if (is401) return "danger";
  if (["active", "ok", "healthy"].includes(normalized)) return "success";
  if (["error", "failed", "disabled", "invalid", "banned"].includes(normalized)) return "danger";
  if (["paused", "warning"].includes(normalized)) return "warning";
  return value ? "accent" : "muted";
}

function eventTone(severity?: string, is401?: boolean): "accent" | "success" | "warning" | "danger" | "muted" {
  if (is401 || severity === "critical") return "danger";
  if (severity === "warning") return "warning";
  if (severity === "info") return "accent";
  return "muted";
}

function planTagTone(value?: string): string {
  const normalized = (value || "").toLowerCase();
  if (normalized === "bug_team") return "plan-team";
  if (normalized === "plus") return "plan-plus";
  if (normalized === "free") return "plan-free";
  if (normalized === "team") return "plan-team";
  if (normalized === "k12") return "plan-k12";
  if (normalized === "pro") return "plan-pro";
  return "plan-other";
}

function displayPlan(value?: string) {
  if (!value) return "unknown";
  if (value.toLowerCase() === "bug_team") return "Bug Team";
  if (value.toLowerCase() === "k12") return "K12";
  return value;
}

function presenceLabel(value?: string) {
  if (value === "present") return "仍在 sub2";
  if (value === "missing_suspected") return "疑似删除";
  if (value === "removed") return "已确认删除";
  return value || "-";
}

function displayNotification(value?: string) {
  if (!value) return "-";
  if (value === "skipped_non_pro") return "非pro跳过";
  if (value === "queued_batch") return "已进入聚合";
  if (value === "sent_batch") return "已通知";
  if (value === "failed_batch" || value === "failed") return "通知失败";
  return value;
}

function remoteIdsText(values?: Array<number | string>, fallback?: number | string) {
  const items = [...(values || [])];
  if (fallback !== undefined && fallback !== null) items.push(fallback);
  const unique = [...new Set(items.filter((item) => item !== "" && item !== null && item !== undefined).map((item) => String(item)))];
  return unique.length ? unique.map((item) => `#${item}`).join(" / ") : "-";
}

function usageNumber(source: Record<string, unknown> | undefined, key: string): number | null {
  if (!source) return null;
  const value = source[key];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function firstUsageNumber(
  primary: Record<string, unknown> | undefined,
  fallback: Record<string, unknown> | undefined,
  keys: string[],
): number | null {
  for (const source of [primary, fallback]) {
    for (const key of keys) {
      const value = usageNumber(source, key);
      if (value !== null) return value;
    }
  }
  return null;
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function formatUsd(value: unknown) {
  const number = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(number)) return "$-";
  if (Math.abs(number) >= 100) return `$${number.toFixed(0)}`;
  if (Math.abs(number) >= 10) return `$${number.toFixed(1)}`;
  return `$${number.toFixed(2)}`;
}

function formatCompactNumber(value: unknown) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "-";
  if (Math.abs(number) >= 1_000_000) return `${(number / 1_000_000).toFixed(1)}m`;
  if (Math.abs(number) >= 1_000) return `${(number / 1_000).toFixed(1)}k`;
  return String(Math.round(number));
}

function formatDuration(value: unknown) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds <= 0) return "-";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}天 ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}
