# Operations User Site Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add per-user AIWeLink and AIGCLink access permissions to the operations management module, defaulting every unconfigured user to no access.

**Architecture:** Persist operations_site_ids on MongoDB user documents, manage them through dedicated system-settings endpoints, and expose the normalized list through /auth/me. Every operations route resolves an allowed site scope before calling services; services and PostgreSQL repositories receive a non-empty site ID collection so an omitted site filter can never expand to unauthorized data.

**Tech Stack:** FastAPI, Pydantic, Motor/MongoDB, SQLAlchemy async PostgreSQL, React 19, TypeScript, Vitest, Python unittest.

---

## File Structure

- Create backend/app/modules/operations/site_permissions.py: normalize site IDs, list user permission settings, and persist complete user mappings.
- Modify backend/app/schemas.py: request models for the complete per-user permission mapping.
- Modify backend/app/routers/settings.py: owner/admin configuration endpoints and audit logging.
- Modify backend/app/routers/auth.py: expose normalized operations_site_ids.
- Modify backend/app/routers/operations.py: resolve and enforce actor site scope on every operations route.
- Modify backend/app/modules/operations/service.py: pass allowed site collections through cache and data operations.
- Modify backend/app/modules/operations/repository.py: parameterized PostgreSQL filtering by an allowed site collection.
- Create backend/tests/test_operations_site_permissions.py: configuration, normalization, auth response, and route enforcement tests.
- Modify backend/tests/test_operations_routes.py: authorized read/write actors, multi-site scope, refresh, and cache tests.
- Modify backend/tests/test_operations_repository.py: SQL collection scope assertions.
- Create frontend/src/pages/OperationsSitePermissionsPanel.tsx: table-first per-user site permission editor.
- Create frontend/src/pages/OperationsSitePermissionsPanel.test.tsx: rendering and immutable toggle tests.
- Modify frontend/src/pages/ApiTokensPage.tsx: load, dirty-state, and save integration.
- Modify frontend/src/pages/RolePermissionsPanel.tsx: keep role controls independent from personal site permissions.
- Modify frontend/src/types.ts: permission response and user types.
- Modify frontend/src/App.tsx: pass current user site access to operations management.
- Modify frontend/src/pages/OperationsManagementPage.tsx: filtered selectors, request guard, and no-access state.
- Modify frontend/src/pages/OperationsManagementPage.test.tsx: no-access and single-site rendering tests.
- Modify frontend/styles.css: restrained table and empty-state styling consistent with the system-management workspace.

### Task 1: Backend permission configuration

**Files:**
- Create: backend/app/modules/operations/site_permissions.py
- Modify: backend/app/schemas.py
- Modify: backend/app/routers/settings.py
- Modify: backend/app/routers/auth.py
- Create: backend/tests/test_operations_site_permissions.py

- [ ] **Step 1: Write failing normalization and default-deny tests**

Test that normalize_operations_site_ids returns an empty list for missing or malformed values, removes unknown values, deduplicates values, and returns the fixed order aiwelink then aigclink.

- [ ] **Step 2: Run the targeted test and verify RED**

Run: backend/.venv/Scripts/python.exe -m unittest tests.test_operations_site_permissions -v
Expected: FAIL because site_permissions and request schemas do not exist.

- [ ] **Step 3: Implement the permission domain and schemas**

Define:

    OPERATIONS_SITES = (
        {"id": "aiwelink", "label": "AIWeLink"},
        {"id": "aigclink", "label": "AIGCLink"},
    )

    def normalize_operations_site_ids(value: object) -> list[str]:
        ...

Add Pydantic entries containing user_id and operations_site_ids, with only the two supported site IDs accepted and duplicates normalized.

- [ ] **Step 4: Add failing GET/PUT settings endpoint tests**

Cover complete user listing, missing fields defaulting to empty, invalid or missing users being rejected before writes, successful updates, and audit before/after payloads.

- [ ] **Step 5: Implement settings persistence and routes**

GET /settings/operations-site-permissions returns sites and all users. PUT validates the complete mapping against current users, updates operations_site_ids, returns canonical state, and writes action settings.operations_site_permissions.update with resource ID operations_site_permissions.

- [ ] **Step 6: Add and satisfy /auth/me response test**

user_with_permissions must set operations_site_ids to the normalized list even when the stored field is missing or malformed.

- [ ] **Step 7: Run backend permission tests**

Run: backend/.venv/Scripts/python.exe -m unittest tests.test_operations_site_permissions tests.test_role_permissions tests.test_users_dynamic_roles -v
Expected: PASS.

### Task 2: Backend operations enforcement and query scoping

**Files:**
- Modify: backend/app/routers/operations.py
- Modify: backend/app/modules/operations/service.py
- Modify: backend/app/modules/operations/repository.py
- Modify: backend/tests/test_operations_routes.py
- Modify: backend/tests/test_operations_repository.py

- [ ] **Step 1: Write failing route scope tests**

Add tests proving:
- a missing operations_site_ids field is denied with 403;
- an actor authorized only for aiwelink cannot request aigclink;
- an omitted site passes only the actor allowed set to the service;
- refresh defaults to and validates the actor scope;
- owner/admin writes require both the existing role check and personal site access;
- API Token actors without a mapping are denied.

- [ ] **Step 2: Run route tests and verify RED**

Run: backend/.venv/Scripts/python.exe -m unittest tests.test_operations_routes -v
Expected: FAIL because routes currently discard actor and services do not accept allowed_site_ids.

- [ ] **Step 3: Implement centralized route scope resolution**

Add a helper that normalizes actor operations_site_ids, raises 403 for an empty scope or explicit unauthorized site, and returns a tuple in canonical order. Apply it to summary, trends, users, sync status, refresh, internal users, conversion rates, classification tasks, redemption batches, and balance adjustments.

- [ ] **Step 4: Write failing service/cache tests**

Assert the site collection is included in overview, trend, user, sync-status, and list cache keys and forwarded to repository calls. Ensure identical filters reuse cache and different scopes do not.

- [ ] **Step 5: Implement service collection propagation**

Require allowed_site_ids on all operations reads. Resolve an explicit site to a one-item collection after route validation. Restrict refresh to the intersection of requested and authorized sites. Pass the scope into repository list and aggregate functions.

- [ ] **Step 6: Write failing PostgreSQL scope tests**

Assert aggregate, trend, account, sync-status, internal-user, conversion-rate, and classification-task SQL contains a bound site collection condition and no nullable all-sites bypass. Assert parameters contain only the authorized sites.

- [ ] **Step 7: Implement repository site collection filters**

Use a parameterized condition equivalent to:

    site_id = ANY(CAST(:allowed_site_ids AS TEXT[]))

Apply the condition to every operations read. Keep existing single-site synchronization functions unchanged because background sync is system-owned, not a user operation.

- [ ] **Step 8: Enforce site ownership for UUID updates**

Before updating an internal-user or classification resource, read its site ID and reject it when outside the actor scope. Do not rely on frontend payloads for resource ownership.

- [ ] **Step 9: Run backend operations tests**

Run: backend/.venv/Scripts/python.exe -m unittest tests.test_operations_routes tests.test_operations_repository tests.test_operations_sync tests.test_operations_domain -v
Expected: PASS.

### Task 3: Permission management UI

**Files:**
- Create: frontend/src/pages/OperationsSitePermissionsPanel.tsx
- Create: frontend/src/pages/OperationsSitePermissionsPanel.test.tsx
- Modify: frontend/src/pages/ApiTokensPage.tsx
- Modify: frontend/src/types.ts
- Modify: frontend/styles.css

- [ ] **Step 1: Write failing component tests**

Render a settings response with owner, admin, and operator users. Assert the table contains user identity columns, AIWeLink and AIGCLink checkbox columns, unchecked defaults, and a save button. Test that toggling one checkbox returns a new settings value changing only the target user.

- [ ] **Step 2: Run the component test and verify RED**

Run: npm test -- OperationsSitePermissionsPanel.test.tsx
Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement the table editor**

Build a full-width operations-site-permission section below role permissions. Use stable checkbox dimensions, clear status labels, a compact save command, and no nested cards. Export a pure toggleOperationsSitePermission helper for deterministic tests.

- [ ] **Step 4: Add failing ApiTokensPage integration assertions**

Assert the permissions tab loads both role permissions and operations site permissions, tracks dirty state independently, and PUTs a complete users mapping to /settings/operations-site-permissions.

- [ ] **Step 5: Integrate API state and save flow**

Add settings state, dirty ref, loading, independent save action, failure preservation, and refresh protection. A failed save must keep local checkbox choices. A successful save must replace local state with the server response.

- [ ] **Step 6: Run focused frontend permission tests**

Run: npm test -- RolePermissionsPanel.test.tsx OperationsSitePermissionsPanel.test.tsx
Expected: PASS.

### Task 4: Operations page access-aware UI

**Files:**
- Modify: frontend/src/types.ts
- Modify: frontend/src/App.tsx
- Modify: frontend/src/pages/OperationsManagementPage.tsx
- Modify: frontend/src/pages/OperationsManagementPage.test.tsx
- Modify: frontend/styles.css

- [ ] **Step 1: Write failing no-access and single-site tests**

Assert:
- missing or empty allowedSiteIds renders “尚未分配运营站点权限”;
- no-access markup does not render the analytics loading workspace;
- aiwelink-only markup contains AIWeLink and not AIGCLink;
- both sites retain “全部站点”.

- [ ] **Step 2: Run the page test and verify RED**

Run: npm test -- OperationsManagementPage.test.tsx
Expected: FAIL because the page does not accept allowedSiteIds and renders both sites.

- [ ] **Step 3: Pass current-user access from App**

Extend User with operations_site_ids and pass the normalized list into OperationsManagementPage. Missing values must become an empty list.

- [ ] **Step 4: Implement access-aware page behavior**

Filter every SiteSelect by allowedSiteIds. Skip all effects and requests when the list is empty. Render a stable no-access state. Initialize create forms with the first permitted site, and reset invalid selected site filters to all permitted sites when props change.

- [ ] **Step 5: Verify all operations tabs**

Ensure overview, internal personnel, credits, and classification selectors use the same allowed list and cannot create a payload for an unauthorized site.

- [ ] **Step 6: Run focused page tests**

Run: npm test -- OperationsManagementPage.test.tsx App.test.ts
Expected: PASS.

### Task 5: Integration verification

**Files:**
- Review all files above.
- No unrelated refactors.

- [ ] **Step 1: Run backend full test suite**

Run: backend/.venv/Scripts/python.exe -m unittest discover -s tests -v
Expected: PASS with zero failures.

- [ ] **Step 2: Run frontend full test suite**

Run: npm test
Expected: PASS with zero failures.

- [ ] **Step 3: Run production frontend build**

Run: npm run build
Expected: exit 0. The existing Vite chunk-size warning is acceptable.

- [ ] **Step 4: Run static checks**

Run: git diff --check
Expected: no whitespace errors.

- [ ] **Step 5: Review authorization coverage**

Confirm every /api/operations route either applies a site scope or is explicitly system-owned. Confirm no NULL or empty site list can query all operations rows.

- [ ] **Step 6: Final code review**

Dispatch a final reviewer over the complete diff for security regressions, missing route coverage, incorrect cache isolation, and frontend access-state behavior.
