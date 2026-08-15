# Sparse Smart Scheduling Events Design

Date: 2026-08-14
Status: Approved design

## Context

Smart scheduling runs from the shared 60-second account snapshot. The current
implementation writes one `sub2api_smart_scheduling_outcomes` document for every
eligible account on every run, including repeated `unchanged`, `held`, and
`skipped` results.

Production data shows that this append-only detail stream dominates MongoDB:

- about 34.78 million outcome documents;
- about 2.30 GB of collection storage and 1.03 GB of indexes;
- about 97.7% of recent outcomes are repeated no-op or held results;
- no production API or UI reads the outcome collection.

The existing `sub2api_smart_scheduling_states` collection already provides the
reusable per-account record needed by the scheduler. It has one document per
site and remote account and stores the latest managed scheduling state.

## Goals

- Stop writing one account outcome per 60-second evaluation.
- Reuse the existing per-account state document as the scheduler's current
  record.
- Preserve enough history to diagnose remote update failures and meaningful
  scheduler transitions.
- Keep minute-level run totals for operational visibility.
- Reduce event retention and remove redundant index storage.
- Preserve current scheduling decisions and remote account updates.

## Non-Goals

- This change does not delete, rebuild, or compact the existing 34.78 million
  historical outcome documents.
- This change does not alter the 60-second snapshot or probe cadence.
- This change does not change priority, concurrency, load-factor, quota,
  cooldown, or queue-ordering rules.
- This change does not add an outcome-history UI or public API.

Historical cleanup is a separate destructive operation and requires explicit
approval after the sparse writer is deployed.

## Selected Approach

Use three storage layers with distinct responsibilities:

1. `sub2api_smart_scheduling_states` remains the reusable current record for
   each `(site_id, remote_account_id)`.
2. `sub2api_smart_scheduling_runs` remains the compact summary of each
   scheduling run.
3. `sub2api_smart_scheduling_outcomes` becomes a sparse event collection that
   contains only remote update failures and applied state transitions.

This preserves useful diagnostics while avoiding the storage cost of repeated
per-account snapshots.

## State Records

The existing state identity and unique index remain unchanged. State documents
continue to hold fields such as:

- adapted account type;
- managed mode, strategy, and reason;
- last target priority, concurrency, and load factor;
- seven-day quota and reset identity;
- rate-limit cooldown data;
- queue position metadata;
- last probe, run, evaluation, and successful-update timestamps;
- captured original load factor used for recovery.

Successful remote changes update the state after the remote API confirms the
account was updated. An `unchanged` decision may refresh the same state document
in place. Repeated `held` and `skipped` decisions do not advance managed state,
matching the current behavior and preventing a missing or stale quota snapshot
from clearing an active extreme or cooldown state.

State documents remain bounded to one record per account. They contain no full
account snapshot or credentials.

## Event Rules

An outcome document is written only in either of these cases:

### Remote update failure

Write an event whenever an attempted account read or runtime update fails. This
includes per-account failures, bulk update failures, admin authentication or
configuration failures, and scheduler lease loss. Repeated failed attempts are
separate events because each represents a real remote operation that failed.

The failure event keeps the sanitized `error_code` and `error_type`; it must not
store exception messages, credentials, or remote response bodies.

### Applied state transition

Write an event when an existing account state and the newly persisted state
differ in either:

- `mode`; or
- normalized `last_target`, including priority, concurrency, and load factor.

A reason, strategy, quota percentage, timestamp, or queue metadata change alone
does not create an event. Those fields still update in the reusable state when
the normal state-persistence path runs.

The first state created for an account establishes a baseline and does not
create a transition event. This avoids a one-time event burst when the new
writer is deployed.

If a proposed transition fails remotely, only the failure event is written and
the previous successfully applied state remains authoritative. A later
successful retry writes the transition event.

### Suppressed outcomes

Do not write outcome documents for repeated:

- `unchanged` decisions with no mode or target transition;
- `held` decisions;
- `skipped` decisions.

These decisions remain visible through the run summary counters.

## Event Shape

Retain the existing sanitized outcome fields where applicable and add an
explicit `event_type`:

- `remote_update_failed` for failures;
- `state_transition` for successful applied transitions.

Transition events include the previous and applied state projections so the
change is directly inspectable. The projections contain only `mode` and the
normalized target runtime values. Existing contextual fields such as site,
run, probe, account, groups, adapted type, quota metadata, queue metadata, and
evaluation time remain available.

The event `_id` remains deterministic per run and account. At most one outcome
event is written for an account in a run.

## Run Summaries

Run summaries continue to record:

- scanned;
- changed;
- unchanged;
- skipped;
- failed;
- completion status and timing.

The counters describe scheduler decisions and remote results, not the number of
outcome events. No per-account no-op history is needed to calculate them.

Reason-count aggregation is intentionally deferred because no current reader
requires it. It can be added later without changing the sparse event model.

## Retention and Indexes

New outcome events expire after 7 days. Run summaries continue to expire after
90 days. Existing outcome documents retain their stored `expires_at` values and
therefore age out according to the old 30-day schedule unless a separately
approved cleanup is performed.

Keep:

- the outcome `_id` index;
- the outcome `expires_at` TTL index;
- existing state and run indexes.

Remove the outcome compound unique index on
`(site_id, run_id, remote_account_id)`. The deterministic outcome `_id` already
enforces the same one-event-per-run-and-account identity, and no production
query uses the compound index.

Bootstrap must tolerate both fresh databases and deployments where the
redundant index has already been removed.

## Data Flow

For each eligible account in a 60-second scheduling run:

1. Load its reusable state with the fields required for scheduling and
   transition comparison.
2. Evaluate the account from the shared snapshot.
3. For `held` or `skipped`, increment the run counter and write no outcome.
4. For `unchanged`, update the reusable state as currently required; write an
   event only when an existing state's mode or normalized target changed.
5. For `change`, re-read the remote runtime values, batch the remote update, and
   wait for the per-account success result.
6. On success, persist state and write one transition event when the prior state
   differs.
7. On failure, leave the prior applied state authoritative and write one
   sanitized failure event.
8. Finish the run summary independently of how many sparse events were written.

## Error Handling

Event persistence remains isolated from remote update execution. Existing
remote error classification and lease-stop behavior are preserved.

Database persistence failures continue to fail the scheduling run rather than
silently losing state or audit events. This design does not introduce a second
retry queue.

Index removal handles MongoDB's index-not-found result as an already-complete
migration. Other database errors remain visible during bootstrap.

## Testing

Service tests cover:

- repeated unchanged decisions write state but no outcome event;
- held and skipped decisions write no outcome event;
- initial state creation writes no transition event;
- mode transition writes one `state_transition` event;
- priority, concurrency, or load-factor target transition writes one event;
- reason-only and queue-metadata-only changes write no event;
- a successful remote update persists state before recording its transition;
- a failed remote update records `remote_update_failed` and does not advance the
  applied state;
- repeated failures remain independently recorded;
- run counters remain correct when outcome writes are suppressed;
- the event expiration is 7 days;
- bootstrap removes the redundant compound index and preserves the TTL index.

The full backend suite must pass. Frontend behavior is unchanged, so no
frontend code or visual verification is required for this storage-only change.

## Deployment and Historical Cleanup

Deploy the sparse writer first and verify that outcome insert volume falls from
roughly one document per eligible account per minute to only transitions and
failures. Expected recent-volume reduction is approximately 97.7% based on the
current distribution.

Dropping the redundant index can reclaim its index allocation independently of
historical document cleanup. MongoDB may not return all collection storage to
the filesystem merely because TTL deletes documents.

After the new writer is stable, historical cleanup should be planned as a
separate maintenance operation. The preferred approach is to pause writers and
rebuild or drop the unused historical outcome collection, rather than issuing
millions of individual deletes. Exact commands and recovery precautions require
separate approval.
