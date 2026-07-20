from __future__ import annotations

import unittest
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from app.modules.sub2api.hourly_forecast import (
    ForecastInputError,
    ForecastPoint,
    ForecastResult,
    HourlyObservation,
    apply_current_hour_nowcast,
    forecast_cost_over_window,
    forecast_hourly_demand,
    forecast_runway,
    weighted_quantile,
)


AS_OF = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)


def hourly_history(
    *,
    hours: int,
    cost: float = 100.0,
    requests: float = 1000.0,
    tokens: float = 1_000_000.0,
    as_of: datetime = AS_OF,
) -> list[HourlyObservation]:
    start = as_of - timedelta(hours=hours)
    return [
        HourlyObservation(
            bucket_at=start + timedelta(hours=index),
            account_cost=cost,
            requests=requests,
            total_tokens=tokens,
        )
        for index in range(hours)
    ]


class HourlyForecastTests(unittest.TestCase):
    def test_current_hour_nowcast_uses_realtime_spike_for_remaining_minutes(self) -> None:
        forecast = ForecastResult(
            model="test",
            version="1",
            as_of=AS_OF,
            readiness="eligible",
            history_hours=56 * 24,
            completeness_ratio=1.0,
            points=tuple(
                ForecastPoint(index + 1, AS_OF + timedelta(hours=index), 6, 10, 1, "test")
                for index in range(25)
            ),
        )

        nowcast = apply_current_hour_nowcast(
            forecast,
            now=AS_OF + timedelta(minutes=30),
            observed_current_hour_cost_usd=8,
            realtime_cost_per_hour=20,
        )

        self.assertTrue(nowcast.applied)
        self.assertEqual(nowcast.model_p90_remaining_usd, 2.0)
        self.assertEqual(nowcast.realtime_remaining_usd, 10.0)
        self.assertEqual(nowcast.selected_p90_remaining_usd, 10.0)
        self.assertEqual(
            forecast_cost_over_window(
                nowcast.forecast,
                now=AS_OF + timedelta(minutes=30),
                hours=0.5,
                quantile="p90",
            ),
            10.0,
        )

    def test_current_hour_nowcast_uses_model_residual_after_observed_cost(self) -> None:
        forecast = ForecastResult(
            model="test",
            version="1",
            as_of=AS_OF,
            readiness="eligible",
            history_hours=56 * 24,
            completeness_ratio=1.0,
            points=tuple(
                ForecastPoint(index + 1, AS_OF + timedelta(hours=index), 6, 10, 1, "test")
                for index in range(25)
            ),
        )

        nowcast = apply_current_hour_nowcast(
            forecast,
            now=AS_OF + timedelta(minutes=30),
            observed_current_hour_cost_usd=8,
            realtime_cost_per_hour=2,
        )

        self.assertEqual(nowcast.model_p90_remaining_usd, 2.0)
        self.assertEqual(nowcast.realtime_remaining_usd, 1.0)
        self.assertEqual(nowcast.selected_p90_remaining_usd, 2.0)
        self.assertEqual(
            forecast_cost_over_window(
                nowcast.forecast,
                now=AS_OF + timedelta(minutes=30),
                hours=0.5,
                quantile="p90",
            ),
            2.0,
        )

    def test_current_hour_nowcast_accepts_calibrated_selected_remaining(self) -> None:
        forecast = ForecastResult(
            model="test",
            version="1",
            as_of=AS_OF,
            readiness="eligible",
            history_hours=56 * 24,
            completeness_ratio=1.0,
            points=tuple(
                ForecastPoint(index + 1, AS_OF + timedelta(hours=index), 6, 10, 1, "test")
                for index in range(25)
            ),
        )

        nowcast = apply_current_hour_nowcast(
            forecast,
            now=AS_OF + timedelta(minutes=30),
            observed_current_hour_cost_usd=8,
            realtime_cost_per_hour=20,
            selected_remaining_usd=7,
        )

        self.assertEqual(nowcast.selected_p90_remaining_usd, 7)

    def test_runway_prorates_partial_natural_hours(self) -> None:
        forecast = ForecastResult(
            model="test",
            version="1",
            as_of=AS_OF,
            readiness="eligible",
            history_hours=56 * 24,
            completeness_ratio=1.0,
            points=tuple(
                ForecastPoint(
                    horizon=index + 1,
                    target_at=AS_OF + timedelta(hours=index),
                    p50=5,
                    p90=10,
                    candidate_count=1,
                    source="test",
                )
                for index in range(3)
            ),
        )

        result = forecast_runway(
            forecast,
            remaining_usd=10,
            now=AS_OF + timedelta(minutes=30),
            quantile="p90",
            max_hours=2,
        )

        self.assertEqual(result.hours, 1.0)
        self.assertFalse(result.capped)
        self.assertEqual(result.projected_cost_usd, 10.0)

    def test_runway_is_capped_when_budget_outlasts_forecast_window(self) -> None:
        forecast = ForecastResult(
            model="test",
            version="1",
            as_of=AS_OF,
            readiness="eligible",
            history_hours=56 * 24,
            completeness_ratio=1.0,
            points=tuple(
                ForecastPoint(index + 1, AS_OF + timedelta(hours=index), 5, 10, 1, "test")
                for index in range(3)
            ),
        )

        result = forecast_runway(
            forecast,
            remaining_usd=100,
            now=AS_OF + timedelta(minutes=30),
            quantile="p90",
            max_hours=2,
        )

        self.assertEqual(result.hours, 2.0)
        self.assertTrue(result.capped)
        self.assertEqual(result.projected_cost_usd, 20.0)
        self.assertEqual(
            forecast_cost_over_window(
                forecast,
                now=AS_OF + timedelta(minutes=30),
                hours=2,
                quantile="p90",
            ),
            20.0,
        )

    def test_forecasts_next_twenty_four_natural_hours(self) -> None:
        history = hourly_history(hours=14 * 24)

        result = forecast_hourly_demand(history, as_of=AS_OF)

        self.assertEqual(result.model, "robust_seasonal_analog")
        self.assertEqual(result.version, "1")
        self.assertEqual(result.readiness, "provisional")
        self.assertEqual(result.completeness_ratio, 1.0)
        self.assertEqual(len(result.points), 24)
        self.assertEqual(result.points[0].horizon, 1)
        self.assertEqual(result.points[0].target_at, AS_OF)
        self.assertEqual(result.points[-1].horizon, 24)
        self.assertEqual(result.points[-1].target_at, AS_OF + timedelta(hours=23))

    def test_classifies_limited_and_eligible_history(self) -> None:
        limited = forecast_hourly_demand(hourly_history(hours=7 * 24), as_of=AS_OF)
        eligible = forecast_hourly_demand(hourly_history(hours=56 * 24), as_of=AS_OF)

        self.assertEqual(limited.readiness, "limited")
        self.assertEqual(eligible.readiness, "eligible")

    def test_rejects_less_than_seven_days(self) -> None:
        with self.assertRaisesRegex(ForecastInputError, "seven complete days"):
            forecast_hourly_demand(hourly_history(hours=7 * 24 - 1), as_of=AS_OF)

    def test_rejects_non_hour_aligned_as_of(self) -> None:
        with self.assertRaisesRegex(ForecastInputError, "natural UTC hour"):
            forecast_hourly_demand(
                hourly_history(hours=14 * 24),
                as_of=AS_OF + timedelta(minutes=1),
            )

    def test_rejects_duplicate_and_future_buckets(self) -> None:
        history = hourly_history(hours=14 * 24)
        with self.assertRaisesRegex(ForecastInputError, "duplicate"):
            forecast_hourly_demand([*history, history[-1]], as_of=AS_OF)

        future = HourlyObservation(
            bucket_at=AS_OF,
            account_cost=1,
            requests=1,
            total_tokens=1,
        )
        with self.assertRaisesRegex(ForecastInputError, "before as_of"):
            forecast_hourly_demand([*history, future], as_of=AS_OF)

    def test_rejects_stale_latest_complete_hour(self) -> None:
        history = hourly_history(hours=14 * 24, as_of=AS_OF - timedelta(hours=3))

        with self.assertRaisesRegex(ForecastInputError, "latest complete hour"):
            forecast_hourly_demand(history, as_of=AS_OF)

    def test_rejects_history_below_ninety_five_percent_completeness(self) -> None:
        complete = hourly_history(hours=14 * 24)
        sparse = [
            item
            for index, item in enumerate(complete)
            if index in {0, len(complete) - 1} or index % 10 != 0
        ]

        with self.assertRaisesRegex(ForecastInputError, "95% complete"):
            forecast_hourly_demand(sparse, as_of=AS_OF)

    def test_constant_history_produces_constant_quantiles(self) -> None:
        result = forecast_hourly_demand(hourly_history(hours=14 * 24), as_of=AS_OF)

        self.assertTrue(all(point.p50 == 100 for point in result.points))
        self.assertTrue(all(point.p90 == 100 for point in result.points))
        self.assertTrue(all(point.candidate_count > 0 for point in result.points))

    def test_recent_multivariate_level_scales_analog_candidates(self) -> None:
        history = hourly_history(hours=14 * 24, cost=100, requests=1000, tokens=1_000_000)
        for index in range(len(history) - 3, len(history)):
            original = history[index]
            history[index] = HourlyObservation(
                bucket_at=original.bucket_at,
                account_cost=200,
                requests=2000,
                total_tokens=2_000_000,
            )

        result = forecast_hourly_demand(history, as_of=AS_OF)

        self.assertGreaterEqual(result.points[0].p50, 190)
        self.assertLessEqual(result.points[0].p50, 210)

    def test_long_inactive_gap_starts_a_new_effective_history_regime(self) -> None:
        history = hourly_history(hours=24 * 24)
        inactive_start = 7 * 24
        inactive_end = 10 * 24
        for index in range(inactive_start, inactive_end):
            item = history[index]
            history[index] = HourlyObservation(item.bucket_at, 0, 0, 0)

        result = forecast_hourly_demand(history, as_of=AS_OF)

        self.assertEqual(result.history_hours, 14 * 24)
        self.assertEqual(result.readiness, "provisional")

    def test_p90_is_never_below_p50(self) -> None:
        history = hourly_history(hours=21 * 24)
        for index, item in enumerate(history):
            day = index // 24
            history[index] = HourlyObservation(
                bucket_at=item.bucket_at,
                account_cost=50 + day * 10 + item.bucket_at.hour,
                requests=1000 + day * 20,
                total_tokens=1_000_000 + day * 10_000,
            )

        result = forecast_hourly_demand(history, as_of=AS_OF)

        self.assertTrue(all(point.p90 >= point.p50 >= 0 for point in result.points))

    def test_weighted_quantile_is_deterministic(self) -> None:
        values = [(10.0, 1.0), (20.0, 2.0), (30.0, 1.0)]

        self.assertEqual(weighted_quantile(values, 0.5), 20.0)
        self.assertEqual(weighted_quantile(values, 0.9), 30.0)

    def test_same_input_produces_identical_output(self) -> None:
        history = hourly_history(hours=14 * 24)

        first = forecast_hourly_demand(history, as_of=AS_OF)
        second = forecast_hourly_demand(history, as_of=AS_OF)

        self.assertEqual(asdict(first), asdict(second))


if __name__ == "__main__":
    unittest.main()
