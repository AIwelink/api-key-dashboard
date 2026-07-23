from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.system import permissions
from app.routers import auth as auth_router
from app.routers import settings as settings_router
from app.schemas import RolePermissionEntry, RolePermissionsUpdate, UserRoleCreate


def fake_db(document: dict | None):
    collection = SimpleNamespace(
        find_one=AsyncMock(return_value=document),
        update_one=AsyncMock(),
    )
    return SimpleNamespace(app_settings=collection), collection


def fake_permissions_db(document: dict | None, *, user: dict | None = None, modified_count: int = 1):
    settings_collection = SimpleNamespace(
        find_one=AsyncMock(return_value=document),
        update_one=AsyncMock(return_value=SimpleNamespace(modified_count=modified_count)),
    )
    users_collection = SimpleNamespace(find_one=AsyncMock(return_value=user))
    return SimpleNamespace(app_settings=settings_collection, users=users_collection), settings_collection, users_collection


class RolePermissionSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_permissions_return_database_backed_defaults(self) -> None:
        db, _ = fake_db(None)

        result = await permissions.get_role_permissions_settings(db)

        self.assertEqual(result["roles"]["operator"]["allowed_views"], ["traffic-analysis", "operations-management"])
        self.assertEqual(result["roles"]["operator"]["default_view"], "traffic-analysis")
        self.assertIn("api-tokens", result["roles"]["admin"]["allowed_views"])
        self.assertNotIn("presence", result["roles"]["admin"]["allowed_views"])

    async def test_permission_update_is_normalized_and_persisted(self) -> None:
        db, collection = fake_db(None)
        payload = RolePermissionsUpdate(
            roles={
                "operator": RolePermissionEntry(
                    allowed_views=["operations-management", "traffic-analysis", "traffic-analysis"],
                    default_view="operations-management",
                ),
            }
        )

        result = await permissions.update_role_permissions_settings(
            db,
            payload=payload,
            actor={"_id": "admin@example.com"},
        )

        updates = collection.update_one.await_args.args[1]["$set"]
        self.assertEqual(updates["roles.operator"]["allowed_views"], ["operations-management", "traffic-analysis"])
        self.assertEqual(updates["roles.operator"]["default_view"], "operations-management")
        self.assertEqual(updates["updated_by"], "admin@example.com")
        self.assertEqual(result["roles"]["operator"]["allowed_views"], ["operations-management", "traffic-analysis"])

    def test_permission_schema_rejects_unknown_views_and_defaults_outside_allowed_views(self) -> None:
        with self.assertRaises(ValidationError):
            RolePermissionEntry(allowed_views=["unknown-view"])
        with self.assertRaises(ValidationError):
            RolePermissionEntry(allowed_views=["traffic-analysis"], default_view="operations-management")

    async def test_user_permissions_are_loaded_from_current_database_settings(self) -> None:
        db, _ = fake_db(
            {
                "_id": "role_permissions",
                "roles": {
                    "operator": {
                        "allowed_views": ["operations-management"],
                        "default_view": "operations-management",
                    }
                },
            }
        )

        result = await permissions.permissions_for_user(db, {"role": "operator"})

        self.assertEqual(result["allowed_views"], ["operations-management"])
        self.assertEqual(result["default_view"], "operations-management")

    async def test_default_permissions_can_be_ensured_in_app_settings(self) -> None:
        db, collection = fake_db(None)

        result = await permissions.ensure_role_permissions_settings(db)

        updates = collection.update_one.await_args.args[1]["$set"]
        self.assertEqual(collection.update_one.await_args.args[0], {"_id": "role_permissions"})
        self.assertEqual(updates["roles"]["operator"]["allowed_views"], ["traffic-analysis", "operations-management"])
        self.assertEqual(updates["updated_by"], "system")
        self.assertEqual(result["roles"]["operator"]["default_view"], "traffic-analysis")

    async def test_stored_custom_roles_and_order_are_preserved(self) -> None:
        db, _ = fake_db(
            {
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
            }
        )

        result = await permissions.get_role_permissions_settings(db)

        self.assertEqual(result["role_order"][-1], "support")
        self.assertEqual(result["roles"]["support"]["label"], "Customer Support")
        self.assertFalse(result["roles"]["support"]["builtin"])
        self.assertNotIn("api-tokens", result["roles"]["admin"]["allowed_views"])
        self.assertIn("api-tokens", result["roles"]["owner"]["allowed_views"])

    async def test_create_custom_role_appends_empty_role(self) -> None:
        db, collection, _ = fake_permissions_db(None)

        result = await permissions.create_user_role(
            db,
            role_id="support",
            label="Customer Support",
            actor={"_id": "owner@example.com"},
        )

        update = collection.update_one.await_args.args[1]
        self.assertEqual(update["$set"]["roles.support"]["label"], "Customer Support")
        self.assertFalse(update["$set"]["roles.support"]["builtin"])
        self.assertEqual(update["$push"]["role_order"], "support")
        self.assertEqual(result["role_order"][-1], "support")
        self.assertEqual(result["roles"]["support"]["allowed_views"], [])
        self.assertIsNone(result["roles"]["support"]["default_view"])

    async def test_create_duplicate_role_is_rejected(self) -> None:
        db, collection, _ = fake_permissions_db(
            {
                "_id": "role_permissions",
                "roles": {
                    "support": {
                        "label": "Support",
                        "builtin": False,
                        "allowed_views": [],
                        "default_view": None,
                    }
                },
            }
        )

        with self.assertRaises(permissions.RoleAlreadyExistsError):
            await permissions.create_user_role(
                db,
                role_id="support",
                label="Customer Support",
                actor={"_id": "owner@example.com"},
            )

        collection.update_one.assert_not_awaited()

    async def test_delete_referenced_role_is_rejected(self) -> None:
        db, collection, users = fake_permissions_db(
            {
                "_id": "role_permissions",
                "role_order": [*permissions.ROLE_ORDER, "support"],
                "roles": {
                    "support": {
                        "label": "Support",
                        "builtin": False,
                        "allowed_views": [],
                        "default_view": None,
                    }
                },
            },
            user={"_id": "support@example.com"},
        )

        with self.assertRaises(permissions.RoleInUseError):
            await permissions.delete_user_role(db, role_id="support", actor={"_id": "owner@example.com"})

        users.find_one.assert_awaited_once_with({"role": "support"}, {"_id": 1})
        collection.update_one.assert_not_awaited()

    async def test_delete_builtin_role_is_rejected(self) -> None:
        db, collection, _ = fake_permissions_db(None)

        with self.assertRaises(permissions.BuiltinRoleDeleteError):
            await permissions.delete_user_role(db, role_id="viewer", actor={"_id": "owner@example.com"})

        collection.update_one.assert_not_awaited()

    async def test_delete_unused_custom_role_removes_role_and_order(self) -> None:
        db, collection, _ = fake_permissions_db(
            {
                "_id": "role_permissions",
                "role_order": [*permissions.ROLE_ORDER, "support"],
                "roles": {
                    "support": {
                        "label": "Support",
                        "builtin": False,
                        "allowed_views": ["todos"],
                        "default_view": "todos",
                    }
                },
            }
        )

        result = await permissions.delete_user_role(
            db,
            role_id="support",
            actor={"_id": "owner@example.com"},
        )

        update = collection.update_one.await_args.args[1]
        self.assertEqual(update["$unset"], {"roles.support": ""})
        self.assertEqual(update["$pull"], {"role_order": "support"})
        self.assertNotIn("support", result["roles"])
        self.assertNotIn("support", result["role_order"])


class RolePermissionRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_update_writes_audit_log(self) -> None:
        before = {"roles": {"operator": {"allowed_views": ["traffic-analysis"], "default_view": "traffic-analysis"}}}
        after = {"roles": {"operator": {"allowed_views": ["operations-management"], "default_view": "operations-management"}}}
        update_mock = AsyncMock(return_value=after)
        audit_mock = AsyncMock()

        with (
            patch.object(settings_router, "get_role_permissions_settings", AsyncMock(return_value=before), create=True),
            patch.object(settings_router, "update_role_permissions_settings", update_mock, create=True),
            patch.object(settings_router, "write_audit_log", audit_mock),
        ):
            response = await settings_router.put_role_permissions_settings(
                RolePermissionsUpdate(
                    roles={
                        "operator": RolePermissionEntry(
                            allowed_views=["operations-management"],
                            default_view="operations-management",
                        )
                    }
                ),
                actor={"_id": "owner@example.com", "role": "owner"},
                db=MagicMock(),
            )

        self.assertEqual(response, after)
        self.assertEqual(audit_mock.await_args.kwargs["resource_id"], "role_permissions")
        update_mock.assert_awaited_once()

    async def test_role_create_writes_audit_log(self) -> None:
        before = {"roles": {}}
        after = {"roles": {"support": {"label": "Support"}}}
        create_mock = AsyncMock(return_value=after)
        audit_mock = AsyncMock()

        with (
            patch.object(settings_router, "get_role_permissions_settings", AsyncMock(return_value=before)),
            patch.object(settings_router, "create_user_role", create_mock, create=True),
            patch.object(settings_router, "write_audit_log", audit_mock),
        ):
            response = await settings_router.post_user_role(
                UserRoleCreate(id="support", label="Support"),
                actor={"_id": "owner@example.com", "role": "owner"},
                db=MagicMock(),
            )

        self.assertEqual(response, after)
        self.assertEqual(audit_mock.await_args.kwargs["action"], "settings.role.create")
        self.assertEqual(audit_mock.await_args.kwargs["resource_id"], "support")

    async def test_role_delete_maps_in_use_to_conflict(self) -> None:
        with (
            patch.object(settings_router, "get_role_permissions_settings", AsyncMock(return_value={"roles": {}})),
            patch.object(
                settings_router,
                "delete_user_role",
                AsyncMock(side_effect=permissions.RoleInUseError("Role is assigned to users")),
                create=True,
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await settings_router.delete_user_role_route(
                    "support",
                    actor={"_id": "owner@example.com", "role": "owner"},
                    db=MagicMock(),
                )

        self.assertEqual(raised.exception.status_code, 409)

    async def test_auth_me_returns_current_permissions(self) -> None:
        user = {"_id": "operator@example.com", "email": "operator@example.com", "role": "operator", "password_hash": "secret"}
        db = MagicMock()
        with patch.object(
            auth_router,
            "permissions_for_user",
            AsyncMock(return_value={"allowed_views": ["operations-management"], "default_view": "operations-management"}),
            create=True,
        ):
            result = await auth_router.me(user=user, db=db)

        self.assertNotIn("password_hash", result)
        self.assertEqual(result["permissions"]["allowed_views"], ["operations-management"])


class RolePermissionDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_view_permission_dependency_uses_database_settings(self) -> None:
        db, _ = fake_db(
            {
                "_id": "role_permissions",
                "roles": {
                    "operator": {
                        "allowed_views": ["operations-management"],
                        "default_view": "operations-management",
                    }
                },
            }
        )
        dependency = permissions.require_view_permission("traffic-analysis")

        with self.assertRaises(HTTPException) as raised:
            await dependency(user={"_id": "operator@example.com", "role": "operator"}, db=db)

        self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
