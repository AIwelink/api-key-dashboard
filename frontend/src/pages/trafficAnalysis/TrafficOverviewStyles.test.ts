import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("../../../styles.css", import.meta.url), "utf8");
const marker = "/* Confirmed traffic and attribution dashboard */";
const dashboardStyles = styles.slice(styles.indexOf(marker));
const redesignMarker = "/* Open traffic data workspace redesign */";
const redesignStyles = styles.slice(styles.indexOf(redesignMarker));

function rule(selector: string) {
  const start = dashboardStyles.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`Missing final dashboard rule: ${selector}`);
  const end = dashboardStyles.indexOf("}", start);
  return dashboardStyles.slice(start, end + 1);
}

function redesignRule(selector: string) {
  const start = redesignStyles.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`Missing redesign rule: ${selector}`);
  const end = redesignStyles.indexOf("}", start);
  return redesignStyles.slice(start, end + 1);
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

  it("uses an indexed open workspace instead of framed section cards", () => {
    expect(styles).toContain(redesignMarker);
    expect(redesignRule(".traffic-overview-workspace")).toContain("grid-template-columns: 176px minmax(0, 1fr);");
    expect(redesignRule(".traffic-overview-query")).toContain("position: sticky;");

    const section = redesignRule(".traffic-overview-section");
    expect(section).toContain("background: transparent;");
    expect(section).not.toContain("border-radius");
    expect(section).not.toContain("box-shadow");
  });

  it("keeps the final KPI definition tooltip aligned inside the workspace", () => {
    expect(redesignStyles).not.toContain(
      ".traffic-overview-metric .metric-definition.align-end .metric-definition-tooltip",
    );
    const finalMetricTooltip = redesignRule(
      ".traffic-overview-metric:nth-child(6n) .metric-definition-tooltip",
    );
    expect(finalMetricTooltip).toContain("right: 0;");
    expect(finalMetricTooltip).toContain("left: auto;");
  });

  it("uses the agreed tablet and mobile KPI breakpoints", () => {
    const tabletStart = redesignStyles.indexOf("@media (max-width: 1179px)");
    const mobileStart = redesignStyles.indexOf("@media (max-width: 759px)");

    expect(tabletStart).toBeGreaterThanOrEqual(0);
    expect(mobileStart).toBeGreaterThan(tabletStart);

    const tabletStyles = redesignStyles.slice(tabletStart, mobileStart);
    const mobileStyles = redesignStyles.slice(mobileStart);
    expect(tabletStyles).toContain("grid-template-columns: repeat(3, minmax(0, 1fr));");
    expect(tabletStyles).toContain(".traffic-overview-metric:nth-child(3n) .metric-definition-tooltip");
    expect(tabletStyles).toContain(".traffic-overview-split");
    expect(mobileStyles).toContain("grid-template-columns: repeat(2, minmax(0, 1fr));");
    expect(mobileStyles).toContain(".traffic-overview-metric:nth-child(3n) .metric-definition-tooltip");
    expect(mobileStyles).toContain(".traffic-overview-metric:nth-child(2n) .metric-definition-tooltip");
  });
});
