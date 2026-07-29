# Smart Scheduling 429 Delayed Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delay recovery of an extreme-mode account for 30 minutes after HTTP 429, then restore normal runtime values and block extreme re-entry until quota recovery or reset.

**Architecture:** Extend the pure smart-scheduling evaluator with two persisted rate-limit modes and one timestamp. The existing service preloads and persists that timestamp, while all actual changes continue through the existing bulk-update grouping path.

**Tech Stack:** Python 3.14, `unittest`, Motor/MongoDB state documents, existing Sub2API bulk Admin API client

---

### Task 1: Add 429 delayed-recovery evaluator behavior

**Files:**
- Modify: `backend/tests/test_smart_scheduling.py`
- Modify: `backend/app/modules/sub2api/smart_scheduling.py`

- [ ] **Step 1: Extend the test account helper with status and error fields**

Add optional `status` and `error_message` arguments and include them in the returned account:

```python
def account(
    self,
    account_type: str,
    *,
    priority: int,
    concurrency: int = 30,
    used: float | None = 20,
    sampled_at: datetime | None = None,
    reset_at: datetime | None = None,
    status: str = "active",
    error_message: str | None = None,
) -> dict[str, object]:
    usage: dict[str, object] = {}
    if used is not None:
        usage["codex_7d_used_percent"] = used
    if sampled_at is not None or used is not None:
        usage["codex_usage_synced_at"] = (sampled_at or self.now).isoformat()
    if reset_at is not None or used is not None:
        usage["codex_7d_reset_at"] = (
            reset_at or (self.now + timedelta(days=3))
        ).isoformat()
    return {
        "remote_account_id": 7,
        "account_type": account_type,
        "priority": priority,
        "concurrency": concurrency,
        "group_ids": [3],
        "status": status,
        "error_message": error_message,
        "usage_snapshot": usage,
    }
```

- [ ] **Step 2: Write failing evaluator tests for the pending delay**

Add tests that assert the first exact 429 records the timestamp without changing extreme values, repeated 429 retains the original timestamp, 29:59 stays pending, and exactly 30:00 targets normal values:

```python
def test_extreme_429_starts_pending_delay_without_runtime_change(self) -> None:
    reset_at = self.now + timedelta(days=3)
    decision = self.evaluate(
        self.account(
            "plus",
            priority=10,
            concurrency=100,
            used=95,
            reset_at=reset_at,
            error_message="API returned 429: rate limited",
        ),
        state={"mode": "extreme", "seven_day_reset_at": reset_at.isoformat()},
    )
    self.assertEqual(decision["status"], "unchanged")
    self.assertEqual(decision["mode"], "rate_limit_pending")
    self.assertEqual(decision["rate_limit_detected_at"], self.now.isoformat())
    self.assertEqual(decision["target"], {"priority": 10, "concurrency": 100})

def test_pending_429_uses_first_detection_time(self) -> None:
    detected_at = self.now - timedelta(minutes=10)
    decision = self.evaluate(
        self.account("plus", priority=10, concurrency=100, used=95, error_message="429"),
        state={"mode": "rate_limit_pending", "rate_limit_detected_at": detected_at.isoformat()},
    )
    self.assertEqual(decision["rate_limit_detected_at"], detected_at.isoformat())

def test_pending_waits_until_exact_thirty_minute_boundary(self) -> None:
    for elapsed, expected_mode in (
        (timedelta(minutes=29, seconds=59), "rate_limit_pending"),
        (timedelta(minutes=30), "rate_limited_cooldown"),
    ):
        with self.subTest(elapsed=elapsed):
            decision = self.evaluate(
                self.account("plus", priority=10, concurrency=100, used=95),
                state={
                    "mode": "rate_limit_pending",
                    "rate_limit_detected_at": (self.now - elapsed).isoformat(),
                },
            )
            self.assertEqual(decision["mode"], expected_mode)
    self.assertEqual(decision["target"], {"priority": 191, "concurrency": 30})
```

- [ ] **Step 3: Write failing evaluator tests for cooldown and exact detection**

Cover cooldown hold, reset/recovery release, stale quota hold, ordinary-mode non-triggering, status-based detection, and `4290` rejection:

```python
def test_cooldown_blocks_extreme_until_quota_recovers(self) -> None:
    decision = self.evaluate(
        self.account("plus", priority=191, concurrency=30, used=95),
        state={"mode": "rate_limited_cooldown", "rate_limit_detected_at": self.now.isoformat()},
    )
    self.assertEqual(decision["mode"], "rate_limited_cooldown")
    self.assertEqual(decision["target"], {"priority": 191, "concurrency": 30})

def test_cooldown_releases_below_recovery_threshold(self) -> None:
    decision = self.evaluate(
        self.account("plus", priority=191, concurrency=30, used=79.9),
        state={"mode": "rate_limited_cooldown", "rate_limit_detected_at": self.now.isoformat()},
    )
    self.assertEqual(decision["mode"], "normal")
    self.assertIsNone(decision["rate_limit_detected_at"])

def test_pending_reset_recovers_before_delay_elapses(self) -> None:
    old_reset = self.now + timedelta(hours=1)
    new_reset = self.now + timedelta(days=7)
    decision = self.evaluate(
        self.account(
            "plus",
            priority=10,
            concurrency=100,
            used=10,
            reset_at=new_reset,
        ),
        state={
            "mode": "rate_limit_pending",
            "rate_limit_detected_at": (self.now - timedelta(minutes=5)).isoformat(),
            "seven_day_reset_at": old_reset.isoformat(),
        },
    )
    self.assertEqual(decision["mode"], "normal")
    self.assertEqual(decision["reason"], "seven_day_window_reset")

def test_stale_quota_holds_cooldown_at_normal_values(self) -> None:
    decision = self.evaluate(
        self.account(
            "plus",
            priority=191,
            concurrency=30,
            used=95,
            sampled_at=self.now - timedelta(minutes=6),
        ),
        state={"mode": "rate_limited_cooldown", "rate_limit_detected_at": self.now.isoformat()},
    )
    self.assertEqual(decision["mode"], "rate_limited_cooldown")
    self.assertEqual(decision["target"], {"priority": 191, "concurrency": 30})

def test_rate_limited_status_starts_pending(self) -> None:
    decision = self.evaluate(
        self.account(
            "plus",
            priority=10,
            concurrency=100,
            used=95,
            status="rate_limited",
        ),
        state={"mode": "extreme"},
    )
    self.assertEqual(decision["mode"], "rate_limit_pending")

def test_normal_account_ignores_429_recovery_signal(self) -> None:
    decision = self.evaluate(
        self.account("plus", priority=250, used=20, error_message="API returned 429"),
        state={"mode": "normal"},
    )
    self.assertEqual(decision["mode"], "normal")
    self.assertNotEqual(decision["strategy"], "rate_limit_recovery")

def test_4290_does_not_start_rate_limit_pending(self) -> None:
    decision = self.evaluate(
        self.account("plus", priority=10, concurrency=100, used=95, error_message="wait 4290 milliseconds"),
        state={"mode": "extreme"},
    )
    self.assertEqual(decision["mode"], "extreme")
```

- [ ] **Step 4: Run evaluator tests to verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling -v
```

Expected: new tests fail because rate-limit modes and `rate_limit_detected_at` are not implemented.

- [ ] **Step 5: Implement the evaluator state machine**

In `smart_scheduling.py`, import `re`, add the fixed delay, and implement exact detection:

```python
import re

RATE_LIMIT_RECOVERY_DELAY = timedelta(minutes=30)
_HTTP_429_PATTERN = re.compile(r"(?<!\d)429(?!\d)")
_RATE_LIMITED_STATUSES = {"429", "rate_limited", "rate-limited", "rate limited"}

def _account_is_rate_limited(account: dict[str, Any]) -> bool:
    status = str(account.get("status") or "").strip().lower()
    if status in _RATE_LIMITED_STATUSES:
        return True
    return bool(_HTTP_429_PATTERN.search(str(account.get("error_message") or "")))
```

Extend decision metadata and evaluate rate-limit modes before ordinary extreme continuation:

```python
rate_limit_detected_at = _parse_datetime(state.get("rate_limit_detected_at"))
base["rate_limit_detected_at"] = (
    rate_limit_detected_at.isoformat() if rate_limit_detected_at else None
)
state_mode = str(state.get("mode") or "")
managed_modes = {"extreme", "rate_limit_pending", "rate_limited_cooldown"}

if state_mode in managed_modes and not quota_acceleration_enabled:
    return base | _result("held", reason="quota_strategy_disabled_extreme_held")

if state_mode in managed_modes:
    previous_reset = _datetime_identity(state.get("seven_day_reset_at"))
    reset_changed = bool(
        quota["reason"] == "quota_ready"
        and previous_reset
        and previous_reset != quota["reset_at"]
    )
    recovered = bool(
        quota["reason"] == "quota_ready"
        and float(quota["percent"]) < float(rule["recovery_percent"])
    )
    if reset_changed or recovered:
        return (base | {"rate_limit_detected_at": None}) | _target_result(
            account,
            priority=int(rule["automatic_priority"]),
            concurrency=int(rule["normal_concurrency"]),
            strategy="quota_recovery",
            mode="normal",
            reason="seven_day_window_reset" if reset_changed else "quota_recovered",
        )
    if state_mode == "rate_limited_cooldown":
        return base | _target_result(
            account,
            priority=int(rule["automatic_priority"]),
            concurrency=int(rule["normal_concurrency"]),
            strategy="rate_limit_recovery",
            mode="rate_limited_cooldown",
            reason="rate_limit_cooldown_held",
        )
    if state_mode == "rate_limit_pending":
        detected_at = rate_limit_detected_at or now.astimezone(UTC)
        base["rate_limit_detected_at"] = detected_at.isoformat()
        if now.astimezone(UTC) - detected_at >= RATE_LIMIT_RECOVERY_DELAY:
            return base | _target_result(
                account,
                priority=int(rule["automatic_priority"]),
                concurrency=int(rule["normal_concurrency"]),
                strategy="rate_limit_recovery",
                mode="rate_limited_cooldown",
                reason="rate_limit_delay_elapsed",
            )
        return base | _target_result(
            account,
            priority=int(normalized_rules["extreme"]["priority"]),
            concurrency=int(rule["extreme_concurrency"]),
            strategy="rate_limit_recovery",
            mode="rate_limit_pending",
            reason="rate_limit_delay_pending",
        )
    if _account_is_rate_limited(account):
        base["rate_limit_detected_at"] = now.astimezone(UTC).isoformat()
        return base | _target_result(
            account,
            priority=int(normalized_rules["extreme"]["priority"]),
            concurrency=int(rule["extreme_concurrency"]),
            strategy="rate_limit_recovery",
            mode="rate_limit_pending",
            reason="rate_limit_delay_started",
        )
    if quota["reason"] != "quota_ready":
        suffix = "stale" if quota["reason"] == "quota_stale" else "missing"
        return base | _result("held", reason=f"quota_{suffix}_extreme_held")
    return base | _target_result(
        account,
        priority=int(normalized_rules["extreme"]["priority"]),
        concurrency=int(rule["extreme_concurrency"]),
        strategy="quota_acceleration",
        mode="extreme",
        reason="quota_extreme_continues",
    )
```

Use these exact decision labels:

```python
strategy="rate_limit_recovery"
reason="rate_limit_delay_started"
reason="rate_limit_delay_pending"
reason="rate_limit_delay_elapsed"
reason="rate_limit_cooldown_held"
```

- [ ] **Step 6: Run evaluator tests to verify GREEN**

Run the Task 1 command again.

Expected: all `tests.test_smart_scheduling` tests pass.

- [ ] **Step 7: Commit evaluator behavior**

```powershell
git add backend/app/modules/sub2api/smart_scheduling.py backend/tests/test_smart_scheduling.py
git commit -m "feat: add delayed 429 scheduling recovery"
```

### Task 2: Persist rate-limit state and exercise bulk recovery

**Files:**
- Modify: `backend/tests/test_smart_scheduling_service.py`
- Modify: `backend/app/modules/sub2api/smart_scheduling_service.py`

- [ ] **Step 1: Write a failing state projection test**

Extend `test_states_are_preloaded_once_with_compact_projection` to require:

```python
{
    "remote_account_id": 1,
    "mode": 1,
    "seven_day_reset_at": 1,
    "rate_limit_detected_at": 1,
}
```

- [ ] **Step 2: Write failing service tests for pending and delayed recovery**

Add one test where a first 429 at extreme produces no remote calls but persists `rate_limit_pending`, and one where a 31-minute-old pending state calls bulk update with normal values and persists cooldown:

```python
async def test_first_extreme_429_persists_pending_without_remote_update(self) -> None:
    account = self.account(7, priority=10, concurrency=100, used=95)
    account["error_message"] = "API returned 429"
    db = self.db(states=[{"remote_account_id": 7, "mode": "extreme"}])
    client = SimpleNamespace(get_account=AsyncMock(), bulk_update_accounts_runtime=AsyncMock())
    result = await run_smart_scheduling(
        db,
        site=self.site(),
        accounts=[account],
        group_settings={3: {"type_priority_enabled": True, "quota_acceleration_enabled": True}},
        probe_run_id="probe-1",
        rules=self.rules,
        client=client,
        now=self.now,
    )
    client.get_account.assert_not_awaited()
    client.bulk_update_accounts_runtime.assert_not_awaited()
    self.assertEqual(result["unchanged"], 1)
    state = db.sub2api_smart_scheduling_states.update_one.await_args.args[1]["$set"]
    self.assertEqual(state["mode"], "rate_limit_pending")
    self.assertEqual(state["rate_limit_detected_at"], self.now.isoformat())

async def test_elapsed_429_delay_bulk_restores_normal_values(self) -> None:
    detected_at = self.now - timedelta(minutes=31)
    db = self.db(states=[{
        "remote_account_id": 7,
        "mode": "rate_limit_pending",
        "rate_limit_detected_at": detected_at,
    }])
    client = SimpleNamespace(
        get_account=AsyncMock(return_value={"id": 7, "priority": 10, "concurrency": 100, "group_ids": [3]}),
        bulk_update_accounts_runtime=AsyncMock(return_value={"success_ids": [7], "failed_ids": []}),
    )
    result = await run_smart_scheduling(
        db,
        site=self.site(),
        accounts=[self.account(7, priority=10, concurrency=100, used=95)],
        group_settings={3: {"type_priority_enabled": True, "quota_acceleration_enabled": True}},
        probe_run_id="probe-1",
        rules=self.rules,
        client=client,
        now=self.now,
    )
    client.bulk_update_accounts_runtime.assert_awaited_once_with(
        [7], {"priority": 191, "concurrency": 30, "group_ids": [3]}
    )
    self.assertEqual(result["changed"], 1)
    state = db.sub2api_smart_scheduling_states.update_one.await_args.args[1]["$set"]
    self.assertEqual(state["mode"], "rate_limited_cooldown")
```

- [ ] **Step 3: Run service tests to verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling_service -v
```

Expected: projection and state timestamp assertions fail.

- [ ] **Step 4: Implement state projection and persistence**

Add `rate_limit_detected_at` to `_states_for_accounts` projection and `_persist_scheduler_state` updates:

```python
{
    "remote_account_id": 1,
    "mode": 1,
    "seven_day_reset_at": 1,
    "rate_limit_detected_at": 1,
}
```

```python
updates["rate_limit_detected_at"] = decision.get("rate_limit_detected_at")
```

- [ ] **Step 5: Run service tests to verify GREEN**

Run the Task 2 command again.

Expected: all `tests.test_smart_scheduling_service` tests pass.

- [ ] **Step 6: Commit service integration**

```powershell
git add backend/app/modules/sub2api/smart_scheduling_service.py backend/tests/test_smart_scheduling_service.py
git commit -m "feat: persist 429 scheduling cooldown state"
```

### Task 3: Verify the complete backend behavior

**Files:**
- Verify: `backend/app/modules/sub2api/smart_scheduling.py`
- Verify: `backend/app/modules/sub2api/smart_scheduling_service.py`
- Verify: `backend/tests/test_smart_scheduling.py`
- Verify: `backend/tests/test_smart_scheduling_service.py`

- [ ] **Step 1: Run focused scheduling tests**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling tests.test_smart_scheduling_service tests.test_account_probe -v
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the complete backend suite**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 3: Check the final diff**

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only the intended implementation files plus any pre-existing user changes are present.
