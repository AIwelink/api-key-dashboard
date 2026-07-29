# Growth Analytics Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace misleading legacy Growth metrics with the confirmed homepage traffic, promotion traffic, immutable registration attribution, and data-quality views.

**Architecture:** Keep the existing admin-only `/growth/analytics/overview` and `/growth/analytics/users` endpoints, but replace their DTOs and PostgreSQL queries in place. The backend remains the only aggregation layer over the Traffic Analysis `0004_relax_link_visit_optional_metadata` schema; the React page renders only backend-computed metrics and explicit capability states.

**Tech Stack:** FastAPI, SQLAlchemy text queries, asyncpg/PostgreSQL, React 19, TypeScript, Vitest, Python unittest.

---

### Task 1: Lock the analytics contract with backend tests

**Files:**
- Modify: `backend/tests/test_growth_analytics.py`
- Modify: `backend/tests/test_growth_routes.py`
- Modify: `backend/app/modules/growth/analytics_schemas.py`

- [ ] Replace milestone-query tests with registration-attribution pagination tests.
- [ ] Add failing repository assertions for recorded/effective/excluded homepage traffic and nullable valid rate.
- [ ] Add failing SQL assertions for last-counted-event exclusive Session UV distribution.
- [ ] Add failing assertions that `active_source_kind` and `classified_source_kind` are queried separately.
- [ ] Add failing assertions for link exclusions, attribution updates, registration fact states, and data-quality summaries.
- [ ] Run `python -m unittest backend.tests.test_growth_analytics backend.tests.test_growth_routes -v` from `backend` and confirm failures are caused by the old contract.
- [ ] Simplify schemas so the users endpoint no longer accepts downstream milestones.

### Task 2: Implement confirmed PostgreSQL aggregations

**Files:**
- Modify: `backend/app/modules/growth/analytics_repository.py`
- Test: `backend/tests/test_growth_analytics.py`

- [ ] Replace the legacy cohort CTE with an immutable registration-attribution CTE that distinguishes `normal`, `excluded`, and `facts_pending`.
- [ ] Query homepage audit totals, valid PV, valid Session UV, excluded visits, latest event time, and bucket timezone.
- [ ] Query link audit totals, valid PV/UV, exclusions, and `is_attribution_update` counts.
- [ ] Build traffic trends from valid homepage/link events only.
- [ ] Build mutually exclusive active-source UV with `ROW_NUMBER()` over each anonymous visitor's last valid homepage event.
- [ ] Build a separate classified-source diagnostic using valid event PV/UV.
- [ ] Return promotion-link status, validity window, valid traffic, exclusions, attribution updates, and non-excluded attributed registrations.
- [ ] Add data-quality queries for exclusion reasons, bot counts, redirect results, HTTP statuses, fact freshness, and pending fact rows.
- [ ] Return paginated registration attribution rows without downstream call/payment fields or sensitive evidence fields.
- [ ] Run the focused backend tests until green.

### Task 3: Assemble stable API DTOs and capability states

**Files:**
- Modify: `backend/app/modules/growth/analytics_service.py`
- Modify: `backend/app/routers/growth.py`
- Test: `backend/tests/test_growth_analytics.py`
- Test: `backend/tests/test_growth_routes.py`

- [ ] Return `capabilities`, `homepage_summary`, `link_summary`, `registration_summary`, `traffic_trends`, both source breakdowns, `link_performance`, and `quality`.
- [ ] Compute only homepage valid rate; return `null` when recorded visits are zero.
- [ ] Mark downstream facts `unavailable` and remove registration/call/payment rates and amounts.
- [ ] Keep generated time, explicit query window, read-only transaction, timeout, site filters, pagination, and identifier masking.
- [ ] Update the users route/query to registration attribution only.
- [ ] Run backend analytics and route tests until green.

### Task 4: Replace the dashboard with the confirmed information hierarchy

**Files:**
- Modify: `frontend/src/pages/trafficAnalysis/TrafficOverview.test.tsx`
- Modify: `frontend/src/pages/trafficAnalysis/TrafficOverview.tsx`

- [ ] Write failing component tests for effective homepage metrics, nullable rates, separate source views, promotion performance, fact states, quality diagnostics, and visible `未接入` downstream capabilities.
- [ ] Remove milestone switching and request only paginated registration attribution rows.
- [ ] Update TypeScript response types to match the new backend DTO.
- [ ] Render compact filters; two primary homepage metrics with smaller quality context; traffic trend; active-source distribution; classified-source diagnostics; link performance; immutable registration attribution; and collapsible/secondary data quality.
- [ ] Ensure no registration-rate calculation or downstream zero is produced in the browser.
- [ ] Keep all wide tables keyboard-focusable and account identifiers masked.
- [ ] Run `npm test -- TrafficOverview.test.tsx` until green.

### Task 5: Refine the operational dashboard styling

**Files:**
- Modify: `frontend/styles.css`
- Test: `frontend/src/pages/trafficAnalysis/TrafficOverview.test.tsx`

- [ ] Replace legacy funnel styles with a restrained, dense operational layout consistent with the existing management panel.
- [ ] Give primary metrics clear hierarchy without card nesting or oversized display type.
- [ ] Add stable responsive grids and table widths for desktop and mobile.
- [ ] Preserve focus states, readable status badges, loading, empty, delayed, and error states.
- [ ] Run the frontend tests and production build.

### Task 6: Verify end to end

**Files:**
- Verify: `backend/app/modules/growth/analytics_*.py`
- Verify: `frontend/src/pages/trafficAnalysis/TrafficOverview.tsx`
- Verify: `frontend/styles.css`

- [ ] Run focused backend analytics/routes tests.
- [ ] Run the full backend test suite.
- [ ] Run the full frontend test suite and production build.
- [ ] Run `git diff --check`.
- [ ] Start the local frontend server and inspect desktop/mobile screenshots for overflow, blank regions, and status clarity.
- [ ] Review the final diff to confirm no raw session keys, evidence hashes, credentials, or raw email identifiers are exposed.
