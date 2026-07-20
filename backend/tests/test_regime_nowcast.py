from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.sub2api.regime_nowcast import (
    detect_demand_regime,
    estimate_direct_cost_per_minute,
    select_nowcast_remaining,
)


NOW = datetime(2026, 7, 20, 12, 30, tzinfo=UTC)


def samples(cost_rates: list[float], *, tpm_rates: list[float] | None = None) -> list[dict[str, object]]:
    tpm_rates = tpm_rates or [value * 1_000 for value in cost_rates]
    start = NOW - timedelta(minutes=len(cost_rates) - 1)
    return [
        {
            "sampled_at": start + timedelta(minutes=index),
            "account_cost_per_minute": cost,
            "tpm": tpm_rates[index],
            "rpm": max(1.0, tpm_rates[index] / 1_000),
        }
        for index, cost in enumerate(cost_rates)
    ]


class DemandRegimeTests(unittest.TestCase):
    def test_constant_direct_cost_is_stable(self) -> None:
        result = detect_demand_regime(samples([10.0] * 30))

        self.assertEqual(result.stage, "stable")
        self.assertEqual(result.cost_source, "direct_account_cost")
        self.assertEqual(result.signal_count, 0)
        self.assertLess(result.strength, 0.1)

    def test_single_recent_jump_only_enters_warming(self) -> None:
        result = detect_demand_regime(samples([10.0] * 29 + [25.0]))

        self.assertEqual(result.stage, "warming")
        self.assertEqual(result.signal_count, 1)

    def test_two_of_three_confirmed_jumps_enter_surge(self) -> None:
        result = detect_demand_regime(samples([10.0] * 27 + [20.0, 25.0, 30.0]))

        self.assertEqual(result.stage, "surge")
        self.assertGreaterEqual(result.signal_count, 2)
        self.assertGreater(result.strength, 0.5)
        self.assertGreater(result.confidence, 0.5)

    def test_confirmed_drop_enters_cooling(self) -> None:
        result = detect_demand_regime(samples([30.0] * 20 + [10.0] * 10))

        self.assertEqual(result.stage, "cooling")

    def test_direct_cost_prevents_tpm_only_spike_from_becoming_confirmed_surge(self) -> None:
        result = detect_demand_regime(
            samples(
                [10.0] * 30,
                tpm_rates=[10_000.0] * 27 + [30_000.0, 40_000.0, 50_000.0],
            )
        )

        self.assertNotEqual(result.stage, "surge")

    def test_tpm_is_used_when_direct_cost_has_not_accumulated(self) -> None:
        sample_items = samples([10.0] * 27 + [20.0, 25.0, 30.0])
        for item in sample_items:
            item["account_cost_per_minute"] = None

        result = detect_demand_regime(sample_items)

        self.assertEqual(result.stage, "surge")
        self.assertEqual(result.cost_source, "tpm_fallback")


class DynamicSelectionTests(unittest.TestCase):
    def test_direct_cost_rate_uses_sensitive_average_only_during_rise(self) -> None:
        sample_items = samples([10.0] * 27 + [20.0, 25.0, 30.0])

        stable_rate = estimate_direct_cost_per_minute(sample_items, stage="stable")
        surge_rate = estimate_direct_cost_per_minute(sample_items, stage="surge")

        self.assertIsNotNone(stable_rate)
        self.assertIsNotNone(surge_rate)
        self.assertGreater(surge_rate, stable_rate)

    def test_direct_cost_rate_requires_five_valid_samples(self) -> None:
        sample_items = samples([10.0] * 4)

        self.assertIsNone(estimate_direct_cost_per_minute(sample_items, stage="stable"))

    def test_stable_mid_hour_blends_instead_of_taking_maximum(self) -> None:
        result = select_nowcast_remaining(
            model_remaining=2.0,
            realtime_remaining=10.0,
            minute=30,
            stage="stable",
            surge_strength=0.0,
        )

        self.assertAlmostEqual(result.realtime_weight, 30 / 45)
        self.assertAlmostEqual(result.selected_remaining, 2 * (1 - 30 / 45) + 10 * (30 / 45))
        self.assertLess(result.selected_remaining, 10.0)

    def test_confirmed_surge_increases_realtime_weight_without_exceeding_realtime_prediction(self) -> None:
        result = select_nowcast_remaining(
            model_remaining=2.0,
            realtime_remaining=10.0,
            minute=30,
            stage="surge",
            surge_strength=1.0,
        )

        self.assertAlmostEqual(result.realtime_weight, 11 / 12)
        self.assertGreater(result.selected_remaining, 9.0)
        self.assertLessEqual(result.selected_remaining, 10.0)


if __name__ == "__main__":
    unittest.main()
