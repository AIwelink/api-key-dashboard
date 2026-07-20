from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.sub2api import capacity_risk
from app.modules.sub2api.hourly_forecast import ForecastPoint, ForecastResult


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def samples(
    values: list[float],
    *,
    rpm: float = 60,
    duration_ms: float = 1000,
    current_concurrency: float | None = 1,
    latest_at: datetime = NOW,
    account_cost_per_minute: list[float] | None = None,
) -> list[dict[str, object]]:
    start = latest_at - timedelta(minutes=len(values) - 1)
    result = [
        {
            "sampled_at": start + timedelta(minutes=index),
            "tpm": value,
            "rpm": rpm,
            "average_duration_ms": duration_ms,
            "current_concurrency": current_concurrency,
            "account_cost_per_minute": account_cost_per_minute[index] if account_cost_per_minute else None,
        }
        for index, value in enumerate(values)
    ]
    return result


def calculate(
    sample_items: list[dict[str, object]],
    **overrides: object,
) -> dict[str, object]:
    params: dict[str, object] = {
        "samples": sample_items,
        "now": NOW,
        "cost_per_token": 1 / 60_000,
        "actual_five_hour_remaining_usd": 1.5,
        "dynamic_five_hour_remaining_usd": 4.0,
        "actual_seven_day_remaining_usd": 20.0,
        "dynamic_seven_day_remaining_usd": 20.0,
        "available_accounts": 10,
        "safe_concurrency_available": 2.0,
        "per_account_five_hour_usd": 2.0,
        "per_account_seven_day_usd": 10.0,
        "average_account_concurrency": 1.0,
    }
    params.update(overrides)
    return capacity_risk.calculate_capacity_risk(**params)


class CapacityRiskTests(unittest.TestCase):
    def test_latest_pool_sample_is_separate_from_pressure_forecast(self) -> None:
        result = calculate(samples([100] * 15 + [300] * 5, rpm=93))

        self.assertEqual(result["latest_tpm"], 300)
        self.assertEqual(result["latest_rpm"], 93)
        self.assertGreater(result["pressure_tpm"], result["latest_tpm"])

    def test_rpm_cost_channel_drives_realtime_burn_when_it_is_higher(self) -> None:
        result = calculate(
            samples([1000] * 20, rpm=120),
            cost_per_request=0.01,
        )

        self.assertEqual(result["realtime_tpm_burn_usd_per_hour"], 1.0)
        self.assertEqual(result["realtime_rpm_burn_usd_per_hour"], 72.0)
        self.assertEqual(result["realtime_burn_usd_per_hour"], 72.0)
        self.assertEqual(result["realtime_burn_source"], "rpm")

    def test_request_cost_can_make_realtime_risk_ready_without_token_cost(self) -> None:
        result = calculate(
            samples([1000] * 20, rpm=60),
            cost_per_token=None,
            cost_per_request=0.01,
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["realtime_burn_source"], "rpm")
        self.assertEqual(result["realtime_burn_usd_per_hour"], 36.0)

    def test_direct_account_cost_rate_has_priority_over_tpm_and_rpm_conversion(self) -> None:
        result = calculate(
            samples([1000] * 20, rpm=120, account_cost_per_minute=[2.0] * 20),
            cost_per_request=0.01,
        )

        self.assertEqual(result["realtime_direct_burn_usd_per_hour"], 120.0)
        self.assertEqual(result["realtime_burn_usd_per_hour"], 120.0)
        self.assertEqual(result["realtime_burn_source"], "direct_account_cost")

    def test_request_cost_without_rpm_samples_stays_pending(self) -> None:
        sample_items = samples([1000] * 20)
        for item in sample_items:
            item["rpm"] = None

        result = calculate(
            sample_items,
            cost_per_token=None,
            cost_per_request=0.01,
        )

        self.assertFalse(result["ready"])
        self.assertEqual(result["health_status"], "pending")

    def test_hourly_p50_forecast_drives_runway_while_p90_remains_risk_reference(self) -> None:
        demand_forecast = ForecastResult(
            model="robust_seasonal_analog",
            version="1",
            as_of=NOW,
            readiness="provisional",
            history_hours=21 * 24,
            completeness_ratio=1.0,
            points=tuple(
                ForecastPoint(index + 1, NOW + timedelta(hours=index), 0.5, 2.0, 14, "analog")
                for index in range(25)
            ),
        )

        result = calculate(samples([1000] * 20), demand_forecast=demand_forecast)

        self.assertEqual(result["runway_source"], "hourly_forecast_p50")
        self.assertEqual(result["forecast_status"], "active")
        self.assertEqual(result["forecast_readiness"], "provisional")
        self.assertEqual(result["burn_usd_per_hour"], 0.5)
        self.assertEqual(result["actual_runway_hours"], 3.0)
        self.assertEqual(result["dynamic_runway_hours"], 8.0)
        self.assertEqual(result["forecast_p50_runway_hours"], 8.0)
        self.assertEqual(result["forecast_p90_runway_hours"], 2.0)

    def test_expected_runway_is_not_collapsed_by_an_extreme_p90_current_hour(self) -> None:
        now = NOW + timedelta(minutes=30)
        demand_forecast = ForecastResult(
            model="robust_seasonal_analog",
            version="1",
            as_of=NOW,
            readiness="provisional",
            history_hours=21 * 24,
            completeness_ratio=1.0,
            points=tuple(
                ForecastPoint(index + 1, NOW + timedelta(hours=index), 600, 3000, 14, "analog")
                for index in range(25)
            ),
        )

        result = calculate(
            samples([10.0] * 20, latest_at=now, account_cost_per_minute=[10.0] * 20),
            now=now,
            demand_forecast=demand_forecast,
            actual_five_hour_remaining_usd=1000,
            dynamic_five_hour_remaining_usd=1000,
            actual_seven_day_remaining_usd=2000,
            dynamic_seven_day_remaining_usd=2000,
            current_hour_observed_cost_usd=300,
        )

        self.assertEqual(result["realtime_burn_usd_per_hour"], 600.0)
        self.assertAlmostEqual(result["actual_runway_hours"], 1000 / 600, places=4)
        self.assertAlmostEqual(result["dynamic_runway_hours"], 1000 / 600, places=4)
        self.assertLess(result["forecast_p90_runway_hours"], 0.2)
        self.assertEqual(result["runway_source"], "hourly_forecast_p50_nowcast")

    def test_incomplete_hourly_forecast_falls_back_to_tpm_runway(self) -> None:
        incomplete_forecast = ForecastResult(
            model="robust_seasonal_analog",
            version="1",
            as_of=NOW,
            readiness="limited",
            history_hours=7 * 24,
            completeness_ratio=1.0,
            points=(ForecastPoint(1, NOW, 0.5, 2.0, 7, "analog"),),
        )

        result = calculate(samples([1000] * 20), demand_forecast=incomplete_forecast)

        self.assertEqual(result["runway_source"], "tpm_pressure")
        self.assertEqual(result["forecast_status"], "fallback")
        self.assertEqual(result["dynamic_runway_hours"], 4.0)
        self.assertIn("cover", result["forecast_fallback_reason"])

    def test_current_hour_nowcast_replaces_uniform_proration_during_spike(self) -> None:
        now = NOW + timedelta(minutes=30)
        demand_forecast = ForecastResult(
            model="robust_seasonal_analog",
            version="1",
            as_of=NOW,
            readiness="provisional",
            history_hours=21 * 24,
            completeness_ratio=1.0,
            points=tuple(
                ForecastPoint(index + 1, NOW + timedelta(hours=index), 6, 10, 14, "analog")
                for index in range(25)
            ),
        )

        result = calculate(
            samples([1_000] * 15 + [20_000] * 5, latest_at=now),
            now=now,
            demand_forecast=demand_forecast,
            actual_five_hour_remaining_usd=10,
            dynamic_five_hour_remaining_usd=10,
            current_hour_observed_cost_usd=8,
        )

        self.assertTrue(result["forecast_nowcast_applied"])
        self.assertEqual(result["forecast_current_hour_model_remaining_usd"], 2.0)
        self.assertEqual(result["demand_regime_stage"], "surge")
        self.assertGreater(result["demand_regime_strength"], 0.5)
        self.assertGreater(result["forecast_nowcast_realtime_weight"], 0.8)
        self.assertGreater(
            result["forecast_current_hour_realtime_remaining_usd"],
            result["forecast_current_hour_model_remaining_usd"],
        )
        self.assertGreater(
            result["forecast_current_hour_candidate_remaining_usd"],
            result["forecast_current_hour_model_remaining_usd"],
        )
        self.assertLess(
            result["forecast_current_hour_candidate_remaining_usd"],
            result["forecast_current_hour_realtime_remaining_usd"],
        )
        self.assertEqual(
            result["forecast_current_hour_selected_remaining_usd"],
            result["forecast_current_hour_realtime_remaining_usd"],
        )
        self.assertEqual(result["forecast_nowcast_selector"], "current_max_v1")

    def test_stable_late_hour_exposes_candidate_but_keeps_v1_selector(self) -> None:
        now = NOW + timedelta(minutes=50)
        demand_forecast = ForecastResult(
            model="robust_seasonal_analog",
            version="1",
            as_of=NOW,
            readiness="provisional",
            history_hours=21 * 24,
            completeness_ratio=1.0,
            points=tuple(
                ForecastPoint(index + 1, NOW + timedelta(hours=index), 10, 20, 14, "analog")
                for index in range(25)
            ),
        )

        result = calculate(
            samples([1_000] * 20, latest_at=now),
            now=now,
            demand_forecast=demand_forecast,
            current_hour_observed_cost_usd=10,
        )

        self.assertEqual(result["demand_regime_stage"], "stable")
        self.assertEqual(result["forecast_nowcast_realtime_weight"], 1.0)
        self.assertLess(
            result["forecast_current_hour_candidate_remaining_usd"],
            result["forecast_current_hour_model_remaining_usd"],
        )
        self.assertEqual(
            result["forecast_current_hour_selected_remaining_usd"],
            result["forecast_current_hour_model_remaining_usd"],
        )

    def test_waits_for_fifteen_fresh_samples(self) -> None:
        result = calculate(samples([1000] * 14))

        self.assertFalse(result["ready"])
        self.assertEqual(result["health_status"], "pending")
        self.assertEqual(result["pressure_stage"], "waiting_data")

    def test_stale_latest_sample_waits_for_data(self) -> None:
        result = calculate(samples([1000] * 20, latest_at=NOW - timedelta(minutes=4)))

        self.assertFalse(result["ready"])
        self.assertEqual(result["health_status"], "pending")

    def test_sparse_samples_do_not_count_as_continuous_minute_data(self) -> None:
        sparse = [
            {
                "sampled_at": NOW - timedelta(minutes=(14 - index) * 10),
                "tpm": 1000,
                "rpm": 60,
                "average_duration_ms": 1000,
            }
            for index in range(15)
        ]

        result = calculate(sparse)

        self.assertFalse(result["ready"])

    def test_request_duration_is_not_used_for_concurrency_pressure(self) -> None:
        incomplete = samples([1000] * 20)
        for item in incomplete:
            item["average_duration_ms"] = None

        result = calculate(incomplete)

        self.assertTrue(result["ready"])
        self.assertEqual(result["estimated_concurrency"], 1.0)
        self.assertEqual(result["concurrency_coverage"], 3.0)

    def test_recorded_concurrency_replaces_rpm_duration_estimate(self) -> None:
        result = calculate(
            samples([1000] * 20, rpm=45, duration_ms=0.001, current_concurrency=5),
            safe_concurrency_available=100,
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["estimated_concurrency"], 5.0)
        self.assertEqual(result["concurrency_coverage"], 21.0)

    def test_zero_recorded_concurrency_has_no_coverage_multiplier(self) -> None:
        result = calculate(
            samples([1000] * 20, current_concurrency=0),
            safe_concurrency_available=100,
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["estimated_concurrency"], 0.0)
        self.assertIsNone(result["concurrency_coverage"])

    def test_zero_safe_spare_still_has_one_times_total_coverage(self) -> None:
        result = calculate(
            samples([1000] * 20, current_concurrency=5),
            safe_concurrency_available=0,
        )

        self.assertEqual(result["estimated_concurrency"], 5.0)
        self.assertEqual(result["concurrency_coverage"], 1.0)

    def test_healthy_when_runway_and_concurrency_targets_are_met(self) -> None:
        result = calculate(samples([1000] * 20))

        self.assertTrue(result["ready"])
        self.assertAlmostEqual(result["pressure_tpm"], 1000.0)
        self.assertAlmostEqual(result["burn_usd_per_hour"], 1.0)
        self.assertAlmostEqual(result["actual_runway_hours"], 1.5)
        self.assertAlmostEqual(result["dynamic_runway_hours"], 4.0)
        self.assertAlmostEqual(result["estimated_concurrency"], 1.0)
        self.assertAlmostEqual(result["concurrency_coverage"], 3.0)
        self.assertAlmostEqual(result["concurrency_target_coverage"], 2.2)
        self.assertEqual(result["health_status"], "healthy")
        self.assertEqual(result["pressure_stage"], "stable")
        self.assertFalse(result["replenishment_required"])
        self.assertEqual(result["recommended_refill_accounts"], 0)

    def test_three_hour_target_triggers_tight_refill(self) -> None:
        result = calculate(
            samples([1000] * 20),
            dynamic_five_hour_remaining_usd=2.0,
        )

        self.assertEqual(result["health_status"], "tight")
        self.assertTrue(result["replenishment_required"])
        self.assertEqual(result["recommended_refill_accounts"], 1)

    def test_one_hour_actual_target_and_concurrency_are_danger_gates(self) -> None:
        actual = calculate(samples([1000] * 20), actual_five_hour_remaining_usd=0.75)
        concurrency = calculate(samples([1000] * 20), safe_concurrency_available=0.8)

        self.assertEqual(actual["health_status"], "danger")
        self.assertEqual(concurrency["health_status"], "danger")

    def test_two_accounts_or_less_is_exhausted(self) -> None:
        result = calculate(samples([1000] * 20), available_accounts=2)

        self.assertEqual(result["health_status"], "exhausted")
        self.assertEqual(result["pressure_stage"], "peak_guard")

    def test_accelerating_tpm_sets_pressure_stage(self) -> None:
        result = calculate(samples([100] * 15 + [300] * 5))

        self.assertEqual(result["pressure_stage"], "accelerating")
        self.assertGreaterEqual(result["demand_ratio"], 1.5)

    def test_falling_demand_with_six_hours_runway_marks_inventory_risk(self) -> None:
        result = calculate(
            samples([300] * 15 + [100] * 5),
            actual_five_hour_remaining_usd=2.0,
            dynamic_five_hour_remaining_usd=10.0,
        )

        self.assertEqual(result["pressure_stage"], "inventory_risk")
        self.assertTrue(result["inventory_risk"])
        self.assertFalse(result["replenishment_required"])

    def test_confirmed_recovery_does_not_keep_using_old_two_hour_p90(self) -> None:
        result = calculate(
            samples([1000] * 15 + [100] * 5),
            dynamic_five_hour_remaining_usd=2.0,
        )

        self.assertLess(result["pressure_tpm"], 1000)
        self.assertIn(result["pressure_stage"], {"recovering", "inventory_risk"})

    def test_refill_count_uses_largest_quota_or_concurrency_gap(self) -> None:
        result = calculate(
            samples([1000] * 20),
            actual_five_hour_remaining_usd=0.5,
            dynamic_five_hour_remaining_usd=1.0,
            safe_concurrency_available=0.0,
        )

        self.assertEqual(result["quota_refill_accounts"], 1)
        self.assertEqual(result["concurrency_refill_accounts"], 2)
        self.assertEqual(result["recommended_refill_accounts"], 2)

    def test_refill_options_use_same_gap_with_each_account_type_limits(self) -> None:
        result = calculate(
            samples([1000] * 20),
            cost_per_token=1 / 1000,
            actual_five_hour_remaining_usd=0.0,
            dynamic_five_hour_remaining_usd=0.0,
            actual_seven_day_remaining_usd=0.0,
            dynamic_seven_day_remaining_usd=0.0,
            safe_concurrency_available=0.0,
            refill_account_options={
                "plus": {"five_hour_usd": 110.0, "seven_day_usd": 110.0},
                "k12": {"five_hour_usd": 20.0, "seven_day_usd": 100.0},
            },
            primary_refill_account_type="plus",
        )

        self.assertEqual(result["recommended_refill_accounts"], 2)
        self.assertEqual(
            result["recommended_refill_options"],
            {
                "plus": {
                    "account_type": "plus",
                    "quota_refill_accounts": 2,
                    "concurrency_refill_accounts": 2,
                    "recommended_refill_accounts": 2,
                },
                "k12": {
                    "account_type": "k12",
                    "quota_refill_accounts": 9,
                    "concurrency_refill_accounts": 2,
                    "recommended_refill_accounts": 9,
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
