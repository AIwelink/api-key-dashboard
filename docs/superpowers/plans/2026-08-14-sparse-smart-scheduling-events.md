# Sparse Smart Scheduling Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reuse one smart-scheduling state document per account and write outcome documents only for remote failures and applied mode/target transitions.

**Architecture:** Keep the 60-second scheduler and run summaries unchanged. Compare each new decision with the previously loaded state, persist the reusable state through the existing path, and gate outcome persistence behind an explicit failure or transition event. Shorten new event retention to 7 days and remove the redundant outcome compound index during idempotent bootstrap.

**Tech Stack:** Python 3.12, FastAPI service modules, Motor/PyMongo, unittest/pytest, MongoDB TTL indexes.

---

### Task 1: Define and test sparse event behavior

**Files:**
- Modify: `backend/tests/test_smart_scheduling_service.py`
- Modify: `backend/app/modules/sub2api/smart_scheduling_service.py`

- [x] **Step 1: Write failing suppression and transition tests**

Add focused service tests covering an unchanged account, a held account, an
initial successful change, an existing-state mode transition, a target-only
transition, and a reason-only state refresh. Use state fixtures containing
`mode`, `last_target`, `last_strategy`, and `last_reason`.

The core assertions are:

```python
db.sub2api_smart_scheduling_outcomes.update_one.assert_not_awaited()
```

for unchanged, held, skipped, initial-baseline, and reason-only cases, and:

```python
outcome = db.sub2api_smart_scheduling_outcomes.update_one.await_args.args[1]["$set"]
self.assertEqual(outcome["event_type"], "state_transition")
self.assertEqual(
    outcome["previous_state"],
    {"mode": "normal", "target": {"priority": 250, "concurrency": 30}},
)
self.assertEqual(
    outcome["applied_state"],
    {"mode": "extreme", "target": {"priority": 10, "concurrency": 100, "load_factor": 10000}},
)
```

Keep existing failure tests and add:

```python
self.assertEqual(failed_outcome["event_type"], "remote_update_failed")
self.assertNotIn("previous_state", failed_outcome)
self.assertNotIn("applied_state", failed_outcome)
```

Update older tests that expected a baseline or no-op outcome so they assert
state/run behavior instead. Tests whose purpose is transition event content
must provide an existing state fixture.

- [x] **Step 2: Run focused tests and verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_smart_scheduling_service.py -q
```

Expected: new suppression assertions fail because the service currently writes
an outcome for every evaluated account, and new event fields are absent.

- [x] **Step 3: Load transition fields from reusable state records**

Expand `_states_for_accounts` projection with the existing persisted fields:

```python
"adapted_type": 1,
"last_strategy": 1,
"last_reason": 1,
"last_target": 1,
```

Do not fetch full account documents or credentials.

- [x] **Step 4: Add a stable transition comparison helper**

Add focused pure helpers near `_runtime_values`:

```python
def _managed_state_projection(
    *,
    mode: Any,
    target: Any,
) -> dict[str, Any]:
    source = target if isinstance(target, dict) else {}
    normalized_target = {
        field: value
        for field in ("priority", "concurrency", "load_factor")
        if (value := _optional_int(source.get(field))) is not None
    }
    return {
        "mode": str(mode).strip() if mode is not None else None,
        "target": normalized_target or None,
    }


def _state_transition(
    state: dict[str, Any] | None,
    decision: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    if not state:
        return None
    previous = _managed_state_projection(
        mode=state.get("mode"),
        target=state.get("last_target"),
    )
    applied = _managed_state_projection(
        mode=decision.get("mode"),
        target=decision.get("target"),
    )
    return None if previous == applied else (previous, applied)
```

This deliberately ignores reason, strategy, quota, timestamps, and queue
metadata.

- [x] **Step 5: Gate outcome writes by event type**

Extend `_persist_outcome` with:

```python
event_type: str,
previous_state: dict[str, Any] | None = None,
applied_state: dict[str, Any] | None = None,
```

Always persist `event_type`. Add `previous_state` and `applied_state` to `$set`
only for `state_transition` events.

Change the account loop so:

```python
transition = _state_transition(state, decision)
```

is calculated before a successful state write. After persisting the new state,
write a `state_transition` outcome only when `transition` is not `None`.
Remove unconditional outcome writes for `unchanged`, `held`, and `skipped`.

On per-account or batch failure, write exactly one outcome with:

```python
event_type="remote_update_failed"
```

and do not persist the proposed decision as the applied state. Preserve existing
lease-stop, error sanitization, counters, and bulk update behavior.

- [x] **Step 6: Run focused tests and verify GREEN**

Run the Task 1 pytest command. Expected: all smart-scheduling service tests pass.

- [x] **Step 7: Commit sparse service events**

```powershell
git add backend/app/modules/sub2api/smart_scheduling_service.py backend/tests/test_smart_scheduling_service.py
git commit -m "feat: store sparse smart scheduling events"
```

### Task 2: Shorten retention and remove the redundant index

**Files:**
- Modify: `backend/tests/test_smart_scheduling_routes.py`
- Modify: `backend/tests/test_smart_scheduling_service.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Modify: `backend/app/modules/sub2api/smart_scheduling_service.py`

- [x] **Step 1: Write failing retention and migration tests**

Change the event-retention assertion to:

```python
self.assertEqual(outcome["expires_at"], self.now + timedelta(days=7))
```

Give the mocked outcome collection `index_information` and `drop_index` methods.
Assert the bootstrap keeps the TTL index, does not recreate the compound index,
and drops it when present:

```python
outcomes.index_information = AsyncMock(
    return_value={
        "_id_": {"key": [("_id", 1)]},
        "site_id_1_run_id_1_remote_account_id_1": {
            "key": [("site_id", 1), ("run_id", 1), ("remote_account_id", 1)]
        },
    }
)
outcomes.drop_index.assert_awaited_once_with(
    "site_id_1_run_id_1_remote_account_id_1"
)
outcomes.create_index.assert_awaited_once_with(
    "expires_at",
    expireAfterSeconds=0,
)
```

Add a fresh-database case where `index_information` returns only `_id_` and
assert `drop_index` is not awaited.

- [x] **Step 2: Run focused tests and verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_smart_scheduling_service.py backend\tests\test_smart_scheduling_routes.py -q
```

Expected: retention remains 30 days and bootstrap still creates the compound
index.

- [x] **Step 3: Implement retention and idempotent index removal**

Set:

```python
OUTCOME_RETENTION = timedelta(days=7)
```

In `ensure_smart_scheduling_indexes`, inspect existing outcome indexes and drop
the exact legacy index name only when present:

```python
legacy_outcome_index = "site_id_1_run_id_1_remote_account_id_1"
outcome_indexes = await db.sub2api_smart_scheduling_outcomes.index_information()
if legacy_outcome_index in outcome_indexes:
    await db.sub2api_smart_scheduling_outcomes.drop_index(legacy_outcome_index)
await db.sub2api_smart_scheduling_outcomes.create_index(
    "expires_at",
    expireAfterSeconds=0,
)
```

Remove creation of the redundant compound index. Do not alter state or run
indexes and do not issue document deletes.

- [x] **Step 4: Run focused tests and verify GREEN**

Run the Task 2 pytest command. Expected: both test modules pass.

- [x] **Step 5: Commit retention and index migration**

```powershell
git add backend/app/modules/sub2api/smart_scheduling_service.py backend/app/modules/system/bootstrap.py backend/tests/test_smart_scheduling_service.py backend/tests/test_smart_scheduling_routes.py
git commit -m "perf: reduce smart scheduling event storage"
```

### Task 3: Verify, document, and publish

**Files:**
- Modify: `docs/superpowers/plans/2026-08-14-sparse-smart-scheduling-events.md`

- [x] **Step 1: Run the complete backend suite**

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests -q
```

Expected: all backend tests pass with no failures.

- [x] **Step 2: Run static repository checks**

```powershell
git diff --check origin/achernar/dev...HEAD
git status --short
```

Expected: no whitespace errors and only the intended plan checkbox update, if
not yet committed.

- [x] **Step 3: Audit requirements against current code**

Confirm with searches and test evidence that:

- no unconditional per-account outcome write remains;
- failure and transition calls set explicit event types;
- state comparison uses only mode and normalized target;
- new events expire after 7 days;
- bootstrap no longer creates the compound outcome index;
- no historical document deletion code was added;
- frontend files are unchanged.

- [x] **Step 4: Mark the plan complete and commit documentation**

Change all plan checkboxes to `[x]`, then run:

```powershell
git add docs/superpowers/plans/2026-08-14-sparse-smart-scheduling-events.md
git commit -m "docs: complete sparse scheduling event plan"
```

- [x] **Step 5: Push and open a pull request**

Push `codex/sparse-smart-scheduling-events` and open a draft PR targeting
`achernar/dev`. The PR body must summarize sparse state/event storage, 7-day
retention, redundant-index removal, verification results, and the fact that
historical outcome cleanup is excluded.
