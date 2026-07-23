import { describe, expect, it } from "vitest";

import type { RolePermissionsSettings } from "../types";
import { roleOptionsFromSettings } from "./UsersPage";


const settings: RolePermissionsSettings = {
  available_views: ["users"],
  role_order: ["owner", "admin", "support"],
  roles: {
    owner: { label: "owner", builtin: true, allowed_views: ["users"], default_view: "users" },
    admin: { label: "admin", builtin: true, allowed_views: ["users"], default_view: "users" },
    support: { label: "客服", builtin: false, allowed_views: [], default_view: null },
  },
};


describe("user role options", () => {
  it("builds user role options from backend settings", () => {
    expect(roleOptionsFromSettings(settings, false)).toEqual([
      { label: "admin", value: "admin" },
      { label: "客服", value: "support" },
    ]);
  });

  it("keeps owner available when editing an owner", () => {
    expect(roleOptionsFromSettings(settings, true)[0]).toEqual({ label: "owner", value: "owner" });
  });
});
