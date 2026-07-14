from __future__ import annotations

import unittest

from app.modules.api_pools.capacity_limits import normalize_capacity_limits
from app.modules.sub2api import cache


def bug_team_account(*, used_percent: float = 54) -> dict:
    return {
        "id": 1779,
        "plan_type": "team",
        "credentials": {"plan_type": "team"},
        "extra": {
            "codex_5h_reset_after_seconds": 0,
            "codex_5h_used_percent": 0,
            "codex_5h_window_minutes": 0,
            "codex_7d_reset_after_seconds": 2_607_895,
            "codex_7d_used_percent": used_percent,
            "codex_7d_window_minutes": 43_800,
        },
    }


class BugTeamCapacityTests(unittest.TestCase):
    def test_old_saved_limits_receive_bug_team_defaults(self) -> None:
        limits = normalize_capacity_limits({"team": {"five_hour_usd": 15, "seven_day_usd": 75}})

        self.assertEqual(limits["bug_team"], {"five_hour_usd": 230.0, "seven_day_usd": 230.0})

    def test_bug_team_is_detected_before_regular_team(self) -> None:
        self.assertEqual(cache._capacity_account_type(bug_team_account()), "bug_team")

    def test_bug_team_uses_seven_day_window_for_both_estimates(self) -> None:
        account = bug_team_account(used_percent=54)

        usage = cache._dynamic_five_hour_usage(
            account,
            five_hour_limit_usd=230,
            seven_day_limit_usd=230,
            five_hour_available=True,
        )

        self.assertAlmostEqual(usage["actual_used_usd"], 124.2)
        self.assertAlmostEqual(usage["actual_remaining_usd"], 105.8)
        self.assertAlmostEqual(usage["seven_day_actual_used_usd"], 124.2)
        self.assertAlmostEqual(usage["seven_day_actual_remaining_usd"], 105.8)

    def test_bug_team_is_separate_in_capacity_summary(self) -> None:
        limits = normalize_capacity_limits(None)

        summary = cache._capacity_by_account_type(
            [bug_team_account(used_percent=54)],
            [bug_team_account(used_percent=54)],
            limits,
        )

        self.assertEqual(cache._primary_capacity_type(summary), "bug_team")
        self.assertEqual(summary["bug_team"]["available_accounts"], 1)
        self.assertAlmostEqual(summary["bug_team"]["five_hour_capacity_usd"], 230)
        self.assertAlmostEqual(summary["bug_team"]["seven_day_capacity_usd"], 230)

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


if __name__ == "__main__":
    unittest.main()
