# Classification Task Occurrence Ordering Design

## Goal

Make the operations management classification table show the newest business
events first. The visible `occurred_at` value is the primary ordering key.

## Current Behavior and Root Cause

`list_classification_tasks()` currently orders rows by
`classification_tasks.created_at DESC`. A historical credit event can receive a
new classification task during a backfill, so an old event may appear above a
newer event even though the table labels the visible timestamp as the event
occurrence time.

The service and frontend preserve repository order, so the mismatch originates
in the repository query.

## Design

Change the repository query to use this deterministic order:

1. `credit_events.occurred_at DESC`
2. `classification_tasks.created_at DESC`
3. `classification_tasks.classification_task_id DESC`

The event occurrence timestamp expresses the business meaning shown in the
table. Task creation time handles events with the same occurrence timestamp,
and the task identifier supplies a final stable tie-breaker.

The rule applies to every status requested through the existing `status`
filter. The API schema, service behavior, frontend rendering, and database
schema remain unchanged.

## Alternatives Rejected

- Frontend sorting would only repair this page and could diverge from server
  pagination or another API consumer.
- Keeping task creation time as the primary key describes queue ingestion, not
  the visible business occurrence time requested by operations users.

## Error Handling

No new error path is introduced. Existing database and API error handling
continues unchanged.

## Testing and Acceptance

Add a repository regression test that captures the generated SQL and verifies
the three ordering keys in their required order. Run the focused test first and
observe it fail against the existing query, then update the query and verify the
focused and complete backend test suites.

Acceptance criterion: for classification records returned by the endpoint, a
record with a later `occurred_at` appears before one with an earlier
`occurred_at`; ties are deterministic by task creation time and task ID.
