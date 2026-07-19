import { describe, expect, it } from "vitest";

import { poolTrafficMetrics } from "./poolTrafficMetrics";

describe("poolTrafficMetrics", () => {
  it("shows the latest 5001 sample instead of the model pressure estimate", () => {
    const result = poolTrafficMetrics({
      traffic_site_id: "us06-5001",
      latest_tpm: 9_424_160,
      latest_rpm: 95,
      pressure_tpm: 15_862_446,
      pressure_rpm: 97,
    });

    expect(result).toEqual({ siteLabel: "5001", tpm: 9_424_160, rpm: 95 });
  });
});
