from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx

from app.modules.sub2api import cache
from app.modules.sub2api.client import Sub2ApiClient


SQL_DSN = "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable"


class Sub2ApiUsageRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_pages_after_first_page_are_fetched_in_parallel_for_live_runtime_data(self) -> None:
        remaining_started: set[int] = set()
        all_remaining_started = asyncio.Event()

        async def list_accounts(*, page: int, **_: object) -> dict[str, object]:
            if page == 1:
                return {"items": [{"id": 1}], "total": 450}
            remaining_started.add(page)
            if remaining_started == {2, 3}:
                all_remaining_started.set()
            await all_remaining_started.wait()
            return {"items": [{"id": page}]}

        client = AsyncMock()
        client.list_accounts.side_effect = list_accounts

        accounts = await asyncio.wait_for(cache._fetch_all_accounts(client), timeout=1)

        self.assertEqual([account["id"] for account in accounts], [1, 2, 3])
        self.assertEqual(remaining_started, {2, 3})

    async def test_account_usage_client_method_remains_available_for_explicit_actions(self) -> None:
        requests: list[httpx.Request] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"data": {"updated_at": "now"}})

        client = Sub2ApiClient(base_url="http://127.0.0.1:5001", token="secret")
        transport = httpx.MockTransport(handle)
        async with httpx.AsyncClient(transport=transport) as http_client:
            result = await client.get_account_usage(2976, http_client=http_client)

        self.assertEqual(result, {"updated_at": "now"})
        self.assertEqual(len(requests), 1)

    async def test_database_usage_is_applied_without_http_requests(self) -> None:
        synced_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
        accounts = [
            {"id": 2976, "platform": "openai", "type": "oauth"},
            {"id": 2977, "platform": "openai", "type": "oauth"},
            {"name": "missing remote id"},
        ]
        database_snapshots = {
            2976: {
                "five_hour": {"utilization": 12, "window_stats": {"requests": 20, "tokens": 200, "cost": 2.5}},
                "seven_day": {"utilization": 34, "window_stats": {"requests": 50, "tokens": 500, "cost": 6.5}},
            },
            2977: {
                "five_hour": {"utilization": 20, "window_stats": {"requests": 7}},
                "seven_day": None,
            },
        }
        client = AsyncMock()

        with (
            patch.object(cache, "_restore_cached_usage_snapshots", AsyncMock(return_value={})),
            patch.object(cache, "fetch_postgres_account_usage_snapshots", AsyncMock(return_value=database_snapshots)) as fetch_database,
        ):
            result = await cache._apply_account_usage_windows(
                object(),
                "api-5001",
                client,
                accounts,
                synced_at,
                sql_dsn=SQL_DSN,
                pool_snapshot_source="postgresql",
            )

        fetch_database.assert_awaited_once()
        client.get_account_usage.assert_not_awaited()
        self.assertEqual(accounts[0]["codex_5h_request_count"], 20)
        self.assertEqual(accounts[0]["codex_7d_token_count"], 500)
        self.assertEqual(accounts[1]["codex_5h_used_percent"], 20)
        self.assertEqual(result["source"], "postgresql")
        self.assertEqual(result["database_accounts"], 2)
        self.assertEqual(result["http_accounts"], 0)

    async def test_database_failure_propagates_without_http_fallback(self) -> None:
        client = AsyncMock()
        accounts = [{"id": 2976, "platform": "openai", "type": "oauth"}]
        with (
            patch.object(cache, "_restore_cached_usage_snapshots", AsyncMock(return_value={})),
            patch.object(
                cache,
                "fetch_postgres_account_usage_snapshots",
                AsyncMock(side_effect=RuntimeError("database unavailable")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "database unavailable"):
                await cache._apply_account_usage_windows(
                    object(),
                    "api-5001",
                    client,
                    accounts,
                    datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
                    sql_dsn=SQL_DSN,
                    pool_snapshot_source="postgresql",
                )

        client.get_account_usage.assert_not_awaited()

    async def test_missing_database_row_is_marked_missing_without_http_fallback(self) -> None:
        client = AsyncMock()
        accounts = [{"id": 2976}, {"id": 2977}]
        with (
            patch.object(cache, "_restore_cached_usage_snapshots", AsyncMock(return_value={})),
            patch.object(
                cache,
                "fetch_postgres_account_usage_snapshots",
                AsyncMock(return_value={2976: {"five_hour": {"window_stats": {"requests": 20}}}}),
            ),
        ):
            result = await cache._apply_account_usage_windows(
                object(),
                "api-5001",
                client,
                accounts,
                datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
                sql_dsn=SQL_DSN,
                pool_snapshot_source="postgresql",
            )

        client.get_account_usage.assert_not_awaited()
        self.assertEqual(result["database_accounts"], 1)
        self.assertEqual(result["http_accounts"], 0)

    async def test_missing_sql_dsn_fails_without_http(self) -> None:
        client = AsyncMock()
        with patch.object(cache, "_restore_cached_usage_snapshots", AsyncMock(return_value={})):
            with self.assertRaisesRegex(ValueError, "SQL_DSN"):
                await cache._apply_account_usage_windows(
                    object(),
                    "api-5001",
                    client,
                    [{"id": 2976}],
                    datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
                )
        client.get_account_usage.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
