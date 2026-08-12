# Operations Internal User Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the operations overview exclude recognized internal users by default and immediately reclassify their historical aggregates.

**Architecture:** Keep the existing snapshot and aggregate model. Change the React default segment to `ordinary`, make cache invalidation understand tuple site scopes, and rebuild the affected site's historical aggregates inside the internal-user write transaction under the existing site advisory lock.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy async PostgreSQL, unittest, React 19, TypeScript, Vitest.

---

### Task 1: Fix site-scoped response cache invalidation

**Files:**
- Modify: `backend/app/modules/operations/cache.py`
- Test: `backend/tests/test_operations_repository.py`

- [ ] **Step 1: Write the failing nested-scope cache test**

Add a test that caches keys containing `("aiwelink", "aigclink")` and `("other",)`, calls `invalidate(site_id="aiwelink")`, then asserts only the first loader runs again.

- [ ] **Step 2: Run the focused test and verify failure**

Run: `.\.venv\Scripts\python.exe -B -m unittest tests.test_operations_repository.OperationsCacheTests.test_cache_invalidates_nested_site_scope`

Expected: FAIL because `site_id in key` does not inspect the tuple site scope.

- [ ] **Step 3: Implement recursive cache-key membership**

Add a small private predicate that checks tuple/list/set/frozenset key parts and use it from `OperationsResponseCache.invalidate`.

- [ ] **Step 4: Run the focused cache tests**

Run: `.\.venv\Scripts\python.exe -B -m unittest tests.test_operations_repository.OperationsCacheTests`

Expected: PASS.

### Task 2: Rebuild historical aggregates after internal-user recognition

**Files:**
- Modify: `backend/app/modules/operations/service.py`
- Test: `backend/tests/test_operations_routes.py`

- [ ] **Step 1: Write failing service tests**

Cover two cases: a recognized create acquires the site lock and calls `replace_affected_aggregates` with the historical start; a pending create does not rebuild aggregates.

- [ ] **Step 2: Run the focused tests and verify failure**

Run: `.\.venv\Scripts\python.exe -B -m unittest tests.test_operations_routes.OperationsInternalUserServiceTests`

Expected: FAIL because the service only creates the configuration and invalidates the cache.

- [ ] **Step 3: Implement atomic reclassification**

Inside the existing write connection, acquire `acquire_operations_sync_lock`, create or update the internal-user record, and when the result is recognized call:

```python
await repository.replace_affected_aggregates(
    connection,
    site_id=site_id,
    start_at=HISTORICAL_CONVERSION_RATE_START,
    end_at=datetime.now(UTC),
)
```

Keep cache invalidation after the transaction exits successfully.

- [ ] **Step 4: Run operations service and repository tests**

Run: `.\.venv\Scripts\python.exe -B -m unittest tests.test_operations_routes tests.test_operations_repository`

Expected: PASS.

### Task 3: Default the operations overview to ordinary users

**Files:**
- Modify: `frontend/src/pages/OperationsManagementPage.tsx`
- Test: `frontend/src/pages/OperationsManagementPage.test.tsx`

- [ ] **Step 1: Write the failing default-segment test**

Render `OperationsManagementPage` and assert the `ordinary` option is selected while the `all` option remains available.

- [ ] **Step 2: Run the focused frontend test and verify failure**

Run: `npm test -- --run src/pages/OperationsManagementPage.test.tsx`

Expected: FAIL because component state initializes and resets to `segment: "all"`.

- [ ] **Step 3: Implement one shared default segment**

Export `DEFAULT_OPERATIONS_SEGMENT = "ordinary"` and use it for initial state and the authorization-scope reset effect. Do not add another effect or duplicate state.

- [ ] **Step 4: Run the focused frontend test**

Run: `npm test -- --run src/pages/OperationsManagementPage.test.tsx`

Expected: PASS.

### Task 4: Verify and publish

**Files:**
- Verify all changed files from Tasks 1-3.

- [ ] **Step 1: Run backend full tests**

Run: `.\.venv\Scripts\python.exe -B -m unittest discover -s tests`

Expected: all tests PASS.

- [ ] **Step 2: Run frontend full tests and production build**

Run: `npm test -- --run`

Run: `npm run build`

Expected: all tests PASS and build exits 0.

- [ ] **Step 3: Review and commit intended files**

Inspect `git diff --check`, `git status -sb`, and the complete diff. Stage only the spec, plan, implementation, and tests, then commit with a focused message.

- [ ] **Step 4: Push and create the PR**

Push `achernar/dev` to `origin` and create a draft PR targeting `main` with the root cause, behavior change, and verification commands in the body.

