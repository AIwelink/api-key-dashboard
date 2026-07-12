from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.sub2api import account_probe


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


if __name__ == "__main__":
    unittest.main()
