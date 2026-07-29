# Account Quota Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect the first 5h and 7d transition to 100% for every account window, aggregate `account_stats_cost` independently by site and account type, and show read-only average/minimum/maximum/sample-count results below the configured quota estimates.

**Architecture:** Add a focused Sub2API quota-detection module with pure observation/transition/classification functions and a MongoDB persistence layer for compact detector state, short-lived samples, generation profiles, and permanent daily rollups. Reuse accounts already enriched by the PostgreSQL refresh path, expose a read-only `/api-pools/quota-detection` endpoint, and render the result in the existing account-pool configuration page without changing configured limits.

**Tech Stack:** Python 3.14, FastAPI, Motor/PyMongo, PostgreSQL-derived Sub2API snapshots, `unittest`, React 19, TypeScript, Vite, CSS.

---

### Task 1: Pure Window Observation And Transition Rules

**Files:**
- Create: `backend/app/modules/sub2api/quota_detection.py`
- Create: `backend/tests/test_quota_detection.py`

- [ ] **Step 1: Write failing tests for valid observations**

Create `QuotaObservationTests` covering 5h and 7d extraction, source freshness, canonical timestamps, missing windows, cost rollback, credential errors, and account type passthrough. Use a fixed UTC time and an account factory:

```python
NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

def account_snapshot(**overrides: object) -> dict[str, object]:
    account = {
        "id": 953,
        "status": "active",
        "error_message": None,
        "codex_usage_synced_at": NOW - timedelta(minutes=1),
        "codex_5h_used_percent": 94,
        "codex_5h_reset_at": NOW + timedelta(hours=2),
        "codex_5h_window_minutes": 300,
        "codex_5h_actual_cost": 107.2,
        "codex_7d_used_percent": 80,
        "codex_7d_reset_at": NOW + timedelta(days=4),
        "codex_7d_window_minutes": 10_080,
        "codex_7d_actual_cost": 112.5,
    }
    account.update(overrides)
    return account

def test_builds_fresh_five_hour_observation(self) -> None:
    observation = quota_detection.build_window_observation(
        account_snapshot(),
        window_type="five_hour",
        account_type="plus",
        observed_at=NOW,
    )
    self.assertEqual(observation["quality"], "valid")
    self.assertEqual(observation["used_percent"], 94.0)
    self.assertEqual(observation["cost_usd"], 107.2)
    self.assertEqual(observation["account_type"], "plus")
```

- [ ] **Step 2: Run the observation tests and verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_quota_detection.QuotaObservationTests -v
```

Expected: import failure because `quota_detection.py` does not exist.

- [ ] **Step 3: Implement normalized observations**

Create constants and a pure builder in `quota_detection.py`:

```python
WINDOW_FIELDS = {
    "five_hour": {
        "percent": "codex_5h_used_percent",
        "reset_at": "codex_5h_reset_at",
        "window_minutes": "codex_5h_window_minutes",
        "cost": "codex_5h_actual_cost",
    },
    "seven_day": {
        "percent": "codex_7d_used_percent",
        "reset_at": "codex_7d_reset_at",
        "window_minutes": "codex_7d_window_minutes",
        "cost": "codex_7d_actual_cost",
    },
}

MAX_SOURCE_AGE = timedelta(minutes=5)
RESET_JITTER = timedelta(minutes=2)

def build_window_observation(
    account: dict[str, Any],
    *,
    window_type: str,
    account_type: str,
    observed_at: datetime,
) -> dict[str, Any]:
    fields = WINDOW_FIELDS[window_type]
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    remote_account_id = account.get("id")
    used_percent = _number(_first(account, extra, fields["percent"]))
    reset_at = _utc_datetime(_first(account, extra, fields["reset_at"]))
    window_minutes = _number(_first(account, extra, fields["window_minutes"]))
    cost_usd = _number(_first(account, extra, fields["cost"]))
    synced_at = _utc_datetime(_first(account, extra, "codex_usage_synced_at"))
    invalid_reason = _observation_invalid_reason(
        account=account,
        remote_account_id=remote_account_id,
        used_percent=used_percent,
        reset_at=reset_at,
        window_minutes=window_minutes,
        cost_usd=cost_usd,
        synced_at=synced_at,
        observed_at=observed_at,
    )
    return {
        "quality": "invalid" if invalid_reason else "valid",
        "reason": invalid_reason,
        "remote_account_id": remote_account_id,
        "window_type": window_type,
        "window_reset_at": reset_at,
        "window_minutes": window_minutes,
        "used_percent": used_percent,
        "cost_usd": cost_usd,
        "usage_synced_at": synced_at,
        "observed_at": _as_utc(observed_at),
        "account_type": account_type,
    }
```

Implement `_first`, `_number`, `_utc_datetime`, `_as_utc`, and `_observation_invalid_reason` in the same module. `_observation_invalid_reason` returns one stable reason such as `missing_remote_id`, `missing_window`, `stale_usage`, `expired_window`, `invalid_cost`, or `credential_error`; it never raises for one malformed account.

- [ ] **Step 4: Write failing transition tests**

Add `QuotaTransitionTests` for:

```python
def test_under_limit_to_full_creates_candidate(self) -> None:
    previous = quota_detection.state_from_observation(valid_observation(percent=94, cost=107.2))
    current = valid_observation(percent=100, cost=113.6)
    decision = quota_detection.evaluate_transition(previous, current)
    self.assertEqual(decision["action"], "candidate")
    self.assertEqual(decision["observed_limit_usd"], 113.6)

def test_full_to_full_is_ignored(self) -> None:
    previous = {**quota_detection.state_from_observation(valid_observation(percent=94)), "hit_recorded": True}
    self.assertEqual(quota_detection.evaluate_transition(previous, valid_observation(percent=100))["action"], "ignore")
```

Also assert: first observation at 100 only creates a state; `<100 -> <100` updates baseline; reset changes create a new baseline; reset jitter within two minutes stays in the same window; older observations are ignored; cost rollback is invalid; and a plan-type change invalidates that window.

- [ ] **Step 5: Run transition tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_quota_detection.QuotaTransitionTests -v
```

Expected: failure because transition helpers are missing.

- [ ] **Step 6: Implement transition helpers**

Implement pure functions with dictionary results:

```python
def state_from_observation(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "window_reset_at": observation["window_reset_at"],
        "last_under_limit_percent": observation["used_percent"] if observation["used_percent"] < 100 else None,
        "last_under_limit_cost_usd": observation["cost_usd"] if observation["used_percent"] < 100 else None,
        "last_observed_at": observation["observed_at"],
        "account_type": observation["account_type"],
        "hit_recorded": False,
    }

def evaluate_transition(state: dict[str, Any] | None, observation: dict[str, Any]) -> dict[str, Any]:
    if observation["quality"] != "valid":
        return {"action": "invalid", "reason": observation["reason"], "state": state}
    if state is None or not _same_window(state.get("window_reset_at"), observation["window_reset_at"]):
        return {"action": "baseline", "state": state_from_observation(observation)}
    if observation["observed_at"] <= state["last_observed_at"]:
        return {"action": "ignore", "reason": "late_observation", "state": state}
    if state.get("account_type") != observation["account_type"]:
        return {"action": "invalid", "reason": "account_type_changed", "state": state}
    if state.get("hit_recorded"):
        return {"action": "ignore", "reason": "window_already_recorded", "state": state}
    if observation["used_percent"] < 100:
        return {"action": "update", "state": state_from_observation(observation)}
    previous_percent = state.get("last_under_limit_percent")
    previous_cost = state.get("last_under_limit_cost_usd")
    if previous_percent is None or previous_cost is None:
        return {"action": "ignore", "reason": "no_under_limit_baseline", "state": state}
    if observation["cost_usd"] < previous_cost:
        return {"action": "invalid", "reason": "cost_rollback", "state": state}
    return {
        "action": "candidate",
        "previous_percent": previous_percent,
        "previous_cost_usd": previous_cost,
        "observed_limit_usd": observation["cost_usd"],
        "state": {**state, "hit_recorded": True, "last_observed_at": observation["observed_at"]},
    }
```

Actions are `baseline`, `update`, `candidate`, `ignore`, and `invalid`. A candidate must include previous percent/cost and current hit cost. An invalid observation must not advance valid detector state.

- [ ] **Step 7: Run Task 1 tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_quota_detection.QuotaObservationTests tests.test_quota_detection.QuotaTransitionTests -v
```

Expected: all tests pass.

Commit:

```powershell
git add backend/app/modules/sub2api/quota_detection.py backend/tests/test_quota_detection.py
git commit -m "feat: detect first full quota transitions"
```

### Task 2: Robust Sample Classification And Generation Changes

**Files:**
- Modify: `backend/app/modules/sub2api/quota_detection.py`
- Modify: `backend/tests/test_quota_detection.py`

- [ ] **Step 1: Write failing classifier tests**

Add `QuotaCandidateClassificationTests` for the initial five samples, stable accepted samples, MAD outliers, and zero-median safety:

```python
def test_sixth_far_candidate_is_outlier(self) -> None:
    result = quota_detection.classify_candidate(
        220,
        accepted_values=[108, 109, 110, 111, 112],
    )
    self.assertEqual(result["classification"], "outlier")
    self.assertEqual(result["direction"], "above")

def test_near_candidate_is_accepted(self) -> None:
    result = quota_detection.classify_candidate(
        113,
        accepted_values=[108, 109, 110, 111, 112],
    )
    self.assertEqual(result["classification"], "accepted")
```

- [ ] **Step 2: Run classifier tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_quota_detection.QuotaCandidateClassificationTests -v
```

Expected: failure because classification is missing.

- [ ] **Step 3: Implement median/MAD classification**

Implement:

```python
BASELINE_SAMPLE_COUNT = 5
RECENT_SAMPLE_LIMIT = 100
MIN_RELATIVE_TOLERANCE = 0.25
MAD_MULTIPLIER = 3.0

def classify_candidate(value: float, *, accepted_values: list[float]) -> dict[str, Any]:
    if len(accepted_values) < BASELINE_SAMPLE_COUNT:
        return {"classification": "accepted", "reason": "baseline", "direction": None}
    median_value = statistics.median(accepted_values[-RECENT_SAMPLE_LIMIT:])
    mad = statistics.median(abs(item - median_value) for item in accepted_values[-RECENT_SAMPLE_LIMIT:])
    tolerance = max(MIN_RELATIVE_TOLERANCE, MAD_MULTIPLIER * mad / median_value) if median_value > 0 else MIN_RELATIVE_TOLERANCE
    deviation = abs(value - median_value) / median_value if median_value > 0 else 0.0
    if deviation <= tolerance:
        return {"classification": "accepted", "reason": "within_tolerance", "direction": None, "median": median_value, "mad": mad, "tolerance": tolerance, "deviation": deviation}
    return {"classification": "outlier", "reason": "outside_tolerance", "direction": "above" if value > median_value else "below", "median": median_value, "mad": mad, "tolerance": tolerance, "deviation": deviation}
```

Return median, MAD, tolerance, deviation, direction, classification, and reason so rejected samples remain explainable.

- [ ] **Step 4: Write failing generation-promotion tests**

Test `new_generation_candidate(outliers)` with five same-direction candidates from at least three accounts and relative spread no greater than 10%. Reject mixed direction, fewer than three accounts, and dispersed values.

```python
def test_clustered_outliers_promote_new_generation(self) -> None:
    outliers = [
        {"remote_account_id": account_id, "observed_limit_usd": value, "direction": "above"}
        for account_id, value in [(1, 218), (2, 220), (3, 221), (4, 219), (5, 222)]
    ]
    result = quota_detection.new_generation_candidate(outliers)
    self.assertTrue(result["promote"])
```

- [ ] **Step 5: Implement and verify generation rules**

Implement the pure promotion helper, run the full `test_quota_detection` module, and commit:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_quota_detection -v
git add backend/app/modules/sub2api/quota_detection.py backend/tests/test_quota_detection.py
git commit -m "feat: classify quota samples with tolerance"
```

### Task 3: Idempotent MongoDB Persistence And Daily Rollups

**Files:**
- Modify: `backend/app/modules/sub2api/quota_detection.py`
- Modify: `backend/tests/test_quota_detection.py`

- [ ] **Step 1: Write failing detector persistence tests**

Add `QuotaDetectionPersistenceTests` using `SimpleNamespace` collections with `AsyncMock`. Cover:

- bulk state lookup by deterministic IDs;
- first `<100` observation stores baseline only;
- the next 100 observation upserts exactly one sample;
- repeating 100 does not insert or count again;
- a new reset window can insert a new sample;
- invalid observations do not overwrite a valid under-limit state;
- 5h and 7d candidates from one account are independent;
- account type resolver is called once per account and all known types remain separate.

The public orchestration function is `observe_account_quota_limits(db, *, site_id, accounts, observed_at, account_type_for) -> dict[str, Any]`. It resolves each account type once, builds both window observations, bulk-loads detector states, evaluates decisions, persists new samples, rebuilds affected daily rollups, and returns counts for observed, accepted, outlier, invalid, and ignored decisions.

- [ ] **Step 2: Run persistence tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_quota_detection.QuotaDetectionPersistenceTests -v
```

Expected: failure because orchestration is missing.

- [ ] **Step 3: Implement compact state and sample persistence**

Use one initial `$in` state query, then unordered `UpdateOne` operations. State documents contain only detector fields and `expires_at = observed_at + 30 days`. Sample IDs use the canonical state reset time:

```python
sample_id = f"{site_id}:{remote_account_id}:{window_type}:{_iso_z(canonical_reset_at)}"
```

Use `$setOnInsert` for immutable sample identity and classification input. Determine whether the sample was newly inserted before updating rollups, so retries never increase counts.

- [ ] **Step 4: Write failing profile and daily-rollup tests**

Cover profile creation at generation 1, recent accepted sample loading, outlier storage, atomic generation compare/update, promotion of the five-candidate cluster, and deterministic daily replacement:

```python
expected_rollup = {
    "sample_count": 3,
    "sample_sum_usd": 330.0,
    "sample_min_usd": 108.0,
    "sample_max_usd": 112.0,
}
```

Run the test once with duplicate source samples and prove the replacement result is unchanged.

- [ ] **Step 5: Implement profiles and deterministic rollups**

Add four focused helpers. `_profile_for_dimension` upserts generation 1 and returns the profile. `_recent_dimension_samples` returns at most 100 numeric values in hit-time order for one profile generation and classification. `_promote_generation_if_ready` checks consecutive same-direction outliers, distinct accounts, and relative spread before performing a compare-and-set generation update. `_rebuild_daily_rollup` recomputes count/sum/min/max from accepted samples for one Shanghai date and calls `replace_one({"_id": rollup_id}, document, upsert=True)`.

Promotion updates the profile only with `{"current_generation": old_generation}` in the query; only the winner reclassifies candidate IDs and rebuilds affected days.

- [ ] **Step 6: Run Task 3 tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_quota_detection -v
```

Expected: all quota-detection tests pass.

Commit:

```powershell
git add backend/app/modules/sub2api/quota_detection.py backend/tests/test_quota_detection.py
git commit -m "feat: persist quota observations and rollups"
```

### Task 4: Indexes And Read-Only Summary API

**Files:**
- Modify: `backend/app/modules/system/bootstrap.py`
- Modify: `backend/app/modules/sub2api/quota_detection.py`
- Modify: `backend/app/routers/api_pools.py`
- Modify: `backend/tests/test_quota_detection.py`
- Create: `backend/tests/test_quota_detection_routes.py`

- [ ] **Step 1: Write failing index tests**

Add a bootstrap test that calls `ensure_quota_detection_indexes(db)` and verifies:

```python
states.create_index.assert_any_await("expires_at", expireAfterSeconds=0)
samples.create_index.assert_any_await("expires_at", expireAfterSeconds=0)
samples.create_index.assert_any_await(
    [("site_id", 1), ("account_type", 1), ("window_type", 1), ("hit_at", -1)]
)
rollups.create_index.assert_any_await(
    [("site_id", 1), ("account_type", 1), ("window_type", 1), ("generation", 1), ("local_date", 1)],
    unique=True,
)
```

- [ ] **Step 2: Implement quota index bootstrap**

Add `ensure_quota_detection_indexes` beside existing focused index functions and call it from `ensure_indexes`. Include profile uniqueness and state lookup indexes; do not recreate account history indexes or `remote_account_change_batches`.

- [ ] **Step 3: Write failing summary tests**

Test `get_quota_detection_summary(db, site_id)` with daily rollups from several dates. Assert weighted average uses `sum/count`, not average-of-averages; min/max span all current-generation days; and all known account types return stable empty 5h/7d objects.

```python
self.assertEqual(summary["items"][1]["account_type"], "plus")
self.assertEqual(summary["items"][1]["five_hour"]["sample_count"], 3)
self.assertAlmostEqual(summary["items"][1]["five_hour"]["average_usd"], 110.0)
```

- [ ] **Step 4: Implement the summary query**

Load profiles for the site, aggregate only each profile's current generation rollups, and return fixed type order:

```python
KNOWN_ACCOUNT_TYPES = ("free", "plus", "team", "bug_team", "k12", "pro")
```

Return `unknown` only when it has a profile with samples. Serialize datetimes with existing `serialize_doc`.

- [ ] **Step 5: Write failing route tests**

Directly call the FastAPI route as existing route tests do. Assert missing site returns 404, a configured Sub2API site calls the summary service once, and auth role dependency remains owner/admin/maintainer.

- [ ] **Step 6: Add the GET route and verify**

Add to `api_pools.py`:

```python
@router.get("/quota-detection")
async def get_quota_detection(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    site = await get_site(db, site_id)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    return await get_quota_detection_summary(db, site_id)
```

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_quota_detection tests.test_quota_detection_routes -v
```

Commit:

```powershell
git add backend/app/modules/system/bootstrap.py backend/app/modules/sub2api/quota_detection.py backend/app/routers/api_pools.py backend/tests/test_quota_detection.py backend/tests/test_quota_detection_routes.py
git commit -m "feat: expose account quota detection summary"
```

### Task 5: Attach Detection To The Existing PostgreSQL Refresh

**Files:**
- Modify: `backend/app/modules/sub2api/cache.py`
- Modify: `backend/tests/test_sub2api_usage_refresh.py`

- [ ] **Step 1: Write a failing refresh-hook test**

Extract a small helper so integration is testable without reproducing all cache writes:

```python
async def _observe_quota_limits_after_usage_refresh(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    accounts: list[dict[str, Any]],
    observed_at: datetime,
) -> dict[str, Any]:
    try:
        return await observe_account_quota_limits(
            db,
            site_id=site_id,
            accounts=accounts,
            observed_at=observed_at,
            account_type_for=_capacity_account_type,
        )
    except Exception as exc:
        logger.warning("sub2api_quota_detection_failed site_id=%s error_type=%s", site_id, type(exc).__name__)
        return {"ok": False, "site_id": site_id, "status": "failed", "error_type": type(exc).__name__}
```

Patch `observe_account_quota_limits` and assert the helper passes the already-normalized accounts, fetched timestamp, and `_capacity_account_type` resolver. Add a second test proving detector failure returns a compact failed summary and does not raise from account-pool refresh.

- [ ] **Step 2: Run the hook tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_sub2api_usage_refresh.Sub2ApiQuotaDetectionHookTests -v
```

Expected: failure because the helper does not exist.

- [ ] **Step 3: Implement the best-effort hook**

After `_apply_account_usage_windows` has populated all accounts and before Mongo cache replacement, call the detector once:

```python
quota_detection_summary = await _observe_quota_limits_after_usage_refresh(
    db,
    site_id=site_id,
    accounts=accounts,
    observed_at=fetched_at,
)
```

Include the compact result under `quota_detection` in the refresh response. Log only site ID, counts, and exception type; never log account JSON or credentials. Do not add SQL or HTTP calls.

- [ ] **Step 4: Verify refresh behavior and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_sub2api_usage_refresh tests.test_quota_detection -v
```

Commit:

```powershell
git add backend/app/modules/sub2api/cache.py backend/tests/test_sub2api_usage_refresh.py
git commit -m "feat: sample actual quotas during pool refresh"
```

### Task 6: Read-Only Quota Detection UI

**Files:**
- Modify: `frontend/src/pages/AccountPoolsPage.tsx`
- Modify: `frontend/styles.css`

- [ ] **Step 1: Add typed API state and loading behavior**

Define types next to `CapacityLimitsResponse`:

```typescript
type QuotaDetectionWindow = {
  average_usd: number | null;
  maximum_usd: number | null;
  minimum_usd: number | null;
  sample_count: number;
  generation: number | null;
  generation_started_at?: string | null;
};

type QuotaDetectionItem = {
  account_type: CapacityLimitKey | "unknown";
  five_hour: QuotaDetectionWindow;
  seven_day: QuotaDetectionWindow;
};

type QuotaDetectionResponse = {
  site_id: string;
  items: QuotaDetectionItem[];
  last_evaluated_at?: string | null;
};
```

Add `quotaDetection`, `loadingQuotaDetection`, and a request sequence ref. Implement `loadQuotaDetection(siteId)` with the same stale-response protection as `loadCapacityLimits`.

- [ ] **Step 2: Integrate site switching and silent refresh**

On selected-site changes, load capacity limits and quota detection together. On `sub2api-cache-updated` and the existing page-level silent refresh, reload quota detection without clearing the previous result; only site changes clear stale site data.

- [ ] **Step 3: Render the panel below quota estimates**

Add a sibling section immediately after `capacity-limit-config-panel`:

```tsx
<section className="panel quota-detection-panel">
  <div className="panel-header">
    <div>
      <h3>实际额度检测</h3>
      <p>{quotaDetection?.last_evaluated_at ? `最近检测 ${formatDateTime(quotaDetection.last_evaluated_at)}` : "等待账号首次达到满额"}</p>
    </div>
  </div>
  <div className="quota-detection-table">
    {quotaDetectionItems.map((item) => (
      <div className="quota-detection-row" key={item.account_type}>
        <strong>{quotaDetectionLabel(item.account_type)}</strong>
        <QuotaWindowResult label="5h" value={item.five_hour} />
        <QuotaWindowResult label="7d" value={item.seven_day} />
      </div>
    ))}
  </div>
</section>
```

Implement the result inline or as a small local component. Each window shows average as the primary number, then `最低 / 最高 / N 个样本`. Use `$0.00` formatting only for real values; empty values display `-` and `0 个样本`. Do not expose an apply/update action.

- [ ] **Step 4: Add desktop and mobile styles**

Use an unframed row/table treatment rather than nested cards:

```css
.quota-detection-table { display: grid; }
.quota-detection-row {
  display: grid;
  grid-template-columns: minmax(90px, .55fr) repeat(2, minmax(220px, 1fr));
  gap: 16px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}
.quota-window-result { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; }
```

At the existing mobile breakpoint, keep each account type as one row and stack its two window results without horizontal overflow. Do not use viewport-scaled font sizes.

- [ ] **Step 5: Build and commit**

Run:

```powershell
cd frontend
npm run build
```

Expected: TypeScript and Vite build pass; the existing chunk-size warning may remain.

Commit:

```powershell
git add frontend/src/pages/AccountPoolsPage.tsx frontend/styles.css
git commit -m "feat: show detected account quota limits"
```

### Task 7: Full Verification And Storage Guard

**Files:**
- Modify if needed: `backend/tests/test_quota_detection.py`
- Modify if needed: `backend/tests/test_quota_detection_routes.py`
- Modify if needed: `backend/tests/test_sub2api_usage_refresh.py`

- [ ] **Step 1: Run the focused backend suite**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_quota_detection tests.test_quota_detection_routes tests.test_sub2api_usage_refresh -v
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the complete backend suite**

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: zero failures.

- [ ] **Step 3: Run the production frontend build**

```powershell
cd ..\frontend
npm run build
```

Expected: build succeeds. Report but do not conflate the pre-existing chunk-size warning with a failure.

- [ ] **Step 4: Verify storage shape in tests**

Assert state and sample documents do not contain `credentials`, `extra`, `account`, `usage_snapshot`, email, or account name. Assert one account with unchanged usage produces no new sample write after its initial state update.

- [ ] **Step 5: Inspect final diff and commit corrections**

```powershell
cd ..
git diff --check
git status --short
```

If verification required corrections, commit only those scoped files:

```powershell
git add backend frontend
git commit -m "test: verify account quota detection"
```
