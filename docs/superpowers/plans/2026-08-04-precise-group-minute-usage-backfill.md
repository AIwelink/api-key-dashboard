# Precise Group Minute Usage Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace approximate hourly-counter delta sampling with exact completed-minute `usage_logs` aggregation, recent-window recalibration, and a persistent seven-day historical backfill cursor.

**Architecture:** PostgreSQL will aggregate all active group IDs by UTC minute for one requested range. The sampler will write a complete group-by-minute matrix to MongoDB with idempotent bulk upserts, preserving existing concurrency fields during recalibration. Each active site will persist one historical one-hour cursor and advance it only after both SQL read and MongoDB writes succeed; cache readers will accept only version-3 exact-minute samples.

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy async engine, PostgreSQL `usage_logs`, Motor/PyMongo bulk writes, MongoDB TTL and compound indexes, `unittest`/ `unittest.mock`.

---

### Task 1: Add the PostgreSQL exact-minute repository contract

**Files:**
- Modify: `backend/app/modules/sub2api/dashboard_postgres_repository.py`
- Test: `backend/tests/test_sub2api_dashboard_postgres_repository.py`

- [ ] **Step 1: Write the failing aggregation contract test**

Add `test_group_minute_usage_aggregates_all_groups_with_left_closed_right_open_bounds` to the existing repository test class. Feed `FakeConnection` two rows:

```python
{
    "group_id": 3,
    "bucket_at": datetime(2026, 8, 4, 1, 4, tzinfo=UTC),
    "total_requests": 4,
    "total_tokens": 100,
    "input_tokens": 40,
    "output_tokens": 30,
    "cache_creation_tokens": 10,
    "cache_read_tokens": 20,
    "account_cost": Decimal("2.50"),
    "source_updated_at": datetime(2026, 8, 4, 1, 4, 55, tzinfo=UTC),
}
```

Call `fetch_group_minute_usage` with `group_ids=[5, 3, 3]`, `start_at=2026-08-04 01:00Z`, and `end_at=2026-08-04 01:20Z`. Assert the returned key `(3, bucket)` contains numeric fields, group 5 has no returned row, the SQL contains `date_trunc('minute'`, `group_id = ANY`, `created_at >= :start_at`, and `created_at < :end_at`, and the parameters normalize IDs to `[3, 5]` and preserve both UTC boundaries.

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run:

```text
backend\\.venv\\Scripts\\python.exe -m unittest tests.test_sub2api_dashboard_postgres_repository.Sub2ApiDashboardPostgresRepositoryTests.test_group_minute_usage_aggregates_all_groups_with_left_closed_right_open_bounds
```

Expected: `AttributeError` because `fetch_group_minute_usage` does not exist yet.

- [ ] **Step 3: Implement the repository query and mapper**

In `dashboard_postgres_repository.py`:

1. Add `GROUP_MINUTE_USAGE_QUERY` that groups `usage_logs` by `group_id` and `date_trunc('minute', created_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'`, sums all four token columns, counts requests, and sums `COALESCE(account_stats_cost, total_cost) * COALESCE(account_rate_multiplier, 1)`.
2. Add:

```python
async def fetch_group_minute_usage(
    sql_dsn: str,
    *,
    group_ids: list[int],
    start_at: datetime,
    end_at: datetime,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[tuple[int, datetime], dict[str, Any]]:
```

Normalize IDs, return `{}` without SQL for an empty list or non-positive range, call `_fetch_rows`, discard rows for unknown groups or invalid buckets, and map integer, float, and UTC datetime values with the existing `_integer`, `_number`, and `_as_utc` helpers.

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```text
backend\\.venv\\Scripts\\python.exe -m unittest tests.test_sub2api_dashboard_postgres_repository.Sub2ApiDashboardPostgresRepositoryTests.test_group_minute_usage_aggregates_all_groups_with_left_closed_right_open_bounds
```

Expected: `PASS`.

- [ ] **Step 5: Add the empty-range and SQL cleanup regression tests**

Add tests asserting an empty group list performs no engine call and `end_at <= start_at` performs no engine call. Reuse the existing failed-read test pattern to assert the engine is disposed when the minute query raises.

- [ ] **Step 6: Run the repository test module**

Run:

```text
backend\\.venv\\Scripts\\python.exe -m unittest tests.test_sub2api_dashboard_postgres_repository
```

Expected: all repository tests pass.

### Task 2: Replace the sampler with exact-minute writes and a backfill cursor

**Files:**
- Modify: `backend/app/modules/sub2api/tpm_sampler.py`
- Test: `backend/tests/test_tpm_sampler.py`

- [ ] **Step 1: Add failing tests for completed-minute windows and v3 documents**

Add small test doubles for an async Mongo cursor and a collection supporting `find_one`, `bulk_write`, and `update_one`. Add these tests:

1. `test_recent_window_excludes_current_partial_minute`: freeze `tpm_sampler.now_utc` at `2026-08-04 01:20:38Z`, return one usage row at `01:19Z`, run the site sampler, and assert the repository receives `start_at=01:00Z`, `end_at=01:20Z`; no `01:20Z` bucket is written.
2. `test_missing_group_minutes_are_written_as_zero`: use groups 3 and 5 with one row for group 3, run the minute writer, and assert two group-minute operations exist, with group 5 `tpm`, `rpm`, and cost equal to zero.
3. `test_recalibration_preserves_existing_concurrency`: run the writer against a pre-existing group-minute document containing `current_concurrency=17`; assert the update operation does not set `current_concurrency` for a historical/recent non-latest bucket.
4. `test_latest_completed_minute_updates_current_concurrency`: assert the bucket `closed_end - 1 minute` receives the remote group concurrency while older buckets do not.

- [ ] **Step 2: Run the focused sampler tests and verify they fail**

Run:

```text
backend\\.venv\\Scripts\\python.exe -m unittest tests.test_tpm_sampler.TpmExactMinuteTests
```

Expected: failures because the v3 writer and exact-minute sampler do not exist.

- [ ] **Step 3: Define v3 sampler constants and exact document mapping**

In `tpm_sampler.py`:

```python
TPM_SAMPLE_SCHEMA_VERSION = 3
TPM_COUNTER_SOURCE = "postgresql_usage_logs_minute"
TPM_RECENT_MINUTES = 20
TPM_BACKFILL_WINDOW = timedelta(hours=1)
TPM_BACKFILL_RANGE = timedelta(days=7)
TPM_BACKFILL_STATE_COLLECTION = "sub2api_tpm_backfill_state"
```

Replace the counter-delta document path with a mapper that writes `tpm`, `calculated_tpm`, `rpm`, `calculated_rpm`, `minute_tokens`, `minute_requests`, token category fields, `minute_account_cost`, `account_cost_per_minute`, `account_cost_per_hour`, `stats_updated_at`, `elapsed_seconds=60`, `source="exact_minute"`, version 3, source name, and the 60-day TTL. Also write compatibility aliases `total_account_cost=minute_account_cost` and `account_cost_delta=minute_account_cost` for existing capacity projections.

- [ ] **Step 4: Implement complete group-minute matrix generation and idempotent writes**

Add focused helpers:

```python
def _minute_buckets(start_at: datetime, end_at: datetime) -> list[datetime]:
    """Return every UTC minute start in [start_at, end_at)."""

def _sample_document(
    *,
    site_id: str,
    group_id: int,
    bucket_at: datetime,
    sampled_at: datetime,
    usage: dict[str, Any],
    current_concurrency: float | None,
) -> dict[str, Any]:
    """Map one exact PostgreSQL row or zero-fill into a v3 sample document."""

async def _write_minute_samples(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_ids: list[int],
    start_at: datetime,
    end_at: datetime,
    usage_by_key: dict[tuple[int, datetime], dict[str, Any]],
    sampled_at: datetime,
    latest_bucket_at: datetime | None,
    concurrency_by_group: dict[int, float | None] | None,
) -> int:
    """Bulk-upsert the complete group-minute matrix and return operation count."""
```

`_write_minute_samples` must create one `pymongo.UpdateOne` per `group_id × minute` with filter `_id`, `$set` for exact usage fields, and `$setOnInsert: {"current_concurrency": None}`. Only when `bucket_at == latest_bucket_at` may `$set` include `current_concurrency`; historical writes must leave an existing concurrency value untouched. Use `bulk_write(operations, ordered=False)` and do not write anything for an empty matrix.

- [ ] **Step 5: Implement persisted backfill state helpers**

Add:

```python
async def _load_backfill_window(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    closed_end: datetime,
    recent_start: datetime,
) -> tuple[datetime, datetime]:
    """Return the clamped historical [start, end) window for this site."""

async def _advance_backfill_cursor(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    window_start: datetime,
    window_end: datetime,
    next_window_end: datetime,
    completed_at: datetime,
) -> None:
    """Persist the next historical window only after a successful write."""
```

Use `_id=site_id`. The initial `next_window_end` is `recent_start`. Clamp stale state to `[closed_end - 7 days, recent_start]`; when the next historical window would cross the seven-day boundary, reset it to `recent_start`. Only call `_advance_backfill_cursor` after the historical write returns successfully. Store `last_window_start`, `last_window_end`, `last_completed_at`, and `updated_at` in UTC.

- [ ] **Step 6: Rework `sample_site_tpm` to run recent calibration and one historical hour**

Keep the existing site validation, SQL DSN check, group lookup, per-site lock, account fetch, runtime cache enrichment, and group concurrency calculation. The new sequence is:

1. Compute `closed_end=floor(now_utc(), minute)`, `recent_start=closed_end-20 minutes`, and `latest_bucket=closed_end-1 minute`.
2. Load the historical window.
3. Call `fetch_group_minute_usage` for the recent range.
4. Fetch remote accounts best-effort and compute concurrency by group; failures set latest concurrency to `None` but do not discard usage.
5. Write the complete recent matrix.
6. Call `fetch_group_minute_usage` for the historical one-hour range.
7. Write the historical matrix without changing existing concurrency.
8. Advance the cursor.

Return `ok=False` only for site validation, missing SQL DSN, recent query failure, or recent write failure. If historical work fails after recent work succeeds, return `ok=True` with `historical_ok=False` and log the stage and exception type; the cursor must remain unchanged.

- [ ] **Step 7: Add failing cursor tests and run them**

Add tests for: no cursor starts before the recent window; successful historical write advances one hour; historical failure does not advance; reaching the seven-day boundary resets to `recent_start`; and a stale cursor is clamped. Run:

```text
backend\\.venv\\Scripts\\python.exe -m unittest tests.test_tpm_sampler.TpmBackfillTests
```

Expected: failures until the state helpers and site flow are implemented.

- [ ] **Step 8: Run all sampler tests and refactor only after green**

Run:

```text
backend\\.venv\\Scripts\\python.exe -m unittest tests.test_tpm_sampler
```

Update old counter-delta tests to assert the v3 exact-minute contract rather than retaining tests for deleted behavior. Expected: all sampler tests pass with no warnings.

### Task 3: Switch capacity reads and bootstrap indexes to v3

**Files:**
- Modify: `backend/app/modules/sub2api/cache.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Test: `backend/tests/test_capacity_risk_integration.py`
- Test: `backend/tests/test_tpm_sampler.py`

- [ ] **Step 1: Add failing read-version assertions**

Change the existing `_load_group_tpm_samples` test fixture to use a version-3 exact-minute document and assert its Mongo filter includes both `schema_version=3` and `counter_source="postgresql_usage_logs_minute"`. Add an assertion that a legacy v2 filter is not used.

- [ ] **Step 2: Update `_load_group_tpm_samples`**

Change only the version/source filter and retain the existing six-hour cutoff, sort, limit, and capacity projection fields. Include `minute_account_cost` in the projection while retaining `account_cost_per_minute`, `account_cost_delta`, and `total_account_cost` compatibility fields.

- [ ] **Step 3: Add the backfill state index in bootstrap**

Extend `ensure_tpm_indexes` with:

```python
await db.sub2api_tpm_backfill_state.create_index("updated_at")
```

Keep the existing unique minute bucket, TTL, and sampled-at indexes. Add an assertion to the existing bootstrap test that the state collection receives the index.

- [ ] **Step 4: Run capacity integration and index tests**

Run:

```text
backend\\.venv\\Scripts\\python.exe -m unittest tests.test_capacity_risk_integration tests.test_tpm_sampler.TpmIndexTests
```

Expected: all pass and the capacity code still sees minute costs and concurrency samples.

### Task 4: Full verification and implementation commit

**Files:**
- Modify: `backend/app/modules/sub2api/dashboard_postgres_repository.py`
- Modify: `backend/app/modules/sub2api/tpm_sampler.py`
- Modify: `backend/app/modules/sub2api/cache.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Test: `backend/tests/test_sub2api_dashboard_postgres_repository.py`
- Test: `backend/tests/test_tpm_sampler.py`
- Test: `backend/tests/test_capacity_risk_integration.py`

- [ ] **Step 1: Run all backend tests**

Run:

```text
backend\\.venv\\Scripts\\python.exe -m unittest discover -s tests
```

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 2: Run static syntax and diff checks**

Run:

```text
backend\\.venv\\Scripts\\python.exe -m compileall -q app tests
git diff --check
```

Expected: both commands exit successfully.

- [ ] **Step 3: Review the final diff for contract safety**

Confirm that no SQL DSN, API key, account credential, or full usage payload is logged; the status page reads only v3 samples; a historical failure never advances its cursor; and no front-end files changed.

- [ ] **Step 4: Commit the implementation**

```text
git add backend/app/modules/sub2api/dashboard_postgres_repository.py backend/app/modules/sub2api/tpm_sampler.py backend/app/modules/sub2api/cache.py backend/app/modules/system/bootstrap.py backend/tests/test_sub2api_dashboard_postgres_repository.py backend/tests/test_tpm_sampler.py backend/tests/test_capacity_risk_integration.py
git commit -m "feat: sample exact group minute usage"
```
