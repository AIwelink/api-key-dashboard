from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException

from app.modules.system import growth_database_settings
from app.routers import settings as settings_router


DSN = "postgresql://growth_app:topsecret@growth.internal:5432/aiwelink_growth?sslmode=disable"


def fake_db(document: dict | None):
    collection = SimpleNamespace(
        find_one=AsyncMock(return_value=document),
        update_one=AsyncMock(),
    )
    return SimpleNamespace(app_settings=collection), collection


class GrowthSchemaServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_status_requires_configured_database(self) -> None:
        db, _ = fake_db(None)

        with self.assertRaisesRegex(ValueError, "not configured"):
            await growth_database_settings.get_growth_schema_status(db)

    async def test_schema_status_uses_configured_engine_and_disposes_it(self) -> None:
        db, _ = fake_db({"_id": "growth_database", "sql_dsn": DSN})
        engine = SimpleNamespace(dispose=AsyncMock())
        engine_factory = MagicMock(return_value=engine)
        expected = {
            "initialized": True,
            "current_version": "0001_initial",
            "latest_version": "0001_initial",
            "pending_versions": [],
            "domain_table_count": 12,
        }

        with patch.object(
            growth_database_settings,
            "inspect_growth_database",
            AsyncMock(return_value=expected),
            create=True,
        ):
            result = await growth_database_settings.get_growth_schema_status(
                db,
                engine_factory=engine_factory,
            )

        self.assertEqual(result, expected)
        engine.dispose.assert_awaited_once()
        factory_text = str(engine_factory.call_args)
        self.assertNotIn("topsecret", str(result))
        self.assertIn("postgresql+asyncpg", factory_text)

    async def test_initialize_runs_migrations_persists_public_result_and_disposes(self) -> None:
        db, collection = fake_db({"_id": "growth_database", "sql_dsn": DSN})
        engine = SimpleNamespace(dispose=AsyncMock())
        engine_factory = MagicMock(return_value=engine)
        expected = {
            "initialized": True,
            "current_version": "0001_initial",
            "latest_version": "0001_initial",
            "applied_versions": ["0001_initial"],
            "pending_versions": [],
            "domain_table_count": 12,
        }

        with patch.object(
            growth_database_settings,
            "run_growth_migrations",
            AsyncMock(return_value=expected),
            create=True,
        ):
            result = await growth_database_settings.initialize_growth_database(
                db,
                actor={"_id": "admin@example.com"},
                engine_factory=engine_factory,
            )

        self.assertEqual(result, expected)
        updates = collection.update_one.await_args.args[1]["$set"]
        self.assertEqual(updates["last_schema_version"], "0001_initial")
        self.assertEqual(updates["last_schema_initialized_by"], "admin@example.com")
        self.assertNotIn("sql_dsn", updates)
        engine.dispose.assert_awaited_once()


class GrowthSchemaRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_route_writes_safe_audit(self) -> None:
        result = {
            "initialized": True,
            "current_version": "0001_initial",
            "latest_version": "0001_initial",
            "applied_versions": ["0001_initial"],
            "pending_versions": [],
            "domain_table_count": 12,
        }
        initialize_mock = AsyncMock(return_value=result)
        audit_mock = AsyncMock()

        with (
            patch.object(settings_router, "initialize_growth_database", initialize_mock, create=True),
            patch.object(settings_router, "write_audit_log", audit_mock),
        ):
            response = await settings_router.post_growth_database_initialize(
                actor={"_id": "owner@example.com", "role": "owner"},
                db=MagicMock(),
            )

        self.assertEqual(response, result)
        self.assertEqual(audit_mock.await_args.kwargs["after"], result)
        self.assertNotIn("sql_dsn", str(audit_mock.await_args.kwargs))

    async def test_schema_route_maps_missing_config_to_http_400(self) -> None:
        with patch.object(
            settings_router,
            "get_growth_schema_status",
            AsyncMock(side_effect=ValueError("PostgreSQL SQL_DSN is not configured")),
            create=True,
        ):
            with self.assertRaises(HTTPException) as caught:
                await settings_router.get_growth_database_schema(
                    actor={"_id": "admin@example.com", "role": "admin"},
                    db=MagicMock(),
                )

        self.assertEqual(caught.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()
