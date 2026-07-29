import { describe, expect, it } from "vitest";
import { clientMetricDisplay, type ClientMetricStatus } from "./clientMetricStatus";


function status(overrides: Partial<ClientMetricStatus> = {}): ClientMetricStatus {
  return {
    site_id: "newapi-us01",
    client_type: "newapi",
    last_attempt_at: "2026-07-19T01:02:05Z",
    last_success_at: "2026-07-19T01:02:05Z",
    last_bucket_at: "2026-07-19T01:01:00Z",
    last_quality: "complete",
    last_rpm: 12,
    last_tpm: 3456,
    consecutive_failures: 0,
    last_error: null,
    updated_at: "2026-07-19T01:02:05Z",
    ...overrides,
  };
}


describe("clientMetricDisplay", () => {
  it("formats complete reported metrics", () => {
    const display = clientMetricDisplay(status());

    expect(display.rpm).toBe("12");
    expect(display.tpm).toBe("3,456");
    expect(display.quality).toBe("完整");
    expect(display.tone).toBe("success");
  });

  it("shows missing and delayed metrics as no data", () => {
    for (const quality of ["missing", "delayed"] as const) {
      const display = clientMetricDisplay(status({
        last_quality: quality,
        last_rpm: null,
        last_tpm: null,
        consecutive_failures: 1,
      }));

      expect(display.rpm).toBe("无数据");
      expect(display.tpm).toBe("无数据");
      expect(display.tone).toBe("error");
    }
  });

  it("keeps confirmed zero traffic distinct from missing data", () => {
    const display = clientMetricDisplay(status({ last_rpm: 0, last_tpm: 0 }));

    expect(display.rpm).toBe("0");
    expect(display.tpm).toBe("0");
  });
});
