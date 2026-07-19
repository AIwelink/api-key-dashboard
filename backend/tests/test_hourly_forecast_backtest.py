from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.sub2api.hourly_forecast import (
    ForecastPoint,
    ForecastResult,
    HourlyObservation,
    forecast_hourly_demand,
)
from app.modules.sub2api.hourly_forecast_backtest import (
    BacktestRecord,
    evaluate_records,
    forecast_persistence,
    forecast_seasonal_24h,
    forecast_seasonal_168h,
    horizon_band,
    rolling_origin_backtest,
)


START = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)


def history(hours: int, *, value: float = 100) -> list[HourlyObservation]:
    return [
        HourlyObservation(
            bucket_at=START + timedelta(hours=index),
            account_cost=value,
            requests=value * 10,
            total_tokens=value * 10_000,
        )
        for index in range(hours)
    ]


class BacktestMetricTests(unittest.TestCase):
    def test_calculates_documented_point_quantile_and_risk_metrics(self) -> None:
        records = [
            BacktestRecord(
                model="candidate",
                origin=START,
                horizon=1,
                target_at=START,
                actual=100,
                p50=90,
                p90=110,
            ),
            BacktestRecord(
                model="candidate",
                origin=START,
                horizon=2,
                target_at=START + timedelta(hours=1),
                actual=200,
                p50=220,
                p90=250,
            ),
        ]

        result = evaluate_records(records)["overall"]

        self.assertEqual(result["count"], 2)
        self.assertAlmostEqual(result["mae"], 15)
        self.assertAlmostEqual(result["wape"], 0.1)
        self.assertAlmostEqual(result["bias"], 1 / 30)
        self.assertAlmostEqual(result["p50_pinball"], 7.5)
        self.assertAlmostEqual(result["p90_pinball"], 3.0)
        self.assertAlmostEqual(result["p50_coverage"], 0.5)
        self.assertAlmostEqual(result["p90_coverage"], 1.0)
        self.assertAlmostEqual(result["p90_exceedance_rate"], 0.0)
        self.assertAlmostEqual(result["mean_interval_width"], 25.0)
        self.assertAlmostEqual(result["normalized_interval_width"], 1 / 6)
        self.assertAlmostEqual(result["risk_loss"], 0.2)

    def test_zero_actuals_do_not_divide_by_zero(self) -> None:
        record = BacktestRecord(
            model="candidate",
            origin=START,
            horizon=1,
            target_at=START,
            actual=0,
            p50=0,
            p90=0,
        )

        result = evaluate_records([record])["overall"]

        self.assertEqual(result["mae"], 0)
        self.assertIsNone(result["wape"])
        self.assertIsNone(result["bias"])
        self.assertIsNone(result["normalized_interval_width"])
        self.assertIsNone(result["risk_loss"])

    def test_groups_metrics_by_documented_horizon_bands(self) -> None:
        records = [
            BacktestRecord("candidate", START, horizon, START + timedelta(hours=horizon - 1), 100, 100, 110)
            for horizon in range(1, 25)
        ]

        result = evaluate_records(records)

        self.assertEqual(horizon_band(1), "1h")
        self.assertEqual(horizon_band(3), "2-3h")
        self.assertEqual(horizon_band(6), "4-6h")
        self.assertEqual(horizon_band(12), "7-12h")
        self.assertEqual(horizon_band(24), "13-24h")
        self.assertEqual(result["horizon_bands"]["1h"]["count"], 1)
        self.assertEqual(result["horizon_bands"]["2-3h"]["count"], 2)
        self.assertEqual(result["horizon_bands"]["4-6h"]["count"], 3)
        self.assertEqual(result["horizon_bands"]["7-12h"]["count"], 6)
        self.assertEqual(result["horizon_bands"]["13-24h"]["count"], 12)


class RollingBacktestTests(unittest.TestCase):
    def test_rolling_origins_never_pass_future_observations_to_forecaster(self) -> None:
        observations = history(10 * 24)
        seen: list[tuple[datetime, datetime]] = []

        def capturing_forecaster(items, *, as_of, horizons):
            seen.append((max(item.bucket_at for item in items), as_of))
            return forecast_hourly_demand(items, as_of=as_of, horizons=horizons)

        result = rolling_origin_backtest(
            observations,
            forecaster=capturing_forecaster,
            horizons=3,
            minimum_history_hours=7 * 24,
            origin_step_hours=12,
        )

        self.assertGreater(result.origins, 0)
        self.assertTrue(all(latest == origin - timedelta(hours=1) for latest, origin in seen))
        self.assertTrue(all(record.target_at >= record.origin for record in result.records))

    def test_persistence_baseline_repeats_latest_hour(self) -> None:
        observations = history(8 * 24)
        observations[-1] = HourlyObservation(observations[-1].bucket_at, 321, 1, 1)
        as_of = observations[-1].bucket_at + timedelta(hours=1)

        result = forecast_persistence(observations, as_of=as_of, horizons=3)

        self.assertEqual([point.p50 for point in result.points], [321, 321, 321])

    def test_daily_and_weekly_baselines_use_matching_historical_hours(self) -> None:
        observations = history(10 * 24)
        by_bucket = {item.bucket_at: item for item in observations}
        as_of = observations[-1].bucket_at + timedelta(hours=1)

        daily = forecast_seasonal_24h(observations, as_of=as_of, horizons=2)
        weekly = forecast_seasonal_168h(observations, as_of=as_of, horizons=2)

        self.assertEqual(daily.points[0].p50, by_bucket[as_of - timedelta(hours=24)].account_cost)
        self.assertEqual(daily.points[1].p50, by_bucket[as_of - timedelta(hours=23)].account_cost)
        self.assertEqual(weekly.points[0].p50, by_bucket[as_of - timedelta(hours=168)].account_cost)
        self.assertEqual(weekly.points[1].p50, by_bucket[as_of - timedelta(hours=167)].account_cost)

    def test_baseline_p90_uses_only_historical_residuals(self) -> None:
        observations = history(10 * 24)
        for index, item in enumerate(observations):
            observations[index] = HourlyObservation(
                item.bucket_at,
                100 + (index // 24) * 10,
                item.requests,
                item.total_tokens,
            )
        as_of = observations[-1].bucket_at + timedelta(hours=1)

        daily = forecast_seasonal_24h(observations, as_of=as_of, horizons=1)
        weekly = forecast_seasonal_168h(observations, as_of=as_of, horizons=1)

        self.assertGreater(daily.points[0].p90, daily.points[0].p50)
        self.assertGreater(weekly.points[0].p90, weekly.points[0].p50)

    def test_rolling_result_contains_operational_segments(self) -> None:
        result = rolling_origin_backtest(
            history(10 * 24),
            forecaster=forecast_persistence,
            horizons=3,
            minimum_history_hours=7 * 24,
            origin_step_hours=24,
        )

        self.assertIn("overall", result.metrics)
        self.assertIn("horizon_bands", result.metrics)
        self.assertIn("shanghai_hours", result.metrics)
        self.assertIn("day_types", result.metrics)
        self.assertIn("calendar_weeks", result.metrics)
        self.assertIn("traffic_stages", result.metrics)
        self.assertIn("data_quality", result.metrics)


if __name__ == "__main__":
    unittest.main()
