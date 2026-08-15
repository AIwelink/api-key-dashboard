# Flexible Work Plans Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the universally visible work-plan workspace with an authenticated-member form, desktop Gantt schedule, mobile date list, historical editing/cancellation, presence integration, and manager controls.

**Architecture:** A focused `work_plans` backend module owns schemas, pure date/time rules, MongoDB operations, authorization, and schedule composition. A single FastAPI router exposes those operations. The React page uses small pure view-model helpers, a reusable form drawer, separate desktop/mobile schedule renderers, and the existing API/toast/presence infrastructure.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, Motor/MongoDB, React 19, TypeScript 5.9, Vite 7, Vitest 4, lucide-react, CSS.

---

## File Map

**Backend**

- Create `backend/app/modules/work_plans/__init__.py`: package boundary.
- Create `backend/app/modules/work_plans/schemas.py`: request and response models.
- Create `backend/app/modules/work_plans/domain.py`: pure date/time, identity, permission, and collaboration-status rules.
- Create `backend/app/modules/work_plans/service.py`: MongoDB reads/writes, idempotency, schedule assembly, and audit calls.
- Create `backend/app/routers/work_plans.py`: authenticated HTTP contract and Chinese error mapping.
- Modify `backend/app/modules/system/presence.py`: reusable current/last-online summaries.
- Modify `backend/app/modules/system/permissions.py`: mandatory universal `work-plans` access.
- Modify `backend/app/modules/system/bootstrap.py`: work-plan indexes.
- Modify `backend/app/schemas.py`: register `work-plans` in `ViewName`.
- Modify `backend/app/main.py`: include the new router.
- Create `backend/tests/test_work_plan_domain.py`: pure business rules.
- Create `backend/tests/test_work_plan_service.py`: persistence, authorization, presence join, and audit behavior.
- Create `backend/tests/test_work_plan_routes.py`: route dependency and error contract.
- Modify `backend/tests/test_role_permissions.py`: universal view migration tests.
- Modify `backend/tests/test_database_schema.py`: index assertions.

**Frontend**

- Add `lucide-react` to `frontend/package.json` and lockfile.
- Create `frontend/src/pages/workPlans/types.ts`: API and UI types.
- Create `frontend/src/pages/workPlans/dateSelection.ts`: quick/range/multi/weekday date resolution and time helpers.
- Create `frontend/src/pages/workPlans/dateSelection.test.ts`: pure date/time tests.
- Create `frontend/src/pages/workPlans/workPlanViewModel.ts`: Gantt geometry, grouping, and collaboration labels.
- Create `frontend/src/pages/workPlans/workPlanViewModel.test.ts`: view-model tests.
- Create `frontend/src/pages/workPlans/WorkPlanFormDrawer.tsx`: create/edit form and all date modes.
- Create `frontend/src/pages/workPlans/WorkPlanSchedule.tsx`: desktop Gantt and mobile grouped list.
- Create `frontend/src/pages/workPlans/MyPlansDrawer.tsx`: full personal history with edit/cancel actions.
- Create `frontend/src/pages/WorkPlansPage.tsx`: data fetching, mutation orchestration, and refresh state.
- Create `frontend/src/pages/WorkPlansPage.test.tsx`: static state and reducer tests.
- Create `frontend/src/pages/WorkPlansPage.css`: responsive layout and reduced-motion behavior.
- Modify `frontend/src/types.ts`, `frontend/src/navigation.ts`, `frontend/src/App.tsx`, and `frontend/src/App.test.ts`: first-menu route integration.

### Task 1: Register Universal Navigation and Database Indexes

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/modules/system/permissions.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Modify: `backend/tests/test_role_permissions.py`
- Modify: `backend/tests/test_database_schema.py`

- [ ] **Step 1: Write failing permission and index tests**

Add assertions that prove every built-in role and a stored custom role receive the view, and that normalization cannot remove it:

```python
async def test_every_existing_and_future_role_gets_work_plans(self) -> None:
    db, _ = fake_db({
        "_id": "role_permissions",
        "roles": {"support": {"label": "Support", "builtin": False, "allowed_views": []}},
        "role_order": [*permissions.ROLE_ORDER, "support"],
    })
    result = await permissions.get_role_permissions_settings(db)
    self.assertTrue(all("work-plans" in entry["allowed_views"] for entry in result["roles"].values()))

def test_work_plan_indexes_are_declared(self) -> None:
    self.assertIn(("member_id", 1), WORK_PLAN_IDEMPOTENCY_INDEX)
    self.assertIn(("idempotency_key", 1), WORK_PLAN_IDEMPOTENCY_INDEX)
    self.assertIn(("plan_date", 1), WORK_PLAN_IDEMPOTENCY_INDEX)
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
& '..\..\backend\.venv\Scripts\python.exe' -m unittest tests.test_role_permissions tests.test_database_schema -v
```

Expected: failure because `work-plans` and work-plan indexes do not exist.

- [ ] **Step 3: Add the mandatory view and indexes**

Register `work-plans` in the `ViewName` literal and at the front of `AVAILABLE_VIEWS`. In `_normalize_entry`, append the mandatory view for every role before resolving `default_view`:

```python
MANDATORY_ROLE_VIEWS: set[ViewName] = {"work-plans"}

allowed_set = set(allowed_views) | MANDATORY_ROLE_VIEWS
allowed_views = [view for view in AVAILABLE_VIEWS if view in allowed_set]
```

Add indexes:

```python
await db.work_plans.create_index(
    [("member_id", 1), ("idempotency_key", 1), ("plan_date", 1)],
    unique=True,
)
await db.work_plans.create_index([("plan_date", 1), ("member_id", 1), ("created_at", -1)])
await db.work_plans.create_index([("member_id", 1), ("plan_date", -1), ("created_at", -1)])
await db.work_plans.create_index([("is_cancelled", 1), ("plan_date", 1)])
```

- [ ] **Step 4: Re-run focused tests and commit**

Expected: all focused tests pass.

```powershell
git add backend/app/schemas.py backend/app/modules/system/permissions.py backend/app/modules/system/bootstrap.py backend/tests/test_role_permissions.py backend/tests/test_database_schema.py
git commit -m "feat: register universal work plan access"
```

### Task 2: Implement Pure Work-Plan Rules and Schemas

**Files:**
- Create: `backend/app/modules/work_plans/__init__.py`
- Create: `backend/app/modules/work_plans/schemas.py`
- Create: `backend/app/modules/work_plans/domain.py`
- Create: `backend/tests/test_work_plan_domain.py`

- [ ] **Step 1: Write failing domain tests**

Cover 30-minute parsing, chronological deduplication, the five-date limit, deterministic IDs, manager roles, and one-hour temporary-unavailable lead time:

```python
def test_temporary_unavailable_requires_one_hour_notice(self) -> None:
    observed_at = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)  # 09:00 Shanghai
    with self.assertRaisesRegex(WorkPlanRuleError, "至少晚于当前时间 1 小时"):
        build_plan_drafts(
            actor={"_id": "member@example.com", "name": "成员"},
            payload=WorkPlanCreate(plan_type="temporary_unavailable", dates=[date(2026, 8, 15)], start_time=time(9, 30), end_time=time(10, 30), idempotency_key=UUID(int=1)),
            observed_at=observed_at,
        )

def test_create_rejects_more_than_five_dates(self) -> None:
    payload = WorkPlanCreate(plan_type="work", dates=[date(2026, 8, 15) + timedelta(days=index) for index in range(6)], start_time=time(9), end_time=time(18), idempotency_key=UUID(int=2))
    with self.assertRaisesRegex(WorkPlanRuleError, "一次最多添加 5 天计划"):
        build_plan_drafts(actor={"_id": "member@example.com"}, payload=payload, observed_at=datetime.now(UTC))
```

- [ ] **Step 2: Run and verify RED**

Run `python -m unittest tests.test_work_plan_domain -v` with the shared virtualenv. Expected: import failure for the missing module.

- [ ] **Step 3: Implement schemas and pure rules**

Define:

```python
PlanType = Literal["work", "temporary_unavailable"]

class WorkPlanCreate(BaseModel):
    plan_type: PlanType
    dates: list[date] = Field(min_length=1, max_length=366)
    start_time: time
    end_time: time
    note: str | None = Field(default=None, max_length=500)
    idempotency_key: UUID

class WorkPlanUpdate(BaseModel):
    plan_type: PlanType | None = None
    start_time: time | None = None
    end_time: time | None = None
    note: str | None = Field(default=None, max_length=500)
    expected_updated_at: datetime | None = None
```

Implement `time_to_minute`, `deterministic_plan_id`, `is_plan_manager`, `build_plan_drafts`, and `validate_update`. Normalize notes with `strip()` and store empty notes as `None`.

- [ ] **Step 4: Run domain tests and commit**

Expected: all domain tests pass.

```powershell
git add backend/app/modules/work_plans backend/tests/test_work_plan_domain.py
git commit -m "feat: define work plan domain rules"
```

### Task 3: Implement Idempotent Creation and Personal History

**Files:**
- Create: `backend/app/modules/work_plans/service.py`
- Create: `backend/tests/test_work_plan_service.py`

- [ ] **Step 1: Write failing creation-service tests**

Use small async collection fakes to prove authenticated identity, one result per date, duplicate replay, and partial write reporting:

```python
async def test_create_returns_a_result_for_every_requested_date(self) -> None:
    result = await create_work_plans(db, actor=MEMBER, payload=two_day_payload(), observed_at=NOW)
    self.assertEqual([item["plan_date"] for item in result["results"]], ["2026-08-16", "2026-08-17"])
    self.assertEqual([item["outcome"] for item in result["results"]], ["created", "created"])
    self.assertTrue(all(item["plan"]["member_id"] == MEMBER["_id"] for item in result["results"]))

async def test_retry_reports_duplicates_without_new_documents(self) -> None:
    await create_work_plans(db, actor=MEMBER, payload=two_day_payload(), observed_at=NOW)
    replay = await create_work_plans(db, actor=MEMBER, payload=two_day_payload(), observed_at=NOW)
    self.assertEqual([item["outcome"] for item in replay["results"]], ["duplicate", "duplicate"])
    self.assertEqual(db.work_plans.insert_count, 2)
```

- [ ] **Step 2: Run and verify RED**

Run `python -m unittest tests.test_work_plan_service.WorkPlanCreationTests -v`. Expected: missing service functions.

- [ ] **Step 3: Implement creation and history reads**

Implement each deterministic ID with an upsert guarded by `_id`, then read all IDs before forming the complete response:

```python
result = await db.work_plans.update_one(
    {"_id": draft["_id"]},
    {"$setOnInsert": draft},
    upsert=True,
)
outcome = "created" if result.upserted_id is not None else "duplicate"
```

Catch per-date infrastructure errors, continue processing, then report `failed` with a Chinese message for that date. Add `list_my_work_plans` sorted by `plan_date DESC, created_at DESC`, always including cancelled entries. Write one create audit entry per newly inserted record.

- [ ] **Step 4: Run focused service tests and commit**

```powershell
git add backend/app/modules/work_plans/service.py backend/tests/test_work_plan_service.py
git commit -m "feat: create idempotent work plans"
```

### Task 4: Compose Team Schedule With Presence

**Files:**
- Modify: `backend/app/modules/system/presence.py`
- Modify: `backend/tests/test_frontend_presence.py`
- Modify: `backend/app/modules/work_plans/service.py`
- Modify: `backend/tests/test_work_plan_service.py`

- [ ] **Step 1: Write failing presence and schedule tests**

```python
async def test_member_presence_summary_keeps_last_seen_for_offline_users(self) -> None:
    result = await list_member_presence_summaries(db, observed_at=NOW)
    self.assertEqual(result["offline@example.com"]["last_seen_at"], LAST_HEARTBEAT)
    self.assertFalse(result["offline@example.com"]["is_online"])

def test_temporary_unavailable_suppresses_offline_plan_hint(self) -> None:
    status = collaboration_status(is_online=False, active_plan={"plan_type": "temporary_unavailable"})
    self.assertEqual(status, "temporary_unavailable")
```

Add schedule assertions for `7d`, `30d`, `all`, member filters, embedded deleted-member names, cancelled exclusion, and neutral planned-offline status.

- [ ] **Step 2: Run and verify RED**

Run the two focused modules. Expected: missing summary and schedule composition.

- [ ] **Step 3: Implement reusable presence summaries and schedule query**

Query active presence with the existing `ACTIVE_PRESENCE_SECONDS`, aggregate latest retained minute per user, and return:

```python
{
    user_id: {
        "is_online": bool(active_clients),
        "active_clients": len(active_clients),
        "last_seen_at": latest_seen,
    }
}
```

Implement `list_work_plan_schedule(db, range_name, member_ids, include_cancelled, observed_at)` with Asia/Shanghai date boundaries and a maximum of 4,000 returned plans. Return `members`, `plans`, `start_date`, `end_date`, `observed_at`, and `timezone`.

- [ ] **Step 4: Re-run tests and commit**

```powershell
git add backend/app/modules/system/presence.py backend/app/modules/work_plans/service.py backend/tests/test_frontend_presence.py backend/tests/test_work_plan_service.py
git commit -m "feat: combine work plans with presence"
```

### Task 5: Add Update, Cancellation, and HTTP Routes

**Files:**
- Create: `backend/app/routers/work_plans.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/modules/work_plans/service.py`
- Modify: `backend/tests/test_work_plan_service.py`
- Create: `backend/tests/test_work_plan_routes.py`

- [ ] **Step 1: Write failing authorization and route tests**

```python
async def test_member_cannot_cancel_another_members_plan(self) -> None:
    with self.assertRaisesRegex(WorkPlanPermissionError, "不能修改其他成员"):
        await cancel_work_plan(db, plan_id="plan-1", actor=MEMBER, observed_at=NOW)

async def test_admin_can_update_another_members_plan(self) -> None:
    updated = await update_work_plan(db, plan_id="plan-1", actor=ADMIN, payload=UPDATE, observed_at=NOW)
    self.assertEqual(updated["updated_by"], ADMIN["_id"])

def test_router_uses_work_plan_view_permission(self) -> None:
    dependencies = route_dependencies(work_plans_router.router)
    self.assertTrue(all("work-plans" in dependency for dependency in dependencies))
```

- [ ] **Step 2: Run and verify RED**

Expected: missing update/cancel functions and router.

- [ ] **Step 3: Implement atomic writes and endpoints**

Use ownership-or-manager filters and cancellation state:

```python
query: dict[str, Any] = {"_id": plan_id, "is_cancelled": False}
if not is_plan_manager(actor):
    query["member_id"] = str(actor["_id"])
if payload.expected_updated_at is not None:
    query["updated_at"] = payload.expected_updated_at
```

Expose `GET /schedule`, `GET /mine`, `POST /`, `PATCH /{plan_id}`, and `POST /{plan_id}/cancel`. Reject `actor_type == "api_token"` with `403` and Chinese detail. Map missing, stale, cancelled, rule, and permission errors to `404`, `409`, `400`, and `403`. Register the router in `main.py` and audit successful mutations.

- [ ] **Step 4: Run backend work-plan tests and commit**

```powershell
git add backend/app/routers/work_plans.py backend/app/main.py backend/app/modules/work_plans/service.py backend/tests/test_work_plan_service.py backend/tests/test_work_plan_routes.py
git commit -m "feat: expose work plan APIs"
```

### Task 6: Implement Frontend Date and Gantt View Models

**Files:**
- Create: `frontend/src/pages/workPlans/types.ts`
- Create: `frontend/src/pages/workPlans/dateSelection.ts`
- Create: `frontend/src/pages/workPlans/dateSelection.test.ts`
- Create: `frontend/src/pages/workPlans/workPlanViewModel.ts`
- Create: `frontend/src/pages/workPlans/workPlanViewModel.test.ts`

- [ ] **Step 1: Write failing frontend rule tests**

```typescript
it("resolves weekday selections chronologically and rejects more than five dates", () => {
  expect(resolveWeekdays("2026-08-15", "2026-08-23", [1, 3])).toEqual(["2026-08-17", "2026-08-19"]);
  expect(validateSelectedDates(["2026-08-15", "2026-08-16", "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"]))
    .toBe("一次最多添加 5 天计划，请缩小日期范围");
});

it("maps minutes to stable Gantt percentages", () => {
  expect(ganttGeometry(570, 1080)).toEqual({ leftPercent: 39.5833, widthPercent: 35.4167 });
});
```

- [ ] **Step 2: Run and verify RED**

Run `npm test -- src/pages/workPlans/dateSelection.test.ts src/pages/workPlans/workPlanViewModel.test.ts`. Expected: module import failures.

- [ ] **Step 3: Implement pure frontend helpers**

Export `isoDateRange`, `resolveWeekdays`, `normalizeSelectedDates`, `validateSelectedDates`, `thirtyMinuteOptions`, `ganttGeometry`, `groupPlansByDate`, and `collaborationLabel`. Avoid `new Date("YYYY-MM-DD")`; parse date components and use UTC arithmetic so the helpers are browser-timezone independent.

- [ ] **Step 4: Re-run tests and commit**

```powershell
git add frontend/src/pages/workPlans
git commit -m "feat: add work plan view models"
```

### Task 7: Build the Form and Schedule Components

**Files:**
- Add: `frontend/package.json`
- Add: `frontend/package-lock.json`
- Create: `frontend/src/pages/workPlans/WorkPlanFormDrawer.tsx`
- Create: `frontend/src/pages/workPlans/WorkPlanSchedule.tsx`
- Create: `frontend/src/pages/workPlans/MyPlansDrawer.tsx`
- Create: `frontend/src/pages/WorkPlansPage.test.tsx`

- [ ] **Step 1: Install icons and write failing component-state tests**

Run `npm install lucide-react`.

Add tests around exported reducers and server-rendered states:

```typescript
it("temporary unavailable keeps one date and disables bulk modes", () => {
  const next = workPlanDraftReducer(twoDayDraft, { type: "set-plan-type", value: "temporary_unavailable" });
  expect(next.selectedDates).toEqual(["2026-08-15"]);
  expect(next.moreDateMode).toBe("single");
});

it("renders sticky member rows and cancelled history actions correctly", () => {
  const html = renderToStaticMarkup(<WorkPlanSchedule response={SCHEDULE} range="7d" />);
  expect(html).toContain("work-plan-member-cell");
  expect(html).toContain("09:30 - 18:00");
});

it("shows manager actions for another member without exposing them to ordinary viewers", () => {
  expect(canManagePlan({ role: "admin", id: "admin@example.com" }, OTHER_MEMBER_PLAN)).toBe(true);
  expect(canManagePlan({ role: "viewer", id: "viewer@example.com" }, OTHER_MEMBER_PLAN)).toBe(false);
});
```

- [ ] **Step 2: Run and verify RED**

Expected: missing components and reducer.

- [ ] **Step 3: Implement components**

The form component receives:

```typescript
type WorkPlanFormDrawerProps = {
  open: boolean;
  serverToday: string;
  initialPlan?: WorkPlan | null;
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: WorkPlanCreatePayload | WorkPlanUpdatePayload) => Promise<void>;
};
```

Use `CalendarDays`, `Clock3`, `Plus`, `X`, `Pencil`, and `Ban` from `lucide-react`. Keep the submit footer sticky. Build desktop Gantt cells and the mobile grouped list from the same plans. The history drawer includes all timestamps and disabled edit actions for cancelled rows.

Selecting a Gantt bar opens a detail popover. Pass `currentUser`, `onEditPlan`, and `onCancelPlan` to the schedule; show mutation controls only when `canManagePlan(currentUser, plan)` is true. Add the manager-only `含已取消` toggle when the selected range is `all`.

- [ ] **Step 4: Re-run component tests and commit**

```powershell
git add frontend/package.json frontend/package-lock.json frontend/src/pages/workPlans frontend/src/pages/WorkPlansPage.test.tsx
git commit -m "feat: build work plan components"
```

### Task 8: Integrate the Page, Navigation, API, and Motion

**Files:**
- Create: `frontend/src/pages/WorkPlansPage.tsx`
- Create: `frontend/src/pages/WorkPlansPage.css`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/navigation.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.ts`
- Modify: `frontend/src/pages/WorkPlansPage.test.tsx`

- [ ] **Step 1: Write failing navigation and orchestration tests**

```typescript
it("places work plans in the first visible navigation group", () => {
  expect(getVisibleNavigationGroups(permissions(["work-plans", "api-pools"]))[0]).toEqual([["work-plans", "工作计划"]]);
  expect(viewFromPath("/work-plans")).toBe("work-plans");
});

it("reuses one idempotency key while a create request is in flight", () => {
  const state = beginCreate(initialRequestState, "request-key");
  expect(beginCreate(state, "different-key").idempotencyKey).toBe("request-key");
});
```

- [ ] **Step 2: Run and verify RED**

Run App and WorkPlansPage tests. Expected: route and orchestration functions are missing.

- [ ] **Step 3: Implement the page and styles**

Fetch schedule and personal history through the existing `api()` helper. Refresh both after create/update/cancel. Preserve old schedule data when a refresh fails. Register `work-plans` as the first navigation group and render `<WorkPlansPage token={token} currentUser={user} showToast={showToast} />`.

CSS requirements:

```css
.work-plan-member-cell { position: sticky; left: 0; z-index: 3; }
.work-plan-schedule-scroll { min-width: 0; overflow: auto; overscroll-behavior-inline: contain; }
.work-plan-drawer { transform: translateX(100%); transition: transform 280ms cubic-bezier(.22, 1, .36, 1); }
.work-plan-drawer.open { transform: translateX(0); }
@media (max-width: 720px), (max-width: 900px) and (orientation: portrait) {
  .work-plan-gantt { display: none; }
  .work-plan-mobile-list { display: block; }
  .work-plan-drawer { width: 100%; transform: translateY(100%); }
  .work-plan-drawer.open { transform: translateY(0); }
}
@media (prefers-reduced-motion: reduce) {
  .work-plan-page *, .work-plan-page *::before, .work-plan-page *::after { animation: none !important; transition: none !important; }
}
```

- [ ] **Step 4: Run frontend tests/build and commit**

Run `npm test` and `npm run build`. Expected: 0 failures and build exit 0.

```powershell
git add frontend/src
git commit -m "feat: add flexible work plan workspace"
```

### Task 9: Full Verification and Browser QA

**Files:**
- Modify only files required by failures found during verification.

- [ ] **Step 1: Run full automated gates**

```powershell
& 'D:\Data\Codex 项目文件夹\API key 后端管理面板开发\backend\.venv\Scripts\python.exe' -m unittest discover -s tests -v
npm test
npm run build
git diff --check origin/main...HEAD
```

Expected: all backend tests pass, all frontend tests pass, production build succeeds, and no whitespace errors.

- [ ] **Step 2: Start local services and inspect in browser**

Start FastAPI and Vite on unused ports. Verify authenticated desktop at 1440x900 and 1024x768, then mobile at 390x844 and 360x800. Capture screenshots for the schedule, open form, history, and mobile list.

- [ ] **Step 3: Verify visual and interaction invariants**

Confirm:

- `工作计划` is the first menu entry for every test role;
- only the schedule region scrolls horizontally;
- the member column remains fixed;
- the form opens from the right on desktop and bottom on mobile;
- text and controls never overlap;
- the page has no viewport-level horizontal overflow;
- create/edit/cancel update schedule and history;
- reduced-motion disables nonessential animation;
- no copy describes the feature as attendance.

- [ ] **Step 4: Fix any discovered issue using a failing test first**

For each issue, add a focused regression test, observe it fail, implement the smallest fix, and rerun the relevant suite plus build.

### Task 10: Review, Push, and Open Draft PR

**Files:**
- Update: `docs/superpowers/plans/2026-08-15-flexible-work-plans.md` checkboxes only if the repository convention tracks execution.

- [ ] **Step 1: Review the complete diff against the design**

Run:

```powershell
git status --short
git diff --stat origin/main...HEAD
git diff --check origin/main...HEAD
```

Inspect authorization, idempotency, cancellation history, presence wording, responsive CSS, and all request/response fields.

- [ ] **Step 2: Commit final verification fixes**

```powershell
git add -u
git commit -m "test: verify flexible work plans"
```

Skip this commit when no final fixes exist.

- [ ] **Step 3: Push and create a draft PR**

Push `codex/flexible-work-plans` and create a draft PR targeting `main`. The PR body must summarize the Gantt-first workflow, authenticated ownership model, universal role visibility, presence integration, validation/idempotency, responsive behavior, motion/reduced-motion support, and exact verification results.
