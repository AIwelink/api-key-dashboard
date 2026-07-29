import { describe, expect, it } from "vitest";

import {
  buildOperationsSitePermissionsPayload,
  mergeRolePermissionsSettings,
  shouldApplyOperationsSitePermissionsRefresh,
  shouldApplyRolePermissionsRefresh,
  shouldPauseSystemAutoRefresh,
} from "./ApiTokensPage";
import type { OperationsSitePermissionsSettings, RolePermissionsSettings } from "../types";


describe("system management auto refresh", () => {
  it("pauses permission refresh while edits are unsaved", () => {
    expect(shouldPauseSystemAutoRefresh("permissions", false, null, true)).toBe(true);
  });

  it("does not pause other tabs because permission edits are dirty", () => {
    expect(shouldPauseSystemAutoRefresh("notifications", false, null, true)).toBe(false);
  });

  it("ignores an in-flight automatic refresh after edits become dirty", () => {
    expect(shouldApplyRolePermissionsRefresh(true, false)).toBe(false);
    expect(shouldApplyRolePermissionsRefresh(true, true)).toBe(true);
  });

  it("keeps unsaved role edits when a role lifecycle request returns fresh settings", () => {
    const local: RolePermissionsSettings = {
      available_views: ["todos", "users"],
      role_order: ["owner", "support"],
      roles: {
        owner: { label: "owner", builtin: true, allowed_views: ["users"], default_view: "users" },
        support: { label: "客服", builtin: false, allowed_views: ["todos"], default_view: "todos" },
      },
    };
    const remote: RolePermissionsSettings = {
      available_views: ["todos", "users"],
      role_order: ["owner", "support", "sales"],
      roles: {
        owner: { label: "owner", builtin: true, allowed_views: ["users"], default_view: "users" },
        support: { label: "Support", builtin: false, allowed_views: [], default_view: null },
        sales: { label: "销售", builtin: false, allowed_views: [], default_view: null },
      },
    };

    const merged = mergeRolePermissionsSettings(remote, local);

    expect(merged.roles.support).toEqual(local.roles.support);
    expect(merged.roles.sales).toEqual(remote.roles.sales);
  });
});

describe("operations site permission settings", () => {
  it("pauses the permissions tab while personal site edits are unsaved", () => {
    expect(shouldPauseSystemAutoRefresh("permissions", false, null, false, true)).toBe(true);
    expect(shouldPauseSystemAutoRefresh("notifications", false, null, false, true)).toBe(false);
  });

  it("protects unsaved personal site edits from automatic refresh", () => {
    expect(shouldApplyOperationsSitePermissionsRefresh(true, false)).toBe(false);
    expect(shouldApplyOperationsSitePermissionsRefresh(true, true)).toBe(true);
  });

  it("builds a complete user mapping without presentation fields", () => {
    const settings: OperationsSitePermissionsSettings = {
      available_sites: [
        { id: "aiwelink", label: "AIWeLink" },
        { id: "aigclink", label: "AIGCLink" },
      ],
      users: [
        {
          user_id: "owner@example.com",
          email: "owner@example.com",
          role: "owner",
          status: "active",
          operations_site_ids: ["aiwelink"],
        },
        {
          user_id: "operator@example.com",
          operations_site_ids: [],
        },
      ],
    };

    expect(buildOperationsSitePermissionsPayload(settings)).toEqual({
      users: [
        { user_id: "owner@example.com", operations_site_ids: ["aiwelink"] },
        { user_id: "operator@example.com", operations_site_ids: [] },
      ],
    });
  });
});
