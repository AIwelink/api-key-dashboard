# Extreme Load Factor Scheduling Design

## Goal

Extend quota-aware smart scheduling so extreme acceleration also applies a configurable `load_factor` per adapted account type. The scheduler must restore the exact account value that existed before entering extreme mode when quota recovery or the existing 30-minute 429 cooldown completes.

## Scope and Decisions

- The scheduling weight is the Sub2API account field `load_factor`.
- `rate_multiplier` is a billing/fee multiplier and is never changed by smart scheduling.
- Configuration is stored once per site and contains an `extreme_load_factor` for each adapted type: `pro`, `plus`, `k12`, and `team`. The default for every type is `10000`.
- Group strategy switches remain database-backed and default to disabled. The frontend continues to read groups through the existing group settings API; it does not scan accounts.
- The existing 60-second PostgreSQL snapshot remains the scheduling input. No additional polling interval is introduced.

## Decision Flow

For each eligible account, the pure evaluator keeps the existing precedence:

1. If quota acceleration is entering or continuing extreme mode, target the global extreme priority, the type's extreme concurrency, and the type's `extreme_load_factor`.
2. If the account is in the existing 429 `rate_limit_pending` state, keep all three extreme targets until the fixed 30-minute delay elapses.
3. When the seven-day quota recovers or its reset identity changes, or when the 429 delay elapses, target the type's fixed normal priority, normal concurrency, and the saved original load factor.
4. Outside extreme mode, type-priority scheduling preserves legal priorities and only corrects normal concurrency as before; it does not alter a normal account's load factor.

The evaluator returns the target `load_factor` alongside priority and concurrency, plus whether the decision needs an original-value capture. Unsupported types and stale or missing quota data retain the existing skip/hold behavior.

## State and Update Ordering

Scheduler state remains one compact document per site and remote account. Add:

- `original_load_factor`: the value captured immediately before the first extreme update;
- `original_load_factor_captured_at`: UTC timestamp for diagnostics.

The service reuses the current state lease and batch update flow:

1. Re-evaluate candidate accounts against the latest Admin API account.
2. If an extreme target is needed and the state has no saved original, persist the latest snapshot value before issuing the remote update. This makes a process crash between state capture and the API call recoverable.
3. POST the changed fields in `/api/v1/admin/accounts/bulk-update`, including `load_factor` whenever it differs from the latest remote value. Batches are still split by target priority, concurrency, load factor, and unchanged group IDs.
4. Only after a successful account update mark the state mode and target values. Failed updates retain the captured original value and retry on the next 60-second run.
5. On successful recovery, send the saved original load factor and then clear both saved fields. If no saved value exists for a legacy extreme state, capture the current snapshot value before applying any new extreme target; the scheduler never invents a normal fallback.

An account that is already at the configured extreme load factor is still captured as-is. Manual changes during extreme mode do not replace the saved original; the configured extreme value is re-applied on the next run and the saved original is used for recovery.

## Configuration and API

Extend each account-type smart scheduling rule with:

```json
{
  "extreme_load_factor": 10000
}
```

The backend normalizer, Pydantic settings schema, GET/PATCH response, and frontend form use the same field name. Existing settings documents without the field receive `10000` through normalization. Validation requires an integer greater than or equal to `1` and within the existing Sub2API load-factor limit used by account update schemas.

The frontend adds one editable `extreme_load_factor` column to the existing per-type rule table. It submits the complete normalized rules document and displays backend validation errors without changing group toggle behavior.

## Consistency and Failure Handling

- `load_factor` is included in the existing allowed bulk-update payload fields and in the request-shape tests.
- No-op updates compare all three managed fields against the latest remote account before calling the Admin API.
- A partial bulk response updates state only for successful account IDs; unsuccessful IDs keep their captured originals and receive explicit failure outcomes.
- Disabling quota acceleration preserves the existing hold-extreme-state behavior; it does not silently restore values. Recovery happens only through the existing quota or cooldown transitions.
- State and outcome records contain no credentials or full account snapshots.

## Testing

Decision tests cover:

- default `extreme_load_factor` of `10000` for all four types;
- per-type normalization and validation;
- entering and continuing extreme mode targets priority, concurrency, and load factor;
- capturing the original load factor only once;
- quota recovery and 429 cooldown restoring the captured value;
- missing legacy capture behavior and stale quota holds;
- normal-mode scheduling leaving load factor untouched.

Service and client tests cover:

- bulk payload acceptance and grouping by load factor;
- state capture before remote update;
- state clearing only after successful recovery;
- partial failures and retry preservation;
- no-op suppression when all three managed fields already match.

Frontend tests cover default rendering, editing each type's extreme load factor, payload serialization, validation errors, and save-in-flight controls. The backend and frontend production test/build commands remain the verification gates.
