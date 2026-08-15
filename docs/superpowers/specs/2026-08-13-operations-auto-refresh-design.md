# Operations Overview Auto Refresh Design

## Goal

Keep the open operations overview current as scheduled source synchronization adds trend,
retention cohort, lifecycle, and sync-status data.

## Design

Reuse the shared `usePageAutoRefresh` hook with its existing 60-second interval and
visibility-aware behavior. The operations page refreshes the existing `loadOverview(true)`
request group, preserving the current parallel API calls and avoiding a loading-state flash.

Auto refresh is enabled only when the overview tab is active, the user has site access, and
the query is valid. It pauses during manual source refreshes, saves, open modals, one-time
redemption results, redemption reveals, and internal-user deletion confirmation so background
responses cannot interrupt an active command workflow.

Each overview load captures both a monotonically increasing request ID and the exact query
string. Results, background errors, and foreground loading completion are applied only when the
request is still current, so a slow response for an earlier site or date range cannot overwrite
the latest selection.

## Error Handling

`loadOverview(true)` already preserves the visible data and reports a background failure via
the existing toast. The shared scheduler prevents overlapping interval requests and skips work
while the document is hidden.

## Verification

Add a unit-tested pure predicate for the pause/enable rules, run the operations page tests and
full frontend suite, build the production bundle, and verify that the component-provided refresh
callback requests summary, trends, lifecycle, and sync status. The shared scheduler tests cover
the 60-second interval, visibility return refresh, error containment, and overlap prevention. A
focused regression test covers rejection of superseded request IDs and obsolete query strings.
