# Automated CI Design

## Goal

Add GitHub Actions quality checks for the API key management dashboard without
deploying or changing production state.

## Trigger and permissions

- Run on every push to any branch.
- Run on every pull request targeting any branch.
- Cancel an older in-progress run for the same branch or pull request when a
  newer commit arrives.
- Grant the workflow only `contents: read` permission.

## Jobs

### Frontend

Run on Ubuntu with Node.js 22 and a 15-minute timeout. Restore npm's dependency cache, install the
locked dependency graph with `npm ci` from `frontend/package-lock.json`, run
the Vitest suite with `npm test`, and verify the production bundle with
`npm run build`.

### Backend

Run on Ubuntu with Python 3.12 and a 20-minute timeout. Install `uv 0.12.3`, synchronize the
environment from `backend/uv.lock` with `uv sync --locked --python 3.12`, and
run the full backend unittest discovery command with `uv run --python 3.12`.
The explicit interpreter selection overrides the repository's local Python
3.14 development preference and verifies the project's minimum supported
version.

## Failure behavior

Each job is independent and runs in parallel. A failed test, dependency
installation, or build fails the workflow and blocks the corresponding check.
No deployment, secrets, database service, or external integration is required.

## Verification

Validate the workflow YAML structurally, then run the same frontend and backend
commands locally where the workspace toolchain is available.
