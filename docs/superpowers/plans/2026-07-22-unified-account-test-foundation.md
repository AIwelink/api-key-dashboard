# Unified Account Test Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a durable, globally serial `gpt-5.4` account testing foundation that stores every result before immediately dispatching reusable scheduling and Free-to-Plus judgments.

**Architecture:** A small outcome module owns response classification, a service owns remote execution and durable event/state writes, a dispatcher owns idempotent judgment handlers, and a scheduler selects every due account across active Sub2API sites under a MongoDB lease. Cache and account-probe normalization consume only persisted verified plan types; the existing 5002 workflow remains unchanged.

**Tech Stack:** Python 3.12+, asyncio, FastAPI, Motor/MongoDB, PyMongo update operators, existing `Sub2ApiClient`, `unittest` and `unittest.mock`.

---

## File Map

- Create `backend/app/modules/sub2api/account_test_outcomes.py`: pure outcome classification and confirmed account-disable reasons.
- Create `backend/app/modules/sub2api/account_test_service.py`: execute one test, sanitize/persist result, update latest state/cache, then dispatch.
- Create `backend/app/modules/sub2api/account_test_dispatcher.py`: handler registry, durable handler state, scheduling and plan-correction judgments, pending-event replay.
- Create `backend/app/modules/sub2api/account_test_scheduler.py`: due-account selection, distributed lease, site credentials and global serial loop.
- Modify `backend/app/modules/sub2api/cache.py`: separate Plus candidate recognition from verified type application and batch-load persisted verification state.
- Modify `backend/app/modules/sub2api/account_probe.py`: batch-load persisted verified type before identity history updates.
- Modify `backend/app/modules/system/bootstrap.py`: create latest-state, event and site-meta indexes.
- Modify `backend/app/main.py`: replace long-7d scheduler task with unified test scheduler task.
- Create focused tests under `backend/tests/test_account_test_*.py`; update existing capacity, account-probe, bootstrap and main tests where contracts change.
- Update `docs/design/15-api-pool-status-cache.md`: document persisted verification as the only Free-to-Plus override.

### Task 1: Standard Outcome Classification

**Files:**
- Create: `backend/app/modules/sub2api/account_test_outcomes.py`
- Create: `backend/tests/test_account_test_outcomes.py`

- [ ] **Step 1: Write failing pure-classifier tests**

Cover success, 429, confirmed 401/402/403, unrelated 403, model-not-supported and transport errors:

```python
class AccountTestOutcomeTests(unittest.TestCase):
    def test_classifies_supported_account_results(self) -> None:
        self.assertEqual(classify_test_result({"success": True}), "passed")
        self.assertEqual(classify_test_result({"error": "API returned 429"}), "rate_limited")
        self.assertEqual(classify_test_result({"error": "API returned 401: token_invalidated"}), "unauthorized")
        self.assertEqual(classify_test_result({"error": "API returned 402: deactivated_workspace"}), "payment_required")
        self.assertEqual(
            classify_test_result({"error": "API returned 403: Personal access token owner is inactive"}),
            "inactive_owner",
        )
        self.assertEqual(classify_test_result({"error": "API returned 403: model_not_allowed"}), "forbidden_other")
        self.assertEqual(
            classify_test_result({"error": "model is not supported when using codex with a chatgpt account"}),
            "model_not_supported",
        )

    def test_disable_reason_is_limited_to_confirmed_account_failures(self) -> None:
        self.assertEqual(disable_reason("unauthorized"), "token_invalidated")
        self.assertEqual(disable_reason("payment_required"), "deactivated_workspace")
        self.assertEqual(disable_reason("inactive_owner"), "inactive_token_owner")
        self.assertIsNone(disable_reason("forbidden_other"))
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_account_test_outcomes`

Expected: import failure because `account_test_outcomes` does not exist.

- [ ] **Step 3: Implement the pure classifier**

Create constants and functions with no database or HTTP dependencies:

```python
MODEL_NOT_SUPPORTED_TEXT = "model is not supported when using codex with a chatgpt account"
CONFIRMED_DISABLE_REASONS = {
    "unauthorized": "token_invalidated",
    "payment_required": "deactivated_workspace",
    "inactive_owner": "inactive_token_owner",
}

def classify_test_result(verification: dict[str, Any] | None = None, *, transport_error: str | None = None) -> str:
    verification = verification or {}
    if transport_error:
        return "transport_error"
    error = str(verification.get("error") or "").strip().lower()
    if MODEL_NOT_SUPPORTED_TEXT in error:
        return "model_not_supported"
    if _has_http_status(error, 401):
        return "unauthorized"
    if _has_http_status(error, 402):
        return "payment_required"
    if _has_http_status(error, 403):
        if "personal access token owner is inactive" in error or "biscuit_baker_service_auth_credential_error_status" in error:
            return "inactive_owner"
        return "forbidden_other"
    if verification.get("success") is True:
        return "passed"
    if _has_http_status(error, 429):
        return "rate_limited"
    return "failed"

def disable_reason(outcome: str) -> str | None:
    return CONFIRMED_DISABLE_REASONS.get(outcome)
```

- [ ] **Step 4: Run tests and verify GREEN**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_account_test_outcomes`

Expected: all outcome tests pass.

- [ ] **Step 5: Commit Task 1**

```bash
git add backend/app/modules/sub2api/account_test_outcomes.py backend/tests/test_account_test_outcomes.py
git commit -m "feat: standardize sub2api account test outcomes"
```

### Task 2: Durable Test Execution and Storage

**Files:**
- Create: `backend/app/modules/sub2api/account_test_service.py`
- Create: `backend/tests/test_account_test_service.py`

- [ ] **Step 1: Write failing service-order and persistence tests**

Use fake collections that append `event_inserted`, `state_updated`, `cache_updated`, and `dispatched` to a shared list. Assert the first dispatch occurs after event and state writes. Assert the request is exactly `gpt-5.4`, empty prompt, default mode and secrets are absent from stored documents.

```python
result = await execute_account_test(
    db,
    site=site,
    account=account,
    client=client,
    dispatcher=dispatcher,
    now=fixed_now,
)
self.assertEqual(client.test_account.await_args.kwargs, {"model_id": "gpt-5.4", "prompt": "", "mode": "default"})
self.assertLess(order.index("event_inserted"), order.index("dispatched"))
self.assertLess(order.index("state_updated"), order.index("dispatched"))
self.assertEqual(result["outcome"], "passed")
self.assertNotIn("credentials", stored_event)
```

Also test that `InvalidAdminApiKeyError` is re-raised without inserting an account event, while ordinary request exceptions save `transport_error` and a 24-hour `next_test_at`.

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_account_test_service`

Expected: import failure because the service does not exist.

- [ ] **Step 3: Implement event/state writes and dispatch ordering**

Implement:

```python
TEST_MODEL = "gpt-5.4"
TEST_INTERVAL = timedelta(hours=24)
EVENT_RETENTION = timedelta(days=90)

async def execute_account_test(db, *, site, account, client, dispatcher=dispatch_test_event, now=None):
    tested_at = _as_utc(now or now_utc())
    verification = await _test_remote(client, _remote_id(account))
    outcome = classify_test_result(verification)
    event = _event_document(site, account, verification, outcome, tested_at)
    await db.sub2api_account_test_events.insert_one(event)
    await db.sub2api_account_test_states.update_one(
        {"_id": event["state_id"]},
        {"$set": _latest_state(event), "$setOnInsert": {"created_at": tested_at}},
        upsert=True,
    )
    await _sync_cache_test_fields(db, event)
    await dispatcher(db, event["_id"])
    return event
```

Generate an event UUID once per execution; bound `response_preview` and `error` lengths; normalize email; initialize both handler states as pending; store `expires_at=tested_at+90d` and `next_test_at=tested_at+24h`.

Add `repair_latest_states_from_events(db, limit=100)` that finds recent events whose state is missing or points to an older event and idempotently rebuilds the latest state without calling the remote test endpoint.

- [ ] **Step 4: Run service tests and verify GREEN**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_account_test_service`

Expected: all service tests pass.

- [ ] **Step 5: Commit Task 2**

```bash
git add backend/app/modules/sub2api/account_test_service.py backend/tests/test_account_test_service.py
git commit -m "feat: persist unified account test results"
```

### Task 3: Durable Dispatcher and Judgment Handlers

**Files:**
- Create: `backend/app/modules/sub2api/account_test_dispatcher.py`
- Create: `backend/tests/test_account_test_dispatcher.py`

- [ ] **Step 1: Write failing scheduling-handler tests**

Assert `passed` re-enables a disabled account, confirmed 401/402/inactive-owner outcomes disable an enabled account, and 429/unrelated 403 do not call `set_account_schedulable`. Assert remote updates only change schedulable and never include name/group fields.

```python
await handle_scheduling(db, event, site=site, client=client)
client.set_account_schedulable.assert_awaited_once_with(4072, True)
self.assertFalse(any(hasattr(call, "name") for call in client.method_calls if call[0] == "update_account"))
```

- [ ] **Step 2: Write failing plan-correction and replay tests**

Assert a complete misreported-Free signature receives `verified_plan_type=plus` only for passed/429; model-not-supported clears it; other failures preserve it. Assert dispatcher loads the saved event by ID, marks each handler processing/completed, retries failed handlers, and a completed handler is not called twice.

- [ ] **Step 3: Run tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_account_test_dispatcher`

Expected: import failure because dispatcher does not exist.

- [ ] **Step 4: Implement registry, claims and handlers**

Use an explicit registry:

```python
HANDLERS = {
    "scheduling": handle_scheduling,
    "plan_correction": handle_plan_correction,
}

async def dispatch_test_event(db, event_id: str) -> dict[str, Any]:
    event = await db.sub2api_account_test_events.find_one({"_id": event_id})
    for name, handler in HANDLERS.items():
        if not await _claim_handler(db, event_id, name):
            continue
        try:
            await handler(db, event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _finish_handler(db, event_id, name, status="failed", error=str(exc))
        else:
            await _finish_handler(db, event_id, name, status="completed")
```

`handle_scheduling` obtains site credentials and client lazily, reads current cache schedulable state, performs only necessary idempotent remote changes and syncs returned state to cache. `handle_plan_correction` re-reads the cached raw account signature and updates only latest test state verification fields.

Implement `replay_pending_dispatches(db, limit=100)` over pending and due failed handler paths.

- [ ] **Step 5: Run dispatcher tests and verify GREEN**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_account_test_dispatcher`

Expected: all handler and replay tests pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add backend/app/modules/sub2api/account_test_dispatcher.py backend/tests/test_account_test_dispatcher.py
git commit -m "feat: dispatch persisted account test judgments"
```

### Task 4: Global 24-Hour Serial Scheduler

**Files:**
- Create: `backend/app/modules/sub2api/account_test_scheduler.py`
- Create: `backend/tests/test_account_test_scheduler.py`
- Create: `backend/tests/test_account_test_bootstrap.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_storage_bootstrap.py` or the existing bootstrap-index test file

- [ ] **Step 1: Write failing due-selection and serial-run tests**

Provide active sites with never-tested, expired and recent accounts including `schedulable=false`. Assert order is never-tested first then oldest due, recent state is skipped, disabled accounts remain eligible, and max active requests is one across sites.

```python
self.assertEqual([item["sub2api_account_id"] for item in due], [10, 12])
self.assertIn(12, tested_ids)  # schedulable=false account
self.assertEqual(max_active_requests, 1)
```

Test lease denial skips a second worker and admin-key failure records site backoff without an account event.

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_account_test_scheduler`

Expected: import failure because scheduler does not exist.

- [ ] **Step 3: Implement lease, due selection and loop**

Implement a single scheduler cycle that acquires `operation_locks._id=unified-account-test-scheduler`, first repairs latest states from saved events and replays pending dispatches, then iterates active Sub2API sites and picks one globally oldest due account. Prefer PostgreSQL admin key when SQL DSN exists, fallback to configured token, call `execute_account_test`, then release/renew the lease safely.

```python
async def account_test_scheduler_loop(db):
    while True:
        try:
            result = await run_account_test_cycle(db)
            await asyncio.sleep(1 if result.get("tested") else 30)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sub2api_account_test_scheduler_failed")
            await asyncio.sleep(30)
```

- [ ] **Step 4: Add indexes and switch startup task**

Create indexes from the approved design in `ensure_indexes`. Replace `long_7d_probe_scheduler_loop` import/task in `main.py` with `account_test_scheduler_loop`; leave legacy module and collections intact. Add the unified task to shutdown cancellation.

- [ ] **Step 5: Run scheduler/bootstrap/startup tests and verify GREEN**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_account_test_scheduler tests.test_account_test_bootstrap`

Expected: scheduler and index tests pass.

- [ ] **Step 6: Commit Task 4**

```bash
git add backend/app/modules/sub2api/account_test_scheduler.py backend/tests/test_account_test_scheduler.py backend/tests/test_account_test_bootstrap.py backend/app/modules/system/bootstrap.py backend/app/main.py
git commit -m "feat: test every sub2api account daily"
```

### Task 5: Persisted Plus Verification in Cache and Probe Paths

**Files:**
- Modify: `backend/app/modules/sub2api/cache.py`
- Modify: `backend/app/modules/sub2api/account_probe.py`
- Modify: `backend/tests/test_capacity_limits.py`
- Modify: `backend/tests/test_account_probe.py`

- [ ] **Step 1: Replace immediate-signature expectations with failing verification-gate tests**

Change the existing sub-bundle tests so the raw signature remains Free without persisted verification. Add tests that batch annotation `verified_plan_type=plus` changes cache and probe normalization to Plus, while model-not-supported/absent verification remains Free and valid historical K12/Pro still wins.

```python
raw = misreported_free_account()
self.assertEqual(cache._capacity_account_type(cache._normalize_account_snapshot(raw)), "free")
verified = cache._account_with_verified_plan_type(raw, {"verified_plan_type": "plus"})
self.assertEqual(cache._capacity_account_type(cache._normalize_account_snapshot(verified)), "plus")
```

- [ ] **Step 2: Run focused tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_capacity_limits tests.test_account_probe`

Expected: old immediate correction behavior fails the new gate assertions.

- [ ] **Step 3: Implement batch verification enrichment**

Keep `_plan_type_from_plus_bundle_signature` as candidate recognition, but remove its direct override from `_normalize_account_snapshot`. Add an explicit persisted field such as `codex_verified_plan_type` with source `account_test` and apply it before remote misreported Free. During site refresh, batch-read `sub2api_account_test_states` for current IDs and annotate raw accounts before normalization. During account probe, batch-read the same states once per site and annotate normalized probe accounts before identity updates.

Only a saved `verified_plan_type=plus` may override raw Free. Explicit non-Free remote plans and effective non-fallback history remain higher priority.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_capacity_limits tests.test_account_probe`

Expected: all cache/probe type tests pass.

- [ ] **Step 5: Commit Task 5**

```bash
git add backend/app/modules/sub2api/cache.py backend/app/modules/sub2api/account_probe.py backend/tests/test_capacity_limits.py backend/tests/test_account_probe.py
git commit -m "fix: gate Plus corrections on persisted tests"
```

### Task 6: Documentation and Full Regression Verification

**Files:**
- Modify: `docs/design/15-api-pool-status-cache.md`
- Test: all backend tests

- [ ] **Step 1: Update maintained design documentation**

Document that the sub-bundle signature creates a test candidate only; persisted passed/429 `gpt-5.4` verification creates the local Plus override. Document the daily global serial test, successful scheduling recovery, confirmed-failure disabling and the fact that 5002 remains separate.

- [ ] **Step 2: Run diff and syntax checks**

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 3: Run the complete backend suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`

Expected: all tests pass, including unchanged 5002 tests.

- [ ] **Step 4: Inspect final changes**

Run: `git status --short` and `git diff --stat`.

Expected: only the planned backend modules, tests and maintained documentation are changed.

- [ ] **Step 5: Commit final integration**

```bash
git add backend docs/design/15-api-pool-status-cache.md docs/superpowers/plans/2026-07-22-unified-account-test-foundation.md
git commit -m "feat: add unified sub2api account testing foundation"
```
