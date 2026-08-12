# Operations Analytics and Internal Users Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build cached AIWeLink/AIGCLink operations analytics with ordinary/internal user segmentation, versioned CNY balance conversion, classified redemption and adjustment events, and a complete management page.

**Architecture:** Add an isolated `app.modules.operations` domain that owns normalized operations facts, repositories, source adapters, aggregation, and the 15-minute scheduler while reusing the existing Growth PostgreSQL connection. Expose `/api/operations/*` through a dedicated router and render only cached Growth data in a query-first React workspace with four tabs.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy asyncio, PostgreSQL, MySQL via aiomysql, MongoDB for existing site secrets and audit, React 19, TypeScript, Vitest.

---

## File Map

Backend files:

- Modify `backend/app/modules/growth/migrations.py`: add the versioned operations schema migration.
- Create `backend/app/modules/operations/schemas.py`: request models and enums.
- Create `backend/app/modules/operations/domain.py`: time ranges, user segment, conversion, income, and sync-status rules.
- Create `backend/app/modules/operations/repository.py`: Growth PostgreSQL CRUD, fact UPSERT, aggregate queries, and task records.
- Create `backend/app/modules/operations/cache.py`: bounded 60-second response cache with explicit invalidation.
- Create `backend/app/modules/operations/adapters/base.py`: normalized adapter protocol and source record models.
- Create `backend/app/modules/operations/adapters/sub2api.py`: PostgreSQL read-only mapping.
- Create `backend/app/modules/operations/adapters/newapi.py`: MySQL read-only mapping.
- Create `backend/app/modules/operations/sync.py`: 48-hour reconciliation, aggregate refresh, leases, manual refresh, and 15-minute loop.
- Create `backend/app/modules/operations/service.py`: permission-neutral use cases and source write capability handling.
- Create `backend/app/routers/operations.py`: HTTP contract, role gates, and audit writes.
- Modify `backend/app/main.py`: mount the router and lifecycle scheduler.

Frontend files:

- Replace `frontend/src/pages/OperationsManagementPage.tsx`: four-tab operations workspace.
- Create `frontend/src/pages/OperationsManagementPage.test.tsx`: rendering, form, permissions, and status tests.
- Modify `frontend/src/App.tsx`: pass token, role, and toast callbacks.
- Modify `frontend/styles.css`: full-width query-first operations layout and 75%-width dialogs.

Test files:

- Modify `backend/tests/test_growth_migrations.py`.
- Create `backend/tests/test_operations_domain.py`.
- Create `backend/tests/test_operations_repository.py`.
- Create `backend/tests/test_operations_routes.py`.
- Create `backend/tests/test_operations_adapters.py`.
- Create `backend/tests/test_operations_sync.py`.

### Task 1: Operations Schema Migration

**Files:**
- Modify: `backend/app/modules/growth/migrations.py`
- Modify: `backend/tests/test_growth_migrations.py`

- [ ] **Step 1: Write the failing migration contract test**

```python
def test_operations_migration_contains_cached_analytics_tables(self) -> None:
    from app.modules.growth.migrations import OPERATIONS_MIGRATION

    sql = "\n".join(OPERATIONS_MIGRATION.statements)
    for table in (
        "internal_users", "balance_conversion_rates", "ops_user_snapshots",
        "credit_events", "redemption_batches", "balance_adjustment_requests",
        "usage_facts", "classification_tasks", "ops_hourly_stats", "ops_daily_stats",
    ):
        self.assertIn(f"CREATE TABLE IF NOT EXISTS growth.{table}", sql)
    self.assertIn("balance_units_per_cny > 0", sql)
    self.assertIn("UNIQUE (site_id, source_type, source_record_id)", sql)
```

- [ ] **Step 2: Run the migration test and verify RED**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_growth_migrations.py" -v`

Expected: FAIL because `OPERATIONS_MIGRATION` does not exist.

- [ ] **Step 3: Add migration `0002_operations_analytics`**

Add `OPERATIONS_MIGRATION = Migration(version="0002_operations_analytics", ...)` with all ten tables, constraints, foreign keys to `growth.sites`, exclusion constraints or equivalent overlap guards for effective ranges, and indexes for site/time/segment/status queries. Append it to `MIGRATIONS` without modifying `0001_initial`.

```python
MIGRATIONS = (INITIAL_MIGRATION, OPERATIONS_MIGRATION)
```

- [ ] **Step 4: Run the migration tests and verify GREEN**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_growth_migrations.py" -v`

Expected: all migration tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/growth/migrations.py backend/tests/test_growth_migrations.py
git commit -m "feat: add operations analytics schema"
```

### Task 2: Domain Rules and Request Schemas

**Files:**
- Create: `backend/app/modules/operations/__init__.py`
- Create: `backend/app/modules/operations/schemas.py`
- Create: `backend/app/modules/operations/domain.py`
- Create: `backend/tests/test_operations_domain.py`

- [ ] **Step 1: Write failing tests for segmentation, conversion, income, time range, and status**

```python
def test_aiwelink_ten_units_cost_one_cny() -> None:
    assert convert_balance_to_cny(Decimal("25"), Decimal("10")) == Decimal("2.5")

def test_non_sale_credit_has_zero_cash_income() -> None:
    assert normalized_cash_amount("internal", Decimal("99")) == Decimal("0")

def test_internal_user_is_not_ordinary() -> None:
    assert user_segment(is_internal=True) == "internal"

def test_sync_status_is_delayed_after_thirty_minutes() -> None:
    assert sync_health(now=NOW, last_success_at=NOW - timedelta(minutes=31), running=False) == "delayed"
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_operations_domain.py" -v`

Expected: import failures for the new module.

- [ ] **Step 3: Implement focused domain functions and Pydantic models**

Define `Purpose`, `UserSegment`, `CreditDirection`, `OperationsRange`, `InternalUserCreate/Update`, `ConversionRateCreate`, `RedemptionBatchCreate`, `BalanceAdjustmentCreate`, `ClassificationUpdate`, and `RefreshRequest`. Enforce non-negative sale cash amounts and non-sale zero cash amounts in model validators; a sale fact with zero cash records credit usage without asserting payment.

```python
def convert_balance_to_cny(balance_units: Decimal, units_per_cny: Decimal) -> Decimal:
    if units_per_cny <= 0:
        raise ValueError("balance conversion rate must be greater than zero")
    return balance_units / units_per_cny
```

- [ ] **Step 4: Run and verify GREEN**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_operations_domain.py" -v`

Expected: all domain tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/modules/operations backend/tests/test_operations_domain.py
git commit -m "feat: define operations analytics domain"
```

### Task 3: Operations Repository and 60-Second Cache

**Files:**
- Create: `backend/app/modules/operations/repository.py`
- Create: `backend/app/modules/operations/cache.py`
- Create: `backend/tests/test_operations_repository.py`

- [ ] **Step 1: Write failing repository tests**

Cover internal-user CRUD, non-overlapping effective windows, conversion-rate version creation, classification-task update, summary query parameterization, source-fact UPSERT, and cache invalidation.

```python
async def test_create_internal_user_uses_site_and_external_user_identity(self) -> None:
    row = await create_internal_user(connection, payload, actor_id="owner")
    self.assertEqual(row["site_id"], "aiwelink")
    self.assertEqual(row["external_user_id"], "42")
    self.assertIn("growth.internal_users", connection.statements[0])
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_operations_repository.py" -v`

Expected: repository import failure.

- [ ] **Step 3: Implement repository functions**

Use SQLAlchemy `text()` with bound values only. Expose config CRUD, `upsert_user_snapshots`, `upsert_usage_facts`, `upsert_credit_events`, `replace_affected_aggregates`, `get_operations_summary`, `get_operations_trends`, `list_operations_users`, `get_sync_status`, and task/batch request operations. Return JSON-safe rows using the established Growth `_public_value` behavior without importing private helpers.

- [ ] **Step 4: Implement bounded cache**

```python
class OperationsResponseCache:
    def __init__(self, ttl_seconds: int = 60, max_entries: int = 256): ...
    async def get_or_load(self, key: tuple[object, ...], loader: Callable[[], Awaitable[Any]]) -> Any: ...
    def invalidate(self, *, site_id: str | None = None) -> None: ...
```

- [ ] **Step 5: Run and verify GREEN**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_operations_repository.py" -v`

Expected: all repository tests PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/modules/operations/repository.py backend/app/modules/operations/cache.py backend/tests/test_operations_repository.py
git commit -m "feat: add operations repository and cache"
```

### Task 4: Read-Only Source Adapters and Incremental Sync

**Files:**
- Create: `backend/app/modules/operations/adapters/__init__.py`
- Create: `backend/app/modules/operations/adapters/base.py`
- Create: `backend/app/modules/operations/adapters/sub2api.py`
- Create: `backend/app/modules/operations/adapters/newapi.py`
- Create: `backend/app/modules/operations/sync.py`
- Create: `backend/tests/test_operations_adapters.py`
- Create: `backend/tests/test_operations_sync.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing adapter mapping tests**

Use fake SQLAlchemy mapping results representing the verified source tables. Assert that password/token fields are absent, successful usage becomes `UsageFactInput`, payment becomes classified `sale`, and unmatched admin credit becomes a pending `CreditEventInput` plus classification task.

- [ ] **Step 2: Write failing scheduler tests**

Assert a 48-hour reconciliation start, duplicate refresh coalescing, 15-minute loop interval, one site failure not stopping another, and aggregation only after fact UPSERT succeeds.

- [ ] **Step 3: Run and verify RED**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_operations_adapters.py" -v`

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_operations_sync.py" -v`

Expected: adapter and sync modules missing.

- [ ] **Step 4: Implement the adapter protocol and database engines**

```python
class OperationsSourceAdapter(Protocol):
    async def read_users(self, *, connection: Any, since: datetime) -> list[UserSnapshotInput]: ...
    async def read_usage(self, *, connection: Any, since: datetime) -> list[UsageFactInput]: ...
    async def read_credit_events(self, *, connection: Any, since: datetime) -> list[CreditEventInput]: ...
```

Build source engines from `client_sites.sql_dsn` using existing DSN parsing and `NullPool`; connections are read-only and disposed after each run. Keep Sub2API and NewAPI SQL in separate adapter files.

- [ ] **Step 5: Implement sync orchestration**

`sync_site_operations()` loads the site secret, selects the adapter by `client_type`, rewinds the cursor by 48 hours, UPSERTs normalized facts, refreshes only affected buckets, persists a `sync_runs` result, and invalidates the response cache. `operations_sync_loop()` sleeps to a 900-second cadence and catches per-site failures.

- [ ] **Step 6: Register and cancel the scheduler in FastAPI lifespan**

Create `operations_sync_task = asyncio.create_task(operations_sync_loop(db))`, include it in `background_tasks`, and preserve current cancellation behavior.

- [ ] **Step 7: Run and verify GREEN**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_operations_adapters.py" -v`

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_operations_sync.py" -v`

Expected: all adapter and sync tests PASS.

- [ ] **Step 8: Commit**

```bash
git add backend/app/modules/operations/adapters backend/app/modules/operations/sync.py backend/app/main.py backend/tests/test_operations_adapters.py backend/tests/test_operations_sync.py
git commit -m "feat: sync cached operations facts"
```

### Task 5: Operations API, Permissions, Auditing, and Credit Commands

**Files:**
- Create: `backend/app/modules/operations/service.py`
- Create: `backend/app/routers/operations.py`
- Create: `backend/tests/test_operations_routes.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing route tests**

Test operator read and refresh access, operator write rejection, owner/admin writes, audit calls, range validation, summary caching, classification invalidation, idempotency conflicts, and explicit `capability_unavailable` for sites without a verified write adapter.

```python
async def test_operator_cannot_create_internal_user(self) -> None:
    with self.assertRaises(HTTPException) as raised:
        await post_internal_user(payload, actor={"role": "operator"}, db=self.db)
    self.assertEqual(raised.exception.status_code, 403)
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_operations_routes.py" -v`

Expected: operations router missing.

- [ ] **Step 3: Implement role gates and read APIs**

Use `require_view_permission("operations-management")` for all endpoints. Add an explicit `_require_operations_writer(actor)` accepting only `owner/admin` for internal users, rates, redemption generation, adjustments, and classification changes. Mount summary, trends, users, sync-status, and refresh routes.

- [ ] **Step 4: Implement credit command boundary**

Define a `CreditCommandAdapter` protocol with `create_redemption_batch`, `get_redemption_batch_by_idempotency_key`, `adjust_balance`, and `get_adjustment_by_idempotency_key`. Never fall back to direct database writes. Unsupported site versions return HTTP 409 with `capability_unavailable`; verified adapters persist masked/hash-only results and return plaintext codes only in the immediate response.

- [ ] **Step 5: Add audit writes and cache invalidation**

Audit action names are `operations.internal_user.*`, `operations.conversion_rate.create`, `operations.redemption_batch.create`, `operations.balance_adjustment.create`, and `operations.classification.update`.

- [ ] **Step 6: Mount router and run tests**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_operations_routes.py" -v`

Expected: all route tests PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/modules/operations/service.py backend/app/routers/operations.py backend/app/main.py backend/tests/test_operations_routes.py
git commit -m "feat: expose operations management api"
```

### Task 6: Four-Tab Operations Frontend

**Files:**
- Replace: `frontend/src/pages/OperationsManagementPage.tsx`
- Create: `frontend/src/pages/OperationsManagementPage.test.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Write failing rendering and form tests**

Use `renderToStaticMarkup` like `TrafficAnalysisPage.test.tsx`. Assert the four tabs, default 7-day filter, site/segment filters, freshness states, summary cards, trend tables, internal-user query table, redemption/adjustment dialogs, pending classification dialog, owner/admin write controls, and operator read-only controls.

- [ ] **Step 2: Run and verify RED**

Run: `npm --prefix frontend test -- OperationsManagementPage.test.tsx`

Expected: FAIL because the current page is blank.

- [ ] **Step 3: Implement typed contracts and pure helpers**

Keep page-local types for `OperationsSummary`, `OperationsTrend`, `InternalUser`, `ConversionRate`, `CreditEvent`, `ClassificationTask`, and forms. Export validation/build helpers for direct unit tests.

- [ ] **Step 4: Implement the workspace component**

Render full-width tabs `overview`, `internal-users`, `credits`, and `classification`; query controls above tables; stable metric dimensions; accessible tables; and dialogs via `GrowthCreateModal` or a focused operations modal wrapper. Do not nest page sections in cards.

- [ ] **Step 5: Implement API loading and mutations**

Load only endpoints needed by the active tab, preserve cached data during refresh errors, show freshness state and source timestamp, trigger manual refresh asynchronously, and refresh only the affected tab after writes.

- [ ] **Step 6: Pass authenticated props from App**

```tsx
{view === "operations-management" && (
  <OperationsManagementPage token={token} role={user?.role || "viewer"} showToast={showToast} />
)}
```

- [ ] **Step 7: Run and verify GREEN**

Run: `npm --prefix frontend test -- OperationsManagementPage.test.tsx`

Expected: all operations frontend tests PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/pages/OperationsManagementPage.tsx frontend/src/pages/OperationsManagementPage.test.tsx frontend/src/App.tsx
git commit -m "feat: build operations management workspace"
```

### Task 7: Responsive Styling and Integration Verification

**Files:**
- Modify: `frontend/styles.css`
- Modify: `frontend/src/pages/OperationsManagementPage.test.tsx`

- [ ] **Step 1: Add layout assertions before CSS**

Assert stable classes for the query bar, metric grid, trend grid, data table scroll containers, freshness banner, and 75%-width dialog.

- [ ] **Step 2: Add restrained responsive styles**

Use full-width bands, compact metric tiles, `minmax()` grids, horizontal table scrolling, and dialogs with `width: min(75vw, 1120px)` plus a mobile `width: calc(100vw - 24px)`. Keep cards at 8px radius or less and prevent label/value overlap.

- [ ] **Step 3: Run frontend unit and build verification**

Run: `npm --prefix frontend test`

Expected: all Vitest tests PASS.

Run: `npm --prefix frontend run build`

Expected: TypeScript and Vite build PASS.

- [ ] **Step 4: Run complete backend verification**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -v`

Expected: all backend tests PASS.

- [ ] **Step 5: Apply migrations to the configured Growth database**

Run: `python -m uv --directory backend run python scripts/init_growth_database.py`

Expected: `0002_operations_analytics` applied or already present, with all required tables reported healthy. Do not print the DSN.

- [ ] **Step 6: Run the local server and inspect desktop/mobile**

Start the development server using the existing project command, open `/operations-management`, and verify at 1440x900 and 390x844: no overlaps, all tabs reachable, tables scroll, dialogs fit, stale states remain readable, and write controls are absent for operator.

- [ ] **Step 7: Commit**

```bash
git add frontend/styles.css frontend/src/pages/OperationsManagementPage.test.tsx
git commit -m "style: finish operations management layout"
```

### Task 8: Production Source Mapping and Acceptance Audit

**Files:**
- Modify: `backend/app/modules/operations/adapters/sub2api.py`
- Modify: `backend/app/modules/operations/adapters/newapi.py`
- Modify: `backend/tests/test_operations_adapters.py`
- Create: `docs/operations/source-field-mapping-2026-07-25.md`

- [ ] **Step 1: Run read-only schema inspection for `aiwelink` and `aigclink`**

Use the existing configured client-site DSNs and read-only inspection utility. Record table/column/version evidence without values or secrets.

- [ ] **Step 2: Replace any rejected candidate mapping with verified column mapping**

Update each adapter query only from inspected schema evidence. Add anonymized mapping-result fixtures that cover payment, redemption, admin adjustment, usage, and user snapshots.

- [ ] **Step 3: Run adapter and full regression tests**

Run: `python -m uv --directory backend run python -m unittest discover -s tests -p "test_operations_adapters.py" -v`

Expected: all verified mapping fixtures PASS.

Run: `python -m uv --directory backend run python -m unittest discover -s tests -v`

Expected: full backend suite PASS.

- [ ] **Step 4: Execute V1 acceptance with non-production test identities**

Verify both rates, one internal identity per site, sales/promotion/internal credit, internal and ordinary consumption, a pending out-of-band record, classification reaggregation, cached reads during source outage, operator write denial, and audit records.

- [ ] **Step 5: Commit source evidence**

```bash
git add backend/app/modules/operations/adapters backend/tests/test_operations_adapters.py docs/operations/source-field-mapping-2026-07-25.md
git commit -m "docs: verify operations source mappings"
```
