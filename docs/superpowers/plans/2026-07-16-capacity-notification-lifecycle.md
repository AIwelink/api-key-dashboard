# Capacity Notification Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make capacity notifications repeat only at danger/exhausted severity, send worsening alerts immediately, send one recovery notification, and include enough single-pool metrics for operators to act.

**Architecture:** Keep threshold and cooldown policy in the pure `capacity_notification_decision()` function. Extend `_evaluate_group_capacity_notification()` to dispatch either an alert or recovery event from that decision, while retaining the existing notification channel fan-out. Keep alert and recovery text generation separate so each message remains concise and testable.

**Tech Stack:** Python 3.14, asyncio, Motor/MongoDB, unittest, existing notification event service

---

### Task 1: Notification lifecycle decision

**Files:**
- Modify: `backend/tests/test_capacity_notifications.py`
- Modify: `backend/app/modules/sub2api/capacity_notifications.py`

- [ ] **Step 1: Write failing decision tests**

Add tests proving that an active `tight` alert does not repeat after cooldown, `danger` and `exhausted` do repeat after cooldown, worsening bypasses cooldown, healthy recovery returns `send=True` with `notification_type="recovery"`, and pending data does not recover an alert.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `..\.venv\Scripts\python.exe -m unittest discover -s tests -p test_capacity_notifications.py -v`

Expected: failures for tight cooldown suppression and recovery because the current function repeats every below-threshold status and never emits recovery.

- [ ] **Step 3: Implement the lifecycle policy**

Return a decision containing `notification_type` (`alert`, `recovery`, or `None`) and an explicit reason. Preserve an active alert while health is `pending` or only partially recovered to `tight`; clear it only when disabled or after a recovery attempt. Apply cooldown repeats only when current health is `danger` or `exhausted`.

- [ ] **Step 4: Re-run the focused tests and verify GREEN**

Run the command from Step 2 and expect all decision tests to pass.

### Task 2: Alert and recovery delivery

**Files:**
- Modify: `backend/tests/test_capacity_notifications.py`
- Modify: `backend/app/modules/sub2api/capacity_notifications.py`

- [ ] **Step 1: Write failing async delivery tests**

Patch `send_notification_event` and verify a recovery decision sends event type `sub2api.capacity.recovered`, uses success severity, stores `active_alert=False`, and records `last_recovered_at`. Verify normal alerts continue using `sub2api.capacity.low`.

- [ ] **Step 2: Run the focused tests and verify RED**

Expected: recovery delivery test fails because the existing evaluator exits without sending whenever health is above the warning threshold.

- [ ] **Step 3: Implement one dispatch path for both event types**

Build title, text, event type, severity, and dedupe key from `notification_type`. Update metadata after the attempt; recovery ends the active alert, while alert delivery keeps it active and updates the last notified status.

- [ ] **Step 4: Re-run the focused tests and verify GREEN**

Run the command from Task 1 Step 2 and expect all lifecycle and delivery tests to pass.

### Task 3: Operator-focused message content

**Files:**
- Modify: `backend/tests/test_capacity_notifications.py`
- Modify: `backend/app/modules/sub2api/capacity_notifications.py`

- [ ] **Step 1: Write failing text assertions**

Require alert text to contain the health reason, 5h actual/dynamic/capacity dollars, 7d actual/dynamic/capacity dollars, actual/dynamic runway, TPM/RPM, concurrency coverage, available accounts, and suggested refill count. Require recovery text to contain recovered status, runway, concurrency coverage, and recovery time.

- [ ] **Step 2: Run the focused tests and verify RED**

Expected: alert assertions for actual dollars fail and `_capacity_recovery_text` is missing.

- [ ] **Step 3: Implement compact alert and recovery formatters**

Use existing `_hours`, `_metric`, `_multiple`, and `_money` formatters. Keep one metric family per line and use `-` for unavailable values rather than inventing zero capacity.

- [ ] **Step 4: Re-run the focused tests and verify GREEN**

Run the command from Task 1 Step 2 and expect all notification tests to pass.

### Task 4: Full verification

**Files:**
- Verify: `backend/tests/*.py`
- Verify: `frontend`

- [ ] **Step 1: Run all backend tests**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` from `backend`.

Expected: all tests pass.

- [ ] **Step 2: Build the frontend**

Run: `npm run build` from `frontend`.

Expected: TypeScript and Vite build pass; the existing chunk-size warning may remain.

- [ ] **Step 3: Validate the diff**

Run: `git diff --check` and inspect `git status --short` to confirm only planned files changed.
