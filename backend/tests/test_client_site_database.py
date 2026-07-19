from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.routers import client_sites as client_sites_router
from app.modules.system.client_site_database import (
    driver_database_url,
    probe_database_connection,
    run_client_site_database_test,
)
from app.modules.system.sql_dsn import parse_sql_dsn, redact_sql_error


class FakeResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class FakeConnectionContext:
    def __init__(self, connection: object | None = None, error: Exception | None = None) -> None:
        self.connection = connection
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self.connection

    async def __aexit__(self, *_args) -> None:
        return None


class FakeEngine:
    def __init__(self, connection_context: FakeConnectionContext) -> None:
        self.connection_context = connection_context
        self.dispose = AsyncMock()

    def connect(self) -> FakeConnectionContext:
        return self.connection_context


class ClientSiteDatabaseTests(unittest.IsolatedAsyncioTestCase):
    def test_sql_error_redaction_removes_url_encoded_password(self) -> None:
        dsn = "host=postgres.internal user=reader password=pa@ss dbname=sub2api sslmode=disable"
        driver_url = parse_sql_dsn(dsn, "postgresql").driver_url()

        result = redact_sql_error(RuntimeError(f"failed to connect to {driver_url}"), dsn, "postgresql")

        self.assertNotIn("pa@ss", result)
        self.assertNotIn("pa%40ss", result)
        self.assertNotIn(driver_url, result)

    def test_driver_database_url_uses_fixed_async_driver(self) -> None:
        self.assertEqual(
            driver_database_url("reader:secret@tcp(mysql.internal:3306)/newapi", "newapi"),
            "mysql+aiomysql://reader:secret@mysql.internal/newapi",
        )
        self.assertEqual(
            driver_database_url(
                "host=postgres.internal port=5432 user=reader password=secret dbname=sub2api sslmode=disable",
                "sub2api",
            ),
            "postgresql+asyncpg://reader:secret@postgres.internal/sub2api",
        )

    def test_driver_database_url_auto_detects_database_env_block(self) -> None:
        mysql_env = """DATABASE_HOST=mysql.internal
DATABASE_PORT=3307
DATABASE_DBNAME=newapi
DATABASE_USER=reader
DATABASE_PASSWORD=secret@word"""
        postgres_env = """DATABASE_HOST=postgres.internal
DATABASE_PORT=
DATABASE_DBNAME=sub2api
DATABASE_USER=reader
DATABASE_PASSWORD='secret word'"""

        self.assertEqual(
            driver_database_url(mysql_env, "newapi"),
            "mysql+aiomysql://reader:secret%40word@mysql.internal:3307/newapi",
        )
        self.assertEqual(
            driver_database_url(postgres_env, "sub2api"),
            "postgresql+asyncpg://reader:secret word@postgres.internal/sub2api",
        )

    def test_database_env_block_requires_connection_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "DATABASE_PASSWORD"):
            driver_database_url(
                "DATABASE_HOST=postgres.internal\nDATABASE_DBNAME=sub2api\nDATABASE_USER=reader",
                "sub2api",
            )

    async def test_database_connection_executes_queries_and_disposes_engine(self) -> None:
        connection = MagicMock()
        connection.execute = AsyncMock(side_effect=[FakeResult(1), FakeResult("MySQL 8.4")])
        engine = FakeEngine(FakeConnectionContext(connection=connection))
        engine_factory = MagicMock(return_value=engine)
        site = {
            "id": "customer-newapi-us01",
            "client_type": "newapi",
            "sql_dsn": "reader:secret@tcp(mysql.internal:3307)/newapi",
        }

        result = await probe_database_connection(site, engine_factory=engine_factory)

        self.assertTrue(result["ok"])
        self.assertEqual(result["database_type"], "mysql")
        self.assertEqual(result["database_endpoint"], "mysql.internal:3307/newapi")
        self.assertEqual(result["server_version"], "MySQL 8.4")
        self.assertGreaterEqual(result["latency_ms"], 0)
        self.assertEqual(connection.execute.await_count, 2)
        engine.dispose.assert_awaited_once()
        driver_url = engine_factory.call_args.args[0]
        self.assertTrue(driver_url.startswith("mysql+aiomysql://"))

    async def test_database_connection_redacts_credentials_on_failure(self) -> None:
        dsn = "host=postgres.internal port=5432 user=reader password=topsecret dbname=sub2api sslmode=disable"
        engine = FakeEngine(FakeConnectionContext(error=RuntimeError(f"failed to connect using {dsn}")))
        engine_factory = MagicMock(return_value=engine)
        site = {
            "id": "customer-sub2api-us01",
            "client_type": "sub2api",
            "sql_dsn": dsn,
        }

        result = await probe_database_connection(site, engine_factory=engine_factory)

        self.assertFalse(result["ok"])
        self.assertIn("failed to connect", result["error"])
        self.assertNotIn("reader", result["error"])
        self.assertNotIn("topsecret", result["error"])
        self.assertNotIn(dsn, result["error"])
        engine.dispose.assert_awaited_once()

    async def test_database_connection_handles_driver_initialization_failure(self) -> None:
        dsn = "reader:topsecret@tcp(mysql.internal:3306)/newapi"
        engine_factory = MagicMock(side_effect=RuntimeError(f"driver failed for {dsn}"))

        result = await probe_database_connection(
            {"id": "customer-newapi-us01", "client_type": "newapi", "sql_dsn": dsn},
            engine_factory=engine_factory,
        )

        self.assertFalse(result["ok"])
        self.assertIn("driver failed", result["error"])
        self.assertNotIn("reader", result["error"])
        self.assertNotIn("topsecret", result["error"])

    async def test_run_database_test_persists_success_result(self) -> None:
        site = {
            "_id": "customer-newapi-us01",
            "client_type": "newapi",
            "sql_dsn": "reader:secret@tcp(mysql.internal:3306)/newapi",
            "status": "active",
        }
        collection = MagicMock()
        collection.find_one = AsyncMock(return_value=site)
        collection.update_one = AsyncMock()
        db = MagicMock(client_sites=collection)
        connection = MagicMock()
        connection.execute = AsyncMock(side_effect=[FakeResult(1), FakeResult("MySQL 8.4")])
        engine = FakeEngine(FakeConnectionContext(connection=connection))

        result = await run_client_site_database_test(
            db,
            "customer-newapi-us01",
            engine_factory=MagicMock(return_value=engine),
        )

        self.assertTrue(result["ok"])
        updates = collection.update_one.await_args.args[1]["$set"]
        self.assertTrue(updates["last_database_test_ok"])
        self.assertEqual(updates["last_database_version"], "MySQL 8.4")
        self.assertNotIn("sql_dsn", result)

    async def test_run_database_test_persists_failure_result(self) -> None:
        site = {
            "_id": "customer-sub2api-us01",
            "client_type": "sub2api",
            "sql_dsn": "host=postgres.internal port=5432 user=reader password=secret dbname=sub2api sslmode=disable",
            "status": "active",
        }
        collection = MagicMock()
        collection.find_one = AsyncMock(return_value=site)
        collection.update_one = AsyncMock()
        db = MagicMock(client_sites=collection)
        engine = FakeEngine(FakeConnectionContext(error=RuntimeError("connection refused")))

        result = await run_client_site_database_test(
            db,
            "customer-sub2api-us01",
            engine_factory=MagicMock(return_value=engine),
        )

        self.assertFalse(result["ok"])
        updates = collection.update_one.await_args.args[1]["$set"]
        self.assertFalse(updates["last_database_test_ok"])
        self.assertEqual(updates["last_database_test_error"], "connection refused")
        self.assertEqual(updates["last_database_version"], "")

    async def test_run_database_test_rejects_missing_site_or_dsn(self) -> None:
        missing_collection = MagicMock()
        missing_collection.find_one = AsyncMock(return_value=None)
        with self.assertRaisesRegex(LookupError, "not found"):
            await run_client_site_database_test(
                MagicMock(client_sites=missing_collection),
                "missing",
            )

        no_dsn_collection = MagicMock()
        no_dsn_collection.find_one = AsyncMock(
            return_value={"_id": "customer-newapi-us01", "client_type": "newapi", "status": "active"}
        )
        with self.assertRaisesRegex(ValueError, "not configured"):
            await run_client_site_database_test(
                MagicMock(client_sites=no_dsn_collection),
                "customer-newapi-us01",
            )

    async def test_router_runs_database_test_and_writes_safe_audit(self) -> None:
        result = {
            "ok": True,
            "database_type": "mysql",
            "database_endpoint": "mysql.internal:3306/newapi",
            "latency_ms": 12.5,
            "server_version": "MySQL 8.4",
            "tested_at": "2026-07-19T00:00:00Z",
        }
        run_mock = AsyncMock(return_value=result)
        audit_mock = AsyncMock()

        with (
            patch.object(client_sites_router, "run_client_site_database_test", run_mock, create=True),
            patch.object(client_sites_router, "write_audit_log", audit_mock),
        ):
            response = await client_sites_router.test_site_database(
                "customer-newapi-us01",
                actor={"_id": "admin@example.com", "role": "admin"},
                db=MagicMock(),
            )

        self.assertEqual(response, result)
        run_mock.assert_awaited_once()
        audit_payload = audit_mock.await_args.kwargs["after"]
        self.assertNotIn("sql_dsn", audit_payload)


if __name__ == "__main__":
    unittest.main()
