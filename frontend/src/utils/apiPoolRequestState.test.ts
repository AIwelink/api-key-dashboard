import { describe, expect, it } from "vitest";

import { isCurrentSiteRequest, mergeCapacitySummaryForRequest } from "./apiPoolRequestState";

describe("isCurrentSiteRequest", () => {
  it("rejects a groups response from the previously selected site", () => {
    expect(isCurrentSiteRequest("old-site", "current-site")).toBe(false);
    expect(isCurrentSiteRequest("current-site", "current-site")).toBe(true);
  });
});

describe("mergeCapacitySummaryForRequest", () => {
  it("does not let a stale site response overwrite the current group with the same id", () => {
    const groups = [{ id: 3, capacity_summary: { dynamic_five_hour_capacity_usd: 1_680 } }];

    const result = mergeCapacitySummaryForRequest(
      groups,
      "old-site:3:1:50:all",
      "current-site:3:1:50:all",
      3,
      { dynamic_five_hour_capacity_usd: 2_400 },
    );

    expect(result).toBe(groups);
    expect(result[0].capacity_summary.dynamic_five_hour_capacity_usd).toBe(1_680);
  });

  it("updates the matching request group", () => {
    const groups = [{ id: 3, capacity_summary: { dynamic_five_hour_capacity_usd: 1_680 } }];
    const requestKey = "current-site:3:1:50:all";

    const result = mergeCapacitySummaryForRequest(
      groups,
      requestKey,
      requestKey,
      3,
      { dynamic_five_hour_capacity_usd: 1_560 },
    );

    expect(result[0].capacity_summary.dynamic_five_hour_capacity_usd).toBe(1_560);
  });
});
