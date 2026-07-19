from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.modules.sub2api import account_usage_postgres_repository


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


class Sub2ApiAccountUsagePostgresRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_windows_match_sub2api_window_starts_and_usage_contract(self) -> None:
        observed_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
        connection = FakeConnection(
            [
                {
                    "account_id": 10,
                    "five_hour_requests": 12,
                    "five_hour_tokens": 345,
                    "five_hour_cost": Decimal("8.25"),
                    "five_hour_standard_cost": Decimal("7.50"),
                    "five_hour_user_cost": Decimal("9.00"),
                    "seven_day_requests": 48,
                    "seven_day_tokens": 1234,
                    "seven_day_cost": Decimal("31.25"),
                    "seven_day_standard_cost": Decimal("30.00"),
                    "seven_day_user_cost": Decimal("34.00"),
                },
                {
                    "account_id": 11,
                    "five_hour_requests": 0,
                    "five_hour_tokens": 0,
                    "five_hour_cost": Decimal("0"),
                    "five_hour_standard_cost": Decimal("0"),
                    "five_hour_user_cost": Decimal("0"),
                    "seven_day_requests": 3,
                    "seven_day_tokens": 30,
                    "seven_day_cost": Decimal("1.50"),
                    "seven_day_standard_cost": Decimal("1.25"),
                    "seven_day_user_cost": Decimal("1.75"),
                },
                {
                    "account_id": 12,
                    "five_hour_requests": 0,
                    "five_hour_tokens": 0,
                    "five_hour_cost": Decimal("0"),
                    "five_hour_standard_cost": Decimal("0"),
                    "five_hour_user_cost": Decimal("0"),
                    "seven_day_requests": 0,
                    "seven_day_tokens": 0,
                    "seven_day_cost": Decimal("0"),
                    "seven_day_standard_cost": Decimal("0"),
                    "seven_day_user_cost": Decimal("0"),
                },
            ]
        )
        engine = FakeEngine(connection)
        engine_factory = MagicMock(return_value=engine)
        accounts = [
            {
                "id": 10,
                "codex_5h_used_percent": 25,
                "codex_5h_reset_at": observed_at + timedelta(hours=2),
                "codex_7d_used_percent": 60,
                "codex_7d_reset_at": observed_at + timedelta(days=3),
                "credentials": {"access_token": "must-not-enter-query"},
            },
            {
                "id": 11,
                "codex_5h_used_percent": 0,
                "codex_5h_reset_at": observed_at - timedelta(minutes=1),
                "codex_7d_used_percent": 10,
            },
            {"id": 12},
        ]

        result = await account_usage_postgres_repository.fetch_account_usage_snapshots(
            "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            accounts=accounts,
            observed_at=observed_at,
            engine_factory=engine_factory,
        )

        self.assertEqual(
            result[10],
            {
                "five_hour": {
                    "utilization": 25,
                    "resets_at": observed_at + timedelta(hours=2),
                    "remaining_seconds": 7200,
                    "window_stats": {
                        "requests": 12,
                        "tokens": 345,
                        "cost": 8.25,
                        "standard_cost": 7.5,
                        "user_cost": 9.0,
                    },
                },
                "seven_day": {
                    "utilization": 60,
                    "resets_at": observed_at + timedelta(days=3),
                    "remaining_seconds": 259200,
                    "window_stats": {
                        "requests": 48,
                        "tokens": 1234,
                        "cost": 31.25,
                        "standard_cost": 30.0,
                        "user_cost": 34.0,
                    },
                },
            },
        )
        self.assertEqual(result[11]["five_hour"]["utilization"], 0)
        self.assertEqual(result[11]["five_hour"]["resets_at"], observed_at - timedelta(minutes=1))
        self.assertIsNone(result[12]["five_hour"])
        self.assertIsNone(result[12]["seven_day"])

        sql, parameters = connection.executed[0]
        self.assertNotIn("select *", sql.lower())
        self.assertIn("usage_logs", sql)
        self.assertIn("jsonb_to_recordset", sql)
        self.assertIn("account_stats_cost", sql)
        windows = json.loads(str(parameters["windows"]))
        self.assertEqual(windows[0]["five_hour_start"], (observed_at - timedelta(hours=3)).isoformat())
        self.assertEqual(windows[0]["seven_day_start"], (observed_at - timedelta(days=4)).isoformat())
        self.assertEqual(windows[1]["five_hour_start"], (observed_at - timedelta(hours=5)).isoformat())
        self.assertEqual(windows[1]["seven_day_start"], (observed_at - timedelta(days=7)).isoformat())
        self.assertNotIn("must-not-enter-query", str(parameters))
        self.assertEqual(engine_factory.call_args.kwargs["isolation_level"], "REPEATABLE READ")
        engine.dispose.assert_awaited_once()

    async def test_failed_database_read_still_disposes_engine(self) -> None:
        engine = FakeEngine(error=RuntimeError("database unavailable"))

        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            await account_usage_postgres_repository.fetch_account_usage_snapshots(
                "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
                accounts=[{"id": 10}],
                observed_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
                engine_factory=lambda *_args, **_kwargs: engine,
            )

        engine.dispose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
