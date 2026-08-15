import { describe, expect, it } from "vitest";

import {
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

  it("provides every stable half-hour option", () => {
    const options = thirtyMinuteOptions();
    expect(options).toHaveLength(48);
    expect(options[0]).toEqual({ value: "00:00", label: "00:00" });
    expect(options.at(-1)).toEqual({ value: "23:30", label: "23:30" });
  });
});
