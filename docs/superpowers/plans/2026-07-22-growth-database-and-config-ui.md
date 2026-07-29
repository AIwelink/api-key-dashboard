# Growth Database and Configuration UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Initialize the complete Growth PostgreSQL schema and replace the blank traffic-analysis page with working site, channel, campaign, and tracking-link configuration.

**Architecture:** Keep Growth PostgreSQL access in a new `app.modules.growth` boundary using SQLAlchemy Core and the existing MongoDB-held DSN. Use ordered Python migrations recorded in `growth.schema_migrations`, then expose owner/admin-only configuration APIs through the existing management backend. The frontend remains a quiet operational workspace and only presents configuration supported by real APIs; redirect attribution and analytics metrics remain outside this delivery.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy asyncio/asyncpg, MongoDB/Motor, React 19, TypeScript, Vitest, CSS.

---

## File Map

- Create `backend/app/modules/growth/__init__.py`: Growth module boundary.
- Create `backend/app/modules/growth/database.py`: configured engine lifecycle and transactional connection helper.
- Create `backend/app/modules/growth/migrations.py`: ordered schema migrations, status inspection, and idempotent initializer.
- Create `backend/app/modules/growth/repository.py`: site, channel, campaign, and tracking-link SQL operations.
- Create `backend/app/modules/growth/schemas.py`: Pydantic input models and validation.
- Create `backend/app/routers/growth.py`: owner/admin-only configuration API and audit calls.
- Create `backend/scripts/init_growth_database.py`: explicit command-line initializer using the configured DSN.
- Create `backend/tests/test_growth_migrations.py`: migration order, idempotency, schema coverage, and status tests.
- Create `backend/tests/test_growth_repository.py`: configuration validation, SQL parameterization, and mapping tests.
- Create `backend/tests/test_growth_routes.py`: role boundary, error mapping, and audit tests.
- Modify `backend/app/modules/system/growth_database_settings.py`: expose schema status and initialization service.
- Modify `backend/app/routers/settings.py`: schema status and initialize endpoints.
- Modify `backend/app/main.py`: register the Growth router.
- Modify `frontend/src/pages/TrafficAnalysisConfigPage.tsx`: show schema status and initialize action.
- Modify `frontend/src/pages/TrafficAnalysisConfigPage.test.tsx`: schema initialization rendering tests.
- Replace `frontend/src/pages/TrafficAnalysisPage.tsx`: configuration workspace and API integration.
- Create `frontend/src/pages/TrafficAnalysisPage.test.tsx`: form and payload tests.
- Modify `frontend/src/App.tsx`: pass auth props and enforce owner/admin access.
- Modify `frontend/src/App.test.ts`: traffic-analysis visibility and route access tests.
- Modify `frontend/styles.css`: scoped responsive operational layout.
- Modify `backend/README.md` and `frontend/README.md`: initialization and page behavior.

## Task 1: Versioned Growth Schema

- [ ] **Step 1: Write failing migration contract tests**

Add tests that require migration version `0001_initial`, `growth.schema_migrations`, all 12 required domain tables, composite account keys, immutable tracking code constraints, status checks, foreign keys, and indexes. The test should assert statement content and runner behavior without needing a production database.

```python
class GrowthMigrationContractTests(unittest.TestCase):
    def test_initial_migration_contains_required_tables(self) -> None:
        sql = "\n".join(INITIAL_MIGRATION.statements)
        for table in REQUIRED_TABLES:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS growth.{table}", sql)
        self.assertIn("PRIMARY KEY (site_id, external_user_id)", sql)
        self.assertIn("UNIQUE (code)", sql)
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_growth_migrations -v
```

Expected: import failure because `app.modules.growth.migrations` does not exist.

- [ ] **Step 3: Implement database and migration modules**

Implement a `Migration` dataclass and ordered `MIGRATIONS`. The initializer must:

1. Create schema and migration ledger.
2. Acquire `pg_advisory_xact_lock(hashtext('aiwelink-growth-migrations'))`.
3. Read applied versions.
4. Execute each unapplied statement in one transaction.
5. Insert the version only after all statements succeed.
6. Return `current_version`, `latest_version`, `pending_versions`, and `initialized`.

The initial migration creates:

```text
sites, channels, campaigns, tracking_links, link_visits,
user_attributions, user_exclusions, user_usage_daily,
billing_facts, user_facts, sync_cursors, sync_runs
```

- [ ] **Step 4: Verify GREEN**

Run the migration test command again. Expected: all migration tests pass.

## Task 2: Initialization Service and CLI

- [ ] **Step 1: Write failing service tests**

Tests require configured DSN parsing, engine disposal, public schema status, initialization result persistence, and secret-free errors.

```python
async def test_initialize_disposes_engine(self) -> None:
    result = await initialize_growth_database(fake_mongo, engine_factory=factory)
    self.assertTrue(result["initialized"])
    engine.dispose.assert_awaited_once()
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_growth_migrations tests.test_growth_database_settings -v
```

Expected: missing initialization functions and status fields.

- [ ] **Step 3: Implement service, settings routes, and CLI**

Add:

```http
GET  /api/settings/growth-database/schema
POST /api/settings/growth-database/initialize
```

Both routes require `owner/admin`. The POST audit stores versions and table count, never the DSN. The CLI runs:

```powershell
.\.venv\Scripts\python.exe -m scripts.init_growth_database
```

and prints a JSON summary without credentials.

- [ ] **Step 4: Verify GREEN**

Run the same backend tests. Expected: all pass.

## Task 3: Growth Configuration Repository and API

- [ ] **Step 1: Write failing repository and route tests**

Cover:

- site configuration only for an existing MongoDB `client_sites` record;
- channel code normalization and uniqueness conflict mapping;
- campaign ownership by one site and channel;
- eight-character server-generated tracking code;
- one tracking link targeting one site;
- relative landing-path validation and open-redirect rejection;
- maximum three flat string dimensions;
- owner/admin role dependencies and safe audit payloads.

```python
def test_landing_path_rejects_external_url(self) -> None:
    with self.assertRaises(ValidationError):
        TrackingLinkCreate(
            site_id="aiwelink",
            campaign_id=uuid4(),
            source_type="post",
            source_name="post",
            landing_path="https://evil.example/",
        )
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_growth_repository tests.test_growth_routes -v
```

Expected: missing repository, schemas, and router.

- [ ] **Step 3: Implement minimal configuration APIs**

Add:

```http
GET  /api/growth/sites
PUT  /api/growth/sites/{site_id}
GET  /api/growth/channels
POST /api/growth/channels
PATCH /api/growth/channels/{channel_id}
GET  /api/growth/campaigns
POST /api/growth/campaigns
PATCH /api/growth/campaigns/{campaign_id}
GET  /api/growth/tracking-links
POST /api/growth/tracking-links
PATCH /api/growth/tracking-links/{tracking_link_id}
```

All SQL uses `sqlalchemy.text()` bind parameters. Map unique violations to HTTP 409, missing references to 404, validation to 400, and unavailable/uninitialized Growth DB to 503.

- [ ] **Step 4: Verify GREEN**

Run repository, route, migration, and existing Growth settings tests. Expected: all pass.

## Task 4: Database Configuration Initialization UI

- [ ] **Step 1: Write failing rendering tests**

Require these states:

```text
未初始化 → 初始化数据库 enabled
已初始化且最新 → shows schema version and 12 tables
存在待执行版本 → shows upgrade action
initializing → all database actions disabled
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
npm.cmd test -- --run src/pages/TrafficAnalysisConfigPage.test.tsx
```

Expected: assertions fail because schema status is not rendered.

- [ ] **Step 3: Implement schema status and initialize action**

Load `/settings/growth-database/schema` after database settings. POST initialize only after explicit click, refresh status, and show the backend result. Do not automatically modify the database when saving DSN.

- [ ] **Step 4: Verify GREEN**

Run the same frontend test. Expected: pass.

## Task 5: Traffic Configuration Workspace

- [ ] **Step 1: Write failing page tests**

Tests require the page to render:

- tabs for `推广链接`, `渠道与活动`, and `站点接入`;
- a site-first tracking-link form;
- channel and campaign selectors;
- concrete source name/type, promoter, source URL, landing path, and three optional dimensions;
- generated `/r/{code}` result rows;
- empty/loading/error states without fake metric numbers;
- payload builders that trim strings and omit empty dimensions.

- [ ] **Step 2: Verify RED**

Run:

```powershell
npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx src/App.test.ts
```

Expected: tests fail because the page is still blank and App supplies no props.

- [ ] **Step 3: Implement the page and scoped CSS**

Use a compact operations layout:

```text
Header: 访问流量分析 + 新建推广链接
Tabs: 推广链接 | 渠道与活动 | 站点接入
Toolbar: site / channel / campaign / status filters
Main band: searchable link table
Side panel or inline editor: selected configuration form
```

Use existing 8 px-or-less radii, panel borders, controls, toast behavior, and responsive breakpoints. No marketing hero, nested cards, decorative gradients, or unsupported analytics figures.

- [ ] **Step 4: Verify GREEN**

Run page and App tests. Expected: pass.

## Task 6: Initialize Configured Database and Verify End to End

- [ ] **Step 1: Run all focused backend tests**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_growth_migrations tests.test_growth_database_settings tests.test_growth_repository tests.test_growth_routes -v
```

- [ ] **Step 2: Run the configured database initializer**

```powershell
.\.venv\Scripts\python.exe -m scripts.init_growth_database
```

Expected JSON includes:

```json
{"initialized": true, "current_version": "0001_initial", "pending_versions": [], "domain_table_count": 12}
```

- [ ] **Step 3: Verify the remote schema read-only**

Query `information_schema.tables`, `table_constraints`, and `pg_indexes`; confirm all tables and required constraints exist without printing the DSN.

- [ ] **Step 4: Run frontend tests and build**

```powershell
npm.cmd test -- --run src/App.test.ts src/pages/TrafficAnalysisConfigPage.test.tsx src/pages/TrafficAnalysisPage.test.tsx
npm.cmd run build
```

- [ ] **Step 5: Run complete regression suites**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm.cmd test
```

- [ ] **Step 6: Start the local dev server and inspect desktop/mobile layouts**

Open `/traffic-analysis-config` and `/traffic-analysis` at desktop and mobile widths. Confirm no overlap, clipped labels, nested cards, secret exposure, or fake statistics.

- [ ] **Step 7: Commit only Growth files**

Use path-specific `git add`. Do not stage existing Sub2API quota/account-pool changes.
