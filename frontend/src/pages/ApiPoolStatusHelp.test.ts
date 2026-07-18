import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(new URL("./ApiPoolStatusPage.tsx", import.meta.url), "utf8");

describe("API pool metric help", () => {
  it("documents realtime runway and safe concurrency coverage", () => {
    expect(source).toContain('"实时可用时间": {');
    expect(source).toContain('"安全并发覆盖": {');
    expect(source).toContain("48小时及以上为紫色顶级");
    expect(source).toContain("10x及以上为紫色顶级");
  });
});
