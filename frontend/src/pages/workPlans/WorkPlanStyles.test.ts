import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const globalStyles = readFileSync(new URL("../../../styles.css", import.meta.url), "utf8");
const workPlanStyles = readFileSync(new URL("../WorkPlansPage.css", import.meta.url), "utf8");

function zIndex(styles: string, selector: string) {
  const value = styleRule(styles, selector).match(/z-index:\s*(\d+)/)?.[1];
  if (!value) throw new Error(`Missing z-index for: ${selector}`);
  return Number(value);
}

function styleRule(styles: string, selector: string) {
  const rule = styles.match(new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{[^}]+\\}`))?.[0];
  if (!rule) throw new Error(`Missing style rule: ${selector}`);
  return rule;
}

describe("work plan overlay styles", () => {
  it("keeps confirmation dialogs above an open work-plan drawer", () => {
    expect(zIndex(globalStyles, ".confirm-backdrop")).toBeGreaterThan(
      zIndex(workPlanStyles, ".work-plan-drawer-layer"),
    );
  });

  it("keeps the layer visible through the drawer exit animation", () => {
    expect(styleRule(workPlanStyles, ".work-plan-drawer-layer")).toContain(
      "transition: visibility 0s linear 280ms;",
    );
    expect(styleRule(workPlanStyles, ".work-plan-drawer-layer.open")).toContain(
      "transition-delay: 0s;",
    );
    expect(workPlanStyles).toMatch(
      /@media \(prefers-reduced-motion: reduce\) \{\s*\.work-plan-drawer-layer,/,
    );
  });

  it("scopes neutral hover backgrounds for the backdrop and advanced dates", () => {
    expect(styleRule(workPlanStyles, ".work-plan-drawer-backdrop:hover")).toMatch(/background:/);
    expect(styleRule(workPlanStyles, ".work-plan-more-date-toggle:hover")).toMatch(/background:/);
  });
});
