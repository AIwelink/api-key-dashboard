import { FormEvent, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import type { AccountDocument, AccountType, PoolStatus } from "../types";
import { errorMessage, formatDateTime, formatPayment, pretty, text } from "../utils/format";
import { parseLooseJsonLocal } from "../utils/jsonParser";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type AccountListResponse = {
  items: AccountDocument[];
  total: number;
  skip: number;
  limit: number;
};

type Filters = {
  q: string;
  account_type: string;
  payment_type: string;
  pool_status: string;
  sort_by: string;
  sort_dir: "asc" | "desc";
  limit: number;
};

type AccountScope = "normal" | "problem" | "archived";

type EditFields = {
  email_session: string;
  account_type: AccountType;
  payment_type: "paypal_multi" | "paypal_single" | "no_card" | "gopay" | "other";
  twoFA: string;
  self_produced: "true" | "false";
  purchase_source: string;
  purchase_account_type: AccountType | "";
  phone_bound: "true" | "false";
  phone_number: string;
  remark: string;
  manual_status_label: string;
  account_json: string;
};

type ConfirmState = {
  confirmText?: string;
  details?: Array<[string, string | number | null | undefined]>;
  message?: string;
  onConfirm: () => void;
  title: string;
  tone?: "default" | "danger";
};

const initialFilters: Filters = {
  q: "",
  account_type: "",
  payment_type: "",
  pool_status: "",
  sort_by: "updated_at",
  sort_dir: "desc",
  limit: 50,
};

export function AccountsPage({ token, showToast }: Props) {
  const [accounts, setAccounts] = useState<AccountDocument[]>([]);
  const [filters, setFilters] = useState<Filters>(initialFilters);
  const [accountScope, setAccountScope] = useState<AccountScope>("normal");
  const [draftQuery, setDraftQuery] = useState(initialFilters.q);
  const [skip, setSkip] = useState(0);
  const [total, setTotal] = useState(0);
  const [editingAccount, setEditingAccount] = useState<AccountDocument | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(() => new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);

  const summary = useMemo(() => summarize(accounts), [accounts]);
  const selectedOnPage = useMemo(() => accounts.filter((account) => selectedIds.has(account.id)), [accounts, selectedIds]);
  const allPageSelected = accounts.length > 0 && selectedOnPage.length === accounts.length;
  const somePageSelected = selectedOnPage.length > 0 && !allPageSelected;

  const loadAccounts = async () => {
    const params = new URLSearchParams();
    Object.entries(filters).forEach(([key, value]) => {
      if (value !== "" && value !== undefined && value !== null) params.set(key, String(value));
    });
    params.set("account_scope", accountScope);
    params.set("skip", String(skip));
    const data = await api<AccountListResponse>(`/accounts?${params.toString()}`, token);
    setAccounts(data.items);
    setTotal(data.total);
  };

  useEffect(() => {
    loadAccounts().catch((error) => showToast(errorMessage(error), true));
  }, [filters, skip, accountScope]);

  useEffect(() => {
    const pageIds = new Set(accounts.map((account) => account.id));
    setSelectedIds((current) => new Set([...current].filter((id) => pageIds.has(id))));
  }, [accounts]);

  const setFilter = <K extends keyof Filters>(key: K, value: Filters[K]) => {
    setSkip(0);
    setFilters((current) => ({ ...current, [key]: value }));
  };

  const selectAccountScope = (nextScope: AccountScope) => {
    setAccountScope(nextScope);
    setSkip(0);
    setSelectedIds(new Set());
    setFilters((current) => {
      if (nextScope === "problem") return { ...current, pool_status: "", sort_by: "last_operation_at", sort_dir: "desc" };
      if (nextScope === "archived") return { ...current, pool_status: "", sort_by: "last_operation_at", sort_dir: "desc" };
      if (current.pool_status === "problem" || current.pool_status === "discarded") return { ...current, pool_status: "" };
      return current;
    });
  };

  const applySearch = () => {
    setFilter("q", draftQuery.trim());
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

  const bulkTransfer = async (targetStatus: PoolStatus, label: string, extra: Record<string, unknown> = {}) => {
    if (!selectedOnPage.length) {
      showToast("请先选择账号", true);
      return;
    }
    setBulkBusy(true);
    try {
      await Promise.all(
        selectedOnPage.map((account) =>
          api<AccountDocument>(`/accounts/${account.id}/manual-transfer`, token, {
            method: "POST",
            body: JSON.stringify({
              target_status: targetStatus,
              reason: label,
              ...extra,
            }),
          }),
        ),
      );
      showToast(`${label}：${selectedOnPage.length} 个账号`);
      setSelectedIds(new Set());
      await loadAccounts();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBulkBusy(false);
    }
  };

  const refreshAfterRemoval = async (removedCount: number) => {
    if (accounts.length <= removedCount && skip > 0) {
      setSkip(Math.max(0, skip - filters.limit));
      return;
    }
    await loadAccounts();
  };

  const performDeleteAccount = async (account: AccountDocument) => {
    setBusyId(account.id);
    try {
      await api<null>(`/accounts/${account.id}`, token, { method: "DELETE" });
      showToast("账号已删除");
      setSelectedIds((current) => {
        const next = new Set(current);
        next.delete(account.id);
        return next;
      });
      await refreshAfterRemoval(1);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusyId(null);
    }
  };

  const deleteAccount = (account: AccountDocument) => {
    setConfirmState({
      title: "确认删除账号",
      message: "删除后不会出现在账号列表和导出结果中。",
      details: [["账号", accountEmail(account)]],
      confirmText: "删除账号",
      tone: "danger",
      onConfirm: () => performDeleteAccount(account),
    });
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

  const performBulkDelete = async () => {
    setBulkBusy(true);
    try {
      await Promise.all(selectedOnPage.map((account) => api<null>(`/accounts/${account.id}`, token, { method: "DELETE" })));
      showToast(`已删除 ${selectedOnPage.length} 个账号`);
      const removedCount = selectedOnPage.length;
      setSelectedIds(new Set());
      await refreshAfterRemoval(removedCount);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBulkBusy(false);
    }
  };

  const bulkDelete = () => {
    if (!selectedOnPage.length) {
      showToast("请先选择账号", true);
      return;
    }
    setConfirmState({
      title: "确认批量删除",
      message: "删除后不会出现在账号列表和导出结果中。",
      details: [["账号数量", selectedOnPage.length]],
      confirmText: "批量删除",
      tone: "danger",
      onConfirm: performBulkDelete,
    });
  };

  return (
    <section className="view accounts-page">
      <div className="topbar">
        <div>
          <h2>{accountScope === "problem" ? "问题账号" : accountScope === "archived" ? "归档账号" : "正常账号"}</h2>
          <p>账号总库负责筛选、查看和编辑。账号进入可用池、问题账号、弃用等状态都由人工按钮触发。</p>
        </div>
        <div className="button-row">
          <div className="account-view-menu" aria-label="账号列表视图">
            <button
              className={`account-view-menu-item ${accountScope === "normal" ? "active" : ""}`}
              onClick={() => selectAccountScope("normal")}
              type="button"
            >
              <strong>正常账号</strong>
              <span>不含问题和弃用</span>
            </button>
            <button
              className={`account-view-menu-item ${accountScope === "problem" ? "active" : ""}`}
              onClick={() => selectAccountScope("problem")}
              type="button"
            >
              <strong>问题账号</strong>
              <span>只查看 problem</span>
            </button>
            <button
              className={`account-view-menu-item ${accountScope === "archived" ? "active" : ""}`}
              onClick={() => selectAccountScope("archived")}
              type="button"
            >
              <strong>归档账号</strong>
              <span>只查看 discarded</span>
            </button>
          </div>
          <button className="ghost" onClick={() => loadAccounts().catch((error) => showToast(errorMessage(error), true))} type="button">
            刷新
          </button>
        </div>
      </div>

      <CompactStats
        items={[
          ["当前结果", `${accounts.length} / ${total}`],
          ["总库", summary.library],
          ["可用池", summary.available],
          ["问题账号", summary.problem],
          ["归档账号", summary.discarded],
        ]}
      />

      <section className="panel filter-panel">
        <div className="filter-grid">
          <label className="span-2">
            <span className="field-label">
              <strong>搜索</strong>
            </span>
            <input
              value={draftQuery}
              onChange={(event) => setDraftQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") applySearch();
              }}
              placeholder="邮箱、上传人、备注、状态标注"
            />
          </label>
          <label>
            <span className="field-label">
              <strong>账号类型</strong>
            </span>
            <select value={filters.account_type} onChange={(event) => setFilter("account_type", event.target.value)}>
              <option value="">全部</option>
              <option value="plus">plus</option>
              <option value="team">team子号</option>
              <option value="free">free</option>
              <option value="pro">pro</option>
              <option value="other">其他</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>支付类型</strong>
            </span>
            <select value={filters.payment_type} onChange={(event) => setFilter("payment_type", event.target.value)}>
              <option value="">全部</option>
              <option value="paypal_multi">PayPal 一卡多号</option>
              <option value="paypal_single">PayPal 一卡一号</option>
              <option value="no_card">不绑卡</option>
              <option value="gopay">gopay</option>
              <option value="other">其他</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>本地状态</strong>
            </span>
            <select
              value={accountScope === "problem" ? "problem" : accountScope === "archived" ? "discarded" : filters.pool_status}
              disabled={accountScope !== "normal"}
              onChange={(event) => setFilter("pool_status", event.target.value)}
            >
              <option value="">全部</option>
              <option value="library">总库</option>
              <option value="available">可用池</option>
              <option value="reserve">使用备选池</option>
              <option value="active">实际使用池</option>
              {accountScope === "problem" && <option value="problem">问题账号</option>}
              {accountScope === "archived" && <option value="discarded">归档账号</option>}
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>排序</strong>
            </span>
            <select value={filters.sort_by} onChange={(event) => setFilter("sort_by", event.target.value)}>
              <option value="updated_at">更新时间</option>
              <option value="created_at">创建时间</option>
              <option value="email">邮箱</option>
              <option value="account_type">账号类型</option>
              <option value="payment_type">支付类型</option>
              <option value="pool_status">本地状态</option>
              <option value="priority">优先级</option>
              <option value="last_operation_at">最后操作时间</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>方向</strong>
            </span>
            <select value={filters.sort_dir} onChange={(event) => setFilter("sort_dir", event.target.value as Filters["sort_dir"])}>
              <option value="desc">降序</option>
              <option value="asc">升序</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>每页</strong>
            </span>
            <select value={filters.limit} onChange={(event) => setFilter("limit", Number(event.target.value))}>
              <option value={50}>50</option>
              <option value={200}>200</option>
              <option value={500}>500</option>
            </select>
          </label>
          <div className="filter-actions">
            <button
              className="ghost"
              onClick={() => {
                setSkip(0);
                setFilters(initialFilters);
                setDraftQuery(initialFilters.q);
              }}
              type="button"
            >
              重置
            </button>
            <button className="ghost" onClick={applySearch} type="button">
              搜索
            </button>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-header">
          <h3>账号</h3>
          <span className="muted">
            {total ? skip + 1 : 0}-{Math.min(skip + filters.limit, total)} / {total}
          </span>
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
            <button
              className="ghost compact-button"
              disabled={bulkBusy || !selectedOnPage.length}
              onClick={() => bulkTransfer("available", "已批量移入可用池")}
              type="button"
            >
              移入可用池
            </button>
            <button
              className="ghost compact-button"
              disabled={bulkBusy || !selectedOnPage.length}
              onClick={() => bulkTransfer("library", "已批量退回总库")}
              type="button"
            >
              退回总库
            </button>
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
            <button className="ghost compact-button danger-button" disabled={bulkBusy || !selectedOnPage.length} onClick={bulkDelete} type="button">
              删除所选
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
                <th>是否自产</th>
                <th>购买来源</th>
                <th>支付</th>
                <th>绑卡</th>
                <th>使用过</th>
                <th>本地状态</th>
                <th>时间</th>
                <th>上传 / 修改 / 操作</th>
                <th>备注</th>
                <th>操作</th>
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
                  <td>
                    <StatusPill value={selfProducedLabel(account.metadata.self_produced)} tone={selfProducedTone(account.metadata.self_produced)} />
                  </td>
                  <td>{text(account.metadata.purchase_source) || <span className="muted">-</span>}</td>
                  <td>{formatPayment(account.metadata.payment_type) || <span className="muted">未填写</span>}</td>
                  <td>
                    <StatusPill value={cardBindingLabel(account.metadata.payment_type)} tone={cardBindingTone(account.metadata.payment_type)} />
                  </td>
                  <td>
                    <StatusPill value={usedLabel(account)} tone={usedTone(account)} />
                  </td>
                  <td>
                    <StatusPill value={poolStatusLabel(text(account.metadata.pool_status) || "library")} tone={poolStatusTone(text(account.metadata.pool_status) || "library")} />
                    {text(account.metadata.last_error) && <div className="cell-sub danger">{text(account.metadata.last_error)}</div>}
                  </td>
                  <td>
                    <div className="cell-sub">创建 {formatDateTime(account.metadata.created_at)}</div>
                    <div className="cell-sub">更新 {formatDateTime(account.metadata.updated_at)}</div>
                  </td>
                  <td>
                    <div>{text(account.metadata.uploader_name) || <span className="muted">未知</span>}</div>
                    <div className="cell-sub">修改 {text(account.metadata.updated_by_name) || "-"}</div>
                    <div className="cell-sub">操作 {text(account.metadata.last_operation_by_name) || "-"}</div>
                    {text(account.metadata.last_operation_name) && (
                      <div className="cell-sub">
                        {text(account.metadata.last_operation_name)}
                        {text(account.metadata.last_operation_at) ? ` · ${formatDateTime(account.metadata.last_operation_at)}` : ""}
                      </div>
                    )}
                  </td>
                  <td className="remark-cell">{text(account.metadata.remark) || <span className="muted">-</span>}</td>
                  <td>
                    <div className="button-row action-wrap">
                      <button className="ghost compact-button" disabled={busyId === account.id} onClick={() => openEditAccount(account)} type="button">
                        编辑
                      </button>
                      <button
                        className="ghost compact-button danger-button"
                        disabled={busyId === account.id}
                        onClick={() => deleteAccount(account)}
                        type="button"
                      >
                        删除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
              {!accounts.length && (
                <tr>
                  <td className="muted" colSpan={13}>
                    暂无账号
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="pagination">
          <label className="inline-select">
            <span>每页</span>
            <select value={filters.limit} onChange={(event) => setFilter("limit", Number(event.target.value))}>
              <option value={50}>50</option>
              <option value={200}>200</option>
              <option value={500}>500</option>
            </select>
          </label>
          <button className="ghost" type="button" disabled={skip <= 0} onClick={() => setSkip(Math.max(0, skip - filters.limit))}>
            上一页
          </button>
          <button className="ghost" type="button" disabled={skip + filters.limit >= total} onClick={() => setSkip(skip + filters.limit)}>
            下一页
          </button>
        </div>
      </section>

      {editingAccount && (
        <AccountEditPanel
          account={editingAccount}
          token={token}
          showToast={showToast}
          onClose={() => setEditingAccount(null)}
          onSaved={async () => {
            setEditingAccount(null);
            await loadAccounts();
          }}
        />
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
        open={confirmState !== null}
        title={confirmState?.title || ""}
        tone={confirmState?.tone}
      />
    </section>
  );
}

function AccountEditPanel({
  account,
  token,
  showToast,
  onClose,
  onSaved,
}: {
  account: AccountDocument;
  token: string;
  showToast: Props["showToast"];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [fields, setFields] = useState<EditFields>(() => buildEditFields(account));
  const [saving, setSaving] = useState(false);
  const [refreshJson, setRefreshJson] = useState("");
  const [refreshingJson, setRefreshingJson] = useState(false);
  const canRefreshCredentials = true;

  const setField = <K extends keyof EditFields>(key: K, value: EditFields[K]) => {
    setFields((current) => ({ ...current, [key]: value }));
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    try {
      await api<AccountDocument>(`/accounts/${account.id}`, token, {
        method: "PATCH",
        body: JSON.stringify({
          metadata: buildEditMetadata(fields),
        }),
      });
      showToast("账号已更新");
      await onSaved();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  };

  const refreshCredentialsJson = async () => {
    if (!refreshJson.trim()) {
      showToast("请先粘贴新获取的账号 JSON", true);
      return;
    }
    setRefreshingJson(true);
    try {
      await api<AccountDocument>(`/accounts/${account.id}/refresh-credentials-json`, token, {
        method: "POST",
        body: JSON.stringify({ account_json: refreshJson }),
      });
      showToast("账号 JSON 参数已更新");
      await onSaved();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setRefreshingJson(false);
    }
  };

  return (
    <div className="drawer-backdrop" role="dialog" aria-modal="true">
      <aside className="drawer-panel">
        <div className="drawer-header">
          <div>
            <h3>编辑账号</h3>
            <p>{accountEmail(account)}</p>
          </div>
          <button className="ghost compact-button" type="button" onClick={onClose}>
            关闭
          </button>
        </div>

        <form className="form-grid drawer-form" onSubmit={submit}>
          <label className="span-2">
            <span className="field-label">
              <strong>邮箱和接码 session</strong>
              <span>（必填）</span>
            </span>
            <input value={fields.email_session} onChange={(event) => setField("email_session", event.target.value)} required />
          </label>
          <label>
            <span className="field-label">
              <strong>账号类型</strong>
              <span>（必填）</span>
            </span>
            <select value={fields.account_type} onChange={(event) => setField("account_type", event.target.value as EditFields["account_type"])} required>
              <option value="plus">plus</option>
              <option value="team">team子号</option>
              <option value="free">free</option>
              <option value="pro">pro</option>
              <option value="other">其他</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>支付类型</strong>
              <span>（必填）</span>
            </span>
            <select value={fields.payment_type} onChange={(event) => setField("payment_type", event.target.value as EditFields["payment_type"])} required>
              <option value="paypal_multi">PayPal 一卡多号</option>
              <option value="paypal_single">PayPal 一卡一号</option>
              <option value="no_card">不绑卡</option>
              <option value="gopay">gopay</option>
              <option value="other">其他</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>是否自产</strong>
              <span>（必填，布尔值）</span>
            </span>
            <select
              value={fields.self_produced}
              onChange={(event) => {
                const value = event.target.value as EditFields["self_produced"];
                setFields((current) => ({
                  ...current,
                  self_produced: value,
                  purchase_account_type: value === "false" ? current.purchase_account_type || "free" : current.purchase_account_type,
                }));
              }}
              required
            >
              <option value="true">是</option>
              <option value="false">否</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>购买来源</strong>
              {fields.self_produced === "false" && <span>（必填）</span>}
            </span>
            <input value={fields.purchase_source} onChange={(event) => setField("purchase_source", event.target.value)} required={fields.self_produced === "false"} />
          </label>
          <label>
            <span className="field-label">
              <strong>购买时账号类型</strong>
              {fields.self_produced === "false" && <span>（必填）</span>}
            </span>
            <select
              value={fields.purchase_account_type}
              onChange={(event) => setField("purchase_account_type", event.target.value as EditFields["purchase_account_type"])}
              required={fields.self_produced === "false"}
            >
              <option value="">未标注</option>
              <option value="free">free</option>
              <option value="plus">plus</option>
              <option value="team">team子号</option>
              <option value="pro">pro</option>
              <option value="other">其他</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>是否绑定手机</strong>
              <span>（必填，布尔值）</span>
            </span>
            <select value={fields.phone_bound} onChange={(event) => setField("phone_bound", event.target.value as EditFields["phone_bound"])} required>
              <option value="true">是</option>
              <option value="false">否</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>手机号</strong>
            </span>
            <input value={fields.phone_number} onChange={(event) => setField("phone_number", event.target.value)} />
          </label>
          <label className="span-2">
            <span className="field-label">
              <strong>2FA</strong>
            </span>
            <input value={fields.twoFA} onChange={(event) => setField("twoFA", event.target.value)} />
          </label>
          <label className="span-2">
            <span className="field-label">
              <strong>备注</strong>
            </span>
            <textarea value={fields.remark} onChange={(event) => setField("remark", event.target.value)} rows={2} />
          </label>
          <label className="span-2">
            <span className="field-label">
              <strong>状态标注</strong>
            </span>
            <input value={fields.manual_status_label} onChange={(event) => setField("manual_status_label", event.target.value)} />
          </label>
          <label className="span-4" hidden>
            <span className="field-label">
              <strong>account_json</strong>
              <span>（必填）</span>
            </span>
            <textarea
              className="json-input edit-json-input"
              value={fields.account_json}
              onChange={(event) => setField("account_json", event.target.value)}
              spellCheck={false}
              disabled
              required
            />
          </label>
          {canRefreshCredentials && (
            <label className="span-4">
              <span className="field-label">
                <strong>更新 JSON</strong>
                <span>只更新 access_token / refresh_token / id_token / session_token / expires_at</span>
              </span>
              <textarea
                className="json-input edit-json-input"
                value={refreshJson}
                onChange={(event) => setRefreshJson(event.target.value)}
                placeholder="粘贴新导出的 JSON，可以是单个账号对象，也可以是包含 accounts 的导出包。只会更新重新获取的凭证字段，不会替换完整账号 JSON。"
                spellCheck={false}
              />
            </label>
          )}
          <div className="button-row span-4">
            <button type="submit" disabled={saving}>
              {saving ? "保存中..." : "保存修改"}
            </button>
            {canRefreshCredentials && (
              <button className="ghost" type="button" onClick={refreshCredentialsJson} disabled={refreshingJson || saving}>
                {refreshingJson ? "更新中..." : "更新 JSON"}
              </button>
            )}
            <button className="ghost" type="button" onClick={onClose} disabled={saving}>
              取消
            </button>
          </div>
        </form>
      </aside>
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

function summarize(accounts: AccountDocument[]) {
  return accounts.reduce(
    (summary, account) => {
      const status = text(account.metadata.pool_status) || "library";
      if (status === "library") summary.library += 1;
      if (status === "available") summary.available += 1;
      if (status === "problem") summary.problem += 1;
      if (status === "discarded") summary.discarded += 1;
      return summary;
    },
    { library: 0, available: 0, problem: 0, discarded: 0 },
  );
}

function accountEmail(account: AccountDocument) {
  const credentials = asRecord(account.account_json.credentials);
  return text(account.metadata.email) || text(credentials.email) || text(account.account_json.name) || "未识别邮箱";
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
  if (value === "available" || value === "reserve") return "accent";
  if (value === "active") return "success";
  if (value === "problem") return "warning";
  if (value === "discarded") return "danger";
  return "muted";
}

function cardBindingLabel(value: unknown) {
  const paymentType = text(value);
  if (paymentType === "paypal_multi" || paymentType === "paypal_single") return "已绑卡";
  if (paymentType === "no_card") return "未绑卡";
  if (paymentType === "gopay") return "gopay";
  if (paymentType === "other") return "其他";
  return "未标注";
}

function cardBindingTone(value: unknown): "accent" | "success" | "warning" | "danger" | "muted" {
  const paymentType = text(value);
  if (paymentType === "paypal_multi" || paymentType === "paypal_single") return "success";
  if (paymentType === "no_card") return "muted";
  if (paymentType === "gopay") return "accent";
  if (paymentType === "other") return "warning";
  return "muted";
}

function selfProducedLabel(value: unknown) {
  if (value === true || text(value).toLowerCase() === "true") return "自产";
  if (value === false || text(value).toLowerCase() === "false") return "购买";
  return "未标注";
}

function selfProducedTone(value: unknown): "accent" | "success" | "warning" | "danger" | "muted" {
  if (value === true || text(value).toLowerCase() === "true") return "success";
  if (value === false || text(value).toLowerCase() === "false") return "accent";
  return "muted";
}

function usedLabel(account: AccountDocument) {
  return hasUsageSignal(account) ? "已使用" : "未使用";
}

function usedTone(account: AccountDocument): "accent" | "success" | "warning" | "danger" | "muted" {
  return hasUsageSignal(account) ? "success" : "muted";
}

function hasUsageSignal(account: AccountDocument) {
  const metadata = account.metadata || {};
  return Boolean(
    text(metadata.last_request_at) ||
      hasPositiveNumber(metadata.used_quota) ||
      text(metadata.last_used),
  );
}

function hasPositiveNumber(value: unknown) {
  if (typeof value === "number") return value > 0;
  const parsed = Number(text(value));
  return Number.isFinite(parsed) && parsed > 0;
}

function buildEditFields(account: AccountDocument): EditFields {
  const metadata = account.metadata || {};
  const accountJson = account.account_json || {};
  const credentials = asRecord(accountJson.credentials);
  const extra = asRecord(accountJson.extra);
  const selfProduced = normalizeBooleanSelect(metadata.self_produced ?? extra.self_produced, "true");
  const purchaseSource = text(metadata.purchase_source) || text(extra.purchase_source);
  return {
    email_session: text(metadata.email_session) || text(extra.email_session) || text(metadata.email) || text(credentials.email) || text(accountJson.name),
    account_type: normalizeAccountType(text(metadata.account_type) || text(extra.account_type) || text(credentials.plan_type)),
    payment_type: normalizePaymentType(text(metadata.payment_type) || text(extra.payment_type)),
    twoFA: text(metadata["2FA"]) || text(extra["2FA"]),
    self_produced: selfProduced,
    purchase_source: purchaseSource,
    purchase_account_type: normalizePurchaseAccountType(
      text(metadata.purchase_account_type) || text(extra.purchase_account_type),
      purchaseSource || selfProduced === "false" ? "free" : "",
    ),
    phone_bound: normalizePhoneBoundSelect(metadata.phone_bound ?? extra.phone_bound),
    phone_number: text(metadata.phone_number) || text(extra.phone_number),
    remark: text(metadata.remark) || text(extra.remark),
    manual_status_label: text(metadata.manual_status_label) || text(extra.manual_status_label),
    account_json: pretty(accountJson),
  };
}

function buildEditMetadata(fields: EditFields) {
  return {
    email_session: fields.email_session,
    account_type: fields.account_type,
    payment_type: fields.payment_type,
    "2FA": fields.twoFA,
    self_produced: fields.self_produced === "true",
    purchase_source: fields.purchase_source,
    purchase_account_type: fields.purchase_account_type,
    phone_bound: fields.phone_bound === "true",
    phone_number: fields.phone_number,
    remark: fields.remark,
    manual_status_label: fields.manual_status_label,
    source: "edit",
  };
}

function normalizeEditedAccountJson(parsed: unknown): Record<string, unknown> {
  if (Array.isArray(parsed)) {
    if (parsed.length !== 1) throw new Error("编辑时只能保存一个账号 JSON");
    return normalizeEditedAccountJson(parsed[0]);
  }
  if (!isRecord(parsed)) throw new Error("account_json 必须是对象");
  const wrapperAccounts = parsed.accounts;
  if (Array.isArray(wrapperAccounts)) {
    if (wrapperAccounts.length !== 1) throw new Error("编辑时 accounts 数组只能包含一个账号");
    return normalizeEditedAccountJson(wrapperAccounts[0]);
  }
  if (!isRecord(parsed.credentials)) throw new Error("account_json 需要包含 credentials");
  return parsed;
}

function normalizeAccountType(value: string): AccountType {
  const normalized = value.trim().toLowerCase();
  if (["team", "team_sub", "team-sub", "team_child", "team_child_account", "team子号", "team 子号"].includes(normalized)) return "team";
  if (normalized === "plus" || normalized === "free" || normalized === "pro" || normalized === "other") return normalized;
  return "plus";
}

function normalizePurchaseAccountType(value: string, fallback: AccountType | ""): EditFields["purchase_account_type"] {
  const normalized = value.trim().toLowerCase();
  if (["team", "team_sub", "team-sub", "team_child", "team_child_account", "team子号", "team 子号"].includes(normalized)) return "team";
  if (normalized === "plus" || normalized === "free" || normalized === "pro" || normalized === "other") return normalized;
  return fallback;
}

function normalizePaymentType(value: string): EditFields["payment_type"] {
  if (value === "paypal_multi" || value === "paypal_single" || value === "no_card" || value === "gopay" || value === "other") return value;
  return "paypal_multi";
}

function normalizePhoneBoundSelect(value: unknown): EditFields["phone_bound"] {
  return normalizeBooleanSelect(value, "true");
}

function normalizeBooleanSelect(value: unknown, fallback: "true" | "false"): "true" | "false" {
  if (value === false) return "false";
  if (value === true) return "true";
  const normalized = text(value).trim().toLowerCase();
  if (["false", "no", "0"].includes(normalized)) return "false";
  if (["true", "yes", "1"].includes(normalized)) return "true";
  return fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
