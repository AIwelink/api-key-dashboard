import { describe, expect, it } from "vitest";

import type { UserRoleCatalog } from "../types";
import { canEditUser, roleOptionsFromCatalog } from "./UsersPage";


const catalog: UserRoleCatalog = {
  role_order: ["owner", "admin", "support"],
  roles: {
    owner: { label: "owner", builtin: true },
    admin: { label: "admin", builtin: true },
    support: { label: "客服", builtin: false },
  },
};


describe("user role options", () => {
  it("builds user role options from backend settings", () => {
    expect(roleOptionsFromCatalog(catalog, false)).toEqual([
      { label: "admin", value: "admin" },
      { label: "客服", value: "support" },
    ]);
  });

  it("keeps owner available when editing an owner", () => {
    expect(roleOptionsFromCatalog(catalog, true)[0]).toEqual({ label: "owner", value: "owner" });
  });

  it("only lets owners edit owner accounts", () => {
    const owner = { email: "owner@example.com", role: "owner" };

    expect(canEditUser(owner, false)).toBe(false);
    expect(canEditUser(owner, true)).toBe(true);
    expect(canEditUser({ email: "admin@example.com", role: "admin" }, false)).toBe(true);
  });
});
