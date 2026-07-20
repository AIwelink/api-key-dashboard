from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from app.modules.sub2api import tpm_sampler
from app.modules.system import bootstrap


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self._items = items

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self._items:
            yield item


class TpmSampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_sample_uses_postgres_counter_delta(self) -> None:
        samples = SimpleNamespace(
            find_one=AsyncMock(
                return_value={
                    "schema_version": 2,
                    "sampled_at": datetime(2026, 7, 16, 6, 52, 42, tzinfo=UTC),
                    "total_tokens": 1_000,
                    "total_requests": 10,
                    "total_account_cost": 20.0,
                }
            ),
            replace_one=AsyncMock(),
        )
        db = SimpleNamespace(sub2api_tpm_samples=samples)
        sampled_at = datetime(2026, 7, 16, 6, 53, 42, tzinfo=UTC)

        result = await tpm_sampler.sample_group_tpm(
            db,
            site_id="api-5001",
            group_id=5,
            sampled_at=sampled_at,
            counters={
                "total_tokens": 1_600,
                "total_requests": 18,
                "total_account_cost": 25.0,
                "source_updated_at": datetime(2026, 7, 16, 6, 53, 30, tzinfo=UTC),
            },
            current_concurrency=17,
        )

        samples.find_one.assert_awaited_once_with(
            {
                "site_id": "api-5001",
                "group_id": 5,
                "schema_version": 2,
                "counter_source": "postgresql_usage_logs",
                "bucket_at": {"$lt": datetime(2026, 7, 16, 6, 53, tzinfo=UTC)},
            },
            sort=[("bucket_at", -1)],
        )
        samples.replace_one.assert_awaited_once()
        document = samples.replace_one.await_args.args[1]
        self.assertEqual(document["tpm"], 600.0)
        self.assertEqual(document["rpm"], 8.0)
        self.assertEqual(document["account_cost_delta"], 5.0)
        self.assertEqual(document["account_cost_per_minute"], 5.0)
        self.assertEqual(document["account_cost_per_hour"], 300.0)
        self.assertEqual(document["current_concurrency"], 17.0)
        self.assertEqual(document["source"], "postgresql_group_counter_delta")
        self.assertEqual(document["counter_source"], "postgresql_usage_logs")
        self.assertEqual(document["stats_updated_at"], datetime(2026, 7, 16, 6, 53, 30, tzinfo=UTC))
        self.assertEqual(document["expires_at"], sampled_at + timedelta(days=60))
        self.assertEqual(result["sample"]["_id"], document["_id"])

    async def test_counter_reset_does_not_create_negative_tpm(self) -> None:
        samples = SimpleNamespace(
            find_one=AsyncMock(
                return_value={
                    "sampled_at": datetime(2026, 7, 16, 6, 52, tzinfo=UTC),
                    "total_tokens": 2_000,
                    "total_requests": 20,
                }
            ),
            replace_one=AsyncMock(),
        )
        db = SimpleNamespace(sub2api_tpm_samples=samples)

        result = await tpm_sampler.sample_group_tpm(
            db,
            site_id="api-5001",
            group_id=3,
            sampled_at=datetime(2026, 7, 16, 6, 54, tzinfo=UTC),
            counters={"total_tokens": 100, "total_requests": 1},
        )

        self.assertIsNone(result["sample"]["tpm"])
        self.assertIsNone(result["sample"]["rpm"])
        self.assertEqual(result["sample"]["source"], "unavailable")


class TpmSiteSamplingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tpm_sampler._site_sample_locks.clear()

    async def test_all_group_counters_use_one_postgres_query(self) -> None:
        groups = SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([{"group_id": 3}, {"group_id": 5}]))
        db = SimpleNamespace(sub2api_groups_cache=groups)
        accounts = [
            {"id": 1, "group_ids": [3], "current_concurrency": 2},
            {"id": 2, "groups": [{"id": 3}, {"id": 5}], "current_concurrency": 4},
        ]
        fetch_counters = AsyncMock(
            return_value={
                3: {"total_tokens": 100, "total_requests": 10},
                5: {"total_tokens": 50, "total_requests": 5},
            }
        )
        sample_group = AsyncMock(side_effect=lambda *_args, group_id, **_kwargs: {"ok": True, "group_id": group_id})

        with (
            patch.object(
                tpm_sampler,
                "get_site",
                AsyncMock(
                    return_value={
                        "id": "api-5001",
                        "base_url": "http://127.0.0.1:5001",
                        "token": "secret",
                        "sql_dsn": "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
                    }
                ),
            ),
            patch.object(tpm_sampler, "_fetch_all_accounts", AsyncMock(return_value=accounts)),
            patch.object(tpm_sampler, "update_cached_account_runtime_fields", AsyncMock(return_value={"updated": 2})),
            patch.object(tpm_sampler, "fetch_group_hour_counters", fetch_counters),
            patch.object(tpm_sampler, "sample_group_tpm", sample_group),
        ):
            result = await tpm_sampler.sample_site_tpm(db, site_id="api-5001")

        self.assertTrue(result["ok"])
        fetch_counters.assert_awaited_once()
        self.assertEqual(fetch_counters.await_args.kwargs["group_ids"], [3, 5])
        counters_by_group = {
            item.kwargs["group_id"]: item.kwargs["counters"]
            for item in sample_group.await_args_list
        }
        self.assertEqual(counters_by_group[3]["total_tokens"], 100)
        self.assertEqual(counters_by_group[5]["total_tokens"], 50)

    async def test_missing_sql_dsn_does_not_fall_back_to_group_dashboard_urls(self) -> None:
        groups = SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([{"group_id": 3}]))
        db = SimpleNamespace(sub2api_groups_cache=groups)
        with patch.object(
            tpm_sampler,
            "get_site",
            AsyncMock(return_value={"id": "api-5001", "base_url": "http://127.0.0.1:5001", "token": "secret"}),
        ):
            result = await tpm_sampler.sample_site_tpm(db, site_id="api-5001")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "sql_dsn_not_configured")

    async def test_same_site_overlap_is_skipped(self) -> None:
        groups = SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([{"group_id": 3}]))
        db = SimpleNamespace(sub2api_groups_cache=groups)
        started = asyncio.Event()
        release = asyncio.Event()

        async def fetch_counters(*_args, **_kwargs):
            started.set()
            await release.wait()
            return {3: {"total_tokens": 1, "total_requests": 1}}

        with (
            patch.object(
                tpm_sampler,
                "get_site",
                AsyncMock(return_value={"id": "api-5001", "sql_dsn": "host=db user=u password=p dbname=d"}),
            ),
            patch.object(tpm_sampler, "_fetch_all_accounts", AsyncMock(return_value=[])),
            patch.object(tpm_sampler, "fetch_group_hour_counters", AsyncMock(side_effect=fetch_counters)),
            patch.object(tpm_sampler, "sample_group_tpm", AsyncMock(return_value={"ok": True, "group_id": 3})),
        ):
            first = asyncio.create_task(tpm_sampler.sample_site_tpm(db, site_id="api-5001"))
            await started.wait()
            second = await tpm_sampler.sample_site_tpm(db, site_id="api-5001")
            release.set()
            await first

        self.assertEqual(second["status"], "skipped")


class TpmAllSitesSamplingTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_active_sub2api_sites_are_sampled(self) -> None:
        with (
            patch.object(
                tpm_sampler,
                "list_sites",
                AsyncMock(
                    return_value={
                        "items": [
                            {"id": "api-5001", "status": "active"},
                            {"id": "api-disabled", "status": "disabled"},
                            {"id": "api-5002", "status": "active", "site_type": "sub2api"},
                        ]
                    }
                ),
            ),
            patch.object(
                tpm_sampler,
                "sample_site_tpm",
                AsyncMock(return_value={"ok": True, "sampled": 2, "failed": 0}),
            ) as sample_site,
        ):
            result = await tpm_sampler.sample_all_sites_tpm(object())

        self.assertEqual(result["sites"], 2)
        self.assertEqual(sample_site.await_count, 2)


class TpmSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_sampler_runs_immediately_and_cancellation_propagates(self) -> None:
        sample_all = AsyncMock(return_value={"ok": True, "sites": 1})
        sleep = AsyncMock(side_effect=asyncio.CancelledError)
        with (
            patch.object(tpm_sampler, "sample_all_sites_tpm", sample_all),
            patch.object(tpm_sampler.asyncio, "sleep", sleep),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await tpm_sampler.tpm_sampler_loop(object())
        sample_all.assert_awaited_once()


class TpmIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_tpm_indexes_include_unique_minute_bucket_and_ttl(self) -> None:
        collection = SimpleNamespace(create_index=AsyncMock())
        db = SimpleNamespace(sub2api_tpm_samples=collection)

        await bootstrap.ensure_tpm_indexes(db)

        collection.create_index.assert_has_awaits(
            [
                call([("site_id", 1), ("group_id", 1), ("bucket_at", 1)], unique=True),
                call("expires_at", expireAfterSeconds=0),
                call([("site_id", 1), ("group_id", 1), ("sampled_at", -1)]),
            ]
        )


if __name__ == "__main__":
    unittest.main()
