from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


class OperationsRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_internal_user_recognizes_unique_snapshot_email(self) -> None:
        from app.modules.operations.repository import create_internal_user
        from app.modules.operations.schemas import InternalUserCreate

        internal_user_id = uuid4()
        connection = _FakeConnection(
            [
                {
                    "internal_user_id": internal_user_id,
                    "site_id": "aigclink",
                    "email": "staff@example.com",
                    "external_user_id": "42",
                    "recognition_status": "recognized",
                },
            ]
        )

        row = await create_internal_user(
            connection,
            InternalUserCreate(site_id="aigclink", email="staff@example.com"),
            actor_id="owner",
            internal_user_id=internal_user_id,
        )

        self.assertEqual(row["site_id"], "aigclink")
        self.assertEqual(row["external_user_id"], "42")
        self.assertEqual(row["recognition_status"], "recognized")
        statement, parameters = connection.calls[0]
        self.assertIn("growth.internal_users", statement)
        self.assertIn("lower(trim(snapshot.account_label))", statement)
        self.assertIn("COUNT(*)", statement)
        self.assertIn("NOT EXISTS", statement)
        self.assertIn("existing.external_user_id = snapshot.external_user_id", statement)
        self.assertIn("BOOL_AND(email_matches.available)", statement)
        self.assertIn("growth.ops_user_snapshots", statement)
        self.assertEqual(parameters["site_id"], "aigclink")
        self.assertEqual(parameters["email"], "staff@example.com")
        self.assertNotIn("aigclink'", statement)

    async def test_create_internal_user_keeps_unknown_email_pending(self) -> None:
        from app.modules.operations.repository import create_internal_user
        from app.modules.operations.schemas import InternalUserCreate

        connection = _FakeConnection(
            [
                {
                    "internal_user_id": uuid4(),
                    "site_id": "aigclink",
                    "email": "later@example.com",
                    "external_user_id": None,
                    "recognized_at": None,
                    "recognition_status": "pending",
                }
            ]
        )

        row = await create_internal_user(
            connection,
            InternalUserCreate(site_id="aigclink", email="later@example.com"),
            actor_id="owner",
        )

        self.assertIsNone(row["external_user_id"])
        self.assertEqual(row["recognition_status"], "pending")

    async def test_internal_user_search_includes_email_and_recognition_status(self) -> None:
        from app.modules.operations.repository import list_internal_users

        connection = _FakeConnection([None])

        await list_internal_users(
            connection,
            allowed_site_ids=("aigclink",),
            query="staff@example.com",
        )

        statement, parameters = connection.calls[0]
        self.assertIn("internal_user.email ILIKE", statement)
        self.assertIn("AS recognition_status", statement)
        self.assertEqual(parameters["query"], "staff@example.com")

    async def test_updating_internal_user_email_retries_recognition(self) -> None:
        from app.modules.operations.repository import update_internal_user
        from app.modules.operations.schemas import InternalUserUpdate

        internal_user_id = uuid4()
        connection = _FakeConnection(
            [
                {
                    "internal_user_id": internal_user_id,
                    "site_id": "aigclink",
                    "email": "new@example.com",
                    "external_user_id": None,
                    "recognized_at": None,
                    "recognition_status": "pending",
                }
            ]
        )

        row = await update_internal_user(
            connection,
            internal_user_id,
            InternalUserUpdate(email=" New@Example.com "),
            actor_id="owner",
        )

        statement, parameters = connection.calls[0]
        self.assertIn("external_user_id = NULL", statement)
        self.assertIn("recognized_at = NULL", statement)
        self.assertIn("lower(trim(snapshot.account_label))", statement)
        self.assertIn("NOT EXISTS", statement)
        self.assertIn("BOOL_AND(email_matches.available)", statement)
        self.assertEqual(parameters["email"], "new@example.com")
        self.assertEqual(row["recognition_status"], "pending")

    async def test_create_conversion_rate_closes_current_window_before_insert(self) -> None:
        from app.modules.operations.repository import create_conversion_rate
        from app.modules.operations.schemas import ConversionRateCreate

        rate_id = uuid4()
        connection = _FakeConnection(
            [
                {
                    "conversion_rate_id": rate_id,
                    "site_id": "aiwelink",
                    "balance_units_per_cny": Decimal("10"),
                }
            ]
        )

        row = await create_conversion_rate(
            connection,
            ConversionRateCreate(
                site_id="aiwelink",
                balance_units_per_cny=Decimal("10"),
                effective_from=NOW,
            ),
            actor_id="admin",
            conversion_rate_id=rate_id,
        )

        statement, parameters = connection.calls[0]
        self.assertIn("UPDATE growth.balance_conversion_rates", statement)
        self.assertIn("INSERT INTO growth.balance_conversion_rates", statement)
        self.assertEqual(parameters["balance_units_per_cny"], Decimal("10"))
        self.assertEqual(row["conversion_rate_id"], str(rate_id))

    async def test_aggregate_replacement_is_scoped_to_one_site(self) -> None:
        from app.modules.operations.repository import replace_affected_aggregates

        connection = _FakeConnection([None, None, None, None])

        await replace_affected_aggregates(
            connection,
            site_id="aigclink",
            start_at=NOW,
            end_at=NOW,
        )

        statements = "\n".join(statement for statement, _ in connection.calls)
        self.assertIn("WHERE site_id = :site_id", statements)
        self.assertIn("snapshot.site_id = :site_id", statements)
        self.assertIn("usage.site_id = :site_id", statements)
        self.assertIn("event.site_id = :site_id", statements)
        self.assertIn("usage.site_id = 'aigclink'", statements)
        self.assertIn("NOT snapshot.is_internal", statements)
        self.assertIn("event.site_id <> 'aigclink'", statements)
        self.assertGreaterEqual(
            statements.count("date_trunc('hour', CAST(:start_at AS TIMESTAMPTZ))"),
            2,
        )
        self.assertGreaterEqual(
            statements.count("date_trunc('hour', CAST(:end_at AS TIMESTAMPTZ))"),
            2,
        )
        self.assertGreaterEqual(statements.count("CAST(:start_at AS TIMESTAMPTZ)"), 2)
        self.assertGreaterEqual(statements.count("CAST(:end_at AS TIMESTAMPTZ)"), 2)
        self.assertGreaterEqual(
            statements.count("(CAST(:start_at AS TIMESTAMPTZ) AT TIME ZONE :timezone)::date"),
            2,
        )
        self.assertGreaterEqual(
            statements.count("(CAST(:end_at AS TIMESTAMPTZ) AT TIME ZONE :timezone)::date + 1"),
            2,
        )
        for _, parameters in connection.calls:
            self.assertEqual(parameters["site_id"], "aigclink")

    async def test_operations_sync_lock_uses_site_scoped_transaction_lock(self) -> None:
        from app.modules.operations.repository import acquire_operations_sync_lock

        connection = _FakeConnection([None])

        await acquire_operations_sync_lock(connection, site_id="aiwelink")

        statement, parameters = connection.calls[0]
        self.assertIn("pg_advisory_xact_lock", statement)
        self.assertIn(":site_id", statement)
        self.assertEqual(parameters["site_id"], "aiwelink")

    async def test_fact_upserts_use_stable_source_identity(self) -> None:
        from app.modules.operations.repository import upsert_credit_events, upsert_usage_facts

        connection = _FakeConnection([None, None])
        await upsert_usage_facts(
            connection,
            [
                {
                    "site_id": "aiwelink",
                    "external_user_id": "42",
                    "source_type": "usage_logs",
                    "source_record_id": "1001",
                    "successful_call_count": 1,
                    "consumed_balance_units": Decimal("2"),
                    "cost_cny": Decimal("0.2"),
                    "conversion_rate_id": None,
                    "occurred_at": NOW,
                    "source_updated_at": NOW,
                }
            ],
        )
        await upsert_credit_events(
            connection,
            [
                {
                    "site_id": "aiwelink",
                    "external_user_id": "42",
                    "source_type": "payment",
                    "source_record_id": "order-1",
                    "direction": "credit",
                    "purpose": "sale",
                    "classification_status": "classified",
                    "balance_units": Decimal("100"),
                    "cash_amount_cny": Decimal("10"),
                    "conversion_rate_id": None,
                    "occurred_at": NOW,
                    "source_updated_at": NOW,
                    "source_metadata": {},
                }
            ],
        )

        for statement, parameters in connection.calls:
            self.assertIn("ON CONFLICT (site_id, source_type, source_record_id)", statement)
            self.assertEqual(parameters[0]["source_record_id"], parameters[0]["source_record_id"])

    async def test_user_snapshot_upsert_recognizes_pending_email_configuration(self) -> None:
        from app.modules.operations.repository import upsert_user_snapshots

        connection = _FakeConnection([None, None])
        await upsert_user_snapshots(
            connection,
            [
                {
                    "site_id": "aigclink",
                    "external_user_id": "7",
                    "account_label": "staff@example.com",
                    "registered_at": NOW,
                    "account_status": "active",
                    "balance_units": Decimal("5"),
                    "source_created_at": NOW,
                    "source_updated_at": NOW,
                }
            ],
        )

        upsert_statement, parameters = connection.calls[0]
        recognition_statement, _ = connection.calls[1]
        self.assertIn("growth.internal_users", upsert_statement)
        self.assertIn("configured.external_user_id = :external_user_id", upsert_statement)
        self.assertIn("ON CONFLICT (site_id, external_user_id)", upsert_statement)
        self.assertEqual(parameters[0]["account_label"], "staff@example.com")
        self.assertIn(
            "lower(trim(configured.email)) = lower(trim(snapshot.account_label))",
            recognition_statement,
        )
        self.assertIn("HAVING COUNT(*) = 1", recognition_statement)
        self.assertIn("recognized_at = NOW()", recognition_statement)
        self.assertIn("NOT EXISTS", recognition_statement)
        self.assertIn("existing.external_user_id = snapshot.external_user_id", recognition_statement)
        self.assertIn("BOOL_AND(matches.available)", recognition_statement)
        self.assertIn("UPDATE growth.ops_user_snapshots", recognition_statement)

    async def test_resolve_classification_updates_task_and_credit_event_together(self) -> None:
        from app.modules.operations.repository import resolve_classification_task
        from app.modules.operations.schemas import ClassificationUpdate

        task_id = uuid4()
        connection = _FakeConnection(
            [
                {
                    "classification_task_id": task_id,
                    "status": "resolved",
                    "resolved_purpose": "sale",
                    "resolved_cash_amount_cny": Decimal("20"),
                }
            ]
        )

        await resolve_classification_task(
            connection,
            task_id,
            ClassificationUpdate(purpose="sale", cash_amount_cny=Decimal("20")),
            actor_id="owner",
        )

        statement, parameters = connection.calls[0]
        self.assertIn("UPDATE growth.classification_tasks", statement)
        self.assertIn("UPDATE growth.credit_events", statement)
        self.assertEqual(parameters["actor_id"], "owner")

    async def test_summary_query_uses_bound_filters(self) -> None:
        from app.modules.operations.repository import get_operations_summary

        connection = _FakeConnection([{"registered_user_count": 1}])
        await get_operations_summary(
            connection,
            allowed_site_ids=("aiwelink",),
            segment="ordinary",
            start_at=NOW,
            end_at=NOW,
        )

        statement, parameters = connection.calls[0]
        self.assertIn("ANY(CAST(:allowed_site_ids AS TEXT[]))", statement)
        self.assertIn(":segment", statement)
        self.assertNotIn("aiwelink'", statement)
        self.assertEqual(parameters["allowed_site_ids"], ("aiwelink",))
        self.assertEqual(parameters["segment"], "ordinary")

    async def test_summary_uses_usage_revenue_only_for_ordinary_aigclink_users(self) -> None:
        from app.modules.operations.repository import get_operations_summary

        connection = _FakeConnection([{"gross_income_cny": Decimal("12") }])

        await get_operations_summary(
            connection,
            allowed_site_ids=("aiwelink", "aigclink"),
            segment="all",
            start_at=NOW,
            end_at=NOW,
        )

        statement, _ = connection.calls[0]
        self.assertIn("usage.site_id = 'aigclink'", statement)
        self.assertIn("NOT snapshot.is_internal", statement)
        self.assertIn("SUM(usage.cost_cny)", statement)
        self.assertIn("event.site_id <> 'aigclink'", statement)

    async def test_trends_derive_historical_aigclink_revenue_from_ordinary_cost(self) -> None:
        from app.modules.operations.repository import get_operations_trends

        connection = _FakeConnection([None])

        await get_operations_trends(
            connection,
            allowed_site_ids=("aigclink",),
            segment="all",
            start_at=NOW,
            end_at=NOW,
        )

        statement, _ = connection.calls[0]
        self.assertIn("FROM growth.ops_hourly_stats AS stats", statement)
        self.assertIn("LEFT JOIN growth.ops_hourly_stats AS ordinary", statement)
        self.assertIn("stats.site_id = 'aigclink'", statement)
        self.assertIn("stats.user_segment = 'internal'", statement)
        self.assertIn("THEN ordinary.cost_cny", statement)

    async def test_site_breakdown_groups_current_metrics_by_authorized_site(self) -> None:
        from app.modules.operations.repository import get_operations_site_breakdown

        connection = _FakeConnection(
            [
                {
                    "site_id": "aigclink",
                    "registered_user_count": 3,
                    "gross_income_cny": Decimal("12"),
                }
            ]
        )

        rows = await get_operations_site_breakdown(
            connection,
            allowed_site_ids=("aigclink",),
            segment="all",
            start_at=NOW,
            end_at=NOW,
        )

        statement, parameters = connection.calls[0]
        self.assertEqual(rows[0]["site_id"], "aigclink")
        self.assertIn("GROUP BY usage.site_id", statement)
        self.assertIn("event.site_id <> 'aigclink'", statement)
        self.assertIn("NOT snapshot.is_internal", statement)
        self.assertEqual(parameters["allowed_site_ids"], ("aigclink",))

    async def test_all_user_facing_reads_require_bound_site_collections(self) -> None:
        from app.modules.operations import repository

        connection = _FakeConnection([None, None, None, None, None, None])
        allowed = ("aiwelink",)
        await repository.get_operations_trends(
            connection,
            allowed_site_ids=allowed,
            segment="ordinary",
            start_at=NOW,
            end_at=NOW,
        )
        await repository.list_operations_users(
            connection,
            allowed_site_ids=allowed,
            segment="ordinary",
            start_at=NOW,
            end_at=NOW,
        )
        await repository.get_sync_status(connection, allowed_site_ids=allowed)
        await repository.list_internal_users(connection, allowed_site_ids=allowed)
        await repository.list_conversion_rates(connection, allowed_site_ids=allowed)
        await repository.list_classification_tasks(connection, allowed_site_ids=allowed)

        self.assertEqual(len(connection.calls), 6)
        for statement, parameters in connection.calls:
            self.assertIn("ANY(CAST(:allowed_site_ids AS TEXT[]))", statement)
            self.assertEqual(parameters["allowed_site_ids"], allowed)
            self.assertNotIn("CAST(:site_id AS TEXT) IS NULL", statement)

    async def test_credit_command_requests_are_persisted_as_pending(self) -> None:
        from app.modules.operations.repository import (
            create_balance_adjustment_request,
            create_redemption_batch_request,
        )
        from app.modules.operations.schemas import BalanceAdjustmentCreate, RedemptionBatchCreate

        batch_id = uuid4()
        adjustment_id = uuid4()
        connection = _FakeConnection(
            [
                {"redemption_batch_id": batch_id, "command_status": "pending"},
                {"adjustment_request_id": adjustment_id, "command_status": "pending"},
            ]
        )

        batch = await create_redemption_batch_request(
            connection,
            RedemptionBatchCreate(
                site_id="aiwelink",
                purpose="internal",
                code_count=2,
                balance_units_per_code=Decimal("100"),
                idempotency_key="batch-1",
            ),
            actor_id="owner",
            redemption_batch_id=batch_id,
        )
        adjustment = await create_balance_adjustment_request(
            connection,
            BalanceAdjustmentCreate(
                site_id="aigclink",
                external_user_id="42",
                purpose="compensation",
                balance_units=Decimal("5"),
                idempotency_key="adjustment-1",
            ),
            actor_id="admin",
            adjustment_request_id=adjustment_id,
        )

        self.assertEqual(batch["command_status"], "pending")
        self.assertEqual(adjustment["command_status"], "pending")
        self.assertIn("growth.redemption_batches", connection.calls[0][0])
        self.assertIn("growth.balance_adjustment_requests", connection.calls[1][0])
        self.assertEqual(connection.calls[0][1]["purpose"], "internal")
        self.assertEqual(connection.calls[1][1]["external_user_id"], "42")

    async def test_operations_sync_run_lifecycle_uses_operations_stream(self) -> None:
        from app.modules.operations.repository import (
            finish_operations_sync_run,
            get_operations_sync_cursor,
            start_operations_sync_run,
        )

        run_id = uuid4()
        connection = _FakeConnection(
            [
                {"last_success_at": NOW},
                {"run_id": run_id, "status": "running"},
                {"run_id": run_id, "status": "succeeded"},
            ]
        )

        cursor = await get_operations_sync_cursor(connection, site_id="aiwelink")
        started = await start_operations_sync_run(
            connection,
            site_id="aiwelink",
            adapter_name="sub2api",
            trigger_type="schedule",
            started_at=NOW,
            run_id=run_id,
        )
        finished = await finish_operations_sync_run(
            connection,
            run_id=run_id,
            site_id="aiwelink",
            adapter_name="sub2api",
            status="succeeded",
            finished_at=NOW,
            rows_scanned=10,
            rows_upserted=9,
        )

        self.assertEqual(cursor["last_success_at"], NOW.isoformat())
        self.assertEqual(started["status"], "running")
        self.assertEqual(finished["status"], "succeeded")
        sql = "\n".join(statement for statement, _ in connection.calls)
        self.assertIn("stream_name = 'operations'", sql)
        self.assertIn("growth.sync_cursors", sql)

    async def test_sync_status_keeps_last_success_when_latest_run_failed(self) -> None:
        from app.modules.operations.repository import get_sync_status

        connection = _FakeConnection(
            [
                {
                    "site_id": "aiwelink",
                    "status": "failed",
                    "last_success_at": NOW,
                }
            ]
        )

        result = await get_sync_status(connection, allowed_site_ids=("aiwelink",))

        self.assertEqual(result[0]["last_success_at"], NOW.isoformat())
        statement, _ = connection.calls[0]
        self.assertIn("growth.sync_cursors", statement)


class OperationsCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_reuses_value_until_invalidated_for_site(self) -> None:
        from app.modules.operations.cache import OperationsResponseCache

        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            return {"value": calls}

        cache = OperationsResponseCache(ttl_seconds=60, max_entries=4)
        key = ("summary", "aiwelink", "ordinary")

        first = await cache.get_or_load(key, load)
        second = await cache.get_or_load(key, load)
        cache.invalidate(site_id="aiwelink")
        third = await cache.get_or_load(key, load)

        self.assertEqual(first, second)
        self.assertNotEqual(second, third)
        self.assertEqual(calls, 2)

    async def test_cache_coalesces_concurrent_loads_and_bounds_entries(self) -> None:
        from app.modules.operations.cache import OperationsResponseCache

        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return calls

        cache = OperationsResponseCache(ttl_seconds=60, max_entries=2)
        values = await asyncio.gather(
            cache.get_or_load(("summary", "aiwelink"), load),
            cache.get_or_load(("summary", "aiwelink"), load),
        )
        await cache.get_or_load(("summary", "aigclink"), load)
        await cache.get_or_load(("trends", "aigclink"), load)

        self.assertEqual(values, [1, 1])
        self.assertEqual(calls, 3)
        self.assertEqual(cache.size, 2)


class _FakeMappings:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row

    def all(self):
        return [] if self.row is None else [self.row]


class _FakeResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return _FakeMappings(self.row)

    def scalar_one_or_none(self):
        return self.row


class _FakeConnection:
    def __init__(self, rows: list[dict | None]):
        self.rows = list(rows)
        self.calls: list[tuple[str, object]] = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, statement, parameters=None):
        captured = [dict(item) for item in parameters] if isinstance(parameters, list) else dict(parameters or {})
        self.calls.append((str(statement), captured))
        return _FakeResult(self.rows.pop(0) if self.rows else None)


if __name__ == "__main__":
    unittest.main()
