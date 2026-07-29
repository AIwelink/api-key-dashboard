# Quota-Aware Smart Scheduling Design

## Goal

Add quota-aware account scheduling to the existing 60-second Sub2API account probe. The scheduler automatically recognizes the adapted account type, preserves valid manual priorities, corrects invalid priorities and concurrency, and temporarily promotes accounts whose seven-day quota usage reaches a configured extreme threshold.

Scheduling is opt-in per group. Both strategies are disabled by default. Strategy parameters are shared at the site level rather than duplicated for every group.

## Scope

The first release changes only the account-level Sub2API `priority` and `concurrency` fields. It does not change `schedulable`, account status, group membership, credentials, model mappings, or per-group `account_groups.priority`.

The design leaves room for additional scheduling strategies by separating pure account decisions from probe orchestration and remote updates.

## Priority Semantics

Sub2API schedules smaller priority numbers first. The system therefore reserves lower-numbered bands immediately before each account type's manual band, plus a global extreme band at the front.

Default site-level rules are:

| Adapted type | Manual band | System band | Fixed automatic priority | Normal concurrency | Extreme entry | Recovery threshold | Extreme concurrency |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Pro | 1000-1090 | 991-999 | 991 | 30 | 95% seven-day used | 80% | 100 |
| Plus | 200-290 | 191-199 | 191 | 30 | 90% seven-day used | 80% | 100 |
| K12 | 100-190 | 91-99 | 91 | 30 | 90% seven-day used | 80% | 100 |
| BugTeam / adapted Team | 50-90 | 41-49 | 41 | 30 | 90% seven-day used | 80% | 100 |

The global extreme band is 1-20 and the fixed extreme priority is 10.

The legal normal priority set for a type is the union of its manual and system bands. With the defaults these bands are adjacent, so the effective legal ranges are Pro 991-1090, Plus 191-290, K12 91-190, and BugTeam / adapted Team 41-90.

All numbers in this table are editable at the site level. Validation requires ordered non-overlapping bands, a fixed automatic priority inside the matching system band, an extreme priority inside the extreme band and ahead of all normal bands, positive concurrency values, and recovery thresholds below entry thresholds.

## Strategies

### Account Type Normalization

This strategy is controlled by the group-level `type_priority_enabled` flag.

For each supported account below its extreme threshold:

- Reuse the existing adapted account type rules, including verified or cached plan types, special Plus and Team handling, BugTeam recognition, K12 fallback rules, and Pro recognition.
- If the current priority is inside the type's legal normal priority set, preserve it.
- If the current priority is outside that set, assign the type's fixed automatic priority.
- Set concurrency to the configured normal concurrency whenever it differs.
- Skip Free, unknown, and unsupported types with `unsupported_account_type`.

### Seven-Day Extreme Acceleration

This strategy is controlled by the group-level `quota_acceleration_enabled` flag and takes precedence over account type normalization.

- When fresh `codex_7d_used_percent` reaches the type's entry threshold, set priority to the fixed extreme priority and concurrency to the type's extreme concurrency.
- Keep the account extreme while it remains in the same seven-day window and usage is above the recovery threshold.
- Exit extreme when the normalized seven-day reset identity changes or usage falls below the configured recovery threshold.
- On exit, set the fixed automatic priority and normal concurrency. Do not restore the value that existed before extreme acceleration.
- Missing, invalid, or stale quota data never starts or exits an extreme transition. Quota data older than five minutes is stale. An account already recorded in extreme mode keeps its current values until fresh quota data can confirm either continued acceleration or recovery; account type normalization must not override it while quota is stale.

Account-level scheduler state records the last applied mode and seven-day reset identity. This prevents an extreme-only configuration from treating an unrelated manual priority in the 1-20 band as a scheduler-owned value.

## Group Eligibility

Each group has two independent flags:

```json
{
  "type_priority_enabled": false,
  "quota_acceleration_enabled": false
}
```

Missing fields in existing documents resolve to `false`.

An account is eligible for a strategy when at least one of its current groups enables that strategy. Accounts in multiple eligible groups are deduplicated by remote account ID and evaluated once. The site-level rules are identical across groups, so eligible memberships cannot produce competing target values.

Disabling a strategy stops future actions but does not roll back values already written. A later manual change remains untouched until an enabled strategy evaluates the account again.

## Probe Integration

The existing account probe remains the scheduler. The deployed group probe interval is already configured to 60 seconds, so no second scheduling interval is introduced.

The due-group calculation considers observability and smart scheduling independently. A group is due when its 60-second interval has elapsed and observability or either scheduling strategy is enabled. This allows scheduling to continue even when ordinary observability is disabled.

For each due site:

1. Acquire a site-scoped MongoDB lease so only one process can apply smart scheduling.
2. Read the site, site-level scheduling settings, and group settings.
3. Fetch one Sub2API PostgreSQL pool snapshot containing accounts, account-group relationships, and quota fields.
4. Run the existing probe normalization and monitoring work.
5. Pass the same normalized snapshot to the smart scheduling evaluator.
6. Produce at most one decision per remote account ID, with extreme acceleration taking precedence.
7. For decisions that differ from the PostgreSQL snapshot, fetch the latest account through the Admin API and evaluate again against its current priority and concurrency.
8. Group re-evaluated changes by target `priority`, `concurrency`, and the latest unchanged `group_ids`, then POST each batch to `/accounts/bulk-update`.
9. Record the run, per-account outcomes, and scheduler state, then release the lease.

The backend reads groups and accounts directly from PostgreSQL. The frontend never scans accounts or performs scheduling calculations.

## Storage

### Site Settings

Use `app_settings` document `smart_scheduling:<site_id>` for the four type rules and global extreme rule. The document includes audit metadata and returns normalized defaults when it does not yet exist.

### Group Settings

Extend existing `group_observability_settings` documents with the two strategy flags. This reuses the established database-backed group list and PATCH flow without attaching a full parameter set to every group.

### Scheduler State

Store one compact document per site and remote account in `sub2api_smart_scheduling_states`. It contains the adapted type, last applied mode, last target values, last seven-day reset identity, last evaluated time, and last successful update time. It contains no credentials or full account snapshot.

### Runs and Outcomes

Store run summaries in `sub2api_smart_scheduling_runs` and bounded account outcomes in `sub2api_smart_scheduling_outcomes`. Outcomes include remote account ID, group IDs, adapted type, quota percentage and freshness, matched strategy, before and target values, status, and a sanitized failure code. Retention indexes remove detailed outcomes after 30 days while retaining compact run summaries for 90 days.

## API

Add:

```text
GET   /api-pools/smart-scheduling/settings?site_id=<site_id>
PATCH /api-pools/smart-scheduling/settings?site_id=<site_id>
```

The GET response contains normalized current rules, defaults, last-run summary, and update metadata. PATCH validates the complete effective configuration before atomically replacing the stored rules and writing an audit record.

Extend the existing group observability response and PATCH schema with `type_priority_enabled` and `quota_acceleration_enabled`. Existing documents and callers remain compatible because both fields default to false and are optional in PATCH requests.

The existing immediate probe endpoint also runs smart scheduling because it uses the same probe pipeline.

## Frontend

Add a full-width Smart Scheduling section to the site configuration workspace.

- A site-level rule table edits manual bands, system bands, fixed automatic priority, normal concurrency, seven-day entry and recovery thresholds, and extreme concurrency for all four adapted types.
- A global control edits the extreme band and fixed extreme priority.
- A group table uses the database-backed group response and exposes only the two strategy toggles plus latest execution status.
- The section shows the current 60-second probe interval and the most recent scanned, changed, skipped, and failed counters.
- The browser never reads the account list for scheduling and never computes target values.

Controls save through the backend API, show validation errors without discarding edits, and disable repeated submissions while a save is in flight.

## Consistency and Failure Handling

- A MongoDB lease prevents duplicate writers for the same site across processes.
- Remote state is re-read only for candidate changes, limiting Admin API traffic while avoiding stale-snapshot overwrites of recent manual edits.
- Bulk updates contain target `priority` and `concurrency` plus each batch's unchanged latest `group_ids`, so accounts with different group memberships are never mixed.
- One account failure does not stop other accounts. An unavailable Admin API ends remote updates for the run and retries on the next probe.
- Missing type, quota, or reset data produces explicit skip outcomes instead of guessed decisions.
- Identical target and current values do not call the Admin API.
- Settings changes, group toggle changes, and run summaries are audited without credentials or sensitive account fields.
- Disabling strategies does not trigger rollback.

## Testing

Backend decision tests cover:

- valid manual and system priorities are preserved;
- out-of-band values use 991, 191, 91, or 41;
- mismatched normal concurrency becomes 30 without changing a legal priority;
- Plus, K12, and adapted Team enter extreme at 90%;
- Pro enters extreme at 95%;
- exact threshold boundaries are inclusive;
- extreme priority and concurrency become 10 and 100;
- reset identity changes and sub-80% usage restore fixed normal values;
- stale or missing quota cannot trigger extreme;
- Free and unknown types are skipped;
- extreme precedence, multi-group deduplication, and default-off behavior.

Orchestration tests cover one PostgreSQL snapshot per probe, site lease behavior, latest-account revalidation, bulk grouping by target and group membership, partial per-account failures, no-op suppression, sanitized records, state persistence, and retention indexes.

API tests cover normalized defaults, complete configuration validation, atomic rejection of invalid ranges, group toggle persistence, backward-compatible false defaults, and audit writes.

Frontend tests cover rule editing, validation display, group toggle independence, default-off rendering, save-in-flight controls, backend-only group loading, last-run counters, and the production build.
