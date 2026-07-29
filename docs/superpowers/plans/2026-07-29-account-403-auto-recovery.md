# Account 403 Automatic Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retest HTTP 403 accounts every three minutes with `gpt-5.5`, recover their remote state after a successful test, and then reopen scheduling.

**Architecture:** Keep `sub2api_accounts_cache` as the sole account-state input and extend the existing unified account-test event pipeline. Shared HTTP-status helpers drive event timing and scheduler priority, while the existing scheduling dispatcher performs durable recover-state and schedulable phases with replay-safe progress fields.

**Tech Stack:** Python 3.14, `unittest`, Motor/MongoDB, existing Sub2API Admin API client

---

### Task 1: Record `gpt-5.5` 403 recovery context and timing

**Files:**
- Modify: `backend/app/modules/sub2api/account_test_outcomes.py`
- Modify: `backend/app/modules/sub2api/account_test_service.py`
- Modify: `backend/tests/test_account_test_outcomes.py`
- Modify: `backend/tests/test_account_test_service.py`

- [ ] **Step 1: Write failing exact-status and event-timing tests**

Add public status-helper assertions:

```python
from app.modules.sub2api.account_test_outcomes import (
    classify_test_result,
    disable_reason,
    has_http_status,
    snapshot_has_http_status,
)

def test_http_status_detection_is_exact_and_reads_cached_snapshot(self) -> None:
    self.assertTrue(has_http_status("API returned 403: forbidden", 403))
    self.assertTrue(snapshot_has_http_status({"status": "403"}, 403))
    self.assertTrue(
        snapshot_has_http_status(
            {"account": {"error_message": "API returned 403"}}, 403
        )
    )
    self.assertFalse(has_http_status("wait 4030 milliseconds", 403))
```

Update the existing persistence test to expect `gpt-5.5`, then add tests for a successful snapshot-403 event and a model-response 403 event:

```python
async def test_snapshot_403_success_records_recovery_context_and_rapid_interval(self) -> None:
    db = _db()
    client = SimpleNamespace(
        test_account=AsyncMock(return_value={"success": True, "model": "gpt-5.5"})
    )
    result = await execute_account_test(
        db,
        site={"_id": "US06-5001"},
        account={
            "sub2api_account_id": 4072,
            "fetched_at": NOW - timedelta(seconds=20),
            "account": {"error_message": "API returned 403"},
        },
        client=client,
        dispatcher=AsyncMock(),
        now=NOW,
    )
    self.assertEqual(result["next_test_at"], NOW + timedelta(minutes=3))
    self.assertTrue(result["recovery"]["required"])
    self.assertTrue(result["recovery"]["snapshot_http_403"])
    self.assertEqual(
        result["dispatch"]["scheduling"]["recover_state_status"],
        "pending",
    )

async def test_model_403_uses_rapid_interval_without_starting_recovery(self) -> None:
    db = _db()
    result = await execute_account_test(
        db,
        site={"_id": "US06-5001"},
        account={"sub2api_account_id": 4072},
        client=SimpleNamespace(
            test_account=AsyncMock(
                return_value={"success": False, "error": "API returned 403"}
            )
        ),
        dispatcher=AsyncMock(),
        now=NOW,
    )
    self.assertEqual(result["http_status"], 403)
    self.assertEqual(result["next_test_at"], NOW + timedelta(minutes=3))
    self.assertFalse(result["recovery"]["required"])
```

- [ ] **Step 2: Run Task 1 tests to verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_account_test_outcomes tests.test_account_test_service -v
```

Expected: failures for missing helpers, old `gpt-5.4`, missing recovery context, and the 24-hour 403 interval.

- [ ] **Step 3: Implement shared status recognition and event metadata**

Expose shared helpers in `account_test_outcomes.py` and use the existing contextual HTTP pattern:

```python
def has_http_status(value: Any, status_code: int) -> bool:
    text = str(value or "").strip().lower()
    if text == str(status_code):
        return True
    return re.search(
        rf"\b(?:returned|status|http(?:/\d(?:\.\d)?)?)[^0-9]{{0,12}}{status_code}\b",
        text,
    ) is not None

def snapshot_has_http_status(account: dict[str, Any], status_code: int) -> bool:
    nested = account.get("account") if isinstance(account.get("account"), dict) else {}
    return any(
        has_http_status(source.get(field), status_code)
        for source in (account, nested)
        for field in ("status", "error_message")
    )
```

Replace the private classifier calls with `has_http_status`. In `account_test_service.py`, define:

```python
TEST_MODEL = "gpt-5.5"
TEST_INTERVAL = timedelta(hours=24)
RAPID_403_TEST_INTERVAL = timedelta(minutes=3)
```

Compute `http_status`, `snapshot_http_403`, recovery context, dispatch phase defaults, and the interval once in `_event_document`. Store `last_http_status`, `last_snapshot_http_403`, `last_snapshot_fetched_at`, and `interval_mode` in `_latest_state`. Rename `_sanitize_text` to public `sanitize_account_test_text` so dispatcher errors can reuse the same credential redaction.

Use this event shape:

```python
http_status = _http_status(error)
snapshot_http_403 = snapshot_has_http_status(account, 403)
rapid_http_403 = snapshot_http_403 or http_status == 403
recovery_required = snapshot_http_403 and outcome == "passed"
next_test_at = tested_at + (
    RAPID_403_TEST_INTERVAL if rapid_http_403 else TEST_INTERVAL
)
recovery = {
    "required": recovery_required,
    "snapshot_http_403": snapshot_http_403,
    "snapshot_fetched_at": _optional_datetime(account.get("fetched_at")),
}
scheduling_dispatch = {
    "status": "pending",
    "attempts": 0,
    "recover_state_status": "pending" if recovery_required else "not_required",
    "recover_state_attempts": 0,
    "enable_schedulable_status": "pending" if recovery_required else "not_required",
    "enable_schedulable_attempts": 0,
}
```

- [ ] **Step 4: Run Task 1 tests to verify GREEN**

Run the Task 1 command again.

Expected: all outcome and account-test service tests pass.

- [ ] **Step 5: Commit event behavior**

```powershell
git add backend/app/modules/sub2api/account_test_outcomes.py backend/app/modules/sub2api/account_test_service.py backend/tests/test_account_test_outcomes.py backend/tests/test_account_test_service.py
git commit -m "feat: add rapid 403 account test events"
```

### Task 2: Prioritize current 403 snapshots in the unified queue

**Files:**
- Modify: `backend/app/modules/sub2api/account_test_scheduler.py`
- Modify: `backend/tests/test_account_test_scheduler.py`

- [ ] **Step 1: Write failing scheduler priority tests**

Add pure-selection tests:

```python
def test_due_snapshot_403_precedes_never_tested_and_normal_due(self) -> None:
    accounts = [
        {"site_id": "site-a", "sub2api_account_id": 10},
        {
            "site_id": "site-a",
            "sub2api_account_id": 11,
            "fetched_at": NOW,
            "account": {"error_message": "API returned 403"},
        },
        {"site_id": "site-a", "sub2api_account_id": 12},
    ]
    states = {
        "site-a:11": {
            "last_tested_at": NOW - timedelta(minutes=4),
            "next_test_at": NOW + timedelta(hours=20),
        },
        "site-a:12": {"next_test_at": NOW - timedelta(hours=1)},
    }
    selected = select_due_account(
        [{"_id": "site-a"}], accounts, states, now=NOW
    )
    self.assertEqual(selected["account"]["sub2api_account_id"], 11)

def test_recovered_account_ignores_older_cached_403(self) -> None:
    selected = select_due_account(
        [{"_id": "site-a"}],
        [{
            "site_id": "site-a",
            "sub2api_account_id": 11,
            "fetched_at": NOW - timedelta(seconds=30),
            "account": {"error_message": "API returned 403"},
        }],
        {"site-a:11": {
            "last_tested_at": NOW - timedelta(minutes=1),
            "next_test_at": NOW + timedelta(hours=23),
            "recovery_completed_at": NOW,
        }},
        now=NOW,
    )
    self.assertIsNone(selected)
```

Add a load test that asserts `sub2api_accounts_cache.find` projects top-level/nested status, error, schedulable, and `fetched_at`:

```python
class AsyncCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def sort(self, *_args, **_kwargs):
        return self

    def __aiter__(self):
        self.iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

async def test_load_due_account_reads_403_fields_from_account_cache(self) -> None:
    account_find = MagicMock(return_value=AsyncCursor([]))
    db = SimpleNamespace(
        sub2api_sites=SimpleNamespace(
            find=MagicMock(return_value=AsyncCursor([{"_id": "site-a"}]))
        ),
        sub2api_accounts_cache=SimpleNamespace(find=account_find),
        sub2api_account_test_states=SimpleNamespace(
            find=MagicMock(return_value=AsyncCursor([]))
        ),
        sub2api_account_test_site_meta=SimpleNamespace(
            find=MagicMock(return_value=AsyncCursor([]))
        ),
    )
    await load_due_account(db, now=NOW)
    projection = account_find.call_args.args[1]
    self.assertEqual(projection["status"], 1)
    self.assertEqual(projection["account.error_message"], 1)
    self.assertEqual(projection["account.schedulable"], 1)
    self.assertEqual(projection["fetched_at"], 1)
```

- [ ] **Step 2: Run scheduler tests to verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_account_test_scheduler -v
```

Expected: the future normal deadline prevents account 11 from being selected and the source projection lacks 403 fields.

- [ ] **Step 3: Implement effective 403 due time and priority**

Import `snapshot_has_http_status` and `RAPID_403_TEST_INTERVAL`. Extend the cache projection with:

```python
{
    "site_id": 1,
    "sub2api_account_id": 1,
    "status": 1,
    "schedulable": 1,
    "fetched_at": 1,
    "account.status": 1,
    "account.error_message": 1,
    "account.schedulable": 1,
    "account.credentials.email": 1,
}
```

In `select_due_account`, treat a cached 403 as stale when its `fetched_at` is not newer than `recovery_completed_at`. A current 403 with no prior snapshot-403 test is immediately due; otherwise cap its effective deadline at `last_tested_at + RAPID_403_TEST_INTERVAL`. A latest model event with `last_http_status == 403` also uses the rapid deadline. Sort due rapid accounts before never-tested and ordinary due accounts.

- [ ] **Step 4: Run scheduler tests to verify GREEN**

Run the Task 2 command again.

Expected: all scheduler tests pass.

- [ ] **Step 5: Commit queue selection**

```powershell
git add backend/app/modules/sub2api/account_test_scheduler.py backend/tests/test_account_test_scheduler.py
git commit -m "feat: prioritize 403 account recovery tests"
```

### Task 3: Recover remote state before reopening scheduling

**Files:**
- Modify: `backend/app/modules/sub2api/account_test_dispatcher.py`
- Modify: `backend/tests/test_account_test_dispatcher.py`

- [ ] **Step 1: Write failing ordered-recovery and replay tests**

Extend the event helper with `model="gpt-5.5"` and recovery phase context:

```python
def _event(
    outcome: str,
    *,
    recovery_required: bool = False,
    recover_state_status: str = "pending",
) -> dict:
    return {
        "_id": "event-1",
        "state_id": "US06-5001:4072",
        "site_id": "US06-5001",
        "remote_account_id": 4072,
        "model": "gpt-5.5",
        "outcome": outcome,
        "tested_at": NOW,
        "recovery": {
            "required": recovery_required,
            "snapshot_http_403": recovery_required,
            "snapshot_fetched_at": NOW,
        },
        "dispatch": {
            "scheduling": {
                "status": "pending",
                "attempts": 0,
                "recover_state_status": (
                    recover_state_status if recovery_required else "not_required"
                ),
                "recover_state_attempts": 0,
                "enable_schedulable_status": (
                    "pending" if recovery_required else "not_required"
                ),
                "enable_schedulable_attempts": 0,
            },
            "plan_correction": {"status": "pending"},
        },
    }
```

Add the ordered-success test:

```python
async def test_snapshot_403_success_recovers_before_enabling(self) -> None:
    order: list[str] = []
    db = _db({
        "fetched_at": NOW,
        "schedulable": False,
        "account": {"schedulable": False, "error_message": "API returned 403"},
    })
    client = SimpleNamespace(
        recover_account_state=AsyncMock(
            side_effect=lambda _account_id: order.append("recover") or {
                "status": "active", "error_message": "", "schedulable": False
            }
        ),
        set_account_schedulable=AsyncMock(
            side_effect=lambda _account_id, _desired: order.append("enable") or {}
        ),
    )
    await handle_scheduling(
        db,
        _event("passed", recovery_required=True),
        site={"_id": "US06-5001"},
        client=client,
    )
    self.assertEqual(order, ["recover", "enable"])
    state_update = db.sub2api_account_test_states.update_one.await_args_list[-1]
    self.assertEqual(
        state_update.args[1]["$set"]["next_test_at"],
        NOW + timedelta(hours=24),
    )
```

Add exact failure and replay coverage:

```python
async def test_recover_failure_does_not_enable_scheduling(self) -> None:
    db = _db({"schedulable": False})
    client = SimpleNamespace(
        recover_account_state=AsyncMock(side_effect=RuntimeError("recover failed")),
        set_account_schedulable=AsyncMock(),
    )
    with self.assertRaisesRegex(RuntimeError, "recover failed"):
        await handle_scheduling(
            db, _event("passed", recovery_required=True), client=client
        )
    client.set_account_schedulable.assert_not_awaited()
    phase_updates = [
        call.args[1]["$set"]
        for call in db.sub2api_account_test_events.update_one.await_args_list
        if "$set" in call.args[1]
    ]
    self.assertTrue(
        any(update.get("dispatch.scheduling.recover_state_status") == "failed"
            for update in phase_updates)
    )

async def test_replay_skips_completed_recover_phase(self) -> None:
    db = _db({"schedulable": False})
    client = SimpleNamespace(
        recover_account_state=AsyncMock(),
        set_account_schedulable=AsyncMock(return_value={}),
    )
    await handle_scheduling(
        db,
        _event(
            "passed",
            recovery_required=True,
            recover_state_status="completed",
        ),
        client=client,
    )
    client.recover_account_state.assert_not_awaited()
    client.set_account_schedulable.assert_awaited_once_with(4072, True)
```

In the existing ordinary passed test, add `recover_account_state=AsyncMock()` to the client and assert it is not awaited. The existing stale-event test continues to prove that no remote action runs. Update plan-correction provenance assertions to expect `gpt-5.5`.

- [ ] **Step 2: Run dispatcher tests to verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_account_test_dispatcher -v
```

Expected: missing recover-state calls and recovery phase fields cause failures.

- [ ] **Step 3: Implement durable two-phase recovery**

For a latest `passed` event with `recovery.required=true`:

1. Load the full account only from `sub2api_accounts_cache`.
2. If `recover_state_status` is not completed, mark the phase processing, call `recover_account_state`, then persist completion or a sanitized phase error.
3. Recheck the latest-event guard.
4. If `enable_schedulable_status` is not completed, call `set_account_schedulable(event["remote_account_id"], True)` only when the cached value is not already true, then persist completion or a sanitized phase error.
5. Mark event/state recovery completion, update the latest state deadline to `tested_at + TEST_INTERVAL`, and mirror action response fields to the cache.

Use a three-minute `_finish_handler` retry delay for recovery scheduling events and retain the existing five-minute delay for other handler failures. Read completed phase fields from the freshly loaded event during replay and skip their remote calls. Use `sanitize_account_test_text` for handler and phase errors.

For ordinary `passed` events, preserve the current enable-only behavior. Change plan-correction provenance from the literal `gpt-5.4` to `event.get("model") or "gpt-5.5"`.

- [ ] **Step 4: Run dispatcher tests to verify GREEN**

Run the Task 3 command again.

Expected: all dispatcher tests pass.

- [ ] **Step 5: Commit recovery actions**

```powershell
git add backend/app/modules/sub2api/account_test_dispatcher.py backend/tests/test_account_test_dispatcher.py
git commit -m "feat: recover 403 accounts before scheduling"
```

### Task 4: Verify unified account testing and backend behavior

**Files:**
- Verify: `backend/app/modules/sub2api/account_test_outcomes.py`
- Verify: `backend/app/modules/sub2api/account_test_service.py`
- Verify: `backend/app/modules/sub2api/account_test_scheduler.py`
- Verify: `backend/app/modules/sub2api/account_test_dispatcher.py`

- [ ] **Step 1: Run focused account-test tests**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_account_test_outcomes tests.test_account_test_service tests.test_account_test_scheduler tests.test_account_test_dispatcher tests.test_account_test_bootstrap -v
```

Expected: all focused tests pass.

- [ ] **Step 2: Run related cache and probe tests**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_account_probe tests.test_sub2api_cache tests.test_sub2api_client_update -v
```

Expected: all related source and client tests pass.

- [ ] **Step 3: Run the complete backend suite**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Expected: zero failures and zero errors.

- [ ] **Step 4: Check committed and working-tree differences**

```powershell
git diff --check origin/achernar/dev...HEAD
git status --short
```

Expected: no whitespace errors; intended 403 recovery commits are present, while pre-existing unrelated changes in `cache.py`, `test_capacity_risk_integration.py`, and `docs/design/30-api-pool-realtime-capacity-and-presence.md` remain unstaged.
