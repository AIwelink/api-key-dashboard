from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.modules.auto_replenishment import service, settings
from app.modules.auto_replenishment.sogouedu import SogouEduError
from app.routers import auto_replenishment as routes
from app.schemas import AutoReplenishmentSettingsUpdate


TEST_SECRET_KEY = "test-only-application-secret"


class AutoReplenishmentServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_test_decrypts_credentials_and_persists_only_public_result(self) -> None:
        current = {
            **settings.DEFAULT_SETTINGS,
            "_id": settings.SETTINGS_ID,
            "username": "buyer",
            "password_ciphertext": settings.encrypt_secret("supplier-password", TEST_SECRET_KEY),
            "target_group_id": 3,
        }
        collection = SimpleNamespace(
            find_one=AsyncMock(return_value=current),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(auto_replenishment_settings=collection)
        client = SimpleNamespace(
            test_connection=AsyncMock(
                return_value={
                    "ok": True,
                    "tested_at": datetime(2026, 8, 1, tzinfo=UTC),
                    "balance": {"available_fen": 7_200},
                    "inventory": {"available": 18},
                }
            )
        )

        result = await service.test_supplier_connection(
            db,
            actor={"_id": "owner@example.com", "name": "Owner"},
            client=client,
            secret_key=TEST_SECRET_KEY,
        )

        client.test_connection.assert_awaited_once_with(
            username="buyer",
            password="supplier-password",
            product="oauth_7d",
        )
        stored = collection.update_one.await_args.args[1]["$set"]
        self.assertEqual(stored["last_test_balance"], {"available_fen": 7_200})
        self.assertEqual(stored["last_test_inventory"], {"available": 18})
        self.assertNotIn("password", str(stored).lower())
        self.assertNotIn("token", str(stored).lower())
        self.assertNotIn("supplier-password", str(result))

    async def test_connection_failure_is_sanitized_and_persisted(self) -> None:
        current = {
            **settings.DEFAULT_SETTINGS,
            "_id": settings.SETTINGS_ID,
            "username": "buyer",
            "password_ciphertext": settings.encrypt_secret("supplier-password", TEST_SECRET_KEY),
        }
        collection = SimpleNamespace(find_one=AsyncMock(return_value=current), update_one=AsyncMock())
        db = SimpleNamespace(auto_replenishment_settings=collection)
        client = SimpleNamespace(test_connection=AsyncMock(side_effect=SogouEduError("SogouEdu credentials are invalid", status_code=401)))

        result = await service.test_supplier_connection(
            db,
            actor={"_id": "owner@example.com"},
            client=client,
            secret_key=TEST_SECRET_KEY,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "SogouEdu credentials are invalid")
        stored = collection.update_one.await_args.args[1]["$set"]
        self.assertFalse(stored["last_test_ok"])
        self.assertEqual(stored["last_test_error"], result["error"])
        self.assertNotIn("supplier-password", str(stored))


class AutoReplenishmentRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_save_audits_only_public_settings(self) -> None:
        payload = AutoReplenishmentSettingsUpdate(
            username="buyer",
            password="supplier-password",
            enabled=True,
            minimum_account_count=2,
            minimum_runway_minutes=5,
        )
        public = {
            **settings.DEFAULT_SETTINGS,
            "password_configured": True,
            "target_group_id": 3,
        }
        actor = {"_id": "owner@example.com", "name": "Owner"}
        with (
            patch.object(routes, "save_auto_replenishment_settings", AsyncMock(return_value=public)),
            patch.object(routes, "write_audit_log", AsyncMock()) as audit,
        ):
            result = await routes.put_auto_replenishment_settings(payload, actor=actor, db=SimpleNamespace())

        self.assertEqual(result, public)
        audit_payload = audit.await_args.kwargs["after"]
        self.assertNotIn("password", str(audit_payload).lower().replace("password_configured", ""))
        self.assertNotIn("supplier-password", str(audit.await_args))

    async def test_save_maps_missing_target_to_404(self) -> None:
        payload = AutoReplenishmentSettingsUpdate(username="buyer", password="secret")
        with patch.object(
            routes,
            "save_auto_replenishment_settings",
            AsyncMock(side_effect=LookupError("target sub2api group not found")),
        ):
            with self.assertRaises(HTTPException) as raised:
                await routes.put_auto_replenishment_settings(payload, actor={"_id": "owner"}, db=SimpleNamespace())

        self.assertEqual(raised.exception.status_code, 404)

    async def test_failed_connection_test_is_audited_and_returns_502(self) -> None:
        result = {"ok": False, "tested_at": "2026-08-01T00:00:00Z", "error": "SogouEdu credentials are invalid"}
        with (
            patch.object(routes, "test_supplier_connection", AsyncMock(return_value=result)),
            patch.object(routes, "write_audit_log", AsyncMock()) as audit,
        ):
            with self.assertRaises(HTTPException) as raised:
                await routes.post_auto_replenishment_test(actor={"_id": "owner"}, db=SimpleNamespace())

        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(audit.await_args.kwargs["after"], result)
        self.assertNotIn("token", str(audit.await_args).lower())


if __name__ == "__main__":
    unittest.main()
