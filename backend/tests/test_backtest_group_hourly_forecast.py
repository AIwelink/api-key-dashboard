from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.sub2api.hourly_forecast import HourlyObservation, forecast_hourly_demand
from app.modules.sub2api.hourly_forecast_backtest import (
    forecast_persistence,
    forecast_seasonal_24h,
    forecast_seasonal_168h,
    rolling_origin_backtest,
)
from scripts.backtest_group_hourly_forecast import build_report


START = datetime(2026, 6, 1, tzinfo=UTC)


def history(hours: int) -> list[HourlyObservation]:
    return [
        HourlyObservation(
            bucket_at=START + timedelta(hours=index),
            account_cost=100 + (index % 24),
            requests=1000 + index,
            total_tokens=1_000_000 + index * 100,
        )
        for index in range(hours)
    ]


class BacktestGroupHourlyForecastScriptTests(unittest.TestCase):
    def test_build_report_contains_reproducible_forecast_and_gate_evidence(self) -> None:
        observations = history(15 * 24)
        as_of = observations[-1].bucket_at + timedelta(hours=1)
        evaluation_start = as_of - timedelta(days=3)
        forecast = forecast_hourly_demand(observations, as_of=as_of)
        candidate = rolling_origin_backtest(
            observations,
            forecaster=forecast_hourly_demand,
            horizons=24,
            minimum_history_hours=7 * 24,
            origin_step_hours=24,
            evaluation_start=evaluation_start,
        )
        baselines = {
            "persistence": rolling_origin_backtest(
                observations,
                forecaster=forecast_persistence,
                horizons=24,
                minimum_history_hours=7 * 24,
                origin_step_hours=24,
                evaluation_start=evaluation_start,
            ),
            "seasonal_24h": rolling_origin_backtest(
                observations,
                forecaster=forecast_seasonal_24h,
                horizons=24,
                minimum_history_hours=7 * 24,
                origin_step_hours=24,
                evaluation_start=evaluation_start,
            ),
            "seasonal_168h": rolling_origin_backtest(
                observations,
                forecaster=forecast_seasonal_168h,
                horizons=24,
                minimum_history_hours=7 * 24,
                origin_step_hours=24,
                evaluation_start=evaluation_start,
            ),
        }

        report = build_report(
            site_id="us06-5001",
            group_id=3,
            history=observations,
            forecast=forecast,
            candidate=candidate,
            baselines=baselines,
            evaluation_start=evaluation_start,
            origin_step_hours=24,
        )

        self.assertEqual(report["schema_version"], "hourly_capacity_forecast_backtest.v1")
        self.assertEqual(report["site_id"], "us06-5001")
        self.assertEqual(report["group_id"], 3)
        self.assertEqual(report["model"]["name"], "robust_seasonal_analog")
        self.assertEqual(report["history"]["hours"], 15 * 24)
        self.assertEqual(len(report["forecast"]["points"]), 24)
        self.assertIn("overall", report["candidate"])
        self.assertIn("horizon_bands", report["candidate"])
        self.assertEqual(set(report["baselines"]), {"persistence", "seasonal_24h", "seasonal_168h"})
        self.assertIn("short_horizon_wape_improvement", report["comparison"])
        self.assertIn("history_eligible", report["promotion_gates"])
        self.assertFalse(report["promotion_gates"]["history_eligible"])
        self.assertEqual(report["parameters"]["origin_step_hours"], 24)


if __name__ == "__main__":
    unittest.main()
