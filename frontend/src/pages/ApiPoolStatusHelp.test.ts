import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./ApiPoolStatusPage.tsx", import.meta.url), "utf8");

describe("API pool metric help", () => {
  it("prefers the derived account type over the raw remote plan type", () => {
    expect(source).toContain("text(account.account_type) || text(account.plan_type) || text(credentials.plan_type)");
  });

  it("uses P90 as the primary runway with current speed and P50 below", () => {
    expect(source).toContain('label={hasP90Runway ? "P90 保守可用时间" : "当前速度可用时间"}');
    expect(source).toContain("const primaryRunwayHours = hasP90Runway ? summary?.forecast_p90_runway_hours : summary?.actual_runway_hours");
    expect(source).toContain('<MetricHelp helpKey="当前速度">当前速度</MetricHelp>');
    expect(source).toContain('<MetricHelp helpKey="P50 期望">P50 期望</MetricHelp>');
    expect(source.indexOf('helpKey="当前速度"')).toBeLessThan(source.indexOf('helpKey="P50 期望"'));
  });

  it("documents P90, current-speed, P50, and safe concurrency metrics", () => {
    expect(source).toContain('"P90 保守可用时间": {');
    expect(source).toContain("历史相似上涨后的延续率");
    expect(source).toContain('"当前速度": {');
    expect(source).toContain('"P50 期望": {');
    expect(source).toContain('"安全并发覆盖": {');
    expect(source).toContain("高消耗风险边界");
    expect(source).toContain("不是最可能发生的时长");
    expect(source).toContain("中位需求路径");
    expect(source).toContain("account_cost分钟速率");
    expect(source).toContain("超过24小时显示为 >24小时");
    expect(source).toContain("10x及以上为紫色顶级");
  });

  it("documents pressure stage inputs and every operational stage", () => {
    expect(source).toContain('"压力阶段": {');
    expect(source).toContain("TPM/RPM的EMA5、EMA15、EMA60");
    for (const label of ["等待数据", "稳定", "压力传导", "加速上涨", "峰值保底", "回落观察", "库存风险"]) {
      expect(source).toContain(label);
    }
  });

  it("shows the backend current consumption rate beside the pressure stage", () => {
    expect(source).toContain("current_consumption_rate_usd_per_hour?: number | null;");
    expect(source).toContain('current_consumption_rate_source?: "previous_full_hour" | "current_hour_prorated" | "unavailable";');
    expect(source).toContain("current_consumption_rate_elapsed_minutes?: number | null;");
    expect(source).toContain("current_consumption_rate_hour?: string | null;");

    const rateMetric = source.indexOf('label="当前消耗速度"');
    const pressureMetric = source.indexOf('label="压力阶段"');
    expect(rateMetric).toBeGreaterThan(-1);
    expect(rateMetric).toBeLessThan(pressureMetric);
    expect(source).toContain('if (source === "previous_full_hour") return "上一完整小时";');
    expect(source).toContain('if (source === "current_hour_prorated") return "本小时折算";');
    expect(source).toContain('return "等待小时消耗数据";');
  });

  it("documents the current consumption rate guard and formula", () => {
    expect(source).toContain('"当前消耗速度": {');
    expect(source).toContain("整点后前5分钟");
    expect(source).toContain("本小时累计消耗 / 已过分钟 * 60");
    expect(source).toContain("当前站点、当前分组");
  });
});
