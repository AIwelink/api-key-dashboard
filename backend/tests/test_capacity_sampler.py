from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from app.modules.sub2api import capacity_sampler
from app.modules.system import bootstrap


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self._items = items

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self._items:
            yield item


class CapacitySnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_snapshot_uses_five_minute_bucket_and_compact_scalar_metrics(self) -> None:
        samples = SimpleNamespace(replace_one=AsyncMock())
        db = SimpleNamespace(sub2api_capacity_samples=samples)
        sampled_at = datetime(2026, 7, 18, 6, 53, 42, tzinfo=UTC)
        fetched_at = datetime(2026, 7, 18, 6, 52, 10, tzinfo=UTC)
        group_doc = {
            "group_id": 3,
            "fetched_at": fetched_at,
            "group": {
                "id": 3,
                "name": "plus-pool-01",
                "status": "active",
                "capacity_summary": {"stale": True},
            },
        }
        summary = {
            "health_status": "abundant",
            "dynamic_runway_hours": 16,
            "concurrency_coverage": 3.4,
            "pool_active_normal_accounts": 6,
            "account_type": "plus",
            "current_consumption_rate_usd_per_hour": 640.0,
            "current_consumption_rate_source": "current_hour_prorated",
            "current_consumption_rate_elapsed_minutes": 30.0,
            "current_consumption_rate_hour": "2026-07-18T06:00:00+00:00",
            "type_summary": {
                "plus": {
                    "available_accounts": 126,
                    "available_5h_accounts": 120,
                    "five_hour_capacity_usd": 13860,
                    "five_hour_dynamic_remaining_usd": 12000,
                    "five_hour_actual_remaining_usd": 11000,
                    "seven_day_capacity_usd": 13860,
                    "seven_day_dynamic_remaining_usd": 10000,
                    "seven_day_actual_remaining_usd": 9000,
                },
                "k12": {"available_accounts": 0},
                "total": {"available_accounts": 126},
            },
            "capacity_limits": {
                "plus": {"five_hour_usd": 110, "seven_day_usd": 110},
                "k12": {"five_hour_usd": 20, "seven_day_usd": 100},
            },
            "recommended_refill_options": {
                "plus": {"account_type": "plus", "recommended_refill_accounts": 4},
            },
            "calculated_at": sampled_at,
        }

        with patch.object(
            capacity_sampler,
            "_get_or_update_group_capacity_summary",
            AsyncMock(return_value=summary),
        ) as calculate:
            result = await capacity_sampler.sample_group_capacity(
                db,
                site_id="api-5001",
                group_doc=group_doc,
                sampled_at=sampled_at,
            )

        calculate.assert_awaited_once_with(db, "api-5001", 3)
        samples.replace_one.assert_awaited_once()
        query, document = samples.replace_one.await_args.args
        bucket_at = datetime(2026, 7, 18, 6, 50, tzinfo=UTC)
        self.assertEqual(query, {"_id": "api-5001:3:2026-07-18T06:50:00Z"})
        self.assertEqual(document["bucket_at"], bucket_at)
        self.assertEqual(document["sampled_at"], sampled_at)
        self.assertEqual(document["account_cache_fetched_at"], fetched_at)
        self.assertEqual(
            document["metrics"],
            {
                "health_status": "abundant",
                "dynamic_runway_hours": 16,
                "concurrency_coverage": 3.4,
                "pool_active_normal_accounts": 6,
                "account_type": "plus",
                "current_consumption_rate_usd_per_hour": 640.0,
                "current_consumption_rate_source": "current_hour_prorated",
                "current_consumption_rate_elapsed_minutes": 30.0,
                "current_consumption_rate_hour": "2026-07-18T06:00:00+00:00",
                "calculated_at": sampled_at,
            },
        )
        self.assertEqual(
            document["dimensions"],
            {
                "account_types": [
                    {
                        "account_type": "plus",
                        "available_accounts": 126,
                        "available_5h_accounts": 120,
                        "five_hour_capacity_usd": 13860,
                        "five_hour_dynamic_remaining_usd": 12000,
                        "five_hour_actual_remaining_usd": 11000,
                        "seven_day_capacity_usd": 13860,
                        "seven_day_dynamic_remaining_usd": 10000,
                        "seven_day_actual_remaining_usd": 9000,
                    }
                ],
                "capacity_limits": [
                    {"account_type": "plus", "five_hour_usd": 110, "seven_day_usd": 110},
                ],
                "refill_options": [
                    {"account_type": "plus", "recommended_refill_accounts": 4},
                ],
            },
        )
        self.assertNotIn("capacity_summary", document)
        self.assertNotIn("group", document)
        self.assertEqual(document["schema_version"], 2)
        self.assertEqual(document["expires_at"], sampled_at + timedelta(days=30))
        self.assertEqual(result["sample"]["_id"], document["_id"])


class CapacitySiteSamplingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        capacity_sampler._site_sample_locks.clear()

    async def test_groups_run_in_parallel_and_one_failure_is_isolated(self) -> None:
        groups = SimpleNamespace(
            find=lambda *_args, **_kwargs: AsyncCursor(
                [
                    {"group_id": 3, "group": {"id": 3}},
                    {"group_id": 5, "group": {"id": 5}},
                    {"group_id": 7, "group": {"id": 7}},
                ]
            )
        )
        db = SimpleNamespace(sub2api_groups_cache=groups)
        started: set[int] = set()
        all_started = asyncio.Event()

        async def sample_group(*_args, group_doc: dict[str, object], **_kwargs):
            group_id = int(group_doc["group_id"])
            started.add(group_id)
            if len(started) == 3:
                all_started.set()
            await all_started.wait()
            if group_id == 5:
                raise RuntimeError("group unavailable")
            return {"ok": True, "group_id": group_id}

        with (
            patch.object(
                capacity_sampler,
                "get_site",
                AsyncMock(return_value={"id": "api-5001", "status": "active", "site_type": "sub2api"}),
            ),
            patch.object(capacity_sampler, "sample_group_capacity", AsyncMock(side_effect=sample_group)),
        ):
            result = await asyncio.wait_for(capacity_sampler.sample_site_capacity(db, site_id="api-5001"), timeout=1)

        self.assertEqual(started, {3, 5, 7})
        self.assertTrue(result["ok"])
        self.assertEqual(result["groups"], 3)
        self.assertEqual(result["sampled"], 2)
        self.assertEqual(result["failed"], 1)


class CapacitySchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_sampler_runs_immediately_and_cancellation_propagates(self) -> None:
        sample_all = AsyncMock(return_value={"ok": True, "sites": 1})
        sleep = AsyncMock(side_effect=asyncio.CancelledError)

        with (
            patch.object(capacity_sampler, "sample_all_sites_capacity", sample_all),
            patch.object(capacity_sampler.asyncio, "sleep", sleep),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await capacity_sampler.capacity_sampler_loop(object())

        sample_all.assert_awaited_once()
        sleep.assert_awaited_once()
        sleep_seconds = sleep.await_args.args[0]
        self.assertGreater(sleep_seconds, 0)
        self.assertLessEqual(sleep_seconds, 300)


class CapacitySampleIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_sample_indexes_include_unique_bucket_ttl_and_history_lookup(self) -> None:
        collection = SimpleNamespace(create_index=AsyncMock())
        db = SimpleNamespace(sub2api_capacity_samples=collection)

        await bootstrap.ensure_capacity_sample_indexes(db)

        collection.create_index.assert_has_awaits(
            [
                call([("site_id", 1), ("group_id", 1), ("bucket_at", 1)], unique=True),
                call("expires_at", expireAfterSeconds=0),
                call([("site_id", 1), ("group_id", 1), ("sampled_at", -1)]),
            ]
        )
        self.assertEqual(collection.create_index.await_count, 3)


if __name__ == "__main__":
    unittest.main()
