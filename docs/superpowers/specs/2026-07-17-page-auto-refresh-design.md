# Page-level Auto Refresh Design

## Goal

Keep server-backed data on every active application page current without resetting user context or interrupting account operations.

## Refresh Policy

- Refresh the currently mounted page every 60 seconds.
- Pause scheduled refreshes while the browser document is hidden.
- Refresh once when the document becomes visible after a refresh interval has elapsed.
- Skip a tick while the previous automatic refresh is still running.
- Keep the current page, scroll position, filters, pagination, tabs, and expanded rows.
- Do not show loading overlays or success toasts for automatic refreshes.
- Keep existing data when an automatic refresh fails. Authentication expiry continues to use the existing global handling.

## Page Integration

A shared React hook owns timer, visibility, and in-flight behavior. Each server-backed page registers a callback that reloads the data relevant to its current tab, filters, and pagination.

Pages with editable forms refresh read-only collections and status data only. They do not replace form state while a user is editing. Pages with open destructive confirmation dialogs or active account workflows may pause their callback until the operation closes.

Login and account-upload forms have no server collection to poll and therefore do not issue automatic requests.

## Existing Status Refresh

The API pool status page will use the shared 60-second policy for cached frontend data. This is separate from the explicit remote Sub2API synchronization action: automatic frontend refresh reads current backend cache and does not force a remote cache rebuild every minute.

## Verification

- Unit-test timer, visibility recovery, overlap prevention, and cleanup behavior where the frontend test setup permits it.
- Type-check and build the production frontend.
- Verify that page callbacks preserve filters, pagination, and form state by reviewing each integration call.
