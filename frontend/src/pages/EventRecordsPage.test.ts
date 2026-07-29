import { describe, expect, it } from "vitest";
import { changeFieldLabel, formatChangeValue } from "./eventRecordHistory";


describe("account history change formatting", () => {
  it("uses readable labels for common usage and subscription fields", () => {
    expect(changeFieldLabel("usage.codex_5h_used_percent")).toBe("5h 已用比例");
    expect(changeFieldLabel("subscription.plan_type")).toBe("订阅类型");
    expect(changeFieldLabel("usage.future_metric")).toBe("用量 · future_metric");
  });

  it("formats scalar and structured new values compactly", () => {
    expect(formatChangeValue(true)).toBe("是");
    expect(formatChangeValue(false)).toBe("否");
    expect(formatChangeValue(null)).toBe("空值");
    expect(formatChangeValue({ remaining: 12 })).toBe('{"remaining":12}');
  });
});
