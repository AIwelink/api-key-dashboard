# Extreme Load Factor Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or subagent-driven-development) to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add per-account-type extreme `load_factor` scheduling with default `10000`, preserving and restoring each account's pre-extreme value.

**Architecture:** Extend the pure smart-scheduling evaluator to return an optional load-factor target. The orchestration service captures the original value in the existing per-account state before remote writes, groups bulk updates by all target fields, and clears the saved value only after recovery succeeds. The existing site-level settings API and database-backed group toggles remain the source of configuration.

**Tech Stack:** FastAPI/Pydantic, Python async service with Motor and httpx, React/TypeScript/Vitest, pytest/unittest.

---

### Task 1: Extend the pure scheduling contract

**Files:**
- Modify: `backend/app/modules/sub2api/smart_scheduling.py`
- Test: `backend/tests/test_smart_scheduling.py`

- [ ] **Step 1: Write failing decision tests**

Add tests asserting every default account rule contains `extreme_load_factor == 10000`, the normalizer fills a missing value, and extreme entry/continuation returns `{"priority": 10, "concurrency": 100, "load_factor": 10000}`. Add a recovery test with state `{"mode": "extreme", "original_load_factor": 7}` and assert the target is normal priority/concurrency plus `load_factor: 7`. Add a normal-mode test asserting the target remains only priority/concurrency and does not change a normal load factor.

- [ ] **Step 2: Run the focused tests and verify RED**

Run `python -m pytest backend/tests/test_smart_scheduling.py -q`. Expected: the new assertions fail because the rule schema and target currently have no load-factor field.

- [ ] **Step 3: Implement the minimal evaluator changes**

Add `extreme_load_factor: 10000` to every default account rule and include it in `_ACCOUNT_INTEGER_FIELDS`. Extend `_target_result` with `load_factor: int | None = None`; include it in `target` and in the current-value comparison only when supplied. Pass the configured extreme value for extreme and rate-limit-pending targets. For recovery targets, use `state["original_load_factor"]` when it is an integer, otherwise use the current account load factor as the legacy fallback. Leave normal type-priority targets without a load-factor key.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run `python -m pytest backend/tests/test_smart_scheduling.py -q`. Expected: all decision tests pass.

- [ ] **Step 5: Commit the decision-layer change**

Run `git add backend/app/modules/sub2api/smart_scheduling.py backend/tests/test_smart_scheduling.py && git commit -m "feat: add extreme load factor targets"`.

### Task 2: Add the per-type configuration field to the API

**Files:**
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_smart_scheduling_routes.py`
- Test: `backend/tests/test_smart_scheduling.py`

- [ ] **Step 1: Write failing schema/API tests**

Assert `SmartSchedulingAccountRule` accepts and returns `extreme_load_factor`, rejects values below `1`, and the GET settings response normalizes legacy documents without the field to `10000` for all four types. Assert PATCH accepts a different value per type and returns it.

- [ ] **Step 2: Run tests to verify RED**

Run `python -m pytest backend/tests/test_smart_scheduling_routes.py backend/tests/test_smart_scheduling.py -q`. Expected: Pydantic rejects the new field or the normalized response omits it.

- [ ] **Step 3: Implement the schema field**

Add `extreme_load_factor: int = Field(ge=1, le=100_000)` to `SmartSchedulingAccountRule`. The existing service normalizer and route already serialize the complete model, so no route shape change is needed.

- [ ] **Step 4: Run tests to verify GREEN**

Run the same focused pytest command and confirm all pass.

- [ ] **Step 5: Commit the API contract change**

Run `git add backend/app/schemas.py backend/tests/test_smart_scheduling_routes.py backend/tests/test_smart_scheduling.py && git commit -m "feat: expose per-type extreme load factor setting"`.

### Task 3: Extend the Sub2API bulk update client

**Files:**
- Modify: `backend/app/modules/sub2api/client.py`
- Test: `backend/tests/test_sub2api_client_update.py`

- [ ] **Step 1: Write failing client tests**

Add a test that calls `bulk_update_accounts_runtime([5864, 5863], {"priority": 10, "concurrency": 100, "load_factor": 10000, "group_ids": [3]})` and asserts the POST JSON contains all four fields. Keep the existing test asserting a payload without `load_factor` remains valid for normal updates.

- [ ] **Step 2: Run tests to verify RED**

Run `python -m pytest backend/tests/test_sub2api_client_update.py -q`. Expected: the method rejects the four-field payload.

- [ ] **Step 3: Implement optional load-factor serialization**

Allow the required keys `priority`, `concurrency`, and `group_ids`, with optional `load_factor`. Add `load_factor` to the JSON only when present, preserving the normal-update payload shape.

- [ ] **Step 4: Run tests to verify GREEN**

Run the focused client tests and confirm all pass.

- [ ] **Step 5: Commit the client change**

Run `git add backend/app/modules/sub2api/client.py backend/tests/test_sub2api_client_update.py && git commit -m "feat: support load factor in runtime bulk updates"`.

### Task 4: Persist original values and batch all managed fields

**Files:**
- Modify: `backend/app/modules/sub2api/smart_scheduling_service.py`
- Test: `backend/tests/test_smart_scheduling_service.py`

- [ ] **Step 1: Write failing service tests**

Add service cases for entering extreme mode with account `load_factor=7`: assert state capture persists `original_load_factor=7` before the bulk call, the batch payload includes `load_factor=10000`, and the successful state keeps the original. Add a recovery case with saved original `7`: assert the bulk payload restores `load_factor=7` and the successful state update unsets `original_load_factor` and its timestamp. Add a partial-failure case asserting the saved original is retained. Add a latest-account revalidation case where the remote `load_factor` differs from the snapshot.

- [ ] **Step 2: Run tests to verify RED**

Run `python -m pytest backend/tests/test_smart_scheduling_service.py -q`. Expected: payloads omit load factor and state updates do not capture or clear the original.

- [ ] **Step 3: Implement state capture and batching**

Include `load_factor` in `_states_for_accounts` projection and `_runtime_values`, and merge `latest["load_factor"]` during revalidation. Before each extreme pending batch is sent, call a helper that conditionally writes `original_load_factor` and `original_load_factor_captured_at` only when the state has no saved original. Add a `clear_original_load_factor` path to successful recovery state persistence using `$unset`. Extend pending batch keys to `(priority, concurrency, load_factor_or_none, group_ids)`, pass `load_factor` only when the target contains it, and keep normal batches compatible. Ensure unchanged recovery decisions also clear the saved fields; failed updates never clear them.

- [ ] **Step 4: Run tests to verify GREEN**

Run `python -m pytest backend/tests/test_smart_scheduling_service.py -q` and confirm all existing and new orchestration tests pass.

- [ ] **Step 5: Commit the service change**

Run `git add backend/app/modules/sub2api/smart_scheduling_service.py backend/tests/test_smart_scheduling_service.py && git commit -m "feat: persist and restore original load factors"`.

### Task 5: Add frontend configuration editing

**Files:**
- Modify: `frontend/src/pages/smartScheduling.ts`
- Modify: `frontend/src/pages/AccountPoolsPage.tsx`
- Test: `frontend/src/pages/smartScheduling.test.ts`

- [ ] **Step 1: Write failing frontend tests**

Assert the default form exposes `pro`, `plus`, `k12`, and `team` `extreme_load_factor` values as `10000`; assert payload serialization preserves a per-type custom value; assert the scheduling table source contains the `extreme_load_factor` column and input.

- [ ] **Step 2: Run tests to verify RED**

Run `npm --prefix frontend test -- --run src/pages/smartScheduling.test.ts`. Expected: the field is missing from the type/default/form and table source.

- [ ] **Step 3: Implement the form and table field**

Add `extreme_load_factor` to the account rule type, defaults, field list, integer validation, and labels. Add a table header and numeric input bound to `updateSmartSchedulingRule(accountType, "extreme_load_factor", ...)`, with `min=1` and `max=100000`. Keep site switching and save-in-flight disabling unchanged.

- [ ] **Step 4: Run tests and production build**

Run `npm --prefix frontend test -- --run src/pages/smartScheduling.test.ts` and `npm --prefix frontend run build`. Expected: tests pass and Vite completes successfully.

- [ ] **Step 5: Commit the frontend change**

Run `git add frontend/src/pages/smartScheduling.ts frontend/src/pages/AccountPoolsPage.tsx frontend/src/pages/smartScheduling.test.ts && git commit -m "feat: configure extreme load factor per account type"`.

### Task 6: Full verification and PR handoff

**Files:**
- Modify: none unless verification finds a regression.

- [ ] **Step 1: Run backend regression tests**

Run `python -m pytest backend/tests/test_smart_scheduling.py backend/tests/test_smart_scheduling_service.py backend/tests/test_smart_scheduling_routes.py backend/tests/test_sub2api_client_update.py -q`.

- [ ] **Step 2: Run frontend regression tests and build**

Run `npm --prefix frontend test -- --run src/pages/smartScheduling.test.ts` and `npm --prefix frontend run build`.

- [ ] **Step 3: Inspect the final diff and status**

Run `git diff origin/achernar/dev...HEAD --stat`, `git diff origin/achernar/dev...HEAD --check`, and `git status --short`. Confirm only the design, plan, backend scheduling, client/schema/tests, and frontend scheduling files changed.

- [ ] **Step 4: Push and open the PR**

Run `git push -u origin codex/extreme-load-factor`, then create a PR targeting `achernar/dev` with a summary of per-type default `10000`, original-value restoration, 429 cooldown behavior, and verification commands.
