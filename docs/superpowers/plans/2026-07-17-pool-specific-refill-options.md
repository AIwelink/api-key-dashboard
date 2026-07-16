# Pool-Specific Refill Options Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return and display pool-bound equivalent refill options, with Plus pools offering Plus/K12 alternatives and Pro pools offering only Pro.

**Architecture:** The pure real-time risk model will calculate one recommendation per supplied account option from the same quota, concurrency, and account-floor gaps. The cache layer owns pool binding and passes only allowed account types. Frontend and notification formatters consume the same structured `recommended_refill_options` response and retain the legacy scalar recommendation for compatibility.

**Tech Stack:** Python 3.14, unittest, React, TypeScript, Vite

---

### Task 1: Calculate equivalent refill options

**Files:**
- Modify: `backend/tests/test_capacity_risk.py`
- Modify: `backend/app/modules/sub2api/capacity_risk.py`

- [ ] Add a failing test that supplies Plus `110/110` and K12 `20/100` limits and expects independent option counts from the same capacity gap.
- [ ] Run `..\.venv\Scripts\python.exe -m unittest discover -s tests -p test_capacity_risk.py -v` and verify RED.
- [ ] Add an optional `refill_account_options` input and return `recommended_refill_options` entries containing account type, quota count, concurrency count, and final count.
- [ ] Preserve `recommended_refill_accounts` by selecting the primary option count, and return empty options while data is pending or replenishment is not required.
- [ ] Re-run the focused test and verify GREEN.

### Task 2: Bind options to the current pool type

**Files:**
- Modify: `backend/tests/test_capacity_limits.py`
- Modify: `backend/app/modules/sub2api/cache.py`

- [ ] Add failing tests proving Plus selects `plus,k12`, Pro selects only `pro`, and other pools select only their primary type.
- [ ] Run `..\.venv\Scripts\python.exe -m unittest discover -s tests -p test_capacity_limits.py -v` and verify RED.
- [ ] Add a pure cache helper that filters site-specific capacity limits using the pool binding rules.
- [ ] Pass the filtered options and primary type into `calculate_capacity_risk`.
- [ ] Re-run capacity limit and risk integration tests and verify GREEN.

### Task 3: Present the options consistently

**Files:**
- Modify: `backend/tests/test_capacity_notifications.py`
- Modify: `backend/app/modules/sub2api/capacity_notifications.py`
- Modify: `frontend/src/pages/ApiPoolStatusPage.tsx`

- [ ] Add a failing notification assertion for `建议动作：补 Plus X 个，或补 K12 Y 个。仅供参考，请结合实时供货和账号质量判断。`.
- [ ] Update notification formatting to prefer structured options and retain the legacy scalar fallback.
- [ ] Add the structured response type and a shared frontend formatter.
- [ ] Replace scalar suggestions in the concurrency card and health reason with pool-specific alternatives plus the reference disclaimer.
- [ ] Run focused notification tests and `npm run build`; both must pass.

### Task 4: Verify the complete change

**Files:**
- Verify: `backend/tests/*.py`
- Verify: `frontend`

- [ ] Run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` from `backend`.
- [ ] Run `npm run build` from `frontend`.
- [ ] Run `git diff --check` and inspect `git status --short`.
