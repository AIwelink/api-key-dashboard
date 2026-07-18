from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.system.storage_cleanup import (
    cleanup_obsolete_storage,
    compact_legacy_capacity_sample,
)


class AsyncCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self._documents = documents

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for document in self._documents:
            yield document


class StorageCleanupTests(unittest.IsolatedAsyncioTestCase):
    def test_legacy_capacity_sample_is_compacted_without_losing_scalar_history(self) -> None:
        sampled_at = datetime(2026, 7, 18, 7, 39, tzinfo=UTC)
        legacy = {
            "_id": "us06-5001:3:2026-07-18T07:35:00Z",
            "schema_version": 1,
            "site_id": "us06-5001",
            "group_id": 3,
            "bucket_at": datetime(2026, 7, 18, 7, 35, tzinfo=UTC),
            "sampled_at": sampled_at,
            "account_cache_fetched_at": sampled_at - timedelta(minutes=1),
            "capacity_calculated_at": sampled_at - timedelta(seconds=10),
            "expires_at": sampled_at + timedelta(days=180),
            "group": {"id": 3, "name": "plus pool"},
            "capacity_summary": {
                "account_type": "plus",
                "available_accounts": 12,
                "five_hour_capacity_usd": 1320.0,
                "calculated_at": sampled_at,
                "ready": True,
                "capacity_limits": {
                    "plus": {"five_hour_usd": 110.0, "seven_day_usd": 110.0},
                },
                "type_summary": {
                    "plus": {
                        "available_accounts": 12,
                        "available_5h_accounts": 10,
                        "five_hour_capacity_usd": 1320.0,
                    },
                    "total": {"available_accounts": 12},
                },
                "recommended_refill_options": {
                    "plus": {"account_type": "plus", "suggested_add_count": 3},
                },
            },
        }

        compact = compact_legacy_capacity_sample(legacy)

        self.assertEqual(compact["schema_version"], 2)
        self.assertEqual(compact["_id"], legacy["_id"])
        self.assertEqual(compact["metrics"]["available_accounts"], 12)
        self.assertEqual(compact["metrics"]["five_hour_capacity_usd"], 1320.0)
        self.assertEqual(compact["metrics"]["ready"], True)
        self.assertEqual(compact["dimensions"]["account_types"][0]["account_type"], "plus")
        self.assertEqual(compact["dimensions"]["capacity_limits"][0]["five_hour_usd"], 110.0)
        self.assertEqual(compact["dimensions"]["refill_options"][0]["suggested_add_count"], 3)
        self.assertEqual(compact["expires_at"], sampled_at + timedelta(days=30))
        self.assertNotIn("capacity_summary", compact)
        self.assertNotIn("group", compact)

    async def test_dry_run_reports_candidates_without_writing(self) -> None:
        db = self._database(capacity_documents=[])

        report = await cleanup_obsolete_storage(db, execute=False)

        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual(report["before"]["dashboard_raw_documents"], 6)
        self.assertEqual(report["before"]["duplicated_group_summaries"], 2)
        self.assertEqual(report["before"]["legacy_capacity_samples"], 3)
        db.sub2api_dashboard_trends.update_many.assert_not_awaited()
        db.sub2api_dashboard_models.update_many.assert_not_awaited()
        db.sub2api_dashboard_snapshots.update_many.assert_not_awaited()
        db.sub2api_groups_cache.update_many.assert_not_awaited()
        db.sub2api_capacity_samples.bulk_write.assert_not_awaited()

    async def test_execute_unsets_duplicates_and_compacts_only_legacy_capacity_samples(self) -> None:
        sampled_at = datetime(2026, 7, 18, 7, 39, tzinfo=UTC)
        db = self._database(
            capacity_documents=[
                {
                    "_id": "sample-1",
                    "schema_version": 1,
                    "site_id": "us06-5001",
                    "group_id": 3,
                    "bucket_at": sampled_at,
                    "sampled_at": sampled_at,
                    "capacity_summary": {"available_accounts": 12},
                }
            ]
        )

        report = await cleanup_obsolete_storage(db, execute=True, batch_size=1)

        self.assertEqual(report["mode"], "execute")
        db.sub2api_dashboard_trends.update_many.assert_awaited_once_with(
            {"raw": {"$exists": True}},
            {"$unset": {"raw": ""}},
        )
        db.sub2api_groups_cache.update_many.assert_awaited_once_with(
            {"group.capacity_summary": {"$exists": True}},
            {"$unset": {"group.capacity_summary": ""}},
        )
        operation = db.sub2api_capacity_samples.bulk_write.await_args.args[0][0]
        self.assertEqual(operation._filter, {"_id": "sample-1", "schema_version": {"$ne": 2}})
        self.assertEqual(operation._doc["schema_version"], 2)
        self.assertNotIn("capacity_summary", operation._doc)
        self.assertFalse(operation._upsert)
        self.assertEqual(report["results"]["legacy_capacity_samples"]["scanned"], 1)

    @staticmethod
    def _database(*, capacity_documents: list[dict[str, object]]) -> SimpleNamespace:
        def collection(count: int) -> SimpleNamespace:
            return SimpleNamespace(
                count_documents=AsyncMock(side_effect=[count, 0]),
                update_many=AsyncMock(return_value=SimpleNamespace(matched_count=count, modified_count=count)),
            )

        capacity = collection(3)
        capacity.find = lambda *_args, **_kwargs: AsyncCursor(capacity_documents)
        capacity.bulk_write = AsyncMock(return_value=SimpleNamespace(matched_count=1, modified_count=1))
        return SimpleNamespace(
            sub2api_dashboard_trends=collection(2),
            sub2api_dashboard_models=collection(1),
            sub2api_dashboard_snapshots=collection(3),
            sub2api_groups_cache=collection(2),
            sub2api_capacity_samples=capacity,
        )


if __name__ == "__main__":
    unittest.main()
