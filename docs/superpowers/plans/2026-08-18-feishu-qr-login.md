# Feishu QR Login Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Feishu QR authorization login, automatic binding for existing users, forced binding after password login, and zero-permission onboarding for new Feishu identities.

**Architecture:** FastAPI owns the OAuth authorization-code flow and stores short-lived one-time sessions in MongoDB. Stable Feishu identities bind to existing local users or create pending users, after which the backend issues the existing local JWT. React opens the official Feishu authorization page, polls the local session, exchanges the one-time ticket, and renders a dedicated pending-authorization state when the backend returns no views.

**Tech Stack:** FastAPI, Motor/MongoDB, httpx, Pydantic, unittest, React 19, TypeScript, Vite, Vitest

---

## File Map

- Create `backend/app/modules/auth/__init__.py`: authentication module package.
- Create `backend/app/modules/auth/feishu.py`: OAuth client, authorization-session persistence, identity binding, pending-user provisioning, and ticket consumption.
- Create `backend/tests/test_feishu_auth.py`: Feishu service and binding concurrency tests.
- Create `backend/tests/test_auth_routes.py`: password binding, QR callback, polling, and ticket exchange route tests.
- Modify `backend/app/config.py`: Feishu application, endpoint, tenant, and timeout settings.
- Modify `backend/app/schemas.py`: Feishu session, exchange, and password binding response schemas; authorization status on user updates.
- Modify `backend/app/security.py`: separate authenticated users from business-authorized users.
- Modify `backend/app/modules/system/permissions.py`: return no views for pending users.
- Modify `backend/app/modules/system/bootstrap.py`: backfill authorization status and create Feishu/session indexes.
- Modify `backend/app/routers/auth.py`: expose Feishu routes and force first-time password binding.
- Modify `backend/app/routers/users.py`: activate pending users when an administrator assigns a role and expose safe binding metadata.
- Modify `backend/app/logging_config.py`: redact Feishu codes, states, tickets, and identity values.
- Modify `.env.example` and `backend/README.md`: document production Feishu configuration.
- Create `frontend/src/auth/feishu.ts`: popup/redirect flow helpers and status polling.
- Create `frontend/src/auth/feishu.test.ts`: popup, fallback, and polling helper tests.
- Create `frontend/src/pages/LoginPage.test.tsx`: QR login and forced-binding component tests.
- Create `frontend/src/pages/PendingAuthorizationPage.tsx`: zero-permission authenticated state.
- Modify `frontend/src/pages/LoginPage.tsx`: primary Feishu action, password fallback, and binding state.
- Modify `frontend/src/App.tsx`: consume both login outcomes and render pending authorization safely.
- Modify `frontend/src/types.ts`: authorization status, Feishu binding metadata, and auth response types.
- Modify `frontend/src/pages/UsersPage.tsx` and `frontend/src/pages/UsersPage.test.ts`: pending-first display and role activation.
- Modify `styles.css`: responsive login, popup-state, and pending-authorization presentation.

### Task 1: Feishu Configuration and API Schemas

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/schemas.py`
- Modify: `.env.example`
- Test: `backend/tests/test_feishu_auth.py`

- [x] **Step 1: Write failing configuration and schema tests**

Test that comma-separated tenant keys normalize to a set, production defaults keep Feishu disabled, and `LoginBindingRequiredResponse` serializes `status="binding_required"`, `authorization_url`, `session_id`, and timezone-aware `expires_at`.

```python
def test_allowed_tenant_keys_are_normalized() -> None:
    settings = Settings(feishu_allowed_tenant_keys="tenant-b, tenant-a,tenant-b")
    assert settings.allowed_feishu_tenant_keys() == {"tenant-a", "tenant-b"}

def test_binding_required_response_is_explicit() -> None:
    payload = LoginBindingRequiredResponse(
        authorization_url="https://accounts.feishu.cn/open-apis/authen/v1/authorize?app_id=cli_example&state=state-1",
        session_id="session-1",
        expires_at=datetime(2026, 8, 18, tzinfo=UTC),
    )
    assert payload.status == "binding_required"
```

- [x] **Step 2: Run the focused tests and confirm missing types fail**

Run from `backend`: `.venv/Scripts/python.exe -m unittest tests.test_feishu_auth -v`

Expected: import or attribute failure for the new Feishu settings/schema.

- [x] **Step 3: Add typed settings and schemas**

Add settings with these defaults:

```python
feishu_auth_enabled: bool = False
feishu_app_id: str | None = None
feishu_app_secret: str | None = None
feishu_redirect_uri: str | None = None
feishu_allowed_tenant_keys: str = ""
feishu_authorize_base_url: str = "https://accounts.feishu.cn"
feishu_open_api_base_url: str = "https://open.feishu.cn"
feishu_request_timeout_seconds: float = 8.0
```

Add Pydantic response models for session creation/status, ticket exchange, password binding required, and `authorization_status` updates. All response datetimes must be timezone-aware.

- [x] **Step 4: Run tests and commit**

Run from `backend`: `.venv/Scripts/python.exe -m unittest tests.test_feishu_auth -v`

Expected: PASS.

Commit: `feat: add Feishu auth configuration`

### Task 2: Feishu OAuth Sessions and Identity Binding

**Files:**
- Create: `backend/app/modules/auth/__init__.py`
- Create: `backend/app/modules/auth/feishu.py`
- Test: `backend/tests/test_feishu_auth.py`

- [x] **Step 1: Write failing service tests**

Cover authorization URL encoding, hashed state storage, allowed-tenant rejection, identity-key lookup, verified-email auto-binding, password-target binding with different email, disabled-user rejection, pending-user provisioning, and single-use ticket consumption.

```python
async def test_existing_unbound_email_is_bound_atomically(self) -> None:
    identity = FeishuIdentity(
        tenant_key="tenant-a",
        open_id="open-1",
        union_id="union-1",
        user_id="user-1",
        name="Member",
        email="Member@Example.com",
        avatar_url=None,
    )
    user = await resolve_feishu_user(db, identity=identity, purpose="login", target_user_id=None)
    self.assertEqual(user["_id"], "member@example.com")
    self.assertEqual(user["feishu_identity"]["identity_key"], "tenant-a:union:union-1")
```

- [x] **Step 2: Confirm the service tests fail before implementation**

Run from `backend`: `.venv/Scripts/python.exe -m unittest tests.test_feishu_auth.FeishuIdentityResolutionTests -v`

Expected: missing module/functions.

- [x] **Step 3: Implement the Feishu module**

Implement the focused public operations `create_authorization_session`, `complete_authorization_session`, `get_authorization_session_status`, `consume_login_ticket`, and `resolve_feishu_user`. Each function accepts the database as its first argument; session functions use keyword-only IDs/tokens, while identity resolution accepts `identity`, `purpose`, and optional `target_user_id` keyword arguments.

Use SHA-256 hashes for `state` and tickets, `secrets.token_urlsafe(32)` for raw values, five-minute sessions, sixty-second tickets, conditional MongoDB updates, and `DuplicateKeyError` reconciliation. Use the OAuth v2 token endpoint and user-info endpoint through an injected `httpx.AsyncClient`; never persist external access tokens.

- [x] **Step 4: Run all Feishu service tests and commit**

Run from `backend`: `.venv/Scripts/python.exe -m unittest tests.test_feishu_auth -v`

Expected: PASS.

Commit: `feat: add Feishu OAuth identity service`

### Task 3: Pending Authorization Security Boundary and Migration

**Files:**
- Modify: `backend/app/security.py`
- Modify: `backend/app/modules/system/permissions.py`
- Modify: `backend/app/modules/system/bootstrap.py`
- Modify: `backend/app/logging_config.py`
- Test: `backend/tests/test_role_permissions.py`
- Test: `backend/tests/test_feishu_auth.py`

- [x] **Step 1: Write failing deny-by-default tests**

```python
async def test_pending_user_has_no_views(self) -> None:
    result = await permissions_for_user(db, {"role": "viewer", "authorization_status": "pending"})
    self.assertEqual(result, {"allowed_views": [], "default_view": None})

async def test_business_current_user_rejects_pending_user(self) -> None:
    with self.assertRaises(HTTPException) as raised:
        await require_business_authorization({"_id": "pending", "authorization_status": "pending"})
    self.assertEqual(raised.exception.status_code, 403)
```

Also test that missing `authorization_status` remains active, API-token actors remain authorized, indexes are partial/unique, and sensitive logging keys include `code`, `state`, `ticket`, `open_id`, and `union_id`.

- [x] **Step 2: Run focused tests and verify failures**

Run from `backend`: `.venv/Scripts/python.exe -m unittest tests.test_role_permissions tests.test_feishu_auth -v`

Expected: pending users currently inherit viewer/work-plan permissions.

- [x] **Step 3: Implement authentication/authorization separation**

Create `get_authenticated_user` for JWT/user lookup and make `get_current_user` call it plus a pending-state rejection. Change `/auth/me` later to use `get_authenticated_user`. Keep API token actors authorized. Add idempotent status backfill and partial unique indexes in bootstrap.

- [x] **Step 4: Run focused tests and commit**

Run from `backend`: `.venv/Scripts/python.exe -m unittest tests.test_role_permissions tests.test_feishu_auth -v`

Expected: PASS.

Commit: `feat: enforce pending user authorization boundary`

### Task 4: Authentication Routes and Password Binding

**Files:**
- Modify: `backend/app/routers/auth.py`
- Create: `backend/tests/test_auth_routes.py`
- Modify: `backend/app/schemas.py`

- [x] **Step 1: Write failing route tests**

Cover disabled Feishu configuration, session creation, callback success/error HTML, session polling, ticket exchange, password login for a bound user, and password login returning `binding_required` for an unbound user.

```python
async def test_password_login_requires_binding_for_unbound_user(self) -> None:
    response = await login(LoginRequest(email="member@example.com", password="secret123"), db=db)
    self.assertEqual(response.status, "binding_required")
    create_session.assert_awaited_once_with(db, purpose="bind", target_user_id="member@example.com")
```

- [x] **Step 2: Run route tests and confirm failure**

Run from `backend`: `.venv/Scripts/python.exe -m unittest tests.test_auth_routes -v`

Expected: missing routes and old password response shape.

- [x] **Step 3: Implement routes and audit events**

Add session start/status, callback, and exchange routes. Return a minimal callback HTML page that posts only the session ID to the configured frontend origin and closes. Change password login to return either `LoginResponse` or `LoginBindingRequiredResponse`; use `get_authenticated_user` for `/auth/me`. Write only redacted result codes to audit.

- [x] **Step 4: Run auth tests and commit**

Run from `backend`: `.venv/Scripts/python.exe -m unittest tests.test_auth_routes tests.test_feishu_auth -v`

Expected: PASS.

Commit: `feat: expose Feishu QR authentication routes`

### Task 5: Administrator Activation and Safe User Responses

**Files:**
- Modify: `backend/app/routers/users.py`
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_users_dynamic_roles.py`

- [x] **Step 1: Write failing pending-user administration tests**

Verify list responses contain safe Feishu binding metadata but no identity IDs, pending users sort before active users, role assignment atomically sets `authorization_status=active`, and non-owner users still cannot manage owner records.

- [x] **Step 2: Run focused tests and verify failures**

Run from `backend`: `.venv/Scripts/python.exe -m unittest tests.test_users_dynamic_roles -v`

Expected: no activation/status fields and creation-order sorting.

- [x] **Step 3: Implement safe public-user projection and activation**

Return only `feishu_bound`, `feishu_name`, `feishu_avatar_url`, `feishu_email`, `feishu_bound_at`, and `last_feishu_login_at`. On a pending user, a valid role update must set both role and `authorization_status=active` in the same guarded write.

- [x] **Step 4: Run user tests and commit**

Run from `backend`: `.venv/Scripts/python.exe -m unittest tests.test_users_dynamic_roles -v`

Expected: PASS.

Commit: `feat: manage pending Feishu users`

### Task 6: React QR Login and Pending State

**Files:**
- Create: `frontend/src/auth/feishu.ts`
- Create: `frontend/src/auth/feishu.test.ts`
- Create: `frontend/src/pages/LoginPage.test.tsx`
- Create: `frontend/src/pages/PendingAuthorizationPage.tsx`
- Modify: `frontend/src/pages/LoginPage.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/types.ts`

- [x] **Step 1: Write failing frontend tests**

Test the primary Feishu button, popup launch, blocked-popup redirect fallback, session polling cleanup, password `binding_required` transition, successful ticket exchange, and `authorization_status=pending` rendering only the pending page.

```ts
it("falls back to same-tab authorization when the popup is blocked", () => {
  vi.spyOn(window, "open").mockReturnValue(null);
  startFeishuAuthorization(session, callbacks);
  expect(window.location.assign).toHaveBeenCalledWith(session.authorization_url);
});
```

- [x] **Step 2: Run frontend tests and confirm failures**

Run: `npm test -- src/auth/feishu.test.ts src/pages/LoginPage.test.tsx src/App.test.ts`

Expected: missing flow/component behavior.

- [x] **Step 3: Implement the frontend auth state machine**

Use explicit states `idle`, `starting`, `waiting`, `exchanging`, `binding`, `failed`. Poll no faster than once per second and cancel timers/listeners on unmount. Validate `postMessage.origin` against `window.location.origin`. Store only the local JWT/user using the existing `onLogin` path. Pending users render `PendingAuthorizationPage` with refresh and logout actions, while the sidebar contains no business links.

- [x] **Step 4: Run frontend tests and commit**

Run: `npm test -- src/auth/feishu.test.ts src/pages/LoginPage.test.tsx src/App.test.ts`

Expected: PASS.

Commit: `feat: add Feishu QR login experience`

### Task 7: Pending Users in User Management and Responsive Styling

**Files:**
- Modify: `frontend/src/pages/UsersPage.tsx`
- Modify: `frontend/src/pages/UsersPage.test.ts`
- Modify: `styles.css`

- [ ] **Step 1: Write failing user-list behavior tests**

Test pending-first sorting, labels for bound/pending users, role activation payloads, and preservation of owner edit restrictions.

- [ ] **Step 2: Run tests and verify failure**

Run: `npm test -- src/pages/UsersPage.test.ts`

Expected: missing pending sorting and labels.

- [ ] **Step 3: Implement user-management presentation**

Add a compact Feishu binding/status row, visually distinct pending state, and a role selector action labeled “分配权限”. Keep cards at the current radius, avoid nested cards, prevent horizontal overflow, and add reduced-motion fallbacks. Login motion must use opacity/transform only and preserve stable form dimensions.

- [ ] **Step 4: Run frontend tests/build and commit**

Run: `npm test -- src/pages/UsersPage.test.ts && npm run build`

Expected: PASS and successful production build.

Commit: `feat: surface Feishu authorization status`

### Task 8: Documentation, Full Verification, and Publish

**Files:**
- Modify: `backend/README.md`
- Modify: `docs/superpowers/specs/2026-08-18-feishu-qr-login-design.md`
- Modify: `docs/superpowers/plans/2026-08-18-feishu-qr-login.md`

- [ ] **Step 1: Document exact Feishu deployment configuration**

Document the enterprise self-built app requirement, callback URI, user-info/email permissions, tenant allowlist, `accounts.feishu.cn` authorization base, and `open.feishu.cn` API base. Mark every plan checkbox completed as its task finishes.

- [ ] **Step 2: Run complete backend verification**

Run from `backend`: `.venv/Scripts/python.exe -m unittest discover -s tests`

Expected: all tests PASS.

- [ ] **Step 3: Run complete frontend verification**

Run from `frontend`: `npm test` then `npm run build`.

Expected: all tests PASS and build succeeds.

- [ ] **Step 4: Run repository checks**

Run: `git diff --check` and `git status --short --branch`.

Expected: no whitespace errors and only intentional changes.

- [ ] **Step 5: Perform browser QA**

Start the local backend/frontend, open desktop and mobile viewports, and verify login, binding-required, pending authorization, popup-blocked fallback, no overlap, no page-level horizontal overflow, and no console errors. Real Feishu exchange remains a production-configuration smoke test; local QA uses the mocked backend contract.

- [ ] **Step 6: Commit documentation and publish**

Commit: `docs: document Feishu login deployment`

Push `codex/feishu-qr-login`, open a draft PR targeting `achernar/dev`, and wait for Backend and Frontend CI.
