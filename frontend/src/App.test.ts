import { describe, expect, it } from "vitest";
import { canAccessView, defaultViewForPermissions, getVisibleNavigationGroups, viewFromPath } from "./navigation";
import type { UserPermissions, ViewName } from "./types";

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
      ["api-pools", "plus-self-produced"],
      ["traffic-analysis", "operations-management"],
      ["event-records", "alert-center", "pool-lifecycle", "client-sites", "traffic-analysis-config"],
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
      ["api-pools", "plus-self-produced"],
      ["traffic-analysis", "operations-management"],
      ["event-records", "alert-center", "pool-lifecycle", "client-sites", "traffic-analysis-config"],
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
    expect(viewFromPath("/traffic-analysis")).toBe("traffic-analysis");
    expect(viewFromPath("/traffic-analysis-config")).toBe("traffic-analysis-config");
    expect(viewFromPath("/operations-management")).toBe("operations-management");
    expect(viewFromPath("/plus-self-produced")).toBe("plus-self-produced");
    expect(viewFromPath("/")).toBe("api-pools");
    expect(viewFromPath("/unknown-page")).toBe("api-pools");
  });
});
