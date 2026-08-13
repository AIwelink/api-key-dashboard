# Dynamic Account Priority Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recalculate site-wide normal priorities by adapted account type so usable accounts with the oldest remote `created_at` are used first, temporarily unusable accounts move behind them, and recovered accounts return to their chronological position.

**Architecture:** Add a pure queue planner to `smart_scheduling.py` that classifies extreme versus normal accounts, partitions normal accounts by usability, and assigns configured manual-band priorities with overflow clamping. `smart_scheduling_service.py` creates one immutable queue plan per run and passes each calculated normal priority through snapshot evaluation, remote revalidation, bulk updates, and existing state/outcome persistence.

**Tech Stack:** Python 3.14, `unittest`, Motor/MongoDB mocks, existing Sub2API bulk runtime update client.

---

### Task 1: Add pure chronological queue planning

**Files:**
- Modify: `backend/app/modules/sub2api/smart_scheduling.py`
- Modify: `backend/tests/test_smart_scheduling.py`

- [ ] **Step 1: Add failing queue-planner tests**

Import `build_type_priority_queue` and add a helper returning entries with `remote_account_id`, `account`, `state`, `type_priority_enabled`, and `quota_acceleration_enabled`. Add tests equivalent to:

```python
def test_oldest_usable_accounts_receive_contiguous_type_priorities(self) -> None:
    plan = build_type_priority_queue(
        [
            self.queue_entry(3, created_at="2026-01-03T00:00:00+00:00"),
            self.queue_entry(1, created_at="2026-01-01T00:00:00+00:00"),
            self.queue_entry(2, created_at="2026-01-02T00:00:00+00:00"),
        ],
        rules=self.rules,
        now=self.now,
    )
    self.assertEqual(
        {key: value["priority"] for key, value in plan.items()},
        {"1": 50, "2": 51, "3": 52},
    )

def test_unusable_oldest_moves_after_usable_and_recovery_returns_head(self) -> None:
    entries = [
        self.queue_entry(1, created_at="2026-01-01T00:00:00+00:00"),
        self.queue_entry(2, created_at="2026-01-02T00:00:00+00:00"),
        self.queue_entry(3, created_at="2026-01-03T00:00:00+00:00"),
    ]
    entries[0]["account"]["error_message"] = "API returned 429"
    unavailable = build_type_priority_queue(entries, rules=self.rules, now=self.now)
    self.assertEqual(
        {key: value["priority"] for key, value in unavailable.items()},
        {"2": 50, "3": 51, "1": 52},
    )
    entries[0]["account"]["error_message"] = None
    recovered = build_type_priority_queue(entries, rules=self.rules, now=self.now)
    self.assertEqual(recovered["1"]["priority"], 50)
```

Also cover:

- equal timestamps tie-break by ascending account ID;
- missing/invalid timestamps sort after valid timestamps;
- team overflow clamps the 41st and later accounts to `90`;
- pro, plus, k12, and team use their configured manual intervals;
- `bug_team` and `special_team` share the team queue;
- entries without type-priority enablement are absent;
- `extreme` and initial `rate_limit_pending` entries do not consume normal slots;
- cooldown, disabled, unschedulable, 403, error, and 429 entries occupy the temporarily-unusable tail.

- [ ] **Step 2: Run tests to verify RED**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling -v
```

Expected: import/test failure because `build_type_priority_queue` does not exist.

- [ ] **Step 3: Implement the planner**

In `smart_scheduling.py`:

```python
def build_type_priority_queue(
    entries: list[dict[str, Any]],
    *,
    rules: dict[str, Any],
    now: datetime,
) -> dict[str, dict[str, Any]]:
    ...
```

The function must normalize rules and aliases, preliminarily call `evaluate_account` without a queue override to identify extreme/pending entries, group remaining type-enabled entries by adapted type, partition usable before temporarily unusable, sort by parsed remote `created_at` then account ID, and return:

```python
{
    str(remote_account_id): {
        "priority": min(manual_priority_min + queue_index, manual_priority_max),
        "queue_index": queue_index,
        "queue_partition": "usable" | "temporarily_unusable",
        "queue_created_at": normalized_created_at_or_none,
    }
}
```

Add helpers for usability and stable ID/time ordering. Do not mutate input entries.

Extend `evaluate_account` with `normal_priority: int | None = None`. All normal, recovery, and cooldown targets use `normal_priority` when provided, otherwise retain `automatic_priority` as a compatibility fallback. Extreme and initial 429 pending targets always retain the extreme priority.

- [ ] **Step 4: Run tests to verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling -v
```

Expected: all pure scheduling tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/modules/sub2api/smart_scheduling.py backend/tests/test_smart_scheduling.py
git commit -m "feat: add chronological account priority queues"
```

### Task 2: Apply one immutable queue plan per run

**Files:**
- Modify: `backend/app/modules/sub2api/smart_scheduling_service.py`
- Modify: `backend/tests/test_smart_scheduling_service.py`

- [ ] **Step 1: Add failing service tests**

Extend the service test account helper with `created_at`, `status`, `schedulable`, and `error_message`. Add tests that run `run_smart_scheduling` and assert:

```python
# Two team accounts in different enabled groups still share one site-wide queue.
self.assertEqual(
    [call.args for call in client.bulk_update_accounts_runtime.await_args_list],
    [
        ([older_id], {"priority": 50, "concurrency": 30, "group_ids": [4]}),
        ([newer_id], {"priority": 51, "concurrency": 30, "group_ids": [3]}),
    ],
)

# An unavailable oldest account moves behind the usable account.
self.assertEqual(target_by_id[usable_id]["priority"], 50)
self.assertEqual(target_by_id[unavailable_id]["priority"], 51)

# After recovery, the oldest account returns to the head.
self.assertEqual(recovered_target["priority"], 50)

# An extreme account remains at 10 and the next normal account gets 50.
self.assertEqual(normal_target, {"priority": 50, "concurrency": 30})
```

Also assert queue metadata is persisted in state and outcome documents:

```python
self.assertEqual(state_update["queue_partition"], "usable")
self.assertEqual(state_update["queue_index"], 0)
self.assertEqual(outcome_update["queue_priority"], 50)
self.assertEqual(
    outcome_update["queue_created_at"],
    "2026-01-01T00:00:00+00:00",
)
```

Update existing expectations that intentionally preserved manual values such as `250`; with type priority enabled they now expect the calculated queue value, normally `200` for the first plus account. Tests for quota-only groups continue expecting no normal queue rewrite.

- [ ] **Step 2: Run service tests to verify RED**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling_service -v
```

Expected: new and changed expectations fail because the service still uses fixed/manual normal priorities.

- [ ] **Step 3: Integrate the planner**

In `_run_smart_scheduling_locked`, build queue entries after `_states_for_accounts` and call the planner once. Retrieve each account's queue entry and use:

```python
decision = evaluate_account(
    account=account,
    rules=effective_rules,
    type_priority_enabled=item["type_priority_enabled"],
    quota_acceleration_enabled=item["quota_acceleration_enabled"],
    state=state,
    now=now,
    normal_priority=(queue_entry or {}).get("priority"),
)
decision = _with_queue_metadata(decision, queue_entry)
```

Repeat the same override and metadata application after `get_account` remote revalidation. Never rebuild ranks during a run.

Add:

```python
def _with_queue_metadata(
    decision: dict[str, Any],
    queue_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    if not queue_entry:
        return decision
    return decision | {
        "queue_partition": queue_entry["queue_partition"],
        "queue_index": queue_entry["queue_index"],
        "queue_priority": queue_entry["priority"],
        "queue_created_at": queue_entry["queue_created_at"],
    }
```

Extend `_persist_scheduler_state` and `_persist_outcome` to write these four fields, using `None` when the account has no normal queue slot. Keep existing lease renewal, lazy client creation, per-account remote reads, batch grouping, partial success, error redaction, and state modes unchanged.

- [ ] **Step 4: Run service tests to verify GREEN**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling_service -v
```

Expected: all service tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/modules/sub2api/smart_scheduling_service.py backend/tests/test_smart_scheduling_service.py
git commit -m "feat: apply site-wide account priority queues"
```

### Task 3: Verify integration and regression safety

**Files:**
- Verify: `backend/app/modules/sub2api/smart_scheduling.py`
- Verify: `backend/app/modules/sub2api/smart_scheduling_service.py`
- Verify: smart scheduling, account probe, and snapshot tests.

- [ ] **Step 1: Run focused scheduling tests**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling tests.test_smart_scheduling_service tests.test_smart_scheduling_routes -v
```

Expected: zero failures and errors.

- [ ] **Step 2: Run source regressions**

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_account_probe tests.test_sub2api_snapshot_source tests.test_sub2api_account_list -v
```

Expected: zero failures and errors; `created_at`, group membership, and PostgreSQL snapshot behavior remain intact.

- [ ] **Step 3: Run the complete backend suite**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Expected: zero failures and errors.

- [ ] **Step 4: Check branch scope**

```powershell
git diff --check origin/achernar/dev...HEAD
git status --short --branch
git log --oneline origin/achernar/dev..HEAD
```

Expected: no whitespace errors, no uncommitted changes, and only this feature's design, plan, implementation, and test commits.

### Task 4: Publish the pull request

**Files:**
- Verify: Git branch and GitHub PR metadata.

- [ ] **Step 1: Push the branch**

```powershell
git push -u origin codex/dynamic-account-priority-queue
```

- [ ] **Step 2: Create the PR**

```powershell
gh pr create --base achernar/dev --head codex/dynamic-account-priority-queue --title "feat: dynamically queue account priorities" --body "## Summary`n- Order enabled account types by usability and remote creation time.`n- Move later usable accounts forward and restore recovered accounts to chronological position.`n- Preserve extreme quota and delayed 429 behavior.`n`n## Test Plan`n- Focused smart scheduling tests`n- Snapshot and account probe tests`n- Complete backend unittest suite"
```

- [ ] **Step 3: Verify the PR target**

```powershell
gh pr view --json baseRefName,headRefName,state,url,title
```

Expected: base `achernar/dev`, head `codex/dynamic-account-priority-queue`, state `OPEN`.
