from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from app.modules.sub2api.account_history import (
    apply_history_entries,
    build_daily_checkpoint_documents,
    build_history_change,
    chunk_history_changes,
    ensure_daily_checkpoint,
    load_identity_changes,
    persist_history_changes,
    public_change_entry,
)
from app.modules.system import bootstrap


class AccountHistoryChangeTests(unittest.TestCase):
    def test_records_only_new_values_and_removed_fields(self) -> None:
        change = build_history_change(
            identity_id="api-5001:user@example.com",
            remote_account_id=953,
            previous={
                "usage": {"codex_5h_used_percent": 40, "removed": 1},
                "subscription": {},
            },
            current={
                "usage": {"codex_5h_used_percent": 42},
                "subscription": {},
            },
        )

        self.assertIsNotNone(change)
        assert change is not None
        self.assertEqual(change["changes"], {"usage.codex_5h_used_percent": 42})
        self.assertEqual(change["unset"], ["usage.removed"])
        self.assertEqual(
            change["event_id"],
            build_history_change(
                identity_id="api-5001:user@example.com",
                remote_account_id=953,
                previous={"usage": {"codex_5h_used_percent": 40, "removed": 1}, "subscription": {}},
                current={"usage": {"codex_5h_used_percent": 42}, "subscription": {}},
            )["event_id"],
        )
        self.assertNotIn("_new_state", public_change_entry(change))

    def test_equal_snapshots_do_not_create_change(self) -> None:
        snapshot = {"usage": {"codex_5h_used_percent": 40}, "subscription": {}}

        self.assertIsNone(
            build_history_change(
                identity_id="api-5001:user@example.com",
                remote_account_id=953,
                previous=snapshot,
                current=snapshot,
            )
        )

    def test_usage_reset_is_stored_as_new_zero_value(self) -> None:
        change = build_history_change(
            identity_id="api-5001:user@example.com",
            remote_account_id=953,
            previous={"usage": {"codex_7d_used_percent": 80}, "subscription": {}},
            current={"usage": {"codex_7d_used_percent": 0}, "subscription": {}},
        )

        assert change is not None
        self.assertEqual(change["changes"], {"usage.codex_7d_used_percent": 0})


class AccountHistoryChunkTests(unittest.TestCase):
    def test_splits_after_five_hundred_entries(self) -> None:
        observed_at = datetime(2026, 7, 18, 6, 30, tzinfo=UTC)
        changes = [
            build_history_change(
                identity_id=f"api-5001:user-{index}@example.com",
                remote_account_id=index,
                previous={"usage": {"value": 0}, "subscription": {}},
                current={"usage": {"value": 1}, "subscription": {}},
            )
            for index in range(501)
        ]

        batches = chunk_history_changes(
            [item for item in changes if item is not None],
            site_id="api-5001",
            run_id="run-1",
            observed_at=observed_at,
        )

        self.assertEqual([item["entry_count"] for item in batches], [500, 1])
        self.assertEqual(batches[0]["_id"], "api-5001:run-1:0")
        self.assertEqual(batches[1]["_id"], "api-5001:run-1:1")

    def test_splits_before_bson_size_target(self) -> None:
        observed_at = datetime(2026, 7, 18, 6, 30, tzinfo=UTC)
        changes = [
            build_history_change(
                identity_id=f"api-5001:user-{index}@example.com",
                remote_account_id=index,
                previous={"usage": {"text": ""}, "subscription": {}},
                current={"usage": {"text": "x" * 400}, "subscription": {}},
            )
            for index in range(2)
        ]

        batches = chunk_history_changes(
            [item for item in changes if item is not None],
            site_id="api-5001",
            run_id="run-size",
            observed_at=observed_at,
            max_bson_bytes=1_100,
        )

        self.assertEqual([item["entry_count"] for item in batches], [1, 1])


class AccountHistoryReconstructionTests(unittest.TestCase):
    def test_applies_unset_before_changes_and_deduplicates_event_id(self) -> None:
        entries = [
            {
                "event_id": "event-1",
                "changes": {"usage.value": 2, "subscription.status": "active"},
                "unset": ["usage.old"],
            },
            {
                "event_id": "event-1",
                "changes": {"usage.value": 999},
                "unset": [],
            },
        ]

        rebuilt = apply_history_entries(
            {"usage": {"value": 1, "old": True}, "subscription": {}},
            entries,
        )

        self.assertEqual(rebuilt, {"usage": {"value": 2}, "subscription": {"status": "active"}})


class AccountHistoryPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_baseline_advances_only_after_batch_write(self) -> None:
        batches = SimpleNamespace(bulk_write=AsyncMock())
        identities = SimpleNamespace(bulk_write=AsyncMock())
        db = SimpleNamespace(
            remote_account_change_batches=batches,
            remote_account_identities=identities,
        )
        change = build_history_change(
            identity_id="api-5001:user@example.com",
            remote_account_id=953,
            previous={"usage": {"value": 1}, "subscription": {}},
            current={"usage": {"value": 2}, "subscription": {}},
        )
        assert change is not None

        result = await persist_history_changes(
            db,
            site_id="api-5001",
            run_id="run-1",
            observed_at=datetime(2026, 7, 18, 6, 30, tzinfo=UTC),
            changes=[change],
        )

        batches.bulk_write.assert_awaited_once()
        identities.bulk_write.assert_awaited_once()
        identity_update = identities.bulk_write.await_args.args[0][0]
        self.assertEqual(identity_update._filter["history_baseline_hash"], change["previous_state_hash"])
        self.assertEqual(identity_update._doc["$set"]["history_baseline_snapshot"], change["_new_state"])
        self.assertEqual(result["changed_accounts"], 1)
        self.assertEqual(result["batches"], 1)

    async def test_failed_batch_write_does_not_advance_baseline(self) -> None:
        batches = SimpleNamespace(bulk_write=AsyncMock(side_effect=RuntimeError("write failed")))
        identities = SimpleNamespace(bulk_write=AsyncMock())
        db = SimpleNamespace(
            remote_account_change_batches=batches,
            remote_account_identities=identities,
        )
        change = build_history_change(
            identity_id="api-5001:user@example.com",
            remote_account_id=953,
            previous={"usage": {"value": 1}, "subscription": {}},
            current={"usage": {"value": 2}, "subscription": {}},
        )
        assert change is not None

        with self.assertRaisesRegex(RuntimeError, "write failed"):
            await persist_history_changes(
                db,
                site_id="api-5001",
                run_id="run-1",
                observed_at=datetime(2026, 7, 18, 6, 30, tzinfo=UTC),
                changes=[change],
            )

        identities.bulk_write.assert_not_awaited()


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, value: int):
        self.items = self.items[:value]
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class AccountHistoryCheckpointTests(unittest.IsolatedAsyncioTestCase):
    def test_daily_checkpoint_stores_only_dynamic_state(self) -> None:
        checkpoint_at = datetime(2026, 7, 18, 16, 5, tzinfo=UTC)
        documents = build_daily_checkpoint_documents(
            [
                {
                    "_id": "api-5001:user@example.com",
                    "email": "user@example.com",
                    "name": "not repeated",
                    "last_usage_snapshot": {"codex_5h_used_percent": 42},
                    "current_subscription_snapshot": {"subscription_status": "active"},
                    "cumulative_usage_snapshot": {"codex_7d_actual_cost_cumulative": 100},
                }
            ],
            site_id="api-5001",
            checkpoint_at=checkpoint_at,
        )

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["_id"], "api-5001:2026-07-19:0")
        entry = documents[0]["entries"][0]
        self.assertEqual(
            entry,
            {
                "identity_id": "api-5001:user@example.com",
                "usage": {"codex_5h_used_percent": 42},
                "subscription": {"subscription_status": "active"},
                "cumulative_usage": {"codex_7d_actual_cost_cumulative": 100},
            },
        )
        self.assertNotIn("email", entry)
        self.assertNotIn("name", entry)
        self.assertEqual(documents[0]["expires_at"].year, 2027)

    async def test_complete_manifest_skips_checkpoint_rewrite(self) -> None:
        checkpoints = SimpleNamespace(
            find_one=AsyncMock(return_value={"_id": "api-5001:2026-07-19:manifest", "complete": True}),
            bulk_write=AsyncMock(),
        )
        identities = SimpleNamespace(find=AsyncMock())
        db = SimpleNamespace(
            remote_account_daily_checkpoints=checkpoints,
            remote_account_identities=identities,
        )

        result = await ensure_daily_checkpoint(
            db,
            site_id="api-5001",
            checkpoint_at=datetime(2026, 7, 18, 16, 5, tzinfo=UTC),
        )

        self.assertEqual(result["status"], "skipped")
        identities.find.assert_not_called()
        checkpoints.bulk_write.assert_not_awaited()


class AccountHistoryReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_changes_are_expanded_and_deduplicated(self) -> None:
        batches = [
            {
                "_id": "batch-new",
                "observed_at": datetime(2026, 7, 18, 6, 30, tzinfo=UTC),
                "entries": [
                    {"event_id": "event-1", "identity_id": "api-5001:user@example.com", "changes": {"usage.value": 2}, "unset": []},
                    {"event_id": "other", "identity_id": "api-5001:other@example.com", "changes": {"usage.value": 9}, "unset": []},
                ],
            },
            {
                "_id": "batch-retry",
                "observed_at": datetime(2026, 7, 18, 6, 29, tzinfo=UTC),
                "entries": [
                    {"event_id": "event-1", "identity_id": "api-5001:user@example.com", "changes": {"usage.value": 2}, "unset": []},
                ],
            },
        ]
        collection = SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor(batches))
        db = SimpleNamespace(remote_account_change_batches=collection)

        changes = await load_identity_changes(
            db,
            site_id="api-5001",
            identity_id="api-5001:user@example.com",
        )

        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["event_id"], "event-1")
        self.assertEqual(changes[0]["batch_id"], "batch-new")
        self.assertEqual(changes[0]["observed_at"], datetime(2026, 7, 18, 6, 30, tzinfo=UTC))


class AccountHistoryIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_change_and_checkpoint_indexes_include_ttl_and_site_time(self) -> None:
        changes = SimpleNamespace(create_index=AsyncMock())
        checkpoints = SimpleNamespace(create_index=AsyncMock())
        db = SimpleNamespace(
            remote_account_change_batches=changes,
            remote_account_daily_checkpoints=checkpoints,
        )

        await bootstrap.ensure_account_history_indexes(db)

        changes.create_index.assert_has_awaits(
            [
                call([("site_id", 1), ("observed_at", -1)]),
                call("expires_at", expireAfterSeconds=0),
            ]
        )
        checkpoints.create_index.assert_has_awaits(
            [
                call([("site_id", 1), ("local_date", -1)]),
                call("expires_at", expireAfterSeconds=0),
            ]
        )


if __name__ == "__main__":
    unittest.main()
