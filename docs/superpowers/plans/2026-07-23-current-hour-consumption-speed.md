# Current Hour Consumption Speed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a group-scoped current consumption speed metric in USD/hour, using the previous complete natural hour during the first five minutes and prorating the current hour afterward.

**Architecture:** Calculate the value once in the backend from the existing group-specific PostgreSQL hourly trend, flatten it into the capacity summary, and let the existing five-minute sampler persist the scalar fields. The frontend renders the backend result beside the pressure-stage metric and does not recalculate it.

**Tech Stack:** Python 3, FastAPI service modules, `unittest`, React, TypeScript, Vitest, Vite.

---

### Task 1: Deterministic Hourly Rate Calculation

**Files:**
- Modify: `backend/app/modules/sub2api/cache.py`
- Test: `backend/tests/test_capacity_risk_integration.py`

- [ ] **Step 1: Write failing boundary tests**

Add table-driven tests for `_current_consumption_rate(hourly, now=...)` covering minutes 0, 4, 5, 30, and 59, with hourly rows shaped as `{"bucket_at": <UTC datetime>, "account_cost": <number>}`. Assert that minutes below five return the exact prior natural hour with `source="previous_full_hour"`, while minute five and later return `current cost / elapsed minutes * 60` with `source="current_hour_prorated"`.

- [ ] **Step 2: Write failing gap and validation tests**

Assert that an older row cannot replace a missing immediate prior hour, a prior row cannot replace a missing current hour after minute five, day/month rollover finds the exact preceding hour, and negative or non-numeric selected cost returns:

```python
{
    "usd_per_hour": None,
    "source": "unavailable",
    "elapsed_minutes": expected_elapsed,
    "hour": None,
}
```

- [ ] **Step 3: Run tests and verify RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m unittest tests.test_capacity_risk_integration
```

Expected: FAIL because `_current_consumption_rate` does not exist.

- [ ] **Step 4: Implement the pure calculator**

In `cache.py`, add `_current_consumption_rate(hourly, *, now=None)` near `_burst_1h_summary`. Normalize `now` to `Asia/Shanghai`, truncate to the current natural hour, choose the exact expected bucket, validate `_capacity_cost` input without treating invalid/negative values as zero, and return the four internal keys `usd_per_hour`, `source`, `elapsed_minutes`, and `hour`. Serialize a successful hour as an ISO datetime string.

- [ ] **Step 5: Run tests and verify GREEN**

Run the Task 1 test module again and expect all tests to pass.

### Task 2: Capacity Summary and Five-Minute Sampling

**Files:**
- Modify: `backend/app/modules/sub2api/cache.py`
- Modify: `backend/tests/test_capacity_risk_integration.py`
- Modify: `backend/tests/test_capacity_limits.py`
- Modify: `backend/tests/test_capacity_sampler.py`

- [ ] **Step 1: Write failing integration tests**

Extend the dashboard summary test so the mocked group trend proves `account_cost` is used and `current_consumption_rate` is returned. Extend a capacity summary test with:

```python
"current_consumption_rate": {
    "usd_per_hour": 640.0,
    "source": "current_hour_prorated",
    "elapsed_minutes": 30.0,
    "hour": "2026-07-16T12:00:00+00:00",
}
```

Assert the flattened public fields exactly match the approved four-field API contract.

- [ ] **Step 2: Write the failing sampler assertion**

Add the four public fields to the mocked summary in `test_capacity_sampler.py` and assert they appear under `document["metrics"]`; keep schema version 2 because this is an additive scalar extension.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m unittest tests.test_capacity_risk_integration tests.test_capacity_limits tests.test_capacity_sampler
```

Expected: FAIL because the dashboard and capacity summaries do not expose the new fields.

- [ ] **Step 4: Wire the calculator into summaries**

Call `_current_consumption_rate(hourly, now=current_time)` from `_dashboard_cost_summary`. In `_capacity_summary_for_accounts`, flatten it as:

```python
"current_consumption_rate_usd_per_hour": _round_optional(rate["usd_per_hour"]),
"current_consumption_rate_source": rate["source"],
"current_consumption_rate_elapsed_minutes": rate["elapsed_minutes"],
"current_consumption_rate_hour": rate["hour"],
```

Do not feed these fields into risk, health, notification, or refill calculations.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the Task 2 command again and expect all tests to pass.

### Task 3: API Pool Status UI

**Files:**
- Modify: `frontend/src/pages/ApiPoolStatusPage.tsx`
- Modify: `frontend/src/pages/ApiPoolStatusHelp.test.ts`

- [ ] **Step 1: Write the failing frontend source test**

Assert the page declares the four response fields, renders `label="当前消耗速度"` before `label="压力阶段"`, maps `previous_full_hour` to `上一完整小时`, maps `current_hour_prorated` to `本小时折算`, uses `等待小时消耗数据` for unavailable data, and documents both the five-minute guard and `本小时累计消耗 / 已过分钟 * 60` formula.

- [ ] **Step 2: Run the frontend test and verify RED**

Run:

```powershell
npm test -- ApiPoolStatusHelp.test.ts
```

Expected: FAIL because the new metric and help content are absent.

- [ ] **Step 3: Implement the display**

Extend `CapacitySummary`, add a small source-label formatter, and render a `CapacityMetric` beside the pressure stage. Use `formatUsd(rate) + "/小时"`, `-` when unavailable, `AnimatedValue` through the existing component, and no new warning or health semantics. Add `METRIC_HELP_DETAILS["当前消耗速度"]` with the approved guard and formula.

- [ ] **Step 4: Run frontend tests and build**

Run:

```powershell
npm test
npm run build
```

Expected: all tests pass and the production build succeeds.

### Task 4: Documentation and Full Verification

**Files:**
- Modify: `docs/design/30-api-pool-realtime-capacity-and-presence.md`

- [ ] **Step 1: Update maintained developer documentation**

Document the four backend fields, exact five-minute switch rule, strict bucket matching, group isolation, and persistence into schema-version-2 five-minute capacity samples.

- [ ] **Step 2: Run the complete backend suite**

Run from `backend`:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Expected: all backend tests pass.

- [ ] **Step 3: Run repository checks**

Run:

```powershell
git diff --check
git status --short --branch
```

Expected: no whitespace errors; only intentional plan, backend, frontend, test, and maintained-document changes are present.
