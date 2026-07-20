from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.sub2api.hourly_forecast import ForecastPoint, ForecastResult
from app.modules.sub2api.minute_forecast_repository import MinuteObservation
from app.modules.sub2api.regime_nowcast_backtest import rolling_minute_nowcast_backtest
from scripts.backtest_group_regime_nowcast import build_report


START = datetime(2026, 7, 20, tzinfo=UTC)


def forecaster(history, *, as_of, horizons):
    return ForecastResult(
        model="test",
        version="1",
        as_of=as_of,
        readiness="provisional",
        history_hours=len(history),
        completeness_ratio=1.0,
        points=tuple(
            ForecastPoint(index + 1, as_of + timedelta(hours=index), 60, 72, 1, "test")
            for index in range(horizons)
        ),
    )


class RegimeNowcastBacktestScriptTests(unittest.TestCase):
    def test_report_contains_strategy_comparison_and_release_gates(self) -> None:
        history = [
            MinuteObservation(
                bucket_at=START + timedelta(minutes=index),
                account_cost=1,
                requests=10,
                total_tokens=1000,
            )
            for index in range(4 * 60)
        ]
        evaluation_start = START + timedelta(hours=2)
        backtest = rolling_minute_nowcast_backtest(
            history,
            forecaster=forecaster,
            minimum_history_hours=2,
            evaluation_start=evaluation_start,
            issue_step_minutes=15,
        )

        report = build_report(
            site_id="us06-5001",
            group_id=3,
            history=history,
            backtest=backtest,
            evaluation_start=evaluation_start,
            issue_step_minutes=15,
        )

        self.assertEqual(report["schema_version"], "regime_nowcast_backtest.v1")
        self.assertEqual(report["site_id"], "us06-5001")
        self.assertEqual(report["group_id"], 3)
        self.assertEqual(set(report["strategies"]), set(backtest.strategies))
        self.assertIn("risk_loss_improvement_vs_current_max", report["comparison"])
        self.assertIn("wape_change_vs_realtime_only", report["comparison"])
        self.assertIn("p90_calibrated", report["promotion_gates"])
        self.assertIn("surge_detection_timely", report["promotion_gates"])
        self.assertFalse(report["promotion_gates"]["eligible_for_v2"])
        self.assertEqual(report["parameters"]["issue_step_minutes"], 15)


if __name__ == "__main__":
    unittest.main()
