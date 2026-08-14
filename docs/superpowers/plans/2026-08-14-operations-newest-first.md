# Operations Time Tables Newest First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display Operations Trend and Retention Cohort rows with the newest timestamp first.

**Architecture:** Add one pure, immutable sorting helper in the existing operations page module and reuse it for both visible time-series arrays after permission filtering. Keep API responses and non-time-based tables unchanged.

**Tech Stack:** React 19, TypeScript, Vitest, Vite

---

### Task 1: Newest-First Sorting Contract

**Files:**
- Modify: `frontend/src/pages/OperationsManagementPage.test.tsx`
- Modify: `frontend/src/pages/OperationsManagementPage.tsx`

- [ ] **Step 1: Write the failing immutable ordering test**

Import `sortNewestFirst` and add a test with rows ordered as older, latest, same-time A, and same-time B. Assert the returned IDs are latest, same-time A, same-time B, and older, and assert the original array remains in its initial order.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `npm test -- --run src/pages/OperationsManagementPage.test.tsx`

Expected: FAIL because `sortNewestFirst` is not exported.

- [ ] **Step 3: Add the minimal pure helper**

Add this exported helper near the existing operations ordering helpers:

```ts
export function sortNewestFirst<T>(items: T[], timestamp: (item: T) => string) {
  return [...items].sort((left, right) => (
    Date.parse(timestamp(right)) - Date.parse(timestamp(left))
  ));
}
```

- [ ] **Step 4: Apply it to both visible time tables**

Wrap the filtered retention rows with `sortNewestFirst(..., (item) => item.cohort_date)` and the filtered trend rows with `sortNewestFirst(..., (item) => item.bucket)`.

- [ ] **Step 5: Run the focused test and verify GREEN**

Run: `npm test -- --run src/pages/OperationsManagementPage.test.tsx`

Expected: the page test file passes with no failures.

### Task 2: Verification And Publication

**Files:**
- Verify: `frontend/src/pages/OperationsManagementPage.tsx`
- Verify: `frontend/src/pages/OperationsManagementPage.test.tsx`

- [ ] **Step 1: Run the complete frontend test suite**

Run: `npm test -- --run`

Expected: all test files pass.

- [ ] **Step 2: Build the production frontend**

Run: `npm run build`

Expected: TypeScript and Vite complete successfully.

- [ ] **Step 3: Check the final diff**

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 4: Commit and publish**

Stage only the design, plan, operations page, and page test. Commit the implementation, push `codex/operations-newest-first`, and create a draft PR targeting `achernar/dev`.
