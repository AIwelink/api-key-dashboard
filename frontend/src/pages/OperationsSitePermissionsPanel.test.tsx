import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  OperationsSitePermissionsPanel,
  toggleOperationsSitePermission,
} from "./OperationsSitePermissionsPanel";
import type { OperationsSitePermissionsSettings } from "../types";


const settings: OperationsSitePermissionsSettings = {
  available_sites: [
    { id: "aiwelink", label: "AIWeLink" },
    { id: "aigclink", label: "AIGCLink" },
  ],
  users: [
    {
      user_id: "owner@example.com",
      email: "owner@example.com",
      name: "Owner",
      role: "owner",
      status: "active",
      operations_site_ids: ["aiwelink", "aigclink"],
    },
    {
      user_id: "admin@example.com",
      email: "admin@example.com",
      name: "Admin",
      role: "admin",
      status: "active",
      operations_site_ids: [],
    },
    {
      user_id: "operator@example.com",
      email: "operator@example.com",
      name: "\u8fd0\u8425",
      role: "operator",
      status: "disabled",
      operations_site_ids: ["aiwelink"],
    },
  ],
};


describe("operations site permissions panel", () => {
  it("renders a user table with independent site columns and save action", () => {
    const html = renderToStaticMarkup(
      <OperationsSitePermissionsPanel
        settings={settings}
        busy={false}
        onChange={() => undefined}
        onSave={() => undefined}
      />,
    );

    expect(html).toContain("\u8fd0\u8425\u7ad9\u70b9\u6743\u9650");
    expect(html).toContain("\u7528\u6237");
    expect(html).toContain("\u90ae\u7bb1");
    expect(html).toContain("\u7528\u6237\u7c7b\u578b");
    expect(html).toContain("\u72b6\u6001");
    expect(html).toContain("AIWeLink");
    expect(html).toContain("AIGCLink");
    expect(html).toContain("\u4fdd\u5b58\u7ad9\u70b9\u6743\u9650");
    expect(html).toContain("\u5df2\u505c\u7528");
    expect(html).toMatch(/aria-label="admin@example.com AIWeLink"[^>]*type="checkbox"/);
    expect(html).not.toMatch(/aria-label="admin@example.com AIWeLink"[^>]*checked=""/);
  });

  it("toggles only the selected user and returns a new settings object", () => {
    const next = toggleOperationsSitePermission(settings, "admin@example.com", "aigclink");

    expect(next).not.toBe(settings);
    expect(next.users).not.toBe(settings.users);
    expect(next.users[0]).toBe(settings.users[0]);
    expect(next.users[1]).not.toBe(settings.users[1]);
    expect(next.users[1].operations_site_ids).toEqual(["aigclink"]);
    expect(next.users[2]).toBe(settings.users[2]);
    expect(settings.users[1].operations_site_ids).toEqual([]);
  });
});
