# Plus Routing Groups Configuration Design

## Goal

Make every group destination used by the US06-5002 `plus自产` automation selectable without changing the existing account classification rules. The four configurable roles are:

- self-produced source group, default `4`
- Plus normal group, default `6`
- banned account group, default `7`
- Plus error group, default `9`

The four roles must always map one-to-one to four distinct, existing PostgreSQL groups.

## Data Ownership

US06-5002 PostgreSQL remains the source of truth for accounts and groups. The backend reads the current groups directly from PostgreSQL for the settings options and reads the pool snapshot directly from PostgreSQL for each probe run.

MongoDB stores only the automation settings, run records, and account result records. The settings document keeps the four selected group IDs together with the existing enabled flag and interval. Missing group fields in an older settings document fall back to `4`, `6`, `7`, and `9` so the current workflow continues without migration downtime.

The site ID and probe model remain fixed as `US06-5002` and `gpt-5.6-sol`.

## Backend Contract

Add `GET /plus-self-produced/groups`. It loads the US06-5002 SQL DSN from the existing site record and queries the PostgreSQL groups table. The response contains only the fields needed by the settings UI, including each group's integer ID, name, and status. It does not use the MongoDB group cache.

Extend `PATCH /plus-self-produced/settings` with:

- `source_group_id`
- `plus_group_id`
- `banned_group_id`
- `plus_error_group_id`

Each supplied ID must be a positive integer. The effective four IDs, including unchanged stored values and defaults, must be distinct. All four effective IDs must exist in the current US06-5002 PostgreSQL group list before the settings document is updated. A validation failure returns a client error and leaves the stored settings unchanged.

`GET /plus-self-produced/status` returns the effective group IDs from settings at both the existing top-level workflow fields and inside `settings`. This preserves the current page contract while making the displayed routes dynamic.

## Probe Data Flow

At the start of every scheduled or manual probe, the service reads the effective settings once and uses that immutable snapshot for the entire run. It then reads the accounts and groups from PostgreSQL.

Before testing any account, it verifies that all four configured groups still exist and are distinct. If validation fails, the run records an error and stops before any remote account update.

The configured roles replace every fixed group constant in candidate selection and routing:

- Source success or 429: move to the configured Plus normal group, add the `plus ` prefix when absent, and set `plan_type=plus`.
- Source 401: move to the configured banned group without renaming.
- Plus normal success or 429: keep the account in place.
- Plus normal unsupported-model 400: move to the configured source group, remove one leading `plus` prefix, and set `plan_type=free`.
- Plus normal 401: move to the configured Plus error group without renaming.
- Other failures: keep the account in place.

Run and account result records retain the actual source and destination group IDs used by that run, so changing settings later does not rewrite history.

## Frontend

The page loads status, result history, and PostgreSQL group options. The settings band adds four labeled select controls for the four roles. Each option shows the group ID and name.

The form initializes from effective backend settings. Saving sends the enabled flag, interval, and all four group IDs in one request. While loading, saving, or running a probe, the selects remain disabled alongside the existing controls.

The client detects duplicate selections immediately, explains that the four roles must be one-to-one, and disables Save. The backend remains authoritative and repeats both distinctness and existence validation.

The workflow facts use the unsaved form selection so an operator can inspect all four proposed routes before saving:

- source to Plus normal
- source to banned
- Plus normal to source
- Plus normal to Plus error

If group options cannot be loaded, the page reports the existing API error and does not silently substitute cached options.

## Error Handling

PostgreSQL connection errors are sanitized through the existing SQL error redaction path. Missing site or SQL DSN errors use the existing US06-5002 configuration messages. No database credentials or Admin API Key values are returned by the new endpoint.

Settings validation distinguishes duplicate role selection from missing PostgreSQL group IDs. Probe-time validation is required even after save-time validation because groups may be deleted or changed externally.

The groups endpoint and settings save are read-only with respect to Sub2API accounts. They never trigger a model probe or account move.

## Testing

Backend tests cover defaults, persisted custom IDs, partial updates, positive ID schema validation, duplicate-role rejection, missing-group rejection, PostgreSQL-backed group options, dynamic status fields, dynamic candidate selection, and all five routing outcomes using custom IDs.

Frontend tests cover rendering the four selects, ID-and-name options, initialization from settings, dynamic workflow facts, duplicate-selection blocking, and the complete settings request payload.

Verification includes the focused backend and frontend tests, the full backend suite, the full frontend suite, the production frontend build, and `git diff --check`. No live probe or live account mutation is part of verification.

## Out Of Scope

The site ID, probe model, classification rules, probe interval limits, and result retention are not made configurable. This change does not introduce a generic route-rule editor or modify the PostgreSQL group records.
