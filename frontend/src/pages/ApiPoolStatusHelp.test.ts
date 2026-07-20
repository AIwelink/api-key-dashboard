import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./ApiPoolStatusPage.tsx", import.meta.url), "utf8");

describe("API pool metric help", () => {
  it("prefers the derived account type over the raw remote plan type", () => {
    expect(source).toContain("text(account.account_type) || text(account.plan_type) || text(credentials.plan_type)");
  });

  it("documents realtime runway and safe concurrency coverage", () => {
    expect(source).toContain('"实时可用时间": {');
    expect(source).toContain('"安全并发覆盖": {');
    expect(source).toContain("P50逐小时预测");
    expect(source).toContain("P90风险参考");
    expect(source).toContain("当前速度");
    expect(source).toContain("未来P50");
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
