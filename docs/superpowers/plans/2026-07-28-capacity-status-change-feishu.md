# Capacity Status Change Feishu Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send exactly one Feishu notification whenever an enabled API-pool group moves between valid capacity health states.

**Architecture:** Add a pure state-transition decision beside the existing threshold decision, preserving the last valid status across `pending`. Reuse an existing alert/recovery delivery when it already covers the transition; otherwise send a dedicated status-change event only to active Feishu channels and store separate transition audit metadata.

**Tech Stack:** Python 3, Motor/MongoDB, existing notification service, `unittest` and `AsyncMock`.

---

### Task 1: Pure Capacity State Transition Decision

**Files:**
- Modify: `backend/app/modules/sub2api/capacity_notifications.py`
- Test: `backend/tests/test_capacity_notifications.py`

- [ ] **Step 1: Write failing state-transition tests**

Add tests for `capacity_status_change_decision(setting, summary, meta)` covering disabled notifications, missing baseline, unchanged status, valid improvement, valid deterioration, current `pending`, and `pending` as an invalid previous baseline. A valid change must return `send=True`, `previous_health_status`, `health_status`, and `reason="status_changed"`.

- [ ] **Step 2: Run the focused test and verify RED**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m unittest tests.test_capacity_notifications
```

Expected: failure because `capacity_status_change_decision` does not exist.

- [ ] **Step 3: Implement the minimal pure decision**

Define the six valid statuses in one constant. Return no transition for disabled groups, `pending`, missing/invalid previous state, and unchanged state; return a transition for every other valid status difference without comparing health rank.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the Task 1 command and expect all tests to pass.

### Task 2: Feishu Routing and Status-Change Message

**Files:**
- Modify: `backend/app/modules/sub2api/capacity_notifications.py`
- Test: `backend/tests/test_capacity_notifications.py`

- [ ] **Step 1: Write failing routing and text tests**

Test `_active_feishu_channel_ids(db)` with mixed active/inactive Feishu, DingTalk, and Telegram documents and expect only active Feishu IDs. Test `_capacity_status_change_text(...)` contains site, group, translated previous/current labels, pressure stage, runway, concurrency, reason, and Shanghai-local change time.

- [ ] **Step 2: Run focused tests and verify RED**

Run the Task 1 command. Expected: failures because both helpers are absent.

- [ ] **Step 3: Implement routing and message helpers**

Query `notification_channels` with `{"status": "active", "channel_type": "feishu"}` and project `_id`. Format the message with existing `_runway_hours`, `_multiple`, `HEALTH_LABELS`, and `SHANGHAI_TZ`; add severity mapping for all valid current states.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 command and expect all tests to pass.

### Task 3: Integrate, De-Duplicate, and Persist Transition Audits

**Files:**
- Modify: `backend/app/modules/sub2api/capacity_notifications.py`
- Test: `backend/tests/test_capacity_notifications.py`

- [ ] **Step 1: Write failing delivery integration tests**

Add tests proving:

```text
abundant -> healthy with no threshold delivery
  => one sub2api.capacity.status_changed event
  => channel_ids contains only active Feishu IDs
  => active_alert remains unchanged

tight -> danger with existing status-worsened alert
  => one send_notification_event call total
  => event type remains sub2api.capacity.low
  => transition audit fields point to that event

pending current status
  => does not overwrite last_observed_status
```

Also assert dedicated state-change delivery does not update `last_attempt_at` or `last_notification_type`.

- [ ] **Step 2: Run focused tests and verify RED**

Run the Task 1 command. Expected: the dedicated event is absent, duplicate prevention audit fields are absent, or `pending` overwrites the valid baseline.

- [ ] **Step 3: Integrate the transition path**

Compute both decisions before updates. Only set `last_observed_status` for valid current states. If the threshold decision sends, execute the existing alert/recovery path once and attach transition audit fields to the same event. If it does not send but a transition exists, call `send_notification_event` with `event_type="sub2api.capacity.status_changed"` and active Feishu `channel_ids`; persist only state-change audit fields plus ordinary observation fields.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 1 command and expect all tests to pass.

### Task 4: Documentation and Full Verification

**Files:**
- Modify: `docs/design/30-api-pool-realtime-capacity-and-presence.md`

- [ ] **Step 1: Update maintained notification documentation**

Document valid statuses, baseline behavior, `pending` preservation, Feishu-only routing, alert/recovery de-duplication, event type, and state-change audit fields.

- [ ] **Step 2: Run the complete backend suite**

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Expected: all backend tests pass.

- [ ] **Step 3: Run repository checks**

```powershell
git diff --check
git status --short --branch
```

Expected: no whitespace errors and only the capacity notification implementation, tests, plan, and maintained documentation are part of this task; unrelated concurrent changes remain untouched.
