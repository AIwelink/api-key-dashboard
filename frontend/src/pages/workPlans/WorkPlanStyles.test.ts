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
  const rule = styles.match(new RegExp(`(?:^|\\n)${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{[^}]+\\}`))?.[0];
  if (!rule) throw new Error(`Missing style rule: ${selector}`);
  return rule;
}

describe("work plan overlay styles", () => {
  it("gives the timeline 1.8 times more daily space while narrowing the member column", () => {
    expect(styleRule(workPlanStyles, ".work-plan-page")).toContain(
      "--work-plan-member-column-width: 176px;",
    );
    expect(styleRule(workPlanStyles, ".work-plan-page")).toContain(
      "--work-plan-day-width: 342px;",
    );
    expect(styleRule(workPlanStyles, ".work-plan-gantt")).toContain(
      "grid-template-columns: var(--work-plan-member-column-width)",
    );
    expect(styleRule(workPlanStyles, ".work-plan-timeline-header")).toContain(
      "minmax(var(--work-plan-day-width), 1fr)",
    );
  });

  it("contains the wide timeline and closed drawer without creating page overflow", () => {
    expect(styleRule(workPlanStyles, ".work-plan-schedule")).toContain("max-width: 100%;");
    expect(styleRule(workPlanStyles, ".work-plan-schedule-scroll")).toContain("width: 100%;");
    expect(styleRule(workPlanStyles, ".work-plan-schedule-scroll")).toContain("overflow-x: auto;");
    expect(styleRule(workPlanStyles, ".work-plan-schedule-scroll")).toContain("overflow-y: hidden;");
    expect(styleRule(workPlanStyles, ".work-plan-drawer-layer")).toContain("overflow: hidden;");
  });

  it("packs overview metrics and filters into one compact command bar", () => {
    expect(styleRule(workPlanStyles, ".work-plan-command-bar")).toContain("display: flex;");
    expect(styleRule(workPlanStyles, ".work-plan-summary-band > div")).toContain("min-height: 48px;");
    expect(styleRule(workPlanStyles, ".work-plan-summary-band strong")).toContain("font-size: 18px;");
    expect(workPlanStyles).toMatch(
      /@media \(max-width: 720px\)[\s\S]*?\.work-plan-summary-band > div\s*\{[^}]*min-height:\s*40px;/,
    );
  });

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

  it("animates each work-plan surface from its interaction origin", () => {
    expect(styleRule(workPlanStyles, ".work-plan-drawer.open .work-plan-drawer-header"))
      .toContain("animation:");
    expect(styleRule(workPlanStyles, ".work-plan-more-dates")).toContain("animation:");
    expect(styleRule(workPlanStyles, ".work-plan-detail-popover")).toContain("animation:");
    expect(styleRule(workPlanStyles, ".work-plan-priority-popover")).toContain("animation:");
  });

  it("animates global confirmations and toast feedback", () => {
    expect(styleRule(globalStyles, ".confirm-backdrop")).toContain("animation:");
    expect(styleRule(globalStyles, ".confirm-dialog")).toContain("animation:");
    expect(styleRule(globalStyles, ".toast")).toContain("animation:");
  });

  it("disables overlay motion when reduced motion is requested", () => {
    expect(workPlanStyles).toContain(
      ".work-plan-priority-popover {\n    animation: none !important;\n    transition: none !important;\n  }",
    );
    expect(globalStyles).toContain(
      ".confirm-backdrop,\n  .confirm-dialog,\n  .toast {\n    animation: none !important;\n    transition: none !important;\n  }",
    );
  });
});
