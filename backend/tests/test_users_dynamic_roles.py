from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.routers import users as users_router
from app.schemas import PasswordResetRequest, UserCreate, UserUpdate


class FakeUserCursor:
    def __init__(self, items: list[dict]) -> None:
        self.items = items

    def sort(self, *_args, **_kwargs):
        return self

    def __aiter__(self):
        async def iterate():
            for item in self.items:
                yield item

        return iterate()


def fake_user_db(*, existing: dict | None = None, listed: list[dict] | None = None):
    users = SimpleNamespace(
        find=MagicMock(return_value=FakeUserCursor(listed or [])),
        find_one=AsyncMock(return_value=existing),
        insert_one=AsyncMock(),
        update_one=AsyncMock(),
        delete_one=AsyncMock(),
    )
    return SimpleNamespace(users=users)


class DynamicUserRoleTests(unittest.IsolatedAsyncioTestCase):
    def test_public_user_exposes_only_safe_feishu_metadata(self) -> None:
        result = users_router.public_user(
            {
                "_id": "member@example.com",
                "email": "member@example.com",
                "password_hash": "secret",
                "feishu_identity": {
                    "identity_key": "tenant-a:union:union-1",
                    "tenant_key": "tenant-a",
                    "union_id": "union-1",
                    "open_id": "open-1",
                    "user_id": "user-1",
                    "name": "飞书成员",
                    "email": "member@feishu.example",
                    "avatar_url": "https://example.com/avatar.png",
                    "bound_at": "2026-08-18T08:00:00+00:00",
                },
                "last_feishu_login_at": "2026-08-18T09:00:00+00:00",
            }
        )

        self.assertNotIn("password_hash", result)
        self.assertNotIn("feishu_identity", result)
        self.assertTrue(result["feishu_bound"])
        self.assertEqual(result["feishu_name"], "飞书成员")
        self.assertEqual(result["feishu_email"], "member@feishu.example")
        self.assertEqual(result["feishu_avatar_url"], "https://example.com/avatar.png")
        self.assertEqual(result["feishu_bound_at"], "2026-08-18T08:00:00+00:00")
        self.assertEqual(result["last_feishu_login_at"], "2026-08-18T09:00:00+00:00")

    async def test_list_users_places_pending_authorization_first(self) -> None:
        active = {
            "_id": "active@example.com",
            "created_at": "2026-08-18T09:00:00+00:00",
            "authorization_status": "active",
        }
        pending = {
            "_id": "pending@example.com",
            "created_at": "2026-08-18T08:00:00+00:00",
            "authorization_status": "pending",
        }
        db = fake_user_db(listed=[active, pending])

        result = await users_router.list_users(_={}, db=db)

        self.assertEqual([item["id"] for item in result["items"]], ["pending@example.com", "active@example.com"])

    async def test_assigning_role_atomically_activates_pending_user(self) -> None:
        pending = {
            "_id": "pending@example.com",
            "email": "pending@example.com",
            "role": "viewer",
            "authorization_status": "pending",
        }
        db = fake_user_db(existing=pending)
        db.users.update_one.return_value = SimpleNamespace(matched_count=1, modified_count=1)
        with (
            patch.object(users_router, "role_exists", AsyncMock(side_effect=[True, True])),
            patch.object(users_router, "write_audit_log", AsyncMock()) as audit_mock,
        ):
            await users_router.update_user(
                "pending@example.com",
                UserUpdate(role="maintainer"),
                actor={"_id": "admin@example.com", "role": "admin"},
                db=db,
            )

        role_write = db.users.update_one.await_args_list[0]
        self.assertEqual(role_write.args[1]["$set"]["role"], "maintainer")
        self.assertEqual(role_write.args[1]["$set"]["authorization_status"], "active")
        self.assertEqual(audit_mock.await_args.kwargs["action"], "user.authorization_activated")

    async def test_failed_pending_role_assignment_rolls_back_authorization(self) -> None:
        pending = {
            "_id": "pending@example.com",
            "email": "pending@example.com",
            "role": "viewer",
            "authorization_status": "pending",
        }
        db = fake_user_db(existing=pending)
        db.users.update_one.return_value = SimpleNamespace(matched_count=1, modified_count=1)
        with (
            patch.object(users_router, "role_exists", AsyncMock(side_effect=[True, False])),
            patch.object(users_router, "write_audit_log", AsyncMock()),
        ):
            with self.assertRaises(HTTPException):
                await users_router.update_user(
                    "pending@example.com",
                    UserUpdate(role="maintainer"),
                    actor={"_id": "admin@example.com", "role": "admin"},
                    db=db,
                )

        rollback = db.users.update_one.await_args_list[1].args[1]["$set"]
        self.assertEqual(rollback["authorization_status"], "pending")

    async def test_all_users_routes_use_database_view_permission(self) -> None:
        app_settings = SimpleNamespace(
            find_one=AsyncMock(
                return_value={
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
        )
        actor = {"_id": "support@example.com", "role": "support"}

        for route in users_router.router.routes:
            with self.subTest(path=route.path, methods=route.methods):
                dependency = route.dependant.dependencies[0].call
                self.assertEqual(
                    await dependency(user=actor, db=SimpleNamespace(app_settings=app_settings)),
                    actor,
                )

    async def test_non_owner_cannot_create_owner(self) -> None:
        db = fake_user_db()
        with (
            patch.object(users_router, "role_exists", AsyncMock(return_value=True)),
            patch.object(users_router, "write_audit_log", AsyncMock()),
        ):
            with self.assertRaises(HTTPException) as raised:
                await users_router.create_user(
                    UserCreate(
                        email="other-owner@example.com",
                        name="Other Owner",
                        role="owner",
                        password="password123",
                    ),
                    actor={"_id": "admin@example.com", "role": "admin"},
                    db=db,
                )

        self.assertEqual(raised.exception.status_code, 403)
        db.users.insert_one.assert_not_awaited()

    async def test_non_owner_cannot_promote_user_to_owner(self) -> None:
        db = fake_user_db(existing={"_id": "member@example.com", "role": "maintainer"})

        with self.assertRaises(HTTPException) as raised:
            await users_router.update_user(
                "member@example.com",
                UserUpdate(role="owner"),
                actor={"_id": "admin@example.com", "role": "admin"},
                db=db,
            )

        self.assertEqual(raised.exception.status_code, 403)
        db.users.update_one.assert_not_awaited()

    async def test_non_owner_cannot_update_owner(self) -> None:
        db = fake_user_db(existing={"_id": "owner@example.com", "role": "owner"})

        with self.assertRaises(HTTPException) as raised:
            await users_router.update_user(
                "owner@example.com",
                UserUpdate(name="Changed"),
                actor={"_id": "admin@example.com", "role": "admin"},
                db=db,
            )

        self.assertEqual(raised.exception.status_code, 403)
        db.users.update_one.assert_not_awaited()

    async def test_non_owner_cannot_reset_owner_password(self) -> None:
        db = fake_user_db(existing={"_id": "owner@example.com", "role": "owner"})

        with self.assertRaises(HTTPException) as raised:
            await users_router.reset_password(
                "owner@example.com",
                PasswordResetRequest(password="password123"),
                actor={"_id": "admin@example.com", "role": "admin"},
                db=db,
            )

        self.assertEqual(raised.exception.status_code, 403)
        db.users.update_one.assert_not_awaited()

    async def test_non_owner_cannot_change_owner_status(self) -> None:
        for action in (users_router.disable_user, users_router.enable_user):
            db = fake_user_db(existing={"_id": "owner@example.com", "role": "owner"})
            with self.subTest(action=action.__name__):
                with self.assertRaises(HTTPException) as raised:
                    await action(
                        "owner@example.com",
                        actor={"_id": "admin@example.com", "role": "admin"},
                        db=db,
                    )

                self.assertEqual(raised.exception.status_code, 403)
                db.users.update_one.assert_not_awaited()

    async def test_non_owner_update_rejects_concurrent_owner_promotion(self) -> None:
        db = fake_user_db(existing={"_id": "member@example.com", "role": "maintainer"})
        db.users.update_one.return_value = SimpleNamespace(matched_count=0, modified_count=0)

        with patch.object(users_router, "write_audit_log", AsyncMock()) as audit_mock:
            with self.assertRaises(HTTPException) as raised:
                await users_router.update_user(
                    "member@example.com",
                    UserUpdate(name="Changed"),
                    actor={"_id": "admin@example.com", "role": "admin"},
                    db=db,
                )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(
            db.users.update_one.await_args.args[0],
            {"_id": "member@example.com", "role": {"$ne": "owner"}},
        )
        audit_mock.assert_not_awaited()

    async def test_non_owner_password_reset_rejects_concurrent_owner_promotion(self) -> None:
        db = fake_user_db(existing={"_id": "member@example.com", "role": "maintainer"})
        db.users.update_one.return_value = SimpleNamespace(matched_count=0, modified_count=0)

        with patch.object(users_router, "write_audit_log", AsyncMock()) as audit_mock:
            with self.assertRaises(HTTPException) as raised:
                await users_router.reset_password(
                    "member@example.com",
                    PasswordResetRequest(password="password123"),
                    actor={"_id": "admin@example.com", "role": "admin"},
                    db=db,
                )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(
            db.users.update_one.await_args.args[0],
            {"_id": "member@example.com", "role": {"$ne": "owner"}},
        )
        audit_mock.assert_not_awaited()

    async def test_create_user_accepts_database_role(self) -> None:
        db = fake_user_db()
        with (
            patch.object(users_router, "role_exists", AsyncMock(side_effect=[True, True]), create=True),
            patch.object(users_router, "write_audit_log", AsyncMock()),
        ):
            result = await users_router.create_user(
                UserCreate(
                    email="support@example.com",
                    name="Support",
                    role="support",
                    password="password123",
                ),
                actor={"_id": "owner@example.com"},
                db=db,
            )

        self.assertEqual(result["role"], "support")
        db.users.insert_one.assert_awaited_once()
        db.users.delete_one.assert_not_awaited()

    async def test_create_user_rejects_missing_database_role(self) -> None:
        db = fake_user_db()
        with (
            patch.object(users_router, "role_exists", AsyncMock(return_value=False), create=True),
            patch.object(users_router, "write_audit_log", AsyncMock()),
        ):
            with self.assertRaises(HTTPException) as raised:
                await users_router.create_user(
                    UserCreate(
                        email="support@example.com",
                        name="Support",
                        role="removed-role",
                        password="password123",
                    ),
                    actor={"_id": "owner@example.com"},
                    db=db,
                )

        self.assertEqual(raised.exception.status_code, 400)
        db.users.insert_one.assert_not_awaited()

    async def test_create_user_rolls_back_when_role_is_deleted_during_write(self) -> None:
        db = fake_user_db()
        with (
            patch.object(users_router, "role_exists", AsyncMock(side_effect=[True, False]), create=True),
            patch.object(users_router, "write_audit_log", AsyncMock()),
        ):
            with self.assertRaises(HTTPException) as raised:
                await users_router.create_user(
                    UserCreate(
                        email="support@example.com",
                        name="Support",
                        role="support",
                        password="password123",
                    ),
                    actor={"_id": "owner@example.com"},
                    db=db,
                )

        self.assertEqual(raised.exception.status_code, 400)
        db.users.delete_one.assert_awaited_once_with({"_id": "support@example.com", "role": "support"})

    async def test_update_user_conditionally_rolls_back_only_role_when_role_is_deleted_during_write(self) -> None:
        original = {
            "_id": "member@example.com",
            "email": "member@example.com",
            "name": "Original",
            "role": "maintainer",
            "status": "active",
            "updated_by": "admin@example.com",
            "updated_at": "before",
        }
        db = fake_user_db(existing=original)
        with (
            patch.object(users_router, "role_exists", AsyncMock(side_effect=[True, False]), create=True),
            patch.object(users_router, "write_audit_log", AsyncMock()),
        ):
            with self.assertRaises(HTTPException) as raised:
                await users_router.update_user(
                    "member@example.com",
                    UserUpdate(name="Changed", role="support", status="disabled"),
                    actor={"_id": "owner@example.com"},
                    db=db,
                )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(db.users.update_one.await_count, 2)
        role_write = db.users.update_one.await_args_list[0]
        self.assertNotIn("name", role_write.args[1]["$set"])
        self.assertNotIn("status", role_write.args[1]["$set"])
        self.assertEqual(role_write.args[1]["$set"]["role"], "support")
        rollback_call = db.users.update_one.await_args_list[1]
        self.assertEqual(rollback_call.args[0], {"_id": "member@example.com", "role": "support"})
        rollback = rollback_call.args[1]["$set"]
        self.assertEqual(rollback["role"], "maintainer")
        self.assertEqual(rollback["updated_by"], "admin@example.com")
        self.assertEqual(rollback["updated_at"], "before")


if __name__ == "__main__":
    unittest.main()
