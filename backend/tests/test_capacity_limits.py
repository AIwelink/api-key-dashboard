from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.api_pools import capacity_limits
from app.modules.api_pools.capacity_limits import normalize_capacity_limits
from app.modules.sub2api import auto_refill, cache


def bug_team_account(*, used_percent: float = 54, reset_after_seconds: float = 2_607_895) -> dict:
    return {
        "id": 1779,
        "status": "active",
        "schedulable": True,
        "plan_type": "team",
        "credentials": {"plan_type": "team"},
        "extra": {
            "codex_5h_reset_after_seconds": 0,
            "codex_5h_used_percent": 0,
            "codex_5h_window_minutes": 0,
            "codex_7d_reset_after_seconds": reset_after_seconds,
            "codex_7d_used_percent": used_percent,
            "codex_7d_window_minutes": 43_800,
        },
    }


class BugTeamCapacityTests(unittest.TestCase):
    def test_group_cache_stores_capacity_summary_only_at_document_root(self) -> None:
        group = {"id": 3, "name": "plus", "capacity_summary": {"stale": True}}
        summary = {"health_status": "healthy"}

        stored = cache._group_cache_snapshot(group)
        response = cache._group_with_capacity_summary(stored, summary)

        self.assertNotIn("capacity_summary", stored)
        self.assertEqual(response["capacity_summary"], summary)

    def test_refill_options_are_bound_to_primary_pool_type(self) -> None:
        limits = normalize_capacity_limits(None)

        self.assertEqual(list(cache._refill_account_options("plus", limits)), ["plus", "k12"])
        self.assertEqual(list(cache._refill_account_options("pro", limits)), ["pro"])
        self.assertEqual(list(cache._refill_account_options("team", limits)), ["team"])
        self.assertEqual(list(cache._refill_account_options("k12", limits)), ["k12"])

    def test_missing_remote_plan_type_defaults_to_k12(self) -> None:
        account = cache._normalize_account_snapshot(
            {
                "id": 1,
                "plan_type": "",
                "credentials": {"plan_type": ""},
                "status": "active",
            }
        )

        self.assertEqual(account["plan_type"], "k12")
        self.assertEqual(account["codex_plan_type_source"], "fallback_k12")
        self.assertEqual(cache._capacity_account_type(account), "k12")

    def test_missing_remote_plan_type_uses_standard_plus_name_prefix(self) -> None:
        account = cache._normalize_account_snapshot(
            {
                "id": 3585,
                "name": "plus +56959278873---taftaubertine14500@outlook.com",
                "credentials": {
                    "chatgpt_account_id": "account-3585",
                    "email": "taftaubertine14500@outlook.com",
                },
                "extra": {
                    "codex_5h_window_minutes": 0,
                    "codex_7d_window_minutes": 10_080,
                },
                "status": "active",
            }
        )

        self.assertEqual(account["plan_type"], "plus")
        self.assertEqual(account["codex_plan_type_source"], "name_prefix")
        self.assertEqual(cache._capacity_account_type(account), "plus")

    def test_missing_remote_plan_type_does_not_treat_email_local_part_as_plus_prefix(self) -> None:
        account = cache._normalize_account_snapshot(
            {
                "id": 3586,
                "name": "plususer@example.com",
                "credentials": {"email": "plususer@example.com"},
                "status": "active",
            }
        )

        self.assertEqual(account["plan_type"], "k12")
        self.assertEqual(account["codex_plan_type_source"], "fallback_k12")

    def test_cached_effective_plan_type_wins_over_standard_plus_name(self) -> None:
        account = cache._normalize_account_snapshot({"id": 3587, "name": "plus person@example.com"})

        cache._copy_cached_plan_type(account, {"id": 3587, "plan_type": "pro"})

        self.assertEqual(account["plan_type"], "pro")
        self.assertEqual(account["codex_plan_type_source"], "cached")

    def test_cached_k12_fallback_does_not_replace_standard_plus_name(self) -> None:
        account = cache._normalize_account_snapshot({"id": 3588, "name": "plus person@example.com"})

        cache._copy_cached_plan_type(
            account,
            {
                "id": 3588,
                "plan_type": "k12",
                "codex_plan_type_source": "fallback_k12",
            },
        )

        self.assertEqual(account["plan_type"], "plus")
        self.assertEqual(account["codex_plan_type_source"], "name_prefix")

    def test_sub_bundle_free_signature_requires_persisted_verification(self) -> None:
        account = cache._normalize_account_snapshot(
            {
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
        )

        self.assertEqual(account["plan_type"], "free")
        self.assertNotEqual(account.get("codex_plan_type_source"), "account_test")
        self.assertEqual(cache._capacity_account_type(account), "free")

        verified = cache._account_with_verified_plan_type(
            {
                "id": 4072,
                "credentials": {"plan_type": "free"},
                "extra": {
                    "source": "sub_bundle_input",
                    "codex_5h_window_minutes": 0,
                    "codex_7d_window_minutes": 10_080,
                },
                "groups": [{"id": 3, "name": "plus"}],
            },
            {"verified_plan_type": "plus"},
        )
        account = cache._normalize_account_snapshot(verified)
        self.assertEqual(account["plan_type"], "plus")
        self.assertEqual(account["codex_plan_type_source"], "account_test")
        self.assertEqual(cache._capacity_account_type(account), "plus")

    def test_sub_bundle_free_requires_plus_group_and_paid_window_signature(self) -> None:
        base = {
            "id": 4073,
            "name": "person@example.com",
            "credentials": {"email": "person@example.com", "plan_type": "free"},
            "extra": {
                "source": "sub_bundle_input",
                "codex_5h_window_minutes": 0,
                "codex_7d_window_minutes": 10_080,
            },
            "groups": [{"id": 2, "name": "free 账号池 01"}],
        }

        account = cache._normalize_account_snapshot(base)

        self.assertEqual(account["plan_type"], "free")
        self.assertNotIn("codex_plan_type_source", account)

    def test_cached_effective_type_wins_over_plus_bundle_signature(self) -> None:
        account = cache._normalize_account_snapshot(
            cache._account_with_verified_plan_type(
                {
                "id": 4074,
                "credentials": {"plan_type": "free"},
                "extra": {
                    "source": "sub_bundle_input",
                    "codex_5h_window_minutes": 0,
                    "codex_7d_window_minutes": 10_080,
                },
                "groups": [{"id": 3, "name": "plus 账号池 01"}],
                },
                {"verified_plan_type": "plus"},
            )
        )

        cache._copy_cached_plan_type(account, {"id": 4074, "plan_type": "k12"})

        self.assertEqual(account["plan_type"], "k12")
        self.assertEqual(account["codex_plan_type_source"], "cached")

    def test_cached_misreported_free_does_not_replace_plus_bundle_signature(self) -> None:
        account = cache._normalize_account_snapshot(
            cache._account_with_verified_plan_type(
                {
                "id": 4075,
                "credentials": {"plan_type": "free"},
                "extra": {
                    "source": "sub_bundle_input",
                    "codex_5h_window_minutes": 0,
                    "codex_7d_window_minutes": 10_080,
                },
                "groups": [{"id": 3, "name": "plus 账号池 01"}],
                },
                {"verified_plan_type": "plus"},
            )
        )

        cache._copy_cached_plan_type(account, {"id": 4075, "plan_type": "free"})

        self.assertEqual(account["plan_type"], "plus")
        self.assertEqual(account["codex_plan_type_source"], "account_test")

    def test_cached_plan_type_replaces_temporary_k12_fallback(self) -> None:
        account = cache._normalize_account_snapshot({"id": 1, "plan_type": "", "status": "error"})

        cache._copy_cached_plan_type(account, {"id": 1, "plan_type": "plus"})

        self.assertEqual(account["plan_type"], "plus")
        self.assertEqual(account["codex_plan_type_source"], "cached")
        self.assertEqual(cache._capacity_account_type(account), "plus")

    def test_remote_plan_type_is_not_replaced_by_cached_value(self) -> None:
        account = cache._normalize_account_snapshot({"id": 1, "plan_type": "pro", "status": "active"})

        cache._copy_cached_plan_type(account, {"id": 1, "plan_type": "plus"})

        self.assertEqual(account["plan_type"], "pro")

    def test_old_saved_limits_receive_bug_team_defaults(self) -> None:
        limits = normalize_capacity_limits({"team": {"five_hour_usd": 15, "seven_day_usd": 75}})

        self.assertEqual(limits["bug_team"], {"five_hour_usd": 230.0, "seven_day_usd": 230.0})

    def test_bug_team_is_detected_before_regular_team(self) -> None:
        self.assertEqual(cache._capacity_account_type(bug_team_account()), "bug_team")

    def test_explicit_bug_team_account_type_wins_over_remote_team_plan(self) -> None:
        account = {
            "id": 1781,
            "plan_type": "team",
            "credentials": {"plan_type": "team"},
            "extra": {"account_type": "bug_team"},
        }

        self.assertTrue(cache.is_bug_team_account(account))
        self.assertEqual(cache._capacity_account_type(account), "bug_team")

    def test_team_with_long_seven_day_window_and_missing_five_hour_window_is_bug_team(self) -> None:
        account = {
            "id": 1782,
            "plan_type": "team",
            "credentials": {"plan_type": "team"},
            "extra": {
                "codex_7d_window_minutes": 43_800,
                "codex_7d_used_percent": 35,
            },
        }

        self.assertTrue(cache.is_bug_team_account(account))
        self.assertEqual(cache._capacity_account_type(account), "bug_team")

    def test_team_with_false_privacy_mode_is_bug_team_before_usage_windows_exist(self) -> None:
        for privacy_mode in (False, "false"):
            with self.subTest(privacy_mode=privacy_mode):
                account = {
                    "id": 1785,
                    "plan_type": "team",
                    "privacy_mode": privacy_mode,
                    "credentials": {"plan_type": "team"},
                }

                self.assertTrue(cache.is_bug_team_account(account))
                self.assertEqual(cache._capacity_account_type(account), "bug_team")

    def test_regular_team_with_true_privacy_mode_keeps_team_type(self) -> None:
        account = {
            "id": 1786,
            "plan_type": "team",
            "privacy_mode": True,
            "credentials": {"plan_type": "team"},
        }

        self.assertFalse(cache.is_bug_team_account(account))
        self.assertEqual(cache._capacity_account_type(account), "team")

    def test_regular_team_without_five_hour_data_keeps_team_type(self) -> None:
        account = {
            "id": 1783,
            "plan_type": "team",
            "credentials": {"plan_type": "team"},
            "extra": {"codex_7d_window_minutes": 10_080},
        }

        self.assertFalse(cache.is_bug_team_account(account))
        self.assertEqual(cache._capacity_account_type(account), "team")

    def test_team_with_valid_five_hour_window_is_not_bug_team_even_if_seven_day_window_is_long(self) -> None:
        account = {
            "id": 1784,
            "plan_type": "team",
            "credentials": {"plan_type": "team"},
            "extra": {
                "codex_5h_window_minutes": 300,
                "codex_7d_window_minutes": 43_800,
            },
        }

        self.assertFalse(cache.is_bug_team_account(account))
        self.assertEqual(cache._capacity_account_type(account), "team")

    def test_bug_team_label_variants_normalize_to_bug_team(self) -> None:
        for label in ("bug_team", "Bug Team", "bug-team"):
            with self.subTest(label=label):
                self.assertEqual(cache._normalize_capacity_account_type(label), "bug_team")

    def test_special_plus_and_team_keep_separate_quota_detection_dimensions(self) -> None:
        cases = (
            (
                {"plan_type": "plus", "extra": {"account_type": "特殊plus"}},
                "special_plus",
            ),
            (
                {
                    "plan_type": "team",
                    "privacy_mode": False,
                    "name": "special-team-directed",
                    "extra": {"codex_7d_window_minutes": 43_800},
                },
                "special_team",
            ),
            (
                {"plan_type": "plus", "quota_dimension": "customer-a"},
                "special_plus",
            ),
            (
                {"plan_type": "team", "quota_dimension": "global"},
                "team",
            ),
        )

        for account, expected in cases:
            with self.subTest(expected=expected, account=account):
                self.assertEqual(cache._quota_detection_account_type(account), expected)

    def test_plus_without_an_independent_five_hour_window_is_not_bug_team(self) -> None:
        account = {
            "id": 1780,
            "plan_type": "plus",
            "credentials": {"plan_type": "plus"},
            "extra": {
                "codex_5h_window_minutes": 0,
                "codex_7d_window_minutes": 43_800,
                "codex_7d_used_percent": 25,
                "codex_7d_reset_after_seconds": 2_607_895,
            },
        }

        self.assertFalse(cache.is_bug_team_account(account))
        self.assertEqual(cache._capacity_account_type(account), "plus")

        usage = cache._dynamic_five_hour_usage(
            account,
            five_hour_limit_usd=120,
            seven_day_limit_usd=120,
            five_hour_available=True,
        )
        self.assertAlmostEqual(usage["actual_used_usd"], 30)
        self.assertAlmostEqual(usage["actual_remaining_usd"], 90)

    def test_no_five_hour_window_prioritizes_seven_day_rate_limit_when_usage_is_stale(self) -> None:
        current_time = datetime(2026, 7, 19, 15, 30, tzinfo=timezone.utc)
        account = {
            "id": 2020,
            "status": "active",
            "schedulable": True,
            "plan_type": "plus",
            "rate_limit_reset_at": current_time + timedelta(days=3),
            "extra": {
                "codex_5h_used_percent": 0,
                "codex_5h_reset_after_seconds": 0,
                "codex_5h_window_minutes": 0,
                "codex_7d_used_percent": 0,
                "codex_7d_reset_after_seconds": 3 * 24 * 60 * 60,
                "codex_7d_window_minutes": 10_080,
            },
        }

        with patch.object(cache, "now_utc", return_value=current_time):
            self.assertTrue(cache._is_7d_exhausted(account))
            self.assertFalse(cache._is_five_hour_rate_limited(account))
            status = cache._pool_account_status_summary([account])
            capacity_accounts = [item for item in [account] if cache._is_capacity_account(item)]
            capacity = cache._capacity_by_account_type(
                capacity_accounts,
                [item for item in capacity_accounts if not cache._is_7d_exhausted(item)],
                normalize_capacity_limits({"plus": {"five_hour_usd": 110, "seven_day_usd": 110}}),
            )

        self.assertEqual(status["pool_five_hour_rate_limited_accounts"], 0)
        self.assertEqual(status["pool_seven_day_rate_limited_accounts"], 1)
        self.assertEqual(status["pool_active_normal_accounts"], 0)
        self.assertEqual(capacity["plus"]["available_accounts"], 1)
        self.assertEqual(capacity["plus"]["available_5h_accounts"], 0)
        self.assertEqual(capacity["plus"]["five_hour_dynamic_capacity_usd"], 0)

    def test_bug_team_below_seven_day_limit_participates_in_capacity_summary(self) -> None:
        limits = normalize_capacity_limits(None)
        account = bug_team_account(used_percent=54)

        summary = cache._capacity_by_account_type(
            [account],
            [account],
            limits,
        )

        self.assertTrue(cache._is_capacity_account(account))
        self.assertEqual(cache._primary_capacity_type(summary), "bug_team")
        self.assertEqual(summary["bug_team"]["available_accounts"], 1)
        self.assertEqual(summary["total"]["available_accounts"], 1)
        self.assertAlmostEqual(summary["total"]["five_hour_capacity_usd"], 230)
        self.assertAlmostEqual(summary["total"]["seven_day_capacity_usd"], 230)

    def test_bug_team_temporary_403_is_not_treated_as_seven_day_exhaustion(self) -> None:
        current_time = datetime(2026, 7, 20, 20, 20, tzinfo=timezone.utc)
        account = bug_team_account(used_percent=1)
        account["temp_unschedulable_until"] = current_time + timedelta(minutes=10)
        account["temp_unschedulable_reason"] = "OpenAI 403 temporary cooldown"

        with patch.object(cache, "now_utc", return_value=current_time):
            status = cache._pool_account_status_summary([account])

            self.assertFalse(cache._is_bug_team_seven_day_exhausted(account))
            self.assertFalse(cache._is_excluded_bug_team_account(account))
            self.assertTrue(cache._is_dynamic_capacity_account(account))

        self.assertEqual(status["pool_normal_accounts"], 1)
        self.assertEqual(status["pool_active_normal_accounts"], 1)
        self.assertEqual(status["pool_seven_day_rate_limited_accounts"], 0)
        self.assertEqual(status["pool_excluded_bug_team_accounts"], 0)

    def test_exhausted_bug_team_beyond_one_day_from_reset_is_excluded(self) -> None:
        account = bug_team_account(used_percent=100, reset_after_seconds=24 * 60 * 60 + 1)
        status = cache._pool_account_status_summary([account])

        self.assertTrue(cache._is_excluded_bug_team_account(account))
        self.assertFalse(cache._is_capacity_account(account))
        self.assertEqual(status["pool_normal_accounts"], 0)
        self.assertEqual(status["pool_excluded_bug_team_accounts"], 1)

    def test_exhausted_bug_team_within_one_day_participates_in_dynamic_capacity(self) -> None:
        account = bug_team_account(used_percent=100, reset_after_seconds=24 * 60 * 60)
        limits = normalize_capacity_limits(None)
        status = cache._pool_account_status_summary([account])
        capacity_accounts = [item for item in [account] if cache._is_capacity_account(item)]
        dynamic_accounts = [item for item in capacity_accounts if cache._is_dynamic_capacity_account(item)]
        summary = cache._capacity_by_account_type(capacity_accounts, dynamic_accounts, limits)

        self.assertFalse(cache._is_excluded_bug_team_account(account))
        self.assertTrue(cache._is_capacity_account(account))
        self.assertEqual(status["pool_normal_accounts"], 1)
        self.assertEqual(status["pool_seven_day_rate_limited_accounts"], 1)
        self.assertEqual(status["pool_excluded_bug_team_accounts"], 0)
        self.assertEqual(summary["bug_team"]["available_accounts"], 1)
        self.assertEqual(summary["bug_team"]["available_5h_accounts"], 1)
        self.assertEqual(summary["bug_team"]["five_hour_dynamic_capacity_usd"], 230)
        self.assertGreater(summary["bug_team"]["five_hour_dynamic_remaining_usd"], 0)
        self.assertGreater(summary["bug_team"]["seven_day_dynamic_remaining_usd"], 0)

    def test_schedulable_false_rate_limited_account_is_excluded_from_total_capacity(self) -> None:
        account = {
            "id": 9,
            "status": "rate_limited",
            "schedulable": False,
            "plan_type": "plus",
            "codex_5h_used_percent": 100,
            "codex_5h_reset_after_seconds": 3600,
            "codex_7d_used_percent": 40,
        }

        self.assertFalse(cache._is_capacity_account(account))

        capacity_accounts = [item for item in [account] if cache._is_capacity_account(item)]
        summary = cache._capacity_by_account_type(
            capacity_accounts,
            capacity_accounts,
            normalize_capacity_limits({"plus": {"five_hour_usd": 110, "seven_day_usd": 110}}),
        )
        self.assertEqual(summary["plus"]["available_accounts"], 0)
        self.assertEqual(summary["plus"]["five_hour_capacity_usd"], 0)
        self.assertEqual(summary["plus"]["seven_day_capacity_usd"], 0)

    def test_regular_team_keeps_its_five_hour_window(self) -> None:
        account = {
            "id": 1,
            "plan_type": "team",
            "extra": {
                "codex_5h_used_percent": 20,
                "codex_5h_reset_after_seconds": 18_000,
                "codex_5h_window_minutes": 300,
                "codex_7d_used_percent": 80,
                "codex_7d_reset_after_seconds": 604_800,
                "codex_7d_window_minutes": 10_080,
            },
        }

        usage = cache._dynamic_five_hour_usage(
            account,
            five_hour_limit_usd=15,
            seven_day_limit_usd=75,
            five_hour_available=True,
        )

        self.assertEqual(cache._capacity_account_type(account), "team")
        self.assertAlmostEqual(usage["actual_used_usd"], 3)
        self.assertAlmostEqual(usage["actual_remaining_usd"], 12)

    def test_equal_limits_use_seven_day_usage_for_five_hour_capacity(self) -> None:
        accounts = [
            {
                "plan_type": account_type,
                "extra": {
                    "codex_5h_used_percent": 90,
                    "codex_5h_reset_after_seconds": 18_000,
                    "codex_5h_window_minutes": 300,
                    "codex_7d_used_percent": 25,
                    "codex_7d_reset_after_seconds": 604_800,
                    "codex_7d_window_minutes": 10_080,
                },
            }
            for account_type in ("free", "plus", "team", "k12", "pro")
        ] + [
            bug_team_account(used_percent=25),
        ]

        for account in accounts:
            with self.subTest(account_type=cache._capacity_account_type(account)):
                usage = cache._dynamic_five_hour_usage(
                    account,
                    five_hour_limit_usd=100,
                    seven_day_limit_usd=100,
                    five_hour_available=True,
                )
                self.assertAlmostEqual(usage["actual_used_usd"], 25)
                self.assertAlmostEqual(usage["actual_remaining_usd"], 75)

    def test_unequal_limits_keep_five_hour_usage(self) -> None:
        base_extra = {
            "codex_5h_used_percent": 90,
            "codex_5h_reset_after_seconds": 18_000,
            "codex_5h_window_minutes": 300,
            "codex_7d_used_percent": 25,
            "codex_7d_reset_after_seconds": 604_800,
            "codex_7d_window_minutes": 10_080,
        }
        cases = [
            ({"plan_type": account_type, "extra": base_extra}, 100, 500)
            for account_type in ("free", "plus", "team", "k12", "pro")
        ]

        for account, five_hour_limit, seven_day_limit in cases:
            with self.subTest(account_type=account["plan_type"], seven_day_limit=seven_day_limit):
                usage = cache._dynamic_five_hour_usage(
                    account,
                    five_hour_limit_usd=five_hour_limit,
                    seven_day_limit_usd=seven_day_limit,
                    five_hour_available=True,
                )
                self.assertAlmostEqual(usage["actual_used_usd"], 90)
                self.assertAlmostEqual(usage["actual_remaining_usd"], 10)

    def test_five_hour_dynamic_recovery_stops_after_one_hour(self) -> None:
        def usage(reset_after_seconds: int) -> dict[str, float]:
            return cache._dynamic_five_hour_usage(
                {
                    "plan_type": "team",
                    "extra": {
                        "codex_5h_used_percent": 100,
                        "codex_5h_reset_after_seconds": reset_after_seconds,
                        "codex_5h_window_minutes": 300,
                        "codex_7d_used_percent": 20,
                        "codex_7d_reset_after_seconds": 4 * 24 * 60 * 60,
                        "codex_7d_window_minutes": 10_080,
                    },
                },
                five_hour_limit_usd=20,
                seven_day_limit_usd=100,
                five_hour_available=True,
            )

        at_boundary = usage(60 * 60)
        beyond_boundary = usage(60 * 60 + 1)

        self.assertGreater(at_boundary["remaining_usd"], 0)
        self.assertEqual(beyond_boundary["remaining_usd"], beyond_boundary["actual_remaining_usd"])
        self.assertEqual(beyond_boundary["remaining_usd"], 0)


class SiteCapacityLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_site_specific_limits_are_loaded_before_legacy_global_limits(self) -> None:
        app_settings = SimpleNamespace(
            find_one=AsyncMock(
                return_value={
                    "_id": "capacity_account_limits:api-5002",
                    "site_id": "api-5002",
                    "limits": {"plus": {"five_hour_usd": 120, "seven_day_usd": 120}},
                }
            )
        )
        result = await capacity_limits.get_capacity_account_limits(SimpleNamespace(app_settings=app_settings), "api-5002")

        self.assertEqual(result["site_id"], "api-5002")
        self.assertFalse(result["inherited_from_global"])
        self.assertEqual(result["limits"]["plus"], {"five_hour_usd": 120.0, "seven_day_usd": 120.0})
        app_settings.find_one.assert_awaited_once_with({"_id": "capacity_account_limits:api-5002"})

    async def test_missing_site_limits_inherit_legacy_global_limits(self) -> None:
        app_settings = SimpleNamespace(
            find_one=AsyncMock(
                side_effect=[
                    None,
                    {"_id": "capacity_account_limits", "limits": {"plus": {"five_hour_usd": 88, "seven_day_usd": 440}}},
                ]
            )
        )
        result = await capacity_limits.get_capacity_account_limits(SimpleNamespace(app_settings=app_settings), "api-5003")

        self.assertTrue(result["inherited_from_global"])
        self.assertEqual(result["limits"]["plus"], {"five_hour_usd": 88.0, "seven_day_usd": 440.0})

    async def test_updating_site_limits_invalidates_cached_group_capacity(self) -> None:
        app_settings = SimpleNamespace(
            update_one=AsyncMock(),
            find_one=AsyncMock(
                return_value={
                    "_id": "capacity_account_limits:api-5001",
                    "site_id": "api-5001",
                    "limits": {"plus": {"five_hour_usd": 120, "seven_day_usd": 600}},
                }
            ),
        )
        groups_cache = SimpleNamespace(update_many=AsyncMock())
        db = SimpleNamespace(app_settings=app_settings, sub2api_groups_cache=groups_cache)

        await capacity_limits.update_capacity_account_limits(
            db,
            normalize_capacity_limits({"plus": {"five_hour_usd": 120, "seven_day_usd": 600}}),
            {"_id": "owner-1", "name": "Owner"},
            "api-5001",
        )

        groups_cache.update_many.assert_awaited_once()
        query, update = groups_cache.update_many.await_args.args
        self.assertEqual(query, {"site_id": "api-5001"})
        self.assertIn("capacity_summary", update["$unset"])
        self.assertIn("group.capacity_summary", update["$unset"])

    def test_auto_refill_uses_capacity_limits_from_the_site_summary(self) -> None:
        summary = {
            "account_type": "plus",
            "capacity_limits": {"plus": {"five_hour_usd": 120, "seven_day_usd": 120}},
            "active_five_hour_capacity_usd": 0,
            "active_seven_day_capacity_usd": 0,
            "recent_day_five_hour_peak_cost": 120,
            "recent_24h_cost": 0,
        }

        self.assertEqual(auto_refill._needed_refill_count(summary), 2)


class FiveHourCapacityPercentageTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_capacity_sums_each_remote_account_type_with_site_limits(self) -> None:
        limits = normalize_capacity_limits(
            {
                "plus": {"five_hour_usd": 110, "seven_day_usd": 110},
                "k12": {"five_hour_usd": 20, "seven_day_usd": 100},
            }
        )
        accounts = [
            {
                "id": 1,
                "status": "active",
                "schedulable": True,
                "plan_type": "plus",
                "extra": {
                    "codex_5h_used_percent": 40,
                    "codex_5h_reset_after_seconds": 10_800,
                    "codex_5h_window_minutes": 300,
                    "codex_7d_used_percent": 25,
                    "codex_7d_reset_after_seconds": 604_800,
                    "codex_7d_window_minutes": 10_080,
                },
            },
            {
                "id": 2,
                "status": "active",
                "schedulable": True,
                "plan_type": "k12",
                "extra": {
                    "codex_5h_used_percent": 0,
                    "codex_5h_reset_after_seconds": 10_800,
                    "codex_5h_window_minutes": 300,
                    "codex_7d_used_percent": 0,
                    "codex_7d_reset_after_seconds": 604_800,
                    "codex_7d_window_minutes": 10_080,
                },
            },
        ]
        cost_summary = {
            "five_hour_peak_cost": 0.0,
            "recent_day_five_hour_peak_cost": 0.0,
            "seven_day_24h_peak_cost": 0.0,
            "recent_5h_cost": 0.0,
            "recent_24h_cost": 0.0,
            "seven_day_cost": 0.0,
            "burst_1h": {
                "observed_cost": 0.0,
                "elapsed_minutes": 60,
                "projection_multiplier": 1.0,
                "cost": 0.0,
                "five_hour_estimated_cost": 0.0,
                "source": "test",
                "window_count": 0,
                "trend": "steady",
                "trend_label": "平稳",
                "trend_strength": "weak",
                "trend_strength_label": "弱",
                "trend_change_percent": 0.0,
                "previous_cost": 0.0,
                "trend_recent_avg_cost": 0.0,
                "trend_baseline_avg_cost": 0.0,
                "trend_recent_hours": 0,
                "trend_baseline_hours": 0,
            },
        }

        with (
            patch.object(cache, "get_capacity_account_limits", AsyncMock(return_value={"limits": limits})),
            patch.object(cache, "_dashboard_cost_summary", AsyncMock(return_value=cost_summary)),
        ):
            summary = await cache._capacity_summary_for_accounts(object(), "api-5001", accounts, group_id=1)

        self.assertEqual(summary["account_type"], "plus")
        self.assertEqual(summary["active_five_hour_capacity_usd"], 130)
        self.assertEqual(summary["active_seven_day_capacity_usd"], 210)
        self.assertEqual(summary["five_hour_actual_remaining_usd"], 102.5)
        self.assertEqual(summary["seven_day_actual_remaining_usd"], 182.5)

    async def test_dynamic_and_actual_available_percentages_use_dynamic_capacity(self) -> None:
        limits = normalize_capacity_limits(None)
        accounts = [
            {
                "id": 1,
                "status": "active",
                "schedulable": True,
                "plan_type": "plus",
                "extra": {
                    "codex_5h_used_percent": 100,
                    "codex_5h_reset_after_seconds": 3_600,
                    "codex_5h_window_minutes": 300,
                    "codex_7d_used_percent": 50,
                    "codex_7d_reset_after_seconds": 86_400,
                    "codex_7d_window_minutes": 10_080,
                },
            },
            {
                "id": 2,
                "status": "active",
                "schedulable": True,
                "plan_type": "plus",
                "extra": {
                    "codex_5h_used_percent": 50,
                    "codex_5h_reset_after_seconds": 10_800,
                    "codex_5h_window_minutes": 300,
                    "codex_7d_used_percent": 10,
                    "codex_7d_reset_after_seconds": 604_800,
                    "codex_7d_window_minutes": 10_080,
                },
            },
        ]
        empty_reserve = cache._empty_capacity_type_summary(limits)
        cost_summary = {
            "five_hour_peak_cost": 0.0,
            "recent_day_five_hour_peak_cost": 0.0,
            "seven_day_24h_peak_cost": 0.0,
            "recent_5h_cost": 0.0,
            "recent_24h_cost": 0.0,
            "seven_day_cost": 0.0,
            "burst_1h": {
                "observed_cost": 0.0,
                "elapsed_minutes": 60,
                "projection_multiplier": 1.0,
                "cost": 0.0,
                "five_hour_estimated_cost": 0.0,
                "source": "test",
                "window_count": 0,
                "trend": "steady",
                "trend_label": "平稳",
                "trend_strength": "weak",
                "trend_strength_label": "弱",
                "trend_change_percent": 0.0,
                "previous_cost": 0.0,
                "trend_recent_avg_cost": 0.0,
                "trend_baseline_avg_cost": 0.0,
                "trend_recent_hours": 0,
                "trend_baseline_hours": 0,
            },
        }

        with (
            patch.object(cache, "get_capacity_account_limits", AsyncMock(return_value={"limits": limits})),
            patch.object(cache, "_reserve_capacity_by_account_type", AsyncMock(return_value=empty_reserve)),
            patch.object(cache, "_dashboard_cost_summary", AsyncMock(return_value=cost_summary)),
        ):
            summary = await cache._capacity_summary_for_accounts(object(), "api-5001", accounts, group_id=1)

        self.assertEqual(summary["dynamic_five_hour_capacity_usd"], 56)
        self.assertEqual(summary["available_5h_percent"], 65)
        self.assertEqual(summary["actual_available_5h_percent"], 25)
        self.assertEqual(summary["active_available_5h_percent"], 65)
        self.assertEqual(summary["active_actual_available_5h_percent"], 25)
        self.assertEqual(summary["available_7d_percent"], 91)
        self.assertEqual(summary["actual_available_7d_percent"], 70)

    async def test_plus_dynamic_capacity_includes_seven_day_reset_within_one_day(self) -> None:
        limits = normalize_capacity_limits({"plus": {"five_hour_usd": 120, "seven_day_usd": 600}})
        accounts = [
            {
                "id": 1,
                "status": "active",
                "schedulable": True,
                "plan_type": "plus",
                "extra": {
                    "codex_5h_used_percent": 20,
                    "codex_5h_reset_after_seconds": 3_600,
                    "codex_5h_window_minutes": 300,
                    "codex_7d_used_percent": 40,
                    "codex_7d_reset_after_seconds": 86_400,
                    "codex_7d_window_minutes": 10_080,
                },
            },
            {
                "id": 2,
                "status": "active",
                "schedulable": True,
                "plan_type": "plus",
                "extra": {
                    "codex_5h_used_percent": 100,
                    "codex_5h_reset_after_seconds": 7_200,
                    "codex_5h_window_minutes": 300,
                    "codex_7d_used_percent": 80,
                    "codex_7d_reset_after_seconds": 86_400,
                    "codex_7d_window_minutes": 10_080,
                },
            },
            {
                "id": 3,
                "status": "active",
                "schedulable": True,
                "plan_type": "plus",
                "extra": {
                    "codex_5h_used_percent": 10,
                    "codex_5h_reset_after_seconds": 3_600,
                    "codex_5h_window_minutes": 300,
                    "codex_7d_used_percent": 100,
                    "codex_7d_reset_after_seconds": 86_400,
                    "codex_7d_window_minutes": 10_080,
                },
            },
        ]
        empty_reserve = cache._empty_capacity_type_summary(limits)
        cost_summary = {
            "five_hour_peak_cost": 0.0,
            "recent_day_five_hour_peak_cost": 0.0,
            "seven_day_24h_peak_cost": 0.0,
            "recent_5h_cost": 0.0,
            "recent_24h_cost": 0.0,
            "seven_day_cost": 0.0,
            "burst_1h": {
                "observed_cost": 0.0,
                "elapsed_minutes": 60,
                "projection_multiplier": 1.0,
                "cost": 0.0,
                "five_hour_estimated_cost": 0.0,
                "source": "test",
                "window_count": 0,
                "trend": "steady",
                "trend_label": "平稳",
                "trend_strength": "weak",
                "trend_strength_label": "弱",
                "trend_change_percent": 0.0,
                "previous_cost": 0.0,
                "trend_recent_avg_cost": 0.0,
                "trend_baseline_avg_cost": 0.0,
                "trend_recent_hours": 0,
                "trend_baseline_hours": 0,
            },
        }

        with (
            patch.object(cache, "get_capacity_account_limits", AsyncMock(return_value={"limits": limits})),
            patch.object(cache, "_reserve_capacity_by_account_type", AsyncMock(return_value=empty_reserve)),
            patch.object(cache, "_dashboard_cost_summary", AsyncMock(return_value=cost_summary)),
        ):
            summary = await cache._capacity_summary_for_accounts(object(), "api-5001", accounts, group_id=1)

        self.assertEqual(summary["account_type"], "plus")
        self.assertEqual(summary["capacity_limits"]["plus"]["five_hour_usd"], 120)
        self.assertEqual(summary["active_five_hour_capacity_usd"], 360)
        self.assertEqual(summary["dynamic_five_hour_capacity_usd"], 360)


if __name__ == "__main__":
    unittest.main()
