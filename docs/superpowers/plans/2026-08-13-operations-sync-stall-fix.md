# Operations Sync Stall Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent operations synchronization from accumulating unfinished runs, recover stale state automatically, and keep AIGCLink usage billing independent from balance conversion rates.

**Architecture:** Enforce one active operations run per site with a PostgreSQL partial unique index and a transaction-scoped advisory lock that serializes expiration and insertion. Bound each site sync with an application timeout, include successful run finalization in the protected lifecycle, and downgrade stale UI state based on the run start time. Preserve AIGCLink source-priced usage without querying or applying Growth balance conversion rates.

**Tech Stack:** FastAPI, asyncio, SQLAlchemy async SQL, PostgreSQL, Python unittest, GitHub Actions.

---

### Task 1: Database single-flight invariant

**Files:**
- Modify: `backend/app/modules/growth/migrations.py`
- Modify: `backend/app/modules/operations/repository.py`
- Test: `backend/tests/test_growth_migrations.py`
- Test: `backend/tests/test_operations_repository.py`

- [x] Add failing tests for an idempotent migration that closes legacy operations `running` rows and creates a partial unique index on active runs per site.
- [x] Add a failing repository test requiring `start_operations_sync_run` to expire timed-out rows and use the partial index conflict target before returning a new run.
- [x] Implement migration `0006_operations_sync_single_flight` and the transaction-serialized start sequence.
- [x] Run `python -m uv run python -m unittest tests.test_growth_migrations tests.test_operations_repository -q` and confirm it passes.

### Task 2: Bounded and fully finalized sync lifecycle

**Files:**
- Modify: `backend/app/modules/operations/sync.py`
- Test: `backend/tests/test_operations_sync.py`

- [x] Add failing tests proving a duplicate run skips source access, a timed-out run is finalized as failed, and a successful-finalizer error is also finalized as failed.
- [x] Move the success finalizer inside the guarded lifecycle, add a 600-second timeout, and preserve the original exception when fallback finalization also fails.
- [x] Propagate `skipped` status from a site result through the scheduled cycle.
- [x] Run `python -m uv run python -m unittest tests.test_operations_sync -q` and confirm it passes.

### Task 3: Accurate health and AIGCLink source pricing

**Files:**
- Modify: `backend/app/modules/operations/domain.py`
- Modify: `backend/app/modules/operations/service.py`
- Modify: `backend/app/modules/operations/sync.py`
- Test: `backend/tests/test_operations_domain.py`
- Test: `backend/tests/test_operations_sync.py`

- [x] Add failing tests proving an old `running` row is delayed while a fresh row is running, and AIGCLink sync does not load or apply conversion rates.
- [x] Pass `started_at` into `sync_health` and treat runs older than 15 minutes as delayed.
- [x] Apply balance conversion only to non-AIGCLink usage facts.
- [x] Run focused domain and sync tests and confirm they pass.

### Task 4: Verification and pull request

**Files:**
- Review all files changed above.

- [x] Run the complete backend test suite, frontend test suite, production build, Python compile check, and `git diff --check`.
- [x] Inspect the final diff for unrelated changes and credential exposure.
- [ ] Commit only the sync fix, push `codex/fix-operations-sync-stall`, and open a draft pull request targeting `main`.
