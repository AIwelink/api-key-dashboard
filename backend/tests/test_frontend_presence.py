from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.system import presence
from app.schemas import FrontendPresenceHeartbeat


class PresenceCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items
        self.sort_args: tuple[object, ...] | None = None
        self.limit_value: int | None = None

    def sort(self, *args):
        self.sort_args = args
        return self

    def limit(self, value: int):
        self.limit_value = value
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class FrontendPresenceTests(unittest.IsolatedAsyncioTestCase):
    def test_heartbeat_schema_accepts_browser_client_metadata(self) -> None:
        payload = FrontendPresenceHeartbeat(
            client_id="client-a",
            session_id="tab-a",
            client_label="Windows · Chrome",
            device_type="desktop",
            view="api-pools",
            path="/api-pool-status",
            foreground_since_at="2026-07-18T09:00:00Z",
        )

        self.assertEqual(payload.foreground_since_at, datetime(2026, 7, 18, 9, 0, tzinfo=UTC))

    async def test_different_clients_are_stored_separately_for_the_same_user(self) -> None:
        collection = SimpleNamespace(update_one=AsyncMock())
        minute_collection = SimpleNamespace(update_one=AsyncMock())
        db = SimpleNamespace(frontend_presence=collection, frontend_presence_minutes=minute_collection)
        actor = {"_id": "user-1", "name": "Owner", "email": "owner@example.com", "role": "owner"}
        now = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)

        await presence.record_frontend_presence(
            db,
            actor=actor,
            payload={"client_id": "client-a", "session_id": "tab-a", "view": "api-pools", "path": "/api-pool-status"},
            observed_at=now,
        )
        await presence.record_frontend_presence(
            db,
            actor=actor,
            payload={"client_id": "client-b", "session_id": "tab-b", "view": "accounts", "path": "/accounts"},
            observed_at=now,
        )

        first_filter = collection.update_one.await_args_list[0].args[0]
        second_filter = collection.update_one.await_args_list[1].args[0]
        self.assertNotEqual(first_filter["_id"], second_filter["_id"])

    async def test_new_tab_reuses_the_user_and_client_presence_record(self) -> None:
        collection = SimpleNamespace(update_one=AsyncMock())
        minute_collection = SimpleNamespace(update_one=AsyncMock())
        db = SimpleNamespace(frontend_presence=collection, frontend_presence_minutes=minute_collection)
        actor = {"_id": "user-1", "name": "Owner", "email": "owner@example.com", "role": "owner"}

        for session_id in ("tab-a", "tab-b"):
            await presence.record_frontend_presence(
                db,
                actor=actor,
                payload={"client_id": "client-a", "session_id": session_id, "view": "api-pools", "path": "/api-pool-status"},
            )

        first_filter = collection.update_one.await_args_list[0].args[0]
        second_filter = collection.update_one.await_args_list[1].args[0]
        self.assertEqual(first_filter["_id"], second_filter["_id"])
        self.assertEqual(collection.update_one.await_args_list[1].args[1]["$set"]["session_id"], "tab-b")

    async def test_api_token_actor_cannot_be_reported_as_a_browser_user(self) -> None:
        db = SimpleNamespace(frontend_presence=SimpleNamespace(update_one=AsyncMock()))

        with self.assertRaisesRegex(ValueError, "browser user"):
            await presence.record_frontend_presence(
                db,
                actor={"_id": "api_token:1", "actor_type": "api_token"},
                payload={"client_id": "client-a", "session_id": "tab-a", "view": "api-pools", "path": "/api-pool-status"},
            )

    async def test_online_list_uses_the_active_window_and_latest_heartbeat_order(self) -> None:
        now = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
        cursor = PresenceCursor([{"_id": "presence-1", "last_seen_at": now}])
        collection = SimpleNamespace(find=unittest.mock.MagicMock(return_value=cursor))
        db = SimpleNamespace(frontend_presence=collection)

        result = await presence.list_active_frontend_presence(db, observed_at=now)

        self.assertEqual(
            collection.find.call_args.args[0],
            {"last_seen_at": {"$gte": datetime(2026, 7, 18, 8, 59, tzinfo=UTC)}},
        )
        self.assertEqual(cursor.sort_args, ("last_seen_at", -1))
        self.assertEqual(cursor.limit_value, 500)
        self.assertEqual(result["total"], 1)

    async def test_heartbeats_share_one_five_minute_history_bucket_across_clients(self) -> None:
        current = SimpleNamespace(update_one=AsyncMock())
        minutes = SimpleNamespace(update_one=AsyncMock())
        db = SimpleNamespace(frontend_presence=current, frontend_presence_minutes=minutes)
        actor = {"_id": "user-1", "name": "Owner", "email": "owner@example.com", "role": "owner"}

        await presence.record_frontend_presence(
            db,
            actor=actor,
            payload={"client_id": "client-a", "session_id": "tab-a", "view": "api-pools", "path": "/api-pool-status"},
            observed_at=datetime(2026, 7, 18, 9, 2, tzinfo=UTC),
        )
        await presence.record_frontend_presence(
            db,
            actor=actor,
            payload={"client_id": "client-b", "session_id": "tab-b", "view": "accounts", "path": "/accounts"},
            observed_at=datetime(2026, 7, 18, 9, 4, tzinfo=UTC),
        )

        first_filter = minutes.update_one.await_args_list[0].args[0]
        second_filter = minutes.update_one.await_args_list[1].args[0]
        self.assertEqual(first_filter, second_filter)
        first_update = minutes.update_one.await_args_list[0].args[1]
        self.assertEqual(first_update["$setOnInsert"]["bucket_at"], datetime(2026, 7, 18, 9, 0, tzinfo=UTC))


class PresenceHistoryAggregationTests(unittest.TestCase):
    def test_builds_thirty_day_timeline_and_keeps_users_without_history(self) -> None:
        observed_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
        users = [
            {"_id": "user-1", "name": "Active", "email": "active@example.com", "role": "maintainer"},
            {"_id": "user-2", "name": "Quiet", "email": "quiet@example.com", "role": "viewer"},
        ]
        minute_docs = []
        for day_offset in range(30):
            local_day = datetime(2026, 6, 19, 1, 0, tzinfo=UTC) + timedelta(days=day_offset)
            minute_docs.extend(
                [
                    {"user_id": "user-1", "bucket_at": local_day, "last_seen_at": local_day.replace(minute=2)},
                    {"user_id": "user-1", "bucket_at": local_day.replace(minute=5), "last_seen_at": local_day.replace(minute=7)},
                ]
            )

        result = presence.build_presence_history(
            users=users,
            minute_docs=minute_docs,
            current_docs=[],
            observed_at=observed_at,
            days=30,
        )

        self.assertEqual(result["days"], 30)
        self.assertEqual(len(result["items"]), 2)
        active = next(item for item in result["items"] if item["user_id"] == "user-1")
        quiet = next(item for item in result["items"] if item["user_id"] == "user-2")
        self.assertEqual(len(active["daily_timeline"]), 30)
        self.assertEqual(len(active["daily_timeline"][0]["segments"]), 48)
        self.assertGreater(active["online_minutes"], 0)
        self.assertGreater(active["online_ratio_percent"], 0)
        self.assertEqual(active["last_seen_at"], datetime(2026, 7, 18, 1, 7, tzinfo=UTC))
        self.assertEqual(active["common_periods"][0]["start"], "09:00")
        self.assertEqual(active["common_periods"][0]["end"], "09:30")
        self.assertEqual(quiet["online_minutes"], 0)
        self.assertEqual(quiet["online_ratio_percent"], 0)


if __name__ == "__main__":
    unittest.main()
