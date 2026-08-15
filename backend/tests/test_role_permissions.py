from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import get_args
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.system import permissions
from app.routers import accounts as accounts_router
from app.routers import agent as agent_router
from app.routers import api_pools as api_pools_router
from app.routers import api_tokens as api_tokens_router
from app.routers import auth as auth_router
from app.routers import settings as settings_router
from app.routers import sub2api_sites as sub2api_sites_router
from app.routers import todo_items as todo_items_router
from app.schemas import RolePermissionEntry, RolePermissionsUpdate, UserRoleCreate, ViewName


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
    def test_work_plans_is_a_valid_permission_view(self) -> None:
        self.assertIn("work-plans", get_args(ViewName))

    async def test_work_plans_is_mandatory_for_builtin_and_stored_custom_roles(self) -> None:
        roles = {
            role: {
                "allowed_views": ["todos"],
                "default_view": "todos",
            }
            for role in permissions.ROLE_ORDER
        }
        roles["support"] = {
            "label": "Support",
            "builtin": False,
            "allowed_views": ["todos"],
            "default_view": "todos",
        }
        db, _ = fake_db(
            {
                "_id": "role_permissions",
                "roles": roles,
                "role_order": [*permissions.ROLE_ORDER, "support"],
            }
        )

        result = await permissions.get_role_permissions_settings(db)

        self.assertEqual(result["available_views"][0], "work-plans")
        for role in (*permissions.ROLE_ORDER, "support"):
            with self.subTest(role=role):
                self.assertIn("work-plans", result["roles"][role]["allowed_views"])
                self.assertEqual(result["roles"][role]["default_view"], "todos")

    async def test_permission_update_cannot_remove_work_plans(self) -> None:
        stored = {
            "_id": "role_permissions",
            "roles": {
                "support": {
                    "label": "Support",
                    "builtin": False,
                    "allowed_views": ["todos"],
                    "default_view": "todos",
                }
            },
            "role_order": [*permissions.ROLE_ORDER, "support"],
        }
        db, collection, _ = fake_permissions_db(stored)
        collection.find_one.side_effect = [stored, stored]
        collection.update_one.return_value = SimpleNamespace(matched_count=1, modified_count=1)

        result = await permissions.update_role_permissions_settings(
            db,
            payload=RolePermissionsUpdate(
                roles={
                    "support": RolePermissionEntry(
                        allowed_views=["todos"],
                        default_view="todos",
                    )
                }
            ),
            actor={"_id": "admin@example.com"},
        )

        stored_update = collection.update_one.await_args.args[1]["$set"]["roles.support"]
        self.assertIn("work-plans", stored_update["allowed_views"])
        self.assertIn("work-plans", result["roles"]["support"]["allowed_views"])

    async def test_unconfigured_permissions_return_database_backed_defaults(self) -> None:
        db, _ = fake_db(None)

        result = await permissions.get_role_permissions_settings(db)

        self.assertEqual(
            result["roles"]["operator"]["allowed_views"],
            ["work-plans", "traffic-analysis", "operations-management"],
        )
        self.assertEqual(result["roles"]["operator"]["default_view"], "traffic-analysis")
        self.assertIn("system-management", result["roles"]["admin"]["allowed_views"])
        self.assertNotIn("api-tokens", result["roles"]["admin"]["allowed_views"])
        self.assertNotIn("presence", result["roles"]["admin"]["allowed_views"])
        self.assertNotIn("users", result["roles"]["maintainer"]["allowed_views"])
        self.assertNotIn("users", result["roles"]["viewer"]["allowed_views"])
        self.assertIn("auto-replenishment", result["roles"]["owner"]["allowed_views"])
        self.assertIn("auto-replenishment", result["roles"]["admin"]["allowed_views"])
        self.assertIn("auto-replenishment", result["roles"]["maintainer"]["allowed_views"])
        self.assertIn("auto-replenishment", result["roles"]["viewer"]["allowed_views"])
        self.assertNotIn("auto-replenishment", result["roles"]["operator"]["allowed_views"])

    async def test_existing_pool_management_permission_inherits_auto_replenishment(self) -> None:
        db, _ = fake_db(
            {
                "_id": "role_permissions",
                "roles": {
                    "support": {
                        "label": "Support",
                        "builtin": False,
                        "allowed_views": ["pool-lifecycle"],
                        "default_view": "pool-lifecycle",
                    }
                },
                "role_order": [*permissions.ROLE_ORDER, "support"],
            }
        )

        result = await permissions.get_role_permissions_settings(db)

        self.assertEqual(
            result["roles"]["support"]["allowed_views"],
            ["work-plans", "pool-lifecycle", "auto-replenishment"],
        )

    async def test_user_role_catalog_excludes_permissions_and_deleting_roles(self) -> None:
        db, _, _ = fake_permissions_db(
            {
                "_id": "role_permissions",
                "role_order": [*permissions.ROLE_ORDER, "support", "retired"],
                "roles": {
                    "support": {
                        "label": "Support",
                        "builtin": False,
                        "allowed_views": ["users"],
                        "default_view": "users",
                    },
                    "retired": {
                        "label": "Retired",
                        "builtin": False,
                        "deleting": True,
                        "allowed_views": [],
                        "default_view": None,
                    },
                },
            }
        )

        result = await permissions.get_user_role_catalog(db)

        self.assertIn("support", result["roles"])
        self.assertNotIn("allowed_views", result["roles"]["support"])
        self.assertNotIn("retired", result["roles"])
        self.assertNotIn("retired", result["role_order"])

    async def test_permission_update_is_normalized_and_persisted(self) -> None:
        db, collection = fake_db(None)
        collection.find_one.side_effect = [
            None,
            {
                "_id": "role_permissions",
                "roles": {
                    "operator": {
                        "label": "运营",
                        "builtin": True,
                        "allowed_views": ["operations-management", "traffic-analysis"],
                        "default_view": "operations-management",
                    }
                },
            },
        ]
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
        self.assertEqual(
            updates["roles.operator"]["allowed_views"],
            ["work-plans", "traffic-analysis", "operations-management"],
        )
        self.assertEqual(updates["roles.operator"]["default_view"], "operations-management")
        self.assertEqual(updates["updated_by"], "admin@example.com")
        self.assertEqual(
            result["roles"]["operator"]["allowed_views"],
            ["work-plans", "traffic-analysis", "operations-management"],
        )

    async def test_permission_update_rejects_role_deleted_after_read(self) -> None:
        db, collection, _ = fake_permissions_db(
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
            modified_count=0,
        )
        collection.update_one.return_value = SimpleNamespace(matched_count=0, modified_count=0)
        payload = RolePermissionsUpdate(
            roles={
                "support": RolePermissionEntry(
                    label="Support",
                    allowed_views=["todos"],
                    default_view="todos",
                )
            }
        )

        with self.assertRaises(permissions.RoleNotFoundError):
            await permissions.update_role_permissions_settings(
                db,
                payload=payload,
                actor={"_id": "admin@example.com"},
            )

        update_filter = collection.update_one.await_args.args[0]
        self.assertEqual(update_filter["roles.support"], {"$exists": True})
        self.assertEqual(update_filter["roles.support.deleting"], {"$ne": True})

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

        self.assertEqual(result["allowed_views"], ["work-plans", "operations-management"])
        self.assertEqual(result["default_view"], "operations-management")

    async def test_default_permissions_can_be_ensured_in_app_settings(self) -> None:
        db, collection = fake_db(None)

        result = await permissions.ensure_role_permissions_settings(db)

        updates = collection.update_one.await_args.args[1]["$setOnInsert"]
        self.assertEqual(collection.update_one.await_args.args[0], {"_id": "role_permissions"})
        self.assertEqual(
            updates["roles"]["operator"]["allowed_views"],
            ["work-plans", "traffic-analysis", "operations-management"],
        )
        self.assertEqual(updates["updated_by"], "system")
        self.assertEqual(result["roles"]["operator"]["default_view"], "traffic-analysis")

    async def test_ensure_permissions_does_not_overwrite_a_concurrent_update(self) -> None:
        stored = {
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
        fresh = {
            "_id": "role_permissions",
            "role_order": [*permissions.ROLE_ORDER, "sales"],
            "roles": {
                "sales": {
                    "label": "Sales",
                    "builtin": False,
                    "allowed_views": ["traffic-analysis"],
                    "default_view": "traffic-analysis",
                }
            },
        }
        db, collection = fake_db(stored)
        collection.find_one.side_effect = [stored, fresh]
        collection.update_one.return_value = SimpleNamespace(matched_count=0, modified_count=0)

        result = await permissions.ensure_role_permissions_settings(db)

        update_filter = collection.update_one.await_args.args[0]
        self.assertEqual(update_filter["roles"], stored["roles"])
        self.assertEqual(update_filter["role_order"], stored["role_order"])
        self.assertIn("sales", result["roles"])
        self.assertNotIn("support", result["roles"])

    async def test_api_token_capability_cannot_be_the_default_page(self) -> None:
        db, _ = fake_db(
            {
                "_id": "role_permissions",
                "roles": {
                    "owner": {
                        "allowed_views": ["system-management", "api-tokens"],
                        "default_view": "api-tokens",
                    }
                },
            }
        )

        result = await permissions.get_role_permissions_settings(db)

        self.assertEqual(result["roles"]["owner"]["default_view"], "system-management")

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
        self.assertIn("system-management", result["roles"]["admin"]["allowed_views"])

    async def test_create_custom_role_appends_empty_role(self) -> None:
        db, collection, _ = fake_permissions_db(None)
        collection.find_one.side_effect = [
            None,
            {
                "_id": "role_permissions",
                "role_order": [*permissions.ROLE_ORDER, "support"],
                "roles": {
                    "support": {
                        "label": "Customer Support",
                        "builtin": False,
                        "allowed_views": [],
                        "default_view": None,
                    }
                },
            },
        ]

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
        self.assertEqual(result["roles"]["support"]["allowed_views"], ["work-plans"])
        self.assertEqual(result["roles"]["support"]["default_view"], "work-plans")

    async def test_create_role_returns_fresh_database_state(self) -> None:
        db, collection, _ = fake_permissions_db(None)
        collection.find_one.side_effect = [
            None,
            {
                "_id": "role_permissions",
                "role_order": [*permissions.ROLE_ORDER, "support", "sales"],
                "roles": {
                    "support": {
                        "label": "Support",
                        "builtin": False,
                        "allowed_views": [],
                        "default_view": None,
                    },
                    "sales": {
                        "label": "Sales",
                        "builtin": False,
                        "allowed_views": ["traffic-analysis"],
                        "default_view": "traffic-analysis",
                    },
                },
            },
        ]

        result = await permissions.create_user_role(
            db,
            role_id="support",
            label="Support",
            actor={"_id": "owner@example.com"},
        )

        self.assertIn("sales", result["roles"])
        self.assertEqual(result["role_order"][-1], "sales")

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
        self.assertEqual(collection.update_one.await_count, 2)
        mark_update = collection.update_one.await_args_list[0]
        self.assertEqual(mark_update.args[0]["roles.support.deleting"], {"$ne": True})
        self.assertTrue(mark_update.args[1]["$set"]["roles.support.deleting"])
        clear_update = collection.update_one.await_args_list[1]
        self.assertEqual(clear_update.args[1]["$unset"], {"roles.support.deleting": ""})

    async def test_delete_role_clears_deleting_marker_when_user_check_fails(self) -> None:
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
            }
        )
        users.find_one.side_effect = RuntimeError("database unavailable")

        with self.assertRaises(RuntimeError):
            await permissions.delete_user_role(db, role_id="support", actor={"_id": "owner@example.com"})

        self.assertEqual(collection.update_one.await_count, 2)
        clear_update = collection.update_one.await_args_list[1]
        self.assertEqual(clear_update.args[0]["roles.support.deleting"], True)
        self.assertEqual(clear_update.args[1]["$unset"], {"roles.support.deleting": ""})

    async def test_delete_role_resumes_an_existing_deleting_marker(self) -> None:
        initial = {
            "_id": "role_permissions",
            "role_order": [*permissions.ROLE_ORDER, "support"],
            "roles": {
                "support": {
                    "label": "Support",
                    "builtin": False,
                    "deleting": True,
                    "allowed_views": [],
                    "default_view": None,
                }
            },
        }
        db, collection, _ = fake_permissions_db(initial)
        collection.find_one.side_effect = [
            initial,
            {"_id": "role_permissions", "role_order": list(permissions.ROLE_ORDER), "roles": {}},
        ]
        collection.update_one.side_effect = [
            SimpleNamespace(matched_count=1, modified_count=0),
            SimpleNamespace(matched_count=1, modified_count=1),
        ]

        result = await permissions.delete_user_role(
            db,
            role_id="support",
            actor={"_id": "owner@example.com"},
        )

        self.assertNotIn("support", result["roles"])
        self.assertEqual(collection.update_one.await_count, 2)

    async def test_delete_builtin_role_is_rejected(self) -> None:
        db, collection, _ = fake_permissions_db(None)

        with self.assertRaises(permissions.BuiltinRoleDeleteError):
            await permissions.delete_user_role(db, role_id="viewer", actor={"_id": "owner@example.com"})

        collection.update_one.assert_not_awaited()

    async def test_delete_unused_custom_role_removes_role_and_order(self) -> None:
        initial = {
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
        db, collection, _ = fake_permissions_db(initial)
        collection.find_one.side_effect = [
            initial,
            {
                "_id": "role_permissions",
                "role_order": list(permissions.ROLE_ORDER),
                "roles": {},
            },
        ]

        result = await permissions.delete_user_role(
            db,
            role_id="support",
            actor={"_id": "owner@example.com"},
        )

        self.assertEqual(collection.update_one.await_count, 2)
        mark_update = collection.update_one.await_args_list[0].args[1]
        self.assertTrue(mark_update["$set"]["roles.support.deleting"])
        delete_update = collection.update_one.await_args_list[1].args[1]
        self.assertEqual(delete_update["$unset"], {"roles.support": ""})
        self.assertEqual(delete_update["$pull"], {"role_order": "support"})
        self.assertNotIn("support", result["roles"])
        self.assertNotIn("support", result["role_order"])

    async def test_delete_role_returns_fresh_database_state(self) -> None:
        initial = {
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
        }
        fresh = {
            "_id": "role_permissions",
            "role_order": [*permissions.ROLE_ORDER, "sales"],
            "roles": {
                "sales": {
                    "label": "Sales",
                    "builtin": False,
                    "allowed_views": ["traffic-analysis"],
                    "default_view": "traffic-analysis",
                }
            },
        }
        db, collection, _ = fake_permissions_db(initial)
        collection.find_one.side_effect = [initial, fresh]

        result = await permissions.delete_user_role(
            db,
            role_id="support",
            actor={"_id": "owner@example.com"},
        )

        self.assertNotIn("support", result["roles"])
        self.assertIn("sales", result["roles"])

    async def test_deleting_role_is_not_assignable(self) -> None:
        db, _, _ = fake_permissions_db(
            {
                "_id": "role_permissions",
                "roles": {
                    "support": {
                        "label": "Support",
                        "builtin": False,
                        "deleting": True,
                        "allowed_views": [],
                        "default_view": None,
                    }
                },
            }
        )

        self.assertFalse(await permissions.role_exists(db, "support"))

    async def test_deleting_role_does_not_grant_its_permissions(self) -> None:
        db, _, _ = fake_permissions_db(
            {
                "_id": "role_permissions",
                "roles": {
                    "support": {
                        "label": "Support",
                        "builtin": False,
                        "deleting": True,
                        "allowed_views": ["users"],
                        "default_view": "users",
                    }
                },
            }
        )

        result = await permissions.permissions_for_user(db, {"role": "support"})

        self.assertNotIn("users", result["allowed_views"])

    async def test_delete_role_restores_role_when_a_user_appears_after_delete(self) -> None:
        initial = {
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
        db, collection, users = fake_permissions_db(initial)
        users.find_one.side_effect = [None, {"_id": "support@example.com"}]

        with self.assertRaises(permissions.RoleInUseError):
            await permissions.delete_user_role(
                db,
                role_id="support",
                actor={"_id": "owner@example.com"},
            )

        restore_update = collection.update_one.await_args_list[2]
        self.assertEqual(restore_update.args[0]["roles.support"], {"$exists": False})
        self.assertEqual(restore_update.args[1]["$set"]["roles.support"]["label"], "Support")


class RolePermissionRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_user_role_catalog_route_uses_users_permission(self) -> None:
        route = next(
            route
            for route in settings_router.router.routes
            if route.path == "/settings/user-roles" and "GET" in route.methods
        )
        dependency = route.dependant.dependencies[0].call
        db, _ = fake_db(
            {
                "_id": "role_permissions",
                "roles": {
                    "support": {
                        "label": "Support",
                        "builtin": False,
                        "allowed_views": ["users"],
                        "default_view": "users",
                    }
                },
            }
        )
        actor = {"_id": "support@example.com", "role": "support"}

        self.assertEqual(await dependency(user=actor, db=db), actor)

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
    def custom_role_db(self, view: str):
        return fake_db(
            {
                "_id": "role_permissions",
                "roles": {
                    "custom": {
                        "label": "Custom",
                        "builtin": False,
                        "allowed_views": [view],
                        "default_view": view,
                    }
                },
            }
        )[0]

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

    async def test_any_view_permission_accepts_one_database_permission(self) -> None:
        db, _ = fake_db(
            {
                "_id": "role_permissions",
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
        actor = {"_id": "support@example.com", "role": "support"}
        dependency = permissions.require_any_view_permission("accounts", "todos")

        self.assertEqual(await dependency(user=actor, db=db), actor)

    async def test_accounts_api_uses_database_page_permission(self) -> None:
        route = next(route for route in accounts_router.router.routes if route.path == "/accounts" and "GET" in route.methods)
        dependency = route.dependant.dependencies[0].call
        db, _ = fake_db(
            {
                "_id": "role_permissions",
                "roles": {
                    "support": {
                        "label": "Support",
                        "builtin": False,
                        "allowed_views": ["accounts"],
                        "default_view": "accounts",
                    }
                },
            }
        )
        actor = {"_id": "support@example.com", "role": "support"}

        self.assertEqual(await dependency(user=actor, db=db), actor)

    async def test_todo_api_uses_database_page_permission(self) -> None:
        route = next(
            route
            for route in todo_items_router.router.routes
            if route.path == "/todo-items/free-to-plus/accounts" and "GET" in route.methods
        )
        dependency = route.dependant.dependencies[0].call
        db, _ = fake_db(
            {
                "_id": "role_permissions",
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
        actor = {"_id": "support@example.com", "role": "support"}

        self.assertEqual(await dependency(user=actor, db=db), actor)

    async def test_growth_database_api_uses_database_page_permission(self) -> None:
        route = next(
            route
            for route in settings_router.router.routes
            if route.path == "/settings/growth-database" and "GET" in route.methods
        )
        dependency = route.dependant.dependencies[0].call
        db, _ = fake_db(
            {
                "_id": "role_permissions",
                "roles": {
                    "analyst": {
                        "label": "Analyst",
                        "builtin": False,
                        "allowed_views": ["traffic-analysis-config"],
                        "default_view": "traffic-analysis-config",
                    }
                },
            }
        )
        actor = {"_id": "analyst@example.com", "role": "analyst"}

        self.assertEqual(await dependency(user=actor, db=db), actor)

    async def test_agent_analysis_cannot_pause_scheduler(self) -> None:
        route = next(
            route
            for route in agent_router.router.routes
            if route.path == "/agent/scheduler/pause" and "POST" in route.methods
        )
        dependency = route.dependant.dependencies[0].call

        with self.assertRaises(HTTPException) as raised:
            await dependency(
                user={"_id": "custom@example.com", "role": "custom"},
                db=self.custom_role_db("agent-analysis"),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_todos_permission_cannot_process_push_errors(self) -> None:
        route = next(
            route
            for route in todo_items_router.router.routes
            if route.path == "/todo-items/push-errors/accounts/{account_id}/decide" and "POST" in route.methods
        )
        dependency = route.dependant.dependencies[0].call

        with self.assertRaises(HTTPException) as raised:
            await dependency(
                user={"_id": "custom@example.com", "role": "custom"},
                db=self.custom_role_db("todos"),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_alert_center_cannot_create_api_pools(self) -> None:
        route = next(route for route in api_pools_router.router.routes if route.path == "/api-pools" and "POST" in route.methods)
        dependency = route.dependant.dependencies[0].call

        with self.assertRaises(HTTPException) as raised:
            await dependency(
                user={"_id": "custom@example.com", "role": "custom"},
                db=self.custom_role_db("alert-center"),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_reserve_pool_cannot_delete_accounts(self) -> None:
        route = next(
            route
            for route in accounts_router.router.routes
            if route.path == "/accounts/{account_id}" and "DELETE" in route.methods
        )
        dependency = route.dependant.dependencies[0].call

        with self.assertRaises(HTTPException) as raised:
            await dependency(
                user={"_id": "custom@example.com", "role": "custom"},
                db=self.custom_role_db("reserve-pool"),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_event_records_can_read_sub2api_sites(self) -> None:
        route = next(
            route
            for route in sub2api_sites_router.router.routes
            if route.path == "/sub2api-sites" and "GET" in route.methods
        )
        dependency = route.dependant.dependencies[0].call
        actor = {"_id": "custom@example.com", "role": "custom"}

        self.assertEqual(await dependency(user=actor, db=self.custom_role_db("event-records")), actor)

    async def test_api_token_route_permission_rejects_admin_and_accepts_owner(self) -> None:
        route = next(
            route
            for route in api_tokens_router.router.routes
            if route.path == "/api-tokens" and "GET" in route.methods
        )
        dependency = route.dependant.dependencies[0].call
        db, _ = fake_db(None)

        with self.assertRaises(HTTPException) as raised:
            await dependency(user={"_id": "admin@example.com", "role": "admin"}, db=db)
        self.assertEqual(raised.exception.status_code, 403)

        owner = {"_id": "owner@example.com", "role": "owner"}
        self.assertEqual(await dependency(user=owner, db=db), owner)


if __name__ == "__main__":
    unittest.main()
