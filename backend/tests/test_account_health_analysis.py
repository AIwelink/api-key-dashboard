from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.sub2api import account_health_analysis


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = [dict(item) for item in items]

    def sort(self, *args: object) -> "AsyncCursor":
        if len(args) == 1 and isinstance(args[0], list):
            fields = args[0]
        else:
            fields = [(args[0], args[1])]
        for field, direction in reversed(fields):
            self.items.sort(key=lambda item: item.get(str(field)), reverse=int(direction) < 0)
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield dict(item)


class MemoryCollection:
    def __init__(self, documents: list[dict[str, object]] | None = None) -> None:
        self.documents = {str(item["_id"]): dict(item) for item in (documents or [])}
        self.find_calls: list[dict[str, object]] = []
        self.replace_one_calls: list[tuple[dict[str, object], dict[str, object], bool]] = []

    def find(self, query: dict[str, object], *_args: object, **_kwargs: object) -> AsyncCursor:
        self.find_calls.append(query)
        return AsyncCursor(list(self.documents.values()))

    async def find_one(self, query: dict[str, object], *_args: object, **_kwargs: object):
        document = self.documents.get(str(query.get("_id")))
        return dict(document) if document else None

    async def replace_one(self, query: dict[str, object], document: dict[str, object], *, upsert: bool = False):
        self.replace_one_calls.append((dict(query), dict(document), upsert))
        self.documents[str(document["_id"])] = dict(document)
        return SimpleNamespace(matched_count=1, modified_count=1)


def identity(
    identity_id: str,
    *,
    plan_type: str = "plus",
    name: str = "plus account",
    status: str = "active",
    error_message: str | None = None,
    first_seen_at: datetime | None = None,
    last_seen_at: datetime | None = None,
    presence: str = "present",
    usage: dict[str, object] | None = None,
    account_type: str | None = None,
) -> dict[str, object]:
    document = {
        "_id": identity_id,
        "site_id": "api-5001",
        "plan_type": plan_type,
        "name": name,
        "first_seen_at": first_seen_at or NOW - timedelta(days=8),
        "last_seen_at": last_seen_at or NOW,
        "last_present_at": last_seen_at or NOW,
        "current_presence": presence,
        "current_status": status,
        "current_error_message": error_message,
        "last_usage_snapshot": usage
        or {
            "codex_5h_used_percent": 40,
            "codex_7d_used_percent": 60,
            "codex_5h_actual_cost": 44,
            "codex_7d_actual_cost": 66,
        },
    }
    if account_type is not None:
        document["account_type"] = account_type
    return document


def transition(
    identity_id: str,
    detected_at: datetime,
    *,
    previous_status: str = "active",
    previous_error: str | None = None,
    current_status: str = "active",
    current_error: str | None = None,
    event_type: str = "error_changed",
) -> dict[str, object]:
    return {
        "_id": f"{identity_id}:{detected_at.isoformat()}:{event_type}",
        "site_id": "api-5001",
        "identity_id": identity_id,
        "detected_at": detected_at,
        "event_type": event_type,
        "previous_status": previous_status,
        "previous_error_message": previous_error,
        "current_status": current_status,
        "current_error_message": current_error,
    }


class UnavailableClassificationTests(unittest.TestCase):
    def test_merges_auth_ban_and_502_failures(self) -> None:
        unavailable = (
            ("active", "Authentication failed (401): token_invalidated"),
            ("revoked", None),
            ("active", "account deactivated by provider"),
            ("active", "502: Bad Gateway from upstream"),
        )

        for status, message in unavailable:
            with self.subTest(status=status, message=message):
                self.assertTrue(account_health_analysis.is_unavailable_state(status, message))

    def test_excludes_rate_limits_temporary_403_and_scheduling_state(self) -> None:
        available = (
            ("active", "429 rate limit reached"),
            ("active", "403 forbidden, retry later"),
            ("disabled", None),
            ("active", None),
        )

        for status, message in available:
            with self.subTest(status=status, message=message):
                self.assertFalse(account_health_analysis.is_unavailable_state(status, message))


class AccountLifetimeAnalysisTests(unittest.TestCase):
    def test_classifies_only_terminal_account_failures(self) -> None:
        terminal = (
            ("active", "Authentication failed (401): token_invalidated"),
            ("revoked", None),
            ("active", "account deactivated by provider"),
        )
        non_terminal = (
            ("active", "502: Bad Gateway from upstream"),
            ("active", "429 rate limit reached"),
            ("active", "403 forbidden, retry later"),
            ("disabled", None),
        )

        for status, message in terminal:
            with self.subTest(status=status, message=message):
                self.assertTrue(account_health_analysis.is_terminal_failure_state(status, message))
        for status, message in non_terminal:
            with self.subTest(status=status, message=message):
                self.assertFalse(account_health_analysis.is_terminal_failure_state(status, message))

    def test_uses_first_terminal_failure_once_after_recovery(self) -> None:
        first_seen_at = NOW - timedelta(days=3)
        account = identity("a", first_seen_at=first_seen_at)
        events = [
            transition("a", NOW - timedelta(hours=6), current_error="502 Bad Gateway"),
            transition("a", NOW - timedelta(hours=4), current_error="Token revoked (401)", event_type="401_detected"),
            transition("a", NOW - timedelta(hours=4), current_error="Token revoked (401)", event_type="status_changed"),
            transition("a", NOW - timedelta(hours=2), previous_error="Token revoked (401)", event_type="401_recovered"),
            transition("a", NOW - timedelta(hours=1), current_status="banned", event_type="status_changed"),
        ]

        result = account_health_analysis.build_account_lifetime(account, events, end_at=NOW)

        self.assertEqual(result["failed_at"], NOW - timedelta(hours=4))
        self.assertEqual(result["lifetime_seconds"], (NOW - timedelta(hours=4) - first_seen_at).total_seconds())

    def test_uses_persisted_first_401_when_event_is_missing(self) -> None:
        first_seen_at = NOW - timedelta(days=4)
        first_401_at = NOW - timedelta(hours=8)
        account = identity("a", first_seen_at=first_seen_at)
        account["first_401_at"] = first_401_at

        result = account_health_analysis.build_account_lifetime(account, [], end_at=NOW)

        self.assertEqual(result["failed_at"], first_401_at)
        self.assertEqual(result["lifetime_seconds"], (first_401_at - first_seen_at).total_seconds())

    def test_groups_lifetimes_by_first_failure_time_and_excludes_normal_accounts(self) -> None:
        accounts = [
            identity("normal"),
            identity(
                "recent",
                first_seen_at=NOW - timedelta(days=2),
                usage={
                    "codex_5h_used_percent": 80,
                    "codex_7d_used_percent": 90,
                    "codex_5h_actual_cost": 88,
                    "codex_7d_actual_cost": 99,
                },
            ),
            identity("older", first_seen_at=NOW - timedelta(days=9)),
        ]
        events = {
            "recent": [transition("recent", NOW - timedelta(hours=12), current_error="Token revoked (401)")],
            "older": [transition("older", NOW - timedelta(days=3), current_status="banned")],
        }

        one_day = account_health_analysis.summarize_lifetimes(
            accounts,
            events,
            start_at=NOW - timedelta(days=1),
            end_at=NOW,
        )
        seven_days = account_health_analysis.summarize_lifetimes(
            accounts,
            events,
            start_at=NOW - timedelta(days=7),
            end_at=NOW,
        )

        self.assertEqual(one_day["overall"]["failed_accounts"], 1)
        self.assertEqual(one_day["overall"]["average_lifetime_seconds"], 36 * 60 * 60)
        self.assertEqual(one_day["overall"]["average_five_hour_used_percent"], 80.0)
        self.assertEqual(one_day["overall"]["average_seven_day_actual_cost_usd"], 99.0)
        self.assertEqual(seven_days["overall"]["failed_accounts"], 2)
        self.assertEqual(seven_days["overall"]["minimum_lifetime_seconds"], 36 * 60 * 60)
        self.assertEqual(seven_days["overall"]["maximum_lifetime_seconds"], 6 * 24 * 60 * 60)
        self.assertEqual(seven_days["overall"]["median_lifetime_seconds"], 3.75 * 24 * 60 * 60)


class AccountHealthPeriodTests(unittest.TestCase):
    def test_deduplicates_probe_events_and_builds_recovered_episodes(self) -> None:
        account = identity("a")
        events = [
            transition("a", NOW - timedelta(hours=6), current_error="502 Bad Gateway"),
            transition(
                "a",
                NOW - timedelta(hours=6),
                current_error="502 Bad Gateway",
                event_type="status_changed",
            ),
            transition(
                "a",
                NOW - timedelta(hours=4),
                previous_error="502 Bad Gateway",
            ),
            transition(
                "a",
                NOW - timedelta(hours=2),
                current_error="Token revoked (401)",
                event_type="401_detected",
            ),
            transition(
                "a",
                NOW - timedelta(hours=1),
                previous_error="Token revoked (401)",
                event_type="401_recovered",
            ),
        ]

        result = account_health_analysis.build_account_period(
            account,
            events,
            start_at=NOW - timedelta(days=1),
            end_at=NOW,
        )

        self.assertEqual(result["episode_count"], 2)
        self.assertEqual(result["unavailable_seconds"], 3 * 60 * 60)
        self.assertEqual(result["episode_durations_seconds"], [2 * 60 * 60, 60 * 60])
        self.assertFalse(result["ongoing"])

    def test_clips_an_ongoing_episode_to_the_period(self) -> None:
        account = identity(
            "a",
            status="active",
            error_message="upstream returned 502 bad gateway",
        )

        result = account_health_analysis.build_account_period(
            account,
            [],
            start_at=NOW - timedelta(days=1),
            end_at=NOW,
        )

        self.assertEqual(result["episode_count"], 1)
        self.assertEqual(result["unavailable_seconds"], 24 * 60 * 60)
        self.assertTrue(result["ongoing"])

    def test_uses_last_observed_time_for_removed_accounts(self) -> None:
        last_seen = NOW - timedelta(hours=6)
        account = identity(
            "a",
            status="revoked",
            last_seen_at=last_seen,
            presence="removed",
        )

        result = account_health_analysis.build_account_period(
            account,
            [],
            start_at=NOW - timedelta(days=1),
            end_at=NOW,
        )

        self.assertEqual(result["observed_until"], last_seen)
        self.assertFalse(result["ongoing"])

    def test_present_account_not_seen_inside_the_period_is_not_observed(self) -> None:
        account = identity("a", last_seen_at=NOW - timedelta(days=2))

        result = account_health_analysis.build_account_period(
            account,
            [],
            start_at=NOW - timedelta(days=1),
            end_at=NOW,
        )

        self.assertFalse(result["observed"])

    def test_summarizes_unique_probability_types_and_existing_usage(self) -> None:
        accounts = [
            identity("normal"),
            identity(
                "special",
                name="特殊 plus 定向账号",
                status="active",
                error_message="502 bad gateway",
                usage={
                    "codex_5h_used_percent": 80,
                    "codex_7d_used_percent": 90,
                    "codex_5h_actual_cost": 88,
                    "codex_7d_actual_cost": 99,
                },
            ),
        ]

        result = account_health_analysis.summarize_period(
            accounts,
            {},
            start_at=NOW - timedelta(days=1),
            end_at=NOW,
        )

        self.assertEqual(result["overall"]["observed_accounts"], 2)
        self.assertEqual(result["overall"]["unavailable_accounts"], 1)
        self.assertEqual(result["overall"]["unavailable_probability"], 0.5)
        self.assertEqual(result["overall"]["average_five_hour_used_percent"], 60.0)
        self.assertEqual(result["overall"]["average_seven_day_actual_cost_usd"], 82.5)
        special = next(item for item in result["items"] if item["account_type"] == "special_plus")
        self.assertEqual(special["observed_accounts"], 1)
        self.assertEqual(special["unavailable_probability"], 1.0)

    def test_prefers_the_persisted_special_account_type(self) -> None:
        accounts = [identity("special", name="ordinary name", plan_type="team", account_type="special_team")]

        result = account_health_analysis.summarize_period(
            accounts,
            {},
            start_at=NOW - timedelta(days=1),
            end_at=NOW,
        )

        self.assertEqual(result["items"][0]["account_type"], "special_team")


class AccountHealthCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_analysis_for_one_hour(self) -> None:
        cached = {
            "_id": "api-5001",
            "site_id": "api-5001",
            "schema_version": account_health_analysis.ANALYSIS_SCHEMA_VERSION,
            "computed_at": NOW - timedelta(minutes=30),
            "periods": {},
        }
        db = SimpleNamespace(sub2api_account_health_analyses=MemoryCollection([cached]))

        with patch.object(account_health_analysis, "compute_account_health_analysis", AsyncMock()) as compute:
            result = await account_health_analysis.get_account_health_analysis(db, "api-5001", now=NOW)

        self.assertEqual(result["computed_at"], cached["computed_at"])
        self.assertFalse(result["stale"])
        compute.assert_not_awaited()

    async def test_recomputes_a_fresh_cache_from_an_old_schema(self) -> None:
        cached = {
            "_id": "api-5001",
            "site_id": "api-5001",
            "schema_version": account_health_analysis.ANALYSIS_SCHEMA_VERSION - 1,
            "computed_at": NOW - timedelta(minutes=30),
            "periods": {},
        }
        collection = MemoryCollection([cached])
        db = SimpleNamespace(sub2api_account_health_analyses=collection)
        computed = {"site_id": "api-5001", "computed_at": NOW, "periods": {}}

        with patch.object(
            account_health_analysis,
            "compute_account_health_analysis",
            AsyncMock(return_value=computed),
        ) as compute:
            await account_health_analysis.get_account_health_analysis(db, "api-5001", now=NOW)

        compute.assert_awaited_once()


    async def test_does_not_serve_an_old_schema_when_recomputation_fails(self) -> None:
        cached = {
            "_id": "api-5001",
            "site_id": "api-5001",
            "schema_version": account_health_analysis.ANALYSIS_SCHEMA_VERSION - 1,
            "computed_at": NOW - timedelta(minutes=30),
            "periods": {"one_day": {"overall": {"unavailable_probability": 1.0}}},
        }
        db = SimpleNamespace(sub2api_account_health_analyses=MemoryCollection([cached]))

        with (
            patch.object(
                account_health_analysis,
                "compute_account_health_analysis",
                AsyncMock(side_effect=RuntimeError("database unavailable")),
            ),
            self.assertRaisesRegex(RuntimeError, "database unavailable"),
        ):
            await account_health_analysis.get_account_health_analysis(db, "api-5001", now=NOW)


    async def test_replaces_expired_analysis_without_storing_account_rows(self) -> None:
        collection = MemoryCollection()
        db = SimpleNamespace(sub2api_account_health_analyses=collection)
        computed = {"site_id": "api-5001", "computed_at": NOW, "periods": {"one_day": {}, "seven_days": {}}}

        with patch.object(
            account_health_analysis,
            "compute_account_health_analysis",
            AsyncMock(return_value=computed),
        ) as compute:
            result = await account_health_analysis.get_account_health_analysis(db, "api-5001", now=NOW)

        self.assertEqual(result["periods"], computed["periods"])
        compute.assert_awaited_once()
        stored = collection.replace_one_calls[0][1]
        self.assertNotIn("accounts", stored)
        self.assertEqual(stored["_id"], "api-5001")


if __name__ == "__main__":
    unittest.main()
