from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.modules.api_pools.capacity_limits import normalize_capacity_limits
from app.modules.sub2api import cache


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def remote_accounts(*, used_5h_percent: float) -> list[dict[str, object]]:
    return [
        {
            "id": account_id,
            "email": f"account-{account_id}@example.com",
            "status": "active",
            "schedulable": True,
            "plan_type": "plus",
            "concurrency": 10,
            "current_concurrency": 0,
            "codex_5h_used_percent": used_5h_percent,
            "codex_5h_reset_after_seconds": 4 * 60 * 60,
            "codex_5h_window_minutes": 300,
            "codex_7d_used_percent": 0,
            "codex_7d_reset_after_seconds": 7 * 24 * 60 * 60,
            "codex_7d_window_minutes": 10_080,
        }
        for account_id in range(1, 5)
    ]


def minute_samples() -> list[dict[str, object]]:
    return [
        {
            "sampled_at": NOW - timedelta(minutes=19 - index),
            "tpm": 1000,
            "rpm": 60,
            "average_duration_ms": 1000,
        }
        for index in range(20)
    ]


def cost_summary(*, historical_peak: float = 0.0) -> dict[str, object]:
    return {
        "five_hour_peak_cost": historical_peak,
        "recent_day_five_hour_peak_cost": historical_peak,
        "seven_day_24h_peak_cost": historical_peak,
        "recent_5h_cost": historical_peak,
        "recent_24h_cost": historical_peak,
        "seven_day_cost": historical_peak,
        "recent_6h_cost_per_token": 1 / 60_000,
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


class SinglePoolCapacityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_reserve_pool_is_not_queried_or_included(self) -> None:
        limits = normalize_capacity_limits({"plus": {"five_hour_usd": 2, "seven_day_usd": 10}})
        reserve = AsyncMock()

        with (
            patch.object(cache, "now_utc", return_value=NOW),
            patch.object(cache, "get_capacity_account_limits", AsyncMock(return_value={"limits": limits})),
            patch.object(cache, "_dashboard_cost_summary", AsyncMock(return_value=cost_summary())),
            patch.object(cache, "_load_group_tpm_samples", AsyncMock(return_value=minute_samples())),
            patch.object(cache, "_reserve_capacity_by_account_type", reserve),
        ):
            summary = await cache._capacity_summary_for_accounts(
                object(),
                "api-5001",
                remote_accounts(used_5h_percent=75),
                group_id=3,
            )

        reserve.assert_not_awaited()
        self.assertEqual(summary["reserve_available_accounts"], 0)
        self.assertEqual(summary["reserve_five_hour_capacity_usd"], 0)
        self.assertEqual(summary["health_status"], "tight")
        self.assertTrue(summary["replenishment_required"])
        self.assertEqual(summary["recommended_refill_accounts"], 1)
        self.assertFalse(summary["auto_refill_required"])

    async def test_realtime_risk_replaces_historical_day_scale_status(self) -> None:
        limits = normalize_capacity_limits({"plus": {"five_hour_usd": 2, "seven_day_usd": 10}})

        with (
            patch.object(cache, "now_utc", return_value=NOW),
            patch.object(cache, "get_capacity_account_limits", AsyncMock(return_value={"limits": limits})),
            patch.object(cache, "_dashboard_cost_summary", AsyncMock(return_value=cost_summary(historical_peak=100))),
            patch.object(cache, "_load_group_tpm_samples", AsyncMock(return_value=minute_samples())),
        ):
            summary = await cache._capacity_summary_for_accounts(
                object(),
                "api-5001",
                remote_accounts(used_5h_percent=0),
                group_id=3,
            )

        self.assertTrue(summary["realtime_risk_ready"])
        self.assertEqual(summary["health_status"], "abundant")
        self.assertEqual(summary["pressure_stage"], "stable")
        self.assertEqual(summary["dynamic_runway_hours"], 8.0)
        self.assertFalse(summary["replenishment_required"])


if __name__ == "__main__":
    unittest.main()
