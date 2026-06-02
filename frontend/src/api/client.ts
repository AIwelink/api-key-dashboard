export const API_BASE =
  localStorage.getItem("apiBase") || import.meta.env.VITE_API_BASE_URL || "/api";

function notifyAuthExpired() {
  window.dispatchEvent(new CustomEvent("auth-expired"));
}

function notifySub2apiCacheUpdated(path: string, options: RequestInit) {
  const method = (options.method || "GET").toUpperCase();
  if (method !== "POST" || !/^\/sub2api-sites\/[^/]+\/refresh$/.test(path)) return;
  const version = String(Date.now());
  localStorage.setItem("sub2apiCacheVersion", version);
  window.dispatchEvent(new CustomEvent("sub2api-cache-updated", { detail: { version } }));
}

export async function api<T>(path: string, token: string, options: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> | undefined),
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });

  if (response.status === 204) {
    return null as T;
  }

  const text = await response.text();
  const data = parseResponseBody(text);
  if (!response.ok) {
    const message = responseErrorMessage(data, text, response.statusText);
    if (response.status === 401 && token) {
      notifyAuthExpired();
      throw new Error("登录过期");
    }
    throw new Error(message === "Token expired" ? "登录过期" : message);
  }
  notifySub2apiCacheUpdated(path, options);
  return data as T;
}

function parseResponseBody(text: string) {
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function responseErrorMessage(data: unknown, text: string, fallback: string) {
  let raw = "";
  if (data && typeof data === "object") {
    const record = data as Record<string, unknown>;
    const error = record.error && typeof record.error === "object" ? (record.error as Record<string, unknown>) : null;
    raw = textValue(record.detail) || textValue(error?.message) || textValue(record.message) || text.trim() || fallback;
  } else {
    raw = text.trim() || fallback;
  }
  return readableErrorMessage(raw, fallback);
}

function textValue(value: unknown) {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return JSON.stringify(value);
}

function readableErrorMessage(value: string, fallback: string) {
  const raw = value.trim();
  if (!raw) return fallback;
  const htmlSummary = summarizeHtmlError(raw);
  return htmlSummary || raw;
}

function summarizeHtmlError(value: string) {
  if (!/<\/?[a-z][\s\S]*>/i.test(value)) return "";
  const title = htmlText(value.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1] || "");
  const code = htmlText(value.match(/Error code\s*<\/span>\s*<\/h1>|Error code\s*(\d+)/i)?.[1] || "");
  const cloudflareHost = htmlText(value.match(/cf-host-status[\s\S]*?<span[^>]*>([^<]+)<\/span>/i)?.[1] || "");
  const isCloudflare = /Cloudflare|cf-error|Bad gateway|Error code 5\d\d/i.test(value);
  if (isCloudflare) {
    const parts = [title || "Cloudflare 错误", cloudflareHost ? `Host ${cloudflareHost}` : "", code ? `Error code ${code}` : ""].filter(Boolean);
    return `${parts.join(" · ")}。这是上游服务不可用，不是回调 URL 格式错误。`;
  }
  return title ? `${title}。服务返回了 HTML 错误页。` : "服务返回了 HTML 错误页。";
}

function htmlText(value: string) {
  return value
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/\s+/g, " ")
    .trim();
}
