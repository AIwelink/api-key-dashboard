import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const globalStyles = readFileSync(new URL("../../../styles.css", import.meta.url), "utf8");
const workPlanStyles = readFileSync(new URL("../WorkPlansPage.css", import.meta.url), "utf8");

function zIndex(styles: string, selector: string) {
  const rule = styles.match(new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{[^}]+\\}`))?.[0];
  if (!rule) throw new Error(`Missing style rule: ${selector}`);
  const value = rule.match(/z-index:\s*(\d+)/)?.[1];
  if (!value) throw new Error(`Missing z-index for: ${selector}`);
  return Number(value);
}

describe("work plan overlay styles", () => {
  it("keeps confirmation dialogs above an open work-plan drawer", () => {
    expect(zIndex(globalStyles, ".confirm-backdrop")).toBeGreaterThan(
      zIndex(workPlanStyles, ".work-plan-drawer-layer"),
    );
  });
});
