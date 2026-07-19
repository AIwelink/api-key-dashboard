from __future__ import annotations

import unittest
from datetime import UTC, datetime

from scripts.compare_sub2api_pool_snapshot_sources import compare_snapshots


class CompareSub2ApiPoolSnapshotSourcesTests(unittest.TestCase):
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
