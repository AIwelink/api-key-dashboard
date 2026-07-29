# Traffic Analytics Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a last-touch traffic overview and milestone account drill-down to the existing traffic-analysis management page while preserving independent configuration pages and ordinary/internal user segmentation.

**Architecture:** The external `traffic-analysis` service remains the only runtime writer. The current admin backend adds read-only aggregation endpoints against Growth PostgreSQL through a focused analytics schema/repository/service boundary. A standalone React overview component consumes those endpoints; the existing configuration workspace only adds a new default tab and passes site/channel/campaign/link metadata.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy async, PostgreSQL, React 19, TypeScript, Vitest, existing API client and CSS.

---

## File Structure

- Create `backend/app/modules/growth/analytics_schemas.py`: ranges, filters, window resolution, rate calculation, response models.
- Create `backend/app/modules/growth/analytics_repository.py`: read-only PostgreSQL aggregation and milestone-user queries.
- Create `backend/app/modules/growth/analytics_service.py`: Growth connection lifecycle and response assembly.
- Modify `backend/app/routers/growth.py`: authenticated overview and users routes.
- Create `backend/tests/test_growth_analytics.py`: range, rate, SQL filter and response assembly tests.
- Modify `backend/tests/test_growth_routes.py`: route permission and normalized 503 coverage.
- Create `frontend/src/pages/trafficAnalysis/TrafficOverview.tsx`: overview query state and pure rendering surface.
- Create `frontend/src/pages/trafficAnalysis/TrafficOverview.test.tsx`: rendering and URL/filter tests.
- Modify `frontend/src/pages/TrafficAnalysisPage.tsx`: default overview tab and independent overview mounting.
- Modify `frontend/src/pages/TrafficAnalysisPage.test.tsx`: five-tab and isolation expectations.
- Modify `frontend/styles.css`: dense full-width overview, funnel, metric band and responsive tables.

### Task 1: Analytics Domain Contract

**Files:**
- Create: `backend/app/modules/growth/analytics_schemas.py`
- Test: `backend/tests/test_growth_analytics.py`

- [ ] **Step 1: Write failing domain tests**

Add tests for a fixed `now` covering `24h`, `7d`, `30d`, and `90d`, `day` versus `hour` buckets, invalid filter combinations, and nullable rates:

```python
window = resolve_traffic_window("7d", now=datetime(2026, 7, 25, tzinfo=UTC))
self.assertEqual(window.start_at, datetime(2026, 7, 18, tzinfo=UTC))
self.assertEqual(window.bucket, "day")
self.assertIsNone(safe_rate(1, 0))
self.assertEqual(safe_rate(1, 4), 0.25)
```

- [ ] **Step 2: Run the domain tests and confirm failure**

Run:

```powershell
python -m uv --directory backend run python -m unittest discover -s tests -p "test_growth_analytics.py" -v
```

Expected: import failure for `app.modules.growth.analytics_schemas`.

- [ ] **Step 3: Implement query and response models**

Define:

```python
TrafficRange = Literal["24h", "7d", "30d", "90d"]
UserSegment = Literal["ordinary", "internal", "all"]
SourceKind = Literal["promotion", "direct", "organic_search", "referral"]
Milestone = Literal["registered", "called", "paid", "second_paid", "continued", "refunded"]

class TrafficAnalyticsFilters(BaseModel):
    range_key: TrafficRange = "7d"
    segment: UserSegment = "ordinary"
    site_id: str | None = None
    source_kind: SourceKind | None = None
    channel_id: UUID | None = None
    campaign_id: UUID | None = None
    tracking_link_id: UUID | None = None

class TrafficUsersQuery(TrafficAnalyticsFilters):
    milestone: Milestone = "registered"
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
```

Add immutable `TrafficWindow`, summary/rate/amount/trend/source/link/user response models, `resolve_traffic_window()`, and `safe_rate()`.

- [ ] **Step 4: Run the domain tests and confirm pass**

Run the command from Step 2. Expected: all analytics domain tests pass.

### Task 2: Read-Only Growth Aggregations

**Files:**
- Create: `backend/app/modules/growth/analytics_repository.py`
- Modify: `backend/tests/test_growth_analytics.py`

- [ ] **Step 1: Write failing repository tests**

Use a fake async connection that captures SQL and returns mapping rows. Assert:

```python
result = await load_traffic_summary(connection, filters, window)
self.assertEqual(result["registered_accounts"], 3)
self.assertIn("growth.homepage_visits", connection.calls[0][0])
self.assertIn("growth.internal_users", "\n".join(sql for sql, _ in connection.calls))
self.assertNotIn("SELECT *", "\n".join(sql for sql, _ in connection.calls))
```

Cover ordinary/internal/all segment predicates, `site_id` and promotion metadata filters, currency grouping, milestone predicates, limit and offset.

- [ ] **Step 2: Run repository tests and confirm failure**

Run the Task 1 test command. Expected: repository imports or functions missing.

- [ ] **Step 3: Implement bound SQL queries**

Implement these focused async functions with the stated return values: `load_traffic_summary` returns one normalized summary mapping; `load_traffic_trends`, `load_source_breakdown`, `load_link_performance`, and `load_amounts` return JSON-safe mapping lists; `list_milestone_users` returns `(items, total)`. Every function accepts the same connection plus typed filters and resolved window, and the user function accepts `TrafficUsersQuery` for milestone and pagination.

Use bound `text()` statements only. Build a shared registration Cohort CTE that:

```sql
LEFT JOIN growth.internal_users internal
  ON internal.site_id = attribution.site_id
 AND internal.external_user_id = attribution.external_user_id
 AND internal.active_from <= attribution.registered_at
 AND (internal.active_until IS NULL OR internal.active_until > attribution.registered_at)
LEFT JOIN growth.user_exclusions excluded
  ON excluded.site_id = attribution.site_id
 AND excluded.external_user_id = attribution.external_user_id
 AND excluded.is_active
```

`ordinary` requires no internal match and no active generic exclusion. `internal` requires an internal match even if a generic exclusion exists. `all` includes ordinary plus internal but still removes non-internal excluded accounts. Promotion metadata filters join `tracking_links`, `campaigns`, and `channels`. Link rankings are capped at 50.

- [ ] **Step 4: Run repository tests and confirm pass**

Run the Task 1 test command. Expected: all tests pass.

### Task 3: Analytics Service and HTTP Routes

**Files:**
- Create: `backend/app/modules/growth/analytics_service.py`
- Modify: `backend/app/routers/growth.py`
- Modify: `backend/tests/test_growth_analytics.py`
- Modify: `backend/tests/test_growth_routes.py`

- [ ] **Step 1: Write failing service and route tests**

Test service assembly with patched repository functions and assert rate denominators. Add route tests asserting:

```python
self.assertIn("/growth/analytics/overview", paths)
self.assertIn("/growth/analytics/users", paths)
self.assertEqual(_dependency_permission(dependencies[0]), "traffic-analysis")
```

Patch service functions to verify query conversion and normalize SQL/schema errors to HTTP 503 without affecting configuration routes.

- [ ] **Step 2: Run route tests and confirm failure**

Run:

```powershell
python -m uv --directory backend run python -m unittest discover -s tests -p "test_growth_analytics.py" -v
python -m uv --directory backend run python -m unittest discover -s tests -p "test_growth_routes.py" -v
```

Expected: analytics service and routes missing.

- [ ] **Step 3: Implement service assembly**

Use `growth_connection(mongo_db)` once per request, call repository functions on one consistent connection, calculate rates with `safe_rate()`, and return `generated_at`, window, summary, rates, amounts, trends, source breakdown and link performance. The users service returns `{items, total, limit, offset, generated_at}`.

- [ ] **Step 4: Add authenticated routes**

Add the routes with explicit FastAPI query parsing:

```python
@router.get("/analytics/overview")
async def get_growth_analytics_overview_route(
    range_key: TrafficRange = Query(default="7d", alias="range"),
    segment: UserSegment = Query(default="ordinary"),
    site_id: str | None = Query(default=None),
    source_kind: SourceKind | None = Query(default=None),
    channel_id: UUID | None = Query(default=None),
    campaign_id: UUID | None = Query(default=None),
    tracking_link_id: UUID | None = Query(default=None),
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    del actor
    filters = TrafficAnalyticsFilters(
        range_key=range_key,
        segment=segment,
        site_id=site_id,
        source_kind=source_kind,
        channel_id=channel_id,
        campaign_id=campaign_id,
        tracking_link_id=tracking_link_id,
    )
    try:
        return await get_traffic_analytics_overview(db, filters)
    except Exception as exc:
        _raise_http_error(exc)

@router.get("/analytics/users")
async def get_growth_analytics_users_route(
    range_key: TrafficRange = Query(default="7d", alias="range"),
    segment: UserSegment = Query(default="ordinary"),
    milestone: Milestone = Query(default="registered"),
    site_id: str | None = Query(default=None),
    source_kind: SourceKind | None = Query(default=None),
    channel_id: UUID | None = Query(default=None),
    campaign_id: UUID | None = Query(default=None),
    tracking_link_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    del actor
    query = TrafficUsersQuery(
        range_key=range_key,
        segment=segment,
        milestone=milestone,
        site_id=site_id,
        source_kind=source_kind,
        channel_id=channel_id,
        campaign_id=campaign_id,
        tracking_link_id=tracking_link_id,
        limit=limit,
        offset=offset,
    )
    try:
        return await get_traffic_analytics_users(db, query)
    except Exception as exc:
        _raise_http_error(exc)
```

Both routes use `require_view_permission(GROWTH_PERMISSION)`. Parse `range` with a FastAPI alias, pass UUID filters without string interpolation, and reuse `_raise_http_error()`.

- [ ] **Step 5: Run backend analytics and route tests**

Run both commands from Step 2. Expected: all tests pass.

### Task 4: Pure Traffic Overview Component

**Files:**
- Create: `frontend/src/pages/trafficAnalysis/TrafficOverview.tsx`
- Create: `frontend/src/pages/trafficAnalysis/TrafficOverview.test.tsx`

- [ ] **Step 1: Write failing frontend tests**

Render a supplied overview fixture and assert:

```typescript
expect(html).toContain("主页 PV（全站）");
expect(html).toContain("推广链接 UV");
expect(html).toContain("注册转化漏斗");
expect(html).toContain("末次触发归因");
expect(html).toContain("普通用户");
```

Test empty values as `--`, milestone selection callback, source labels, link ranking, account rows, and query URL generation.

- [ ] **Step 2: Run component tests and confirm failure**

Run:

```powershell
npm test -- --run src/pages/trafficAnalysis/TrafficOverview.test.tsx
```

Working directory: `frontend`. Expected: component import missing.

- [ ] **Step 3: Implement types, query builder and view**

Export `TrafficOverviewResponse`, `TrafficUsersResponse`, `TrafficOverviewFilters`, `buildTrafficAnalyticsQuery()` and `TrafficOverviewView`. Render full-width sections in this order: filters, metric band, funnel, trend table, source table, link table, account drill-down. Buttons for funnel stages use clear text commands and preserve stable dimensions.

- [ ] **Step 4: Implement the data-owning component**

`TrafficOverview` accepts `token`, site/channel/campaign/link metadata, and `showToast`. It independently fetches `/growth/analytics/overview` and `/growth/analytics/users`, defaults to `7d` and `ordinary`, aborts stale effects, and keeps configuration-page failures isolated. Clicking a milestone loads the matching account list.

- [ ] **Step 5: Run component tests and confirm pass**

Run the command from Step 2. Expected: all component tests pass.

### Task 5: Add the Default Overview Tab

**Files:**
- Modify: `frontend/src/pages/TrafficAnalysisPage.tsx`
- Modify: `frontend/src/pages/TrafficAnalysisPage.test.tsx`

- [ ] **Step 1: Write failing workspace tests**

Update fixtures to assert the tab order:

```text
流量概览 / 推广链接 / 渠道管理 / 活动管理 / 站点接入
```

Assert the page defaults to `overview`, mounts `TrafficOverview`, and existing configuration loading/error behavior remains unchanged.

- [ ] **Step 2: Run page tests and confirm failure**

Run:

```powershell
npm test -- --run src/pages/TrafficAnalysisPage.test.tsx
```

Working directory: `frontend`. Expected: overview tab missing.

- [ ] **Step 3: Mount the isolated overview**

Extend `TrafficAnalysisTab` with `overview`, make it the initial tab, add the tab first, pass metadata and token to `TrafficOverview`, and render configuration loading/error only for configuration tabs. Do not move existing CRUD forms or mutate their state model.

- [ ] **Step 4: Run page and overview tests**

Run both frontend test files. Expected: all pass.

### Task 6: Responsive Styling and Full Verification

**Files:**
- Modify: `frontend/styles.css`

- [ ] **Step 1: Add focused overview styles**

Add `.traffic-overview-*` selectors for a restrained metric band, horizontal funnel, full-width tables, status/error rows and mobile overflow. Keep card radius at or below 8px, avoid nested cards, and preserve existing Growth configuration selectors.

- [ ] **Step 2: Run focused backend tests**

Run:

```powershell
python -m uv --directory backend run python -m unittest discover -s tests -p "test_growth*.py" -v
```

Expected: all Growth tests pass.

- [ ] **Step 3: Run full frontend tests and build**

Run in `frontend`:

```powershell
npm test
npm run build
```

Expected: Vitest exits 0 and TypeScript/Vite build exits 0.

- [ ] **Step 4: Inspect desktop and mobile rendering**

Start the existing Vite server, open `/traffic-analysis` authenticated at 1440x900 and 390x844, and verify: no overlaps, tab scrolling works, metric values do not shift layout, tables scroll horizontally, and configuration tabs remain usable when overview returns an error.

- [ ] **Step 5: Review final scope**

Confirm the diff does not alter `traffic-analysis` runtime attribution code, operations-management work in progress, or unrelated files. Confirm the authoritative docs contain no first-touch rule and the implementation contains no fake analytics fixture in production paths.
