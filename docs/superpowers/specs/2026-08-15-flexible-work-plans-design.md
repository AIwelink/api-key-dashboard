# Flexible Work Plans Design

## Goal

Add a team collaboration workspace for planned working hours and temporary unavailability. The workspace is the first item in the left navigation for every user. Its primary desktop view is a horizontal member-by-date Gantt schedule; the top-right `填写我的计划` button opens the authenticated member's plan form. Mobile uses a date-grouped list and a full-height form sheet.

The feature is not attendance, timesheet, leave, approval, payroll, or performance software. Product copy and API responses must not imply those uses.

## Scope and Decisions

- Every calendar day is eligible, including weekends and public holidays.
- Plans use Asia/Shanghai calendar dates and 30-minute time slots.
- A submission creates one independent record per selected date and accepts at most five dates.
- Plan types are `work` (`工作时间`) and `temporary_unavailable` (`临时有事`).
- Temporary unavailability covers exactly one date and must start at least one hour after server time.
- The authenticated user identity is authoritative. Normal members cannot submit a different `member_id`.
- `owner` and `admin` are plan managers. They may edit or cancel any member's plan; other roles may only change their own.
- All existing and future roles automatically receive the `work-plans` view. This page access cannot be removed through dynamic role settings, while manager actions continue to use the explicit `owner/admin` role check.
- Cancellation is soft. A cancelled record remains visible in history and retains cancellation metadata.
- The existing frontend presence heartbeat remains the only online detector. Work plans query and combine its current and latest-known state; they do not write presence records.

## Navigation and Page Structure

Add `work-plans` as a new view at `/work-plans`. It is a dedicated first navigation group before all account, traffic, operations, and administration groups. The label is `工作计划`; its collapsed label is `时`.

The page contains one desktop workspace with these controls:

1. Header title and neutral collaboration subtitle.
2. Current online-member summary.
3. `我的安排` button, which opens a history panel for the current member.
4. Primary `填写我的计划` button in the top-right.
5. Range control for future 7 days, future 30 days, and all records.
6. Member filter and a `今天` navigation control.
7. Horizontally scrollable member-by-date Gantt table.

The member column remains sticky while dates scroll. Each date column contains a 00:00-24:00 axis. Work bars use green, temporary-unavailable bars use amber, and cancelled bars are neutral and dashed. The current-day column and current-time marker are highlighted without presenting a compliance alert.

Rows show member name, current online/offline state, last online time, and collaboration status:

- `计划工作中` when current time is inside a work plan;
- `临时有事` when inside a temporary-unavailable plan;
- neutral `计划工作中 · 当前离线` when a planned worker is offline;
- no anomaly language during temporary unavailability;
- the normal online/offline label when outside a plan.

An empty date or result set shows a clear empty state rather than an empty grid illusion.

## Responsive Behavior

Desktop at 721px and above uses the Gantt workspace. The page itself never creates horizontal overflow; only the schedule region scrolls horizontally.

Mobile and portrait tablet use:

- the same header and range/member filters;
- plans grouped by date, with member, type, time, and online state in compact rows;
- a full-height bottom sheet for `填写我的计划`;
- a sticky submission footer above device safe-area insets;
- no full desktop Gantt table.

Long member names, notes, status labels, and validation messages wrap or truncate inside stable row dimensions. No form control creates viewport-level horizontal scrolling.

## Plan Form

Opening `填写我的计划` creates a fresh draft for the authenticated member. The form includes:

- a segmented type control;
- seven quick date choices starting today;
- `查看更多日期`, which expands a structured picker supporting a date range, explicit multiple dates, and weekday selection;
- 30-minute start and end time selects;
- optional note;
- selected-date count and submit action.

Date selection preserves insertion-independent chronological ordering and removes duplicates. More than five resolved dates blocks submission with `一次最多添加 5 天计划，请缩小日期范围`. Temporary unavailability disables multi-date selection and keeps exactly one selected date.

The client generates one UUID idempotency key for each submit attempt and keeps it stable while that request is in flight. Duplicate clicks are disabled. A successful submission clears and closes the draft, refreshes the Gantt schedule and `我的安排`, and surfaces a concise success toast. Failed submission keeps the draft and displays an understandable Chinese message. A repeated idempotency key returns the original successful result and is rendered as an already-submitted state rather than duplicating records.

Editing opens the same form with member and date fixed to the existing record. Cancelling requires confirmation and does not remove the row from history.

## Motion and Interaction

Motion communicates state and uses only transform and opacity for large elements:

- the desktop form drawer enters in 280ms with `cubic-bezier(.22, 1, .36, 1)` and a 220ms backdrop fade;
- the mobile form sheet uses the same easing and rises from the bottom;
- selected dates respond in 120-180ms without changing grid dimensions;
- Gantt bars grow from their start time after data loads, staggered only within the visible rows;
- range changes crossfade and shift the schedule content by no more than 6px;
- successful submission changes the button to progress, then success, before the new bar appears;
- drawers close with Escape and backdrop click when no request is in flight.

`prefers-reduced-motion: reduce` removes nonessential animation and transitions. Animation never delays a server action or blocks keyboard use.

## Data Model

Store plans in MongoDB collection `work_plans`:

```json
{
  "_id": "UUID",
  "member_id": "user@example.com",
  "member_name": "成员姓名",
  "plan_type": "work",
  "plan_date": "2026-08-15",
  "start_minute": 570,
  "end_minute": 1080,
  "note": "远程协作",
  "status": "active",
  "is_cancelled": false,
  "idempotency_key": "UUID",
  "created_at": "UTC datetime",
  "created_by": "user@example.com",
  "updated_at": "UTC datetime",
  "updated_by": "user@example.com",
  "cancelled_at": null,
  "cancelled_by": null
}
```

`plan_date` is an ISO local-date string so calendar boundaries remain stable. `start_minute` and `end_minute` are minutes after local midnight and must be multiples of 30 between 0 and 1440, with `end_minute > start_minute`. A record never spans midnight.

Indexes:

- unique `(member_id, idempotency_key, plan_date)` for retry safety;
- `(plan_date, member_id, created_at)` for schedule queries;
- `(member_id, plan_date, created_at)` for personal history;
- `(is_cancelled, plan_date)` for active-range queries.

Create/update/cancel actions also write the existing `audit_logs` collection with before/after snapshots.

## API Design

All routes require `work-plans` view permission and a browser user. API-token actors are rejected because plans belong to platform members.

### `GET /api/work-plans/schedule`

Query parameters:

- `range=7d|30d|all`, default `7d`;
- repeated optional `member_id` filters;
- optional `include_cancelled`, default false.

Returns member profiles, plans, server observation time, timezone, active presence, last online time, and computed collaboration status. `all` returns all stored records with explicit server-side safety bounds and is intended for historical browsing, not an unbounded MongoDB cursor.

### `GET /api/work-plans/mine`

Returns all history and future records for the authenticated member, newest plan date first by default, including cancelled entries and creation/update/cancellation timestamps.

### `POST /api/work-plans`

Request:

```json
{
  "plan_type": "work",
  "dates": ["2026-08-15", "2026-08-16"],
  "start_time": "09:30",
  "end_time": "18:00",
  "note": "远程协作",
  "idempotency_key": "ad405a39-48c2-4d62-afd3-2a570004491c"
}
```

The service validates the entire batch before writing, so business-rule failures write nothing. Each record ID is derived deterministically from the authenticated member, idempotency key, and date. A bulk write is followed by a read of all deterministic IDs, and the response reports `created`, `duplicate`, or `failed` for every requested date. Infrastructure failure cannot create an unreported record, and retrying the same idempotency key fills only missing dates without duplicating successful ones.

### `PATCH /api/work-plans/{plan_id}`

Changes type, time, or note. Normal users must own the record. Managers may update another member's record but cannot reassign ownership. The plan date is immutable; users cancel and create a new record when the date changes. Cancelled records cannot be edited.

### `POST /api/work-plans/{plan_id}/cancel`

Soft-cancels the plan using an ownership-or-manager atomic update filter. Repeated cancellation is idempotent and returns the stored cancelled record.

## Validation and Concurrency

Server validation is authoritative and returns Chinese errors for:

- empty or duplicate date selections;
- more than five dates;
- invalid ISO dates;
- times outside 30-minute boundaries;
- end time not later than start time;
- temporary unavailability spanning more than one date;
- temporary-unavailable start less than one hour after current server time;
- edits or cancellations by unauthorized users;
- edits to cancelled records;
- API-token actors.

Create idempotency is enforced by the unique index and read-after-conflict response. Update and cancellation use atomic filters containing ownership, cancellation state, and the previous `updated_at` when supplied, preventing stale edits from silently overwriting a newer version.

## Presence Integration

Extract a reusable presence summary query from the existing presence module instead of duplicating heartbeat rules. The work-plan schedule joins users, active presence records inside the existing 60-second window, and the latest retained presence minute for last-online time.

The schedule computes plan context using Asia/Shanghai server time. Presence does not generate, edit, cancel, or infer plans. Being offline during planned work creates only neutral collaboration copy in the response and UI. No attendance status is stored.

## Error and Empty States

- Initial load uses stable skeleton rows so the page does not jump.
- A refresh failure keeps previously loaded schedule data and shows a nonblocking Chinese inline error.
- An empty team or filter result states what is empty and provides the plan button when appropriate.
- Create, edit, and cancel failures retain the open panel and entered values.
- Network retries reuse the in-flight idempotency key.
- Cancel and edit controls show progress and cannot be double-invoked.

## Testing and Verification

Backend tests cover schemas, pure validation, date/time normalization, five-date limit, temporary-unavailable lead time, idempotent batch insertion, complete per-date results after partial infrastructure failure, ownership, manager actions, soft cancellation, audit entries, indexes, range filters, presence joins, and browser-user enforcement.

Frontend tests cover navigation ordering and universal role visibility, date resolution modes, date-count rejection, form state, idempotency-key reuse, drawer state, create/edit/cancel flows, Gantt geometry, collaboration labels, empty/error states, and mobile grouping.

Verification gates are:

- full backend unit suite;
- full frontend Vitest suite;
- TypeScript/Vite production build;
- browser tests against desktop and mobile viewports;
- screenshots confirming the sticky member column, scroll containment, drawer/sheet layout, no overlaps, and no page-level horizontal overflow;
- reduced-motion inspection;
- manual API authorization checks for own-record and manager-record mutations.
