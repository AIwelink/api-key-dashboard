# Plus Plan Type Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set `credentials.plan_type` to `plus` in the same Sub2API update that promotes an eligible plus自产 account.

**Architecture:** The plus自产 workflow adds a minimal nested credential update only for successful and 429 probes. `Sub2ApiClient` detects credential updates, fetches the latest account, deep-merges credentials, and sends one complete PUT so existing credential fields are preserved. The 401 path remains a group-only move.

**Tech Stack:** Python 3.14, FastAPI/httpx, `unittest`

---

### Task 1: Preserve credentials in complete account PUTs

**Files:**
- Modify: `backend/app/modules/sub2api/client.py`
- Test: `backend/tests/test_sub2api_client_update.py`

- [x] **Step 1: Write the failing test**

Add a client test that calls `update_account()` with `credentials={"plan_type": "plus"}` and verifies that it fetches the latest account, preserves all existing credential fields, overlays `plan_type`, and sends a complete PUT without first attempting PATCH.

- [x] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_sub2api_client_update -v`

Expected: FAIL because the current client sends PATCH first and shallowly replaces `credentials`.

- [x] **Step 3: Write minimal implementation**

Update `build_account_put_payload()` to deep-merge credential dictionaries. When `update_account()` receives a credential update, fetch the current account and use the complete PUT path directly.

- [x] **Step 4: Run test to verify it passes**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_sub2api_client_update -v`

Expected: both client update tests PASS.

### Task 2: Mark promoted accounts as Plus

**Files:**
- Modify: `backend/app/modules/sub2api/plus_self_produced.py`
- Test: `backend/tests/test_plus_self_produced.py`

- [x] **Step 1: Write the failing test**

Update the promotion assertions so successful and 429 account payloads include `credentials={"plan_type": "plus"}`, while the 401 payload contains no credential update.

- [x] **Step 2: Run test to verify it fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_plus_self_produced -v`

Expected: FAIL because promotion payloads currently contain only name and group fields.

- [x] **Step 3: Write minimal implementation**

Extend `_move_payload()` with an optional plan type and pass `plus` only from the eligible promotion branch.

- [x] **Step 4: Run tests and verify the repository**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s tests`

Expected: all backend tests PASS. Also run frontend tests and the production build to verify the existing page remains compatible.
