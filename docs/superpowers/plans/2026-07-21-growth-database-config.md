# Growth Database Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an owner/admin-only management page that securely saves and tests the single global PostgreSQL connection used by future traffic analysis features.

**Architecture:** Store the private DSN in the existing MongoDB `app_settings` collection under `_id=growth_database`, expose only a redacted public settings contract, and reuse the current SQL DSN parser and connection probe. Register the page directly below “客户站点”; the page calls three authenticated `/settings/growth-database` endpoints and never receives the full DSN.

**Tech Stack:** FastAPI, Motor/MongoDB, SQLAlchemy async, asyncpg, React 19, TypeScript, Vitest, Python unittest

---

### Task 1: Generalize the Database Probe and Add Global Settings Logic

**Files:**
- Create: `backend/app/modules/system/growth_database_settings.py`
- Create: `backend/tests/test_growth_database_settings.py`
- Modify: `backend/app/modules/system/client_site_database.py`
- Modify: `backend/tests/test_client_site_database.py`

- [x] **Step 1: Write failing global settings tests**

Create tests that import the wished-for API:

```python
from app.modules.system.growth_database_settings import (
    get_growth_database_settings,
    run_growth_database_test,
    update_growth_database_settings,
)
```

Cover these behaviors with `unittest.IsolatedAsyncioTestCase` and `AsyncMock` collections:

```python
async def test_unconfigured_settings_return_public_defaults(self):
    settings = await get_growth_database_settings(fake_db_with_find_one(None))
    self.assertEqual(settings["database_type"], "postgresql")
    self.assertFalse(settings["sql_dsn_configured"])
    self.assertNotIn("sql_dsn", settings)

async def test_valid_dsn_is_saved_but_never_returned(self):
    result = await update_growth_database_settings(
        db,
        sql_dsn="host=growth.internal user=growth_app password=secret dbname=aiwelink_growth sslmode=disable",
        actor={"_id": "admin@example.com"},
    )
    self.assertTrue(result["sql_dsn_configured"])
    self.assertEqual(result["database_endpoint"], "growth.internal:5432/aiwelink_growth")
    self.assertNotIn("secret", str(result))

async def test_blank_update_preserves_existing_secret(self):
    await update_growth_database_settings(db, sql_dsn="", actor={"_id": "admin@example.com"})
    self.assertNotIn("sql_dsn", collection.update_one.await_args.args[1]["$set"])

async def test_invalid_or_missing_dsn_is_rejected(self):
    with self.assertRaises(ValueError):
        await update_growth_database_settings(db, sql_dsn="reader:secret@tcp(mysql:3306)/db", actor={})
```

Also cover persisted successful and failed probes, missing configuration, and failure redaction.

- [x] **Step 2: Run the new tests and verify RED**

Run:

```powershell
python -m unittest tests.test_growth_database_settings -v
```

Expected: import failure because `growth_database_settings.py` does not exist.

- [x] **Step 3: Extract a generic SQL probe**

Add this boundary to `client_site_database.py` while preserving `probe_database_connection(site, ...)`:

```python
async def probe_sql_database_connection(
    sql_dsn: str,
    database_type: str,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    ...
```

It parses the explicit database type, executes `SELECT 1` and `SELECT VERSION()`, returns the same safe result contract, redacts failures, and always disposes the engine. `probe_database_connection` becomes a client-site adapter that derives the database type and calls this generic function.

- [x] **Step 4: Implement the global settings module**

Implement these public functions in `growth_database_settings.py`:

```python
SETTINGS_ID = "growth_database"

async def get_growth_database_settings(db) -> dict[str, Any]: ...
async def get_growth_database_settings_private(db) -> dict[str, Any]: ...
async def update_growth_database_settings(db, *, sql_dsn: str, actor: dict[str, Any]) -> dict[str, Any]: ...
async def run_growth_database_test(db, *, engine_factory=create_async_engine) -> dict[str, Any]: ...
```

Use `parse_sql_dsn(value, "postgresql")`, `sql_dsn_endpoint`, `now_utc`, and an `_id=growth_database` singleton. Public conversion must always remove `_id`, `sql_dsn`, `updated_by`, and any database username/password. Blank input preserves an existing secret and rejects an unconfigured document.

`run_growth_database_test` calls `probe_sql_database_connection`, persists all five `last_database_*` fields, and returns `{**probe_result, "settings": public_settings}` for both successful and failed probes.

- [x] **Step 5: Run focused backend tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_growth_database_settings tests.test_client_site_database -v
```

Expected: all tests in both modules pass.

### Task 2: Add Authenticated Settings Routes and Safe Auditing

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/routers/settings.py`
- Modify: `backend/tests/test_growth_database_settings.py`

- [x] **Step 1: Write failing router tests**

Patch the service functions and `write_audit_log`, then directly call the route functions to assert:

```python
response = await settings_router.put_growth_database_settings(
    GrowthDatabaseSettingsUpdate(sql_dsn=dsn),
    actor={"_id": "admin@example.com", "role": "admin"},
    db=MagicMock(),
)
self.assertNotIn("sql_dsn", response)
self.assertNotIn("secret", str(audit_mock.await_args.kwargs))
```

Also assert the test route returns `ok=false` without leaking the DSN and that service `ValueError` becomes HTTP 400.

- [x] **Step 2: Run router tests and verify RED**

Run:

```powershell
python -m unittest tests.test_growth_database_settings -v
```

Expected: failure because the schema and route functions do not exist.

- [x] **Step 3: Add the update schema and routes**

Add to `schemas.py`:

```python
class GrowthDatabaseSettingsUpdate(BaseModel):
    sql_dsn: str = ""
```

Add to `routers/settings.py`:

```python
@router.get("/growth-database")
async def get_growth_database_settings_route(...): ...

@router.put("/growth-database")
async def put_growth_database_settings(payload: GrowthDatabaseSettingsUpdate, ...): ...

@router.post("/growth-database/test")
async def post_growth_database_test(...): ...
```

All three routes use `require_roles("owner", "admin")`. Save audits use public `before` and `after`; test audits include only `ok`, endpoint, latency, version, and redacted error. Convert missing or invalid settings `ValueError` to HTTP 400. A failed connection remains HTTP 200 with `ok=false`.

- [x] **Step 4: Run backend settings tests and verify GREEN**

Run:

```powershell
python -m unittest tests.test_growth_database_settings -v
```

Expected: all global settings and route tests pass.

### Task 3: Add Role-Aware Navigation and the Configuration Page

**Files:**
- Create: `frontend/src/pages/TrafficAnalysisConfigPage.tsx`
- Create: `frontend/src/pages/TrafficAnalysisConfigPage.test.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.ts`

- [x] **Step 1: Write failing navigation tests**

Change `getVisibleNavigationGroups` to the wished-for role API in tests and assert:

```ts
expect(keysFor("owner")).toContain("traffic-analysis-config");
expect(keysFor("admin")).toContain("traffic-analysis-config");
expect(keysFor("maintainer")).not.toContain("traffic-analysis-config");
expect(keysFor("viewer")).not.toContain("traffic-analysis-config");
expect(viewFromPath("/traffic-analysis-config")).toBe("traffic-analysis-config");
```

Assert the pool operations group order ends with:

```ts
["event-records", "alert-center", "pool-lifecycle", "client-sites", "traffic-analysis-config"]
```

- [x] **Step 2: Write failing form rendering tests**

Use `renderToStaticMarkup` with an exported presentational `GrowthDatabaseConfigForm` to verify:

```tsx
expect(unconfiguredHtml).toContain("访问流量分析配置");
expect(unconfiguredHtml).toContain("PostgreSQL");
expect(unconfiguredHtml).toContain("disabled");
expect(configuredHtml).toContain("growth.internal:5432/aiwelink_growth");
expect(configuredHtml).not.toContain("secret");
```

- [x] **Step 3: Run focused frontend tests and verify RED**

Run:

```powershell
npm.cmd test -- App.test.ts TrafficAnalysisConfigPage.test.tsx
```

Expected: failures because the new view, role API, and page do not exist.

- [x] **Step 4: Implement role-aware navigation**

Add `"traffic-analysis-config"` to `ViewName`, import `UserRole`, and change navigation selection to:

```ts
export function canAccessTrafficAnalysisConfig(role?: UserRole) {
  return role === "owner" || role === "admin";
}

export function getVisibleNavigationGroups(role?: UserRole) {
  ...
}
```

Append `["traffic-analysis-config", "访问流量分析配置"]` immediately after `client-sites`, add short label `配`, route `/traffic-analysis-config`, and render the page only when `canAccessTrafficAnalysisConfig(user?.role)` is true. Extend the protected-view redirect effect so disallowed direct access returns to `api-pools`.

- [x] **Step 5: Implement the page**

Define these contracts in `TrafficAnalysisConfigPage.tsx`:

```ts
export type GrowthDatabaseSettings = {
  database_type: "postgresql";
  sql_dsn_configured: boolean;
  database_endpoint: string;
  last_database_test_at: string | null;
  last_database_test_ok: boolean | null;
  last_database_test_error: string;
  last_database_latency_ms: number | null;
  last_database_version: string;
};
```

`TrafficAnalysisConfigPage` loads `GET /settings/growth-database`, saves with `PUT /settings/growth-database`, and tests with `POST /settings/growth-database/test`. Keep DSN state empty after load and after successful save. Use the existing `site-database-section`, `site-config-grid`, `sql-dsn-input`, `database-test-result`, button, toast, date formatting, and error formatting patterns.

Export `GrowthDatabaseConfigForm` with explicit props for settings, DSN, loading states, and callbacks so server-rendered tests exercise the real presentation. The test button is disabled until `sql_dsn_configured=true`.

- [x] **Step 6: Run focused frontend tests and verify GREEN**

Run:

```powershell
npm.cmd test -- App.test.ts TrafficAnalysisConfigPage.test.tsx
```

Expected: all focused tests pass.

### Task 4: Full Verification and Commit

**Files:**
- Verify all files above
- Modify: `docs/superpowers/plans/2026-07-21-growth-database-config.md`

- [x] **Step 1: Run focused backend regression tests**

```powershell
python -m unittest tests.test_growth_database_settings tests.test_client_site_database tests.test_client_sites -v
```

Expected: all focused tests pass.

- [x] **Step 2: Run the complete backend suite**

```powershell
python -m unittest discover -s tests -p "test_*.py"
```

Expected: all backend tests pass.

- [x] **Step 3: Run the complete frontend suite**

```powershell
npm.cmd test
```

Expected: all frontend tests pass.

- [x] **Step 4: Run the production frontend build**

```powershell
npm.cmd run build
```

Expected: TypeScript and Vite build complete successfully.

- [x] **Step 5: Verify desktop and mobile UI**

Open `/traffic-analysis-config` while authenticated as owner/admin at `1440x900` and `390x844`. Confirm the navigation item appears below “客户站点”, the form has no overflow or overlap, a configured DSN is never visible, and maintainer/viewer navigation does not contain the page.

- [x] **Step 6: Commit the implementation**

```powershell
git add backend/app/modules/system/client_site_database.py backend/app/modules/system/growth_database_settings.py backend/app/routers/settings.py backend/app/schemas.py backend/tests/test_client_site_database.py backend/tests/test_growth_database_settings.py frontend/src/App.tsx frontend/src/App.test.ts frontend/src/types.ts frontend/src/pages/TrafficAnalysisConfigPage.tsx frontend/src/pages/TrafficAnalysisConfigPage.test.tsx docs/superpowers/plans/2026-07-21-growth-database-config.md
git commit -m "feat: configure growth PostgreSQL"
```
