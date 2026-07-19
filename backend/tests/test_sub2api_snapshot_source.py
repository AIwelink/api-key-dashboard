from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.sub2api import cache


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class Sub2ApiSnapshotSourceTests(unittest.IsolatedAsyncioTestCase):
    def test_http_snapshot_marks_runtime_fresh_while_database_keeps_original_timestamp(self) -> None:
        fetched_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
        previous_runtime_at = fetched_at - timedelta(minutes=1)

        self.assertEqual(
            cache._runtime_fetched_at_for_snapshot("http_fallback", 10, fetched_at, {10: previous_runtime_at}),
            fetched_at,
        )
        self.assertEqual(
            cache._runtime_fetched_at_for_snapshot("postgresql", 10, fetched_at, {10: previous_runtime_at}),
            previous_runtime_at,
        )

    def test_cached_http_runtime_fields_are_preserved_when_database_has_no_replacement(self) -> None:
        account = {"id": 10, "current_concurrency": None}
        cached = {
            "current_concurrency": 7,
            "credentials_status": {"state": "valid"},
            "current_rpm": 12,
            "access_token": "must-not-copy-unlisted-fields",
        }

        copied_at = cache._copy_cached_http_runtime_fields(
            account,
            cached,
            runtime_fetched_at=datetime.now(UTC),
        )

        self.assertEqual(account["current_concurrency"], 7)
        self.assertEqual(account["credentials_status"], {"state": "valid"})
        self.assertEqual(account["current_rpm"], 12)
        self.assertNotIn("access_token", account)
        self.assertIsNotNone(copied_at)

    def test_stale_cached_http_runtime_fields_are_not_preserved(self) -> None:
        account = {"id": 10, "current_concurrency": None}

        cache._copy_cached_http_runtime_fields(
            account,
            {"current_concurrency": 7},
            runtime_fetched_at=datetime.now(UTC) - timedelta(minutes=4),
        )

        self.assertIsNone(account["current_concurrency"])

    async def test_runtime_restore_returns_original_runtime_timestamp(self) -> None:
        runtime_fetched_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
        accounts_cache = SimpleNamespace(
            find=lambda *_args, **_kwargs: AsyncCursor(
                [
                    {
                        "sub2api_account_id": 10,
                        "runtime_fetched_at": runtime_fetched_at,
                        "account": {"id": 10, "current_concurrency": 7},
                    }
                ]
            )
        )
        db = SimpleNamespace(sub2api_accounts_cache=accounts_cache)
        accounts = [{"id": 10, "current_concurrency": None}]

        with patch.object(cache, "now_utc", return_value=runtime_fetched_at + timedelta(seconds=30)):
            restored = await cache._restore_cached_usage_snapshots(db, "api-5001", accounts)

        self.assertEqual(restored, {10: runtime_fetched_at})
        self.assertEqual(accounts[0]["current_concurrency"], 7)

    async def test_runtime_cache_update_writes_only_allowlisted_fields(self) -> None:
        accounts_cache = SimpleNamespace(bulk_write=AsyncMock())
        db = SimpleNamespace(sub2api_accounts_cache=accounts_cache)
        observed_at = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)

        result = await cache.update_cached_account_runtime_fields(
            db,
            "api-5001",
            [
                {
                    "id": 10,
                    "current_concurrency": 7,
                    "current_rpm": 12,
                    "credentials_status": {"state": "valid"},
                    "access_token": "must-not-write",
                }
            ],
            observed_at=observed_at,
        )

        self.assertEqual(result, {"updated": 1, "observed_at": observed_at})
        operation = accounts_cache.bulk_write.await_args.args[0][0]
        self.assertEqual(operation._filter, {"site_id": "api-5001", "sub2api_account_id": 10})
        updates = operation._doc["$set"]
        self.assertEqual(updates["account.current_concurrency"], 7)
        self.assertEqual(updates["account.current_rpm"], 12)
        self.assertEqual(updates["runtime_fetched_at"], observed_at)
        self.assertNotIn("account.access_token", updates)
        self.assertEqual(operation._doc["$unset"]["account.active_sessions"], "")

    async def test_runtime_cache_update_waits_for_site_refresh_lock(self) -> None:
        site_id = "runtime-lock-test"
        accounts_cache = SimpleNamespace(bulk_write=AsyncMock())
        db = SimpleNamespace(sub2api_accounts_cache=accounts_cache)
        lock = cache._site_locks.setdefault(site_id, asyncio.Lock())
        await lock.acquire()
        try:
            task = asyncio.create_task(
                cache.update_cached_account_runtime_fields(
                    db,
                    site_id,
                    [{"id": 10, "current_concurrency": 1}],
                )
            )
            await asyncio.sleep(0)
            accounts_cache.bulk_write.assert_not_awaited()
            lock.release()
            await asyncio.wait_for(task, timeout=1)
        finally:
            if lock.locked():
                lock.release()
            cache._site_locks.pop(site_id, None)

        accounts_cache.bulk_write.assert_awaited_once()

    async def test_configured_database_is_preferred_over_http(self) -> None:
        site = {
            "sql_dsn": "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
        }
        client = AsyncMock()
        database_snapshot = {"groups": [{"id": 3}], "accounts": [{"id": 10}]}

        with patch.object(cache, "fetch_postgres_pool_snapshot", AsyncMock(return_value=database_snapshot)) as fetch_database:
            result = await cache._fetch_pool_snapshot(site, client)

        self.assertEqual(result["source"], "postgresql")
        self.assertEqual(result["groups"], [{"id": 3}])
        self.assertEqual(result["accounts"], [{"id": 10}])
        fetch_database.assert_awaited_once_with(site["sql_dsn"])
        client.list_groups.assert_not_awaited()
        client.list_accounts.assert_not_awaited()

    async def test_database_failure_falls_back_to_http_with_redacted_reason(self) -> None:
        dsn = "host=postgres.internal user=reader password=topsecret dbname=sub2api sslmode=disable"
        site = {"sql_dsn": dsn}
        client = AsyncMock()
        client.list_groups.return_value = {"items": [{"id": 3}]}

        with (
            patch.object(cache, "fetch_postgres_pool_snapshot", AsyncMock(side_effect=RuntimeError(f"failed with {dsn}"))),
            patch.object(cache, "_fetch_all_accounts", AsyncMock(return_value=[{"id": 10}])),
        ):
            result = await cache._fetch_pool_snapshot(site, client)

        self.assertEqual(result["source"], "http_fallback")
        self.assertEqual(result["groups"], [{"id": 3}])
        self.assertEqual(result["accounts"], [{"id": 10}])
        self.assertIn("***", result["fallback_reason"])
        self.assertNotIn("topsecret", result["fallback_reason"])
        self.assertNotIn(dsn, result["fallback_reason"])

    async def test_site_without_database_uses_http(self) -> None:
        client = AsyncMock()
        client.list_groups.return_value = {"items": [{"id": 3}]}

        with patch.object(cache, "_fetch_all_accounts", AsyncMock(return_value=[{"id": 10}])):
            result = await cache._fetch_pool_snapshot({}, client)

        self.assertEqual(result["source"], "http")
        self.assertNotIn("fallback_reason", result)


if __name__ == "__main__":
    unittest.main()
