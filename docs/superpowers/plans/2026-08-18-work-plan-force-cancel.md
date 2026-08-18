# Work Plan Force Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `owner` and `admin` append an audited cancellation to another member's active or future work-plan interval without changing past schedule history.

**Architecture:** Add a dedicated manager-only command that derives the target member from the stored source plan, clips the requested segment to the next authoritative 30-minute boundary, and reuses the target member's append-only operation lease. Keep ordinary create/cancel APIs identity-bound to the authenticated member. The React schedule labels and submits the manager action separately while reusing the existing confirmation and refresh flow.

**Tech Stack:** FastAPI, Pydantic v2, Motor/MongoDB, Python `unittest`, React 19, TypeScript, Vitest, Vite.

---

## File Map

- `backend/app/modules/work_plans/schemas.py`: timezone-aware force-cancel request contract.
- `backend/app/modules/work_plans/domain.py`: authoritative 30-minute ceiling helper and configurable cancellation lead validation with the existing one-hour default preserved.
- `backend/app/modules/work_plans/service.py`: manager authorization, target derivation, target-member command persistence, legacy-aware projection, audit metadata, and idempotent response.
- `backend/app/routers/work_plans.py`: dedicated `/force-cancel` route and standard work-plan error mapping.
- `backend/tests/test_work_plan_domain.py`: boundary and lead-time regression tests.
- `backend/tests/test_work_plan_service.py`: authorization, clipping, target identity, idempotency, legacy projection, and audit tests.
- `backend/tests/test_work_plan_routes.py`: route registration and HTTP error mapping tests.
- `frontend/src/pages/workPlans/types.ts`: force-cancel payload type.
- `frontend/src/pages/workPlans/WorkPlanSchedule.tsx`: own-plan versus manager-action labels.
- `frontend/src/pages/WorkPlansPage.tsx`: force-cancel payload construction, endpoint selection, confirmation copy, and mutation refresh.
- `frontend/src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx`: manager-only action rendering.
- `frontend/src/pages/WorkPlansPage.test.tsx`: ownership and payload helper tests.

### Task 1: Domain Boundary and Request Contract

**Files:**
- Modify: `backend/app/modules/work_plans/schemas.py`
- Modify: `backend/app/modules/work_plans/domain.py`
- Test: `backend/tests/test_work_plan_domain.py`

- [ ] **Step 1: Write failing schema and boundary tests**

Add tests that express the approved behavior:

```python
def test_force_cancel_requires_timezone_aware_bounds(self) -> None:
    with self.assertRaisesRegex(ValueError, "强制取消时间必须包含时区"):
        WorkPlanForceCancel(
            start_at=datetime(2026, 8, 18, 10),
            end_at=datetime(2026, 8, 18, 12, tzinfo=UTC),
            idempotency_key=UUID("341b0035-391c-4926-90a4-4f0ff36c9752"),
        )

def test_ceil_work_plan_boundary_uses_next_half_hour(self) -> None:
    self.assertEqual(
        ceil_work_plan_boundary(datetime(2026, 8, 18, 2, 17, 9, tzinfo=UTC)),
        datetime(2026, 8, 18, 2, 30, tzinfo=UTC),
    )
    self.assertEqual(
        ceil_work_plan_boundary(datetime(2026, 8, 18, 2, 30, tzinfo=UTC)),
        datetime(2026, 8, 18, 2, 30, tzinfo=UTC),
    )
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
& $lockedPython -m unittest tests.test_work_plan_domain
```

Expected: import failures for `WorkPlanForceCancel` and `ceil_work_plan_boundary`.

- [ ] **Step 3: Add the minimal schema and boundary implementation**

Add this request shape in `schemas.py`:

```python
class WorkPlanForceCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: datetime
    end_at: datetime
    idempotency_key: UUID

    @model_validator(mode="after")
    def validate_interval(self) -> "WorkPlanForceCancel":
        for value in (self.start_at, self.end_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("强制取消时间必须包含时区")
            if value.second or value.microsecond or value.minute not in {0, 30}:
                raise ValueError("强制取消时间必须以 30 分钟为间隔")
        if self.end_at <= self.start_at:
            raise ValueError("强制取消结束时间必须晚于开始时间")
        return self
```

Add this pure helper in `domain.py`:

```python
def ceil_work_plan_boundary(value: datetime) -> datetime:
    observed = _as_utc(value, field_name="observed_at")
    base = observed.replace(second=0, microsecond=0)
    remainder = base.minute % 30
    if remainder == 0 and observed == base:
        return base
    return base + timedelta(minutes=30 - remainder if remainder else 30)
```

Extend `build_operation_drafts` with keyword-only `minimum_cancel_lead_minutes: int = 60` and use that value in the existing cancellation threshold. The default must leave ordinary cancellation unchanged.

- [ ] **Step 4: Run domain tests and verify GREEN**

Run the same command. Expected: all domain tests pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/modules/work_plans/schemas.py backend/app/modules/work_plans/domain.py backend/tests/test_work_plan_domain.py
git commit -m "feat: validate manager work plan cancellation"
```

### Task 2: Target-Member Force-Cancel Service

**Files:**
- Modify: `backend/app/modules/work_plans/service.py`
- Test: `backend/tests/test_work_plan_service.py`

- [ ] **Step 1: Write failing authorization and clipping tests**

Add service tests with the existing `fake_db` helper:

```python
async def test_admin_force_cancel_appends_to_target_member_from_next_boundary(self) -> None:
    activation = {
        "_id": "operation-1",
        "schema_version": 2,
        "record_kind": "operation",
        "member_id": "member@example.com",
        "member_name": "Member",
        "operation_type": "activate",
        "anchor_date": "2026-08-18",
        "effective_start_at": datetime(2026, 8, 18, 1, tzinfo=UTC),
        "effective_end_at": datetime(2026, 8, 18, 7, tzinfo=UTC),
        "member_sequence": 1,
        "created_by": "member@example.com",
        "created_at": datetime(2026, 8, 17, 12, tzinfo=UTC),
    }
    db = fake_db(plans=[activation])
    result = await force_cancel_work_plan(
        db,
        plan_id="operation-1",
        actor={"_id": "admin@example.com", "name": "Admin", "role": "admin", "actor_type": "user"},
        payload=WorkPlanForceCancel(
            start_at=datetime(2026, 8, 18, 1, tzinfo=UTC),
            end_at=datetime(2026, 8, 18, 7, tzinfo=UTC),
            idempotency_key=UUID("341b0035-391c-4926-90a4-4f0ff36c9752"),
        ),
        observed_at=datetime(2026, 8, 18, 2, 17, tzinfo=UTC),
    )
    operation = result["results"][0]["operation"]
    self.assertEqual(operation["member_id"], "member@example.com")
    self.assertEqual(operation["created_by"], "admin@example.com")
    self.assertEqual(operation["effective_start_at"], "2026-08-18T02:30:00+00:00")
    self.assertTrue(operation["force_cancelled"])

async def test_member_cannot_force_cancel_another_member(self) -> None:
    activation = {
        "_id": "operation-1",
        "schema_version": 2,
        "record_kind": "operation",
        "member_id": "member@example.com",
        "member_name": "Member",
        "operation_type": "activate",
        "anchor_date": "2026-08-18",
        "effective_start_at": datetime(2026, 8, 18, 1, tzinfo=UTC),
        "effective_end_at": datetime(2026, 8, 18, 7, tzinfo=UTC),
        "member_sequence": 1,
        "created_by": "member@example.com",
        "created_at": datetime(2026, 8, 17, 12, tzinfo=UTC),
    }
    db = fake_db(plans=[activation])
    with self.assertRaisesRegex(WorkPlanPermissionError, "只有 owner 或 admin"):
        await force_cancel_work_plan(
            db,
            plan_id="operation-1",
            actor={"_id": "viewer@example.com", "name": "Viewer", "role": "viewer", "actor_type": "user"},
            payload=WorkPlanForceCancel(
                start_at=datetime(2026, 8, 18, 2, 30, tzinfo=UTC),
                end_at=datetime(2026, 8, 18, 7, tzinfo=UTC),
                idempotency_key=UUID("341b0035-391c-4926-90a4-4f0ff36c9752"),
            ),
            observed_at=datetime(2026, 8, 18, 2, 17, tzinfo=UTC),
        )
    self.assertEqual(list(db.work_plans.documents), ["operation-1"])
```

Also add cases for owner access, self-target rejection, ended intervals, future intervals, duplicate idempotency replay, legacy activation projection, and audit action `work_plan.force_cancel`.

- [ ] **Step 2: Run focused service tests and verify RED**

Run:

```powershell
& $lockedPython -m unittest tests.test_work_plan_service.WorkPlanMutationServiceTests
```

Expected: `force_cancel_work_plan` is missing.

- [ ] **Step 3: Generalize operation persistence without changing member behavior**

Change the internal signature to keep caller and target separate:

```python
async def _create_work_plan_operations(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    payload: WorkPlanOperationCreate,
    observed_at: datetime,
    target_actor: dict[str, Any] | None = None,
    audit_action: str = "work_plan.create",
    operation_metadata: dict[str, Any] | None = None,
    minimum_cancel_lead_minutes: int = 60,
) -> dict[str, Any]:
```

Build drafts from `target_actor or actor`, acquire the target member's lease, query idempotency by target member, override `created_by` with the authenticated actor ID, merge only the explicit force metadata, and use `audit_action` for audit intents. The regular call uses every default.

Update `_expand_operation_drafts` to read every record for the target member, normalize legacy records with `normalize_legacy_records`, append valid version 2 operations, project the combined history, and clip only green fragments.

- [ ] **Step 4: Implement the manager command**

Add:

```python
async def force_cancel_work_plan(
    db: AsyncIOMotorDatabase,
    *,
    plan_id: str,
    actor: dict[str, Any],
    payload: WorkPlanForceCancel,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
```

The function must check `is_plan_manager(actor)` before plan lookup, derive target identity from the stored record, reject self-target use, compute `max(payload.start_at, ceil_work_plan_boundary(observed))`, reject an empty remaining interval, convert the clipped absolute interval to an `Asia/Shanghai` anchor date and offsets, then call `_create_work_plan_operations` with:

```python
target_actor={"_id": member_id, "name": member_name, "role": source.get("member_role")},
audit_action="work_plan.force_cancel",
minimum_cancel_lead_minutes=0,
operation_metadata={
    "force_cancelled": True,
    "force_cancel_source_id": plan_id,
    "force_cancel_requested_start_at": payload.start_at.astimezone(UTC),
    "force_cancel_requested_end_at": payload.end_at.astimezone(UTC),
},
```

- [ ] **Step 5: Run service tests and verify GREEN**

Run the focused service class, then all work-plan service tests. Expected: all pass.

- [ ] **Step 6: Commit**

```powershell
git add backend/app/modules/work_plans/service.py backend/tests/test_work_plan_service.py
git commit -m "feat: force cancel member work plans"
```

### Task 3: Manager-Only HTTP Endpoint

**Files:**
- Modify: `backend/app/routers/work_plans.py`
- Test: `backend/tests/test_work_plan_routes.py`

- [ ] **Step 1: Write failing route tests**

Add route tests that assert `/api/work-plans/{plan_id}/force-cancel` is registered, passes `plan_id`, actor, payload, and database to the service, maps `WorkPlanPermissionError` to `403`, `WorkPlanNotFoundError` to `404`, `WorkPlanRuleError` to `400`, and `WorkPlanConflictError` to `409`.

- [ ] **Step 2: Run route tests and verify RED**

```powershell
& $lockedPython -m unittest tests.test_work_plan_routes
```

Expected: the route is absent.

- [ ] **Step 3: Add the route**

Import `WorkPlanForceCancel` and `force_cancel_work_plan`, then add:

```python
@router.post("/{plan_id}/force-cancel")
async def post_force_cancel_work_plan(
    plan_id: str,
    payload: WorkPlanForceCancel,
    actor: dict = Depends(WORK_PLAN_PERMISSION),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    _require_browser_actor(actor)
    try:
        return await force_cancel_work_plan(db, plan_id=plan_id, actor=actor, payload=payload)
    except (
        WorkPlanNotFoundError,
        WorkPlanPermissionError,
        WorkPlanAccessError,
        WorkPlanConflictError,
        WorkPlanRuleError,
    ) as exc:
        _raise_http_error(exc)
```

- [ ] **Step 4: Run route and combined backend tests**

Run route tests, then:

```powershell
& $lockedPython -m unittest tests.test_work_plan_domain tests.test_work_plan_routes tests.test_work_plan_service
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add backend/app/routers/work_plans.py backend/tests/test_work_plan_routes.py
git commit -m "feat: expose work plan force cancellation"
```

### Task 4: React Manager Interaction

**Files:**
- Modify: `frontend/src/pages/workPlans/types.ts`
- Modify: `frontend/src/pages/workPlans/WorkPlanSchedule.tsx`
- Modify: `frontend/src/pages/WorkPlansPage.tsx`
- Test: `frontend/src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx`
- Test: `frontend/src/pages/WorkPlansPage.test.tsx`

- [ ] **Step 1: Write failing ownership, label, and payload tests**

Add pure helper tests:

```typescript
expect(isOwnWorkPlan({ id: "admin-id", email: "admin@example.com", role: "admin" }, OPERATION)).toBe(false);
expect(buildForceCancelPayload(SEGMENT, "idem-1")).toEqual({
  start_at: SEGMENT.start_at,
  end_at: SEGMENT.end_at,
  idempotency_key: "idem-1",
});
```

Render `WorkPlanSchedule` with an owner whose ID/email differs from the target and assert that the detail action is `强制取消计划`. Render the same plan as its owner and assert that the action remains `取消计划`.

- [ ] **Step 2: Run frontend tests and verify RED**

```powershell
& $bundledNode node_modules/vitest/vitest.mjs run src/pages/WorkPlansPage.test.tsx src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx
```

Expected: missing helpers and missing manager label.

- [ ] **Step 3: Add payload type and ownership helper**

Add:

```typescript
export type WorkPlanForceCancelPayload = {
  start_at: string;
  end_at: string;
  idempotency_key: string;
};
```

Use both authenticated identifiers:

```typescript
export function isOwnWorkPlan(
  currentUser: Pick<User, "email" | "id">,
  plan: WorkPlanHistoryItem,
): boolean {
  return [currentUser.id, currentUser.email].filter(Boolean).includes(plan.member_id);
}
```

In the detail dialog, keep manager authorization unchanged but label another member's cancel action `强制取消计划`.

- [ ] **Step 4: Route manager confirmation through the dedicated endpoint**

Add:

```typescript
export function buildForceCancelPayload(
  segment: Pick<WorkPlanSegment, "start_at" | "end_at">,
  idempotencyKey: string,
): WorkPlanForceCancelPayload {
  return { start_at: segment.start_at, end_at: segment.end_at, idempotency_key: idempotencyKey };
}
```

Derive `forceCancelling = canManageAll && cancelPlan != null && !isOwnWorkPlan(currentUser, cancelPlan)`. For that branch require `cancelSegment`, POST to `/work-plans/${cancelPlan.id}/force-cancel`, apply returned operation history, and refresh the schedule. Do not call `cancellationStartsTooSoon` in the manager branch.

Set confirmation text to:

```text
title: 确认强制取消这段计划？
message: 仅取消当前及未来区间，历史记录仍会保留。
confirmText: 强制取消
```

The own-plan branch remains byte-for-byte behaviorally equivalent.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run the same Vitest command. Expected: all pass.

- [ ] **Step 6: Run the complete frontend suite and build**

```powershell
& $bundledNode node_modules/vitest/vitest.mjs run
& $bundledNode node_modules/typescript/bin/tsc -p tsconfig.json
& $bundledNode node_modules/vite/bin/vite.js build
```

Expected: tests and production build pass with no TypeScript errors.

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/pages/workPlans/types.ts frontend/src/pages/workPlans/WorkPlanSchedule.tsx frontend/src/pages/WorkPlansPage.tsx frontend/src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx frontend/src/pages/WorkPlansPage.test.tsx
git commit -m "feat: add manager force cancel action"
```

### Task 5: End-to-End Verification and Publication

**Files:**
- Verify all changed files

- [ ] **Step 1: Run full backend tests**

```powershell
& $lockedPython -m unittest discover -s tests
```

Expected: exit code `0` and no failures.

- [ ] **Step 2: Run full frontend tests and production build**

Use the bundled Node 24 executable for Vitest, TypeScript, and Vite. Expected: exit code `0` for all commands.

- [ ] **Step 3: Run repository checks**

```powershell
git diff --check origin/achernar/dev...HEAD
git status --short --branch
git log --oneline origin/achernar/dev..HEAD
```

Expected: no whitespace errors, only intentional commits, and no untracked product files.

- [ ] **Step 4: Validate the rendered interaction**

The flow under test is: `/work-plans` -> manager opens another member's green segment -> `强制取消计划` opens a confirmation -> confirmation shows the target member and current/future-only copy -> cancel/close restores focus with no console error.

Use the in-app Browser when available. Verify desktop and mobile widths, nonblank content, no framework overlay, console health, screenshot evidence, and the interaction state. A real mutation may use a local mocked API fixture; do not change live team data for visual QA.

- [ ] **Step 5: Push and open a draft PR**

Push `codex/work-plan-force-cancel`, create a draft PR targeting `achernar/dev`, include exact test counts, and wait for Backend and Frontend CI checks.
