import { describe, expect, it } from "vitest";
import { formatHealthDuration, formatHealthLifetimeRange } from "./AccountPoolsPage";
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
  it("formats the lifetime distribution for operators", () => {
    expect(formatHealthLifetimeRange(null, null, null)).toBe("-");
    expect(formatHealthLifetimeRange(3_600, 7_200, 10_800)).toBe(
      "最短 1小时 · 中位 2小时 · 最长 3小时",
    );
  });

  it("formats account lifetimes for operators", () => {
    expect(formatHealthDuration(null)).toBe("-");
    expect(formatHealthDuration(59)).toBe("59秒");
    expect(formatHealthDuration(90)).toBe("1分30秒");
    expect(formatHealthDuration(9_000)).toBe("2小时30分");
  });
});
