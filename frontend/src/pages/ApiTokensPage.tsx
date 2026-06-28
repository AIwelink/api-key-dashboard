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

type NotificationChannel = {
  id: string;
  name: string;
  channel_type: "dingtalk";
  status: "active" | "disabled";
  note?: string | null;
  webhook_configured?: boolean;
  webhook_preview?: string | null;
  signing_secret_configured?: boolean;
  last_test_at?: string | null;
  last_test_status?: string | null;
  last_test_message?: string | null;
  created_at?: string;
  updated_at?: string;
};

type NotificationForm = {
  name: string;
  channel_type: "dingtalk";
  status: "active" | "disabled";
  webhook_url: string;
  signing_secret: string;
  note: string;
};

const emptyNotificationForm: NotificationForm = {
  name: "",
  channel_type: "dingtalk",
  status: "active",
  webhook_url: "",
  signing_secret: "",
  note: "",
};

export function ApiTokensPage({ token, showToast }: Props) {
  const [activeTab, setActiveTab] = useState<"tokens" | "notifications">("tokens");
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [createdToken, setCreatedToken] = useState<ApiToken | null>(null);
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [notificationForm, setNotificationForm] = useState<NotificationForm>(emptyNotificationForm);
  const [editingChannelId, setEditingChannelId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadTokens = async () => {
    const data = await api<{ items: ApiToken[] }>("/api-tokens", token);
    setTokens(data.items);
  };

  const loadNotificationChannels = async () => {
    const data = await api<{ items: NotificationChannel[] }>("/notification-channels", token);
    setChannels(data.items);
  };

  useEffect(() => {
    Promise.all([loadTokens(), loadNotificationChannels()]).catch((error) => showToast(errorMessage(error), true));
  }, []);

  const submitToken = async (event: FormEvent<HTMLFormElement>) => {
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

  const resetNotificationForm = () => {
    setNotificationForm(emptyNotificationForm);
    setEditingChannelId(null);
  };

  const editNotificationChannel = (item: NotificationChannel) => {
    setEditingChannelId(item.id);
    setNotificationForm({
      name: item.name,
      channel_type: item.channel_type,
      status: item.status,
      webhook_url: "",
      signing_secret: "",
      note: item.note || "",
    });
  };

  const submitNotificationChannel = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const webhookUrl = notificationForm.webhook_url.trim();
    const signingSecret = notificationForm.signing_secret.trim();
    const payload: Record<string, unknown> = {
      name: notificationForm.name.trim(),
      channel_type: notificationForm.channel_type,
      status: notificationForm.status,
      note: notificationForm.note.trim(),
    };
    if (!editingChannelId || webhookUrl) payload.webhook_url = webhookUrl;
    if (!editingChannelId || signingSecret) payload.signing_secret = signingSecret;

    setBusy(true);
    try {
      await api<NotificationChannel>(editingChannelId ? `/notification-channels/${editingChannelId}` : "/notification-channels", token, {
        method: editingChannelId ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      await loadNotificationChannels();
      resetNotificationForm();
      showToast(editingChannelId ? "通知配置已保存" : "通知配置已创建");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const testNotificationChannel = async (item: NotificationChannel) => {
    setBusy(true);
    try {
      const result = await api<{ message?: string; channel?: NotificationChannel }>(`/notification-channels/${item.id}/test`, token, { method: "POST" });
      await loadNotificationChannels();
      showToast(result.message || "测试通知已发送");
    } catch (error) {
      await loadNotificationChannels().catch(() => undefined);
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const deleteNotificationChannel = async (item: NotificationChannel) => {
    if (!window.confirm(`确定删除通知配置 ${item.name} 吗？`)) return;
    setBusy(true);
    try {
      await api<null>(`/notification-channels/${item.id}`, token, { method: "DELETE" });
      await loadNotificationChannels();
      if (editingChannelId === item.id) resetNotificationForm();
      showToast("通知配置已删除");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const refreshCurrentTab = () => {
    const loader = activeTab === "tokens" ? loadTokens : loadNotificationChannels;
    loader().catch((error) => showToast(errorMessage(error), true));
  };

  return (
    <section className="view">
      <div className="topbar">
        <div>
          <h2>系统管理</h2>
          <p>管理外部系统 Token 和通知机器人配置。</p>
        </div>
        <button onClick={refreshCurrentTab} type="button">
          刷新
        </button>
      </div>

      <div className="system-tabs">
        <button className={activeTab === "tokens" ? "active" : ""} onClick={() => setActiveTab("tokens")} type="button">
          系统 Token
        </button>
        <button className={activeTab === "notifications" ? "active" : ""} onClick={() => setActiveTab("notifications")} type="button">
          通知
        </button>
      </div>

      {activeTab === "tokens" && createdToken?.token && (
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

      {activeTab === "tokens" && (
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
          <form className="form-grid single" onSubmit={submitToken}>
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
      )}

      {activeTab === "notifications" && (
        <div className="grid two">
          <section className="panel notification-list-panel">
            <div className="panel-header">
              <div>
                <h3>通知列表</h3>
                <p>这里是总通知备选。后续不同通知可以选择不同机器人。</p>
              </div>
            </div>
            <div className="list">
              {channels.map((item) => (
                <div className="list-item notification-channel-item" key={item.id}>
                  <div>
                    <div className="notification-channel-title">
                      <strong>{item.name}</strong>
                      <span className={`status-pill ${item.status === "active" ? "success" : ""}`}>{item.status === "active" ? "启用" : "停用"}</span>
                      <span className="status-pill accent">钉钉</span>
                    </div>
                    <div className="muted">Webhook {item.webhook_configured ? item.webhook_preview || "已配置" : "未配置"} · 加签 {item.signing_secret_configured ? "已配置" : "未配置"}</div>
                    <div className="muted">创建 {formatDateTime(item.created_at)} · 最近测试 {formatDateTime(item.last_test_at)}</div>
                    {item.last_test_status && (
                      <div className={item.last_test_status === "success" ? "success-text" : "warning-text"}>
                        测试 {item.last_test_status === "success" ? "成功" : "失败"}：{item.last_test_message || "-"}
                      </div>
                    )}
                    {item.note && <div>{item.note}</div>}
                  </div>
                  <div className="notification-channel-actions">
                    <button className="ghost success-button" disabled={busy || item.status !== "active"} onClick={() => testNotificationChannel(item)} type="button">
                      测试
                    </button>
                    <button className="ghost" disabled={busy} onClick={() => editNotificationChannel(item)} type="button">
                      编辑
                    </button>
                    <button className="ghost danger-button" disabled={busy} onClick={() => deleteNotificationChannel(item)} type="button">
                      删除
                    </button>
                  </div>
                </div>
              ))}
              {!channels.length && <div className="muted">还没有通知配置。</div>}
            </div>
          </section>

          <section className="panel">
            <div className="panel-header">
              <div>
                <h3>{editingChannelId ? "编辑通知" : "新增通知"}</h3>
                <p>当前先支持钉钉自定义机器人。</p>
              </div>
              {editingChannelId && (
                <button className="ghost" onClick={resetNotificationForm} type="button">
                  新增
                </button>
              )}
            </div>
            <form className="form-grid single" onSubmit={submitNotificationChannel}>
              <label>
                通知名称 <input required value={notificationForm.name} onChange={(event) => setNotificationForm((current) => ({ ...current, name: event.target.value }))} placeholder="例如 容量预警钉钉群" />
              </label>
              <label>
                通知选型
                <select value={notificationForm.channel_type} onChange={(event) => setNotificationForm((current) => ({ ...current, channel_type: event.target.value as "dingtalk" }))}>
                  <option value="dingtalk">钉钉自定义机器人</option>
                </select>
              </label>
              <label>
                状态
                <select value={notificationForm.status} onChange={(event) => setNotificationForm((current) => ({ ...current, status: event.target.value as "active" | "disabled" }))}>
                  <option value="active">启用</option>
                  <option value="disabled">停用</option>
                </select>
              </label>
              <label>
                钉钉自定义机器人 Webhook 地址 *
                <textarea
                  required={!editingChannelId}
                  rows={3}
                  value={notificationForm.webhook_url}
                  onChange={(event) => setNotificationForm((current) => ({ ...current, webhook_url: event.target.value }))}
                  placeholder={editingChannelId ? "留空不修改" : "https://oapi.dingtalk.com/robot/send?access_token=..."}
                />
              </label>
              <label>
                钉钉自定义机器人加签密钥 *
                <input
                  required={!editingChannelId}
                  type="password"
                  value={notificationForm.signing_secret}
                  onChange={(event) => setNotificationForm((current) => ({ ...current, signing_secret: event.target.value }))}
                  placeholder={editingChannelId ? "留空不修改" : "SEC..."}
                />
              </label>
              <label>
                备注 <textarea rows={4} value={notificationForm.note} onChange={(event) => setNotificationForm((current) => ({ ...current, note: event.target.value }))} placeholder="用途、触发场景、群名等" />
              </label>
              <button className="success-button" disabled={busy} type="submit">
                保存通知配置
              </button>
            </form>
          </section>
        </div>
      )}
    </section>
  );
}
