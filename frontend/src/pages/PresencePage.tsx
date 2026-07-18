import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import { errorMessage, formatDateTime } from "../utils/format";


type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type PresenceItem = {
  id: string;
  user_id: string;
  user_name?: string;
  user_email?: string;
  role?: string;
  client_id: string;
  client_label?: string;
  device_type?: string;
  view?: string;
  path?: string;
  foreground_since_at?: string;
  last_seen_at?: string;
};

type PresenceResponse = {
  items: PresenceItem[];
  total: number;
  active_window_seconds: number;
  observed_at?: string;
};

const VIEW_LABELS: Record<string, string> = {
  upload: "上传账号",
  todos: "代办与错误账号处理",
  "push-error-todos": "疑问账号分配面板",
  accounts: "账号列表",
  "available-pool": "可用池",
  "reserve-pool": "使用备选池",
  "api-pools": "API 账号池状态",
  "event-records": "事件记录",
  "alert-center": "异常告警",
  "pool-lifecycle": "账号池管理",
  "client-sites": "客户站点",
  "agent-analysis": "Agent分析",
  "agent-workbench": "Agent工作台",
  "api-tokens": "系统管理",
  presence: "前台在线",
  users: "用户管理",
  logs: "日志",
};

export function PresencePage({ token, showToast }: Props) {
  const [data, setData] = useState<PresenceResponse>({ items: [], total: 0, active_window_seconds: 60 });
  const [loading, setLoading] = useState(false);
  const userCount = useMemo(() => new Set(data.items.map((item) => item.user_id)).size, [data.items]);

  const loadPresence = async (notify = false) => {
    setLoading(true);
    try {
      const next = await api<PresenceResponse>("/presence", token);
      setData(next);
    } catch (error) {
      if (notify) showToast(errorMessage(error), true);
      throw error;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPresence(true).catch(() => undefined);
  }, []);

  usePageAutoRefresh(() => loadPresence(false), { intervalMs: 15_000 });

  return (
    <section className="view presence-page">
      <div className="topbar">
        <div>
          <h2>前台在线</h2>
          <p>浏览器前台活跃用户与客户端</p>
        </div>
        <button disabled={loading} onClick={() => loadPresence(true).catch(() => undefined)} type="button">
          {loading ? "刷新中" : "刷新"}
        </button>
      </div>

      <div className="presence-summary" aria-label="在线概览">
        <div><strong>{userCount}</strong><span>在线用户</span></div>
        <div><strong>{data.total}</strong><span>活跃客户端</span></div>
      </div>

      <section className="panel presence-list-panel">
        <div className="panel-header">
          <div>
            <h3>当前前台</h3>
            <p>判定窗口 {data.active_window_seconds} 秒</p>
          </div>
        </div>
        <div className="table-wrap">
          <table className="presence-table">
            <thead>
              <tr>
                <th>用户</th>
                <th>客户端</th>
                <th>当前页面</th>
                <th>前台停留</th>
                <th>最后心跳</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((item) => (
                <tr key={item.id}>
                  <td>
                    <div className="presence-user-line"><span className="presence-dot" /><strong>{item.user_name || item.user_email || item.user_id}</strong></div>
                    <div className="cell-sub">{item.user_email || "-"} · {item.role || "-"}</div>
                  </td>
                  <td>
                    <div>{item.client_label || item.device_type || "未知客户端"}</div>
                    <div className="cell-sub" title={item.client_id}>ID {shortClientId(item.client_id)}</div>
                  </td>
                  <td>
                    <div>{VIEW_LABELS[item.view || ""] || item.view || "未知页面"}</div>
                    <div className="cell-sub">{item.path || "-"}</div>
                  </td>
                  <td>
                    <div>{formatElapsed(item.foreground_since_at)}</div>
                    <div className="cell-sub">开始 {formatDateTime(item.foreground_since_at)}</div>
                  </td>
                  <td>{formatHeartbeat(item.last_seen_at)}</td>
                </tr>
              ))}
              {!data.items.length && (
                <tr>
                  <td className="muted" colSpan={5}>{loading ? "加载中..." : "当前没有前台活跃用户"}</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </section>
  );
}

function shortClientId(value: string) {
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

function formatElapsed(value?: string) {
  const timestamp = value ? new Date(value).getTime() : Number.NaN;
  if (!Number.isFinite(timestamp)) return "-";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分钟`;
}

function formatHeartbeat(value?: string) {
  const timestamp = value ? new Date(value).getTime() : Number.NaN;
  if (!Number.isFinite(timestamp)) return "-";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  return seconds < 10 ? "刚刚" : `${seconds} 秒前`;
}
