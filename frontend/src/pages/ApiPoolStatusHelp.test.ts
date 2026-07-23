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
});
