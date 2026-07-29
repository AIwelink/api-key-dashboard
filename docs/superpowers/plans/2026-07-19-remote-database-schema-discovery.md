# Remote Database Schema Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely inspect configured NewAPI MySQL and Sub2API PostgreSQL schemas without reading business rows, then document the real tables, columns, keys, and indexes for the database-read refactor.

**Architecture:** A reusable system module executes fixed read-only catalog queries through short-lived SQLAlchemy async engines and normalizes both database products into one schema model. A CLI loads configured sites from MongoDB, isolates per-site failures, prints sanitized JSON, and a generated Markdown report records the findings.

**Tech Stack:** Python 3.12, SQLAlchemy asyncio, aiomysql, asyncpg, Motor/MongoDB, unittest.

---

### Task 1: Normalize catalog rows

**Files:**
- Create: `backend/app/modules/system/database_schema.py`
- Create: `backend/tests/test_database_schema.py`

- [ ] Write failing tests for MySQL columns/indexes/constraints and PostgreSQL multi-schema columns/indexes/constraints.
- [ ] Run `python -m unittest tests.test_database_schema -v` and verify imports fail.
- [ ] Implement pure normalization functions that group catalog rows without retaining defaults, comments, or business values.
- [ ] Re-run the targeted tests and verify they pass.

### Task 2: Execute safe schema scans

**Files:**
- Modify: `backend/app/modules/system/database_schema.py`
- Modify: `backend/tests/test_database_schema.py`

- [ ] Write failing async tests asserting only fixed catalog SQL is executed, the configured database is parameterized, Engine disposal always runs, and errors are redacted.
- [ ] Implement MySQL `information_schema` and PostgreSQL `information_schema`/`pg_catalog` scanners using `NullPool` and the existing parsed SQL_DSN.
- [ ] Add site enumeration for `sub2api_sites` and `client_sites`, with per-site failure isolation and sanitized endpoints.
- [ ] Run all database schema tests and the full backend test suite.

### Task 3: Add and run the inspection command

**Files:**
- Create: `backend/scripts/inspect_remote_database_schemas.py`

- [ ] Implement an async CLI that connects to MongoDB, calls the scanner, prints UTF-8 JSON, and always closes MongoDB.
- [ ] Run `python scripts/inspect_remote_database_schemas.py` with network access.
- [ ] Confirm every configured site is either scanned or has a redacted error and that no SQL_DSN/password appears in output.

### Task 4: Document real schemas

**Files:**
- Create: `docs/database/remote-schema-scan-2026-07-19.md`

- [ ] Convert sanitized scan output into per-product and per-site tables.
- [ ] Identify candidate Sub2API accounts/groups/usage tables and NewAPI users/logs/models/channels tables based only on discovered names and columns.
- [ ] Record cross-site schema compatibility and explicit unknowns.
- [ ] Run `git diff --check`, compile the backend, and commit implementation plus report.
