from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pymongo.errors import AutoReconnect

from app.modules.sub2api.account_history import snapshot_hash
from app.modules.sub2api.account_history_migration import (
    LegacyReplayState,
    _persist_sampled_at_group,
    compare_reconstructed_states,
    clamp_migration_batch_size,
    convert_legacy_account_history,
    delete_verified_legacy_samples,
    evaluate_source_idle,
    migration_id_for_boundary,
    reconstruct_migrated_states,
    restore_replay_from_targets,
    retry_mongo_operation,
    verify_migrated_account_history,
)


class LegacyReplayTests(unittest.TestCase):
    def test_missing_change_entry_represents_an_empty_dynamic_state(self) -> None:
        empty_hash = snapshot_hash({"usage": {}, "subscription": {}})

        result = compare_reconstructed_states(
            {"api-5001:empty@example.com": empty_hash},
            {},
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["mismatch_count"], 0)

    def test_migration_id_and_batch_size_are_deterministic_and_bounded(self) -> None:
        boundary = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)

        self.assertEqual(
            migration_id_for_boundary(boundary, site_id="api-5001"),
            migration_id_for_boundary(boundary, site_id="api-5001"),
        )
        self.assertNotEqual(
            migration_id_for_boundary(boundary, site_id="api-5001"),
            migration_id_for_boundary(boundary, site_id="api-5002"),
        )
        self.assertEqual(clamp_migration_batch_size(1), 100)
        self.assertEqual(clamp_migration_batch_size(2_000), 2_000)
        self.assertEqual(clamp_migration_batch_size(50_000), 10_000)

    def test_first_sample_initializes_state_and_unchanged_sample_is_skipped(self) -> None:
        replay = LegacyReplayState(migration_id="migration-1")
        first_at = datetime(2026, 7, 16, 1, 0, tzinfo=UTC)

        first = replay.consume_run(
            site_id="api-5001",
            run_id="run-1",
            observed_at=first_at,
            samples=[_sample("sample-1", 40, cumulative=100)],
        )
        same = replay.consume_run(
            site_id="api-5001",
            run_id="run-2",
            observed_at=datetime(2026, 7, 16, 1, 3, tzinfo=UTC),
            samples=[_sample("sample-2", 40, cumulative=100)],
        )
        changed = replay.consume_run(
            site_id="api-5001",
            run_id="run-3",
            observed_at=datetime(2026, 7, 16, 1, 6, tzinfo=UTC),
            samples=[_sample("sample-3", 42, cumulative=102)],
        )

        self.assertEqual(first["change_batches"][0]["entries"][0]["changes"], {"usage.codex_5h_used_percent": 40})
        self.assertEqual(same["change_batches"], [])
        self.assertEqual(changed["change_batches"][0]["entries"][0]["changes"], {"usage.codex_5h_used_percent": 42})
        self.assertEqual(replay.source_documents, 3)
        self.assertEqual(replay.changed_accounts, 2)

    def test_first_run_of_shanghai_day_creates_dynamic_checkpoint(self) -> None:
        replay = LegacyReplayState(migration_id="migration-1")
        result = replay.consume_run(
            site_id="api-5001",
            run_id="run-1",
            observed_at=datetime(2026, 7, 16, 16, 5, tzinfo=UTC),
            samples=[_sample("sample-1", 40, cumulative=100)],
        )

        chunks = [item for item in result["checkpoint_documents"] if item.get("document_type") != "manifest"]
        manifests = [item for item in result["checkpoint_documents"] if item.get("document_type") == "manifest"]
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(manifests), 1)
        self.assertEqual(chunks[0]["local_date"], "2026-07-17")
        self.assertEqual(chunks[0]["migration_id"], "migration-1")
        self.assertEqual(
            chunks[0]["entries"][0],
            {
                "identity_id": "api-5001:user@example.com",
                "usage": {"codex_5h_used_percent": 40},
                "subscription": {},
                "cumulative_usage": {"codex_total_actual_cost_cumulative": 100},
            },
        )
        self.assertNotIn("email", chunks[0]["entries"][0])
        self.assertTrue(manifests[0]["complete"])

    def test_full_reconstruction_detects_missing_change_batch(self) -> None:
        replay = LegacyReplayState(migration_id="migration-1")
        first = replay.consume_run(
            site_id="api-5001",
            run_id="run-1",
            observed_at=datetime(2026, 7, 16, 1, 0, tzinfo=UTC),
            samples=[_sample("sample-1", 40, cumulative=100)],
        )
        changed = replay.consume_run(
            site_id="api-5001",
            run_id="run-2",
            observed_at=datetime(2026, 7, 16, 1, 3, tzinfo=UTC),
            samples=[_sample("sample-2", 42, cumulative=102)],
        )
        batches = [*first["change_batches"], *changed["change_batches"]]

        rebuilt = reconstruct_migrated_states(batches)
        verified = compare_reconstructed_states(replay.final_state_hashes, rebuilt)
        incomplete = compare_reconstructed_states(
            replay.final_state_hashes,
            reconstruct_migrated_states(batches[:1]),
        )

        self.assertTrue(verified["ok"])
        self.assertEqual(verified["mismatch_count"], 0)
        self.assertFalse(incomplete["ok"])
        self.assertEqual(incomplete["mismatch_count"], 1)


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, value: int):
        self.items = self.items[:value]
        return self

    def batch_size(self, _value: int):
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class LegacyMigrationSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_conversion_progress_is_throttled_by_run_interval(self) -> None:
        observed_at = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
        replay = LegacyReplayState(migration_id="migration-1")
        samples = [
            {"_id": "sample-1", "probe_run_id": "run-1"},
            {"_id": "sample-2", "probe_run_id": "run-2"},
        ]

        with (
            patch(
                "app.modules.sub2api.account_history_migration._persist_replayed_run",
                AsyncMock(return_value=[]),
            ) as persist_run,
            patch(
                "app.modules.sub2api.account_history_migration._update_conversion_progress",
                AsyncMock(),
            ) as update_progress,
        ):
            _, persisted = await _persist_sampled_at_group(
                SimpleNamespace(),
                replay=replay,
                site_id="api-5001",
                observed_at=observed_at,
                samples=samples,
                migration_id="migration-1",
                run_count=98,
                progress_interval_runs=100,
            )

        self.assertEqual(persisted, 2)
        self.assertEqual(persist_run.await_count, 2)
        update_progress.assert_awaited_once()
        self.assertEqual(update_progress.await_args.args[3], 100)

    async def test_verification_failed_migration_reuses_completed_conversion(self) -> None:
        ledger = {"_id": "migration-1", "stage": "verification_failed"}
        source = SimpleNamespace(count_documents=AsyncMock())
        db = SimpleNamespace(
            remote_account_history_migrations=SimpleNamespace(find_one=AsyncMock(return_value=ledger)),
            remote_account_probe_samples=source,
        )

        result = await convert_legacy_account_history(
            db,
            migration_id="migration-1",
            source_max_sampled_at=datetime(2026, 7, 18, 8, 0, tzinfo=UTC),
        )

        self.assertEqual(result, ledger)
        source.count_documents.assert_not_awaited()

    async def test_transient_mongo_disconnect_is_retried(self) -> None:
        operation = AsyncMock(side_effect=[AutoReconnect("connection closed"), "ok"])

        result = await retry_mongo_operation(operation, attempts=3, base_delay_seconds=0)

        self.assertEqual(result, "ok")
        self.assertEqual(operation.await_count, 2)

    def test_replay_can_resume_from_persisted_target_batches(self) -> None:
        initial = LegacyReplayState(migration_id="migration-1")
        first = initial.consume_run(
            site_id="api-5001",
            run_id="run-1",
            observed_at=datetime(2026, 7, 16, 1, 0, tzinfo=UTC),
            samples=[_sample("sample-1", 40, cumulative=100)],
        )
        changed = initial.consume_run(
            site_id="api-5001",
            run_id="run-2",
            observed_at=datetime(2026, 7, 16, 1, 3, tzinfo=UTC),
            samples=[_sample("sample-2", 42, cumulative=102)],
        )
        restored = LegacyReplayState(migration_id="migration-1")

        resume = restore_replay_from_targets(
            restored,
            [*first["change_batches"], *changed["change_batches"]],
            checkpoint_documents=first["checkpoint_documents"],
        )
        next_result = restored.consume_run(
            site_id="api-5001",
            run_id="run-3",
            observed_at=datetime(2026, 7, 16, 1, 6, tzinfo=UTC),
            samples=[_sample("sample-3", 44, cumulative=104)],
        )

        self.assertEqual(resume["api-5001"], datetime(2026, 7, 16, 1, 3, tzinfo=UTC))
        self.assertEqual(
            next_result["change_batches"][0]["entries"][0]["changes"],
            {"usage.codex_5h_used_percent": 44},
        )

    def test_recent_source_is_not_idle(self) -> None:
        now = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)

        recent = evaluate_source_idle(now - timedelta(minutes=3), now=now, idle_minutes=10)
        old = evaluate_source_idle(now - timedelta(minutes=11), now=now, idle_minutes=10)

        self.assertFalse(recent["idle"])
        self.assertTrue(old["idle"])

    async def test_verification_updates_ledger_only_for_matching_states(self) -> None:
        replay = LegacyReplayState(migration_id="migration-1")
        result = replay.consume_run(
            site_id="api-5001",
            run_id="run-1",
            observed_at=datetime(2026, 7, 16, 1, 0, tzinfo=UTC),
            samples=[_sample("sample-1", 40, cumulative=100)],
        )
        ledger = {
            "_id": "migration-1",
            "stage": "converted",
            "source_documents_expected": 1,
            "source_documents_processed": 1,
            "final_state_hashes": [
                {"identity_id": identity_id, "state_hash": state_hash}
                for identity_id, state_hash in replay.final_state_hashes.items()
            ],
            "checkpoint_manifest_ids": [],
        }
        migrations = SimpleNamespace(find_one=AsyncMock(return_value=ledger), update_one=AsyncMock())
        db = SimpleNamespace(
            remote_account_history_migrations=migrations,
            remote_account_change_batches=SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor(result["change_batches"])),
            remote_account_daily_checkpoints=SimpleNamespace(count_documents=AsyncMock(return_value=0)),
        )

        verified = await verify_migrated_account_history(db, "migration-1")

        self.assertTrue(verified["ok"])
        self.assertEqual(verified["stage"], "verified")
        update = migrations.update_one.await_args.args[1]["$set"]
        self.assertEqual(update["stage"], "verified")

    async def test_conversion_persists_deterministic_targets_and_converted_ledger(self) -> None:
        observed_at = datetime(2026, 7, 16, 1, 0, tzinfo=UTC)
        samples = [
            {
                **_sample("sample-1", 40, cumulative=100),
                "site_id": "api-5001",
                "probe_run_id": "run-1",
                "sampled_at": observed_at,
            },
            {
                **_sample("sample-2", 42, cumulative=102),
                "site_id": "api-5001",
                "probe_run_id": "run-2",
                "sampled_at": observed_at + timedelta(minutes=3),
            },
        ]
        source = SimpleNamespace(
            count_documents=AsyncMock(return_value=2),
            distinct=AsyncMock(return_value=["api-5001"]),
            find=lambda *_args, **_kwargs: AsyncCursor(samples),
        )
        migrations = SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock())
        changes = SimpleNamespace(bulk_write=AsyncMock())
        checkpoints = SimpleNamespace(find_one=AsyncMock(return_value=None), bulk_write=AsyncMock())
        db = SimpleNamespace(
            remote_account_probe_samples=source,
            remote_account_history_migrations=migrations,
            remote_account_change_batches=changes,
            remote_account_daily_checkpoints=checkpoints,
        )

        result = await convert_legacy_account_history(
            db,
            migration_id="migration-1",
            source_max_sampled_at=observed_at + timedelta(minutes=3),
        )

        self.assertEqual(result["stage"], "converted")
        self.assertEqual(result["source_documents_processed"], 2)
        self.assertEqual(result["change_batches"], 2)
        self.assertEqual(changes.bulk_write.await_count, 2)
        self.assertGreaterEqual(checkpoints.bulk_write.await_count, 1)
        final_update = migrations.update_one.await_args.args[1]["$set"]
        self.assertEqual(final_update["stage"], "converted")
        self.assertEqual(len(final_update["final_state_hashes"]), 1)

    async def test_failed_conversion_resumes_from_latest_target_time(self) -> None:
        first_at = datetime(2026, 7, 16, 1, 0, tzinfo=UTC)
        all_samples = [
            {**_sample("sample-1", 40, cumulative=100), "site_id": "api-5001", "probe_run_id": "run-1", "sampled_at": first_at},
            {**_sample("sample-2", 42, cumulative=102), "site_id": "api-5001", "probe_run_id": "run-2", "sampled_at": first_at + timedelta(minutes=3)},
            {**_sample("sample-3", 44, cumulative=104), "site_id": "api-5001", "probe_run_id": "run-3", "sampled_at": first_at + timedelta(minutes=6)},
        ]
        initial = LegacyReplayState(migration_id="migration-1")
        first = initial.consume_run(site_id="api-5001", run_id="run-1", observed_at=first_at, samples=[all_samples[0]])
        second = initial.consume_run(site_id="api-5001", run_id="run-2", observed_at=first_at + timedelta(minutes=3), samples=[all_samples[1]])
        source = MigrationSourceCollection(all_samples)
        migrations = SimpleNamespace(
            find_one=AsyncMock(return_value={"_id": "migration-1", "stage": "conversion_failed"}),
            update_one=AsyncMock(),
        )
        existing_batches = [*first["change_batches"], *second["change_batches"]]
        changes = SimpleNamespace(
            find=lambda *_args, **_kwargs: AsyncCursor(existing_batches),
            bulk_write=AsyncMock(),
        )
        checkpoints = SimpleNamespace(
            find=lambda *_args, **_kwargs: AsyncCursor(first["checkpoint_documents"]),
            find_one=AsyncMock(return_value={"migration_id": "migration-1", "complete": True}),
            bulk_write=AsyncMock(),
        )
        db = SimpleNamespace(
            remote_account_probe_samples=source,
            remote_account_history_migrations=migrations,
            remote_account_change_batches=changes,
            remote_account_daily_checkpoints=checkpoints,
        )

        result = await convert_legacy_account_history(
            db,
            migration_id="migration-1",
            source_max_sampled_at=first_at + timedelta(minutes=6),
        )

        resumed_queries = [query for query in source.find_queries if "$gte" in query.get("sampled_at", {})]
        self.assertTrue(resumed_queries)
        self.assertEqual(resumed_queries[0]["sampled_at"]["$gte"], first_at + timedelta(minutes=3))
        self.assertEqual(result["source_documents_processed"], 3)

    async def test_verified_source_is_deleted_in_requested_batches(self) -> None:
        now = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
        source = InMemorySourceCollection(
            [
                {"_id": f"sample-{index}", "sampled_at": now - timedelta(hours=1), "site_id": "api-5001"}
                for index in range(4_500)
            ]
        )
        ledger = {
            "_id": "migration-1",
            "stage": "verified",
            "source_max_sampled_at": now - timedelta(minutes=30),
            "site_id": None,
            "deleted_documents": 0,
        }
        migrations = SimpleNamespace(find_one=AsyncMock(return_value=ledger), update_one=AsyncMock())
        db = SimpleNamespace(
            remote_account_history_migrations=migrations,
            remote_account_probe_samples=source,
        )

        result = await delete_verified_legacy_samples(
            db,
            "migration-1",
            batch_size=2_000,
            idle_minutes=10,
            now=now,
        )

        self.assertEqual(source.deleted_batch_sizes, [2_000, 2_000, 500])
        self.assertEqual(result["deleted_documents"], 4_500)
        self.assertEqual(result["stage"], "completed")

    async def test_global_deletion_uses_site_scoped_index_queries(self) -> None:
        now = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
        source = InMemorySourceCollection(
            [
                {"_id": "sample-a", "sampled_at": now - timedelta(hours=1), "site_id": "api-5001"},
                {"_id": "sample-b", "sampled_at": now - timedelta(hours=1), "site_id": "api-5002"},
            ]
        )
        ledger = {
            "_id": "migration-1",
            "stage": "verified",
            "source_max_sampled_at": now - timedelta(minutes=30),
            "site_id": None,
            "deleted_documents": 0,
        }
        db = SimpleNamespace(
            remote_account_history_migrations=SimpleNamespace(
                find_one=AsyncMock(return_value=ledger),
                update_one=AsyncMock(),
            ),
            remote_account_probe_samples=source,
        )

        result = await delete_verified_legacy_samples(
            db,
            "migration-1",
            batch_size=2_000,
            idle_minutes=10,
            now=now,
        )

        self.assertEqual(result["deleted_documents"], 2)
        self.assertEqual(
            {query.get("site_id") for query in source.find_queries},
            {"api-5001", "api-5002"},
        )

    async def test_completed_deletion_reconciles_unrecorded_deleted_batch(self) -> None:
        now = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
        source = InMemorySourceCollection([])
        ledger = {
            "_id": "migration-1",
            "stage": "completed",
            "source_max_sampled_at": now - timedelta(minutes=30),
            "source_documents_expected": 10_000,
            "site_id": None,
            "deleted_documents": 4_000,
        }
        migrations = SimpleNamespace(find_one=AsyncMock(return_value=ledger), update_one=AsyncMock())
        db = SimpleNamespace(
            remote_account_history_migrations=migrations,
            remote_account_probe_samples=source,
        )

        result = await delete_verified_legacy_samples(
            db,
            "migration-1",
            batch_size=2_000,
            idle_minutes=10,
            now=now,
        )

        self.assertEqual(result["deleted_documents"], 10_000)
        final_update = migrations.update_one.await_args.args[1]["$set"]
        self.assertEqual(final_update["deleted_documents"], 10_000)


class InMemorySourceCollection:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items
        self.deleted_batch_sizes: list[int] = []
        self.find_queries: list[dict[str, object]] = []

    async def find_one(self, *_args, **_kwargs):
        return max(self.items, key=lambda item: item["sampled_at"]) if self.items else None

    def find(self, query, *_args, **_kwargs):
        self.find_queries.append(query)
        boundary = query.get("sampled_at", {}).get("$lte")
        site_id = query.get("site_id")
        items = [
            item
            for item in self.items
            if (boundary is None or item["sampled_at"] <= boundary)
            and (site_id is None or item.get("site_id") == site_id)
        ]
        return AsyncCursor(items)

    async def distinct(self, field, query):
        boundary = query.get("sampled_at", {}).get("$lte")
        return sorted(
            {
                item[field]
                for item in self.items
                if item.get(field) and (boundary is None or item["sampled_at"] <= boundary)
            }
        )

    async def delete_many(self, query):
        ids = set(query["_id"]["$in"])
        before = len(self.items)
        self.items = [item for item in self.items if item["_id"] not in ids]
        deleted = before - len(self.items)
        self.deleted_batch_sizes.append(deleted)
        return SimpleNamespace(deleted_count=deleted)

    async def count_documents(self, query):
        boundary = query.get("sampled_at", {}).get("$lte")
        site_id = query.get("site_id")
        return sum(
            1
            for item in self.items
            if (boundary is None or item["sampled_at"] <= boundary)
            and (site_id is None or item.get("site_id") == site_id)
        )


class MigrationSourceCollection:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items
        self.find_queries: list[dict[str, object]] = []

    async def count_documents(self, query):
        return len(self._filtered(query))

    async def distinct(self, _field, _query):
        return sorted({str(item["site_id"]) for item in self.items})

    def find(self, query, *_args, **_kwargs):
        self.find_queries.append(query)
        return AsyncCursor(self._filtered(query))

    def _filtered(self, query):
        site_id = query.get("site_id")
        bounds = query.get("sampled_at", {})
        return [
            item
            for item in self.items
            if (not site_id or item.get("site_id") == site_id)
            and ("$lt" not in bounds or item["sampled_at"] < bounds["$lt"])
            and ("$gte" not in bounds or item["sampled_at"] >= bounds["$gte"])
            and ("$lte" not in bounds or item["sampled_at"] <= bounds["$lte"])
        ]


def _sample(sample_id: str, used_percent: int, *, cumulative: int) -> dict[str, object]:
    return {
        "_id": sample_id,
        "identity_id": "api-5001:user@example.com",
        "remote_account_id": 953,
        "normalized_email": "user@example.com",
        "usage_snapshot": {"codex_5h_used_percent": used_percent},
        "cumulative_usage_snapshot": {"codex_total_actual_cost_cumulative": cumulative},
    }


if __name__ == "__main__":
    unittest.main()
