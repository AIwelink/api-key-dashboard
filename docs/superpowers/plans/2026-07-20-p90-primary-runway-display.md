# P90 Primary Runway Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make P90 the primary runway value on the API pool status page while showing current-speed and P50 expected runways below it with clear hover explanations.

**Architecture:** Reuse the existing `CapacitySummary` fields and `CapacityMetric`/`MetricHelp` components. Compute the display hierarchy inside `CapacityRunwaySummary`; keep all backend calculations unchanged. Extend the existing source-contract test to lock the labels, order, and fallback markers.

**Tech Stack:** React, TypeScript, Vitest, Vite

---

### Task 1: P90 Runway Hierarchy

**Files:**
- Modify: `frontend/src/pages/ApiPoolStatusHelp.test.ts`
- Modify: `frontend/src/pages/ApiPoolStatusPage.tsx`

- [ ] **Step 1: Write the failing display-contract test**

Require the page source to contain `P90 保守可用时间`, `当前速度`, and `P50 期望`; assert that the current-speed label appears before the P50 label; require the P90 value to use `forecast_p90_runway_hours` with `actual_runway_hours` as fallback.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `npm.cmd test -- ApiPoolStatusHelp.test.ts`

Expected: FAIL because the current implementation still uses current-speed runway as the primary value.

- [ ] **Step 3: Implement the display hierarchy**

Inside `CapacityRunwaySummary`, compute whether an active P90 forecast exists, then select the primary runway, target, label, meter value, and tone. Render secondary values as JSX so `当前速度` and `P50 期望` each use `MetricHelp`, in that order. Use current speed as the primary fallback when P90 is unavailable.

- [ ] **Step 4: Update hover explanations**

Add help entries for `P90 保守可用时间`, `当前速度`, and `P50 期望`. State that P90 is conservative rather than most likely, current speed ignores future day/night variation, and P50 is the median seasonal path.

- [ ] **Step 5: Verify GREEN and production build**

Run: `npm.cmd test`

Expected: all frontend tests pass.

Run: `npm.cmd run build`

Expected: TypeScript and Vite production build pass; the existing chunk-size warning may remain.

- [ ] **Step 6: Commit**

Stage only the plan, component, and frontend test, then commit with `feat: make P90 the primary runway display`.
