from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from app.modules.sub2api.hourly_forecast_repository import fetch_group_hourly_observations


SQL_DSN = "host=localhost user=user password=password dbname=database sslmode=disable"


class FakeMappings:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return FakeMappings(self.rows)


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.parameters = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, query, parameters):
        self.query = str(query)
        self.parameters = parameters
        return FakeResult(self.rows)


class FakeEngine:
    def __init__(self, rows):
        self.connection = FakeConnection(rows)
        self.disposed = False

    def connect(self):
        return self.connection

    async def dispose(self):
        self.disposed = True


class HourlyForecastRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_account_cost_and_fills_complete_zero_hours(self) -> None:
        rows = [
            {
                "bucket_at": datetime(2026, 7, 19, 6, tzinfo=UTC),
                "requests": 10,
                "total_tokens": 1000,
                "account_cost": Decimal("12.5"),
            },
            {
                "bucket_at": datetime(2026, 7, 19, 8, tzinfo=UTC),
                "requests": 20,
                "total_tokens": 2000,
                "account_cost": Decimal("25.0"),
            },
        ]
        engine = FakeEngine(rows)

        result = await fetch_group_hourly_observations(
            SQL_DSN,
            group_id=3,
            start_at=datetime(2026, 7, 19, 0, 15, tzinfo=UTC),
            end_at=datetime(2026, 7, 19, 10, 37, tzinfo=UTC),
            engine_factory=lambda *_args, **_kwargs: engine,
        )

        self.assertEqual(
            [item.bucket_at for item in result],
            [datetime(2026, 7, 19, hour, tzinfo=UTC) for hour in range(6, 10)],
        )
        self.assertEqual([item.account_cost for item in result], [12.5, 0.0, 25.0, 0.0])
        self.assertEqual(result[0].requests, 10)
        self.assertEqual(result[0].total_tokens, 1000)
        self.assertEqual(result[1].requests, 0)
        self.assertNotIn(datetime(2026, 7, 19, 10, tzinfo=UTC), [item.bucket_at for item in result])
        self.assertTrue(engine.disposed)

    async def test_uses_parameterized_group_and_half_open_complete_hour_range(self) -> None:
        engine = FakeEngine([])
        start_at = datetime(2026, 7, 1, 0, 33, tzinfo=UTC)
        end_at = datetime(2026, 7, 2, 5, 59, tzinfo=UTC)

        await fetch_group_hourly_observations(
            SQL_DSN,
            group_id=9,
            start_at=start_at,
            end_at=end_at,
            engine_factory=lambda *_args, **_kwargs: engine,
        )

        self.assertIn("group_id = :group_id", engine.connection.query)
        self.assertIn("created_at >= :start_at", engine.connection.query)
        self.assertIn("created_at < :end_at", engine.connection.query)
        self.assertIn("account_stats_cost", engine.connection.query)
        self.assertEqual(engine.connection.parameters["group_id"], 9)
        self.assertEqual(engine.connection.parameters["start_at"], datetime(2026, 7, 1, 0, tzinfo=UTC))
        self.assertEqual(engine.connection.parameters["end_at"], datetime(2026, 7, 2, 5, tzinfo=UTC))

    async def test_empty_source_does_not_invent_pre_creation_zero_history(self) -> None:
        engine = FakeEngine([])

        result = await fetch_group_hourly_observations(
            SQL_DSN,
            group_id=3,
            start_at=datetime(2026, 7, 1, tzinfo=UTC),
            end_at=datetime(2026, 7, 20, tzinfo=UTC),
            engine_factory=lambda *_args, **_kwargs: engine,
        )

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
