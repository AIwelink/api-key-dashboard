import { describe, expect, it } from "vitest";

import {
  fortyEightHourOptions,
  formatOffsetInterval,
  isoDateRange,
  normalizeSelectedDates,
  resolveWeekdays,
  thirtyMinuteOptions,
  validateSelectedDates,
} from "./dateSelection";

describe("work plan date selection", () => {
  it("builds inclusive ranges without browser timezone parsing", () => {
    expect(isoDateRange("2026-12-30", "2027-01-02")).toEqual([
      "2026-12-30",
      "2026-12-31",
      "2027-01-01",
      "2027-01-02",
    ]);
    expect(isoDateRange("2026-01-01", "2099-12-31", 6)).toHaveLength(6);
  });

  it("resolves weekdays chronologically and rejects more than five dates", () => {
    expect(resolveWeekdays("2026-08-15", "2026-08-23", [1, 3])).toEqual([
      "2026-08-17",
      "2026-08-19",
    ]);
    expect(
      validateSelectedDates([
        "2026-08-15",
        "2026-08-16",
        "2026-08-17",
        "2026-08-18",
        "2026-08-19",
        "2026-08-20",
      ]),
    ).toBe("一次最多添加 5 天计划，请缩小日期范围");
  });

  it("normalizes duplicate unsorted selections and reports an empty selection", () => {
    expect(normalizeSelectedDates(["2026-08-17", "2026-08-15", "2026-08-17"])).toEqual([
      "2026-08-15",
      "2026-08-17",
    ]);
    expect(validateSelectedDates([])).toBe("请至少选择 1 个计划日期");
  });

  it("treats cleared native date inputs as an empty selection", () => {
    expect(isoDateRange("", "2026-08-20", 6)).toEqual([]);
    expect(resolveWeekdays("2026-08-15", "", [1], 6)).toEqual([]);
    expect(normalizeSelectedDates([""])).toEqual([]);
  });

  it("provides every stable half-hour option", () => {
    const options = thirtyMinuteOptions();
    expect(options).toHaveLength(48);
    expect(options[0]).toEqual({ value: "00:00", label: "00:00" });
    expect(options.at(-1)).toEqual({ value: "23:30", label: "23:30" });
    const endOptions = thirtyMinuteOptions({ includeEndOfDay: true });
    expect(endOptions).toHaveLength(49);
    expect(endOptions.at(-1)).toEqual({ value: "24:00", label: "24:00" });
  });

  it("builds unambiguous 48 hour half-hour options", () => {
    const options = fortyEightHourOptions();

    expect(options).toHaveLength(97);
    expect(options[0]).toEqual({ value: 0, label: "当天 00:00" });
    expect(options[48]).toEqual({ value: 1_440, label: "次日 00:00" });
    expect(options[96]).toEqual({ value: 2_880, label: "两日后 00:00" });
  });

  it("formats cross-day offset intervals without duplicate clock ambiguity", () => {
    expect(formatOffsetInterval(22 * 60, 26 * 60)).toBe("当天 22:00 - 次日 02:00");
    expect(formatOffsetInterval(24 * 60, 48 * 60)).toBe("次日 00:00 - 两日后 00:00");
  });
});
