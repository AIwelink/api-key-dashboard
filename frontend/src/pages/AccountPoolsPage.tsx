import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import type { ApiPool } from "../types";
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
  refresh_interval_minutes?: number;
  auto_remove_abnormal_accounts?: boolean;
};

type Group = {
  id: number;
  name: string;
  platform?: string;
  status?: string;
  account_count?: number;
  active_account_count?: number;
  rate_limited_account_count?: number;
  subscription_type?: string;
};

type SitesResponse = {
  items: Site[];
  total: number;
};

type GroupsResponse = {
  items: Group[];
  total: number;
  cache_meta?: {
    last_refreshed_at?: string;
  };
};

type AccountsResponse = {
  items: unknown[];
  total: number;
};

type ApiPoolResponse = {
  items: ApiPool[];
  total: number;
};

type CapacityLimitKey = "free" | "plus" | "team" | "bug_team" | "k12" | "pro";

type CapacityLimitForm = Record<CapacityLimitKey, { five_hour_usd: string; seven_day_usd: string }>;

type CapacityLimitsResponse = {
  site_id?: string | null;
  limits: Record<CapacityLimitKey, { five_hour_usd: number; seven_day_usd: number }>;
  inherited_from_global?: boolean;
  updated_at?: string | null;
  updated_by_name?: string | null;
};

type GroupObservabilitySetting = {
  id?: string;
  site_id: string;
  group_id: number;
  group_name?: string;
  enabled: boolean;
  detailed_enabled: boolean;
  probe_interval_seconds?: number;
  sample_retention_days?: number;
  record_usage_samples?: boolean;
  record_status_events?: boolean;
  record_duplicate_email_warning?: boolean;
  group_account_count?: number;
  group_active_account_count?: number;
  updated_at?: string;
};

type GroupObservabilityResponse = {
  items: GroupObservabilitySetting[];
  total: number;
};

type ProbeResponse = {
  accounts_seen?: number;
  accounts_changed?: number;
  accounts_401?: number;
  duplicate_email_count?: number;
  accounts_removed_confirmed?: number;
  message?: string;
};

type RefreshResponse = {
  groups?: number;
  accounts?: number;
  message?: string;
};

type SiteForm = {
  id: string;
  name: string;
  base_url: string;
  token: string;
  status: string;
  refresh_interval_minutes: number;
  auto_remove_abnormal_accounts: boolean;
};

type ConfirmState = {
  confirmText?: string;
  details?: Array<[string, string | number | null | undefined]>;
  message?: string;
  onConfirm: () => void;
  title: string;
  tone?: "default" | "danger";
};

const emptySiteForm: SiteForm = {
  id: "",
  name: "",
  base_url: "",
  token: "",
  status: "active",
  refresh_interval_minutes: 30,
  auto_remove_abnormal_accounts: false,
};

const capacityLimitLabels: Record<CapacityLimitKey, string> = {
  free: "free",
  plus: "plus",
  team: "team 子号",
  bug_team: "bug team",
  k12: "k12",
  pro: "pro 20x",
};

const defaultCapacityLimitForm: CapacityLimitForm = {
  free: { five_hour_usd: "2", seven_day_usd: "10" },
  plus: { five_hour_usd: "28", seven_day_usd: "140" },
  team: { five_hour_usd: "15", seven_day_usd: "75" },
  bug_team: { five_hour_usd: "230", seven_day_usd: "230" },
  k12: { five_hour_usd: "20", seven_day_usd: "100" },
  pro: { five_hour_usd: "360", seven_day_usd: "2100" },
};

export function AccountPoolsPage({ token, showToast }: Props) {
  const [sites, setSites] = useState<Site[]>([]);
  const [selectedSiteId, setSelectedSiteId] = useState("");
  const [groups, setGroups] = useState<Group[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null);
  const [reserveTotal, setReserveTotal] = useState(0);
  const [reserveSummary, setReserveSummary] = useState({ plus: 0, team: 0, k12: 0, free: 0, pro: 0, phoneBound: 0, problem: 0 });
  const [localPools, setLocalPools] = useState<ApiPool[]>([]);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingReserveSummary, setLoadingReserveSummary] = useState(false);
  const [editingSiteId, setEditingSiteId] = useState<string | null>(null);
  const [siteForm, setSiteForm] = useState<SiteForm>(emptySiteForm);
  const [savingSite, setSavingSite] = useState(false);
  const [capacityLimits, setCapacityLimits] = useState<CapacityLimitForm>(defaultCapacityLimitForm);
  const [capacityLimitsMeta, setCapacityLimitsMeta] = useState<{ updated_at?: string | null; updated_by_name?: string | null; inherited_from_global?: boolean }>({});
  const [loadingCapacityLimits, setLoadingCapacityLimits] = useState(false);
  const [savingCapacityLimits, setSavingCapacityLimits] = useState(false);
  const capacityLimitsRequestRef = useRef(0);
  const [observabilitySettings, setObservabilitySettings] = useState<GroupObservabilitySetting[]>([]);
  const [savingObservabilityKey, setSavingObservabilityKey] = useState<string | null>(null);
  const [probing, setProbing] = useState(false);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);

  const selectedSite = sites.find((site) => site.id === selectedSiteId) || null;
  const selectedGroup = groups.find((group) => group.id === selectedGroupId) || null;
  const groupSummary = useMemo(() => summarizeGroups(groups), [groups]);

  const loadSites = async () => {
    const data = await api<SitesResponse>("/sub2api-sites", token);
    setSites(data.items);
    if (!selectedSiteId && data.items[0]) {
      setSelectedSiteId(data.items[0].id);
    }
  };

  const startCreateSite = () => {
    setSelectedSiteId("");
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
      auto_remove_abnormal_accounts: siteForm.auto_remove_abnormal_accounts,
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
      setSites(data.items);
      setSelectedSiteId(saved.id);
      setEditingSiteId(saved.id);
      setSiteForm(siteToForm(saved));
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
          const nextSite = data.items[0] || null;
          setSelectedSiteId(nextSite?.id || "");
          setEditingSiteId(nextSite?.id || null);
          setSiteForm(nextSite ? siteToForm(nextSite) : emptySiteForm);
          showToast("站点已删除");
        } catch (error) {
          showToast(errorMessage(error), true);
        } finally {
          setSavingSite(false);
          setConfirmState(null);
        }
      },
    });
  };

  const loadGroups = async (siteId = selectedSiteId) => {
    if (!siteId) return [];
    const data = await api<GroupsResponse>(`/sub2api-sites/${siteId}/groups?page=1&page_size=500`, token);
    setGroups(data.items);
    setLastRefreshedAt(data.cache_meta?.last_refreshed_at || null);
    if (!selectedGroupId || !data.items.some((group) => group.id === selectedGroupId)) {
      setSelectedGroupId(data.items[0]?.id ?? null);
    }
    return data.items;
  };

  const loadReserveSummary = async () => {
    setLoadingReserveSummary(true);
    try {
      const targetParams = new URLSearchParams({ pool_status: "reserve", limit: "1" });
      if (selectedSiteId) targetParams.set("site_id", selectedSiteId);
      if (selectedGroupId !== null) targetParams.set("pool_id", String(selectedGroupId));
      const withFilter = (key?: string, value?: string) => {
        const params = new URLSearchParams(targetParams);
        if (key && value) params.set(key, value);
        return `/accounts?${params.toString()}`;
      };
      const [allData, plusData, teamData, k12Data, freeData, proData, phoneData] = await Promise.all([
        api<AccountsResponse>(withFilter(), token),
        api<AccountsResponse>(withFilter("account_type", "plus"), token),
        api<AccountsResponse>(withFilter("account_type", "team"), token),
        api<AccountsResponse>(withFilter("account_type", "k12"), token),
        api<AccountsResponse>(withFilter("account_type", "free"), token),
        api<AccountsResponse>(withFilter("account_type", "pro"), token),
        api<AccountsResponse>(withFilter("phone_bound", "true"), token),
      ]);
      setReserveTotal(allData.total);
      setReserveSummary({
        plus: plusData.total,
        team: teamData.total,
        k12: k12Data.total,
        free: freeData.total,
        pro: proData.total,
        phoneBound: phoneData.total,
        problem: 0,
      });
    } finally {
      setLoadingReserveSummary(false);
    }
  };

  const loadLocalPools = async () => {
    const data = await api<ApiPoolResponse>("/api-pools", token);
    setLocalPools(data.items);
  };

  const loadCapacityLimits = async (siteId = selectedSiteId) => {
    const requestId = ++capacityLimitsRequestRef.current;
    setCapacityLimits(capacityLimitsToForm({} as CapacityLimitsResponse["limits"]));
    setCapacityLimitsMeta({});
    if (!siteId) {
      setLoadingCapacityLimits(false);
      return;
    }
    setLoadingCapacityLimits(true);
    try {
      const data = await api<CapacityLimitsResponse>(`/api-pools/capacity-limits?site_id=${encodeURIComponent(siteId)}`, token);
      if (requestId !== capacityLimitsRequestRef.current) return;
      setCapacityLimits(capacityLimitsToForm(data.limits));
      setCapacityLimitsMeta({
        updated_at: data.updated_at,
        updated_by_name: data.updated_by_name,
        inherited_from_global: data.inherited_from_global,
      });
    } finally {
      if (requestId === capacityLimitsRequestRef.current) setLoadingCapacityLimits(false);
    }
  };

  const loadObservabilitySettings = async (siteId = selectedSiteId) => {
    if (!siteId) return;
    const data = await api<GroupObservabilityResponse>(`/api-pools/observability/groups?site_id=${encodeURIComponent(siteId)}`, token);
    setObservabilitySettings(data.items);
  };

  const saveObservabilitySetting = async (setting: GroupObservabilitySetting, updates: Partial<GroupObservabilitySetting>) => {
    if (!selectedSiteId) return;
    const key = `${selectedSiteId}:${setting.group_id}`;
    setSavingObservabilityKey(key);
    try {
      const updated = await api<GroupObservabilitySetting>(`/api-pools/observability/groups/${setting.group_id}?site_id=${encodeURIComponent(selectedSiteId)}`, token, {
        method: "PATCH",
        body: JSON.stringify(updates),
      });
      setObservabilitySettings((current) => current.map((item) => (item.group_id === updated.group_id ? updated : item)));
      showToast("探测配置已保存");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSavingObservabilityKey(null);
    }
  };

  const runAccountProbe = async () => {
    if (!selectedSiteId) return;
    setProbing(true);
    try {
      const result = await api<ProbeResponse>(`/api-pools/observability/probe?site_id=${encodeURIComponent(selectedSiteId)}`, token, { method: "POST" });
      showToast(
        `探测完成：${result.accounts_seen || 0} 个账号，变化 ${result.accounts_changed || 0}，401 ${result.accounts_401 || 0}，重复邮箱 ${result.duplicate_email_count || 0}`,
      );
      await loadObservabilitySettings(selectedSiteId);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setProbing(false);
    }
  };

  const saveCapacityLimits = async () => {
    if (!selectedSiteId) {
      showToast("请先选择站点", true);
      return;
    }
    const payload = capacityLimitPayload(capacityLimits);
    if (!payload) {
      showToast("额度估计必须是大于等于 0 的数字", true);
      return;
    }
    setSavingCapacityLimits(true);
    try {
      const data = await api<CapacityLimitsResponse>(`/api-pools/capacity-limits?site_id=${encodeURIComponent(selectedSiteId)}`, token, {
        method: "PATCH",
        body: JSON.stringify({ limits: payload }),
      });
      setCapacityLimits(capacityLimitsToForm(data.limits));
      setCapacityLimitsMeta({ updated_at: data.updated_at, updated_by_name: data.updated_by_name, inherited_from_global: false });
      showToast(`${selectedSite?.name || selectedSiteId} 的账号额度估计已保存`);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSavingCapacityLimits(false);
    }
  };

  const refreshRemoteCache = async () => {
    if (!selectedSiteId) return;
    setRefreshing(true);
    try {
      const result = await api<RefreshResponse>(`/sub2api-sites/${selectedSiteId}/refresh`, token, { method: "POST" });
      const nextGroups = await loadGroups(selectedSiteId);
      const nextGroupId =
        selectedGroupId !== null && nextGroups.some((group) => group.id === selectedGroupId)
          ? selectedGroupId
          : nextGroups[0]?.id ?? null;
      if (nextGroupId !== null) {
        setSelectedGroupId(nextGroupId);
      }
      showToast(
        typeof result.groups === "number" || typeof result.accounts === "number"
          ? `同步完成：${result.groups || 0} 个分组，${result.accounts || 0} 个账号`
          : result.message || "同步完成",
      );
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => {
    Promise.all([loadSites(), loadLocalPools(), loadReserveSummary()]).catch((error) => showToast(errorMessage(error), true));
  }, []);

  useEffect(() => {
    const handleCacheUpdated = () => {
      if (!selectedSiteId) {
        loadSites().catch((error) => showToast(errorMessage(error), true));
        return;
      }
      loadGroups(selectedSiteId).catch((error) => showToast(errorMessage(error), true));
    };
    window.addEventListener("sub2api-cache-updated", handleCacheUpdated);
    return () => window.removeEventListener("sub2api-cache-updated", handleCacheUpdated);
  }, [selectedSiteId, selectedGroupId]);

  useEffect(() => {
    if (!selectedSiteId) {
      setGroups([]);
      setSelectedGroupId(null);
      setObservabilitySettings([]);
      loadCapacityLimits("").catch((error) => showToast(errorMessage(error), true));
      return;
    }
    const site = sites.find((item) => item.id === selectedSiteId);
    if (site) {
      setEditingSiteId(site.id);
      setSiteForm(siteToForm(site));
    }
    setGroups([]);
    setSelectedGroupId(null);
    loadGroups(selectedSiteId).catch((error) => showToast(errorMessage(error), true));
    loadObservabilitySettings(selectedSiteId).catch((error) => showToast(errorMessage(error), true));
    loadCapacityLimits(selectedSiteId).catch((error) => showToast(errorMessage(error), true));
  }, [selectedSiteId, sites]);

  useEffect(() => {
    loadReserveSummary().catch((error) => showToast(errorMessage(error), true));
  }, [selectedSiteId, selectedGroupId]);

  return (
    <section className="view accounts-page">
      <div className="topbar">
        <div>
          <h2>账号池管理</h2>
          <p>管理 sub2api 站点配置、目标分组、本地备选池和后续账号池策略。</p>
        </div>
        <div className="button-row">
          <button className="ghost" type="button" onClick={() => loadGroups().catch((error) => showToast(errorMessage(error), true))}>
            刷新缓存
          </button>
          <button type="button" onClick={refreshRemoteCache} disabled={!selectedSiteId || refreshing}>
            {refreshing ? "同步中..." : "同步 sub2api 账号池数据"}
          </button>
        </div>
      </div>

      <section className="panel site-config-panel">
        <div className="panel-header">
          <div>
            <h3>站点配置</h3>
            <p>配置 sub2api 站点、API Key、刷新间隔和异常账号自动处理。</p>
          </div>
          <div className="button-row">
            <button className="compact-button" type="button" onClick={saveSiteForm} disabled={savingSite}>
              {savingSite ? "保存中..." : editingSiteId ? "保存站点" : "创建站点"}
            </button>
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
              <strong>已有站点</strong>
            </span>
            <select value={editingSiteId || ""} onChange={(event) => event.target.value && setSelectedSiteId(event.target.value)}>
              <option value="">选择已有站点</option>
              {sites.map((site) => (
                <option key={site.id} value={site.id}>
                  {site.name || site.id}
                </option>
              ))}
            </select>
          </label>
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
            <input value={siteForm.name} onChange={(event) => setSiteForm((current) => ({ ...current, name: event.target.value }))} placeholder="sub2api 5001" />
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
              min={30}
              max={1440}
              type="number"
              value={siteForm.refresh_interval_minutes}
              onChange={(event) => setSiteForm((current) => ({ ...current, refresh_interval_minutes: Number(event.target.value) }))}
            />
          </label>
          <label className="switch-field site-config-switch">
            <input
              type="checkbox"
              checked={siteForm.auto_remove_abnormal_accounts}
              onChange={(event) => setSiteForm((current) => ({ ...current, auto_remove_abnormal_accounts: event.target.checked }))}
            />
            <span className="switch-track" aria-hidden="true">
              <span className="switch-thumb" />
            </span>
            <span className="switch-copy">
              <strong>自动移除异常账号</strong>
              <em>{siteForm.auto_remove_abnormal_accounts ? "已开启" : "已关闭"}</em>
            </span>
          </label>
        </div>
      </section>

      <section className="panel capacity-limit-config-panel">
        <div className="panel-header">
          <div>
            <h3>账号额度估计</h3>
            <p>配置当前站点每种账号参与容量预估时使用的 5h 和 7d 美金额度；API 账号池状态页和自动补号会读取这里的配置。</p>
          </div>
          <div className="button-row">
            <button className="compact-button" type="button" onClick={saveCapacityLimits} disabled={!selectedSiteId || loadingCapacityLimits || savingCapacityLimits}>
              {savingCapacityLimits ? "保存中..." : loadingCapacityLimits ? "读取中..." : "保存额度估计"}
            </button>
          </div>
        </div>
        <div className="capacity-limit-grid">
          {(Object.keys(capacityLimitLabels) as CapacityLimitKey[]).map((accountType) => (
            <div className="capacity-limit-row" key={accountType}>
              <strong>{capacityLimitLabels[accountType]}</strong>
              <label>
                <span>5h</span>
                <input
                  min={0}
                  step="0.01"
                  type="number"
                  disabled={!selectedSiteId || loadingCapacityLimits}
                  value={capacityLimits[accountType].five_hour_usd}
                  onChange={(event) =>
                    setCapacityLimits((current) => ({
                      ...current,
                      [accountType]: { ...current[accountType], five_hour_usd: event.target.value },
                    }))
                  }
                />
              </label>
              <label>
                <span>7d</span>
                <input
                  min={0}
                  step="0.01"
                  type="number"
                  disabled={!selectedSiteId || loadingCapacityLimits}
                  value={capacityLimits[accountType].seven_day_usd}
                  onChange={(event) =>
                    setCapacityLimits((current) => ({
                      ...current,
                      [accountType]: { ...current[accountType], seven_day_usd: event.target.value },
                    }))
                  }
                />
              </label>
            </div>
          ))}
        </div>
        <div className="cell-sub">
          {capacityLimitsMeta.updated_at
            ? `${capacityLimitsMeta.inherited_from_global ? "继承旧全局配置" : `当前站点：${selectedSite?.name || selectedSiteId}`} · 最后保存：${formatDateTime(capacityLimitsMeta.updated_at)}${capacityLimitsMeta.updated_by_name ? ` · ${capacityLimitsMeta.updated_by_name}` : ""}`
            : selectedSiteId
              ? loadingCapacityLimits
                ? `正在读取 ${selectedSite?.name || selectedSiteId} 的额度配置`
                : `当前站点：${selectedSite?.name || selectedSiteId} · 使用默认额度估计`
              : "请先选择站点"}
        </div>
      </section>

      <section className="panel observability-config-panel">
        <div className="panel-header">
          <div>
            <h3>账号探测配置</h3>
            <p>按分组配置轻量探测间隔；详细记录用于后续 Agent 分析账号寿命、401、恢复和重复邮箱。</p>
          </div>
          <div className="button-row">
            <button className="ghost compact-button" type="button" onClick={() => loadObservabilitySettings().catch((error) => showToast(errorMessage(error), true))} disabled={!selectedSiteId}>
              刷新配置
            </button>
            <button className="compact-button" type="button" onClick={runAccountProbe} disabled={!selectedSiteId || probing}>
              {probing ? "探测中..." : "立即探测"}
            </button>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>分组</th>
                <th>账号</th>
                <th>探测</th>
                <th>间隔</th>
                <th>详细记录</th>
                <th>样本保留</th>
                <th>记录内容</th>
                <th>最后更新</th>
              </tr>
            </thead>
            <tbody>
              {observabilitySettings.map((setting) => {
                const rowKey = `${setting.site_id}:${setting.group_id}`;
                const busy = savingObservabilityKey === rowKey;
                return (
                  <tr key={rowKey}>
                    <td>
                      <div className="cell-main">{setting.group_name || `#${setting.group_id}`}</div>
                      <div className="cell-sub">#{setting.group_id}</div>
                    </td>
                    <td>
                      {numberValue(setting.group_active_account_count)} / {numberValue(setting.group_account_count)}
                    </td>
                    <td>
                      <label className="inline-check">
                        <input
                          checked={setting.enabled !== false}
                          disabled={busy}
                          type="checkbox"
                          onChange={(event) => saveObservabilitySetting(setting, { enabled: event.target.checked })}
                        />
                        <span>{setting.enabled !== false ? "开启" : "关闭"}</span>
                      </label>
                    </td>
                    <td>
                      <input
                        className="compact-number-input"
                        disabled={busy || setting.enabled === false}
                        min={1}
                        max={60}
                        type="number"
                        defaultValue={secondsToMinutes(setting.probe_interval_seconds || 180)}
                        onBlur={(event) => {
                          const minutes = clampInt(event.target.value, 1, 60, secondsToMinutes(setting.probe_interval_seconds || 180));
                          saveObservabilitySetting(setting, { probe_interval_seconds: minutes * 60 });
                        }}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") event.currentTarget.blur();
                        }}
                      />
                      <span className="cell-sub">分钟</span>
                    </td>
                    <td>
                      <label className="inline-check">
                        <input
                          checked={setting.detailed_enabled !== false}
                          disabled={busy || setting.enabled === false}
                          type="checkbox"
                          onChange={(event) => saveObservabilitySetting(setting, { detailed_enabled: event.target.checked })}
                        />
                        <span>{setting.detailed_enabled !== false ? "开启" : "关闭"}</span>
                      </label>
                    </td>
                    <td>
                      <input
                        className="compact-number-input"
                        disabled={busy || setting.detailed_enabled === false}
                        min={1}
                        max={90}
                        type="number"
                        defaultValue={setting.sample_retention_days || 14}
                        onBlur={(event) => saveObservabilitySetting(setting, { sample_retention_days: clampInt(event.target.value, 1, 90, setting.sample_retention_days || 14) })}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") event.currentTarget.blur();
                        }}
                      />
                      <span className="cell-sub">天</span>
                    </td>
                    <td>
                      <div className="inline-check-stack">
                        <label className="inline-check">
                          <input
                            checked={setting.record_status_events !== false}
                            disabled={busy || setting.enabled === false}
                            type="checkbox"
                            onChange={(event) => saveObservabilitySetting(setting, { record_status_events: event.target.checked })}
                          />
                          <span>事件</span>
                        </label>
                        <label className="inline-check">
                          <input
                            checked={setting.record_usage_samples !== false}
                            disabled={busy || setting.detailed_enabled === false}
                            type="checkbox"
                            onChange={(event) => saveObservabilitySetting(setting, { record_usage_samples: event.target.checked })}
                          />
                          <span>样本</span>
                        </label>
                        <label className="inline-check">
                          <input
                            checked={setting.record_duplicate_email_warning !== false}
                            disabled={busy || setting.enabled === false}
                            type="checkbox"
                            onChange={(event) => saveObservabilitySetting(setting, { record_duplicate_email_warning: event.target.checked })}
                          />
                          <span>重复邮箱</span>
                        </label>
                      </div>
                    </td>
                    <td>{formatDateTime(setting.updated_at)}</td>
                  </tr>
                );
              })}
              {!observabilitySettings.length && (
                <tr>
                  <td className="muted" colSpan={8}>
                    暂无分组探测配置，请先同步 sub2api 分组。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="compact-stats">
        <div className="compact-stat">
          <span>sub2api 分组</span>
          <strong>{groups.length}</strong>
        </div>
        <div className="compact-stat">
          <span>总账号</span>
          <strong>{groupSummary.totalAccounts}</strong>
        </div>
        <div className="compact-stat">
          <span>活跃账号</span>
          <strong>{groupSummary.activeAccounts}</strong>
        </div>
        <div className="compact-stat">
          <span>本地备选账号</span>
          <strong>{reserveTotal}</strong>
        </div>
      </section>

      <section className="grid two">
        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>sub2api 目标分组</h3>
              <p>这些分组来自统一的 sub2api 账号池缓存，当前只作为后续推送目标分组。</p>
            </div>
            <label className="inline-select">
              <span>站点</span>
              <select value={selectedSiteId} onChange={(event) => setSelectedSiteId(event.target.value)}>
                {sites.map((site) => (
                  <option key={site.id} value={site.id}>
                    {site.name}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <div className="site-meta logic-site-meta">
            <strong>{selectedSite?.base_url || "未配置"}</strong>
            <span>{selectedSite?.token_configured ? "密钥已配置" : "密钥未配置"}</span>
            <span>最后同步：{lastRefreshedAt ? formatDateTime(lastRefreshedAt) : "-"}</span>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>分组</th>
                  <th>平台</th>
                  <th>账号</th>
                  <th>限流</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {groups.map((group) => (
                  <tr
                    className={selectedGroupId === group.id ? "selected-row" : ""}
                    key={group.id}
                    onClick={() => setSelectedGroupId(group.id)}
                  >
                    <td>
                      <div className="cell-main">{group.name}</div>
                      <div className="cell-sub">#{group.id}</div>
                    </td>
                    <td>{displayValue(group.platform)}</td>
                    <td>
                      {numberValue(group.active_account_count)} / {numberValue(group.account_count)}
                    </td>
                    <td>{numberValue(group.rate_limited_account_count)}</td>
                    <td>
                      <StatusPill value={displayValue(group.status)} tone={group.status === "active" ? "success" : "muted"} />
                    </td>
                  </tr>
                ))}
                {!groups.length && (
                  <tr>
                    <td className="muted" colSpan={5}>
                      暂无 sub2api 分组缓存，请先同步。
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>当前备选账号池</h3>
              <p>从可用池手动加入的本地 reserve 账号；目标分组：{selectedGroup ? `${selectedGroup.name} · #${selectedGroup.id}` : "未选择"}</p>
            </div>
            <button className="ghost compact-button" type="button" onClick={() => loadReserveSummary().catch((error) => showToast(errorMessage(error), true))}>
              刷新参数
            </button>
          </div>

          <section className="compact-stats logic-account-stats">
            <div className="compact-stat">
              <span>备选总数</span>
              <strong>{loadingReserveSummary ? "..." : reserveTotal}</strong>
            </div>
            <div className="compact-stat">
              <span>Plus</span>
              <strong>{reserveSummary.plus}</strong>
            </div>
            <div className="compact-stat">
              <span>Team子号</span>
              <strong>{reserveSummary.team}</strong>
            </div>
            <div className="compact-stat">
              <span>K12</span>
              <strong>{reserveSummary.k12}</strong>
            </div>
            <div className="compact-stat">
              <span>已绑手机</span>
              <strong>{reserveSummary.phoneBound}</strong>
            </div>
            <div className="compact-stat">
              <span>目标分组</span>
              <strong>{selectedGroup ? `#${selectedGroup.id}` : "-"}</strong>
            </div>
          </section>

          <div className="list">
            <div className="list-item">
              <strong>显示范围</strong>
              <div className="cell-sub">这里只显示整体参数，不展示账号明细。账号明细请到“使用备选池”页面查看和操作。</div>
            </div>
            <div className="list-item">
              <strong>当前推送目标</strong>
              <div className="cell-sub">
                {selectedGroup ? `${selectedGroup.name} · #${selectedGroup.id}` : "尚未选择 sub2api 目标分组"}
              </div>
            </div>
            <div className="list-item">
              <strong>本地配置状态</strong>
              <div className="cell-sub">本地池配置暂不实际使用，后续再开发容量阈值和自动推送策略。</div>
            </div>
          </div>
        </section>
      </section>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h3>本地池配置</h3>
            <p>暂不实际使用。后续再开发容量阈值、推送策略、验证 group 和自动化规则。</p>
          </div>
          <span className="muted">{localPools.length} 条历史配置</span>
        </div>
        <div className="list">
          {localPools.map((pool) => (
            <div className="list-item" key={pool.id}>
              <strong>{pool.name}</strong>
              <div className="cell-sub">
                {pool.account_type} / site={pool.site_id} / active group #{pool.active_group_id}
              </div>
              <div className="cell-sub">当前仅保留配置记录，不参与备选池判断和自动推送。</div>
            </div>
          ))}
          {!localPools.length && <div className="empty-state">暂无本地池配置</div>}
        </div>
      </section>
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
        open={Boolean(confirmState)}
        title={confirmState?.title || ""}
        tone={confirmState?.tone}
      />
    </section>
  );
}

function StatusPill({ value, tone = "muted" }: { value: string; tone?: "success" | "warning" | "danger" | "muted" }) {
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

function displayValue(value: unknown) {
  return text(value) || "-";
}

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function clampInt(value: unknown, min: number, max: number, fallback: number) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(min, Math.min(max, Math.floor(number)));
}

function secondsToMinutes(value: unknown) {
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return 3;
  return Math.max(1, Math.round(number / 60));
}

function capacityLimitsToForm(limits: CapacityLimitsResponse["limits"]): CapacityLimitForm {
  return (Object.keys(defaultCapacityLimitForm) as CapacityLimitKey[]).reduce((form, accountType) => {
    form[accountType] = {
      five_hour_usd: String(limits?.[accountType]?.five_hour_usd ?? defaultCapacityLimitForm[accountType].five_hour_usd),
      seven_day_usd: String(limits?.[accountType]?.seven_day_usd ?? defaultCapacityLimitForm[accountType].seven_day_usd),
    };
    return form;
  }, { ...defaultCapacityLimitForm } as CapacityLimitForm);
}

function capacityLimitPayload(form: CapacityLimitForm): Record<CapacityLimitKey, { five_hour_usd: number; seven_day_usd: number }> | null {
  const payload = {} as Record<CapacityLimitKey, { five_hour_usd: number; seven_day_usd: number }>;
  for (const accountType of Object.keys(form) as CapacityLimitKey[]) {
    const fiveHour = Number(form[accountType].five_hour_usd);
    const sevenDay = Number(form[accountType].seven_day_usd);
    if (!Number.isFinite(fiveHour) || !Number.isFinite(sevenDay) || fiveHour < 0 || sevenDay < 0) return null;
    payload[accountType] = { five_hour_usd: fiveHour, seven_day_usd: sevenDay };
  }
  return payload;
}

function siteToForm(site: Site): SiteForm {
  return {
    id: site.id,
    name: site.name || site.id,
    base_url: site.base_url || "",
    token: "",
    status: site.status || "active",
    refresh_interval_minutes: site.refresh_interval_minutes || 30,
    auto_remove_abnormal_accounts: site.auto_remove_abnormal_accounts === true,
  };
}
