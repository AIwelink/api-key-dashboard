# Traffic Overview Scroll And Density Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the traffic analytics page use page-level vertical scrolling while showing the same data in a denser operational layout.

**Architecture:** Keep `TrafficOverview.tsx` and all data contracts unchanged. Add a source-level CSS regression test for the final dashboard override block, then update that override block so table regions only scroll horizontally and the existing sections use smaller stable dimensions.

**Tech Stack:** React 19, TypeScript, CSS, Vitest, Vite

---

### Task 1: Lock The Scroll Contract

**Files:**
- Create: `frontend/src/pages/trafficAnalysis/TrafficOverviewStyles.test.ts`
- Read: `frontend/styles.css`

- [x] **Step 1: Write the failing scroll regression test**

Read `styles.css`, isolate the rules after `/* Confirmed traffic and attribution dashboard */`, and assert that `.traffic-overview-table-scroll` declares `overflow-x: auto`, `overflow-y: visible`, and contains neither `max-height` nor `overscroll-behavior`.

- [x] **Step 2: Write the failing density regression test**

Assert the final override block uses a 12px page gap, control heights no larger than 34px, primary metric height no larger than 104px, section headers no larger than 48px, and table cell height no larger than 40px.

- [x] **Step 3: Run the targeted test and verify RED**

Run: `npm.cmd test -- src/pages/trafficAnalysis/TrafficOverviewStyles.test.ts`

Expected: FAIL because the current final override still uses `gap: 16px`, 36px controls, 126px primary metrics, 58px headers, 48px cells, and inherited vertical table scrolling.

### Task 2: Implement The Confirmed CSS Design

**Files:**
- Modify: `frontend/styles.css`
- Test: `frontend/src/pages/trafficAnalysis/TrafficOverviewStyles.test.ts`

- [x] **Step 1: Make table regions horizontal-only scroll containers**

In the final dashboard override, set `.traffic-overview-table-scroll` to `overflow-x: auto` and `overflow-y: visible`, remove its height limit and containment, retain thin native scrollbars and touch momentum scrolling, and keep the existing focus-visible outline.

- [x] **Step 2: Tighten the page-level controls and summary sections**

Use a 12px page gap, 10px by 12px filter padding, 34px controls, 96px primary metrics, 48px section headers, and smaller context/capability/summary padding without changing responsive column counts.

- [x] **Step 3: Tighten tables and diagnostics**

Use 40px table cells, narrower safe minimum table widths, compact status pills, pagination, and quality diagnostics. Preserve readable 11px minimum text, non-overlapping content, and horizontal access to every column.

- [x] **Step 4: Run the targeted tests and verify GREEN**

Run: `npm.cmd test -- src/pages/trafficAnalysis/TrafficOverviewStyles.test.ts src/pages/trafficAnalysis/TrafficOverview.test.tsx`

Expected: both test files pass.

### Task 3: Verify Production Behavior

**Files:**
- Verify: `frontend/styles.css`
- Verify: `frontend/src/pages/trafficAnalysis/TrafficOverview.tsx`

- [x] **Step 1: Run the complete frontend test suite**

Run: `npm.cmd test`

Expected: all frontend tests pass with zero failures.

- [x] **Step 2: Run the production build**

Run: `npm.cmd run build`

Expected: TypeScript compilation and Vite production build exit successfully.

- [x] **Step 3: Verify desktop and mobile computed layout**

At desktop and mobile viewports, confirm table regions compute to horizontal-only overflow, the document has no global horizontal overflow, ordinary vertical scrolling over a table advances the page, and no text overlaps or clips.
