# AIWeLink Risk Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 60-second AIWeLink risk detector that combines suspicious email-local-part rules with seven-day shared-IP evidence, automatically disables high-confidence accounts, supports manual review and release, and excludes confirmed bans from operations analytics.

**Architecture:** A new `risk` module owns pure decisions, Sub2API source access, Growth persistence, orchestration, and scheduling. Growth PostgreSQL stores compressed account-IP evidence and immutable action history; Sub2API remains the source of user, audit, usage, and API-key state. A dedicated operations risk router and panel expose current state without changing AIGCLink behavior.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy asyncio, PostgreSQL, Pydantic, React 19, TypeScript, Vitest, CSS.

---

### Task 1: Growth risk schema

**Files:**
- Modify: `backend/app/modules/growth/migrations.py`
- Modify: `backend/tests/test_growth_migrations.py`

- [x] **Step 1: Write the failing migration tests**

Add assertions for migration `0007_aiwelink_risk_control`, the five risk tables, the two snapshot columns, paused AIWeLink defaults, JSONB checks, unique keys, and IP/account lookup indexes.

```python
self.assertIn("risk_accounts", RISK_DOMAIN_TABLES)
self.assertEqual(MIGRATIONS[-1].version, "0007_aiwelink_risk_control")
sql = "\n".join(MIGRATIONS[-1].statements)
self.assertIn("detector_enabled BOOLEAN NOT NULL DEFAULT FALSE", sql)
self.assertIn("is_risk_excluded BOOLEAN NOT NULL DEFAULT FALSE", sql)
self.assertIn("UNIQUE (site_id, external_user_id, ip_address, source_type)", sql)
```

- [x] **Step 2: Verify RED**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_growth_migrations.py -q`

Expected: FAIL because the risk migration and tables do not exist.

- [x] **Step 3: Implement the migration**

Create `RISK_DOMAIN_TABLES`, add it to `REQUIRED_DOMAIN_TABLES`, and append one idempotent migration containing `growth.risk_settings`, `growth.risk_sync_cursors`, `growth.risk_accounts`, `growth.risk_ip_accounts`, `growth.risk_actions`, `growth.risk_events`, snapshot risk fields, foreign keys, checks, and indexes. Seed AIWeLink with `detector_enabled = FALSE`, `auto_ban_enabled = FALSE`, `poll_interval_seconds = 60`, `ip_window_days = 7`, and `shared_ip_min_accounts = 3`.

- [x] **Step 4: Verify GREEN**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_growth_migrations.py -q`

Expected: PASS.

### Task 2: Pure email, IP, health, and decision rules

**Files:**
- Create: `backend/app/modules/risk/__init__.py`
- Create: `backend/app/modules/risk/domain.py`
- Create: `backend/tests/test_risk_domain.py`

- [x] **Step 1: Write failing domain tests**

Cover every domain, dotted local parts, non-empty plus tags, invalid addresses, canonical IPv4/IPv6, seven-day boundary behavior, two versus three distinct accounts, cross-source deduplication, source health thresholds, manual overrides, and the complete decision matrix.

```python
self.assertEqual(match_email_rules("e.l.lame@gmail.com"), ("email_local_part_dot",))
self.assertEqual(match_email_rules("person+batch@outlook.com"), ("email_plus_tag",))
self.assertEqual(decide_risk(email_rules=("email_plus_tag",), shared_ips=(evidence,), manual_override=False), "ban")
self.assertEqual(decide_risk(email_rules=(), shared_ips=(evidence,), manual_override=False), "high_risk")
```

- [x] **Step 2: Verify RED**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_risk_domain.py -q`

Expected: FAIL with `ModuleNotFoundError`.

- [x] **Step 3: Implement immutable domain values and functions**

Use `email.partition("@")`, `ipaddress.ip_address`, timezone-aware datetimes, distinct external-user IDs, and explicit `RiskDecision` values. Do not perform database access in this module.

- [x] **Step 4: Verify GREEN**

Run the Task 2 command and expect PASS.

### Task 3: Sub2API source adapter and reversible mutations

**Files:**
- Create: `backend/app/modules/risk/adapters/__init__.py`
- Create: `backend/app/modules/risk/adapters/sub2api.py`
- Create: `backend/tests/test_risk_sub2api_adapter.py`

- [x] **Step 1: Write failing adapter tests**

Verify bounded ID-cursor queries for `audit_logs` and `usage_logs`, seven-day first-read timestamps, audit actor/email/request-body resolution, latest-source timestamps, pre-ban state capture, user/API-key locking, active-key-only disablement, stale-state conflicts, and release that restores only unchanged rows.

```python
self.assertIn("WHERE id > :after_id", statement)
self.assertIn("LIMIT :limit", statement)
self.assertIn("FOR UPDATE", mutation_sql)
self.assertIn("status = 'active'", mutation_sql)
self.assertNotIn("request_body", persisted_observation)
```

- [x] **Step 2: Verify RED**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_risk_sub2api_adapter.py -q`

Expected: FAIL because the adapter is absent.

- [x] **Step 3: Implement source reads and mutations**

Return normalized observation records from audit and usage pages. Open source writes with a dedicated PostgreSQL engine and `engine.begin()`. For bans compare captured `status` and `updated_at`, set the user to `disabled`, and set only active keys to `inactive`. For release compare current values with the action snapshot and return per-row restored/conflicted results.

- [x] **Step 4: Verify GREEN**

Run the Task 3 command and expect PASS.

### Task 4: Growth risk repository and operations exclusion

**Files:**
- Create: `backend/app/modules/risk/repository.py`
- Create: `backend/tests/test_risk_repository.py`
- Modify: `backend/app/modules/operations/repository.py`
- Modify: `backend/tests/test_operations_repository.py`

- [x] **Step 1: Write failing repository tests**

Test settings and cursor reads, idempotent observation upserts, shared-IP grouping, risk-account upsert, deterministic action insertion, action terminal transitions, append-only events, 30-day cleanup, paged filters, detail lookup, stats exclusion, traffic exclusion, and aggregate SQL excluding risk snapshots.

```python
self.assertIn("ON CONFLICT (site_id, external_user_id, ip_address, source_type)", sql)
self.assertIn("COUNT(DISTINCT external_user_id) >= :minimum_accounts", sql)
self.assertIn("NOT snapshot.is_risk_excluded", aggregate_sql)
self.assertIn("source = 'rule'", exclusion_sql)
```

- [x] **Step 2: Verify RED**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_risk_repository.py backend/tests/test_operations_repository.py -q`

Expected: FAIL because risk persistence and analytics filtering are missing.

- [x] **Step 3: Implement repository functions and analytics filters**

Use bound parameters for every query. Keep one current account row, compressed source-specific account-IP rows, mutable action rows, immutable events, and a deterministic action key. Make `_segment_filter` require `NOT is_risk_excluded`; add the same condition to raw aggregate event branches. Setting `banned` updates both snapshot exclusion fields and `growth.user_exclusions`; release reverses only the risk-owned exclusion.

- [x] **Step 4: Verify GREEN**

Run the Task 4 command and expect PASS.

### Task 5: Risk service and 60-second scheduler

**Files:**
- Create: `backend/app/modules/risk/service.py`
- Create: `backend/app/modules/risk/scheduler.py`
- Create: `backend/tests/test_risk_service.py`
- Create: `backend/tests/test_risk_scheduler.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_account_test_bootstrap.py`

- [x] **Step 1: Write failing service and scheduler tests**

Cover paused defaults, advisory single-flight, first seven-day backfill, independent cursors, bounded pagination, 60-second sleep, audit and usage union, high-risk versus auto-ban outcomes, pending-action recovery, ban failures, release partial conflicts, manual false-positive exceptions, stale usage health, and task startup/cancellation.

Also verify that any account with historical verified payment remains `high_risk` and never enters automatic enforcement, using both completed Sub2API cash orders and classified Growth cash-sale facts.

```python
self.assertEqual(RISK_POLL_INTERVAL_SECONDS, 60)
self.assertEqual(result["decision"], "ban")
self.assertEqual(result["source_health"]["usage_logs"]["status"], "stale")
```

- [x] **Step 2: Verify RED**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_risk_service.py backend/tests/test_risk_scheduler.py backend/tests/test_account_test_bootstrap.py -q`

Expected: FAIL because orchestration and lifecycle wiring are absent.

- [x] **Step 3: Implement the orchestration**

Each cycle initializes the shared schema, reads AIWeLink settings, takes a Growth advisory lock, fetches pages from both streams, writes compressed observations and cursors, recomputes affected accounts, records state transitions, executes or recovers actions, expires old evidence, and updates health errors without advancing failed cursors. Add one named lifespan task and cancel it with the existing task group.

- [x] **Step 4: Verify GREEN**

Run the Task 5 command and expect PASS.

### Task 6: Operations risk API

**Files:**
- Create: `backend/app/modules/risk/schemas.py`
- Create: `backend/app/routers/risk.py`
- Create: `backend/tests/test_risk_routes.py`
- Modify: `backend/app/main.py`

- [x] **Step 1: Write failing route tests**

Test overview, account pagination and filters, account detail, shared-IP clusters, events, source health, settings, manual ban, release, false-positive override, override removal, AIWeLink site scoping, owner/admin writes, operator reads, page limits, and required action notes.

```python
response = client.post("/api/operations/risk/accounts/risk-1/release", json={"reason": "误报核验"})
self.assertEqual(response.status_code, 200)
self.assertEqual(forbidden.status_code, 403)
```

- [x] **Step 2: Verify RED**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_risk_routes.py -q`

Expected: FAIL because routes are not registered.

- [x] **Step 3: Implement schemas and routes**

Expose `/api/operations/risk/overview`, `/accounts`, `/accounts/{id}`, `/ip-clusters`, `/events`, `/settings`, and account action endpoints. Reuse `operations-management` permission, require AIWeLink access, require non-blank notes for account actions, and write management audit logs for every mutation.

- [x] **Step 4: Verify GREEN**

Run the Task 6 command and expect PASS.

### Task 7: Operations risk workspace

**Files:**
- Create: `frontend/src/pages/operations/OperationsRiskPanel.tsx`
- Create: `frontend/src/pages/operations/OperationsRiskPanel.test.tsx`
- Create: `frontend/src/pages/operations/OperationsRiskPanel.css`
- Modify: `frontend/src/pages/OperationsManagementPage.tsx`
- Modify: `frontend/src/pages/OperationsManagementPage.test.tsx`

- [x] **Step 1: Write failing component tests**

Test the new risk tab, summaries, filters, state and formula tooltips, stale-source warning, empty/error/loading states, account and IP tables, detail drawer timeline, mandatory notes, owner/admin controls, read-only operator view, ban/release/false-positive flows, and emergency pause switches.

```tsx
expect(html).toContain("风控")
expect(html).toContain("7 天内同一 IP 至少关联 3 个账号")
expect(html).toContain("调用日志已过期")
expect(html).not.toContain("AIGCLink 风控")
```

- [x] **Step 2: Verify RED**

Run: `npm test -- --run src/pages/operations/OperationsRiskPanel.test.tsx src/pages/OperationsManagementPage.test.tsx`

Expected: FAIL because the tab and panel do not exist.

- [x] **Step 3: Implement the panel**

Use a full-width, query-first layout consistent with the redesigned operations page. Use Lucide icons for commands, native title tooltips plus visible source status labels, one detail drawer, and confirmation dialogs with a required note. Fetch only while the risk tab is active and refresh every 60 seconds.

- [x] **Step 4: Verify GREEN**

Run the Task 7 command and expect PASS.

### Task 8: Full verification and publication

**Files:**
- Modify only files required by verification findings.

- [x] **Step 1: Run focused risk tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_risk_domain.py backend/tests/test_risk_sub2api_adapter.py backend/tests/test_risk_repository.py backend/tests/test_risk_service.py backend/tests/test_risk_scheduler.py backend/tests/test_risk_routes.py -q`

Expected: PASS.

- [x] **Step 2: Run full backend and frontend suites**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests -q`

Run: `npm test -- --run`

Expected: PASS with zero failures.

- [x] **Step 3: Build and visually inspect**

Run: `npm run build`.

Start the local app, open the operations risk tab in the in-app browser, and inspect desktop and mobile widths for overlap, clipped text, action visibility, stale health messaging, drawer behavior, and empty states. Do not enable the detector against production data.

- [x] **Step 4: Review migration and source-write safety**

Confirm migration defaults are paused, no secrets or request bodies are persisted, source queries are bounded, auto-ban needs both signals, normal shared-IP users are not auto-banned, and release uses captured state checks.

- [ ] **Step 5: Commit, push, and open the PR**

Commit the tested implementation, push `codex/aiwelink-risk-control`, and open a pull request targeting `achernar/dev` with the rule matrix, paused rollout requirement, usage-log staleness disclosure, and verification results.
