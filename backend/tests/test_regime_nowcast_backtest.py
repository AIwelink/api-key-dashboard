from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.sub2api.hourly_forecast import ForecastPoint, ForecastResult
from app.modules.sub2api.minute_forecast_repository import MinuteObservation
from app.modules.sub2api.regime_nowcast_backtest import (
    NowcastBacktestRecord,
    evaluate_nowcast_records,
    evaluate_surge_detections,
    rolling_minute_nowcast_backtest,
)


START = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)


def minute_history(hours: int, *, cost: float = 1.0) -> list[MinuteObservation]:
    return [
        MinuteObservation(
            bucket_at=START + timedelta(minutes=index),
            account_cost=cost,
            requests=10,
            total_tokens=1000,
        )
        for index in range(hours * 60)
    ]


def constant_forecaster(history, *, as_of, horizons):
    assert all(item.bucket_at < as_of for item in history)
    return ForecastResult(
        model="constant",
        version="1",
        as_of=as_of,
        readiness="provisional",
        history_hours=len(history),
        completeness_ratio=1.0,
        points=tuple(
            ForecastPoint(
                horizon=index + 1,
                target_at=as_of + timedelta(hours=index),
                p50=60,
                p90=72,
                candidate_count=1,
                source="test",
            )
            for index in range(horizons)
        ),
    )


class RegimeNowcastMetricTests(unittest.TestCase):
    def test_metrics_include_wape_bias_coverage_pinball_and_asymmetric_risk(self) -> None:
        records = [
            NowcastBacktestRecord("candidate", START, START + timedelta(hours=1), 10, 8, "stable", 0),
            NowcastBacktestRecord("candidate", START, START + timedelta(hours=1), 20, 22, "surge", 0),
        ]

        metrics = evaluate_nowcast_records(records)

        self.assertAlmostEqual(metrics["overall"]["wape"], 4 / 30)
        self.assertAlmostEqual(metrics["overall"]["bias"], 0.0)
        self.assertAlmostEqual(metrics["overall"]["coverage"], 0.5)
        self.assertAlmostEqual(metrics["overall"]["pinball"], 1.0)
        self.assertAlmostEqual(metrics["overall"]["risk_loss"], 12 / 30)
        self.assertIn("00-19", metrics["issue_minute_bands"])
        self.assertIn("stable", metrics["regime_stages"])

    def test_surge_detection_reports_event_recall_precision_and_delay(self) -> None:
        truth = [START + timedelta(minutes=30), START + timedelta(minutes=90)]
        detections = [START + timedelta(minutes=33), START + timedelta(minutes=96), START + timedelta(minutes=150)]

        metrics = evaluate_surge_detections(truth, detections, match_window_minutes=10)

        self.assertEqual(metrics["events"], 2)
        self.assertEqual(metrics["detected_events"], 2)
        self.assertAlmostEqual(metrics["recall"], 1.0)
        self.assertAlmostEqual(metrics["precision"], 2 / 3)
        self.assertEqual(metrics["median_detection_delay_minutes"], 4.5)


class RollingRegimeNowcastTests(unittest.TestCase):
    def test_compares_all_strategies_and_uses_only_data_before_issue_time(self) -> None:
        base = minute_history(4)
        changed = list(base)
        changed[2 * 60 + 6] = MinuteObservation(
            bucket_at=changed[2 * 60 + 6].bucket_at,
            account_cost=1000,
            requests=1000,
            total_tokens=1_000_000,
        )

        first = rolling_minute_nowcast_backtest(
            base,
            forecaster=constant_forecaster,
            minimum_history_hours=2,
            evaluation_start=START + timedelta(hours=2),
            issue_step_minutes=5,
        )
        second = rolling_minute_nowcast_backtest(
            changed,
            forecaster=constant_forecaster,
            minimum_history_hours=2,
            evaluation_start=START + timedelta(hours=2),
            issue_step_minutes=5,
        )

        self.assertEqual(
            set(first.strategies),
            {"current_max", "model_only", "realtime_only", "fixed_25", "fixed_50", "fixed_75", "regime_aware"},
        )
        first_issue = START + timedelta(hours=2, minutes=5)
        for strategy in first.strategies:
            first_record = next(item for item in first.strategies[strategy].records if item.issued_at == first_issue)
            second_record = next(item for item in second.strategies[strategy].records if item.issued_at == first_issue)
            self.assertEqual(first_record.predicted_remaining, second_record.predicted_remaining)
            self.assertNotEqual(first_record.actual_remaining, second_record.actual_remaining)

    def test_issues_only_inside_complete_hours_and_exposes_release_evidence(self) -> None:
        result = rolling_minute_nowcast_backtest(
            minute_history(4),
            forecaster=constant_forecaster,
            minimum_history_hours=2,
            evaluation_start=START + timedelta(hours=2),
            issue_step_minutes=15,
        )

        candidate = result.strategies["regime_aware"]
        self.assertEqual([item.issued_at.minute for item in candidate.records[:3]], [15, 30, 45])
        self.assertTrue(all(item.target_at == item.issued_at.replace(minute=0) + timedelta(hours=1) for item in candidate.records))
        self.assertIn("coverage", candidate.metrics["overall"])
        self.assertIn("median_detection_delay_minutes", result.surge_metrics)
        self.assertTrue(result.no_future_data)

    def test_contiguous_surge_truth_is_counted_as_one_event(self) -> None:
        observations = minute_history(3)
        for index in range(2 * 60, 2 * 60 + 10):
            observations[index] = MinuteObservation(
                bucket_at=observations[index].bucket_at,
                account_cost=5,
                requests=50,
                total_tokens=5000,
            )

        result = rolling_minute_nowcast_backtest(
            observations,
            forecaster=constant_forecaster,
            minimum_history_hours=1,
            evaluation_start=START + timedelta(hours=2),
            issue_step_minutes=15,
        )

        self.assertEqual(result.surge_metrics["events"], 1)

    def test_partial_first_hour_is_not_used_as_model_history(self) -> None:
        observations = [
            MinuteObservation(
                bucket_at=START + timedelta(minutes=30 + index),
                account_cost=1,
                requests=10,
                total_tokens=1000,
            )
            for index in range(3 * 60 + 30)
        ]
        training_sizes = []

        def recording_forecaster(history, *, as_of, horizons):
            training_sizes.append(len(history))
            return constant_forecaster(history, as_of=as_of, horizons=horizons)

        rolling_minute_nowcast_backtest(
            observations,
            forecaster=recording_forecaster,
            minimum_history_hours=2,
            evaluation_start=START + timedelta(hours=3),
            issue_step_minutes=15,
        )

        self.assertEqual(training_sizes, [2])


if __name__ == "__main__":
    unittest.main()
