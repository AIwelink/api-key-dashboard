import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
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

type RefreshResponse = {
  groups?: number;
  accounts?: number;
  message?: string;
};

export function AccountPoolsPage({ token, showToast }: Props) {
  const [sites, setSites] = useState<Site[]>([]);
  const [selectedSiteId, setSelectedSiteId] = useState("");
  const [groups, setGroups] = useState<Group[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState<number | null>(null);
  const [reserveTotal, setReserveTotal] = useState(0);
  const [reserveSummary, setReserveSummary] = useState({ plus: 0, free: 0, pro: 0, phoneBound: 0, problem: 0 });
  const [localPools, setLocalPools] = useState<ApiPool[]>([]);
  const [lastRefreshedAt, setLastRefreshedAt] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingReserveSummary, setLoadingReserveSummary] = useState(false);

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
      const [allData, plusData, freeData, proData, phoneData] = await Promise.all([
        api<AccountsResponse>(withFilter(), token),
        api<AccountsResponse>(withFilter("account_type", "plus"), token),
        api<AccountsResponse>(withFilter("account_type", "free"), token),
        api<AccountsResponse>(withFilter("account_type", "pro"), token),
        api<AccountsResponse>(withFilter("phone_bound", "true"), token),
      ]);
      setReserveTotal(allData.total);
      setReserveSummary({
        plus: plusData.total,
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
    if (!selectedSiteId) return;
    setGroups([]);
    setSelectedGroupId(null);
    loadGroups(selectedSiteId).catch((error) => showToast(errorMessage(error), true));
  }, [selectedSiteId]);

  useEffect(() => {
    loadReserveSummary().catch((error) => showToast(errorMessage(error), true));
  }, [selectedSiteId, selectedGroupId]);

  return (
    <section className="view accounts-page">
      <div className="topbar">
        <div>
          <h2>账号池逻辑管理</h2>
          <p>当前页面读取统一的 sub2api 账号池缓存；分组和账号状态只同步一份，前端按页面需要展示。</p>
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
