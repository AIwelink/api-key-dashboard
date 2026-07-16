from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.sub2api.capacity_notifications import _capacity_notification_text, capacity_notification_decision


class CapacityNotificationDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
        self.setting = {
            "capacity_notification_enabled": True,
            "capacity_notification_threshold": "tight",
            "capacity_notification_cooldown_minutes": 60,
        }

    def test_first_threshold_crossing_sends(self) -> None:
        decision = capacity_notification_decision(
            setting=self.setting,
            summary={"health_status": "tight"},
            meta={},
            now=self.now,
        )

        self.assertTrue(decision["send"])
        self.assertEqual(decision["reason"], "threshold_crossed")

    def test_cooldown_prevents_repeated_notification(self) -> None:
        decision = capacity_notification_decision(
            setting=self.setting,
            summary={"health_status": "tight"},
            meta={
                "active_alert": True,
                "last_notified_status": "tight",
                "last_attempt_at": self.now - timedelta(minutes=20),
            },
            now=self.now,
        )

        self.assertFalse(decision["send"])
        self.assertEqual(decision["reason"], "cooldown_active")

    def test_worsening_status_bypasses_cooldown(self) -> None:
        decision = capacity_notification_decision(
            setting=self.setting,
            summary={"health_status": "danger"},
            meta={
                "active_alert": True,
                "last_notified_status": "tight",
                "last_attempt_at": self.now - timedelta(minutes=5),
            },
            now=self.now,
        )

        self.assertTrue(decision["send"])
        self.assertEqual(decision["reason"], "status_worsened")

    def test_status_above_threshold_does_not_send(self) -> None:
        decision = capacity_notification_decision(
            setting=self.setting,
            summary={"health_status": "healthy"},
            meta={"active_alert": True},
            now=self.now,
        )

        self.assertFalse(decision["send"])
        self.assertFalse(decision["below_threshold"])
        self.assertEqual(decision["reason"], "above_threshold")

    def test_danger_threshold_does_not_send_for_tight(self) -> None:
        setting = {**self.setting, "capacity_notification_threshold": "danger"}
        decision = capacity_notification_decision(
            setting=setting,
            summary={"health_status": "tight"},
            meta={},
            now=self.now,
        )

        self.assertFalse(decision["send"])
        self.assertEqual(decision["reason"], "above_threshold")


class CapacityNotificationTextTests(unittest.TestCase):
    def test_realtime_risk_fields_are_included(self) -> None:
        message = _capacity_notification_text(
            site_id="api-5001",
            group_id=3,
            group_name="Plus 池",
            threshold="tight",
            summary={
                "health_status": "tight",
                "health_label": "需要补号",
                "health_reason": "动态容量不足 3 小时",
                "pressure_stage_label": "加速上涨",
                "actual_runway_hours": 1.25,
                "dynamic_runway_hours": 2.5,
                "pressure_tpm": 497365,
                "pressure_rpm": 45,
                "concurrency_coverage": 1.08,
                "recommended_refill_accounts": 4,
                "available_accounts": 12,
                "available_5h_accounts": 10,
            },
        )

        self.assertIn("压力阶段：加速上涨", message)
        self.assertIn("实际 / 动态可用：1.2小时 / 2.5小时", message)
        self.assertIn("TPM / RPM：497,365 / 45", message)
        self.assertIn("并发覆盖：1.08x", message)
        self.assertIn("建议补号：4 个", message)


if __name__ == "__main__":
    unittest.main()
