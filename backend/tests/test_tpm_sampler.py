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


class BulkWriteCollection:
    def __init__(self, state: dict[str, object] | None = None) -> None:
        self.find_one = AsyncMock(return_value=state)
        self.bulk_calls: list[tuple[list[object], bool]] = []
        self.update_calls: list[tuple[dict[str, object], dict[str, object], bool]] = []

    async def bulk_write(self, operations: list[object], *, ordered: bool) -> None:
        self.bulk_calls.append((operations, ordered))

    async def update_one(
        self,
        query: dict[str, object],
        update: dict[str, object],
        *,
        upsert: bool,
    ) -> None:
        self.update_calls.append((query, update, upsert))


def _minute_usage(total_tokens: int = 100, total_requests: int = 4, account_cost: float = 2.5) -> dict[str, object]:
    return {
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "input_tokens": 40,
        "output_tokens": 30,
        "cache_creation_tokens": 10,
        "cache_read_tokens": 20,
        "account_cost": account_cost,
        "source_updated_at": datetime(2026, 8, 4, 1, 19, 55, tzinfo=UTC),
    }


class TpmSampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_minute_document_uses_direct_usage_values(self) -> None:
        sampled_at = datetime(2026, 7, 16, 6, 53, 42, tzinfo=UTC)
        document = tpm_sampler._sample_document(
            site_id="api-5001",
            group_id=5,
            bucket_at=datetime(2026, 7, 16, 6, 53, tzinfo=UTC),
            sampled_at=sampled_at,
            usage={
                "total_tokens": 600,
                "total_requests": 8,
                "input_tokens": 300,
                "output_tokens": 200,
                "cache_creation_tokens": 50,
                "cache_read_tokens": 50,
                "account_cost": 5.0,
                "source_updated_at": datetime(2026, 7, 16, 6, 53, 30, tzinfo=UTC),
            },
            current_concurrency=17,
        )

        self.assertEqual(document["tpm"], 600.0)
        self.assertEqual(document["rpm"], 8.0)
        self.assertEqual(document["account_cost_delta"], 5.0)
        self.assertEqual(document["account_cost_per_minute"], 5.0)
        self.assertEqual(document["account_cost_per_hour"], 300.0)
        self.assertEqual(document["current_concurrency"], 17.0)
        self.assertEqual(document["schema_version"], 3)
        self.assertEqual(document["source"], "exact_minute")
        self.assertEqual(document["counter_source"], "postgresql_usage_logs_minute")
        self.assertEqual(document["sampled_at"], datetime(2026, 7, 16, 6, 53, tzinfo=UTC))
        self.assertEqual(document["recorded_at"], sampled_at)
        self.assertEqual(document["stats_updated_at"], datetime(2026, 7, 16, 6, 53, 30, tzinfo=UTC))
        self.assertEqual(document["expires_at"], sampled_at + timedelta(days=60))


class TpmExactMinuteTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_window_excludes_current_partial_minute(self) -> None:
        collection = BulkWriteCollection()
        db = SimpleNamespace(sub2api_tpm_samples=collection)
        await tpm_sampler._write_minute_samples(
            db,
            site_id="api-5001",
            group_ids=[3],
            start_at=datetime(2026, 8, 4, 1, 0, tzinfo=UTC),
            end_at=datetime(2026, 8, 4, 1, 20, tzinfo=UTC),
            usage_by_key={(3, datetime(2026, 8, 4, 1, 19, tzinfo=UTC)): _minute_usage()},
            sampled_at=datetime(2026, 8, 4, 1, 20, 38, tzinfo=UTC),
            latest_bucket_at=datetime(2026, 8, 4, 1, 19, tzinfo=UTC),
            concurrency_by_group={3: 17},
        )

        operations, ordered = collection.bulk_calls[0]
        self.assertFalse(ordered)
        self.assertEqual(len(operations), 20)
        ids = {operation._filter["_id"] for operation in operations}
        self.assertNotIn("api-5001:3:2026-08-04T01:20:00Z", ids)
        self.assertIn("api-5001:3:2026-08-04T01:19:00Z", ids)

    async def test_missing_group_minutes_are_written_as_zero(self) -> None:
        collection = BulkWriteCollection()
        db = SimpleNamespace(sub2api_tpm_samples=collection)
        await tpm_sampler._write_minute_samples(
            db,
            site_id="api-5001",
            group_ids=[3, 5],
            start_at=datetime(2026, 8, 4, 1, 19, tzinfo=UTC),
            end_at=datetime(2026, 8, 4, 1, 20, tzinfo=UTC),
            usage_by_key={(3, datetime(2026, 8, 4, 1, 19, tzinfo=UTC)): _minute_usage()},
            sampled_at=datetime(2026, 8, 4, 1, 20, 38, tzinfo=UTC),
            latest_bucket_at=None,
            concurrency_by_group=None,
        )

        operations, _ = collection.bulk_calls[0]
        zero_operation = next(
            operation
            for operation in operations
            if operation._filter["_id"] == "api-5001:5:2026-08-04T01:19:00Z"
        )
        zero_fields = zero_operation._doc["$set"]
        self.assertEqual(zero_fields["tpm"], 0.0)
        self.assertEqual(zero_fields["rpm"], 0.0)
        self.assertEqual(zero_fields["minute_account_cost"], 0.0)

    async def test_recalibration_preserves_existing_concurrency(self) -> None:
        collection = BulkWriteCollection()
        db = SimpleNamespace(sub2api_tpm_samples=collection)
        await tpm_sampler._write_minute_samples(
            db,
            site_id="api-5001",
            group_ids=[3],
            start_at=datetime(2026, 8, 4, 1, 0, tzinfo=UTC),
            end_at=datetime(2026, 8, 4, 1, 1, tzinfo=UTC),
            usage_by_key={},
            sampled_at=datetime(2026, 8, 4, 1, 20, 38, tzinfo=UTC),
            latest_bucket_at=datetime(2026, 8, 4, 1, 19, tzinfo=UTC),
            concurrency_by_group={3: 17},
        )

        operation = collection.bulk_calls[0][0][0]
        self.assertNotIn("current_concurrency", operation._doc["$set"])
        self.assertEqual(operation._doc["$setOnInsert"]["current_concurrency"], None)

    async def test_latest_completed_minute_updates_current_concurrency(self) -> None:
        collection = BulkWriteCollection()
        db = SimpleNamespace(sub2api_tpm_samples=collection)
        await tpm_sampler._write_minute_samples(
            db,
            site_id="api-5001",
            group_ids=[3],
            start_at=datetime(2026, 8, 4, 1, 18, tzinfo=UTC),
            end_at=datetime(2026, 8, 4, 1, 20, tzinfo=UTC),
            usage_by_key={},
            sampled_at=datetime(2026, 8, 4, 1, 20, 38, tzinfo=UTC),
            latest_bucket_at=datetime(2026, 8, 4, 1, 19, tzinfo=UTC),
            concurrency_by_group={3: 17},
        )

        operations, _ = collection.bulk_calls[0]
        latest = next(operation for operation in operations if operation._filter["_id"].endswith("01:19:00Z"))
        older = next(operation for operation in operations if operation._filter["_id"].endswith("01:18:00Z"))
        self.assertEqual(latest._doc["$set"]["current_concurrency"], 17.0)
        self.assertNotIn("current_concurrency", older._doc["$set"])


class TpmBackfillTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_cursor_starts_with_hour_before_recent_window(self) -> None:
        state = BulkWriteCollection()
        db = SimpleNamespace(sub2api_tpm_backfill_state=state)
        closed_end = datetime(2026, 8, 4, 1, 20, tzinfo=UTC)
        recent_start = datetime(2026, 8, 4, 1, 0, tzinfo=UTC)

        window_start, window_end = await tpm_sampler._load_backfill_window(
            db,
            site_id="api-5001",
            closed_end=closed_end,
            recent_start=recent_start,
        )

        self.assertEqual(window_start, datetime(2026, 8, 4, 0, 0, tzinfo=UTC))
        self.assertEqual(window_end, recent_start)

    async def test_cursor_document_without_next_window_is_treated_as_new(self) -> None:
        state = BulkWriteCollection({"_id": "api-5001", "updated_at": datetime(2026, 8, 4, tzinfo=UTC)})
        db = SimpleNamespace(sub2api_tpm_backfill_state=state)
        closed_end = datetime(2026, 8, 4, 1, 20, tzinfo=UTC)
        recent_start = datetime(2026, 8, 4, 1, 0, tzinfo=UTC)

        window_start, window_end = await tpm_sampler._load_backfill_window(
            db,
            site_id="api-5001",
            closed_end=closed_end,
            recent_start=recent_start,
        )

        self.assertEqual(window_start, datetime(2026, 8, 4, 0, 0, tzinfo=UTC))
        self.assertEqual(window_end, recent_start)

    async def test_backfill_cursor_advances_only_to_previous_window_start(self) -> None:
        state = BulkWriteCollection()
        db = SimpleNamespace(sub2api_tpm_backfill_state=state)
        window_start = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)
        window_end = datetime(2026, 8, 4, 1, 0, tzinfo=UTC)

        await tpm_sampler._advance_backfill_cursor(
            db,
            site_id="api-5001",
            window_start=window_start,
            window_end=window_end,
            next_window_end=window_start,
            completed_at=datetime(2026, 8, 4, 1, 20, tzinfo=UTC),
        )

        query, update, upsert = state.update_calls[0]
        self.assertEqual(query, {"_id": "api-5001"})
        self.assertTrue(upsert)
        self.assertEqual(update["$set"]["next_window_end"], window_start)
        self.assertEqual(update["$set"]["last_window_start"], window_start)
        self.assertEqual(update["$set"]["last_window_end"], window_end)

    async def test_cursor_at_or_before_seven_day_boundary_resets_to_recent_window(self) -> None:
        closed_end = datetime(2026, 8, 4, 1, 20, tzinfo=UTC)
        recent_start = datetime(2026, 8, 4, 1, 0, tzinfo=UTC)
        state = BulkWriteCollection(
            {
                "_id": "api-5001",
                "next_window_end": closed_end - timedelta(days=7, minutes=1),
            }
        )
        db = SimpleNamespace(sub2api_tpm_backfill_state=state)

        window_start, window_end = await tpm_sampler._load_backfill_window(
            db,
            site_id="api-5001",
            closed_end=closed_end,
            recent_start=recent_start,
        )

        self.assertEqual(window_end, recent_start)
        self.assertEqual(window_start, recent_start - timedelta(hours=1))

    async def test_future_cursor_is_clamped_to_recent_window(self) -> None:
        closed_end = datetime(2026, 8, 4, 1, 20, tzinfo=UTC)
        recent_start = datetime(2026, 8, 4, 1, 0, tzinfo=UTC)
        state = BulkWriteCollection(
            {
                "_id": "api-5001",
                "next_window_end": closed_end + timedelta(minutes=5),
            }
        )
        db = SimpleNamespace(sub2api_tpm_backfill_state=state)

        window_start, window_end = await tpm_sampler._load_backfill_window(
            db,
            site_id="api-5001",
            closed_end=closed_end,
            recent_start=recent_start,
        )

        self.assertEqual(window_end, recent_start)
        self.assertEqual(window_start, recent_start - timedelta(hours=1))


class TpmSiteSamplingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        tpm_sampler._site_sample_locks.clear()

    async def test_all_groups_are_loaded_in_two_minute_queries(self) -> None:
        groups = SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([{"group_id": 3}, {"group_id": 5}]))
        db = SimpleNamespace(
            sub2api_groups_cache=groups,
            sub2api_tpm_samples=BulkWriteCollection(),
            sub2api_tpm_backfill_state=BulkWriteCollection(),
        )
        usage = AsyncMock(side_effect=[{}, {}])
        frozen_now = datetime(2026, 8, 4, 1, 20, 38, tzinfo=UTC)

        with (
            patch.object(
                tpm_sampler,
                "get_site",
                AsyncMock(
                    return_value={
                        "id": "api-5001",
                        "site_type": "sub2api",
                        "base_url": "http://127.0.0.1:5001",
                        "token": "secret",
                        "sql_dsn": "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
                    }
                ),
            ),
            patch.object(tpm_sampler, "now_utc", return_value=frozen_now),
            patch.object(tpm_sampler, "_fetch_all_accounts", AsyncMock(return_value=[])),
            patch.object(tpm_sampler, "update_cached_account_runtime_fields", AsyncMock(return_value={"updated": 0})),
            patch.object(tpm_sampler, "fetch_group_minute_usage", usage),
        ):
            result = await tpm_sampler.sample_site_tpm(db, site_id="api-5001")

        self.assertTrue(result["ok"])
        self.assertEqual(result["sampled"], 2)
        self.assertEqual(result["documents_written"], 160)
        self.assertEqual(usage.await_count, 2)
        self.assertEqual(usage.await_args_list[0].kwargs["group_ids"], [3, 5])
        self.assertEqual(usage.await_args_list[1].kwargs["group_ids"], [3, 5])

    async def test_site_sampler_uses_recent_and_historical_minute_queries(self) -> None:
        groups = SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([{"group_id": 3}]))
        samples = BulkWriteCollection()
        state = BulkWriteCollection()
        db = SimpleNamespace(
            sub2api_groups_cache=groups,
            sub2api_tpm_samples=samples,
            sub2api_tpm_backfill_state=state,
        )
        frozen_now = datetime(2026, 8, 4, 1, 20, 38, tzinfo=UTC)
        usage = AsyncMock(
            side_effect=[
                {(3, datetime(2026, 8, 4, 1, 19, tzinfo=UTC)): _minute_usage()},
                {},
            ]
        )

        with (
            patch.object(
                tpm_sampler,
                "get_site",
                AsyncMock(
                    return_value={
                        "id": "api-5001",
                        "site_type": "sub2api",
                        "base_url": "http://127.0.0.1:5001",
                        "token": "secret",
                        "sql_dsn": "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
                    }
                ),
            ),
            patch.object(tpm_sampler, "now_utc", return_value=frozen_now),
            patch.object(tpm_sampler, "fetch_group_minute_usage", usage),
            patch.object(tpm_sampler, "_fetch_all_accounts", AsyncMock(return_value=[])),
            patch.object(tpm_sampler, "update_cached_account_runtime_fields", AsyncMock(return_value={"updated": 0})),
        ):
            result = await tpm_sampler.sample_site_tpm(db, site_id="api-5001")

        self.assertTrue(result["ok"])
        self.assertEqual(usage.await_count, 2)
        self.assertEqual(
            usage.await_args_list[0].kwargs["start_at"],
            datetime(2026, 8, 4, 1, 0, tzinfo=UTC),
        )
        self.assertEqual(
            usage.await_args_list[0].kwargs["end_at"],
            datetime(2026, 8, 4, 1, 20, tzinfo=UTC),
        )
        self.assertEqual(
            usage.await_args_list[1].kwargs["start_at"],
            datetime(2026, 8, 4, 0, 0, tzinfo=UTC),
        )
        self.assertEqual(
            usage.await_args_list[1].kwargs["end_at"],
            datetime(2026, 8, 4, 1, 0, tzinfo=UTC),
        )

    async def test_historical_failure_keeps_recent_samples_without_advancing_cursor(self) -> None:
        groups = SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([{"group_id": 3}]))
        samples = BulkWriteCollection()
        state = BulkWriteCollection()
        db = SimpleNamespace(
            sub2api_groups_cache=groups,
            sub2api_tpm_samples=samples,
            sub2api_tpm_backfill_state=state,
        )
        usage = AsyncMock(side_effect=[{}, RuntimeError("historical read failed")])

        with (
            patch.object(
                tpm_sampler,
                "get_site",
                AsyncMock(
                    return_value={
                        "id": "api-5001",
                        "site_type": "sub2api",
                        "base_url": "http://127.0.0.1:5001",
                        "sql_dsn": "host=db user=u password=p dbname=d",
                    }
                ),
            ),
            patch.object(tpm_sampler, "now_utc", return_value=datetime(2026, 8, 4, 1, 20, 38, tzinfo=UTC)),
            patch.object(tpm_sampler, "_fetch_all_accounts", AsyncMock(return_value=[])),
            patch.object(tpm_sampler, "fetch_group_minute_usage", usage),
        ):
            result = await tpm_sampler.sample_site_tpm(db, site_id="api-5001")

        self.assertTrue(result["ok"])
        self.assertFalse(result["historical_ok"])
        self.assertEqual(len(samples.bulk_calls), 1)
        self.assertEqual(state.update_calls, [])

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
        db = SimpleNamespace(
            sub2api_groups_cache=groups,
            sub2api_tpm_samples=BulkWriteCollection(),
            sub2api_tpm_backfill_state=BulkWriteCollection(),
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def fetch_usage(*_args, **_kwargs):
            started.set()
            await release.wait()
            return {}

        with (
            patch.object(
                tpm_sampler,
                "get_site",
                AsyncMock(
                    return_value={
                        "id": "api-5001",
                        "site_type": "sub2api",
                        "base_url": "http://127.0.0.1:5001",
                        "sql_dsn": "host=db user=u password=p dbname=d",
                    }
                ),
            ),
            patch.object(tpm_sampler, "_fetch_all_accounts", AsyncMock(return_value=[])),
            patch.object(tpm_sampler, "fetch_group_minute_usage", AsyncMock(side_effect=fetch_usage)),
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
        state_collection = SimpleNamespace(create_index=AsyncMock())
        db = SimpleNamespace(
            sub2api_tpm_samples=collection,
            sub2api_tpm_backfill_state=state_collection,
        )

        await bootstrap.ensure_tpm_indexes(db)

        collection.create_index.assert_has_awaits(
            [
                call([("site_id", 1), ("group_id", 1), ("bucket_at", 1)], unique=True),
                call("expires_at", expireAfterSeconds=0),
                call([("site_id", 1), ("group_id", 1), ("sampled_at", -1)]),
            ]
        )
        state_collection.create_index.assert_awaited_once_with("updated_at")


if __name__ == "__main__":
    unittest.main()
