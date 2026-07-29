from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.modules.sub2api import cache


class ConcurrencyCapacityTests(unittest.TestCase):
    def test_no_five_hour_window_rate_limit_is_seven_day_for_concurrency(self) -> None:
        current_time = datetime(2026, 7, 19, 15, 30, tzinfo=timezone.utc)
        account = {
            "status": "active",
            "schedulable": True,
            "concurrency": 10,
            "current_concurrency": 0,
            "rate_limit_reset_at": current_time + timedelta(hours=12),
            "codex_5h_used_percent": 0,
            "codex_5h_window_minutes": 0,
            "codex_7d_used_percent": 0,
            "codex_7d_reset_after_seconds": 12 * 60 * 60,
            "codex_7d_window_minutes": 10_080,
        }

        with patch.object(cache, "now_utc", return_value=current_time):
            self.assertEqual(cache._current_concurrency_unavailable_kind(account), "short_seven_day")
            summary = cache._concurrency_capacity_summary([account])

        self.assertEqual(summary["concurrency_five_hour_limited_accounts"], 0)
        self.assertEqual(summary["concurrency_short_seven_day_limited_accounts"], 1)

    def test_five_hour_and_seven_day_safe_thresholds_are_both_eighty_percent(self) -> None:
        safe = {"codex_5h_used_percent": 79.99, "codex_7d_used_percent": 79.99}
        five_hour_boundary = {"codex_5h_used_percent": 80, "codex_7d_used_percent": 20}
        seven_day_boundary = {"codex_5h_used_percent": 20, "codex_7d_used_percent": 80}

        self.assertTrue(cache._is_safe_concurrency_account(safe))
        self.assertFalse(cache._is_safe_concurrency_account(five_hour_boundary))
        self.assertFalse(cache._is_safe_concurrency_account(seven_day_boundary))

    def test_three_level_concurrency_capacity_rules(self) -> None:
        accounts = [
            {
                "id": 1,
                "status": "active",
                "schedulable": True,
                "concurrency": 10,
                "current_concurrency": 3,
                "codex_5h_used_percent": 20,
                "codex_7d_used_percent": 40,
            },
            {
                "id": 2,
                "status": "active",
                "schedulable": True,
                "concurrency": 4,
                "current_concurrency": 1,
                "codex_5h_used_percent": 85,
                "codex_7d_used_percent": 50,
            },
            {
                "id": 3,
                "status": "active",
                "schedulable": True,
                "concurrency": 3,
                "current_concurrency": 0,
                "codex_5h_used_percent": 50,
                "codex_7d_used_percent": 80,
            },
            {
                "id": 4,
                "status": "error",
                "error_message": "429 rate limit",
                "concurrency": 8,
                "current_concurrency": 0,
                "codex_5h_used_percent": 100,
                "codex_5h_reset_after_seconds": 3600,
                "codex_7d_used_percent": 30,
            },
            {
                "id": 5,
                "status": "error",
                "error_message": "429 rate limit",
                "concurrency": 6,
                "current_concurrency": 0,
                "codex_7d_used_percent": 100,
                "codex_7d_reset_after_seconds": 12 * 60 * 60,
            },
            {
                "id": 6,
                "status": "error",
                "error_message": "429 rate limit",
                "concurrency": 5,
                "current_concurrency": 0,
                "codex_7d_used_percent": 100,
                "codex_7d_reset_after_seconds": 2 * 24 * 60 * 60,
            },
            {
                "id": 7,
                "status": "rate_limited",
                "schedulable": False,
                "concurrency": 7,
                "current_concurrency": 0,
                "codex_5h_used_percent": 100,
                "codex_5h_reset_after_seconds": 2 * 60 * 60,
                "codex_7d_used_percent": 40,
                "codex_7d_reset_after_seconds": 6 * 24 * 60 * 60,
            },
        ]

        capacity_accounts = [account for account in accounts if cache._is_capacity_account(account)]
        summary = cache._concurrency_capacity_summary(capacity_accounts)

        self.assertEqual(summary["concurrency_actual_in_use"], 4)
        self.assertEqual(summary["concurrency_safe_available"], 7)
        self.assertEqual(summary["concurrency_near_limit_available"], 6)
        self.assertEqual(summary["concurrency_actual_available"], 13)
        self.assertEqual(summary["concurrency_total_capacity"], 31)
        self.assertEqual(summary["concurrency_temporarily_unavailable"], 14)
        self.assertEqual(summary["concurrency_temporarily_unavailable_accounts"], 2)
        self.assertEqual(summary["concurrency_eligible_accounts"], 5)
        self.assertEqual(summary["concurrency_available_accounts"], 3)
        self.assertEqual(summary["concurrency_safe_accounts"], 1)
        self.assertEqual(summary["concurrency_near_limit_accounts"], 2)
        self.assertEqual(summary["concurrency_five_hour_limited_accounts"], 1)
        self.assertEqual(summary["concurrency_short_seven_day_limited_accounts"], 1)
        self.assertEqual(summary["concurrency_other_unavailable_accounts"], 0)
        self.assertEqual(summary["concurrency_long_seven_day_limited_accounts"], 1)
        self.assertFalse(cache._is_long_seven_day_concurrency_limit(accounts[6]))
        self.assertFalse(cache._is_capacity_account(accounts[6]))

    def test_pool_overview_excludes_bug_team_and_prioritizes_401_as_abnormal(self) -> None:
        accounts = [
            {"id": 1, "status": "active", "schedulable": True, "credentials_status": "valid"},
            {
                "id": 2,
                "status": "active",
                "schedulable": False,
                "credentials_status": {"status": "valid", "expires_at": "2026-07-20T00:00:00Z"},
                "temp_unschedulable_reason": "manually disabled",
            },
            {
                "id": 3,
                "status": "active",
                "schedulable": False,
                "error_message": "Authentication failed (401): token invalidated",
            },
            {
                "id": 4,
                "status": "error",
                "error_message": "429 rate limit",
                "codex_5h_used_percent": 100,
                "codex_7d_used_percent": 30,
            },
            {
                "id": 5,
                "status": "error",
                "error_message": "429 rate limit",
                "codex_5h_used_percent": 20,
                "codex_7d_used_percent": 100,
            },
            {
                "id": 6,
                "status": "error",
                "plan_type": "team",
                "error_message": "429 rate limit",
                "codex_5h_window_minutes": 0,
                "codex_7d_window_minutes": 43_800,
                "codex_7d_used_percent": 100,
            },
        ]

        summary = cache._pool_account_status_summary(accounts)

        self.assertEqual(summary["pool_normal_accounts"], 4)
        self.assertEqual(summary["pool_active_normal_accounts"], 1)
        self.assertEqual(summary["pool_five_hour_rate_limited_accounts"], 1)
        self.assertEqual(summary["pool_seven_day_rate_limited_accounts"], 1)
        self.assertEqual(summary["pool_abnormal_accounts"], 1)
        self.assertEqual(summary["pool_excluded_bug_team_accounts"], 1)
        self.assertFalse(cache._is_abnormal_account(accounts[0]))
        self.assertFalse(cache._is_abnormal_account(accounts[1]))
        self.assertTrue(cache._is_capacity_account(accounts[0]))
        self.assertFalse(cache._is_capacity_account(accounts[1]))
        self.assertFalse(cache._is_capacity_account(accounts[2]))

    def test_success_message_does_not_exclude_normal_capacity(self) -> None:
        normalized = cache._normalize_account_snapshot(
            {
                "id": 7,
                "status": "active",
                "schedulable": True,
                "message": "success",
                "credentials_status": {"status": "valid"},
                "concurrency": 10,
                "current_concurrency": 2,
                "codex_5h_used_percent": 20,
                "codex_7d_used_percent": 30,
            }
        )

        self.assertIsNone(normalized["error_message"])
        self.assertFalse(cache._is_abnormal_account(normalized))
        self.assertTrue(cache._is_capacity_account(normalized))
        concurrency = cache._concurrency_capacity_summary([normalized])
        self.assertEqual(concurrency["concurrency_actual_in_use"], 2)
        self.assertEqual(concurrency["concurrency_actual_available"], 8)
        self.assertEqual(concurrency["concurrency_total_capacity"], 10)


if __name__ == "__main__":
    unittest.main()
