# Quota-Aware Smart Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in, group-gated smart scheduling that reuses each 60-second PostgreSQL account-probe snapshot to normalize account priority/concurrency and accelerate accounts near seven-day quota exhaustion.

**Architecture:** Keep scheduling decisions pure in `smart_scheduling.py`, persistence and remote mutations in `smart_scheduling_service.py`, and call the service once from the existing account probe. Store one normalized rules document per site, keep only two booleans per group, and update remote accounts through the Admin API after re-reading candidate accounts.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, Motor/PyMongo, unittest, React 19, TypeScript, Vite, Vitest.

---

## File Structure

- Create `backend/app/modules/sub2api/smart_scheduling.py`: defaults, normalization, validation helpers, adapted-type mapping, pure account decision function.
- Create `backend/app/modules/sub2api/smart_scheduling_service.py`: settings persistence, lease, run/outcome/state persistence, Admin API updates.
- Create `backend/tests/test_smart_scheduling.py`: pure rule and decision coverage.
- Create `backend/tests/test_smart_scheduling_service.py`: Mongo persistence, lease, deduplication, revalidation, no-op, and failure-isolation coverage.
- Create `backend/tests/test_smart_scheduling_routes.py`: request-schema, settings-route, audit, group-default, and index coverage.
- Modify `backend/app/schemas.py`: typed site rules and two group flags.
- Modify `backend/app/routers/api_pools.py`: settings GET/PATCH endpoints.
- Modify `backend/app/modules/sub2api/account_probe.py`: group defaults, due logic, normalized runtime fields, and one call into the scheduling service.
- Modify `backend/app/modules/system/bootstrap.py`: state/run/outcome/lease indexes.
- Modify `backend/tests/test_account_probe.py`: snapshot reuse, due-group behavior, and probe-result integration tests.
- Modify `backend/tests/test_group_observability_settings.py`: default-off and persistence tests for both flags.
- Modify `frontend/src/pages/AccountPoolsPage.tsx`: settings types/state/API flow, rule editor, group switches, and last-run counters.
- Create `frontend/src/pages/smartScheduling.ts`: typed form conversion and client-side validation helpers.
- Create `frontend/src/pages/smartScheduling.test.ts`: helper and payload tests.
- Modify `frontend/styles.css`: stable responsive rule grid, status strip, and group strategy controls.

### Task 1: Pure Rules and Decision Engine

**Files:**
- Create: `backend/app/modules/sub2api/smart_scheduling.py`
- Create: `backend/tests/test_smart_scheduling.py`

- [ ] **Step 1: Write failing default and validation tests**

```python
class SmartSchedulingDefaultsTests(unittest.TestCase):
    def test_defaults_match_confirmed_priority_and_concurrency_rules(self) -> None:
        rules = default_smart_scheduling_rules()
        self.assertEqual(rules["account_types"]["plus"]["automatic_priority"], 191)
        self.assertEqual(rules["account_types"]["k12"]["automatic_priority"], 91)
        self.assertEqual(rules["account_types"]["team"]["automatic_priority"], 41)
        self.assertEqual(rules["account_types"]["pro"]["automatic_priority"], 991)
        self.assertEqual(rules["account_types"]["pro"]["extreme_entry_percent"], 95)
        self.assertEqual(rules["extreme"], {"priority_min": 1, "priority_max": 20, "priority": 10})

    def test_rejects_overlapping_bands_and_invalid_recovery_threshold(self) -> None:
        rules = default_smart_scheduling_rules()
        rules["account_types"]["plus"]["system_priority_max"] = 205
        with self.assertRaisesRegex(ValueError, "priority bands"):
            normalize_smart_scheduling_rules(rules)
        rules = default_smart_scheduling_rules()
        rules["account_types"]["plus"]["recovery_percent"] = 90
        with self.assertRaisesRegex(ValueError, "recovery"):
            normalize_smart_scheduling_rules(rules)
```

- [ ] **Step 2: Run tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling.SmartSchedulingDefaultsTests -v`

Expected: import failure because `app.modules.sub2api.smart_scheduling` does not exist.

- [ ] **Step 3: Implement normalized defaults**

```python
SUPPORTED_TYPES = ("pro", "plus", "k12", "team")
DEFAULT_SMART_SCHEDULING_RULES = {
    "account_types": {
        "pro": {"manual_priority_min": 1000, "manual_priority_max": 1090, "system_priority_min": 991, "system_priority_max": 999, "automatic_priority": 991, "normal_concurrency": 30, "extreme_entry_percent": 95.0, "recovery_percent": 80.0, "extreme_concurrency": 100},
        "plus": {"manual_priority_min": 200, "manual_priority_max": 290, "system_priority_min": 191, "system_priority_max": 199, "automatic_priority": 191, "normal_concurrency": 30, "extreme_entry_percent": 90.0, "recovery_percent": 80.0, "extreme_concurrency": 100},
        "k12": {"manual_priority_min": 100, "manual_priority_max": 190, "system_priority_min": 91, "system_priority_max": 99, "automatic_priority": 91, "normal_concurrency": 30, "extreme_entry_percent": 90.0, "recovery_percent": 80.0, "extreme_concurrency": 100},
        "team": {"manual_priority_min": 50, "manual_priority_max": 90, "system_priority_min": 41, "system_priority_max": 49, "automatic_priority": 41, "normal_concurrency": 30, "extreme_entry_percent": 90.0, "recovery_percent": 80.0, "extreme_concurrency": 100},
    },
    "extreme": {"priority_min": 1, "priority_max": 20, "priority": 10},
}

def default_smart_scheduling_rules() -> dict[str, Any]:
    return deepcopy(DEFAULT_SMART_SCHEDULING_RULES)
```

Implement integer/float coercion and reject inverted bands, cross-type overlaps, automatic values outside system bands, extreme values outside 1-20, an extreme band that is not ahead of every normal band, non-positive concurrency, percentages outside 0-100, and `recovery_percent >= extreme_entry_percent`.

- [ ] **Step 4: Add failing account decision tests**

```python
def test_legal_priority_is_preserved_while_concurrency_is_corrected(self) -> None:
    decision = evaluate_account(
        account={"remote_account_id": 7, "account_type": "plus", "priority": 250, "concurrency": 20, "usage_snapshot": {}},
        rules=default_smart_scheduling_rules(),
        type_priority_enabled=True,
        quota_acceleration_enabled=False,
        state=None,
        now=self.now,
    )
    self.assertEqual(decision["target"], {"priority": 250, "concurrency": 30})
    self.assertEqual(decision["strategy"], "type_priority")

def test_extreme_precedes_type_normalization_at_exact_threshold(self) -> None:
    decision = evaluate_account(
        account=self.account("plus", priority=250, concurrency=30, used=90),
        rules=default_smart_scheduling_rules(),
        type_priority_enabled=True,
        quota_acceleration_enabled=True,
        state=None,
        now=self.now,
    )
    self.assertEqual(decision["target"], {"priority": 10, "concurrency": 100})
    self.assertEqual(decision["mode"], "extreme")

def test_pro_enters_at_95_not_90(self) -> None:
    normal = evaluate_account(account=self.account("pro", priority=1000, used=94.9), rules=self.rules, type_priority_enabled=True, quota_acceleration_enabled=True, state=None, now=self.now)
    extreme = evaluate_account(account=self.account("pro", priority=1000, used=95), rules=self.rules, type_priority_enabled=True, quota_acceleration_enabled=True, state=None, now=self.now)
    self.assertEqual(normal["mode"], "normal")
    self.assertEqual(extreme["mode"], "extreme")
```

Also test out-of-band fixed values, Team/BugTeam/special Team mapping, Free/unknown skip, stale/missing quota, reset-identity recovery, sub-80 recovery, stale extreme hold, and both-flags-off skip.

- [ ] **Step 5: Run decision tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling.SmartSchedulingDecisionTests -v`

Expected: failures because `evaluate_account` is missing.

- [ ] **Step 6: Implement the pure evaluator**

```python
def adapted_scheduling_type(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    if normalized in {"team", "bug_team", "special_team"}:
        return "team"
    if normalized in {"plus", "special_plus"}:
        return "plus"
    return normalized if normalized in {"pro", "k12"} else None

def priority_in_normal_bands(priority: int, rule: dict[str, Any]) -> bool:
    return (
        rule["manual_priority_min"] <= priority <= rule["manual_priority_max"]
        or rule["system_priority_min"] <= priority <= rule["system_priority_max"]
    )
```

Implement evaluator precedence as: disabled/unsupported -> stale extreme hold -> fresh extreme entry/continue -> confirmed extreme recovery -> normal type normalization -> no-op. Return a structured decision with `status`, `mode`, `strategy`, `reason`, `target`, normalized quota/reset identity, and adapted type.

- [ ] **Step 7: Run pure tests and verify GREEN**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling -v`

Expected: all tests pass.

- [ ] **Step 8: Commit**

```powershell
git add backend/app/modules/sub2api/smart_scheduling.py backend/tests/test_smart_scheduling.py
git commit -m "feat: add smart scheduling decision engine"
```

### Task 2: Settings Persistence, Schemas, Routes, and Indexes

**Files:**
- Create: `backend/tests/test_smart_scheduling_routes.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/routers/api_pools.py`
- Create: `backend/app/modules/sub2api/smart_scheduling_service.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Modify: `backend/tests/test_group_observability_settings.py`
- Modify: `backend/app/modules/sub2api/account_probe.py`

- [ ] **Step 1: Write failing API/default/index tests**

```python
async def test_missing_settings_return_normalized_defaults_and_last_run(self) -> None:
    db = SimpleNamespace(
        app_settings=SimpleNamespace(find_one=AsyncMock(return_value=None)),
        sub2api_smart_scheduling_runs=SimpleNamespace(find_one=AsyncMock(return_value={"changed": 2})),
    )
    result = await get_smart_scheduling_settings(db, "api-5001")
    self.assertEqual(result["rules"]["account_types"]["plus"]["automatic_priority"], 191)
    self.assertEqual(result["last_run"]["changed"], 2)

async def test_group_strategy_flags_default_false_and_persist(self) -> None:
    setting = default_group_observability_setting("api-5001", 3, "plus")
    self.assertFalse(setting["type_priority_enabled"])
    self.assertFalse(setting["quota_acceleration_enabled"])
```

Index assertions must cover unique state ID, site/run queries, and `expires_at` TTL for 30-day outcomes and 90-day runs.

- [ ] **Step 2: Run route tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling_routes tests.test_group_observability_settings -v`

Expected: missing schemas, endpoints, service functions, and flags.

- [ ] **Step 3: Add typed request models and validation**

```python
class SmartSchedulingAccountRule(BaseModel):
    manual_priority_min: int = Field(ge=1, le=100000)
    manual_priority_max: int = Field(ge=1, le=100000)
    system_priority_min: int = Field(ge=1, le=100000)
    system_priority_max: int = Field(ge=1, le=100000)
    automatic_priority: int = Field(ge=1, le=100000)
    normal_concurrency: int = Field(ge=1, le=10000)
    extreme_entry_percent: float = Field(ge=0, le=100)
    recovery_percent: float = Field(ge=0, le=100)
    extreme_concurrency: int = Field(ge=1, le=10000)

class SmartSchedulingRules(BaseModel):
    account_types: dict[Literal["pro", "plus", "k12", "team"], SmartSchedulingAccountRule]
    extreme: SmartSchedulingExtremeRule

class SmartSchedulingSettingsUpdate(BaseModel):
    rules: SmartSchedulingRules
```

Use a model validator to call the same normalization rules as the domain module. Extend `GroupObservabilitySettingUpdate` with the two optional booleans.

- [ ] **Step 4: Implement settings persistence and endpoints**

```python
SMART_SCHEDULING_SETTING_PREFIX = "smart_scheduling"

def smart_scheduling_setting_id(site_id: str) -> str:
    return f"{SMART_SCHEDULING_SETTING_PREFIX}:{site_id.strip()}"

async def update_smart_scheduling_settings(db, site_id, rules, actor):
    normalized = normalize_smart_scheduling_rules(rules)
    await db.app_settings.update_one(
        {"_id": smart_scheduling_setting_id(site_id)},
        {"$set": {"site_id": site_id, "rules": normalized, "updated_at": now_utc(), "updated_by_user_id": actor.get("_id")}, "$setOnInsert": {"created_at": now_utc()}},
        upsert=True,
    )
    return await get_smart_scheduling_settings(db, site_id)
```

Add GET/PATCH routes, verify the site exists, require `pool-lifecycle`, and write `api_pool.smart_scheduling.update` audit records. Extend group defaults/allowlist and return false for missing fields.

- [ ] **Step 5: Add indexes**

```python
async def ensure_smart_scheduling_indexes(db):
    await db.sub2api_smart_scheduling_states.create_index([("site_id", 1), ("remote_account_id", 1)], unique=True)
    await db.sub2api_smart_scheduling_runs.create_index([("site_id", 1), ("started_at", -1)])
    await db.sub2api_smart_scheduling_runs.create_index("expires_at", expireAfterSeconds=0)
    await db.sub2api_smart_scheduling_outcomes.create_index([("site_id", 1), ("run_id", 1), ("remote_account_id", 1)], unique=True)
    await db.sub2api_smart_scheduling_outcomes.create_index("expires_at", expireAfterSeconds=0)
```

Call it from `ensure_indexes`.

- [ ] **Step 6: Run API/default/index tests and verify GREEN**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling_routes tests.test_group_observability_settings -v`

Expected: all tests pass.

- [ ] **Step 7: Commit**

```powershell
git add backend/app/schemas.py backend/app/routers/api_pools.py backend/app/modules/sub2api/smart_scheduling_service.py backend/app/modules/system/bootstrap.py backend/app/modules/sub2api/account_probe.py backend/tests/test_smart_scheduling_routes.py backend/tests/test_group_observability_settings.py
git commit -m "feat: expose smart scheduling settings"
```

### Task 3: Remote Scheduling Runner

**Files:**
- Create: `backend/tests/test_smart_scheduling_service.py`
- Modify: `backend/app/modules/sub2api/smart_scheduling_service.py`

- [ ] **Step 1: Write failing orchestration tests**

```python
async def test_runner_deduplicates_multi_group_account_and_updates_minimal_fields(self) -> None:
    account = self.account(group_ids=[3, 4], priority=250, concurrency=20)
    client = SimpleNamespace(
        get_account=AsyncMock(return_value={"id": 7, "priority": 250, "concurrency": 20}),
        update_account=AsyncMock(return_value={"id": 7, "priority": 250, "concurrency": 30}),
    )
    result = await run_smart_scheduling(
        self.db(), site=self.site(), accounts=[account, dict(account)], group_settings={3: {"type_priority_enabled": True}, 4: {"type_priority_enabled": True}}, client=client,
    )
    client.update_account.assert_awaited_once_with(7, {"priority": 250, "concurrency": 30})
    self.assertEqual(result["changed"], 1)

async def test_latest_manual_priority_is_revalidated_before_update(self) -> None:
    client = SimpleNamespace(get_account=AsyncMock(return_value={"id": 7, "priority": 220, "concurrency": 30}), update_account=AsyncMock())
    result = await run_smart_scheduling(
        self.db(),
        site=self.site(),
        accounts=[self.account(group_ids=[3], priority=300, concurrency=30)],
        group_settings={3: {"type_priority_enabled": True, "quota_acceleration_enabled": False}},
        client=client,
        now=self.now,
    )
    client.update_account.assert_not_awaited()
    self.assertEqual(result["unchanged"], 1)
```

Also test default-off no client calls, stale extreme hold, one account failure followed by another success, sanitized error code, state upsert, run/outcome expiry, active lease conflict, and lease release.

- [ ] **Step 2: Run service tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling_service -v`

Expected: missing runner and lease functions.

- [ ] **Step 3: Implement site lease and client construction**

```python
async def acquire_smart_scheduling_lease(db, site_id, owner, now=None):
    acquired_at = now or now_utc()
    lock_id = f"smart-scheduling:{site_id}"
    document = await db.operation_locks.find_one_and_update(
        {"_id": lock_id, "$or": [{"expires_at": {"$lte": acquired_at}}, {"expires_at": {"$exists": False}}, {"owner": owner}]},
        {"$set": {"owner": owner, "lock_type": "smart_scheduling", "expires_at": acquired_at + timedelta(minutes=5), "updated_at": acquired_at}, "$setOnInsert": {"created_at": acquired_at}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return bool(document and document.get("owner") == owner)
```

Fetch the Admin API key through `fetch_admin_api_key(site["sql_dsn"])` and construct `Sub2ApiClient` only after at least one decision needs a remote re-read.

- [ ] **Step 4: Implement evaluate/re-read/update/persist loop**

For each deduplicated eligible account: load compact state, evaluate snapshot, fetch latest remote account only for a candidate change, replace only current priority/concurrency for re-evaluation, call `update_account(id, target)`, and upsert state/outcome. Count `scanned`, `changed`, `unchanged`, `skipped`, and `failed`. Stop further remote writes on Admin API authentication/configuration failures, but retain completed outcomes.

- [ ] **Step 5: Run service tests and verify GREEN**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling_service -v`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/modules/sub2api/smart_scheduling_service.py backend/tests/test_smart_scheduling_service.py
git commit -m "feat: apply quota-aware account scheduling"
```

### Task 4: Reuse the Existing Probe Snapshot

**Files:**
- Modify: `backend/app/modules/sub2api/account_probe.py`
- Modify: `backend/tests/test_account_probe.py`

- [ ] **Step 1: Write failing integration tests**

```python
async def test_scheduling_enabled_group_is_due_when_observability_is_disabled(self) -> None:
    settings = {3: {"enabled": False, "type_priority_enabled": True, "quota_acceleration_enabled": False, "probe_interval_seconds": 60}}
    with patch.object(account_probe, "_settings_for_site", AsyncMock(return_value=settings)):
        self.assertEqual(await account_probe._due_group_ids(object(), "api-5001"), [3])

async def test_probe_scheduling_adapter_passes_the_already_fetched_accounts(self) -> None:
    snapshot_accounts = [{"id": 7, "group_ids": [3], "priority": 250, "concurrency": 20, "credentials": {"plan_type": "plus"}, "extra": {}}]
    schedule_result = {"scanned": 1, "changed": 1, "unchanged": 0, "skipped": 0, "failed": 0}
    with patch.object(account_probe, "run_smart_scheduling", AsyncMock(return_value=schedule_result)) as schedule:
        result = await account_probe._run_smart_scheduling_for_probe(
            object(),
            site={"id": "api-5001"},
            accounts=snapshot_accounts,
            group_settings={3: {"type_priority_enabled": True}},
            probe_run_id="probe-1",
        )
    self.assertEqual(schedule.await_args.kwargs["accounts"][0]["priority"], 250)
    self.assertEqual(result["smart_scheduling_changed"], 1)
```

- [ ] **Step 2: Run integration tests and verify RED**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_account_probe.AccountProbeSchedulingTests -v`

Expected: due-group and scheduling integration assertions fail.

- [ ] **Step 3: Extend normalization and due logic**

Add `priority`, `concurrency`, and quota freshness/reset fields to normalized accounts. A group is due when `enabled is not False` or either strategy flag is true. Filtering for ordinary monitoring still uses `enabled`, while smart scheduling receives all accounts in due strategy groups.

- [ ] **Step 4: Call scheduling once and merge counters**

Call `run_smart_scheduling` after normalization with the same in-memory accounts and group settings. Prefix returned counters in the probe result and persisted probe run with `smart_scheduling_`.

- [ ] **Step 5: Run probe and scheduling suites**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_account_probe tests.test_smart_scheduling tests.test_smart_scheduling_service -v`

Expected: all tests pass and `_fetch_probe_accounts` remains one call.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/modules/sub2api/account_probe.py backend/tests/test_account_probe.py
git commit -m "feat: run scheduling from account probe snapshots"
```

### Task 5: Frontend Data Model and Validation

**Files:**
- Create: `frontend/src/pages/smartScheduling.ts`
- Create: `frontend/src/pages/smartScheduling.test.ts`
- Modify: `frontend/src/pages/AccountPoolsPage.tsx`

- [ ] **Step 1: Write failing helper tests**

```typescript
it("creates the confirmed defaults", () => {
  const form = smartSchedulingRulesToForm(defaultSmartSchedulingRules);
  expect(form.plus.automatic_priority).toBe("191");
  expect(form.pro.extreme_entry_percent).toBe("95");
  expect(form.extreme.priority).toBe("10");
});

it("rejects overlap and recovery at the entry threshold", () => {
  const form = smartSchedulingRulesToForm(defaultSmartSchedulingRules);
  form.plus.system_priority_max = "205";
  expect(buildSmartSchedulingPayload(form)).toEqual({ ok: false, error: expect.stringContaining("区间") });
  form.plus.system_priority_max = "199";
  form.plus.recovery_percent = "90";
  expect(buildSmartSchedulingPayload(form)).toEqual({ ok: false, error: expect.stringContaining("恢复") });
});
```

- [ ] **Step 2: Run helper tests and verify RED**

Run: `cd frontend; npm test -- src/pages/smartScheduling.test.ts`

Expected: module import failure.

- [ ] **Step 3: Implement typed form conversion and validation**

Export `SmartSchedulingRules`, `SmartSchedulingForm`, defaults, `smartSchedulingRulesToForm`, and `buildSmartSchedulingPayload`. Mirror backend validation so invalid input is caught before PATCH while the backend remains authoritative.

- [ ] **Step 4: Add page state and API flow**

Load `/api-pools/smart-scheduling/settings` with site changes, store normalized form plus last-run summary, and PATCH `{rules}` on save. Extend `GroupObservabilitySetting` with both booleans. Include settings in foreground refresh only when a site is selected and pause refresh during edits/saves.

- [ ] **Step 5: Run helper tests and verify GREEN**

Run: `cd frontend; npm test -- src/pages/smartScheduling.test.ts`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```powershell
git add frontend/src/pages/smartScheduling.ts frontend/src/pages/smartScheduling.test.ts frontend/src/pages/AccountPoolsPage.tsx
git commit -m "feat: add smart scheduling frontend model"
```

### Task 6: Smart Scheduling Operator UI

**Files:**
- Modify: `frontend/src/pages/AccountPoolsPage.tsx`
- Modify: `frontend/styles.css`
- Modify: `frontend/src/pages/smartScheduling.test.ts`

- [ ] **Step 1: Add failing source-contract tests**

Read `AccountPoolsPage.tsx` as UTF-8 and assert it contains `智能调度`, `账号类型自动归档`, `7d 极限加速`, both API paths, last-run counters, and no account-list fetch inside the scheduling section.

- [ ] **Step 2: Run frontend tests and verify RED**

Run: `cd frontend; npm test -- src/pages/smartScheduling.test.ts`

Expected: source-contract assertions fail because the controls are absent.

- [ ] **Step 3: Build the site-level editor**

Add a full-width section after quota detection. Use a dense table with one row for Pro, Plus, K12, and Team/BugTeam; numeric inputs for both bands, automatic priority, normal concurrency, entry/recovery thresholds, and extreme concurrency. Put the global 1-20 band and priority 10 controls in a compact unframed toolbar. Save with a clear command button and show inline validation errors.

- [ ] **Step 4: Build group switches and status**

Render one database-backed group row with two labeled switches. Both use `checked={setting.<flag> === true}` so missing fields render off. Do not disable them when ordinary monitoring is off. Show the 60-second interval and most recent scanned/changed/skipped/failed counters.

- [ ] **Step 5: Add responsive CSS**

Use stable table column widths, `overflow-x: auto` for narrow viewports, 36px minimum controls, no nested cards, and existing neutral/success/warning tokens. At mobile widths, keep group names visible and allow numeric fields to wrap without overlapping labels.

- [ ] **Step 6: Run frontend tests and build**

Run: `cd frontend; npm test -- src/pages/smartScheduling.test.ts`

Run: `cd frontend; npm run build`

Expected: tests pass and Vite production build succeeds without TypeScript errors.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/pages/AccountPoolsPage.tsx frontend/src/pages/smartScheduling.test.ts frontend/styles.css
git commit -m "feat: add smart scheduling controls"
```

### Task 7: Full Regression and Runtime Verification

**Files:**
- Modify only files needed to fix failures introduced by Tasks 1-6.

- [ ] **Step 1: Run focused backend suites**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest tests.test_smart_scheduling tests.test_smart_scheduling_service tests.test_smart_scheduling_routes tests.test_group_observability_settings tests.test_account_probe -v`

Expected: all pass.

- [ ] **Step 2: Run complete backend suite**

Run: `cd backend; .\.venv\Scripts\python.exe -m unittest discover -s tests -v`

Expected: all tests pass with no tracebacks or resource warnings caused by this feature.

- [ ] **Step 3: Run complete frontend suite and build**

Run: `cd frontend; npm test`

Run: `cd frontend; npm run build`

Expected: all Vitest tests pass and production build succeeds.

- [ ] **Step 4: Inspect diff and secrets**

Run: `git diff --check`

Run: `rg -n "credentials|access_token|refresh_token|sql_dsn|password" backend/app/modules/sub2api/smart_scheduling*.py backend/tests/test_smart_scheduling*.py`

Expected: no whitespace errors and no production logging/persistence of credential payloads.

- [ ] **Step 5: Start the frontend dev server and verify the page**

Run: `cd frontend; npm run dev`

Open the reported local URL, inspect the site configuration at desktop and mobile widths, and verify there is no overlap, default group switches are off, the settings table is usable, and requests come only from the settings/group endpoints.

- [ ] **Step 6: Final commit if verification required fixes**

```powershell
git add backend/app/modules/sub2api/smart_scheduling.py backend/app/modules/sub2api/smart_scheduling_service.py backend/app/modules/sub2api/account_probe.py backend/app/modules/system/bootstrap.py backend/app/routers/api_pools.py backend/app/schemas.py backend/tests/test_smart_scheduling.py backend/tests/test_smart_scheduling_service.py backend/tests/test_smart_scheduling_routes.py backend/tests/test_account_probe.py backend/tests/test_group_observability_settings.py frontend/src/pages/smartScheduling.ts frontend/src/pages/smartScheduling.test.ts frontend/src/pages/AccountPoolsPage.tsx frontend/styles.css
git commit -m "fix: harden smart scheduling workflow"
```
