# Plus Pool Revalidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revalidate accounts in Plus group 6 and automatically correct false Plus classifications or route unauthorized Plus accounts to error group 10.

**Architecture:** The existing serial probe becomes a two-source state machine over groups 4 and 6. Group 4 retains promotion and banned routing; group 6 remains unchanged on success/429, reverts to group 4 with `plan_type=free` on the specific unsupported-model 400, and moves to group 10 on 401. The status API and page expose both reverse routes and their action counts.

**Tech Stack:** Python 3.14, FastAPI/httpx, MongoDB result records, PostgreSQL pool snapshots, React/TypeScript, `unittest`, Vitest

---

### Task 1: Add failing backend state-machine tests

**Files:**
- Modify: `backend/tests/test_plus_self_produced.py`

- [x] **Step 1: Test Plus-prefix removal**

Add assertions that `free_account_name("plus user@example.com")`, `free_account_name("plususer@example.com")`, and the uppercase variant return the original non-prefixed name.

- [x] **Step 2: Test group 6 correction routes**

Create group 6 accounts whose probe results are success, 429, the exact unsupported-model 400, 401, and an unrelated failure. Assert that only the unsupported-model account receives:

```python
{
    "name": "free@example.com",
    "group_id": 4,
    "group_ids": [4],
    "credentials": {"plan_type": "free"},
}
```

Assert that the group 6 401 account receives only `{"group_id": 10, "group_ids": [10]}` and that successful/429 group 6 accounts are not updated.

- [x] **Step 3: Verify the backend test fails**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_plus_self_produced -v`

Expected: FAIL because group 6 is not selected and no reverse transitions exist.

### Task 2: Implement group-aware probe routing

**Files:**
- Modify: `backend/app/modules/sub2api/plus_self_produced.py`

- [x] **Step 1: Add fixed group and name helper**

Add `PLUS_ERROR_GROUP_ID = 10` and a `free_account_name()` helper that removes one case-insensitive leading `plus` plus optional whitespace.

- [x] **Step 2: Select both source pools**

Require groups 4, 6, 7, and 10 to exist, and select accounts belonging to group 4 or group 6 exactly once.

- [x] **Step 3: Route by current group and classification**

Keep group 4 behavior unchanged. For group 6, leave success/429 in place with `verified_plus`; route `model_not_supported` to group 4 with the restored name and Free credentials; route 401 to group 10 without changing the name or credentials.

- [x] **Step 4: Verify backend tests pass**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_plus_self_produced -v`

Expected: PASS.

### Task 3: Expose correction routes on the page

**Files:**
- Modify: `frontend/src/pages/PlusSelfProducedPage.tsx`
- Modify: `frontend/src/pages/PlusSelfProducedPage.test.tsx`

- [x] **Step 1: Add failing frontend assertions**

Assert the page renders `6 → 4`, `6 → 10`, `已还原 Free`, and `Plus 错误池` from the status/result fixture.

- [x] **Step 2: Verify the frontend test fails**

Run: `npx.cmd vitest run --configLoader runner src/pages/PlusSelfProducedPage.test.tsx`

Expected: FAIL because the current status contract and labels do not expose the reverse flow.

- [x] **Step 3: Update status types, workflow facts, counters, toast, and action labels**

Add `plus_error_group_id`, `downgraded`, and `plus_errors` fields and render the two corrective routes and action statuses.

- [x] **Step 4: Run full verification**

Run backend `unittest discover`, all Vitest tests, the frontend production build, and `git diff --check`. All must exit successfully.
