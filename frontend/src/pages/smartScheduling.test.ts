import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  buildSmartSchedulingPayload,
  defaultSmartSchedulingRules,
  isCurrentSiteRequest,
  smartSchedulingRulesToForm,
} from "./smartScheduling";

const accountPoolsSource = readFileSync(new URL("./AccountPoolsPage.tsx", import.meta.url), "utf8");

describe("smart scheduling form", () => {
  it("rejects responses from an old site or request generation", () => {
    expect(isCurrentSiteRequest("site-a", "site-a", 3, 3)).toBe(true);
    expect(isCurrentSiteRequest("site-a", "site-b", 3, 3)).toBe(false);
    expect(isCurrentSiteRequest("site-a", "site-a", 2, 3)).toBe(false);
  });

  it("creates the confirmed defaults", () => {
    const form = smartSchedulingRulesToForm(defaultSmartSchedulingRules);

    expect(form.plus.automatic_priority).toBe("191");
    expect(form.k12.automatic_priority).toBe("91");
    expect(form.team.automatic_priority).toBe("41");
    expect(form.pro.automatic_priority).toBe("991");
    expect(form.pro.extreme_entry_percent).toBe("95");
    expect(form.plus.normal_concurrency).toBe("30");
    expect(form.plus.extreme_concurrency).toBe("100");
    expect(form.extreme.priority).toBe("10");
  });

  it("builds a complete numeric payload", () => {
    const form = smartSchedulingRulesToForm(defaultSmartSchedulingRules);

    const result = buildSmartSchedulingPayload(form);

    expect(result).toEqual({ ok: true, payload: { rules: defaultSmartSchedulingRules } });
  });

  it("rejects overlap and recovery at the entry threshold", () => {
    const form = smartSchedulingRulesToForm(defaultSmartSchedulingRules);
    form.plus.system_priority_max = "205";

    expect(buildSmartSchedulingPayload(form)).toEqual({
      ok: false,
      error: expect.stringContaining("区间"),
    });

    form.plus.system_priority_max = "199";
    form.plus.recovery_percent = "90";
    expect(buildSmartSchedulingPayload(form)).toEqual({
      ok: false,
      error: expect.stringContaining("恢复"),
    });
  });

  it("rejects an extreme priority outside its reserved range", () => {
    const form = smartSchedulingRulesToForm(defaultSmartSchedulingRules);
    form.extreme.priority = "21";

    expect(buildSmartSchedulingPayload(form)).toEqual({
      ok: false,
      error: expect.stringContaining("极限"),
    });
  });

  it("rejects blank, fractional, and non-numeric integer fields", () => {
    for (const value of ["", "1.5", "not-a-number"]) {
      const form = smartSchedulingRulesToForm(defaultSmartSchedulingRules);
      form.plus.normal_concurrency = value;

      expect(buildSmartSchedulingPayload(form)).toEqual({
        ok: false,
        error: expect.stringContaining("并发"),
      });
    }
  });
});

describe("smart scheduling operator controls", () => {
  it("renders the site rule editor and database-backed group strategies", () => {
    const schedulingSection = accountPoolsSource.match(
      /<section className="panel smart-scheduling-panel">[\s\S]*?<\/section>/,
    )?.[0] || "";

    expect(schedulingSection).toContain("智能调度");
    expect(schedulingSection).toContain("账号类型自动归档");
    expect(schedulingSection).toContain("7d 极限加速");
    expect(schedulingSection).toContain("type_priority_enabled");
    expect(schedulingSection).toContain("quota_acceleration_enabled");
    expect(schedulingSection).toContain("smartSchedulingMeta.lastRun?.scanned");
    expect(schedulingSection).toContain("smartSchedulingMeta.lastRun?.changed");
    expect(schedulingSection).toContain("smartSchedulingMeta.lastRun?.skipped");
    expect(schedulingSection).toContain("smartSchedulingMeta.lastRun?.failed");
    expect(schedulingSection).not.toMatch(/api-pools\/accounts|sub2api-sites\/[^`]*accounts/);
  });

  it("uses the settings and group endpoints without a frontend account scan", () => {
    expect(accountPoolsSource).toContain("/api-pools/smart-scheduling/settings?site_id=");
    expect(accountPoolsSource).toContain("/api-pools/observability/groups?site_id=");
    expect(accountPoolsSource).toContain("isCurrentSiteRequest(");
    expect(accountPoolsSource).toContain("disabled={siteSwitchingDisabled}");
  });
});
