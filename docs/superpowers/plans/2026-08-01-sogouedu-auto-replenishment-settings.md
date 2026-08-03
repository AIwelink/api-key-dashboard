# SogouEdu Auto-Replenishment Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secure automatic-replenishment configuration page for SogouEdu credentials, minimum account/runway thresholds, and non-billing connection tests.

**Architecture:** A focused backend module owns encrypted configuration persistence and public serialization. A separate SogouEdu client exposes only login, balance, and inventory reads; an authenticated router composes those units and writes sanitized audits. A standalone React page uses the existing navigation, API client, toast, and responsive form styles.

**Tech Stack:** FastAPI, Motor/MongoDB, Pydantic, HTTPX, cryptography/Fernet, Python unittest, React 19, TypeScript, Vitest, Vite.

---

### Task 1: Secret Encryption And Settings Domain

**Files:**
- Create: `backend/app/modules/auto_replenishment/__init__.py`
- Create: `backend/app/modules/auto_replenishment/secrets.py`
- Create: `backend/app/modules/auto_replenishment/settings.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/test_auto_replenishment_settings.py`

- [ ] **Step 1: Write failing settings tests**

Cover defaults, initial password requirement, encrypted-at-rest storage, public secret masking, blank-password preservation, numeric validation, and target site/group validation. The primary assertions are:

```python
self.assertEqual(result["minimum_account_count"], 2)
self.assertEqual(result["minimum_runway_minutes"], 5)
self.assertNotEqual(saved["password_ciphertext"], "supplier-password")
self.assertNotIn("password_ciphertext", result)
self.assertTrue(result["password_configured"])
```

- [ ] **Step 2: Run tests and verify the expected import failure**

Run: `python -m unittest tests.test_auto_replenishment_settings -v`

Expected: FAIL because `app.modules.auto_replenishment.settings` does not exist.

- [ ] **Step 3: Implement authenticated encryption and configuration persistence**

Use Fernet with SHA-256-derived key material from `Settings.app_secret_key`. Store one stable document with fixed provider/base URL/product/account type and validated target site/group. Public serialization removes ciphertext and emits `password_configured`.

```python
DEFAULT_SETTINGS = {
    "provider": "sogouedu",
    "base_url": "https://sogouedu.cc",
    "enabled": False,
    "minimum_account_count": 2,
    "minimum_runway_minutes": 5,
    "product": "oauth_7d",
    "local_account_type": "team",
    "target_site_id": "us06-5001",
    "target_group_name": "plus账号池01",
}
```

- [ ] **Step 4: Run settings tests**

Run: `python -m unittest tests.test_auto_replenishment_settings -v`

Expected: PASS.

### Task 2: Read-Only SogouEdu Provider Test

**Files:**
- Create: `backend/app/modules/auto_replenishment/sogouedu.py`
- Create: `backend/app/modules/auto_replenishment/service.py`
- Test: `backend/tests/test_sogouedu_client.py`

- [ ] **Step 1: Write failing provider tests**

Use `httpx.MockTransport` to assert the exact sequence and headers:

```python
self.assertEqual(requests, [
    ("POST", "/api/customer/login"),
    ("GET", "/api/customer/balance"),
    ("GET", "/api/customer/inventory"),
])
self.assertNotIn("/api/customer/pickup/orders", requested_paths)
self.assertNotIn("token", str(result).lower())
```

Also cover HTTP 401, non-JSON responses, and transport failures with sanitized error messages.

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m unittest tests.test_sogouedu_client -v`

Expected: FAIL because the provider client is missing.

- [ ] **Step 3: Implement the read-only client and test service**

Expose only `login`, `get_balance`, `get_inventory`, and `test_connection`. Do not implement order creation, polling, or take delivery. Keep the 12-hour customer token request-local and return only normalized fen/inventory/remaining-time fields.

- [ ] **Step 4: Run provider tests**

Run: `python -m unittest tests.test_sogouedu_client -v`

Expected: PASS.

### Task 3: API, Audit, Permissions, And Router Registration

**Files:**
- Create: `backend/app/routers/auto_replenishment.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/modules/system/permissions.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Test: `backend/tests/test_auto_replenishment_routes.py`
- Test: `backend/tests/test_role_permissions.py`

- [ ] **Step 1: Write failing route and permission tests**

Test GET defaults, PUT save/audit, POST test/audit, error status mapping, and that public/audit payloads contain no password, ciphertext, or token. Add `auto-replenishment` to owner/admin/maintainer/viewer defaults according to the existing pool-management boundary.

- [ ] **Step 2: Run route and permission tests and verify failure**

Run: `python -m unittest tests.test_auto_replenishment_routes tests.test_role_permissions -v`

Expected: FAIL because the route and view name are not registered.

- [ ] **Step 3: Implement router and index registration**

Register:

```text
GET  /api/auto-replenishment/settings
PUT  /api/auto-replenishment/settings
POST /api/auto-replenishment/settings/test
```

Use `require_view_permission("auto-replenishment")`, current-user dependencies, existing audit logging, and a unique index for the stable settings identity. Map validation to HTTP 400, missing target data to HTTP 404, and provider failures to a sanitized HTTP 502 response while persisting the failed test status.

- [ ] **Step 4: Run route and permission tests**

Run: `python -m unittest tests.test_auto_replenishment_routes tests.test_role_permissions -v`

Expected: PASS.

### Task 4: Frontend Page And Navigation

**Files:**
- Create: `frontend/src/pages/AutoReplenishmentPage.tsx`
- Create: `frontend/src/pages/AutoReplenishmentPage.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.ts`
- Modify: `frontend/src/navigation.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write failing page/navigation tests**

Render the exported form to static markup and assert fixed supplier/product/type/target labels, default `2` and `5`, masked password state, disabled test before credentials exist, mobile-friendly class names, and `/auto-replenishment` path mapping. Update navigation-group expectations to include the new page in the pool operations section.

- [ ] **Step 2: Run frontend tests and verify failure**

Run: `npm test -- src/App.test.ts src/pages/AutoReplenishmentPage.test.tsx`

Expected: FAIL because the view and component are missing.

- [ ] **Step 3: Implement the page and responsive styles**

Load settings on mount, preserve a blank password on updates, validate integer thresholds client-side, save with PUT, and test with POST. Show configured/test status and public balance/inventory summaries. Use a compact unframed form layout and existing button/toast conventions.

- [ ] **Step 4: Run focused frontend tests**

Run: `npm test -- src/App.test.ts src/pages/AutoReplenishmentPage.test.tsx`

Expected: PASS.

### Task 5: Full Verification

**Files:**
- Verify all files changed in Tasks 1-4.

- [ ] **Step 1: Run focused backend suite**

Run: `python -m unittest tests.test_auto_replenishment_settings tests.test_sogouedu_client tests.test_auto_replenishment_routes tests.test_role_permissions -v`

Expected: PASS.

- [ ] **Step 2: Run complete backend suite**

Run: `python -m unittest discover -s tests -v`

Expected: PASS.

- [ ] **Step 3: Run complete frontend tests and production build**

Run: `npm test`

Expected: PASS.

Run: `npm run build`

Expected: TypeScript and Vite build successfully.

- [ ] **Step 4: Inspect the final diff**

Run: `git diff --check`

Expected: no whitespace errors. Confirm there is no order-creation endpoint or client method and no secret appears in frontend responses, audits, logs, or tests outside controlled fixtures.
