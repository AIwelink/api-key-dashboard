# Plus Self-Produced Account Promotion Design

## Goal

Add a dedicated background workflow and operations page for the `US06-5002` Sub2API site. The workflow periodically tests accounts in group `4` (`plus自产`) with `gpt-5.4`, recognizes usable Plus accounts, prefixes their names with `plus `, and moves them to group `6` (`plus 正常号池`).

## Fixed Scope

- Site ID: `US06-5002`
- Source group: `4` (`plus自产`)
- Destination group: `6` (`plus 正常号池`)
- Probe model: `gpt-5.4`
- Default interval: 15 minutes
- Processing order: serial, one account test at a time
- Failed or uncertain accounts remain unchanged in group `4`

The first version exposes only the enabled state and interval as editable settings. Site, groups, and model are shown as read-only workflow facts. This keeps a narrowly targeted operational tool while allowing the requested interval to be changed later.

## Result Classification

The classifier receives either a normal `Sub2ApiClient.test_account` result or an exception/error string raised by the account-test endpoint. Matching is case-insensitive.

Classification precedence is:

1. If the error contains `model is not supported when using Codex with a ChatGPT account`, classify it as `model_not_supported` and fail the account. This rule takes precedence over every success rule and is independent of the model name embedded in the message.
2. If the test returns `success=true`, classify it as `passed`.
3. If the error represents HTTP 429, including text such as `API returned 429` or a direct test-endpoint 429 response, classify it as `rate_limited_but_eligible` and treat the account as successful.
4. All other responses and exceptions are failures. Record the normalized error and leave the remote account unchanged.

HTTP 429 counts as success because it proves the account can reach the requested model even though capacity is temporarily limited. The ChatGPT-account model compatibility error does not prove Plus eligibility and must never promote an account.

## Backend Architecture

Create a focused `plus_self_produced` Sub2API module. It owns settings, due-time calculation, result classification, serial probing, remote promotion, run history, and the scheduler loop. It reuses `Sub2ApiClient` for live account listing, model tests, and account updates.

The application lifespan starts one scheduler task. The scheduler wakes on a short polling cadence, reads the persisted setting, and starts a run only when the workflow is enabled and the configured interval has elapsed. An in-process lock prevents manual and scheduled runs from overlapping. The persisted last-run timestamps also make due-time behavior explicit after a restart.

One run performs these steps:

1. Load the configured Sub2API site with its token and validate that groups `4` and `6` exist.
2. Fetch the live remote accounts belonging to group `4`.
3. Test each account serially with model `gpt-5.4`, an empty prompt, and default mode.
4. Classify the result using the precedence above.
5. For successful classifications, build an idempotent name: compare the beginning of the current name case-insensitively and preserve it unchanged whenever it already starts with `plus`, whether or not a space follows the prefix. Otherwise prepend exactly `plus `.
6. Update the remote account in one PATCH-compatible call with the new name, `group_id=6`, and `group_ids=[6]`, while preserving its current status and schedulable values.
7. Count an account as promoted only after the remote update succeeds. Refresh or upsert the account cache from the returned remote snapshot.
8. Store the latest per-account result and the run summary. Continue after individual test or update failures.

If model testing qualifies an account but the remote update fails, record `promotion_failed`; do not report it as promoted. The next scheduled run can retry it because it remains in source group `4`.

## Persistence

Use three MongoDB collections with focused documents:

- `plus_self_produced_settings`: singleton workflow setting containing enabled state, interval seconds, last start/finish timestamps, last run ID, and update metadata. Missing settings resolve to enabled with a 900-second interval.
- `plus_self_produced_runs`: one summary per execution containing trigger, start/finish timestamps, status, candidate/tested/passed/promoted/failed counts, and a run-level error when setup fails.
- `plus_self_produced_account_results`: latest result per `site_id + remote_account_id`, including account name/email, run ID, test classification, raw error excerpt, model, latency, promotion status, previous/new names, groups, and timestamps.

Indexes support newest-first run/result reads and unique latest-result identity. Result documents contain short excerpts rather than credentials or full remote account payloads.

## API

Add an authenticated router under `/api/plus-self-produced`:

- `GET /status`: return fixed workflow facts, effective settings, current running state, last run summary, and aggregate counts.
- `GET /results`: return paginated latest account results, newest first, with an optional result-status filter.
- `PATCH /settings`: allow owner/admin/maintainer roles to change `enabled` and `interval_minutes`; accept 1 to 1440 minutes.
- `POST /run`: allow owner/admin/maintainer roles to trigger an immediate run. Return a conflict-style response when another run is active.

The API never accepts arbitrary site IDs, group IDs, model IDs, or remote update payloads in this version.

## Frontend

Add a `plus自产` navigation entry immediately below `API 账号池状态`, with path `/plus-self-produced`. The page follows the existing quiet operational visual language rather than introducing a new dashboard theme.

The page contains:

- A compact header with workflow state and an `立即探测` command.
- An inline settings band with an enabled toggle and numeric interval control, initially showing 15 minutes.
- Read-only workflow facts for `US06-5002`, group `4 -> 6`, and `gpt-5.4`.
- Last-run metrics for candidates, tested, eligible, promoted, and failed.
- A result table showing account, classification, promotion status, error excerpt, and last test time.
- Loading, empty, running, save-failure, and run-failure states.

The page refreshes its status and results after settings changes and manual runs. It may use the existing page auto-refresh convention for passive updates, but scheduler correctness never depends on the browser being open.

## Error Handling and Safety

- Test requests and account updates remain serial to reduce load and avoid generating avoidable rate limits.
- One account failure does not abort the rest of the run.
- A missing site or missing group fails the run before any account mutation.
- Account names are changed idempotently and never gain repeated prefixes. Existing forms such as `plus account@example.com` and `plusaccount@example.com` are both preserved and moved directly.
- Only accounts currently returned from source group `4` are eligible.
- Destination changes always replace group membership with `[6]`; the workflow does not leave the account in both groups.
- No credential fields, tokens, or full SSE event bodies are persisted or returned by the new API.
- Scheduler shutdown follows the existing FastAPI lifespan cancellation pattern.

## Tests

Backend tests cover:

- Classification of `success=true`, HTTP 429, the ChatGPT-account unsupported-model error, and unrelated errors.
- Unsupported-model classification precedence.
- Idempotent name construction for names with no prefix, `plus ` with a space, and `plus` without a space.
- Serial processing and continuation after one account failure.
- Exact remote update payload and no mutation for failed tests.
- Promotion-update failure remaining retryable.
- Default 15-minute setting, persisted interval changes, due-time behavior, and overlap prevention.
- Router authorization and response contracts.

Frontend tests cover:

- Navigation label/path and page rendering.
- Default/effective interval display and settings update request.
- Manual run request and running/disabled control states.
- Rendering successful, 429-eligible, unsupported-model, and failed result rows.

Final verification runs the focused backend and frontend tests, the full existing test suites where practical, the frontend production build, and a desktop/mobile browser check for overflow and control layout.
