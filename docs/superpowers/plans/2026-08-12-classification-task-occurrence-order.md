# Classification Task Occurrence Ordering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return classification tasks in descending business occurrence time with deterministic task-level tie-breakers.

**Architecture:** Keep ordering ownership in the operations PostgreSQL repository, where the full result set is queried. Add a focused repository contract test that captures the generated SQL, then make the smallest possible `ORDER BY` change; the service, API schema, and frontend continue to preserve backend order.

**Tech Stack:** Python 3.12, SQLAlchemy text queries, `unittest.IsolatedAsyncioTestCase`, PostgreSQL

---

### Task 1: Lock the classification ordering contract

**Files:**
- Modify: `backend/tests/test_operations_repository.py`
- Test: `backend/tests/test_operations_repository.py`

- [x] **Step 1: Write the failing repository test**

Add this test to `OperationsRepositoryTests`:

```python
async def test_classification_tasks_sort_by_business_occurrence_time(self) -> None:
    from app.modules.operations.repository import list_classification_tasks

    connection = _FakeConnection([None])

    await list_classification_tasks(
        connection,
        allowed_site_ids=("aiwelink",),
    )

    statement, _ = connection.calls[0]
    normalized_statement = " ".join(statement.split())
    self.assertIn(
        "ORDER BY event.occurred_at DESC, task.created_at DESC, "
        "task.classification_task_id DESC",
        normalized_statement,
    )
```

- [x] **Step 2: Run the focused test and verify RED**

Run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_operations_repository.OperationsRepositoryTests.test_classification_tasks_sort_by_business_occurrence_time -v
```

Expected: `FAIL`; the captured SQL only contains `ORDER BY task.created_at DESC`.

- [x] **Step 3: Commit the regression test only after observing RED**

Do not commit at RED. Continue immediately to the minimal implementation so the branch is not left with an intentionally failing test.

### Task 2: Order classification tasks by occurrence time

**Files:**
- Modify: `backend/app/modules/operations/repository.py`
- Test: `backend/tests/test_operations_repository.py`

- [x] **Step 1: Apply the minimal repository change**

Replace the existing ordering in `list_classification_tasks()` with:

```sql
ORDER BY event.occurred_at DESC,
         task.created_at DESC,
         task.classification_task_id DESC
```

- [x] **Step 2: Run the focused test and verify GREEN**

Run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_operations_repository.OperationsRepositoryTests.test_classification_tasks_sort_by_business_occurrence_time -v
```

Expected: `OK` with one passing test.

- [x] **Step 3: Run the complete backend suite**

Run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Expected: all tests pass with zero failures and zero errors.

- [x] **Step 4: Verify repository cleanliness and scope**

Run from the repository root:

```powershell
git diff --check
git status --short
git diff -- backend/app/modules/operations/repository.py backend/tests/test_operations_repository.py docs/superpowers/plans/2026-08-12-classification-task-occurrence-order.md
```

Expected: no whitespace errors, and the implementation diff contains only the plan, focused test, and three-key `ORDER BY` change.

- [x] **Step 5: Commit and push the completed change**

```powershell
git add -- docs/superpowers/plans/2026-08-12-classification-task-occurrence-order.md backend/tests/test_operations_repository.py backend/app/modules/operations/repository.py
git commit -m "fix classification task occurrence ordering"
git push origin codex/redemption-code-list
```

Expected: the current PR branch contains the design commit and the verified implementation commit.
