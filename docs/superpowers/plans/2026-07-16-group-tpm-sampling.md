# Group TPM Sampling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record one lightweight TPM sample per active sub2api group every minute.

**Architecture:** Add a dedicated `tpm_sampler` module that fetches only dashboard stats for each cached `group_id`, normalizes the response into an idempotent minute bucket, and stores it with 14-day retention. The sampler runs independently from account usage synchronization and uses per-site overlap protection plus per-group error isolation.

**Tech Stack:** Python 3.14, asyncio, FastAPI lifespan, Motor/MongoDB, unittest/AsyncMock

---

### Task 1: TPM sample calculation and storage

**Files:**
- Create: `backend/app/modules/sub2api/tpm_sampler.py`
- Create: `backend/tests/test_tpm_sampler.py`

- [ ] **Step 1: Write failing tests for reported TPM and request parameters**

Create a test that calls `sample_group_tpm` with a fixed UTC time and an `AsyncMock` client returning `stats.tpm`, `rpm`, cumulative token fields, and `stats_updated_at`. Assert that the request includes only the target `group_id`, `include_stats=True`, and all heavy response flags disabled. Assert that the replacement document uses the reported TPM and the expected UTC minute `_id`.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_tpm_sampler.py -v`

Expected: import failure because `app.modules.sub2api.tpm_sampler` does not exist.

- [ ] **Step 3: Implement the minimal sample module**

Add:

```python
async def sample_group_tpm(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    client: Sub2ApiClient,
    sampled_at: datetime | None = None,
) -> dict[str, Any]:
```

The implementation must:

- normalize `sampled_at` to UTC and truncate it to a minute bucket;
- use the Shanghai local date for `start_date` and `end_date`;
- request `granularity="hour"`, `include_stats=True`, `include_trend=False`, `include_model_stats=False`, `include_group_stats=False`, and `include_users_trend=False`;
- map `stats.total_input_tokens`, `total_output_tokens`, `total_cache_creation_tokens`, and `total_cache_read_tokens` to stored token fields;
- prefer a non-negative numeric `stats.tpm` as the final TPM;
- write with `replace_one({"_id": sample_id}, document, upsert=True)`;
- set `expires_at` to 14 days after sampling.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the command from Step 2 and expect one passing test.

- [ ] **Step 5: Write failing tests for calculated TPM and counter resets**

Add tests where reported TPM is absent. The first test supplies an older sample with `total_tokens=1_000`, a current total of `1_600`, and a 120-second elapsed interval; expect `calculated_tpm=300`. The reset test supplies a lower current total and expects `tpm=None`, `token_delta=None`, and `source="unavailable"`.

- [ ] **Step 6: Run the new tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_tpm_sampler.py -v`

Expected: fallback assertions fail because delta calculation is not implemented.

- [ ] **Step 7: Implement elapsed-time fallback**

Query only the preceding minute bucket for the same `site_id` and `group_id`:

```python
previous = await db.sub2api_tpm_samples.find_one(
    {"site_id": site_id, "group_id": group_id, "bucket_at": {"$lt": bucket_at}},
    sort=[("bucket_at", -1)],
)
```

Calculate `token_delta / (elapsed_seconds / 60)` only when both totals exist, the delta is non-negative, and elapsed time is positive. Keep reported TPM as the preferred final value while storing both values for auditability.

- [ ] **Step 8: Run all TPM sample tests and verify GREEN**

Run the command from Step 6 and expect all sample tests to pass.

### Task 2: Per-group orchestration and overlap protection

**Files:**
- Modify: `backend/app/modules/sub2api/tpm_sampler.py`
- Modify: `backend/tests/test_tpm_sampler.py`

- [ ] **Step 1: Write failing tests for concurrent group sampling and failure isolation**

Create a fake async cursor containing group IDs `3`, `5`, and `7`. Patch `sample_group_tpm` so all calls must start before any can finish, and make group `5` raise an exception. Assert all groups were invoked, two succeeded, one failed, and the site result remains successful.

- [ ] **Step 2: Run the orchestration test and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_tpm_sampler.py -v`

Expected: failure because site orchestration is missing.

- [ ] **Step 3: Implement site and all-site sampling**

Add `sample_site_tpm` and `sample_all_sites_tpm`. `sample_site_tpm` reads distinct integer group IDs from `sub2api_groups_cache`, runs all group samples through `asyncio.gather`, and summarizes sampled/failed counts. `sample_all_sites_tpm` reads only active sites and samples sites concurrently.

- [ ] **Step 4: Run the orchestration test and verify GREEN**

Run the command from Step 2 and expect it to pass.

- [ ] **Step 5: Write a failing overlap test**

Start two concurrent `sample_site_tpm` calls for the same site while the first is blocked. Assert the second returns `status="skipped"` and does not start another group request.

- [ ] **Step 6: Run the overlap test and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_tpm_sampler.py -v`

Expected: duplicate calls occur because locking is missing.

- [ ] **Step 7: Add per-site locks**

Maintain an in-process `dict[str, asyncio.Lock]`. Return a skipped result when the lock is already held; otherwise hold it for the entire site sampling round.

- [ ] **Step 8: Run all TPM sampler tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_tpm_sampler.py -v`

Expected: all TPM tests pass.

### Task 3: Scheduler and application lifecycle

**Files:**
- Modify: `backend/app/modules/sub2api/tpm_sampler.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_tpm_sampler.py`

- [ ] **Step 1: Write a failing scheduler cancellation test**

Patch `sample_all_sites_tpm` and `asyncio.sleep`, run one scheduler iteration, then cancel the task. Assert cancellation propagates and the sampler was invoked.

- [ ] **Step 2: Run the scheduler test and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_tpm_sampler.py -v`

Expected: failure because `tpm_sampler_loop` does not exist.

- [ ] **Step 3: Implement the 60-second loop**

Add `tpm_sampler_loop(db)` that samples immediately, catches ordinary exceptions with `logger.exception`, propagates `CancelledError`, and sleeps only the remainder of the 60-second period based on `time.monotonic()`.

- [ ] **Step 4: Run scheduler tests and verify GREEN**

Run the command from Step 2 and expect it to pass.

- [ ] **Step 5: Register the lifecycle task**

Import `tpm_sampler_loop` in `backend/app/main.py`, create the task during startup, add it to the shutdown cancellation tuple, and do not couple it to dashboard or account-cache startup tasks.

- [ ] **Step 6: Run all TPM tests after lifecycle integration**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_tpm_sampler.py -v`

Expected: all TPM tests pass and the application imports successfully.

### Task 4: MongoDB indexes and regression verification

**Files:**
- Modify: `backend/app/modules/system/bootstrap.py`
- Test: `backend/tests/test_tpm_sampler.py`

- [ ] **Step 1: Add TPM indexes**

Extend `ensure_indexes` with:

```python
await db.sub2api_tpm_samples.create_index(
    [("site_id", 1), ("group_id", 1), ("bucket_at", 1)],
    unique=True,
)
await db.sub2api_tpm_samples.create_index("expires_at", expireAfterSeconds=0)
await db.sub2api_tpm_samples.create_index(
    [("site_id", 1), ("group_id", 1), ("sampled_at", -1)]
)
```

- [ ] **Step 2: Run the full backend test suite**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all existing and new tests pass.

- [ ] **Step 3: Run static import and diff checks**

Run: `.\.venv\Scripts\python.exe -m compileall app tests`

Expected: exit code 0.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 4: Review scope**

Confirm the diff contains only the TPM sampler, its tests, lifecycle registration, TPM indexes, and plan documentation in addition to the pre-existing capacity-notification changes. Do not modify frontend behavior for this task.
