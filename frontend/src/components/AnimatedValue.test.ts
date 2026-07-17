import { describe, expect, it } from "vitest";
import { changeDirection } from "./AnimatedValue";

describe("changeDirection", () => {
  it("detects increases in formatted values", () => {
    expect(changeDirection("$1,204", "$1,260")).toBe("up");
    expect(changeDirection("91%", "96%")).toBe("up");
  });

  it("detects decreases in formatted values", () => {
    expect(changeDirection("2.4x", "1.9x")).toBe("down");
  });

  it("uses a neutral change for non-numeric values and ignores equal values", () => {
    expect(changeDirection("等待数据", "健康")).toBe("changed");
    expect(changeDirection("$120", "$120")).toBeNull();
  });
});
