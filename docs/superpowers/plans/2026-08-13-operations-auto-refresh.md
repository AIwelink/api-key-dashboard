# Operations Overview Auto Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically refresh an open operations overview every 60 seconds as synchronized trend and cohort data advances.

**Architecture:** Reuse the existing visibility-aware page refresh hook and the page's parallel background overview loader. Isolate enable/pause rules in a pure function so command-state protection is explicit and testable.

**Tech Stack:** React 19, TypeScript, Vitest, Vite

---

### Task 1: Auto Refresh Decision

**Files:**
- Modify: `frontend/src/pages/OperationsManagementPage.test.tsx`
- Modify: `frontend/src/pages/OperationsManagementPage.tsx`

- [x] Add failing tests for overview-only refresh and command-state pauses.
- [x] Run the focused test and confirm the missing behavior fails.
- [x] Add the minimal pure decision helper.
- [x] Run the focused test and confirm it passes.

### Task 2: Hook Integration

**Files:**
- Modify: `frontend/src/pages/OperationsManagementPage.tsx`

- [x] Import `usePageAutoRefresh` directly from the shared hook.
- [x] Schedule `loadOverview(true)` with the predicate-derived enable/pause state.
- [x] Preserve the existing parallel overview requests and background error behavior.
- [x] Reject stale responses after a newer overview request or query change.
- [x] Run the page test and full frontend suite.

### Task 3: Verification And Publication

**Files:**
- Verify: `frontend/src/pages/OperationsManagementPage.tsx`
- Verify: `frontend/src/pages/OperationsManagementPage.test.tsx`

- [x] Run the production build.
- [x] Validate component-to-scheduler wiring and interval behavior with focused tests; the in-app browser control runtime was unavailable in this session.
- [x] Inspect the final diff and run `git diff --check`.
- [x] Commit and push `codex/operations-auto-refresh`.
- [x] Open a pull request targeting `achernar/dev`.
