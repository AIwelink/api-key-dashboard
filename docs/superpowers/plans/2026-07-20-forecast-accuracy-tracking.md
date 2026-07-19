# Forecast Accuracy Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist forecast outcomes, measure hourly and current-hour Nowcast accuracy, and expose rolling accuracy evidence on the API pool status page.

**Architecture:** Store one deterministic evaluation document per forecast point or five-minute Nowcast sample. A ten-minute background evaluator reads completed natural hours from PostgreSQL, writes provisional results after 15 minutes and final results after 90 minutes, then materializes compact 24-hour, 7-day, and 28-day summaries in MongoDB for the existing group capacity response.

**Tech Stack:** Python 3.14, FastAPI lifespan tasks, Motor/MongoDB, SQLAlchemy PostgreSQL reads, React 19, TypeScript, Vitest.

---

### Task 1: Pure Evaluation Model

**Files:**
- Create: `backend/app/modules/sub2api/hourly_forecast_evaluation.py`
- Test: `backend/tests/test_hourly_forecast_evaluation.py`

- [ ] **Step 1: Write failing tests for hourly evaluations**

Cover signed P50/P90 errors, absolute error, P90 coverage, pinball loss, Shanghai local-hour dimensions, and deterministic IDs.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `backend/.venv/Scripts/python.exe -m unittest backend/tests/test_hourly_forecast_evaluation.py`

Expected: import failure because the evaluation module does not exist.

- [ ] **Step 3: Implement hourly and Nowcast evaluation builders**

Each persisted document copies prediction values and model metadata so it remains useful after the seven-day forecast cache expires. Nowcast actual remaining demand is clamped to `max(0, final_hour_cost - observed_cost_at_issue)`.

- [ ] **Step 4: Implement rolling metrics**

Calculate `WAPE = sum(abs(error)) / sum(actual)`, normalized Bias, MAE, P90 coverage, and average pinball loss. Build horizon buckets `1h`, `2-3h`, `4-6h`, `7-12h`, and `13-24h`, plus local-hour, day-type, pressure-stage, and constrained/unconstrained segments.

- [ ] **Step 5: Run the focused test and verify GREEN**

Run: `backend/.venv/Scripts/python.exe -m unittest backend/tests/test_hourly_forecast_evaluation.py`

Expected: all evaluation tests pass.

### Task 2: Settlement Service and Storage

**Files:**
- Create: `backend/app/modules/sub2api/hourly_forecast_evaluation_service.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_hourly_forecast_evaluation_service.py`
- Test: `backend/tests/test_hourly_forecast_evaluation.py`

- [ ] **Step 1: Write failing service tests**

Verify provisional settlement at target-hour end plus 15 minutes, final replacement at plus 90 minutes, final-result idempotence, one PostgreSQL range read per site/group, and rolling summary persistence.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `backend/.venv/Scripts/python.exe -m unittest backend/tests/test_hourly_forecast_evaluation_service.py`

Expected: import failure because the service does not exist.

- [ ] **Step 3: Implement evaluator and summary cache**

Scan candidate forecast points and Nowcast capacity samples, skip already-final evaluations, batch actual-hour reads by site/group, upsert deterministic documents, and isolate failures by group. Backfill seven days on startup and use a four-hour incremental lookback afterward.

- [ ] **Step 4: Add indexes and lifecycle task**

Create evaluation lookup and 180-day TTL indexes, summary lookup indexes, start the ten-minute loop in FastAPI lifespan, and cancel it during shutdown.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `backend/.venv/Scripts/python.exe -m unittest backend/tests/test_hourly_forecast_evaluation_service.py backend/tests/test_hourly_forecast_evaluation.py`

Expected: all settlement and index tests pass.

### Task 3: Capacity Response Integration

**Files:**
- Modify: `backend/app/modules/sub2api/cache.py`
- Modify: `backend/tests/test_capacity_risk_integration.py`

- [ ] **Step 1: Write a failing integration test**

Require the group capacity summary to include the cached `forecast_accuracy` document without failing in tests or deployments where the new collection is not yet available.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `backend/.venv/Scripts/python.exe -m unittest backend/tests/test_capacity_risk_integration.py`

- [ ] **Step 3: Merge the compact accuracy summary**

Read one summary document by `site_id` and `group_id`, serialize it through the existing response path, and return an explicit waiting state before final samples exist.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `backend/.venv/Scripts/python.exe -m unittest backend/tests/test_capacity_risk_integration.py`

### Task 4: API Pool Status UI

**Files:**
- Modify: `frontend/src/pages/ApiPoolStatusPage.tsx`
- Modify: `frontend/styles.css`
- Create: `frontend/src/pages/ApiPoolForecastAccuracy.test.ts`

- [ ] **Step 1: Write a failing source-presence test**

Require the 24h/7d/28d controls, P50 WAPE, P90 coverage, Bias, Nowcast WAPE, sample count, settlement time, and horizon breakdown labels.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `npm.cmd test -- ApiPoolForecastAccuracy.test.ts`

- [ ] **Step 3: Implement the responsive accuracy band**

Place it directly after capacity estimation, use a compact segmented window control, display a waiting state for insufficient final samples, and keep horizon metrics readable on mobile without nested cards.

- [ ] **Step 4: Run frontend tests and verify GREEN**

Run: `npm.cmd test`

### Task 5: Verification and Commit

**Files:**
- Modify: `backend/README.md`

- [ ] **Step 1: Document collections and settlement semantics**

Document `sub2api_forecast_evaluations`, `sub2api_forecast_accuracy_summaries`, provisional/final timing, and retention.

- [ ] **Step 2: Run complete backend tests**

Run: `backend/.venv/Scripts/python.exe -m unittest discover -s backend/tests`

- [ ] **Step 3: Run complete frontend tests and production build**

Run: `npm.cmd test` and `npm.cmd run build` from `frontend`.

- [ ] **Step 4: Inspect the final diff and commit**

Confirm no unrelated files changed, then commit on `achernar/dev` with an intentional feature message.
