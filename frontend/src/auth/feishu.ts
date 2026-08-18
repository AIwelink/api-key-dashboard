import { api } from "../api/client";
import type { AuthLoginResponse, LoginResponse, User } from "../types";

export const FEISHU_SESSION_STORAGE_KEY = "aiwelink.feishu-auth-session.v1";

export type FeishuAuthPhase = "idle" | "starting" | "waiting" | "exchanging" | "binding" | "failed";
export type FeishuAuthFlow = "login" | "binding";

export type FeishuAuthorizationSession = {
  session_id: string;
  authorization_url: string;
  ticket: string;
  expires_at: string;
  flow?: FeishuAuthFlow;
};

type FeishuAuthorizationStatus = {
  session_id: string;
  status: "pending" | "processing" | "completed" | "failed";
  error_code?: string | null;
  expires_at: string;
};

type PopupWindow = Pick<Window, "closed" | "close" | "focus"> & { location: Pick<Location, "replace"> };
type Requester = (path: string, token: string, options?: RequestInit) => Promise<unknown>;

type LaunchOptions = {
  popup?: PopupWindow | null;
  redirect?: (url: string) => void;
  storage?: Storage;
  flow?: FeishuAuthFlow;
};

type PollCallbacks = {
  onLogin: (token: string, user: User) => void;
  onPhase: (phase: FeishuAuthPhase) => void;
  onError: (message: string) => void;
};

type PollOptions = {
  request?: Requester;
  intervalMs?: number;
  expectedOrigin?: string;
  storage?: Storage;
};

export function openFeishuPopup(): PopupWindow | null {
  return window.open(
    "about:blank",
    "aiwelink-feishu-auth",
    "popup=yes,width=520,height=720,resizable=yes,scrollbars=yes",
  ) as PopupWindow | null;
}

export function launchFeishuAuthorization(
  session: FeishuAuthorizationSession,
  options: LaunchOptions = {},
): "popup" | "redirect" {
  const storage = options.storage ?? window.sessionStorage;
  storeFeishuSession({ ...session, ...(options.flow ? { flow: options.flow } : {}) }, storage);
  const popup = options.popup === undefined ? openFeishuPopup() : options.popup;
  if (popup && !popup.closed) {
    popup.location.replace(session.authorization_url);
    popup.focus();
    return "popup";
  }
  (options.redirect ?? ((url) => window.location.assign(url)))(session.authorization_url);
  return "redirect";
}

export function readStoredFeishuSession(storage: Storage = window.sessionStorage): FeishuAuthorizationSession | null {
  const raw = storage.getItem(FEISHU_SESSION_STORAGE_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<FeishuAuthorizationSession>;
    if (
      typeof value.session_id !== "string"
      || typeof value.authorization_url !== "string"
      || typeof value.ticket !== "string"
      || typeof value.expires_at !== "string"
    ) {
      storage.removeItem(FEISHU_SESSION_STORAGE_KEY);
      return null;
    }
    return value as FeishuAuthorizationSession;
  } catch {
    storage.removeItem(FEISHU_SESSION_STORAGE_KEY);
    return null;
  }
}

export function clearStoredFeishuSession(storage: Storage = window.sessionStorage) {
  storage.removeItem(FEISHU_SESSION_STORAGE_KEY);
}

export function startFeishuSessionPolling(
  session: FeishuAuthorizationSession,
  callbacks: PollCallbacks,
  options: PollOptions = {},
): () => void {
  const request = options.request ?? (api as Requester);
  const intervalMs = Math.max(options.intervalMs ?? 1_000, 1_000);
  const expectedOrigin = options.expectedOrigin ?? window.location.origin;
  const storage = options.storage ?? window.sessionStorage;
  const controller = new AbortController();
  let timer: number | null = null;
  let stopped = false;
  let polling = false;

  const cleanup = () => {
    if (stopped) return;
    stopped = true;
    controller.abort();
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
    window.removeEventListener("message", handleMessage);
  };

  const fail = (message: string) => {
    clearStoredFeishuSession(storage);
    callbacks.onPhase("failed");
    callbacks.onError(message);
    cleanup();
  };

  const schedule = () => {
    if (stopped) return;
    timer = window.setTimeout(() => {
      timer = null;
      void poll();
    }, intervalMs);
  };

  const poll = async () => {
    if (stopped || polling) return;
    polling = true;
    try {
      const status = await request(
        `/auth/feishu/sessions/${encodeURIComponent(session.session_id)}`,
        "",
        {
          signal: controller.signal,
          headers: { "X-Feishu-Session-Ticket": session.ticket },
        },
      ) as FeishuAuthorizationStatus;
      if (stopped) return;
      if (status.status === "failed") {
        fail(feishuErrorMessage(status.error_code));
        return;
      }
      if (status.status !== "completed") {
        callbacks.onPhase(session.flow === "binding" ? "binding" : "waiting");
        schedule();
        return;
      }

      callbacks.onPhase("exchanging");
      const result = await request("/auth/feishu/exchange", "", {
        method: "POST",
        body: JSON.stringify({ ticket: session.ticket }),
        signal: controller.signal,
      }) as LoginResponse;
      if (stopped) return;
      clearStoredFeishuSession(storage);
      callbacks.onLogin(result.access_token, result.user);
      cleanup();
    } catch (error) {
      if (!controller.signal.aborted) {
        fail(error instanceof Error ? error.message : "飞书授权失败，请重新扫码");
      }
    } finally {
      polling = false;
    }
  };

  function handleMessage(event: MessageEvent) {
    if (event.origin !== expectedOrigin) return;
    const payload = event.data as { type?: unknown; sessionId?: unknown } | null;
    if (payload?.type !== "feishu-auth-complete" || payload.sessionId !== session.session_id) return;
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
    void poll();
  }

  window.addEventListener("message", handleMessage);
  void poll();
  return cleanup;
}

export function isBindingRequired(response: AuthLoginResponse): response is Exclude<AuthLoginResponse, LoginResponse> {
  return "status" in response && response.status === "binding_required";
}

function storeFeishuSession(session: FeishuAuthorizationSession, storage: Storage) {
  storage.setItem(FEISHU_SESSION_STORAGE_KEY, JSON.stringify(session));
}

function feishuErrorMessage(code?: string | null) {
  if (code === "access_denied") return "飞书授权已取消，请重新扫码";
  if (code === "tenant_not_allowed") return "当前飞书组织未获准登录";
  if (code === "user_disabled") return "当前系统账号已停用";
  return "飞书授权已失效，请重新扫码";
}
