import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const poolSource = readFileSync(new URL("./pages/ApiPoolStatusPage.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

const renderedViews = [
  "work-plans",
  "upload",
  "todos",
  "push-error-todos",
  "accounts",
  "available-pool",
  "reserve-pool",
  "api-pools",
  "plus-self-produced",
  "traffic-analysis",
  "operations-management",
  "event-records",
  "alert-center",
  "pool-lifecycle",
  "auto-replenishment",
  "client-sites",
  "traffic-analysis-config",
  "agent-analysis",
  "agent-workbench",
  "system-management",
  "presence",
  "users",
  "logs",
] as const;

describe("site-wide motion system", () => {
  it("uses a keyed route stage without moving the toast into it", () => {
    expect(appSource).toContain('className="app-view-stage site-motion-scope"');
    expect(appSource).toContain('key={token ? view : "login"}');
    expect(styles).toContain("/* Site-wide motion system */");
    expect(styles).toContain(".app-view-stage {");
    expect(styles).toContain("animation: app-view-enter");
  });

  it("keeps every renderable route inside the shared motion scope", () => {
    for (const view of renderedViews) {
      expect(appSource, `missing routed page: ${view}`).toContain(`view === "${view}"`);
    }
    expect(appSource.indexOf('className="app-view-stage site-motion-scope"')).toBeLessThan(
      appSource.indexOf(`view === "${renderedViews[0]}"`),
    );
  });

  it("defines a delayed data synchronization rail and stable loading surface", () => {
    expect(styles).toContain(".data-sync-rail.is-active {");
    expect(styles).toContain("animation-delay: 100ms;");
    expect(styles).toContain(".data-loading-surface {");
    expect(styles).toContain(".table-loading-surface {");
  });

  it("connects API pool request state to accessible motion feedback", () => {
    expect(poolSource).toContain("aria-busy={pageBusy}");
    expect(poolSource).toContain('className={`data-sync-rail ${pageBusy ? "is-active" : ""}`}');
    expect(poolSource).toContain('className="table-loading-surface"');
  });

  it("choreographs content, tab changes, rows, lists, and feedback across every routed page", () => {
    const choreography = styles.slice(
      styles.indexOf("/* All-page content choreography */"),
      styles.indexOf(".data-loading-surface"),
    );
    expect(appSource).toContain('className="app-view-stage site-motion-scope"');
    expect(appSource).toContain('data-view={token ? view : "login"}');
    expect(styles).toContain("/* All-page content choreography */");
    expect(styles).toContain(".site-motion-scope > .view:not(");
    expect(styles).toContain('[class*="-tab-body"]');
    expect(styles).toContain("tbody > tr:nth-child(-n + 6)");
    expect(styles).toContain('[class$="-list"] > :nth-child(-n + 6)');
    expect(styles).toContain('.list > :nth-child(-n + 6)');
    expect(styles).toContain('[class*="-empty"]');
    expect(choreography).not.toContain('[class*="-error"]');
    expect(choreography).toContain('[role="alert"]');
    expect(styles).toContain(':nth-child(1):not([class*="backdrop"]):not([role="dialog"])');
    expect(choreography).toContain(':not(:has([class*="backdrop"], .floating-menu, [role="dialog"]))');
  });

  it("keeps generic state feedback out of pages with detailed choreography", () => {
    expect(styles).toContain(
      '.site-motion-scope > .view:not(.work-plan-page):not(.pool-status-page):not(.growth-workspace-page):not(.operations-workspace-page) :where([class*="-empty"], [role="status"], [role="alert"])',
    );
  });

  it("keeps generic row, list, and state choreography out of overlays", () => {
    const rowRule =
      styles.match(/([^{}]*tbody > tr:nth-child\(-n \+ 6\)[^{}]*)\{\s*animation: data-row-enter/)?.[1] || "";
    const stateRule =
      styles.match(/([^{}]*\[class\*="-empty"\][^{}]*)\{\s*animation: content-state-enter/)?.[1] || "";

    for (const rule of [rowRule, stateRule]) {
      expect(rule).toContain(':not([class*="backdrop"] *)');
      expect(rule).toContain(':not([role="dialog"] *)');
      expect(rule).toContain(':not(.floating-menu *)');
    }
  });

  it("removes transforms after entry animations so fixed overlays stay viewport-bound", () => {
    expect(styles).toContain("animation: app-view-enter var(--motion-page) var(--ease-enter) backwards;");
    expect(styles).toContain("animation: content-band-enter 340ms var(--ease-enter) backwards;");
    const appFrames = styles.match(/@keyframes app-view-enter\s*\{([\s\S]*?)\n\}/)?.[1] || "";
    expect(appFrames).not.toContain("transform:");
    const entryFrames = ["content-band-enter", "tab-stage-enter", "data-row-enter", "content-state-enter"];
    for (const name of entryFrames) {
      const frames = styles.match(new RegExp(`@keyframes ${name}\\s*\\{([\\s\\S]*?)\\n\\}`))?.[1] || "";
      expect(frames, `${name} must end without a transform containing block`).toContain("transform: none;");
    }
  });

  it("fully disables non-essential motion for reduced-motion users", () => {
    const reducedMotion = styles.slice(styles.lastIndexOf("@media (prefers-reduced-motion: reduce)"));
    expect(reducedMotion).toContain(".app-view-stage");
    expect(reducedMotion).toContain(".site-motion-scope");
    expect(reducedMotion).toContain(".list > *");
    expect(reducedMotion).toContain(".data-sync-rail");
    expect(reducedMotion).toContain("animation: none !important;");
    expect(reducedMotion).toContain("transition: none !important;");
  });
});
