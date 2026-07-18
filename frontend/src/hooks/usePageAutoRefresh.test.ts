import { describe, expect, it, vi } from "vitest";
import { createPageAutoRefreshScheduler, PAGE_AUTO_REFRESH_INTERVAL_MS } from "./usePageAutoRefresh";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function createHarness(refresh: () => void | Promise<void>) {
  let intervalCallback: () => void = () => undefined;
  let visibilityCallback: () => void = () => undefined;
  let visible = true;
  let now = 0;
  const clearIntervalFn = vi.fn();
  const unsubscribeVisibility = vi.fn();
  const scheduler = createPageAutoRefreshScheduler({
    refresh,
    intervalMs: PAGE_AUTO_REFRESH_INTERVAL_MS,
    now: () => now,
    isVisible: () => visible,
    setIntervalFn: (callback, intervalMs) => {
      intervalCallback = callback;
      expect(intervalMs).toBe(PAGE_AUTO_REFRESH_INTERVAL_MS);
      return 7;
    },
    clearIntervalFn,
    subscribeVisibilityChange: (callback) => {
      visibilityCallback = callback;
      return unsubscribeVisibility;
    },
  });

  return {
    scheduler,
    clearIntervalFn,
    unsubscribeVisibility,
    tick: () => intervalCallback(),
    visibilityChanged: () => visibilityCallback(),
    setVisible: (next: boolean) => {
      visible = next;
    },
    advance: (milliseconds: number) => {
      now += milliseconds;
    },
  };
}

describe("createPageAutoRefreshScheduler", () => {
  it("refreshes every sixty seconds while visible", async () => {
    const refresh = vi.fn();
    const harness = createHarness(refresh);

    harness.advance(PAGE_AUTO_REFRESH_INTERVAL_MS);
    harness.tick();
    await Promise.resolve();

    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("does not overlap a pending refresh", async () => {
    const pending = deferred();
    const refresh = vi.fn(() => pending.promise);
    const harness = createHarness(refresh);

    const first = harness.scheduler.run();
    const second = await harness.scheduler.run();

    expect(second).toBe(false);
    expect(refresh).toHaveBeenCalledTimes(1);
    pending.resolve();
    await first;
  });

  it("pauses while hidden and catches up when visible", async () => {
    const refresh = vi.fn();
    const harness = createHarness(refresh);
    harness.setVisible(false);
    harness.advance(PAGE_AUTO_REFRESH_INTERVAL_MS);

    harness.tick();
    await Promise.resolve();
    expect(refresh).not.toHaveBeenCalled();

    harness.setVisible(true);
    harness.visibilityChanged();
    await Promise.resolve();
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("cleans up its timer and visibility listener", async () => {
    const refresh = vi.fn();
    const harness = createHarness(refresh);

    harness.scheduler.stop();

    expect(harness.clearIntervalFn).toHaveBeenCalledWith(7);
    expect(harness.unsubscribeVisibility).toHaveBeenCalledTimes(1);
    expect(await harness.scheduler.run()).toBe(false);
    expect(refresh).not.toHaveBeenCalled();
  });

  it("reports and contains automatic refresh failures", async () => {
    const error = new Error("offline");
    const onError = vi.fn();
    const scheduler = createPageAutoRefreshScheduler({
      refresh: () => Promise.reject(error),
      onError,
      setIntervalFn: () => 1,
      clearIntervalFn: () => undefined,
    });

    expect(await scheduler.run()).toBe(false);
    expect(onError).toHaveBeenCalledWith(error);
  });
});
