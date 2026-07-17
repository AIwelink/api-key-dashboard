import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import type { AccountDocument, PoolStatus } from "../types";
import { errorMessage, formatDateTime, formatPayment, formatPhoneBound, text } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type AccountListResponse = {
  items: AccountDocument[];
  total: number;
  skip?: number;
  limit?: number;
};

type Site = {
  id: string;
  name: string;
  base_url?: string;
  status: string;
  token_configured: boolean;
};

type Group = {
  id: number;
  name: string;
  platform?: string;
  status?: string;
  account_count?: number;
  active_account_count?: number;
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

type PushToSub2ApiResponse = {
  account: AccountDocument;
  remote_account?: Record<string, unknown>;
  verification?: {
    success?: boolean | null;
    status?: string;
    model?: string;
    response_preview?: string;
    error?: string;
    complete_event?: Record<string, unknown> | null;
    events?: Record<string, unknown>[] | null;
  };
};

type BatchResult = {
  failed: number;
  succeeded: number;
};

type AutoRefillLogAccount = {
  account_id?: string;
  email?: string;
  succeeded?: boolean;
  result?: string;
  current_status?: string;
  current_status_label?: string;
  remote_id?: string | number | null;
  verification_status?: string;
  error?: string | null;
  updated_at?: string;
};

type AutoRefillLog = {
  id: string;
  created_at?: string;
  finished_at?: string;
  status?: string;
  site_id?: string;
  group_id?: number;
  group_name?: string;
  need_count?: number;
  selected?: number;
  succeeded?: number;
  failed?: number;
  skipped?: boolean;
  reason?: string;
  accounts?: AutoRefillLogAccount[];
};

type AutoRefillLogsResponse = {
  items: AutoRefillLog[];
  total: number;
};

type ManualPoolMode = "todos" | "available" | "reserve";

type PageConfig = {
  title: string;
  description: string;
  empty: string;
  poolStatus: PoolStatus;
};

type ConfirmState = {
  confirmText?: string;
  details?: Array<[string, string | number | null | undefined]>;
  message?: string;
  onConfirm: () => void;
  title: string;
  tone?: "default" | "danger";
};

const configs: Record<ManualPoolMode, PageConfig> = {
  todos: {
    title: "待办",
    description: "当前先展示本地总库中已标记为问题的账号。sub2api 里单独存在的问题账号，后续需要先删除远端并写入本地库后再显示。",
    empty: "暂无本地问题账号",
    poolStatus: "problem",
  },
  available: {
    title: "可用池",
    description: "展示人工确认可用、准备进入使用备选池的账号。当前所有流转都由人工按钮触发。",
    empty: "暂无可用池账号",
    poolStatus: "available",
  },
  reserve: {
    title: "使用备选池",
    description: "展示已经通过验证或人工加入备选区的账号。可以单账号手动推送到 sub2api 分组并测试可用性。",
    empty: "暂无使用备选池账号",
    poolStatus: "reserve",
  },
};

export function TodoPage(props: Props) {
  return <ManualPoolPage {...props} mode="todos" />;
}

export function AvailablePoolPage(props: Props) {
  return <ManualPoolPage {...props} mode="available" />;
}

export function ReservePoolPage(props: Props) {
  return <ManualPoolPage {...props} mode="reserve" />;
}

function ManualPoolPage({ token, showToast, mode }: Props & { mode: ManualPoolMode }) {
  const config = configs[mode];
  const [accounts, setAccounts] = useState<AccountDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [sites, setSites] = useState<Site[]>([]);
  const [selectedSiteId, setSelectedSiteId] = useState("");
  const [groups, setGroups] = useState<Group[]>([]);
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [lastGroupsRefreshedAt, setLastGroupsRefreshedAt] = useState<string | null>(null);
  const [priority, setPriority] = useState("0");
  const [pushConcurrency, setPushConcurrency] = useState("10");
  const [pushLoadFactor, setPushLoadFactor] = useState("10");
  const [pushPriority, setPushPriority] = useState("100");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [skip, setSkip] = useState(0);
  const [limit, setLimit] = useState(50);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [refillLogs, setRefillLogs] = useState<AutoRefillLog[]>([]);
  const [loadingRefillLogs, setLoadingRefillLogs] = useState(false);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const pushingIdsRef = useRef<Set<string>>(new Set());

  const selectedSite = useMemo(() => sites.find((site) => site.id === selectedSiteId) || null, [sites, selectedSiteId]);
  const selectedGroup = useMemo(() => groups.find((group) => String(group.id) === selectedGroupId) || null, [groups, selectedGroupId]);
  const selectedOnPage = useMemo(() => accounts.filter((account) => selectedIds.has(account.id)), [accounts, selectedIds]);
  const allPageSelected = accounts.length > 0 && selectedOnPage.length === accounts.length;
  const somePageSelected = selectedOnPage.length > 0 && !allPageSelected;

  const loadAccounts = async () => {
    if (mode === "reserve" && !selectedSiteId) return;
    const params = new URLSearchParams({
      pool_status: config.poolStatus,
      sort_by: mode === "reserve" ? "reserve_order" : "updated_at",
      sort_dir: mode === "reserve" ? "asc" : "desc",
      skip: String(skip),
      limit: String(limit),
    });
    if (query) params.set("q", query);
    if (mode === "reserve" && selectedSiteId) params.set("site_id", selectedSiteId);
    const data = await api<AccountListResponse>(`/accounts?${params.toString()}`, token);
    setAccounts(data.items);
    setTotal(data.total);
  };

  const loadRefillLogs = async () => {
    if (mode !== "reserve" || !selectedSiteId) return;
    setLoadingRefillLogs(true);
    try {
      const params = new URLSearchParams({ site_id: selectedSiteId, limit: "10" });
      const data = await api<AutoRefillLogsResponse>(`/api-pools/auto-refill-logs?${params.toString()}`, token);
      setRefillLogs(data.items);
    } finally {
      setLoadingRefillLogs(false);
    }
  };

  const loadSites = async () => {
    const data = await api<SitesResponse>("/sub2api-sites?site_type=sub2api", token);
    setSites(data.items);
    if (!selectedSiteId && data.items[0]) {
      setSelectedSiteId(data.items[0].id);
    }
  };

  useEffect(() => {
    loadSites().catch((error) => showToast(errorMessage(error), true));
  }, []);

  const loadGroups = async (siteId = selectedSiteId) => {
    if (!siteId) return [];
    const data = await api<GroupsResponse>(`/sub2api-sites/${siteId}/groups?page=1&page_size=500`, token);
    setGroups(data.items);
    setLastGroupsRefreshedAt(data.cache_meta?.last_refreshed_at || null);
    if (selectedGroupId && !data.items.some((group) => String(group.id) === selectedGroupId)) {
      setSelectedGroupId("");
    }
    return data.items;
  };

  useEffect(() => {
    if (!selectedSiteId) return;
    setGroups([]);
    setSelectedGroupId("");
    loadGroups(selectedSiteId).catch((error) => showToast(errorMessage(error), true));
  }, [selectedSiteId]);

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
    loadAccounts().catch((error) => showToast(errorMessage(error), true));
  }, [mode, skip, limit, selectedSiteId]);

  useEffect(() => {
    if (mode !== "reserve") return;
    loadRefillLogs().catch((error) => showToast(errorMessage(error), true));
  }, [mode, selectedSiteId]);

  useEffect(() => {
    const pageIds = new Set(accounts.map((account) => account.id));
    setSelectedIds((current) => new Set([...current].filter((id) => pageIds.has(id))));
  }, [accounts]);

  const removeAccountFromPage = (accountId: string) => {
    setAccounts((current) => current.filter((item) => item.id !== accountId));
    setTotal((current) => Math.max(0, current - 1));
    setSelectedIds((current) => {
      const next = new Set(current);
      next.delete(accountId);
      return next;
    });
  };

  const replaceAccountOnPage = (updated: AccountDocument) => {
    setAccounts((current) => current.map((item) => (item.id === updated.id ? updated : item)));
  };

  const toggleReservePin = async (account: AccountDocument) => {
    const nextPinned = !isReservePinned(account);
    setBusyId(account.id);
    try {
      await api<AccountDocument>(`/accounts/${account.id}/reserve-pin`, token, {
        method: "POST",
        body: JSON.stringify({ pinned: nextPinned }),
      });
      showToast(nextPinned ? "已置顶到使用备选池前面" : "已取消置顶");
      await loadAccounts();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusyId(null);
    }
  };

  const transfer = async (account: AccountDocument, targetStatus: PoolStatus, label: string, extra: Record<string, unknown> = {}) => {
    setBusyId(account.id);
    try {
      await api<AccountDocument>(`/accounts/${account.id}/manual-transfer`, token, {
        method: "POST",
        body: JSON.stringify({
          target_status: targetStatus,
          reason: label,
          ...extra,
        }),
      });
      showToast(label);
      removeAccountFromPage(account.id);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusyId(null);
    }
  };

  const requireGroup = () => {
    if (!selectedGroupId) {
      showToast("请先选择 sub2api 目标分组", true);
      return false;
    }
    return true;
  };

  const performPushToSub2api = async (
    account: AccountDocument,
    targetGroupId: number,
    targetSiteId: string,
    concurrency: number,
    loadFactor: number,
    remotePriority: number,
  ) => {
    if (pushingIdsRef.current.has(account.id)) {
      showToast("这个账号正在推送中，请等待当前操作完成", true);
      return;
    }
    pushingIdsRef.current.add(account.id);
    setBusyId(account.id);
    try {
      const result = await api<PushToSub2ApiResponse>(`/accounts/${account.id}/push-to-sub2api`, token, {
        method: "POST",
        body: JSON.stringify({
          site_id: targetSiteId,
          group_id: targetGroupId,
          run_verification: true,
          model_id: "gpt-5.4-mini",
          prompt: "",
          concurrency,
          load_factor: loadFactor,
          priority: remotePriority,
          reason: "manual push from reserve pool",
        }),
      });
      const verification = result.verification;
      if (verification?.success === true) {
        showToast(`已推送并测试通过：${verification.response_preview || "success"}`);
      } else if (verification?.status === "skipped") {
        showToast("已推送，测试已跳过");
      } else {
        showToast(`已推送，但测试失败：${formatVerificationError(verification)}`, true);
      }
      removeAccountFromPage(account.id);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      pushingIdsRef.current.delete(account.id);
      setBusyId(null);
    }
  };

  const unbindSub2api = (account: AccountDocument) => {
    const remoteId = text(account.metadata.sub2api_account_id);
    if (!remoteId) {
      showToast("这个账号没有本地 sub2api 绑定", true);
      return;
    }
    setConfirmState({
      title: "确认解除绑定",
      message: "只清除本地绑定记录，不会删除 sub2api 里的远端账号。解除后可以重新推送并绑定新的远端账号。",
      details: [
        ["账号", accountEmail(account)],
        ["当前远端账号", `#${remoteId}`],
        ["目标分组", targetGroupLabel(account) || "-"],
      ],
      confirmText: "解除绑定",
      tone: "danger",
      onConfirm: async () => {
        setBusyId(account.id);
        try {
          const updated = await api<AccountDocument>(`/accounts/${account.id}/unbind-sub2api`, token, { method: "POST" });
          showToast("已解除本地 sub2api 绑定");
          replaceAccountOnPage(updated);
        } catch (error) {
          showToast(errorMessage(error), true);
        } finally {
          setBusyId(null);
        }
      },
    });
  };

  const pushToSub2api = (account: AccountDocument) => {
    const targetGroupId = accountTargetGroupId(account);
    if (!targetGroupId) {
      showToast("这个账号没有目标分组，请先从可用池加入使用备选池并选择分组", true);
      return;
    }
    if (busyId === account.id || pushingIdsRef.current.has(account.id)) {
      showToast("这个账号正在推送中，请等待当前操作完成", true);
      return;
    }
    const groupLabel = accountTargetGroupLabel(account, groups) || `#${targetGroupId}`;
    const targetSiteId = accountTargetSiteId(account) || selectedSiteId || "default";
    const concurrency = positiveInt(pushConcurrency, 10);
    const loadFactor = positiveInt(pushLoadFactor, 10);
    const remotePriority = nonNegativeInt(pushPriority, 100);
    setConfirmState({
      title: "确认推送并测试",
      message: "推送成功后会执行 gpt-5.4-mini 测试，并写入本地 active 状态。",
      details: [
        ["账号", accountEmail(account)],
        ["目标分组", groupLabel],
        ["并发", concurrency],
        ["负载因子", loadFactor],
        ["优先级", remotePriority],
      ],
      confirmText: "推送并测试",
      onConfirm: () => performPushToSub2api(account, targetGroupId, targetSiteId, concurrency, loadFactor, remotePriority),
    });
  };

  const togglePageSelection = (checked: boolean) => {
    setSelectedIds(checked ? new Set(accounts.map((account) => account.id)) : new Set());
  };

  const toggleAccountSelection = (accountId: string, checked: boolean) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (checked) next.add(accountId);
      else next.delete(accountId);
      return next;
    });
  };

  const changeLimit = (nextLimit: number) => {
    setLimit(nextLimit);
    setSkip(0);
  };

  const bulkTransfer = async (targetStatus: PoolStatus, label: string, extra: Record<string, unknown> = {}) => {
    if (!selectedOnPage.length) {
      showToast("请先选择账号", true);
      return;
    }
    setBulkBusy(true);
    try {
      const targets = [...selectedOnPage];
      const succeededIds = new Set<string>();
      const result = await runLimited(targets, 6, async (account) => {
        await api<AccountDocument>(`/accounts/${account.id}/manual-transfer`, token, {
            method: "POST",
            body: JSON.stringify({
              target_status: targetStatus,
              reason: label,
              ...extra,
            }),
          });
        succeededIds.add(account.id);
      });
      showToast(`${label}：成功 ${result.succeeded}，失败 ${result.failed}`, result.failed > 0);
      setSelectedIds(new Set());
      setAccounts((current) => current.filter((account) => !succeededIds.has(account.id)));
      setTotal((current) => Math.max(0, current - succeededIds.size));
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBulkBusy(false);
    }
  };

  const bulkUnbindSub2api = () => {
    const targets = selectedOnPage.filter((account) => text(account.metadata.sub2api_account_id));
    if (!targets.length) {
      showToast("请选择已有远端绑定的账号", true);
      return;
    }
    setConfirmState({
      title: "确认批量解除绑定",
      message: "只清除本地绑定记录，不会删除 sub2api 里的远端账号。",
      details: [
        ["账号数", targets.length],
      ],
      confirmText: "批量解除绑定",
      tone: "danger",
      onConfirm: async () => {
        setBulkBusy(true);
        try {
          const result = await runLimited(targets, 5, async (account) => {
            const updated = await api<AccountDocument>(`/accounts/${account.id}/unbind-sub2api`, token, { method: "POST" });
            replaceAccountOnPage(updated);
          });
          showToast(`批量解除绑定完成：成功 ${result.succeeded}，失败 ${result.failed}`, result.failed > 0);
          setSelectedIds(new Set());
        } finally {
          setBulkBusy(false);
        }
      },
    });
  };

  const bulkPushToSub2api = () => {
    const targets = selectedOnPage.filter((account) => accountTargetGroupId(account));
    if (!targets.length) {
      showToast("请选择已有目标分组的账号", true);
      return;
    }
    const concurrency = positiveInt(pushConcurrency, 10);
    const loadFactor = positiveInt(pushLoadFactor, 10);
    const remotePriority = nonNegativeInt(pushPriority, 100);
    setConfirmState({
      title: "确认批量推送并测试",
      message: "会按每个账号保存的目标分组推送到 sub2api，并执行测试。批量操作会限制并发，避免压垮 sub2api。",
      details: [
        ["账号数", targets.length],
        ["并发", concurrency],
        ["负载因子", loadFactor],
        ["远端优先级", remotePriority],
      ],
      confirmText: "批量推送并测试",
      onConfirm: async () => {
        setBulkBusy(true);
        try {
          const result = await runLimited(targets, 3, async (account) => {
            const targetGroupId = accountTargetGroupId(account);
            if (!targetGroupId) throw new Error("missing target group");
            const targetSiteId = accountTargetSiteId(account) || selectedSiteId || "default";
            const response = await api<PushToSub2ApiResponse>(`/accounts/${account.id}/push-to-sub2api`, token, {
              method: "POST",
              body: JSON.stringify({
                site_id: targetSiteId,
                group_id: targetGroupId,
                run_verification: true,
                model_id: "gpt-5.4-mini",
                prompt: "",
                concurrency,
                load_factor: loadFactor,
                priority: remotePriority,
                reason: "bulk push from reserve pool",
              }),
            });
            if (response.account) removeAccountFromPage(account.id);
          });
          showToast(`批量推送完成：成功 ${result.succeeded}，失败 ${result.failed}`, result.failed > 0);
          setSelectedIds(new Set());
        } finally {
          setBulkBusy(false);
        }
      },
    });
  };

  return (
    <section className="view accounts-page">
      <div className="topbar">
        <div>
          <h2>{config.title}</h2>
          <p>{config.description}</p>
        </div>
        <div className="topbar-actions-stack">
          <div className="button-row">
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索邮箱、备注、标注" />
            <button
              className="ghost"
              type="button"
              onClick={() => {
                if (skip === 0) {
                  loadAccounts().catch((error) => showToast(errorMessage(error), true));
                } else {
                  setSkip(0);
                }
              }}
            >
              搜索/刷新
            </button>
          </div>
          {mode !== "todos" && (
            <span className="muted sync-under-refresh">
              最后同步：{lastGroupsRefreshedAt ? formatDateTime(lastGroupsRefreshedAt) : "-"}
            </span>
          )}
        </div>
      </div>

      <CompactStats
        items={[
          ["当前账号", `${accounts.length} / ${total}`],
          ["Plus", countBy(accounts, "account_type", "plus")],
          ["Team子号", countBy(accounts, "account_type", "team")],
          ["已绑手机", accounts.filter((item) => item.metadata.phone_bound === true).length],
          ["问题标记", accounts.filter((item) => text(item.metadata.last_error)).length],
        ]}
      />

      {mode === "reserve" && (
        <section className="panel refill-log-panel">
          <div className="panel-header">
            <div>
              <h3>补号日志</h3>
              <p>记录自动补号批次、账号、时间、结果和当前状态。</p>
            </div>
            <button className="ghost compact-button" type="button" onClick={() => loadRefillLogs().catch((error) => showToast(errorMessage(error), true))}>
              {loadingRefillLogs ? "刷新中..." : "刷新日志"}
            </button>
          </div>
          <div className="refill-log-list">
            {refillLogs.map((log) => (
              <div className="refill-log-item" key={log.id}>
                <div className="refill-log-head">
                  <strong>{log.group_name || `分组 #${log.group_id || "-"}`}</strong>
                  <span>{formatDateTime(log.finished_at || log.created_at)}</span>
                  <span>需要 {numberValue(log.need_count)} / 选择 {numberValue(log.selected)} / 成功 {numberValue(log.succeeded)} / 失败 {numberValue(log.failed)}</span>
                  {log.skipped && <span className="muted">{log.reason || "已跳过"}</span>}
                </div>
                <div className="refill-log-accounts">
                  {(log.accounts || []).map((item) => (
                    <div className="refill-log-account" key={`${log.id}:${item.account_id}`}>
                      <span className={item.succeeded ? "success-text" : "danger"}>
                        {item.result || (item.succeeded ? "成功" : "失败")}
                      </span>
                      <strong>{item.email || item.account_id || "-"}</strong>
                      <span>现状 {item.current_status_label || poolStatusLabel(text(item.current_status))}</span>
                      {item.remote_id ? <span>远端 #{item.remote_id}</span> : null}
                      {item.verification_status ? <span>测试 {verificationLabel(text(item.verification_status))}</span> : null}
                      {item.updated_at ? <span>更新 {formatDateTime(item.updated_at)}</span> : null}
                      {item.error ? <span className="danger truncate" title={text(item.error)}>{text(item.error)}</span> : null}
                    </div>
                  ))}
                  {!log.accounts?.length && <div className="cell-sub">{log.reason || "本批次没有选择账号"}</div>}
                </div>
              </div>
            ))}
            {!refillLogs.length && <div className="empty-state">{loadingRefillLogs ? "正在读取补号日志..." : "暂无补号日志"}</div>}
          </div>
        </section>
      )}

      <section className="panel">
        <div className="panel-header">
          <div>
            <h3>{config.title}账号</h3>
            <p>
              {mode === "todos"
                ? "问题账号处理不需要选择 sub2api 分组"
                : mode === "reserve"
                  ? "推送 sub2api 时使用每个账号已保存的目标分组；目标分组在可用池加入备选池时写入。"
                  : selectedGroup
                    ? `当前目标分组：${selectedGroup.name} · #${selectedGroup.id}`
                    : "加入备选池前必须先手动选择 sub2api 目标分组"}
            </p>
          </div>
          <div className="button-row">
            {mode !== "todos" && (
              <>
                <select value={selectedSiteId} onChange={(event) => setSelectedSiteId(event.target.value)}>
                  {sites.map((site) => (
                    <option key={site.id} value={site.id}>
                      {site.name}
                    </option>
                  ))}
                </select>
              </>
            )}
            {mode === "available" && (
              <>
                <select value={selectedGroupId} onChange={(event) => setSelectedGroupId(event.target.value)}>
                  <option value="">选择 sub2api 分组</option>
                  {groups.map((group) => (
                    <option key={group.id} value={group.id}>
                      {group.name} · #{group.id} · {numberValue(group.active_account_count)}/{numberValue(group.account_count)}
                    </option>
                  ))}
                </select>
              </>
            )}
            {mode === "reserve" && (
              <>
                <label className="inline-select">
                  <span>并发</span>
                  <input
                    aria-label="sub2api 并发"
                    min={1}
                    onChange={(event) => setPushConcurrency(event.target.value)}
                    style={{ width: 76 }}
                    type="number"
                    value={pushConcurrency}
                  />
                </label>
                <label className="inline-select">
                  <span>负载因子</span>
                  <input
                    aria-label="sub2api 负载因子"
                    min={1}
                    onChange={(event) => setPushLoadFactor(event.target.value)}
                    style={{ width: 76 }}
                    type="number"
                    value={pushLoadFactor}
                  />
                </label>
                <label className="inline-select">
                  <span>远端优先级</span>
                  <input
                    aria-label="sub2api 远端优先级"
                    min={0}
                    onChange={(event) => setPushPriority(event.target.value)}
                    style={{ width: 82 }}
                    type="number"
                    value={pushPriority}
                  />
                </label>
              </>
            )}
            {mode === "available" && (
              <input
                aria-label="优先级"
                min={0}
                onChange={(event) => setPriority(event.target.value)}
                style={{ width: 96 }}
                type="number"
                value={priority}
              />
            )}
            <label className="inline-select">
              <span>每页</span>
              <select value={limit} onChange={(event) => changeLimit(Number(event.target.value))}>
                <option value={50}>50</option>
                <option value={200}>200</option>
                <option value={500}>500</option>
              </select>
            </label>
          </div>
        </div>

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
          <span className="muted">已选 {selectedOnPage.length}</span>
          <div className="button-row list-toolbar-actions">
            {mode === "todos" && (
              <>
                <button className="ghost compact-button" disabled={bulkBusy || !selectedOnPage.length} onClick={() => bulkTransfer("available", "已批量移回可用池")} type="button">
                  移回可用池
                </button>
                <button className="ghost compact-button" disabled={bulkBusy || !selectedOnPage.length} onClick={() => bulkTransfer("library", "已批量移回总库")} type="button">
                  移回总库
                </button>
              </>
            )}
            {mode === "available" && (
              <>
                <button
                  className="ghost compact-button"
                  disabled={bulkBusy || !selectedOnPage.length}
                  onClick={() => {
                    if (requireGroup()) {
                      bulkTransfer("reserve", "已批量加入使用备选池", {
                        pool_id: selectedGroupId,
                        site_id: selectedSiteId,
                        priority: Number(priority) || 0,
                      });
                    }
                  }}
                  type="button"
                >
                  加入备选池
                </button>
                <button className="ghost compact-button" disabled={bulkBusy || !selectedOnPage.length} onClick={() => bulkTransfer("library", "已批量退回总库")} type="button">
                  退回总库
                </button>
              </>
            )}
            {mode === "reserve" && (
              <>
                <button className="ghost compact-button" disabled={bulkBusy || !selectedOnPage.length} onClick={bulkPushToSub2api} type="button">
                  批量推送并测试
                </button>
                <button
                  className="ghost compact-button danger-button"
                  disabled={bulkBusy || !selectedOnPage.some((account) => text(account.metadata.sub2api_account_id))}
                  onClick={bulkUnbindSub2api}
                  type="button"
                >
                  批量解除绑定
                </button>
                <button className="ghost compact-button" disabled={bulkBusy || !selectedOnPage.length} onClick={() => bulkTransfer("available", "已批量退回可用池")} type="button">
                  退回可用池
                </button>
              </>
            )}
            <button
              className="ghost compact-button"
              disabled={bulkBusy || !selectedOnPage.length}
              onClick={() => bulkTransfer("problem", "已批量标记为问题账号", { last_error: "manual problem mark" })}
              type="button"
            >
              标记问题
            </button>
            <button
              className="ghost compact-button"
              disabled={bulkBusy || !selectedOnPage.length}
              onClick={() => bulkTransfer("discarded", "已批量弃用", { last_error: "manual discarded" })}
              type="button"
            >
              弃用
            </button>
          </div>
        </div>

        <div className="table-wrap account-table-wrap">
          <table className="account-table">
            <thead>
              <tr>
                <th className="select-col">选择</th>
                <th>账号</th>
                <th>类型</th>
                <th>支付</th>
                <th>手机</th>
                <th>状态信息</th>
                <th>时间</th>
                <th>备注</th>
                <th>手动操作</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => (
                <tr key={account.id}>
                  <td className="select-col">
                    <input
                      aria-label={`选择 ${accountEmail(account)}`}
                      checked={selectedIds.has(account.id)}
                      onChange={(event) => toggleAccountSelection(account.id, event.target.checked)}
                      type="checkbox"
                    />
                  </td>
                  <td>
                    <div className="cell-main">{accountEmail(account)}</div>
                    <div className="cell-sub">{text(account.account_json.name)}</div>
                  </td>
                  <td>{text(account.metadata.account_type) || "-"}</td>
                  <td>{formatPayment(account.metadata.payment_type) || <span className="muted">未填写</span>}</td>
                  <td>
                    <div>{formatPhoneBound(account.metadata.phone_bound)}</div>
                    <div className="cell-sub">{text(account.metadata.phone_number)}</div>
                  </td>
                  <td>
                    <StatusPill value={poolStatusLabel(text(account.metadata.pool_status) || config.poolStatus)} tone={poolStatusTone(text(account.metadata.pool_status) || config.poolStatus)} />
                    {mode === "reserve" && isReservePinned(account) && <div className="cell-sub reserve-pin-label">已置顶</div>}
                    {targetGroupLabel(account) && <div className="cell-sub">{targetGroupLabel(account)}</div>}
                    {text(account.metadata.priority) && <div className="cell-sub">优先级 {text(account.metadata.priority)}</div>}
                    {text(account.metadata.sub2api_account_id) && <div className="cell-sub">远端账号 #{text(account.metadata.sub2api_account_id)}</div>}
                    {text(account.metadata.verification_status) && <div className="cell-sub">测试 {verificationLabel(text(account.metadata.verification_status))}</div>}
                    {text(account.metadata.last_error) && <div className="cell-sub danger">{text(account.metadata.last_error)}</div>}
                  </td>
                  <td>
                    <div className="cell-sub">创建 {formatDateTime(account.metadata.created_at)}</div>
                    <div className="cell-sub">更新 {formatDateTime(account.metadata.updated_at)}</div>
                  </td>
                  <td className="remark-cell">{text(account.metadata.remark) || <span className="muted">-</span>}</td>
                  <td>
                    <div className="button-row action-wrap">
                      {mode === "todos" && (
                        <>
                          <button
                            className="ghost compact-button"
                            disabled={busyId === account.id}
                            onClick={() => transfer(account, "available", "已移回可用池")}
                            type="button"
                          >
                            移回可用池
                          </button>
                          <button
                            className="ghost compact-button"
                            disabled={busyId === account.id}
                            onClick={() => transfer(account, "library", "已移回总库")}
                            type="button"
                          >
                            移回总库
                          </button>
                        </>
                      )}
                      {mode === "available" && (
                        <>
                          <button
                            className="ghost compact-button"
                            disabled={busyId === account.id}
                            onClick={() => {
                              if (requireGroup()) {
                                transfer(account, "reserve", "已加入使用备选池", {
                                  pool_id: selectedGroupId,
                                  site_id: selectedSiteId,
                                  priority: Number(priority) || 0,
                                });
                              }
                            }}
                            type="button"
                          >
                            加入备选池
                          </button>
                          <button
                            className="ghost compact-button"
                            disabled={busyId === account.id}
                            onClick={() => transfer(account, "library", "已退回总库")}
                            type="button"
                          >
                            退回总库
                          </button>
                        </>
                      )}
                      {mode === "reserve" && (
                        <>
                          <button
                            className="ghost compact-button"
                            disabled={busyId === account.id}
                            onClick={() => toggleReservePin(account)}
                            type="button"
                          >
                            {isReservePinned(account) ? "取消置顶" : "置顶"}
                          </button>
                          <button
                            className="ghost compact-button"
                            disabled={busyId === account.id || !accountTargetGroupId(account)}
                            onClick={() => pushToSub2api(account)}
                            type="button"
                          >
                            推送并测试
                          </button>
                          {text(account.metadata.sub2api_account_id) && (
                            <button
                              className="ghost compact-button danger-button"
                              disabled={busyId === account.id}
                              onClick={() => unbindSub2api(account)}
                              type="button"
                            >
                              解除绑定
                            </button>
                          )}
                          <button
                            className="ghost compact-button"
                            disabled={busyId === account.id}
                            onClick={() => transfer(account, "available", "已退回可用池")}
                            type="button"
                          >
                            退回可用池
                          </button>
                        </>
                      )}
                      <button
                        className="ghost compact-button"
                        disabled={busyId === account.id}
                        onClick={() => transfer(account, "problem", "已标记为问题账号", { last_error: "manual problem mark" })}
                        type="button"
                      >
                        标记问题
                      </button>
                      <button
                        className="ghost compact-button"
                        disabled={busyId === account.id}
                        onClick={() => transfer(account, "discarded", "已弃用", { last_error: "manual discarded" })}
                        type="button"
                      >
                        弃用
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {!accounts.length && (
                <tr>
                  <td className="muted" colSpan={9}>
                    {config.empty}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="pagination">
          <label className="inline-select">
            <span>每页</span>
            <select value={limit} onChange={(event) => changeLimit(Number(event.target.value))}>
              <option value={50}>50</option>
              <option value={200}>200</option>
              <option value={500}>500</option>
            </select>
          </label>
          <button className="ghost" type="button" disabled={skip <= 0} onClick={() => setSkip(Math.max(0, skip - limit))}>
            上一页
          </button>
          <span className="muted">
            {total ? skip + 1 : 0}-{Math.min(skip + limit, total)} / {total}
          </span>
          <button className="ghost" type="button" disabled={skip + limit >= total} onClick={() => setSkip(skip + limit)}>
            下一页
          </button>
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
        open={confirmState !== null}
        title={confirmState?.title || ""}
        tone={confirmState?.tone}
      />
    </section>
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

function StatusPill({ value, tone = "muted" }: { value: string; tone?: "accent" | "success" | "warning" | "danger" | "muted" }) {
  return <span className={`status-pill ${tone}`}>{value}</span>;
}

function accountEmail(account: AccountDocument) {
  const credentials = asRecord(account.account_json.credentials);
  return text(account.metadata.email) || text(credentials.email) || text(account.account_json.name) || "未识别邮箱";
}

function countBy(accounts: AccountDocument[], key: string, value: string) {
  return accounts.filter((account) => text(account.metadata[key]) === value).length;
}

function targetGroupLabel(account: AccountDocument) {
  const groupName = text(account.metadata.sub2api_group_name);
  const groupId = text(account.metadata.sub2api_group_id) || text(account.metadata.pool_id);
  if (groupName && groupId) return `目标分组 ${groupName} · #${groupId}`;
  if (groupId) return `目标分组 #${groupId}`;
  return "";
}

function accountTargetGroupId(account: AccountDocument) {
  const raw = text(account.metadata.sub2api_group_id) || text(account.metadata.pool_id);
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

function accountTargetGroupLabel(account: AccountDocument, groups: Group[]) {
  const targetGroupId = accountTargetGroupId(account);
  if (!targetGroupId) return "";
  const storedName = text(account.metadata.sub2api_group_name);
  if (storedName) return `${storedName} #${targetGroupId}`;
  const group = groups.find((item) => item.id === targetGroupId);
  return group ? `${group.name} #${targetGroupId}` : `#${targetGroupId}`;
}

function accountTargetSiteId(account: AccountDocument) {
  return text(account.metadata.sub2api_site_id);
}

function isReservePinned(account: AccountDocument): boolean {
  return Boolean(text(account.metadata.reserve_pinned_at));
}

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function positiveInt(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 1 ? Math.floor(parsed) : fallback;
}

function nonNegativeInt(value: string, fallback: number) {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? Math.floor(parsed) : fallback;
}

function poolStatusLabel(value: string) {
  const labels: Record<string, string> = {
    library: "总库",
    available: "可用池",
    reserve: "使用备选池",
    active: "实际使用池",
    problem: "问题账号",
    discarded: "弃用",
  };
  return labels[value] || value;
}

function poolStatusTone(value: string): "accent" | "success" | "warning" | "danger" | "muted" {
  if (value === "available") return "accent";
  if (value === "reserve") return "accent";
  if (value === "active") return "success";
  if (value === "problem") return "warning";
  if (value === "discarded") return "danger";
  return "muted";
}

function verificationLabel(value: string) {
  const labels: Record<string, string> = {
    passed: "通过",
    failed: "失败",
    skipped: "跳过",
    testing: "测试中",
    not_tested: "未测试",
  };
  return labels[value] || value;
}

function formatVerificationError(verification?: PushToSub2ApiResponse["verification"]) {
  if (!verification) return "请查看账号状态";
  const parts = [verification.error, verification.response_preview]
    .map((value) => text(value).trim())
    .filter(Boolean);
  if (verification.complete_event && Object.keys(verification.complete_event).length > 0) {
    parts.push(`complete_event=${text(verification.complete_event)}`);
  }
  if (verification.events?.length) {
    parts.push(`events=${text(verification.events)}`);
  }
  return parts.length ? parts.join(" | ") : "请查看账号状态";
}

async function runLimited<T>(items: T[], concurrency: number, worker: (item: T) => Promise<void>): Promise<BatchResult> {
  let index = 0;
  let succeeded = 0;
  let failed = 0;
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (index < items.length) {
      const item = items[index];
      index += 1;
      try {
        await worker(item);
        succeeded += 1;
      } catch {
        failed += 1;
      }
    }
  });
  await Promise.all(workers);
  return { succeeded, failed };
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}
