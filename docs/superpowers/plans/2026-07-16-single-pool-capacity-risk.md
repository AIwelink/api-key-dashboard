# Single-Pool Capacity Risk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace reserve-inclusive, day-scale capacity alerts with a single-pool, minute-rate risk model targeting three hours of dynamic capacity, one hour of immediately usable quota, and 1.2x safe concurrency coverage.

**Architecture:** Add a focused `capacity_risk.py` module for minute-series statistics and pure risk decisions. The existing cache module supplies actual account quota and concurrency data, loads the group risk summary, and falls back to historical health until minute data is ready. The React status page renders the new operational metrics without removing legacy pool screens.

**Tech Stack:** Python 3.14, asyncio, Motor/MongoDB, unittest, React, TypeScript, Vite

---

### Task 1: Extend TPM samples

**Files:**
- Modify: `backend/app/modules/sub2api/tpm_sampler.py`
- Modify: `backend/tests/test_tpm_sampler.py`

- [ ] Add a failing assertion that `average_duration_ms` is stored from dashboard stats.
- [ ] Run `test_tpm_sampler.py` and verify RED.
- [ ] Store a validated non-negative duration in each minute sample.
- [ ] Re-run the focused test and verify GREEN.

### Task 2: Build pure minute-risk calculations

**Files:**
- Create: `backend/app/modules/sub2api/capacity_risk.py`
- Create: `backend/tests/test_capacity_risk.py`

- [ ] Write failing tests for EMA/P90 pressure TPM, freshness, pressure phases, runway thresholds, concurrency coverage, and refill account calculations.
- [ ] Run `test_capacity_risk.py` and verify RED because the module is missing.
- [ ] Implement pure helpers and `calculate_capacity_risk` with the constants defined in the design.
- [ ] Re-run focused tests and verify GREEN.

### Task 3: Integrate single-pool summaries

**Files:**
- Modify: `backend/app/modules/sub2api/cache.py`
- Modify: `backend/tests/test_capacity_limits.py`

- [ ] Write failing async tests proving reserve capacity is not queried or included and real-time health replaces day-scale health when data is ready.
- [ ] Extend dashboard cost summary with recent six-hour cost per token.
- [ ] Load up to six hours of group TPM samples and call the pure risk module.
- [ ] Force reserve summary and reserve output fields to zero, set `auto_refill_required=false`, and expose replenishment fields.
- [ ] Verify focused capacity tests pass.

### Task 4: Update notifications and status UI

**Files:**
- Modify: `backend/app/modules/sub2api/capacity_notifications.py`
- Modify: `backend/tests/test_capacity_notifications.py`
- Modify: `frontend/src/pages/ApiPoolStatusPage.tsx`

- [ ] Add a failing notification text test for pressure stage, runway, TPM/RPM, concurrency coverage, and recommended refill accounts.
- [ ] Update notification formatting and verify focused tests pass.
- [ ] Add TypeScript fields and render compact cards for real-time runway, pressure stage, and concurrency coverage.
- [ ] Remove reserve values and reserve overlays from capacity cards while leaving pool navigation untouched.
- [ ] Replace the capacity help text with the new 30-minute/1-hour/3-hour/1.2x rules.

### Task 5: Verify

**Files:**
- Test: `backend/tests/*.py`
- Build: `frontend`

- [ ] Run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` from `backend`.
- [ ] Run `npm run build` from `frontend`.
- [ ] Run `git diff --check` and inspect the scoped diff.
