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
});
