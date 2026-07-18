import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import { errorMessage, formatDateTime } from "../utils/format";
import { safeHttpUrl } from "../utils/url";

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
  uptime_kuma_url?: string;
  uptime_kuma_api_key_configured?: boolean;
};

type SitesResponse = {
  items: Site[];
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
  record_usage_samples?: boolean;
  record_status_events?: boolean;
  record_duplicate_email_warning?: boolean;
  capacity_notification_enabled?: boolean;
  capacity_notification_threshold?: "tight" | "danger" | "exhausted";
  capacity_notification_cooldown_minutes?: number;
  capacity_notification_last_at?: string | null;
  capacity_notification_last_status?: string | null;
  capacity_notification_last_health_status?: string | null;
  uptime_kuma_monitor_url?: string;
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
  uptime_kuma_url: string;
  uptime_kuma_api_key: string;
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
  uptime_kuma_url: "",
  uptime_kuma_api_key: "",
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
  const [refreshing, setRefreshing] = useState(false);
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

  const loadSites = async () => {
    const data = await api<SitesResponse>("/sub2api-sites?site_type=sub2api", token);
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
      uptime_kuma_url: siteForm.uptime_kuma_url.trim(),
      ...(siteForm.token.trim() ? { token: siteForm.token.trim() } : {}),
      ...(siteForm.uptime_kuma_api_key.trim() ? { uptime_kuma_api_key: siteForm.uptime_kuma_api_key.trim() } : {}),
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
      showToast("账号池站点已保存");
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

  usePageAutoRefresh(
    () => loadObservabilitySettings(selectedSiteId),
    {
      enabled: Boolean(selectedSiteId),
      paused: Boolean(refreshing || savingSite || savingCapacityLimits || savingObservabilityKey || probing || confirmState),
    },
  );

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
      await loadObservabilitySettings(selectedSiteId);
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
    loadSites().catch((error) => showToast(errorMessage(error), true));
  }, []);

  useEffect(() => {
    const handleCacheUpdated = () => {
      if (!selectedSiteId) {
        loadSites().catch((error) => showToast(errorMessage(error), true));
        return;
      }
      loadObservabilitySettings(selectedSiteId).catch((error) => showToast(errorMessage(error), true));
    };
    window.addEventListener("sub2api-cache-updated", handleCacheUpdated);
    return () => window.removeEventListener("sub2api-cache-updated", handleCacheUpdated);
  }, [selectedSiteId]);

  useEffect(() => {
    if (!selectedSiteId) {
      setObservabilitySettings([]);
      loadCapacityLimits("").catch((error) => showToast(errorMessage(error), true));
      return;
    }
    const site = sites.find((item) => item.id === selectedSiteId);
    if (site) {
      setEditingSiteId(site.id);
      setSiteForm(siteToForm(site));
    }
    loadObservabilitySettings(selectedSiteId).catch((error) => showToast(errorMessage(error), true));
    loadCapacityLimits(selectedSiteId).catch((error) => showToast(errorMessage(error), true));
  }, [selectedSiteId, sites]);

  return (
    <section className="view accounts-page">
      <div className="topbar">
        <div>
          <h2>账号池管理</h2>
          <p>管理账号池后端 Sub2API、分组探测、容量参数和监控映射。</p>
        </div>
        {editingSiteId && (
          <button type="button" onClick={refreshRemoteCache} disabled={!selectedSiteId || refreshing}>
            {refreshing ? "同步中..." : "同步 Sub2API 账号池数据"}
          </button>
        )}
      </div>

      <section className="panel site-config-panel">
        <div className="panel-header">
          <div>
            <h3>账号池后端连接</h3>
            <p>这里只配置承载账号与调度状态的 Sub2API，不包含向客户提供服务的站点。</p>
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
            <input
              value={siteForm.name}
              onChange={(event) => setSiteForm((current) => ({ ...current, name: event.target.value }))}
              placeholder="Sub2API US06"
            />
          </label>
          <label className="span-2">
            <span className="field-label">
              <strong>Base URL</strong>
            </span>
            <input
              value={siteForm.base_url}
              onChange={(event) => setSiteForm((current) => ({ ...current, base_url: event.target.value }))}
              placeholder="http://104.238.221.47:5001"
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
          <>
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
              <label className="span-2">
                <span className="field-label">
                  <strong>Uptime Kuma 地址</strong>
                </span>
                <input
                  value={siteForm.uptime_kuma_url}
                  onChange={(event) => setSiteForm((current) => ({ ...current, uptime_kuma_url: event.target.value }))}
                  placeholder="https://status.aiwelink.cn"
                  type="url"
                />
              </label>
              <label>
                <span className="field-label">
                  <strong>Uptime Kuma API Key</strong>
                </span>
                <input
                  value={siteForm.uptime_kuma_api_key}
                  onChange={(event) => setSiteForm((current) => ({ ...current, uptime_kuma_api_key: event.target.value }))}
                  placeholder={selectedSite?.uptime_kuma_api_key_configured ? "已配置，留空不修改" : "API Key"}
                  type="password"
                />
                {selectedSite?.uptime_kuma_api_key_configured && <span className="cell-sub">密钥已配置</span>}
              </label>
          </>
        </div>
      </section>

      {editingSiteId && (
        <>
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
            <h3>分组监控与通知</h3>
            <p>按分组配置账号探测和容量预警；容量通知会发送到所有已启用的通知通道。</p>
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
                <th>记录内容</th>
                <th>容量通知</th>
                <th>Uptime Kuma</th>
                <th>最后更新</th>
              </tr>
            </thead>
            <tbody>
              {observabilitySettings.map((setting) => {
                const rowKey = `${setting.site_id}:${setting.group_id}`;
                const busy = savingObservabilityKey === rowKey;
                const uptimeMonitorHref = safeHttpUrl(setting.uptime_kuma_monitor_url);
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
                          <span>记录动态变化</span>
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
                    <td>
                      <div className="inline-check-stack">
                        <label className="inline-check">
                          <input
                            checked={setting.capacity_notification_enabled === true}
                            disabled={busy}
                            type="checkbox"
                            onChange={(event) => saveObservabilitySetting(setting, { capacity_notification_enabled: event.target.checked })}
                          />
                          <span>{setting.capacity_notification_enabled === true ? "开启" : "关闭"}</span>
                        </label>
                        <select
                          disabled={busy || setting.capacity_notification_enabled !== true}
                          value={setting.capacity_notification_threshold || "tight"}
                          onChange={(event) =>
                            saveObservabilitySetting(setting, {
                              capacity_notification_threshold: event.target.value as "tight" | "danger" | "exhausted",
                            })
                          }
                        >
                          <option value="tight">紧张及以下</option>
                          <option value="danger">危险及以下</option>
                          <option value="exhausted">仅耗尽</option>
                        </select>
                        <div>
                          <input
                            className="compact-number-input"
                            disabled={busy || setting.capacity_notification_enabled !== true}
                            min={5}
                            max={1440}
                            type="number"
                            defaultValue={setting.capacity_notification_cooldown_minutes || 60}
                            onBlur={(event) =>
                              saveObservabilitySetting(setting, {
                                capacity_notification_cooldown_minutes: clampInt(
                                  event.target.value,
                                  5,
                                  1440,
                                  setting.capacity_notification_cooldown_minutes || 60,
                                ),
                              })
                            }
                            onKeyDown={(event) => {
                              if (event.key === "Enter") event.currentTarget.blur();
                            }}
                          />
                          <span className="cell-sub"> 分钟重复提醒</span>
                        </div>
                        {setting.capacity_notification_last_at && (
                          <span className="cell-sub">
                            最近 {formatDateTime(setting.capacity_notification_last_at)} · {setting.capacity_notification_last_status || "-"}
                          </span>
                        )}
                      </div>
                    </td>
                    <td className="uptime-monitor-cell">
                      <div className="uptime-monitor-field">
                        <input
                          key={`${rowKey}:${setting.uptime_kuma_monitor_url || ""}`}
                          defaultValue={setting.uptime_kuma_monitor_url || ""}
                          disabled={busy}
                          onBlur={(event) => {
                            const monitorUrl = event.target.value.trim();
                            if (monitorUrl !== (setting.uptime_kuma_monitor_url || "")) {
                              saveObservabilitySetting(setting, { uptime_kuma_monitor_url: monitorUrl });
                            }
                          }}
                          onKeyDown={(event) => {
                            if (event.key === "Enter") event.currentTarget.blur();
                          }}
                          placeholder="https://status.aiwelink.cn/dashboard/4"
                          type="url"
                        />
                        {uptimeMonitorHref && (
                          <a
                            className="uptime-monitor-link"
                            href={uptimeMonitorHref}
                            rel="noreferrer"
                            target="_blank"
                          >
                            打开监控
                          </a>
                        )}
                      </div>
                    </td>
                    <td>{formatDateTime(setting.updated_at)}</td>
                  </tr>
                );
              })}
              {!observabilitySettings.length && (
                <tr>
                  <td className="muted" colSpan={10}>
                    暂无分组探测配置，请先同步 sub2api 分组。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
        </>
      )}
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
    uptime_kuma_url: site.uptime_kuma_url || "",
    uptime_kuma_api_key: "",
  };
}
