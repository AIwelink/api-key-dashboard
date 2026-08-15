# Dynamic Account Priority Queue Design

## Goal

Replace the fixed normal automatic priority for smart-scheduled accounts with a stable, site-wide queue per adapted account type. Older usable accounts receive smaller priority values and are consumed first. When an account becomes temporarily unusable, later usable accounts move forward. When that account recovers, it returns to the position implied by its original account creation time.

## Scope

- Extend the existing Sub2API smart scheduling runner; do not add a separate scheduler.
- Reuse each group's existing `type_priority_enabled` switch. The default remains disabled.
- Calculate one queue per site and adapted account type, not one queue per group.
- Continue reading accounts, group membership, quota, runtime status, and creation time from the PostgreSQL-backed 60-second snapshot already passed into smart scheduling.
- Continue writing account runtime changes through the existing bulk account update endpoint.
- Do not add frontend account-list requests or frontend-side sorting decisions.

## Eligibility And Identity

An account participates when it belongs to at least one group whose `type_priority_enabled` setting is true. Membership in additional disabled groups does not exclude it. An account that belongs to multiple enabled groups appears only once in the site-wide queue.

The queue uses the existing adapted scheduling types:

- `pro`;
- `plus`, including aliases already adapted to plus;
- `k12`;
- `team`, including `bug_team` and `special_team` aliases.

Unsupported account types remain unchanged. Accounts that do not belong to any type-priority-enabled group remain unchanged, even if quota acceleration is enabled for another strategy.

## Stable Order

For every site and adapted account type, eligible accounts are divided into two normal-queue partitions:

1. currently usable accounts;
2. temporarily unusable accounts that may still hold a normal priority.

Within each partition, accounts sort by:

1. remote account `created_at`, oldest first;
2. remote account ID, ascending, as a deterministic tie-breaker.

Missing or invalid `created_at` sorts after valid creation timestamps within the same partition, then by account ID. The source value is the remote account `created_at` supplied by the PostgreSQL snapshot, not the MongoDB cache document insertion time or smart-scheduling state creation time.

This order is recomputed on every smart scheduling run. No persistent queue position is stored. Recalculation makes account additions, removals, group changes, status changes, and recovery self-correcting.

## Usability Classification

A normal-queue account is currently usable only when the current snapshot reports that it is active and schedulable and it is not in a smart-scheduling rate-limit recovery state. Existing exact HTTP 429 recognition is reused.

The temporarily unusable partition includes accounts that remain eligible for type-priority management but are currently disabled, unschedulable, in error, HTTP 403, or in `rate_limit_pending` / `rate_limited_cooldown`. These accounts retain a value inside the normal type interval but sort after usable accounts.

Quota acceleration remains authoritative over the normal queue. An account actively in `extreme` or waiting in the initial 30-minute `rate_limit_pending` stage keeps the existing extreme target priority and concurrency. It does not consume a unique normal queue slot while the extreme target is being applied. Once the existing 429 delay restores normal concurrency and the account enters cooldown, it joins the temporarily unusable normal partition. When the quota recovery condition or window reset makes it usable again, it returns to its original chronological position.

## Priority Assignment

Normal queue values use each type's configured manual priority interval, from smallest to largest:

| Adapted type | Default interval | Example |
| --- | --- | --- |
| team | `50-90` | `50, 51, 52, ...` |
| k12 | `100-190` | `100, 101, 102, ...` |
| plus | `200-290` | `200, 201, 202, ...` |
| pro | `1000-1090` | `1000, 1001, 1002, ...` |

Each normal-queue account receives:

```text
min(manual_priority_min + queue_index, manual_priority_max)
```

The queue index is zero-based after excluding accounts whose current decision is still the extreme target. If the account count exceeds the configured interval capacity, every overflow account receives the interval maximum. For example, the 41st and all later team accounts use priority `90`.

The existing fixed `automatic_priority` setting remains the recovery fallback for legacy state and incomplete queue context, but normal type-priority runs use the calculated queue priority. The existing extreme priority configuration remains unchanged, with default priority `10`.

## Dynamic Movement

Example with three team accounts ordered by original creation time:

```text
All usable:       A=50, B=51, C=52
A unavailable:   B=50, C=51, A=52
A recovered:     A=50, B=51, C=52
```

The scheduler is allowed to overwrite any current priority inside the normal manual or system intervals. This is necessary to eliminate gaps and duplicates after state changes. Normal concurrency continues to use the configured per-type `normal_concurrency`; extreme concurrency continues to use `extreme_concurrency`.

## Evaluation Flow

The smart scheduling service performs a site-wide planning phase before individual remote updates:

1. Build the existing deduplicated eligible-account set from group settings.
2. Preload current smart-scheduling states once.
3. Adapt account types and compute the existing quota/extreme/rate-limit mode for every account.
4. For each type, exclude accounts currently receiving an extreme target from normal slot consumption.
5. Partition the remaining accounts by usability, apply the stable sort, and assign normal priorities from the configured interval.
6. Feed each account's calculated normal priority into the existing account evaluation result.
7. Re-read only accounts whose target differs, preserving the current remote verification behavior.
8. Recalculate the target against the same immutable run plan and issue existing bulk runtime updates only for confirmed differences.
9. Persist the existing scheduling state and outcome documents with the calculated target and a queue-specific reason.

The plan is immutable for one run. A newer 60-second snapshot is handled by the next run instead of changing ranks midway through remote writes.

## Manual And Strategy Interaction

- Enabling type priority authorizes the scheduler to rewrite normal priority values, including values manually set within the configured normal interval.
- Disabling type priority stops queue-based priority changes for the affected account unless another enabled group still authorizes the strategy.
- Quota acceleration can still move an account to the extreme target independently.
- Type priority enabled without quota acceleration still maintains the chronological normal queue.
- Quota acceleration enabled without type priority retains existing quota behavior and does not opt the account into normal queue ranking.
- Existing 429 detection, 30-minute delay, cooldown, quota recovery threshold, and reset-window behavior remain unchanged except that the restored normal priority comes from the current queue plan when type priority is enabled.

## Failure And Consistency

- A failed remote update does not persist a successful target state for that account.
- Other accounts in the same bulk target may still succeed according to the existing partial-success handling.
- A failed account is reconsidered from the next full snapshot and queue plan; no rank mutation is committed separately.
- The existing per-site scheduling lease prevents two workers from applying competing site-wide plans.
- Unsupported or malformed creation times never abort a run; deterministic account-ID ordering remains available.
- Priority values never cross a configured type interval boundary.

## Observability

Scheduling outcomes retain the existing before/target values and add enough queue context to explain movement:

- adapted account type;
- queue partition (`usable` or `temporarily_unusable`);
- zero-based queue index;
- calculated normal priority;
- queue order timestamp when valid;
- reason such as `type_queue_positioned`, `type_queue_advanced`, or the existing extreme/recovery reason.

No credentials, raw tokens, or unsanitized remote responses are added.

## Configuration

No new group switch is introduced. The existing type-priority switch controls the feature and remains off by default.

The existing editable manual priority minimum and maximum fields define the dynamic queue interval. Existing normal/extreme concurrency, extreme priority, entry threshold, recovery threshold, and automatic priority fields remain configurable. The UI may relabel the manual interval as the normal queue interval in a later presentation-only change, but this feature does not require that frontend change.

## Testing

Unit and service coverage will include:

- oldest usable account receives the smallest type priority;
- equal creation timestamps use remote account ID order;
- missing creation timestamps sort after valid timestamps;
- a temporarily unusable oldest account moves behind usable accounts;
- later usable accounts close the priority gap immediately;
- a recovered oldest account returns to the queue head;
- team, k12, plus, and pro use their configured intervals;
- overflow accounts clamp to the interval maximum;
- aliases share their adapted type queue;
- accounts across different groups share one site-wide type queue;
- an account in multiple enabled groups is ranked once;
- accounts with no enabled type-priority group remain unchanged;
- manual normal priorities are overwritten by calculated positions;
- extreme accounts retain priority `10` and do not consume normal slots;
- delayed 429 recovery uses normal concurrency and the planned normal queue priority;
- quota-only groups do not enable normal queue ranking;
- only changed accounts invoke remote verification and bulk update;
- partial remote failures do not persist false success states;
- all existing smart-scheduling, account-probe, cache-source, and backend tests pass.

## Deployment

No database migration is required. The first smart scheduling run after deployment recalculates eligible normal priorities from the latest PostgreSQL snapshot. This may intentionally update many accounts once to remove gaps and establish chronological ordering. Later runs update only accounts whose calculated priority, concurrency, or strategy state changed.
