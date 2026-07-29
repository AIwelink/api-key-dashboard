import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("../../../styles.css", import.meta.url), "utf8");
const marker = "/* Confirmed traffic and attribution dashboard */";
const dashboardStyles = styles.slice(styles.indexOf(marker));

function rule(selector: string) {
  const start = dashboardStyles.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`Missing final dashboard rule: ${selector}`);
  const end = dashboardStyles.indexOf("}", start);
  return dashboardStyles.slice(start, end + 1);
}

describe("traffic overview layout styles", () => {
  it("keeps table regions horizontal-only so the page owns vertical wheel scrolling", () => {
    const tableScroll = rule(".traffic-overview-table-scroll");

    expect(tableScroll).toContain("overflow-x: auto;");
    expect(tableScroll).toContain("overflow-y: visible;");
    expect(tableScroll).not.toContain("max-height");
    expect(tableScroll).not.toContain("overscroll-behavior");
  });

  it("uses the compact operational density contract", () => {
    expect(rule(".traffic-overview")).toContain("gap: 12px;");
    expect(rule(".traffic-overview-primary-metric")).toContain("min-height: 96px;");
    expect(rule(".traffic-overview-section-head")).toContain("min-height: 48px;");
    expect(rule(".traffic-overview-table-scroll td")).toContain("height: 40px;");

    const compactControls = dashboardStyles.match(
      /\.traffic-overview-query select,\s*\.traffic-overview-query > button\s*\{[^}]+\}/,
    )?.[0] || "";
    expect(compactControls).toContain("height: 34px;");
    expect(compactControls).toContain("min-height: 34px;");
  });
});
