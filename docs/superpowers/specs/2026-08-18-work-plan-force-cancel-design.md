# Work Plan Force Cancellation Design

**Status:** Approved by the product owner on 2026-08-18

**Scope:** Allow authenticated `owner` and `admin` users to force-cancel another member's active work-plan interval from the current server time forward while preserving immutable history.

## 1. Goal

Managers need an explicit way to correct a member's active or future work plan when that member cannot update it. This is an administrative collaboration action, not an attendance decision.

The implementation must:

- allow only `owner` and `admin` users to force-cancel another member's plan;
- derive the target member from the stored plan instead of trusting a frontend member ID;
- affect only the current and future portion of the selected active interval;
- retain the original activation and append a grey cancellation operation;
- record the manager identity and complete before/after audit context;
- remain idempotent under repeated clicks and retries.

## 2. Non-Goals

- Managers cannot create a work plan on behalf of another member.
- Managers cannot reassign a plan to another member.
- Force cancellation does not delete or rewrite operation history.
- Force cancellation does not change past, already-ended schedule state.
- Ordinary members do not receive a way to bypass the existing one-hour cancellation lead time.

## 3. Confirmed Product Rules

### 3.1 Authorization

Only browser-authenticated actors whose current role is exactly `owner` or `admin` may call the force-cancel command. Dynamic page visibility is not sufficient authorization.

The service loads the target plan by ID and derives `member_id` and `member_name` from the stored record. A caller-provided target member ID is never accepted.

Calling the force-cancel endpoint as any other role returns HTTP `403` and writes no operation or audit record.

### 3.2 Time boundary

Force cancellation applies from the authoritative server time forward:

- a selected future interval keeps its original start;
- a selected interval currently in progress is clipped to the first 30-minute boundary at or after the current `Asia/Shanghai` time;
- an interval that has already ended, or has no remaining 30-minute slot after clipping, is rejected;
- the end of the selected visible interval remains the upper bound;
- past slots remain in their previous state.

The server computes the boundary. Browser time is never authoritative.

Example: at `10:17`, force-cancelling a green `09:00-15:00` segment creates an effective grey interval of `10:30-15:00`. At exactly `10:30`, the effective interval begins at `10:30`.

### 3.3 Projection and history

The command appends a version 2 `cancel` operation to the target member's sequence. It does not mutate the original activation.

Cancellation is clipped again against the target member's current green projection while holding the member operation lease. If another command has already removed some or all of the selected green interval, the service cancels only the remaining green fragments. If no green fragment remains, it returns a clear Chinese conflict message and writes nothing.

The current schedule renders the effective cancellation in grey. The original activation and force-cancel operation both remain visible in history.

## 4. API Contract

Add a dedicated route:

```text
POST /api/work-plans/{plan_id}/force-cancel
```

Request body:

```json
{
  "start_at": "2026-08-18T01:00:00+00:00",
  "end_at": "2026-08-18T07:00:00+00:00",
  "idempotency_key": "341b0035-391c-4926-90a4-4f0ff36c9752"
}
```

Rules:

- `start_at` and `end_at` must contain a timezone;
- `end_at` must be later than `start_at`;
- the requested interval must stay within the selected projected segment;
- the target record must belong to the selected segment and target member;
- the service converts the effective UTC interval into an `Asia/Shanghai` anchor date and 30-minute offsets for storage;
- the response uses the existing `WorkPlanMutationResult` shape so local reconciliation and retry handling remain consistent.

The endpoint is separate from `POST /work-plans`. The general creation endpoint continues to derive membership only from the authenticated actor and cannot be used to submit commands for another member.

## 5. Service Flow

1. Require a browser actor and explicit plan-manager role.
2. Load the target record by `plan_id`; return `404` when absent.
3. Derive the target member identity from the record.
4. Validate the timezone-aware requested interval.
5. Compute the current server boundary in `Asia/Shanghai` and round it upward to the next 30-minute boundary.
6. Intersect the requested interval with `[effective_now, requested_end)`.
7. Acquire the target member's operation lease.
8. Check idempotency for the target member and return the stored result on replay.
9. Project legacy and version 2 operations for the target member over the requested window.
10. Clip the cancellation to currently green fragments.
11. Append one cancellation operation per effective fragment with consecutive target-member sequences.
12. Store the manager in `created_by` and the target member in `member_id`.
13. Advance the member sequence head and reconcile audit intents.
14. Return the standard mutation response.

The regular member cancellation path and its one-hour lead-time rule remain unchanged.

## 6. Data and Audit

Each generated cancellation operation retains the existing immutable operation fields and adds manager context where needed:

```text
member_id = target member
member_name = target member
operation_type = cancel
created_by = manager actor ID
force_cancelled = true
force_cancel_source_id = selected plan ID
requested_start_at / requested_end_at
effective_start_at / effective_end_at
```

The audit action is `work_plan.force_cancel`. Its snapshot includes:

- manager actor ID, name, and role;
- target member ID and name;
- selected source plan ID;
- requested interval;
- server-clipped interval;
- generated cancellation fragments;
- idempotency key and creation time.

Audit write reconciliation follows the existing durable audit-intent mechanism. A temporary audit sink failure does not roll back an already durable cancellation.

## 7. Frontend Interaction

The existing schedule detail dialog remains the entry point.

- A member viewing their own plan sees the existing `取消计划` action.
- An `owner` or `admin` viewing another member's active green segment sees `强制取消计划`.
- Other users receive read-only details.
- Cancelled grey segments do not offer another cancellation action.

The manager confirmation dialog displays member, date, selected interval, and the message `仅取消当前及未来区间，历史记录仍会保留。` Its confirm button is labelled `强制取消`.

The frontend submits the visible segment's timezone-aware `start_at` and `end_at` plus one stable idempotency key. It does not submit a target member ID. On success, local schedule reconciliation runs immediately and the normal background refresh confirms the authoritative projection.

Loading, success, failure, and duplicate states reuse the existing motion and toast patterns. Focus returns to the triggering schedule segment when the confirmation dialog closes.

## 8. Error Contract

User-facing errors remain Chinese and distinguish:

- `只有 owner 或 admin 可以强制取消成员计划` (`403`);
- `工作计划不存在` (`404`);
- `强制取消时间必须包含时区` (`400`);
- `强制取消结束时间必须晚于开始时间` (`400`);
- `该计划已结束，没有可取消的未来区间` (`400`);
- `所选时间段没有可取消的工作计划` (`400`);
- `计划正在更新，请稍后重试` (`409`);
- idempotent duplicate replay returns the original successful result rather than an error.

## 9. Compatibility

Version 2 operation records use the normal append-only path.

When the selected schedule segment originates from a legacy record, the force-cancel service includes the normalized legacy activation in projection and appends a version 2 cancellation. It does not soft-delete the entire legacy record, because doing so would incorrectly change past schedule history.

The existing legacy `POST /work-plans/{plan_id}/cancel` route remains available for a member's own legacy record and for compatibility. New manager UI actions use the dedicated force-cancel route.

## 10. Testing Strategy

Backend schema and domain tests cover timezone requirements, interval ordering, 30-minute ceiling behavior, exact-boundary behavior, future intervals, and already-ended intervals.

Service tests cover:

- owner and admin force-cancelling another member;
- ordinary-member `403` behavior with no writes;
- target identity derived from storage;
- current interval clipping and future interval preservation;
- no mutation of past projection;
- clipping against concurrent projection changes under the member lease;
- multiple remaining green fragments;
- idempotent replay;
- audit actor and target snapshots;
- legacy-source projection compatibility;
- audit reconciliation after a transient failure.

Route tests cover response codes and error mapping.

Frontend tests cover manager-only labels, own-plan behavior, confirmation copy, request path and payload, stable idempotency, disabled duplicate submission, success reconciliation, error retention, and focus restoration.

## 11. Acceptance Criteria

- `owner` and `admin` can force-cancel another member's active or future green schedule segment.
- Ordinary members cannot call the force-cancel command or mutate another member's plan.
- A currently active interval is cancelled only from the next valid 30-minute boundary onward.
- A future interval is cancelled from its selected start.
- Past and already-ended slots do not change.
- The target member's timeline, not the manager's timeline, receives the cancellation.
- The effective cancellation appears grey while the original activation remains in history.
- Repeated submission with the same idempotency key does not create duplicate operations.
- Audit history identifies the manager, target member, source plan, requested interval, and effective interval.
- Existing own-plan cancellation behavior and its one-hour lead-time rule remain unchanged.
- All backend, frontend, build, diff, and browser interaction checks pass before merge.
