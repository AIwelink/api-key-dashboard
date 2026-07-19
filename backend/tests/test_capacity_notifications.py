from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.sub2api.capacity_notifications import (
    _capacity_recovery_text,
    _capacity_notification_text,
    _evaluate_group_capacity_notification,
    capacity_notification_decision,
)


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

    def test_tight_does_not_repeat_after_cooldown(self) -> None:
        decision = capacity_notification_decision(
            setting=self.setting,
            summary={"health_status": "tight"},
            meta={
                "active_alert": True,
                "last_notified_status": "tight",
                "last_attempt_at": self.now - timedelta(minutes=120),
            },
            now=self.now,
        )

        self.assertFalse(decision["send"])
        self.assertEqual(decision["reason"], "tight_repeat_suppressed")

    def test_danger_repeats_after_cooldown(self) -> None:
        decision = capacity_notification_decision(
            setting=self.setting,
            summary={"health_status": "danger"},
            meta={
                "active_alert": True,
                "last_notified_status": "danger",
                "last_attempt_at": self.now - timedelta(minutes=120),
            },
            now=self.now,
        )

        self.assertTrue(decision["send"])
        self.assertEqual(decision["notification_type"], "alert")
        self.assertEqual(decision["reason"], "cooldown_elapsed")

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
            meta={},
            now=self.now,
        )

        self.assertFalse(decision["send"])
        self.assertFalse(decision["below_threshold"])
        self.assertEqual(decision["reason"], "above_threshold")

    def test_healthy_status_sends_one_recovery_for_active_alert(self) -> None:
        decision = capacity_notification_decision(
            setting=self.setting,
            summary={"health_status": "healthy"},
            meta={"active_alert": True, "last_notified_status": "danger"},
            now=self.now,
        )

        self.assertTrue(decision["send"])
        self.assertFalse(decision["below_threshold"])
        self.assertEqual(decision["notification_type"], "recovery")
        self.assertEqual(decision["reason"], "recovered")

    def test_pending_status_does_not_recover_active_alert(self) -> None:
        decision = capacity_notification_decision(
            setting=self.setting,
            summary={"health_status": "pending"},
            meta={"active_alert": True, "last_notified_status": "danger"},
            now=self.now,
        )

        self.assertFalse(decision["send"])
        self.assertEqual(decision["reason"], "waiting_data")
        self.assertTrue(decision["keep_active_alert"])

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

    def test_sub_one_hour_realtime_runway_alerts_even_when_threshold_is_exhausted(self) -> None:
        setting = {**self.setting, "capacity_notification_threshold": "exhausted"}
        decision = capacity_notification_decision(
            setting=setting,
            summary={
                "health_status": "danger",
                "realtime_risk_ready": True,
                "actual_runway_hours": 0.8,
                "dynamic_runway_hours": 0.9,
            },
            meta={},
            now=self.now,
        )

        self.assertTrue(decision["send"])
        self.assertTrue(decision["below_threshold"])
        self.assertEqual(decision["reason"], "realtime_runway_below_one_hour")


class CapacityNotificationTextTests(unittest.TestCase):
    def test_sub_one_hour_alert_explains_exhausted_threshold_override(self) -> None:
        message = _capacity_notification_text(
            site_id="api-5001",
            group_id=3,
            group_name="Plus 池",
            threshold="exhausted",
            trigger_reason="realtime_runway_below_one_hour",
            summary={
                "health_status": "danger",
                "health_label": "危险",
                "actual_runway_hours": 0.8,
                "dynamic_runway_hours": 0.9,
            },
        )

        self.assertIn("通知阈值：仅耗尽（实时<1h仍告警）", message)
        self.assertIn("触发方式：实时可用时间低于1小时", message)

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
                "forecast_status": "active",
                "forecast_nowcast_applied": True,
                "actual_runway_hours": 1.25,
                "dynamic_runway_hours": 2.5,
                "pressure_tpm": 497365,
                "pressure_rpm": 45,
                "latest_tpm": 321000,
                "latest_rpm": 42,
                "traffic_site_id": "api-5001",
                "concurrency_coverage": 1.08,
                "recommended_refill_accounts": 4,
                "recommended_refill_options": {
                    "plus": {"account_type": "plus", "recommended_refill_accounts": 2},
                    "k12": {"account_type": "k12", "recommended_refill_accounts": 9},
                },
                "available_accounts": 12,
                "available_5h_accounts": 10,
                "five_hour_actual_remaining_usd": 80,
                "dynamic_five_hour_remaining_estimated_usd": 160,
                "dynamic_five_hour_capacity_usd": 220,
                "seven_day_actual_remaining_usd": 500,
                "seven_day_remaining_estimated_usd": 700,
                "seven_day_capacity_usd": 900,
            },
        )

        self.assertIn("压力阶段：加速上涨", message)
        self.assertIn("预测口径：未来24小时 P90逐小时 + 当前小时Nowcast", message)
        self.assertIn("实际 / 动态可用：1.2小时 / 2.5小时", message)
        self.assertIn("5001 TPM / RPM：321,000 / 42", message)
        self.assertNotIn("497,365 / 45", message)
        self.assertIn("并发覆盖：1.08x", message)
        self.assertIn("5h 可用：实际 $80.00 / 动态 $160.00 / 容量 $220.00", message)
        self.assertIn("7d 可用：实际 $500.00 / 动态 $700.00 / 容量 $900.00", message)
        self.assertIn("当前账号：12 个，5h 可用 10 个", message)
        self.assertIn("建议动作：补 Plus 2 个，或补 K12 9 个。仅供参考，请结合实时供货和账号质量判断。", message)
        self.assertIn("判断原因：动态容量不足 3 小时", message)

    def test_recovery_text_contains_current_operating_state(self) -> None:
        message = _capacity_recovery_text(
            site_id="api-5001",
            group_id=3,
            group_name="Plus 池",
            recovered_at=datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
            summary={
                "health_status": "healthy",
                "health_label": "健康",
                "pressure_stage_label": "稳定",
                "actual_runway_hours": 1.5,
                "dynamic_runway_hours": 3.5,
                "concurrency_coverage": 1.3,
                "available_accounts": 14,
            },
        )

        self.assertIn("恢复状态：健康", message)
        self.assertIn("实际 / 动态可用：1.5小时 / 3.5小时", message)
        self.assertIn("并发覆盖：1.30x", message)
        self.assertIn("可用账号：14 个", message)
        self.assertIn("恢复时间：2026-07-16 20:00", message)


class CapacityNotificationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.setting = {
            "capacity_notification_enabled": True,
            "capacity_notification_threshold": "tight",
            "capacity_notification_cooldown_minutes": 60,
        }

    async def test_recovery_is_sent_once_and_closes_active_alert(self) -> None:
        meta_collection = SimpleNamespace(
            find_one=AsyncMock(return_value={"active_alert": True, "last_notified_status": "danger"}),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(sub2api_capacity_notification_meta=meta_collection)
        sender = AsyncMock(return_value={"event": {"id": "event-1", "status": "success"}, "success": 1})

        with patch("app.modules.sub2api.capacity_notifications.send_notification_event", sender):
            result = await _evaluate_group_capacity_notification(
                db,
                site_id="api-5001",
                group_id=3,
                group_name="Plus 池",
                setting=self.setting,
                summary={
                    "health_status": "healthy",
                    "health_label": "健康",
                    "actual_runway_hours": 1.5,
                    "dynamic_runway_hours": 3.5,
                    "concurrency_coverage": 1.3,
                },
            )

        self.assertTrue(result["sent"])
        self.assertEqual(result["notification_type"], "recovery")
        send_kwargs = sender.await_args.kwargs
        self.assertEqual(send_kwargs["event_type"], "sub2api.capacity.recovered")
        self.assertEqual(send_kwargs["severity"], "success")
        update = meta_collection.update_one.await_args.args[1]["$set"]
        self.assertFalse(update["active_alert"])
        self.assertIn("last_recovered_at", update)

    async def test_capacity_alert_keeps_low_capacity_event_type(self) -> None:
        meta_collection = SimpleNamespace(find_one=AsyncMock(return_value={}), update_one=AsyncMock())
        db = SimpleNamespace(sub2api_capacity_notification_meta=meta_collection)
        sender = AsyncMock(return_value={"event": {"id": "event-2", "status": "success"}, "success": 1})

        with patch("app.modules.sub2api.capacity_notifications.send_notification_event", sender):
            result = await _evaluate_group_capacity_notification(
                db,
                site_id="api-5001",
                group_id=3,
                group_name="Plus 池",
                setting=self.setting,
                summary={"health_status": "danger", "health_label": "危险"},
            )

        self.assertTrue(result["sent"])
        self.assertEqual(result["notification_type"], "alert")
        self.assertEqual(sender.await_args.kwargs["event_type"], "sub2api.capacity.low")


if __name__ == "__main__":
    unittest.main()
