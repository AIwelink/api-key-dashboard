from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock


class GrowthMigrationContractTests(unittest.IsolatedAsyncioTestCase):
    def test_initial_migration_contains_every_required_domain_table(self) -> None:
        from app.modules.growth.migrations import INITIAL_DOMAIN_TABLES, INITIAL_MIGRATION

        sql = "\n".join(INITIAL_MIGRATION.statements)

        self.assertEqual(len(INITIAL_DOMAIN_TABLES), 12)
        for table_name in INITIAL_DOMAIN_TABLES:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS growth.{table_name}", sql)
        self.assertIn("PRIMARY KEY (site_id, external_user_id)", sql)
        self.assertIn("UNIQUE (code)", sql)
        self.assertIn("FOREIGN KEY (campaign_id, site_id)", sql)
        self.assertNotIn("password", sql.lower())
        self.assertNotIn("api_key", sql.lower())

    def test_operations_migration_contains_cached_analytics_tables(self) -> None:
        from app.modules.growth.migrations import OPERATIONS_DOMAIN_TABLES, OPERATIONS_MIGRATION

        sql = "\n".join(OPERATIONS_MIGRATION.statements)

        self.assertEqual(len(OPERATIONS_DOMAIN_TABLES), 11)
        for table_name in (
            "internal_users",
            "balance_conversion_rates",
            "ops_user_snapshots",
            "credit_events",
            "redemption_batches",
            "balance_adjustment_requests",
            "usage_facts",
            "classification_tasks",
            "ops_hourly_stats",
            "ops_daily_stats",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS growth.{table_name}", sql)
        self.assertIn("balance_units_per_cny > 0", sql)
        self.assertIn("UNIQUE (site_id, source_type, source_record_id)", sql)
        self.assertIn("sync_cursors_stream_name_check", sql)
        self.assertIn("sync_runs_stream_name_check", sql)
        self.assertIn("'operations'", sql)

    def test_internal_email_migration_supports_pending_recognition(self) -> None:
        from app.modules.growth.migrations import INTERNAL_EMAIL_MIGRATION

        sql = "\n".join(INTERNAL_EMAIL_MIGRATION.statements)

        self.assertIn("ADD COLUMN IF NOT EXISTS email TEXT", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS recognized_at TIMESTAMPTZ", sql)
        self.assertIn("ALTER COLUMN external_user_id DROP NOT NULL", sql)
        self.assertIn("HAVING COUNT(*) = 1", sql)
        self.assertIn("growth_internal_users_site_email_unique_idx", sql)

    def test_lifecycle_migration_adds_priced_usage_and_subscription_entitlements(self) -> None:
        from app.modules.growth.migrations import LIFECYCLE_METRICS_MIGRATION

        sql = "\n".join(LIFECYCLE_METRICS_MIGRATION.statements)

        self.assertIn(
            "ADD COLUMN IF NOT EXISTS billed_amount_cny NUMERIC(30, 10)",
            sql,
        )
        self.assertIn("ADD COLUMN IF NOT EXISTS model_name TEXT", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS token_count BIGINT", sql)
        self.assertIn(
            "CREATE TABLE IF NOT EXISTS growth.subscription_entitlements",
            sql,
        )
        self.assertIn("UNIQUE (site_id, source_type, source_record_id)", sql)
        self.assertIn("growth_subscription_entitlements_user_window_idx", sql)

    def test_sale_credit_cash_migration_drops_only_positive_cash_checks(self) -> None:
        from app.modules.growth.migrations import SALE_CREDIT_CASH_MIGRATION

        sql = "\n".join(SALE_CREDIT_CASH_MIGRATION.statements)

        self.assertIn("'redemption_batches', 'balance_adjustment_requests'", sql)
        self.assertIn("namespace.nspname = 'growth'", sql)
        self.assertIn("pg_get_constraintdef", sql)
        self.assertIn("%cash_amount_cny%>%", sql)
        self.assertNotIn("purpose = 'sale' OR cash_amount_cny = 0", sql)

    def test_operations_sync_single_flight_migration_closes_legacy_runs(self) -> None:
        from app.modules.growth.migrations import OPERATIONS_SYNC_SINGLE_FLIGHT_MIGRATION

        sql = "\n".join(OPERATIONS_SYNC_SINGLE_FLIGHT_MIGRATION.statements)

        self.assertIn("LOCK TABLE growth.sync_runs", sql)
        self.assertIn("status = 'failed'", sql)
        self.assertIn("error_code = 'SyncInterrupted'", sql)
        self.assertIn("ROW_NUMBER() OVER", sql)
        self.assertIn("ranked.active_rank > 1", sql)
        self.assertIn("INTERVAL '15 minutes'", sql)
        self.assertIn("CREATE UNIQUE INDEX", sql)
        self.assertIn("growth_operations_sync_running_site_unique_idx", sql)
        self.assertIn("WHERE stream_name = 'operations' AND status = 'running'", sql)

    def test_risk_migration_creates_paused_aiwelink_control_plane(self) -> None:
        from app.modules.growth.migrations import RISK_DOMAIN_TABLES, RISK_MIGRATION

        sql = "\n".join(RISK_MIGRATION.statements)

        self.assertEqual(
            RISK_DOMAIN_TABLES,
            (
                "risk_settings",
                "risk_sync_cursors",
                "risk_accounts",
                "risk_ip_accounts",
                "risk_actions",
                "risk_events",
            ),
        )
        for table_name in RISK_DOMAIN_TABLES:
            self.assertIn(f"CREATE TABLE IF NOT EXISTS growth.{table_name}", sql)
        self.assertIn("detector_enabled BOOLEAN NOT NULL DEFAULT FALSE", sql)
        self.assertIn("auto_ban_enabled BOOLEAN NOT NULL DEFAULT FALSE", sql)
        self.assertIn("poll_interval_seconds INTEGER NOT NULL DEFAULT 60", sql)
        self.assertIn("ip_window_days INTEGER NOT NULL DEFAULT 7", sql)
        self.assertIn("shared_ip_min_accounts INTEGER NOT NULL DEFAULT 3", sql)
        self.assertIn("VALUES ('aiwelink', FALSE, FALSE, 60, 7, 3", sql)
        self.assertIn("is_risk_excluded BOOLEAN NOT NULL DEFAULT FALSE", sql)
        self.assertIn("risk_account_id UUID", sql)
        self.assertIn(
            "UNIQUE (site_id, external_user_id, ip_address, source_type)",
            sql,
        )
        self.assertIn("growth_risk_ip_accounts_ip_window_idx", sql)
        self.assertIn("growth_risk_accounts_status_idx", sql)
        self.assertIn("growth_risk_actions_account_success_idx", sql)
        self.assertIn("growth_risk_events_site_time_idx", sql)
        self.assertIn("'auto_ban_conflicted'", sql)
        self.assertNotIn("request_body", sql)

    def test_risk_hardening_migration_updates_already_deployed_risk_schema(self) -> None:
        from app.modules.growth.migrations import RISK_HARDENING_MIGRATION

        sql = "\n".join(RISK_HARDENING_MIGRATION.statements)

        self.assertEqual(RISK_HARDENING_MIGRATION.version, "0008_aiwelink_risk_hardening")
        self.assertIn("DROP CONSTRAINT IF EXISTS risk_events_event_type_check", sql)
        self.assertIn("ADD CONSTRAINT risk_events_event_type_check", sql)
        self.assertIn("'auto_ban_conflicted'", sql)
        self.assertIn("growth_risk_actions_account_success_idx", sql)
        self.assertIn("growth_risk_events_site_time_idx", sql)
        self.assertIn("aggregates_dirty BOOLEAN NOT NULL DEFAULT FALSE", sql)

    def test_manual_risk_approval_migration_cancels_automatic_bans(self) -> None:
        from app.modules.growth.migrations import MANUAL_RISK_APPROVAL_MIGRATION

        sql = "\n".join(MANUAL_RISK_APPROVAL_MIGRATION.statements)

        self.assertEqual(MANUAL_RISK_APPROVAL_MIGRATION.version, "0009_manual_risk_approval")
        self.assertIn("'cancelled'", sql)
        self.assertIn("action_type = 'auto_ban'", sql)
        self.assertIn("action_status IN ('pending', 'failed')", sql)
        self.assertIn("action_status = 'cancelled'", sql)
        self.assertIn("error_code = 'AutoBanDisabled'", sql)
        self.assertIn("risk_status = 'high_risk'", sql)
        self.assertIn("risk_status = 'ban_pending'", sql)
        self.assertIn("auto_ban_enabled = FALSE", sql)

    def test_required_tables_include_initial_operations_and_risk_domains(self) -> None:
        from app.modules.growth.migrations import (
            INITIAL_DOMAIN_TABLES,
            OPERATIONS_DOMAIN_TABLES,
            REQUIRED_DOMAIN_TABLES,
            RISK_DOMAIN_TABLES,
        )

        self.assertEqual(
            REQUIRED_DOMAIN_TABLES,
            INITIAL_DOMAIN_TABLES + OPERATIONS_DOMAIN_TABLES + RISK_DOMAIN_TABLES,
        )
        self.assertEqual(len(REQUIRED_DOMAIN_TABLES), 29)

    def test_migrations_are_ordered_and_uniquely_versioned(self) -> None:
        from app.modules.growth.migrations import MIGRATIONS

        versions = [migration.version for migration in MIGRATIONS]

        self.assertEqual(versions, sorted(versions))
        self.assertEqual(len(versions), len(set(versions)))
        self.assertEqual(
            versions,
            [
                "0001_initial",
                "0002_operations_analytics",
                "0003_operations_internal_email",
                "0004_operations_lifecycle_metrics",
                "0005_operations_sale_credit_without_cash",
                "0006_operations_sync_single_flight",
                "0007_aiwelink_risk_control",
                "0008_aiwelink_risk_hardening",
                "0009_manual_risk_approval",
            ],
        )

    async def test_unapplied_migration_executes_and_records_version(self) -> None:
        from app.modules.growth.migrations import MIGRATIONS, apply_pending_migrations

        connection = _FakeConnection(applied_versions=[])

        result = await apply_pending_migrations(connection)

        self.assertEqual(
            result["applied_versions"],
            [
                "0001_initial",
                "0002_operations_analytics",
                "0003_operations_internal_email",
                "0004_operations_lifecycle_metrics",
                "0005_operations_sale_credit_without_cash",
                "0006_operations_sync_single_flight",
                "0007_aiwelink_risk_control",
                "0008_aiwelink_risk_hardening",
                "0009_manual_risk_approval",
            ],
        )
        self.assertEqual(result["current_version"], "0009_manual_risk_approval")
        self.assertEqual(result["pending_versions"], [])
        executed_sql = "\n".join(connection.statements)
        for migration in MIGRATIONS:
            for statement in migration.statements:
                self.assertIn(statement, connection.statements)
        self.assertIn("INSERT INTO growth.schema_migrations", executed_sql)

    async def test_applied_migration_is_not_executed_twice(self) -> None:
        from app.modules.growth.migrations import MIGRATIONS, apply_pending_migrations

        connection = _FakeConnection(
            applied_versions=[
                "0001_initial",
                "0002_operations_analytics",
                "0003_operations_internal_email",
                "0004_operations_lifecycle_metrics",
                "0005_operations_sale_credit_without_cash",
                "0006_operations_sync_single_flight",
                "0007_aiwelink_risk_control",
                "0008_aiwelink_risk_hardening",
                "0009_manual_risk_approval",
            ]
        )

        result = await apply_pending_migrations(connection)

        self.assertEqual(result["applied_versions"], [])
        self.assertEqual(result["current_version"], "0009_manual_risk_approval")
        for migration in MIGRATIONS:
            for statement in migration.statements:
                self.assertNotIn(statement, connection.statements)

    async def test_schema_status_is_uninitialized_without_migration_ledger(self) -> None:
        from app.modules.growth.migrations import inspect_growth_schema

        connection = _FakeConnection(applied_versions=[], ledger_exists=False)

        result = await inspect_growth_schema(connection)

        self.assertFalse(result["initialized"])
        self.assertIsNone(result["current_version"])
        self.assertEqual(
            result["pending_versions"],
            [
                "0001_initial",
                "0002_operations_analytics",
                "0003_operations_internal_email",
                "0004_operations_lifecycle_metrics",
                "0005_operations_sale_credit_without_cash",
                "0006_operations_sync_single_flight",
                "0007_aiwelink_risk_control",
                "0008_aiwelink_risk_hardening",
                "0009_manual_risk_approval",
            ],
        )
        self.assertEqual(result["domain_table_count"], 0)

    async def test_schema_status_reports_applied_version_and_table_count(self) -> None:
        from app.modules.growth.migrations import inspect_growth_schema

        connection = _FakeConnection(
            applied_versions=[
                "0001_initial",
                "0002_operations_analytics",
                "0003_operations_internal_email",
                "0004_operations_lifecycle_metrics",
                "0005_operations_sale_credit_without_cash",
                "0006_operations_sync_single_flight",
                "0007_aiwelink_risk_control",
                "0008_aiwelink_risk_hardening",
                "0009_manual_risk_approval",
            ],
            ledger_exists=True,
            domain_table_count=29,
        )

        result = await inspect_growth_schema(connection)

        self.assertTrue(result["initialized"])
        self.assertEqual(result["current_version"], "0009_manual_risk_approval")
        self.assertEqual(result["pending_versions"], [])
        self.assertEqual(result["domain_table_count"], 29)


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
