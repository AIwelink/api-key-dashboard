import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  RolePermissionsPanel,
  toggleRoleViewPermission,
} from "./RolePermissionsPanel";
import type { RolePermissionsSettings } from "../types";


const settings: RolePermissionsSettings = {
  available_views: ["traffic-analysis", "operations-management", "system-management", "api-tokens", "api-pools"],
  role_order: ["owner", "admin", "maintainer", "operator", "viewer", "support"],
  roles: {
    owner: { label: "owner", builtin: true, allowed_views: ["api-pools", "system-management", "api-tokens"], default_view: "api-pools" },
    admin: { label: "admin", builtin: true, allowed_views: ["api-pools", "system-management"], default_view: "api-pools" },
    maintainer: { label: "maintainer", builtin: true, allowed_views: ["api-pools"], default_view: "api-pools" },
    operator: { label: "运营", builtin: true, allowed_views: ["traffic-analysis", "operations-management"], default_view: "traffic-analysis" },
    viewer: { label: "viewer", builtin: true, allowed_views: ["api-pools"], default_view: "api-pools" },
    support: { label: "客服", builtin: false, allowed_views: [], default_view: null },
  },
};

describe("role permissions panel", () => {
  it("renders the operator permissions returned by the backend", () => {
    const html = renderToStaticMarkup(
      <RolePermissionsPanel
        settings={settings}
        busy={false}
        onChange={() => undefined}
        onCreate={async () => undefined}
        onDelete={async () => undefined}
        onSave={() => undefined}
      />,
    );

    expect(html).toContain("权限管理");
    expect(html).toContain("运营");
    expect(html).toContain("访问流量分析");
    expect(html).toContain("运营管理");
    expect(html).toContain("客服");
    expect(html).toContain("添加用户类型");
    expect(html).toContain("API Key 管理（仅 owner）");
    expect(html).toContain("系统管理（仅 owner/admin）");
    expect(html).toContain('title="删除客服"');
    expect(html).toMatch(/<input[^>]*checked=""[^>]*value="traffic-analysis"/);
    expect(html).toMatch(/<input[^>]*checked=""[^>]*value="operations-management"/);
    expect(html).toMatch(/<input[^>]*disabled=""[^>]*value="api-tokens"/);
    expect(html).toMatch(/<input[^>]*disabled=""[^>]*value="system-management"/);
  });

  it("falls back to an allowed default view when the current default is removed", () => {
    const next = toggleRoleViewPermission(settings, "operator", "traffic-analysis");

    expect(next.roles.operator.allowed_views).toEqual(["operations-management"]);
    expect(next.roles.operator.default_view).toBe("operations-management");
  });

  it("does not toggle backend-managed system permissions", () => {
    expect(toggleRoleViewPermission(settings, "admin", "system-management")).toBe(settings);
    expect(toggleRoleViewPermission(settings, "owner", "api-tokens")).toBe(settings);
  });
});
