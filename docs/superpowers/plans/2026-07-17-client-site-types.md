# Client Site Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add newapi client configuration while preserving and isolating existing sub2api operations.

**Architecture:** Extend the existing site collection with a backward-compatible discriminator and newapi admin identity. Filter every sub2api worker and operational selector by type, then simplify the existing management page into a type-aware site configuration surface.

**Tech Stack:** FastAPI, Motor/MongoDB, Python unittest, React 19, TypeScript, Vite.

---

### Task 1: Site Data Contract

**Files:**
- Modify: `backend/app/modules/sub2api/cache.py`
- Modify: `backend/app/routers/sub2api_sites.py`
- Test: `backend/tests/test_sub2api_sites.py`

- [ ] Add failing tests for legacy defaults, newapi validation, persistence, update, and type filtering.
- [ ] Run `backend/.venv/Scripts/python.exe -m unittest tests.test_sub2api_sites` and confirm failures describe missing type behavior.
- [ ] Add normalized `site_type`, required newapi `admin_user_id`, filtered listing, and clear validation errors.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Protocol Isolation

**Files:**
- Modify: `backend/app/modules/sub2api/cache.py`
- Modify: `backend/app/modules/sub2api/tpm_sampler.py`
- Modify: `backend/app/modules/sub2api/dashboard.py`
- Modify: `backend/app/routers/sub2api_sites.py`
- Test: `backend/tests/test_sub2api_sites.py`
- Test: `backend/tests/test_tpm_sampler.py`

- [ ] Add failing tests proving background work selects only sub2api sites.
- [ ] Filter startup refresh, scheduler, dashboard and minute sampling while treating missing legacy type as sub2api.
- [ ] Reject sub2api client operations for newapi site records.
- [ ] Run the focused backend tests.

### Task 3: Site Configuration UI

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/pages/AccountPoolsPage.tsx`
- Modify: `frontend/styles.css`
- Modify: other frontend site selectors that operate on sub2api only

- [ ] Rename the navigation label, page heading and canonical path to site configuration while retaining the old path alias.
- [ ] Add a site-type segmented selector and conditional newapi Admin User ID field.
- [ ] Hide sub2api-only controls for newapi and filter operational pages to sub2api sites.
- [ ] Remove target-group, reserve summary and local-pool history UI and its unused requests/state.
- [ ] Run `npm run build` from `frontend`.

### Task 4: Verification

**Files:**
- Verify all changed files.

- [ ] Run the full backend unittest discovery command.
- [ ] Run the frontend production build.
- [ ] Run `git diff --check` and inspect the final changed-file list.

