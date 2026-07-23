# Adaptive P90 Surge Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make P90 runway respond to confirmed traffic recovery and surges by blending realtime demand with each group's historical three-hour post-surge persistence.

**Architecture:** Extend the cached hourly forecast with group-specific rising-demand persistence profiles calculated only from completed history. Add a pure adaptive-P90 transformation that leaves P50 and the seasonal P90 floor intact, then invoke it from capacity risk after the current-hour Nowcast and expose diagnostics.

**Tech Stack:** Python 3.14, dataclasses, FastAPI service modules, MongoDB forecast cache, `unittest`/pytest-compatible backend tests.

---

### Task 1: Historical surge persistence profiles

**Files:**
- Modify: `backend/app/modules/sub2api/hourly_forecast.py`
- Test: `backend/tests/test_hourly_forecast.py`

- [ ] **Step 1: Write failing profile tests**

Add tests that build 56 days of complete hourly observations containing repeated rising-demand anchors. Assert that `forecast_hourly_demand()` returns separate `warming` and `surge` profiles, each with three persistence ratios, an event count, and bounded confidence. Add a second fixture where post-surge hours remain high and assert its persistence ratios exceed a fixture where traffic immediately falls.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/test_hourly_forecast.py -q
```

Expected: failure because `ForecastResult` does not expose persistence profiles.

- [ ] **Step 3: Implement profile calculation**

Add an immutable profile type and attach profiles to `ForecastResult` with an empty-tuple default for backward-compatible direct construction:

```python
@dataclass(frozen=True, slots=True)
class SurgePersistenceProfile:
    stage: str
    event_count: int
    preferred_event_count: int
    confidence: float
    persistence_ratios: tuple[float, float, float]
    source: str


@dataclass(frozen=True, slots=True)
class ForecastResult:
    # existing fields
    points: tuple[ForecastPoint, ...]
    surge_profiles: tuple[SurgePersistenceProfile, ...] = ()
```

Scan completed history for anchors whose cost is at least 1.20 times the preceding three-hour median. Classify ratios below 1.50 as `warming` and the rest as `surge`. Merge rising hours inside the next three-hour window into one event. Normalize the three following hours against the greater of the anchor cost and first following complete-hour cost, then calculate recency-weighted P90 ratios. This prevents a partially interrupted anchor hour from multiplying an already recovered realtime rate twice. Prefer matching Shanghai local-time bands and weekday/weekend class when enough events exist; otherwise use all events in the same class. Confidence grows with event count and is reduced for fallback samples.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 1 command and expect all tests to pass.

### Task 2: Pure adaptive P90 transformation

**Files:**
- Modify: `backend/app/modules/sub2api/hourly_forecast.py`
- Test: `backend/tests/test_hourly_forecast.py`

- [ ] **Step 1: Write failing propagation tests**

Construct forecasts with identical seasonal points but different historical persistence profiles. Assert:

```python
result = apply_adaptive_p90_propagation(
    forecast,
    now=AS_OF + timedelta(minutes=30),
    realtime_cost_per_hour=600,
    stage="surge",
    strength=1.0,
    confidence=1.0,
)
self.assertTrue(result.applied)
self.assertEqual(result.adjusted_points, 3)
self.assertEqual(result.forecast.points[0], forecast.points[0])
self.assertGreater(result.forecast.points[1].p90, forecast.points[1].p90)
self.assertEqual(result.forecast.points[1].p50, forecast.points[1].p50)
```

Also assert that stable/cooling states do nothing, sparse profiles have less influence than mature profiles, persistent profiles adjust more strongly than short-lived profiles, and adjusted P90 never falls below the original P90.

- [ ] **Step 2: Run the focused tests and verify RED**

Run the Task 1 command. Expected: import or attribute failure for `apply_adaptive_p90_propagation`.

- [ ] **Step 3: Implement the transformation**

Add `AdaptiveP90Propagation` and a pure transformation. For each of the next three complete hours:

```python
continuation = realtime_cost_per_hour * profile.persistence_ratios[index]
weight = regime_weight * profile.confidence * (0.80, 0.60, 0.40)[index]
adjusted_p90 = point.p90 + max(0.0, continuation - point.p90) * weight
```

Use `confidence * (0.5 + 0.5 * strength)` as the surge regime weight and reduce it for `warming`. Do not modify the current partial-hour point, P50, stable/cooling forecasts, or horizons after the next three complete hours.

- [ ] **Step 4: Run tests and verify GREEN**

Run the Task 1 command and expect all tests to pass.

### Task 3: Forecast cache compatibility

**Files:**
- Modify: `backend/app/modules/sub2api/hourly_forecast.py`
- Modify: `backend/app/modules/sub2api/hourly_forecast_service.py`
- Test: `backend/tests/test_hourly_forecast_service.py`

- [ ] **Step 1: Write failing cache tests**

Assert that generated forecast documents persist profile dictionaries, cached documents deserialize them, and a cached forecast from the previous model version is regenerated rather than silently returning no profiles.

- [ ] **Step 2: Run cache tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/test_hourly_forecast_service.py -q
```

Expected: generated documents lack profile data and old cache entries are reused.

- [ ] **Step 3: Implement cache serialization and invalidation**

Bump `MODEL_VERSION`, deserialize `surge_profiles`, and reuse a cached forecast only when its model and version match the current constants. Existing same-hour documents are then rebuilt immediately after deployment.

- [ ] **Step 4: Run cache tests and verify GREEN**

Run the Task 3 command and expect all tests to pass.

### Task 4: Capacity-risk integration and diagnostics

**Files:**
- Modify: `backend/app/modules/sub2api/capacity_risk.py`
- Test: `backend/tests/test_capacity_risk.py`

- [ ] **Step 1: Write failing end-to-end calculation tests**

Create a confirmed direct-cost surge with a seasonal P90 runway near 11 hours and current-speed runway near 3.6 hours. Attach a moderate persistence profile and assert the adaptive P90 runway moves between those values. Assert a persistent profile produces a shorter P90 runway, while a stable regime leaves the forecast unchanged.

- [ ] **Step 2: Run focused capacity tests and verify RED**

Run:

```powershell
cd backend
python -m pytest tests/test_capacity_risk.py -q
```

Expected: P90 future points remain seasonal and the runway does not respond.

- [ ] **Step 3: Invoke propagation after current-hour Nowcast**

Pass the effective forecast, direct realtime hourly cost, and detected regime fields to the pure transformation. Use the returned forecast for P90 runway only. Add diagnostics for applied state, profile class/count/confidence/ratios, realtime rate, adjusted point count, and original/adjusted P90 totals.

- [ ] **Step 4: Run focused and integration tests**

Run:

```powershell
cd backend
python -m pytest tests/test_capacity_risk.py tests/test_capacity_risk_integration.py tests/test_hourly_forecast.py tests/test_hourly_forecast_service.py -q
```

Expected: all focused tests pass.

### Task 5: Full verification

**Files:**
- Verify only.

- [ ] **Step 1: Run all backend tests**

```powershell
cd backend
python -m pytest -q
```

Expected: all backend tests pass.

- [ ] **Step 2: Run frontend tests and production build**

```powershell
cd frontend
npm test -- --run
npm run build
```

Expected: all frontend tests pass and Vite produces a successful production build. The existing chunk-size warning may remain.

- [ ] **Step 3: Review the final diff**

Confirm that only forecast profile generation, adaptive P90 propagation, cache compatibility, diagnostics, tests, and planning documents changed. Confirm P50 and current-speed semantics remain unchanged.
