from __future__ import annotations

import unittest

from app.modules.sub2api import cache


class ConcurrencyCapacityTests(unittest.TestCase):
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
        ]

        summary = cache._concurrency_capacity_summary(accounts)

        self.assertEqual(summary["concurrency_actual_in_use"], 4)
        self.assertEqual(summary["concurrency_safe_available"], 7)
        self.assertEqual(summary["concurrency_near_limit_available"], 6)
        self.assertEqual(summary["concurrency_actual_available"], 13)
        self.assertEqual(summary["concurrency_total_capacity"], 31)
        self.assertEqual(summary["concurrency_temporarily_unavailable"], 14)
        self.assertEqual(summary["concurrency_eligible_accounts"], 5)
        self.assertEqual(summary["concurrency_available_accounts"], 3)
        self.assertEqual(summary["concurrency_safe_accounts"], 1)
        self.assertEqual(summary["concurrency_near_limit_accounts"], 2)
        self.assertEqual(summary["concurrency_five_hour_limited_accounts"], 1)
        self.assertEqual(summary["concurrency_short_seven_day_limited_accounts"], 1)
        self.assertEqual(summary["concurrency_other_unavailable_accounts"], 0)
        self.assertEqual(summary["concurrency_long_seven_day_limited_accounts"], 1)

    def test_pool_overview_excludes_bug_team_and_prioritizes_401_as_abnormal(self) -> None:
        accounts = [
            {"id": 1, "status": "active", "schedulable": True},
            {"id": 2, "status": "active", "schedulable": False},
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

        self.assertEqual(summary["pool_normal_accounts"], 2)
        self.assertEqual(summary["pool_active_normal_accounts"], 1)
        self.assertEqual(summary["pool_five_hour_rate_limited_accounts"], 1)
        self.assertEqual(summary["pool_seven_day_rate_limited_accounts"], 1)
        self.assertEqual(summary["pool_abnormal_accounts"], 1)
        self.assertEqual(summary["pool_excluded_bug_team_accounts"], 1)
        self.assertFalse(cache._is_capacity_account(accounts[2]))


if __name__ == "__main__":
    unittest.main()
