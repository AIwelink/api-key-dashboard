# Legacy Account History Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert every bounded legacy account probe snapshot into deterministic field-change batches and daily dynamic checkpoints, verify exact final states, then delete source snapshots in resumable batches of 2,000.

**Architecture:** A pure replay module groups chronological legacy samples by site and probe run, emits initialization and subsequent dynamic changes, and builds first-run daily checkpoints. An async migration service persists deterministic targets and a migration ledger, reconstructs all migrated identities for verification, enforces a source-idle safety gate, and only then deletes bounded source documents. A CLI exposes inspect, convert, verify, and explicitly destructive delete behavior.

**Tech Stack:** Python 3.14, Motor/PyMongo, MongoDB BSON, unittest.

---

### Task 1: Pure legacy replay model

**Files:**
- Create: `backend/app/modules/sub2api/account_history_migration.py`
- Create: `backend/tests/test_account_history_migration.py`

- [ ] **Step 1: Write failing initialization and delta tests**

Create samples for one identity at 40%, unchanged 40%, then 42%. Assert the replay emits an initialization change from `{}` and one later change containing only `usage.codex_5h_used_percent: 42`.

- [ ] **Step 2: Run the focused test**

Run: `cd backend && .\.venv\Scripts\python.exe -m unittest tests.test_account_history_migration -v`

Expected: FAIL because `account_history_migration` does not exist.

- [ ] **Step 3: Implement run replay state**

Implement `LegacyReplayState.consume_run(site_id, run_id, observed_at, samples)` using `build_history_change` and `chunk_history_changes`. Keep `dynamic_states`, `cumulative_states`, final state hashes, source document count, changed fields, and generated documents in the replay result.

- [ ] **Step 4: Write failing Shanghai checkpoint tests**

Assert the first complete run of each Shanghai date creates deterministic checkpoint chunks and a manifest containing dynamic and cumulative values but no email, name, status, or error fields.

- [ ] **Step 5: Implement migrated checkpoints**

Use `build_daily_checkpoint_documents`. Add `migration_id`, deterministic manifest metadata, original `checkpoint_at`, and the 365-day TTL. Do not overwrite an existing complete manifest unless it belongs to the same migration.

- [ ] **Step 6: Run focused tests**

Run: `cd backend && .\.venv\Scripts\python.exe -m unittest tests.test_account_history_migration -v`

Expected: PASS.

### Task 2: Persistence ledger and verification

**Files:**
- Modify: `backend/app/modules/sub2api/account_history_migration.py`
- Modify: `backend/tests/test_account_history_migration.py`
- Modify: `backend/app/modules/system/bootstrap.py`

- [ ] **Step 1: Write failing persistence and resume tests**

Use fake async collections to assert deterministic upserts, ledger stages (`converting`, `converted`, `verified`, `deleting`, `completed`), and rerunning conversion without duplicate target documents.

- [ ] **Step 2: Implement bounded conversion**

Implement `convert_legacy_account_history(db, migration_id, source_max_sampled_at, site_id=None)` with a projection containing only migration fields. Query each site using `{site_id, sampled_at <= boundary}`, sort by `sampled_at, _id`, group contiguous runs, persist run batches and first-run daily checkpoints, and record counters after each run.

- [ ] **Step 3: Write failing full-state verification tests**

Assert verification succeeds when reconstructed per-identity hashes match source final hashes and fails when one target entry is missing or changed. A failed verification must not call source deletion.

- [ ] **Step 4: Implement target verification**

Read only target batches carrying the migration ID, order by `observed_at` and chunk, apply entries from empty states with event-ID deduplication, and compare every identity hash plus source count and checkpoint manifests. Persist the mismatch summary and stage.

- [ ] **Step 5: Add migration indexes**

Create indexes on `remote_account_history_migrations.updated_at`, target `migration_id`, and source `{site_id, sampled_at}` while retaining the existing TTL indexes.

- [ ] **Step 6: Run focused tests**

Run: `cd backend && .\.venv\Scripts\python.exe -m unittest tests.test_account_history_migration tests.test_account_history -v`

Expected: PASS.

### Task 3: Safety gate and batch deletion

**Files:**
- Modify: `backend/app/modules/sub2api/account_history_migration.py`
- Modify: `backend/tests/test_account_history_migration.py`

- [ ] **Step 1: Write failing source-idle tests**

Assert a latest sample newer than 10 minutes prevents deletion, while an older latest sample allows a verified migration to continue.

- [ ] **Step 2: Implement source-idle check**

Implement `assert_legacy_source_idle(db, idle_minutes=10)` using the latest `sampled_at`. Include the latest timestamp and required cutoff in the raised error.

- [ ] **Step 3: Write failing resumable deletion tests**

Provide 4,500 source IDs and assert deletion runs as 2,000, 2,000, and 500, updates `deleted_documents` after every batch, resumes a ledger already at 2,000, and refuses any stage other than `verified` or `deleting`.

- [ ] **Step 4: Implement batch deletion**

Implement `delete_verified_legacy_samples(db, migration_id, batch_size=2000)`. Repeatedly query `_id` values bounded by the ledger boundary and optional site, delete only those IDs, compare requested and actual deleted counts, update the ledger, and finish only when the bounded count is zero.

- [ ] **Step 5: Run focused tests**

Run: `cd backend && .\.venv\Scripts\python.exe -m unittest tests.test_account_history_migration -v`

Expected: PASS.

### Task 4: Migration CLI and production execution

**Files:**
- Create: `backend/scripts/migrate_account_history.py`
- Modify: `backend/tests/test_account_history_migration.py`

- [ ] **Step 1: Add failing argument safety tests**

Assert conversion is the default, deletion requires `--delete-source`, deletion requires a verified migration ID, and `--batch-size` is clamped between 100 and 10,000.

- [ ] **Step 2: Implement CLI**

Support `--migration-id`, `--site-id`, `--idle-minutes`, `--batch-size`, `--delete-source`, and `--json`. With no migration ID, create a deterministic ID from the source boundary. Print source/target counts, stage, mismatches, and deleted count. Never infer deletion from an interactive prompt.

- [ ] **Step 3: Run full verification**

Run: `cd backend && .\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all tests pass.

- [ ] **Step 4: Inspect production source activity**

Run: `cd backend && .\.venv\Scripts\python.exe scripts\migrate_account_history.py --json`

Expected: conversion succeeds only if source is stable; otherwise the command reports that production must deploy the new writer first and performs no deletion.

- [ ] **Step 5: Execute verified deletion**

Run: `cd backend && .\.venv\Scripts\python.exe scripts\migrate_account_history.py --migration-id <verified-id> --delete-source --batch-size 2000`

Expected: source is idle, verification is already successful, bounded source count reaches zero, and the migration stage is `completed`.
