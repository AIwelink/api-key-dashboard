# Plus Routing Groups Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all four US06-5002 Plus workflow group roles configurable one-to-one and clear every candidate account's model mapping before each probe.

**Architecture:** Extend the existing Plus workflow rather than introducing a generic rule engine. PostgreSQL remains authoritative for group options and probe snapshots, MongoDB stores the selected role IDs, and each run takes one immutable settings snapshot before applying the existing state machine. The React page loads PostgreSQL group options and submits all four role selections atomically.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, SQLAlchemy async PostgreSQL, Motor/MongoDB, httpx, React 19, TypeScript, Vitest, unittest

---

## File Structure

- `backend/app/modules/sub2api/postgres_repository.py`: add a focused PostgreSQL groups-only reader.
- `backend/app/modules/sub2api/plus_self_produced.py`: own effective settings, group validation, group options, dynamic routing, and pre-probe model reset.
- `backend/app/routers/plus_self_produced.py`: expose the authenticated groups endpoint.
- `backend/app/schemas.py`: accept four positive group IDs.
- `backend/tests/test_plus_self_produced.py`: cover settings, group validation, dynamic routing, and model reset behavior.
- `backend/tests/test_plus_self_produced_routes.py`: cover schema and router contracts.
- `backend/tests/test_sub2api_postgres_repository.py`: cover the groups-only SQL reader.
- `frontend/src/pages/PlusSelfProducedPage.tsx`: load group options, manage four group selections, validate one-to-one selection, and save the complete form.
- `frontend/src/pages/PlusSelfProducedPage.test.tsx`: cover select rendering, proposed routes, and duplicate blocking.
- `frontend/styles.css`: fit the four select controls into the existing operational settings band.

### Task 1: PostgreSQL Group Options

**Files:**
- Modify: `backend/app/modules/sub2api/postgres_repository.py`
- Modify: `backend/tests/test_sub2api_postgres_repository.py`
- Modify: `backend/app/modules/sub2api/plus_self_produced.py`
- Modify: `backend/app/routers/plus_self_produced.py`
- Modify: `backend/tests/test_plus_self_produced_routes.py`

- [ ] **Step 1: Write failing repository and route tests**

Add a repository test that supplies a fake async engine and asserts `fetch_groups()` returns normalized group rows without issuing account queries. Add a route test that patches `list_groups` and asserts `get_plus_self_produced_groups()` delegates with the database dependency.

```python
async def test_fetch_groups_reads_only_postgresql_groups(self) -> None:
    groups = await fetch_groups("postgresql://reader:secret@db/sub2api", engine_factory=engine_factory)
    self.assertEqual(groups, [{"id": 4, "name": "plus自产", "status": "active"}])
    connection.execute.assert_awaited_once()

async def test_groups_delegate_to_postgresql_service(self) -> None:
    with patch.object(router, "list_groups", AsyncMock(return_value=[{"id": 4, "name": "plus自产"}])):
        result = await router.get_plus_self_produced_groups(_={}, db=object())
    self.assertEqual(result, [{"id": 4, "name": "plus自产"}])
```

- [ ] **Step 2: Run tests to verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_sub2api_postgres_repository tests.test_plus_self_produced_routes -v`

Working directory: `backend`

Expected: FAIL because `fetch_groups`, `list_groups`, and the groups route do not exist.

- [ ] **Step 3: Implement the groups-only reader and service**

Add `fetch_groups()` using the existing DSN parsing, timeout, normalization, SQL redaction, and engine disposal conventions:

```python
async def fetch_groups(sql_dsn: str, *, engine_factory=create_async_engine) -> list[dict[str, Any]]:
    parsed = parse_sql_dsn(sql_dsn, "postgresql")
    engine = engine_factory(parsed.driver_url(), poolclass=NullPool, connect_args=parsed.connect_args(DATABASE_READ_TIMEOUT_SECONDS))
    try:
        async with asyncio.timeout(DATABASE_READ_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                result = await connection.execute(text(GROUPS_QUERY))
                return [_normalize_row(row) for row in result.mappings().all()]
    finally:
        await engine.dispose()
```

Add a Plus service that loads US06-5002, requires `sql_dsn`, calls the PostgreSQL reader, sanitizes connection errors, and returns `{id, name, status}` options sorted by the repository query. Expose it as `GET /plus-self-produced/groups`.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_sub2api_postgres_repository tests.test_plus_self_produced_routes -v`

Working directory: `backend`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/modules/sub2api/postgres_repository.py backend/app/modules/sub2api/plus_self_produced.py backend/app/routers/plus_self_produced.py backend/tests/test_sub2api_postgres_repository.py backend/tests/test_plus_self_produced_routes.py
git commit -m "Add PostgreSQL Plus group options"
```

### Task 2: Persist and Validate Four Group Roles

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/modules/sub2api/plus_self_produced.py`
- Modify: `backend/tests/test_plus_self_produced.py`
- Modify: `backend/tests/test_plus_self_produced_routes.py`

- [ ] **Step 1: Write failing schema and settings tests**

Replace the fixed-target settings test with coverage for stored custom IDs, complete persistence, positive values, effective partial updates, duplicate rejection, and missing PostgreSQL groups.

```python
payload = PlusSelfProducedSettingsUpdate(
    source_group_id=14,
    plus_group_id=16,
    banned_group_id=17,
    plus_error_group_id=19,
)
self.assertEqual(payload.source_group_id, 14)

with self.assertRaises(ValidationError):
    PlusSelfProducedSettingsUpdate(source_group_id=0)

with self.assertRaisesRegex(HTTPException, "distinct"):
    await update_settings(db, {"source_group_id": 6}, actor)
```

Mock the PostgreSQL groups reader with IDs `14/16/17/19` and assert the `$set` payload stores all selected IDs. Assert a partial interval-only update validates the effective stored IDs rather than reverting them to defaults.

- [ ] **Step 2: Run tests to verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_plus_self_produced tests.test_plus_self_produced_routes -v`

Working directory: `backend`

Expected: FAIL because custom IDs are overwritten by constants and the schema ignores group fields.

- [ ] **Step 3: Implement effective settings and validation**

Add positive optional fields to `PlusSelfProducedSettingsUpdate`:

```python
source_group_id: int | None = Field(default=None, ge=1)
plus_group_id: int | None = Field(default=None, ge=1)
banned_group_id: int | None = Field(default=None, ge=1)
plus_error_group_id: int | None = Field(default=None, ge=1)
```

Use defaults only when a stored field is missing. Implement a single helper returning the four effective IDs and reject `len(set(ids)) != 4`. In `update_settings`, merge supplied fields over current settings, validate distinctness, fetch PostgreSQL group IDs, reject missing selections with HTTP 422, and only then persist the supplied fields. Make `get_status()` return the IDs from effective settings.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_plus_self_produced tests.test_plus_self_produced_routes -v`

Working directory: `backend`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/schemas.py backend/app/modules/sub2api/plus_self_produced.py backend/tests/test_plus_self_produced.py backend/tests/test_plus_self_produced_routes.py
git commit -m "Make Plus routing groups configurable"
```

### Task 3: Dynamic Routing and Pre-Probe Model Reset

**Files:**
- Modify: `backend/app/modules/sub2api/plus_self_produced.py`
- Modify: `backend/tests/test_plus_self_produced.py`
- Modify: `backend/tests/test_sub2api_client_update.py`

- [ ] **Step 1: Write failing dynamic-route and model-reset tests**

Use settings `14/16/17/19` and a PostgreSQL snapshot containing those groups. Assert only accounts in `14` and `16` are candidates and all resulting moves use configured IDs.

Before every `test_account` call, assert the preceding update is:

```python
await client.update_account(account_id, {"credentials": {"model_mapping": {}}})
```

Cover source success, source 401, Plus unsupported-model 400, Plus 401, and unchanged failures. Add a test where the first reset raises `RuntimeError("reset failed")`: the first account is not tested or moved, its result is `model_reset_failed`, and the second account continues.

Add a client payload test proving an existing non-empty mapping becomes `{}` while all other credentials remain present in the PUT body.

- [ ] **Step 2: Run tests to verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_plus_self_produced tests.test_sub2api_client_update -v`

Working directory: `backend`

Expected: FAIL because the run still uses constants and does not reset model mappings.

- [ ] **Step 3: Implement one settings snapshot and reset-before-test**

At the beginning of `_run_probe_locked()`, call `get_settings()` once and bind local role IDs. Validate all four are distinct and present in the pool snapshot. Use those locals for candidate selection, source-role detection, and every destination payload.

Inside the serial account loop, perform the reset before incrementing `tested`:

```python
try:
    await client.update_account(remote_account_id, {"credentials": {"model_mapping": {}}})
except InvalidAdminApiKeyError:
    raise
except Exception as exc:
    counters["failed"] += 1
    await _write_account_result(
        db,
        run_id=run_id,
        account=account,
        verification={"model": PROBE_MODEL, "latency_ms": None},
        classification="failed",
        action_status="model_reset_failed",
        error=_exception_error(exc),
        resulting_name=_account_name(account),
        destination_group_id=None,
        source_group_id=source_group_id,
        tested_at=now_utc(),
    )
    continue

verification = await _test_account(client, remote_account_id)
counters["tested"] += 1
```

The existing credential deep merge already replaces a dictionary value at the `model_mapping` key, so the resulting full PUT retains other current credentials while setting the mapping to `{}`. Preserve the reset response only as transient remote state; do not count it as a group action or cache it as a completed classification.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_plus_self_produced tests.test_sub2api_client_update -v`

Working directory: `backend`

Expected: PASS with reset calls preceding every model test.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/modules/sub2api/plus_self_produced.py backend/tests/test_plus_self_produced.py backend/tests/test_sub2api_client_update.py
git commit -m "Reset model mappings before Plus probes"
```

### Task 4: Four One-to-One Group Selects

**Files:**
- Modify: `frontend/src/pages/PlusSelfProducedPage.tsx`
- Modify: `frontend/src/pages/PlusSelfProducedPage.test.tsx`
- Modify: `frontend/styles.css`

- [ ] **Step 1: Write failing frontend tests**

Add settings IDs to the status fixture and pass group options plus selection callbacks to `PlusSelfProducedView`. Assert four labeled selects render `14 · plus自产`, the workflow facts use the current form IDs, and duplicate selections show a one-to-one validation message while disabling Save.

```tsx
const groups = [
  { id: 14, name: "plus自产", status: "active" },
  { id: 16, name: "plus 正常号池", status: "active" },
  { id: 17, name: "封禁账号池", status: "active" },
  { id: 19, name: "plus 错误池", status: "active" },
];
expect(html).toContain("14 · plus自产");
expect(html).toContain("四个分组必须一对一");
```

- [ ] **Step 2: Run the page test to verify RED**

Run: `npx.cmd vitest run --configLoader runner src/pages/PlusSelfProducedPage.test.tsx`

Working directory: `frontend`

Expected: FAIL because the view has no group options or selects.

- [ ] **Step 3: Implement group loading, form state, validation, and layout**

Add `PlusGroupOption`, the four settings fields, four state values, and a `/plus-self-produced/groups` request to initial load. Export and test a pure `buildSettingsPayload()` helper, then use it to save this complete body:

```ts
JSON.stringify({
  enabled,
  interval_minutes: intervalMinutes,
  source_group_id: sourceGroupId,
  plus_group_id: plusGroupId,
  banned_group_id: bannedGroupId,
  plus_error_group_id: plusErrorGroupId,
})
```

Compute `groupsAreDistinct` from a four-item `Set`; disable Save and show `四个分组必须一对一，不能重复` when false. Render stable select controls with options formatted as `${id} · ${name}`. Use current form selections in the four workflow facts. Extend `frontend/styles.css` with a responsive four-column settings grid that collapses without nested cards or overflow.

- [ ] **Step 4: Run frontend tests and build to verify GREEN**

Run: `npx.cmd vitest run --configLoader runner src/pages/PlusSelfProducedPage.test.tsx`

Run: `npm.cmd run build -- --configLoader runner`

Working directory: `frontend`

Expected: both commands exit 0.

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/pages/PlusSelfProducedPage.tsx frontend/src/pages/PlusSelfProducedPage.test.tsx frontend/styles.css
git commit -m "Add configurable Plus routing controls"
```

### Task 5: Full Regression Verification

**Files:**
- Verify all files changed above.

- [ ] **Step 1: Run the full backend suite**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests`

Working directory: `backend`

Expected: all tests pass with zero failures and zero errors.

- [ ] **Step 2: Run the full frontend suite**

Run: `npx.cmd vitest run --configLoader runner`

Working directory: `frontend`

Expected: all tests pass with zero failures.

- [ ] **Step 3: Run the production build**

Run: `npm.cmd run build -- --configLoader runner`

Working directory: `frontend`

Expected: TypeScript and Vite exit 0.

- [ ] **Step 4: Check the final diff**

Run: `git diff --check`

Run: `git status --short`

Expected: no whitespace errors; only intentional implementation and plan changes are present.

- [ ] **Step 5: Confirm safety boundaries**

Review the diff and confirm no command or test called the live `/plus-self-produced/run` endpoint, no real Sub2API account was mutated, and no Admin API Key or SQL DSN was logged or committed.
