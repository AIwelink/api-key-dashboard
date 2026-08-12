# Operations Lifecycle Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add accurate activation, retention, churn, payment conversion, subscription entitlement, and AIGCLink usage-billing metrics to the operations overview.

**Architecture:** Extend normalized Growth facts so source adapters preserve subscription windows and source-priced usage. Compute lifecycle metrics from user-level facts in PostgreSQL through a dedicated read API, while correcting the existing AIGCLink summary/aggregate income and payer semantics. Render compact lifecycle, cohort, and value-ranking sections inside the existing operations overview.

**Tech Stack:** FastAPI, SQLAlchemy async SQL, PostgreSQL, Python dataclasses, React 19, TypeScript, CSS, Python unittest, Node test runner.

---

### Task 1: Growth schema and normalized fact types

**Files:**
- Modify: `backend/app/modules/growth/migrations.py`
- Modify: `backend/app/modules/operations/adapters/base.py`
- Test: `backend/tests/test_growth_migrations.py`

- [ ] **Step 1: Write failing migration tests**

Add tests asserting migration `0004_operations_lifecycle_metrics` adds `billed_amount_cny`, `model_name`, and `token_count` to `growth.usage_facts`, creates `growth.subscription_entitlements`, adds lookup indexes, and appears last in `MIGRATIONS`.

- [ ] **Step 2: Run the migration test and verify it fails**

Run: `python -m unittest tests.test_growth_migrations -v`

Expected: FAIL because the lifecycle migration and subscription table do not exist.

- [ ] **Step 3: Add the migration and normalized types**

Add immutable fields to `UsageFactInput`:

```python
billed_amount_cny: Decimal = Decimal("0")
model_name: str = ""
token_count: int = 0
```

Add a `SubscriptionEntitlementInput` dataclass with site/user/source identity, `starts_at`, `ends_at`, `status`, and `source_updated_at`. Add the migration with non-negative checks and a `(site_id, external_user_id, starts_at, ends_at)` index.

- [ ] **Step 4: Run migration tests**

Run: `python -m unittest tests.test_growth_migrations -v`

Expected: PASS.

### Task 2: Source adapters and entitlement synchronization

**Files:**
- Modify: `backend/app/modules/operations/adapters/base.py`
- Modify: `backend/app/modules/operations/adapters/sub2api.py`
- Modify: `backend/app/modules/operations/adapters/newapi.py`
- Modify: `backend/app/modules/operations/repository.py`
- Modify: `backend/app/modules/operations/sync.py`
- Test: `backend/tests/test_operations_adapters.py`
- Test: `backend/tests/test_operations_repository.py`
- Test: `backend/tests/test_operations_sync.py`

- [ ] **Step 1: Write failing adapter tests**

Cover these exact source mappings:

```text
AIWeLink usage_logs -> billed_amount_cny=0, token_count=input_tokens+output_tokens, model_name=model
AIGCLink quota_data -> billed_amount_cny=quota/QuotaPerUnit, token_count=token_used, model_name=model_name
AIWeLink user_subscriptions -> starts_at/expires_at/status
AIGCLink user_subscriptions -> start_time/end_time/status
AIWeLink payment order metadata -> order_type and subscription_days
```

- [ ] **Step 2: Run adapter tests and verify failure**

Run: `python -m unittest tests.test_operations_adapters -v`

Expected: FAIL on missing normalized fields and `read_subscription_entitlements`.

- [ ] **Step 3: Implement adapter reads**

Extend both adapters with `read_subscription_entitlements`. Keep entitlement reads as full snapshots. Expand usage queries only with non-sensitive model/token/priced quota fields. Never select request content, access tokens, provider payloads, or redemption plaintext.

- [ ] **Step 4: Write failing repository/sync tests**

Assert `upsert_usage_facts` persists the new fields, `replace_subscription_entitlements` deletes only the selected site then inserts its source snapshot, sync calls entitlement replacement transactionally, and `OPERATIONS_AGGREGATE_VERSION == 3` triggers historical usage backfill.

- [ ] **Step 5: Run repository/sync tests and verify failure**

Run: `python -m unittest tests.test_operations_repository tests.test_operations_sync -v`

Expected: FAIL because persistence and v3 reconciliation are missing.

- [ ] **Step 6: Implement persistence and v3 backfill**

Add `replace_subscription_entitlements`, extend the usage UPSERT, call entitlement reads/replacement in `sync_adapter_records`, and use the historical start for usage as well as credits when a cursor predates v3.

- [ ] **Step 7: Run adapter, repository, and sync tests**

Run: `python -m unittest tests.test_operations_adapters tests.test_operations_repository tests.test_operations_sync -v`

Expected: PASS.

### Task 3: Correct existing AIGCLink income and payer metrics

**Files:**
- Modify: `backend/app/modules/operations/repository.py`
- Test: `backend/tests/test_operations_repository.py`

- [ ] **Step 1: Write failing SQL contract tests**

Assert summary, site comparison, trend, and aggregate queries use `usage.billed_amount_cny` for AIGCLink ordinary-user income and count distinct ordinary users with positive billed usage as payers. Assert AIGCLink credit events do not add income or payer counts.

- [ ] **Step 2: Verify failures**

Run: `python -m unittest tests.test_operations_repository -v`

Expected: FAIL because existing queries use `cost_cny` and credit-event payer counts.

- [ ] **Step 3: Correct query semantics**

Use source-priced billed usage for AIGCLink and cash sale facts for AIWeLink. Preserve internal-user exclusion and existing refund compatibility fields.

- [ ] **Step 4: Run repository tests**

Run: `python -m unittest tests.test_operations_repository -v`

Expected: PASS.

### Task 4: Lifecycle analytics repository and API

**Files:**
- Modify: `backend/app/modules/operations/repository.py`
- Modify: `backend/app/modules/operations/service.py`
- Modify: `backend/app/routers/operations.py`
- Test: `backend/tests/test_operations_repository.py`
- Test: `backend/tests/test_operations_routes.py`

- [ ] **Step 1: Write failing repository tests**

Add SQL contract tests for `get_operations_lifecycle_summary`, `get_operations_retention`, `get_operations_model_breakdown`, and `get_operations_customer_breakdown`. Require:

```text
24h/7d activation maturity based on registered_at
D1/D3/D7/D14/D30 Shanghai natural-day retention
14-30 day warning, >=30 day churn, and >=30 day return gaps
AIWeLink cash payer and pending-redemption unknown states
AIGCLink positive billed-usage customer semantics
null rate for zero denominator
subscription amortization from subscription_days
```

- [ ] **Step 2: Verify repository failures**

Run: `python -m unittest tests.test_operations_repository -v`

Expected: FAIL because lifecycle query functions do not exist.

- [ ] **Step 3: Implement lifecycle queries**

Build scoped-user CTEs using the existing site permission and segment filters. Return dictionaries with explicit `numerator`, `denominator`, and `rate`; cap customer/model rankings at 20 rows and apply deterministic tie ordering.

- [ ] **Step 4: Write failing route/service tests**

Test `GET /operations/lifecycle` site authorization, query-window forwarding, cache-key isolation, and response sections.

- [ ] **Step 5: Verify route failures**

Run: `python -m unittest tests.test_operations_routes -v`

Expected: FAIL because the endpoint and service function do not exist.

- [ ] **Step 6: Implement the lifecycle service and route**

Reuse `OperationsQuery`, `_resolve_operations_site_ids`, `_window`, `growth_connection`, and `operations_response_cache`. Load independent repository reads concurrently only if they share no connection state; otherwise execute them serially inside one read connection.

- [ ] **Step 7: Run repository and route tests**

Run: `python -m unittest tests.test_operations_repository tests.test_operations_routes -v`

Expected: PASS.

### Task 5: Operations overview lifecycle UI

**Files:**
- Modify: `frontend/src/pages/OperationsManagementPage.tsx`
- Modify: `frontend/src/pages/OperationsManagementPage.css`
- Test: `frontend/src/pages/OperationsManagementPage.test.tsx`

- [ ] **Step 1: Write failing render and helper tests**

Test ratio formatting with null denominators, lifecycle metric labels, `--` for immature retention, AIWeLink cash/subscription wording, AIGCLink usage-billing wording, unknown-payment counts, model ranking, and customer ranking.

- [ ] **Step 2: Run frontend tests and verify failure**

Run: `node --import tsx --test src/**/*.test.ts src/**/*.test.tsx`

Expected: FAIL because lifecycle response types and sections are absent.

- [ ] **Step 3: Implement lifecycle loading and rendering**

Fetch `/operations/lifecycle` alongside summary, trends, and sync status. Add compact unframed sections below the summary cards, use tables for cohorts/rankings, and keep existing filters and site-permission filtering.

- [ ] **Step 4: Add responsive styles**

Use stable grid tracks, existing operation-page colors, maximum 8 px radius, horizontal table scrolling, and no nested cards. Ensure long customer/model labels wrap without resizing controls.

- [ ] **Step 5: Run frontend tests and build**

Run: `node --import tsx --test src/**/*.test.ts src/**/*.test.tsx`

Run: `npm run build`

Expected: all tests and the production build pass.

### Task 6: End-to-end verification and delivery

**Files:**
- Modify if evidence requires: files changed in Tasks 1-5

- [ ] **Step 1: Run focused verification**

Run all operations, migration, route, adapter, sync, and frontend page tests. Fix any failures test-first.

- [ ] **Step 2: Run full verification**

Run:

```text
python -m unittest discover -s tests -q
node --import tsx --test src/**/*.test.ts src/**/*.test.tsx
npm run build
python -m compileall app
git diff --check
```

Expected: all commands pass.

- [ ] **Step 3: Validate live read-only totals**

For one completed Shanghai day, compare AIGCLink `SUM(quota) / QuotaPerUnit` and distinct positive-quota users against the normalized formula. Use read-only SQL and report only aggregate values.

- [ ] **Step 4: Review the complete diff**

Confirm every design requirement has code and test evidence, no source credentials or request content were added, and no unrelated worktree changes are present.

- [ ] **Step 5: Commit, push, and open a draft PR**

Commit only lifecycle files, push `codex/operations-lifecycle-metrics`, and open a draft PR targeting `main` with the metric definitions, migration/backfill behavior, and verification results.
