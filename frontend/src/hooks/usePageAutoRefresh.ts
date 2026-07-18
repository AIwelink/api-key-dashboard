import { useEffect, useRef } from "react";

export const PAGE_AUTO_REFRESH_INTERVAL_MS = 60_000;

type SchedulerOptions = {
  refresh: () => void | Promise<void>;
  intervalMs?: number;
  onError?: (error: unknown) => void;
  now?: () => number;
  isVisible?: () => boolean;
  setIntervalFn?: (callback: () => void, intervalMs: number) => unknown;
  clearIntervalFn?: (timerId: unknown) => void;
  subscribeVisibilityChange?: (callback: () => void) => () => void;
};

export type PageAutoRefreshScheduler = {
  run: () => Promise<boolean>;
  visibilityChanged: () => void;
  stop: () => void;
};

export function createPageAutoRefreshScheduler({
  refresh,
  intervalMs = PAGE_AUTO_REFRESH_INTERVAL_MS,
  onError,
  now = Date.now,
  isVisible = () => typeof document === "undefined" || document.visibilityState === "visible",
  setIntervalFn = (callback, delay) => window.setInterval(callback, delay),
  clearIntervalFn = (timerId) => window.clearInterval(timerId as number),
  subscribeVisibilityChange,
}: SchedulerOptions): PageAutoRefreshScheduler {
  let stopped = false;
  let inFlight = false;
  let lastAttemptAt = now();

  const run = async () => {
    if (stopped || inFlight || !isVisible()) return false;
    inFlight = true;
    lastAttemptAt = now();
    try {
      await refresh();
      return true;
    } catch (error) {
      onError?.(error);
      return false;
    } finally {
      inFlight = false;
    }
  };

  const visibilityChanged = () => {
    if (isVisible() && now() - lastAttemptAt >= intervalMs) {
      void run();
    }
  };

  const timerId = setIntervalFn(() => void run(), intervalMs);
  const unsubscribeVisibility = subscribeVisibilityChange?.(visibilityChanged);

  return {
    run,
    visibilityChanged,
    stop: () => {
      if (stopped) return;
      stopped = true;
      clearIntervalFn(timerId);
      unsubscribeVisibility?.();
    },
  };
}

type PageAutoRefreshOptions = {
  enabled?: boolean;
  paused?: boolean;
  intervalMs?: number;
  onError?: (error: unknown) => void;
};

export function usePageAutoRefresh(
  refresh: () => void | Promise<void>,
  {
    enabled = true,
    paused = false,
    intervalMs = PAGE_AUTO_REFRESH_INTERVAL_MS,
    onError,
  }: PageAutoRefreshOptions = {},
) {
  const refreshRef = useRef(refresh);
  const onErrorRef = useRef(onError);
  refreshRef.current = refresh;
  onErrorRef.current = onError;

  useEffect(() => {
    if (!enabled || paused) return;
    const scheduler = createPageAutoRefreshScheduler({
      refresh: () => refreshRef.current(),
      intervalMs,
      onError: (error) => onErrorRef.current?.(error),
      isVisible: () => document.visibilityState === "visible",
      setIntervalFn: (callback, delay) => window.setInterval(callback, delay),
      clearIntervalFn: (timerId) => window.clearInterval(timerId as number),
      subscribeVisibilityChange: (callback) => {
        document.addEventListener("visibilitychange", callback);
        return () => document.removeEventListener("visibilitychange", callback);
      },
    });
    return scheduler.stop;
  }, [enabled, paused, intervalMs]);
}
