import { useEffect, useState } from "react";
import { api } from "../api/client";
import { errorMessage, formatDateTime } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type AlertItem = {
  id?: string;
  alert_type?: string;
  alert_category?: string;
  alert_label?: string;
  alert_severity?: string;
  alert_at?: string | null;
  message?: string;
  normalized_email?: string;
  email?: string;
  site_id?: string;
  site_name?: string;
  site_base_url?: string;
  current_remote_account_id?: number | string | null;
  current_remote_account_ids?: Array<number | string>;
  duplicate_remote_count?: number;
  current_group_ids?: number[];
  group_names?: string[];
  current_status?: string;
  current_error_message?: string | null;
  last_seen_at?: string | null;
  updated_at?: string | null;
  is_read?: boolean;
  read_at?: string | null;
  read_by_name?: string | null;
};

type AlertsResponse = {
  items: AlertItem[];
  total: number;
};

export function AlertCenterPage({ token, showToast }: Props) {
  const [items, setItems] = useState<AlertItem[]>([]);
  const [total, setTotal] = useState(0);
  const [includeRead, setIncludeRead] = useState(false);
  const [loading, setLoading] = useState(false);
  const [markingId, setMarkingId] = useState<string | null>(null);

  const loadAlerts = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        limit: "300",
        include_read: includeRead ? "true" : "false",
      });
      const data = await api<AlertsResponse>(`/api-pools/observability/alerts?${params.toString()}`, token);
      setItems(data.items || []);
      setTotal(numberValue(data.total));
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setLoading(false);
    }
  };

  const markRead = async (item: AlertItem) => {
    if (!item.id) return;
    setMarkingId(item.id);
    try {
      await api(`/api-pools/observability/alerts/${encodeURIComponent(item.id)}/read`, token, {
        method: "POST",
        body: JSON.stringify({ note: "manual read from alert center" }),
      });
      showToast("告警已标记已读，后续通知会跳过它");
      await loadAlerts();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setMarkingId(null);
    }
  };

  useEffect(() => {
    loadAlerts();
  }, [includeRead]);

  const unreadCount = includeRead ? items.filter((item) => !item.is_read).length : total;

  return (
    <section className="view alert-center-page">
      <section className="alert-center-hero">
        <div>
          <h2>异常告警</h2>
          <p>集中查看 sub2api 探测发现的问题。默认未读优先、最新告警优先；已读后写回后端，后续通知机器人可以停止重复提醒。</p>
        </div>
        <div className="alert-center-summary">
          <span>未读</span>
          <strong>{unreadCount}</strong>
        </div>
      </section>

      <section className="alert-center-toolbar">
        <div className="account-view-menu">
          <button className={`account-view-menu-item ${!includeRead ? "active" : ""}`} type="button" onClick={() => setIncludeRead(false)}>
            未读告警
          </button>
          <button className={`account-view-menu-item ${includeRead ? "active" : ""}`} type="button" onClick={() => setIncludeRead(true)}>
            全部告警
          </button>
        </div>
        <button className="ghost compact-button" type="button" onClick={loadAlerts} disabled={loading}>
          {loading ? "刷新中..." : "刷新"}
        </button>
      </section>

      <div className="duplicate-alert-explain alert-center-rule">
        <strong>当前告警</strong>
        <span>当前先接入“同邮箱多个 sub2 remote id”。后续容量、风控、删除重加等告警会继续进入同一个列表，并共用已读状态。</span>
      </div>

      {loading && !items.length ? (
        <div className="empty-state">正在加载异常告警...</div>
      ) : items.length ? (
        <div className="duplicate-alert-list alert-center-list">
          {items.map((item) => (
            <AlertCenterItem
              item={item}
              key={item.id || `${item.site_id}-${item.normalized_email}-${item.updated_at}`}
              marking={markingId === item.id}
              onMarkRead={() => markRead(item)}
            />
          ))}
        </div>
      ) : (
        <div className="empty-state">{includeRead ? "暂无异常告警" : "暂无未读异常告警"}</div>
      )}
    </section>
  );
}

function AlertCenterItem({
  item,
  marking,
  onMarkRead,
}: {
  item: AlertItem;
  marking: boolean;
  onMarkRead: () => void;
}) {
  const email = item.email || item.normalized_email || "未识别邮箱";
  const remoteIds = normalizeIdList(item.current_remote_account_ids, item.current_remote_account_id);
  const groupIds = normalizeIdList(item.current_group_ids);
  const groupNames = item.group_names?.length ? item.group_names : groupIds.map((id) => `#${id}`);
  return (
    <article className={`duplicate-alert-item alert-center-item ${item.is_read ? "is-read" : ""}`}>
      <div className="duplicate-alert-main alert-center-item-main">
        <div>
          <strong title={email}>{email}</strong>
          <span>{item.alert_category ? `${item.alert_category} · ` : ""}{item.alert_label || displayAlertType(item.alert_type)}</span>
        </div>
        <div className="alert-center-actions">
          <StatusPill value={`${numberValue(item.duplicate_remote_count || remoteIds.length)} 个 remote id`} tone="warning" />
          {item.is_read ? (
            <span className="alert-read-mark">已读 {formatOptionalDate(item.read_at)}</span>
          ) : (
            <button className="ghost compact-button success-button" type="button" disabled={marking} onClick={onMarkRead}>
              {marking ? "处理中..." : "标记已读"}
            </button>
          )}
        </div>
      </div>

      <dl className="duplicate-alert-meta alert-center-meta">
        <div>
          <dt>站点</dt>
          <dd title={item.site_base_url || item.site_name || item.site_id}>{item.site_name || item.site_id || "-"}</dd>
        </div>
        <div>
          <dt>分组</dt>
          <dd>{groupNames.length ? groupNames.join(" / ") : "-"}</dd>
        </div>
        <div>
          <dt>Remote IDs</dt>
          <dd>{remoteIds.length ? remoteIds.join(" / ") : "-"}</dd>
        </div>
        <div>
          <dt>状态</dt>
          <dd>
            <StatusPill value={displayStatus(item.current_status)} tone={statusTone(item.current_status)} />
          </dd>
        </div>
        <div>
          <dt>最近探测</dt>
          <dd>{formatOptionalDate(item.alert_at || item.last_seen_at || item.updated_at)}</dd>
        </div>
      </dl>
      {item.current_error_message && (
        <div className="duplicate-alert-error" title={item.current_error_message}>
          {item.current_error_message}
        </div>
      )}
      <p>{item.message || "容量预估按一个账号处理，用量按重复 remote id 加和。"}</p>
    </article>
  );
}

function StatusPill({ value, tone = "muted" }: { value: string; tone?: "accent" | "success" | "warning" | "danger" | "muted" }) {
  return <span className={`status-pill ${tone}`}>{value}</span>;
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

function displayAlertType(value?: string): string {
  if (value === "duplicate_email") return "同邮箱多个 sub2 账号";
  return value || "异常告警";
}

function normalizeIdList(values?: Array<number | string> | null, fallback?: number | string | null): Array<number | string> {
  const source = Array.isArray(values) ? values : [];
  const combined = [...source];
  if (fallback !== undefined && fallback !== null && fallback !== "") combined.push(fallback);
  return [...new Map(combined.filter((value) => value !== null && value !== undefined && value !== "").map((value) => [String(value), value])).values()];
}

function formatOptionalDate(value: unknown): string {
  const formatted = formatDateTime(value);
  return formatted && formatted !== "-" ? formatted : "从未";
}

function numberValue(value: unknown): number {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}
