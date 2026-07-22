from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock


class GrowthMigrationContractTests(unittest.IsolatedAsyncioTestCase):
    def test_initial_migration_contains_every_required_domain_table(self) -> None:
        from app.modules.growth.migrations import INITIAL_MIGRATION, REQUIRED_DOMAIN_TABLES

        sql = "\n".join(INITIAL_MIGRATION.statements)

        self.assertEqual(len(REQUIRED_DOMAIN_TABLES), 12)
        for table_name in REQUIRED_DOMAIN_TABLES:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS growth.{table_name}", sql)
        self.assertIn("PRIMARY KEY (site_id, external_user_id)", sql)
        self.assertIn("UNIQUE (code)", sql)
        self.assertIn("FOREIGN KEY (campaign_id, site_id)", sql)
        self.assertNotIn("password", sql.lower())
        self.assertNotIn("api_key", sql.lower())

    def test_migrations_are_ordered_and_uniquely_versioned(self) -> None:
        from app.modules.growth.migrations import MIGRATIONS

        versions = [migration.version for migration in MIGRATIONS]

        self.assertEqual(versions, sorted(versions))
        self.assertEqual(len(versions), len(set(versions)))
        self.assertEqual(versions, ["0001_initial"])

    async def test_unapplied_migration_executes_and_records_version(self) -> None:
        from app.modules.growth.migrations import INITIAL_MIGRATION, apply_pending_migrations

        connection = _FakeConnection(applied_versions=[])

        result = await apply_pending_migrations(connection)

        self.assertEqual(result["applied_versions"], ["0001_initial"])
        self.assertEqual(result["current_version"], "0001_initial")
        self.assertEqual(result["pending_versions"], [])
        executed_sql = "\n".join(connection.statements)
        for statement in INITIAL_MIGRATION.statements:
            self.assertIn(statement, connection.statements)
        self.assertIn("INSERT INTO growth.schema_migrations", executed_sql)

    async def test_applied_migration_is_not_executed_twice(self) -> None:
        from app.modules.growth.migrations import INITIAL_MIGRATION, apply_pending_migrations

        connection = _FakeConnection(applied_versions=["0001_initial"])

        result = await apply_pending_migrations(connection)

        self.assertEqual(result["applied_versions"], [])
        self.assertEqual(result["current_version"], "0001_initial")
        for statement in INITIAL_MIGRATION.statements:
            self.assertNotIn(statement, connection.statements)

    async def test_schema_status_is_uninitialized_without_migration_ledger(self) -> None:
        from app.modules.growth.migrations import inspect_growth_schema

        connection = _FakeConnection(applied_versions=[], ledger_exists=False)

        result = await inspect_growth_schema(connection)

        self.assertFalse(result["initialized"])
        self.assertIsNone(result["current_version"])
        self.assertEqual(result["pending_versions"], ["0001_initial"])
        self.assertEqual(result["domain_table_count"], 0)

    async def test_schema_status_reports_applied_version_and_table_count(self) -> None:
        from app.modules.growth.migrations import inspect_growth_schema

        connection = _FakeConnection(
            applied_versions=["0001_initial"],
            ledger_exists=True,
            domain_table_count=12,
        )

        result = await inspect_growth_schema(connection)

        self.assertTrue(result["initialized"])
        self.assertEqual(result["current_version"], "0001_initial")
        self.assertEqual(result["pending_versions"], [])
        self.assertEqual(result["domain_table_count"], 12)


class _FakeScalarResult:
    def __init__(self, values: list):
        self._values = values

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._values))

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None


class _FakeConnection:
    def __init__(
        self,
        *,
        applied_versions: list[str],
        ledger_exists: bool = True,
        domain_table_count: int = 12,
    ):
        self.applied_versions = applied_versions
        self.ledger_exists = ledger_exists
        self.domain_table_count = domain_table_count
        self.statements: list[str] = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, statement, parameters=None):
        sql = str(statement)
        self.statements.append(sql)
        if "to_regclass" in sql:
            return _FakeScalarResult(["growth.schema_migrations"] if self.ledger_exists else [])
        if "information_schema.tables" in sql:
            return _FakeScalarResult([self.domain_table_count])
        if "SELECT version" in sql:
            return _FakeScalarResult(self.applied_versions)
        return _FakeScalarResult([])


if __name__ == "__main__":
    unittest.main()
