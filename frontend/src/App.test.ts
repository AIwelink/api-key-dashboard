import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const toastHookHarness = vi.hoisted(() => {
  let callback: ((message: string, isError?: boolean) => void) | null = null;
  const timerRef: { current: number | null } = { current: null };
  let cleanup: (() => void) | null = null;

  return {
    reset() {
      callback = null;
      timerRef.current = null;
      cleanup = null;
    },
    runCleanup() {
      cleanup?.();
    },
    useCallback(next: (message: string, isError?: boolean) => void) {
      callback ??= next;
      return callback;
    },
    useEffect(effect: () => void | (() => void)) {
      if (cleanup === null) cleanup = effect() ?? null;
    },
    useRef() {
      return timerRef;
    },
  };
});

vi.mock("react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react")>()),
  useCallback: toastHookHarness.useCallback,
  useEffect: toastHookHarness.useEffect,
  useRef: toastHookHarness.useRef,
}));

import { useStableToast } from "./App";
import { canAccessView, defaultViewForPermissions, getVisibleNavigationGroups, viewFromPath } from "./navigation";
import type { UserPermissions, ViewName } from "./types";

describe("app toast", () => {
  const scheduled = new Map<number, () => void>();
  let nextTimerId = 1;
  const clearTimeout = vi.fn((timerId: number) => {
    scheduled.delete(timerId);
  });
  const setTimeout = vi.fn((callback: () => void) => {
    const timerId = nextTimerId;
    nextTimerId += 1;
    scheduled.set(timerId, callback);
    return timerId;
  });
  const runTimer = (timerId: number) => {
    const callback = scheduled.get(timerId);
    scheduled.delete(timerId);
    callback?.();
  };

  beforeEach(() => {
    toastHookHarness.reset();
    scheduled.clear();
    nextTimerId = 1;
    clearTimeout.mockClear();
    setTimeout.mockClear();
    vi.stubGlobal("window", { clearTimeout, setTimeout });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps the callback stable across renders and replaces the pending dismissal", () => {
    const setToast = vi.fn();

    const first = useStableToast(setToast);
    first("第一次通知");
    const second = useStableToast(setToast);
    second("第二次通知", true);

    expect(second).toBe(first);
    expect(clearTimeout).toHaveBeenCalledWith(1);
    expect([...scheduled]).toHaveLength(1);
    runTimer(2);
    expect(setToast.mock.calls.filter(([value]) => value === null)).toHaveLength(1);

    second("等待卸载");
    toastHookHarness.runCleanup();
    expect(clearTimeout).toHaveBeenLastCalledWith(3);
    expect([...scheduled]).toHaveLength(0);
  });
});

describe("app navigation", () => {
  const hiddenViews = [
    "upload",
    "todos",
    "push-error-todos",
    "accounts",
    "available-pool",
    "reserve-pool",
  ];

  const permissions = (allowedViews: ViewName[], defaultView = allowedViews[0] || "api-pools"): UserPermissions => ({
    allowed_views: allowedViews,
    default_view: defaultView,
  });

  const ownerPermissions = permissions([
    "work-plans",
    "upload",
    "todos",
    "push-error-todos",
    "accounts",
    "available-pool",
    "reserve-pool",
    "api-pools",
    "plus-self-produced",
    "traffic-analysis",
    "operations-management",
    "event-records",
    "alert-center",
    "pool-lifecycle",
    "auto-replenishment",
    "client-sites",
    "traffic-analysis-config",
    "agent-analysis",
    "agent-workbench",
    "system-management" as ViewName,
    "api-tokens",
    "presence",
    "users",
    "logs",
  ]);

  const adminPermissions = permissions(
    ownerPermissions.allowed_views.filter((view) => view !== "presence" && view !== "api-tokens"),
  );

  const visibleGroups = (userPermissions: UserPermissions) =>
    getVisibleNavigationGroups(userPermissions).map((group) => group.map(([key]) => key));

  it("shows owners the retained navigation groups without hidden account workflow pages", () => {
    const groups = getVisibleNavigationGroups(ownerPermissions);
    const visibleKeys = groups.flat().map(([key]) => key);

    expect(groups.every((group) => group.length > 0)).toBe(true);
    expect(groups.map((group) => group.map(([key]) => key))).toEqual([
      ["work-plans"],
      ["api-pools", "plus-self-produced"],
      ["traffic-analysis", "operations-management"],
      ["event-records", "alert-center", "pool-lifecycle", "auto-replenishment", "client-sites", "traffic-analysis-config"],
      ["agent-analysis", "agent-workbench", "system-management", "presence", "users", "logs"],
    ]);
    expect(visibleKeys).toContain("presence");
    hiddenViews.forEach((view) => expect(visibleKeys).not.toContain(view));
  });

  it("shows growth database configuration to admins without owner presence", () => {
    const groups = getVisibleNavigationGroups(adminPermissions);
    const visibleKeys = groups.flat().map(([key]) => key);

    expect(groups.every((group) => group.length > 0)).toBe(true);
    expect(groups.map((group) => group.map(([key]) => key))).toEqual([
      ["work-plans"],
      ["api-pools", "plus-self-produced"],
      ["traffic-analysis", "operations-management"],
      ["event-records", "alert-center", "pool-lifecycle", "auto-replenishment", "client-sites", "traffic-analysis-config"],
      ["agent-analysis", "agent-workbench", "system-management", "users", "logs"],
    ]);
    expect(visibleKeys).not.toContain("presence");
    expect(visibleKeys).not.toContain("api-tokens");
    hiddenViews.forEach((view) => expect(visibleKeys).not.toContain(view));
  });

  it("uses backend permissions to decide traffic analysis access", () => {
    const maintainerPermissions = permissions(["api-pools", "plus-self-produced"]);
    const customPermissions = permissions(["traffic-analysis", "traffic-analysis-config"], "traffic-analysis");

    expect(canAccessView(ownerPermissions, "traffic-analysis")).toBe(true);
    expect(canAccessView(adminPermissions, "traffic-analysis-config")).toBe(true);
    expect(canAccessView(maintainerPermissions, "traffic-analysis")).toBe(false);
    expect(canAccessView(customPermissions, "traffic-analysis")).toBe(true);
    expect(canAccessView(customPermissions, "api-pools")).toBe(false);
  });

  it("limits operators to the views returned by the backend", () => {
    const operatorPermissions = permissions(["traffic-analysis", "operations-management"], "traffic-analysis");

    expect(visibleGroups(operatorPermissions)).toEqual([["traffic-analysis", "operations-management"]]);
  });

  it("blocks direct view access outside the backend permission list", () => {
    const operatorPermissions = permissions(["traffic-analysis", "operations-management"], "traffic-analysis");

    expect(defaultViewForPermissions(operatorPermissions)).toBe("traffic-analysis");
    expect(canAccessView(operatorPermissions, "traffic-analysis")).toBe(true);
    expect(canAccessView(operatorPermissions, "operations-management")).toBe(true);
    expect(canAccessView(operatorPermissions, "api-pools")).toBe(false);
    expect(canAccessView(operatorPermissions, "traffic-analysis-config")).toBe(false);
    expect(canAccessView(operatorPermissions, "users")).toBe(false);
    expect(canAccessView(ownerPermissions, "users")).toBe(true);
  });

  it("keeps navigation driven by changed backend permission lists", () => {
    const changedPermissions = permissions(["operations-management", "users"], "operations-management");

    expect(defaultViewForPermissions(changedPermissions)).toBe("operations-management");
    expect(visibleGroups(changedPermissions)).toEqual([["operations-management"], ["users"]]);
  });

  it("maps the API key capability default to the system management page", () => {
    const capabilityDefault = permissions(["system-management", "api-tokens"], "api-tokens");

    expect(defaultViewForPermissions(capabilityDefault)).toBe("system-management");
  });

  it("keeps hidden account workflow pages directly addressable", () => {
    expect(viewFromPath("/upload-accounts")).toBe("upload");
    expect(viewFromPath("/todo-and-error-accounts")).toBe("todos");
    expect(viewFromPath("/question-account-assignment")).toBe("push-error-todos");
    expect(viewFromPath("/accounts")).toBe("accounts");
    expect(viewFromPath("/available-pool")).toBe("available-pool");
    expect(viewFromPath("/reserve-pool")).toBe("reserve-pool");
  });

  it("uses the API pools page for root and unknown paths", () => {
    expect(viewFromPath("/work-plans")).toBe("work-plans");
    expect(viewFromPath("/traffic-analysis")).toBe("traffic-analysis");
    expect(viewFromPath("/traffic-analysis-config")).toBe("traffic-analysis-config");
    expect(viewFromPath("/operations-management")).toBe("operations-management");
    expect(viewFromPath("/plus-self-produced")).toBe("plus-self-produced");
    expect(viewFromPath("/auto-replenishment")).toBe("auto-replenishment");
    expect(viewFromPath("/")).toBe("api-pools");
    expect(viewFromPath("/unknown-page")).toBe("api-pools");
  });

  it("places work plans in the first visible navigation group", () => {
    const result = getVisibleNavigationGroups(permissions(["api-pools", "work-plans"]));
    expect(result[0]).toEqual([["work-plans", "工作计划"]]);
  });
});
