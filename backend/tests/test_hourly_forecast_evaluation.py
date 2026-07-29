from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.sub2api.hourly_forecast_evaluation import (
    build_hourly_evaluation,
    build_nowcast_evaluation,
    summarize_forecast_accuracy,
)


NOW = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
TARGET_AT = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


class HourlyEvaluationTests(unittest.TestCase):
    def test_builds_hourly_result_with_calibration_metrics_and_local_dimensions(self) -> None:
        forecast = {
            "_id": "api-5001:3:2026-07-20T12:00:00Z",
            "site_id": "api-5001",
            "group_id": 3,
            "model": "robust_seasonal_analog",
            "version": "1",
            "generated_at": datetime(2026, 7, 20, 12, 4, tzinfo=UTC),
            "as_of": datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
        }
        point = {
            "horizon": 1,
            "target_at": TARGET_AT,
            "p50": 80,
            "p90": 120,
            "candidate_count": 14,
            "source": "analog",
        }

        result = build_hourly_evaluation(
            forecast,
            point,
            actual_account_cost=100,
            actual_requests=50,
            actual_total_tokens=1_000,
            evaluated_at=NOW,
            status="final",
        )

        self.assertEqual(result["_id"], "hourly:api-5001:3:2026-07-20T12:00:00Z:1")
        self.assertEqual(result["kind"], "hourly")
        self.assertEqual(result["error_p50"], -20)
        self.assertEqual(result["absolute_error_p50"], 20)
        self.assertEqual(result["error_p90"], 20)
        self.assertEqual(result["p90_covered"], True)
        self.assertEqual(result["pinball_loss_p50"], 10)
        self.assertEqual(result["pinball_loss_p90"], 2)
        self.assertEqual(result["target_local_hour"], 20)
        self.assertEqual(result["day_type"], "weekday")
        self.assertEqual(result["finalized_at"], NOW)
        self.assertEqual(result["expires_at"], TARGET_AT + timedelta(days=180))

    def test_builds_nowcast_result_for_every_candidate_channel(self) -> None:
        sample = {
            "_id": "api-5001:3:2026-07-20T12:25:00Z",
            "site_id": "api-5001",
            "group_id": 3,
            "sampled_at": datetime(2026, 7, 20, 12, 26, tzinfo=UTC),
            "metrics": {
                "forecast_model": "robust_seasonal_analog",
                "forecast_version": "1",
                "forecast_as_of": datetime(2026, 7, 20, 12, tzinfo=UTC),
                "forecast_nowcast_applied": True,
                "forecast_current_hour_observed_usd": 50,
                "forecast_current_hour_model_remaining_usd": 60,
                "forecast_current_hour_realtime_remaining_usd": 80,
                "forecast_current_hour_selected_remaining_usd": 80,
                "realtime_burn_source": "rpm",
                "pressure_stage": "accelerating",
                "concurrency_coverage": 0.8,
            },
        }

        result = build_nowcast_evaluation(
            sample,
            actual_account_cost=100,
            actual_requests=50,
            actual_total_tokens=1_000,
            evaluated_at=NOW,
            status="provisional",
        )

        self.assertEqual(result["_id"], f"nowcast:{sample['_id']}")
        self.assertEqual(result["actual_remaining"], 50)
        self.assertEqual(result["error_model_remaining"], 10)
        self.assertEqual(result["error_realtime_remaining"], 30)
        self.assertEqual(result["error_selected_remaining"], 30)
        self.assertEqual(result["absolute_error_selected_remaining"], 30)
        self.assertEqual(result["realtime_burn_source"], "rpm")
        self.assertEqual(result["pressure_stage"], "accelerating")
        self.assertTrue(result["capacity_constrained"])
        self.assertNotIn("finalized_at", result)


class AccuracySummaryTests(unittest.TestCase):
    def test_summarizes_rolling_hourly_and_nowcast_accuracy(self) -> None:
        hourly_one = _hourly_evaluation(
            evaluation_id="hourly:one",
            horizon=1,
            target_at=NOW - timedelta(hours=2),
            p50=80,
            p90=120,
            actual=100,
        )
        hourly_two = _hourly_evaluation(
            evaluation_id="hourly:two",
            horizon=3,
            target_at=NOW - timedelta(hours=1),
            p50=250,
            p90=190,
            actual=200,
        )
        nowcast = {
            "_id": "nowcast:one",
            "kind": "nowcast",
            "status": "final",
            "site_id": "api-5001",
            "group_id": 3,
            "model": "robust_seasonal_analog",
            "version": "1",
            "target_at": NOW - timedelta(hours=1),
            "actual_remaining": 50,
            "predicted_model_remaining": 60,
            "predicted_realtime_remaining": 80,
            "predicted_selected_remaining": 80,
            "error_model_remaining": 10,
            "error_realtime_remaining": 30,
            "error_selected_remaining": 30,
            "absolute_error_model_remaining": 10,
            "absolute_error_realtime_remaining": 30,
            "absolute_error_selected_remaining": 30,
            "evaluated_at": NOW,
            "finalized_at": NOW,
            "target_local_hour": 21,
            "day_type": "weekday",
            "pressure_stage": "stable",
            "capacity_constrained": False,
        }

        result = summarize_forecast_accuracy(
            [hourly_one, hourly_two, nowcast],
            site_id="api-5001",
            group_id=3,
            now=NOW,
        )

        window = result["windows"]["24h"]
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["model"], "robust_seasonal_analog")
        self.assertEqual(result["version"], "1")
        self.assertEqual(window["hourly_sample_count"], 2)
        self.assertEqual(window["nowcast_sample_count"], 1)
        self.assertAlmostEqual(window["p50_wape_percent"], 23.333333, places=5)
        self.assertAlmostEqual(window["p50_bias_percent"], 10, places=5)
        self.assertAlmostEqual(window["p50_mae_usd"], 35, places=5)
        self.assertAlmostEqual(window["p90_coverage_percent"], 50, places=5)
        self.assertAlmostEqual(window["p90_pinball_loss_usd"], 5.5, places=5)
        self.assertAlmostEqual(window["nowcast_selected_wape_percent"], 60, places=5)
        self.assertAlmostEqual(window["nowcast_model_wape_percent"], 20, places=5)
        self.assertAlmostEqual(window["nowcast_realtime_wape_percent"], 60, places=5)
        buckets = {item["key"]: item for item in window["horizon_buckets"]}
        self.assertEqual(buckets["1h"]["sample_count"], 1)
        self.assertEqual(buckets["2-3h"]["sample_count"], 1)
        self.assertEqual(window["segments"]["day_types"]["weekday"]["sample_count"], 2)
        self.assertEqual(result["last_finalized_at"], NOW)

    def test_ignores_nonfinal_and_previous_model_results(self) -> None:
        current = _hourly_evaluation(
            evaluation_id="hourly:current",
            horizon=1,
            target_at=NOW - timedelta(hours=1),
            p50=90,
            p90=110,
            actual=100,
        )
        current["issued_at"] = NOW - timedelta(hours=1)
        provisional = {**current, "_id": "hourly:provisional", "status": "provisional"}
        previous = {
            **current,
            "_id": "hourly:previous",
            "version": "0",
            "issued_at": NOW - timedelta(hours=3),
        }

        result = summarize_forecast_accuracy(
            [previous, provisional, current],
            site_id="api-5001",
            group_id=3,
            now=NOW,
        )

        self.assertEqual(result["version"], "1")
        self.assertEqual(result["windows"]["24h"]["hourly_sample_count"], 1)


def _hourly_evaluation(
    *,
    evaluation_id: str,
    horizon: int,
    target_at: datetime,
    p50: float,
    p90: float,
    actual: float,
) -> dict[str, object]:
    error_p50 = p50 - actual
    error_p90 = p90 - actual
    return {
        "_id": evaluation_id,
        "kind": "hourly",
        "status": "final",
        "site_id": "api-5001",
        "group_id": 3,
        "model": "robust_seasonal_analog",
        "version": "1",
        "horizon": horizon,
        "target_at": target_at,
        "predicted_p50": p50,
        "predicted_p90": p90,
        "actual_account_cost": actual,
        "error_p50": error_p50,
        "error_p90": error_p90,
        "absolute_error_p50": abs(error_p50),
        "absolute_error_p90": abs(error_p90),
        "p90_covered": actual <= p90,
        "pinball_loss_p50": 0.5 * abs(error_p50),
        "pinball_loss_p90": 0.9 * (actual - p90) if actual >= p90 else 0.1 * (p90 - actual),
        "evaluated_at": NOW,
        "finalized_at": NOW,
        "target_local_hour": target_at.astimezone().hour,
        "day_type": "weekday",
        "capacity_constrained": False,
    }


if __name__ == "__main__":
    unittest.main()
