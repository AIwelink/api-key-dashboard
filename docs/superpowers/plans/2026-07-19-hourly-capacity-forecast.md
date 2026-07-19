# Hourly Capacity Forecast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a deterministic, non-deep-learning 24-hour P50/P90 group demand forecast with rolling-origin backtesting against persistence, daily-seasonal, and weekly-seasonal baselines.

**Architecture:** Keep forecasting as pure Python modules under `app.modules.sub2api`, independent from the existing online capacity alert calculation. A read-only PostgreSQL repository supplies complete hourly `account_cost`, request, and token observations; a CLI runs reproducible forecasts and backtests without changing online alerts.

**Tech Stack:** Python 3.14 standard library, SQLAlchemy asyncio, PostgreSQL, unittest

---

### Task 1: Define Forecast Domain And Readiness

**Files:**
- Create: `backend/app/modules/sub2api/hourly_forecast.py`
- Create: `backend/tests/test_hourly_forecast.py`

- [ ] **Step 1: Write failing tests for time semantics and readiness**

Add tests that construct UTC hourly observations and assert:

```python
history = hourly_history(days=14, value=100)
result = forecast_hourly_demand(history, as_of=history[-1].bucket_at + timedelta(hours=1))
self.assertEqual(len(result.points), 24)
self.assertEqual(result.points[0].horizon, 1)
self.assertEqual(result.points[0].target_at, result.as_of)
self.assertEqual(result.points[-1].target_at, result.as_of + timedelta(hours=23))
```

Also assert that non-hour-aligned `as_of`, duplicate buckets, future observations, fewer than 7 days, and a latest complete hour older than 2 hours raise `ForecastInputError` or return `readiness="unavailable"` as defined by the public API.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_hourly_forecast.py -v
```

Expected: import failure because `hourly_forecast.py` does not exist.

- [ ] **Step 3: Implement immutable forecast types and validation**

Define:

```python
@dataclass(frozen=True, slots=True)
class HourlyObservation:
    bucket_at: datetime
    account_cost: float
    requests: float
    total_tokens: float

@dataclass(frozen=True, slots=True)
class ForecastPoint:
    horizon: int
    target_at: datetime
    p50: float
    p90: float
    candidate_count: int
    source: str

@dataclass(frozen=True, slots=True)
class ForecastResult:
    model: str
    version: str
    as_of: datetime
    readiness: str
    history_hours: int
    points: tuple[ForecastPoint, ...]
```

Validation must normalize aware datetimes to UTC, require natural-hour buckets, sort observations, reject duplicates and future buckets, and classify 7-13 days as `limited`, 14-55 days as `provisional`, and at least 56 days as `eligible`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Use the command from Step 2 and expect all readiness tests to pass.

### Task 2: Implement Robust Analog P50/P90 Forecasts

**Files:**
- Modify: `backend/app/modules/sub2api/hourly_forecast.py`
- Modify: `backend/tests/test_hourly_forecast.py`

- [ ] **Step 1: Write failing tests for candidate selection and quantiles**

Cover these behaviors independently:

- a constant 14-day series produces `p50=p90=constant` for all 24 horizons;
- candidate targets are always before `as_of`;
- a doubled recent three-hour context scales historical candidates upward;
- P90 is never below P50;
- same weekday candidates receive more weight than different day-type candidates;
- insufficient analog candidates fall back deterministically to seasonal values or the latest hour;
- identical input returns byte-for-byte equivalent serialized output.

- [ ] **Step 2: Run tests and verify RED**

Expected: assertions fail because only domain validation exists.

- [ ] **Step 3: Implement the analog model**

For each horizon, examine 1 to 28 day lags whose candidate target and three-hour context are strictly before `as_of`. Compute context ratios for positive `account_cost`, request, and Token sums; use their median as a `0.25..4.0` scale. Weight each candidate by exponential recency, Shanghai weekday agreement, and disagreement among context ratios. Return weighted quantiles at 0.50 and 0.90.

Keep helper functions pure:

```python
def weighted_quantile(values: list[tuple[float, float]], q: float) -> float: ...
def context_scale(current: Sequence[HourlyObservation], historical: Sequence[HourlyObservation]) -> tuple[float, float]: ...
def forecast_hourly_demand(history: Sequence[HourlyObservation], *, as_of: datetime, horizons: int = 24) -> ForecastResult: ...
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the focused unittest command and expect all model tests to pass.

### Task 3: Add Baselines And Rolling Backtest Metrics

**Files:**
- Create: `backend/app/modules/sub2api/hourly_forecast_backtest.py`
- Create: `backend/tests/test_hourly_forecast_backtest.py`

- [ ] **Step 1: Write failing metric tests**

Use small hand-calculated records to assert exact values for MAE, WAPE, signed bias, P50/P90 Pinball Loss, empirical coverage, normalized interval width, and 5:1 asymmetric risk loss. Include all-zero actuals and verify ratio metrics return `None` instead of dividing by zero.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_hourly_forecast_backtest.py -v
```

Expected: import failure for the new module.

- [ ] **Step 3: Implement metrics and horizon bands**

Define immutable `BacktestRecord` values and:

```python
def evaluate_records(records: Sequence[BacktestRecord]) -> dict[str, Any]: ...
def horizon_band(horizon: int) -> str: ...
```

The response must include overall metrics and `1h`, `2-3h`, `4-6h`, `7-12h`, and `13-24h` sections.

- [ ] **Step 4: Write failing rolling-origin and baseline tests**

Assert that each origin only passes earlier observations to the forecaster, that truth begins at the origin hour, and that persistence, 24-hour seasonal, and 168-hour seasonal forecasts follow the documented fallback order.

- [ ] **Step 5: Implement rolling-origin backtesting**

Add:

```python
def rolling_origin_backtest(
    history: Sequence[HourlyObservation],
    *,
    forecaster: ForecastCallable,
    horizons: int = 24,
    minimum_history_hours: int = 168,
    origin_step_hours: int = 1,
    evaluation_start: datetime | None = None,
) -> BacktestResult: ...
```

Return raw records plus overall, horizon-band, Shanghai-hour, weekday/weekend, and calendar-week metrics.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run all backtest tests and confirm they pass.

### Task 4: Read Complete Group Hours From PostgreSQL

**Files:**
- Create: `backend/app/modules/sub2api/hourly_forecast_repository.py`
- Create: `backend/tests/test_hourly_forecast_repository.py`

- [ ] **Step 1: Write failing repository contract tests**

Mock the SQLAlchemy engine boundary and assert that the query:

- filters one `group_id` and a half-open UTC range;
- groups by Shanghai natural hour;
- returns `account_cost`, requests, and total tokens;
- excludes the current partial hour;
- fills missing hours between the first and final complete observation with zeros;
- does not create hours before the first observed request.

- [ ] **Step 2: Run the focused test and verify RED**

Expected: module import failure.

- [ ] **Step 3: Implement the read-only repository**

Use the existing SQL DSN parser, `NullPool`, bounded timeout, `REPEATABLE READ`, parameterized SQL, and the same account cost expression as `account_usage_postgres_repository.py`. Return only `HourlyObservation` values; do not expose request rows or credentials.

- [ ] **Step 4: Run repository tests and verify GREEN**

Run the focused unittest file and inspect the SQL parameter assertions.

### Task 5: Add A Reproducible Forecast And Backtest CLI

**Files:**
- Create: `backend/scripts/backtest_group_hourly_forecast.py`
- Create: `backend/tests/test_backtest_group_hourly_forecast.py`

- [ ] **Step 1: Write failing CLI formatting tests**

Test pure report construction with a synthetic candidate result and three baseline results. Assert the JSON report contains model/version, site/group, history range, readiness, parameters, overall metrics, horizon bands, baseline comparison, and promotion-gate observations.

- [ ] **Step 2: Run the test and verify RED**

Expected: script module is missing.

- [ ] **Step 3: Implement the CLI**

Support:

```powershell
python scripts/backtest_group_hourly_forecast.py us06-5001 3 --history-days 60 --holdout-days 7 --origin-step-hours 1
```

The script must read the configured site SQL DSN through MongoDB, load complete group hours, run the candidate and all baselines on identical origins, print JSON only, never print credentials, and exit nonzero for unavailable data.

- [ ] **Step 4: Run CLI unit tests and verify GREEN**

Run the focused test and confirm deterministic JSON fields.

### Task 6: Real Backtest, Documentation, And Verification

**Files:**
- Create: `docs/forecasting/first-backtest-2026-07-19.md`
- Verify: `backend/tests/*.py`

- [ ] **Step 1: Run the real Plus group backtest**

Run the CLI for `us06-5001`, `group_id=3`, using all available history and a 7-day final holdout. Preserve the complete machine-readable JSON outside committed secrets and summarize metrics in the Markdown report.

- [ ] **Step 2: Interpret results without changing the gate**

Record candidate and baseline WAPE, P90 coverage, Pinball Loss, risk loss, horizon-band performance, history length, and why the result remains provisional when history is below 56 days. Do not tune the promotion thresholds to make the first model pass.

- [ ] **Step 3: Run the complete backend test suite**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: zero failures and zero errors.

- [ ] **Step 4: Run repository checks**

Run `git diff --check`, inspect `git status --short`, and confirm no generated credentials, raw request data, or connection strings were added.
