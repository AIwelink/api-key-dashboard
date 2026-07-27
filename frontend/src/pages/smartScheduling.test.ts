import { describe, expect, it } from "vitest";
import {
  buildSmartSchedulingPayload,
  defaultSmartSchedulingRules,
  smartSchedulingRulesToForm,
} from "./smartScheduling";

describe("smart scheduling form", () => {
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
