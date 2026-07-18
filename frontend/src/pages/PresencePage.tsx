import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import { errorMessage, formatDateTime } from "../utils/format";
import { formatOnlineMinutes, halfHourLabel, presenceDaysRecentFirst, presenceSegmentTone, visiblePresenceDays } from "./presenceTimeline";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type CommonPeriod = {
  start: string;
  end: string;
  frequency_percent: number;
};

type PresenceDay = {
  date: string;
  online_minutes: number;
  online_ratio_percent: number;
  segments: Array<number | null>;
};

type PresenceUser = {
  user_id: string;
  user_name?: string;
  user_email?: string;
  role?: string;
  status?: string;
  is_online: boolean;
  active_clients: number;
  active_client_details?: Array<{
    client_id: string;
    client_label?: string;
    device_type?: string;
    session_count: number;
    last_seen_at?: string;
  }>;
  last_seen_at?: string | null;
  online_minutes: number;
  online_ratio_percent: number;
  common_pattern: number[];
  common_periods: CommonPeriod[];
  daily_timeline: PresenceDay[];
};

type PresenceHistoryResponse = {
  items: PresenceUser[];
  total: number;
  online_users: number;
  days: number;
  bucket_minutes: number;
  start_at?: string;
  end_at?: string;
  timezone?: string;
};

const EMPTY_HISTORY: PresenceHistoryResponse = {
  items: [],
  total: 0,
  online_users: 0,
  days: 30,
  bucket_minutes: 5,
};

export function PresencePage({ token, showToast }: Props) {
  const [history, setHistory] = useState<PresenceHistoryResponse>(EMPTY_HISTORY);
  const [selectedUserId, setSelectedUserId] = useState("");
  const [showAllDays, setShowAllDays] = useState(false);
  const [loading, setLoading] = useState(false);
  const selectedUser = useMemo(
    () => history.items.find((item) => item.user_id === selectedUserId) || history.items[0] || null,
    [history.items, selectedUserId],
  );

  const loadHistory = async (notify = false) => {
    setLoading(true);
    try {
      const next = await api<PresenceHistoryResponse>("/presence/history", token);
      setHistory(next);
      setSelectedUserId((current) => next.items.some((item) => item.user_id === current) ? current : next.items[0]?.user_id || "");
    } catch (error) {
      if (notify) showToast(errorMessage(error), true);
      throw error;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadHistory(true).catch(() => undefined);
  }, []);

  useEffect(() => {
    setShowAllDays(false);
  }, [selectedUserId]);

  usePageAutoRefresh(() => loadHistory(false), { intervalMs: 60_000 });

  return (
    <section className="view presence-page">
      <div className="topbar">
        <div>
          <h2>前台在线</h2>
          <p>自 2026/07/18 起 · 最多 30 天 · 上海时间</p>
        </div>
        <button disabled={loading} onClick={() => loadHistory(true).catch(() => undefined)} type="button">
          {loading ? "刷新中" : "刷新"}
        </button>
      </div>

      <div className="presence-summary" aria-label="在线概览">
        <div><strong>{history.online_users}</strong><span>当前在线</span></div>
        <div><strong>{history.total}</strong><span>检测用户</span></div>
        <div><strong>{history.days}</strong><span>已记录天数</span></div>
      </div>

      <div className="presence-workspace">
        <aside className="presence-users" aria-label="用户在线概览">
          <div className="presence-section-heading">
            <h3>用户</h3>
            <span>{history.total}</span>
          </div>
          <div className="presence-user-list">
            {history.items.map((item) => (
              <button
                className={`presence-user-row ${selectedUser?.user_id === item.user_id ? "selected" : ""}`}
                key={item.user_id}
                onClick={() => setSelectedUserId(item.user_id)}
                type="button"
              >
                <span className="presence-user-heading">
                  <span className={`presence-dot ${item.is_online ? "online" : "offline"}`} />
                  <strong>{item.user_name || item.user_email || item.user_id}</strong>
                  <em>{item.role || "-"}</em>
                </span>
                <span className="presence-month-strip" aria-label={`${item.user_name || item.user_id} 监测期在线概览`}>
                  {presenceDaysRecentFirst(item.daily_timeline).map((day) => (
                    <i
                      className={`presence-segment ${presenceSegmentTone(day.online_ratio_percent)}`}
                      key={day.date}
                      title={`${day.date} · ${formatOnlineMinutes(day.online_minutes)} · ${day.online_ratio_percent}%`}
                    />
                  ))}
                </span>
                <span className="presence-user-stats">
                  <span>{formatOnlineMinutes(item.online_minutes)}</span>
                  <span>{item.online_ratio_percent}%</span>
                </span>
              </button>
            ))}
            {!history.items.length && <div className="muted presence-empty">{loading ? "加载中..." : "暂无用户"}</div>}
          </div>
        </aside>

        <main className="presence-detail">
          {selectedUser ? (
            <>
              <header className="presence-detail-header">
                <div>
                  <div className="presence-detail-name">
                    <span className={`presence-dot ${selectedUser.is_online ? "online" : "offline"}`} />
                    <h3>{selectedUser.user_name || selectedUser.user_email || selectedUser.user_id}</h3>
                    <span className="presence-role">{selectedUser.role || "-"}</span>
                  </div>
                  <p>{selectedUser.user_email || selectedUser.user_id}</p>
                </div>
                <div className="presence-current-state">
                  <strong>{selectedUser.is_online ? "前台在线" : "当前离线"}</strong>
                  <span>{selectedUser.is_online ? `${selectedUser.active_clients} 个客户端` : `最后心跳 ${formatDateTime(selectedUser.last_seen_at || undefined)}`}</span>
                  {selectedUser.is_online && Boolean(selectedUser.active_client_details?.length) && (
                    <div className="presence-client-list">
                      {selectedUser.active_client_details?.map((client) => (
                        <span key={client.client_id} title={client.client_id}>
                          {client.client_label || client.device_type || "未知客户端"} · {shortClientId(client.client_id)}
                          {client.session_count > 1 ? ` · ${client.session_count} 标签页` : ""}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </header>

              <div className="presence-detail-metrics">
                <div><span>在线总时长</span><strong>{formatOnlineMinutes(selectedUser.online_minutes)}</strong></div>
                <div><span>统计期在线占比</span><strong>{selectedUser.online_ratio_percent}%</strong></div>
                <div><span>常见在线时段</span><strong className="presence-period-text">{formatCommonPeriods(selectedUser.common_periods)}</strong></div>
              </div>

              <section className="presence-pattern-section">
                <div className="presence-section-heading">
                  <h3>常见在线时段</h3>
                  <span>统计期半小时分布</span>
                </div>
                <TimelineAxis />
                <div className="presence-pattern-strip">
                  {selectedUser.common_pattern.map((value, index) => (
                    <i
                      className={`presence-segment ${presenceSegmentTone(value)}`}
                      key={index}
                      title={`${halfHourLabel(index)}-${halfHourLabel(index + 1)} · 出现频率 ${value}%`}
                    />
                  ))}
                </div>
                <div className="presence-legend">
                  <span><i className="presence-segment offline" />离线</span>
                  <span><i className="presence-segment low" />偶尔在线</span>
                  <span><i className="presence-segment medium" />经常在线</span>
                  <span><i className="presence-segment high" />高频在线</span>
                </div>
              </section>

              <section className="presence-history-section">
                <div className="presence-section-heading">
                  <h3>在线时间段</h3>
                  <div className="presence-history-heading-actions">
                    <span>最近日期优先 · 5 分钟采样</span>
                    {selectedUser.daily_timeline.length > 7 && (
                      <button className="ghost compact-button" onClick={() => setShowAllDays((current) => !current)} type="button">
                        {showAllDays ? "收起到最近7天" : `展开全部${selectedUser.daily_timeline.length}天`}
                      </button>
                    )}
                  </div>
                </div>
                <div className="presence-day-bars">
                  <div className="presence-day-axis-row">
                    <span />
                    <TimelineAxis />
                    <span />
                  </div>
                  {visiblePresenceDays(selectedUser.daily_timeline, showAllDays).map((day) => (
                    <div className="presence-day-bar" key={day.date}>
                      <strong className="presence-day-date">{formatDayLabel(day.date)}</strong>
                      <div className="presence-day-segments">
                        {day.segments.map((value, index) => (
                          <i
                            aria-label={`${day.date} ${halfHourLabel(index)} 到 ${halfHourLabel(index + 1)} ${value === null ? "尚未到达" : `在线 ${value}%`}`}
                            className={`presence-segment ${presenceSegmentTone(value)}`}
                            key={index}
                            title={`${day.date} · ${halfHourLabel(index)}-${halfHourLabel(index + 1)} · ${value === null ? "尚未到达" : `在线约 ${Math.round(value * 0.3)} 分钟`}`}
                          />
                        ))}
                      </div>
                      <span className="presence-day-summary">{formatOnlineMinutes(day.online_minutes)} · {day.online_ratio_percent}%</span>
                    </div>
                  ))}
                </div>
              </section>
            </>
          ) : (
            <div className="presence-detail-empty">{loading ? "加载中..." : "暂无在线历史"}</div>
          )}
        </main>
      </div>
    </section>
  );
}

function TimelineAxis() {
  return (
    <div className="presence-time-axis">
      <div><span>00:00</span><span>06:00</span><span>12:00</span><span>18:00</span><span>24:00</span></div>
    </div>
  );
}

function formatCommonPeriods(periods: CommonPeriod[]) {
  if (!periods.length) return "暂无稳定时段";
  return periods.slice(0, 3).map((period) => `${period.start}-${period.end}`).join(" · ");
}

function formatDayLabel(value: string) {
  const date = new Date(`${value}T00:00:00+08:00`);
  const weekday = new Intl.DateTimeFormat("zh-CN", { weekday: "short", timeZone: "Asia/Shanghai" }).format(date);
  return `${value.slice(5).replace("-", "/")} ${weekday}`;
}

function shortClientId(value: string) {
  return value.length > 10 ? `${value.slice(0, 6)}…${value.slice(-3)}` : value;
}
