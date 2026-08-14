from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from app.modules.system import bootstrap, database_schema
from app.modules.system.database_schema import normalize_schema_catalog


class DatabaseSchemaNormalizationTests(unittest.TestCase):
    def test_mysql_catalog_accepts_uppercase_driver_mapping_keys(self) -> None:
        result = normalize_schema_catalog(
            database_type="mysql",
            table_rows=[{"SCHEMA_NAME": "newapi", "TABLE_NAME": "logs", "TABLE_TYPE": "BASE TABLE"}],
            column_rows=[
                {
                    "SCHEMA_NAME": "newapi",
                    "TABLE_NAME": "logs",
                    "COLUMN_NAME": "created_at",
                    "ORDINAL_POSITION": 1,
                    "DATA_TYPE": "bigint",
                    "NATIVE_TYPE": "bigint",
                    "IS_NULLABLE": "NO",
                }
            ],
            index_rows=[],
            constraint_rows=[],
        )

        self.assertEqual(result[0]["tables"][0]["name"], "logs")
        self.assertEqual(result[0]["tables"][0]["columns"][0]["name"], "created_at")

    def test_mysql_catalog_rows_are_grouped_without_defaults_or_comments(self) -> None:
        result = normalize_schema_catalog(
            database_type="mysql",
            table_rows=[{"schema_name": "newapi", "table_name": "users", "table_type": "BASE TABLE"}],
            column_rows=[
                {
                    "schema_name": "newapi",
                    "table_name": "users",
                    "column_name": "id",
                    "ordinal_position": 1,
                    "data_type": "bigint",
                    "native_type": "bigint unsigned",
                    "is_nullable": "NO",
                    "column_default": "sensitive-default",
                    "column_comment": "private-comment",
                },
                {
                    "schema_name": "newapi",
                    "table_name": "users",
                    "column_name": "username",
                    "ordinal_position": 2,
                    "data_type": "varchar",
                    "native_type": "varchar(64)",
                    "is_nullable": "YES",
                },
            ],
            index_rows=[
                {
                    "schema_name": "newapi",
                    "table_name": "users",
                    "index_name": "PRIMARY",
                    "is_unique": True,
                    "index_method": "BTREE",
                    "column_name": "id",
                    "ordinal_position": 1,
                }
            ],
            constraint_rows=[
                {
                    "schema_name": "newapi",
                    "table_name": "users",
                    "constraint_name": "PRIMARY",
                    "constraint_type": "PRIMARY KEY",
                    "column_name": "id",
                    "ordinal_position": 1,
                    "referenced_schema": None,
                    "referenced_table": None,
                    "referenced_column": None,
                }
            ],
        )

        table = result[0]["tables"][0]
        self.assertEqual(result[0]["name"], "newapi")
        self.assertEqual(table["primary_key"], ["id"])
        self.assertEqual(table["indexes"][0]["columns"], ["id"])
        self.assertFalse(table["columns"][0]["nullable"])
        self.assertNotIn("column_default", str(result))
        self.assertNotIn("private-comment", str(result))


class FakeResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self, results: list[list[dict[str, object]]], executed: list[tuple[str, dict[str, object]]]) -> None:
        self.results = list(results)
        self.executed = executed

    async def execute(self, statement, parameters=None):
        self.executed.append((str(statement), dict(parameters or {})))
        return FakeResult(self.results.pop(0))


class AsyncContext:
    def __init__(self, value=None, error: Exception | None = None) -> None:
        self.value = value
        self.error = error

    async def __aenter__(self):
        if self.error:
            raise self.error
        return self.value

    async def __aexit__(self, *_args):
        return False


class FakeEngine:
    def __init__(self, connection: FakeConnection | None = None, error: Exception | None = None) -> None:
        self.connection = connection
        self.error = error
        self.dispose = AsyncMock()

    def connect(self):
        return AsyncContext(self.connection, self.error)


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class DatabaseSchemaInspectionTests(unittest.IsolatedAsyncioTestCase):
    def test_postgresql_foreign_key_query_aligns_composite_columns_by_position(self) -> None:
        constraint_query = database_schema.POSTGRESQL_CATALOG_QUERIES[3].lower()

        self.assertIn("position_in_unique_constraint", constraint_query)
        self.assertIn("key_column_usage rcu", constraint_query)

    async def test_mysql_scan_executes_only_parameterized_catalog_queries_and_disposes_engine(self) -> None:
        executed: list[tuple[str, dict[str, object]]] = []
        connection = FakeConnection(
            [
                [{"schema_name": "newapi", "table_name": "users", "table_type": "BASE TABLE"}],
                [{"schema_name": "newapi", "table_name": "users", "column_name": "id", "ordinal_position": 1, "data_type": "bigint", "native_type": "bigint", "is_nullable": "NO"}],
                [],
                [],
            ],
            executed,
        )
        engine = FakeEngine(connection)
        engine_factory = lambda *_args, **_kwargs: engine

        result = await database_schema.inspect_database_schema(
            {
                "_id": "newapi-us01",
                "client_type": "newapi",
                "sql_dsn": "reader:secret@tcp(mysql.internal:3306)/newapi",
            },
            site_scope="client",
            engine_factory=engine_factory,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["database_endpoint"], "mysql.internal:3306/newapi")
        self.assertNotIn("secret", str(result))
        self.assertEqual(len(executed), 4)
        for sql, parameters in executed:
            self.assertIn("information_schema", sql.lower())
            self.assertNotIn("FROM users", sql)
            self.assertEqual(parameters, {"database_name": "newapi"})
        engine.dispose.assert_awaited_once()

    async def test_failed_connection_still_disposes_engine(self) -> None:
        engine = FakeEngine(error=RuntimeError("connection refused"))

        with self.assertRaisesRegex(RuntimeError, "connection refused"):
            await database_schema.inspect_database_schema(
                {
                    "_id": "sub2-us01",
                    "client_type": "sub2api",
                    "sql_dsn": "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
                },
                site_scope="client",
                engine_factory=lambda *_args, **_kwargs: engine,
            )

        engine.dispose.assert_awaited_once()

    async def test_all_site_scan_isolates_failures_and_redacts_passwords(self) -> None:
        account_pool_site = {
            "_id": "api-5001",
            "sql_dsn": "host=postgres.pool user=reader password=pool-secret dbname=sub2api sslmode=disable",
        }
        client_site = {
            "_id": "newapi-us01",
            "client_type": "newapi",
            "sql_dsn": "reader:client-secret@tcp(mysql.client:3306)/newapi",
        }
        db = SimpleNamespace(
            sub2api_sites=SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([account_pool_site])),
            client_sites=SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([client_site])),
        )

        async def inspect(site, *, site_scope, engine_factory):
            del engine_factory
            if site_scope == "account_pool":
                raise RuntimeError("pool-secret connection failed")
            return {
                "ok": True,
                "site_id": site["_id"],
                "site_scope": site_scope,
                "client_type": site["client_type"],
                "database_type": "mysql",
                "database_endpoint": "mysql.client:3306/newapi",
                "schemas": [],
            }

        with patch.object(database_schema, "inspect_database_schema", side_effect=inspect):
            result = await database_schema.inspect_all_configured_database_schemas(db, engine_factory=object())

        self.assertEqual(result["summary"], {"sites": 2, "succeeded": 1, "failed": 1})
        failed = next(item for item in result["sites"] if item["ok"] is False)
        self.assertEqual(failed["site_id"], "api-5001")
        self.assertNotIn("pool-secret", str(result))
        self.assertIn("***", failed["error"])

    def test_postgresql_catalog_preserves_schemas_and_foreign_keys(self) -> None:
        result = normalize_schema_catalog(
            database_type="postgresql",
            table_rows=[
                {"schema_name": "public", "table_name": "accounts", "table_type": "BASE TABLE"},
                {"schema_name": "audit", "table_name": "events", "table_type": "BASE TABLE"},
            ],
            column_rows=[
                {
                    "schema_name": "public",
                    "table_name": "accounts",
                    "column_name": "id",
                    "ordinal_position": 1,
                    "data_type": "bigint",
                    "native_type": "int8",
                    "is_nullable": "NO",
                },
                {
                    "schema_name": "audit",
                    "table_name": "events",
                    "column_name": "account_id",
                    "ordinal_position": 1,
                    "data_type": "bigint",
                    "native_type": "int8",
                    "is_nullable": "NO",
                },
            ],
            index_rows=[
                {
                    "schema_name": "audit",
                    "table_name": "events",
                    "index_name": "events_account_idx",
                    "is_unique": False,
                    "index_method": "btree",
                    "column_name": "account_id",
                    "ordinal_position": 1,
                }
            ],
            constraint_rows=[
                {
                    "schema_name": "public",
                    "table_name": "accounts",
                    "constraint_name": "accounts_pkey",
                    "constraint_type": "PRIMARY KEY",
                    "column_name": "id",
                    "ordinal_position": 1,
                    "referenced_schema": None,
                    "referenced_table": None,
                    "referenced_column": None,
                },
                {
                    "schema_name": "audit",
                    "table_name": "events",
                    "constraint_name": "events_account_fkey",
                    "constraint_type": "FOREIGN KEY",
                    "column_name": "account_id",
                    "ordinal_position": 1,
                    "referenced_schema": "public",
                    "referenced_table": "accounts",
                    "referenced_column": "id",
                },
            ],
        )

        self.assertEqual([schema["name"] for schema in result], ["audit", "public"])
        events = result[0]["tables"][0]
        self.assertEqual(events["indexes"][0]["name"], "events_account_idx")
        self.assertEqual(
            events["foreign_keys"][0],
            {
                "name": "events_account_fkey",
                "columns": ["account_id"],
                "referenced_schema": "public",
                "referenced_table": "accounts",
                "referenced_columns": ["id"],
            },
        )


class MongoBootstrapIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_work_plan_indexes_cover_idempotency_schedule_history_and_cancellation(self) -> None:
        collections: dict[str, SimpleNamespace] = {}

        class IndexDatabase:
            def __getattr__(self, name: str) -> SimpleNamespace:
                if name not in collections:
                    collections[name] = SimpleNamespace(
                        create_index=AsyncMock(),
                        index_information=AsyncMock(return_value={}),
                        drop_index=AsyncMock(),
                        delete_many=AsyncMock(),
                    )
                return collections[name]

        db = IndexDatabase()

        await bootstrap.ensure_indexes(db)

        db.work_plans.create_index.assert_has_awaits(
            [
                call(
                    [("member_id", 1), ("idempotency_key", 1), ("plan_date", 1)],
                    unique=True,
                ),
                call([("plan_date", 1), ("member_id", 1), ("created_at", -1)]),
                call([("member_id", 1), ("plan_date", -1), ("created_at", -1)]),
                call([("is_cancelled", 1), ("plan_date", 1)]),
            ]
        )


if __name__ == "__main__":
    unittest.main()
