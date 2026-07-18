# Client Minute Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record one site-wide RPM/TPM result per minute for every active NewAPI and Sub2API customer site, expose query and status APIs, and show the latest sampler state on the customer-site page.

**Architecture:** A new `app.modules.client_metrics` package owns a protocol-neutral adapter result, NewAPI and Sub2API HTTP adapters, MongoDB persistence/query logic, and a wall-clock-aligned sampler. It reads only `client_sites`, writes only `client_minute_metrics` and `client_metric_sampler_state`, and leaves account-pool sampling unchanged.

**Tech Stack:** Python 3.12, FastAPI, HTTPX, Motor/MongoDB, unittest, React 19, TypeScript, Vitest, Vite.

---

## File Structure

- Create `backend/app/modules/client_metrics/__init__.py`: package boundary.
- Create `backend/app/modules/client_metrics/models.py`: UTC minute helpers, quality constants, adapter result model, document construction, safe errors.
- Create `backend/app/modules/client_metrics/adapters/base.py`: adapter protocol.
- Create `backend/app/modules/client_metrics/adapters/newapi.py`: NewAPI `/api/log/stat` client and parser.
- Create `backend/app/modules/client_metrics/adapters/sub2api.py`: site-wide snapshot client, cursor parsing, and counter deltas.
- Create `backend/app/modules/client_metrics/sampler.py`: active-site enumeration, adapter dispatch, MongoDB writes, locks, parallelism, and minute loop.
- Create `backend/app/modules/client_metrics/queries.py`: range queries, gap/completeness calculation, and status reads.
- Create `backend/app/routers/client_metrics.py`: metrics, status, and manual sample endpoints.
- Modify `backend/app/modules/system/bootstrap.py`: metric and TTL indexes.
- Modify `backend/app/main.py`: register router and one client metric sampler task.
- Create `backend/tests/test_client_metric_adapters.py`: adapter contracts and edge cases.
- Create `backend/tests/test_client_metric_sampler.py`: persistence, isolation, missing data, scheduling, and status.
- Create `backend/tests/test_client_metric_queries.py`: range/gap/completeness behavior.
- Create `backend/tests/test_client_metric_routes.py`: permissions and HTTP error mapping.
- Modify `frontend/src/pages/ClientSitesPage.tsx`: latest metric state and manual sample control.
- Create `frontend/src/pages/clientMetricStatus.ts`: pure display-state conversion.
- Create `frontend/src/pages/clientMetricStatus.test.ts`: display semantics for complete and missing samples.
- Modify `frontend/styles.css`: compact responsive sampling status layout.

### Task 1: Adapter contracts and NewAPI adapter

**Files:**
- Create: `backend/app/modules/client_metrics/models.py`
- Create: `backend/app/modules/client_metrics/adapters/base.py`
- Create: `backend/app/modules/client_metrics/adapters/newapi.py`
- Test: `backend/tests/test_client_metric_adapters.py`

- [ ] **Step 1: Write failing NewAPI adapter tests**

Test `NewApiMetricAdapter.sample()` with `httpx.MockTransport`. Assert that it calls `/api/log/stat`, sends `Authorization: Bearer ...` and `New-Api-User`, and returns an `AdapterSample` with the upstream non-negative RPM/TPM. Add separate tests asserting `quality="missing"` for `success=false` and invalid metrics.

- [ ] **Step 2: Run the adapter test and verify RED**

Run: `python -m uv --directory backend run python -m unittest backend.tests.test_client_metric_adapters -v`

Expected: import failure for `app.modules.client_metrics`.

- [ ] **Step 3: Implement the shared result and NewAPI adapter**

Define:

```python
@dataclass(slots=True)
class AdapterSample:
    rpm: float | None
    tpm: float | None
    quality: str
    source: str
    source_updated_at: datetime | None = None
    total_requests: int | None = None
    total_tokens: int | None = None
    elapsed_seconds: float | None = None
    error_code: str | None = None
    cursor: dict[str, Any] = field(default_factory=dict)
```

The adapter accepts an injectable `httpx.AsyncClient`, validates the response envelope and numbers, and returns missing results instead of exposing remote response bodies.

- [ ] **Step 4: Run the adapter tests and verify GREEN**

Run the Task 1 unittest command and expect all NewAPI tests to pass.

### Task 2: Sub2API adapter and cursor semantics

**Files:**
- Modify: `backend/app/modules/client_metrics/adapters/sub2api.py`
- Modify: `backend/tests/test_client_metric_adapters.py`

- [ ] **Step 1: Write failing Sub2API tests**

Cover first cursor (`missing`), same-hour delta, hour rollover, unchanged upstream (`delayed`), valid zero traffic, and unexplained counter rollback (`counter_reset`). Assert the request does not contain `group_id`.

- [ ] **Step 2: Run targeted tests and verify RED**

Run the Sub2API adapter test class and expect missing module/function failures.

- [ ] **Step 3: Implement site-wide Sub2API sampling**

Use `/api/v1/admin/dashboard/snapshot-v2` with `granularity=hour`, `include_stats=true`, `include_trend=true`, and no group. Parse the current remote hour bucket, compare it with the persisted cursor, and return the exact quality semantics from the design.

- [ ] **Step 4: Run all adapter tests and verify GREEN**

Run: `python -m uv --directory backend run python -m unittest backend.tests.test_client_metric_adapters -v`

### Task 3: Sampler persistence, state, and indexes

**Files:**
- Create: `backend/app/modules/client_metrics/sampler.py`
- Modify: `backend/app/modules/client_metrics/models.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Test: `backend/tests/test_client_metric_sampler.py`

- [ ] **Step 1: Write failing sampler tests**

Use async fake collections to assert active API-configured sites are sampled, disabled/unconfigured sites are skipped, deterministic minute IDs are replaced, failed adapters write `rpm=None`/`tpm=None`, retention controls `expires_at`, cursor state persists, one site failure does not stop another, and no `sub2api_sites` collection is accessed.

- [ ] **Step 2: Run sampler tests and verify RED**

Run: `python -m uv --directory backend run python -m unittest backend.tests.test_client_metric_sampler -v`

- [ ] **Step 3: Implement persistence and scheduler**

Implement `sample_client_site()`, `sample_all_client_sites()`, `seconds_until_next_sample()`, and `client_metric_sampler_loop()`. Align to the next wall-clock minute plus five seconds, cap site concurrency with a semaphore, use per-site locks, upsert metric/state documents, and sanitize errors.

- [ ] **Step 4: Add indexes**

Create unique `(site_id, bucket_at)`, descending range, quality, state update, and `expires_at` TTL indexes in bootstrap.

- [ ] **Step 5: Run sampler and bootstrap tests and verify GREEN**

Run sampler tests plus existing bootstrap-related backend tests.

### Task 4: Query and manual-sample APIs

**Files:**
- Create: `backend/app/modules/client_metrics/queries.py`
- Create: `backend/app/routers/client_metrics.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_client_metric_queries.py`
- Test: `backend/tests/test_client_metric_routes.py`

- [ ] **Step 1: Write failing query tests**

Assert UTC range validation, sorted results, explicit null values, empty-document minutes, absent gap minutes, and `completeness_ratio = complete_minutes / total_minutes`.

- [ ] **Step 2: Write failing route tests**

Assert missing sites return 404, bad ranges return 400, status reads return a stable empty state, manual sampling calls the unified sampler, and manual errors do not expose credentials.

- [ ] **Step 3: Run query/route tests and verify RED**

Run both new test modules and expect import failures.

- [ ] **Step 4: Implement queries and routes**

Add the three approved endpoints under `/api/client-sites/{site_id}/metrics`. Register the router and sampler loop in `main.py`, cancel it during shutdown, and keep reads available to viewer while writes require maintainer or above.

- [ ] **Step 5: Run query/route tests and verify GREEN**

Run both new test modules, then the full backend suite.

### Task 5: Customer-site sampling status UI

**Files:**
- Create: `frontend/src/pages/clientMetricStatus.ts`
- Create: `frontend/src/pages/clientMetricStatus.test.ts`
- Modify: `frontend/src/pages/ClientSitesPage.tsx`
- Modify: `frontend/styles.css`

- [ ] **Step 1: Write failing display-state tests**

Assert complete samples format RPM/TPM, missing/delayed samples display `无数据`, zero remains `0`, and consecutive failures produce the correct error tone.

- [ ] **Step 2: Run frontend test and verify RED**

Run: `npm.cmd --prefix frontend test -- clientMetricStatus.test.ts`

- [ ] **Step 3: Implement status conversion and UI**

Load the selected site's status, render latest time/quality/RPM/TPM/failure count, add a `立即采样` button with toast feedback, refresh status after site selection and manual sampling, and use compact responsive CSS.

- [ ] **Step 4: Run frontend tests and build**

Run: `npm.cmd --prefix frontend test`

Run: `npm.cmd --prefix frontend run build`

Expected: all tests and production build pass.

### Task 6: Final verification

**Files:**
- Verify all files above.

- [ ] **Step 1: Run full backend tests**

Run: `python -m uv --directory backend run python -m unittest discover -s backend/tests -v`

- [ ] **Step 2: Run full frontend tests and build**

Run: `npm.cmd --prefix frontend test`

Run: `npm.cmd --prefix frontend run build`

- [ ] **Step 3: Run static checks**

Run: `python -m uv --directory backend run python -m compileall backend/app`

Run: `git diff --check`

- [ ] **Step 4: Review security and isolation**

Search the diff for `api_key`, `sql_dsn`, `sub2api_sites`, and raw response persistence. Confirm secrets only enter adapter request construction, client samples only read `client_sites`, and account-pool collections are unchanged.

- [ ] **Step 5: Commit the implementation**

Stage only the implementation, tests, styles, and this plan; commit with `Implement client minute RPM TPM sampling`.
