from __future__ import annotations

import unittest
from datetime import UTC, datetime

from scripts.compare_sub2api_pool_snapshot_sources import (
    _account_usage_sample,
    compare_account_usage_snapshots,
    compare_dashboard_snapshots,
    compare_snapshots,
)


class CompareSub2ApiPoolSnapshotSourcesTests(unittest.TestCase):
    def test_account_usage_sample_prioritizes_accounts_with_both_official_windows(self) -> None:
        accounts = [
            {"id": 1},
            {"id": 2, "extra": {"codex_5h_used_percent": 10}},
            {"id": 3, "extra": {"codex_5h_used_percent": 20, "codex_7d_used_percent": 30}},
            {"id": 4, "codex_5h_used_percent": 40, "codex_7d_used_percent": 50},
        ]

        result = _account_usage_sample(accounts, 2)

        self.assertEqual([account["id"] for account in result], [3, 4])

    def test_account_usage_comparison_reports_window_metric_differences(self) -> None:
        database = {
            10: {
                "five_hour": {
                    "utilization": 25,
                    "resets_at": "2026-07-19T14:00:00Z",
                    "remaining_seconds": 7200,
                    "window_stats": {"requests": 12, "tokens": 100, "cost": 2.5, "standard_cost": 2.0, "user_cost": 3.0},
                },
                "seven_day": {"utilization": 60, "window_stats": {"requests": 50}},
            }
        }
        http = {
            10: {
                "five_hour": {
                    "utilization": 25,
                    "resets_at": "2026-07-19T22:00:00+08:00",
                    "remaining_seconds": 7198,
                    "window_stats": {"requests": 13, "tokens": 100, "cost": 2.5, "standard_cost": 2.0, "user_cost": 3.0},
                },
                "seven_day": {"utilization": 60, "window_stats": {"requests": 50}},
                "credentials": {"access_token": "must-not-be-reported"},
            }
        }

        result = compare_account_usage_snapshots(database, http)

        self.assertEqual(result["database_accounts"], 1)
        self.assertEqual(result["http_accounts"], 1)
        self.assertEqual(result["difference_count"], 1)
        self.assertEqual(
            result["difference_examples"][0],
            {"id": 10, "windows": {"five_hour": {"requests": {"database": 12, "http": 13}}}},
        )
        self.assertNotIn("must-not-be-reported", str(result))

    def test_dashboard_comparison_reports_only_metric_differences(self) -> None:
        database = {
            "trend": [
                {"date": "2026-07-19 12:00", "requests": 10, "total_tokens": 100, "cost": 2.0, "actual_cost": 1.5},
            ]
        }
        http = {
            "trend": [
                {"date": "2026-07-19 12:00", "requests": 11, "total_tokens": 100, "cost": 2.0, "actual_cost": 1.5},
            ],
            "models": [{"model": "must-not-be-reported"}],
        }

        result = compare_dashboard_snapshots(database, http)

        self.assertEqual(result["database_points"], 1)
        self.assertEqual(result["http_points"], 1)
        self.assertEqual(result["difference_count"], 1)
        self.assertEqual(result["difference_examples"][0]["metrics"], {"requests": {"database": 10, "http": 11}})
        self.assertNotIn("must-not-be-reported", str(result))

    def test_equivalent_timestamps_do_not_create_account_differences(self) -> None:
        database = {
            "groups": [{"id": 3, "status": "active", "account_count": 1, "active_account_count": 1}],
            "accounts": [
                {
                    "id": 10,
                    "status": "active",
                    "schedulable": True,
                    "priority": 100,
                    "concurrency": 10,
                    "rate_limit_reset_at": datetime(2026, 7, 19, 1, 0, tzinfo=UTC),
                    "group_ids": [3],
                    "credentials": {"access_token": "must-not-be-printed"},
                }
            ],
        }
        http = {
            "groups": [{"id": 3, "status": "active", "account_count": 1, "active_account_count": 1}],
            "accounts": [
                {
                    "id": 10,
                    "status": "active",
                    "schedulable": True,
                    "priority": 100,
                    "concurrency": 10,
                    "rate_limit_reset_at": "2026-07-19T09:00:00+08:00",
                    "group_ids": [3],
                    "credentials": {"access_token": "also-must-not-be-printed"},
                }
            ],
        }

        result = compare_snapshots(database, http)

        self.assertEqual(result["account_difference_count"], 0)
        self.assertNotIn("must-not-be-printed", str(result))


if __name__ == "__main__":
    unittest.main()
