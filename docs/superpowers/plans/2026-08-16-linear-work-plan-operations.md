# Linear Work Plan Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace independent work-plan blocks with append-only 48-hour interval operations that project into continuous green and grey tracks, add configurable member priority, and fix incorrect red hover surfaces.

**Architecture:** Keep version 2 operations in `work_plans`, normalize legacy records into virtual operations, and project ordered operations into merged 30-minute segments on the server. Serialize each member's mutations with a recoverable Mongo lease/head document; return effective segments and authoritative member order to React. Keep the existing page shell, focus model, presence integration, and history pagination.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, Motor/MongoDB, unittest/pytest-compatible tests, React 19, TypeScript, Vitest, lucide-react, CSS, Browser QA.

---

## File Responsibility Map

- `backend/app/modules/work_plans/projection.py`: pure operation normalization, interval projection, cancellation clipping, and member sort keys.
- `backend/app/modules/work_plans/schemas.py`: version 2 create/edit/priority payload validation.
- `backend/app/modules/work_plans/domain.py`: authenticated command construction, 48-hour offset rules, and compensation drafts.
- `backend/app/modules/work_plans/service.py`: lease acquisition, idempotent persistence, schedule/history composition, edit compensation, and priority writes.
- `backend/app/routers/work_plans.py`: HTTP contracts and Chinese error mapping.
- `backend/app/modules/system/bootstrap.py`: indexes, lease collection, and initial Zhang Chengwei priority.
- `frontend/src/pages/workPlans/dateSelection.ts`: 48-hour time-option and date helpers.
- `frontend/src/pages/workPlans/workPlanViewModel.ts`: segment geometry and display labels.
- `frontend/src/pages/workPlans/WorkPlanFormDrawer.tsx`: activate/cancel form and 48-hour preview.
- `frontend/src/pages/workPlans/WorkPlanSchedule.tsx`: continuous desktop/mobile tracks and detail interaction.
- `frontend/src/pages/workPlans/WorkPlanPriorityPopover.tsx`: manager-only priority editing.
- `frontend/src/pages/workPlans/MyPlansDrawer.tsx`: immutable operation history presentation.
- `frontend/src/pages/WorkPlansPage.tsx`: API orchestration and state reconciliation.
- `frontend/src/pages/WorkPlansPage.css`: continuous tracks, neutral hover states, responsive layout, and motion.

### Task 1: Build the Pure Interval Projector

**Files:**
- Create: `backend/app/modules/work_plans/projection.py`
- Create: `backend/tests/test_work_plan_projection.py`

- [x] **Step 1: Write failing projection tests**

Add focused tests using normalized operation dictionaries:

```python
def test_activation_union_merges_into_one_segment(self) -> None:
    operations = [
        operation("activate", 720, 1_440, sequence=1),
        operation("activate", 540, 900, sequence=2),
    ]
    self.assertEqual(
        project_operations(operations, window_start=LOCAL_MIDNIGHT, window_end=LOCAL_MIDNIGHT + timedelta(days=1)),
        [segment("active", 540, 1_440, winning_sequence=2)],
    )

def test_cancel_then_activation_then_cancel_uses_last_operation(self) -> None:
    operations = [
        operation("activate", 540, 1_440, sequence=1),
        operation("cancel", 720, 900, sequence=2),
        operation("activate", 660, 960, sequence=3),
        operation("cancel", 720, 900, sequence=4),
    ]
    self.assertEqual(states(project_operations(operations, LOCAL_MIDNIGHT, DAY_END)), [
        ("active", 540, 720), ("cancelled", 720, 900), ("active", 900, 1_440),
    ])

def test_cancellation_is_clipped_to_green_fragments(self) -> None:
    green = [segment("active", 540, 720), segment("active", 780, 1_080)]
    self.assertEqual(clip_cancellation(green, minute(480), minute(900)), [
        (minute(540), minute(720)), (minute(780), minute(900)),
    ])
```

- [x] **Step 2: Run tests and verify RED**

Run from `backend`:

```powershell
& '.\.venv\Scripts\python.exe' -m unittest tests.test_work_plan_projection -v
```

Expected: import failure for `app.modules.work_plans.projection`.

- [x] **Step 3: Implement normalized operations and projection**

Implement immutable dataclasses and slot projection:

```python
@dataclass(frozen=True, slots=True)
class NormalizedOperation:
    operation_id: str
    member_id: str
    operation_type: Literal["activate", "cancel"]
    start_at: datetime
    end_at: datetime
    order_key: tuple[int, int, str]

@dataclass(frozen=True, slots=True)
class EffectiveSegment:
    state: Literal["active", "cancelled"]
    start_at: datetime
    end_at: datetime
    winning_operation_id: str

def project_operations(operations, window_start, window_end):
    slot_count = int((window_end - window_start).total_seconds() // 1_800)
    slots: list[tuple[str, str] | None] = [None] * slot_count
    for operation in sorted(operations, key=lambda item: item.order_key):
        first = max(0, int((operation.start_at - window_start).total_seconds() // 1_800))
        last = min(slot_count, int((operation.end_at - window_start).total_seconds() // 1_800))
        state = "active" if operation.operation_type == "activate" else "cancelled"
        for index in range(first, last):
            slots[index] = (state, operation.operation_id)
    return merge_slots(slots, window_start)
```

Implement `clip_cancellation`, `normalize_v2_operation`, `normalize_legacy_records`, and deterministic merge behavior. Treat intervals as half-open and reject unaligned normalized input.

- [x] **Step 4: Verify GREEN and commit**

Run the focused tests. Expected: all pass.

```powershell
git add backend/app/modules/work_plans/projection.py backend/tests/test_work_plan_projection.py
git commit -m "feat: project linear work plan operations"
```

### Task 2: Add 48-Hour Command Schemas and Domain Rules

**Files:**
- Modify: `backend/app/modules/work_plans/schemas.py`
- Modify: `backend/app/modules/work_plans/domain.py`
- Modify: `backend/tests/test_work_plan_domain.py`

- [x] **Step 1: Write failing 48-hour and command tests**

```python
def test_operation_accepts_the_full_48_hour_window(self) -> None:
    payload = WorkPlanCreate(
        operation_type="activate", anchor_dates=[date(2026, 8, 16)],
        start_offset_minute=0, end_offset_minute=2_880,
        idempotency_key=UUID(int=1),
    )
    draft = build_operation_drafts(ACTOR, payload, OBSERVED_AT)[0]
    self.assertEqual(draft["effective_end_at"] - draft["effective_start_at"], timedelta(hours=48))

def test_cancel_requires_one_date_and_one_hour_notice(self) -> None:
    with self.assertRaisesRegex(WorkPlanRuleError, "至少晚于当前时间 1 小时"):
        build_operation_drafts(ACTOR, cancel_payload(start_offset_minute=570), OBSERVED_AT)

def test_offsets_must_be_aligned_and_inside_48_hours(self) -> None:
    for start, end in ((1, 60), (0, 2_881), (60, 60)):
        with self.subTest(start=start, end=end), self.assertRaises(ValidationError):
            WorkPlanCreate(operation_type="activate", anchor_dates=[TODAY], start_offset_minute=start, end_offset_minute=end, idempotency_key=UUID(int=2))
```

- [x] **Step 2: Run and verify RED**

Run `python -m unittest tests.test_work_plan_domain -v`. Expected: missing version 2 fields and builder.

- [x] **Step 3: Implement schemas and domain construction**

Add:

```python
OperationType = Literal["activate", "cancel"]

class WorkPlanCreate(BaseModel):
    operation_type: OperationType
    anchor_dates: list[date] = Field(min_length=1, max_length=366)
    start_offset_minute: int = Field(ge=0, le=2_850)
    end_offset_minute: int = Field(ge=30, le=2_880)
    note: str | None = Field(default=None, max_length=500)
    idempotency_key: UUID

    @model_validator(mode="after")
    def validate_interval(self):
        if self.start_offset_minute % 30 or self.end_offset_minute % 30:
            raise ValueError("时间必须以 30 分钟为间隔")
        if self.end_offset_minute <= self.start_offset_minute:
            raise ValueError("结束时间必须晚于开始时间")
        return self
```

Retain compatibility parsing for version 1 payloads. Implement `anchor_offset_to_utc`, `build_operation_drafts`, and compensation metadata. Never accept member identity fields.

- [x] **Step 4: Verify GREEN and commit**

```powershell
git add backend/app/modules/work_plans/schemas.py backend/app/modules/work_plans/domain.py backend/tests/test_work_plan_domain.py
git commit -m "feat: support 48 hour work plan commands"
```

### Task 3: Serialize and Persist Immutable Commands

**Files:**
- Modify: `backend/app/modules/work_plans/service.py`
- Modify: `backend/tests/test_work_plan_service.py`

- [x] **Step 1: Write failing lease, sequence, idempotency, and clipping tests**

Add tests proving:

```python
async def test_member_commands_receive_monotonic_sequences(self) -> None:
    first = await create_work_plans(db, ACTOR, activate_payload(), OBSERVED_AT)
    second = await create_work_plans(db, ACTOR, activate_payload(idempotency=UUID(int=2)), OBSERVED_AT)
    self.assertEqual(first["results"][0]["operation"]["member_sequence"], 1)
    self.assertEqual(second["results"][0]["operation"]["member_sequence"], 2)

async def test_cancel_persists_only_green_overlap(self) -> None:
    db.work_plans.seed(v2_activate(540, 1_080, sequence=1))
    result = await create_work_plans(db, ACTOR, cancel_payload(480, 720), OBSERVED_AT)
    operation = result["results"][0]["operation"]
    self.assertEqual((operation["effective_start_offset_minute"], operation["effective_end_offset_minute"]), (540, 720))

async def test_cancel_without_green_overlap_writes_nothing(self) -> None:
    with self.assertRaisesRegex(WorkPlanRuleError, "没有可取消的工作计划"):
        await create_work_plans(db, ACTOR, cancel_payload(540, 720), OBSERVED_AT)
    self.assertEqual(db.work_plans.insert_count, 0)
```

Include expired lease recovery and lost-acknowledgement replay.

- [x] **Step 2: Run focused tests and verify RED**

Run the new service test classes. Expected: version 2 service behavior absent.

- [x] **Step 3: Implement member lease and immutable persistence**

Add a recoverable lease context and sequence repair helpers:

```python
@asynccontextmanager
async def _member_operation_lease(db, member_id: str, observed_at: datetime):
    owner = str(uuid4())
    lease_until = observed_at + timedelta(seconds=10)
    head = await db.work_plan_member_heads.find_one_and_update(
        {
            "_id": member_id,
            "$or": [
                {"lease_until": {"$lte": observed_at}},
                {"lease_until": {"$exists": False}},
            ],
        },
        {
            "$set": {"lease_owner": owner, "lease_until": lease_until, "updated_at": observed_at},
            "$setOnInsert": {"last_sequence": 0},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if head is None or head.get("lease_owner") != owner:
        raise WorkPlanConflictError("计划正在更新，请稍后重试")
    try:
        yield owner
    finally:
        await db.work_plan_member_heads.update_one(
            {"_id": member_id, "lease_owner": owner},
            {"$unset": {"lease_owner": "", "lease_until": ""}},
        )

async def _repair_sequence_head(db, member_id: str) -> int:
    latest = await db.work_plans.find_one(
        {"member_id": member_id, "schema_version": 2},
        sort=[("member_sequence", -1)],
        projection={"member_sequence": 1},
    )
    highest = int((latest or {}).get("member_sequence") or 0)
    await db.work_plan_member_heads.update_one(
        {"_id": member_id}, {"$max": {"last_sequence": highest}}, upsert=True,
    )
    return highest
```

Acquire once per command, compute cancellation fragments from committed green segments, assign sequences, insert idempotently, update the head, and release in `finally`. Preserve the existing uncertain-result behavior when acknowledgement and readback both fail.

- [x] **Step 4: Verify GREEN and commit**

```powershell
git add backend/app/modules/work_plans/service.py backend/tests/test_work_plan_service.py
git commit -m "feat: persist ordered work plan operations"
```

### Task 4: Compose Effective Schedule, Legacy History, and Member Ordering

**Files:**
- Modify: `backend/app/modules/work_plans/projection.py`
- Modify: `backend/app/modules/work_plans/service.py`
- Modify: `backend/tests/test_work_plan_projection.py`
- Modify: `backend/tests/test_work_plan_service.py`

- [x] **Step 1: Write failing schedule and ordering tests**

```python
def test_member_order_uses_pin_priority_active_future_past_and_name(self) -> None:
    members = [
        member("无计划"), member("未来近", next_start=NOW + HOUR),
        member("正在工作", active=True), member("张城玮", priority=1, role="owner"),
        member("高优先", priority=2), member("低优先", priority=20),
    ]
    self.assertEqual([item["member_name"] for item in sort_members(members)], [
        "张城玮", "高优先", "低优先", "正在工作", "未来近", "无计划",
    ])

async def test_schedule_returns_cross_day_continuous_segments(self) -> None:
    response = await list_work_plan_schedule(db, range_name="7d", member_ids=None, include_cancelled=False, actor=ACTOR, observed_at=NOW)
    self.assertEqual(response["segments"][0]["start_at"], "2026-08-16T14:00:00+00:00")
    self.assertEqual(response["segments"][0]["end_at"], "2026-08-17T02:00:00+00:00")
```

Add legacy work/cancelled-work/temporary-unavailable compatibility cases and history compensation labels.

- [x] **Step 2: Run and verify RED**

Expected: schedule still returns independent plans and current member sorting.

- [x] **Step 3: Implement schedule projection and authoritative sorting**

Return `segments`, `current_green`, `next_green_start`, `latest_green_end`, and priority per member. Project a one-slot buffer around requested boundaries, clip returned segments to the response window, and preserve deleted-member embedded names.

- [x] **Step 4: Verify GREEN and commit**

```powershell
git add backend/app/modules/work_plans/projection.py backend/app/modules/work_plans/service.py backend/tests/test_work_plan_projection.py backend/tests/test_work_plan_service.py
git commit -m "feat: compose continuous work plan schedules"
```

### Task 5: Add Priority Management, Bootstrap, and HTTP Contracts

**Files:**
- Modify: `backend/app/modules/system/bootstrap.py`
- Modify: `backend/app/routers/work_plans.py`
- Modify: `backend/app/modules/work_plans/schemas.py`
- Modify: `backend/app/modules/work_plans/service.py`
- Modify: `backend/tests/test_work_plan_indexes.py`
- Modify: `backend/tests/test_work_plan_routes.py`
- Modify: `backend/tests/test_work_plan_service.py`

- [x] **Step 1: Write failing index, bootstrap, priority, and route tests**

```python
async def test_priority_requires_manager_and_accepts_any_positive_integer(self) -> None:
    with self.assertRaises(WorkPlanPermissionError):
        await set_member_priority(db, actor=VIEWER, member_id="member", priority=10, observed_at=NOW)
    result = await set_member_priority(db, actor=ADMIN, member_id="member", priority=10_000_000, observed_at=NOW)
    self.assertEqual(result["work_plan_priority"], 10_000_000)

async def test_bootstrap_sets_zhang_once_without_overwriting_clear(self) -> None:
    users = fake_users([{"_id": "owner", "name": "张城玮", "role": "owner"}])
    await ensure_work_plan_priority_defaults(users)
    self.assertEqual(users.docs[0]["work_plan_priority"], 1)
```

- [x] **Step 2: Run and verify RED**

Run work-plan index, route, and priority service tests.

- [x] **Step 3: Implement indexes, bootstrap, and route**

Add `WorkPlanPriorityUpdate(priority: int | None)` with positive validation. Expose `PATCH /api/work-plans/members/{member_id}/priority`. Audit before/after values and return the public member summary. Add version 2 and lease indexes. Bootstrap only a unique active owner named `张城玮` whose field is absent.

- [x] **Step 4: Verify GREEN and commit**

```powershell
git add backend/app/modules/system/bootstrap.py backend/app/routers/work_plans.py backend/app/modules/work_plans/schemas.py backend/app/modules/work_plans/service.py backend/tests/test_work_plan_indexes.py backend/tests/test_work_plan_routes.py backend/tests/test_work_plan_service.py
git commit -m "feat: manage work plan member priority"
```

### Task 6: Build Frontend 48-Hour View Models

**Files:**
- Modify: `frontend/src/pages/workPlans/types.ts`
- Modify: `frontend/src/pages/workPlans/dateSelection.ts`
- Modify: `frontend/src/pages/workPlans/dateSelection.test.ts`
- Modify: `frontend/src/pages/workPlans/workPlanViewModel.ts`
- Modify: `frontend/src/pages/workPlans/workPlanViewModel.test.ts`

- [x] **Step 1: Write failing option, label, and segment geometry tests**

```typescript
it("builds unambiguous 48 hour half-hour options", () => {
  const options = fortyEightHourOptions();
  expect(options).toHaveLength(97);
  expect(options[0]).toEqual({ value: 0, label: "当天 00:00" });
  expect(options[48]).toEqual({ value: 1440, label: "次日 00:00" });
  expect(options[96]).toEqual({ value: 2880, label: "两日后 00:00" });
});

it("maps an absolute cross-day segment onto one continuous track", () => {
  expect(timelineGeometry(START, END, SEGMENT_START, SEGMENT_END)).toEqual({ leftPercent: 25, widthPercent: 50 });
});
```

- [x] **Step 2: Run and verify RED**

Run the two focused Vitest files. Expected: helpers and version 2 types are missing.

- [x] **Step 3: Implement types and pure helpers**

Define `WorkPlanOperation`, `WorkPlanSegment`, `WorkPlanMemberSummary`, operation payloads, `fortyEightHourOptions`, `formatOffsetInterval`, and `timelineGeometry`. Keep date arithmetic timezone-independent by parsing ISO components and using explicit UTC milliseconds for geometry only.

- [x] **Step 4: Verify GREEN and commit**

```powershell
git add frontend/src/pages/workPlans/types.ts frontend/src/pages/workPlans/dateSelection.ts frontend/src/pages/workPlans/dateSelection.test.ts frontend/src/pages/workPlans/workPlanViewModel.ts frontend/src/pages/workPlans/workPlanViewModel.test.ts
git commit -m "feat: add 48 hour work plan view models"
```

### Task 7: Update the Form and Fix Hover Surfaces

**Files:**
- Modify: `frontend/src/pages/workPlans/WorkPlanFormDrawer.tsx`
- Modify: `frontend/src/pages/WorkPlansPage.css`
- Modify: `frontend/src/pages/WorkPlansPage.test.tsx`
- Modify: `frontend/src/pages/workPlans/WorkPlanStyles.test.ts`

- [x] **Step 1: Write failing form and CSS contract tests**

```typescript
it("uses activate and cancel product labels", () => {
  const html = renderToStaticMarkup(<WorkPlanFormDrawer {...PROPS} open />);
  expect(html).toContain("创建工作计划");
  expect(html).toContain("取消计划");
  expect(html).not.toContain("临时有事");
});

it("scopes neutral hover backgrounds for the backdrop and advanced dates", () => {
  expect(css).toMatch(/\.work-plan-drawer-backdrop:hover\s*{[^}]*background:/s);
  expect(css).toMatch(/\.work-plan-more-date-toggle:hover\s*{[^}]*background:/s);
});
```

- [x] **Step 2: Run and verify RED**

Expected: old labels remain and explicit hover rules are absent.

- [x] **Step 3: Implement the form and scoped styles**

Use numeric offset values, disable multi-date modes for cancellation, render full local interval preview, and validate start/end offsets. Set explicit non-red default and hover backgrounds on `.work-plan-drawer-backdrop` and `.work-plan-more-date-toggle`. Keep transform/opacity motion and reduced-motion overrides.

- [x] **Step 4: Verify GREEN and commit**

```powershell
git add frontend/src/pages/workPlans/WorkPlanFormDrawer.tsx frontend/src/pages/WorkPlansPage.css frontend/src/pages/WorkPlansPage.test.tsx frontend/src/pages/workPlans/WorkPlanStyles.test.ts
git commit -m "fix: refine work plan form interactions"
```

### Task 8: Render Continuous Tracks, Priority Controls, and Grey History

**Files:**
- Create: `frontend/src/pages/workPlans/WorkPlanPriorityPopover.tsx`
- Modify: `frontend/src/pages/workPlans/WorkPlanSchedule.tsx`
- Modify: `frontend/src/pages/workPlans/MyPlansDrawer.tsx`
- Modify: `frontend/src/pages/WorkPlansPage.css`
- Modify: `frontend/src/pages/WorkPlansPage.test.tsx`
- Modify: `frontend/src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx`

- [x] **Step 1: Write failing rendering and interaction tests**

```typescript
it("renders one member track with active and cancelled segments", () => {
  const html = renderToStaticMarkup(<WorkPlanSchedule response={LINEAR_SCHEDULE} {...PROPS} />);
  expect(html.match(/work-plan-member-track/g)?.length).toBe(1);
  expect(html).toContain("work-plan-segment active");
  expect(html).toContain("work-plan-segment cancelled");
});

it("shows priority editing only to managers", () => {
  expect(renderSchedule({ role: "admin" })).toContain("设置排班优先级");
  expect(renderSchedule({ role: "viewer" })).not.toContain("设置排班优先级");
});
```

Retain the existing focus-entry, Tab-cycle, Escape-close, and focus-restoration assertions.

- [x] **Step 2: Run and verify RED**

Expected: component still renders per-day independent plan bars and has no priority control.

- [x] **Step 3: Implement continuous desktop/mobile tracks and history**

Render one absolute track per member over the response window. Use segment geometry from absolute response boundaries. Group mobile by member with an internal scroll region. Add a `ListOrdered` priority popover for owner/admin. Render cancelled and replaced history rows with explicit text and grey styling; keep later-active current segments green.

- [x] **Step 4: Verify GREEN and commit**

```powershell
git add frontend/src/pages/workPlans/WorkPlanPriorityPopover.tsx frontend/src/pages/workPlans/WorkPlanSchedule.tsx frontend/src/pages/workPlans/MyPlansDrawer.tsx frontend/src/pages/WorkPlansPage.css frontend/src/pages/WorkPlansPage.test.tsx frontend/src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx
git commit -m "feat: render continuous work plan tracks"
```

### Task 9: Integrate API Orchestration and Reconciliation

**Files:**
- Modify: `frontend/src/pages/WorkPlansPage.tsx`
- Modify: `frontend/src/pages/WorkPlansPage.test.tsx`

- [x] **Step 1: Write failing orchestration tests**

Cover version 2 create payloads, operation-history pagination, priority update, stale schedule generation rejection, and refresh after every successful mutation.

```typescript
it("sends offsets and operation type without member identity", () => {
  expect(buildCreatePayload(DRAFT)).toEqual({
    operation_type: "activate", anchor_dates: ["2026-08-16"],
    start_offset_minute: 540, end_offset_minute: 1800,
    note: null, idempotency_key: DRAFT.idempotencyKey,
  });
});
```

- [x] **Step 2: Run and verify RED**

Expected: page still sends version 1 `plan_type`, `dates`, and clock strings.

- [x] **Step 3: Implement API calls and state replacement**

Send version 2 commands, consume `segments`, update priority through the dedicated route, and refresh schedule/history after commands. Preserve stale-data warnings and request-generation guards. Do not optimistically invent interval projection in the client.

- [x] **Step 4: Verify GREEN and commit**

```powershell
git add frontend/src/pages/WorkPlansPage.tsx frontend/src/pages/WorkPlansPage.test.tsx
git commit -m "feat: integrate linear work plan commands"
```

### Task 10: Full Verification, Browser QA, and PR Update

**Files:**
- Modify only files required by failures discovered during verification.
- Update: `docs/superpowers/plans/2026-08-16-linear-work-plan-operations.md` checkbox states.

- [x] **Step 1: Run complete automated gates**

```powershell
& '.\backend\.venv\Scripts\python.exe' -m unittest discover -s backend\tests -v
npm test --prefix frontend
npm run build --prefix frontend
git diff --check origin/achernar/dev...HEAD
```

Expected: all backend and frontend tests pass, build exits zero, and no whitespace errors are reported.

- [x] **Step 2: Run browser acceptance**

Use the Browser plugin when available. Test desktop `1440x960` and mobile `390x844`:

- drawer exterior hover remains neutral;
- advanced-date hover remains neutral;
- overlapping activation becomes one green track;
- middle cancellation becomes grey;
- reactivation turns coverage green while history remains grey;
- cross-midnight and 48-hour limits render correctly;
- priority set/change/clear changes server order;
- ordinary member priority controls are absent;
- no page-level horizontal overflow, overlap, console error, or framework overlay;
- focus and reduced-motion behavior remain correct.

- [x] **Step 3: Fix every discovered issue with RED-GREEN tests**

For each issue, write a focused failing regression test, run it to verify the expected failure, implement the smallest correction, and rerun the focused plus complete suites.

- [x] **Step 4: Review and update the existing PR**

```powershell
git status --short
git diff --stat origin/achernar/dev...HEAD
git diff --check origin/achernar/dev...HEAD
git push origin HEAD:achernar/dev
```

Update PR #34 to describe the interval operation model, 48-hour window, member priority, legacy compatibility, hover fix, validation results, and known build warnings. Confirm the remote head equals local HEAD and all GitHub CI checks pass.
