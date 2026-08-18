# Feishu Pending Identity Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a password-verified local user to recover a Feishu identity that is held only by an unprivileged auto-provisioned pending user, while preserving identity uniqueness and audit history.

**Architecture:** Keep the unique external identity on the pending source user and turn that document into a disabled identity proxy pointing at the verified target user. Store only a safe local source reference and display metadata on the target; future Feishu logins resolve the proxy, while password login treats the local reference as a completed binding.

**Tech Stack:** FastAPI, Motor/MongoDB, Pydantic, Python unittest/AsyncMock

---

## File Map

- Modify `backend/app/modules/auth/feishu.py`: binding detection, pending-source qualification, proxy merge, proxy resolution, and audit.
- Modify `backend/app/routers/auth.py`: recognize direct and proxy-backed Feishu bindings during password login.
- Modify `backend/app/modules/system/user_projection.py`: expose proxy-backed users as safely bound without exposing local source IDs.
- Modify `backend/app/routers/users.py`: exclude merged identity proxies from the active user-management list.
- Modify `backend/tests/test_feishu_auth.py`: recovery eligibility, merge, proxy login, conflict, and safe projection tests.
- Modify `backend/tests/test_auth_routes.py`: proxy-backed password login regression test.
- Modify `backend/tests/test_users_dynamic_roles.py`: merged proxy filtering regression test.
- Create `backend/scripts/recover_feishu_pending_identity.py`: explicit, idempotent production repair command using the same service path.
- Create `backend/tests/test_recover_feishu_pending_identity.py`: script preview and execution contract tests.

### Task 1: Pending Identity Proxy Service

**Files:**
- Modify: `backend/tests/test_feishu_auth.py`
- Modify: `backend/app/modules/auth/feishu.py`

- [x] **Step 1: Write failing recovery tests**

Add tests that arrange an identity source with `created_by="feishu"`, `authorization_status="pending"`, `email_is_placeholder=True`, `role="viewer"`, and a different target local user. Assert `resolve_feishu_user(... purpose="bind")` returns the target, conditionally disables the source with `merged_into_user_id`, writes a target `feishu_identity.source_user_id` reference without an `identity_key`, and records `auth.feishu.pending_identity_merged`.

```python
result = await feishu.resolve_feishu_user(
    db,
    identity=identity(email=None),
    purpose="bind",
    target_user_id="owner@example.com",
)
self.assertEqual(result["_id"], "owner@example.com")
self.assertEqual(source_write["$set"]["merged_into_user_id"], "owner@example.com")
self.assertEqual(target_write["$set"]["feishu_identity.source_user_id"], source["_id"])
self.assertNotIn("feishu_identity.identity_key", target_write["$set"])
```

Also add separate tests rejecting active, non-placeholder, non-viewer, already-merged-to-another-target, disabled-target, and already-bound-target cases. Add an idempotency test where the source already points to the requested target but the target reference is missing.

- [x] **Step 2: Run the focused tests and verify RED**

Run from `backend`:

```powershell
.venv\Scripts\python.exe -m unittest tests.test_feishu_auth.FeishuIdentityResolutionTests -v
```

Expected: the eligible recovery test fails with `identity_already_bound`; proxy-resolution helpers do not exist.

- [x] **Step 3: Implement binding detection and proxy merge**

Add:

```python
def has_feishu_binding(user: dict[str, Any]) -> bool:
    identity = user.get("feishu_identity") or {}
    return bool(identity.get("identity_key") or identity.get("source_user_id"))
```

In `resolve_feishu_user`, resolve an existing `merged_into_user_id` before checking whether its source is disabled. For `purpose="bind"` with a different current holder, call a focused `_recover_pending_identity(...)` helper. The helper must use guarded `find_one_and_update` calls with the qualification fields from the design, keep the unique identity key only on the source, and permit only same-target idempotent completion.

Use these target fields:

```python
{
    "feishu_identity.source_user_id": str(source["_id"]),
    "feishu_identity.name": identity.name,
    "feishu_identity.email": _normalize_email(identity.email),
    "feishu_identity.avatar_url": identity.avatar_url,
    "feishu_identity.bound_via": "password_binding_recovery",
    "feishu_identity.bound_at": timestamp,
    "last_feishu_login_at": timestamp,
    "updated_at": timestamp,
}
```

Audit only local IDs and safe result codes.

- [x] **Step 4: Run focused tests and verify GREEN**

Run the same unittest command. Expected: all `FeishuIdentityResolutionTests` pass.

- [x] **Step 5: Commit the service change**

```powershell
git add backend/app/modules/auth/feishu.py backend/tests/test_feishu_auth.py
git commit -m "fix: recover pending Feishu identities"
```

### Task 2: Password Login, Projection, and User List

**Files:**
- Modify: `backend/tests/test_auth_routes.py`
- Modify: `backend/tests/test_users_dynamic_roles.py`
- Modify: `backend/app/routers/auth.py`
- Modify: `backend/app/modules/system/user_projection.py`
- Modify: `backend/app/routers/users.py`

- [x] **Step 1: Write failing integration-boundary tests**

Add a password-login test with `feishu_identity={"source_user_id": "feishu-pending"}` and assert no binding session is created. Add a projection test asserting `feishu_bound=True` while `source_user_id` remains absent from the response. Add a user-list test asserting the database query is `{"merged_into_user_id": {"$exists": False}}`.

- [x] **Step 2: Run boundary tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_auth_routes.PasswordLoginBindingTests tests.test_users_dynamic_roles.DynamicUserRoleTests -v
```

Expected: proxy-backed password login requests another binding, projection reports unbound, and list query is `{}`.

- [x] **Step 3: Implement boundary changes**

Import and use `has_feishu_binding(user)` in the password login route. In `public_user`, compute bound state from `identity_key` or `source_user_id` before discarding `feishu_identity`. Change the list query to:

```python
db.users.find({"merged_into_user_id": {"$exists": False}})
```

- [x] **Step 4: Run boundary tests and verify GREEN**

Run the same unittest command. Expected: all selected tests pass.

- [x] **Step 5: Commit boundary changes**

```powershell
git add backend/app/routers/auth.py backend/app/modules/system/user_projection.py backend/app/routers/users.py backend/tests/test_auth_routes.py backend/tests/test_users_dynamic_roles.py
git commit -m "fix: recognize Feishu identity proxies"
```

### Task 3: Idempotent Production Repair Command

**Files:**
- Create: `backend/scripts/recover_feishu_pending_identity.py`
- Create: `backend/tests/test_recover_feishu_pending_identity.py`

- [x] **Step 1: Write failing command tests**

Test a pure `safe_summary(user)` helper and an async `recover(db, source_user_id, target_user_id)` function. The latter must load the exact source, reconstruct `FeishuIdentity` from stored fields, call `resolve_feishu_user(... purpose="bind")`, and reject source/target ambiguity. Assert summaries contain local IDs and state flags but no identity key or external IDs.

- [x] **Step 2: Run command tests and verify RED**

```powershell
.venv\Scripts\python.exe -m unittest tests.test_recover_feishu_pending_identity -v
```

Expected: import failure because the command module does not exist.

- [x] **Step 3: Implement preview-first command**

Create an argparse command requiring exact `--source-user-id` and `--target-user-id`. Without `--yes`, print a redacted preview and exit without writes. With `--yes`, call `recover`, print the redacted final target summary, and never print `identity_key`, `open_id`, `union_id`, `user_id`, credentials, or tokens.

- [x] **Step 4: Run command tests and verify GREEN**

Run the same unittest command. Expected: all command tests pass.

- [x] **Step 5: Commit the repair command**

```powershell
git add backend/scripts/recover_feishu_pending_identity.py backend/tests/test_recover_feishu_pending_identity.py
git commit -m "ops: add Feishu identity recovery command"
```

### Task 4: Current Owner Repair and Full Verification

**Files:**
- Modify only MongoDB records selected by exact local IDs.

- [x] **Step 1: Preview the current repair**

```powershell
.venv\Scripts\python.exe -m scripts.recover_feishu_pending_identity --source-user-id feishu-9a8ae544e3494cfc9b168e856711458c --target-user-id 1020290137@qq.com
```

Expected: a redacted preview showing a qualifying pending source and active unbound Owner target, with no external identity values.

- [x] **Step 2: Execute and re-run idempotently**

Run the same command with `--yes`, then run it a second time with `--yes`. Expected: both runs resolve to the Owner; the second run reports the already-completed relationship without creating another user or audit conflict.

- [x] **Step 3: Run complete backend tests**

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests
```

Expected: zero failures and zero errors.

- [x] **Step 4: Run frontend tests and build**

```powershell
npm test
npm run build
```

Expected: all Vitest tests pass and the Vite production build exits zero.

- [x] **Step 5: Verify repository state and publish**

```powershell
git diff --check
git status --short --branch
git push origin codex/feishu-qr-login
```

Expected: no whitespace errors, a clean worktree after commits, and the remote branch updated for supplemental PR #51 targeting `achernar/dev`.
