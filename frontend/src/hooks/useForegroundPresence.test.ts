import { describe, expect, it, vi } from "vitest";
import { createForegroundPresenceScheduler, getPresenceClientIdentity, PRESENCE_HEARTBEAT_INTERVAL_MS } from "./useForegroundPresence";

function memoryStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => values.get(key) || null,
    setItem: (key: string, value: string) => values.set(key, value),
  };
}

describe("getPresenceClientIdentity", () => {
  it("shares a persistent client id while keeping each tab session distinct", () => {
    const local = memoryStorage();
    const firstTab = memoryStorage();
    const secondTab = memoryStorage();
    const randomId = vi.fn().mockReturnValueOnce("client-a").mockReturnValueOnce("tab-a").mockReturnValueOnce("tab-b");

    const first = getPresenceClientIdentity({ localStorage: local, sessionStorage: firstTab, randomId });
    const repeated = getPresenceClientIdentity({ localStorage: local, sessionStorage: firstTab, randomId });
    const second = getPresenceClientIdentity({ localStorage: local, sessionStorage: secondTab, randomId });

    expect(first).toEqual({ clientId: "client-a", sessionId: "tab-a" });
    expect(repeated).toEqual(first);
    expect(second).toEqual({ clientId: "client-a", sessionId: "tab-b" });
  });
});

describe("createForegroundPresenceScheduler", () => {
  it("reports only while the page is visible and focused", async () => {
    let foreground = false;
    let intervalCallback = () => undefined;
    let foregroundCallback = () => undefined;
    const heartbeat = vi.fn();
    const scheduler = createForegroundPresenceScheduler({
      heartbeat,
      isForeground: () => foreground,
      now: () => new Date("2026-07-18T09:00:00Z"),
      setIntervalFn: (callback, intervalMs) => {
        intervalCallback = callback;
        expect(intervalMs).toBe(PRESENCE_HEARTBEAT_INTERVAL_MS);
        return 7;
      },
      clearIntervalFn: () => undefined,
      subscribeForegroundChange: (callback) => {
        foregroundCallback = callback;
        return () => undefined;
      },
    });

    intervalCallback();
    await Promise.resolve();
    expect(heartbeat).not.toHaveBeenCalled();

    foreground = true;
    foregroundCallback();
    await Promise.resolve();
    expect(heartbeat).toHaveBeenCalledWith("2026-07-18T09:00:00.000Z");

    foreground = false;
    foregroundCallback();
    intervalCallback();
    await Promise.resolve();
    expect(heartbeat).toHaveBeenCalledTimes(1);
    scheduler.stop();
  });
});
