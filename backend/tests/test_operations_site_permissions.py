from __future__ import annotations

import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import OperationFailure

from app.modules.operations import site_permissions
from app.routers import auth as auth_router
from app.routers import settings as settings_router
from app.schemas import OperationsSitePermissionEntry, OperationsSitePermissionsUpdate


class _Cursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents
        self.sort_args: tuple[object, ...] | None = None

    def sort(self, *args: object) -> "_Cursor":
        self.sort_args = args
        return self

    def __aiter__(self):
        async def iterator():
            for document in self.documents:
                yield document

        return iterator()


class _UsersCollection:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents
        self.find_calls: list[tuple[object, ...]] = []
        self.matched_count: int | None = None
        self.bulk_write = AsyncMock(side_effect=self._bulk_write)

    def find(self, *args: object) -> _Cursor:
        self.find_calls.append(args)
        return _Cursor(self.documents)

    async def _bulk_write(self, operations: list[object], *, ordered: bool, session: object | None = None) -> SimpleNamespace:
        matched_count = len(operations) if self.matched_count is None else self.matched_count
        for operation in operations[:matched_count]:
            user_id = operation._filter["_id"]
            for document in self.documents:
                if document["_id"] == user_id:
                    document["operations_site_ids"] = operation._doc["$set"]["operations_site_ids"]
                    break
        return SimpleNamespace(matched_count=matched_count)


class _Transaction:
    def __init__(self, users: _UsersCollection) -> None:
        self.users = users
        self.snapshot: list[dict] | None = None

    async def __aenter__(self) -> "_Transaction":
        self.snapshot = deepcopy(self.users.documents)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_type is not None and self.snapshot is not None:
            self.users.documents[:] = self.snapshot
        return False


class _Session:
    def __init__(self, users: _UsersCollection) -> None:
        self.users = users

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> bool:
        return False

    def start_transaction(self) -> _Transaction:
        return _Transaction(self.users)


class _Client:
    def __init__(self, users: _UsersCollection) -> None:
        self.start_session = AsyncMock(return_value=_Session(users))


def fake_db(documents: list[dict]) -> SimpleNamespace:
    users = _UsersCollection(documents)
    return SimpleNamespace(users=users, client=_Client(users))


class OperationsSitePermissionsTests(unittest.IsolatedAsyncioTestCase):
    def test_normalization_defaults_to_deny_and_uses_canonical_order(self) -> None:
        self.assertEqual(site_permissions.normalize_operations_site_ids(None), [])
        self.assertEqual(site_permissions.normalize_operations_site_ids("aiwelink"), [])
        self.assertEqual(
            site_permissions.normalize_operations_site_ids(["aigclink", "unknown", "aiwelink", "aigclink"]),
            ["aiwelink", "aigclink"],
        )

    def test_schema_rejects_unknown_sites_and_canonicalizes_duplicates(self) -> None:
        payload = OperationsSitePermissionsUpdate(
            users=[{"user_id": "member@example.com", "operations_site_ids": ["aigclink", "aiwelink", "aigclink"]}]
        )

        self.assertEqual(payload.users[0].operations_site_ids, ["aiwelink", "aigclink"])
        with self.assertRaises(ValidationError):
            OperationsSitePermissionEntry(user_id="member@example.com", operations_site_ids=["unknown"])

    async def test_get_returns_available_sites_and_normalized_users(self) -> None:
        db = fake_db(
            [
                {
                    "_id": "member@example.com",
                    "email": "member@example.com",
                    "name": "Member",
                    "role": "operator",
                    "status": "active",
                    "operations_site_ids": ["aigclink", "unknown", "aiwelink", "aigclink"],
                },
                {"_id": "denied@example.com", "email": "denied@example.com", "name": "Denied", "role": "viewer", "status": "disabled"},
            ]
        )

        result = await site_permissions.get_operations_site_permissions(db)

        self.assertEqual(result["available_sites"], [{"id": "aiwelink", "label": "AIWeLink"}, {"id": "aigclink", "label": "AIGCLink"}])
        self.assertEqual(result["users"][0]["operations_site_ids"], ["aiwelink", "aigclink"])
        self.assertEqual(result["users"][1]["operations_site_ids"], [])
        self.assertEqual(db.users.find_calls, [({},)])

    async def test_get_route_returns_full_permissions_settings(self) -> None:
        settings = {
            "available_sites": [{"id": "aiwelink", "label": "AIWeLink"}],
            "users": [{"user_id": "member@example.com", "operations_site_ids": []}],
        }
        with patch.object(settings_router, "get_operations_site_permissions", AsyncMock(return_value=settings), create=True):
            result = await settings_router.get_operations_site_permissions_route(
                _={"_id": "owner@example.com", "role": "owner"},
                db=MagicMock(),
            )

        self.assertEqual(result, settings)
    async def test_put_rejects_missing_or_unknown_users_before_writes(self) -> None:
        db = fake_db(
            [
                {"_id": "first@example.com", "email": "first@example.com"},
                {"_id": "second@example.com", "email": "second@example.com"},
            ]
        )
        payload = OperationsSitePermissionsUpdate(
            users=[
                OperationsSitePermissionEntry(user_id="first@example.com", operations_site_ids=["aiwelink"]),
                OperationsSitePermissionEntry(user_id="missing@example.com", operations_site_ids=[]),
            ]
        )

        with self.assertRaises(site_permissions.OperationsSitePermissionsValidationError):
            await site_permissions.update_operations_site_permissions(db, payload=payload)

        db.users.bulk_write.assert_not_awaited()

    async def test_concurrent_user_deletion_rolls_back_all_permission_updates(self) -> None:
        documents = [
            {"_id": "first@example.com", "email": "first@example.com", "operations_site_ids": ["aiwelink"]},
            {"_id": "second@example.com", "email": "second@example.com", "operations_site_ids": ["aigclink"]},
        ]
        db = fake_db(documents)
        db.users.matched_count = 1
        payload = OperationsSitePermissionsUpdate(
            users=[
                OperationsSitePermissionEntry(user_id="first@example.com", operations_site_ids=["aigclink"]),
                OperationsSitePermissionEntry(user_id="second@example.com", operations_site_ids=["aiwelink"]),
            ]
        )

        with self.assertRaises(site_permissions.OperationsSitePermissionsConflictError):
            await site_permissions.update_operations_site_permissions(db, payload=payload)

        self.assertEqual(documents[0]["operations_site_ids"], ["aiwelink"])
        self.assertEqual(documents[1]["operations_site_ids"], ["aigclink"])

    async def test_standalone_mongo_rolls_back_partial_permission_updates(self) -> None:
        documents = [
            {"_id": "first@example.com", "email": "first@example.com", "operations_site_ids": ["aiwelink"]},
            {"_id": "second@example.com", "email": "second@example.com", "operations_site_ids": ["aigclink"]},
        ]
        db = fake_db(documents)
        db.client.start_session.side_effect = OperationFailure(
            "Transaction numbers are only allowed on a replica set member or mongos",
            code=20,
        )
        db.users.matched_count = 1
        payload = OperationsSitePermissionsUpdate(
            users=[
                OperationsSitePermissionEntry(user_id="first@example.com", operations_site_ids=["aigclink"]),
                OperationsSitePermissionEntry(user_id="second@example.com", operations_site_ids=["aiwelink"]),
            ]
        )

        with self.assertRaises(site_permissions.OperationsSitePermissionsConflictError):
            await site_permissions.update_operations_site_permissions(db, payload=payload)

        self.assertEqual(documents[0]["operations_site_ids"], ["aiwelink"])
        self.assertEqual(documents[1]["operations_site_ids"], ["aigclink"])
        self.assertEqual(db.users.bulk_write.await_count, 2)

    async def test_successful_put_persists_canonical_mapping_and_writes_audit(self) -> None:
        documents = [
            {"_id": "first@example.com", "email": "first@example.com", "name": "First", "role": "owner", "status": "active"},
            {"_id": "second@example.com", "email": "second@example.com", "name": "Second", "role": "operator", "status": "active"},
        ]
        db = fake_db(documents)
        payload = OperationsSitePermissionsUpdate(
            users=[
                OperationsSitePermissionEntry(user_id="first@example.com", operations_site_ids=["aigclink", "aiwelink", "aigclink"]),
                OperationsSitePermissionEntry(user_id="second@example.com", operations_site_ids=[]),
            ]
        )

        result = await site_permissions.update_operations_site_permissions(db, payload=payload)

        self.assertEqual(result["users"][0]["operations_site_ids"], ["aiwelink", "aigclink"])
        self.assertEqual(documents[1]["operations_site_ids"], [])
        db.users.bulk_write.assert_awaited_once()
        self.assertTrue(db.users.bulk_write.await_args.kwargs["ordered"])

        audit_mock = AsyncMock()
        with (
            patch.object(settings_router, "get_operations_site_permissions", AsyncMock(return_value={"users": []}), create=True),
            patch.object(settings_router, "update_operations_site_permissions", AsyncMock(return_value=result), create=True),
            patch.object(settings_router, "write_audit_log", audit_mock),
        ):
            response = await settings_router.put_operations_site_permissions(
                payload,
                actor={"_id": "owner@example.com", "role": "owner"},
                db=MagicMock(),
            )

        self.assertEqual(response, result)
        self.assertEqual(audit_mock.await_args.kwargs["action"], "settings.operations_site_permissions.update")
        self.assertEqual(audit_mock.await_args.kwargs["resource_type"], "setting")
        self.assertEqual(audit_mock.await_args.kwargs["resource_id"], "operations_site_permissions")
        self.assertEqual(audit_mock.await_args.kwargs["before"], {"users": []})
        self.assertEqual(audit_mock.await_args.kwargs["after"], result)

    async def test_settings_routes_reject_non_owner_admin(self) -> None:
        db = SimpleNamespace(app_settings=SimpleNamespace(find_one=AsyncMock(return_value=None)))
        routes = [
            route
            for route in settings_router.router.routes
            if route.path == "/settings/operations-site-permissions"
        ]

        for route in routes:
            with self.subTest(methods=route.methods):
                dependency = route.dependant.dependencies[0].call
                with self.assertRaises(HTTPException) as raised:
                    await dependency(user={"_id": "operator@example.com", "role": "operator"}, db=db)
                self.assertEqual(raised.exception.status_code, 403)

    async def test_auth_response_always_exposes_normalized_site_permissions(self) -> None:
        user = {
            "_id": "operator@example.com",
            "email": "operator@example.com",
            "role": "operator",
            "password_hash": "secret",
            "operations_site_ids": ["aigclink", "unknown", "aiwelink", "aigclink"],
        }
        with patch.object(auth_router, "permissions_for_user", AsyncMock(return_value={"allowed_views": [], "default_view": None})):
            result = await auth_router.user_with_permissions(MagicMock(), user)

        self.assertEqual(result["operations_site_ids"], ["aiwelink", "aigclink"])
        self.assertNotIn("password_hash", result)


if __name__ == "__main__":
    unittest.main()
