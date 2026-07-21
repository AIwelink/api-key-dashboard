# Plus Self-Produced Account Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically test `US06-5002` group-4 accounts every 15 minutes, promote successful/429 accounts to group 6 with an idempotent Plus prefix, route 401 accounts unchanged to group 7, and expose controls and results on a new `plus自产` page.

**Architecture:** A focused Sub2API domain module owns classification, settings, serial execution, persistence, and scheduling. A small authenticated router exposes status/results/settings/manual-run operations. A dedicated React page consumes those endpoints and is inserted directly below API pool status in the existing SPA navigation.

**Tech Stack:** Python 3.12, FastAPI, Motor/MongoDB, unittest, React 19, TypeScript, Vite, Vitest.

---

## File Structure

- Create `backend/app/modules/sub2api/plus_self_produced.py`: workflow constants, classifier, name normalization, settings, run execution, result listing, and scheduler.
- Create `backend/app/routers/plus_self_produced.py`: authenticated HTTP contract and audit logging.
- Create `backend/tests/test_plus_self_produced.py`: pure rules, settings, serial execution, remote payloads, scheduler due checks, and indexes.
- Create `backend/tests/test_plus_self_produced_routes.py`: router service delegation and conflict behavior.
- Modify `backend/app/schemas.py`: settings patch validation.
- Modify `backend/app/modules/system/bootstrap.py`: workflow result/run indexes.
- Modify `backend/app/main.py`: router and lifespan scheduler task.
- Create `frontend/src/pages/PlusSelfProducedPage.tsx`: status/settings/run/results operational page.
- Create `frontend/src/pages/PlusSelfProducedPage.test.tsx`: static render coverage for workflow states and classifications.
- Modify `frontend/src/types.ts`: add the new view key.
- Modify `frontend/src/App.tsx`: navigation label, URL path, and page rendering.
- Modify `frontend/src/App.test.ts`: navigation order and path coverage.
- Modify `frontend/styles.css`: scoped responsive layout for the new page.

### Task 1: Pure Probe Decisions

**Files:**
- Create: `backend/tests/test_plus_self_produced.py`
- Create: `backend/app/modules/sub2api/plus_self_produced.py`

- [ ] **Step 1: Write failing classifier and name tests**

Cover this exact contract:

```python
self.assertEqual(classify_probe_result({"success": True}), "passed")
self.assertEqual(classify_probe_result({"success": False, "error": "API returned 429"}), "rate_limited_but_eligible")
self.assertEqual(classify_probe_result({"success": False, "error": "API returned 401"}), "unauthorized_banned")
self.assertEqual(
    classify_probe_result({
        "success": False,
        "error": "API returned 400: The 'gpt-5.6-sol' model is not supported when using Codex with a ChatGPT account.",
    }),
    "model_not_supported",
)
self.assertEqual(plus_account_name("user@example.com"), "plus user@example.com")
self.assertEqual(plus_account_name("plus user@example.com"), "plus user@example.com")
self.assertEqual(plus_account_name("plususer@example.com"), "plususer@example.com")
self.assertEqual(plus_account_name("PLUS user@example.com"), "PLUS user@example.com")
```

Also assert that the unsupported-model phrase wins when another success indicator is present.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python -m unittest tests.test_plus_self_produced.PlusProbeDecisionTests -v` from `backend`.

Expected: import failure because `app.modules.sub2api.plus_self_produced` does not exist.

- [ ] **Step 3: Implement the minimum pure rules**

Define constants for site/group/model/default interval and these interfaces:

```python
SITE_ID = "US06-5002"
SOURCE_GROUP_ID = 4
PLUS_GROUP_ID = 6
BANNED_GROUP_ID = 7
PROBE_MODEL = "gpt-5.4"
DEFAULT_INTERVAL_SECONDS = 15 * 60

def classify_probe_result(verification: dict[str, Any] | None = None, *, error: str | None = None) -> str: ...
def plus_account_name(name: Any) -> str: ...
```

Normalize combined verification/exception error text, check the unsupported-model phrase first, detect HTTP 401/429 with bounded status-code patterns, then use `success is True`. Preserve names whose lowercase form starts with `plus`; otherwise prepend `plus `.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same unittest command. Expected: all decision tests pass.

### Task 2: Settings, Serial Run, and Remote Moves

**Files:**
- Modify: `backend/tests/test_plus_self_produced.py`
- Modify: `backend/app/modules/sub2api/plus_self_produced.py`

- [ ] **Step 1: Write failing settings and execution tests**

Use lightweight async collection doubles, patched PostgreSQL pool snapshot/Admin Key reads, and a patched `Sub2ApiClient`. PostgreSQL supplies groups/accounts and `settings.admin_api_key`; the client supplies only model-test and account-update operations. Assert:

```python
settings = await get_settings(db)
self.assertTrue(settings["enabled"])
self.assertEqual(settings["interval_minutes"], 15)
```

For a live group-4 list containing pass, 429, 401, and unsupported-model accounts, verify calls are awaited serially and update payloads are exactly:

```python
{"name": "plus user@example.com", "group_id": 6, "group_ids": [6], "status": "active", "schedulable": True}
{"group_id": 7, "group_ids": [7], "status": "active", "schedulable": True}
```

The 401 payload must omit `name`. Unsupported and unrelated failures must not call `update_account`. A failed group move must be persisted as `promotion_failed` or `ban_move_failed` and counted as failed, while later accounts continue.

- [ ] **Step 2: Run execution tests and verify RED**

Run: `python -m unittest tests.test_plus_self_produced.PlusSelfProducedRunTests -v`.

Expected: missing workflow settings/run functions.

- [ ] **Step 3: Implement settings and run execution**

Add:

```python
async def get_settings(db: AsyncIOMotorDatabase) -> dict[str, Any]: ...
async def update_settings(db, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]: ...
async def run_probe(db, *, trigger: str) -> dict[str, Any]: ...
async def get_status(db) -> dict[str, Any]: ...
async def list_results(db, *, page: int, page_size: int, classification: str | None) -> dict[str, Any]: ...
```

Use one module-level `asyncio.Lock`. Before tests, load the active site's Base URL and SQL_DSN, read the Admin Key plus pool snapshot from PostgreSQL, require group IDs 4/6/7, then select all group-4 accounts. Await every test and update inside a normal `for` loop. Merge the update response with the original account and force the expected name/group fields before calling `upsert_cached_account_snapshot`.

Persist one run summary and latest per-account results without credentials or raw SSE events. Final counters include `candidates`, `tested`, `eligible`, `promoted`, `banned`, and `failed`.

- [ ] **Step 4: Run execution tests and verify GREEN**

Run the same unittest command. Expected: settings and execution tests pass.

- [ ] **Step 5: Refactor with tests green**

Extract `_test_account`, `_move_account`, `_write_account_result`, and `_finish_run` only where this keeps the run loop readable. Re-run the complete `test_plus_self_produced.py` file.

### Task 3: Scheduler, Indexes, and Router

**Files:**
- Modify: `backend/tests/test_plus_self_produced.py`
- Create: `backend/tests/test_plus_self_produced_routes.py`
- Modify: `backend/app/modules/sub2api/plus_self_produced.py`
- Create: `backend/app/routers/plus_self_produced.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Modify: `backend/app/main.py`

- [ ] **Step 1: Write failing scheduler, index, schema, and route tests**

Assert a missing setting is immediately due, a recent `last_finished_at` is not due until its interval elapses, disabled settings never run, and a locked run reports conflict. Validate `interval_minutes` accepts 1..1440 and rejects values outside that range.

Assert indexes:

```python
await db.plus_self_produced_runs.create_index([("started_at", -1)])
await db.plus_self_produced_account_results.create_index(
    [("site_id", 1), ("remote_account_id", 1)], unique=True
)
await db.plus_self_produced_account_results.create_index([("tested_at", -1)])
```

Route tests directly call status/results/settings/run handlers with patched services and verify audit calls for mutations.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python -m unittest tests.test_plus_self_produced tests.test_plus_self_produced_routes -v`.

Expected: missing schema/router/scheduler/index functions.

- [ ] **Step 3: Implement scheduler and HTTP contract**

Add `PlusSelfProducedSettingsUpdate` to schemas. Add `run_due_probe` and `scheduler_loop` with a short poll sleep and cancellation propagation. Register the router at `/api/plus-self-produced` with owner/admin/maintainer authorization:

```text
GET    /status
GET    /results?page=1&page_size=50&classification=
PATCH  /settings
POST   /run
```

Return HTTP 409 when the in-process run lock is active. Register indexes, router, and the lifespan scheduler task/cancellation in `main.py`.

- [ ] **Step 4: Run focused backend tests and verify GREEN**

Run the same two-module unittest command. Expected: all tests pass.

### Task 4: Navigation Contract

**Files:**
- Modify: `frontend/src/App.test.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Write the failing navigation test**

Update expected first navigation group to:

```typescript
expect(groups[0].map(([key]) => key)).toEqual(["api-pools", "plus-self-produced"]);
expect(viewFromPath("/plus-self-produced")).toBe("plus-self-produced");
```

- [ ] **Step 2: Run and verify RED**

Run: `npm test -- App.test.ts` from `frontend`.

Expected: the view key/path is absent.

- [ ] **Step 3: Add the view and navigation entry**

Add `plus-self-produced` to `ViewName`, directly after `api-pools` in `poolNavItems`, with label `plus自产`, short label `产`, and path `/plus-self-produced`. Page import/render wiring follows Task 5 after the page module exists.

- [ ] **Step 4: Run and verify GREEN**

Run the same Vitest command. Expected: navigation tests pass.

### Task 5: Operational Page

**Files:**
- Create: `frontend/src/pages/PlusSelfProducedPage.test.tsx`
- Create: `frontend/src/pages/PlusSelfProducedPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/styles.css`

- [ ] **Step 1: Write failing static-render tests**

Render an exported presentational `PlusSelfProducedView` with fixture status/results. Assert the markup includes `US06-5002`, `4 → 6`, `4 → 7`, `gpt-5.4`, `15 分钟`, and labels for `已晋级`, `已转封禁`, `429 可用`, `模型不支持`, and `失败`.

- [ ] **Step 2: Run and verify RED**

Run: `npm test -- PlusSelfProducedPage.test.tsx`.

Expected: missing page module.

- [ ] **Step 3: Implement page data flow and view**

The container loads `/plus-self-produced/status` and `/plus-self-produced/results` together, saves `{enabled, interval_minutes}`, and POSTs `/plus-self-produced/run`. Use `usePageAutoRefresh` for passive refresh while not saving/running. Import and render `PlusSelfProducedPage` from the matching `App.tsx` view branch.

The view uses existing `topbar`, `panel`, `switch-field`, `status-pill`, and table conventions. Add only scoped `.plus-self-produced-*` styles for a stable settings row, metric grid, classification colors, ellipsized errors, and responsive table overflow. Keep controls full-width on narrow screens and ensure labels wrap.

- [ ] **Step 4: Run and verify GREEN**

Run the page test and `npm test`. Expected: all frontend tests pass.

### Task 6: Verification

**Files:**
- Verify all modified files.

- [ ] **Step 1: Run backend tests**

Run: `python -m unittest discover -s tests -v` from `backend`.

Expected: all tests pass.

- [ ] **Step 2: Run frontend tests and build**

Run: `npm test` and `npm run build` from `frontend`.

Expected: all tests pass and Vite production build completes without TypeScript errors.

- [ ] **Step 3: Start the frontend and inspect desktop/mobile**

Start Vite on an available localhost port. Use browser screenshots at desktop and mobile widths to verify the page renders, controls do not overlap, long account/error text stays contained, and the results table scrolls horizontally when required.

- [ ] **Step 4: Review the final diff**

Run `git diff --check` and `git status --short`. Confirm no credentials, unrelated refactors, generated screenshots, or build output are included.
