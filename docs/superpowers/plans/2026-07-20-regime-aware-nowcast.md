# Regime-Aware Nowcast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add direct minute cost sampling, sudden-rise detection, a calibrated dynamic Nowcast, and chronological comparison tooling.

**Architecture:** Extend the existing one-minute group counter without adding a new scheduler. Keep surge detection and blending as pure functions in a focused module, then feed its output through `capacity_risk` and the existing accuracy snapshots. Use PostgreSQL minute buckets for reproducible holdout evaluation before changing the model version.

**Tech Stack:** Python 3.14, Motor/MongoDB, SQLAlchemy/PostgreSQL, unittest

---

### Task 1: Direct Account-Cost Samples

**Files:**
- Modify: `backend/app/modules/sub2api/dashboard_postgres_repository.py`
- Modify: `backend/app/modules/sub2api/tpm_sampler.py`
- Modify: `backend/tests/test_sub2api_dashboard_postgres_repository.py`
- Modify: `backend/tests/test_tpm_sampler.py`

- [ ] Write failing tests requiring cumulative account cost and minute deltas.
- [ ] Verify RED.
- [ ] Add the parameterized aggregate and persist cost delta/rates with 60-day retention.
- [ ] Verify GREEN and counter-reset behavior.

### Task 2: Pure Surge Detector and Selector

**Files:**
- Create: `backend/app/modules/sub2api/regime_nowcast.py`
- Create: `backend/tests/test_regime_nowcast.py`

- [ ] Write failing tests for stable, warming, confirmed surge, cooling, direct-cost preference, and elapsed blending.
- [ ] Verify RED.
- [ ] Implement detector features, state output, and dynamic selection.
- [ ] Verify GREEN.

### Task 3: Capacity Integration

**Files:**
- Modify: `backend/app/modules/sub2api/capacity_risk.py`
- Modify: `backend/app/modules/sub2api/hourly_forecast.py`
- Modify: `backend/tests/test_capacity_risk.py`
- Modify: `backend/tests/test_hourly_forecast.py`

- [ ] Write failing tests proving surge output replaces the maximum rule and stable output avoids late-hour overestimation.
- [ ] Verify RED.
- [ ] Feed direct burn rate, detector stage, strength, confidence, blend weight, and selected remaining into the current-hour forecast.
- [ ] Verify GREEN and fallback behavior.

### Task 4: Minute Holdout Backtest

**Files:**
- Create: `backend/app/modules/sub2api/regime_nowcast_backtest.py`
- Create: `backend/app/modules/sub2api/minute_forecast_repository.py`
- Create: `backend/scripts/backtest_group_regime_nowcast.py`
- Create: `backend/tests/test_regime_nowcast_backtest.py`
- Create: `backend/tests/test_minute_forecast_repository.py`

- [ ] Write failing metric, time-boundary, and no-future-data tests.
- [ ] Verify RED.
- [ ] Implement minute observations, candidate comparison, event detection, and chronological report generation.
- [ ] Verify GREEN.

### Task 5: Real Evaluation and Release Decision

**Files:**
- Create: `docs/forecasting/regime-nowcast-backtest-2026-07-20.md`
- Modify only if gates pass: model/version constants and online selection defaults.

- [ ] Run Plus group holdout backtest against production PostgreSQL.
- [ ] Record WAPE, Bias, Coverage, Pinball, risk loss, surge recall, and delay.
- [ ] Switch online selector only if every documented gate passes.
- [ ] Run complete backend and frontend verification, inspect the diff, and commit.
