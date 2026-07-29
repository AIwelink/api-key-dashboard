from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal

from app.modules.sub2api.minute_forecast_repository import fetch_group_minute_observations


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


class MinuteForecastRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_cost_requests_tokens_and_fills_zero_minutes(self) -> None:
        rows = [
            {
                "bucket_at": datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
                "requests": 10,
                "total_tokens": 1000,
                "account_cost": Decimal("1.25"),
            },
            {
                "bucket_at": datetime(2026, 7, 20, 12, 2, tzinfo=UTC),
                "requests": 20,
                "total_tokens": 2000,
                "account_cost": Decimal("2.50"),
            },
        ]
        engine = FakeEngine(rows)

        result = await fetch_group_minute_observations(
            SQL_DSN,
            group_id=3,
            start_at=datetime(2026, 7, 20, 11, 59, 30, tzinfo=UTC),
            end_at=datetime(2026, 7, 20, 12, 4, 59, tzinfo=UTC),
            engine_factory=lambda *_args, **_kwargs: engine,
        )

        self.assertEqual(
            [item.bucket_at for item in result],
            [datetime(2026, 7, 20, 12, minute, tzinfo=UTC) for minute in range(4)],
        )
        self.assertEqual([item.account_cost for item in result], [1.25, 0.0, 2.5, 0.0])
        self.assertEqual([item.requests for item in result], [10.0, 0.0, 20.0, 0.0])
        self.assertEqual([item.total_tokens for item in result], [1000.0, 0.0, 2000.0, 0.0])
        self.assertTrue(engine.disposed)

    async def test_uses_parameterized_half_open_natural_minute_range(self) -> None:
        engine = FakeEngine([])

        result = await fetch_group_minute_observations(
            SQL_DSN,
            group_id=9,
            start_at=datetime(2026, 7, 20, 12, 0, 59, tzinfo=UTC),
            end_at=datetime(2026, 7, 20, 13, 0, 42, tzinfo=UTC),
            engine_factory=lambda *_args, **_kwargs: engine,
        )

        self.assertEqual(result, [])
        self.assertIn("group_id = :group_id", engine.connection.query)
        self.assertIn("created_at >= :start_at", engine.connection.query)
        self.assertIn("created_at < :end_at", engine.connection.query)
        self.assertIn("date_trunc('minute'", engine.connection.query)
        self.assertIn("account_stats_cost", engine.connection.query)
        self.assertEqual(engine.connection.parameters["group_id"], 9)
        self.assertEqual(engine.connection.parameters["start_at"], datetime(2026, 7, 20, 12, 0, tzinfo=UTC))
        self.assertEqual(engine.connection.parameters["end_at"], datetime(2026, 7, 20, 13, 0, tzinfo=UTC))


if __name__ == "__main__":
    unittest.main()
