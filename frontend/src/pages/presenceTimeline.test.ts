import { describe, expect, it } from "vitest";
import { formatOnlineMinutes, presenceSegmentTone } from "./presenceTimeline";

describe("presence timeline presentation", () => {
  it("maps unavailable, offline and increasing online ratios to gray-green tones", () => {
    expect(presenceSegmentTone(null)).toBe("future");
    expect(presenceSegmentTone(0)).toBe("offline");
    expect(presenceSegmentTone(20)).toBe("low");
    expect(presenceSegmentTone(60)).toBe("medium");
    expect(presenceSegmentTone(100)).toBe("high");
  });

  it("formats accumulated online time without hiding long durations", () => {
    expect(formatOnlineMinutes(45)).toBe("45分钟");
    expect(formatOnlineMinutes(150)).toBe("2小时30分钟");
    expect(formatOnlineMinutes(3_030)).toBe("2天2小时30分钟");
  });
});
