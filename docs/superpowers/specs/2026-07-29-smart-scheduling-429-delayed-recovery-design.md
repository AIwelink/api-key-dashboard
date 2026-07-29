# Smart Scheduling 429 Delayed Recovery Design

## Goal

Prevent an account that reached extreme scheduling from remaining at extreme priority and concurrency indefinitely after it starts returning HTTP 429 responses. Recovery is deliberately delayed for 30 minutes, then the account returns to its type-specific normal automatic priority and concurrency.

## Scope

- Apply only to accounts already managed in extreme mode.
- Reuse the existing 60-second PostgreSQL probe snapshot and smart-scheduling run.
- Reuse the existing bulk account update endpoint.
- Do not add a new probe, frontend setting, or background scheduler.
- Keep the 30-minute delay fixed for this release.

## 429 Detection

The evaluator reads the normalized account `status` and `error_message` fields from the current probe snapshot.

A snapshot is rate limited when either:

- `status` is the normalized rate-limited status; or
- `error_message` contains HTTP status 429 as a standalone status token.

Detection must not classify unrelated numbers such as `4290` as HTTP 429.

Accounts outside an extreme-related state ignore this signal.

## State Machine

The scheduler adds two modes and one timestamp to its existing per-account state:

- `rate_limit_pending`: a 429 was observed while extreme, but the 30-minute delay has not elapsed.
- `rate_limited_cooldown`: normal runtime values have been restored and extreme re-entry is blocked.
- `rate_limit_detected_at`: the time of the first qualifying 429.

Transitions:

1. `extreme` plus current 429 enters `rate_limit_pending`, records `rate_limit_detected_at`, and keeps the extreme priority and concurrency.
2. Repeated 429 responses while pending do not extend or replace the original timestamp.
3. Before 30 minutes elapse, pending accounts keep the extreme priority and concurrency.
4. At or after 30 minutes, pending accounts target the type's automatic priority and normal concurrency, then enter `rate_limited_cooldown`.
5. If the seven-day window resets or usage falls below the configured recovery threshold while pending, the existing quota recovery behavior may restore normal values immediately and return to `normal`.
6. Cooldown accounts keep normal values even when seven-day usage remains at or above the extreme-entry threshold.
7. A cooldown account returns to `normal` only after the seven-day window resets or fresh usage falls below the recovery threshold.
8. Missing or stale quota data cannot release cooldown. The account remains at normal values and in `rate_limited_cooldown`.

The 30-minute boundary is inclusive: an elapsed duration of exactly 30 minutes permits delayed recovery.

## Persistence

The compact scheduler state persists `rate_limit_detected_at` alongside the existing mode, reset identity, target, and evaluation metadata. State loading includes the new field.

When normal quota recovery releases either pending or cooldown state, the persisted rate-limit timestamp is cleared. Outcome records use distinct strategies and reasons for pending hold, delayed recovery, and cooldown hold so operators can distinguish the transitions without storing raw error messages.

## Remote Updates

Pending holds are no-ops when the account already has extreme values. Delayed recovery targets are grouped by target priority, target concurrency, and unchanged latest `group_ids`, then sent through the existing `POST /accounts/bulk-update` flow.

Latest-account revalidation remains in place before any remote change. A failed bulk result does not advance the account to a successful cooldown state because scheduler state is persisted as changed only after that account appears in the bulk success result.

## Testing

Pure evaluator coverage includes:

- first 429 enters pending and preserves extreme values;
- repeated 429 does not move the original timestamp;
- 29 minutes 59 seconds remains pending;
- exactly 30 minutes restores normal values and enters cooldown;
- quota reset or recovery during pending restores immediately;
- cooldown blocks extreme re-entry while quota remains high;
- stale or missing quota holds cooldown at normal values;
- reset or recovered usage releases cooldown;
- normal accounts ignore 429; and
- values such as `4290` are not misclassified.

Service coverage verifies state projection and persistence, no remote write during pending hold, and bulk normal-value updates after the delay.

## Operational Behavior

No migration is required. Existing state documents without `rate_limit_detected_at` continue to evaluate under the current modes. The behavior becomes active after the backend is deployed or restarted and the next enabled group probe runs.
