from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.routers import users as users_router
from app.schemas import UserCreate, UserUpdate


def fake_user_db(*, existing: dict | None = None):
    users = SimpleNamespace(
        find_one=AsyncMock(return_value=existing),
        insert_one=AsyncMock(),
        update_one=AsyncMock(),
        delete_one=AsyncMock(),
    )
    return SimpleNamespace(users=users)


class DynamicUserRoleTests(unittest.IsolatedAsyncioTestCase):
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
        db.users.delete_one.assert_awaited_once_with({"_id": "support@example.com"})

    async def test_update_user_rolls_back_all_fields_when_role_is_deleted_during_write(self) -> None:
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
        rollback = db.users.update_one.await_args_list[1].args[1]["$set"]
        self.assertEqual(rollback["name"], "Original")
        self.assertEqual(rollback["role"], "maintainer")
        self.assertEqual(rollback["status"], "active")
        self.assertEqual(rollback["updated_by"], "admin@example.com")
        self.assertEqual(rollback["updated_at"], "before")


if __name__ == "__main__":
    unittest.main()
