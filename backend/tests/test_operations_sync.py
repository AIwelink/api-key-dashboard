from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


class OperationsSyncRuleTests(unittest.TestCase):
    def test_first_sync_reads_30_days_when_no_cursor_or_override_exists(self) -> None:
        from app.modules.operations.sync import reconciliation_start

        self.assertEqual(
            reconciliation_start(now=NOW, last_success_at=None),
            NOW - timedelta(days=30),
        )

    def test_reconciliation_rewinds_cursor_by_48_hours(self) -> None:
        from app.modules.operations.sync import reconciliation_start

        last_success = NOW - timedelta(minutes=15)

        self.assertEqual(
            reconciliation_start(now=NOW, last_success_at=last_success),
            last_success - timedelta(hours=48),
        )

    def test_usage_uses_rate_effective_when_event_occurred(self) -> None:
        from app.modules.operations.adapters.base import UsageFactInput
        from app.modules.operations.sync import apply_usage_conversion_rates

        old_rate_id = uuid4()
        new_rate_id = uuid4()
        facts = [
            UsageFactInput(
                site_id="aiwelink",
                external_user_id="42",
                source_type="usage_logs",
                source_record_id="1",
                successful_call_count=1,
                consumed_balance_units=Decimal("20"),
                occurred_at=NOW - timedelta(days=2),
                source_updated_at=NOW - timedelta(days=2),
            ),
            UsageFactInput(
                site_id="aiwelink",
                external_user_id="42",
                source_type="usage_logs",
                source_record_id="2",
                successful_call_count=1,
                consumed_balance_units=Decimal("20"),
                occurred_at=NOW,
                source_updated_at=NOW,
            ),
        ]
        rates = [
            {
                "conversion_rate_id": str(old_rate_id),
                "balance_units_per_cny": Decimal("10"),
                "effective_from": NOW - timedelta(days=10),
                "effective_until": NOW - timedelta(days=1),
            },
            {
                "conversion_rate_id": str(new_rate_id),
                "balance_units_per_cny": Decimal("5"),
                "effective_from": NOW - timedelta(days=1),
                "effective_until": None,
            },
        ]

        converted = apply_usage_conversion_rates(facts, rates)

        self.assertEqual(converted[0].cost_cny, Decimal("2"))
        self.assertEqual(converted[0].conversion_rate_id, old_rate_id)
        self.assertEqual(converted[1].cost_cny, Decimal("4"))
        self.assertEqual(converted[1].conversion_rate_id, new_rate_id)


class OperationsSyncCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        from app.modules.operations.sync import clear_refresh_tasks

        await clear_refresh_tasks()

    async def test_duplicate_manual_refresh_is_coalesced(self) -> None:
        from app.modules.operations.sync import request_operations_refresh

        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def sync_func(db, *, site_id, trigger_type):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"site_id": site_id, "trigger_type": trigger_type}

        first = asyncio.create_task(
            request_operations_refresh(object(), site_id="aiwelink", sync_func=sync_func)
        )
        await started.wait()
        second = asyncio.create_task(
            request_operations_refresh(object(), site_id="aiwelink", sync_func=sync_func)
        )
        release.set()

        self.assertEqual(await first, await second)
        self.assertEqual(calls, 1)

    async def test_one_site_failure_does_not_stop_another(self) -> None:
        from app.modules.operations import sync

        sync_func = AsyncMock(side_effect=[RuntimeError("source down"), {"site_id": "aigclink"}])

        with patch.object(sync.logger, "exception") as logged:
            results = await sync.run_operations_sync_cycle(
                object(),
                sites=[{"id": "aiwelink"}, {"id": "aigclink"}],
                sync_func=sync_func,
            )

        self.assertEqual(sync_func.await_count, 2)
        logged.assert_called_once()
        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[1]["status"], "succeeded")

    async def test_scheduler_waits_15_minutes_between_cycles(self) -> None:
        from app.modules.operations.sync import operations_sync_loop

        cycle = AsyncMock(return_value=[])
        delays = []

        async def stop_after_delay(seconds):
            delays.append(seconds)
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await operations_sync_loop(object(), cycle_func=cycle, sleep_func=stop_after_delay)

        cycle.assert_awaited_once()
        self.assertEqual(delays, [900])

    async def test_aggregation_runs_only_after_all_fact_upserts_succeed(self) -> None:
        from app.modules.operations import sync

        adapter = AsyncMock()
        adapter.read_users.return_value = []
        adapter.read_usage.return_value = []
        adapter.read_credit_events.return_value = []
        aggregate = AsyncMock()

        with (
            patch.object(sync.repository, "acquire_operations_sync_lock", AsyncMock()),
            patch.object(sync.repository, "list_conversion_rates", AsyncMock(return_value=[])),
            patch.object(sync.repository, "upsert_user_snapshots", AsyncMock(return_value=0)),
            patch.object(
                sync.repository,
                "upsert_usage_facts",
                AsyncMock(side_effect=RuntimeError("upsert failed")),
            ),
            patch.object(sync.repository, "upsert_credit_events", AsyncMock(return_value=0)),
            patch.object(sync.repository, "replace_affected_aggregates", aggregate),
        ):
            with self.assertRaisesRegex(RuntimeError, "upsert failed"):
                await sync.sync_adapter_records(
                    adapter=adapter,
                    source_connection=object(),
                    growth_connection=object(),
                    since=NOW - timedelta(hours=48),
                    now=NOW,
                )

        aggregate.assert_not_awaited()

    async def test_adapter_sync_acquires_site_lock_before_source_reads(self) -> None:
        from app.modules.operations import sync

        calls = []
        adapter = AsyncMock()
        adapter.site_id = "aiwelink"
        adapter.read_users.side_effect = lambda **kwargs: calls.append("read") or []
        adapter.read_usage.return_value = []
        adapter.read_credit_events.return_value = []

        async def lock(connection, *, site_id):
            calls.append(f"lock:{site_id}")

        with (
            patch.object(sync.repository, "acquire_operations_sync_lock", lock),
            patch.object(sync.repository, "list_conversion_rates", AsyncMock(return_value=[])),
            patch.object(sync.repository, "upsert_user_snapshots", AsyncMock(return_value=0)),
            patch.object(sync.repository, "upsert_usage_facts", AsyncMock(return_value=0)),
            patch.object(sync.repository, "upsert_credit_events", AsyncMock(return_value=0)),
            patch.object(sync.repository, "create_pending_classification_tasks", AsyncMock(return_value=0)),
            patch.object(sync.repository, "replace_affected_aggregates", AsyncMock()),
        ):
            await sync.sync_adapter_records(
                adapter=adapter,
                source_connection=object(),
                growth_connection=object(),
                since=NOW - timedelta(hours=48),
                now=NOW,
            )

        self.assertEqual(calls[:2], ["lock:aiwelink", "read"])

    async def test_site_sync_records_success_and_uses_reconciled_since(self) -> None:
        from app.modules.operations import sync

        source = object()
        growth_connections = [object(), object(), object()]
        last_success = NOW - timedelta(minutes=15)
        run_id = uuid4()
        adapter = AsyncMock()
        adapter.site_id = "aiwelink"
        adapter_factory = AsyncMock(return_value=adapter)
        site_loader = AsyncMock(
            return_value={
                "id": "aiwelink",
                "client_type": "sub2api",
                "sql_dsn": "postgresql://reader:secret@db/sub2api",
            }
        )
        records_sync = AsyncMock(
            return_value={"users": 2, "usage": 3, "credits": 1, "classification_tasks": 1}
        )
        run_finisher = AsyncMock(return_value={"run_id": str(run_id)})

        result = await sync.sync_site_operations(
            object(),
            site_id="aiwelink",
            trigger_type="schedule",
            now=NOW,
            site_loader=site_loader,
            adapter_factory=adapter_factory,
            source_connection_factory=lambda site: _async_context(source),
            growth_connection_factory=lambda db, write=True: _async_context(
                growth_connections.pop(0)
            ),
            records_sync=records_sync,
            cursor_loader=AsyncMock(return_value={"last_success_at": last_success.isoformat()}),
            run_starter=AsyncMock(return_value={"run_id": str(run_id)}),
            run_finisher=run_finisher,
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(
            records_sync.await_args.kwargs["since"],
            last_success - timedelta(hours=48),
        )
        self.assertEqual(records_sync.await_args.kwargs["source_connection"], source)
        self.assertEqual(run_finisher.await_args.kwargs["status"], "succeeded")
        self.assertEqual(result["run_id"], str(run_id))

    async def test_site_sync_records_failure_after_fact_transaction_rolls_back(self) -> None:
        from app.modules.operations import sync

        run_id = uuid4()
        adapter = AsyncMock()
        adapter.site_id = "aigclink"
        finisher = AsyncMock(return_value={})

        with self.assertRaisesRegex(RuntimeError, "source failed"):
            await sync.sync_site_operations(
                object(),
                site_id="aigclink",
                trigger_type="manual",
                now=NOW,
                site_loader=AsyncMock(
                    return_value={
                        "id": "aigclink",
                        "client_type": "newapi",
                        "sql_dsn": "reader:secret@tcp(db:3306)/newapi",
                    }
                ),
                adapter_factory=AsyncMock(return_value=adapter),
                source_connection_factory=lambda site: _async_context(object()),
                growth_connection_factory=lambda db, write=True: _async_context(object()),
                records_sync=AsyncMock(side_effect=RuntimeError("source failed")),
                cursor_loader=AsyncMock(return_value={}),
                run_starter=AsyncMock(return_value={"run_id": str(run_id)}),
                run_finisher=finisher,
            )

        self.assertEqual(finisher.await_args.kwargs["status"], "failed")
        self.assertEqual(finisher.await_args.kwargs["error_code"], "RuntimeError")


class OperationsSourceConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_connection_is_read_only_and_disposed(self) -> None:
        from app.modules.operations.sync import source_database_connection

        connection = _SourceConnection()
        engine = _SourceEngine(connection)
        factory = unittest.mock.Mock(return_value=engine)
        site = {
            "client_type": "sub2api",
            "sql_dsn": "postgresql://reader:secret@db/sub2api?sslmode=disable",
        }

        async with source_database_connection(site, engine_factory=factory) as selected:
            self.assertIs(selected, connection)

        self.assertIn("READ ONLY", connection.statements[0])
        engine.dispose.assert_awaited_once()


@asynccontextmanager
async def _async_context(value):
    yield value


class _SourceConnection:
    def __init__(self):
        self.statements = []

    async def execute(self, statement, parameters=None):
        self.statements.append(str(statement))


class _SourceEngine:
    def __init__(self, connection):
        self.connection = connection
        self.dispose = AsyncMock()

    def connect(self):
        return _async_context(self.connection)


if __name__ == "__main__":
    unittest.main()
