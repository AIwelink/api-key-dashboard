from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from app.modules.sub2api import account_probe


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self._items = iter(items)

    def __aiter__(self) -> "AsyncCursor":
        return self

    async def __anext__(self) -> dict[str, object]:
        try:
            return next(self._items)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class AccountProbeSchedulingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:
        account_probe._probe_tasks.clear()

    async def test_same_site_concurrent_probes_share_one_run(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def run_probe(*_: object, **__: object) -> dict[str, object]:
            started.set()
            await release.wait()
            return {"ok": True, "site_id": "api-5001"}

        runner = AsyncMock(side_effect=run_probe)
        with patch.object(account_probe, "_run_site_account_probe", runner):
            first = asyncio.create_task(account_probe.probe_site_accounts(object(), site_id="api-5001", group_ids=[3]))
            await started.wait()
            second = asyncio.create_task(account_probe.probe_site_accounts(object(), site_id="api-5001"))
            await asyncio.sleep(0)
            release.set()
            first_result, second_result = await asyncio.gather(first, second)

        self.assertEqual(first_result, second_result)
        runner.assert_awaited_once_with(ANY, site_id="api-5001", group_ids=[3])

    async def test_scheduling_enabled_group_is_due_when_observability_is_disabled(self) -> None:
        settings = {
            3: {
                "enabled": False,
                "type_priority_enabled": True,
                "quota_acceleration_enabled": False,
                "probe_interval_seconds": 60,
            }
        }

        with patch.object(
            account_probe,
            "_settings_for_site",
            AsyncMock(return_value=settings),
        ):
            due = await account_probe._due_group_ids(object(), "api-5001")

        self.assertEqual(due, [3])

    async def test_probe_scheduling_adapter_passes_the_already_fetched_accounts(self) -> None:
        snapshot_accounts = [
            {
                "remote_account_id": 7,
                "group_ids": [3],
                "priority": 250,
                "concurrency": 20,
                "account_type": "plus",
                "usage_snapshot": {},
            }
        ]
        schedule_result = {
            "scanned": 1,
            "changed": 1,
            "unchanged": 0,
            "skipped": 0,
            "failed": 0,
        }

        with patch.object(
            account_probe,
            "run_smart_scheduling",
            AsyncMock(return_value=schedule_result),
            create=True,
        ) as schedule:
            result = await account_probe._run_smart_scheduling_for_probe(
                object(),
                site={"id": "api-5001"},
                accounts=snapshot_accounts,
                group_settings={3: {"type_priority_enabled": True}},
                probe_run_id="probe-1",
            )

        self.assertIs(schedule.await_args.kwargs["accounts"], snapshot_accounts)
        self.assertEqual(result["smart_scheduling_changed"], 1)

    async def test_site_probe_fetches_postgres_snapshot_once_when_scheduling_runs(self) -> None:
        raw_accounts = [
            {
                "id": 7,
                "email": "plus@example.com",
                "group_ids": [3, 4],
                "priority": 250,
                "concurrency": 20,
                "credentials": {},
                "extra": {},
            }
        ]
        settings = {
            3: {
                "enabled": False,
                "type_priority_enabled": True,
                "quota_acceleration_enabled": False,
                "probe_interval_seconds": 60,
            },
            4: {
                "enabled": False,
                "type_priority_enabled": False,
                "quota_acceleration_enabled": True,
                "probe_interval_seconds": 60,
            },
        }
        fetch_accounts = AsyncMock(return_value=raw_accounts)
        schedule = AsyncMock(
            return_value={
                "scanned": 1,
                "changed": 0,
                "unchanged": 1,
                "skipped": 0,
                "failed": 0,
            }
        )
        db = SimpleNamespace(
            remote_account_probe_runs=SimpleNamespace(
                insert_one=AsyncMock(),
                update_one=AsyncMock(),
            ),
            remote_account_identities=SimpleNamespace(
                find=MagicMock(return_value=AsyncCursor([])),
                find_one=AsyncMock(return_value=None),
            ),
            group_observability_settings=SimpleNamespace(update_many=AsyncMock()),
            remote_account_probe_meta=SimpleNamespace(update_one=AsyncMock()),
        )

        with (
            patch.object(account_probe, "get_site", AsyncMock(return_value={"id": "api-5001", "site_type": "sub2api"})),
            patch.object(account_probe, "is_sub2api_site", return_value=True),
            patch.object(account_probe, "_settings_for_site", AsyncMock(return_value=settings)),
            patch.object(account_probe, "_fetch_probe_accounts", fetch_accounts),
            patch.object(account_probe, "_load_verified_plan_states", AsyncMock(return_value={})),
            patch.object(account_probe, "run_smart_scheduling", schedule, create=True),
            patch.object(
                account_probe,
                "persist_history_changes",
                AsyncMock(return_value={"changed_accounts": 0, "changed_fields": 0, "batches": 0}),
            ),
            patch.object(account_probe, "_ensure_session", AsyncMock(return_value={})),
            patch.object(account_probe, "_update_identity_and_events", AsyncMock(return_value=False)),
            patch.object(
                account_probe,
                "_mark_missing_identities",
                AsyncMock(return_value={"accounts_missing_suspected": 0, "accounts_removed_confirmed": 0}),
            ),
        ):
            result = await account_probe._run_site_account_probe(
                db,
                site_id="api-5001",
                group_ids=[3],
            )

        fetch_accounts.assert_awaited_once()
        schedule.assert_awaited_once()
        scheduled_account = schedule.await_args.kwargs["accounts"][0]
        self.assertEqual(scheduled_account["plan_type"], "k12")
        self.assertEqual(scheduled_account["account_type"], "k12")
        self.assertEqual(schedule.await_args.kwargs["group_settings"], settings)
        self.assertEqual(result["smart_scheduling_unchanged"], 1)
        self.assertEqual(result["accounts_seen"], 0)
        self.assertEqual(result["group_ids_checked"], [3])

    async def test_cached_plan_type_is_resolved_before_scheduling(self) -> None:
        accounts = [
            {
                "normalized_email": "cached@example.com",
                "plan_type": "",
                "plan_type_source": None,
                "account_type": "unknown",
            }
        ]
        db = SimpleNamespace(
            remote_account_identities=SimpleNamespace(
                find=MagicMock(
                    return_value=AsyncCursor(
                        [
                            {
                                "_id": "api-5001:cached@example.com",
                                "plan_type": "plus",
                                "plan_type_source": "remote",
                            }
                        ]
                    )
                )
            )
        )

        await account_probe._resolve_scheduling_account_types(
            db,
            site_id="api-5001",
            accounts=accounts,
        )

        self.assertEqual(accounts[0]["plan_type"], "plus")
        self.assertEqual(accounts[0]["plan_type_source"], "cached")
        self.assertEqual(accounts[0]["account_type"], "plus")


class AccountProbeDatabaseSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_accounts_are_loaded_from_postgres_without_http(self) -> None:
        site = {
            "id": "api-5001",
            "base_url": "http://127.0.0.1:5001",
            "token": "secret",
            "sql_dsn": "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
        }
        fetch_pool = AsyncMock(return_value={"groups": [{"id": 3}], "accounts": [{"id": 953, "group_ids": [3]}]})

        with patch.object(account_probe, "fetch_pool_snapshot", fetch_pool):
            accounts = await account_probe._fetch_probe_accounts(site)

        self.assertEqual(accounts, [{"id": 953, "group_ids": [3]}])
        fetch_pool.assert_awaited_once_with(site["sql_dsn"])

    async def test_probe_requires_sql_dsn_instead_of_falling_back_to_http(self) -> None:
        with self.assertRaisesRegex(ValueError, "SQL_DSN"):
            await account_probe._fetch_probe_accounts(
                {"id": "api-5001", "base_url": "http://127.0.0.1:5001", "token": "secret"}
            )

    async def test_status_events_do_not_duplicate_usage_snapshots(self) -> None:
        events = SimpleNamespace(insert_one=AsyncMock(return_value=SimpleNamespace(inserted_id="event-1")))
        db = SimpleNamespace(remote_account_status_events=events)

        await account_probe._write_event(
            db,
            site_id="api-5001",
            event_type="status_changed",
            severity="warning",
            detected_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
            account={
                "remote_account_id": 953,
                "normalized_email": "person@example.com",
                "usage_snapshot": {"codex_5h_used_percent": 42},
            },
        )

        stored = events.insert_one.await_args.args[0]
        self.assertNotIn("usage_snapshot", stored)


class AccountProbePlanTypeTests(unittest.TestCase):
    def test_normalized_account_keeps_scheduling_runtime_values(self) -> None:
        account = account_probe._normalize_probe_account(
            {
                "id": 7,
                "priority": 250,
                "concurrency": 20,
                "credentials": {"plan_type": "plus"},
                "extra": {},
            }
        )

        self.assertEqual(account["priority"], 250)
        self.assertEqual(account["concurrency"], 20)

    def test_normalized_account_keeps_special_quota_classification(self) -> None:
        account = account_probe._normalize_probe_account(
            {
                "id": 3584,
                "name": "ordinary account",
                "quota_dimension": "dedicated",
                "credentials": {"email": "special@example.com", "plan_type": "plus"},
            }
        )

        self.assertEqual(account["account_type"], "special_plus")

    def test_standard_plus_name_supplies_missing_remote_plan_type(self) -> None:
        account = account_probe._normalize_probe_account(
            {
                "id": 3585,
                "name": "plus +56959278873---taftaubertine14500@outlook.com",
                "credentials": {
                    "chatgpt_account_id": "account-3585",
                    "email": "taftaubertine14500@outlook.com",
                    "plan_type": "   ",
                },
                "extra": {
                    "codex_5h_window_minutes": 0,
                    "codex_7d_window_minutes": 10_080,
                },
            }
        )

        self.assertEqual(account["plan_type"], "plus")
        self.assertEqual(account["plan_type_source"], "name_prefix")
        self.assertEqual(
            account_probe._resolved_probe_plan_type(
                account["plan_type"],
                None,
                current_source=account["plan_type_source"],
            ),
            ("plus", "name_prefix"),
        )

    def test_cached_effective_plan_type_wins_over_name_inference(self) -> None:
        self.assertEqual(
            account_probe._resolved_probe_plan_type("plus", "pro", current_source="name_prefix"),
            ("pro", "cached"),
        )

    def test_sub_bundle_free_requires_persisted_test_verification(self) -> None:
        raw = {
                "id": 4072,
                "name": "jamisonlofaso480829@outlook.com",
                "credentials": {
                    "email": "jamisonlofaso480829@outlook.com",
                    "plan_type": "free",
                },
                "extra": {
                    "source": "sub_bundle_input",
                    "codex_5h_window_minutes": 0,
                    "codex_7d_window_minutes": 10_080,
                },
                "groups": [{"id": 3, "name": "plus 账号池 01"}],
            }
        account = account_probe._normalize_probe_account(raw)

        self.assertEqual(account["plan_type"], "free")
        self.assertIsNone(account["plan_type_source"])

        account = account_probe._normalize_probe_account(
            account_probe._account_with_verified_plan_type(
                raw,
                {"verified_plan_type": "plus"},
            )
        )

        self.assertEqual(account["plan_type"], "plus")
        self.assertEqual(account["plan_type_source"], "account_test")
        self.assertEqual(
            account_probe._resolved_probe_plan_type(
                account["plan_type"],
                "k12",
                current_source=account["plan_type_source"],
            ),
            ("k12", "cached"),
        )
        self.assertEqual(
            account_probe._resolved_probe_plan_type(
                account["plan_type"],
                "free",
                current_source=account["plan_type_source"],
                previous_source="remote",
            ),
            ("plus", "account_test"),
        )

    def test_empty_remote_plan_type_keeps_previous_value(self) -> None:
        self.assertEqual(account_probe._resolved_probe_plan_type("", "plus"), ("plus", "cached"))

    def test_empty_remote_plan_type_without_history_defaults_to_k12(self) -> None:
        self.assertEqual(account_probe._resolved_probe_plan_type("", None), ("k12", "fallback_k12"))

    def test_remote_plan_type_wins_over_history(self) -> None:
        self.assertEqual(account_probe._resolved_probe_plan_type("pro", "plus"), ("pro", "remote"))


class SparkShadowAccountTests(unittest.TestCase):
    def test_spark_shadow_is_excluded_from_email_identity(self) -> None:
        main = {
            "remote_account_id": 12,
            "normalized_email": "pro@example.com",
            "name": "pro@example.com",
            "status": "error",
            "error_message": "status 401 refresh_token_invalidated",
            "group_ids": [2],
            "usage_snapshot": {},
        }
        shadow = {
            "remote_account_id": 99,
            "normalized_email": "pro@example.com",
            "name": "pro@example.com (Spark)",
            "status": "active",
            "error_message": None,
            "group_ids": [2],
            "usage_snapshot": {},
        }

        collapsed = account_probe._collapse_probe_accounts_by_email([shadow, main])

        self.assertEqual(len(collapsed), 1)
        self.assertEqual(collapsed[0]["remote_account_id"], 12)
        self.assertEqual(collapsed[0]["remote_account_ids"], [12])
        self.assertEqual(collapsed[0]["duplicate_remote_count"], 1)
        self.assertTrue(account_probe._is_401(collapsed[0]))

    def test_only_name_suffix_marks_a_spark_shadow(self) -> None:
        self.assertTrue(account_probe._is_spark_shadow_account({"name": "Account (Spark)"}))
        self.assertTrue(account_probe._is_spark_shadow_account({"name": "account (spark)  "}))
        self.assertFalse(account_probe._is_spark_shadow_account({"name": "Spark Team"}))
        self.assertFalse(account_probe._is_spark_shadow_account({"name": "Account"}))


class Confirmed401StateTests(unittest.TestCase):
    def test_single_normal_probe_does_not_recover_a_401(self) -> None:
        state = account_probe._confirmed_401_state(
            account={"status": "active", "error_message": None},
            previous_is_401=True,
            previous_recovery_streak=0,
        )

        self.assertTrue(state["is_401"])
        self.assertEqual(state["recovery_streak"], 1)

    def test_three_normal_probes_confirm_recovery(self) -> None:
        state = account_probe._confirmed_401_state(
            account={"status": "active", "error_message": None},
            previous_is_401=True,
            previous_recovery_streak=2,
        )

        self.assertFalse(state["is_401"])
        self.assertEqual(state["recovery_streak"], 3)

    def test_renewed_401_clears_recovery_streak(self) -> None:
        state = account_probe._confirmed_401_state(
            account={"status": "error", "error_message": "status 401 refresh_token_invalidated"},
            previous_is_401=True,
            previous_recovery_streak=2,
        )

        self.assertTrue(state["is_401"])
        self.assertEqual(state["recovery_streak"], 0)


class OfficialUsageRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.detected_at = datetime(2026, 7, 12, 4, 0, tzinfo=UTC)

    def test_detects_zero_usage_before_expected_reset(self) -> None:
        result = account_probe._official_usage_refresh_state(
            previous_snapshot={
                "codex_7d_used_percent": 63,
                "codex_7d_reset_at": (self.detected_at + timedelta(days=4)).isoformat(),
            },
            current_snapshot={
                "codex_7d_used_percent": 0,
                "codex_7d_reset_at": (self.detected_at + timedelta(days=7)).isoformat(),
            },
            detected_at=self.detected_at,
        )

        self.assertTrue(result["eligible"])
        self.assertTrue(result["detected"])
        self.assertEqual(result["previous_used_percent"], 63.0)

    def test_does_not_detect_reset_after_expected_time(self) -> None:
        result = account_probe._official_usage_refresh_state(
            previous_snapshot={
                "codex_7d_used_percent": 63,
                "codex_7d_reset_at": (self.detected_at - timedelta(seconds=1)).isoformat(),
            },
            current_snapshot={"codex_7d_used_percent": 0},
            detected_at=self.detected_at,
        )

        self.assertFalse(result["eligible"])
        self.assertFalse(result["detected"])

    def test_does_not_repeat_when_usage_was_already_zero(self) -> None:
        result = account_probe._official_usage_refresh_state(
            previous_snapshot={
                "codex_7d_used_percent": 0,
                "codex_7d_reset_at": (self.detected_at + timedelta(days=4)).isoformat(),
            },
            current_snapshot={"codex_7d_used_percent": 0},
            detected_at=self.detected_at,
        )

        self.assertFalse(result["eligible"])
        self.assertFalse(result["detected"])

    def test_bug_team_zero_usage_can_be_a_refresh_candidate(self) -> None:
        result = account_probe._official_usage_refresh_state(
            previous_snapshot={
                "codex_7d_used_percent": 54,
                "codex_7d_reset_at": (self.detected_at + timedelta(days=30)).isoformat(),
            },
            current_snapshot={
                "codex_5h_used_percent": 0,
                "codex_5h_reset_after_seconds": 0,
                "codex_5h_window_minutes": 0,
                "codex_7d_used_percent": 0,
                "codex_7d_window_minutes": 43800,
            },
            detected_at=self.detected_at,
        )

        self.assertTrue(result["eligible"])
        self.assertTrue(result["detected"])

    def test_single_k12_candidate_does_not_confirm_global_refresh(self) -> None:
        refresh = account_probe._official_usage_refresh_state(
            previous_snapshot={
                "codex_7d_used_percent": 67,
                "codex_7d_reset_at": (self.detected_at + timedelta(days=5, hours=22)).isoformat(),
            },
            current_snapshot={"codex_7d_used_percent": 0},
            detected_at=self.detected_at,
        )
        accounts = [{"plan_type": "k12", "official_refresh": refresh}]

        result = account_probe._official_refresh_consensus(accounts, eligible_account_counts={"k12": 12})

        self.assertTrue(refresh["detected"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(result["candidate_count"], 1)

    def test_candidates_from_different_types_do_not_form_consensus(self) -> None:
        accounts = [
            {"plan_type": "plus", "official_refresh": {"detected": True}},
            {"plan_type": "pro", "official_refresh": {"detected": True}},
        ]

        result = account_probe._official_refresh_consensus(accounts, eligible_account_counts={"plus": 1, "pro": 1})

        self.assertFalse(result["confirmed"])
        self.assertEqual(result["candidate_count"], 2)

    def test_same_type_candidates_confirm_refresh(self) -> None:
        accounts = [
            {"remote_account_id": 1, "plan_type": "pro", "official_refresh": {"detected": True}},
            {"remote_account_id": 2, "plan_type": "pro", "official_refresh": {"detected": True}},
        ]

        result = account_probe._official_refresh_consensus(accounts, eligible_account_counts={"pro": 2})

        self.assertTrue(result["confirmed"])
        self.assertEqual(result["confirmed_account_types"], ["pro"])
        self.assertEqual([item["remote_account_id"] for item in result["confirmed_accounts"]], [1, 2])
        self.assertAlmostEqual(result["type_consensus"]["pro"]["candidate_ratio"], 1)

    def test_small_pro_refresh_is_not_diluted_by_plus_accounts(self) -> None:
        accounts = [
            {"remote_account_id": 1, "plan_type": "pro", "official_refresh": {"detected": True}},
            {"remote_account_id": 2, "plan_type": "pro", "official_refresh": {"detected": True}},
        ]

        result = account_probe._official_refresh_consensus(accounts, eligible_account_counts={"pro": 2, "plus": 100})

        self.assertTrue(result["confirmed"])
        self.assertEqual(result["confirmed_account_types"], ["pro"])

    def test_usage_rollover_event_details_keep_only_changed_counter_values(self) -> None:
        result = account_probe._usage_rollover_state(
            previous_snapshot={
                "codex_7d_request_count": 100,
                "codex_7d_token_count": 1000,
                "codex_5h_used_percent": 75,
            },
            current_snapshot={
                "codex_7d_request_count": 2,
                "codex_7d_token_count": 20,
                "codex_5h_used_percent": 5,
            },
            previous_totals={},
        )

        details = result["rollover_details"]
        self.assertNotIn("previous_usage_snapshot", details)
        self.assertNotIn("current_usage_snapshot", details)
        self.assertEqual(details["previous_values"]["codex_7d_request_count"], 100)
        self.assertEqual(details["current_values"]["codex_7d_request_count"], 2)


class AccountProbeSubscriptionTests(unittest.TestCase):
    def test_subscription_timestamps_are_normalized_before_comparison(self) -> None:
        normalized = account_probe._normalize_probe_account(
            {
                "id": 953,
                "email": "person@example.com",
                "credentials": {"expires_at": "2026-08-01T00:00:00Z"},
                "extra": {
                    "chatgpt_subscription_active_until": "2026-08-01T08:00:00+08:00",
                    "chatgpt_subscription_last_checked": "2026-07-18T06:30:00Z",
                },
            }
        )

        self.assertEqual(
            normalized["subscription_snapshot"],
            {
                "chatgpt_subscription_active_until": datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
                "chatgpt_subscription_last_checked": datetime(2026, 7, 18, 6, 30, tzinfo=UTC),
            },
        )

    def test_first_observation_does_not_mirror_usage_or_credential_expiry_to_history(self) -> None:
        account = {
            "normalized_email": "person@example.com",
            "remote_account_id": 953,
            "usage_snapshot": {"codex_5h_used_percent": 42},
            "subscription_snapshot": {
                "credential_expires_at": datetime(2026, 8, 1, tzinfo=UTC),
                "chatgpt_subscription_last_checked": datetime(2026, 7, 19, tzinfo=UTC),
                "subscription_status": "active",
            },
        }

        change, baseline = account_probe._prepare_history_change(
            site_id="api-5001",
            account=account,
            identity=None,
            setting={"detailed_enabled": True, "record_usage_samples": True},
        )

        self.assertIsNone(change)
        self.assertEqual(baseline, {"usage": {}, "subscription": {"subscription_status": "active"}})

    def test_usage_only_change_does_not_create_history_batch(self) -> None:
        account = {
            "normalized_email": "person@example.com",
            "remote_account_id": 953,
            "usage_snapshot": {"codex_5h_used_percent": 0},
            "subscription_snapshot": {},
        }

        change, baseline = account_probe._prepare_history_change(
            site_id="api-5001",
            account=account,
            identity={
                "history_baseline_snapshot": {
                    "usage": {"codex_5h_used_percent": 80},
                    "subscription": {},
                }
            },
            setting={"detailed_enabled": True, "record_usage_samples": True},
        )

        self.assertIsNone(change)
        self.assertEqual(baseline, {"usage": {}, "subscription": {}})

    def test_disabled_history_tracks_baseline_without_writing_change(self) -> None:
        account = {
            "normalized_email": "person@example.com",
            "remote_account_id": 953,
            "usage_snapshot": {"codex_5h_used_percent": 50},
            "subscription_snapshot": {},
        }

        change, baseline = account_probe._prepare_history_change(
            site_id="api-5001",
            account=account,
            identity={
                "history_baseline_snapshot": {
                    "usage": {"codex_5h_used_percent": 40},
                    "subscription": {},
                }
            },
            setting={"detailed_enabled": True, "record_usage_samples": False},
        )

        self.assertIsNone(change)
        self.assertEqual(baseline, {"usage": {}, "subscription": {}})


if __name__ == "__main__":
    unittest.main()
