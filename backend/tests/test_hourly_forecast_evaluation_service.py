from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from app.modules.sub2api.hourly_forecast import HourlyObservation
from app.modules.sub2api import hourly_forecast_evaluation_service as service
from app.modules.system import bootstrap


NOW = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class EvaluationCollection:
    def __init__(self, existing: list[dict[str, object]] | None = None) -> None:
        self.existing = existing or []
        self.replace_one = AsyncMock()
        self.find_queries: list[dict[str, object]] = []

    def find(self, query, *_args, **_kwargs):
        self.find_queries.append(query)
        candidate_ids = set(query.get("_id", {}).get("$in", []))
        return AsyncCursor([item for item in self.existing if item.get("_id") in candidate_ids])


class ForecastEvaluationSettlementTests(unittest.IsolatedAsyncioTestCase):
    async def test_settles_hourly_and_nowcast_candidates_with_one_actual_range_read(self) -> None:
        evaluations = EvaluationCollection()
        db = SimpleNamespace(sub2api_forecast_evaluations=evaluations)
        forecast = {
            "_id": "api-5001:3:2026-07-20T11:00:00Z",
            "site_id": "api-5001",
            "group_id": 3,
            "model": "robust_seasonal_analog",
            "version": "1",
            "generated_at": datetime(2026, 7, 20, 11, 2, tzinfo=UTC),
            "as_of": datetime(2026, 7, 20, 11, tzinfo=UTC),
            "points": [
                {"horizon": 1, "target_at": datetime(2026, 7, 20, 11, tzinfo=UTC), "p50": 80, "p90": 100},
                {"horizon": 2, "target_at": datetime(2026, 7, 20, 12, tzinfo=UTC), "p50": 90, "p90": 110},
            ],
        }
        samples = [
            _nowcast_sample(datetime(2026, 7, 20, 11, 25, tzinfo=UTC), concurrency_coverage=0.8),
            _nowcast_sample(datetime(2026, 7, 20, 12, 25, tzinfo=UTC)),
        ]
        observations = [
            HourlyObservation(datetime(2026, 7, 20, 11, tzinfo=UTC), 100, 10, 1_000),
            HourlyObservation(datetime(2026, 7, 20, 12, tzinfo=UTC), 120, 12, 1_200),
        ]
        fetcher = AsyncMock(return_value=observations)

        with patch.object(service, "refresh_forecast_accuracy_summary", AsyncMock(return_value={"status": "ready"})):
            result = await service.settle_group_forecast_accuracy(
                db,
                site_id="api-5001",
                group_id=3,
                sql_dsn="host=db user=u password=p dbname=d",
                forecasts=[forecast],
                capacity_samples=samples,
                now=NOW,
                observation_fetcher=fetcher,
            )

        fetcher.assert_awaited_once_with(
            "host=db user=u password=p dbname=d",
            group_id=3,
            start_at=datetime(2026, 7, 20, 11, tzinfo=UTC),
            end_at=datetime(2026, 7, 20, 13, tzinfo=UTC),
        )
        self.assertEqual(result["settled"], 4)
        self.assertEqual(result["final"], 2)
        self.assertEqual(result["provisional"], 2)
        documents = [await_args.args[1] for await_args in evaluations.replace_one.await_args_list]
        by_id = {document["_id"]: document for document in documents}
        self.assertEqual(by_id["hourly:api-5001:3:2026-07-20T11:00:00Z:1"]["status"], "final")
        self.assertTrue(by_id["hourly:api-5001:3:2026-07-20T11:00:00Z:1"]["capacity_constrained"])
        self.assertEqual(by_id["hourly:api-5001:3:2026-07-20T11:00:00Z:1"]["pressure_stage"], "stable")
        self.assertEqual(by_id["hourly:api-5001:3:2026-07-20T11:00:00Z:2"]["status"], "provisional")
        self.assertEqual(by_id["nowcast:api-5001:3:2026-07-20T11:25:00Z"]["status"], "final")
        self.assertEqual(by_id["nowcast:api-5001:3:2026-07-20T12:25:00Z"]["status"], "provisional")

    async def test_skips_final_results_but_promotes_existing_provisional_result(self) -> None:
        forecast = {
            "_id": "api-5001:3:2026-07-20T11:00:00Z",
            "site_id": "api-5001",
            "group_id": 3,
            "model": "robust_seasonal_analog",
            "version": "1",
            "as_of": datetime(2026, 7, 20, 11, tzinfo=UTC),
            "points": [
                {"horizon": 1, "target_at": datetime(2026, 7, 20, 11, tzinfo=UTC), "p50": 80, "p90": 100},
                {"horizon": 2, "target_at": datetime(2026, 7, 20, 12, tzinfo=UTC), "p50": 90, "p90": 110},
            ],
        }
        final_id = "hourly:api-5001:3:2026-07-20T11:00:00Z:1"
        provisional_id = "hourly:api-5001:3:2026-07-20T11:00:00Z:2"
        evaluations = EvaluationCollection(
            [
                {"_id": final_id, "status": "final"},
                {"_id": provisional_id, "status": "provisional"},
            ]
        )
        db = SimpleNamespace(sub2api_forecast_evaluations=evaluations)
        fetcher = AsyncMock(
            return_value=[HourlyObservation(datetime(2026, 7, 20, 12, tzinfo=UTC), 120, 12, 1_200)]
        )

        with patch.object(service, "refresh_forecast_accuracy_summary", AsyncMock(return_value={"status": "ready"})):
            result = await service.settle_group_forecast_accuracy(
                db,
                site_id="api-5001",
                group_id=3,
                sql_dsn="dsn",
                forecasts=[forecast],
                capacity_samples=[],
                now=datetime(2026, 7, 20, 14, 31, tzinfo=UTC),
                observation_fetcher=fetcher,
            )

        self.assertEqual(result["settled"], 1)
        stored = evaluations.replace_one.await_args.args[1]
        self.assertEqual(stored["_id"], provisional_id)
        self.assertEqual(stored["status"], "final")


class ForecastAccuracyLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_reads_all_capacity_samples_for_constraint_context(self) -> None:
        forecast_collection = SimpleNamespace(find=lambda query: AsyncCursor([]))
        sample_queries: list[dict[str, object]] = []

        def find_samples(query):
            sample_queries.append(query)
            return AsyncCursor([])

        db = SimpleNamespace(
            sub2api_hourly_forecasts=forecast_collection,
            sub2api_capacity_samples=SimpleNamespace(find=find_samples),
        )

        await service.evaluate_forecast_accuracy_once(db, lookback=timedelta(hours=4), now=NOW)

        self.assertEqual(
            sample_queries,
            [{"bucket_at": {"$gte": NOW - timedelta(hours=4)}}],
        )

    async def test_loop_runs_immediate_backfill_before_sleep(self) -> None:
        evaluate = AsyncMock(return_value={"ok": True})
        sleep = AsyncMock(side_effect=asyncio.CancelledError)

        with (
            patch.object(service, "evaluate_forecast_accuracy_once", evaluate),
            patch.object(service.asyncio, "sleep", sleep),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await service.forecast_accuracy_evaluator_loop(object())

        evaluate.assert_awaited_once()
        self.assertEqual(evaluate.await_args.kwargs["lookback"], timedelta(days=7))
        sleep.assert_awaited_once()


class ForecastEvaluationIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_evaluation_indexes_cover_settlement_summary_and_retention(self) -> None:
        evaluations = SimpleNamespace(create_index=AsyncMock())
        summaries = SimpleNamespace(create_index=AsyncMock())
        db = SimpleNamespace(
            sub2api_forecast_evaluations=evaluations,
            sub2api_forecast_accuracy_summaries=summaries,
        )

        await bootstrap.ensure_forecast_evaluation_indexes(db)

        evaluations.create_index.assert_has_awaits(
            [
                call([("site_id", 1), ("group_id", 1), ("kind", 1), ("status", 1), ("target_at", -1)]),
                call([("model", 1), ("version", 1), ("status", 1), ("target_at", -1)]),
                call("expires_at", expireAfterSeconds=0),
            ]
        )
        summaries.create_index.assert_has_awaits(
            [
                call([("site_id", 1), ("group_id", 1)], unique=True),
                call([("updated_at", -1)]),
            ]
        )


def _nowcast_sample(sampled_at: datetime, *, concurrency_coverage: float | None = None) -> dict[str, object]:
    sample_id = f"api-5001:3:{sampled_at.replace(minute=25).isoformat().replace('+00:00', 'Z')}"
    metrics = {
        "forecast_model": "robust_seasonal_analog",
        "forecast_version": "1",
        "forecast_nowcast_applied": True,
        "forecast_current_hour_observed_usd": 40,
        "forecast_current_hour_model_remaining_usd": 60,
        "forecast_current_hour_realtime_remaining_usd": 70,
        "forecast_current_hour_selected_remaining_usd": 70,
        "pressure_stage": "stable",
    }
    if concurrency_coverage is not None:
        metrics["concurrency_coverage"] = concurrency_coverage
    return {
        "_id": sample_id,
        "site_id": "api-5001",
        "group_id": 3,
        "sampled_at": sampled_at,
        "metrics": metrics,
    }


if __name__ == "__main__":
    unittest.main()
