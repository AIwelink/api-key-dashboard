# Client Site Database Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add masked native MySQL/PostgreSQL SQL_DSN configuration, retention settings, and a full-driver database connection test without changing existing API connection fields, then expose PostgreSQL SQL_DSN testing for account-pool Sub2API backends.

**Architecture:** Extend the independent `client_sites` persistence model with protocol-validated secrets and public masking. Add a focused SQLAlchemy async connection tester used by one client-site router endpoint, then expose the configuration and latest result in the existing customer-site page.

**Tech Stack:** FastAPI, MongoDB Motor, SQLAlchemy asyncio, aiomysql, asyncpg, React, TypeScript, unittest.

---

### Task 1: DSN Validation And Public Masking

**Files:**
- Modify: `backend/tests/test_client_sites.py`
- Modify: `backend/app/modules/system/client_sites.py`

- [ ] **Step 1: Write failing tests** for native NewAPI MySQL SQL_DSN acceptance, customer Sub2API PostgreSQL keyword SQL_DSN acceptance, format mismatch rejection, SQL_DSN masking, default 90-day retention, and blank-update secret preservation.
- [ ] **Step 2: Run** `backend/.venv/Scripts/python.exe -B -m unittest tests.test_client_sites` and verify failures are caused by missing DSN behavior.
- [ ] **Step 3: Implement** shared native SQL_DSN parsing, safe endpoint extraction, `_retention_days`, persistence fields, and public masking.
- [ ] **Step 4: Re-run** the focused tests and verify all pass.

### Task 2: Full-Driver Connection Test

**Files:**
- Modify: `backend/pyproject.toml`
- Modify: `backend/uv.lock`
- Create: `backend/app/modules/system/client_site_database.py`
- Create: `backend/tests/test_client_site_database.py`

- [ ] **Step 1: Add failing tests** that assert driver URL conversion, `SELECT 1`, version lookup, engine disposal, safe success payload, and credential-redacted failure text.
- [ ] **Step 2: Run** `backend/.venv/Scripts/python.exe -B -m unittest tests.test_client_site_database` and verify the module is missing.
- [ ] **Step 3: Add dependencies** `sqlalchemy`, `aiomysql`, and `asyncpg` through `uv add` so the lockfile is updated mechanically.
- [ ] **Step 4: Implement** SQLAlchemy AsyncEngine creation with `NullPool`, 10-second timeout options, fixed protocol-to-driver mapping, queries, latency measurement, and guaranteed disposal.
- [ ] **Step 5: Re-run** the focused tests and verify all pass.

### Task 3: API Endpoint And Persisted Test State

**Files:**
- Modify: `backend/app/routers/client_sites.py`
- Modify: `backend/tests/test_client_site_database.py`

- [ ] **Step 1: Add failing endpoint/service tests** for missing sites, missing DSN, success persistence, failure persistence, and public response safety.
- [ ] **Step 2: Run** the focused test module and verify the expected failures.
- [ ] **Step 3: Implement** `POST /api/client-sites/{site_id}/database/test`, load the secret with `include_api_key=True`, execute the tester, persist `last_database_*`, and audit only the public result.
- [ ] **Step 4: Re-run** the focused tests and verify all pass.

### Task 4: Client Site Configuration UI

**Files:**
- Modify: `frontend/src/pages/ClientSitesPage.tsx`
- Modify: `frontend/styles.css`

- [ ] **Step 1: Extend frontend types and form state** with `sql_dsn`, configured status, safe endpoint, retention, and latest test fields.
- [ ] **Step 2: Add a separate database connection section** that keeps the API connection controls intact, shows the fixed database type, masks saved DSNs, accepts retention days, and provides a test button.
- [ ] **Step 3: Add test-result rendering** for success/failure, latency, version, and timestamp without exposing credentials.
- [ ] **Step 4: Run** `npm.cmd run build` and fix all TypeScript or layout errors.

### Task 5: Indexes, Documentation, And Full Verification

**Files:**
- Modify: `backend/app/modules/system/bootstrap.py`
- Modify: `docs/design/31-newapi-data-api-integration.md`

- [ ] **Step 1: Add client-site test-state indexes only where query patterns require them; avoid unused indexes.**
- [ ] **Step 2: Update integration documentation** to record dual API/database connections and fixed driver protocols.
- [ ] **Step 3: Run** `backend/.venv/Scripts/python.exe -B -m unittest discover -s tests` and verify zero failures.
- [ ] **Step 4: Run** `npm.cmd run build` and verify production output succeeds.
- [ ] **Step 5: Run** `git diff --check`, inspect `git status --short`, and scan the diff for embedded credentials.
