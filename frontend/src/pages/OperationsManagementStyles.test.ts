import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const styles = readFileSync(new URL("./OperationsManagementPage.css", import.meta.url), "utf8");
const marker = "/* Open operations data workspace redesign */";
const redesignStyles = styles.slice(styles.indexOf(marker));

function rule(selector: string) {
  const start = redesignStyles.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`Missing operations redesign rule: ${selector}`);
  const end = redesignStyles.indexOf("}", start);
  return redesignStyles.slice(start, end + 1);
}

describe("operations management open workspace styles", () => {
  it("stages data bands, loading rows, and result overlays", () => {
    expect(styles).toContain("/* Operations motion layer */");
    expect(styles).toContain(".operations-workspace-page > .data-sync-rail {");
    expect(styles).toContain(".operations-tab-stage {");
    expect(styles).toContain(".operations-table-loading {");
    expect(styles).toContain(".operations-data-section tbody tr:nth-child(-n + 6) {");
    expect(styles).toContain("animation: operations-result-enter");
  });

  it("uses a sticky indexed workspace and open data sections", () => {
    expect(styles).toContain(marker);
    expect(rule(".operations-overview-workspace")).toContain("grid-template-columns: 176px minmax(0, 1fr);");
    expect(rule(".operations-query-bar")).toContain("position: sticky;");
    expect(rule(".operations-lifecycle-grid")).toContain("grid-template-columns: repeat(4, minmax(0, 1fr));");

    const section = rule(".operations-data-section");
    expect(section).toContain("background: transparent;");
    expect(section).not.toContain("border-radius");
    expect(section).not.toContain("box-shadow");
  });

  it("uses the agreed tablet and mobile KPI breakpoints", () => {
    const tabletStart = redesignStyles.indexOf("@media (max-width: 1179px)");
    const mobileStart = redesignStyles.indexOf("@media (max-width: 759px)");

    expect(tabletStart).toBeGreaterThanOrEqual(0);
    expect(mobileStart).toBeGreaterThan(tabletStart);

    const tabletStyles = redesignStyles.slice(tabletStart, mobileStart);
    const mobileStyles = redesignStyles.slice(mobileStart);
    expect(tabletStyles).toContain("grid-template-columns: repeat(3, minmax(0, 1fr));");
    expect(tabletStyles).toContain(".operations-workspace-page .operations-metric:nth-child(3n) .metric-definition-tooltip");
    expect(mobileStyles).toContain("grid-template-columns: repeat(2, minmax(0, 1fr));");
    expect(mobileStyles).toContain(".operations-workspace-page .operations-metric:nth-child(3n) .metric-definition-tooltip");
    expect(mobileStyles).toContain(".operations-workspace-page .operations-metric:nth-child(2n) .metric-definition-tooltip");
  });
});
