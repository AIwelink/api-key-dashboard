export type AccountHistoryChange = {
  event_id: string;
  batch_id?: string;
  observed_at?: string;
  changes?: Record<string, unknown>;
  unset?: string[];
};

const CHANGE_FIELD_LABELS: Record<string, string> = {
  "usage.codex_5h_used_percent": "5h 已用比例",
  "usage.codex_7d_used_percent": "7d 已用比例",
  "usage.codex_5h_reset_at": "5h 刷新时间",
  "usage.codex_7d_reset_at": "7d 刷新时间",
  "usage.codex_5h_actual_cost": "5h 实际消耗",
  "usage.codex_7d_actual_cost": "7d 实际消耗",
  "usage.codex_total_actual_cost": "累计实际消耗",
  "subscription.plan_type": "订阅类型",
  "subscription.active_start": "订阅开始时间",
  "subscription.active_until": "订阅到期时间",
  "subscription.last_checked": "订阅检查时间",
  "subscription.credentials_expires_at": "凭证到期时间",
};

export function changeFieldLabel(path: string): string {
  if (CHANGE_FIELD_LABELS[path]) return CHANGE_FIELD_LABELS[path];
  const separator = path.indexOf(".");
  if (separator < 0) return path;
  const section = path.slice(0, separator);
  const field = path.slice(separator + 1);
  const sectionLabel = section === "usage" ? "用量" : section === "subscription" ? "订阅" : section;
  return `${sectionLabel} · ${field}`;
}

export function formatChangeValue(value: unknown): string {
  if (value === null) return "空值";
  if (value === undefined || value === "") return "-";
  if (value === true) return "是";
  if (value === false) return "否";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}
