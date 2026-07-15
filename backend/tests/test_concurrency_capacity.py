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
                "status": "error",
                "error_message": "429 rate limit",
                "concurrency": 8,
                "current_concurrency": 0,
                "codex_5h_used_percent": 100,
                "codex_5h_reset_after_seconds": 3600,
                "codex_7d_used_percent": 30,
            },
            {
                "id": 3,
                "status": "error",
                "error_message": "429 rate limit",
                "concurrency": 6,
                "current_concurrency": 0,
                "codex_7d_used_percent": 100,
                "codex_7d_reset_after_seconds": 12 * 60 * 60,
            },
            {
                "id": 4,
                "status": "error",
                "error_message": "429 rate limit",
                "concurrency": 5,
                "current_concurrency": 0,
                "codex_7d_used_percent": 100,
                "codex_7d_reset_after_seconds": 2 * 24 * 60 * 60,
            },
        ]

        summary = cache._concurrency_capacity_summary(accounts)

        self.assertEqual(summary["concurrency_actual_in_use"], 3)
        self.assertEqual(summary["concurrency_actual_available"], 7)
        self.assertEqual(summary["concurrency_total_capacity"], 24)
        self.assertEqual(summary["concurrency_temporarily_unavailable"], 14)
        self.assertEqual(summary["concurrency_eligible_accounts"], 3)
        self.assertEqual(summary["concurrency_available_accounts"], 1)
        self.assertEqual(summary["concurrency_five_hour_limited_accounts"], 1)
        self.assertEqual(summary["concurrency_short_seven_day_limited_accounts"], 1)
        self.assertEqual(summary["concurrency_other_unavailable_accounts"], 0)
        self.assertEqual(summary["concurrency_long_seven_day_limited_accounts"], 1)


if __name__ == "__main__":
    unittest.main()
