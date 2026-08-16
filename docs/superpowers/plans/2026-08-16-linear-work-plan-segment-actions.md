# Linear Work-Plan Segment Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every linear schedule segment open a detail dialog with the correct edit/cancel actions, and make cancellation submissions return a clear, validated result.

**Architecture:** The schedule service will attach a sanitized source record to each projected segment. The React schedule will resolve that record into the existing legacy-plan or v2-operation form model, while `WorkPlansPage` will keep mutation routing unchanged: legacy plans use the soft-cancel endpoint and v2 operations use the operation POST/update endpoints. Client validation will mirror the one-hour cancellation rule so invalid requests do not become opaque failed POSTs.

**Tech Stack:** FastAPI/Python service, Mongo projection, React/TypeScript, Vitest, pytest, Playwright/browser QA.

---

### Task 1: Add a failing backend contract test for segment source records

**Files:**
- Modify: `backend/tests/test_work_plan_service.py`
- Modify: `backend/app/modules/work_plans/service.py`

- [ ] **Step 1: Write the failing test**

Add a schedule test with one v2 activate operation and assert the projected segment includes a sanitized `record` with the operation id, type, anchor date, requested offsets, member sequence, and note.

- [ ] **Step 2: Run the focused test and verify RED**

Run `python -m pytest backend/tests/test_work_plan_service.py -k segment_source_record -q`.
Expected: failure because `response["segments"][0]["record"]` is absent.

- [ ] **Step 3: Implement the minimal service response**

Build an id-to-document map while projecting each member and add `record: _serialize_plan(source_document)` to each segment when the winning operation id matches a v2 document. For legacy ids, resolve `legacy:<plan-id>:<kind>` back to the legacy document. Do not include audit intent fields.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same pytest command and confirm the test passes.

### Task 2: Add a failing frontend interaction test for linear segments

**Files:**
- Modify: `frontend/src/pages/workPlans/types.ts`
- Modify: `frontend/src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx`
- Modify: `frontend/src/pages/workPlans/WorkPlanSchedule.tsx`

- [ ] **Step 1: Write the failing test**

Extend the test schedule with a v2 segment carrying a source operation record. Click `.work-plan-segment.active`, assert `计划详情` appears, then assert the operation label, `编辑`, and `取消计划` controls are rendered. Add a cancelled segment case that is clickable but does not render a second cancel control.

- [ ] **Step 2: Run the focused test and verify RED**

Run `npm --prefix frontend test -- --run src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx`.
Expected: failure because linear segments currently render non-interactive spans and have no source-record type.

- [ ] **Step 3: Implement segment record resolution**

Add `record?: WorkPlan | WorkPlanOperation` to `WorkPlanSegment` and `record` to the server response type. Render segments with a source record as buttons, pass the union item into the detail dialog, and preserve keyboard focus when handing off to the edit or confirmation dialog. Keep segments without records as informational spans.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same Vitest command and confirm all detail-dialog interaction tests pass.

### Task 3: Route v2 edit and cancellation actions through existing mutation handlers

**Files:**
- Modify: `frontend/src/pages/workPlans/WorkPlanSchedule.tsx`
- Modify: `frontend/src/pages/WorkPlansPage.tsx`
- Modify: `frontend/src/pages/workPlans/WorkPlanFormDrawer.tsx`
- Modify: `frontend/src/pages/WorkPlansPage.test.tsx`

- [ ] **Step 1: Write the failing request-shape tests**

Add tests that build a cancellation payload with `operation_type: "cancel"`, one anchor date, 30-minute-aligned offsets, note, and idempotency key; add a test that a v2 operation detail opens the form with its requested offsets and expected member sequence.

- [ ] **Step 2: Run the focused tests and verify RED**

Run `npm --prefix frontend test -- --run src/pages/WorkPlansPage.test.tsx src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx`.
Expected: failure because the schedule callback only accepts legacy `WorkPlan` values.

- [ ] **Step 3: Implement mutation routing and validation**

Use a `WorkPlanHistoryItem` callback for edit. For a v2 operation, retain the operation record so `WorkPlanFormDrawer` submits `PATCH /work-plans/{operation-id}` with the expected sequence. For an active v2 segment, open a cancellation confirmation that submits `POST /work-plans` using the selected segment date and offsets; preserve the existing idempotency key during retries. Validate the selected start is at least one hour after the observed server time before issuing the POST and surface the exact Chinese rule message. Do not change the backend rule or remove soft-history behavior.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the same Vitest command and confirm payload and callback tests pass.

### Task 4: Verify full behavior and rendered layout

**Files:**
- No new committed files; use existing test and browser tooling.

- [ ] **Step 1: Run backend work-plan tests**

Run `python -m pytest backend/tests/test_work_plan_service.py backend/tests/test_work_plan_routes.py backend/tests/test_work_plan_projection.py -q`.

- [ ] **Step 2: Run the full frontend suite and production build**

Run `npm --prefix frontend test -- --run` and `npm --prefix frontend run build`.

- [ ] **Step 3: Exercise the browser flow**

The flow under test is: 工作计划 -> click green linear segment -> detail -> 编辑 or 取消计划 -> submit -> refreshed segment/history. Verify desktop and 390x844 mobile viewports, no horizontal overflow, no console errors, preserved grey cancellation history, and a readable one-hour validation toast.

- [ ] **Step 4: Commit the implementation**

Commit the plan and implementation with `git add docs/superpowers/plans/2026-08-16-linear-work-plan-segment-actions.md backend frontend && git commit -m "fix: enable actions on linear work plan segments"` after all checks pass.
