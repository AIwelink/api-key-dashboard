import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { errorMessage, formatDateTime, text } from "../utils/format";

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

type CapacitySummary = {
  available_accounts?: number;
  available_5h_accounts?: number;
  account_type?: string;
  five_hour_capacity_usd?: number;
  twenty_four_hour_capacity_usd?: number;
  seven_day_capacity_usd?: number;
  five_hour_used_estimated_usd?: number;
  five_hour_remaining_estimated_usd?: number;
  seven_day_used_estimated_usd?: number;
  seven_day_remaining_estimated_usd?: number;
  five_hour_peak_cost?: number;
  seven_day_five_hour_peak_cost?: number;
  recent_day_five_hour_peak_cost?: number;
  seven_day_24h_peak_cost?: number;
  recent_5h_cost?: number;
  recent_24h_cost?: number;
  seven_day_cost?: number;
  recent_5h_remaining_usd?: number;
  recent_24h_remaining_usd?: number;
  seven_day_remaining_usd?: number;
  five_hour_peak_multiple?: number | null;
  recent_day_five_hour_peak_multiple?: number | null;
  recent_5h_multiple?: number | null;
  twenty_four_hour_peak_multiple?: number | null;
  recent_24h_multiple?: number | null;
  five_x_peak_multiple?: number | null;
  five_x_recent_day_peak_multiple?: number | null;
  five_x_24h_peak_multiple?: number | null;
  ten_x_peak_multiple?: number | null;
  current_speed_multiple?: number | null;
  current_speed_days?: number | null;
  five_x_speed_days?: number | null;
  recent_day_five_hour_peak_daily_cost?: number;
  seven_day_five_hour_peak_daily_cost?: number;
  recent_day_five_hour_peak_speed_days?: number | null;
  five_x_recent_day_five_hour_peak_speed_days?: number | null;
  seven_day_five_hour_peak_speed_days?: number | null;
  five_x_seven_day_five_hour_peak_speed_days?: number | null;
  seven_day_peak_speed_days?: number | null;
  five_x_peak_speed_days?: number | null;
  ten_x_speed_days?: number | null;
  health_status?: string;
  health_label?: string;
  health_tone?: "success" | "warning" | "danger" | "muted";
  health_reason?: string;
  used_5h_percent?: number;
  available_5h_percent?: number;
  used_7d_percent?: number;
  available_7d_percent?: number;
  total_accounts?: number;
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

type SiteForm = {
  id: string;
  name: string;
  base_url: string;
  token: string;
  status: string;
  refresh_interval_minutes: number;
};

const DEFAULT_ACCOUNT_PAGE_SIZE = 50;
const emptySiteForm: SiteForm = {
  id: "",
  name: "",
  base_url: "",
  token: "",
  status: "active",
  refresh_interval_minutes: 5,
};

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
  refreshIntervalMinutes: number;
  autoRemoveAbnormalAccounts: boolean;
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
  const [refreshIntervalMinutes, setRefreshIntervalMinutes] = useState(() => initialCache?.refreshIntervalMinutes || 5);
  const [autoRemoveAbnormalAccounts, setAutoRemoveAbnormalAccounts] = useState(() => initialCache?.autoRemoveAbnormalAccounts || false);
  const [savingSite, setSavingSite] = useState(false);
  const [savingAutoRemove, setSavingAutoRemove] = useState(false);
  const [editingSiteId, setEditingSiteId] = useState<string | null>(() => initialCache?.selectedSiteId || null);
  const [siteForm, setSiteForm] = useState<SiteForm>(emptySiteForm);
  const [refreshingRemote, setRefreshingRemote] = useState(false);
  const [refreshingFrontend, setRefreshingFrontend] = useState(false);
  const [remoteRefreshFeedback, setRemoteRefreshFeedback] = useState<InlineFeedback | null>(null);
  const [connectionResult, setConnectionResult] = useState<unknown>(null);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);

  const selectedSite = sites.find((site) => site.id === selectedSiteId) || null;
  const selectedGroup = groups.find((group) => group.id === selectedGroupId) || null;
  const currentAccountKey = useMemo(
    () => (selectedSiteId && selectedGroupId !== null ? accountCacheKey(selectedSiteId, selectedGroupId, accountPage, accountPageSize, statusFilter) : ""),
    [selectedSiteId, selectedGroupId, accountPage, accountPageSize, statusFilter],
  );
  const currentAccountKeyRef = useRef(currentAccountKey);
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
  const autoRemoveOverridesRef = useRef<Record<string, boolean>>({});

  useEffect(() => {
    currentAccountKeyRef.current = currentAccountKey;
  }, [currentAccountKey]);

  useEffect(() => {
    const pageIds = new Set(visibleAccounts.map((account) => account.id));
    setSelectedRemoteIds((current) => new Set([...current].filter((id) => pageIds.has(id))));
  }, [visibleAccounts]);

  const loadSites = async () => {
    const data = await api<SitesResponse>("/sub2api-sites", token);
    setSites(mergeSitesWithAutoRemoveOverrides(data.items, autoRemoveOverridesRef.current));
    if (!selectedSiteId && data.items[0]) {
      setSelectedSiteId(data.items[0].id);
    }
  };

  const loadGroups = async (siteId = selectedSiteId) => {
    if (!siteId) return [];
    setLoadingGroups(true);
    try {
      const data = await api<GroupsResponse>(`/sub2api-sites/${siteId}/groups?page=1&page_size=100`, token);
      setGroups(data.items);
      setLastLoadedAt(data.cache_meta?.last_refreshed_at || null);
      if (!selectedGroupId && data.items[0]) {
        setSelectedGroupId(data.items[0].id);
      }
      if (selectedGroupId && !data.items.some((group) => group.id === selectedGroupId)) {
        setSelectedGroupId(data.items[0]?.id ?? null);
        setAccountsDataKey("");
      }
      return data.items;
    } finally {
      setLoadingGroups(false);
    }
  };

  const loadAccounts = async (siteId = selectedSiteId, groupId = selectedGroupId, page = accountPage) => {
    if (!siteId || groupId === null) return;
    const requestKey = accountCacheKey(siteId, groupId, page, accountPageSize, statusFilter);
    setLoadingAccountsKey(requestKey);
    try {
      const params = new URLSearchParams({
        page: String(page),
        page_size: String(accountPageSize),
      });
      if (statusFilter) params.set("status", statusFilter);
      const data = await api<AccountsResponse>(`/sub2api-sites/${siteId}/groups/${groupId}/accounts?${params.toString()}`, token);
      cacheAccounts(siteId, groupId, page, accountPageSize, statusFilter, {
        items: data.items,
        total: data.total,
        capacitySummary: data.capacity_summary,
        lastLoadedAt: data.cache_meta?.last_refreshed_at || lastLoadedAt,
        cachedAt: Date.now(),
      });
      if (data.capacity_summary) {
        updateGroupCapacitySummary(groupId, data.capacity_summary);
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

  const performManualDeleteRemoteAccount = async (account: RemoteAccount, targetStatus: "available" | "library", label: string) => {
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
      clearAccountCacheForSite(selectedSiteId);
      const nextGroups = await loadGroups(selectedSiteId);
      const nextGroupId =
        selectedGroupId !== null && nextGroups.some((group) => group.id === selectedGroupId)
          ? selectedGroupId
          : nextGroups[0]?.id ?? null;
      if (nextGroupId !== null) {
        await loadAccounts(selectedSiteId, nextGroupId, accountPage);
      }
      window.dispatchEvent(new CustomEvent("sub2api-cache-updated"));
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setRemoteActionBusyId(null);
    }
  };

  const manualDeleteRemoteAccount = (account: RemoteAccount, targetStatus: "available" | "library") => {
    const label = targetStatus === "available" ? "手动删除并退回可用池" : "手动删除并退回总库";
    setConfirmState({
      title: "确认删除远端账号",
      message: "删除前会先把账号快照写入本地库，然后从 sub2api 删除远端账号。",
      details: [
        ["远端账号", text(account.name) || `#${account.id}`],
        ["远端 ID", account.id],
        ["处理方式", label],
      ],
      confirmText: "删除远端账号",
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
      clearAccountCacheForSite(selectedSiteId);
      await loadAccounts(selectedSiteId, selectedGroupId, accountPage);
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
    if (getApiPoolPageCache()) return;
    loadSites().catch((error) => showToast(errorMessage(error), true));
  }, []);

  useEffect(() => {
    if (!selectedSiteId) return;
    const site = sites.find((item) => item.id === selectedSiteId);
    if (!site) return;
    setEditingSiteId(site.id);
    setSiteForm(siteToForm(site));
    setRefreshIntervalMinutes(site?.refresh_interval_minutes || 5);
    setAutoRemoveAbnormalAccounts(autoRemoveOverridesRef.current[selectedSiteId] ?? site?.auto_remove_abnormal_accounts === true);
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
  }, [selectedSiteId, selectedGroupId, accountPage, accountPageSize, statusFilter]);

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
      refreshIntervalMinutes,
      autoRemoveAbnormalAccounts,
      cachedAt: Date.now(),
      accountPages: apiPoolPageCache?.accountPages || {},
    };
  }, [sites, selectedSiteId, groups, selectedGroupId, accounts, accountsTotal, accountsDataKey, accountPage, accountPageSize, statusFilter, lastLoadedAt, refreshIntervalMinutes, autoRemoveAbnormalAccounts]);

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
          const nextGroupId =
            selectedGroupId !== null && nextGroups.some((group) => group.id === selectedGroupId)
              ? selectedGroupId
              : nextGroups[0]?.id ?? null;
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

  useEffect(() => {
    if (!selectedSiteId) return;
    const intervalMs = Math.max(1, refreshIntervalMinutes || 5) * 60_000;
    const timer = window.setInterval(() => {
      loadGroups(selectedSiteId)
        .then((nextGroups) => {
          const nextGroupId = selectedGroupId ?? nextGroups[0]?.id ?? null;
          if (nextGroupId !== null) {
            return loadAccounts(selectedSiteId, nextGroupId, accountPage);
          }
          return undefined;
        })
        .catch((error) => showToast(errorMessage(error), true));
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [selectedSiteId, selectedGroupId, accountPage, accountPageSize, statusFilter, refreshIntervalMinutes]);

  const refreshAll = async () => {
    if (!selectedSiteId) return;
    setRefreshingRemote(true);
    setRemoteRefreshFeedback({ message: "正在同步 sub2api 账号池数据..." });
    try {
      const result = await api<RefreshResponse>(`/sub2api-sites/${selectedSiteId}/refresh`, token, { method: "POST" });
      clearAccountCacheForSite(selectedSiteId);
      const nextGroups = await loadGroups();
      const nextGroupId = selectedGroupId ?? nextGroups[0]?.id ?? null;
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
      const nextGroups = await loadGroups(selectedSiteId);
      const nextGroupId =
        selectedGroupId !== null && nextGroups.some((group) => group.id === selectedGroupId)
          ? selectedGroupId
          : nextGroups[0]?.id ?? null;
      if (nextGroupId !== null) {
        setSelectedGroupId(nextGroupId);
        await loadAccounts(selectedSiteId, nextGroupId, accountPage);
      } else {
        setAccounts([]);
        setAccountsTotal(0);
      }
      showToast("前端数据已刷新");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setRefreshingFrontend(false);
    }
  };

  const saveSiteConfig = async () => {
    if (!selectedSiteId) return;
    setSavingSite(true);
    try {
      const updated = await api<Site>(`/sub2api-sites/${selectedSiteId}`, token, {
        method: "PATCH",
        body: JSON.stringify({
          refresh_interval_minutes: refreshIntervalMinutes,
        }),
      });
      setSites((current) => current.map((site) => (site.id === updated.id ? { ...site, ...updated } : site)));
      showToast("站点刷新间隔已保存");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSavingSite(false);
    }
  };

  const startCreateSite = () => {
    setEditingSiteId(null);
    setSiteForm(emptySiteForm);
  };

  const saveSiteForm = async () => {
    const payload = {
      id: siteForm.id.trim(),
      name: siteForm.name.trim(),
      base_url: siteForm.base_url.trim(),
      status: siteForm.status,
      refresh_interval_minutes: siteForm.refresh_interval_minutes,
      ...(siteForm.token.trim() ? { token: siteForm.token.trim() } : {}),
    };
    if (!payload.id || !payload.base_url) {
      showToast("站点 ID 和 Base URL 必填", true);
      return;
    }
    setSavingSite(true);
    try {
      const saved = editingSiteId
        ? await api<Site>(`/sub2api-sites/${editingSiteId}`, token, {
            method: "PATCH",
            body: JSON.stringify(payload),
          })
        : await api<Site>("/sub2api-sites", token, {
            method: "POST",
            body: JSON.stringify(payload),
          });
      const data = await api<SitesResponse>("/sub2api-sites", token);
      setSites(mergeSitesWithAutoRemoveOverrides(data.items, autoRemoveOverridesRef.current));
      setSelectedSiteId(saved.id);
      setEditingSiteId(saved.id);
      setSiteForm(siteToForm(saved));
      setRefreshIntervalMinutes(saved.refresh_interval_minutes || 5);
      setAutoRemoveAbnormalAccounts(saved.auto_remove_abnormal_accounts === true);
      showToast("站点配置已保存");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSavingSite(false);
    }
  };

  const deleteCurrentSite = () => {
    if (!editingSiteId) return;
    setConfirmState({
      title: "确认删除站点",
      message: "删除后该站点不会再出现在切换列表中，历史缓存和本地账号记录不会被删除。",
      details: [
        ["站点", siteForm.name || editingSiteId],
        ["Base URL", siteForm.base_url],
      ],
      confirmText: "删除站点",
      tone: "danger",
      onConfirm: async () => {
        setSavingSite(true);
        try {
          await api<null>(`/sub2api-sites/${editingSiteId}`, token, { method: "DELETE" });
          const data = await api<SitesResponse>("/sub2api-sites", token);
          setSites(data.items);
          const nextSiteId = data.items[0]?.id || "";
          setSelectedSiteId(nextSiteId);
          setEditingSiteId(nextSiteId || null);
          setSiteForm(data.items[0] ? siteToForm(data.items[0]) : emptySiteForm);
          showToast("站点已删除");
        } catch (error) {
          showToast(errorMessage(error), true);
        } finally {
          setSavingSite(false);
        }
      },
    });
  };

  const toggleAutoRemoveAbnormal = async (nextValue: boolean) => {
    if (!selectedSiteId) return;
    const siteId = selectedSiteId;
    const previousValue = autoRemoveAbnormalAccounts;
    autoRemoveOverridesRef.current[siteId] = nextValue;
    setAutoRemoveAbnormalAccounts(nextValue);
    setSavingAutoRemove(true);
    try {
      const updated = await api<Site>(`/sub2api-sites/${siteId}`, token, {
        method: "PATCH",
        body: JSON.stringify({ auto_remove_abnormal_accounts: nextValue }),
      });
      if (updated.auto_remove_abnormal_accounts !== nextValue) {
        const verified = await api<SitesResponse>("/sub2api-sites", token);
        const verifiedSite = verified.items.find((site) => site.id === siteId);
        if (verifiedSite?.auto_remove_abnormal_accounts !== nextValue) {
          throw new Error("自动移除异常账号保存未生效，请重启后端后再试");
        }
        setSites(mergeSitesWithAutoRemoveOverrides(verified.items, autoRemoveOverridesRef.current));
      } else {
        setSites((current) => current.map((site) => (site.id === siteId ? { ...site, ...updated } : site)));
      }
      setAutoRemoveAbnormalAccounts(nextValue);
      if (nextValue && updated.auto_remove_refresh) {
        clearAccountCacheForSite(siteId);
        const nextGroups = await loadGroups(siteId);
        const nextGroupId =
          selectedGroupId !== null && nextGroups.some((group) => group.id === selectedGroupId)
            ? selectedGroupId
            : nextGroups[0]?.id ?? null;
        if (nextGroupId !== null) {
          setSelectedGroupId(nextGroupId);
          await loadAccounts(siteId, nextGroupId, accountPage);
        }
        if (updated.auto_remove_refresh.ok === false) {
          showToast(`自动移除异常账号已开启，但扫描失败：${updated.auto_remove_refresh.message || "请查看后端日志"}`, true);
        } else {
          showToast(`自动移除异常账号已开启，已扫描并移除 ${numberValue(updated.auto_remove_refresh.auto_removed_abnormal_accounts)} 个异常账号`);
        }
      } else {
        showToast(nextValue ? "自动移除异常账号已开启" : "自动移除异常账号已关闭");
      }
    } catch (error) {
      if (autoRemoveOverridesRef.current[siteId] === nextValue) {
        delete autoRemoveOverridesRef.current[siteId];
      }
      setAutoRemoveAbnormalAccounts(previousValue);
      showToast(errorMessage(error), true);
    } finally {
      setSavingAutoRemove(false);
    }
  };

  return (
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
            {sites.map((site) => (
              <option key={site.id} value={site.id}>
                {site.name}
              </option>
            ))}
          </select>
        </label>
        <div className="site-meta">
          <strong>{selectedSite?.base_url || "未配置"}</strong>
          <span>{selectedSite?.token_configured ? "密钥已配置" : "密钥未配置"}</span>
          <span>最后刷新：{lastLoadedAt ? formatDateTime(lastLoadedAt) : "-"}</span>
        </div>
        <div className="refresh-config">
          <label>
            <span className="field-label">
              <strong>自动刷新</strong>
            </span>
            <input
              min={1}
              max={1440}
              type="number"
              value={refreshIntervalMinutes}
              onChange={(event) => setRefreshIntervalMinutes(Number(event.target.value))}
            />
          </label>
          <span>分钟</span>
          <button className="ghost compact-button" type="button" onClick={saveSiteConfig} disabled={savingSite || !selectedSiteId}>
            保存
          </button>
        </div>
        <label className="switch-field">
          <input
            type="checkbox"
            checked={autoRemoveAbnormalAccounts}
            disabled={savingAutoRemove || !selectedSiteId}
            onChange={(event) => {
              void toggleAutoRemoveAbnormal(event.target.checked);
            }}
          />
          <span className="switch-track" aria-hidden="true">
            <span className="switch-thumb" />
          </span>
          <span className="switch-copy">
            <strong>自动移除异常账号</strong>
            <em>{savingAutoRemove ? "保存中" : autoRemoveAbnormalAccounts ? "已开启" : "已关闭"}</em>
          </span>
        </label>
        <div className="pool-toolbar-actions">
          <button className="ghost compact-button" type="button" onClick={testConnection} disabled={!selectedSiteId}>
            测试连接
          </button>
          <button className="compact-button" type="button" onClick={refreshAll} disabled={!selectedSiteId || refreshingRemote}>
            {refreshingRemote ? "同步中..." : "同步账号池数据"}
          </button>
          {remoteRefreshFeedback && (
            <span className={`refresh-feedback ${remoteRefreshFeedback.isError ? "danger" : ""}`}>
              {remoteRefreshFeedback.message}
            </span>
          )}
        </div>
      </section>

      <section className="panel site-config-panel">
        <div className="panel-header">
          <div>
            <h3>站点配置</h3>
          </div>
          <div className="button-row">
            <button className="ghost compact-button" type="button" onClick={startCreateSite}>
              新增站点
            </button>
            <button className="ghost compact-button danger-button" type="button" onClick={deleteCurrentSite} disabled={!editingSiteId || savingSite}>
              删除站点
            </button>
          </div>
        </div>
        <div className="site-config-grid">
          <label>
            <span className="field-label">
              <strong>站点 ID</strong>
            </span>
            <input
              value={siteForm.id}
              disabled={Boolean(editingSiteId)}
              onChange={(event) => setSiteForm((current) => ({ ...current, id: event.target.value }))}
              placeholder="api-5001"
            />
          </label>
          <label>
            <span className="field-label">
              <strong>显示名称</strong>
            </span>
            <input
              value={siteForm.name}
              onChange={(event) => setSiteForm((current) => ({ ...current, name: event.target.value }))}
              placeholder="sub2api 5001"
            />
          </label>
          <label className="span-2">
            <span className="field-label">
              <strong>Base URL</strong>
            </span>
            <input
              value={siteForm.base_url}
              onChange={(event) => setSiteForm((current) => ({ ...current, base_url: event.target.value }))}
              placeholder="http://216.167.70.204:5001"
            />
          </label>
          <label>
            <span className="field-label">
              <strong>API Key</strong>
            </span>
            <input
              value={siteForm.token}
              onChange={(event) => setSiteForm((current) => ({ ...current, token: event.target.value }))}
              placeholder={editingSiteId ? "留空不修改" : "x-api-key"}
              type="password"
            />
          </label>
          <label>
            <span className="field-label">
              <strong>状态</strong>
            </span>
            <select value={siteForm.status} onChange={(event) => setSiteForm((current) => ({ ...current, status: event.target.value }))}>
              <option value="active">active</option>
              <option value="disabled">disabled</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>刷新间隔</strong>
            </span>
            <input
              min={1}
              max={1440}
              type="number"
              value={siteForm.refresh_interval_minutes}
              onChange={(event) => setSiteForm((current) => ({ ...current, refresh_interval_minutes: Number(event.target.value) }))}
            />
          </label>
          <div className="site-config-actions">
            <button className="compact-button" type="button" onClick={saveSiteForm} disabled={savingSite}>
              {savingSite ? "保存中..." : editingSiteId ? "保存站点" : "创建站点"}
            </button>
          </div>
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
            {groups.map((group) => (
              <option key={group.id} value={group.id}>
                {group.name} · active {numberValue(group.active_account_count)} / {numberValue(group.account_count)} · 限流 {numberValue(group.rate_limited_account_count)}
              </option>
            ))}
          </select>
        </label>
      </section>

      <section className="panel account-pool-panel">
          <div className="panel-header">
            <div>
              <div className="account-pool-title-row">
                <h3>{selectedGroup?.name || "账号"}</h3>
                <button
                  className="ghost compact-button frontend-refresh-button"
                  type="button"
                  onClick={refreshFrontendData}
                  disabled={!selectedSiteId || refreshingFrontend}
                >
                  {refreshingFrontend ? "前端刷新中..." : "前端数据刷新"}
                </button>
              </div>
              <p>
                {selectedGroup
                  ? `id=${selectedGroup.id} · active ${numberValue(selectedGroup.active_account_count)} / ${numberValue(selectedGroup.account_count)}`
                  : "请选择一个 group"}
              </p>
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
              <span>账号池概览</span>
              <strong>{numberValue(selectedGroup?.active_account_count)}</strong>
              <em>active / {numberValue(selectedGroup?.account_count)}</em>
            </div>
            <div className="pool-health-grid">
              <MiniMetric label="总账号" value={numberValue(selectedGroup?.account_count) || visibleAccountsTotal} />
              <MiniMetric label="活跃" value={numberValue(selectedGroup?.active_account_count)} />
              <MiniMetric label="可用账号" value={numberValue(selectedGroup?.capacity_summary?.available_accounts)} />
              <MiniMetric label="5h可用" value={numberValue(selectedGroup?.capacity_summary?.available_5h_accounts)} />
              <MiniMetric label="限流中" value={numberValue(selectedGroup?.rate_limited_account_count) || accountSummary.rateLimited} />
              <MiniMetric label="异常" value={accountSummary.error} />
            </div>
            <p>当前只保留账号池判断真正需要的基础指标；后续会用历史 cost 数据替换为新的容量预估健康度。</p>
          </section>

          <CapacityRunwaySummary summary={selectedGroup?.capacity_summary} loading={capacitySummaryLoading} />

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
  onManualDelete: (targetStatus: "available" | "library") => void;
  onSelect: (checked: boolean) => void;
  onTest: () => void;
}) {
  const credentials = account.credentials || {};
  const extra = account.extra || {};
  const email = text(account.email) || text(credentials.email) || text(account.extra?.email) || text(account.name);
  const accountName = text(account.name) || email || `#${account.id}`;
  const planType = text(account.plan_type) || text(credentials.plan_type);
  const privacyMode = text(account.privacy_mode) || text(extra.privacy_mode);
  const credentialExpiresAt = account.credential_expires_at ?? account.expires_at ?? credentials.expires_at;
  const subscriptionExpiresAt = account.subscription_expires_at ?? credentials.subscription_expires_at;
  const statusView = accountStatusView(account);

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
        <span>{numberValue(account.current_concurrency)}</span>
        <span className="capacity-separator">/</span>
        <strong>{numberValue(account.concurrency)}</strong>
        <div className="cell-sub">load {numberValue(account.load_factor)}</div>
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
          <div className="cell-sub">
            测试 {remoteTestLabel(account.codex_remote_test_status)}
            {account.codex_remote_tested_at ? ` · ${formatOptionalDate(account.codex_remote_tested_at)}` : ""}
          </div>
        )}
      </td>
      <td>
        <StatusPill value={account.schedulable ? "可调度" : "不可调度"} tone={account.schedulable ? "success" : "warning"} />
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
        <strong>{usedPercent}%</strong>
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

function CapacityRunwaySummary({ summary, loading }: { summary?: CapacitySummary; loading: boolean }) {
  const tone = summary?.health_tone || "muted";
  const accountType = summary?.account_type ? displayPlan(summary.account_type) : "未知";
  const sevenDayFiveHourPeak = summary?.seven_day_five_hour_peak_cost ?? summary?.five_hour_peak_cost;
  const sevenDayFiveHourPeakMultiple = summary?.five_hour_peak_multiple;
  const recentDayFiveHourPeak = summary?.recent_day_five_hour_peak_cost;
  const recentDayFiveHourPeakMultiple = summary?.recent_day_five_hour_peak_multiple;
  const recent24hSpeedDays = summary?.current_speed_days;
  const fiveXRecent24hSpeedDays = summary?.five_x_speed_days ?? summary?.ten_x_speed_days;
  const sevenDayPeak24hSpeedDays = summary?.seven_day_peak_speed_days;
  const fiveXPeak24hSpeedDays = summary?.five_x_peak_speed_days;
  const fiveHourUsedPercent = summary?.used_5h_percent;
  const sevenDayUsedPercent = summary?.used_7d_percent;
  return (
    <section className={`capacity-runway-card ${tone}`}>
      <div className="capacity-runway-head">
        <div>
          <span>容量预估</span>
          <strong>{loading ? "加载中" : summary?.health_label || "暂无数据"}</strong>
          <em>{loading ? "正在读取账号池容量" : summary?.health_reason || "等待 dashboard cost 数据"}</em>
        </div>
        <div className="capacity-runway-type">{accountType} 池</div>
      </div>
      <div className="capacity-runway-grid">
        <CapacityMetric
          label="总容量：5h"
          value={formatUsd(summary?.five_hour_capacity_usd)}
          sub={`可用额度：当前已用 ${formatUsd(summary?.five_hour_used_estimated_usd)}，预估可用 ${formatUsd(summary?.five_hour_remaining_estimated_usd)}`}
          percent={fiveHourUsedPercent}
          tone={usagePercentTone(fiveHourUsedPercent)}
        />
        <CapacityMetric
          label="总容量：7d"
          value={formatUsd(summary?.seven_day_capacity_usd)}
          sub={`可用额度：当前已用 ${formatUsd(summary?.seven_day_used_estimated_usd)}，预估可用 ${formatUsd(summary?.seven_day_remaining_estimated_usd)}`}
          percent={sevenDayUsedPercent}
          tone={usagePercentTone(sevenDayUsedPercent)}
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
          label="预估天数：最近24h"
          value={formatDays(recent24hSpeedDays)}
          sub={`按最近24h消耗 ${formatUsd(summary?.recent_24h_cost)}`}
          percent={daysScalePercent(recent24hSpeedDays)}
          tone={daysScaleTone(recent24hSpeedDays)}
          showMeterHead
          secondary={{
            label: "5倍预估",
            value: formatDays(fiveXRecent24hSpeedDays),
            percent: daysScalePercent(fiveXRecent24hSpeedDays),
            tone: daysScaleTone(fiveXRecent24hSpeedDays),
          }}
        />
        <CapacityMetric
          label="预估天数：7天最高24h"
          value={formatDays(sevenDayPeak24hSpeedDays)}
          sub={`按7天最高24h消耗 ${formatUsd(summary?.seven_day_24h_peak_cost)}`}
          percent={daysScalePercent(sevenDayPeak24hSpeedDays)}
          tone={daysScaleTone(sevenDayPeak24hSpeedDays)}
          showMeterHead
          secondary={{
            label: "5倍预估",
            value: formatDays(fiveXPeak24hSpeedDays),
            percent: daysScalePercent(fiveXPeak24hSpeedDays),
            tone: daysScaleTone(fiveXPeak24hSpeedDays),
          }}
        />
      </div>
    </section>
  );
}

type CapacityMetricTone = "info" | "success" | "warning" | "danger" | "muted";

function CapacityMetric({
  label,
  value,
  sub,
  percent,
  tone = "muted",
  showMeterHead = false,
  secondary,
}: {
  label: string;
  value: string;
  sub: string;
  percent?: number | null;
  tone?: CapacityMetricTone;
  showMeterHead?: boolean;
  secondary?: {
    label: string;
    value: string;
    percent?: number | null;
    tone?: CapacityMetricTone;
  };
}) {
  const [labelMain, labelSuffix] = label.split("：");
  return (
    <div className="capacity-metric">
      <span className="capacity-metric-label">
        <b>{labelMain}</b>
        {labelSuffix ? <em>：{labelSuffix}</em> : null}
      </span>
      <strong>{value}</strong>
      <small>{sub}</small>
      {percent !== undefined && percent !== null && (
        <div className="capacity-primary-meter">
          {showMeterHead && (
            <div className="capacity-secondary-head">
              <span>{sub}</span>
              <strong>{value}</strong>
            </div>
          )}
          <div className="capacity-meter" aria-label={`${label} ${percent}%`}>
            <div className={`capacity-meter-fill ${tone}`} style={{ width: `${clampPercent(percent)}%` }} />
          </div>
        </div>
      )}
      {secondary && (
        <div className="capacity-secondary">
          <div className="capacity-secondary-head">
            <span>{secondary.label}</span>
            <strong>{secondary.value}</strong>
          </div>
          {secondary.percent !== undefined && secondary.percent !== null && (
            <div className="capacity-meter" aria-label={`${label} ${secondary.label} ${secondary.percent}%`}>
              <div className={`capacity-meter-fill ${secondary.tone || "muted"}`} style={{ width: `${clampPercent(secondary.percent)}%` }} />
            </div>
          )}
        </div>
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
          <strong>{value}</strong>
        </div>
      ))}
    </section>
  );
}

function MiniMetric({ label, value }: { label: string; value: number }) {
  return (
    <div className="mini-metric">
      <strong>{value}</strong>
      <span>{label}</span>
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
  if (!status) return "unknown";
  if (isTemporaryRateLimit(account)) return "warning";
  if (["error", "disabled", "paused", "banned", "invalid", "failed"].includes(status)) return "error";
  if (account.error_message) return "error";
  if (isFutureDate(account.temp_unschedulable_until) || isFutureDate(account.rate_limit_reset_at)) return "warning";
  if (status === "active" && account.schedulable === false) return "warning";
  if (status === "active" && account.schedulable === true) return "healthy";
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
  return hasActiveUntil || combined.includes("rate limit") || combined.includes("限流");
}

function isFutureDate(value: unknown): boolean {
  if (!value) return false;
  const date = typeof value === "number"
    ? new Date(value > 10_000_000_000 ? value : value * 1000)
    : new Date(String(value));
  return Number.isFinite(date.getTime()) && date.getTime() > Date.now();
}

function accountStatusView(account: RemoteAccount): { label: string; tone: "accent" | "success" | "warning" | "danger" | "muted"; detail?: string } {
  const status = (account.status || "").toLowerCase();
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
  if (status === "active" && account.schedulable === false) {
    return { label: "不可调度", tone: "warning", detail: "schedulable=false" };
  }
  if (account.error_message) {
    return { label: "异常", tone: "danger" };
  }
  return { label: displayStatus(account.status), tone: statusTone(account.status) };
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
  if (["team", "team_sub", "team-sub", "team_child", "team子号", "team 子号"].includes(normalized)) return "Team子号";
  if (normalized === "plus") return "Plus";
  if (normalized === "pro") return "Pro";
  if (normalized === "free") return "Free";
  return value;
}

function planTagTone(value: string): string {
  const normalized = value.toLowerCase();
  if (["team", "team_sub", "team-sub", "team_child", "team子号", "team 子号"].includes(normalized)) return "plan-team";
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
  if (typeof value === "number" && Number.isFinite(value)) {
    const milliseconds = value > 10_000_000_000 ? value : value * 1000;
    return formatDateTime(new Date(milliseconds).toISOString()) || "从未";
  }
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

function formatDays(value: unknown): string {
  const number = optionalNumberValue(value);
  if (number === null) return "-";
  if (number >= 10) return `${number.toFixed(0)}天`;
  if (number >= 1) return `${number.toFixed(1)}天`;
  return `${Math.max(0, number * 24).toFixed(1)}小时`;
}

function usagePercentTone(value: unknown): "info" | "success" | "warning" | "danger" | "muted" {
  const number = optionalNumberValue(value);
  if (number === null) return "muted";
  if (number >= 90) return "danger";
  if (number >= 75) return "warning";
  if (number >= 50) return "success";
  return "info";
}

function multipleScalePercent(value: unknown): number | null {
  const number = optionalNumberValue(value);
  if (number === null) return null;
  return clampPercent((Math.max(0, number) / 10) * 100);
}

function multipleScaleTone(value: unknown): "info" | "success" | "warning" | "danger" | "muted" {
  const number = optionalNumberValue(value);
  if (number === null) return "muted";
  if (number < 1) return "danger";
  if (number < 5) return "warning";
  if (number < 10) return "success";
  return "info";
}

function daysScalePercent(value: unknown): number | null {
  const number = optionalNumberValue(value);
  if (number === null) return null;
  return clampPercent((Math.max(0, number) / 10) * 100);
}

function daysScaleTone(value: unknown): "info" | "success" | "warning" | "danger" | "muted" {
  const number = optionalNumberValue(value);
  if (number === null) return "muted";
  if (number < 1) return "danger";
  if (number < 5) return "warning";
  if (number < 10) return "success";
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

function siteToForm(site: Site): SiteForm {
  return {
    id: site.id,
    name: site.name || site.id,
    base_url: site.base_url || "",
    token: "",
    status: site.status || "active",
    refresh_interval_minutes: site.refresh_interval_minutes || 5,
  };
}

function mergeSitesWithAutoRemoveOverrides(sites: Site[], overrides: Record<string, boolean>): Site[] {
  return sites.map((site) => {
    const override = overrides[site.id];
    if (override === undefined) return site;
    if (site.auto_remove_abnormal_accounts === override) {
      delete overrides[site.id];
      return site;
    }
    return { ...site, auto_remove_abnormal_accounts: override };
  });
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
      refreshIntervalMinutes: 5,
      autoRemoveAbnormalAccounts: false,
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
