# Account 403 Automatic Recovery Design

## Goal

Automatically retest accounts whose latest database snapshot reports HTTP 403, then restore their remote state and scheduling after a successful model request.

## Scope

- Change the unified account-test model from `gpt-5.4` to `gpt-5.5`.
- Keep the normal account-test interval at 24 hours.
- Retest only HTTP 403 accounts on a three-minute interval.
- Use `sub2api_accounts_cache` as the only account-state input. This collection is refreshed from PostgreSQL at the configured one-minute site cache interval.
- Reuse the existing unified test scheduler, durable events, latest-state documents, dispatcher replay, site clients, and application lifecycle task.
- Add no frontend setting, additional account-list request, or separate recovery scheduler.

## Data Source

The account-test scheduler continues to read accounts from `sub2api_accounts_cache`. The cache refresh task obtains the pool snapshot directly from PostgreSQL and mirrors one document per remote account into MongoDB.

The scheduler reads these snapshot fields:

- site ID and remote account ID;
- top-level and nested `status`;
- top-level and nested `error_message`;
- top-level and nested `schedulable`;
- cache `fetched_at`;
- normalized email and the account attributes already required by plan correction.

No decision reads account state from the frontend or performs a remote account-detail request. Model-test and recovery responses are action results, not alternate account-list sources.

## HTTP 403 Recognition

An account is in snapshot 403 state when its current cached status or error message contains HTTP status 403 as a standalone status token. Recognition must not classify unrelated values such as `4030` as HTTP 403.

A model-test event is a 403 result when the normalized event `http_status` is 403. Both confirmed inactive-owner responses and other HTTP 403 responses qualify for the fast retry interval. `model_not_supported` remains its existing outcome classification, but its HTTP 403 response still qualifies for a three-minute retest while the current snapshot or latest test remains 403.

Only HTTP 403 activates the fast interval. HTTP 401, 402, 429, 5xx, transport errors, and ordinary disabled accounts retain their existing behavior.

## Scheduling Policy

The unified scheduler keeps a single global remote-test concurrency of one.

Candidate ordering is:

1. due HTTP 403 accounts, oldest due first;
2. never-tested accounts;
3. ordinary due accounts, oldest due first.

Normal account-test events set `next_test_at` to 24 hours after `tested_at`. An event created from a current snapshot 403 state, or whose model request returns HTTP 403, initially sets `next_test_at` to three minutes after `tested_at`. A successful recovery handler subsequently changes only the latest state deadline back to the normal 24-hour interval.

The current one-minute snapshot may move an account into rapid recovery before its previously stored 24-hour deadline. A newly observed snapshot 403 account is immediately due; subsequent failed tests use the three-minute interval.

After successful recovery, the latest account-test state returns to the 24-hour interval. A cached 403 snapshot fetched before `recovery_completed_at` is treated as stale action input and cannot immediately requeue the account. A newer cache snapshot that still reports 403 re-enters the three-minute policy.

## Test Execution

All unified account tests use:

- model: `gpt-5.5`;
- mode: `default`;
- prompt: empty string.

The event and latest state record `gpt-5.5`. Plan-correction provenance uses the event model instead of a hard-coded old model name.

The existing event durability order remains unchanged:

1. execute the model test;
2. insert the immutable result payload;
3. upsert the latest test state;
4. synchronize derived cache test fields;
5. dispatch recovery, scheduling, and plan-correction judgments.

## Recovery Context

Each test event records whether it was initiated from a current snapshot 403 state and the snapshot timestamp used for that judgment. The recovery context contains no raw credentials or unsanitized remote errors.

The scheduling dispatch section records two recovery phases:

- remote state recovery status, attempts, completion time, and sanitized error;
- scheduling-enable status, attempts, completion time, and sanitized error.

These phase fields live under the existing mutable dispatch area. The immutable model-test result payload is not rewritten.

## Successful Recovery Flow

When a model test succeeds for an account tested from a current snapshot 403 state, the latest-event guard runs before every remote action. The scheduling handler performs these steps in order:

1. Call `POST /accounts/{account_id}/recover-state`.
2. Persist completion of the remote-state recovery phase.
3. Ensure the account has `schedulable=true`, calling the existing schedulable endpoint only when the cached value is not already true.
4. Persist completion of the scheduling-enable phase.
5. Mark `recovery_completed_at` in the latest test state and restore `next_test_at` to the normal 24-hour interval.
6. Mirror returned `status`, `error_message`, and `schedulable` values into the local account cache when those values are present; the next PostgreSQL cache refresh remains authoritative and verifies the result.

The remote state must be recovered before scheduling is opened. A recover-state failure cannot enable scheduling.

A normal successful test that was not initiated from snapshot 403 retains the existing behavior of re-enabling an explicitly disabled account, without calling recover-state.

## Failure And Replay

- A model request that still returns HTTP 403 performs no recovery action and is tested again after three minutes.
- A non-403 model failure performs no recovery action. It remains on the three-minute interval only while the latest one-minute snapshot still reports HTTP 403.
- A recover-state failure leaves scheduling unchanged and marks the scheduling handler for retry after three minutes.
- A scheduling-enable failure preserves the completed recover-state phase and retries only the incomplete enable phase after three minutes.
- Handler replay rechecks that the event is still the latest account-test event before acting.
- Completed phases are skipped on replay, making partial recovery idempotent without relying on repeated remote calls.
- Site admin authentication errors keep the existing site-level backoff and do not become account 403 events.
- Cancellation and account removal retain the existing scheduler and dispatcher behavior.

## State And Cache Fields

The latest account-test state continues to own test timing and adds compact recovery metadata:

- latest snapshot HTTP 403 identity and observation time;
- recovery event ID;
- recovery completion time;
- current normal or rapid interval decision.

The test event stores the snapshot recovery context and per-phase dispatch results. Existing TTL retention remains 90 days, and no new collection is required.

`sub2api_accounts_cache` remains the account-state input. Immediate cache writes after successful remote actions are derived mirrors only; the one-minute PostgreSQL refresh may correct them.

## Observability

Operators can distinguish:

- ordinary 24-hour tests;
- snapshot-triggered 403 recovery tests;
- model responses that remain HTTP 403;
- recover-state failures;
- scheduling-enable failures;
- fully recovered accounts.

Logs contain site ID, remote account ID, phase, and exception type. They do not contain credentials, admin tokens, or unsanitized response bodies.

## Testing

Unit coverage includes:

- the unified model is `gpt-5.5` for normal and recovery tests;
- normal accounts retain a 24-hour interval;
- exact snapshot HTTP 403 produces a three-minute effective due time;
- exact model-response HTTP 403 produces a three-minute next-test time;
- `4030` and non-403 failures do not activate the rapid policy;
- due 403 accounts precede never-tested and ordinary due accounts;
- a successful snapshot-403 test calls recover-state before enabling scheduling;
- recover-state failure never enables scheduling;
- scheduling-enable failure preserves the completed recovery phase;
- handler replay skips completed phases and rejects stale events;
- successful recovery restores the 24-hour interval;
- a cache snapshot older than recovery completion cannot immediately requeue the account;
- a newer snapshot that still reports 403 re-enters rapid testing;
- ordinary successful tests retain their existing scheduling behavior without recover-state;
- plan-correction provenance reports `gpt-5.5`;
- account source projections come only from `sub2api_accounts_cache`;
- the focused account-test suite and complete backend suite pass.

## Deployment

No data migration is required. Existing state documents without recovery metadata continue to use their stored `next_test_at`. The first scheduler evaluation against the refreshed account cache can advance a current 403 account into the rapid queue. Application restart activates the `gpt-5.5` model and recovery behavior through the existing unified scheduler task.
