import { describe, expect, it } from "vitest";
import { formatOnlineMinutes, presenceDaysRecentFirst, presenceSegmentTone, visiblePresenceDays } from "./presenceTimeline";

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

  it("puts the most recent date first without reversing its 00:00 to 24:00 segments", () => {
    const olderSegments = Array<number | null>(48).fill(0);
    olderSegments.fill(20, 0, 6);
    const recentSegments = Array<number | null>(48).fill(null);
    recentSegments.fill(80, 12, 18);

    const timeline = presenceDaysRecentFirst([
      { date: "2026-07-17", segments: olderSegments },
      { date: "2026-07-18", segments: recentSegments },
    ]);

    expect(timeline.map((item) => item.date)).toEqual(["2026-07-18", "2026-07-17"]);
    expect(timeline[0].segments).toEqual(recentSegments);
  });

  it("shows the latest seven days by default and all days on demand", () => {
    const days = Array.from({ length: 10 }, (_, index) => ({ date: `2026-07-${String(index + 1).padStart(2, "0")}` }));

    expect(visiblePresenceDays(days, false).map((item) => item.date)).toEqual([
      "2026-07-10",
      "2026-07-09",
      "2026-07-08",
      "2026-07-07",
      "2026-07-06",
      "2026-07-05",
      "2026-07-04",
    ]);
    expect(visiblePresenceDays(days, true)).toHaveLength(10);
  });
});
