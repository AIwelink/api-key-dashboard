import { Fragment, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { AccountEditPanel } from "../components/AccountEditPanel";
import type { AccountDocument, ApiPool } from "../types";
import { errorMessage, formatDateTime, formatPayment, text } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type TaskStatus = "open" | "pending" | "processing" | "completed" | "failed" | "all";
type TodoPanel = "problem" | "resurrection" | "upgrade";

type FreeToPlusResponse = {
  items: AccountDocument[];
  total: number;
  skip: number;
  limit: number;
  stats: {
    pending: number;
    processing: number;
    completed: number;
    failed: number;
  };
};

type ProblemAccountsResponse = {
  items: AccountDocument[];
  total: number;
  skip: number;
  limit: number;
};

type ApiPoolResponse = {
  items: ApiPool[];
  total: number;
};

type Sub2ApiSite = {
  id: string;
  name?: string;
  status?: string;
};

type Sub2ApiSitesResponse = {
  items: Sub2ApiSite[];
  total: number;
};

type Sub2ApiGroup = {
  id: number;
  name?: string;
  status?: string;
  subscription_type?: string;
};

type Sub2ApiGroupsResponse = {
  items: Sub2ApiGroup[];
  total: number;
};

type RemoteResurrectionAccount = {
  id: number;
  name?: string;
  status?: string;
  error_message?: string;
  schedulable?: boolean;
  credentials?: Record<string, unknown>;
  extra?: Record<string, unknown>;
  plan_type?: string;
  priority?: number;
  updated_at?: string;
  last_used_at?: string;
  codex_usage_updated_at?: unknown;
  codex_remote_tested_at?: string;
  rate_limited_at?: string | null;
  temp_unschedulable_until?: string | null;
  codex_7d_used_percent?: unknown;
  codex_5h_used_percent?: unknown;
  group_ids?: number[];
  local_account_id?: string;
  uploaded_by_user_id?: string;
  uploader_name?: string;
  local_email_session?: string;
  local_two_fa?: string;
  local_phone_number?: string;
  local_phone_bound?: boolean;
  site_id: string;
  pool_name: string;
  active_group_id: number;
};

type RemoteAccountsResponse = {
  items: RemoteResurrectionAccount[];
  total: number;
};

type AuthSession = {
  auth_url?: string;
  session_id?: string;
  data?: {
    auth_url?: string;
    session_id?: string;
  };
};

type ResurrectionWorkspace = {
  account: RemoteResurrectionAccount;
  auth?: AuthSession;
  callbackUrl: string;
  exchange?: Record<string, unknown>;
  resurrectionResult?: string;
  phoneMessage?: string;
  phoneError?: string;
  totpCode?: string;
  totpSeconds?: number;
  totpError?: string;
};

type ResurrectionEditFields = {
  email_session: string;
  twoFA: string;
  phone_bound: "true" | "false";
  phone_number: string;
  remark: string;
  manual_status_label: string;
};

type PushErrorStatus = "open" | "pending" | "processing" | "archived" | "resolved" | "all";
type PushErrorAccountType = "all" | "plus" | "team" | "free";

type PushErrorResponse = {
  items: AccountDocument[];
  total: number;
  skip: number;
  limit: number;
  stats: {
    pending: number;
    processing: number;
    archived: number;
    resolved: number;
    free: number;
    team: number;
    plus: number;
  };
};

const paymentOptions = [
  ["paypal_multi", "PayPal 一卡多号"],
  ["paypal_single", "PayPal 一卡一号"],
  ["no_card", "不绑卡"],
  ["gopay", "gopay"],
  ["other", "其他"],
] as const;

export function TodoPage({ token, showToast }: Props) {
  const [accounts, setAccounts] = useState<AccountDocument[]>([]);
  const [problemAccounts, setProblemAccounts] = useState<AccountDocument[]>([]);
  const [resurrectionAccounts, setResurrectionAccounts] = useState<RemoteResurrectionAccount[]>([]);
  const [problemTotal, setProblemTotal] = useState(0);
  const [resurrectionTotal, setResurrectionTotal] = useState(0);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<FreeToPlusResponse["stats"]>({ pending: 0, processing: 0, completed: 0, failed: 0 });
  const [status, setStatus] = useState<TaskStatus>("open");
  const [query, setQuery] = useState("");
  const [skip, setSkip] = useState(0);
  const [problemSkip, setProblemSkip] = useState(0);
  const [resurrectionSkip, setResurrectionSkip] = useState(0);
  const [limit, setLimit] = useState(50);
  const [problemLimit, setProblemLimit] = useState(50);
  const [resurrectionLimit, setResurrectionLimit] = useState(50);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [paymentById, setPaymentById] = useState<Record<string, string>>({});
  const [editingAccount, setEditingAccount] = useState<AccountDocument | null>(null);
  const [activePanel, setActivePanel] = useState<TodoPanel>("resurrection");
  const [resurrectionWorkspace, setResurrectionWorkspace] = useState<ResurrectionWorkspace | null>(null);
  const [resurrectionBusy, setResurrectionBusy] = useState<string | null>(null);
  const [resurrectionEdit, setResurrectionEdit] = useState<ResurrectionEditFields>(() => emptyResurrectionEditFields());
  const [copyPopup, setCopyPopup] = useState<{ message: string; tone: "success" | "danger"; nonce: number } | null>(null);

  const currentUserId = useMemo(() => {
    const raw = localStorage.getItem("user");
    if (!raw) return "";
    try {
      const parsed = JSON.parse(raw) as { id?: string; email?: string };
      return parsed.id || parsed.email || "";
    } catch {
      return "";
    }
  }, []);

  const loadAccounts = async () => {
    const params = new URLSearchParams({
      status,
      skip: String(skip),
      limit: String(limit),
    });
    if (query.trim()) params.set("q", query.trim());
    const data = await api<FreeToPlusResponse>(`/todo-items/free-to-plus/accounts?${params.toString()}`, token);
    setAccounts(data.items);
    setTotal(data.total);
    setStats(data.stats);
    setPaymentById((current) => {
      const next = { ...current };
      data.items.forEach((account) => {
        if (!next[account.id]) next[account.id] = text(account.metadata.payment_type) || "no_card";
      });
      return next;
    });
  };

  const loadProblemAccounts = async () => {
    const params = new URLSearchParams({
      account_scope: "problem",
      sort_by: "last_operation_at",
      sort_dir: "desc",
      skip: String(problemSkip),
      limit: String(problemLimit),
    });
    if (query.trim()) params.set("q", query.trim());
    const data = await api<ProblemAccountsResponse>(`/accounts?${params.toString()}`, token);
    setProblemAccounts(sortMineFirst(data.items, currentUserId));
    setProblemTotal(data.total);
  };

  const loadResurrectionAccounts = async () => {
    const [pools, sites] = await Promise.all([
      api<ApiPoolResponse>("/api-pools", token),
      api<Sub2ApiSitesResponse>("/sub2api-sites", token),
    ]);
    const activeSiteIds = new Set(sites.items.filter((site) => site.status !== "deleted" && site.status !== "disabled").map((site) => site.id));
    const fallbackSiteId = activeSiteIds.size === 1 ? Array.from(activeSiteIds)[0] : "";
    const plusPools = pools.items.filter((pool) => pool.status === "active" && pool.account_type === "plus" && pool.site_id && pool.active_group_id);
    const candidates: RemoteResurrectionAccount[] = [];
    const seenAccounts = new Set<string>();
    const targetGroups = new Map<string, { siteId: string; groupId: number; poolName: string }>();
    let skippedPools = 0;
    for (const pool of plusPools) {
      const siteId = resolvePoolSiteId(pool.site_id, activeSiteIds, fallbackSiteId);
      if (!siteId) {
        skippedPools += 1;
        continue;
      }
      targetGroups.set(`${siteId}:${pool.active_group_id}`, { siteId, groupId: pool.active_group_id, poolName: pool.name });
    }

    const appendCandidatesForTargets = async (targets: Array<{ siteId: string; groupId: number; poolName: string }>) => {
      for (const target of targets) {
        const params = new URLSearchParams({ page: "1", page_size: "500" });
        const data = await api<RemoteAccountsResponse>(`/sub2api-sites/${encodeURIComponent(target.siteId)}/groups/${target.groupId}/accounts?${params.toString()}`, token);
        data.items.forEach((account) => {
          const accountKey = `${target.siteId}:${account.id}`;
          if (seenAccounts.has(accountKey)) return;
          const decorated = { ...account, site_id: target.siteId, pool_name: target.poolName, active_group_id: target.groupId };
          if (isResurrectionCandidate(decorated, query)) {
            candidates.push(decorated);
            seenAccounts.add(accountKey);
          }
        });
      }
    };

    await appendCandidatesForTargets(Array.from(targetGroups.values()));

    if (candidates.length === 0) {
      const fallbackTargets: Array<{ siteId: string; groupId: number; poolName: string }> = [];
      for (const siteId of activeSiteIds) {
        const groups = await api<Sub2ApiGroupsResponse>(`/sub2api-sites/${encodeURIComponent(siteId)}/groups?page=1&page_size=500`, token);
        groups.items.forEach((group) => {
          if (!isResurrectionPoolGroup(group)) return;
          const key = `${siteId}:${group.id}`;
          if (targetGroups.has(key)) return;
          fallbackTargets.push({ siteId, groupId: group.id, poolName: text(group.name) || `group #${group.id}` });
        });
      }
      await appendCandidatesForTargets(fallbackTargets);
    }

    candidates.sort((left, right) => {
      const leftMine = isRemoteUploadedByCurrentUser(left, currentUserId) ? 1 : 0;
      const rightMine = isRemoteUploadedByCurrentUser(right, currentUserId) ? 1 : 0;
      if (leftMine !== rightMine) return rightMine - leftMine;
      return numberValue(left.codex_7d_used_percent) - numberValue(right.codex_7d_used_percent);
    });
    setResurrectionTotal(candidates.length);
    setResurrectionAccounts(candidates.slice(resurrectionSkip, resurrectionSkip + resurrectionLimit));
    if (skippedPools > 0) {
      showToast(`已跳过 ${skippedPools} 个站点配置失效的账号池`, true);
    }
  };

  useEffect(() => {
    loadAccounts().catch((error) => showToast(errorMessage(error), true));
  }, [status, skip, limit]);

  useEffect(() => {
    if (!copyPopup) return;
    const timer = window.setTimeout(() => setCopyPopup(null), 2200);
    return () => window.clearTimeout(timer);
  }, [copyPopup]);

  useEffect(() => {
    loadProblemAccounts().catch((error) => showToast(errorMessage(error), true));
  }, [problemSkip, problemLimit, currentUserId]);

  useEffect(() => {
    loadResurrectionAccounts().catch((error) => showToast(errorMessage(error), true));
  }, [resurrectionSkip, resurrectionLimit]);

  const refresh = () => {
    if (skip !== 0) setSkip(0);
    if (problemSkip !== 0) setProblemSkip(0);
    if (resurrectionSkip !== 0) setResurrectionSkip(0);
    if (skip === 0) loadAccounts().catch((error) => showToast(errorMessage(error), true));
    if (problemSkip === 0) loadProblemAccounts().catch((error) => showToast(errorMessage(error), true));
    if (resurrectionSkip === 0) loadResurrectionAccounts().catch((error) => showToast(errorMessage(error), true));
  };

  const runAccountAction = async (account: AccountDocument, action: "start" | "release" | "return-processing" | "complete" | "fail") => {
    setBusyId(account.id);
    try {
      if (action === "complete") {
        await api<AccountDocument>(`/todo-items/free-to-plus/accounts/${account.id}/complete`, token, {
          method: "POST",
          body: JSON.stringify({ payment_type: paymentById[account.id] || "no_card" }),
        });
        showToast("已完成升级，账号已进入可用池");
      } else if (action === "fail") {
        const error = window.prompt("请输入失败原因");
        if (!error) return;
        await api<AccountDocument>(`/todo-items/free-to-plus/accounts/${account.id}/fail`, token, {
          method: "POST",
          body: JSON.stringify({ error }),
        });
        showToast("已标记失败");
      } else {
        await api<AccountDocument>(`/todo-items/free-to-plus/accounts/${account.id}/${action}`, token, { method: "POST" });
        if (action === "start") showToast("已开始处理");
        else if (action === "return-processing") showToast("已退回处理");
        else showToast("已取消处理");
      }
      await loadAccounts();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusyId(null);
    }
  };

  const openEditAccount = async (account: AccountDocument) => {
    setBusyId(account.id);
    try {
      const fullAccount = await api<AccountDocument>(`/accounts/${account.id}`, token);
      setEditingAccount(fullAccount);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusyId(null);
    }
  };

  const resolveProblemAfterCorrection = async (account: AccountDocument) => {
    const confirmed = window.confirm("确认账号信息已经修正，并将该账号移出问题账号状态、重新进入总库？");
    if (!confirmed) return;
    const note = window.prompt("备注：本次修正了什么信息？", "账号信息已修正") || "";
    setBusyId(account.id);
    try {
      await api<AccountDocument>(`/accounts/${account.id}/resolve-problem-info-correction`, token, {
        method: "POST",
        body: JSON.stringify({ note }),
      });
      showToast("已记录错误账号信息修正，账号已重新进入总库");
      await loadProblemAccounts();
      await loadResurrectionAccounts();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusyId(null);
    }
  };

  const startResurrection = async (account: RemoteResurrectionAccount) => {
    const twoFa = twoFaInfo(account);
    setResurrectionEdit(buildResurrectionEditFields(account));
    setResurrectionWorkspace({
      account,
      callbackUrl: "",
      totpError: twoFa.message,
    });
    if (twoFa.status === "valid") {
      const code = safeGenerateTotp(twoFa.value);
      setResurrectionWorkspace((current) =>
        current?.account.id === account.id ? { ...current, totpCode: code?.code, totpSeconds: code?.seconds, totpError: "error" in code ? code.error : undefined } : current,
      );
    }
  };

  const generateAuthUrl = async () => {
    if (!resurrectionWorkspace) return;
    setResurrectionBusy(`auth:${resurrectionWorkspace.account.id}`);
    try {
      const auth = normalizeAuthSession(await api<AuthSession>(`/sub2api-sites/${resurrectionWorkspace.account.site_id}/openai/generate-auth-url`, token, { method: "POST" }));
      setResurrectionWorkspace((current) => (current ? { ...current, auth } : current));
      if (auth.auth_url) {
        try {
          await copyToClipboard(auth.auth_url);
          showCopyPopup("授权链接已生成并复制");
          showToast("授权链接已生成并复制");
          return;
        } catch {
          showCopyPopup("授权链接已生成，请手动复制", "danger");
          showToast("授权链接已生成，请手动复制");
          return;
        }
      }
      showToast("授权链接已生成");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setResurrectionBusy(null);
    }
  };

  const exchangeCode = async () => {
    if (!resurrectionWorkspace?.auth?.session_id) return;
    setResurrectionBusy(`exchange:${resurrectionWorkspace.account.id}`);
    try {
      const exchange = await api<Record<string, unknown>>(`/sub2api-sites/${resurrectionWorkspace.account.site_id}/openai/exchange-code`, token, {
        method: "POST",
        body: JSON.stringify({
          session_id: resurrectionWorkspace.auth.session_id,
          callback_url: resurrectionWorkspace.callbackUrl,
        }),
      });
      setResurrectionWorkspace((current) => (current ? { ...current, exchange } : current));
      showToast("OAuth 凭证已交换成功");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setResurrectionBusy(null);
    }
  };

  const applyOAuthCredentials = async () => {
    if (!resurrectionWorkspace?.exchange) return;
    const credentials = oauthCredentialsFromExchange(resurrectionWorkspace.exchange);
    setResurrectionBusy(`apply:${resurrectionWorkspace.account.id}`);
    try {
      await api(`/sub2api-sites/${resurrectionWorkspace.account.site_id}/accounts/${resurrectionWorkspace.account.id}/apply-oauth-credentials`, token, {
        method: "POST",
        body: JSON.stringify({ account_type: "oauth", credentials }),
      });
      showToast("账号已应用新 OAuth 凭证并恢复调度");
      setResurrectionWorkspace(null);
      await loadResurrectionAccounts();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setResurrectionBusy(null);
    }
  };

  const submitOAuthCallbackAndRevive = async () => {
    if (!resurrectionWorkspace?.auth?.session_id || !resurrectionWorkspace.callbackUrl) return;
    setResurrectionBusy(`submit:${resurrectionWorkspace.account.id}`);
    try {
      const exchange = await api<Record<string, unknown>>(`/sub2api-sites/${resurrectionWorkspace.account.site_id}/openai/exchange-code`, token, {
        method: "POST",
        body: JSON.stringify({
          session_id: resurrectionWorkspace.auth.session_id,
          callback_url: resurrectionWorkspace.callbackUrl,
        }),
      });
      const credentials = oauthCredentialsFromExchange(exchange);
      await api(`/sub2api-sites/${resurrectionWorkspace.account.site_id}/accounts/${resurrectionWorkspace.account.id}/apply-oauth-credentials`, token, {
        method: "POST",
        body: JSON.stringify({ account_type: "oauth", credentials }),
      });
      setResurrectionWorkspace((current) => (current ? { ...current, exchange, resurrectionResult: "复活成功：OAuth 凭证已更新，账号已恢复调度。" } : current));
      showToast("账号已自动交换 OAuth 凭证并复活");
      await loadResurrectionAccounts();
    } catch (error) {
      setResurrectionWorkspace((current) => (current ? { ...current, resurrectionResult: `复活失败：${errorMessage(error)}` } : current));
      showToast(errorMessage(error), true);
    } finally {
      setResurrectionBusy(null);
    }
  };

  const markResurrectionFailed = async (account: RemoteResurrectionAccount) => {
    const reason = window.prompt("请输入复活失败原因");
    if (!reason) return;
    setResurrectionBusy(`fail:${account.id}`);
    try {
      await api(`/sub2api-sites/${account.site_id}/accounts/${account.id}/resurrection-fail`, token, {
        method: "POST",
        body: JSON.stringify({ reason }),
      });
      showToast("已记录复活失败，并转入推送问题账户池");
      if (resurrectionWorkspace?.account.id === account.id) setResurrectionWorkspace(null);
      await loadResurrectionAccounts();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setResurrectionBusy(null);
    }
  };

  const copyText = async (value: string, message: string) => {
    try {
      await copyToClipboard(value);
      showCopyPopup(message);
      showToast(message);
    } catch (error) {
      const message = `复制失败：${errorMessage(error)}。请手动选中文本复制。`;
      showCopyPopup(message, "danger");
      showToast(message, true);
    }
  };

  const showCopyPopup = (message: string, tone: "success" | "danger" = "success") => {
    setCopyPopup({ message, tone, nonce: Date.now() });
  };

  const refreshPhoneCode = async () => {
    const account = resurrectionWorkspace?.account;
    const phone = phoneInfo(account);
    if (!account) return;
    if (phone.status !== "valid") {
      setResurrectionWorkspace((current) =>
        current?.account.id === account.id ? { ...current, phoneMessage: undefined, phoneError: phone.message || "手机接码地址不可用" } : current,
      );
      return;
    }
    try {
      const response = await fetch(phone.url, { cache: "no-store" });
      const body = await response.text();
      setResurrectionWorkspace((current) =>
        current?.account.id === account.id ? { ...current, phoneMessage: body, phoneError: undefined } : current,
      );
      showCopyPopup("手机验证码已刷新");
    } catch (error) {
      const message = errorMessage(error);
      setResurrectionWorkspace((current) =>
        current?.account.id === account.id ? { ...current, phoneError: message } : current,
      );
      showCopyPopup(`刷新失败：${message}`, "danger");
    }
  };

  const submitResurrectionInfoEdit = async () => {
    const account = resurrectionWorkspace?.account;
    const localAccountId = text(account?.local_account_id);
    if (!account || !localAccountId) {
      showToast("该远端账号没有匹配到本地账号，无法保存复活信息。", true);
      return;
    }
    if (resurrectionEdit.phone_bound === "true" && !resurrectionEdit.phone_number.trim()) {
      showToast("绑定手机后必须填写 codex 手机接码地址", true);
      return;
    }
    setResurrectionBusy(`info:${account.id}`);
    try {
      await api<AccountDocument>(`/accounts/${localAccountId}`, token, {
        method: "PATCH",
        body: JSON.stringify({ metadata: buildResurrectionEditMetadata(resurrectionEdit) }),
      });
      const nextAccount: RemoteResurrectionAccount = {
        ...account,
        local_email_session: resurrectionEdit.email_session,
        local_two_fa: resurrectionEdit.twoFA,
        local_phone_number: resurrectionEdit.phone_number,
        local_phone_bound: resurrectionEdit.phone_bound === "true",
        extra: {
          ...(account.extra || {}),
          email_session: resurrectionEdit.email_session,
          "2FA": resurrectionEdit.twoFA,
          phone_bound: resurrectionEdit.phone_bound === "true",
          phone_number: resurrectionEdit.phone_number,
          remark: resurrectionEdit.remark,
          manual_status_label: resurrectionEdit.manual_status_label,
        },
      };
      const twoFa = twoFaInfo(nextAccount);
      const code = twoFa.status === "valid" ? safeGenerateTotp(twoFa.value) : { code: undefined, seconds: undefined, error: twoFa.message };
      setResurrectionWorkspace((current) =>
        current?.account.id === account.id
          ? {
              ...current,
              account: nextAccount,
              phoneMessage: undefined,
              phoneError: undefined,
              totpCode: code.code,
              totpSeconds: code.seconds,
              totpError: "error" in code ? code.error : undefined,
            }
          : current,
      );
      showToast("复活信息已更新");
      await loadResurrectionAccounts();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setResurrectionBusy(null);
    }
  };

  useEffect(() => {
    const account = resurrectionWorkspace?.account;
    const twoFa = twoFaInfo(account);
    if (!account || twoFa.status !== "valid") {
      if (account && twoFa.message) {
        setResurrectionWorkspace((current) =>
          current?.account.id === account.id ? { ...current, totpCode: undefined, totpSeconds: undefined, totpError: twoFa.message } : current,
        );
      }
      return;
    }
    let cancelled = false;
    const tick = () => {
      const code = safeGenerateTotp(twoFa.value);
      if (!cancelled) {
        setResurrectionWorkspace((current) =>
          current?.account.id === account.id ? { ...current, totpCode: code?.code, totpSeconds: code?.seconds, totpError: "error" in code ? code.error : undefined } : current,
        );
      }
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [resurrectionWorkspace?.account.id, twoFaInfo(resurrectionWorkspace?.account).value]);

  useEffect(() => {
    const account = resurrectionWorkspace?.account;
    const phone = phoneInfo(account);
    if (!account || phone.status !== "valid") {
      if (account && phone.message) {
        setResurrectionWorkspace((current) =>
          current?.account.id === account.id ? { ...current, phoneMessage: undefined, phoneError: phone.message } : current,
        );
      }
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const response = await fetch(phone.url, { cache: "no-store" });
        const body = await response.text();
        if (!cancelled) {
          setResurrectionWorkspace((current) =>
            current?.account.id === account.id ? { ...current, phoneMessage: body, phoneError: undefined } : current,
          );
        }
      } catch (error) {
        if (!cancelled) {
          setResurrectionWorkspace((current) =>
            current?.account.id === account.id ? { ...current, phoneError: errorMessage(error) } : current,
          );
        }
      }
    };
    void tick();
    const timer = window.setInterval(tick, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [resurrectionWorkspace?.account.id, phoneCodeUrl(resurrectionWorkspace?.account)]);

  return (
    <section className="view accounts-page">
      <div className="topbar">
        <div>
          <h2>代办与错误账号处理</h2>
          <p>代办与错误账号处理是人工任务池和执行台。账号只在总库或可用池时进入待办；一旦进入使用备选池或实际使用池，就不会出现在这里。</p>
        </div>
        <div className="button-row">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索邮箱、来源、备注、处理人" />
          <button className="ghost" onClick={refresh} type="button">
            搜索/刷新
          </button>
        </div>
      </div>

      <div className="account-view-menu">
        <button className={`account-view-menu-item ${activePanel === "resurrection" ? "active" : ""}`} onClick={() => setActivePanel("resurrection")} type="button">
          账号复活
          <span>{resurrectionTotal} 个待复活账号</span>
        </button>
        <button className={`account-view-menu-item ${activePanel === "problem" ? "active" : ""}`} onClick={() => setActivePanel("problem")} type="button">
          错误账号处理
          <span>{problemTotal} 个问题账号</span>
        </button>
        <button className={`account-view-menu-item ${activePanel === "upgrade" ? "active" : ""}`} onClick={() => setActivePanel("upgrade")} type="button">
          free 升级 plus
          <span>{stats.pending + stats.processing} 个待处理</span>
        </button>
      </div>

      {activePanel === "problem" && (
        <section className="panel">
        <div className="panel-header">
          <div>
            <h3>错误账号处理</h3>
            <p>显示账号列表里的问题账号；当前登录用户上传的账号会置顶，并标记为您的账号错误。</p>
          </div>
          <div className="button-row">
            <label className="inline-select">
              <span>每页</span>
              <select
                value={problemLimit}
                onChange={(event) => {
                  setProblemLimit(Number(event.target.value));
                  setProblemSkip(0);
                }}
              >
                <option value={50}>50</option>
                <option value={200}>200</option>
                <option value={500}>500</option>
              </select>
            </label>
          </div>
        </div>

        <>
            <CompactStats
              items={[
                ["问题账号", problemTotal],
                ["您的账号错误", problemAccounts.filter((account) => isUploadedByCurrentUser(account, currentUserId)).length],
              ]}
            />

            <div className="table-wrap account-table-wrap">
              <table className="account-table">
                <thead>
                  <tr>
                    <th>账号</th>
                    <th>类型</th>
                    <th>来源</th>
                    <th>支付</th>
                    <th>状态</th>
                    <th>上传 / 操作</th>
                    <th>时间</th>
                    <th>错误/备注</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {problemAccounts.map((account) => {
                    const isMine = isUploadedByCurrentUser(account, currentUserId);
                    return (
                      <tr key={account.id}>
                        <td>
                          <div className="cell-main">{accountEmail(account)}</div>
                          <div className="cell-sub">{text(account.account_json.name)}</div>
                          {isMine && <div className="cell-sub"><StatusPill value="您的账号错误" tone="danger" /></div>}
                        </td>
                        <td>{text(account.metadata.account_type) || "-"}</td>
                        <td>{text(account.metadata.purchase_source) || <span className="muted">-</span>}</td>
                        <td>{formatPayment(account.metadata.payment_type) || <span className="muted">未填写</span>}</td>
                        <td>
                          <StatusPill value={poolStatusLabel(text(account.metadata.pool_status) || "problem")} tone="warning" />
                        </td>
                        <td>
                          <div>{text(account.metadata.uploader_name) || <span className="muted">未知</span>}</div>
                          <div className="cell-sub">操作 {text(account.metadata.last_operation_by_name) || "-"}</div>
                          {text(account.metadata.last_operation_name) && (
                            <div className="cell-sub">
                              {text(account.metadata.last_operation_name)}
                              {text(account.metadata.last_operation_at) ? ` · ${formatDateTime(account.metadata.last_operation_at)}` : ""}
                            </div>
                          )}
                        </td>
                        <td>
                          <div className="cell-sub">创建 {formatDateTime(account.metadata.created_at)}</div>
                          <div className="cell-sub">更新 {formatDateTime(account.metadata.updated_at)}</div>
                        </td>
                        <td className="remark-cell">
                          {text(account.metadata.problem_remark_zh) ||
                            text(account.metadata.problem_error) ||
                            text(account.metadata.last_error) ||
                            text(account.metadata.remark) ||
                            <span className="muted">-</span>}
                        </td>
                        <td>
                          <div className="button-row action-wrap">
                            <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => openEditAccount(account)} type="button">
                              编辑
                            </button>
                            <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => resolveProblemAfterCorrection(account)} type="button">
                              修正后回总库
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                  {!problemAccounts.length && (
                    <tr>
                      <td className="muted" colSpan={9}>
                        暂无问题账号
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            <div className="pagination">
              <button className="ghost" type="button" disabled={problemSkip <= 0} onClick={() => setProblemSkip(Math.max(0, problemSkip - problemLimit))}>
                上一页
              </button>
              <span className="muted">
                {problemTotal ? problemSkip + 1 : 0}-{Math.min(problemSkip + problemLimit, problemTotal)} / {problemTotal}
              </span>
              <button className="ghost" type="button" disabled={problemSkip + problemLimit >= problemTotal} onClick={() => setProblemSkip(problemSkip + problemLimit)}>
                下一页
              </button>
            </div>
          </>
        </section>
      )}

      {activePanel === "resurrection" && (
        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>账号复活</h3>
              <p>显示已经完成“错误账号信息修正”并重新进入总库的账号，方便复查修正记录和后续继续处理。</p>
            </div>
            <div className="button-row">
              <label className="inline-select">
                <span>每页</span>
                <select
                  value={resurrectionLimit}
                  onChange={(event) => {
                    setResurrectionLimit(Number(event.target.value));
                    setResurrectionSkip(0);
                  }}
                >
                  <option value={50}>50</option>
                  <option value={200}>200</option>
                  <option value={500}>500</option>
                </select>
              </label>
            </div>
          </div>

          <CompactStats
            items={[
              ["已复活账号", resurrectionTotal],
              ["本页账号", resurrectionAccounts.length],
            ]}
          />

          <div className="table-wrap account-table-wrap">
            <table className="account-table">
              <thead>
                <tr>
                  <th>账号</th>
                  <th>上传人</th>
                  <th>失败时间</th>
                  <th>当前状态</th>
                  <th>修正记录</th>
                  <th>备注</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {resurrectionAccounts.map((account) => (
                  <tr key={account.id}>
                    <td>
                      <div className="cell-main">{remoteAccountEmail(account)}</div>
                      {isRemoteUploadedByCurrentUser(account, currentUserId) && <div className="cell-sub"><StatusPill value="您的账号错误" tone="danger" /></div>}
                      <div className="cell-sub">{text(account.name) || `#${account.id}`}</div>
                      <div className="cell-sub">{account.pool_name} · group #{account.active_group_id}</div>
                    </td>
                    <td>
                      <div>{text(account.uploader_name) || <span className="muted">未知</span>}</div>
                      {text(account.uploaded_by_user_id) && <div className="cell-sub">{text(account.uploaded_by_user_id)}</div>}
                    </td>
                    <td>
                      <div>{formatDateTime(remoteFailureTime(account))}</div>
                    </td>
                    <td>
                      <StatusPill value={remoteAccountStatusLabel(account)} tone={remoteAccountStatusTone(account)} />
                      <div className="cell-sub">7d {numberValue(account.codex_7d_used_percent)}%</div>
                    </td>
                    <td>
                      <div>{account.schedulable === false ? "调度关闭" : "错误账号"}</div>
                      <div className="cell-sub">priority {text(account.priority) || "-"}</div>
                    </td>
                    <td className="remark-cell">
                      {text(account.error_message) || text(account.extra?.last_error) || text(account.extra?.temp_unschedulable_reason) || <span className="muted">-</span>}
                    </td>
                    <td>
                      <div className="button-row action-wrap">
                        <button className="ghost compact-button" disabled={resurrectionBusy !== null} onClick={() => startResurrection(account)} type="button">
                          开始复活
                        </button>
                        <button className="ghost compact-button danger-button" disabled={resurrectionBusy !== null} onClick={() => markResurrectionFailed(account)} type="button">
                          复活失败
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!resurrectionAccounts.length && (
                  <tr>
                    <td className="muted" colSpan={7}>
                      暂无复活账号
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="pagination">
            <button className="ghost" type="button" disabled={resurrectionSkip <= 0} onClick={() => setResurrectionSkip(Math.max(0, resurrectionSkip - resurrectionLimit))}>
              上一页
            </button>
            <span className="muted">
              {resurrectionTotal ? resurrectionSkip + 1 : 0}-{Math.min(resurrectionSkip + resurrectionLimit, resurrectionTotal)} / {resurrectionTotal}
            </span>
            <button className="ghost" type="button" disabled={resurrectionSkip + resurrectionLimit >= resurrectionTotal} onClick={() => setResurrectionSkip(resurrectionSkip + resurrectionLimit)}>
              下一页
            </button>
          </div>
          {resurrectionWorkspace && (
            <div className="resurrection-float-backdrop" role="dialog" aria-modal="true" onClick={() => setResurrectionWorkspace(null)}>
              <aside className="resurrection-float-panel" onClick={(event) => event.stopPropagation()}>
                {copyPopup && (
                  <div className={`copy-feedback-popup ${copyPopup.tone}`} key={copyPopup.nonce}>
                    {copyPopup.message}
                  </div>
                )}
                <div className="resurrection-float-header">
                  <div>
                    <h3>账号复活</h3>
                    <p>{remoteAccountEmail(resurrectionWorkspace.account)} · remote #{resurrectionWorkspace.account.id}</p>
                  </div>
                  <button className="ghost icon-button" type="button" aria-label="关闭账号复活窗口" onClick={() => setResurrectionWorkspace(null)}>
                    ×
                  </button>
                </div>
                <div className="task-detail-panel resurrection-workspace">
              <section>
                <h4>登录信息</h4>
                <CopyFieldList
                  copyText={copyText}
                  items={[
                    ["邮箱", remoteAccountEmail(resurrectionWorkspace.account)],
                    ["邮箱/接码 session", emailSessionValue(resurrectionWorkspace.account)],
                    ["2FA", twoFaDisplayValue(resurrectionWorkspace.account)],
                    ["手机接码地址", phoneRawValue(resurrectionWorkspace.account)],
                  ]}
                />
                <div className="button-row action-wrap">
                  <StatusPill value={twoFaInfo(resurrectionWorkspace.account).message || "2FA 信息可用"} tone={twoFaInfo(resurrectionWorkspace.account).status === "valid" ? "success" : "warning"} />
                  <StatusPill value={phoneInfo(resurrectionWorkspace.account).message || "手机信息可用"} tone={phoneInfo(resurrectionWorkspace.account).status === "valid" ? "success" : "warning"} />
                </div>
                <div className="resurrection-inline-edit">
                  <label>
                    <span>邮箱和接码 session</span>
                    <input value={resurrectionEdit.email_session} onChange={(event) => setResurrectionEdit((current) => ({ ...current, email_session: event.target.value }))} />
                  </label>
                  <label>
                    <span>2FA</span>
                    <input value={resurrectionEdit.twoFA} onChange={(event) => setResurrectionEdit((current) => ({ ...current, twoFA: event.target.value }))} />
                  </label>
                  <label>
                    <span>是否绑定手机</span>
                    <select value={resurrectionEdit.phone_bound} onChange={(event) => setResurrectionEdit((current) => ({ ...current, phone_bound: event.target.value as ResurrectionEditFields["phone_bound"] }))}>
                      <option value="true">是</option>
                      <option value="false">否</option>
                    </select>
                  </label>
                  <label>
                    <span>codex手机接码地址</span>
                    <input
                      value={resurrectionEdit.phone_number}
                      onChange={(event) => setResurrectionEdit((current) => ({ ...current, phone_number: event.target.value }))}
                      placeholder="https://cdc.smslease.link/adminapi/jsscript/smsInfo/ABC_sms?key=..."
                    />
                  </label>
                  <label>
                    <span>备注</span>
                    <input value={resurrectionEdit.remark} onChange={(event) => setResurrectionEdit((current) => ({ ...current, remark: event.target.value }))} />
                  </label>
                  <label>
                    <span>状态标注</span>
                    <input value={resurrectionEdit.manual_status_label} onChange={(event) => setResurrectionEdit((current) => ({ ...current, manual_status_label: event.target.value }))} />
                  </label>
                  <button className="ghost compact-button" type="button" disabled={resurrectionBusy !== null || !resurrectionWorkspace.account.local_account_id} onClick={submitResurrectionInfoEdit}>
                    保存账号信息
                  </button>
                </div>
              </section>
              <section>
                <h4>验证码</h4>
                <div className="verification-card-grid">
                  <div className="verification-card">
                    <span>2FA 动态码</span>
                    <button className="verification-code" type="button" onClick={() => resurrectionWorkspace.totpCode && copyText(resurrectionWorkspace.totpCode, "2FA 动态码已复制")}>{resurrectionWorkspace.totpCode || "-"}</button>
                    <em className="copy-hint">点击验证码即可复制</em>
                    <small>{resurrectionWorkspace.totpError || (resurrectionWorkspace.totpSeconds !== undefined ? `${resurrectionWorkspace.totpSeconds}s` : "本地计算")}</small>
                  </div>
                  <div className="verification-card">
                    <span>手机验证码</span>
                    <button className="verification-code" type="button" onClick={() => extractVerificationCode(resurrectionWorkspace.phoneMessage) && copyText(extractVerificationCode(resurrectionWorkspace.phoneMessage), "手机验证码已复制")}>{extractVerificationCode(resurrectionWorkspace.phoneMessage) || "-"}</button>
                    <em className="copy-hint">点击验证码即可复制</em>
                    <div className="verification-card-actions">
                      <button className="ghost compact-button" type="button" disabled={phoneInfo(resurrectionWorkspace.account).status !== "valid"} onClick={refreshPhoneCode}>
                        手动刷新
                      </button>
                    </div>
                    <small>{resurrectionWorkspace.phoneMessage || resurrectionWorkspace.phoneError || "等待短信"}</small>
                  </div>
                </div>
              </section>
              <section>
                <h4>重新授权</h4>
                <div className="task-action-panel">
                  <button className="ghost compact-button" disabled={resurrectionBusy !== null} type="button" onClick={generateAuthUrl}>
                    获取授权链接
                  </button>
                  <input
                    className="callback-url-input"
                    placeholder="粘贴 http://localhost:1455/auth/callback?... 回调 URL"
                    value={resurrectionWorkspace.callbackUrl}
                    onChange={(event) => setResurrectionWorkspace((current) => (current ? { ...current, callbackUrl: event.target.value } : current))}
                  />
                  <button className="ghost compact-button submit-revive-button" disabled={!resurrectionWorkspace.auth?.session_id || !resurrectionWorkspace.callbackUrl || resurrectionBusy !== null} type="button" onClick={submitOAuthCallbackAndRevive}>
                    <span>提交并复活</span>
                    交换 OAuth 凭证
                  </button>
                  <button hidden className="ghost compact-button" disabled={!resurrectionWorkspace.exchange || resurrectionBusy !== null} type="button" onClick={applyOAuthCredentials}>
                    应用凭证并复活
                  </button>
                  <button className="ghost compact-button danger-button" disabled={resurrectionBusy !== null} type="button" onClick={() => markResurrectionFailed(resurrectionWorkspace.account)}>
                    复活失败
                  </button>
                </div>
                {resurrectionWorkspace.auth?.auth_url && (
                  <div className="copyable-link-row">
                    <span className="copyable-link-label">授权链接</span>
                    <code className="copyable-link-value" title={resurrectionWorkspace.auth.auth_url}>
                      {compactUrl(resurrectionWorkspace.auth.auth_url)}
                    </code>
                    <button className="ghost compact-button" type="button" onClick={() => copyText(resurrectionWorkspace.auth?.auth_url || "", "授权链接已复制")}>
                      复制链接
                    </button>
                  </div>
                )}
                {resurrectionWorkspace.resurrectionResult && (
                  <div className={`resurrection-result ${resurrectionWorkspace.resurrectionResult.startsWith("复活成功") ? "success" : "danger"}`}>
                    {resurrectionWorkspace.resurrectionResult}
                  </div>
                )}
              </section>
                </div>
              </aside>
            </div>
          )}
        </section>
      )}

      {activePanel === "upgrade" && (
        <section className="panel">
        <div className="panel-header">
          <div>
            <h3>free 升级 plus</h3>
            <p>候选账号要求：当前为 free、有邮箱和接码 session，并且在总库或可用池。点击开始处理会加锁，其他用户不能同时处理。</p>
          </div>
          <div className="button-row">
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value as TaskStatus);
                setSkip(0);
              }}
            >
              <option value="open">待处理 + 处理中</option>
              <option value="pending">待处理</option>
              <option value="processing">处理中</option>
              <option value="completed">已完成</option>
              <option value="failed">失败</option>
              <option value="all">全部</option>
            </select>
            <label className="inline-select">
              <span>每页</span>
              <select
                value={limit}
                onChange={(event) => {
                  setLimit(Number(event.target.value));
                  setSkip(0);
                }}
              >
                <option value={50}>50</option>
                <option value={200}>200</option>
                <option value={500}>500</option>
              </select>
            </label>
          </div>
        </div>

        <>
            <CompactStats
              items={[
                ["待处理", stats.pending],
                ["处理中", stats.processing],
                ["已完成", stats.completed],
                ["失败", stats.failed],
              ]}
            />

            <div className="table-wrap account-table-wrap">
              <table className="account-table">
            <thead>
              <tr>
                <th>账号</th>
                <th>当前类型</th>
                <th>购买时类型</th>
                <th>来源</th>
                <th>本地状态</th>
                <th>支付类型</th>
                <th>任务状态</th>
                <th>处理人</th>
                <th>时间</th>
                <th>备注/错误</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => {
                const taskStatus = upgradeStatus(account);
                const lock = lockInfo(account);
                const isMine = lock.lockedByUserId && lock.lockedByUserId === currentUserId;
                const isLockedByOther = taskStatus === "processing" && !isMine;
                return (
                  <Fragment key={account.id}>
                  <tr>
                    <td>
                      <div className="cell-main">{accountEmail(account)}</div>
                      <div className="cell-sub">{text(account.account_json.name)}</div>
                    </td>
                    <td>{text(account.metadata.account_type) || "-"}</td>
                    <td>{text(account.metadata.purchase_account_type) || "-"}</td>
                    <td>{text(account.metadata.purchase_source) || <span className="muted">-</span>}</td>
                    <td>
                      <StatusPill value={poolStatusLabel(text(account.metadata.pool_status) || "library")} tone="muted" />
                    </td>
                    <td>{formatPayment(account.metadata.payment_type) || <span className="muted">未填写</span>}</td>
                    <td>
                      <StatusPill value={upgradeStatusLabel(taskStatus)} tone={upgradeStatusTone(taskStatus)} />
                    </td>
                    <td>
                      <div>
                        {text(account.metadata.upgrade_assignee_name) ? (
                          <>
                            <span className="muted">{taskStatus === "processing" ? "当前 " : "历史 "}</span>
                            {text(account.metadata.upgrade_assignee_name)}
                          </>
                        ) : (
                          <span className="muted">未领取</span>
                        )}
                      </div>
                      {lock.expiresAt && <div className="cell-sub">锁至 {formatDateTime(lock.expiresAt)}</div>}
                    </td>
                    <td>
                      <div className="cell-sub">创建 {formatDateTime(account.metadata.created_at)}</div>
                      <div className="cell-sub">更新 {formatDateTime(account.metadata.updated_at)}</div>
                    </td>
                    <td className="remark-cell">
                      {text(account.metadata.upgrade_error) || text(account.metadata.last_error) || text(account.metadata.remark) || <span className="muted">-</span>}
                    </td>
                    <td>
                      <div className="button-row action-wrap todo-action-wrap">
                        {(taskStatus === "pending" || taskStatus === "failed") && (
                          <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => runAccountAction(account, "start")} type="button">
                            开始处理
                          </button>
                        )}
                        {taskStatus === "processing" && isMine && (
                          <span className="muted">处理面板已展开</span>
                        )}
                        {isLockedByOther && (
                          <button className="ghost compact-button" disabled type="button">
                            他人处理中
                          </button>
                        )}
                        {taskStatus === "completed" && (
                          <>
                            <span className="muted">已自动进入可用池</span>
                            <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => runAccountAction(account, "return-processing")} type="button">
                              退回处理
                            </button>
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                  {taskStatus === "processing" && isMine && (
                    <tr className="task-detail-row">
                      <td colSpan={11}>
                        <div className="task-detail-panel">
                          <section>
                            <h4>登录信息</h4>
                            <LoginInfo account={account} />
                          </section>
                          <section>
                            <h4>处理动作</h4>
                            <div className="task-action-panel">
                              <label className="inline-select">
                                <span>升级后支付类型</span>
                                <select
                                  aria-label="升级后支付类型"
                                  value={paymentById[account.id] || "no_card"}
                                  onChange={(event) => setPaymentById((current) => ({ ...current, [account.id]: event.target.value }))}
                                >
                                  {paymentOptions.map(([value, label]) => (
                                    <option key={value} value={value}>
                                      {label}
                                    </option>
                                  ))}
                                </select>
                              </label>
                              <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => openEditAccount(account)} type="button">
                                编辑账号
                              </button>
                              <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => runAccountAction(account, "complete")} type="button">
                                完成升级
                              </button>
                              <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => runAccountAction(account, "release")} type="button">
                                取消处理
                              </button>
                              <button className="ghost compact-button danger-button" disabled={busyId === account.id} onClick={() => runAccountAction(account, "fail")} type="button">
                                标记失败
                              </button>
                            </div>
                          </section>
                        </div>
                      </td>
                    </tr>
                  )}
                  </Fragment>
                );
              })}
              {!accounts.length && (
                <tr>
                  <td className="muted" colSpan={11}>
                    暂无符合条件的 free 升 plus 待办
                  </td>
                </tr>
              )}
            </tbody>
              </table>
            </div>

            <div className="pagination">
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
          </>
        </section>
      )}
      {editingAccount && (
        <AccountEditPanel
          account={editingAccount}
          token={token}
          showToast={showToast}
          onClose={() => setEditingAccount(null)}
          onSaved={async () => {
            setEditingAccount(null);
            await loadAccounts();
            await loadProblemAccounts();
            await loadResurrectionAccounts();
          }}
        />
      )}
    </section>
  );
}

export function PushErrorTodoPage({ token, showToast }: Props) {
  const [accounts, setAccounts] = useState<AccountDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<PushErrorResponse["stats"]>({ pending: 0, processing: 0, archived: 0, resolved: 0, free: 0, team: 0, plus: 0 });
  const [status, setStatus] = useState<PushErrorStatus>("open");
  const [accountType, setAccountType] = useState<PushErrorAccountType>("plus");
  const [query, setQuery] = useState("");
  const [skip, setSkip] = useState(0);
  const [limit, setLimit] = useState(50);
  const [busyId, setBusyId] = useState<string | null>(null);

  const currentUserId = useMemo(() => {
    const raw = localStorage.getItem("user");
    if (!raw) return "";
    try {
      const parsed = JSON.parse(raw) as { id?: string; email?: string };
      return parsed.id || parsed.email || "";
    } catch {
      return "";
    }
  }, []);

  const loadAccounts = async () => {
    const params = new URLSearchParams({
      status,
      account_type: accountType,
      skip: String(skip),
      limit: String(limit),
    });
    if (query.trim()) params.set("q", query.trim());
    const data = await api<PushErrorResponse>(`/todo-items/push-errors/accounts?${params.toString()}`, token);
    setAccounts(data.items);
    setTotal(data.total);
    setStats(data.stats);
  };

  useEffect(() => {
    loadAccounts().catch((error) => showToast(errorMessage(error), true));
  }, [status, accountType, skip, limit]);

  const refresh = () => {
    if (skip === 0) loadAccounts().catch((error) => showToast(errorMessage(error), true));
    else setSkip(0);
  };

  const runPushErrorAction = async (account: AccountDocument, action: "start" | "release" | "test" | "plus_reprocess" | "problem_library") => {
    setBusyId(account.id);
    try {
      if (action === "test") {
        const result = await api<{ account: AccountDocument; verification: Record<string, unknown> }>(`/todo-items/push-errors/accounts/${account.id}/test`, token, {
          method: "POST",
          body: JSON.stringify({ model_id: "gpt-5.4-mini", prompt: "" }),
        });
        showToast(result.verification?.success === true ? "继续测试通过" : `继续测试失败：${text(result.verification?.error) || "请查看错误状态"}`, result.verification?.success !== true);
      } else if (action === "plus_reprocess" || action === "problem_library") {
        const note = window.prompt(action === "plus_reprocess" ? "备注：为什么进入 plus 重新处理待办" : "备注：为什么归档进问题库") || "";
        await api<AccountDocument>(`/todo-items/push-errors/accounts/${account.id}/decide`, token, {
          method: "POST",
          body: JSON.stringify({ decision: action, note }),
        });
        showToast(action === "plus_reprocess" ? "已加入 plus 重新处理待办" : "已归档进问题库");
      } else {
        await api<AccountDocument>(`/todo-items/push-errors/accounts/${account.id}/${action}`, token, { method: "POST" });
        showToast(action === "start" ? "已开始处理" : "已取消处理");
      }
      await loadAccounts();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="view accounts-page">
      <div className="topbar">
        <div>
          <h2>疑问账号分配面板</h2>
          <p>这里处理推送到使用池后测试失败的账号。当前先接入 401/token_expired 类型，后续可以继续追加其他错误类型。</p>
        </div>
        <div className="button-row">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索邮箱、错误、备注、处理人" />
          <button className="ghost" onClick={refresh} type="button">
            搜索/刷新
          </button>
        </div>
      </div>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h3>推送使用池错误</h3>
            <p>free 的 401 错误会自动归档；plus 需要人工继续测试后决定进入重新处理待办，或归档进问题库。</p>
          </div>
          <div className="button-row">
            <select
              value={accountType}
              onChange={(event) => {
                setAccountType(event.target.value as PushErrorAccountType);
                setSkip(0);
              }}
            >
              <option value="plus">plus</option>
              <option value="team">team子号</option>
              <option value="free">free</option>
              <option value="all">全部</option>
            </select>
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value as PushErrorStatus);
                setSkip(0);
              }}
            >
              <option value="open">待处理 + 处理中</option>
              <option value="pending">待处理</option>
              <option value="processing">处理中</option>
              <option value="archived">已归档</option>
              <option value="resolved">已转重新处理</option>
              <option value="all">全部</option>
            </select>
            <label className="inline-select">
              <span>每页</span>
              <select
                value={limit}
                onChange={(event) => {
                  setLimit(Number(event.target.value));
                  setSkip(0);
                }}
              >
                <option value={50}>50</option>
                <option value={200}>200</option>
                <option value={500}>500</option>
              </select>
            </label>
          </div>
        </div>

        <CompactStats
          items={[
            ["待处理", stats.pending],
            ["处理中", stats.processing],
            ["已归档", stats.archived],
            ["已转处理", stats.resolved],
            ["plus", stats.plus],
            ["team子号", stats.team],
            ["free", stats.free],
          ]}
        />

        <div className="table-wrap account-table-wrap">
          <table className="account-table">
            <thead>
              <tr>
                <th>账号</th>
                <th>类型</th>
                <th>错误状态</th>
                <th>远端信息</th>
                <th>本地状态</th>
                <th>处理人</th>
                <th>时间</th>
                <th>备注/错误</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((account) => {
                const taskStatus = pushErrorStatus(account);
                const lock = problemLockInfo(account);
                const isMine = lock.lockedByUserId && lock.lockedByUserId === currentUserId;
                const isLockedByOther = taskStatus === "processing" && !isMine;
                return (
                  <Fragment key={account.id}>
                    <tr>
                      <td>
                        <div className="cell-main">{accountEmail(account)}</div>
                        <div className="cell-sub">{text(account.account_json.name)}</div>
                      </td>
                      <td>{text(account.metadata.account_type) || "-"}</td>
                      <td>
                        <StatusPill value={pushErrorStatusLabel(taskStatus)} tone={pushErrorStatusTone(taskStatus)} />
                        <div className="cell-sub">{text(account.metadata.problem_class) || "-"}</div>
                        <div className="cell-sub">测试 {text(account.metadata.problem_last_test_status) || "-"}</div>
                      </td>
                      <td>
                        <div className="cell-sub">站点 {text(account.metadata.problem_site_id) || text(account.metadata.sub2api_site_id) || "-"}</div>
                        <div className="cell-sub">远端 #{text(account.metadata.problem_remote_account_id) || text(account.metadata.sub2api_account_id) || "-"}</div>
                        <div className="cell-sub">错误池 {text(account.metadata.problem_group_name) || text(account.metadata.problem_group_id) || "-"}</div>
                      </td>
                      <td>
                        <StatusPill value={poolStatusLabel(text(account.metadata.pool_status) || "problem")} tone="warning" />
                      </td>
                      <td>
                        <div>
                          {text(account.metadata.problem_assignee_name) ? (
                            <>
                              <span className="muted">{taskStatus === "processing" ? "当前 " : "历史 "}</span>
                              {text(account.metadata.problem_assignee_name)}
                            </>
                          ) : (
                            <span className="muted">未领取</span>
                          )}
                        </div>
                        {lock.expiresAt && <div className="cell-sub">锁至 {formatDateTime(lock.expiresAt)}</div>}
                      </td>
                      <td>
                        <div className="cell-sub">发现 {formatDateTime(account.metadata.problem_detected_at)}</div>
                        <div className="cell-sub">测试 {formatDateTime(account.metadata.problem_last_test_at)}</div>
                      </td>
                      <td className="remark-cell">
                        {text(account.metadata.problem_error) || text(account.metadata.problem_remark_zh) || text(account.metadata.last_error) || <span className="muted">-</span>}
                      </td>
                      <td>
                        <div className="button-row action-wrap todo-action-wrap">
                          {taskStatus === "pending" && (
                            <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => runPushErrorAction(account, "start")} type="button">
                              开始处理
                            </button>
                          )}
                          {taskStatus === "processing" && isMine && <span className="muted">处理面板已展开</span>}
                          {isLockedByOther && (
                            <button className="ghost compact-button" disabled type="button">
                              他人处理中
                            </button>
                          )}
                          {taskStatus !== "pending" && taskStatus !== "processing" && <span className="muted">{pushErrorResolution(account)}</span>}
                        </div>
                      </td>
                    </tr>
                    {taskStatus === "processing" && isMine && (
                      <tr className="task-detail-row">
                        <td colSpan={9}>
                          <div className="task-detail-panel">
                            <section>
                              <h4>错误信息</h4>
                              <div className="login-info">
                                <div>
                                  <span className="muted">错误类 </span>
                                  <span>{text(account.metadata.problem_class) || "-"}</span>
                                </div>
                                <div>
                                  <span className="muted">中文备注 </span>
                                  <span>{text(account.metadata.problem_remark_zh) || "-"}</span>
                                </div>
                                <div>
                                  <span className="muted">最后错误 </span>
                                  <span>{text(account.metadata.problem_last_test_error) || text(account.metadata.problem_error) || "-"}</span>
                                </div>
                              </div>
                            </section>
                            <section>
                              <h4>处理动作</h4>
                              <div className="task-action-panel">
                                <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => runPushErrorAction(account, "test")} type="button">
                                  继续测试账号
                                </button>
                                <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => runPushErrorAction(account, "plus_reprocess")} type="button">
                                  加入 plus 重新处理
                                </button>
                                <button className="ghost compact-button danger-button" disabled={busyId === account.id} onClick={() => runPushErrorAction(account, "problem_library")} type="button">
                                  进问题库
                                </button>
                                <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => runPushErrorAction(account, "release")} type="button">
                                  取消处理
                                </button>
                              </div>
                            </section>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {!accounts.length && (
                <tr>
                  <td className="muted" colSpan={9}>
                    暂无推送错误账号
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="pagination">
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
    </section>
  );
}

function LoginInfo({ account }: { account: AccountDocument }) {
  const info = accountLoginInfo(account);
  return (
    <div className="login-info">
      <div>
        <span className="muted">邮箱 </span>
        <span>{info.email || "-"}</span>
      </div>
      <div>
        <span className="muted">session </span>
        <span>{info.emailSession || "-"}</span>
      </div>
      <div>
        <span className="muted">2FA </span>
        <span>{info.twoFA || "-"}</span>
      </div>
      {info.password && (
        <div>
          <span className="muted">密码 </span>
          <span>{info.password}</span>
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

function StatusPill({ value, tone = "muted" }: { value: string; tone?: "accent" | "success" | "warning" | "danger" | "muted" }) {
  return <span className={`status-pill ${tone}`}>{value}</span>;
}

function LoginInfoBlock({ items }: { items: Array<[string, string | undefined]> }) {
  return (
    <div className="login-info">
      {items.map(([label, value]) => (
        <div key={label}>
          <span>{label}</span>
          <code>{value || "-"}</code>
        </div>
      ))}
    </div>
  );
}

function CopyFieldList({
  copyText,
  items,
}: {
  copyText: (value: string, message: string) => Promise<void>;
  items: Array<[string, string | undefined]>;
}) {
  return (
    <div className="copy-field-list">
      {items.map(([label, value]) => {
        const displayValue = value || "-";
        return (
          <div className="copy-field" key={label}>
            <div className="copy-field-label">{label}</div>
            <div className="copy-field-value" title={displayValue}>{displayValue}</div>
            <button className="ghost compact-button" type="button" disabled={!value} onClick={() => value && copyText(value, `${label}已复制`)}>
              复制
            </button>
          </div>
        );
      })}
    </div>
  );
}

async function copyToClipboard(value: string) {
  if (!value) return;
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value);
      return;
    } catch {
      // Fall through to the textarea fallback for plain HTTP deployments.
    }
  }
  const textarea = document.createElement("textarea");
  textarea.value = value;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  textarea.style.top = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  const copied = document.execCommand("copy");
  document.body.removeChild(textarea);
  if (!copied) throw new Error("浏览器阻止了复制");
}

function isUploadedByCurrentUser(account: AccountDocument, currentUserId: string) {
  if (!currentUserId) return false;
  return text(account.metadata.uploaded_by_user_id) === currentUserId;
}

function sortMineFirst(accounts: AccountDocument[], currentUserId: string) {
  return [...accounts].sort((left, right) => {
    const leftMine = isUploadedByCurrentUser(left, currentUserId) ? 1 : 0;
    const rightMine = isUploadedByCurrentUser(right, currentUserId) ? 1 : 0;
    if (leftMine !== rightMine) return rightMine - leftMine;
    return 0;
  });
}

function isRemoteUploadedByCurrentUser(account: RemoteResurrectionAccount, currentUserId: string) {
  return Boolean(currentUserId) && text(account.uploaded_by_user_id) === currentUserId;
}

function accountEmail(account: AccountDocument) {
  const credentials = asRecord(account.account_json.credentials);
  return text(account.metadata.email) || text(credentials.email) || text(account.account_json.name) || "未识别邮箱";
}

function accountLoginInfo(account: AccountDocument) {
  const credentials = asRecord(account.account_json.credentials);
  const extra = asRecord(account.account_json.extra);
  return {
    email: text(account.metadata.email) || text(credentials.email) || text(extra.email) || text(account.account_json.name),
    emailSession: text(account.metadata.email_session) || text(extra.email_session) || text(extra.mailbox_connection),
    twoFA: text(account.metadata["2FA"]) || text(extra["2FA"]),
    password: text(extra.password),
  };
}

function emptyResurrectionEditFields(): ResurrectionEditFields {
  return {
    email_session: "",
    twoFA: "",
    phone_bound: "true",
    phone_number: "",
    remark: "",
    manual_status_label: "",
  };
}

function buildResurrectionEditFields(account: RemoteResurrectionAccount): ResurrectionEditFields {
  return {
    email_session: emailSessionValue(account),
    twoFA: twoFaDisplayValue(account),
    phone_bound: (account.local_phone_bound ?? account.extra?.phone_bound) === false ? "false" : "true",
    phone_number: phoneRawValue(account),
    remark: text(account.extra?.remark),
    manual_status_label: text(account.extra?.manual_status_label),
  };
}

function buildResurrectionEditMetadata(fields: ResurrectionEditFields) {
  return {
    email_session: fields.email_session,
    "2FA": fields.twoFA,
    phone_bound: fields.phone_bound === "true",
    phone_number: fields.phone_number,
    remark: fields.remark,
    manual_status_label: fields.manual_status_label,
    source: "resurrection_edit",
  };
}

function isResurrectionCandidate(account: RemoteResurrectionAccount, query: string) {
  const plan = (text(account.plan_type) || text(account.credentials?.plan_type) || text(account.extra?.account_type)).toLowerCase();
  const used7d = numberValue(account.codex_7d_used_percent ?? account.extra?.codex_7d_used_percent);
  const status = text(account.status).toLowerCase();
  const hasError =
    status !== "active" ||
    account.schedulable === false ||
    Boolean(text(account.error_message)) ||
    Boolean(text(account.extra?.last_error)) ||
    Boolean(text(account.extra?.temp_unschedulable_reason));
  const haystack = [
    remoteAccountEmail(account),
    account.name,
    account.pool_name,
    account.error_message,
    account.extra?.last_error,
  ].map((value) => text(value).toLowerCase()).join(" ");
  return plan === "plus" && used7d < 100 && hasError && (!query.trim() || haystack.includes(query.trim().toLowerCase()));
}

function isResurrectionPoolGroup(group: Sub2ApiGroup) {
  const status = text(group.status).toLowerCase();
  if (status === "deleted" || status === "disabled") return false;
  const name = text(group.name).toLowerCase();
  const excluded = ["问题", "错误", "验证", "备选", "备用", "problem", "error", "verify", "verification", "reserve", "test"];
  return !excluded.some((keyword) => name.includes(keyword));
}

function remoteAccountEmail(account: RemoteResurrectionAccount) {
  return text(account.credentials?.email) || text(account.extra?.email) || text(account.name) || `#${account.id}`;
}

function resolvePoolSiteId(siteId: string, activeSiteIds: Set<string>, fallbackSiteId: string) {
  const normalized = text(siteId);
  if (normalized && activeSiteIds.has(normalized)) return normalized;
  if (fallbackSiteId && (!normalized || normalized === "default" || !activeSiteIds.has(normalized))) return fallbackSiteId;
  return "";
}

function remoteAccountStatusLabel(account: RemoteResurrectionAccount) {
  if (account.schedulable === false) return "调度关闭";
  if (text(account.error_message)) return "错误";
  return text(account.status) || "unknown";
}

function remoteAccountStatusTone(account: RemoteResurrectionAccount): "accent" | "success" | "warning" | "danger" | "muted" {
  if (text(account.error_message)) return "danger";
  if (account.schedulable === false) return "warning";
  if (text(account.status).toLowerCase() === "active") return "success";
  return "muted";
}

function remoteFailureTime(account: RemoteResurrectionAccount) {
  const extra = asRecord(account.extra);
  return (
    text(extra.error_at) ||
    text(extra.last_error_at) ||
    text(extra.failed_at) ||
    text(extra.problem_detected_at) ||
    text(extra.problem_last_test_at) ||
    text(account.codex_remote_tested_at) ||
    text(extra.codex_remote_tested_at) ||
    text(account.updated_at) ||
    text(extra.updated_at) ||
    text(account.codex_usage_updated_at) ||
    text(extra.codex_usage_updated_at) ||
    text(account.last_used_at) ||
    text(account.rate_limited_at) ||
    text(account.temp_unschedulable_until)
  );
}

function phoneCodeUrl(account?: RemoteResurrectionAccount | null) {
  return phoneInfo(account).url;
}

function emailSessionValue(account?: RemoteResurrectionAccount | null) {
  return text(account?.extra?.email_session) || text(account?.extra?.mailbox_connection) || text(account?.local_email_session);
}

function phoneRawValue(account?: RemoteResurrectionAccount | null) {
  return text(account?.extra?.phone_number) || text(account?.extra?.phone) || text(account?.local_phone_number);
}

function phoneInfo(account?: RemoteResurrectionAccount | null): { status: "valid" | "missing" | "invalid"; url: string; message: string } {
  const raw = phoneRawValue(account);
  if (!raw) return { status: "missing", url: "", message: "无手机信息，请及时补充" };
  const match = raw.match(/https?:\/\/[^\s]+/i);
  if (!match?.[0]) return { status: "invalid", url: "", message: "手机信息填写错误，请及时修改" };
  try {
    const url = new URL(match[0]);
    if (!url.hostname.endsWith("cdc.smslease.link") || !url.searchParams.get("key")) {
      return { status: "invalid", url: "", message: "手机接码地址格式错误，请及时修改" };
    }
    return { status: "valid", url: url.toString(), message: "" };
  } catch {
    return { status: "invalid", url: "", message: "手机接码地址无法请求，请及时修改" };
  }
}

function twoFaDisplayValue(account?: RemoteResurrectionAccount | null) {
  return rawTwoFaValue(account);
}

function twoFaInfo(account?: RemoteResurrectionAccount | null): { status: "valid" | "missing" | "invalid"; value: string; message: string } {
  const raw = normalizeTotpSecret(rawTwoFaValue(account));
  if (!raw) return { status: "missing", value: "", message: "无2FA信息，请及时补充" };
  if (!isValidBase32Secret(raw)) return { status: "invalid", value: raw, message: "2FA信息填写错误，请及时修改" };
  return { status: "valid", value: raw, message: "" };
}

function rawTwoFaValue(account?: RemoteResurrectionAccount | null) {
  const extra = account?.extra || {};
  return (
    text(extra["2FA"]) ||
    text(extra["2fa"]) ||
    text(extra.twoFA) ||
    text(extra.totp_secret) ||
    text(extra.totpSecret) ||
    text(account?.local_two_fa)
  );
}

function normalizeTotpSecret(value: string) {
  const raw = value.trim();
  if (!raw) return "";
  if (raw.toLowerCase().startsWith("otpauth://")) {
    try {
      const url = new URL(raw);
      return (url.searchParams.get("secret") || "").replace(/[\s-]+/g, "").toUpperCase();
    } catch {
      return "";
    }
  }
  const secretMatch = raw.match(/(?:secret|totp_secret)\s*[:=]\s*([A-Z2-7=\s-]+)/i);
  const source = secretMatch?.[1] || raw;
  return source.replace(/[\s-]+/g, "").replace(/^"|"$/g, "").toUpperCase();
}

function extractVerificationCode(value?: string) {
  return text(value).match(/\b\d{6}\b/)?.[0] || "";
}

function compactUrl(value: string) {
  if (value.length <= 92) return value;
  return `${value.slice(0, 54)}...${value.slice(-32)}`;
}

function normalizeAuthSession(value: AuthSession): AuthSession {
  const data = asRecord(value.data);
  return {
    auth_url: text(value.auth_url) || text(data.auth_url),
    session_id: text(value.session_id) || text(data.session_id),
  };
}

function numberValue(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const parsed = Number(text(value));
  return Number.isFinite(parsed) ? parsed : 0;
}

function safeGenerateTotp(secret: string): { code: string; seconds: number } | { code?: undefined; seconds?: undefined; error: string } {
  try {
    return generateTotp(secret);
  } catch (error) {
    return { code: undefined, seconds: undefined, error: errorMessage(error) };
  }
}

function oauthCredentialsFromExchange(exchange: Record<string, unknown>) {
  const data = asRecord(exchange.data);
  const source = Object.keys(data).length ? data : exchange;
  return {
    access_token: text(source.access_token),
    refresh_token: text(source.refresh_token),
    id_token: text(source.id_token),
    expires_at: numberValue(source.expires_at),
    chatgpt_account_id: text(source.chatgpt_account_id),
    chatgpt_user_id: text(source.chatgpt_user_id),
    organization_id: text(source.organization_id),
    plan_type: text(source.plan_type) || "plus",
    email: text(source.email),
    client_id: text(source.client_id),
  };
}

function redactOAuthPreview(exchange: Record<string, unknown>) {
  const credentials = oauthCredentialsFromExchange(exchange);
  return {
    ...credentials,
    access_token: credentials.access_token ? `${credentials.access_token.slice(0, 24)}...` : "",
    refresh_token: credentials.refresh_token ? `${credentials.refresh_token.slice(0, 16)}...` : "",
    id_token: credentials.id_token ? `${credentials.id_token.slice(0, 24)}...` : "",
  };
}

function generateTotp(secret: string) {
  if (!isValidBase32Secret(secret)) throw new Error("2FA信息填写错误，请及时修改");
  const key = base32Decode(secret.replace(/\s+/g, ""));
  const epoch = Math.floor(Date.now() / 1000);
  const counter = Math.floor(epoch / 30);
  const counterBytes = new Uint8Array(8);
  const view = new DataView(counterBytes.buffer);
  view.setUint32(4, counter, false);
  const signature = hmacSha1(key, counterBytes);
  const offset = signature[signature.length - 1] & 0x0f;
  const binary =
    ((signature[offset] & 0x7f) << 24) |
    ((signature[offset + 1] & 0xff) << 16) |
    ((signature[offset + 2] & 0xff) << 8) |
    (signature[offset + 3] & 0xff);
  return { code: String(binary % 1_000_000).padStart(6, "0"), seconds: 30 - (epoch % 30) };
}

function hmacSha1(key: Uint8Array, message: Uint8Array) {
  const blockSize = 64;
  let normalizedKey = key;
  if (normalizedKey.length > blockSize) normalizedKey = sha1(normalizedKey);
  const inner = new Uint8Array(blockSize);
  const outer = new Uint8Array(blockSize);
  for (let i = 0; i < blockSize; i += 1) {
    const value = normalizedKey[i] || 0;
    inner[i] = value ^ 0x36;
    outer[i] = value ^ 0x5c;
  }
  const innerMessage = new Uint8Array(inner.length + message.length);
  innerMessage.set(inner);
  innerMessage.set(message, inner.length);
  const innerHash = sha1(innerMessage);
  const outerMessage = new Uint8Array(outer.length + innerHash.length);
  outerMessage.set(outer);
  outerMessage.set(innerHash, outer.length);
  return sha1(outerMessage);
}

function sha1(message: Uint8Array) {
  const bitLength = message.length * 8;
  const paddedLength = (((message.length + 9 + 63) >> 6) << 6);
  const padded = new Uint8Array(paddedLength);
  padded.set(message);
  padded[message.length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(paddedLength - 4, bitLength, false);

  let h0 = 0x67452301;
  let h1 = 0xefcdab89;
  let h2 = 0x98badcfe;
  let h3 = 0x10325476;
  let h4 = 0xc3d2e1f0;
  const words = new Uint32Array(80);

  for (let offset = 0; offset < paddedLength; offset += 64) {
    for (let i = 0; i < 16; i += 1) words[i] = view.getUint32(offset + i * 4, false);
    for (let i = 16; i < 80; i += 1) words[i] = rotl(words[i - 3] ^ words[i - 8] ^ words[i - 14] ^ words[i - 16], 1);

    let a = h0;
    let b = h1;
    let c = h2;
    let d = h3;
    let e = h4;

    for (let i = 0; i < 80; i += 1) {
      let f = 0;
      let k = 0;
      if (i < 20) {
        f = (b & c) | (~b & d);
        k = 0x5a827999;
      } else if (i < 40) {
        f = b ^ c ^ d;
        k = 0x6ed9eba1;
      } else if (i < 60) {
        f = (b & c) | (b & d) | (c & d);
        k = 0x8f1bbcdc;
      } else {
        f = b ^ c ^ d;
        k = 0xca62c1d6;
      }
      const temp = (rotl(a, 5) + f + e + k + words[i]) >>> 0;
      e = d;
      d = c;
      c = rotl(b, 30);
      b = a;
      a = temp;
    }

    h0 = (h0 + a) >>> 0;
    h1 = (h1 + b) >>> 0;
    h2 = (h2 + c) >>> 0;
    h3 = (h3 + d) >>> 0;
    h4 = (h4 + e) >>> 0;
  }

  const digest = new Uint8Array(20);
  const digestView = new DataView(digest.buffer);
  [h0, h1, h2, h3, h4].forEach((value, index) => digestView.setUint32(index * 4, value, false));
  return digest;
}

function rotl(value: number, bits: number) {
  return ((value << bits) | (value >>> (32 - bits))) >>> 0;
}

function base32Decode(value: string) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  value.toUpperCase().replace(/=+$/g, "").split("").forEach((char) => {
    const index = alphabet.indexOf(char);
    if (index >= 0) bits += index.toString(2).padStart(5, "0");
  });
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) {
    bytes.push(parseInt(bits.slice(i, i + 8), 2));
  }
  return new Uint8Array(bytes);
}

function isValidBase32Secret(value: string) {
  const normalized = value.replace(/\s+/g, "").replace(/=+$/g, "").toUpperCase();
  if (normalized.length < 16) return false;
  if (!/^[A-Z2-7]+$/.test(normalized)) return false;
  return base32Decode(normalized).length >= 10;
}

function upgradeStatus(account: AccountDocument) {
  return text(account.metadata.upgrade_status) || "pending";
}

function upgradeStatusLabel(value: string) {
  const labels: Record<string, string> = {
    pending: "待处理",
    processing: "处理中",
    completed: "已完成",
    failed: "失败",
  };
  return labels[value] || value;
}

function upgradeStatusTone(value: string): "accent" | "success" | "warning" | "danger" | "muted" {
  if (value === "processing") return "accent";
  if (value === "completed") return "success";
  if (value === "failed") return "danger";
  return "muted";
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
  if (value === "available" || value === "active") return "success";
  if (value === "reserve") return "accent";
  if (value === "problem") return "warning";
  if (value === "discarded") return "danger";
  return "muted";
}

function lockInfo(account: AccountDocument) {
  const lock = asRecord(account.metadata.upgrade_lock);
  return {
    lockedByUserId: text(lock.locked_by_user_id),
    lockedByName: text(lock.locked_by_name),
    expiresAt: text(lock.expires_at),
  };
}

function problemLockInfo(account: AccountDocument) {
  const lock = asRecord(account.metadata.problem_lock);
  return {
    lockedByUserId: text(lock.locked_by_user_id),
    lockedByName: text(lock.locked_by_name),
    expiresAt: text(lock.expires_at),
  };
}

function pushErrorStatus(account: AccountDocument) {
  return text(account.metadata.problem_task_status) || "pending";
}

function pushErrorStatusLabel(value: string) {
  const labels: Record<string, string> = {
    pending: "待处理",
    processing: "处理中",
    archived: "已归档",
    resolved: "已转处理",
  };
  return labels[value] || value;
}

function pushErrorStatusTone(value: string): "accent" | "success" | "warning" | "danger" | "muted" {
  if (value === "processing") return "accent";
  if (value === "resolved") return "success";
  if (value === "archived") return "muted";
  return "danger";
}

function pushErrorResolution(account: AccountDocument) {
  const resolution = text(account.metadata.problem_resolution);
  const labels: Record<string, string> = {
    free_auto_archived: "free 已自动归档",
    plus_reprocess: "已加入 plus 重新处理",
    problem_library: "已进问题库",
  };
  return labels[resolution] || resolution || "已处理";
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}
