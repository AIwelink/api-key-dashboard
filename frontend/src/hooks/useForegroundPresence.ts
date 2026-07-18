import { useEffect, useRef } from "react";
import { api } from "../api/client";
import type { ViewName } from "../types";


export const PRESENCE_HEARTBEAT_INTERVAL_MS = 15_000;
const CLIENT_ID_KEY = "frontendPresenceClientId";
const SESSION_ID_KEY = "frontendPresenceSessionId";

type StorageLike = {
  getItem: (key: string) => string | null;
  setItem: (key: string, value: string) => void;
};

type IdentityOptions = {
  localStorage: StorageLike;
  sessionStorage: StorageLike;
  randomId?: () => string;
};

type PresenceSchedulerOptions = {
  heartbeat: (foregroundSinceAt: string) => void | Promise<void>;
  isForeground?: () => boolean;
  now?: () => Date;
  setIntervalFn?: (callback: () => void, intervalMs: number) => unknown;
  clearIntervalFn?: (timerId: unknown) => void;
  subscribeForegroundChange?: (callback: () => void) => () => void;
};

export function getPresenceClientIdentity({
  localStorage,
  sessionStorage,
  randomId = createRandomId,
}: IdentityOptions) {
  let clientId = localStorage.getItem(CLIENT_ID_KEY);
  if (!clientId) {
    clientId = randomId();
    localStorage.setItem(CLIENT_ID_KEY, clientId);
  }
  let sessionId = sessionStorage.getItem(SESSION_ID_KEY);
  if (!sessionId) {
    sessionId = randomId();
    sessionStorage.setItem(SESSION_ID_KEY, sessionId);
  }
  return { clientId, sessionId };
}

export function createForegroundPresenceScheduler({
  heartbeat,
  isForeground = () => document.visibilityState === "visible" && document.hasFocus(),
  now = () => new Date(),
  setIntervalFn = (callback, intervalMs) => window.setInterval(callback, intervalMs),
  clearIntervalFn = (timerId) => window.clearInterval(timerId as number),
  subscribeForegroundChange = subscribeToForegroundChanges,
}: PresenceSchedulerOptions) {
  let stopped = false;
  let inFlight = false;
  let foregroundSinceAt: string | null = null;

  const run = async () => {
    if (stopped || inFlight) return false;
    if (!isForeground()) {
      foregroundSinceAt = null;
      return false;
    }
    foregroundSinceAt ||= now().toISOString();
    inFlight = true;
    try {
      await heartbeat(foregroundSinceAt);
      return true;
    } catch {
      return false;
    } finally {
      inFlight = false;
    }
  };

  const foregroundChanged = () => {
    if (!isForeground()) {
      foregroundSinceAt = null;
      return;
    }
    void run();
  };

  const timerId = setIntervalFn(() => void run(), PRESENCE_HEARTBEAT_INTERVAL_MS);
  const unsubscribe = subscribeForegroundChange(foregroundChanged);
  void run();

  return {
    run,
    stop: () => {
      if (stopped) return;
      stopped = true;
      clearIntervalFn(timerId);
      unsubscribe();
    },
  };
}

export function useForegroundPresence(token: string, view: ViewName) {
  const viewRef = useRef(view);
  const identityRef = useRef<{ clientId: string; sessionId: string } | null>(null);
  viewRef.current = view;

  useEffect(() => {
    if (!token) return;
    if (!identityRef.current) {
      identityRef.current = getPresenceClientIdentity({ localStorage: window.localStorage, sessionStorage: window.sessionStorage });
    }
    const identity = identityRef.current;
    const client = describeClient(navigator.userAgent);
    const scheduler = createForegroundPresenceScheduler({
      heartbeat: (foregroundSinceAt) =>
        api("/presence/heartbeat", token, {
          method: "POST",
          body: JSON.stringify({
            client_id: identity.clientId,
            session_id: identity.sessionId,
            client_label: client.label,
            device_type: client.deviceType,
            view: viewRef.current,
            path: window.location.pathname,
            foreground_since_at: foregroundSinceAt,
          }),
        }),
    });

    return () => {
      scheduler.stop();
      void api("/presence/leave", token, {
        method: "POST",
        body: JSON.stringify({ client_id: identity.clientId }),
      }).catch(() => undefined);
    };
  }, [token]);
}

function subscribeToForegroundChanges(callback: () => void) {
  document.addEventListener("visibilitychange", callback);
  window.addEventListener("focus", callback);
  window.addEventListener("blur", callback);
  return () => {
    document.removeEventListener("visibilitychange", callback);
    window.removeEventListener("focus", callback);
    window.removeEventListener("blur", callback);
  };
}

function describeClient(userAgent: string): { label: string; deviceType: "desktop" | "mobile" | "tablet" | "unknown" } {
  const browser = /Edg\//i.test(userAgent)
    ? "Edge"
    : /Firefox\//i.test(userAgent)
      ? "Firefox"
      : /Chrome\//i.test(userAgent)
        ? "Chrome"
        : /Safari\//i.test(userAgent)
          ? "Safari"
          : "Browser";
  const platform = /Android/i.test(userAgent)
    ? "Android"
    : /iPhone|iPad|iPod/i.test(userAgent)
      ? "iOS"
      : /Windows/i.test(userAgent)
        ? "Windows"
        : /Macintosh|Mac OS X/i.test(userAgent)
          ? "macOS"
          : /Linux/i.test(userAgent)
            ? "Linux"
            : "Unknown";
  const deviceType = /iPad|Tablet/i.test(userAgent) ? "tablet" : /Mobi|Android|iPhone|iPod/i.test(userAgent) ? "mobile" : "desktop";
  return { label: `${platform} · ${browser}`, deviceType };
}

function createRandomId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
}
