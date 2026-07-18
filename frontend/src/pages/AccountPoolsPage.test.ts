import { describe, expect, it } from "vitest";
import { safeHttpUrl } from "../utils/url";


describe("safeHttpUrl", () => {
  it("allows http monitoring links and rejects unsafe schemes", () => {
    expect(safeHttpUrl("https://status.aiwelink.cn/dashboard/4")).toBe("https://status.aiwelink.cn/dashboard/4");
    expect(safeHttpUrl("http://127.0.0.1:3001/dashboard/4")).toBe("http://127.0.0.1:3001/dashboard/4");
    expect(safeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(safeHttpUrl("not-a-url")).toBeNull();
  });
});
