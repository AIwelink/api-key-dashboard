from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, AsyncMock, patch

from app.modules.sub2api import account_probe


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


class AccountProbePlanTypeTests(unittest.TestCase):
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
                "credential_expires_at": datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
            },
        )

    def test_first_observation_initializes_history_without_change_event(self) -> None:
        account = {
            "normalized_email": "person@example.com",
            "remote_account_id": 953,
            "usage_snapshot": {"codex_5h_used_percent": 42},
            "subscription_snapshot": {},
        }

        change, baseline = account_probe._prepare_history_change(
            site_id="api-5001",
            account=account,
            identity=None,
            setting={"detailed_enabled": True, "record_usage_samples": True},
        )

        self.assertIsNone(change)
        self.assertEqual(baseline, {"usage": {"codex_5h_used_percent": 42}, "subscription": {}})

    def test_existing_baseline_produces_exact_usage_change(self) -> None:
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

        assert change is not None
        self.assertEqual(change["changes"], {"usage.codex_5h_used_percent": 0})
        self.assertIsNone(baseline)

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
        self.assertEqual(baseline, {"usage": {"codex_5h_used_percent": 50}, "subscription": {}})


if __name__ == "__main__":
    unittest.main()
