import { describe, expect, it } from "vitest";
import {
  concurrencyCoverageScalePercent,
  concurrencyCoverageTone,
  runwayScalePercent,
  runwayTone,
} from "./capacityScale";

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
  it("uses an operational scale that fills at 10x", () => {
    expect(concurrencyCoverageScalePercent(1, 1.2)).toBeCloseTo(100 / 6);
    expect(concurrencyCoverageScalePercent(1.5, 1.2)).toBeCloseTo(200 / 6);
    expect(concurrencyCoverageScalePercent(3, 1.2)).toBeCloseTo(300 / 6);
    expect(concurrencyCoverageScalePercent(5, 1.2)).toBeCloseTo(400 / 6);
    expect(concurrencyCoverageScalePercent(7.5, 1.2)).toBeCloseTo(500 / 6);
    expect(concurrencyCoverageScalePercent(10, 1.2)).toBe(100);
  });
});

describe("capacity scale tones", () => {
  it("uses purple only when realtime runway reaches its 48 hour ceiling", () => {
    expect(runwayTone(0.9, true)).toBe("danger");
    expect(runwayTone(2.9, true)).toBe("warning");
    expect(runwayTone(23.9, true)).toBe("success");
    expect(runwayTone(24, true)).toBe("info");
    expect(runwayTone(48, true)).toBe("excellent");
  });

  it("uses 1.5x, 3x, 5x and 10x as concurrency color boundaries", () => {
    expect(concurrencyCoverageTone(1.49, true)).toBe("danger");
    expect(concurrencyCoverageTone(1.5, true)).toBe("warning");
    expect(concurrencyCoverageTone(2.99, true)).toBe("warning");
    expect(concurrencyCoverageTone(3, true)).toBe("success");
    expect(concurrencyCoverageTone(4.99, true)).toBe("success");
    expect(concurrencyCoverageTone(5, true)).toBe("info");
    expect(concurrencyCoverageTone(9.99, true)).toBe("info");
    expect(concurrencyCoverageTone(10, true)).toBe("excellent");
  });

  it("keeps both metrics muted until realtime samples are ready", () => {
    expect(runwayTone(48, false)).toBe("muted");
    expect(concurrencyCoverageTone(10, false)).toBe("muted");
  });
});
