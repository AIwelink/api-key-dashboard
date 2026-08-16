import { describe, expect, it } from "vitest";

import { findChangedSegmentKeys, memberEntryDelay } from "./workPlanMotion";

describe("work plan motion helpers", () => {
  it("stagger only the first three visible member rows", () => {
    expect([0, 1, 2, 3].map(memberEntryDelay)).toEqual([220, 260, 300, 260]);
  });

  it("detects new and changed timeline segments", () => {
    const previous = [{ key: "member:active:a", state: "active", startAt: "09", endAt: "12" }];
    const next = [
      previous[0],
      { key: "member:cancelled:b", state: "cancelled", startAt: "12", endAt: "13" },
      { key: "member:active:a", state: "active", startAt: "09", endAt: "13" },
    ];
    expect(findChangedSegmentKeys(previous, next)).toEqual(new Set([
      "member:cancelled:b",
      "member:active:a",
    ]));
  });

  it("treats an identical empty snapshot as unchanged", () => {
    expect(findChangedSegmentKeys([], [])).toEqual(new Set());
  });
});
