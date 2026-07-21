import { describe, expect, it } from "vitest";
import { getVisibleNavigationGroups, viewFromPath } from "./App";

describe("app navigation", () => {
  const hiddenViews = [
    "upload",
    "todos",
    "push-error-todos",
    "accounts",
    "available-pool",
    "reserve-pool",
  ];

  const visibleGroups = (role: "owner" | "admin" | "maintainer" | "viewer") =>
    getVisibleNavigationGroups(role).map((group) => group.map(([key]) => key));

  it("shows owners the retained navigation groups without hidden account workflow pages", () => {
    const groups = getVisibleNavigationGroups("owner");
    const visibleKeys = groups.flat().map(([key]) => key);

    expect(groups.every((group) => group.length > 0)).toBe(true);
    expect(groups.map((group) => group.map(([key]) => key))).toEqual([
      ["api-pools", "plus-self-produced"],
      ["traffic-analysis", "operations-management"],
      ["event-records", "alert-center", "pool-lifecycle", "client-sites", "traffic-analysis-config"],
      ["agent-analysis", "agent-workbench", "api-tokens", "presence", "users", "logs"],
    ]);
    expect(visibleKeys).toContain("presence");
    hiddenViews.forEach((view) => expect(visibleKeys).not.toContain(view));
  });

  it("shows growth database configuration to admins without owner presence", () => {
    const groups = getVisibleNavigationGroups("admin");
    const visibleKeys = groups.flat().map(([key]) => key);

    expect(groups.every((group) => group.length > 0)).toBe(true);
    expect(groups.map((group) => group.map(([key]) => key))).toEqual([
      ["api-pools", "plus-self-produced"],
      ["traffic-analysis", "operations-management"],
      ["event-records", "alert-center", "pool-lifecycle", "client-sites", "traffic-analysis-config"],
      ["agent-analysis", "agent-workbench", "api-tokens", "users", "logs"],
    ]);
    expect(visibleKeys).not.toContain("presence");
    hiddenViews.forEach((view) => expect(visibleKeys).not.toContain(view));
  });

  it("hides growth database configuration from maintainers and viewers", () => {
    expect(visibleGroups("maintainer").flat()).not.toContain("traffic-analysis-config");
    expect(visibleGroups("viewer").flat()).not.toContain("traffic-analysis-config");
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
    expect(viewFromPath("/traffic-analysis")).toBe("traffic-analysis");
    expect(viewFromPath("/traffic-analysis-config")).toBe("traffic-analysis-config");
    expect(viewFromPath("/operations-management")).toBe("operations-management");
    expect(viewFromPath("/plus-self-produced")).toBe("plus-self-produced");
    expect(viewFromPath("/")).toBe("api-pools");
    expect(viewFromPath("/unknown-page")).toBe("api-pools");
  });
});
