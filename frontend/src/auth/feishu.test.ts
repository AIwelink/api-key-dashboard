// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest";

import type { User } from "../types";
import {
  FEISHU_SESSION_STORAGE_KEY,
  launchFeishuAuthorization,
  readStoredFeishuSession,
  startFeishuSessionPolling,
  type FeishuAuthorizationSession,
} from "./feishu";

const session: FeishuAuthorizationSession = {
  session_id: "session-1",
  authorization_url: "https://accounts.feishu.cn/open-apis/authen/v1/authorize?app_id=cli_example",
  ticket: "ticket-token-at-least-20-characters",
  expires_at: "2026-08-18T12:05:00+00:00",
};

function memoryStorage(): Storage {
  const values = new Map<string, string>();
  return {
    get length() { return values.size; },
    clear: () => values.clear(),
    getItem: (key) => values.get(key) ?? null,
    key: (index) => [...values.keys()][index] ?? null,
    removeItem: (key) => { values.delete(key); },
    setItem: (key, value) => { values.set(key, value); },
  };
}

afterEach(() => vi.restoreAllMocks());

describe("Feishu authorization helpers", () => {
  it("falls back to same-tab authorization and persists only the short-lived session", () => {
    const storage = memoryStorage();
    const redirect = vi.fn();

    const result = launchFeishuAuthorization(session, { popup: null, redirect, storage });

    expect(result).toBe("redirect");
    expect(redirect).toHaveBeenCalledWith(session.authorization_url);
    expect(readStoredFeishuSession(storage)).toEqual(session);
    expect(storage.getItem(FEISHU_SESSION_STORAGE_KEY)).not.toContain("access_token");
  });

  it("polls with the ticket header and exchanges a completed session", async () => {
    vi.useFakeTimers();
    try {
      const user: User = {
        email: "pending@example.com",
        role: "viewer",
        authorization_status: "pending",
        permissions: { allowed_views: [], default_view: null },
      };
      const request = vi.fn()
        .mockResolvedValueOnce({ ...session, status: "pending" })
        .mockResolvedValueOnce({ ...session, status: "completed" })
        .mockResolvedValueOnce({ access_token: "local-jwt", user });
      const onLogin = vi.fn();

      const stop = startFeishuSessionPolling(session, { onLogin, onPhase: vi.fn(), onError: vi.fn() }, { request });
      await vi.advanceTimersByTimeAsync(0);
      await vi.advanceTimersByTimeAsync(1_000);

      const [statusPath, statusToken, statusOptions] = request.mock.calls[0];
      expect(statusPath).toBe("/auth/feishu/sessions/session-1");
      expect(statusPath).not.toContain(session.ticket);
      expect(statusToken).toBe("");
      expect(statusOptions.headers).toEqual({ "X-Feishu-Session-Ticket": session.ticket });
      expect(request.mock.calls[2][0]).toBe("/auth/feishu/exchange");
      expect(JSON.parse(request.mock.calls[2][2].body)).toEqual({ ticket: session.ticket });
      expect(onLogin).toHaveBeenCalledWith("local-jwt", user);
      stop();
    } finally {
      vi.useRealTimers();
    }
  });

  it("cancels pending polling and aborts the in-flight request", async () => {
    vi.useFakeTimers();
    try {
      let signal: AbortSignal | undefined;
      const request = vi.fn((_path, _token, options: RequestInit) => {
        signal = options.signal || undefined;
        return Promise.resolve({ ...session, status: "pending" });
      });
      const clearTimeoutSpy = vi.spyOn(window, "clearTimeout");

      const stop = startFeishuSessionPolling(
        session,
        { onLogin: vi.fn(), onPhase: vi.fn(), onError: vi.fn() },
        { request },
      );
      await vi.advanceTimersByTimeAsync(0);
      stop();

      expect(signal?.aborted).toBe(true);
      expect(clearTimeoutSpy).toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(2_000);
      expect(request).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
