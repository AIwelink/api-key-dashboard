import { describe, expect, it } from "vitest";

import type { User, UserRoleCatalog } from "../types";
import {
  authorizationLabel,
  canEditUser,
  roleOptionsFromCatalog,
  sortUsersForManagement,
  userEmailLabel,
  userManagementActionLabel,
} from "./UsersPage";


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

describe("Feishu user authorization presentation", () => {
  const active: User = {
    id: "active@example.com",
    email: "active@example.com",
    role: "maintainer",
    authorization_status: "active",
    feishu_bound: true,
  };
  const pending: User = {
    id: "pending@example.com",
    email: "pending@example.com",
    role: "viewer",
    authorization_status: "pending",
    feishu_bound: true,
  };

  it("places pending users first without mutating the API result", () => {
    const source = [active, pending];

    expect(sortUsersForManagement(source).map((user) => user.id)).toEqual([
      "pending@example.com",
      "active@example.com",
    ]);
    expect(source).toEqual([active, pending]);
  });

  it("uses explicit binding labels and a permission assignment action", () => {
    expect(authorizationLabel(pending)).toBe("飞书用户 · 待分配权限");
    expect(authorizationLabel(active)).toBe("飞书已绑定");
    expect(authorizationLabel({ ...active, feishu_bound: false })).toBe("未绑定飞书");
    expect(userManagementActionLabel(pending)).toBe("分配权限");
    expect(userManagementActionLabel(active)).toBe("编辑");
  });

  it("labels an internal placeholder without exposing its value", () => {
    expect(userEmailLabel({ email: null, email_is_placeholder: true })).toBe("飞书未提供邮箱");
    expect(userEmailLabel(active)).toBe("active@example.com");
  });
});
