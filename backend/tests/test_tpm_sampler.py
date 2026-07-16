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
    async def test_reported_tpm_is_stored_for_the_target_group(self) -> None:
        samples = SimpleNamespace(
            find_one=AsyncMock(return_value=None),
            replace_one=AsyncMock(),
        )
        db = SimpleNamespace(sub2api_tpm_samples=samples)
        client = AsyncMock()
        client.get_dashboard_snapshot.return_value = {
            "stats": {
                "stats_updated_at": "2026-07-16T06:53:09Z",
                "rpm": 45,
                "tpm": 497365,
                "total_input_tokens": 17355853370,
                "total_output_tokens": 1294748591,
                "total_cache_creation_tokens": 22572283,
                "total_cache_read_tokens": 113532556448,
                "total_tokens": 132205730692,
            }
        }
        sampled_at = datetime(2026, 7, 16, 6, 53, 42, tzinfo=UTC)

        result = await tpm_sampler.sample_group_tpm(
            db,
            site_id="api-5001",
            group_id=5,
            client=client,
            sampled_at=sampled_at,
        )

        client.get_dashboard_snapshot.assert_awaited_once_with(
            start_date="2026-07-16",
            end_date="2026-07-16",
            granularity="hour",
            timezone="Asia/Shanghai",
            include_stats=True,
            include_trend=False,
            include_model_stats=False,
            include_group_stats=False,
            include_users_trend=False,
            group_id=5,
        )
        samples.find_one.assert_awaited_once_with(
            {
                "site_id": "api-5001",
                "group_id": 5,
                "bucket_at": {"$lt": datetime(2026, 7, 16, 6, 53, tzinfo=UTC)},
            },
            sort=[("bucket_at", -1)],
        )
        samples.replace_one.assert_awaited_once()
        query, document = samples.replace_one.await_args.args
        self.assertEqual(query, {"_id": "api-5001:5:2026-07-16T06:53:00Z"})
        self.assertEqual(document["tpm"], 497365.0)
        self.assertEqual(document["reported_tpm"], 497365.0)
        self.assertIsNone(document["calculated_tpm"])
        self.assertEqual(document["rpm"], 45.0)
        self.assertEqual(document["source"], "reported")
        self.assertEqual(document["group_id"], 5)
        self.assertEqual(document["input_tokens"], 17355853370)
        self.assertEqual(document["expires_at"], sampled_at + timedelta(days=14))
        self.assertEqual(result["sample"]["_id"], document["_id"])

    async def test_missing_reported_tpm_uses_token_delta_over_actual_elapsed_time(self) -> None:
        previous_at = datetime(2026, 7, 16, 6, 52, tzinfo=UTC)
        samples = SimpleNamespace(
            find_one=AsyncMock(
                return_value={
                    "sampled_at": previous_at,
                    "total_tokens": 1000,
                }
            ),
            replace_one=AsyncMock(),
        )
        db = SimpleNamespace(sub2api_tpm_samples=samples)
        client = AsyncMock()
        client.get_dashboard_snapshot.return_value = {
            "stats": {
                "rpm": 8,
                "total_tokens": 1600,
            }
        }

        result = await tpm_sampler.sample_group_tpm(
            db,
            site_id="api-5001",
            group_id=3,
            client=client,
            sampled_at=datetime(2026, 7, 16, 6, 54, tzinfo=UTC),
        )

        sample = result["sample"]
        self.assertIsNone(sample["reported_tpm"])
        self.assertEqual(sample["calculated_tpm"], 300.0)
        self.assertEqual(sample["tpm"], 300.0)
        self.assertEqual(sample["token_delta"], 600)
        self.assertEqual(sample["elapsed_seconds"], 120.0)
        self.assertEqual(sample["source"], "calculated")

    async def test_counter_reset_does_not_create_negative_tpm(self) -> None:
        samples = SimpleNamespace(
            find_one=AsyncMock(
                return_value={
                    "sampled_at": datetime(2026, 7, 16, 6, 52, tzinfo=UTC),
                    "total_tokens": 2000,
                }
            ),
            replace_one=AsyncMock(),
        )
        db = SimpleNamespace(sub2api_tpm_samples=samples)
        client = AsyncMock()
        client.get_dashboard_snapshot.return_value = {"stats": {"total_tokens": 100}}

        result = await tpm_sampler.sample_group_tpm(
            db,
            site_id="api-5001",
            group_id=3,
            client=client,
            sampled_at=datetime(2026, 7, 16, 6, 54, tzinfo=UTC),
        )

        sample = result["sample"]
        self.assertIsNone(sample["tpm"])
        self.assertIsNone(sample["calculated_tpm"])
        self.assertIsNone(sample["token_delta"])
        self.assertEqual(sample["elapsed_seconds"], 120.0)
        self.assertEqual(sample["source"], "unavailable")


class TpmSiteSamplingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tpm_sampler._site_sample_locks.clear()

    async def test_groups_run_in_parallel_and_one_failure_is_isolated(self) -> None:
        groups = SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([{"group_id": 3}, {"group_id": 5}, {"group_id": 7}]))
        db = SimpleNamespace(sub2api_groups_cache=groups)
        started: set[int] = set()
        all_started = asyncio.Event()

        async def sample_group(*_args, group_id: int, **_kwargs):
            started.add(group_id)
            if len(started) == 3:
                all_started.set()
            await all_started.wait()
            if group_id == 5:
                raise RuntimeError("group unavailable")
            return {"ok": True, "group_id": group_id}

        with (
            patch.object(
                tpm_sampler,
                "get_site",
                AsyncMock(return_value={"id": "api-5001", "base_url": "http://127.0.0.1:5001", "token": "secret"}),
            ),
            patch.object(tpm_sampler, "sample_group_tpm", AsyncMock(side_effect=sample_group)),
        ):
            result = await asyncio.wait_for(tpm_sampler.sample_site_tpm(db, site_id="api-5001"), timeout=1)

        self.assertEqual(started, {3, 5, 7})
        self.assertTrue(result["ok"])
        self.assertEqual(result["sampled"], 2)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["groups"], 3)

    async def test_same_site_overlap_is_skipped(self) -> None:
        groups = SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([{"group_id": 3}]))
        db = SimpleNamespace(sub2api_groups_cache=groups)
        started = asyncio.Event()
        release = asyncio.Event()

        async def sample_group(*_args, **_kwargs):
            started.set()
            await release.wait()
            return {"ok": True, "group_id": 3}

        sample_mock = AsyncMock(side_effect=sample_group)
        with (
            patch.object(
                tpm_sampler,
                "get_site",
                AsyncMock(return_value={"id": "api-5001", "base_url": "http://127.0.0.1:5001", "token": "secret"}),
            ),
            patch.object(tpm_sampler, "sample_group_tpm", sample_mock),
        ):
            first = asyncio.create_task(tpm_sampler.sample_site_tpm(db, site_id="api-5001"))
            await started.wait()
            second = await tpm_sampler.sample_site_tpm(db, site_id="api-5001")
            release.set()
            first_result = await first

        self.assertEqual(second["status"], "skipped")
        self.assertTrue(first_result["ok"])
        self.assertEqual(sample_mock.await_count, 1)


class TpmAllSitesSamplingTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_active_sites_run_and_sites_are_sampled_in_parallel(self) -> None:
        started: set[str] = set()
        all_started = asyncio.Event()

        async def sample_site(_db, *, site_id: str):
            started.add(site_id)
            if len(started) == 2:
                all_started.set()
            await all_started.wait()
            return {"ok": True, "site_id": site_id, "sampled": 2, "failed": 0}

        with (
            patch.object(
                tpm_sampler,
                "list_sites",
                AsyncMock(
                    return_value={
                        "items": [
                            {"id": "api-5001", "status": "active"},
                            {"id": "api-disabled", "status": "disabled"},
                            {"id": "api-5002", "status": "active"},
                        ]
                    }
                ),
            ),
            patch.object(tpm_sampler, "sample_site_tpm", AsyncMock(side_effect=sample_site)),
        ):
            result = await asyncio.wait_for(tpm_sampler.sample_all_sites_tpm(object()), timeout=1)

        self.assertEqual(started, {"api-5001", "api-5002"})
        self.assertEqual(result["sites"], 2)
        self.assertEqual(result["sampled"], 4)
        self.assertEqual(result["failed"], 0)

    async def test_one_site_failure_does_not_cancel_other_sites(self) -> None:
        completed: list[str] = []

        async def sample_site(_db, *, site_id: str):
            if site_id == "api-broken":
                raise RuntimeError("site unavailable")
            completed.append(site_id)
            return {"ok": True, "site_id": site_id, "sampled": 3, "failed": 0}

        with (
            patch.object(
                tpm_sampler,
                "list_sites",
                AsyncMock(
                    return_value={
                        "items": [
                            {"id": "api-broken", "status": "active"},
                            {"id": "api-healthy", "status": "active"},
                        ]
                    }
                ),
            ),
            patch.object(tpm_sampler, "sample_site_tpm", AsyncMock(side_effect=sample_site)),
        ):
            result = await tpm_sampler.sample_all_sites_tpm(object())

        self.assertEqual(completed, ["api-healthy"])
        self.assertEqual(result["sites"], 2)
        self.assertEqual(result["site_failures"], 1)
        self.assertEqual(result["sampled"], 3)


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
        sleep.assert_awaited_once()
        sleep_seconds = sleep.await_args.args[0]
        self.assertGreater(sleep_seconds, 0)
        self.assertLessEqual(sleep_seconds, 60)


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
        self.assertEqual(collection.create_index.await_count, 3)


if __name__ == "__main__":
    unittest.main()
