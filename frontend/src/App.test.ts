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

  it("shows owners the retained navigation groups without hidden account workflow pages", () => {
    const groups = getVisibleNavigationGroups(true);
    const visibleKeys = groups.flat().map(([key]) => key);

    expect(groups.every((group) => group.length > 0)).toBe(true);
    expect(groups.map((group) => group.map(([key]) => key))).toEqual([
      ["api-pools"],
      ["operations-management"],
      ["event-records", "alert-center", "pool-lifecycle", "client-sites"],
      ["agent-analysis", "agent-workbench", "api-tokens", "presence", "users", "logs"],
    ]);
    expect(visibleKeys).toContain("presence");
    hiddenViews.forEach((view) => expect(visibleKeys).not.toContain(view));
  });

  it("keeps presence owner-only while retaining general navigation for non-owners", () => {
    const groups = getVisibleNavigationGroups(false);
    const visibleKeys = groups.flat().map(([key]) => key);

    expect(groups.every((group) => group.length > 0)).toBe(true);
    expect(groups.map((group) => group.map(([key]) => key))).toEqual([
      ["api-pools"],
      ["operations-management"],
      ["event-records", "alert-center", "pool-lifecycle", "client-sites"],
      ["agent-analysis", "agent-workbench", "api-tokens", "users", "logs"],
    ]);
    expect(visibleKeys).not.toContain("presence");
    hiddenViews.forEach((view) => expect(visibleKeys).not.toContain(view));
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
    expect(viewFromPath("/operations-management")).toBe("operations-management");
    expect(viewFromPath("/")).toBe("api-pools");
    expect(viewFromPath("/unknown-page")).toBe("api-pools");
  });
});
