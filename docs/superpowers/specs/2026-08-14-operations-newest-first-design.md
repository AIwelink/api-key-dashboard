# Operations Time Tables Newest First Design

## Goal

Show the newest records at the top of the Operations Trend and Retention Cohort tables so operators can inspect current data without scrolling.

## Scope

- Sort Operations Trend rows by `bucket` descending.
- Sort Retention Cohort rows by `cohort_date` descending.
- Keep the existing API contracts, filters, table columns, and all non-time-based rankings unchanged.

## Design

Add a small pure frontend helper that copies an input array and sorts it by a selected timestamp in descending chronological order. Apply it after the existing allowed-site filters when deriving the visible trend and retention rows.

The helper must not mutate API response arrays. JavaScript's stable sort preserves the existing API order for rows with equal timestamps, including multiple sites in the same trend bucket.

## Error Handling

The API supplies ISO timestamps for trend buckets and `YYYY-MM-DD` dates for cohorts. If a value cannot be parsed, the comparator treats it as equal and preserves its relative input order instead of dropping the row.

## Verification

Add a focused unit test with deliberately unordered timestamps. Verify that the returned array is newest-first, equal timestamps remain stable, and the original array is unchanged. Then run the focused page test, the full frontend suite, the production build, and `git diff --check`.
