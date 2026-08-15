# Linear Work Plan Operations Design

**Status:** Approved for implementation on 2026-08-16

**Scope:** Extend the existing flexible work-plan workspace with append-only interval operations, continuous linear schedule tracks, cross-midnight planning, configurable member priority, and fixes for incorrect red hover surfaces.

## 1. Purpose

The current work-plan model treats each submission as an independent record. That is sufficient for displaying isolated blocks, but it cannot correctly express the new behavior:

- overlapping work intervals must merge into one continuous green interval;
- cancellation must affect only the active portion of an existing work interval;
- a later work operation must reactivate any covered cancelled interval;
- every prior operation must remain visible in history;
- one operation may extend for up to 48 hours from its selected anchor date;
- members must be ordered by explicit priority and schedule proximity.

The new model treats every submission as an immutable interval operation and derives the current schedule by replaying those operations in an authoritative server order.

This remains a collaboration feature. It must not be described or used as attendance, leave, timekeeping, approval, payroll, or performance data.

## 2. Confirmed Product Rules

### 2.1 Operation meanings

The user-facing operation choices are:

- **创建工作计划**: marks the effective covered interval as active work and displays it in green.
- **取消计划**: marks the effective covered interval as cancelled and displays it in grey until a later work operation covers it.

The legacy labels `工作时间` and `临时有事` are removed from the creation form. Legacy records continue to be readable.

### 2.2 Last operation wins

For each member and each 30-minute interval, the latest effective operation determines the current state:

- latest `activate` operation: green;
- latest `cancel` operation: grey;
- no effective operation: empty.

Examples:

1. Activate `12:00-24:00`, then activate `09:00-15:00` -> one green interval `09:00-24:00`.
2. Activate `09:00-24:00`, then cancel `12:00-15:00` -> green `09:00-12:00`, grey `12:00-15:00`, green `15:00-24:00`.
3. Apply example 2, then activate `11:00-16:00` -> one green interval `09:00-24:00`.
4. Cancel `12:00-15:00` again after example 3 -> green `09:00-12:00`, grey `12:00-15:00`, green `15:00-24:00`.

Adjacent intervals with the same final state are merged. History remains append-only even when the current schedule becomes a single green interval again.

### 2.3 Cancellation clipping

A cancellation can affect only currently green work time.

If the current green interval is `09:00-18:00` and the member requests cancellation for `08:00-12:00`, the effective cancellation is clipped to `09:00-12:00`. The history response records both the requested interval and the effective interval.

If the requested cancellation does not overlap any current green interval, the service rejects the command with a Chinese message and writes no operation.

Cancellation keeps the existing lead-time rule: its effective start must be at least one hour later than the authoritative server time in `Asia/Shanghai`.

### 2.4 Editing

History is never rewritten. Editing creates compensating operations:

- editing an activation appends a cancellation for its currently effective old interval, then appends the replacement activation;
- editing a cancellation appends an activation for its currently effective old interval, then appends the replacement cancellation;
- the new operations reference the operation they compensate;
- the original operation remains visible and is labelled as replaced in history.

Compensating operations use the same projection rules as manual operations. Therefore later operations remain authoritative and every visible state can be explained from history.

### 2.5 Time range

Each selected date is an anchor date. An operation may use any 30-minute boundary from anchor-date `00:00` through two-days-later `00:00`, for a 48-hour selectable timeline.

Offsets are represented as minutes from the anchor-date local midnight:

- minimum start offset: `0`;
- maximum start offset: `2850`;
- minimum end offset: `30`;
- maximum end offset: `2880`;
- end offset must be greater than start offset.

The maximum duration of one generated operation is 48 hours. An operation may start on the anchor date or the following date and may end at the final `48:00` boundary.

The UI labels offsets explicitly, for example `当天 22:00`, `次日 02:00`, and `两日后 00:00`, so duplicate clock labels are never ambiguous.

### 2.6 Date selection

- Activation supports quick dates and advanced date selection.
- One batch may contain at most five anchor dates.
- Each selected anchor date creates an independent operation result.
- Cancellation selects exactly one anchor date.
- Overlapping activation operations generated from different anchor dates merge during projection.
- The server reports every generated date result and never silently truncates a batch.

## 3. Architecture

The feature is split into four units with explicit responsibilities.

### 3.1 Command validation

The work-plan domain module validates authenticated identity, time offsets, batch size, cancellation lead time, and edit intent. It converts local anchor-date offsets into timezone-aware absolute datetimes.

### 3.2 Per-member command serialization

Mutations for one member are serialized through a short MongoDB-backed lease. The lease protects cancellation clipping and sequence allocation from concurrent create, cancel, or edit commands across application instances.

While holding the lease, the service:

1. checks for an existing idempotent result;
2. repairs the member sequence head if a previous acknowledged insert was not reflected in the head document;
3. reads committed operations through the current sequence;
4. projects the current state required for validation and cancellation clipping;
5. inserts the new immutable operation or compensation batch;
6. advances the sequence head after the insert is acknowledged;
7. releases the lease.

A crash before insert leaves the sequence unchanged. A crash after insert but before head advancement is repaired from the highest committed sequence when the next lease holder starts. Sequence gaps are not created by ordinary failed validation.

### 3.3 Interval projection

The projector accepts normalized operations and a requested time window. It applies operations in member sequence order to 30-minute in-memory slots, then merges adjacent slots with the same state into disjoint continuous segments.

The projector returns only three states:

- `active`;
- `cancelled`;
- absent.

No 30-minute slot documents are stored in MongoDB. Slot expansion is an in-memory calculation, avoiding up to 96 database writes for one 48-hour command.

### 3.4 Presentation

The schedule API returns effective segments rather than asking the browser to infer overlap precedence. Desktop and mobile render the same server projection, so sorting, colours, online context, and interval boundaries cannot diverge across clients.

## 4. Data Model

### 4.1 Version 2 operation record

New records remain in the existing `work_plans` collection and are distinguished by `schema_version: 2` and `record_kind: "operation"`. Keeping one collection preserves existing operational ownership and avoids a cross-collection history cursor.

Each record contains at least:

```text
_id
schema_version = 2
record_kind = operation
member_id
member_name
operation_type = activate | cancel
anchor_date
requested_start_at
requested_end_at
effective_start_at
effective_end_at
start_offset_minute
end_offset_minute
member_sequence
idempotency_key
batch_id
note
compensates_operation_id
compensation_group_id
created_by
created_at
```

All datetime fields are timezone-aware UTC values. `anchor_date` is the ISO date selected in `Asia/Shanghai`. Offset fields preserve the user-facing 48-hour representation.

For activation, requested and effective intervals are identical. For cancellation, requested fields preserve user intent and effective fields preserve server clipping.

Records are immutable after successful insertion. Derived history status such as `replaced` is computed from later compensation references instead of mutating the original record.

### 4.2 Sequence and lease document

The `work_plan_member_heads` collection stores one document per member:

```text
_id = member_id
last_sequence
lease_owner
lease_until
updated_at
```

Lease acquisition uses an atomic conditional update. Expired leases are recoverable. Lease owner tokens are random per command and are never accepted from the frontend.

### 4.3 Member priority

`users.work_plan_priority` is nullable.

- `null` or missing: no explicit priority;
- positive integer: explicitly prioritised;
- smaller value: higher priority;
- there is no product-defined maximum.

The API and UI treat the value as a positive decimal integer and do not impose an arbitrary limit such as 99. Storage validation still rejects values that exceed the database's supported integer representation with a clear Chinese error.

On first bootstrap after deployment, the active `owner` whose normalized display name is exactly `张城玮` receives priority `1` only when the field is missing. A later manager update or explicit clear is authoritative and must not be overwritten by bootstrap.

If multiple users match the bootstrap identity, the service does not guess. It logs an administrative data warning and leaves them unchanged.

### 4.4 Indexes

The design requires indexes for:

- unique version 2 idempotency by member, idempotency key, anchor date, and operation type;
- unique member sequence by member and sequence;
- effective interval overlap queries by member and effective start/end;
- history order by member sequence descending;
- compensation lookup by `compensates_operation_id`;
- lease expiry and member head lookup.

Legacy indexes remain until all existing deployments have completed compatibility verification.

## 5. Legacy Compatibility

Version 1 records are normalized into virtual operations at read time. No destructive migration is required for rollout.

The adapter applies these rules:

- active legacy `work` record -> virtual activation at `created_at`;
- cancelled legacy `work` record -> virtual activation at `created_at`, followed by virtual cancellation of the same interval at `cancelled_at`;
- active legacy `temporary_unavailable` record -> virtual cancellation, clipped to preceding virtual green state;
- cancelled legacy `temporary_unavailable` record -> history only and no current effective segment;
- missing timestamps use the existing deterministic fallback order and are marked as legacy-derived in history.

Virtual operations sort before a version 2 operation created at the same timestamp, ensuring a new explicit command is authoritative.

Legacy records remain queryable in personal and administrative history. New writes never use the version 1 shape.

## 6. Projection Algorithm

For a member and query window `[window_start, window_end)`:

1. Read version 2 operations whose effective interval intersects the window.
2. Read legacy records whose normalized interval intersects the window.
3. Normalize both formats into ordered operations.
4. Sort by authoritative member sequence; place legacy virtual operations using their deterministic legacy order before version 2 operations at the same logical time.
5. Apply each operation to every intersecting 30-minute slot.
6. Store the winning operation ID and state for each slot.
7. Merge adjacent slots when state and winning semantic context are compatible.
8. Return segments ordered by absolute start time.

The half-open interval convention `[start, end)` prevents duplicate coverage at exact boundaries. `09:00-12:00` and `12:00-15:00` merge when their final state is the same.

Cancellation validation projects the member's committed state before accepting the command. The service intersects the requested cancellation with green segments and may generate multiple effective cancellation operations when the requested interval crosses green gaps. All generated cancellation fragments share one batch ID and one idempotent command result.

## 7. API Design

### 7.1 Create operation

The existing create route accepts the version 2 command shape while retaining a temporary compatibility parser for existing clients.

The logical payload contains:

```text
operation_type
anchor_dates
start_offset_minute
end_offset_minute
note
idempotency_key
```

The server derives identity, name, role, absolute times, sequence, and audit actor fields.

### 7.2 Edit operation

Editing accepts the target operation ID, replacement operation type, new anchor/time values, note, idempotency key, and expected current projection revision.

The service creates a compensation group and returns both compensating operation results. It never updates the target document.

### 7.3 Schedule

The schedule response includes:

```text
members
segments
start_at
end_at
observed_at
timezone
next_cursor
total_operations
```

Each member includes online state, last seen, collaboration status, explicit priority, current green status, next green start, and most recent green end.

Each segment includes member identity, state, absolute start/end, display offsets relative to visible dates, and winning operation metadata needed by the detail popover.

### 7.4 History

History returns immutable operations in descending sequence order. It includes:

- requested and effective intervals;
- operation type;
- clipping information;
- compensation target and group;
- derived replaced state;
- actor and creation time;
- legacy-derived markers where applicable.

Cancellation and replaced operations use a grey history tone. A cancellation stays grey in history even after a later activation makes the current schedule green.

### 7.5 Priority management

A dedicated member-priority endpoint accepts a positive integer or `null`.

- `owner/admin`: set and clear any member priority;
- other roles: read only;
- all changes: audited with before and after values.

### 7.6 Error contracts

User-facing errors remain Chinese and distinguish:

- invalid 30-minute boundary;
- end not later than start;
- offset outside `0-2880`;
- duration beyond 48 hours;
- more than five activation dates;
- cancellation with more than one anchor date;
- cancellation less than one hour in advance;
- cancellation with no green overlap;
- invalid or non-positive priority;
- forbidden member mutation;
- stale edit projection;
- duplicate replay;
- uncertain write acknowledgement.

## 8. Member Ordering

The schedule service is authoritative for member order. Clients render the returned order unchanged.

Sort keys are applied in this order:

1. exact member identity for `张城玮` when its persisted priority is `1`;
2. whether an explicit priority is set;
3. explicit priority ascending;
4. whether the member is currently in a green interval;
5. next green start ascending;
6. most recent green end descending;
7. normalized member name ascending;
8. member ID ascending as a deterministic final tie-breaker.

Members with explicit priority always appear before members without priority. Members without current, future, or past green work appear last and are ordered by name.

Other `owner` users receive no implicit ordering benefit. They participate in the normal priority and schedule-proximity rules.

## 9. User Interface

### 9.1 Continuous desktop track

Each member has one horizontal schedule track. Date headers remain visible, but operation bars are no longer independently stacked inside each day cell.

Segments can span midnight and multiple date columns:

- green: active work;
- grey: cancelled interval without later activation;
- empty: no work-plan state.

The member column remains sticky. Only the schedule viewport scrolls horizontally. The current-time marker overlays the track without changing layout.

Selecting any segment opens the existing accessible detail popover with interval, history source, note, and authorised actions.

### 9.2 Mobile track

Mobile uses member-grouped compact rows rather than compressing the desktop grid. Each member row contains an internally scrollable timeline with stable day widths. Page-level horizontal overflow is forbidden.

The member header shows name, online state, priority when set, and nearest schedule context. Green and grey segments use the same projection as desktop.

### 9.3 Priority control

Managers see a `ListOrdered` icon beside the member name. It opens a compact popover containing:

- a positive-integer text input with numeric input mode;
- save command;
- clear command;
- validation and saving state.

The icon has an accessible name and tooltip. Ordinary members do not receive the edit control.

### 9.4 Form

The segmented operation selector labels are:

- `创建工作计划`;
- `取消计划`.

Activation retains quick dates and all advanced date modes. Cancellation forces one anchor date.

Time controls expose 30-minute values across 48 hours with explicit day-relative labels. The form displays a concise interval preview using full local dates and times before submission.

The sticky submit footer remains reachable on mobile. Submission state, success, duplicate, partial failure, and uncertain outcome stay explicit.

### 9.5 History

History displays operations, not only current segments. Each row includes requested and effective intervals and one of these derived labels:

- active operation;
- cancelled operation;
- replaced by edit;
- legacy record;
- uncertain pending reconciliation.

Cancelled and replaced rows use a neutral grey surface. They remain visible after later activation covers the same time.

### 9.6 Hover bug fixes

The root cause is the global `button:hover { background: var(--accent-strong); }` rule. Both the full-screen drawer backdrop and the transparent advanced-date button are buttons, so their transparent backgrounds become red on hover.

The fix is scoped and structural:

- the drawer backdrop gets an explicit background for default and hover states, changing only opacity and never inheriting the global accent background;
- the advanced-date toggle gets an explicit neutral button class with restrained border/background hover styling;
- component tests assert the required classes;
- browser QA verifies that the drawer exterior and advanced-date region never show a red fill.

The global button rule remains unchanged for unrelated platform controls.

### 9.7 Motion and accessibility

- drawer and popover motion uses opacity and transforms only;
- durations remain between 160ms and 280ms;
- interval state changes may cross-fade but must not animate width from an incorrect origin;
- closed layers remain inert;
- focus enters dialogs, cycles inside, closes with Escape, and returns to the opener;
- `prefers-reduced-motion: reduce` disables nonessential transitions and animation;
- colour is never the only state signal: text labels and accessible names identify active and cancelled segments.

## 10. Online Status Semantics

Presence continues to use the existing platform capability.

- online during green work: online with planned-work context;
- offline during green work: neutral planned-offline message;
- grey cancellation interval: no anomaly hint;
- no effective segment: ordinary online/offline context.

No automatic attendance conclusion is generated.

## 11. Security and Reliability

- member identity always comes from the authenticated actor;
- ordinary members may create, cancel, and edit only their own operations;
- all members may view the team projection;
- administrators may manage all member operations;
- only `owner/admin` may modify priority;
- idempotency covers each generated anchor date and compensation group;
- member leases serialize conflicting operations without relying on client time;
- operation and priority changes write audit records;
- ambiguous write acknowledgements return an uncertain result and are reconciled before retrying;
- history records are never physically deleted by product commands.

## 12. Testing Strategy

### 12.1 Domain tests

Cover:

- valid and invalid 48-hour offsets;
- 30-minute alignment;
- half-open boundaries;
- activation union;
- cancellation splitting and clipping;
- repeated activation and cancellation;
- cross-midnight and two-day intervals;
- adjacent-segment merging;
- compensation behavior;
- deterministic legacy normalization.

### 12.2 Service tests

Cover:

- server identity enforcement;
- per-member sequence ordering;
- lease contention and expired lease recovery;
- lost acknowledgement recovery;
- idempotent retry;
- five-date activation batches;
- single-date cancellation;
- cancellation lead time;
- cancellation with no overlap;
- history completeness;
- manager and member authorization;
- priority validation, audit, and clearing;
- Zhang Chengwei bootstrap ambiguity handling;
- member ordering in current, future, past, and empty states.

### 12.3 Frontend tests

Cover:

- new operation labels;
- 48-hour time options and interval preview;
- cancellation single-date behavior;
- continuous green/grey segment rendering;
- cross-date geometry;
- member order supplied by the server;
- manager-only priority controls;
- grey cancellation history after reactivation;
- explicit backdrop and advanced-date hover classes;
- retained focus and exit motion behavior.

### 12.4 Browser acceptance

Verify at desktop and mobile viewports:

1. open the creation drawer and move the pointer across the exterior backdrop;
2. hover and expand advanced dates;
3. create overlapping work operations and observe one green track;
4. cancel a middle interval and observe the grey split;
5. reactivate across the grey interval and observe green continuity;
6. cancel again and verify history remains complete;
7. create an operation crossing midnight and another reaching the 48-hour boundary;
8. set, change, and clear member priority as a manager;
9. verify ordinary members have no priority edit control;
10. verify desktop and mobile have no page-level horizontal overflow;
11. verify focus, Escape, animation, and reduced-motion behavior;
12. verify no unexpected console errors or framework overlays.

## 13. Rollout and Verification Gates

Implementation updates the existing draft PR sourced from `achernar/dev`.

Before pushing:

- all focused red-green TDD cycles pass;
- the complete backend suite passes;
- the complete frontend suite passes;
- the production frontend build succeeds;
- whitespace and diff checks pass;
- desktop and mobile browser acceptance passes;
- the PR description is updated with the new interval model, migration behavior, priority controls, hover fix, and exact verification results;
- GitHub CI passes for backend and frontend.

## 14. Acceptance Criteria

- Zhang Chengwei appears first after its initial priority is applied.
- Any member with an explicit positive priority appears before unprioritised members.
- Equal-priority and unprioritised members are ordered by active work, nearest future green start, latest past green end, then name.
- Overlapping activation intervals merge into one continuous green segment.
- Cancellation affects only current green overlap and shows grey when not reactivated.
- Later activation turns covered grey time green without removing cancellation history.
- Repeated cancel/reactivate cycles remain explainable from immutable history.
- Edits append compensating operations instead of rewriting history.
- Members can select any valid 30-minute interval within an anchor-date 48-hour window.
- Activation retains the five-date batch limit; cancellation remains single-anchor-date and one hour in advance.
- Desktop renders one continuous track per member with a sticky member column.
- Mobile renders compact member timelines without page-level horizontal overflow.
- The creation drawer exterior and advanced-date controls never turn into a red hover surface.
- The form uses `创建工作计划` and `取消计划`.
- All roles can view the schedule; ordinary members cannot change another member's operations or any member priority.
- Cancelled and replaced records remain visible in grey history.
- Presence wording remains neutral and the feature is never described as attendance.
