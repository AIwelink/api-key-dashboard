import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  AutoReplenishmentSettingsForm,
  type AutoReplenishmentFormValue,
  type AutoReplenishmentSettings,
} from "./AutoReplenishmentPage";


const settings: AutoReplenishmentSettings = {
  provider: "sogouedu",
  base_url: "https://sogouedu.cc",
  enabled: false,
  username: "",
  password_configured: false,
  minimum_account_count: 2,
  minimum_runway_minutes: 5,
  product: "oauth_7d",
  local_account_type: "team",
  target_site_id: "us06-5001",
  target_group_id: null,
  target_group_name: "plus账号池01",
};

const form: AutoReplenishmentFormValue = {
  enabled: false,
  username: "",
  password: "",
  minimum_account_count: 2,
  minimum_runway_minutes: 5,
};

const callbacks = {
  onChange: () => undefined,
  onSave: () => undefined,
  onTest: () => undefined,
};

describe("auto replenishment settings form", () => {
  it("renders fixed supplier mapping and safe defaults", () => {
    const html = renderToStaticMarkup(
      <AutoReplenishmentSettingsForm
        settings={settings}
        form={form}
        loading={false}
        saving={false}
        testing={false}
        {...callbacks}
      />,
    );

    expect(html).toContain("自动补号");
    expect(html).toContain("SogouEdu");
    expect(html).toContain("https://sogouedu.cc");
    expect(html).toContain("oauth_7d");
    expect(html).toContain("普通 Team");
    expect(html).toContain("us06-5001");
    expect(html).toContain("plus账号池01");
    expect(html).toMatch(/name="minimum_account_count"[^>]*value="2"/);
    expect(html).toMatch(/name="minimum_runway_minutes"[^>]*value="5"/);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>测试连接<\/button>/);
  });

  it("shows configured and last test summaries without rendering secrets", () => {
    const configured: AutoReplenishmentSettings = {
      ...settings,
      username: "buyer",
      password_configured: true,
      target_group_id: 3,
      last_test_at: "2026-08-01T00:00:00Z",
      last_test_ok: true,
      last_test_balance: {
        balance_fen: 10_000,
        held_fen: 2_800,
        available_fen: 7_200,
        currency: "CNY",
      },
      last_test_inventory: {
        available: 18,
        missing: 0,
        needs_production: false,
        estimated_unit_price_fen: 500,
      },
    };
    const html = renderToStaticMarkup(
      <AutoReplenishmentSettingsForm
        settings={configured}
        form={{ ...form, username: "buyer" }}
        loading={false}
        saving={false}
        testing={false}
        {...callbacks}
      />,
    );

    expect(html).toContain("密码已配置");
    expect(html).toContain("¥72.00");
    expect(html).toContain("18");
    expect(html).toContain("连接正常");
    expect(html).not.toContain("supplier-password");
    expect(html).not.toContain("customer-token");
    expect(html).not.toMatch(/<button[^>]*disabled=""[^>]*>测试连接<\/button>/);
  });
});
