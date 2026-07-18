import { describe, expect, it } from "vitest";
import { concurrencyCoverageScalePercent, runwayScalePercent } from "./capacityScale";

describe("runwayScalePercent", () => {
  it("uses tiered hour thresholds and fills at 48 hours", () => {
    expect(runwayScalePercent(1, 3)).toBeCloseTo(100 / 6);
    expect(runwayScalePercent(3, 3)).toBeCloseTo(200 / 6);
    expect(runwayScalePercent(6, 3)).toBeCloseTo(300 / 6);
    expect(runwayScalePercent(13, 3)).toBeCloseTo(68.06, 1);
    expect(runwayScalePercent(24, 3)).toBeCloseTo(500 / 6);
    expect(runwayScalePercent(48, 3)).toBe(100);
  });
});

describe("concurrencyCoverageScalePercent", () => {
  it("keeps target coverage in the middle and fills at 5x", () => {
    expect(concurrencyCoverageScalePercent(1, 1.2)).toBeCloseTo(200 / 6);
    expect(concurrencyCoverageScalePercent(1.2, 1.2)).toBeCloseTo(300 / 6);
    expect(concurrencyCoverageScalePercent(1.33, 1.2)).toBeCloseTo(57.22, 1);
    expect(concurrencyCoverageScalePercent(1.5, 1.2)).toBeCloseTo(400 / 6);
    expect(concurrencyCoverageScalePercent(2, 1.2)).toBeCloseTo(500 / 6);
    expect(concurrencyCoverageScalePercent(5, 1.2)).toBe(100);
  });
});
