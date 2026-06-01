import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import { errorMessage, formatDateTime } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type ApiToken = {
  id: string;
  name: string;
  role: string;
  status: string;
  token_prefix?: string;
  note?: string | null;
  expires_at?: string | null;
  last_used_at?: string | null;
  usage_count?: number;
  created_at?: string;
  revoked_at?: string | null;
  token?: string;
};

export function ApiTokensPage({ token, showToast }: Props) {
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [createdToken, setCreatedToken] = useState<ApiToken | null>(null);
  const [busy, setBusy] = useState(false);

  const loadTokens = async () => {
    const data = await api<{ items: ApiToken[] }>("/api-tokens", token);
    setTokens(data.items);
  };

  useEffect(() => {
    loadTokens().catch((error) => showToast(errorMessage(error), true));
  }, []);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form).entries());
    const expiresRaw = String(values.expires_in_days || "").trim();
    const payload: Record<string, unknown> = {
      name: values.name,
      role: values.role,
      note: values.note,
    };
    if (expiresRaw) payload.expires_in_days = Number(expiresRaw);

    setBusy(true);
    try {
      const created = await api<ApiToken>("/api-tokens", token, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setCreatedToken(created);
      await loadTokens();
      form.reset();
      showToast("系统 Token 已创建，只显示这一次");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (item: ApiToken) => {
    if (!window.confirm(`确定停用 ${item.name} 吗？停用后对接系统会立即失效。`)) return;
    setBusy(true);
    try {
      await api(`/api-tokens/${item.id}/revoke`, token, { method: "POST" });
      await loadTokens();
      showToast("系统 Token 已停用");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const copyCreatedToken = async () => {
    if (!createdToken?.token) return;
    await navigator.clipboard.writeText(createdToken.token);
    showToast("Token 已复制");
  };

  return (
    <section className="view">
      <div className="topbar">
        <div>
          <h2>系统 Token</h2>
          <p>给外部系统对接使用。创建后只显示一次，数据库只保存哈希。</p>
        </div>
        <button onClick={() => loadTokens().catch((error) => showToast(errorMessage(error), true))} type="button">
          刷新
        </button>
      </div>

      {createdToken?.token && (
        <section className="panel token-created-panel">
          <div className="panel-header">
            <div>
              <h3>新 Token</h3>
              <p>请现在保存到另一个系统里，关闭后无法再次查看明文。</p>
            </div>
            <button onClick={copyCreatedToken} type="button">
              复制
            </button>
          </div>
          <textarea readOnly rows={3} value={createdToken.token} />
        </section>
      )}

      <div className="grid two">
        <section className="panel">
          <h3>Token 列表</h3>
          <div className="list">
            {tokens.map((item) => (
              <div className="list-item token-list-item" key={item.id}>
                <div>
                  <strong>{item.name}</strong>
                  <div className="muted">
                    {item.token_prefix} · {item.role} · {item.status}
                  </div>
                  <div className="muted">
                    创建 {formatDateTime(item.created_at)} · 最近使用 {formatDateTime(item.last_used_at)} · 使用 {item.usage_count || 0} 次
                  </div>
                  {item.expires_at && <div className="muted">过期时间 {formatDateTime(item.expires_at)}</div>}
                  {item.note && <div>{item.note}</div>}
                </div>
                <button className="ghost danger-button" disabled={busy || item.status !== "active"} onClick={() => revoke(item)} type="button">
                  停用
                </button>
              </div>
            ))}
            {!tokens.length && <div className="muted">还没有系统 Token。</div>}
          </div>
        </section>

        <section className="panel">
          <h3>创建 Token</h3>
          <form className="form-grid single" onSubmit={submit}>
            <label>
              名称 <input name="name" placeholder="例如 billing-sync" required />
            </label>
            <label>
              角色
              <select name="role" defaultValue="maintainer">
                <option value="viewer">viewer</option>
                <option value="maintainer">maintainer</option>
                <option value="admin">admin</option>
              </select>
            </label>
            <label>
              有效天数 <input min="1" max="3650" name="expires_in_days" placeholder="留空表示长期有效" type="number" />
            </label>
            <label>
              备注 <textarea name="note" rows={4} placeholder="用途、对接系统、联系人等" />
            </label>
            <button disabled={busy} type="submit">
              创建 Token
            </button>
          </form>
        </section>
      </div>
    </section>
  );
}
