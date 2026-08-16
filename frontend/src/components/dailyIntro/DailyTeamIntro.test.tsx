// @vitest-environment jsdom

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DAILY_TEAM_MESSAGE,
  DailyTeamIntro,
  DailyTeamIntroGate,
} from "./DailyTeamIntro";
import type { DailyIntroStorage } from "./dailyIntro";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function memoryStorage(): DailyIntroStorage {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
  };
}

function stubMotionPreference(reducedMotion: boolean) {
  vi.stubGlobal("matchMedia", vi.fn(() => ({
    matches: reducedMotion,
    media: "(prefers-reduced-motion: reduce)",
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })));
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: globalThis.matchMedia,
  });
}

describe("DailyTeamIntro", () => {
  let container: HTMLDivElement | null = null;
  let root: Root | null = null;

  beforeEach(() => {
    stubMotionPreference(false);
  });

  afterEach(() => {
    act(() => root?.unmount());
    container?.remove();
    document.documentElement.style.overflow = "";
    root = null;
    container = null;
    vi.unstubAllGlobals();
  });

  async function renderIntro(onComplete = vi.fn()) {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root?.render(<DailyTeamIntro onComplete={onComplete} />));
    return onComplete;
  }

  it("renders the real logo, complete quote, and accessible skip control", () => {
    const html = renderToStaticMarkup(<DailyTeamIntro onComplete={() => undefined} />);

    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('alt="AIwelink"');
    expect(html).toContain("AIwelink_logo_bule_A.png");
    expect(html).toContain("世事浮沉，皆为淬炼；商海浩瀚，唯有坚毅。");
    expect(html).toContain("愿君心有赤焰，足履薄冰，终见日月新天。");
    expect(html).toContain(`aria-label="${DAILY_TEAM_MESSAGE}"`);
    expect(html).toContain('aria-label="跳过开场"');
  });

  it("runs the standard 3.9 second sequence and restores document scrolling", async () => {
    vi.useFakeTimers();
    try {
      document.documentElement.style.overflow = "clip";
      document.documentElement.style.scrollbarGutter = "auto";
      const onComplete = await renderIntro();

      expect(document.documentElement.style.overflow).toBe("hidden");
      expect(document.documentElement.style.scrollbarGutter).toBe("auto");
      expect(container?.querySelector(".daily-team-intro")?.getAttribute("data-stage")).toBe("opening");

      await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
      expect(container?.querySelector(".daily-team-intro")?.getAttribute("data-stage")).toBe("exiting");
      expect(onComplete).not.toHaveBeenCalled();

      await act(async () => { await vi.advanceTimersByTimeAsync(900); });
      expect(onComplete).toHaveBeenCalledOnce();

      act(() => root?.unmount());
      root = null;
      expect(document.documentElement.style.overflow).toBe("clip");
      expect(document.documentElement.style.scrollbarGutter).toBe("auto");
    } finally {
      vi.useRealTimers();
    }
  });

  it("exits quickly when Escape or the skip control is used", async () => {
    vi.useFakeTimers();
    try {
      const onComplete = await renderIntro();
      await act(async () => {
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      });
      expect(container?.querySelector(".daily-team-intro")?.getAttribute("data-stage")).toBe("exiting");
      await act(async () => { await vi.advanceTimersByTimeAsync(360); });
      expect(onComplete).toHaveBeenCalledOnce();

      act(() => root?.unmount());
      root = null;
      const secondComplete = await renderIntro();
      await act(async () => {
        container?.querySelector<HTMLButtonElement>('button[aria-label="跳过开场"]')?.click();
      });
      await act(async () => { await vi.advanceTimersByTimeAsync(360); });
      expect(secondComplete).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });

  it("uses a static 600ms sequence for reduced motion", async () => {
    vi.useFakeTimers();
    try {
      stubMotionPreference(true);
      const onComplete = await renderIntro();

      expect(container?.querySelector(".daily-team-intro")?.getAttribute("data-stage")).toBe("reduced");
      await act(async () => { await vi.advanceTimersByTimeAsync(300); });
      expect(container?.querySelector(".daily-team-intro")?.getAttribute("data-stage")).toBe("exiting");
      await act(async () => { await vi.advanceTimersByTimeAsync(300); });
      expect(onComplete).toHaveBeenCalledOnce();
    } finally {
      vi.useRealTimers();
    }
  });

  it("mounts the gate only for the first claim by a member on a Shanghai day", async () => {
    const storage = memoryStorage();
    const user = { id: "member-1", email: "member@example.com" };
    const now = new Date("2026-08-17T00:00:00Z");

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root?.render(
      <DailyTeamIntroGate now={now} storage={storage} user={user} />,
    ));
    expect(container.querySelector(".daily-team-intro")).not.toBeNull();

    act(() => root?.unmount());
    container.remove();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root?.render(
      <DailyTeamIntroGate now={now} storage={storage} user={user} />,
    ));
    expect(container.querySelector(".daily-team-intro")).toBeNull();
  });

  it("finishes an Escape exit when the authenticated member object refreshes", async () => {
    vi.useFakeTimers();
    try {
      const storage = memoryStorage();
      const now = new Date("2026-08-17T00:00:00Z");
      const user = { id: "member-1", email: "member@example.com" };

      container = document.createElement("div");
      document.body.appendChild(container);
      root = createRoot(container);
      await act(async () => root?.render(
        <DailyTeamIntroGate now={now} storage={storage} user={user} />,
      ));
      await act(async () => {
        window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
      });
      await act(async () => root?.render(
        <DailyTeamIntroGate now={now} storage={storage} user={{ ...user }} />,
      ));
      await act(async () => { await vi.advanceTimersByTimeAsync(360); });

      expect(container.querySelector(".daily-team-intro")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("mounts the authenticated intro gate before the application shell", () => {
    const appSource = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");
    const gateIndex = appSource.indexOf("<DailyTeamIntroGate");
    const shellIndex = appSource.indexOf('<div className={`app-shell');

    expect(appSource).toContain("key={dailyIntroIdentity(user)}");
    expect(gateIndex).toBeGreaterThan(-1);
    expect(gateIndex).toBeLessThan(shellIndex);
  });
});
