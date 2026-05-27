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
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const message = data?.detail || data?.error?.message || response.statusText;
    if (response.status === 401 && token) {
      notifyAuthExpired();
      throw new Error("登录过期");
    }
    throw new Error(message === "Token expired" ? "登录过期" : message);
  }
  notifySub2apiCacheUpdated(path, options);
  return data as T;
}
