from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from app.modules.sub2api import quota_detection


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = [dict(item) for item in items]
        self.sort_args: tuple[object, ...] | None = None
        self.limit_value: int | None = None

    def sort(self, *args: object) -> "AsyncCursor":
        self.sort_args = args
        if len(args) == 1 and isinstance(args[0], list):
            fields = list(args[0])
        else:
            fields = [(args[0], args[1])]
        for field, direction in reversed(fields):
            self.items.sort(key=lambda item: item.get(str(field)), reverse=int(direction) < 0)
        return self

    def limit(self, value: int) -> "AsyncCursor":
        self.limit_value = value
        self.items = self.items[:value]
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield dict(item)


def _matches(document: dict[str, object], query: dict[str, object]) -> bool:
    for key, expected in query.items():
        actual = document.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected and not (actual is not None and actual >= expected["$gte"]):
                return False
            if "$lt" in expected and not (actual is not None and actual < expected["$lt"]):
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
        elif actual != expected:
            return False
    return True


class MemoryCollection:
    def __init__(self, documents: list[dict[str, object]] | None = None) -> None:
        self.documents = {
            str(document["_id"]): dict(document)
            for document in (documents or [])
        }
        self.find_calls: list[dict[str, object]] = []
        self.find_one_calls: list[dict[str, object]] = []
        self.cursors: list[AsyncCursor] = []
        self.bulk_write_calls: list[tuple[list[object], bool]] = []
        self.update_one_calls: list[tuple[dict[str, object], dict[str, object], bool]] = []
        self.update_many_calls: list[tuple[dict[str, object], dict[str, object]]] = []
        self.replace_one_calls: list[tuple[dict[str, object], dict[str, object], bool]] = []
        self.duplicate_find_results = False
        self.fail_generation_cas = False
        self.fail_update_many_once = False
        self.fail_update_one_once = False
        self.before_bulk_write = None

    def find(self, query: dict[str, object], *_args: object, **_kwargs: object) -> AsyncCursor:
        self.find_calls.append(query)
        matches = [document for document in self.documents.values() if _matches(document, query)]
        if self.duplicate_find_results:
            matches = [*matches, *(dict(document) for document in matches)]
        cursor = AsyncCursor(matches)
        self.cursors.append(cursor)
        return cursor

    async def find_one(self, query: dict[str, object], *_args: object, **_kwargs: object):
        self.find_one_calls.append(query)
        return next(
            (dict(document) for document in self.documents.values() if _matches(document, query)),
            None,
        )

    async def bulk_write(self, operations: list[object], *, ordered: bool):
        self.bulk_write_calls.append((list(operations), ordered))
        if self.before_bulk_write is not None:
            callback = self.before_bulk_write
            self.before_bulk_write = None
            callback(self)
        upserted_ids: dict[int, str] = {}
        matched_count = 0
        modified_count = 0
        for index, operation in enumerate(operations):
            result = self._update(
                operation._filter,
                operation._doc,
                upsert=bool(operation._upsert),
            )
            matched_count += result.matched_count
            modified_count += result.modified_count
            if result.upserted_id is not None:
                upserted_ids[index] = result.upserted_id
        return SimpleNamespace(
            matched_count=matched_count,
            modified_count=modified_count,
            upserted_count=len(upserted_ids),
            upserted_ids=upserted_ids,
        )

    async def update_one(
        self,
        query: dict[str, object],
        update: dict[str, object],
        *,
        upsert: bool = False,
    ):
        self.update_one_calls.append((query, update, upsert))
        if self.fail_update_one_once:
            self.fail_update_one_once = False
            raise RuntimeError("rollup update failed")
        if self.fail_generation_cas and "current_generation" in query:
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
        return self._update(query, update, upsert=upsert)

    async def update_many(self, query: dict[str, object], update: dict[str, object]):
        self.update_many_calls.append((query, update))
        if self.fail_update_many_once:
            self.fail_update_many_once = False
            raise RuntimeError("sample reclassification failed")
        matched = 0
        modified = 0
        for document_id, document in list(self.documents.items()):
            if not _matches(document, query):
                continue
            matched += 1
            updated = dict(document)
            updated.update(update.get("$set", {}))
            if updated != document:
                modified += 1
            self.documents[document_id] = updated
        return SimpleNamespace(matched_count=matched, modified_count=modified)

    async def replace_one(
        self,
        query: dict[str, object],
        document: dict[str, object],
        *,
        upsert: bool = False,
    ):
        self.replace_one_calls.append((query, dict(document), upsert))
        self.documents[str(document["_id"])] = dict(document)
        return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)

    def _update(
        self,
        query: dict[str, object],
        update: dict[str, object],
        *,
        upsert: bool,
    ):
        match = next(
            (
                (document_id, document)
                for document_id, document in self.documents.items()
                if _matches(document, query)
            ),
            None,
        )
        if match is None:
            if not upsert:
                return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
            document = {
                key: value
                for key, value in query.items()
                if not isinstance(value, dict)
            }
            document = self._apply_update(document, update, inserting=True)
            document_id = str(document["_id"])
            self.documents[document_id] = document
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=document_id)

        document_id, existing = match
        updated = self._apply_update(existing, update, inserting=False)
        modified = int(updated != existing)
        self.documents[document_id] = updated
        return SimpleNamespace(matched_count=1, modified_count=modified, upserted_id=None)

    @staticmethod
    def _apply_update(
        document: dict[str, object],
        update: dict[str, object],
        *,
        inserting: bool,
    ) -> dict[str, object]:
        updated = dict(document)
        if inserting:
            updated.update(update.get("$setOnInsert", {}))
        updated.update(update.get("$set", {}))
        for key, value in update.get("$max", {}).items():
            if updated.get(key) is None or (value is not None and value > updated[key]):
                updated[key] = value
        for key, value in update.get("$min", {}).items():
            if updated.get(key) is None or (value is not None and value < updated[key]):
                updated[key] = value
        for key in update.get("$unset", {}):
            updated.pop(key, None)
        for key, value in update.get("$max", {}).items():
            if key not in updated or updated[key] < value:
                updated[key] = value
        for key, value in update.get("$min", {}).items():
            if key not in updated or updated[key] > value:
                updated[key] = value
        return updated


def quota_db() -> SimpleNamespace:
    return SimpleNamespace(
        sub2api_quota_detection_states=MemoryCollection(),
        sub2api_quota_limit_samples=MemoryCollection(),
        sub2api_quota_limit_profiles=MemoryCollection(),
        sub2api_quota_limit_daily_rollups=MemoryCollection(),
    )


def account_snapshot(**overrides: object) -> dict[str, object]:
    account: dict[str, object] = {
        "id": 953,
        "status": "active",
        "error_message": None,
        "codex_usage_synced_at": NOW - timedelta(minutes=1),
        "codex_5h_used_percent": 94,
        "codex_5h_reset_at": NOW + timedelta(hours=2),
        "codex_5h_window_minutes": 300,
        "codex_5h_actual_cost": 107.2,
        "codex_7d_used_percent": 80,
        "codex_7d_reset_at": NOW + timedelta(days=4),
        "codex_7d_window_minutes": 10_080,
        "codex_7d_actual_cost": 112.5,
    }
    account.update(overrides)
    return account


def valid_observation(
    *,
    percent: float = 94,
    cost: float = 107.2,
    observed_at: datetime = NOW,
    reset_at: datetime | None = None,
    account_type: str = "plus",
    remote_account_id: int = 953,
    window_type: str = "five_hour",
) -> dict[str, object]:
    return {
        "quality": "valid",
        "reason": None,
        "remote_account_id": remote_account_id,
        "window_type": window_type,
        "window_reset_at": reset_at or NOW + timedelta(hours=2),
        "window_minutes": 300.0,
        "used_percent": float(percent),
        "cost_usd": float(cost),
        "usage_synced_at": observed_at - timedelta(minutes=1),
        "observed_at": observed_at,
        "account_type": account_type,
    }


class QuotaObservationTests(unittest.TestCase):
    def test_builds_fresh_five_hour_observation(self) -> None:
        observation = quota_detection.build_window_observation(
            account_snapshot(),
            window_type="five_hour",
            account_type="plus",
            observed_at=NOW,
        )

        self.assertEqual(observation["quality"], "valid")
        self.assertIsNone(observation["reason"])
        self.assertEqual(observation["remote_account_id"], 953)
        self.assertEqual(observation["used_percent"], 94.0)
        self.assertEqual(observation["cost_usd"], 107.2)
        self.assertEqual(observation["account_type"], "plus")

    def test_builds_seven_day_observation_from_extra_and_canonicalizes_timestamps(self) -> None:
        observation = quota_detection.build_window_observation(
            {
                "id": 954,
                "status": "active",
                "extra": {
                    "codex_usage_synced_at": "2026-07-20T11:59:00Z",
                    "codex_7d_used_percent": "80",
                    "codex_7d_reset_at": "2026-07-24T12:00:00+00:00",
                    "codex_7d_window_minutes": "10080",
                    "codex_7d_actual_cost": "112.5",
                },
            },
            window_type="seven_day",
            account_type="unknown",
            observed_at=NOW.replace(tzinfo=None),
        )

        self.assertEqual(observation["quality"], "valid")
        self.assertEqual(observation["account_type"], "unknown")
        self.assertEqual(observation["window_reset_at"], NOW + timedelta(days=4))
        self.assertEqual(observation["usage_synced_at"], NOW - timedelta(minutes=1))
        self.assertEqual(observation["observed_at"], NOW)

    def test_rejects_missing_and_non_active_reset_windows(self) -> None:
        cases = (
            (account_snapshot(codex_5h_reset_at=None), "missing_window"),
            (account_snapshot(codex_5h_reset_at=NOW), "expired_window"),
            (
                account_snapshot(codex_5h_reset_at=NOW + timedelta(hours=6)),
                "reset_outside_window",
            ),
        )

        for account, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                observation = quota_detection.build_window_observation(
                    account,
                    window_type="five_hour",
                    account_type="plus",
                    observed_at=NOW,
                )
                self.assertEqual(observation["quality"], "invalid")
                self.assertEqual(observation["reason"], expected_reason)

    def test_rejects_stale_usage(self) -> None:
        observation = quota_detection.build_window_observation(
            account_snapshot(codex_usage_synced_at=NOW - timedelta(minutes=6)),
            window_type="five_hour",
            account_type="plus",
            observed_at=NOW,
        )

        self.assertEqual(observation["quality"], "invalid")
        self.assertEqual(observation["reason"], "stale_usage")

    def test_rejects_invalid_identity_type_timestamp_percent_and_cost(self) -> None:
        cases = (
            (account_snapshot(id=None), "plus", NOW, "missing_remote_id"),
            (account_snapshot(), "", NOW, "invalid_account_type"),
            (account_snapshot(), "enterprise", NOW, "invalid_account_type"),
            (account_snapshot(), "plus", None, "invalid_observed_at"),
            (account_snapshot(codex_5h_used_percent="not-a-number"), "plus", NOW, "invalid_percent"),
            (account_snapshot(codex_5h_actual_cost=-1), "plus", NOW, "invalid_cost"),
        )

        for account, account_type, observed_at, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                observation = quota_detection.build_window_observation(
                    account,
                    window_type="five_hour",
                    account_type=account_type,
                    observed_at=observed_at,  # type: ignore[arg-type]
                )
                self.assertEqual(observation["quality"], "invalid")
                self.assertEqual(observation["reason"], expected_reason)

    def test_rejects_malformed_remote_ids(self) -> None:
        cases = (
            ("", "invalid_remote_id"),
            ("953", "invalid_remote_id"),
            (0, "invalid_remote_id"),
            (-1, "invalid_remote_id"),
            (1.5, "invalid_remote_id"),
            ([], "invalid_remote_id"),
            ({"id": 953}, "invalid_remote_id"),
        )

        for remote_id, expected_reason in cases:
            with self.subTest(remote_id=remote_id):
                observation = quota_detection.build_window_observation(
                    account_snapshot(id=remote_id),
                    window_type="five_hour",
                    account_type="plus",
                    observed_at=NOW,
                )
                self.assertEqual(observation["quality"], "invalid")
                self.assertEqual(observation["reason"], expected_reason)

    def test_rejects_absurd_window_minutes_without_overflow(self) -> None:
        observation = quota_detection.build_window_observation(
            account_snapshot(codex_5h_window_minutes=1e300),
            window_type="five_hour",
            account_type="plus",
            observed_at=NOW,
        )

        self.assertEqual(observation["quality"], "invalid")
        self.assertEqual(observation["reason"], "invalid_window_minutes")

    def test_rejects_malformed_or_unhashable_window_types_without_raising(self) -> None:
        for window_type in (None, "", [], {}):
            with self.subTest(window_type=window_type):
                observation = quota_detection.build_window_observation(
                    account_snapshot(),
                    window_type=window_type,  # type: ignore[arg-type]
                    account_type="plus",
                    observed_at=NOW,
                )
                self.assertEqual(observation["quality"], "invalid")
                self.assertEqual(observation["reason"], "unsupported_window_type")

    def test_rejects_malformed_and_overflowing_timestamps_without_raising(self) -> None:
        utc_underflow = datetime.min.replace(tzinfo=timezone(timedelta(hours=14)))
        utc_overflow = datetime.max.replace(tzinfo=timezone(-timedelta(hours=14)))
        cases = (
            (account_snapshot(), "not-a-time", "invalid_observed_at"),
            (account_snapshot(), utc_underflow, "invalid_observed_at"),
            (account_snapshot(codex_5h_reset_at="not-a-time"), NOW, "invalid_reset_at"),
            (account_snapshot(codex_5h_reset_at=utc_overflow), NOW, "invalid_reset_at"),
            (
                account_snapshot(codex_usage_synced_at="not-a-time"),
                NOW,
                "invalid_usage_synced_at",
            ),
            (
                account_snapshot(codex_usage_synced_at=utc_underflow),
                NOW,
                "invalid_usage_synced_at",
            ),
        )

        for account, observed_at, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason, observed_at=observed_at):
                observation = quota_detection.build_window_observation(
                    account,
                    window_type="five_hour",
                    account_type="plus",
                    observed_at=observed_at,  # type: ignore[arg-type]
                )
                self.assertEqual(observation["quality"], "invalid")
                self.assertEqual(observation["reason"], expected_reason)

    def test_rejects_credential_or_401_errors(self) -> None:
        for account in (
            account_snapshot(status="error", error_message="status 401 refresh_token_invalidated"),
            account_snapshot(status="error", error_message="OPENAI_OAUTH_TOKEN_REFRESH_FAILED"),
            account_snapshot(credentials_status="revoked"),
        ):
            with self.subTest(account=account):
                observation = quota_detection.build_window_observation(
                    account,
                    window_type="five_hour",
                    account_type="plus",
                    observed_at=NOW,
                )
                self.assertEqual(observation["quality"], "invalid")
                self.assertEqual(observation["reason"], "credential_error")

    def test_credentials_status_mapping_requires_a_usable_token_or_valid_state(self) -> None:
        invalid_statuses = (
            {"has_access_token": False, "has_refresh_token": False},
            {"has_access_token": False},
            {"has_refresh_token": False},
            {"has_id_token": True},
            {"has_access_token": "true", "has_refresh_token": False},
            {"status": "valid", "has_access_token": False, "has_refresh_token": False},
            {"status": "invalid"},
            {},
        )
        valid_statuses = (
            {"has_access_token": True, "has_refresh_token": False},
            {"has_access_token": False, "has_refresh_token": True},
            {"has_access_token": True, "has_refresh_token": True},
            {"status": "valid"},
            {"state": "valid"},
        )

        for credentials_status in invalid_statuses:
            with self.subTest(credentials_status=credentials_status):
                observation = quota_detection.build_window_observation(
                    account_snapshot(credentials_status=credentials_status),
                    window_type="five_hour",
                    account_type="plus",
                    observed_at=NOW,
                )
                self.assertEqual(observation["quality"], "invalid")
                self.assertEqual(observation["reason"], "credential_error")

        for credentials_status in valid_statuses:
            with self.subTest(credentials_status=credentials_status):
                observation = quota_detection.build_window_observation(
                    account_snapshot(credentials_status=credentials_status),
                    window_type="five_hour",
                    account_type="plus",
                    observed_at=NOW,
                )
                self.assertEqual(observation["quality"], "valid")

    def test_rejects_used_percent_above_one_hundred(self) -> None:
        observation = quota_detection.build_window_observation(
            account_snapshot(codex_5h_used_percent=100.01),
            window_type="five_hour",
            account_type="plus",
            observed_at=NOW,
        )

        self.assertEqual(observation["quality"], "invalid")
        self.assertEqual(observation["reason"], "invalid_percent")


class QuotaTransitionTests(unittest.TestCase):
    def test_first_observation_at_full_only_creates_baseline(self) -> None:
        decision = quota_detection.evaluate_transition(None, valid_observation(percent=100, cost=113.6))

        self.assertEqual(decision["action"], "baseline")
        self.assertIsNone(decision["state"]["last_under_limit_percent"])
        self.assertFalse(decision["state"]["hit_recorded"])

    def test_same_window_under_limit_observation_updates_baseline(self) -> None:
        previous = quota_detection.state_from_observation(valid_observation(percent=80, cost=90))

        decision = quota_detection.evaluate_transition(
            previous,
            valid_observation(percent=94, cost=107.2, observed_at=NOW + timedelta(minutes=1)),
        )

        self.assertEqual(decision["action"], "update")
        self.assertEqual(decision["state"]["last_under_limit_percent"], 94.0)
        self.assertEqual(decision["state"]["last_under_limit_cost_usd"], 107.2)

    def test_under_limit_to_full_creates_candidate(self) -> None:
        previous = quota_detection.state_from_observation(valid_observation(percent=94, cost=107.2))
        current = valid_observation(
            percent=100,
            cost=113.6,
            observed_at=NOW + timedelta(minutes=1),
        )

        decision = quota_detection.evaluate_transition(previous, current)

        self.assertEqual(decision["action"], "candidate")
        self.assertEqual(decision["previous_percent"], 94.0)
        self.assertEqual(decision["previous_cost_usd"], 107.2)
        self.assertEqual(decision["observed_limit_usd"], 113.6)
        self.assertTrue(decision["state"]["hit_recorded"])

    def test_percent_above_one_hundred_cannot_become_candidate(self) -> None:
        previous = quota_detection.state_from_observation(valid_observation(percent=94, cost=107.2))

        decision = quota_detection.evaluate_transition(
            previous,
            valid_observation(
                percent=100.01,
                cost=113.6,
                observed_at=NOW + timedelta(minutes=1),
            ),
        )

        self.assertEqual(decision["action"], "invalid")
        self.assertEqual(decision["reason"], "invalid_percent")
        self.assertIs(decision["state"], previous)

    def test_full_after_recorded_hit_is_ignored(self) -> None:
        previous = {
            **quota_detection.state_from_observation(valid_observation(percent=94)),
            "hit_recorded": True,
        }

        decision = quota_detection.evaluate_transition(
            previous,
            valid_observation(percent=100, observed_at=NOW + timedelta(minutes=1)),
        )

        self.assertEqual(decision["action"], "ignore")
        self.assertEqual(decision["reason"], "window_already_recorded")
        self.assertIs(decision["state"], previous)

    def test_continued_full_without_under_limit_baseline_is_ignored(self) -> None:
        previous = quota_detection.state_from_observation(valid_observation(percent=100))

        decision = quota_detection.evaluate_transition(
            previous,
            valid_observation(percent=100, observed_at=NOW + timedelta(minutes=1)),
        )

        self.assertEqual(decision["action"], "ignore")
        self.assertEqual(decision["reason"], "window_ineligible")
        self.assertIs(decision["state"], previous)

    def test_reset_change_creates_new_window_baseline(self) -> None:
        previous = {
            **quota_detection.state_from_observation(valid_observation(percent=94)),
            "hit_recorded": True,
        }
        new_reset = NOW + timedelta(hours=7)

        decision = quota_detection.evaluate_transition(
            previous,
            valid_observation(
                percent=12,
                cost=4,
                observed_at=NOW + timedelta(hours=2, minutes=1),
                reset_at=new_reset,
            ),
        )

        self.assertEqual(decision["action"], "baseline")
        self.assertEqual(decision["state"]["window_reset_at"], new_reset)
        self.assertEqual(decision["state"]["last_under_limit_percent"], 12.0)
        self.assertFalse(decision["state"]["hit_recorded"])

    def test_reset_jitter_within_two_minutes_stays_in_same_window(self) -> None:
        previous = quota_detection.state_from_observation(valid_observation(percent=80, cost=90))

        decision = quota_detection.evaluate_transition(
            previous,
            valid_observation(
                percent=90,
                cost=100,
                observed_at=NOW + timedelta(minutes=1),
                reset_at=NOW + timedelta(hours=2, minutes=2),
            ),
        )

        self.assertEqual(decision["action"], "update")

    def test_backward_reset_jitter_preserves_canonical_reset(self) -> None:
        canonical_reset = NOW + timedelta(hours=2)
        previous = quota_detection.state_from_observation(
            valid_observation(percent=80, cost=90, reset_at=canonical_reset)
        )

        decision = quota_detection.evaluate_transition(
            previous,
            valid_observation(
                percent=90,
                cost=100,
                observed_at=NOW + timedelta(minutes=1),
                reset_at=canonical_reset - timedelta(minutes=2),
            ),
        )

        self.assertEqual(decision["action"], "update")
        self.assertEqual(decision["state"]["window_reset_at"], canonical_reset)

    def test_newer_observation_with_older_reset_is_ignored(self) -> None:
        previous = quota_detection.state_from_observation(
            valid_observation(reset_at=NOW + timedelta(hours=7))
        )

        decision = quota_detection.evaluate_transition(
            previous,
            valid_observation(
                percent=12,
                cost=4,
                observed_at=NOW + timedelta(minutes=1),
                reset_at=NOW + timedelta(hours=2),
            ),
        )

        self.assertEqual(decision["action"], "ignore")
        self.assertEqual(decision["reason"], "reset_regression")
        self.assertIs(decision["state"], previous)

    def test_late_observations_do_not_regress_same_or_new_window_state(self) -> None:
        current_reset = NOW + timedelta(hours=7)
        previous = quota_detection.state_from_observation(
            valid_observation(
                percent=12,
                cost=4,
                observed_at=NOW + timedelta(hours=2),
                reset_at=current_reset,
            )
        )
        late_observations = (
            valid_observation(
                percent=90,
                cost=100,
                observed_at=NOW + timedelta(hours=1),
                reset_at=current_reset,
            ),
            valid_observation(
                percent=100,
                cost=113.6,
                observed_at=NOW + timedelta(hours=1),
                reset_at=NOW + timedelta(hours=2),
            ),
        )

        for observation in late_observations:
            with self.subTest(reset_at=observation["window_reset_at"]):
                decision = quota_detection.evaluate_transition(previous, observation)
                self.assertEqual(decision["action"], "ignore")
                self.assertEqual(decision["reason"], "late_observation")
                self.assertIs(decision["state"], previous)

    def test_cost_rollback_is_invalid_and_preserves_state(self) -> None:
        previous = quota_detection.state_from_observation(valid_observation(percent=94, cost=107.2))

        decision = quota_detection.evaluate_transition(
            previous,
            valid_observation(
                percent=100,
                cost=100,
                observed_at=NOW + timedelta(minutes=1),
            ),
        )

        self.assertEqual(decision["action"], "invalid")
        self.assertEqual(decision["reason"], "cost_rollback")
        self.assertIs(decision["state"], previous)

    def test_plan_change_is_invalid_and_preserves_state(self) -> None:
        previous = quota_detection.state_from_observation(valid_observation(account_type="plus"))

        decision = quota_detection.evaluate_transition(
            previous,
            valid_observation(
                percent=100,
                account_type="pro",
                observed_at=NOW + timedelta(minutes=1),
            ),
        )

        self.assertEqual(decision["action"], "invalid")
        self.assertEqual(decision["reason"], "account_type_changed")
        self.assertIs(decision["state"], previous)

    def test_plan_change_is_rejected_before_new_window_baseline(self) -> None:
        previous = quota_detection.state_from_observation(valid_observation(account_type="plus"))

        decision = quota_detection.evaluate_transition(
            previous,
            valid_observation(
                percent=12,
                cost=4,
                account_type="pro",
                observed_at=NOW + timedelta(minutes=1),
                reset_at=NOW + timedelta(hours=7),
            ),
        )

        self.assertEqual(decision["action"], "invalid")
        self.assertEqual(decision["reason"], "account_type_changed")
        self.assertIs(decision["state"], previous)

    def test_window_first_seen_full_never_becomes_sample_eligible(self) -> None:
        first = quota_detection.evaluate_transition(
            None,
            valid_observation(percent=100, cost=113.6),
        )
        self.assertEqual(first["action"], "baseline")
        self.assertFalse(first["state"]["sample_eligible"])

        under_limit = quota_detection.evaluate_transition(
            first["state"],
            valid_observation(
                percent=80,
                cost=114,
                observed_at=NOW + timedelta(minutes=1),
            ),
        )
        self.assertEqual(under_limit["action"], "update")
        self.assertFalse(under_limit["state"]["sample_eligible"])
        self.assertIsNone(under_limit["state"]["last_under_limit_percent"])

        full_again = quota_detection.evaluate_transition(
            under_limit["state"],
            valid_observation(
                percent=100,
                cost=120,
                observed_at=NOW + timedelta(minutes=2),
            ),
        )
        self.assertEqual(full_again["action"], "ignore")
        self.assertEqual(full_again["reason"], "window_ineligible")
        self.assertFalse(full_again["state"]["sample_eligible"])

    def test_invalid_observations_never_advance_valid_state(self) -> None:
        previous = quota_detection.state_from_observation(valid_observation())

        for reason in ("missing_window", "stale_usage", "credential_error"):
            with self.subTest(reason=reason):
                invalid = {**valid_observation(percent=100), "quality": "invalid", "reason": reason}
                decision = quota_detection.evaluate_transition(previous, invalid)
                self.assertEqual(decision["action"], "invalid")
                self.assertEqual(decision["reason"], reason)
                self.assertIs(decision["state"], previous)


class QuotaCandidateClassificationTests(unittest.TestCase):
    def test_first_five_values_establish_the_baseline(self) -> None:
        for accepted_count in range(5):
            with self.subTest(accepted_count=accepted_count):
                result = quota_detection.classify_candidate(
                    100,
                    accepted_values=[100] * accepted_count,
                )

                self.assertEqual(result["classification"], "accepted")
                self.assertEqual(result["reason"], "baseline_establishing")
                self.assertIsNone(result["direction"])
                self.assertIsNone(result["median"])
                self.assertIsNone(result["mad"])
                self.assertIsNone(result["tolerance"])
                self.assertIsNone(result["deviation"])

    def test_stable_candidate_is_accepted_with_median_and_mad_metrics(self) -> None:
        result = quota_detection.classify_candidate(
            124,
            accepted_values=[98, 99, 100, 101, 102],
        )

        self.assertEqual(result["classification"], "accepted")
        self.assertEqual(result["reason"], "within_tolerance")
        self.assertEqual(result["direction"], "above")
        self.assertEqual(result["median"], 100.0)
        self.assertEqual(result["mad"], 1.0)
        self.assertEqual(result["tolerance"], 0.25)
        self.assertEqual(result["deviation"], 0.24)

    def test_far_candidates_are_outliers_above_and_below(self) -> None:
        cases = ((126, "above"), (74, "below"))

        for value, expected_direction in cases:
            with self.subTest(value=value):
                result = quota_detection.classify_candidate(
                    value,
                    accepted_values=[98, 99, 100, 101, 102],
                )

                self.assertEqual(result["classification"], "outlier")
                self.assertEqual(result["reason"], "outside_tolerance")
                self.assertEqual(result["direction"], expected_direction)
                self.assertAlmostEqual(result["deviation"], 0.26)

    def test_mad_can_expand_tolerance_beyond_twenty_five_percent(self) -> None:
        result = quota_detection.classify_candidate(
            175,
            accepted_values=[50, 75, 100, 125, 150],
        )

        self.assertEqual(result["classification"], "accepted")
        self.assertEqual(result["mad"], 25.0)
        self.assertEqual(result["tolerance"], 0.75)
        self.assertEqual(result["deviation"], 0.75)

    def test_only_the_last_one_hundred_accepted_values_are_used(self) -> None:
        result = quota_detection.classify_candidate(
            100,
            accepted_values=([1] * 100) + ([100] * 100),
        )

        self.assertEqual(result["classification"], "accepted")
        self.assertEqual(result["median"], 100.0)
        self.assertEqual(result["mad"], 0.0)

    def test_zero_median_is_invalid_without_raising(self) -> None:
        result = quota_detection.classify_candidate(1, accepted_values=[0] * 5)

        self.assertEqual(result["classification"], "invalid")
        self.assertEqual(result["reason"], "zero_median")
        self.assertEqual(result["median"], 0.0)
        self.assertEqual(result["mad"], 0.0)
        self.assertIsNone(result["tolerance"])
        self.assertIsNone(result["deviation"])

    def test_nonfinite_or_invalid_candidate_is_rejected_without_raising(self) -> None:
        for value in (float("nan"), float("inf"), -float("inf"), None, True, "invalid"):
            with self.subTest(value=value):
                result = quota_detection.classify_candidate(
                    value,
                    accepted_values=[100] * 5,
                )

                self.assertEqual(result["classification"], "invalid")
                self.assertEqual(result["reason"], "invalid_candidate")

    def test_nonfinite_baseline_is_rejected_without_raising(self) -> None:
        result = quota_detection.classify_candidate(
            100,
            accepted_values=[100, 100, float("nan"), 100, 100],
        )

        self.assertEqual(result["classification"], "invalid")
        self.assertEqual(result["reason"], "invalid_baseline")


class QuotaGenerationCandidateTests(unittest.TestCase):
    @staticmethod
    def _outliers(
        values: list[object],
        *,
        direction: str = "above",
        remote_account_ids: list[int] | None = None,
    ) -> list[dict[str, object]]:
        account_ids = remote_account_ids or [1, 2, 3, 1, 2]
        return [
            {
                "value": value,
                "direction": direction,
                "remote_account_id": account_ids[index],
            }
            for index, value in enumerate(values)
        ]

    def test_promotes_tight_clusters_in_either_direction(self) -> None:
        for direction in ("above", "below"):
            with self.subTest(direction=direction):
                result = quota_detection.new_generation_candidate(
                    self._outliers([98, 99, 100, 101, 102], direction=direction)
                )

                self.assertTrue(result["promote"])
                self.assertEqual(result["reason"], "tight_cluster")
                self.assertEqual(result["direction"], direction)
                self.assertEqual(result["representative_value"], 100.0)
                self.assertAlmostEqual(result["relative_spread"], 0.04)

    def test_promotes_cluster_at_ten_percent_spread_boundary(self) -> None:
        result = quota_detection.new_generation_candidate(
            self._outliers([95, 100, 100, 100, 105])
        )

        self.assertTrue(result["promote"])
        self.assertAlmostEqual(result["relative_spread"], 0.10)

    def test_promotes_decimal_cluster_at_ten_percent_spread_boundary(self) -> None:
        result = quota_detection.new_generation_candidate(
            self._outliers([0.95, 1.0, 1.0, 1.0, 1.05])
        )

        self.assertTrue(result["promote"])
        self.assertAlmostEqual(result["relative_spread"], 0.10)

    def test_rejects_fewer_than_five_outliers(self) -> None:
        result = quota_detection.new_generation_candidate(
            self._outliers([99, 100, 101, 102])
        )

        self.assertFalse(result["promote"])
        self.assertEqual(result["reason"], "insufficient_outliers")
        self.assertIsNone(result["representative_value"])

    def test_rejects_fewer_than_three_remote_accounts(self) -> None:
        result = quota_detection.new_generation_candidate(
            self._outliers(
                [98, 99, 100, 101, 102],
                remote_account_ids=[1, 2, 1, 2, 1],
            )
        )

        self.assertFalse(result["promote"])
        self.assertEqual(result["reason"], "insufficient_accounts")

    def test_rejects_mixed_directions(self) -> None:
        outliers = self._outliers([98, 99, 100, 101, 102])
        outliers[-1]["direction"] = "below"

        result = quota_detection.new_generation_candidate(outliers)

        self.assertFalse(result["promote"])
        self.assertEqual(result["reason"], "mixed_direction")

    def test_rejects_dispersed_values(self) -> None:
        result = quota_detection.new_generation_candidate(
            self._outliers([80, 90, 100, 110, 120])
        )

        self.assertFalse(result["promote"])
        self.assertEqual(result["reason"], "dispersed_values")
        self.assertAlmostEqual(result["relative_spread"], 0.4)

    def test_rejects_nonfinite_values_without_raising(self) -> None:
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                result = quota_detection.new_generation_candidate(
                    self._outliers([98, 99, 100, 101, value])
                )

                self.assertFalse(result["promote"])
                self.assertEqual(result["reason"], "invalid_values")
                self.assertIsNone(result["representative_value"])

    def test_rejects_zero_or_negative_usd_values(self) -> None:
        cases = (
            [0, 0, 0, 0, 0],
            [-1.02, -1.01, -1.0, -0.99, -0.98],
        )

        for values in cases:
            with self.subTest(values=values):
                result = quota_detection.new_generation_candidate(self._outliers(values))

                self.assertFalse(result["promote"])
                self.assertEqual(result["reason"], "invalid_values")
                self.assertIsNone(result["representative_value"])

    def test_rejects_nonnumeric_values_even_when_float_convertible(self) -> None:
        cases = (
            ["98", "99", "100", "101", "102"],
            [98, 99, 100, 101, True],
        )

        for values in cases:
            with self.subTest(values=values):
                result = quota_detection.new_generation_candidate(self._outliers(values))

                self.assertFalse(result["promote"])
                self.assertEqual(result["reason"], "invalid_values")
                self.assertIsNone(result["representative_value"])


class QuotaDetectionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _account(
        remote_account_id: int,
        *,
        observed_at: datetime = NOW,
        five_hour_percent: float = 94,
        five_hour_cost: float = 107.2,
        five_hour_reset_at: datetime | None = None,
        seven_day_percent: float = 80,
        seven_day_cost: float = 112.5,
    ) -> dict[str, object]:
        return account_snapshot(
            id=remote_account_id,
            codex_usage_synced_at=observed_at - timedelta(minutes=1),
            codex_5h_used_percent=five_hour_percent,
            codex_5h_actual_cost=five_hour_cost,
            codex_5h_reset_at=five_hour_reset_at or NOW + timedelta(hours=2),
            codex_7d_used_percent=seven_day_percent,
            codex_7d_actual_cost=seven_day_cost,
        )

    async def test_bulk_loads_all_window_states_once_by_deterministic_ids(self) -> None:
        db = quota_db()
        resolver = Mock(side_effect=lambda account: "plus" if account["id"] == 1 else "pro")

        result = await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[self._account(1), self._account(2)],
            observed_at=NOW,
            account_type_for=resolver,
        )

        self.assertEqual(
            db.sub2api_quota_detection_states.find_calls,
            [
                {
                    "_id": {
                        "$in": [
                            "api-5001:1:five_hour",
                            "api-5001:1:seven_day",
                            "api-5001:2:five_hour",
                            "api-5001:2:seven_day",
                        ]
                    }
                }
            ],
        )
        self.assertEqual(resolver.call_count, 2)
        self.assertEqual(len(db.sub2api_quota_detection_states.bulk_write_calls), 1)
        operations, ordered = db.sub2api_quota_detection_states.bulk_write_calls[0]
        self.assertEqual(len(operations), 4)
        self.assertFalse(ordered)
        self.assertTrue(all(operation._upsert for operation in operations))
        self.assertTrue(all("$setOnInsert" in operation._doc for operation in operations))
        self.assertTrue(all("$set" not in operation._doc for operation in operations))
        self.assertEqual(
            result,
            {
                "site_id": "api-5001",
                "status": "ok",
                "observed": 4,
                "accepted": 0,
                "outlier": 0,
                "invalid": 0,
                "ignored": 0,
                "baseline": 4,
                "updated": 0,
            },
        )

    async def test_first_under_limit_observation_stores_baseline_only(self) -> None:
        db = quota_db()

        result = await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[self._account(953)],
            observed_at=NOW,
            account_type_for=lambda _account: "plus",
        )

        self.assertEqual(result["baseline"], 2)
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["outlier"], 0)
        self.assertEqual(db.sub2api_quota_limit_samples.documents, {})
        self.assertEqual(db.sub2api_quota_limit_samples.bulk_write_calls, [])
        five_hour_state = db.sub2api_quota_detection_states.documents[
            "api-5001:953:five_hour"
        ]
        self.assertEqual(five_hour_state["expires_at"], NOW + timedelta(days=30))
        self.assertEqual(five_hour_state["last_under_limit_cost_usd"], 107.2)
        self.assertNotIn("credentials", five_hour_state)
        self.assertNotIn("extra", five_hour_state)

    async def test_next_full_observation_inserts_one_sample_with_allowed_fields(self) -> None:
        db = quota_db()
        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[self._account(953)],
            observed_at=NOW,
            account_type_for=lambda _account: "plus",
        )
        hit_at = NOW + timedelta(minutes=1)

        result = await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[
                self._account(
                    953,
                    observed_at=hit_at,
                    five_hour_percent=100,
                    five_hour_cost=113.6,
                )
            ],
            observed_at=hit_at,
            account_type_for=lambda _account: "plus",
        )

        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(len(db.sub2api_quota_limit_samples.documents), 1)
        sample = next(iter(db.sub2api_quota_limit_samples.documents.values()))
        self.assertEqual(
            set(sample),
            {
                "_id",
                "site_id",
                "remote_account_id",
                "account_type",
                "window_type",
                "window_reset_at",
                "hit_at",
                "observed_limit_usd",
                "previous_percent",
                "previous_cost_usd",
                "classification",
                "reason",
                "direction",
                "generation",
                "expires_at",
            },
        )
        self.assertEqual(sample["classification"], "accepted")
        self.assertEqual(sample["generation"], 1)
        self.assertEqual(sample["expires_at"], hit_at + timedelta(days=90))
        forbidden = {
            "credentials",
            "extra",
            "account",
            "usage_snapshot",
            "email",
            "name",
        }
        self.assertTrue(forbidden.isdisjoint(sample))
        profile = db.sub2api_quota_limit_profiles.documents["api-5001:plus:five_hour"]
        self.assertEqual(profile["current_generation"], 1)
        self.assertNotIn("expires_at", profile)

    async def test_repeating_full_observation_does_not_insert_or_count_again(self) -> None:
        db = quota_db()
        baseline = self._account(953)
        hit_at = NOW + timedelta(minutes=1)
        full = self._account(
            953,
            observed_at=hit_at,
            five_hour_percent=100,
            five_hour_cost=113.6,
        )
        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[baseline],
            observed_at=NOW,
            account_type_for=lambda _account: "plus",
        )
        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[full],
            observed_at=hit_at,
            account_type_for=lambda _account: "plus",
        )
        sample_writes = len(db.sub2api_quota_limit_samples.bulk_write_calls)
        rollup_writes = len(db.sub2api_quota_limit_daily_rollups.update_one_calls)

        result = await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[full],
            observed_at=hit_at,
            account_type_for=lambda _account: "plus",
        )

        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["outlier"], 0)
        self.assertEqual(result["ignored"], 2)
        self.assertEqual(len(db.sub2api_quota_limit_samples.documents), 1)
        self.assertEqual(len(db.sub2api_quota_limit_samples.bulk_write_calls), sample_writes)
        self.assertEqual(
            len(db.sub2api_quota_limit_daily_rollups.update_one_calls),
            rollup_writes,
        )

    async def test_retry_repairs_rollup_after_sample_insert_succeeds(self) -> None:
        db = quota_db()
        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[self._account(953)],
            observed_at=NOW,
            account_type_for=lambda _account: "plus",
        )
        hit_at = NOW + timedelta(minutes=1)
        full = self._account(
            953,
            observed_at=hit_at,
            five_hour_percent=100,
            five_hour_cost=113.6,
        )
        rollups = db.sub2api_quota_limit_daily_rollups
        rollups.fail_update_one_once = True

        with self.assertRaisesRegex(RuntimeError, "rollup update failed"):
            await quota_detection.observe_account_quota_limits(
                db,
                site_id="api-5001",
                accounts=[full],
                observed_at=hit_at,
                account_type_for=lambda _account: "plus",
            )

        self.assertEqual(len(db.sub2api_quota_limit_samples.documents), 1)
        self.assertEqual(rollups.documents, {})
        state = db.sub2api_quota_detection_states.documents[
            "api-5001:953:five_hour"
        ]
        self.assertFalse(state["hit_recorded"])

        result = await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[full],
            observed_at=hit_at,
            account_type_for=lambda _account: "plus",
        )

        self.assertEqual(len(db.sub2api_quota_limit_samples.documents), 1)
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["ignored"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(
            sum(
                result[key]
                for key in (
                    "accepted",
                    "outlier",
                    "invalid",
                    "ignored",
                    "baseline",
                    "updated",
                )
            ),
            result["observed"],
        )
        sample_id = "api-5001:953:five_hour:2026-07-20T14:00:00Z"
        self.assertIn(
            {"_id": sample_id},
            db.sub2api_quota_limit_samples.find_one_calls,
        )
        rollup = rollups.documents["api-5001:plus:five_hour:1:2026-07-20"]
        self.assertEqual(rollup["sample_count"], 1)
        self.assertEqual(rollup["sample_sum_usd"], 113.6)
        self.assertEqual(len(rollups.update_one_calls), 2)

    async def test_new_reset_window_can_contribute_another_sample(self) -> None:
        db = quota_db()
        first_hit_at = NOW + timedelta(minutes=1)
        new_reset_at = NOW + timedelta(hours=5)
        new_baseline_at = NOW + timedelta(minutes=2)
        second_hit_at = NOW + timedelta(minutes=3)
        observations = (
            (NOW, self._account(953)),
            (
                first_hit_at,
                self._account(
                    953,
                    observed_at=first_hit_at,
                    five_hour_percent=100,
                    five_hour_cost=113.6,
                ),
            ),
            (
                new_baseline_at,
                self._account(
                    953,
                    observed_at=new_baseline_at,
                    five_hour_percent=20,
                    five_hour_cost=20,
                    five_hour_reset_at=new_reset_at,
                ),
            ),
            (
                second_hit_at,
                self._account(
                    953,
                    observed_at=second_hit_at,
                    five_hour_percent=100,
                    five_hour_cost=115,
                    five_hour_reset_at=new_reset_at,
                ),
            ),
        )

        for observed_at, account in observations:
            await quota_detection.observe_account_quota_limits(
                db,
                site_id="api-5001",
                accounts=[account],
                observed_at=observed_at,
                account_type_for=lambda _account: "plus",
            )

        sample_ids = set(db.sub2api_quota_limit_samples.documents)
        self.assertEqual(
            sample_ids,
            {
                "api-5001:953:five_hour:2026-07-20T14:00:00Z",
                "api-5001:953:five_hour:2026-07-20T17:00:00Z",
            },
        )

    async def test_invalid_observation_does_not_overwrite_valid_state(self) -> None:
        db = quota_db()
        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[self._account(953)],
            observed_at=NOW,
            account_type_for=lambda _account: "plus",
        )
        before = {
            key: dict(document)
            for key, document in db.sub2api_quota_detection_states.documents.items()
        }
        invalid_at = NOW + timedelta(minutes=1)
        invalid = self._account(953, observed_at=invalid_at, five_hour_percent=100)
        invalid["codex_usage_synced_at"] = NOW - timedelta(minutes=10)

        result = await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[invalid],
            observed_at=invalid_at,
            account_type_for=lambda _account: "plus",
        )

        self.assertEqual(result["invalid"], 2)
        self.assertEqual(db.sub2api_quota_detection_states.documents, before)

    async def test_stale_state_write_cannot_overwrite_newer_observation(self) -> None:
        db = quota_db()
        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[self._account(953)],
            observed_at=NOW,
            account_type_for=lambda _account: "plus",
        )
        states = db.sub2api_quota_detection_states
        state_id = "api-5001:953:five_hour"
        concurrent_at = NOW + timedelta(minutes=2)

        def advance_state(collection: MemoryCollection) -> None:
            newer = dict(collection.documents[state_id])
            newer["last_observed_at"] = concurrent_at
            newer["last_under_limit_percent"] = 99.0
            newer["last_under_limit_cost_usd"] = 112.0
            collection.documents[state_id] = newer

        states.before_bulk_write = advance_state
        observed_at = NOW + timedelta(minutes=1)
        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[
                self._account(
                    953,
                    observed_at=observed_at,
                    five_hour_percent=95,
                    five_hour_cost=108,
                )
            ],
            observed_at=observed_at,
            account_type_for=lambda _account: "plus",
        )

        persisted = states.documents[state_id]
        self.assertEqual(persisted["last_observed_at"], concurrent_at)
        self.assertEqual(persisted["last_under_limit_percent"], 99.0)
        operations, ordered = states.bulk_write_calls[-1]
        self.assertFalse(ordered)
        five_hour_operation = next(
            operation
            for operation in operations
            if operation._filter.get("_id") == state_id
        )
        self.assertEqual(
            five_hour_operation._filter,
            {"_id": state_id, "last_observed_at": NOW},
        )
        self.assertFalse(five_hour_operation._upsert)

    async def test_five_hour_and_seven_day_candidates_are_independent(self) -> None:
        db = quota_db()
        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[self._account(953)],
            observed_at=NOW,
            account_type_for=lambda _account: "plus",
        )
        hit_at = NOW + timedelta(minutes=1)

        result = await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[
                self._account(
                    953,
                    observed_at=hit_at,
                    five_hour_percent=100,
                    five_hour_cost=113.6,
                    seven_day_percent=100,
                    seven_day_cost=145.2,
                )
            ],
            observed_at=hit_at,
            account_type_for=lambda _account: "plus",
        )

        self.assertEqual(result["accepted"], 2)
        self.assertEqual(
            {sample["window_type"] for sample in db.sub2api_quota_limit_samples.documents.values()},
            {"five_hour", "seven_day"},
        )

    async def test_resolver_runs_once_per_account_and_known_types_stay_separate(self) -> None:
        db = quota_db()
        account_types = ("free", "plus", "team", "bug_team", "k12", "pro")
        type_by_id = {index: account_type for index, account_type in enumerate(account_types, 1)}
        resolver = Mock(side_effect=lambda account: type_by_id[account["id"]])
        baselines = [self._account(account_id) for account_id in type_by_id]
        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=baselines,
            observed_at=NOW,
            account_type_for=resolver,
        )
        hit_at = NOW + timedelta(minutes=1)
        full = [
            self._account(
                account_id,
                observed_at=hit_at,
                five_hour_percent=100,
                five_hour_cost=113.6,
                seven_day_percent=100,
                seven_day_cost=145.2,
            )
            for account_id in type_by_id
        ]

        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=full,
            observed_at=hit_at,
            account_type_for=resolver,
        )

        self.assertEqual(resolver.call_count, 12)
        dimensions = {
            (sample["account_type"], sample["window_type"])
            for sample in db.sub2api_quota_limit_samples.documents.values()
        }
        self.assertEqual(
            dimensions,
            {
                (account_type, window_type)
                for account_type in account_types
                for window_type in ("five_hour", "seven_day")
            },
        )


class QuotaProfileAndRollupTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_starts_at_generation_one_and_is_permanent(self) -> None:
        db = quota_db()

        profile = await quota_detection._profile_for_dimension(
            db,
            site_id="api-5001",
            account_type="plus",
            window_type="five_hour",
            observed_at=NOW,
        )

        self.assertEqual(profile["_id"], "api-5001:plus:five_hour")
        self.assertEqual(profile["current_generation"], 1)
        self.assertEqual(profile["generation_started_at"], NOW)
        self.assertNotIn("expires_at", profile)

    async def test_recent_accepted_loader_returns_only_latest_one_hundred_in_time_order(self) -> None:
        db = quota_db()
        samples = db.sub2api_quota_limit_samples
        for index in range(105):
            document = {
                "_id": f"sample-{index}",
                "site_id": "api-5001",
                "account_type": "plus",
                "window_type": "five_hour",
                "generation": 1,
                "classification": "accepted",
                "hit_at": NOW + timedelta(seconds=index),
                "observed_limit_usd": float(index),
            }
            samples.documents[document["_id"]] = document

        values = await quota_detection._recent_dimension_samples(
            db,
            site_id="api-5001",
            account_type="plus",
            window_type="five_hour",
            generation=1,
        )

        self.assertEqual(values, [float(index) for index in range(5, 105)])
        self.assertEqual(
            samples.cursors[-1].sort_args,
            ([('hit_at', -1), ('_id', -1)],),
        )
        self.assertEqual(samples.cursors[-1].limit_value, 100)
        self.assertEqual(samples.find_calls[-1]["classification"], "accepted")

    async def test_five_clustered_outliers_promote_once_and_become_accepted(self) -> None:
        db = quota_db()
        samples = db.sub2api_quota_limit_samples
        profiles = db.sub2api_quota_limit_profiles
        profiles.documents["api-5001:plus:five_hour"] = {
            "_id": "api-5001:plus:five_hour",
            "site_id": "api-5001",
            "account_type": "plus",
            "window_type": "five_hour",
            "current_generation": 1,
            "generation_started_at": NOW - timedelta(days=1),
            "last_evaluated_at": NOW,
        }
        for index, value in enumerate((98, 99, 100, 101, 102), 1):
            samples.documents[f"accepted-{index}"] = {
                "_id": f"accepted-{index}",
                "site_id": "api-5001",
                "remote_account_id": 100 + index,
                "account_type": "plus",
                "window_type": "five_hour",
                "window_reset_at": NOW,
                "hit_at": NOW - timedelta(minutes=10 - index),
                "observed_limit_usd": float(value),
                "previous_percent": 90.0,
                "previous_cost_usd": 90.0,
                "classification": "accepted",
                "reason": "within_tolerance",
                "direction": "above",
                "generation": 1,
                "expires_at": NOW + timedelta(days=90),
            }
        accounts = [
            QuotaDetectionPersistenceTests._account(
                account_id,
                seven_day_percent=100,
            )
            for account_id in range(1, 6)
        ]
        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=accounts,
            observed_at=NOW,
            account_type_for=lambda _account: "plus",
        )
        hit_at = NOW + timedelta(minutes=1)
        full_accounts = [
            QuotaDetectionPersistenceTests._account(
                account_id,
                observed_at=hit_at,
                five_hour_percent=100,
                five_hour_cost=value,
                seven_day_percent=100,
            )
            for account_id, value in zip(range(1, 6), (198, 199, 200, 201, 202), strict=True)
        ]

        result = await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=full_accounts,
            observed_at=hit_at,
            account_type_for=lambda _account: "plus",
        )

        promoted = [
            sample
            for sample in samples.documents.values()
            if sample["remote_account_id"] in range(1, 6)
        ]
        self.assertEqual(result["accepted"], 5)
        self.assertEqual(result["outlier"], 0)
        self.assertEqual(len(promoted), 5)
        self.assertTrue(all(sample["classification"] == "accepted" for sample in promoted))
        self.assertTrue(all(sample["generation"] == 2 for sample in promoted))
        self.assertTrue(all(sample["reason"] == "generation_promoted" for sample in promoted))
        self.assertEqual(profiles.documents["api-5001:plus:five_hour"]["current_generation"], 2)
        cas_queries = [
            query
            for query, _update, _upsert in profiles.update_one_calls
            if query.get("current_generation") == 1
        ]
        self.assertEqual(
            cas_queries,
            [{"_id": "api-5001:plus:five_hour", "current_generation": 1}],
        )
        rollup = db.sub2api_quota_limit_daily_rollups.documents[
            "api-5001:plus:five_hour:2:2026-07-20"
        ]
        self.assertEqual(rollup["sample_count"], 5)
        self.assertEqual(rollup["sample_sum_usd"], 1000.0)

    async def test_duplicate_outlier_retries_generation_promotion(self) -> None:
        db = quota_db()
        await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[QuotaDetectionPersistenceTests._account(5)],
            observed_at=NOW,
            account_type_for=lambda _account: "plus",
        )
        samples = db.sub2api_quota_limit_samples
        profiles = db.sub2api_quota_limit_profiles
        profile_id = "api-5001:plus:five_hour"
        profiles.documents[profile_id] = {
            "_id": profile_id,
            "site_id": "api-5001",
            "account_type": "plus",
            "window_type": "five_hour",
            "current_generation": 1,
            "generation_started_at": NOW - timedelta(days=1),
            "last_evaluated_at": NOW,
        }
        for index, value in enumerate((98, 99, 100, 101, 102), 1):
            samples.documents[f"accepted-{index}"] = {
                "_id": f"accepted-{index}",
                "site_id": "api-5001",
                "remote_account_id": 100 + index,
                "account_type": "plus",
                "window_type": "five_hour",
                "window_reset_at": NOW,
                "hit_at": NOW - timedelta(minutes=10 - index),
                "observed_limit_usd": float(value),
                "previous_percent": 90.0,
                "previous_cost_usd": 90.0,
                "classification": "accepted",
                "reason": "within_tolerance",
                "direction": "above",
                "generation": 1,
                "expires_at": NOW + timedelta(days=90),
            }
        hit_at = NOW + timedelta(minutes=1)
        for index, value in enumerate((198, 199, 200, 201, 202), 1):
            sample_id = (
                f"api-5001:{index}:five_hour:2026-07-20T14:00:00Z"
            )
            samples.documents[sample_id] = {
                "_id": sample_id,
                "site_id": "api-5001",
                "remote_account_id": index,
                "account_type": "plus",
                "window_type": "five_hour",
                "window_reset_at": NOW + timedelta(hours=2),
                "hit_at": hit_at - timedelta(seconds=5 - index),
                "observed_limit_usd": float(value),
                "previous_percent": 94.0,
                "previous_cost_usd": 107.2,
                "classification": "outlier",
                "reason": "outside_tolerance",
                "direction": "above",
                "generation": 1,
                "expires_at": hit_at + timedelta(days=90),
            }
        duplicate_id = "api-5001:5:five_hour:2026-07-20T14:00:00Z"

        result = await quota_detection.observe_account_quota_limits(
            db,
            site_id="api-5001",
            accounts=[
                QuotaDetectionPersistenceTests._account(
                    5,
                    observed_at=hit_at,
                    five_hour_percent=100,
                    five_hour_cost=202,
                )
            ],
            observed_at=hit_at,
            account_type_for=lambda _account: "plus",
        )

        self.assertEqual(len(samples.documents), 10)
        self.assertIn({"_id": duplicate_id}, samples.find_one_calls)
        self.assertEqual(result["ignored"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["accepted"], 0)
        self.assertEqual(result["outlier"], 0)
        self.assertEqual(profiles.documents[profile_id]["current_generation"], 2)
        promoted = [
            sample
            for sample in samples.documents.values()
            if sample.get("remote_account_id") in range(1, 6)
        ]
        self.assertEqual(len(promoted), 5)
        self.assertTrue(all(sample["classification"] == "accepted" for sample in promoted))
        self.assertTrue(all(sample["generation"] == 2 for sample in promoted))
        rollup = db.sub2api_quota_limit_daily_rollups.documents[
            "api-5001:plus:five_hour:2:2026-07-20"
        ]
        self.assertEqual(rollup["sample_count"], 5)
        self.assertEqual(rollup["sample_sum_usd"], 1000.0)

    async def test_generation_cas_loser_does_not_reclassify_outliers(self) -> None:
        db = quota_db()
        profiles = db.sub2api_quota_limit_profiles
        samples = db.sub2api_quota_limit_samples
        profile = {
            "_id": "api-5001:plus:five_hour",
            "site_id": "api-5001",
            "account_type": "plus",
            "window_type": "five_hour",
            "current_generation": 1,
            "generation_started_at": NOW - timedelta(days=1),
            "last_evaluated_at": NOW,
        }
        profiles.documents[profile["_id"]] = profile
        profiles.fail_generation_cas = True
        for index, value in enumerate((198, 199, 200, 201, 202), 1):
            samples.documents[f"outlier-{index}"] = {
                "_id": f"outlier-{index}",
                "site_id": "api-5001",
                "remote_account_id": index,
                "account_type": "plus",
                "window_type": "five_hour",
                "hit_at": NOW + timedelta(seconds=index),
                "observed_limit_usd": float(value),
                "classification": "outlier",
                "reason": "outside_tolerance",
                "direction": "above",
                "generation": 1,
            }

        result = await quota_detection._promote_generation_if_ready(
            db,
            profile=profile,
            observed_at=NOW + timedelta(minutes=1),
        )

        self.assertFalse(result["promoted"])
        self.assertEqual(result["reason"], "generation_changed")
        self.assertEqual(samples.update_many_calls, [])
        self.assertEqual(
            samples.cursors[-1].sort_args,
            ([('hit_at', -1), ('_id', -1)],),
        )
        self.assertEqual(samples.cursors[-1].limit_value, 5)
        self.assertTrue(
            all(sample["classification"] == "outlier" for sample in samples.documents.values())
        )

    async def test_daily_rollup_replacement_deduplicates_source_documents(self) -> None:
        db = quota_db()
        samples = db.sub2api_quota_limit_samples
        hit_times = (
            datetime(2026, 7, 19, 16, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 3, 0, tzinfo=UTC),
            datetime(2026, 7, 20, 15, 59, tzinfo=UTC),
        )
        for index, (value, hit_at) in enumerate(zip((108, 110, 112), hit_times, strict=True), 1):
            samples.documents[f"sample-{index}"] = {
                "_id": f"sample-{index}",
                "site_id": "api-5001",
                "account_type": "plus",
                "window_type": "five_hour",
                "generation": 1,
                "classification": "accepted",
                "hit_at": hit_at,
                "observed_limit_usd": float(value),
            }
        samples.duplicate_find_results = True

        first = await quota_detection._rebuild_daily_rollup(
            db,
            site_id="api-5001",
            account_type="plus",
            window_type="five_hour",
            generation=1,
            local_date="2026-07-20",
        )
        second = await quota_detection._rebuild_daily_rollup(
            db,
            site_id="api-5001",
            account_type="plus",
            window_type="five_hour",
            generation=1,
            local_date="2026-07-20",
        )

        expected = {
            "sample_count": 3,
            "sample_sum_usd": 330.0,
            "sample_min_usd": 108.0,
            "sample_max_usd": 112.0,
        }
        self.assertEqual({key: first[key] for key in expected}, expected)
        self.assertEqual(first, second)
        self.assertNotIn("expires_at", first)
        self.assertEqual(len(db.sub2api_quota_limit_daily_rollups.documents), 1)
        self.assertEqual(len(db.sub2api_quota_limit_daily_rollups.update_one_calls), 2)


class QuotaDetectionSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_uses_current_generation_and_weighted_average(self) -> None:
        db = quota_db()
        db.sub2api_quota_limit_profiles.documents.update(
            {
                "api-5001:plus:five_hour": {
                    "_id": "api-5001:plus:five_hour",
                    "site_id": "api-5001",
                    "account_type": "plus",
                    "window_type": "five_hour",
                    "current_generation": 2,
                    "generation_started_at": NOW,
                    "last_evaluated_at": NOW + timedelta(minutes=2),
                },
                "api-5001:plus:seven_day": {
                    "_id": "api-5001:plus:seven_day",
                    "site_id": "api-5001",
                    "account_type": "plus",
                    "window_type": "seven_day",
                    "current_generation": 1,
                    "generation_started_at": NOW,
                    "last_evaluated_at": NOW + timedelta(minutes=1),
                },
            }
        )
        db.sub2api_quota_limit_daily_rollups.documents.update(
            {
                "r1": {
                    "_id": "r1", "site_id": "api-5001", "account_type": "plus",
                    "window_type": "five_hour", "generation": 2,
                    "sample_count": 1, "sample_sum_usd": 100.0,
                    "sample_min_usd": 100.0, "sample_max_usd": 100.0,
                },
                "r2": {
                    "_id": "r2", "site_id": "api-5001", "account_type": "plus",
                    "window_type": "five_hour", "generation": 2,
                    "sample_count": 2, "sample_sum_usd": 230.0,
                    "sample_min_usd": 110.0, "sample_max_usd": 120.0,
                },
                "old": {
                    "_id": "old", "site_id": "api-5001", "account_type": "plus",
                    "window_type": "five_hour", "generation": 1,
                    "sample_count": 50, "sample_sum_usd": 5000.0,
                    "sample_min_usd": 90.0, "sample_max_usd": 110.0,
                },
            }
        )

        result = await quota_detection.get_quota_detection_summary(db, "api-5001")

        self.assertEqual([item["account_type"] for item in result["items"][:6]], ["free", "plus", "team", "bug_team", "k12", "pro"])
        plus = result["items"][1]
        self.assertEqual(plus["five_hour"]["sample_count"], 3)
        self.assertEqual(plus["five_hour"]["average_usd"], 110.0)
        self.assertEqual(plus["five_hour"]["minimum_usd"], 100.0)
        self.assertEqual(plus["five_hour"]["maximum_usd"], 120.0)
        self.assertEqual(plus["five_hour"]["generation"], 2)
        self.assertEqual(plus["seven_day"]["sample_count"], 0)
        self.assertEqual(result["last_evaluated_at"], NOW + timedelta(minutes=2))


if __name__ == "__main__":
    unittest.main()
