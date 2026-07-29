import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./ApiPoolStatusPage.tsx", import.meta.url), "utf8");

describe("API pool forecast accuracy", () => {
  it("shows rolling accuracy evidence and settlement state", () => {
    expect(source).toContain("function ForecastAccuracySummary");
    expect(source).toContain("预测准确性");
    expect(source).toContain('["24h", "7d", "28d"]');
    expect(source).toContain("P50 WAPE");
    expect(source).toContain("P90 覆盖率");
    expect(source).toContain("P50 偏差");
    expect(source).toContain("Nowcast WAPE");
    expect(source).toContain("最终样本");
    expect(source).toContain("最后结算");
    expect(source).toContain("等待最终结算样本");
  });

  it("renders accuracy by forecast horizon", () => {
    expect(source).toContain("horizon_buckets");
    expect(source).toContain("预测步长");
    for (const label of ["1h", "2-3h", "4-6h", "7-12h", "13-24h"]) {
      expect(source).toContain(label);
    }
  });
});
