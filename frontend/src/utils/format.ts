export function formatPhoneBound(value: unknown) {
  if (value === true || value === "true" || value === "yes") return "已绑定";
  if (value === false || value === "false" || value === "no") return "未绑定";
  return "未知";
}

export function formatPayment(value: unknown) {
  const labels: Record<string, string> = {
    paypal_multi: "PayPal 一卡多号",
    paypal_single: "PayPal 一卡一号",
    no_card: "不绑卡",
    gopay: "gopay",
    other: "其他",
  };
  return labels[String(value)] || "";
}

export function text(value: unknown) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

export function pretty(value: unknown) {
  return JSON.stringify(value, null, 2);
}

export const DISPLAY_TIME_ZONE = "Asia/Shanghai";

export function formatDateTime(value: unknown) {
  if (!value) return "-";
  const date = parseDisplayDate(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", {
    timeZone: DISPLAY_TIME_ZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function parseDisplayDate(value: unknown) {
  if (value instanceof Date) return value;
  if (typeof value === "number") {
    return new Date(value > 10_000_000_000 ? value : value * 1000);
  }

  const raw = String(value).trim();
  if (/^\d+$/.test(raw)) {
    const timestamp = Number(raw);
    return new Date(timestamp > 10_000_000_000 ? timestamp : timestamp * 1000);
  }

  const normalizedDate = raw.replace(/\s+/, "T");
  const hasExplicitTimeZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalizedDate);
  if (/^\d{4}-\d{2}-\d{2}$/.test(normalizedDate)) {
    return new Date(`${normalizedDate}T00:00:00+08:00`);
  }

  const normalized = hasExplicitTimeZone ? normalizedDate : `${normalizedDate}+08:00`;
  return new Date(normalized);
}

export function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "未知错误";
}
