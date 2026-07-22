from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
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


class Sub2ApiRefreshRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        cache._refresh_tasks.clear()

    async def test_refresh_starts_without_debounce_sleep(self) -> None:
        db = SimpleNamespace(sub2api_cache_meta=SimpleNamespace(update_one=AsyncMock()))
        refresh = AsyncMock(return_value={"ok": True, "site_id": "api-5001"})

        with (
            patch.object(cache, "refresh_site_cache", refresh),
            patch.object(cache.asyncio, "sleep", AsyncMock()) as sleep,
        ):
            result = await cache.request_debounced_refresh(db, "api-5001")

        sleep.assert_not_awaited()
        refresh.assert_awaited_once_with(db, "api-5001")
        self.assertEqual(result, {"ok": True, "site_id": "api-5001"})

    async def test_overlapping_requests_share_one_refresh(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        db = SimpleNamespace(sub2api_cache_meta=SimpleNamespace(update_one=AsyncMock()))

        async def refresh(_db: object, site_id: str) -> dict[str, object]:
            started.set()
            await release.wait()
            return {"ok": True, "site_id": site_id}

        with patch.object(cache, "refresh_site_cache", side_effect=refresh) as refresh_mock:
            first = asyncio.create_task(cache.request_debounced_refresh(db, "api-5001"))
            await started.wait()
            second = asyncio.create_task(cache.request_debounced_refresh(db, "api-5001"))
            await asyncio.sleep(0)
            release.set()
            first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(refresh_mock.await_count, 1)
        self.assertEqual(first_result, second_result)

    async def test_completed_refresh_can_run_again_immediately(self) -> None:
        db = SimpleNamespace(sub2api_cache_meta=SimpleNamespace(update_one=AsyncMock()))
        refresh = AsyncMock(side_effect=[{"run": 1}, {"run": 2}])

        with patch.object(cache, "refresh_site_cache", refresh):
            first = await cache.request_debounced_refresh(db, "api-5001")
            second = await cache.request_debounced_refresh(db, "api-5001")

        self.assertEqual(first, {"run": 1})
        self.assertEqual(second, {"run": 2})
        self.assertEqual(refresh.await_count, 2)


class Sub2ApiQuotaDetectionHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_hook_passes_normalized_accounts_and_resolver(self) -> None:
        observed_at = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
        accounts = [{"id": 1, "plan_type": "plus"}]
        detector = AsyncMock(return_value={"status": "ok", "accepted": 1})
        with patch.object(cache, "observe_account_quota_limits", detector):
            result = await cache._observe_quota_limits_after_usage_refresh(
                object(), site_id="api-5001", accounts=accounts, observed_at=observed_at
            )
        self.assertEqual(result["accepted"], 1)
        detector.assert_awaited_once_with(
            unittest.mock.ANY,
            site_id="api-5001",
            accounts=accounts,
            observed_at=observed_at,
            account_type_for=cache._quota_detection_account_type,
        )

    async def test_hook_failure_is_best_effort(self) -> None:
        with patch.object(cache, "observe_account_quota_limits", AsyncMock(side_effect=RuntimeError("mongo unavailable"))):
            result = await cache._observe_quota_limits_after_usage_refresh(
                object(), site_id="api-5001", accounts=[], observed_at=datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
            )
        self.assertEqual(result, {"ok": False, "site_id": "api-5001", "status": "failed", "error_type": "RuntimeError"})


if __name__ == "__main__":
    unittest.main()
