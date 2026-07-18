from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.routers import sub2api_sites as sub2api_sites_router
from app.modules.sub2api.site_database import run_sub2api_site_database_test


class FakeResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class FakeConnectionContext:
    def __init__(self, connection: object) -> None:
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_args) -> None:
        return None


class FakeEngine:
    def __init__(self, connection: object) -> None:
        self.connection = connection
        self.dispose = AsyncMock()

    def connect(self) -> FakeConnectionContext:
        return FakeConnectionContext(self.connection)


class Sub2ApiSiteDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_database_test_uses_postgresql_and_persists_result(self) -> None:
        site = {
            "_id": "api-5001",
            "site_type": "sub2api",
            "sql_dsn": "host=postgres.internal port=5432 user=reader password=secret dbname=sub2api sslmode=disable",
            "status": "active",
        }
        collection = MagicMock()
        collection.find_one = AsyncMock(return_value=site)
        collection.update_one = AsyncMock()
        connection = MagicMock()
        connection.execute = AsyncMock(side_effect=[FakeResult(1), FakeResult("PostgreSQL 17")])
        engine = FakeEngine(connection)

        result = await run_sub2api_site_database_test(
            MagicMock(sub2api_sites=collection),
            "api-5001",
            engine_factory=MagicMock(return_value=engine),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["database_type"], "postgresql")
        self.assertEqual(result["server_version"], "PostgreSQL 17")
        updates = collection.update_one.await_args.args[1]["$set"]
        self.assertTrue(updates["last_database_test_ok"])
        self.assertNotIn("sql_dsn", result)

    async def test_run_database_test_requires_site_and_sql_dsn(self) -> None:
        missing = MagicMock()
        missing.find_one = AsyncMock(return_value=None)
        with self.assertRaisesRegex(LookupError, "not found"):
            await run_sub2api_site_database_test(MagicMock(sub2api_sites=missing), "missing")

        no_dsn = MagicMock()
        no_dsn.find_one = AsyncMock(return_value={"_id": "api-5001", "site_type": "sub2api"})
        with self.assertRaisesRegex(ValueError, "SQL_DSN"):
            await run_sub2api_site_database_test(MagicMock(sub2api_sites=no_dsn), "api-5001")

    async def test_router_runs_account_pool_database_test(self) -> None:
        result = {
            "ok": True,
            "database_type": "postgresql",
            "database_endpoint": "postgres.internal:5432/sub2api",
            "latency_ms": 8.0,
            "server_version": "PostgreSQL 17",
            "tested_at": "2026-07-19T00:00:00Z",
        }
        run_mock = AsyncMock(return_value=result)
        audit_mock = AsyncMock()
        with (
            patch.object(sub2api_sites_router, "run_sub2api_site_database_test", run_mock, create=True),
            patch.object(sub2api_sites_router, "write_audit_log", audit_mock),
        ):
            response = await sub2api_sites_router.test_site_database(
                "api-5001",
                actor={"_id": "admin@example.com", "role": "admin"},
                db=MagicMock(),
            )

        self.assertEqual(response, result)
        self.assertNotIn("sql_dsn", audit_mock.await_args.kwargs["after"])


if __name__ == "__main__":
    unittest.main()
