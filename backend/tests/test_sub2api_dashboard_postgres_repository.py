from __future__ import annotations

import unittest
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.modules.sub2api import dashboard_postgres_repository


class FakeMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def all(self) -> list[dict[str, object]]:
        return self.rows


class FakeResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def mappings(self) -> FakeMappings:
        return FakeMappings(self.rows)


class FakeConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.executed: list[tuple[str, dict[str, object]]] = []

    async def execute(self, statement, parameters) -> FakeResult:
        self.executed.append((str(statement), dict(parameters)))
        return FakeResult(self.rows)


class AsyncContext:
    def __init__(self, value: object | None = None, error: Exception | None = None) -> None:
        self.value = value
        self.error = error

    async def __aenter__(self):
        if self.error is not None:
            raise self.error
        return self.value

    async def __aexit__(self, *_args) -> bool:
        return False


class FakeEngine:
    def __init__(self, connection: FakeConnection | None = None, error: Exception | None = None) -> None:
        self.connection = connection
        self.error = error
        self.dispose = AsyncMock()

    def connect(self) -> AsyncContext:
        return AsyncContext(self.connection, self.error)


class Sub2ApiDashboardPostgresRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_hourly_rows_map_to_existing_dashboard_snapshot_contract(self) -> None:
        connection = FakeConnection(
            [
                {
                    "bucket": datetime(2026, 7, 18, 1, 0, tzinfo=UTC),
                    "total_requests": 12,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_tokens": 20,
                    "cache_read_tokens": 30,
                    "total_cost": Decimal("3.50"),
                    "actual_cost": Decimal("3.25"),
                    "account_cost": Decimal("2.75"),
                    "computed_at": datetime(2026, 7, 18, 1, 5, tzinfo=UTC),
                }
            ]
        )
        engine = FakeEngine(connection)
        engine_factory = MagicMock(return_value=engine)

        result = await dashboard_postgres_repository.fetch_site_dashboard_snapshot(
            "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            start_date="2026-07-18",
            end_date="2026-07-18",
            granularity="hour",
            engine_factory=engine_factory,
        )

        self.assertEqual(result["granularity"], "hour")
        self.assertEqual(result["start_date"], "2026-07-18")
        self.assertEqual(result["end_date"], "2026-07-18")
        self.assertEqual(result["generated_at"], datetime(2026, 7, 18, 1, 5, tzinfo=UTC))
        self.assertEqual(
            result["trend"],
            [
                {
                    "date": "2026-07-18 09:00",
                    "requests": 12,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_tokens": 20,
                    "cache_read_tokens": 30,
                    "total_tokens": 200,
                    "cost": 3.5,
                    "actual_cost": 3.25,
                    "account_cost": 2.75,
                }
            ],
        )
        sql, parameters = connection.executed[0]
        self.assertNotIn("select *", sql.lower())
        self.assertIn("usage_dashboard_hourly", sql)
        self.assertEqual(parameters["start_at"], datetime(2026, 7, 17, 16, 0, tzinfo=UTC))
        self.assertEqual(parameters["end_at"], datetime(2026, 7, 18, 16, 0, tzinfo=UTC))
        self.assertEqual(engine_factory.call_args.kwargs["isolation_level"], "REPEATABLE READ")
        engine.dispose.assert_awaited_once()

    async def test_daily_rows_use_inclusive_local_dates(self) -> None:
        connection = FakeConnection(
            [
                {
                    "bucket": date(2026, 7, 18),
                    "total_requests": 3,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cache_creation_tokens": 0,
                    "cache_read_tokens": 1,
                    "total_cost": Decimal("1.5"),
                    "actual_cost": Decimal("1.2"),
                    "account_cost": Decimal("1.0"),
                    "computed_at": datetime(2026, 7, 19, 0, 1, tzinfo=UTC),
                }
            ]
        )
        engine = FakeEngine(connection)

        result = await dashboard_postgres_repository.fetch_site_dashboard_snapshot(
            "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            start_date="2026-07-12",
            end_date="2026-07-18",
            granularity="day",
            engine_factory=lambda *_args, **_kwargs: engine,
        )

        self.assertEqual(result["trend"][0]["date"], "2026-07-18")
        sql, parameters = connection.executed[0]
        self.assertIn("usage_dashboard_daily", sql)
        self.assertEqual(parameters, {"start_date": date(2026, 7, 12), "end_date": date(2026, 7, 18)})
        engine.dispose.assert_awaited_once()

    async def test_failed_read_still_disposes_engine(self) -> None:
        engine = FakeEngine(error=RuntimeError("database unavailable"))

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            await dashboard_postgres_repository.fetch_site_dashboard_snapshot(
                "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
                start_date="2026-07-18",
                end_date="2026-07-18",
                granularity="hour",
                engine_factory=lambda *_args, **_kwargs: engine,
            )

        engine.dispose.assert_awaited_once()

    async def test_group_hourly_snapshot_aggregates_usage_logs(self) -> None:
        connection = FakeConnection(
            [
                {
                    "bucket": datetime(2026, 7, 18, 1, 0, tzinfo=UTC),
                    "total_requests": 4,
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "cache_creation_tokens": 30,
                    "cache_read_tokens": 40,
                    "total_cost": Decimal("2.5"),
                    "actual_cost": Decimal("2.0"),
                    "account_cost": Decimal("1.5"),
                    "computed_at": datetime(2026, 7, 18, 1, 12, tzinfo=UTC),
                }
            ]
        )
        engine = FakeEngine(connection)

        result = await dashboard_postgres_repository.fetch_group_dashboard_snapshot(
            "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            group_id=3,
            start_date="2026-07-18",
            end_date="2026-07-18",
            granularity="hour",
            engine_factory=lambda *_args, **_kwargs: engine,
        )

        self.assertEqual(result["trend"][0]["total_tokens"], 100)
        sql, parameters = connection.executed[0]
        self.assertIn("usage_logs", sql)
        self.assertIn("group_id = :group_id", sql)
        self.assertEqual(parameters["group_id"], 3)
        engine.dispose.assert_awaited_once()

    async def test_model_statistics_aggregate_usage_logs_without_http(self) -> None:
        connection = FakeConnection(
            [
                {
                    "model": "gpt-5.4",
                    "total_requests": 9,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_tokens": 20,
                    "cache_read_tokens": 30,
                    "total_cost": Decimal("8.5"),
                    "actual_cost": Decimal("8.0"),
                    "account_cost": Decimal("7.5"),
                }
            ]
        )
        engine = FakeEngine(connection)

        models = await dashboard_postgres_repository.fetch_model_statistics(
            "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            start_date="2026-07-12",
            end_date="2026-07-18",
            engine_factory=lambda *_args, **_kwargs: engine,
        )

        self.assertEqual(
            models,
            [
                {
                    "model": "gpt-5.4",
                    "requests": 9,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cache_creation_tokens": 20,
                    "cache_read_tokens": 30,
                    "total_tokens": 200,
                    "cost": 8.5,
                    "actual_cost": 8.0,
                    "account_cost": 7.5,
                }
            ],
        )
        sql, _ = connection.executed[0]
        self.assertIn("usage_logs", sql)
        self.assertIn("GROUP BY", sql)
        engine.dispose.assert_awaited_once()

    async def test_group_hour_counters_are_loaded_in_one_database_query(self) -> None:
        connection = FakeConnection(
            [
                {
                    "group_id": 3,
                    "total_requests": 12,
                    "total_tokens": 345,
                    "total_account_cost": 27.5,
                    "source_updated_at": datetime(2026, 7, 18, 1, 14, tzinfo=UTC),
                },
                {
                    "group_id": 5,
                    "total_requests": 7,
                    "total_tokens": 89,
                    "source_updated_at": datetime(2026, 7, 18, 1, 13, tzinfo=UTC),
                },
            ]
        )
        engine = FakeEngine(connection)

        counters = await dashboard_postgres_repository.fetch_group_hour_counters(
            "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            group_ids=[3, 5, 7],
            sampled_at=datetime(2026, 7, 18, 1, 15, tzinfo=UTC),
            engine_factory=lambda *_args, **_kwargs: engine,
        )

        self.assertEqual(counters[3]["total_tokens"], 345)
        self.assertEqual(counters[3]["total_account_cost"], 27.5)
        self.assertEqual(counters[5]["total_requests"], 7)
        self.assertEqual(counters[7]["total_tokens"], 0)
        self.assertEqual(len(connection.executed), 1)
        sql, parameters = connection.executed[0]
        self.assertIn("group_id = ANY", sql)
        self.assertIn("account_stats_cost", sql)
        self.assertEqual(parameters["group_ids"], [3, 5, 7])
        engine.dispose.assert_awaited_once()

    async def test_group_minute_usage_aggregates_all_groups_with_left_closed_right_open_bounds(self) -> None:
        bucket = datetime(2026, 8, 4, 1, 4, tzinfo=UTC)
        connection = FakeConnection(
            [
                {
                    "group_id": 3,
                    "bucket_at": bucket,
                    "total_requests": 4,
                    "total_tokens": 100,
                    "input_tokens": 40,
                    "output_tokens": 30,
                    "cache_creation_tokens": 10,
                    "cache_read_tokens": 20,
                    "account_cost": Decimal("2.50"),
                    "source_updated_at": datetime(2026, 8, 4, 1, 4, 55, tzinfo=UTC),
                }
            ]
        )
        engine = FakeEngine(connection)

        result = await dashboard_postgres_repository.fetch_group_minute_usage(
            "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            group_ids=[5, 3, 3],
            start_at=datetime(2026, 8, 4, 1, 0, tzinfo=UTC),
            end_at=datetime(2026, 8, 4, 1, 20, tzinfo=UTC),
            engine_factory=lambda *_args, **_kwargs: engine,
        )

        self.assertEqual(result[(3, bucket)]["total_tokens"], 100)
        self.assertEqual(result[(3, bucket)]["total_requests"], 4)
        self.assertEqual(result[(3, bucket)]["account_cost"], 2.5)
        self.assertNotIn((5, bucket), result)
        sql, parameters = connection.executed[0]
        self.assertIn("date_trunc('minute'", sql)
        self.assertIn("group_id = ANY", sql)
        self.assertIn("created_at >= :start_at", sql)
        self.assertIn("created_at < :end_at", sql)
        self.assertEqual(parameters["group_ids"], [3, 5])
        self.assertEqual(parameters["start_at"], datetime(2026, 8, 4, 1, 0, tzinfo=UTC))
        self.assertEqual(parameters["end_at"], datetime(2026, 8, 4, 1, 20, tzinfo=UTC))
        engine.dispose.assert_awaited_once()

    async def test_group_minute_usage_skips_empty_group_ids_and_invalid_ranges(self) -> None:
        engine_factory = MagicMock()
        dsn = "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable"

        self.assertEqual(
            await dashboard_postgres_repository.fetch_group_minute_usage(
                dsn,
                group_ids=[],
                start_at=datetime(2026, 8, 4, 1, 0, tzinfo=UTC),
                end_at=datetime(2026, 8, 4, 1, 20, tzinfo=UTC),
                engine_factory=engine_factory,
            ),
            {},
        )
        self.assertEqual(
            await dashboard_postgres_repository.fetch_group_minute_usage(
                dsn,
                group_ids=[3],
                start_at=datetime(2026, 8, 4, 1, 20, tzinfo=UTC),
                end_at=datetime(2026, 8, 4, 1, 20, tzinfo=UTC),
                engine_factory=engine_factory,
            ),
            {},
        )
        engine_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
