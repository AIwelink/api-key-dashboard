# Dynamic User Types Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let administrators create and safely delete database-backed user types, assign them to users, and enforce owner-only API Key management through the same permission configuration.

**Architecture:** Extend the existing `role_permissions` document with role metadata and ordering, while keeping the five API Token roles fixed. User account schemas accept validated string role IDs, and user write routes verify those IDs against the current database configuration. The frontend consumes one shared role settings type, with a focused permissions component handling role lifecycle and the user page using the same settings for its dropdowns.

**Tech Stack:** FastAPI, Pydantic v2, Motor/MongoDB, Python `unittest`, React 19, TypeScript, Vitest, Vite.

---

## File Map

- Modify `backend/app/schemas.py`: add validated dynamic user role IDs and role lifecycle request models while preserving fixed API Token roles.
- Modify `backend/app/modules/system/permissions.py`: normalize/migrate dynamic role definitions, enforce protected permissions, and implement role create/delete helpers.
- Modify `backend/app/routers/settings.py`: expose role create/delete endpoints and audit them.
- Modify `backend/app/routers/users.py`: validate dynamic roles before and after user writes.
- Modify `backend/app/routers/api_tokens.py`: enforce the database-backed `api-tokens` permission.
- Modify `backend/tests/test_role_permissions.py`: cover migration, lifecycle, fallback, protected permissions, and API Key authorization.
- Create `backend/tests/test_users_dynamic_roles.py`: cover dynamic role assignment and rollback behavior.
- Modify `frontend/src/types.ts`: make user role IDs dynamic and share role settings types.
- Create `frontend/src/pages/RolePermissionsPanel.tsx`: own role permission editing, add dialog, and delete controls.
- Modify `frontend/src/pages/RolePermissionsPanel.test.tsx`: test dynamic rendering and immutable controls.
- Modify `frontend/src/pages/ApiTokensPage.tsx`: call role lifecycle endpoints and host the extracted panel.
- Modify `frontend/src/pages/UsersPage.tsx`: load role definitions and build dynamic dropdowns.
- Create `frontend/src/pages/UsersPage.test.ts`: test role option derivation without coupling to network effects.
- Modify `frontend/src/App.test.ts`: assert `api-tokens` is absent from non-owner permission fixtures.
- Modify `frontend/styles.css`: style the compact role dialog, editable labels, and delete action.

### Task 1: Dynamic Role Schemas and Settings Migration

**Files:**
- Modify: `backend/tests/test_role_permissions.py`
- Modify: `backend/tests/test_user_roles.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/modules/system/permissions.py`

- [ ] **Step 1: Write failing schema and migration tests**

Add tests that demonstrate custom user role IDs are valid for users but not API Tokens, and that stored custom roles survive normalization:

```python
def test_custom_role_is_valid_for_users(self) -> None:
    payload = UserCreate(email="support@example.com", name="Support", role="support-team", password="password123")
    self.assertEqual(payload.role, "support-team")

def test_invalid_custom_role_is_rejected(self) -> None:
    with self.assertRaises(ValidationError):
        UserCreate(email="support@example.com", name="Support", role="Support Team", password="password123")

async def test_stored_custom_roles_and_order_are_preserved(self) -> None:
    db, _ = fake_db({
        "_id": "role_permissions",
        "role_order": ["owner", "admin", "maintainer", "operator", "viewer", "support"],
        "roles": {
            "support": {
                "label": "Customer Support",
                "builtin": False,
                "allowed_views": ["todos"],
                "default_view": "todos",
            },
            "admin": {
                "allowed_views": ["api-pools", "api-tokens"],
                "default_view": "api-pools",
            },
        },
    })

    result = await permissions.get_role_permissions_settings(db)

    self.assertEqual(result["role_order"][-1], "support")
    self.assertEqual(result["roles"]["support"]["label"], "Customer Support")
    self.assertFalse(result["roles"]["support"]["builtin"])
    self.assertNotIn("api-tokens", result["roles"]["admin"]["allowed_views"])
    self.assertIn("api-tokens", result["roles"]["owner"]["allowed_views"])
```

- [ ] **Step 2: Run focused tests and verify RED**

Run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_user_roles tests.test_role_permissions
```

Expected: failures because `UserCreate.role` is still a fixed `Literal`, role metadata/order are absent, and custom roles are discarded.

- [ ] **Step 3: Add dynamic schema types**

In `backend/app/schemas.py`, keep `Role` for API Tokens and add user-specific models:

```python
from typing import Annotated, Any, Literal

Role = Literal["owner", "admin", "maintainer", "operator", "viewer"]
UserRoleId = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9-]{0,31}$")]

class UserCreate(BaseModel):
    email: EmailStr
    name: str
    role: UserRoleId = "maintainer"
    password: str | None = Field(default=None, min_length=8)

class UserUpdate(BaseModel):
    name: str | None = None
    role: UserRoleId | None = None
    status: Literal["active", "disabled", "pending_password_reset"] | None = None

class RolePermissionEntry(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    builtin: bool = False
    allowed_views: list[ViewName] = Field(default_factory=list, max_length=50)
    default_view: ViewName | None = None

class RolePermissionUpdate(BaseModel):
    label: str = Field(min_length=1, max_length=40)
    allowed_views: list[ViewName] = Field(default_factory=list, max_length=50)
    default_view: ViewName | None = None

class RolePermissionsUpdate(BaseModel):
    roles: dict[UserRoleId, RolePermissionUpdate] = Field(min_length=1)

class UserRoleCreate(BaseModel):
    id: UserRoleId
    label: str = Field(min_length=1, max_length=40)
```

Apply the existing allowed-view dedupe and default-view validator to both permission entry models through a small shared base model.

- [ ] **Step 4: Normalize built-in and custom roles**

In `backend/app/modules/system/permissions.py`:

```python
BUILTIN_ROLE_ORDER = ("owner", "admin", "maintainer", "operator", "viewer")
BUILTIN_ROLE_LABELS = {
    "owner": "owner",
    "admin": "admin",
    "maintainer": "maintainer",
    "operator": "运营",
    "viewer": "viewer",
}
OWNER_REQUIRED_VIEWS = {"api-tokens", "users"}

def _normalize_role_order(settings: dict[str, Any] | None, roles: dict[str, dict[str, Any]]) -> list[str]:
    stored = (settings or {}).get("role_order")
    result = [role for role in stored if role in roles] if isinstance(stored, list) else []
    for role in [*BUILTIN_ROLE_ORDER, *roles]:
        if role in roles and role not in result:
            result.append(role)
    return result
```

Build defaults with `label` and `builtin=True`. In `_public_settings`, normalize every valid custom role key instead of looping only over built-ins. For owner, union `OWNER_REQUIRED_VIEWS`; for every other role, remove `api-tokens`. Return `role_order` alongside `available_views` and `roles`. Update `ensure_role_permissions_settings` to persist both normalized fields when either differs.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_user_roles tests.test_role_permissions
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add backend/app/schemas.py backend/app/modules/system/permissions.py backend/tests/test_user_roles.py backend/tests/test_role_permissions.py
git commit -m "Add dynamic user role settings model"
```

### Task 2: Role Create and Delete Lifecycle

**Files:**
- Modify: `backend/tests/test_role_permissions.py`
- Modify: `backend/app/modules/system/permissions.py`
- Modify: `backend/app/routers/settings.py`

- [ ] **Step 1: Write failing lifecycle tests**

Use fake `app_settings` and `users` collections to cover successful creation, duplicate rejection, protected deletion, and referenced-role conflict:

```python
async def test_create_custom_role_appends_empty_role(self) -> None:
    db, settings_collection, _ = fake_permissions_db(None, user=None)
    result = await permissions.create_user_role(
        db,
        role_id="support",
        label="Customer Support",
        actor={"_id": "owner@example.com"},
    )
    self.assertEqual(result["role_order"][-1], "support")
    self.assertEqual(result["roles"]["support"]["allowed_views"], [])
    self.assertIsNone(result["roles"]["support"]["default_view"])
    settings_collection.update_one.assert_awaited_once()

async def test_delete_referenced_role_returns_conflict(self) -> None:
    db, _, _ = fake_permissions_db(custom_role_document(), user={"_id": "support@example.com"})
    with self.assertRaises(permissions.RoleInUseError):
        await permissions.delete_user_role(db, role_id="support", actor={"_id": "owner@example.com"})
```

Add router tests asserting audit actions `settings.role.create` and `settings.role.delete`, plus HTTP mappings: duplicate `409`, in-use `409`, built-in `400`, and missing `404`.

- [ ] **Step 2: Run lifecycle tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_role_permissions
```

Expected: failures because lifecycle functions, exceptions, schemas, and routes do not exist.

- [ ] **Step 3: Implement service lifecycle functions**

Add focused exceptions and helpers to `permissions.py`:

```python
class RoleAlreadyExistsError(ValueError):
    pass

class RoleNotFoundError(ValueError):
    pass

class BuiltinRoleDeleteError(ValueError):
    pass

class RoleInUseError(ValueError):
    pass

async def role_exists(db: AsyncIOMotorDatabase, role_id: str) -> bool:
    settings = await get_role_permissions_settings(db)
    return role_id in settings["roles"]
```

`create_user_role` must issue one conditional update whose filter contains `{f"roles.{role_id}": {"$exists": False}}`, whose `$set` writes the new `builtin=False` entry and actor metadata, and whose `$push` appends `role_id` to `role_order`. Treat a zero `modified_count` as `RoleAlreadyExistsError`, then return a fresh normalized read. `delete_user_role` must reject built-ins, check `db.users.find_one({"role": role_id}, {"_id": 1})`, then use one update with `$unset: {f"roles.{role_id}": ""}` and `$pull: {"role_order": role_id}` before returning a fresh normalized read.

- [ ] **Step 4: Add HTTP endpoints and audit logs**

In `backend/app/routers/settings.py`:

```python
@router.post("/role-permissions/roles", status_code=status.HTTP_201_CREATED)
async def post_user_role(
    payload: UserRoleCreate,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    before = await get_role_permissions_settings(db)
    try:
        updated = await create_user_role(db, role_id=payload.id, label=payload.label.strip(), actor=actor)
    except RoleAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await write_audit_log(db, actor=actor, action="settings.role.create", resource_type="role", resource_id=payload.id, before=before, after=updated)
    return updated

@router.delete("/role-permissions/roles/{role_id}")
async def delete_user_role_route(
    role_id: str,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    before = await get_role_permissions_settings(db)
    try:
        updated = await delete_user_role(db, role_id=role_id, actor=actor)
    except RoleInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except BuiltinRoleDeleteError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RoleNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await write_audit_log(db, actor=actor, action="settings.role.delete", resource_type="role", resource_id=role_id, before=before, after=updated)
    return updated
```

- [ ] **Step 5: Preserve metadata during permission updates**

Change `update_role_permissions_settings` so it updates only IDs already present in current settings, keeps `builtin`, normalizes `label`, and raises `RoleNotFoundError` for unknown IDs. Persist each submitted entry through a field-level `$set` such as `roles.support` instead of replacing the complete `roles` object; this preserves roles created by a concurrent request. Map the error to `400` in the PUT route. This prevents stale clients from recreating deleted roles or overwriting newly added roles.

- [ ] **Step 6: Run lifecycle tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_role_permissions
```

Expected: all role settings and lifecycle tests pass.

- [ ] **Step 7: Commit Task 2**

```powershell
git add backend/app/modules/system/permissions.py backend/app/routers/settings.py backend/tests/test_role_permissions.py
git commit -m "Add user role lifecycle endpoints"
```

### Task 3: Dynamic Role Validation in User Writes

**Files:**
- Create: `backend/tests/test_users_dynamic_roles.py`
- Modify: `backend/app/routers/users.py`

- [ ] **Step 1: Write failing user route tests**

Directly call router functions with fake collections and patch `role_exists`:

```python
async def test_create_user_accepts_database_role(self) -> None:
    db = fake_user_db(existing=None)
    with patch.object(users_router, "role_exists", AsyncMock(side_effect=[True, True])):
        result = await users_router.create_user(
            UserCreate(email="support@example.com", name="Support", role="support", password="password123"),
            actor={"_id": "owner@example.com"},
            db=db,
        )
    self.assertEqual(result["role"], "support")

async def test_create_user_rejects_missing_database_role(self) -> None:
    db = fake_user_db(existing=None)
    with patch.object(users_router, "role_exists", AsyncMock(return_value=False)):
        with self.assertRaises(HTTPException) as raised:
            await users_router.create_user(
                UserCreate(email="support@example.com", name="Support", role="removed-role", password="password123"),
                actor={"_id": "owner@example.com"},
                db=db,
            )
    self.assertEqual(raised.exception.status_code, 400)
    db.users.insert_one.assert_not_awaited()
```

Add a race test where the first role check is true and the post-write check is false; assert the newly inserted user is deleted. Add an update race test asserting the old role is restored.

- [ ] **Step 2: Run user route tests and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_users_dynamic_roles
```

Expected: failures because user routes do not query role settings or roll back concurrent deletions.

- [ ] **Step 3: Validate and recheck create operations**

Import `role_exists` and add:

```python
async def _require_existing_role(db: AsyncIOMotorDatabase, role_id: str) -> None:
    if not await role_exists(db, role_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User role does not exist")
```

Call it before insertion. After insertion, call `role_exists` again; if false, delete the inserted user by `_id` and return `400`. Write the audit log only after the second check succeeds.

- [ ] **Step 4: Validate and roll back update operations**

When `payload.role` is present, validate before `update_one`. Snapshot every field included in the request before writing. After the update, recheck the role; if it disappeared, restore all fields changed by this request plus the original update metadata, then return `400`. Write the audit log only after successful post-write validation, so an error never leaves a partial name, status, or role update behind.

- [ ] **Step 5: Run user route tests and verify GREEN**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_users_dynamic_roles
```

Expected: all dynamic user role tests pass.

- [ ] **Step 6: Commit Task 3**

```powershell
git add backend/app/routers/users.py backend/tests/test_users_dynamic_roles.py
git commit -m "Validate database roles for user updates"
```

### Task 4: Owner-Only API Key Management

**Files:**
- Modify: `backend/tests/test_role_permissions.py`
- Modify: `backend/app/routers/api_tokens.py`
- Modify: `frontend/src/App.test.ts`

- [ ] **Step 1: Write failing backend authorization tests**

Resolve the dependency attached to an API Token route and invoke it against owner/admin settings:

```python
async def test_api_token_route_permission_rejects_admin_and_accepts_owner(self) -> None:
    route = next(route for route in api_tokens_router.router.routes if route.path == "/api-tokens" and "GET" in route.methods)
    dependency = route.dependant.dependencies[0].call
    db, _ = fake_db(None)

    with self.assertRaises(HTTPException) as raised:
        await dependency(user={"_id": "admin@example.com", "role": "admin"}, db=db)
    self.assertEqual(raised.exception.status_code, 403)

    owner = {"_id": "owner@example.com", "role": "owner"}
    self.assertEqual(await dependency(user=owner, db=db), owner)
```

- [ ] **Step 2: Write the failing frontend fixture assertion**

In `frontend/src/App.test.ts`, build admin permissions without `api-tokens` and assert the system management navigation item is absent:

```typescript
expect(visibleKeys).not.toContain("api-tokens");
```

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m unittest tests.test_role_permissions
cd ..\frontend
.\node_modules\.bin\vitest.cmd run src/App.test.ts --configLoader runner
```

Expected: backend route dependency has the old fixed-role signature and admin remains visible in the frontend fixture.

- [ ] **Step 4: Bind API Token routes to database permission**

In `backend/app/routers/api_tokens.py`, replace every `Depends(require_roles("owner", "admin"))` with:

```python
Depends(require_view_permission("api-tokens"))
```

Import `require_view_permission` from `app.modules.system.permissions`. Keep the actor parameter for create/revoke audit logging.

- [ ] **Step 5: Update frontend owner/admin fixtures**

Keep `api-tokens` in `ownerPermissions`, remove it from `adminPermissions`, and update expected admin navigation groups accordingly.

- [ ] **Step 6: Run focused tests and verify GREEN**

Run the same backend and frontend commands. Expected: both suites pass.

- [ ] **Step 7: Commit Task 4**

```powershell
git add backend/app/routers/api_tokens.py backend/tests/test_role_permissions.py frontend/src/App.test.ts
git commit -m "Restrict API key management to owners"
```

### Task 5: Extract and Extend the Role Permissions Panel

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/pages/RolePermissionsPanel.tsx`
- Modify: `frontend/src/pages/RolePermissionsPanel.test.tsx`
- Modify: `frontend/src/pages/ApiTokensPage.tsx`

- [ ] **Step 1: Write failing component and helper tests**

Update the fixture to include role metadata/order and import from the new component:

```typescript
const settings: RolePermissionsSettings = {
  available_views: ["traffic-analysis", "operations-management", "api-tokens", "api-pools"],
  role_order: ["owner", "admin", "maintainer", "operator", "viewer", "support"],
  roles: {
    owner: { label: "owner", builtin: true, allowed_views: ["api-pools", "api-tokens"], default_view: "api-pools" },
    admin: { label: "admin", builtin: true, allowed_views: ["api-pools"], default_view: "api-pools" },
    maintainer: { label: "maintainer", builtin: true, allowed_views: ["api-pools"], default_view: "api-pools" },
    operator: { label: "运营", builtin: true, allowed_views: ["traffic-analysis", "operations-management"], default_view: "traffic-analysis" },
    viewer: { label: "viewer", builtin: true, allowed_views: ["api-pools"], default_view: "api-pools" },
    support: { label: "客服", builtin: false, allowed_views: [], default_view: null },
  },
};
```

Render the panel and assert it contains `添加用户类型`, `客服`, and one delete action for `support`. Assert the `api-tokens` checkbox is disabled for owner and admin. Keep the existing default-view fallback test but call it with string role IDs.

- [ ] **Step 2: Run panel tests and verify RED**

Run from `frontend`:

```powershell
.\node_modules\.bin\vitest.cmd run src/pages/RolePermissionsPanel.test.tsx --configLoader runner
```

Expected: failure because the shared types and extracted component do not exist.

- [ ] **Step 3: Add shared dynamic types**

In `frontend/src/types.ts`:

```typescript
export type UserRole = string;

export type RolePermissionEntry = {
  label: string;
  builtin: boolean;
  allowed_views: ViewName[];
  default_view: ViewName | null;
};

export type RolePermissionsSettings = {
  available_views: ViewName[];
  role_order: UserRole[];
  roles: Record<UserRole, RolePermissionEntry>;
  updated_at?: string;
  updated_by?: string;
};
```

- [ ] **Step 4: Build the focused permissions component**

Move `toggleRoleViewPermission`, default-view selection, and panel markup into `RolePermissionsPanel.tsx`. Use these props:

```typescript
type Props = {
  settings: RolePermissionsSettings | null;
  busy: boolean;
  onChange: (settings: RolePermissionsSettings) => void;
  onSave: () => void;
  onCreate: (roleId: string, label: string) => Promise<void>;
  onDelete: (roleId: string) => Promise<void>;
};
```

The component owns `showCreate`, `roleId`, and `label` form state. Render cards with `settings.role_order`, use `entry.label`, allow label editing through an immutable update, and show delete only when `!entry.builtin`. Disable every `api-tokens` checkbox so the owner-only rule is visible but cannot be changed. Use a compact `role="dialog"` overlay for creation and `window.confirm` before deletion.

- [ ] **Step 5: Integrate endpoint calls in the system page**

In `ApiTokensPage.tsx`, remove the moved types/helpers/component and import them. Add:

```typescript
const createUserRole = async (roleId: string, label: string) => {
  const updated = await api<RolePermissionsSettings>("/settings/role-permissions/roles", token, {
    method: "POST",
    body: JSON.stringify({ id: roleId, label }),
  });
  setRolePermissionsSettings(updated);
  showToast("用户类型已添加");
};

const deleteUserRole = async (roleId: string) => {
  const updated = await api<RolePermissionsSettings>(`/settings/role-permissions/roles/${encodeURIComponent(roleId)}`, token, {
    method: "DELETE",
  });
  setRolePermissionsSettings(updated);
  showToast("用户类型已删除");
};
```

Pass these handlers into the extracted panel. Let the existing `errorMessage` path show duplicate/in-use errors and rethrow from handlers so the dialog remains open after failure.

- [ ] **Step 6: Run panel tests and verify GREEN**

Run:

```powershell
.\node_modules\.bin\vitest.cmd run src/pages/RolePermissionsPanel.test.tsx --configLoader runner
```

Expected: all panel tests pass.

- [ ] **Step 7: Commit Task 5**

```powershell
git add frontend/src/types.ts frontend/src/pages/RolePermissionsPanel.tsx frontend/src/pages/RolePermissionsPanel.test.tsx frontend/src/pages/ApiTokensPage.tsx
git commit -m "Add dynamic user types to permissions panel"
```

### Task 6: Dynamic Role Options in User Management

**Files:**
- Create: `frontend/src/pages/UsersPage.test.ts`
- Modify: `frontend/src/pages/UsersPage.tsx`

- [ ] **Step 1: Write failing role option tests**

Export a pure helper and test labels, order, and owner filtering:

```typescript
it("builds user role options from backend settings", () => {
  expect(roleOptionsFromSettings(settings, false)).toEqual([
    { label: "admin", value: "admin" },
    { label: "客服", value: "support" },
  ]);
});

it("keeps owner available when editing an owner", () => {
  expect(roleOptionsFromSettings(settings, true)[0]).toEqual({ label: "owner", value: "owner" });
});
```

Use a minimal fixture with `role_order: ["owner", "admin", "support"]`.

- [ ] **Step 2: Run the helper test and verify RED**

Run:

```powershell
.\node_modules\.bin\vitest.cmd run src/pages/UsersPage.test.ts --configLoader runner
```

Expected: failure because `roleOptionsFromSettings` does not exist.

- [ ] **Step 3: Load role settings with users**

Add `roleSettings` state and load it from `/settings/role-permissions`:

```typescript
const [roleSettings, setRoleSettings] = useState<RolePermissionsSettings | null>(null);

const loadPageData = async () => {
  const [usersData, settingsData] = await Promise.all([
    api<{ items: User[] }>("/users", token),
    api<RolePermissionsSettings>("/settings/role-permissions", token),
  ]);
  setUsers(usersData.items);
  setRoleSettings(settingsData);
};
```

Use `loadPageData` for initial load, refresh, and auto-refresh.

- [ ] **Step 4: Replace fixed role helpers**

Implement:

```typescript
export function roleOptionsFromSettings(settings: RolePermissionsSettings | null, includeOwner: boolean) {
  if (!settings) return [];
  return settings.role_order
    .filter((role) => Boolean(settings.roles[role]))
    .filter((role) => includeOwner || role !== "owner")
    .map((role) => ({ label: settings.roles[role].label, value: role }));
}
```

Use it for create/edit selects and role labels. Keep the current role value as a fallback option if a historical user references an unknown role. Disable submission while role settings are unavailable so an empty select cannot submit.

- [ ] **Step 5: Run user page tests and verify GREEN**

Run:

```powershell
.\node_modules\.bin\vitest.cmd run src/pages/UsersPage.test.ts --configLoader runner
```

Expected: both role option tests pass.

- [ ] **Step 6: Commit Task 6**

```powershell
git add frontend/src/pages/UsersPage.tsx frontend/src/pages/UsersPage.test.ts
git commit -m "Load user types dynamically in user management"
```

### Task 7: Styling and Full Verification

**Files:**
- Modify: `frontend/styles.css`
- Test: all backend and frontend files changed above

- [ ] **Step 1: Add scoped role management styles**

Add styles under `.role-permissions-panel` for a compact toolbar, 8px-or-less cards, stable button dimensions, editable label input, delete icon, and modal overlay. Use existing CSS variables and responsive breakpoints. Required selectors:

```css
.role-permissions-toolbar {}
.role-permission-label-input {}
.role-permission-delete {}
.role-create-backdrop {}
.role-create-dialog {}
.role-create-actions {}
```

The dialog must fit at 320px viewport width, labels must wrap instead of overlap, and checkbox rows must retain stable height.

- [ ] **Step 2: Run the complete backend suite**

Run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Expected: all tests pass with no failures or errors.

- [ ] **Step 3: Run the complete frontend suite**

Run from `frontend`:

```powershell
.\node_modules\.bin\vitest.cmd run --configLoader runner
```

Expected: all test files pass.

- [ ] **Step 4: Run the production build**

Run:

```powershell
npm.cmd run build
```

Expected: TypeScript and Vite build complete successfully; the existing large-chunk warning is acceptable.

- [ ] **Step 5: Check the final diff**

Run from repository root:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors and only files from this plan are modified.

- [ ] **Step 6: Commit styling and any verification fixes**

```powershell
git add frontend/styles.css
git commit -m "Polish dynamic user type controls"
```
