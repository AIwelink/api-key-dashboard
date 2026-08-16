import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const appSource = readFileSync(new URL("./App.tsx", import.meta.url), "utf8");
const poolSource = readFileSync(new URL("./pages/ApiPoolStatusPage.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles.css", import.meta.url), "utf8");

describe("site-wide motion system", () => {
  it("uses a keyed route stage without moving the toast into it", () => {
    expect(appSource).toContain('className="app-view-stage"');
    expect(appSource).toContain('key={token ? view : "login"}');
    expect(styles).toContain("/* Site-wide motion system */");
    expect(styles).toContain(".app-view-stage {");
    expect(styles).toContain("animation: app-view-enter");
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

  it("fully disables non-essential motion for reduced-motion users", () => {
    const reducedMotion = styles.slice(styles.lastIndexOf("@media (prefers-reduced-motion: reduce)"));
    expect(reducedMotion).toContain(".app-view-stage");
    expect(reducedMotion).toContain(".data-sync-rail");
    expect(reducedMotion).toContain("animation: none !important;");
    expect(reducedMotion).toContain("transition: none !important;");
  });
});
