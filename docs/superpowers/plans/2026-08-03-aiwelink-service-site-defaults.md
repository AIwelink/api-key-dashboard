# AIWeLink Service Site Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Default authorized operations views and creation forms to AIWeLink, keep site choices ordered, and place campaign management before channel management.

**Architecture:** Keep the backend permission and API contracts unchanged. Add small pure helpers in `OperationsManagementPage.tsx` for stable site ordering and default resolution, make `SiteSelect` put concrete sites before the optional all-sites filter, and update the existing traffic workspace tab tuple. Add regression assertions to the existing page tests.

**Tech Stack:** React 19, TypeScript, Vitest, Vite

---

### Task 1: Add Red Tests For Site Defaults And Tab Order

**Files:**
- Modify: `frontend/src/pages/OperationsManagementPage.test.tsx`
- Modify: `frontend/src/pages/TrafficAnalysisPage.test.tsx`

- [x] **Step 1: Test preferred site ordering and fallback**

Import the new `orderOperationsSites` and `preferredOperationsSiteId` helpers. Assert that an input ordered `[aigclink, aiwelink]` returns `[aiwelink, aigclink]`, that both authorized sites resolve to `aiwelink`, and that an `aigclink`-only list resolves to `aigclink`.

- [x] **Step 2: Test the rendered operations selector defaults**

Render `OperationsManagementPage` with both authorized sites and assert the first site selector starts with `AIWeLink`, marks it selected, and places `全部站点` after concrete site options. Render with only `aigclink` and assert the selector contains no `AIWeLink` and selects `AIGCLink`.

- [x] **Step 3: Test the traffic workspace tab order**

Render the traffic workspace and assert the first occurrence of `活动管理` is before the first occurrence of `渠道管理`.

- [x] **Step 4: Run targeted tests and verify RED**

Run: `npm.cmd test -- src/pages/OperationsManagementPage.test.tsx src/pages/TrafficAnalysisPage.test.tsx`

Expected: FAIL because multiple-site filters currently default to `全部站点`, `SiteSelect` renders `全部站点` first, the new helpers are absent, and the traffic tab tuple still places channels before campaigns.

### Task 2: Implement Stable Defaults And Tab Order

**Files:**
- Modify: `frontend/src/pages/OperationsManagementPage.tsx`
- Modify: `frontend/src/pages/TrafficAnalysisPage.tsx`
- Test: `frontend/src/pages/OperationsManagementPage.test.tsx`
- Test: `frontend/src/pages/TrafficAnalysisPage.test.tsx`

- [x] **Step 1: Add pure site ordering and preferred-default helpers**

Export `orderOperationsSites(sites)` to return the known site priority `aiwelink`, then `aigclink`, while preserving unknown future values after known values. Export `preferredOperationsSiteId(sites)` to return `aiwelink` when present, otherwise the first available site, otherwise an empty string.

- [x] **Step 2: Apply helpers to authorized site state**

Build `allowedSites` from the helper, set `defaultSiteFilter` to `firstAllowedSiteId` for both single- and multi-site users, and make `normalizeSiteFilter` preserve an explicit empty value so users can still choose `全部站点`.

- [x] **Step 3: Reorder selector options**

Render concrete site options first in `SiteSelect`, then append `全部站点` when enabled. Keep value and change behavior unchanged.

- [x] **Step 4: Swap traffic workspace tabs**

Change only the tuple order to `overview`, `links`, `campaigns`, `channels`, `sites`.

- [x] **Step 5: Run targeted tests and verify GREEN**

Run: `npm.cmd test -- src/pages/OperationsManagementPage.test.tsx src/pages/TrafficAnalysisPage.test.tsx`

Expected: all targeted tests pass.

### Task 3: Verify The Frontend

**Files:**
- Verify: `frontend/src/pages/OperationsManagementPage.tsx`
- Verify: `frontend/src/pages/TrafficAnalysisPage.tsx`

- [x] **Step 1: Run complete frontend tests**

Run: `npm.cmd test`

Expected: all frontend test files pass with zero failures.

- [x] **Step 2: Run production build**

Run: `npm.cmd run build`

Expected: TypeScript and Vite build exit successfully.

- [x] **Step 3: Run diff hygiene checks**

Run: `git diff --check`

Expected: no whitespace errors.
