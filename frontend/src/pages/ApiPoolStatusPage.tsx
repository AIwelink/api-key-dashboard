import { Fragment, type ReactNode, useEffect, useId, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { AnimatedValue, AutoRefreshAnimationContext } from "../components/AnimatedValue";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import { isCurrentSiteRequest, mergeCapacitySummaryForRequest } from "../utils/apiPoolRequestState";
import { concurrencyCoverageScalePercent, concurrencyCoverageTone, runwayScalePercent, runwayTone } from "../utils/capacityScale";
import { errorMessage, formatDateTime, parseDisplayDate, text } from "../utils/format";
import { poolTrafficMetrics } from "../utils/poolTrafficMetrics";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type Site = {
  id: string;
  name: string;
  base_url?: string;
  status: string;
  token_configured: boolean;
  source?: string;
  refresh_interval_minutes?: number;
  auto_remove_abnormal_accounts?: boolean;
  auto_remove_refresh?: RefreshResponse;
};

type Group = {
  id: number;
  name: string;
  platform?: string;
  status?: string;
  account_count?: number;
  active_account_count?: number;
  rate_limited_account_count?: number;
  rate_multiplier?: number;
  subscription_type?: string;
  capacity_summary?: CapacitySummary;
};

type ForecastAccuracyHorizon = {
  key: string;
  label: string;
  sample_count?: number;
  p50_wape_percent?: number | null;
  p50_bias_percent?: number | null;
  p90_coverage_percent?: number | null;
};

type ForecastAccuracyWindow = {
  hourly_sample_count?: number;
  nowcast_sample_count?: number;
  p50_wape_percent?: number | null;
  p50_bias_percent?: number | null;
  p50_mae_usd?: number | null;
  p90_coverage_percent?: number | null;
  p90_pinball_loss_usd?: number | null;
  nowcast_selected_wape_percent?: number | null;
  nowcast_model_wape_percent?: number | null;
  nowcast_realtime_wape_percent?: number | null;
  horizon_buckets?: ForecastAccuracyHorizon[];
};

type ForecastAccuracy = {
  status?: "ready" | "waiting";
  model?: string | null;
  version?: string | null;
  updated_at?: string | null;
  last_finalized_at?: string | null;
  windows?: Partial<Record<"24h" | "7d" | "28d", ForecastAccuracyWindow>>;
};

type CapacitySummary = {
  capacity_model?: string;
  forecast_accuracy?: ForecastAccuracy | null;
  available_accounts?: number;
  available_5h_accounts?: number;
  pool_normal_accounts?: number;
  pool_active_normal_accounts?: number;
  pool_five_hour_rate_limited_accounts?: number;
  pool_seven_day_rate_limited_accounts?: number;
  pool_abnormal_accounts?: number;
  pool_excluded_bug_team_accounts?: number;
  concurrency_actual_in_use?: number;
  concurrency_actual_available?: number;
  concurrency_safe_available?: number;
  concurrency_near_limit_available?: number;
  concurrency_total_capacity?: number;
  concurrency_temporarily_unavailable?: number;
  concurrency_temporarily_unavailable_accounts?: number;
  concurrency_used_percent?: number;
  concurrency_available_percent?: number;
  concurrency_eligible_accounts?: number;
  concurrency_available_accounts?: number;
  concurrency_safe_accounts?: number;
  concurrency_near_limit_accounts?: number;
  concurrency_five_hour_limited_accounts?: number;
  concurrency_short_seven_day_limited_accounts?: number;
  concurrency_other_unavailable_accounts?: number;
  concurrency_long_seven_day_limited_accounts?: number;
  account_type?: string;
  five_hour_capacity_usd?: number;
  active_five_hour_capacity_usd?: number;
  reserve_five_hour_capacity_usd?: number;
  dynamic_five_hour_capacity_usd?: number;
  active_dynamic_five_hour_capacity_usd?: number;
  reserve_dynamic_five_hour_capacity_usd?: number;
  twenty_four_hour_capacity_usd?: number;
  seven_day_capacity_usd?: number;
  active_seven_day_capacity_usd?: number;
  reserve_seven_day_capacity_usd?: number;
  five_hour_used_estimated_usd?: number;
  five_hour_remaining_estimated_usd?: number;
  dynamic_five_hour_used_estimated_usd?: number;
  dynamic_five_hour_remaining_estimated_usd?: number;
  active_dynamic_five_hour_used_estimated_usd?: number;
  active_dynamic_five_hour_remaining_estimated_usd?: number;
  reserve_dynamic_five_hour_remaining_estimated_usd?: number;
  five_hour_actual_used_usd?: number;
  five_hour_actual_remaining_usd?: number;
  active_five_hour_actual_remaining_usd?: number;
  reserve_five_hour_actual_remaining_usd?: number;
  seven_day_used_estimated_usd?: number;
  seven_day_remaining_estimated_usd?: number;
  seven_day_actual_used_usd?: number;
  seven_day_actual_remaining_usd?: number;
  active_seven_day_actual_remaining_usd?: number;
  reserve_seven_day_actual_remaining_usd?: number;
  five_hour_peak_cost?: number;
  seven_day_five_hour_peak_cost?: number;
  recent_day_five_hour_peak_cost?: number;
  burst_1h_observed_cost?: number;
  burst_1h_elapsed_minutes?: number;
  burst_1h_projection_multiplier?: number;
  burst_1h_cost?: number;
  burst_1h_previous_cost?: number;
  burst_1h_five_hour_estimated_cost?: number;
  burst_1h_five_hour_multiple?: number | null;
  active_burst_1h_five_hour_multiple?: number | null;
  burst_1h_source?: string;
  burst_1h_window_count?: number;
  burst_1h_trend?: "rising" | "falling" | "flat" | "unknown";
  burst_1h_trend_label?: string;
  burst_1h_trend_strength?: "extreme" | "strong" | "medium" | "weak" | "unknown";
  burst_1h_trend_strength_label?: string;
  burst_1h_trend_change_percent?: number | null;
  burst_1h_trend_recent_avg_cost?: number;
  burst_1h_trend_baseline_avg_cost?: number;
  burst_1h_trend_recent_hours?: number;
  burst_1h_trend_baseline_hours?: number;
  seven_day_24h_peak_cost?: number;
  recent_5h_cost?: number;
  recent_24h_cost?: number;
  estimated_5h_consumed_accounts?: number | null;
  estimated_24h_consumed_accounts?: number | null;
  estimated_recent_24h_consumed_accounts?: number | null;
  estimated_seven_day_peak_24h_consumed_accounts?: number | null;
  seven_day_cost?: number;
  recent_5h_remaining_usd?: number;
  recent_24h_remaining_usd?: number;
  seven_day_remaining_usd?: number;
  five_hour_peak_multiple?: number | null;
  recent_day_five_hour_peak_multiple?: number | null;
  active_recent_day_five_hour_peak_multiple?: number | null;
  recent_5h_multiple?: number | null;
  twenty_four_hour_peak_multiple?: number | null;
  recent_24h_multiple?: number | null;
  five_x_peak_multiple?: number | null;
  five_x_recent_day_peak_multiple?: number | null;
  five_x_24h_peak_multiple?: number | null;
  ten_x_peak_multiple?: number | null;
  current_speed_multiple?: number | null;
  current_speed_days?: number | null;
  active_current_speed_days?: number | null;
  active_five_hour_peak_multiple?: number | null;
  five_x_speed_days?: number | null;
  active_five_x_speed_days?: number | null;
  recent_day_five_hour_peak_daily_cost?: number;
  seven_day_five_hour_peak_daily_cost?: number;
  recent_day_five_hour_peak_speed_days?: number | null;
  five_x_recent_day_five_hour_peak_speed_days?: number | null;
  seven_day_five_hour_peak_speed_days?: number | null;
  five_x_seven_day_five_hour_peak_speed_days?: number | null;
  seven_day_peak_speed_days?: number | null;
  five_x_peak_speed_days?: number | null;
  active_seven_day_peak_speed_days?: number | null;
  active_five_x_peak_speed_days?: number | null;
  ten_x_speed_days?: number | null;
  health_status?: string;
  health_label?: string;
  health_tone?: "excellent" | "info" | "success" | "warning" | "danger" | "muted";
  health_reason?: string;
  auto_refill_required?: boolean;
  realtime_risk_ready?: boolean;
  pressure_stage?: string;
  pressure_stage_label?: string;
  pressure_tpm?: number;
  pressure_rpm?: number;
  latest_tpm?: number;
  latest_rpm?: number;
  traffic_site_id?: string;
  traffic_group_id?: number;
  traffic_metric_source?: string;
  realtime_tpm_burn_usd_per_hour?: number;
  realtime_rpm_burn_usd_per_hour?: number;
  realtime_burn_source?: string;
  sample_count?: number;
  concurrency_sample_count?: number;
  actual_runway_hours?: number | null;
  dynamic_runway_hours?: number | null;
  runway_source?: string;
  forecast_status?: string;
  forecast_fallback_reason?: string | null;
  forecast_model?: string;
  forecast_version?: string;
  forecast_readiness?: string;
  forecast_as_of?: string;
  forecast_horizon_hours?: number;
  forecast_p50_runway_hours?: number | null;
  forecast_p90_runway_hours?: number | null;
  forecast_actual_runway_capped?: boolean;
  forecast_dynamic_runway_capped?: boolean;
  forecast_nowcast_applied?: boolean;
  forecast_current_hour_observed_usd?: number | null;
  forecast_current_hour_model_remaining_usd?: number | null;
  forecast_current_hour_realtime_remaining_usd?: number | null;
  forecast_current_hour_selected_remaining_usd?: number | null;
  target_runway_hours?: number;
  actual_target_hours?: number;
  estimated_concurrency?: number;
  concurrency_coverage?: number | null;
  concurrency_target_coverage?: number;
  replenishment_required?: boolean;
  recommended_refill_accounts?: number;
  recommended_refill_options?: Record<string, {
    account_type?: string;
    quota_refill_accounts?: number;
    concurrency_refill_accounts?: number;
    recommended_refill_accounts?: number;
  }>;
  quota_refill_accounts?: number;
  concurrency_refill_accounts?: number;
  inventory_risk?: boolean;
  used_5h_percent?: number;
  available_5h_percent?: number;
  active_available_5h_percent?: number;
  actual_available_5h_percent?: number;
  active_actual_available_5h_percent?: number;
  used_7d_percent?: number;
  available_7d_percent?: number;
  actual_available_7d_percent?: number;
  active_used_5h_percent?: number;
  active_dynamic_used_5h_percent?: number;
  active_used_7d_percent?: number;
  total_accounts?: number;
  active_available_accounts?: number;
  reserve_available_accounts?: number;
  calculated_at?: string;
};

type RemoteAccount = {
  id: number;
  name?: string;
  platform?: string;
  type?: string;
  credentials?: Record<string, unknown>;
  credentials_status?: Record<string, unknown>;
  extra?: Record<string, unknown>;
  email?: string;
  account_type?: string;
  plan_type?: string;
  privacy_mode?: string;
  notes?: string | null;
  concurrency?: number;
  current_concurrency?: number;
  load_factor?: number;
  priority?: number;
  rate_multiplier?: number;
  status?: string;
  error_message?: string;
  codex_5h_used_percent?: unknown;
  codex_7d_used_percent?: unknown;
  codex_5h_reset_after_seconds?: unknown;
  codex_7d_reset_after_seconds?: unknown;
  codex_5h_reset_at?: unknown;
  codex_7d_reset_at?: unknown;
  codex_usage_updated_at?: unknown;
  codex_usage_synced_at?: unknown;
  codex_5h_request_count?: unknown;
  codex_7d_request_count?: unknown;
  codex_5h_token_count?: unknown;
  codex_7d_token_count?: unknown;
  codex_5h_actual_cost?: unknown;
  codex_7d_actual_cost?: unknown;
  codex_5h_total_cost?: unknown;
  codex_7d_total_cost?: unknown;
  codex_total_request_count?: unknown;
  codex_total_token_count?: unknown;
  codex_total_actual_cost?: unknown;
  codex_total_cost?: unknown;
  codex_primary_used_percent?: unknown;
  codex_secondary_used_percent?: unknown;
  codex_remote_test_status?: string;
  codex_remote_tested_at?: string;
  codex_remote_test_error?: string | null;
  codex_remote_test_response_preview?: string | null;
  codex_remote_test_model?: string | null;
  codex_remote_test_latency_ms?: number | null;
  last_used_at?: string;
  created_at?: string;
  updated_at?: string;
  expires_at?: string | number | null;
  credential_expires_at?: string | number | null;
  subscription_expires_at?: string | number | null;
  auto_pause_on_expired?: boolean;
  schedulable?: boolean;
  rate_limited_at?: string | null;
  rate_limit_reset_at?: string | null;
  temp_unschedulable_until?: string | null;
  temp_unschedulable_reason?: string | null;
  group_ids?: number[];
  groups?: Array<{ id: number; name: string }>;
};

type SitesResponse = {
  items: Site[];
  total: number;
};

type StatusPreferences = {
  pinned_site_id?: string | null;
  pinned_group_id?: number | null;
  updated_at?: string | null;
  updated_by_name?: string | null;
};

type GroupsResponse = {
  items: Group[];
  total: number;
  cache_meta?: CacheMeta;
};

type AccountsResponse = {
  items: RemoteAccount[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
  cache_meta?: CacheMeta;
  capacity_summary?: CapacitySummary;
};

type RefreshResponse = {
  ok?: boolean;
  status?: string;
  groups?: number;
  accounts?: number;
  auto_removed_abnormal_accounts?: number;
  auto_remove_abnormal_failed?: number;
  finished_at?: string;
  message?: string;
};

type RemoteAccountTestResponse = {
  verification?: {
    success?: boolean | null;
    response_preview?: string;
    error?: string;
    model?: string;
    latency_ms?: number | null;
  };
};

type InlineFeedback = {
  message: string;
  isError?: boolean;
};

type ConfirmState = {
  confirmText?: string;
  details?: Array<[string, string | number | null | undefined]>;
  message?: string;
  onConfirm: () => void;
  title: string;
  tone?: "default" | "danger";
};

const DEFAULT_ACCOUNT_PAGE_SIZE = 50;

type CacheMeta = {
  status?: string;
  last_refreshed_at?: string;
  requested_at?: string;
  groups?: number;
  accounts?: number;
};

type ApiPoolPageCache = {
  cacheVersion: string;
  sites: Site[];
  selectedSiteId: string;
  groups: Group[];
  selectedGroupId: number | null;
  accounts: RemoteAccount[];
  accountsTotal: number;
  accountsDataKey: string;
  accountPage: number;
  accountPageSize: number;
  statusFilter: string;
  lastLoadedAt: string | null;
  cachedAt: number;
  accountPages: Record<string, CachedAccounts>;
};

type CachedAccounts = {
  items: RemoteAccount[];
  total: number;
  capacitySummary?: CapacitySummary;
  lastLoadedAt: string | null;
  cachedAt: number;
};

let apiPoolPageCache: ApiPoolPageCache | null = null;

export function ApiPoolStatusPage({ token, showToast }: Props) {
  const initialCache = getApiPoolPageCache();
  const [sites, setSites] = useState<Site[]>(() => initialCache?.sites || []);
  const [selectedSiteId, setSelectedSiteId] = useState(() => initialCache?.selectedSiteId || "");
  const [groups, setGroups] = useState<Group[]>(() => initialCache?.groups || []);
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(() => initialCache?.selectedGroupId ?? null);
  const [accounts, setAccounts] = useState<RemoteAccount[]>(() => initialCache?.accounts || []);
  const [accountsTotal, setAccountsTotal] = useState(() => initialCache?.accountsTotal || 0);
  const [accountsDataKey, setAccountsDataKey] = useState(() => initialCache?.accountsDataKey || "");
  const [accountPage, setAccountPage] = useState(() => initialCache?.accountPage || 1);
  const [accountPageSize, setAccountPageSize] = useState(() => initialCache?.accountPageSize || DEFAULT_ACCOUNT_PAGE_SIZE);
  const [statusFilter, setStatusFilter] = useState(() => initialCache?.statusFilter || "");
  const [selectedRemoteIds, setSelectedRemoteIds] = useState<Set<number>>(() => new Set());
  const [remoteActionBusyId, setRemoteActionBusyId] = useState<number | null>(null);
  const [loadingGroups, setLoadingGroups] = useState(false);
  const [loadingAccountsKey, setLoadingAccountsKey] = useState<string | null>(null);
  const [lastLoadedAt, setLastLoadedAt] = useState<string | null>(() => initialCache?.lastLoadedAt || null);
  const [refreshingRemote, setRefreshingRemote] = useState(false);
  const [refreshingFrontend, setRefreshingFrontend] = useState(false);
  const [remoteRefreshFeedback, setRemoteRefreshFeedback] = useState<InlineFeedback | null>(null);
  const [connectionResult, setConnectionResult] = useState<unknown>(null);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [statusPreferences, setStatusPreferences] = useState<StatusPreferences>({});
  const [savingPreference, setSavingPreference] = useState<"site" | "group" | null>(null);
  const [autoRefreshRevision, setAutoRefreshRevision] = useState(0);

  const selectedSite = sites.find((site) => site.id === selectedSiteId) || null;
  const selectedGroup = groups.find((group) => group.id === selectedGroupId) || null;
  const sortedSites = useMemo(() => sortSitesByPinned(sites, statusPreferences.pinned_site_id), [sites, statusPreferences.pinned_site_id]);
  const sortedGroups = useMemo(() => sortGroupsByPinned(groups, statusPreferences.pinned_group_id), [groups, statusPreferences.pinned_group_id]);
  const selectedSitePinned = Boolean(selectedSiteId && statusPreferences.pinned_site_id === selectedSiteId);
  const selectedGroupPinned = Boolean(selectedGroupId !== null && statusPreferences.pinned_group_id === selectedGroupId);
  const currentAccountKey = useMemo(
    () => (selectedSiteId && selectedGroupId !== null ? accountCacheKey(selectedSiteId, selectedGroupId, accountPage, accountPageSize, statusFilter) : ""),
    [selectedSiteId, selectedGroupId, accountPage, accountPageSize, statusFilter],
  );
  const currentAccountKeyRef = useRef(currentAccountKey);
  const selectedSiteIdRef = useRef(selectedSiteId);
  currentAccountKeyRef.current = currentAccountKey;
  selectedSiteIdRef.current = selectedSiteId;
  const accountsMatchCurrent = Boolean(currentAccountKey && accountsDataKey === currentAccountKey);
  const visibleAccounts = accountsMatchCurrent ? accounts : [];
  const visibleAccountsTotal = accountsMatchCurrent ? accountsTotal : 0;
  const loadingAccounts = loadingAccountsKey !== null;
  const loadingCurrentAccounts = Boolean(currentAccountKey && loadingAccountsKey === currentAccountKey);
  const accountViewLoading = Boolean(currentAccountKey && !accountsMatchCurrent && loadingCurrentAccounts);
  const summary = useMemo(() => summarizeGroups(groups), [groups]);
  const accountSummary = useMemo(() => summarizeRemoteAccounts(visibleAccounts), [visibleAccounts]);
  const capacitySummaryLoading = accountViewLoading || (selectedGroupId !== null && !selectedGroup?.capacity_summary);
  const selectedVisibleAccounts = useMemo(() => visibleAccounts.filter((account) => selectedRemoteIds.has(account.id)), [visibleAccounts, selectedRemoteIds]);
  const allPageSelected = visibleAccounts.length > 0 && selectedVisibleAccounts.length === visibleAccounts.length;
  const somePageSelected = selectedVisibleAccounts.length > 0 && !allPageSelected;
  const totalPages = Math.max(1, Math.ceil(visibleAccountsTotal / accountPageSize));

  useEffect(() => {
    const pageIds = new Set(visibleAccounts.map((account) => account.id));
    setSelectedRemoteIds((current) => new Set([...current].filter((id) => pageIds.has(id))));
  }, [visibleAccounts]);

  const loadSites = async () => {
    const data = await api<SitesResponse>("/sub2api-sites?site_type=sub2api", token);
    setSites(data.items);
    const nextSiteId = chooseSiteId(data.items, selectedSiteId, statusPreferences.pinned_site_id);
    if (nextSiteId && nextSiteId !== selectedSiteId) {
      setSelectedSiteId(nextSiteId);
    }
  };

  const loadStatusPreferences = async () => {
    const data = await api<StatusPreferences>("/api-pools/status-preferences", token);
    setStatusPreferences(data);
    return data;
  };

  const loadGroups = async (siteId = selectedSiteId) => {
    if (!siteId) return [];
    setLoadingGroups(true);
    try {
      const data = await api<GroupsResponse>(`/sub2api-sites/${siteId}/groups?page=1&page_size=100`, token);
      if (!isCurrentSiteRequest(siteId, selectedSiteIdRef.current)) return [];
      setGroups(data.items);
      setLastLoadedAt(data.cache_meta?.last_refreshed_at || null);
      const nextGroupId = chooseGroupId(data.items, selectedGroupId, statusPreferences.pinned_group_id);
      if (nextGroupId !== null && nextGroupId !== selectedGroupId) {
        setSelectedGroupId(nextGroupId);
      }
      if (selectedGroupId && !data.items.some((group) => group.id === selectedGroupId)) {
        setSelectedGroupId(nextGroupId);
        setAccountsDataKey("");
      }
      return data.items;
    } finally {
      if (isCurrentSiteRequest(siteId, selectedSiteIdRef.current)) {
        setLoadingGroups(false);
      }
    }
  };

  const fetchAccountPage = async (siteId: string, groupId: number, page: number) => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(accountPageSize),
    });
    if (statusFilter) params.set("status", statusFilter);
    return api<AccountsResponse>(`/sub2api-sites/${siteId}/groups/${groupId}/accounts?${params.toString()}`, token);
  };

  const loadAccounts = async (siteId = selectedSiteId, groupId = selectedGroupId, page = accountPage) => {
    if (!siteId || groupId === null) return;
    const requestKey = accountCacheKey(siteId, groupId, page, accountPageSize, statusFilter);
    setLoadingAccountsKey(requestKey);
    try {
      const data = await fetchAccountPage(siteId, groupId, page);
      cacheAccounts(siteId, groupId, page, accountPageSize, statusFilter, {
        items: data.items,
        total: data.total,
        capacitySummary: data.capacity_summary,
        lastLoadedAt: data.cache_meta?.last_refreshed_at || lastLoadedAt,
        cachedAt: Date.now(),
      });
      if (data.capacity_summary) {
        setGroups((current) =>
          mergeCapacitySummaryForRequest(
            current,
            requestKey,
            currentAccountKeyRef.current,
            groupId,
            data.capacity_summary as CapacitySummary,
          ),
        );
      }
      if (currentAccountKeyRef.current === requestKey) {
        setAccounts(data.items);
        setAccountsTotal(data.total);
        setAccountsDataKey(requestKey);
        setLastLoadedAt(data.cache_meta?.last_refreshed_at || lastLoadedAt);
      }
    } finally {
      setLoadingAccountsKey((current) => (current === requestKey ? null : current));
    }
  };

  const refreshStatusData = async (animateChanges = false) => {
    const refreshContextKey = currentAccountKeyRef.current;
    const [sitesData, preferences] = await Promise.all([
      api<SitesResponse>("/sub2api-sites?site_type=sub2api", token),
      api<StatusPreferences>("/api-pools/status-preferences", token),
    ]);
    const nextSiteId = chooseSiteId(sitesData.items, selectedSiteId, preferences.pinned_site_id);
    if (!nextSiteId) {
      if (currentAccountKeyRef.current !== refreshContextKey) return;
      setSites(sitesData.items);
      setStatusPreferences(preferences);
      setSelectedSiteId("");
      setGroups([]);
      setSelectedGroupId(null);
      setAccounts([]);
      setAccountsTotal(0);
      setAccountsDataKey("");
      if (animateChanges) setAutoRefreshRevision((current) => current + 1);
      return;
    }

    const groupsData = await api<GroupsResponse>(`/sub2api-sites/${nextSiteId}/groups?page=1&page_size=100`, token);
    const nextGroupId = chooseGroupId(groupsData.items, selectedGroupId, preferences.pinned_group_id);
    if (nextGroupId === null) {
      if (currentAccountKeyRef.current !== refreshContextKey) return;
      setSites(sitesData.items);
      setStatusPreferences(preferences);
      setSelectedSiteId(nextSiteId);
      setGroups(groupsData.items);
      setSelectedGroupId(null);
      setAccounts([]);
      setAccountsTotal(0);
      setAccountsDataKey("");
      setLastLoadedAt(groupsData.cache_meta?.last_refreshed_at || null);
      if (animateChanges) setAutoRefreshRevision((current) => current + 1);
      return;
    }

    const requestKey = accountCacheKey(nextSiteId, nextGroupId, accountPage, accountPageSize, statusFilter);
    const accountsData = await fetchAccountPage(nextSiteId, nextGroupId, accountPage);
    if (currentAccountKeyRef.current !== refreshContextKey) return;
    const snapshotLoadedAt = accountsData.cache_meta?.last_refreshed_at || groupsData.cache_meta?.last_refreshed_at || lastLoadedAt;
    const nextGroups = accountsData.capacity_summary
      ? groupsData.items.map((group) => (group.id === nextGroupId ? { ...group, capacity_summary: accountsData.capacity_summary } : group))
      : groupsData.items;
    cacheAccounts(nextSiteId, nextGroupId, accountPage, accountPageSize, statusFilter, {
      items: accountsData.items,
      total: accountsData.total,
      capacitySummary: accountsData.capacity_summary,
      lastLoadedAt: snapshotLoadedAt,
      cachedAt: Date.now(),
    });
    setSites(sitesData.items);
    setStatusPreferences(preferences);
    setSelectedSiteId(nextSiteId);
    setGroups(nextGroups);
    setSelectedGroupId(nextGroupId);
    setAccounts(accountsData.items);
    setAccountsTotal(accountsData.total);
    setAccountsDataKey(requestKey);
    setLastLoadedAt(snapshotLoadedAt);
    if (animateChanges) setAutoRefreshRevision((current) => current + 1);
  };

  const hydrateAccountsFromCache = (siteId: string, groupId: number, page: number, pageSize: number, filter: string) => {
    const nextAccountKey = accountCacheKey(siteId, groupId, page, pageSize, filter);
    const cached = getCachedAccounts(siteId, groupId, page, pageSize, filter);
    if (cached) {
      setAccounts(cached.items);
      setAccountsTotal(cached.total);
      setAccountsDataKey(nextAccountKey);
      if (cached.capacitySummary) {
        updateGroupCapacitySummary(groupId, cached.capacitySummary);
      }
      setLastLoadedAt(cached.lastLoadedAt || lastLoadedAt);
      setLoadingAccountsKey((current) => (current === nextAccountKey ? null : current));
      return;
    }
    setAccountsDataKey("");
    setLoadingAccountsKey(nextAccountKey);
  };

  const selectGroup = (groupId: number) => {
    setSelectedGroupId(groupId);
    setAccountPage(1);
    if (selectedSiteId) {
      hydrateAccountsFromCache(selectedSiteId, groupId, 1, accountPageSize, statusFilter);
    }
  };

  const selectStatusFilter = (filter: string) => {
    setStatusFilter(filter);
    setAccountPage(1);
    if (selectedSiteId && selectedGroupId !== null) {
      hydrateAccountsFromCache(selectedSiteId, selectedGroupId, 1, accountPageSize, filter);
    }
  };

  const selectAccountPage = (page: number) => {
    setAccountPage(page);
    if (selectedSiteId && selectedGroupId !== null) {
      hydrateAccountsFromCache(selectedSiteId, selectedGroupId, page, accountPageSize, statusFilter);
    }
  };

  const selectAccountPageSize = (pageSize: number) => {
    setAccountPageSize(pageSize);
    setAccountPage(1);
    if (selectedSiteId && selectedGroupId !== null) {
      hydrateAccountsFromCache(selectedSiteId, selectedGroupId, 1, pageSize, statusFilter);
    }
  };

  const saveStatusPreference = async (kind: "site" | "group", pinned: boolean) => {
    if (kind === "site" && !selectedSiteId) return;
    if (kind === "group" && selectedGroupId === null) return;
    setSavingPreference(kind);
    try {
      const payload =
        kind === "site"
          ? { pinned_site_id: pinned ? selectedSiteId : null }
          : { pinned_group_id: pinned ? selectedGroupId : null };
      const data = await api<StatusPreferences>("/api-pools/status-preferences", token, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      setStatusPreferences(data);
      showToast(pinned ? "置顶已保存" : "置顶已取消");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSavingPreference(null);
    }
  };

  const togglePageSelection = (checked: boolean) => {
    setSelectedRemoteIds(checked ? new Set(visibleAccounts.map((account) => account.id)) : new Set());
  };

  const toggleRemoteSelection = (accountId: number, checked: boolean) => {
    setSelectedRemoteIds((current) => {
      const next = new Set(current);
      if (checked) next.add(accountId);
      else next.delete(accountId);
      return next;
    });
  };

  const removeRemoteAccountFromCurrentPage = (accountId: number) => {
    setAccounts((current) => current.filter((item) => item.id !== accountId));
    setAccountsTotal((current) => Math.max(0, current - 1));
  };

  const updateRemoteAccountTestState = (accountId: number, verification?: RemoteAccountTestResponse["verification"]) => {
    const now = new Date().toISOString();
    setAccounts((current) =>
      current.map((item) =>
        item.id === accountId
          ? {
              ...item,
              codex_remote_test_status: verification?.success === true ? "passed" : "failed",
              codex_remote_tested_at: now,
              codex_remote_test_error: verification?.success === true ? null : verification?.error || "测试失败",
              codex_remote_test_response_preview: verification?.response_preview || null,
              codex_remote_test_model: verification?.model || "gpt-5.4-mini",
              codex_remote_test_latency_ms: verification?.latency_ms ?? null,
            }
          : item,
      ),
    );
  };

  const performManualDeleteRemoteAccount = async (account: RemoteAccount, targetStatus: "available" | "library" | "problem", label: string) => {
    if (!selectedSiteId) return;
    setRemoteActionBusyId(account.id);
    try {
      await api(`/sub2api-sites/${selectedSiteId}/accounts/${account.id}/manual-delete`, token, {
        method: "POST",
        body: JSON.stringify({
          target_status: targetStatus,
          reason: label,
        }),
      });
      showToast(`${label}完成`);
      setSelectedRemoteIds((current) => {
        const next = new Set(current);
        next.delete(account.id);
        return next;
      });
      removeRemoteAccountFromCurrentPage(account.id);
      clearAccountCacheForSite(selectedSiteId);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setRemoteActionBusyId(null);
    }
  };

  const manualDeleteRemoteAccount = (account: RemoteAccount, targetStatus: "available" | "library" | "problem") => {
    const label =
      targetStatus === "available"
        ? "手动删除并退回可用池"
        : targetStatus === "problem"
          ? "标记错误并退回问题池"
          : "手动删除并退回总库";
    setConfirmState({
      title: "确认删除远端账号",
      message: "删除前会先把账号快照写入本地库，然后从 sub2api 删除远端账号。",
      details: [
        ["远端账号", text(account.name) || `#${account.id}`],
        ["远端 ID", account.id],
        ["处理方式", label],
      ],
      confirmText: targetStatus === "problem" ? "标记错误并删除远端" : "删除远端账号",
      tone: "danger",
      onConfirm: () => performManualDeleteRemoteAccount(account, targetStatus, label),
    });
  };

  const testRemoteAccount = async (account: RemoteAccount) => {
    if (!selectedSiteId) return;
    setRemoteActionBusyId(account.id);
    try {
      const result = await api<RemoteAccountTestResponse>(`/sub2api-sites/${selectedSiteId}/accounts/${account.id}/test`, token, {
        method: "POST",
        body: JSON.stringify({
          model_id: "gpt-5.4-mini",
          prompt: "",
          reason: "manual remote account test",
        }),
      });
      const verification = result.verification;
      if (verification?.success === true) {
        showToast(`测试通过：${verification.response_preview || "success"}`);
      } else {
        showToast(`测试失败：${verification?.error || "请查看远端状态"}`, true);
      }
      updateRemoteAccountTestState(account.id, verification);
      clearAccountCacheForSite(selectedSiteId);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setRemoteActionBusyId(null);
    }
  };

  const updateGroupCapacitySummary = (groupId: number, capacitySummary: CapacitySummary) => {
    setGroups((current) =>
      current.map((group) => (group.id === groupId ? { ...group, capacity_summary: capacitySummary } : group)),
    );
  };

  usePageAutoRefresh(() => refreshStatusData(true), {
    enabled: Boolean(selectedSiteId && selectedGroupId !== null),
    paused: Boolean(refreshingRemote || refreshingFrontend || remoteActionBusyId !== null || confirmState || savingPreference),
  });

  const testConnection = async () => {
    if (!selectedSiteId) return;
    try {
      const data = await api<unknown>(`/sub2api-sites/${selectedSiteId}/test`, token, { method: "POST" });
      setConnectionResult(data);
      showToast("sub2api 连接测试完成");
    } catch (error) {
      showToast(errorMessage(error), true);
    }
  };

  useEffect(() => {
    if (getApiPoolPageCache()) {
      loadStatusPreferences()
        .then((preferences) => {
          const nextSiteId = chooseSiteId(sites, selectedSiteId, preferences.pinned_site_id);
          if (nextSiteId && nextSiteId !== selectedSiteId) setSelectedSiteId(nextSiteId);
        })
        .catch((error) => showToast(errorMessage(error), true));
      return;
    }
    loadStatusPreferences()
      .then((preferences) =>
        api<SitesResponse>("/sub2api-sites?site_type=sub2api", token).then((data) => {
          setSites(data.items);
          const nextSiteId = chooseSiteId(data.items, selectedSiteId, preferences.pinned_site_id);
          if (nextSiteId) setSelectedSiteId(nextSiteId);
        }),
      )
      .catch((error) => showToast(errorMessage(error), true));
  }, []);

  useEffect(() => {
    if (!selectedSiteId) return;
    const site = sites.find((item) => item.id === selectedSiteId);
    if (!site) return;
    if (getApiPoolPageCache()?.selectedSiteId === selectedSiteId && groups.length) return;
    setGroups([]);
    setAccounts([]);
    setAccountsDataKey("");
    setSelectedGroupId(null);
    setAccountPage(1);
    loadGroups(selectedSiteId).catch((error) => showToast(errorMessage(error), true));
  }, [selectedSiteId, sites]);

  useEffect(() => {
    if (!selectedSiteId || selectedGroupId === null) return;
    const nextAccountKey = accountCacheKey(selectedSiteId, selectedGroupId, accountPage, accountPageSize, statusFilter);
    const cached = getCachedAccounts(selectedSiteId, selectedGroupId, accountPage, accountPageSize, statusFilter);
    if (cached) {
      setAccounts(cached.items);
      setAccountsTotal(cached.total);
      setAccountsDataKey(nextAccountKey);
      setLastLoadedAt(cached.lastLoadedAt || lastLoadedAt);
      return;
    }
    setAccountsDataKey("");
    loadAccounts(selectedSiteId, selectedGroupId, accountPage).catch((error) => showToast(errorMessage(error), true));
  }, [selectedSiteId, selectedGroupId, accountPage, accountPageSize, statusFilter, statusPreferences.pinned_group_id]);

  useEffect(() => {
    apiPoolPageCache = {
      cacheVersion: getSub2apiCacheVersion(),
      sites,
      selectedSiteId,
      groups,
      selectedGroupId,
      accounts,
      accountsTotal,
      accountsDataKey,
      accountPage,
      accountPageSize,
      statusFilter,
      lastLoadedAt,
      cachedAt: Date.now(),
      accountPages: apiPoolPageCache?.accountPages || {},
    };
  }, [sites, selectedSiteId, groups, selectedGroupId, accounts, accountsTotal, accountsDataKey, accountPage, accountPageSize, statusFilter, lastLoadedAt]);

  useEffect(() => {
    const handleCacheUpdated = () => {
      apiPoolPageCache = null;
      clearAccountCacheForSite(selectedSiteId);
      setAccounts([]);
      setAccountsTotal(0);
      setAccountsDataKey("");
      if (!selectedSiteId) {
        loadSites().catch((error) => showToast(errorMessage(error), true));
        return;
      }
      loadGroups(selectedSiteId)
        .then((nextGroups) => {
          const nextGroupId = chooseGroupId(nextGroups, selectedGroupId, statusPreferences.pinned_group_id);
          if (nextGroupId !== null) {
            setSelectedGroupId(nextGroupId);
            return loadAccounts(selectedSiteId, nextGroupId, accountPage);
          }
          return undefined;
        })
        .catch((error) => showToast(errorMessage(error), true));
    };
    window.addEventListener("sub2api-cache-updated", handleCacheUpdated);
    return () => window.removeEventListener("sub2api-cache-updated", handleCacheUpdated);
  }, [selectedSiteId, selectedGroupId, accountPage, accountPageSize, statusFilter]);

  const refreshAll = async () => {
    if (!selectedSiteId) return;
    setRefreshingRemote(true);
    setRemoteRefreshFeedback({ message: "正在同步 sub2api 账号池数据..." });
    try {
      const result = await api<RefreshResponse>(`/sub2api-sites/${selectedSiteId}/refresh`, token, { method: "POST" });
      clearAccountCacheForSite(selectedSiteId);
      const nextGroups = await loadGroups();
      const nextGroupId = chooseGroupId(nextGroups, selectedGroupId, statusPreferences.pinned_group_id);
      if (nextGroupId !== null) {
        const nextAccountKey = accountCacheKey(selectedSiteId, nextGroupId, accountPage, accountPageSize, statusFilter);
        setSelectedGroupId(nextGroupId);
        setAccountsDataKey("");
        setLoadingAccountsKey(nextAccountKey);
        await loadAccounts(selectedSiteId, nextGroupId, accountPage);
      }
      const message = refreshResultMessage(result);
      setRemoteRefreshFeedback({ message });
      showToast(message);
    } catch (error) {
      const message = `账号池数据同步失败：${errorMessage(error)}`;
      setRemoteRefreshFeedback({ message, isError: true });
      showToast(message, true);
    } finally {
      setRefreshingRemote(false);
    }
  };

  const refreshFrontendData = async () => {
    if (!selectedSiteId) return;
    setRefreshingFrontend(true);
    try {
      await refreshStatusData();
      showToast("前端数据已刷新");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setRefreshingFrontend(false);
    }
  };

  return (
    <AutoRefreshAnimationContext.Provider value={autoRefreshRevision}>
    <section className="view pool-status-page">
      <section className="panel pool-compact-toolbar">
        <div className="pool-title">
          <h2>API 账号池状态</h2>
          <p>远程 sub2api groups 和账号调度状态</p>
        </div>
        <label className="pool-site-select">
          <span className="field-label">
            <strong>API 站点</strong>
          </span>
          <select value={selectedSiteId} onChange={(event) => setSelectedSiteId(event.target.value)}>
            {sortedSites.map((site) => (
              <option key={site.id} value={site.id}>
                {site.name}{statusPreferences.pinned_site_id === site.id ? "（置顶）" : ""}
              </option>
            ))}
          </select>
          <button
            className={`pin-button ${selectedSitePinned ? "active" : ""}`}
            type="button"
            onClick={() => saveStatusPreference("site", !selectedSitePinned)}
            disabled={!selectedSiteId || savingPreference === "site"}
          >
            {selectedSitePinned ? "取消置顶站点" : "置顶当前站点"}
          </button>
        </label>
        <div className="site-meta">
          <strong>{selectedSite?.base_url || "未配置"}</strong>
          <span>{selectedSite?.token_configured ? "密钥已配置" : "密钥未配置"}</span>
          <span>最后刷新：{lastLoadedAt ? formatDateTime(lastLoadedAt) : "-"}</span>
        </div>
        <div className="pool-toolbar-actions">
          <button className="ghost compact-button" type="button" onClick={testConnection} disabled={!selectedSiteId}>
            测试连接
          </button>
          <button className="compact-button" type="button" onClick={refreshAll} disabled={!selectedSiteId || refreshingRemote}>
            {refreshingRemote ? "同步中..." : "同步账号池数据"}
          </button>
          <button
            className="ghost compact-button frontend-refresh-button"
            type="button"
            onClick={refreshFrontendData}
            disabled={!selectedSiteId || refreshingFrontend}
          >
            {refreshingFrontend ? "前端刷新中..." : "前端数据刷新"}
          </button>
          {remoteRefreshFeedback && (
            <span className={`refresh-feedback ${remoteRefreshFeedback.isError ? "danger" : ""}`}>
              {remoteRefreshFeedback.message}
            </span>
          )}
        </div>
      </section>

      <section className="panel group-strip-panel">
        <div className="group-strip-left">
          <div>
            <h3>Groups</h3>
            <p>{loadingGroups ? "加载中..." : `共 ${groups.length} 个分组`}</p>
          </div>
          <section className="pool-mini-metrics">
            <MiniMetric label="分组" value={groups.length} />
            <MiniMetric label="总账号" value={summary.totalAccounts} />
            <MiniMetric label="活跃" value={summary.activeAccounts} />
            <MiniMetric label="限流" value={summary.rateLimitedAccounts} />
          </section>
        </div>
        <label className="group-picker group-picker-wide">
          <span className="field-label">
            <strong>账号池</strong>
          </span>
          <select
            value={selectedGroupId ?? ""}
            onChange={(event) => {
              selectGroup(Number(event.target.value));
            }}
          >
            {sortedGroups.map((group) => (
              <option key={group.id} value={group.id}>
                {group.name} · active {numberValue(group.active_account_count)} / {numberValue(group.account_count)} · 限流 {numberValue(group.rate_limited_account_count)}
              </option>
            ))}
          </select>
          <button
            className={`pin-button ${selectedGroupPinned ? "active" : ""}`}
            type="button"
            onClick={() => saveStatusPreference("group", !selectedGroupPinned)}
            disabled={selectedGroupId === null || savingPreference === "group"}
          >
            {selectedGroupPinned ? "取消置顶分组" : "置顶当前分组"}
          </button>
        </label>
      </section>

      <section className="panel account-pool-panel">
          <div className="panel-header">
            <div>
              <div className="account-pool-title-row">
                <h3>{selectedGroup?.name || "账号"}</h3>
                {selectedGroup && <span className="account-pool-id-chip">ID = {selectedGroup.id}</span>}
              </div>
              {!selectedGroup && <p>请选择一个 group</p>}
            </div>
            <div className="button-row">
              <label className="inline-select">
                <span>每页</span>
                <select value={accountPageSize} onChange={(event) => selectAccountPageSize(Number(event.target.value))}>
                  <option value={50}>50</option>
                  <option value={200}>200</option>
                  <option value={500}>500</option>
                </select>
              </label>
              <select
                value={statusFilter}
                onChange={(event) => {
                  selectStatusFilter(event.target.value);
                }}
              >
                <option value="">全部状态</option>
                <option value="active">active</option>
                <option value="error">error</option>
                <option value="disabled">disabled</option>
                <option value="paused">paused</option>
              </select>
            </div>
          </div>

          <section className="pool-health-card">
            <div className="pool-health-main">
              <span><MetricHelp helpKey="账号池概览">账号池概览</MetricHelp></span>
              <strong><AnimatedValue value={numberValue(selectedGroup?.capacity_summary?.pool_active_normal_accounts)} /></strong>
              <em><MetricHelp helpKey="active / 正常">active / 正常 <AnimatedValue value={numberValue(selectedGroup?.capacity_summary?.pool_normal_accounts)} /></MetricHelp></em>
            </div>
            <div className="pool-health-grid">
              <MiniMetric label="5h 429" value={numberValue(selectedGroup?.capacity_summary?.pool_five_hour_rate_limited_accounts)} />
              <MiniMetric label="7d 429" value={numberValue(selectedGroup?.capacity_summary?.pool_seven_day_rate_limited_accounts)} />
              <MiniMetric label="异常数量" value={numberValue(selectedGroup?.capacity_summary?.pool_abnormal_accounts)} />
            </div>
            <p>已排除长期 7d 429 Bug Team <AnimatedValue value={numberValue(selectedGroup?.capacity_summary?.pool_excluded_bug_team_accounts)} /> 个（恢复超过 2 天或时间未知），不参与概览与容量计算。</p>
          </section>

          <ConcurrencyCapacitySummary summary={selectedGroup?.capacity_summary} loading={capacitySummaryLoading} />

          <CapacityRunwaySummary summary={selectedGroup?.capacity_summary} loading={capacitySummaryLoading} />

          <ForecastAccuracySummary
            accuracy={selectedGroup?.capacity_summary?.forecast_accuracy}
            loading={capacitySummaryLoading}
          />

          <div className="list-toolbar">
            <label className="checkbox-line">
              <input
                type="checkbox"
                checked={allPageSelected}
                ref={(input) => {
                  if (input) input.indeterminate = somePageSelected;
                }}
                onChange={(event) => togglePageSelection(event.target.checked)}
              />
              <span>本页全选</span>
            </label>
            <span className="muted">已选 {selectedVisibleAccounts.length}</span>
            <div className="button-row list-toolbar-actions">
              <button className="ghost compact-button" disabled title="远端缓存账号需要先写入本地库后再处理" type="button">
                移入可用池
              </button>
              <button className="ghost compact-button" disabled title="远端缓存账号需要先写入本地库后再处理" type="button">
                标记问题
              </button>
              <button className="ghost compact-button" disabled title="远端缓存账号需要先写入本地库后再处理" type="button">
                弃用
              </button>
              <span className="muted">远端账号需先同步到本地库</span>
            </div>
          </div>

          <div className="table-wrap pool-account-table-wrap">
            <table className="pool-account-table">
              <thead>
                <tr>
                  <th className="select-col">选择</th>
                  <th className="col-name">名称</th>
                  <th className="col-platform">平台/标签</th>
                  <th className="col-capacity">容量</th>
                  <th className="col-status">状态</th>
                  <th className="col-schedule">调度</th>
                  <th className="col-usage">用量/总计</th>
                  <th className="col-time">最近使用</th>
                  <th className="col-expire">过期时间</th>
                  <th className="col-action">操作</th>
                </tr>
              </thead>
              <tbody>
                {visibleAccounts.map((account) => (
                  <RemoteAccountRow
                    account={account}
                    busy={remoteActionBusyId === account.id}
                    isSelected={selectedRemoteIds.has(account.id)}
                    key={account.id}
                    onManualDelete={(targetStatus) => manualDeleteRemoteAccount(account, targetStatus)}
                    onSelect={(checked) => toggleRemoteSelection(account.id, checked)}
                    onTest={() => testRemoteAccount(account)}
                  />
                ))}
                {!visibleAccounts.length && (
                  <tr>
                    <td colSpan={10} className="muted">
                      {accountViewLoading ? "加载中..." : "暂无账号"}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="pagination">
            <label className="inline-select">
              <span>每页</span>
              <select value={accountPageSize} onChange={(event) => selectAccountPageSize(Number(event.target.value))}>
                <option value={50}>50</option>
                <option value={200}>200</option>
                <option value={500}>500</option>
              </select>
            </label>
            <button className="ghost" type="button" disabled={accountPage <= 1} onClick={() => selectAccountPage(Math.max(1, accountPage - 1))}>
              上一页
            </button>
            <span className="muted">
              {accountPage} / {totalPages} · 每页 {accountPageSize}
            </span>
            <button
              className="ghost"
              type="button"
              disabled={accountPage >= totalPages}
              onClick={() => selectAccountPage(accountPage + 1)}
            >
              下一页
            </button>
          </div>
      </section>

      {connectionResult !== null && <pre className="output compact">{JSON.stringify(connectionResult, null, 2)}</pre>}
      <ConfirmDialog
        confirmText={confirmState?.confirmText}
        details={confirmState?.details}
        message={confirmState?.message}
        onCancel={() => setConfirmState(null)}
        onConfirm={() => {
          const action = confirmState?.onConfirm;
          setConfirmState(null);
          action?.();
        }}
        open={confirmState !== null}
        title={confirmState?.title || ""}
        tone={confirmState?.tone}
      />
    </section>
    </AutoRefreshAnimationContext.Provider>
  );
}

function RemoteAccountRow({
  account,
  busy,
  isSelected,
  onManualDelete,
  onSelect,
  onTest,
}: {
  account: RemoteAccount;
  busy: boolean;
  isSelected: boolean;
  onManualDelete: (targetStatus: "available" | "library" | "problem") => void;
  onSelect: (checked: boolean) => void;
  onTest: () => void;
}) {
  const credentials = account.credentials || {};
  const extra = account.extra || {};
  const email = text(account.email) || text(credentials.email) || text(account.extra?.email) || text(account.name);
  const accountName = text(account.name) || email || `#${account.id}`;
  const planType = text(account.account_type) || text(account.plan_type) || text(credentials.plan_type);
  const privacyMode = text(account.privacy_mode) || text(extra.privacy_mode);
  const credentialExpiresAt = account.credential_expires_at ?? account.expires_at ?? credentials.expires_at;
  const subscriptionExpiresAt = account.subscription_expires_at ?? credentials.subscription_expires_at;
  const statusView = accountStatusView(account);
  const schedulableView = accountSchedulableView(account);

  return (
    <tr>
      <td className="select-col">
        <input
          aria-label={`选择 ${accountName}`}
          checked={isSelected}
          onChange={(event) => onSelect(event.target.checked)}
          type="checkbox"
        />
      </td>
      <td className="account-name-cell">
        <div className="cell-main truncate" title={accountName}>{accountName}</div>
        <div className="cell-sub truncate" title={email}>{email || "未识别邮箱"}</div>
        <div className="cell-sub">#{account.id}</div>
      </td>
      <td>
        <div>{displayPlatform(account.platform)}</div>
        <div className="account-tags">
          {planType && <span className={`account-tag ${planTagTone(planType)}`}>{displayPlan(planType)}</span>}
          {privacyMode && <span className={`account-tag ${privacyTagTone(privacyMode)}`}>{displayPrivacy(privacyMode)}</span>}
        </div>
      </td>
      <td className="capacity-cell">
        <span><AnimatedValue value={numberValue(account.current_concurrency)} /></span>
        <span className="capacity-separator">/</span>
        <strong><AnimatedValue value={numberValue(account.concurrency)} /></strong>
        <div className="cell-sub">load <AnimatedValue value={numberValue(account.load_factor)} /></div>
      </td>
      <td>
        <StatusPill value={statusView.label} tone={statusView.tone} />
        {account.error_message && (
          <div className={`cell-sub truncate ${isTemporaryRateLimit(account) ? "warning-text" : "danger"}`} title={account.error_message}>
            {account.error_message}
          </div>
        )}
        {statusView.detail && <div className="cell-sub warning-text">{statusView.detail}</div>}
        {account.codex_remote_test_status && (
          <div
            className={`cell-sub ${account.codex_remote_test_status === "failed" ? "danger" : ""}`}
            title={text(account.codex_remote_test_error) || text(account.codex_remote_test_response_preview)}
          >
            测试 {remoteTestLabel(account.codex_remote_test_status)}
            {account.codex_remote_tested_at ? ` · ${formatOptionalDate(account.codex_remote_tested_at)}` : ""}
          </div>
        )}
      </td>
      <td>
        <StatusPill value={schedulableView.label} tone={schedulableView.tone} />
        <div className="cell-sub">priority {numberValue(account.priority)}</div>
      </td>
      <td>
        <AccountSevenDayUsage account={account} />
        <UsageWindow account={account} label="5h" windowKey="5h" />
        <UsageWindow account={account} label="7d" windowKey="7d" />
      </td>
      <td>
        <div>{formatOptionalDate(account.last_used_at)}</div>
        <div className="cell-sub">添加 {formatOptionalDate(account.created_at)}</div>
        {isTemporaryRateLimit(account) && (
          <div className="cell-sub danger">
            {account.rate_limit_reset_at ? `限流到 ${formatOptionalDate(account.rate_limit_reset_at)}` : `限流记录 ${formatOptionalDate(account.rate_limited_at)}`}
          </div>
        )}
      </td>
      <td>
        <div>订阅 {formatOptionalDate(subscriptionExpiresAt)}</div>
        <div className="cell-sub">凭证 {formatOptionalDate(credentialExpiresAt)}</div>
      </td>
      <td>
        <div className="button-row action-wrap">
          <button className="ghost compact-button" disabled={busy} onClick={onTest} type="button">
            测试
          </button>
          <button className="ghost compact-button" disabled={busy} onClick={() => onManualDelete("available")} type="button">
            手动删除
          </button>
          <button className="ghost compact-button" disabled={busy} onClick={() => onManualDelete("library")} type="button">
            退回总库
          </button>
          <button className="ghost compact-button danger-button" disabled={busy} onClick={() => onManualDelete("problem")} type="button">
            标记错误
          </button>
        </div>
      </td>
    </tr>
  );
}

function UsageWindow({ account, label, windowKey }: { account: RemoteAccount; label: string; windowKey: "5h" | "7d" }) {
  const percent = usageValue(account, `codex_${windowKey}_used_percent`);
  const resetAfter = usageValue(account, `codex_${windowKey}_reset_after_seconds`);
  const usedPercent = clampPercent(numberValue(percent));
  return (
    <div className="usage-window">
      <div className="usage-window-top">
        <span>{label}</span>
        <strong><AnimatedValue value={`${usedPercent}%`} /></strong>
        <em>{formatDuration(resetAfter)}</em>
      </div>
      <div className="usage-track" aria-label={`${label} used ${usedPercent}%`}>
        <div className={`usage-fill ${usageToneClass(usedPercent)}`} style={{ width: `${usedPercent}%` }} />
      </div>
    </div>
  );
}

function AccountSevenDayUsage({ account }: { account: RemoteAccount }) {
  const requestCount = usageValue(account, "codex_7d_request_count");
  const totalCost = usageValue(account, "codex_7d_total_cost");
  const requestLabel = requestCount === null ? "-" : `${numberValue(requestCount)} 次`;
  const costLabel = totalCost === null ? "$-" : `$${numberValue(totalCost).toFixed(4)}`;
  return <div className="cell-sub total-usage-line">7天 {requestLabel} / {costLabel}</div>;
}

function ConcurrencyCapacitySummary({ summary, loading }: { summary?: CapacitySummary; loading: boolean }) {
  const actual = numberValue(summary?.concurrency_actual_in_use);
  const safeAvailable = numberValue(summary?.concurrency_safe_available);
  const immediateAvailable = numberValue(summary?.concurrency_actual_available);
  const nearLimitAvailable = numberValue(summary?.concurrency_near_limit_available);
  const total = numberValue(summary?.concurrency_total_capacity);
  const unavailable = numberValue(summary?.concurrency_temporarily_unavailable);
  const unavailableAccounts = numberValue(summary?.concurrency_temporarily_unavailable_accounts);
  const actualPercent = total > 0 ? Math.max(0, Math.min(100, (actual / total) * 100)) : 0;
  const safePercent = total > 0 ? Math.max(0, Math.min(100 - actualPercent, (safeAvailable / total) * 100)) : 0;
  const nearLimitPercent = total > 0 ? Math.max(0, Math.min(100 - actualPercent - safePercent, (nearLimitAvailable / total) * 100)) : 0;
  const unavailablePercent = Math.max(0, 100 - actualPercent - safePercent - nearLimitPercent);
  return (
    <section className="concurrency-capacity-band">
      <div className="concurrency-capacity-head">
        <div>
          <span><MetricHelp helpKey="并发容量">并发容量</MetricHelp></span>
          <em>当前使用组</em>
        </div>
        <small>
          安全账号 <AnimatedValue value={numberValue(summary?.concurrency_safe_accounts)} /> 个 · 临界账号 <AnimatedValue value={numberValue(summary?.concurrency_near_limit_accounts)} /> 个 · 5h限流 <AnimatedValue value={numberValue(summary?.concurrency_five_hour_limited_accounts)} /> 个 · 短期7d <AnimatedValue value={numberValue(summary?.concurrency_short_seven_day_limited_accounts)} /> 个 · 长期7d排除 <AnimatedValue value={numberValue(summary?.concurrency_long_seven_day_limited_accounts)} /> 个
        </small>
      </div>
      <div className="concurrency-capacity-values">
        <div>
          <span><MetricHelp helpKey="当前并发">当前并发</MetricHelp></span>
          <strong><AnimatedValue value={loading ? "-" : actual} /></strong>
        </div>
        <div>
          <span><MetricHelp helpKey="安全可用并发">安全可用并发</MetricHelp></span>
          <strong><AnimatedValue value={loading ? "-" : safeAvailable} /></strong>
        </div>
        <div>
          <span><MetricHelp helpKey="即时可用并发">即时可用并发</MetricHelp></span>
          <strong><AnimatedValue value={loading ? "-" : immediateAvailable} /></strong>
        </div>
        <div>
          <span><MetricHelp helpKey="可恢复总并发容量">可恢复总并发容量</MetricHelp></span>
          <strong><AnimatedValue value={loading ? "-" : total} /></strong>
        </div>
      </div>
      <div className="concurrency-capacity-meter" aria-label={`当前并发 ${actual}，安全可用并发 ${safeAvailable}，即时可用并发 ${immediateAvailable}，可恢复总并发容量 ${total}`}>
        <span className="used" style={{ width: `${actualPercent}%` }} />
        <span className="safe" style={{ width: `${safePercent}%` }} />
        <span className="near-limit" style={{ width: `${nearLimitPercent}%` }} />
        <span className="unavailable" style={{ width: `${unavailablePercent}%` }} />
      </div>
      <div className="concurrency-capacity-legend">
        <span><i className="used" /><MetricHelp helpKey="当前并发">使用中 <AnimatedValue value={actual} /> 并发</MetricHelp></span>
        <span><i className="safe" /><MetricHelp helpKey="安全可用并发">安全可用 <AnimatedValue value={safeAvailable} /> 并发</MetricHelp></span>
        <span><i className="near-limit" /><MetricHelp helpKey="临界可用并发">临界可用 <AnimatedValue value={nearLimitAvailable} /> 并发</MetricHelp></span>
        <span><i className="unavailable" /><MetricHelp helpKey="暂时不可用并发">暂时不可用 <AnimatedValue value={unavailable} /> 并发 · <AnimatedValue value={unavailableAccounts} /> 个账号</MetricHelp></span>
      </div>
    </section>
  );
}

function CapacityRunwaySummary({ summary, loading }: { summary?: CapacitySummary; loading: boolean }) {
  const tone = summary?.health_tone || "muted";
  const accountType = summary?.account_type ? displayPlan(summary.account_type) : "未知";
  const sevenDayFiveHourPeak = summary?.seven_day_five_hour_peak_cost ?? summary?.five_hour_peak_cost;
  const sevenDayFiveHourPeakMultiple = summary?.five_hour_peak_multiple;
  const recentDayFiveHourPeak = summary?.recent_day_five_hour_peak_cost;
  const recentDayFiveHourPeakMultiple = summary?.recent_day_five_hour_peak_multiple;
  const burstOneHourMultiple = summary?.burst_1h_five_hour_multiple;
  const burstTrendTone = burstTrendMetricTone(summary?.burst_1h_trend, summary?.burst_1h_trend_strength);
  const recent24hSpeedDays = summary?.current_speed_days;
  const sevenDayPeak24hSpeedDays = summary?.seven_day_peak_speed_days;
  const fiveHourUsedPercent = summary?.used_5h_percent;
  const sevenDayUsedPercent = summary?.used_7d_percent;
  const fiveHourRemainingPercent = summary?.available_5h_percent ?? remainingPercent(fiveHourUsedPercent);
  const actualFiveHourRemainingPercent = summary?.actual_available_5h_percent
    ?? availabilityPercent(summary?.five_hour_actual_remaining_usd, summary?.dynamic_five_hour_capacity_usd ?? summary?.five_hour_capacity_usd);
  const sevenDayRemainingPercent = summary?.available_7d_percent ?? remainingPercent(sevenDayUsedPercent);
  const actualSevenDayRemainingPercent = summary?.actual_available_7d_percent
    ?? availabilityPercent(summary?.seven_day_actual_remaining_usd, summary?.seven_day_capacity_usd);
  const traffic = poolTrafficMetrics(summary);
  const hasP90Runway = summary?.forecast_status === "active" && optionalNumberValue(summary?.forecast_p90_runway_hours) !== null;
  const primaryRunwayHours = hasP90Runway ? summary?.forecast_p90_runway_hours : summary?.actual_runway_hours;
  const primaryRunwayTarget = hasP90Runway ? summary?.target_runway_hours ?? 3 : summary?.actual_target_hours ?? 1;
  return (
    <section className={`capacity-runway-card ${tone} ${summary?.health_status || ""}`}>
      <div className="capacity-runway-head">
        <div>
          <span><MetricHelp helpKey="容量预估">容量预估</MetricHelp></span>
          <strong>{loading ? "加载中" : summary?.health_label || "暂无数据"}</strong>
          <em>{loading ? "正在读取账号池容量" : capacityHealthReason(summary)}</em>
        </div>
        <div className="capacity-runway-type">{accountType} 池</div>
      </div>
      <div className="capacity-runway-grid">
        <CapacityMetric
          label="动态5h总容量"
          value={formatUsd(summary?.dynamic_five_hour_capacity_usd ?? summary?.five_hour_capacity_usd)}
          sub={
            <CapacityMoneyLine
              label="动态可用额度"
              values={[
                ["当前已用", formatUsd(summary?.dynamic_five_hour_used_estimated_usd ?? summary?.five_hour_used_estimated_usd)],
                ["动态可用", formatUsd(summary?.dynamic_five_hour_remaining_estimated_usd ?? summary?.five_hour_remaining_estimated_usd)],
                ["实际可用", formatUsd(summary?.five_hour_actual_remaining_usd)],
              ]}
            />
          }
          percent={fiveHourRemainingPercent}
          tone={capacityAvailabilityTone(fiveHourUsedPercent, fiveHourRemainingPercent)}
          overlay={capacityOverlay("实际可用", formatPercent(actualFiveHourRemainingPercent), actualFiveHourRemainingPercent, capacityAvailabilityTone(null, actualFiveHourRemainingPercent))}
          meterLegendLabel="动态可用"
          meterValue={formatPercent(fiveHourRemainingPercent)}
          reverse
        />
        <CapacityMetric
          label="总容量：7d"
          value={formatUsd(summary?.seven_day_capacity_usd)}
          sub={
            <CapacityMoneyLine
              label="可用额度"
              values={[
                ["当前已用", formatUsd(summary?.seven_day_used_estimated_usd)],
                ["动态可用", formatUsd(summary?.seven_day_remaining_estimated_usd)],
                ["实际可用", formatUsd(summary?.seven_day_actual_remaining_usd)],
              ]}
            />
          }
          percent={sevenDayRemainingPercent}
          tone={capacityAvailabilityTone(sevenDayUsedPercent, sevenDayRemainingPercent)}
          overlay={capacityOverlay("实际可用", formatPercent(actualSevenDayRemainingPercent), actualSevenDayRemainingPercent, capacityAvailabilityTone(null, actualSevenDayRemainingPercent))}
          meterLegendLabel="动态可用"
          meterValue={formatPercent(sevenDayRemainingPercent)}
          reverse
        />
        <CapacityMetric
          label={hasP90Runway ? "P90 保守可用时间" : "当前速度可用时间"}
          value={formatRunwayHours(primaryRunwayHours)}
          sub={
            <>
              <MetricHelp helpKey="当前速度">当前速度</MetricHelp>{" "}
              <AnimatedValue value={formatRunwayHours(summary?.actual_runway_hours)} />
              {" · "}
              <MetricHelp helpKey="P50 期望">P50 期望</MetricHelp>{" "}
              <AnimatedValue value={formatRunwayHours(summary?.dynamic_runway_hours, summary?.forecast_dynamic_runway_capped)} />
            </>
          }
          percent={runwayScalePercent(primaryRunwayHours, primaryRunwayTarget)}
          tone={runwayTone(primaryRunwayHours, summary?.realtime_risk_ready)}
          meterLegendLabel={hasP90Runway ? "P90 保守覆盖" : "当前速度覆盖"}
          meterValue={formatRunwayHours(primaryRunwayHours)}
          meterTiered
        />
        <CapacityMetric
          label="压力阶段"
          value={summary?.pressure_stage_label || "等待数据"}
          sub={`TPM ${formatRate(traffic.tpm)} · RPM ${formatRate(traffic.rpm)} · ${summary?.sample_count || 0} 个分钟样本`}
          tone={pressureStageTone(summary?.pressure_stage)}
        />
        <CapacityMetric
          label="安全并发覆盖"
          value={formatMultiple(summary?.concurrency_coverage)}
          sub={`压力并发 ${formatRate(summary?.estimated_concurrency)} · 并发样本 ${summary?.concurrency_sample_count || 0} · 安全可用 ${formatRate(summary?.concurrency_safe_available)}${refillRecommendationText(summary, true) ? ` · ${refillRecommendationText(summary, true)}` : ""}`}
          percent={concurrencyCoverageScalePercent(summary?.concurrency_coverage, summary?.concurrency_target_coverage ?? 1.2)}
          tone={concurrencyCoverageTone(summary?.concurrency_coverage, summary?.realtime_risk_ready)}
          meterLegendLabel="目标"
          meterValue={formatMultiple(summary?.concurrency_target_coverage ?? 1.2)}
          meterTiered
        />
        <CapacityMetric
          label="突发趋势：最近1h"
          value={burstTrendLabel(summary)}
          sub={burstTrendSubText(summary)}
          tone={burstTrendTone}
        />
        <CapacityMetric
          label="峰值容量：最近一天5h"
          value={formatMultiple(recentDayFiveHourPeakMultiple)}
          sub={`峰值 ${formatUsd(recentDayFiveHourPeak)}，总容量：5h ${formatUsd(summary?.five_hour_capacity_usd)}`}
          percent={multipleScalePercent(recentDayFiveHourPeakMultiple)}
          tone={multipleScaleTone(recentDayFiveHourPeakMultiple)}
        />
        <CapacityMetric
          label="峰值容量：7天最高5h"
          value={formatMultiple(sevenDayFiveHourPeakMultiple)}
          sub={`峰值 ${formatUsd(sevenDayFiveHourPeak)}，总容量：5h ${formatUsd(summary?.five_hour_capacity_usd)}`}
          percent={multipleScalePercent(sevenDayFiveHourPeakMultiple)}
          tone={multipleScaleTone(sevenDayFiveHourPeakMultiple)}
        />
        <CapacityMetric
          label="突发峰值：1h预估"
          value={formatMultiple(burstOneHourMultiple)}
          sub={`当前小时已用 ${formatUsd(summary?.burst_1h_observed_cost)}，按${formatMinutes(summary?.burst_1h_elapsed_minutes)}折算1h ${formatUsd(summary?.burst_1h_cost)}，折算5h ${formatUsd(summary?.burst_1h_five_hour_estimated_cost)}`}
          percent={multipleScalePercent(burstOneHourMultiple)}
          tone={multipleScaleTone(burstOneHourMultiple)}
        />
        <CapacityMetric
          label="预估天数：最近24h"
          value={formatDays(recent24hSpeedDays)}
          sub={`按最近24h消耗 ${formatUsd(summary?.recent_24h_cost)}`}
          percent={daysScalePercent(recent24hSpeedDays)}
          tone={daysScaleTone(recent24hSpeedDays)}
        />
        <CapacityMetric
          label="预估天数：7天最高24h"
          value={formatDays(sevenDayPeak24hSpeedDays)}
          sub={`按7天最高24h消耗 ${formatUsd(summary?.seven_day_24h_peak_cost)}`}
          percent={daysScalePercent(sevenDayPeak24hSpeedDays)}
          tone={daysScaleTone(sevenDayPeak24hSpeedDays)}
        />
        <CapacityMetric
          label="预估消耗：最近24h"
          value={formatAccountCount(summary?.estimated_recent_24h_consumed_accounts ?? summary?.estimated_24h_consumed_accounts)}
          sub={`按最近24h消耗 ${formatUsd(summary?.recent_24h_cost)} / 单账号7d限额折算`}
        />
        <CapacityMetric
          label="预估消耗：7天最高24h"
          value={formatAccountCount(summary?.estimated_seven_day_peak_24h_consumed_accounts)}
          sub={`按7天最高24h消耗 ${formatUsd(summary?.seven_day_24h_peak_cost)} / 单账号7d限额折算`}
        />
      </div>
    </section>
  );
}

const ACCURACY_WINDOWS = ["24h", "7d", "28d"] as const;
const ACCURACY_HORIZONS = ["1h", "2-3h", "4-6h", "7-12h", "13-24h"];

function ForecastAccuracySummary({ accuracy, loading }: { accuracy?: ForecastAccuracy | null; loading: boolean }) {
  const [windowKey, setWindowKey] = useState<(typeof ACCURACY_WINDOWS)[number]>("24h");
  const window = accuracy?.windows?.[windowKey];
  const ready = !loading && accuracy?.status === "ready" && numberValue(window?.hourly_sample_count) > 0;
  const horizons = new Map((window?.horizon_buckets || []).map((item) => [item.key, item]));
  return (
    <section className="forecast-accuracy-band">
      <div className="forecast-accuracy-head">
        <div>
          <span><MetricHelp helpKey="预测准确性">预测准确性</MetricHelp></span>
          <strong>{ready ? `${windowKey} 滚动评估` : loading ? "加载中" : "等待最终结算样本"}</strong>
          <em>
            最终样本 <AnimatedValue value={ready ? numberValue(window?.hourly_sample_count) : 0} /> 个
            {ready ? <> · Nowcast <AnimatedValue value={numberValue(window?.nowcast_sample_count)} /> 个</> : null}
            {accuracy?.last_finalized_at ? <> · 最后结算 {formatDateTime(accuracy.last_finalized_at)}</> : null}
          </em>
        </div>
        <div className="forecast-accuracy-window" aria-label="准确性统计窗口">
          {ACCURACY_WINDOWS.map((item) => (
            <button
              className={windowKey === item ? "active" : ""}
              key={item}
              onClick={() => setWindowKey(item)}
              type="button"
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      {ready ? (
        <>
          <div className="forecast-accuracy-metrics">
            <AccuracyMetric
              label="P50 WAPE"
              value={formatAccuracyPercent(window?.p50_wape_percent)}
              tone={accuracyErrorTone(window?.p50_wape_percent)}
              sub={`MAE ${formatUsd(window?.p50_mae_usd)}`}
            />
            <AccuracyMetric
              label="P90 覆盖率"
              value={formatAccuracyPercent(window?.p90_coverage_percent)}
              tone={accuracyCoverageTone(window?.p90_coverage_percent)}
              sub={`Pinball ${formatUsd(window?.p90_pinball_loss_usd)}`}
            />
            <AccuracyMetric
              label="P50 偏差"
              value={formatSignedPercent(window?.p50_bias_percent)}
              tone={accuracyBiasTone(window?.p50_bias_percent)}
              sub="正值偏高估，负值偏低估"
            />
            <AccuracyMetric
              label="Nowcast WAPE"
              value={formatAccuracyPercent(window?.nowcast_selected_wape_percent)}
              tone={accuracyErrorTone(window?.nowcast_selected_wape_percent)}
              sub={`模型 ${formatAccuracyPercent(window?.nowcast_model_wape_percent)} · 实时 ${formatAccuracyPercent(window?.nowcast_realtime_wape_percent)}`}
            />
          </div>

          <div className="forecast-accuracy-horizon-wrap">
            <div className="forecast-accuracy-horizon-title">
              <strong>预测步长</strong>
              <span>{accuracy?.model || "-"} · v{accuracy?.version || "-"}</span>
            </div>
            <div className="forecast-accuracy-horizons">
              {ACCURACY_HORIZONS.map((key) => {
                const item = horizons.get(key);
                return (
                  <div key={key}>
                    <strong>{key}</strong>
                    <span>WAPE <b>{formatAccuracyPercent(item?.p50_wape_percent)}</b></span>
                    <span>P90 <b>{formatAccuracyPercent(item?.p90_coverage_percent)}</b></span>
                    <em>{numberValue(item?.sample_count)} 样本</em>
                  </div>
                );
              })}
            </div>
          </div>
        </>
      ) : (
        <div className="forecast-accuracy-empty">
          <strong>{loading ? "正在读取预测结算记录" : "预测会在目标小时结束 90 分钟后计入最终准确性"}</strong>
          <span>临时结果不会进入团队评估，避免上游用量日志延迟造成误判。</span>
        </div>
      )}
    </section>
  );
}

function AccuracyMetric({
  label,
  value,
  tone,
  sub,
}: {
  label: string;
  value: string;
  tone: CapacityMetricTone;
  sub: string;
}) {
  return (
    <div className="forecast-accuracy-metric">
      <span><MetricHelp helpKey={label}>{label}</MetricHelp></span>
      <strong className={tone}><AnimatedValue value={value} /></strong>
      <em>{sub}</em>
    </div>
  );
}

type CapacityMetricTone = "excellent" | "info" | "success" | "warning" | "danger" | "muted";
type CapacityMeterOverlay = {
  label: string;
  value: string;
  percent?: number | null;
  tone?: CapacityMetricTone;
};

type MetricHelpDetail = {
  purpose: string;
  formula: string;
  note?: string;
};

const METRIC_HELP_DETAILS: Record<string, MetricHelpDetail> = {
  "预测准确性": {
    purpose: "检查当前逐小时预测和当前小时 Nowcast 在真实流量上的误差，确认容量判断是否可信。",
    formula: "目标小时结束 15 分钟后生成临时结果，90 分钟后按 PostgreSQL 最终 account_cost 结算；这里只统计 final 样本。",
    note: "24h、7d、28d 都按当前模型版本独立滚动统计，旧版本记录保留但不会混入当前指标。",
  },
  "P50 WAPE": {
    purpose: "衡量 P50 预测整体偏离真实消耗的幅度，数值越低越准确。",
    formula: "所有样本绝对误差之和 / 所有样本真实 account_cost 之和。",
    note: "使用 WAPE 而不是普通 MAPE，避免低流量和零流量小时放大误差。",
  },
  "P90 覆盖率": {
    purpose: "检查真实消耗落在 P90 风险边界以内的频率。",
    formula: "actual_account_cost <= predicted_p90 的最终样本数 / 最终样本总数。",
    note: "长期明显低于 90% 表示风险边界过窄；长期接近 100% 可能表示预测过于保守。",
  },
  "P50 偏差": {
    purpose: "判断模型是系统性高估还是低估消耗。",
    formula: "所有 (P50预测 - 真实消耗) 之和 / 所有真实消耗之和。",
    note: "正值表示高估，负值表示低估；容量维护更需要警惕持续负偏差。",
  },
  "Nowcast WAPE": {
    purpose: "衡量当前小时中途结合 TPM/RPM 后，对剩余小时消耗的估计准确性。",
    formula: "所有 selected remaining 绝对误差之和 / 所有真实 remaining 消耗之和。",
    note: "下方同时列出原模型通道和实时通道，便于判断最终选择策略是否真正改善误差。",
  },
  "账号池概览": {
    purpose: "快速判断当前分组中有多少账号正在工作，以及还有多少账号属于正常资产。",
    formula: "概览由 active、正常账号、5h 429、7d 429和异常数量共同组成。所有数字来自完整分组缓存，不受当前分页影响。",
  },
  "active / 正常": {
    purpose: "左侧是当前可调度账号，右侧是仍可维护和恢复的正常账号总数。",
    formula: "active = 正常且调度开启，并且当前没有5h/7d 429；正常 = 全部账号 - Bug Team - 401/封禁等异常账号。正常账号包含429和手动关闭调度的账号。",
  },
  "5h 429": {
    purpose: "显示短周期额度已耗尽、等待5h窗口恢复的账号数量。",
    formula: "7d未耗尽，并且5h使用率 >= 100%，或远端返回当前有效的429临时限流。",
    note: "同一账号同时满足5h和7d限流时只计入7d 429，避免重复统计。",
  },
  "7d 429": {
    purpose: "显示周额度已经耗尽的账号数量，用于判断中长期容量缺口。",
    formula: "账号7d使用率 >= 100%。",
  },
  "异常数量": {
    purpose: "显示无法通过等待额度窗口自行恢复、需要人工处理的账号。",
    formula: "401、Token revoked、Token invalidated、Authentication failed、403、封禁/停用，或非429的明确错误状态。",
    note: "凭证错误优先于旧429状态判断；Bug Team单独排除，不计入异常数量。",
  },
  "并发容量": {
    purpose: "判断账号池能同时承接多少请求，以及其中多少并发可以立即、安全地使用。",
    formula: "按账号的最大并发、当前并发、5h/7d用量和恢复时间汇总。Bug Team、异常账号和需要超过1天恢复的7d 429不进入可恢复总量。",
  },
  "当前并发": {
    purpose: "显示当前已经被请求占用的并发槽位。",
    formula: "Σ min(账号当前并发, 账号最大并发)，仅统计可恢复并发范围内的账号。",
  },
  "安全可用并发": {
    purpose: "推荐优先使用的低风险并发余量。",
    formula: "Σ(最大并发 - 当前并发)，且账号正常、可调度、5h使用率 < 80%、7d使用率 < 80%。",
    note: "安全可用并发是即时可用并发的一部分。",
  },
  "即时可用并发": {
    purpose: "显示此刻可以直接接收新请求的全部并发余量。",
    formula: "Σ(最大并发 - 当前并发)，排除当前5h/7d 429、异常、关闭调度和其他不可用账号。",
  },
  "可恢复总并发容量": {
    purpose: "显示当前可用并发加上短期恢复后能够重新投入的理论总并发。",
    formula: "Σ账号最大并发，包含短期5h/7d 429；排除异常、Bug Team和需要超过1天恢复的7d 429。",
  },
  "临界可用并发": {
    purpose: "当前能用，但5h或7d用量已经接近高位的并发余量。",
    formula: "即时可用并发 - 安全可用并发。通常表示至少一个额度窗口使用率 >= 80%。",
  },
  "暂时不可用并发": {
    purpose: "显示可恢复总量中因短期限流或调度状态暂时不能使用的部分。",
    formula: "可恢复总并发容量 - 当前并发 - 即时可用并发。",
    note: "此处主数值单位是并发槽位，不是账号数量；界面会同时显示涉及的账号数。5h 429属于可恢复容量，长期7d、401和Bug Team不进入该值。",
  },
  "容量预估": {
    purpose: "使用未来逐小时额度需求预测、实际额度和安全并发判断当前分组还能支撑多久。",
    formula: "预计可用时间使用未来24小时P50逐小时预测，P90单独保留为风险参考；当前小时结合已发生account_cost与分钟直接成本速度进行Nowcast。流量阶段和并发仍读取每分钟TPM/RPM。",
    note: "耗尽：账号 <=2或动态不足30分钟；危险：实际不足1小时、动态不足1小时或并发<1x；需要补号：动态不足3小时或并发<1.2x。预测不可用时自动降级到TPM实时估算。",
  },
  "P90 保守可用时间": {
    purpose: "按高消耗风险边界估算账号池还能支撑多久，作为容量告警和补号判断的主要显示。",
    formula: "使用未来逐小时P90消耗依次扣减动态可用额度；当前小时同时保留模型风险上界和实时消耗速度中的较高值。",
    note: "P90追求约90%的真实消耗不超过该边界，因此比P50更保守；它不是最可能发生的时长，也不代表预测误差一定更小。",
  },
  "当前速度": {
    purpose: "显示保持当前分钟消耗速度不变时，实际可用额度还能支撑多久。",
    formula: "当前速度可用时间 = min(5h实际可用, 7d实际可用) / account_cost分钟速率。",
    note: "该值响应当前压力最快，但不考虑未来昼夜周期、流量回落或继续上涨。",
  },
  "P50 期望": {
    purpose: "显示按最可能的季节性需求路径，动态可用额度预计还能支撑多久。",
    formula: "使用未来逐小时P50中位需求路径依次扣减动态可用额度；当前小时使用实时account_cost速度进行Nowcast。",
    note: "P50是中位需求路径，约一半情况下真实消耗可能高于它，适合观察正常昼夜变化，不作为单独的保底线；超过24小时显示为 >24小时。",
  },
  "压力阶段": {
    purpose: "把流量变化和当前容量风险归纳为一个运营阶段，用于判断继续观察、准备补号、峰值保底还是关注库存风险。",
    formula: "综合TPM/RPM的EMA5、EMA15、EMA60计算短期动量和需求倍率，并结合流量是否确认回落、实时容量健康状态和动态可用时间判定。",
    note: "页面只显示当前账号池分组的最新原始TPM/RPM。预测内部使用同一站点、同一分组的EMA/P90压力值，不汇总客户端站点。等待数据：分钟样本尚未就绪；稳定：需求倍率低于1.2；压力传导：需求倍率达到1.2；加速上涨：需求倍率达到1.5或TPM短期动量达到1.2；峰值保底：容量已危险或耗尽；回落观察：流量确认下降；库存风险：流量下降且动态可用时间超过6小时。",
  },
  "安全并发覆盖": {
    purpose: "判断低额度风险账号之外的安全并发，能覆盖当前压力并发多少倍。",
    formula: "安全并发覆盖 = 安全可用并发 / 压力并发；压力并发取最近5分钟EMA与最近1小时P90中的较大值。安全可用并发只统计正常、可调度且5h和7d使用率都低于80%的余量。",
    note: "并发覆盖使用独立于峰值容量的分级：低于1.5x为红色，1.5-3x为黄色，3-5x为绿色，5-10x为蓝色，10x及以上为紫色顶级。分钟数据尚未就绪时显示灰色。",
  },
  "动态5h总容量": {
    purpose: "显示当前分组在5h窗口下用于动态评估的总额度。",
    formula: "Σ远端当前分组中对应账号类型的5h额度；排除Bug Team、异常账号和7d已耗尽账号。",
  },
  "总容量：7d": {
    purpose: "显示当前分组完整的周额度规模。",
    formula: "Σ远端当前分组中对应账号类型的7d额度；排除Bug Team和异常账号。",
  },
  "实际池": {
    purpose: "当前已经在远端使用分组中的账号额度。",
    formula: "Σ远端使用池中符合当前账号类型的单账号额度。",
  },
  "备用池": {
    purpose: "本地备用账号可以补入使用池的额度。",
    formula: "Σ绑定当前站点和分组、处于备用池且符合当前账号类型的单账号额度。",
  },
  "动态可用额度": {
    purpose: "比较动态恢复后的可用额度与此刻实际可用额度。",
    formula: "动态可用会计入即将刷新的额度；实际可用只按当前使用率计算。双层进度条外层为动态可用，内层为实际可用。",
  },
  "可用额度": {
    purpose: "显示7d窗口的动态可用和当前实际可用额度。",
    formula: "动态可用会计入2天内即将恢复的周额度；超过2天才恢复的账号只计算当前实际额度。",
  },
  "当前已用": {
    purpose: "显示容量模型中当前已经消耗的等效额度。",
    formula: "总容量 - 动态可用额度。临近刷新时会按剩余恢复时间折算，因此可能低于按使用率直接计算的已用额度。",
  },
  "动态可用": {
    purpose: "显示当前实际剩余额度，加上短期内即将恢复的可用部分。",
    formula: "5h：恢复时间<=2小时则按剩余时间/窗口时长折算，否则只算实际剩余；7d使用相同逻辑，但未来恢复上限为2天。",
  },
  "实际可用": {
    purpose: "显示此刻无需等待刷新就能使用的额度。",
    formula: "Σ 单账号额度 × (1 - 当前使用率)。不计算任何未来刷新。",
  },
  "峰值容量：最近一天5h": {
    purpose: "衡量总容量能覆盖最近24小时内最忙5小时消耗的倍数。",
    formula: "倍数 = 5h总容量 / 最近24小时内任意连续5小时的最高消耗。",
  },
  "峰值容量：7天最高5h": {
    purpose: "衡量总容量能否覆盖最近7天出现过的最坏5小时峰值。",
    formula: "倍数 = 5h总容量 / 最近7天内任意连续5小时的最高消耗。",
  },
  "突发峰值：1h预估": {
    purpose: "比5小时峰值更快发现当前小时突然上涨的流量。",
    formula: "1h预估 = 当前小时已用 / 已经过分钟 × 60；折算5h = 1h预估 × 5；倍数 = 5h总容量 / 折算5h消耗。",
  },
  "突发趋势：最近1h": {
    purpose: "判断近期消耗是在上涨、下降还是保持平稳。",
    formula: "每小时均值 = 对应时间段总消耗 / 有效小时数；变化率 = (近3小时每小时均值 - 前3小时每小时均值) / 前3小时每小时均值。再按变化幅度标记趋势强度。",
  },
  "预估天数：最近24h": {
    purpose: "按当前日常速度估算剩余额度还能维持多久。",
    formula: "可用天数 = 7d动态可用额度 / 最近24小时消耗。",
  },
  "预估天数：7天最高24h": {
    purpose: "按最近7天最忙的一天估算保守可用时间。",
    formula: "可用天数 = 7d动态可用额度 / 最近7天任意24小时最高消耗。",
  },
  "预估消耗：最近24h": {
    purpose: "把最近24小时成本换算成完整账号周额度数量，方便估算补号规模。",
    formula: "预估账号数 = 最近24小时消耗 / 当前账号类型的单账号7d额度。",
  },
  "预估消耗：7天最高24h": {
    purpose: "按最近7天最忙的一天换算需要消耗多少个完整账号周额度。",
    formula: "预估账号数 = 最近7天任意24小时最高消耗 / 当前账号类型的单账号7d额度。",
  },
};

function MetricHelp({ helpKey, children }: { helpKey: string; children: ReactNode }) {
  const tooltipId = useId();
  const help = METRIC_HELP_DETAILS[helpKey];
  if (!help) return <>{children}</>;
  return (
    <span className="metric-help" tabIndex={0} aria-describedby={tooltipId}>
      <span className="metric-help-trigger">{children}</span>
      <span className="metric-help-tooltip" id={tooltipId} role="tooltip">
        <strong>{helpKey}</strong>
        <span className="metric-help-row"><b>用途</b><em>{help.purpose}</em></span>
        <span className="metric-help-row"><b>计算</b><em>{help.formula}</em></span>
        {help.note ? <span className="metric-help-row note"><b>说明</b><em>{help.note}</em></span> : null}
      </span>
    </span>
  );
}

function CapacityMoneyLine({ label, values }: { label: string; values: Array<[string, string]> }) {
  return (
    <span className="capacity-money-line">
      <span><MetricHelp helpKey={label}>{label}</MetricHelp>：</span>
      {values.map(([itemLabel, itemValue], index) => {
        const itemClass = index === 0 ? "current-used" : index === 1 ? "dynamic-available" : "actual-available";
        return (
          <Fragment key={itemLabel}>
            {index === 1 ? <span aria-hidden="true" className="capacity-money-break" /> : null}
            <span className={`capacity-money-item ${itemClass}`}>
              {index > 0 ? <span className="capacity-money-separator">，</span> : null}
              <span><MetricHelp helpKey={itemLabel}>{itemLabel}</MetricHelp> </span>
              <strong><AnimatedValue value={itemValue} /></strong>
            </span>
          </Fragment>
        );
      })}
    </span>
  );
}

function CapacitySubText({ value }: { value: ReactNode }) {
  if (typeof value !== "string") return <>{value}</>;
  const parts = value.split(/(\$[\d,]+(?:\.\d+)?|[-+]?\d[\d,]*(?:\.\d+)?(?:%|x)?)/g);
  return (
    <>
      {parts.map((part, index) => {
        if (part.startsWith("$")) {
          return (
            <strong className="capacity-money-strong" key={`${part}-${index}`}>
              <AnimatedValue value={part} />
            </strong>
          );
        }
        if (/^[-+]?\d[\d,]*(?:\.\d+)?(?:%|x)?$/.test(part)) {
          return <AnimatedValue key={`${part}-${index}`} value={part} />;
        }
        return part;
      })}
    </>
  );
}

function capacityOverlay(label: string, value: string, percent?: number | null, tone?: CapacityMetricTone): CapacityMeterOverlay | undefined {
  if (percent === undefined || percent === null) return undefined;
  return { label, value, percent, tone };
}

function capacityHealthReason(summary?: CapacitySummary) {
  if (!summary) return "等待 dashboard cost 数据";
  const reason = summary.health_reason || "等待 dashboard cost 数据";
  const recommendation = refillRecommendationText(summary);
  const refill = recommendation ? `；${recommendation}` : "";
  return `${reason}${refill}`;
}

const refillAccountTypeLabels: Record<string, string> = {
  free: "Free",
  plus: "Plus",
  team: "Team",
  k12: "K12",
  pro: "Pro",
};

function refillRecommendationText(summary?: CapacitySummary, compact = false) {
  if (!summary?.replenishment_required) return "";
  const options = Object.entries(summary.recommended_refill_options || {})
    .map(([key, option]) => {
      const count = Math.max(0, Math.round(numberValue(option.recommended_refill_accounts)));
      if (count <= 0) return "";
      const accountType = String(option.account_type || key).trim().toLowerCase();
      const label = refillAccountTypeLabels[accountType] || accountType.toUpperCase() || "账号";
      return `${label} ${count} 个`;
    })
    .filter(Boolean);
  if (options.length > 0) {
    if (compact) return `建议 ${options.join(" / ")}（仅供参考）`;
    return `建议补号：${options.join("，或 ")}。仅供参考，请结合实时供货和账号质量判断。`;
  }
  const legacyCount = Math.max(0, Math.round(numberValue(summary.recommended_refill_accounts)));
  if (legacyCount <= 0) return "";
  if (compact) return `建议 ${legacyCount} 个（仅供参考）`;
  return `建议补号：${legacyCount} 个。仅供参考，请结合实时供货和账号质量判断。`;
}

function CapacityMetric({
  label,
  value,
  sideValues,
  sub,
  percent,
  tone = "muted",
  overlay,
  meterLabel,
  meterLegendLabel,
  meterValue,
  meterTiered = false,
  reverse = false,
  showMeterHead = false,
  secondary,
}: {
  label: string;
  value: string;
  sideValues?: Array<{
    label: string;
    value: string;
  } | undefined>;
  sub: ReactNode;
  percent?: number | null;
  tone?: CapacityMetricTone;
  overlay?: CapacityMeterOverlay;
  meterLabel?: string;
  meterLegendLabel?: string;
  meterValue?: string;
  meterTiered?: boolean;
  reverse?: boolean;
  showMeterHead?: boolean;
  secondary?: {
    label: string;
    value: string;
    percent?: number | null;
    tone?: CapacityMetricTone;
    overlay?: CapacityMeterOverlay;
    reverse?: boolean;
  };
}) {
  const [labelMain, labelSuffix] = label.split("：");
  const visibleSideValues = (sideValues || []).filter((item): item is { label: string; value: string } => Boolean(item));
  const showInlineSub = Boolean(overlay && !reverse);
  const valueIsMoney = value.startsWith("$");
  return (
    <div className="capacity-metric">
      <span className="capacity-metric-label">
        <MetricHelp helpKey={label}>
          <b>{labelMain}</b>
          {labelSuffix ? <em>：{labelSuffix}</em> : null}
        </MetricHelp>
      </span>
      <div className="capacity-metric-value-row">
        <strong className={`capacity-metric-value ${tone} ${valueIsMoney ? "money" : ""}`}>
          {showInlineSub ? (
            <>
              <span>含备用</span>
              <AnimatedValue value={value} />
            </>
          ) : <AnimatedValue value={value} />}
        </strong>
        {showInlineSub && (
          <small className="capacity-metric-inline-sub">
            <CapacitySubText value={sub} />
          </small>
        )}
        {visibleSideValues.length > 0 && (
          <span className="capacity-metric-side-values">
            {visibleSideValues.map((item) => (
              <span className="capacity-metric-side-value" key={item.label}>
                <em><MetricHelp helpKey={item.label}>{item.label}</MetricHelp></em>
                <b><AnimatedValue value={item.value} /></b>
              </span>
            ))}
          </span>
        )}
      </div>
      {!showInlineSub && (
        <small>
          <CapacitySubText value={sub} />
        </small>
      )}
      {percent !== undefined && percent !== null && (
        <div className="capacity-primary-meter">
          {(showMeterHead || meterLabel) && (
            <div className="capacity-secondary-head">
              <span>{meterLabel || sub}</span>
              <strong className={`capacity-secondary-value ${tone}`}><AnimatedValue value={meterValue || value} /></strong>
            </div>
          )}
          {overlay && <CapacityMeterLegend overlay={overlay} baseLabel={meterLegendLabel} baseValue={meterValue || value} baseTone={tone} />}
          <CapacityMeter label={label} percent={percent} tone={tone} overlay={overlay} reverse={reverse} tiered={meterTiered} />
        </div>
      )}
      {secondary && (
        <div className="capacity-secondary">
          <div className="capacity-secondary-head">
            <span>{secondary.label}</span>
            <strong className={`capacity-secondary-value ${secondary.tone || "muted"}`}><AnimatedValue value={secondary.value} /></strong>
          </div>
          {secondary.percent !== undefined && secondary.percent !== null && (
            <>
              {secondary.overlay && <CapacityMeterLegend overlay={secondary.overlay} baseValue={secondary.value} baseTone={secondary.tone || "muted"} />}
              <CapacityMeter label={`${label} ${secondary.label}`} percent={secondary.percent} tone={secondary.tone || "muted"} overlay={secondary.overlay} reverse={secondary.reverse} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function CapacityMeterLegend({
  overlay,
  baseLabel = "含备用",
  baseValue,
  baseTone,
}: {
  overlay: CapacityMeterOverlay;
  baseLabel?: string;
  baseValue: string;
  baseTone: CapacityMetricTone;
}) {
  return (
    <div className="capacity-meter-legend">
      <span className={`capacity-meter-legend-value ${overlay.tone || "muted"}`}><MetricHelp helpKey={overlay.label}>{overlay.label}</MetricHelp> <AnimatedValue value={overlay.value} /></span>
      <span className={`capacity-meter-legend-value ${baseTone}`}><MetricHelp helpKey={baseLabel}>{baseLabel}</MetricHelp> <AnimatedValue value={baseValue} /></span>
    </div>
  );
}

function CapacityMeter({
  label,
  percent,
  tone,
  overlay,
  reverse = false,
  tiered = false,
}: {
  label: string;
  percent: number;
  tone: CapacityMetricTone;
  overlay?: CapacityMeterOverlay;
  reverse?: boolean;
  tiered?: boolean;
}) {
  return (
    <div className={`capacity-meter ${overlay ? "layered" : ""} ${reverse ? "reverse" : ""} ${tiered ? "tiered" : ""}`} aria-label={`${label} ${percent}%`}>
      <div className={`capacity-meter-fill reserve ${tone}`} style={{ width: `${clampPercent(percent)}%` }} />
      {overlay?.percent !== undefined && overlay.percent !== null && (
        <div className={`capacity-meter-fill active ${overlay.tone || "muted"}`} style={{ width: `${clampPercent(overlay.percent)}%` }} />
      )}
    </div>
  );
}

function CompactStats({ items }: { items: Array<[string, string | number]> }) {
  return (
    <section className="compact-stats">
      {items.map(([label, value]) => (
        <div className="compact-stat" key={label}>
          <span>{label}</span>
          <strong><AnimatedValue value={value} /></strong>
        </div>
      ))}
    </section>
  );
}

function MiniMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="mini-metric">
      <strong><AnimatedValue value={value} /></strong>
      <span><MetricHelp helpKey={label}>{label}</MetricHelp></span>
    </div>
  );
}

function StatusPill({ value, tone = "muted" }: { value: string; tone?: "accent" | "success" | "warning" | "danger" | "muted" }) {
  return <span className={`status-pill ${tone}`}>{value}</span>;
}

function summarizeGroups(groups: Group[]) {
  return groups.reduce(
    (summary, group) => {
      summary.totalAccounts += numberValue(group.account_count);
      summary.activeAccounts += numberValue(group.active_account_count);
      summary.rateLimitedAccounts += numberValue(group.rate_limited_account_count);
      return summary;
    },
    { totalAccounts: 0, activeAccounts: 0, rateLimitedAccounts: 0 },
  );
}

function sortSitesByPinned(sites: Site[], pinnedSiteId?: string | null): Site[] {
  if (!pinnedSiteId) return sites;
  return [...sites].sort((left, right) => {
    if (left.id === pinnedSiteId) return -1;
    if (right.id === pinnedSiteId) return 1;
    return 0;
  });
}

function sortGroupsByPinned(groups: Group[], pinnedGroupId?: number | null): Group[] {
  if (pinnedGroupId === undefined || pinnedGroupId === null) return groups;
  return [...groups].sort((left, right) => {
    if (left.id === pinnedGroupId) return -1;
    if (right.id === pinnedGroupId) return 1;
    return 0;
  });
}

function chooseSiteId(sites: Site[], currentSiteId?: string | null, pinnedSiteId?: string | null): string {
  if (pinnedSiteId && sites.some((site) => site.id === pinnedSiteId)) return pinnedSiteId;
  if (currentSiteId && sites.some((site) => site.id === currentSiteId)) return currentSiteId;
  return sites[0]?.id || "";
}

function chooseGroupId(groups: Group[], currentGroupId?: number | null, pinnedGroupId?: number | null): number | null {
  if (pinnedGroupId !== undefined && pinnedGroupId !== null && groups.some((group) => group.id === pinnedGroupId)) {
    return pinnedGroupId;
  }
  if (currentGroupId !== undefined && currentGroupId !== null && groups.some((group) => group.id === currentGroupId)) {
    return currentGroupId;
  }
  return groups[0]?.id ?? null;
}

function summarizeRemoteAccounts(accounts: RemoteAccount[]) {
  return accounts.reduce(
    (summary, account) => {
      const health = accountHealth(account);
      if (health === "healthy") summary.healthy += 1;
      if (health === "warning") summary.warning += 1;
      if (health === "error") summary.error += 1;
      if (isRateLimited(account)) summary.rateLimited += 1;
      if (isFutureDate(account.rate_limit_reset_at)) summary.rateLimitResetting += 1;
      if (isFutureDate(account.temp_unschedulable_until)) summary.tempUnschedulable += 1;
      return summary;
    },
    { healthy: 0, warning: 0, error: 0, rateLimited: 0, rateLimitResetting: 0, tempUnschedulable: 0 },
  );
}

function accountHealth(account: RemoteAccount): "healthy" | "warning" | "error" | "unknown" {
  const status = (account.status || "").toLowerCase();
  if (isAuthenticationError(account)) return "error";
  if (!status) return "unknown";
  if (isTemporaryRateLimit(account)) return "warning";
  if (["error", "disabled", "paused", "banned", "invalid", "failed"].includes(status)) return "error";
  if (account.error_message) return "error";
  if (isFutureDate(account.temp_unschedulable_until) || isFutureDate(account.rate_limit_reset_at)) return "warning";
  if (status === "active") return "healthy";
  return "unknown";
}

function isRateLimited(account: RemoteAccount): boolean {
  return isFutureDate(account.rate_limit_reset_at) || isTemporaryRateLimit(account);
}

function isTemporaryRateLimit(account: RemoteAccount): boolean {
  const hasActiveUntil = isFutureDate(account.rate_limit_reset_at);
  const combined = [
    account.status,
    account.error_message,
    account.temp_unschedulable_reason,
  ]
    .map((value) => text(value).toLowerCase())
    .join(" ");
  return hasActiveUntil || combined.includes("429") || combined.includes("529") || combined.includes("rate limit") || combined.includes("限流");
}

function isAuthenticationError(account: RemoteAccount): boolean {
  const extra = account.extra || {};
  const combined = [
    account.error_message,
    account.temp_unschedulable_reason,
    account.credentials_status,
    extra.error_message,
    extra.last_error,
    extra.credentials_status,
  ]
    .map((value) => text(value).toLowerCase())
    .join(" ");
  return [
    "401",
    "unauthorized",
    "authentication failed",
    "token revoked",
    "token_invalidated",
    "token invalidated",
    "invalid oauth",
    "invalid token",
    "oauth token",
    "凭证失效",
    "认证失败",
  ].some((marker) => combined.includes(marker));
}

function isFutureDate(value: unknown): boolean {
  if (!value) return false;
  const date = parseDisplayDate(value);
  return Number.isFinite(date.getTime()) && date.getTime() > Date.now();
}

function accountStatusView(account: RemoteAccount): { label: string; tone: "accent" | "success" | "warning" | "danger" | "muted"; detail?: string } {
  const status = (account.status || "").toLowerCase();
  if (isAuthenticationError(account)) {
    return {
      label: "异常",
      tone: "danger",
      detail: account.error_message ? text(account.error_message) : account.temp_unschedulable_reason ? text(account.temp_unschedulable_reason) : "账号凭证认证失败",
    };
  }
  if (isFutureDate(account.temp_unschedulable_until)) {
    return {
      label: "临时不可调度",
      tone: "warning",
      detail: account.temp_unschedulable_reason ? text(account.temp_unschedulable_reason) : `恢复 ${formatOptionalDate(account.temp_unschedulable_until)}`,
    };
  }
  if (isRateLimited(account)) {
    return {
      label: "限流中",
      tone: "warning",
      detail: account.rate_limit_reset_at ? `重置 ${formatOptionalDate(account.rate_limit_reset_at)}` : undefined,
    };
  }
  if (account.error_message) {
    return { label: "异常", tone: "danger" };
  }
  return { label: displayStatus(account.status), tone: statusTone(account.status) };
}

function accountSchedulableView(account: RemoteAccount): { label: string; tone: "success" | "warning" | "muted" } {
  if (account.schedulable === true) {
    return { label: "调度开启", tone: "success" };
  }
  if (account.schedulable === false) {
    return { label: "调度关闭", tone: "warning" };
  }
  return { label: "未返回", tone: "muted" };
}

function statusTone(value?: string): "accent" | "success" | "warning" | "danger" | "muted" {
  const normalized = (value || "").toLowerCase();
  if (["active", "ok", "healthy"].includes(normalized)) return "success";
  if (["paused", "unknown", ""].includes(normalized)) return "muted";
  if (["expired", "warning"].includes(normalized)) return "warning";
  if (["invalid", "error", "failed", "banned", "disabled"].includes(normalized)) return "danger";
  return "accent";
}

function displayStatus(value?: string): string {
  const normalized = (value || "").toLowerCase();
  if (normalized === "active") return "正常";
  if (normalized === "error") return "异常";
  if (normalized === "disabled") return "禁用";
  if (normalized === "paused") return "暂停";
  return value || "unknown";
}

function displayPlatform(value?: string): string {
  const normalized = (value || "").toLowerCase();
  if (normalized === "openai") return "OpenAI";
  return value || "-";
}

function displayPlan(value: string): string {
  const normalized = value.toLowerCase();
  if (normalized === "bug_team") return "Bug Team";
  if (["team", "team_sub", "team-sub", "team_child", "team子号", "team 子号"].includes(normalized)) return "Team子号";
  if (normalized === "k12") return "K12";
  if (normalized === "plus") return "Plus";
  if (normalized === "pro") return "Pro";
  if (normalized === "free") return "Free";
  return value;
}

function planTagTone(value: string): string {
  const normalized = value.toLowerCase();
  if (normalized === "bug_team") return "plan-team";
  if (["team", "team_sub", "team-sub", "team_child", "team子号", "team 子号"].includes(normalized)) return "plan-team";
  if (normalized === "k12") return "plan-k12";
  if (normalized === "plus") return "plan-plus";
  if (normalized === "free") return "plan-free";
  if (normalized === "pro") return "plan-pro";
  return "plan-other";
}

function displayPrivacy(value: string): string {
  if (value === "training_off") return "Private";
  if (value === "training_on") return "Training";
  return value;
}

function remoteTestLabel(value: string): string {
  if (value === "passed") return "通过";
  if (value === "failed") return "失败";
  return value;
}

function privacyTagTone(value: string): string {
  if (value === "training_off" || value.toLowerCase() === "private") return "privacy-private";
  if (value === "training_on") return "privacy-training";
  return "privacy-other";
}

function formatOptionalDate(value: unknown): string {
  const formatted = formatDateTime(value);
  return formatted && formatted !== "-" ? formatted : "从未";
}

function formatDuration(value: unknown): string {
  const seconds = numberValue(value);
  if (seconds <= 0) return "现在";
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatUsd(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null) return "$-";
  if (Math.abs(number) >= 100) return `$${number.toFixed(0)}`;
  if (Math.abs(number) >= 10) return `$${number.toFixed(1)}`;
  return `$${number.toFixed(2)}`;
}

function formatMultiple(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null) return "-";
  return `${number.toFixed(2)}x`;
}

function formatRunwayHours(value: unknown, capped = false): string {
  const number = optionalNumberValue(value);
  if (number === null) return "-";
  let formatted: string;
  if (number < 1) formatted = `${Math.max(0, Math.round(number * 60))}分钟`;
  else if (number < 10) formatted = `${number.toFixed(1)}小时`;
  else formatted = `${number.toFixed(0)}小时`;
  return capped ? `>${formatted}` : formatted;
}

function formatRate(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null) return "-";
  return Math.round(number).toLocaleString("zh-CN");
}

function pressureStageTone(value?: string): CapacityMetricTone {
  if (value === "peak_guard") return "danger";
  if (value === "accelerating" || value === "transmission") return "warning";
  if (value === "inventory_risk") return "info";
  if (value === "stable" || value === "recovering") return "success";
  return "muted";
}

function formatPercentChange(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null) return "-";
  const sign = number > 0 ? "+" : "";
  return `${sign}${number.toFixed(0)}%`;
}

function formatMinutes(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null) return "-";
  return `${Math.max(1, Math.round(number))}分钟`;
}

function formatHourCount(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null || number <= 0) return "-";
  return `${Math.round(number)}小时`;
}

function burstTrendLabel(summary?: CapacitySummary): string {
  if (!summary?.burst_1h_trend_label) return "等待数据";
  const strength = summary.burst_1h_trend_strength_label && summary.burst_1h_trend_strength_label !== "等待数据" ? ` · ${summary.burst_1h_trend_strength_label}` : "";
  return `${summary.burst_1h_trend_label}${strength}`;
}

function burstTrendSubText(summary?: CapacitySummary): string {
  if (!summary) return "等待 dashboard cost 数据";
  const recent = `近${formatHourCount(summary.burst_1h_trend_recent_hours)}每小时均值 ${formatUsd(summary.burst_1h_trend_recent_avg_cost)}`;
  const baselineHours = optionalNumberValue(summary.burst_1h_trend_baseline_hours);
  if (!baselineHours || baselineHours <= 0) return `${recent}，等待更多历史小时数据`;
  return `${recent}，前${formatHourCount(baselineHours)}每小时均值 ${formatUsd(summary.burst_1h_trend_baseline_avg_cost)}，变化 ${formatPercentChange(summary.burst_1h_trend_change_percent)}`;
}

function formatDays(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null) return "-";
  if (number >= 10) return `${number.toFixed(0)}天`;
  if (number >= 1) return `${number.toFixed(1)}天`;
  return `${Math.max(0, number * 24).toFixed(1)}小时`;
}

function formatAccountCount(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null) return "-";
  if (number >= 10) return `约 ${number.toFixed(0)} 个`;
  if (number >= 1) return `约 ${number.toFixed(1)} 个`;
  return `约 ${Math.max(0, number).toFixed(2)} 个`;
}

function formatPercent(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null) return "-";
  return `${Math.round(number)}%可用`;
}

function formatAccuracyPercent(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null) return "-";
  return `${number.toFixed(1)}%`;
}

function formatSignedPercent(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null) return "-";
  return `${number > 0 ? "+" : ""}${number.toFixed(1)}%`;
}

function accuracyErrorTone(value: unknown): CapacityMetricTone {
  const number = optionalNumberValue(value);
  if (number === null) return "muted";
  if (number <= 15) return "success";
  if (number <= 30) return "warning";
  return "danger";
}

function accuracyCoverageTone(value: unknown): CapacityMetricTone {
  const number = optionalNumberValue(value);
  if (number === null) return "muted";
  if (number >= 85 && number <= 95) return "success";
  if (number >= 75 && number <= 98) return "warning";
  return "danger";
}

function accuracyBiasTone(value: unknown): CapacityMetricTone {
  const number = optionalNumberValue(value);
  if (number === null) return "muted";
  const absolute = Math.abs(number);
  if (absolute <= 10) return "success";
  if (absolute <= 25) return "warning";
  return "danger";
}

function usagePercentTone(value: unknown): CapacityMetricTone {
  const number = optionalNumberValue(value);
  if (number === null) return "muted";
  if (number >= 90) return "danger";
  if (number >= 75) return "warning";
  if (number >= 50) return "success";
  return "info";
}

function capacityAvailabilityTone(_usedValue: unknown, remainingValue: unknown): CapacityMetricTone {
  const remaining = optionalNumberValue(remainingValue);
  if (remaining !== null && remaining >= 100) return "excellent";
  if (remaining === null) return "muted";
  if (remaining < 10) return "danger";
  if (remaining < 25) return "warning";
  if (remaining < 50) return "success";
  return "info";
}

function remainingPercent(usedValue: unknown): number | null {
  const number = optionalNumberValue(usedValue);
  if (number === null) return null;
  return clampPercent(100 - number);
}

function availabilityPercent(remainingValue: unknown, capacityValue: unknown): number | null {
  const remaining = optionalNumberValue(remainingValue);
  const capacity = optionalNumberValue(capacityValue);
  if (remaining === null || capacity === null) return null;
  if (capacity <= 0) return 0;
  return clampPercent((remaining / capacity) * 100);
}

function multipleScalePercent(value: unknown): number | null {
  const number = optionalNumberValue(value);
  if (number === null) return null;
  return clampPercent((Math.max(0, number) / 5) * 100);
}

function multipleScaleTone(value: unknown): CapacityMetricTone {
  const number = optionalNumberValue(value);
  if (number === null) return "muted";
  if (number >= 5) return "excellent";
  if (number < 1) return "danger";
  if (number < 1.5) return "warning";
  if (number < 3) return "success";
  return "info";
}

function burstTrendMetricTone(trend?: string, strength?: string): CapacityMetricTone {
  if (!trend || trend === "unknown") return "muted";
  if (trend === "falling") return "success";
  if (trend === "flat") return "info";
  if (strength === "extreme" || strength === "strong") return "danger";
  return "warning";
}

function daysScalePercent(value: unknown): number | null {
  const number = optionalNumberValue(value);
  if (number === null) return null;
  return clampPercent((Math.max(0, number) / 10) * 100);
}

function daysScaleTone(value: unknown): CapacityMetricTone {
  const number = optionalNumberValue(value);
  if (number === null) return "muted";
  if (number >= 10) return "excellent";
  if (number < 1) return "danger";
  if (number < 3) return "warning";
  if (number < 5) return "success";
  return "info";
}

function usageValue(account: RemoteAccount, key: string): unknown {
  const extra = account.extra || {};
  return (account as unknown as Record<string, unknown>)[key] ?? extra[key];
}

function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, Math.round(value)));
}

function usageToneClass(usedPercent: number): string {
  if (usedPercent >= 90) return "usage-danger";
  if (usedPercent >= 75) return "usage-warning";
  if (usedPercent >= 50) return "usage-success";
  return "usage-calm";
}

function refreshResultMessage(result: RefreshResponse): string {
  if (typeof result.groups === "number" || typeof result.accounts === "number") {
    return `账号池数据同步完成：${numberValue(result.groups)} 个分组，${numberValue(result.accounts)} 个账号`;
  }
  return result.message || "账号池数据同步完成";
}

function accountCacheKey(siteId: string, groupId: number, page: number, pageSize: number, statusFilter: string): string {
  return `${siteId}:${groupId}:${page}:${pageSize}:${statusFilter || "all"}`;
}

function getSub2apiCacheVersion(): string {
  return localStorage.getItem("sub2apiCacheVersion") || "0";
}

function getApiPoolPageCache(): ApiPoolPageCache | null {
  if (!apiPoolPageCache) return null;
  return apiPoolPageCache.cacheVersion === getSub2apiCacheVersion() ? apiPoolPageCache : null;
}

function getCachedAccounts(siteId: string, groupId: number, page: number, pageSize: number, statusFilter: string): CachedAccounts | null {
  return getApiPoolPageCache()?.accountPages?.[accountCacheKey(siteId, groupId, page, pageSize, statusFilter)] || null;
}

function cacheAccounts(siteId: string, groupId: number, page: number, pageSize: number, statusFilter: string, value: CachedAccounts) {
  const currentCache = getApiPoolPageCache();
  const nextPages = {
    ...(currentCache?.accountPages || {}),
    [accountCacheKey(siteId, groupId, page, pageSize, statusFilter)]: value,
  };
  apiPoolPageCache = {
    ...(currentCache || {
      cacheVersion: getSub2apiCacheVersion(),
      sites: [],
      selectedSiteId: siteId,
      groups: [],
      selectedGroupId: groupId,
      accounts: [],
      accountsTotal: 0,
      accountsDataKey: "",
      accountPage: page,
      accountPageSize: pageSize,
      statusFilter,
      lastLoadedAt: value.lastLoadedAt,
      cachedAt: Date.now(),
    }),
    cacheVersion: getSub2apiCacheVersion(),
    accountPages: nextPages,
  };
}

function clearAccountCacheForSite(siteId: string) {
  const currentCache = getApiPoolPageCache();
  if (!currentCache?.accountPages) return;
  const nextPages = Object.fromEntries(
    Object.entries(currentCache.accountPages).filter(([key]) => !key.startsWith(`${siteId}:`)),
  );
  apiPoolPageCache = {
    ...currentCache,
    accountPages: nextPages,
  };
}

function numberValue(value: unknown): number {
  return optionalNumberValue(value) ?? 0;
}

function optionalNumberValue(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}
