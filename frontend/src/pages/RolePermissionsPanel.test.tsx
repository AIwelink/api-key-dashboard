import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  RolePermissionsPanel,
  toggleRoleViewPermission,
  type RolePermissionsSettings,
} from "./ApiTokensPage";


const settings: RolePermissionsSettings = {
  available_views: ["traffic-analysis", "operations-management", "api-pools"],
  roles: {
    owner: { allowed_views: ["api-pools"], default_view: "api-pools" },
    admin: { allowed_views: ["api-pools"], default_view: "api-pools" },
    maintainer: { allowed_views: ["api-pools"], default_view: "api-pools" },
    operator: { allowed_views: ["traffic-analysis", "operations-management"], default_view: "traffic-analysis" },
    viewer: { allowed_views: ["api-pools"], default_view: "api-pools" },
  },
};

describe("role permissions panel", () => {
  it("renders the operator permissions returned by the backend", () => {
    const html = renderToStaticMarkup(
      <RolePermissionsPanel
        settings={settings}
        busy={false}
        onChange={() => undefined}
        onSave={() => undefined}
      />,
    );

    expect(html).toContain("权限管理");
    expect(html).toContain("运营");
    expect(html).toContain("访问流量分析");
    expect(html).toContain("运营管理");
    expect(html).toMatch(/<input[^>]*checked=""[^>]*value="traffic-analysis"/);
    expect(html).toMatch(/<input[^>]*checked=""[^>]*value="operations-management"/);
  });

  it("falls back to an allowed default view when the current default is removed", () => {
    const next = toggleRoleViewPermission(settings, "operator", "traffic-analysis");

    expect(next.roles.operator.allowed_views).toEqual(["operations-management"]);
    expect(next.roles.operator.default_view).toBe("operations-management");
  });
});
