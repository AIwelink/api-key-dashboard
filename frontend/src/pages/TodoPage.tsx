import { Fragment, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { AccountEditPanel } from "../components/AccountEditPanel";
import type { AccountDocument } from "../types";
import { errorMessage, formatDateTime, formatPayment, text } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type TaskStatus = "open" | "pending" | "processing" | "completed" | "failed" | "all";

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
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<FreeToPlusResponse["stats"]>({ pending: 0, processing: 0, completed: 0, failed: 0 });
  const [status, setStatus] = useState<TaskStatus>("open");
  const [query, setQuery] = useState("");
  const [skip, setSkip] = useState(0);
  const [limit, setLimit] = useState(50);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [paymentById, setPaymentById] = useState<Record<string, string>>({});
  const [editingAccount, setEditingAccount] = useState<AccountDocument | null>(null);

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

  useEffect(() => {
    loadAccounts().catch((error) => showToast(errorMessage(error), true));
  }, [status, skip, limit]);

  const refresh = () => {
    if (skip === 0) loadAccounts().catch((error) => showToast(errorMessage(error), true));
    else setSkip(0);
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

  return (
    <section className="view accounts-page">
      <div className="topbar">
        <div>
          <h2>待办与处理</h2>
          <p>待办与处理是人工任务池和执行台。账号只在总库或可用池时进入待办；一旦进入使用备选池或实际使用池，就不会出现在这里。</p>
        </div>
        <div className="button-row">
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索邮箱、来源、备注、处理人" />
          <button className="ghost" onClick={refresh} type="button">
            搜索/刷新
          </button>
        </div>
      </div>

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
          <h2>错误账号处理</h2>
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
