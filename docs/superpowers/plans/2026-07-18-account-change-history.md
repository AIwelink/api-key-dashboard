# Account Change History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-account periodic probe snapshots with site-level field-change batches and daily dynamic-state checkpoints while preserving exact probe timestamps.

**Architecture:** `remote_account_identities` remains the current-state materialization. A focused `account_history.py` module normalizes dynamic state, computes new-value deltas, batches changes, advances confirmed baselines, creates daily checkpoints, and reconstructs history. The probe writes no legacy samples; the account detail API reads both legacy samples and new changes during TTL migration.

**Tech Stack:** Python 3.14, Motor/PyMongo, MongoDB BSON, FastAPI, React/TypeScript, unittest, Vitest.

---

### Task 1: Pure change-history model

**Files:**
- Create: `backend/app/modules/sub2api/account_history.py`
- Create: `backend/tests/test_account_history.py`

- [ ] **Step 1: Write failing tests for new-value deltas and deterministic IDs**

Tests call:

```python
change = build_history_change(
    identity_id="api-5001:user@example.com",
    remote_account_id=953,
    previous={"usage": {"codex_5h_used_percent": 40, "removed": 1}, "subscription": {}},
    current={"usage": {"codex_5h_used_percent": 42}, "subscription": {}},
)
```

Assert that `changes` contains only `usage.codex_5h_used_percent: 42`, `unset` contains `usage.removed`, and repeated calls produce the same `event_id`.

- [ ] **Step 2: Run the focused test and confirm the missing module failure**

Run: `cd backend && .\.venv\Scripts\python.exe -m unittest tests.test_account_history -v`

Expected: FAIL because `app.modules.sub2api.account_history` does not exist.

- [ ] **Step 3: Implement dynamic snapshots and field-level diffs**

Create constants `CHANGE_RETENTION_DAYS = 30`, `CHECKPOINT_RETENTION_DAYS = 365`, `MAX_BATCH_ENTRIES = 500`, and `MAX_BATCH_BSON_BYTES = 8 * 1024 * 1024`.

Implement:

```python
def dynamic_snapshot(account: dict[str, Any]) -> dict[str, dict[str, Any]]
def snapshot_hash(snapshot: dict[str, Any]) -> str
def build_history_change(*, identity_id: str, remote_account_id: Any, previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any] | None
def public_change_entry(change: dict[str, Any]) -> dict[str, Any]
```

Use canonical JSON with sorted keys and UTC datetime ISO strings for hashes. Store internal `_new_state` only in memory and remove it from persisted entries.

- [ ] **Step 4: Add failing tests for chunk limits and reconstruction**

Assert `chunk_history_changes` splits after 500 entries and before 8 MiB. Assert `apply_history_entries` applies `unset` before `changes` and ignores duplicate `event_id` values.

- [ ] **Step 5: Implement chunking and reconstruction**

Implement:

```python
def chunk_history_changes(changes: list[dict[str, Any]], *, site_id: str, run_id: str, observed_at: datetime) -> list[dict[str, Any]]
def apply_history_entries(base: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]
```

Use `bson.BSON.encode` to enforce the 8 MiB target.

- [ ] **Step 6: Run focused tests**

Run: `cd backend && .\.venv\Scripts\python.exe -m unittest tests.test_account_history -v`

Expected: PASS.

### Task 2: Probe integration and baseline reliability

**Files:**
- Modify: `backend/app/modules/sub2api/account_probe.py`
- Modify: `backend/app/modules/sub2api/account_history.py`
- Modify: `backend/tests/test_account_probe.py`
- Modify: `backend/tests/test_account_history.py`

- [ ] **Step 1: Replace the hourly sample test with change-only probe tests**

Test that an existing identity with equal `history_baseline_snapshot` produces no change. Test that first observation initializes a baseline without producing an initialization event. Test that a usage reset from 80 to 0 stores the new zero value.

- [ ] **Step 2: Run tests and verify they fail against the hourly sample writer**

Run: `cd backend && .\.venv\Scripts\python.exe -m unittest tests.test_account_probe tests.test_account_history -v`

Expected: FAIL because the probe still writes `remote_account_probe_samples`.

- [ ] **Step 3: Normalize subscription state**

Extend `_normalize_probe_account` with `subscription_snapshot`, extracted from account, credentials, and extra. Normalize subscription and credential timestamps through the existing datetime parser so equivalent timestamp strings compare equal.

- [ ] **Step 4: Collect history changes before updating identities**

In `_run_site_account_probe`, compare each collapsed account against `history_baseline_snapshot`. Respect `detailed_enabled` and `record_usage_samples`. Accumulate at most one change per identity per probe and remove the second loop that builds `sample_ops`.

- [ ] **Step 5: Write batches and conditionally advance baselines**

Implement async functions:

```python
async def write_history_batches(db, *, site_id: str, run_id: str, observed_at: datetime, changes: list[dict[str, Any]]) -> dict[str, Any]
async def advance_history_baselines(db, *, changes: list[dict[str, Any]], observed_at: datetime) -> int
```

Upsert deterministic batch IDs. Only advance a baseline after all chunks write successfully, using the previous hash in each identity update query.

- [ ] **Step 6: Persist current subscription and initialize missing baselines**

Add `name`, `current_subscription_snapshot`, `history_baseline_snapshot`, and `history_baseline_hash` to identity updates. For identities without a baseline, initialize it to current dynamic state and emit no change.

- [ ] **Step 7: Add probe run counters**

Store `history_changed_accounts`, `history_change_fields`, and `history_batches` in `remote_account_probe_runs` and the returned probe summary.

- [ ] **Step 8: Run focused tests**

Run: `cd backend && .\.venv\Scripts\python.exe -m unittest tests.test_account_probe tests.test_account_history -v`

Expected: PASS.

### Task 3: Daily checkpoints, indexes, and history reads

**Files:**
- Modify: `backend/app/modules/sub2api/account_history.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Modify: `backend/app/modules/events/records.py`
- Modify: `backend/tests/test_account_history.py`
- Create: `backend/tests/test_event_history.py`

- [ ] **Step 1: Write failing checkpoint tests**

Test that `ensure_daily_checkpoint` writes deterministic `site:local-date:chunk` documents and a manifest, stores only dynamic state, skips a complete day, and sets 365-day TTL.

- [ ] **Step 2: Implement daily checkpoint creation**

Use `Asia/Shanghai` for `local_date`. Query present identities, build checkpoint entries from current usage, subscription, and cumulative usage, chunk at 500 entries/8 MiB, replace deterministic chunks, then write a `site:date:manifest` document last.

- [ ] **Step 3: Add indexes**

Create TTL and site/time indexes for `remote_account_change_batches` and `remote_account_daily_checkpoints`. Keep legacy sample indexes until TTL migration completes.

- [ ] **Step 4: Write failing account-detail compatibility test**

Assert `get_event_account_detail` returns both `samples` and expanded, event-ID-deduplicated `changes` sorted newest first.

- [ ] **Step 5: Implement change reads**

Query change batches by `site_id` and `entries.identity_id`, filter the target identity, attach `observed_at` and batch ID, deduplicate by `event_id`, and return at most 120 changes.

- [ ] **Step 6: Run focused tests**

Run: `cd backend && .\.venv\Scripts\python.exe -m unittest tests.test_account_history tests.test_event_history -v`

Expected: PASS.

### Task 4: Configuration and account-detail UI

**Files:**
- Modify: `frontend/src/pages/AccountPoolsPage.tsx`
- Modify: `frontend/src/pages/EventRecordsPage.tsx`
- Modify: `frontend/src/pages/AccountPoolsPage.test.ts`
- Create: `frontend/src/pages/EventRecordsPage.test.ts`

- [ ] **Step 1: Add failing UI helper tests**

Test change-field labels, value formatting, and account detail typing for `changes`.

- [ ] **Step 2: Remove the per-group retention input**

Remove the `sample_retention_days` table control because site change batches use fixed 30-day TTL. Rename the `record_usage_samples` label to `记录动态变化` while preserving the backend property for compatibility.

- [ ] **Step 3: Render dynamic changes in account detail**

Add a compact `动态变化` section before raw JSON. Each item shows time and changed field/value pairs; `unset` fields display `已移除`. Do not duplicate identity static fields.

- [ ] **Step 4: Run frontend tests and production build**

Run: `cd frontend && npm.cmd test && npm.cmd run build`

Expected: 13 or more tests pass and Vite build exits 0.

### Task 5: Storage estimator and end-to-end verification

**Files:**
- Create: `backend/scripts/estimate_account_history_storage.py`
- Modify: `backend/app/modules/system/storage_audit.py`
- Modify: `backend/tests/test_storage_audit.py`

- [ ] **Step 1: Write failing estimator summary test**

Given old BSON bytes, new batch bytes, elapsed hours, and checkpoint bytes, assert the report calculates document reduction, byte reduction, and projected 30-day logical size.

- [ ] **Step 2: Implement read-only production estimator**

Read a configurable recent window of legacy samples with a narrow projection, group them by probe run, replay field changes through `build_history_change`, BSON-encode the resulting batches, and report old/new documents and bytes. Read identities to include daily checkpoint bytes. Do not write to MongoDB.

- [ ] **Step 3: Extend the storage audit**

Report counts and logical sizes for change batches and daily checkpoints, legacy sample count, and latest schema state.

- [ ] **Step 4: Run complete verification**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
cd ..\frontend
npm.cmd test
npm.cmd run build
cd ..
git diff --check
```

Expected: all tests pass, build exits 0, and diff check is clean.

- [ ] **Step 5: Run the read-only estimator against production**

Run: `cd backend && .\.venv\Scripts\python.exe scripts\estimate_account_history_storage.py --hours 6`

Expected output includes observed old/new document counts, BSON bytes, percentage saved, and projected 30-day storage. No collection write operations are permitted.
