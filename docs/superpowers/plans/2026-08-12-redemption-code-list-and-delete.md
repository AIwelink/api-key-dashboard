# Redemption Code List And Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secure, permission-aware redemption-code list with current-user-first ordering, temporary plaintext reveal, and single or batch deletion of unused codes.

**Architecture:** Sub2API remains authoritative for code value and status. The operations service fetches remote codes, joins them to Growth batch attribution, removes plaintext before returning list rows, sorts current-user-created rows first, and paginates the combined result. A focused React table component owns list rendering and selection while the existing operations page owns queries, network calls, reveal, and confirmation dialogs.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy async PostgreSQL, Motor MongoDB, httpx, React 19, TypeScript, Vitest.

---

### Task 1: Sub2API Redemption Management Contract

**Files:**
- Modify: `backend/app/modules/sub2api/client.py`
- Modify: `backend/app/modules/operations/credit_commands.py`
- Test: `backend/tests/test_sub2api_client_update.py`
- Test: `backend/tests/test_operations_credit_commands.py`

- [ ] **Step 1: Write failing client tests**

Add async tests using `httpx.MockTransport` for `list_redemption_codes`, `get_redemption_code`, `delete_redemption_code`, and `batch_delete_redemption_codes`. Assert exact official Sub2API paths and envelope normalization.

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_sub2api_client_update -v
```

Expected: failures because the four client methods do not exist.

- [ ] **Step 3: Implement client methods**

Add methods that call:

```text
GET    /redeem-codes
GET    /redeem-codes/{id}
DELETE /redeem-codes/{id}
POST   /redeem-codes/batch-delete
```

Normalize the Sub2API `{code,message,data}` envelope and validate list/item/delete result shapes without logging response bodies.

- [ ] **Step 4: Write and run failing adapter tests**

Test adapter pass-through for list, get, delete, and batch delete, including the capability error for non-Sub2API sites.

- [ ] **Step 5: Implement adapter methods and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_sub2api_client_update tests.test_operations_credit_commands -v
```

Expected: all selected tests pass.

### Task 2: Growth Attribution And Secure Domain Logic

**Files:**
- Modify: `backend/app/modules/operations/repository.py`
- Modify: `backend/app/modules/operations/service.py`
- Test: `backend/tests/test_operations_repository.py`
- Test: `backend/tests/test_operations_credit_commands.py`

- [ ] **Step 1: Write failing attribution repository test**

Assert `list_redemption_batch_attributions` selects successful batches for one site and returns `source_batch_id`, `code_masks`, `requested_by`, and `created_at` in newest-first order.

- [ ] **Step 2: Run repository test and verify RED**

Run the exact new unittest and expect an import failure for the missing repository function.

- [ ] **Step 3: Implement attribution query**

Add a read-only query scoped by `site_id`; do not return hashes or plaintext.

- [ ] **Step 4: Write failing service tests**

Cover these behaviors with remote adapter and Growth connection fakes:

```text
- management-panel IDs join to requested_by and stored masks
- unmatched IDs become api_site origin
- plaintext code is absent from every list item and from repr(response)
- current actor rows sort before all others
- each group sorts by created_at DESC then remote ID DESC
- origin filtering occurs before pagination
- reaching the remote fetch limit sets truncated=true
- reveal returns plaintext only from a single remote item
- delete rejects any status other than unused
- batch delete validates all records before invoking remote deletion
```

- [ ] **Step 5: Run service tests and verify RED**

Expected: missing service functions and error types.

- [ ] **Step 6: Implement secure service operations**

Add focused functions for list, reveal, single delete, and batch delete. Resolve creator labels from MongoDB users when possible and use the current actor email for the current user's rows. Use a stable error code `redemption_code_not_deletable` for status conflicts.

- [ ] **Step 7: Verify service GREEN**

Run repository and credit-command suites and require all tests to pass.

### Task 3: Schemas, Routes, Permissions, No-Store, And Audit

**Files:**
- Modify: `backend/app/modules/operations/schemas.py`
- Modify: `backend/app/routers/operations.py`
- Test: `backend/tests/test_operations_domain.py`
- Test: `backend/tests/test_operations_routes.py`

- [ ] **Step 1: Write failing schema and route tests**

Test:

```text
- page >= 1, page_size <= 100, search <= 100
- batch code_ids contains 1..100 unique positive IDs
- list is available to a page-authorized read-only actor
- reveal/delete/batch-delete require owner/admin
- all routes enforce operations_site_ids
- reveal sets Cache-Control: no-store
- reveal audit contains only site_id, code_id, and mask
- delete audits masks and IDs without plaintext
```

- [ ] **Step 2: Run route tests and verify RED**

Expected: missing schemas/routes.

- [ ] **Step 3: Implement schemas and routes**

Add `RedemptionCodeListQuery` and `RedemptionCodeBatchDelete`. Mount the four specified endpoints before the existing `/{...}`-free routes so paths are unambiguous. Map the not-deletable error to HTTP 409 with structured detail.

- [ ] **Step 4: Verify route GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_operations_domain tests.test_operations_routes -v
```

Expected: all selected tests pass.

### Task 4: Focused Redemption Code Table Component

**Files:**
- Create: `frontend/src/pages/operations/RedemptionCodeTable.tsx`
- Create: `frontend/src/pages/operations/RedemptionCodeTable.test.tsx`
- Modify: `frontend/src/pages/OperationsManagementPage.css`

- [ ] **Step 1: Write failing render tests**

Render representative rows and assert:

```text
- columns for mask, site, value, status, origin, creator, created/used details
- labels 管理面板创建 and API站点创建
- only unused rows have enabled selection/deletion for writable roles
- read-only mode has no checkbox, reveal, or delete buttons
- pagination buttons have stable accessible names
```

- [ ] **Step 2: Run component test and verify RED**

Run:

```powershell
cd frontend
npm test -- --run src/pages/operations/RedemptionCodeTable.test.tsx
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement the table**

Create exported row/response/filter types and a stateless component. Use existing table and button classes, semantic checkboxes, text status tags, and icon-compatible compact action buttons. Do not sort rows in React.

- [ ] **Step 4: Add restrained responsive styling and verify GREEN**

Keep the table full-width with horizontal scrolling and fixed minimum column widths. Run the component test until green.

### Task 5: Operations Page Data Flow And Interactions

**Files:**
- Modify: `frontend/src/pages/OperationsManagementPage.tsx`
- Modify: `frontend/src/pages/OperationsManagementPage.test.tsx`
- Test: `frontend/src/pages/operations/RedemptionCodeTable.test.tsx`

- [ ] **Step 1: Write failing workspace tests**

Assert the credits tab renders the redemption query controls and table above conversion rates. Assert current-page results are rendered in server order and operator mode is masked/read-only.

- [ ] **Step 2: Run tests and verify RED**

Expected: missing controls/table text.

- [ ] **Step 3: Implement list state and loading**

Add site, status, origin, search, page, loading, row, and selection state. Load only while the credits tab is active; reset page on filter changes; retain existing rows on background failure.

- [ ] **Step 4: Implement reveal and memory cleanup**

Open a no-persistence modal with the fetched plaintext. Clear it on close, tab switch, permission/site reset, and component unmount.

- [ ] **Step 5: Implement single and batch delete confirmations**

Use the existing `ConfirmDialog`. Refresh the list after success; clear deleted selections; move to the previous page if the current page becomes empty.

- [ ] **Step 6: Verify frontend GREEN**

Run both focused frontend test files and require all tests to pass.

### Task 6: Full Verification And PR

**Files:**
- Modify only files required by verification fixes.

- [ ] **Step 1: Run backend full suite**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Expected: zero failures.

- [ ] **Step 2: Run frontend full suite and build**

```powershell
cd frontend
npm test -- --run
npm run build
```

Expected: zero failures and successful TypeScript/Vite build.

- [ ] **Step 3: Run static checks**

```powershell
git diff --check
```

Expected: no output and exit code 0.

- [ ] **Step 4: Run browser verification**

Start the local development server, open the credits tab at desktop and mobile widths, and verify the table, query controls, dialogs, horizontal overflow, disabled states, and absence of overlap. Do not call a real production deletion endpoint.

- [ ] **Step 5: Commit and push**

Commit implementation with a focused message, push `codex/redemption-code-list`, and create a draft PR to `achernar/dev` with root cause, security boundary, and verification evidence.

- [ ] **Step 6: Watch PR checks**

Require backend and frontend GitHub checks to pass before reporting completion.
