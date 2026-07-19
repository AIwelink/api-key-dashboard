from __future__ import annotations

import unittest
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.modules.sub2api import postgres_repository


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
    def __init__(self, row_sets: list[list[dict[str, object]]]) -> None:
        self.row_sets = list(row_sets)
        self.executed: list[str] = []

    async def execute(self, statement) -> FakeResult:
        self.executed.append(str(statement))
        return FakeResult(self.row_sets.pop(0))


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


class Sub2ApiPostgresRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_pool_snapshot_merges_relations_and_calculates_group_counts(self) -> None:
        created_at = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)
        connection = FakeConnection(
            [
                [
                    {
                        "id": 3,
                        "name": "plus pool",
                        "platform": "openai",
                        "status": "active",
                        "sort_order": 3,
                        "rate_multiplier": Decimal("1.25"),
                    },
                    {
                        "id": 4,
                        "name": "empty pool",
                        "platform": "openai",
                        "status": "active",
                        "sort_order": 4,
                        "rate_multiplier": Decimal("1"),
                    },
                ],
                [
                    {
                        "id": 10,
                        "name": "account-10",
                        "platform": "openai",
                        "type": "oauth",
                        "status": "active",
                        "schedulable": True,
                        "priority": 100,
                        "credentials": {
                            "email": "one@example.com",
                            "plan_type": "plus",
                            "access_token": "must-not-enter-cache",
                        },
                        "extra": {
                            "privacy_mode": "private",
                            "email_session": "needed-for-resurrection",
                            "refresh_token": "must-not-enter-cache",
                        },
                        "concurrency": 10,
                        "rate_multiplier": Decimal("1.5"),
                    },
                    {
                        "id": 11,
                        "name": "account-11",
                        "platform": "openai",
                        "type": "oauth",
                        "status": "active",
                        "schedulable": True,
                        "priority": 100,
                        "credentials": {"email": "two@example.com"},
                        "extra": {},
                        "concurrency": 10,
                        "rate_multiplier": Decimal("1"),
                        "rate_limit_reset_at": datetime(2099, 1, 1, tzinfo=UTC),
                    },
                ],
                [
                    {"account_id": 10, "group_id": 3, "priority": 80, "created_at": created_at},
                    {"account_id": 11, "group_id": 3, "priority": 90, "created_at": created_at},
                ],
            ]
        )
        engine = FakeEngine(connection)
        engine_factory = MagicMock(return_value=engine)

        result = await postgres_repository.fetch_pool_snapshot(
            "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            engine_factory=engine_factory,
        )

        self.assertEqual(result["groups"][0]["account_count"], 2)
        self.assertEqual(result["groups"][0]["active_account_count"], 1)
        self.assertEqual(result["groups"][0]["rate_limited_account_count"], 1)
        self.assertEqual(result["groups"][0]["rate_multiplier"], 1.25)
        self.assertNotIn("account_count", result["groups"][1])
        self.assertNotIn("active_account_count", result["groups"][1])
        self.assertNotIn("rate_limited_account_count", result["groups"][1])
        self.assertEqual(result["accounts"][0]["group_ids"], [3])
        self.assertEqual(result["accounts"][0]["groups"], [{"id": 3, "name": "plus pool", "platform": "openai", "status": "active"}])
        self.assertEqual(
            result["accounts"][0]["account_groups"],
            [{"account_id": 10, "group_id": 3, "priority": 80, "created_at": created_at}],
        )
        self.assertEqual(result["accounts"][0]["rate_multiplier"], 1.5)
        self.assertEqual(result["accounts"][0]["credentials"], {"email": "one@example.com", "plan_type": "plus"})
        self.assertEqual(
            result["accounts"][0]["extra"],
            {"privacy_mode": "private", "email_session": "needed-for-resurrection"},
        )
        self.assertEqual(len(connection.executed), 3)
        for sql in connection.executed:
            self.assertNotIn("select *", sql.lower())
        self.assertIn("deleted_at IS NULL", connection.executed[0])
        self.assertIn("deleted_at IS NULL", connection.executed[1])
        self.assertIn("jsonb_build_object", connection.executed[1])
        self.assertIn("credentials -> 'email'", connection.executed[1])
        self.assertIn("extra -> 'email_session'", connection.executed[1])
        self.assertNotIn("jsonb_each", connection.executed[1])
        self.assertEqual(engine_factory.call_args.kwargs["isolation_level"], "REPEATABLE READ")
        engine.dispose.assert_awaited_once()

    async def test_failed_database_read_still_disposes_engine(self) -> None:
        engine = FakeEngine(error=RuntimeError("connection refused"))

        with self.assertRaisesRegex(RuntimeError, "connection refused"):
            await postgres_repository.fetch_pool_snapshot(
                "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
                engine_factory=lambda *_args, **_kwargs: engine,
            )

        engine.dispose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
