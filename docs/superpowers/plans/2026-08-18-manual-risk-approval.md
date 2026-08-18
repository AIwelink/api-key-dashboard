# AIWeLink Manual Risk Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Make AIWeLink risk detection approval-only, safely retire existing automatic-ban actions, and grant operator the same write access as owner/admin inside Operations Management.

**Architecture:** Keep the existing risk detector, evidence tables, manual-ban service, advisory lock, and audit model. Force detector outcomes to `high_risk`, remove automatic-ban recovery/creation, add an idempotent Growth migration that cancels legacy automatic actions, and use one shared operations writer role set (`owner`, `admin`, `operator`) while retaining site and page permission checks. The risk UI will display a fixed manual-approval state and keep the existing reason-required manual action modal.

**Tech Stack:** FastAPI, async SQLAlchemy/PostgreSQL Growth migrations, unittest/pytest, React/TypeScript, Vitest.

---

### Task 1: Lock approval-only risk decisions

**Files:**
- Modify: `backend/app/modules/risk/service.py:139-266`
- Test: `backend/tests/test_risk_service.py`
- Test: `backend/tests/test_risk_domain.py`

- [x] **Step 1: Write failing service tests**

Add tests that call `desired_risk_status(evaluation, auto_ban_enabled=True)` for an unprotected email-plus-shared-IP evaluation and expect `high_risk`, then call `reconcile_risk_inputs` with the same input and assert no candidate is returned and the repository is upserted with `risk_status="high_risk"`.

- [x] **Step 2: Run the focused tests and verify the red failure**

Run:
```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_risk_service.py -k "desired_risk_status or reconcile" -q
```

Expected: the existing implementation returns `ban_pending` and produces a candidate, so the new assertions fail.

- [x] **Step 3: Implement the smallest approval-only decision**

Change `desired_risk_status` so `RiskDecision.BAN` returns `"high_risk"` regardless of `auto_ban_enabled`. Remove the `target_status == "ban_pending"` candidate branch from `reconcile_risk_inputs`; it must return an empty candidate list after persisting the high-risk account. Keep the function parameters for compatibility with existing callers until Task 2 removes automatic callers.

- [x] **Step 4: Run the focused tests and the existing risk service/domain suites**

Run:
```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_risk_service.py backend/tests/test_risk_domain.py -q
```

Expected: all tests pass after updating assertions that describe the old automatic-ban outcome.

- [x] **Step 5: Commit the decision change**

```powershell
git add backend/app/modules/risk/service.py backend/tests/test_risk_service.py backend/tests/test_risk_domain.py
git commit -m "fix: require manual approval for risk bans"
```

### Task 2: Remove automatic enforcement from the coordinator

**Files:**
- Modify: `backend/app/modules/risk/coordinator.py:135-265,412-510`
- Test: `backend/tests/test_risk_coordinator.py`

- [x] **Step 1: Write failing coordinator tests**

Add a test for `_run_enabled_cycle` with detector settings enabled that patches `recover_pending_auto_bans` and asserts it is not awaited, and patches `repository.create_action`/`adapter.disable_account` to assert neither is called for a strong risk input. Add a recovery test that passes a legacy pending action and asserts `recover_pending_auto_bans` leaves it untouched when called defensively with `auto_ban_enabled=False`.

- [x] **Step 2: Run the new tests and verify the red failure**

Run:
```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_risk_coordinator.py -k "automatic or auto_ban or enabled_cycle" -q
```

Expected: the current enabled cycle calls recovery and creates `auto_ban` actions, so the new assertions fail.

- [x] **Step 3: Remove automatic recovery and preparation**

In `_run_enabled_cycle`, remove the `recover_pending_auto_bans` call and its result payload. Continue recovering only manual actions. Remove the `prepared` list and the source-writing loop that creates and executes `auto_ban` actions. Keep source reads, observation upserts, cursor updates, risk reconciliation, and aggregate refresh. Leave `recover_pending_auto_bans` as a defensive compatibility function that immediately returns zero counts when automatic enforcement is disabled and never calls `disable_account` for any setting.

- [x] **Step 4: Run coordinator and scheduler tests**

Run:
```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_risk_coordinator.py backend/tests/test_risk_scheduler.py -q
```

Expected: all tests pass, including proof that manual action recovery still works.

- [x] **Step 5: Commit the coordinator change**

```powershell
git add backend/app/modules/risk/coordinator.py backend/tests/test_risk_coordinator.py
git commit -m "fix: remove automatic risk enforcement"
```

### Task 3: Cancel legacy automatic-ban actions in Growth

**Files:**
- Modify: `backend/app/modules/growth/migrations.py`
- Test: `backend/tests/test_growth_migrations.py`
- Test: `backend/tests/test_risk_repository.py`

- [x] **Step 1: Write failing migration and repository tests**

Assert the new migration is version `0009_manual_risk_approval`, allows `cancelled` in the action-status constraint, updates pending/failed `auto_ban` actions to `cancelled`, sets their completion timestamp and `error_code = 'AutoBanDisabled'`, and changes `ban_pending` accounts to `high_risk`. Add repository SQL assertions that cancelled actions are not returned by pending-action queries and that the action-status schema accepts `cancelled`.

- [x] **Step 2: Run migration/repository tests and verify the red failure**

Run:
```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_growth_migrations.py backend/tests/test_risk_repository.py -k "cancel or pending or migration" -q
```

Expected: version `0009_manual_risk_approval` and the cancellation SQL are absent, so the tests fail.

- [x] **Step 3: Add the idempotent migration and status handling**

Append `MANUAL_RISK_APPROVAL_MIGRATION` after `RISK_HARDENING_MIGRATION`, add it to `MIGRATIONS`, drop/recreate the `risk_actions_action_status_check` constraint with `cancelled`, and execute:
```sql
UPDATE growth.risk_actions
SET action_status = 'cancelled',
    completed_at = COALESCE(completed_at, NOW()),
    error_code = 'AutoBanDisabled',
    error_message = 'Automatic bans require manual approval'
WHERE site_id = 'aiwelink'
  AND action_type = 'auto_ban'
  AND action_status IN ('pending', 'failed');

UPDATE growth.risk_accounts
SET risk_status = 'high_risk', updated_at = NOW()
WHERE site_id = 'aiwelink' AND risk_status = 'ban_pending';

UPDATE growth.risk_settings
SET auto_ban_enabled = FALSE, updated_by = 'system:manual-risk-approval', updated_at = NOW()
WHERE site_id = 'aiwelink';
```
The existing pending-action predicates select only `action_status = 'pending'`; keep them unchanged so cancelled rows remain visible in history but never recover.

- [x] **Step 4: Run migration and repository tests**

Run:
```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_growth_migrations.py backend/tests/test_risk_repository.py -q
```

Expected: all migration ordering, idempotency, action query, and status tests pass.

- [ ] **Step 5: Commit the data-safety change**

```powershell
git add backend/app/modules/growth/migrations.py backend/app/modules/risk/repository.py backend/app/modules/risk/schemas.py backend/tests/test_growth_migrations.py backend/tests/test_risk_repository.py
git commit -m "fix: cancel legacy automatic risk bans"
```

### Task 4: Enforce settings and risk-route permissions

**Files:**
- Modify: `backend/app/modules/risk/service.py:388-402`
- Modify: `backend/app/routers/risk.py:24-29,161-243`
- Test: `backend/tests/test_risk_routes.py`
- Test: `backend/tests/test_risk_service.py`

- [ ] **Step 1: Write failing permission and settings tests**

Add route tests asserting an operator with `operations_site_ids=["aiwelink"]` can call settings update, manual ban, manual release, false-positive, and override removal; a viewer remains 403. Add a settings test asserting `auto_ban_enabled=True` raises HTTP 400 and does not call the repository update. Keep a site-scope test asserting an operator without AIWeLink access receives 403.

- [ ] **Step 2: Run the new route tests and verify the red failure**

Run:
```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_risk_routes.py -k "operator or auto_ban or settings" -q
```

Expected: operator writes are currently 403 and enabling auto-ban is currently accepted.

- [ ] **Step 3: Implement least-surprise backend authorization**

Change the risk writer role set to `{"owner", "admin", "operator"}`. In `update_risk_settings`, reject a truthy `auto_ban_enabled` with `ValueError("Automatic bans require manual approval")`; when updating other fields, force `auto_ban_enabled=False` so stale clients cannot re-enable it. Keep AIWeLink site checks and management audit writes unchanged.

- [ ] **Step 4: Run all risk route/service tests**

Run:
```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_risk_routes.py backend/tests/test_risk_service.py -q
```

Expected: operator approval/settings tests pass, viewer and unauthorized-site tests remain 403, and owner/admin regression tests pass.

- [ ] **Step 5: Commit the risk authorization change**

```powershell
git add backend/app/modules/risk/service.py backend/app/routers/risk.py backend/tests/test_risk_routes.py backend/tests/test_risk_service.py
git commit -m "feat: allow operators to approve risk actions"
```

### Task 5: Grant operator all Operations Management writes

**Files:**
- Modify: `backend/app/routers/operations.py:38-45`
- Modify: `frontend/src/pages/OperationsManagementPage.tsx:443`
- Test: `backend/tests/test_operations_routes.py`
- Test: `frontend/src/pages/OperationsManagementPage.test.tsx`

- [ ] **Step 1: Write failing operator-write tests**

Change the existing tests that assert operator cannot create/delete internal users to assert the operation succeeds with the existing service mocks and audit log. Add one operator credit-write assertion and retain unauthorized-site rejection. Update the frontend test to expect `canManageOperations("operator") === true`.

- [ ] **Step 2: Run the new tests and verify the red failure**

Run:
```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_operations_routes.py -k "operator" -q
npm --prefix frontend test -- --run src/pages/OperationsManagementPage.test.tsx
```

Expected: the backend writer guard and frontend helper currently reject operator.

- [ ] **Step 3: Implement the shared operations writer role set**

Change `_require_operations_writer` to accept `owner`, `admin`, and `operator`; change `canManageOperations` to return true for those same roles. Do not change `normalize_operations_site_ids`, page permission dependencies, system-management routes, or role-management routes.

- [ ] **Step 4: Run operations route and page tests**

Run:
```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_operations_routes.py backend/tests/test_operations_site_permissions.py -q
npm --prefix frontend test -- --run src/pages/OperationsManagementPage.test.tsx
```

Expected: all operator write/read/site-scope tests pass and owner/admin behavior is unchanged.

- [ ] **Step 5: Commit the operations permission change**

```powershell
git add backend/app/routers/operations.py frontend/src/pages/OperationsManagementPage.tsx backend/tests/test_operations_routes.py frontend/src/pages/OperationsManagementPage.test.tsx
git commit -m "feat: grant operators operations write access"
```

### Task 6: Update the risk workspace for fixed manual approval

**Files:**
- Modify: `frontend/src/pages/operations/OperationsRiskPanel.tsx:200-285,435-530,590-730`
- Modify: `frontend/src/pages/operations/OperationsRiskPanel.css`
- Test: `frontend/src/pages/operations/OperationsRiskPanel.test.tsx`

- [ ] **Step 1: Write failing UI tests**

Render the panel as operator and assert the manual action controls are enabled. Assert no editable checkbox exists for `auto_ban_enabled`, the fixed “人工审批” status is rendered, cancelled actions have the “自动封禁已取消” label, and the manual-ban modal still disables submit until a reason is entered.

- [ ] **Step 2: Run the UI tests and verify the red failure**

Run:
```powershell
npm --prefix frontend test -- --run src/pages/operations/OperationsRiskPanel.test.tsx
```

Expected: the current `canWrite` excludes operator and the automatic-ban toggle is rendered.

- [ ] **Step 3: Implement the fixed approval UI**

Set `canWrite` for owner/admin/operator, replace the automatic-ban `RiskToggle` with a non-interactive status element, update the rule note and risk status labels, add `cancelled` to action status labels, and change the read-only note to mention only roles without operations write access. Keep refresh, tooltips, confirmation modal, required reason, and detail timeline behavior.

- [ ] **Step 4: Run frontend risk tests and type/build checks**

Run:
```powershell
npm --prefix frontend test -- --run src/pages/operations/OperationsRiskPanel.test.tsx
npm --prefix frontend run build
```

Expected: focused UI tests pass and the production build exits 0.

- [ ] **Step 5: Commit the risk UI change**

```powershell
git add frontend/src/pages/operations/OperationsRiskPanel.tsx frontend/src/pages/operations/OperationsRiskPanel.css frontend/src/pages/operations/OperationsRiskPanel.test.tsx
git commit -m "feat: show manual risk approval state"
```

### Task 7: Full verification and release handoff

**Files:**
- Test: `backend/tests/` and `frontend/src/**/*.test.*`

- [ ] **Step 1: Run all focused backend risk/operations tests**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_risk_domain.py backend/tests/test_risk_sub2api_adapter.py backend/tests/test_risk_repository.py backend/tests/test_risk_service.py backend/tests/test_risk_scheduler.py backend/tests/test_risk_coordinator.py backend/tests/test_risk_routes.py backend/tests/test_growth_migrations.py backend/tests/test_operations_routes.py -q
```

Expected: zero failures and zero errors.

- [ ] **Step 2: Run the complete backend suite**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -q
```

Expected: zero failures and zero errors.

- [ ] **Step 3: Run the complete frontend test/build suite**

```powershell
npm --prefix frontend test -- --run
npm --prefix frontend run build
```

Expected: all tests pass and the build exits 0.

- [ ] **Step 4: Verify live safety without restarting an old worker**

Read `growth.risk_settings` and action counts with the existing read-only diagnostic script. Confirm `auto_ban_enabled=false`, no `pending`/`failed` `auto_ban` actions remain after migration, cancelled history is present, and detector settings remain unchanged. Do not start the backend against live configuration from the development shell.

- [ ] **Step 5: Review diff and publish**

Run `git diff origin/achernar/dev...HEAD --check` and `git status --short`. Push `codex/manual-risk-approval` and open a pull request targeting `achernar/dev` with the test results and the live safety verification.
