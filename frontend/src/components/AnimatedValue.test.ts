import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { changeDirection } from "./AnimatedValue";

const styles = readFileSync(new URL("../../styles.css", import.meta.url), "utf8");

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

describe("animated value typography", () => {
  it("keeps label typography selectors scoped to direct children", () => {
    expect(styles).toContain(".mini-metric > span {");
    expect(styles).toContain(".capacity-metric-value > span:not(.auto-refresh-value) {");
    expect(styles).toContain(".pool-status-page .concurrency-capacity-values > div > span {");
    expect(styles).toContain(".pool-health-main > span,");
  });
});

describe("percentage bar motion", () => {
  it("animates capacity, concurrency, and usage widths", () => {
    expect(styles).toMatch(/\.capacity-meter-fill\s*\{[^}]*transition: width/s);
    expect(styles).toMatch(/\.overall-fill,\s*\.usage-fill\s*\{[^}]*transition: width/s);
    expect(styles).toMatch(/\.pool-status-page \.concurrency-capacity-meter span\s*\{[^}]*transition: width/s);
    expect(styles).toContain(".capacity-meter.tiered::after {");
    expect(styles).toContain("calc(100% / 6)");
  });

  it("keeps the excellent capacity bar dynamic without a continuous paint animation", () => {
    const rule = styles.match(/\.capacity-meter-fill\.excellent\s*\{([^}]*)\}/s)?.[1] || "";

    expect(rule).toContain("background: #");
    expect(rule).not.toContain("repeating-linear-gradient");
    expect(rule).not.toContain("animation:");
    expect(rule).not.toContain("box-shadow: 0 0");
  });

  it("moves one slow highlight across the excellent bar using transform", () => {
    const waveRule = styles.match(/\.capacity-meter-fill\.excellent::after\s*\{([^}]*)\}/s)?.[1] || "";
    const waveFrames = styles.match(/@keyframes capacity-excellent-wave\s*\{([\s\S]*?)\n\}/)?.[1] || "";

    expect(waveRule).toContain('content: ""');
    expect(waveRule).toContain("animation: capacity-excellent-wave 6s");
    expect(waveFrames).toContain("translate3d");
    expect(waveFrames).not.toContain("background-position");
    expect(styles).not.toContain("repeating-linear-gradient(115deg");
  });
});
