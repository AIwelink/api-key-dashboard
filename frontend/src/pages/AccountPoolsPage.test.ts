import { describe, expect, it } from "vitest";
import { formatHealthDuration, formatHealthProbability } from "./AccountPoolsPage";
import { safeHttpUrl } from "../utils/url";


describe("safeHttpUrl", () => {
  it("allows http monitoring links and rejects unsafe schemes", () => {
    expect(safeHttpUrl("https://status.aiwelink.cn/dashboard/4")).toBe("https://status.aiwelink.cn/dashboard/4");
    expect(safeHttpUrl("http://127.0.0.1:3001/dashboard/4")).toBe("http://127.0.0.1:3001/dashboard/4");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("not-a-url")).toBeNull();
  });
});

describe("account health analysis formatting", () => {
  it("distinguishes missing probabilities from a real zero", () => {
    expect(formatHealthProbability(null)).toBe("-");
    expect(formatHealthProbability(0)).toBe("0.0%");
    expect(formatHealthProbability(0.125)).toBe("12.5%");
  });

  it("formats unavailable durations for operators", () => {
    expect(formatHealthDuration(null)).toBe("-");
    expect(formatHealthDuration(59)).toBe("59秒");
    expect(formatHealthDuration(90)).toBe("1分30秒");
    expect(formatHealthDuration(9_000)).toBe("2小时30分");
  });
});
