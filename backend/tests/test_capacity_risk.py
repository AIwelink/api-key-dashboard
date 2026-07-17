from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.sub2api import capacity_risk


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def samples(
    values: list[float],
    *,
    rpm: float = 60,
    duration_ms: float = 1000,
    current_concurrency: float | None = 1,
    latest_at: datetime = NOW,
) -> list[dict[str, object]]:
    start = latest_at - timedelta(minutes=len(values) - 1)
    return [
        {
            "sampled_at": start + timedelta(minutes=index),
            "tpm": value,
            "rpm": rpm,
            "average_duration_ms": duration_ms,
            "current_concurrency": current_concurrency,
        }
        for index, value in enumerate(values)
    ]


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
        self.assertEqual(result["concurrency_coverage"], 2.0)

    def test_recorded_concurrency_replaces_rpm_duration_estimate(self) -> None:
        result = calculate(
            samples([1000] * 20, rpm=45, duration_ms=0.001, current_concurrency=5),
            safe_concurrency_available=100,
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["estimated_concurrency"], 5.0)
        self.assertEqual(result["concurrency_coverage"], 20.0)

    def test_zero_recorded_concurrency_has_no_coverage_multiplier(self) -> None:
        result = calculate(
            samples([1000] * 20, current_concurrency=0),
            safe_concurrency_available=100,
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["estimated_concurrency"], 0.0)
        self.assertIsNone(result["concurrency_coverage"])

    def test_healthy_when_runway_and_concurrency_targets_are_met(self) -> None:
        result = calculate(samples([1000] * 20))

        self.assertTrue(result["ready"])
        self.assertAlmostEqual(result["pressure_tpm"], 1000.0)
        self.assertAlmostEqual(result["burn_usd_per_hour"], 1.0)
        self.assertAlmostEqual(result["actual_runway_hours"], 1.5)
        self.assertAlmostEqual(result["dynamic_runway_hours"], 4.0)
        self.assertAlmostEqual(result["estimated_concurrency"], 1.0)
        self.assertAlmostEqual(result["concurrency_coverage"], 2.0)
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
