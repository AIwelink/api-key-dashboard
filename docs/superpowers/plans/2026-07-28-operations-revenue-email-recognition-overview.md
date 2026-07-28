# Operations Revenue, Email Recognition, and Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make AIGCLink revenue equal ordinary-user consumption, identify internal users by email, and replace the operations overview's duplicated/low-value tables with a unified trend and per-site comparison.

**Architecture:** Add a forward-only Growth PostgreSQL migration for email-based internal-user identities, then keep matching inside the operations repository so both immediate creation and later source sync use the same normalized identity. Keep revenue rules in repository SQL, expose per-site summaries through the existing overview response, and render the revised data in the existing React workspace without changing operations-site authorization.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy async text queries, PostgreSQL, Python unittest, React 19, TypeScript, Vitest, Vite.

---

### Task 1: Add the internal-user email schema contract

**Files:**
- Modify: `backend/tests/test_growth_migrations.py`
- Modify: `backend/tests/test_operations_domain.py`
- Modify: `backend/app/modules/growth/migrations.py`
- Modify: `backend/app/modules/operations/schemas.py`

- [ ] **Step 1: Write failing migration and schema tests**

Add tests that require a third migration and normalized email input:

```python
def test_internal_email_migration_supports_pending_recognition(self) -> None:
    from app.modules.growth.migrations import INTERNAL_EMAIL_MIGRATION

    sql = "\n".join(INTERNAL_EMAIL_MIGRATION.statements)
    self.assertIn("ADD COLUMN IF NOT EXISTS email TEXT", sql)
    self.assertIn("ADD COLUMN IF NOT EXISTS recognized_at TIMESTAMPTZ", sql)
    self.assertIn("ALTER COLUMN external_user_id DROP NOT NULL", sql)
    self.assertIn("growth_internal_users_site_email_unique_idx", sql)

def test_internal_user_create_normalizes_email(self) -> None:
    from app.modules.operations.schemas import InternalUserCreate

    payload = InternalUserCreate(site_id="aigclink", email=" Staff@Example.com ")
    self.assertEqual(payload.email, "staff@example.com")
    self.assertFalse(hasattr(payload, "external_user_id"))
```

Also test invalid email rejection and `InternalUserUpdate(email=...)` normalization.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_growth_migrations tests.test_operations_domain -v
```

Expected: failures because `INTERNAL_EMAIL_MIGRATION` and the email fields do not exist.

- [ ] **Step 3: Add migration `0003_operations_internal_email`**

Add a migration after `OPERATIONS_MIGRATION` containing idempotent statements equivalent to:

```sql
ALTER TABLE growth.internal_users ADD COLUMN IF NOT EXISTS email TEXT;
ALTER TABLE growth.internal_users ADD COLUMN IF NOT EXISTS recognized_at TIMESTAMPTZ;
ALTER TABLE growth.internal_users ALTER COLUMN external_user_id DROP NOT NULL;
UPDATE growth.internal_users
SET email = lower(trim(account_label))
WHERE email IS NULL AND account_label LIKE '%@%';
CREATE UNIQUE INDEX IF NOT EXISTS growth_internal_users_site_email_unique_idx
ON growth.internal_users (site_id, lower(email))
WHERE email IS NOT NULL;
```

Append the migration to `MIGRATIONS` without editing the already-applied `0002` migration.

- [ ] **Step 4: Replace create/update identity input with email**

Use `EmailStr` and normalize to lowercase after validation:

```python
class InternalUserCreate(BaseModel):
    site_id: str = Field(min_length=1, max_length=120)
    email: EmailStr
    reason: str = Field(default="", max_length=1000)
    active_from: datetime = Field(default_factory=lambda: datetime.now(UTC))
    active_until: datetime | None = None

class InternalUserUpdate(BaseModel):
    email: EmailStr | None = None
    reason: str | None = Field(default=None, max_length=1000)
    active_from: datetime | None = None
    active_until: datetime | None = None
```

Return normalized `str(value).strip().lower()` from email validators. Preserve active-window validation.

- [ ] **Step 5: Run tests and verify GREEN**

Run the Task 1 command and require all tests to pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add backend/app/modules/growth/migrations.py backend/app/modules/operations/schemas.py backend/tests/test_growth_migrations.py backend/tests/test_operations_domain.py
git commit -m "feat: add internal user email identity schema"
```

### Task 2: Resolve internal users by email

**Files:**
- Modify: `backend/tests/test_operations_repository.py`
- Modify: `backend/tests/test_operations_routes.py`
- Modify: `backend/app/modules/operations/repository.py`
- Modify: `backend/app/modules/operations/service.py`

- [ ] **Step 1: Write failing repository tests for recognized and pending records**

Cover these behaviors:

```python
async def test_create_internal_user_recognizes_unique_snapshot_email(self) -> None:
    payload = InternalUserCreate(site_id="aigclink", email="staff@example.com")
    row = await create_internal_user(connection, payload, actor_id="owner")
    self.assertEqual(row["external_user_id"], "42")
    self.assertEqual(row["recognition_status"], "recognized")

async def test_create_internal_user_keeps_unknown_email_pending(self) -> None:
    payload = InternalUserCreate(site_id="aigclink", email="later@example.com")
    row = await create_internal_user(connection, payload, actor_id="owner")
    self.assertIsNone(row["external_user_id"])
    self.assertEqual(row["recognition_status"], "pending")
```

Add SQL assertions that matching uses `lower(trim(snapshot.account_label))`, only a unique match is accepted, and list search includes `internal_user.email`.

- [ ] **Step 2: Write a failing sync recognition test**

Call `upsert_user_snapshots()` with a snapshot whose account label differs only by email case. Assert its SQL updates the pending internal-user row with the source `external_user_id` and sets `recognized_at`, then marks the snapshot internal.

- [ ] **Step 3: Write failing route tests for the new payload**

Update POST/PATCH route fixtures to send email and assert list/create responses expose `email`, `recognition_status`, `external_user_id`, and `recognized_at`. Keep owner/admin and site-scope assertions unchanged.

- [ ] **Step 4: Run focused tests and verify RED**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_operations_repository tests.test_operations_routes -v
```

Expected: email recognition assertions fail against the external-ID-only repository.

- [ ] **Step 5: Implement repository recognition**

Create a small SQL projection used by list/create/update responses:

```sql
CASE
  WHEN internal_user.external_user_id IS NULL THEN 'pending'
  ELSE 'recognized'
END AS recognition_status
```

On create, insert the normalized email and select a business user only when exactly one snapshot matches the site/email. Persist `external_user_id` and `recognized_at` for that unique match. On update, when email changes, clear both fields before rerunning the same match.

Update `upsert_user_snapshots()` so a source snapshot can claim one pending configuration by normalized site/email, persist the ID, and use that internal-user ID in the snapshot upsert. Existing recognized configurations continue matching by business user ID.

- [ ] **Step 6: Preserve identity during later source-email changes**

Ensure the lookup condition is:

```sql
configured.external_user_id = :external_user_id
OR (
  configured.external_user_id IS NULL
  AND configured.email = lower(trim(:account_label))
)
```

This prevents a recognized record from being detached when the source account label changes.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run the Task 2 command and require all tests to pass.

- [ ] **Step 8: Commit Task 2**

```powershell
git add backend/app/modules/operations/repository.py backend/app/modules/operations/service.py backend/tests/test_operations_repository.py backend/tests/test_operations_routes.py
git commit -m "feat: recognize internal users by email"
```

### Task 3: Apply the site-specific revenue rule and expose site breakdowns

**Files:**
- Modify: `backend/tests/test_operations_repository.py`
- Modify: `backend/tests/test_operations_routes.py`
- Modify: `backend/app/modules/operations/repository.py`
- Modify: `backend/app/modules/operations/service.py`

- [ ] **Step 1: Write failing SQL contract tests**

Require summary SQL to calculate AIGCLink income from ordinary usage cost and exclude AIGCLink cash events:

```python
self.assertIn("usage.site_id = 'aigclink'", statement)
self.assertIn("NOT snapshot.is_internal", statement)
self.assertIn("SUM(usage.cost_cny)", statement)
self.assertIn("event.site_id <> 'aigclink'", statement)
```

Require aggregate replacement to write usage-derived `income_cny` only for AIGCLink ordinary users, while AIWeLink keeps classified cash income.

- [ ] **Step 2: Write failing historical-trend and site-breakdown tests**

Test that trend SQL joins the ordinary aggregate row and returns:

```sql
CASE
  WHEN stats.site_id = 'aigclink' AND stats.user_segment = 'internal' THEN 0
  WHEN stats.site_id = 'aigclink' THEN ordinary.cost_cny
  ELSE stats.gross_income_cny
END AS gross_income_cny
```

Add a repository test for `get_operations_site_breakdown()` that groups current-window metrics by authorized site and uses the same revenue rule.

- [ ] **Step 3: Write a failing overview service test**

Assert `get_operations_overview()` returns:

```python
{
    "summary": current,
    "previous_summary": previous,
    "site_breakdown": site_rows,
    "window": ..., 
    "generated_at": ...,
}
```

and only passes `allowed_site_ids` to the repository.

- [ ] **Step 4: Run focused tests and verify RED**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_operations_repository tests.test_operations_routes -v
```

- [ ] **Step 5: Implement live summary and aggregate revenue SQL**

In `get_operations_summary()`, calculate `gross_income_cny` as AIWeLink classified cash plus AIGCLink ordinary usage `cost_cny`. Leave payer and refund facts sourced from credit events. Keep `net_income_cny` in the API result only for compatibility.

In `_replace_aggregate_table()`, set usage-event `income_cny` to `usage.cost_cny` only for AIGCLink non-internal snapshots, and set credit-event `income_cny` to cash only when the site is not AIGCLink.

- [ ] **Step 6: Implement historical trend reads and per-site summary**

Alias the selected stats table as `stats`, left join its `ordinary` row on site and bucket, and derive AIGCLink income from ordinary cost so previously cached buckets read correctly without a full backfill.

Add `get_operations_site_breakdown()` using a site-grouped form of the live summary query. Call it once for the current window inside the existing overview cache load and return `site_breakdown`.

- [ ] **Step 7: Run focused tests and verify GREEN**

Run the Task 3 command and require all tests to pass.

- [ ] **Step 8: Commit Task 3**

```powershell
git add backend/app/modules/operations/repository.py backend/app/modules/operations/service.py backend/tests/test_operations_repository.py backend/tests/test_operations_routes.py
git commit -m "feat: calculate operations revenue by site"
```

### Task 4: Redesign the operations overview and internal-user table

**Files:**
- Modify: `frontend/src/pages/OperationsManagementPage.test.tsx`
- Modify: `frontend/src/pages/OperationsManagementPage.tsx`
- Modify: `frontend/src/pages/OperationsManagementPage.css`

- [ ] **Step 1: Write failing overview rendering tests**

Require the overview markup to contain “运营趋势” and “站点运营对比”, and reject the removed sections/metric:

```typescript
expect(html).toContain("运营趋势");
expect(html).toContain("站点运营对比");
expect(html).toContain("消耗额度");
expect(html).toContain("人均消耗");
expect(html).toContain("付费率");
expect(html).not.toContain("账号运营明细");
expect(html).not.toContain("收入趋势");
expect(html).not.toContain("用户活动趋势");
expect(html).not.toContain("净收入");
```

- [ ] **Step 2: Write failing internal-user rendering tests**

Require the internal-user table and create modal contract to use “邮箱”, “识别状态”, “识别时间”, “识别成功”, and “待识别”, while no longer asking for an editable “业务用户 ID”. Test pure helpers for:

```typescript
averageConsumption({ consumed_balance_units: 20, active_user_count: 4 }) === 5
paymentRate({ payer_count: 1, active_user_count: 4 }) === 25
averageConsumption({ consumed_balance_units: 20, active_user_count: 0 }) === 0
```

- [ ] **Step 3: Run the focused frontend test and verify RED**

```powershell
cd frontend
npm.cmd test -- OperationsManagementPage.test.tsx
```

- [ ] **Step 4: Update frontend response and form types**

Add `site_breakdown` to `OverviewResponse`. Extend internal-user data with `email`, nullable `external_user_id`, `recognition_status`, and `recognized_at`. Replace form `external_user_id` and `account_label` with `email`.

- [ ] **Step 5: Render the revised overview**

Keep six summary metrics and rename `流水收入` to `收入`. Render one full-width trend table with:

```text
时间 | 站点 | 注册 | 活跃 | 成功调用 | 消耗额度 | 付费用户 | 收入 | 退款
```

Render `overview.site_breakdown` below it with:

```text
站点 | 注册用户 | 活跃用户 | 成功调用 | 消耗额度 | 付费用户 | 收入 | 退款 | 人均消耗 | 付费率
```

Calculate rates with zero-denominator guards and format payment rate with one decimal place.

- [ ] **Step 6: Render email recognition management**

The create/edit modal accepts email, reason, and active dates. The table shows the normalized email, a status tag, read-only recognized business ID, recognized time, reason, and active dates. POST/PATCH payloads send email instead of a manually entered ID or account label.

- [ ] **Step 7: Adjust scoped CSS**

Replace the two-column trend grid with a full-width section. Keep table density and existing visual language; do not create nested cards or edit unrelated global styles.

- [ ] **Step 8: Run focused tests and verify GREEN**

Run the Task 4 command and require all tests to pass.

- [ ] **Step 9: Commit Task 4**

```powershell
git add frontend/src/pages/OperationsManagementPage.tsx frontend/src/pages/OperationsManagementPage.test.tsx frontend/src/pages/OperationsManagementPage.css
git commit -m "feat: redesign operations overview"
```

### Task 5: Full verification

**Files:**
- Verify only; no planned source changes.

- [ ] **Step 1: Run all operations and migration backend tests**

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_growth_migrations tests.test_operations_adapters tests.test_operations_domain tests.test_operations_repository tests.test_operations_routes tests.test_operations_site_permissions tests.test_operations_sync -v
```

- [ ] **Step 2: Run the complete frontend suite**

```powershell
cd frontend
npm.cmd test
```

- [ ] **Step 3: Build the production frontend**

```powershell
cd frontend
npm.cmd run build
```

- [ ] **Step 4: Inspect scope and migration safety**

```powershell
git diff --check
git status --short
git log -6 --oneline
```

Confirm the migration is additive/idempotent, existing recognized internal users remain valid, every operations query still binds allowed site IDs, and no unrelated files are included.
