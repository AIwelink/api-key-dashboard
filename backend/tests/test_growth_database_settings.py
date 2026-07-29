from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.modules.system import growth_database_settings
from app.routers import settings as settings_router
from app.schemas import GrowthDatabaseSettingsUpdate


DSN = "host=growth.internal port=5432 user=growth_app password=topsecret dbname=aiwelink_growth sslmode=disable"


def fake_db(document: dict | None):
    collection = SimpleNamespace(
        find_one=AsyncMock(return_value=document),
        update_one=AsyncMock(),
    )
    return SimpleNamespace(app_settings=collection), collection


class GrowthDatabaseSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_settings_return_public_defaults(self) -> None:
        db, _ = fake_db(None)

        result = await growth_database_settings.get_growth_database_settings(db)

        self.assertEqual(result["database_type"], "postgresql")
        self.assertFalse(result["sql_dsn_configured"])
        self.assertEqual(result["database_endpoint"], "")
        self.assertIsNone(result["last_database_test_ok"])
        self.assertNotIn("sql_dsn", result)

    async def test_valid_dsn_is_saved_but_never_returned(self) -> None:
        db, collection = fake_db(None)

        result = await growth_database_settings.update_growth_database_settings(
            db,
            sql_dsn=DSN,
            actor={"_id": "admin@example.com"},
        )

        updates = collection.update_one.await_args.args[1]["$set"]
        self.assertEqual(updates["sql_dsn"], DSN)
        self.assertEqual(updates["database_endpoint"], "growth.internal:5432/aiwelink_growth")
        self.assertTrue(result["sql_dsn_configured"])
        self.assertEqual(result["database_endpoint"], "growth.internal:5432/aiwelink_growth")
        self.assertNotIn("sql_dsn", result)
        self.assertNotIn("growth_app", str(result))
        self.assertNotIn("topsecret", str(result))

    async def test_blank_update_preserves_existing_secret(self) -> None:
        current = {
            "_id": "growth_database",
            "database_type": "postgresql",
            "sql_dsn": DSN,
            "database_endpoint": "growth.internal:5432/aiwelink_growth",
        }
        db, collection = fake_db(current)

        result = await growth_database_settings.update_growth_database_settings(
            db,
            sql_dsn="",
            actor={"_id": "admin@example.com"},
        )

        updates = collection.update_one.await_args.args[1]["$set"]
        self.assertNotIn("sql_dsn", updates)
        self.assertTrue(result["sql_dsn_configured"])
        self.assertNotIn("topsecret", str(result))

    async def test_invalid_or_missing_dsn_is_rejected(self) -> None:
        unconfigured_db, _ = fake_db(None)
        with self.assertRaisesRegex(ValueError, "required"):
            await growth_database_settings.update_growth_database_settings(
                unconfigured_db,
                sql_dsn="",
                actor={"_id": "admin@example.com"},
            )

        invalid_db, _ = fake_db(None)
        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            await growth_database_settings.update_growth_database_settings(
                invalid_db,
                sql_dsn="reader:secret@tcp(mysql.internal:3306)/newapi",
                actor={"_id": "admin@example.com"},
            )

    async def test_database_test_persists_success_and_returns_public_settings(self) -> None:
        current = {
            "_id": "growth_database",
            "database_type": "postgresql",
            "sql_dsn": DSN,
            "database_endpoint": "growth.internal:5432/aiwelink_growth",
        }
        db, collection = fake_db(current)
        probe_result = {
            "ok": True,
            "database_type": "postgresql",
            "database_endpoint": "growth.internal:5432/aiwelink_growth",
            "latency_ms": 18.4,
            "server_version": "PostgreSQL 17.5",
            "tested_at": "2026-07-21T00:00:00Z",
        }

        with patch.object(
            growth_database_settings,
            "probe_sql_database_connection",
            AsyncMock(return_value=probe_result),
        ):
            result = await growth_database_settings.run_growth_database_test(db)

        updates = collection.update_one.await_args.args[1]["$set"]
        self.assertTrue(updates["last_database_test_ok"])
        self.assertEqual(updates["last_database_version"], "PostgreSQL 17.5")
        self.assertEqual(result["settings"]["last_database_latency_ms"], 18.4)
        self.assertNotIn("sql_dsn", result["settings"])
        self.assertNotIn("topsecret", str(result))

    async def test_database_test_persists_redacted_failure(self) -> None:
        current = {
            "_id": "growth_database",
            "database_type": "postgresql",
            "sql_dsn": DSN,
            "database_endpoint": "growth.internal:5432/aiwelink_growth",
        }
        db, collection = fake_db(current)
        probe_result = {
            "ok": False,
            "database_type": "postgresql",
            "database_endpoint": "growth.internal:5432/aiwelink_growth",
            "latency_ms": 21.2,
            "error": "authentication failed",
            "tested_at": "2026-07-21T00:00:00Z",
        }

        with patch.object(
            growth_database_settings,
            "probe_sql_database_connection",
            AsyncMock(return_value=probe_result),
        ):
            result = await growth_database_settings.run_growth_database_test(db)

        updates = collection.update_one.await_args.args[1]["$set"]
        self.assertFalse(updates["last_database_test_ok"])
        self.assertEqual(updates["last_database_test_error"], "authentication failed")
        self.assertEqual(result["settings"]["last_database_test_error"], "authentication failed")
        self.assertNotIn("growth_app", str(result))
        self.assertNotIn("topsecret", str(result))

    async def test_database_test_requires_configured_dsn(self) -> None:
        db, collection = fake_db(None)

        with self.assertRaisesRegex(ValueError, "not configured"):
            await growth_database_settings.run_growth_database_test(db)

        collection.update_one.assert_not_awaited()


class GrowthDatabaseSettingsRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_put_route_returns_public_settings_and_writes_safe_audit(self) -> None:
        before = {
            "database_type": "postgresql",
            "sql_dsn_configured": False,
            "database_endpoint": "",
        }
        updated = {
            "database_type": "postgresql",
            "sql_dsn_configured": True,
            "database_endpoint": "growth.internal:5432/aiwelink_growth",
        }
        get_mock = AsyncMock(return_value=before)
        update_mock = AsyncMock(return_value=updated)
        audit_mock = AsyncMock()

        with (
            patch.object(settings_router, "get_growth_database_settings", get_mock, create=True),
            patch.object(settings_router, "update_growth_database_settings", update_mock, create=True),
            patch.object(settings_router, "write_audit_log", audit_mock),
        ):
            response = await settings_router.put_growth_database_settings(
                GrowthDatabaseSettingsUpdate(sql_dsn=DSN),
                actor={"_id": "admin@example.com", "role": "admin"},
                db=MagicMock(),
            )

        self.assertEqual(response, updated)
        self.assertNotIn("sql_dsn", response)
        self.assertNotIn("growth_app", str(audit_mock.await_args.kwargs))
        self.assertNotIn("topsecret", str(audit_mock.await_args.kwargs))

    async def test_put_route_maps_validation_error_to_http_400(self) -> None:
        with (
            patch.object(
                settings_router,
                "get_growth_database_settings",
                AsyncMock(return_value={"sql_dsn_configured": False}),
                create=True,
            ),
            patch.object(
                settings_router,
                "update_growth_database_settings",
                AsyncMock(side_effect=ValueError("PostgreSQL SQL_DSN is required")),
                create=True,
            ),
        ):
            with self.assertRaises(HTTPException) as caught:
                await settings_router.put_growth_database_settings(
                    GrowthDatabaseSettingsUpdate(sql_dsn=""),
                    actor={"_id": "admin@example.com", "role": "admin"},
                    db=MagicMock(),
                )

        self.assertEqual(caught.exception.status_code, 400)

    async def test_test_route_returns_failed_probe_and_writes_safe_audit(self) -> None:
        result = {
            "ok": False,
            "database_type": "postgresql",
            "database_endpoint": "growth.internal:5432/aiwelink_growth",
            "latency_ms": 21.2,
            "error": "authentication failed",
            "tested_at": "2026-07-21T00:00:00Z",
            "settings": {
                "database_type": "postgresql",
                "sql_dsn_configured": True,
                "database_endpoint": "growth.internal:5432/aiwelink_growth",
            },
        }
        run_mock = AsyncMock(return_value=result)
        audit_mock = AsyncMock()

        with (
            patch.object(settings_router, "run_growth_database_test", run_mock, create=True),
            patch.object(settings_router, "write_audit_log", audit_mock),
        ):
            response = await settings_router.post_growth_database_test(
                actor={"_id": "owner@example.com", "role": "owner"},
                db=MagicMock(),
            )

        self.assertFalse(response["ok"])
        audit_after = audit_mock.await_args.kwargs["after"]
        self.assertFalse(audit_after["ok"])
        self.assertNotIn("settings", audit_after)
        self.assertNotIn("growth_app", str(audit_after))
        self.assertNotIn("topsecret", str(audit_after))


if __name__ == "__main__":
    unittest.main()
