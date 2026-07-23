from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.sub2api.hourly_forecast import MODEL_VERSION, HourlyObservation
from app.modules.sub2api.hourly_forecast_service import get_or_create_group_hourly_forecast


NOW = datetime(2026, 7, 20, 12, 37, tzinfo=UTC)
AS_OF = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


class HourlyForecastServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_generates_twenty_five_points_and_persists_natural_hour_cache(self) -> None:
        collection = SimpleNamespace(find_one=AsyncMock(return_value=None), replace_one=AsyncMock())
        db = SimpleNamespace(sub2api_hourly_forecasts=collection)
        history = [
            HourlyObservation(
                bucket_at=AS_OF - timedelta(hours=56 * 24 - index),
                account_cost=10,
                requests=100,
                total_tokens=1000,
            )
            for index in range(56 * 24)
        ]
        fetcher = AsyncMock(return_value=history)

        result = await get_or_create_group_hourly_forecast(
            db,
            site_id="api-5001",
            group_id=3,
            sql_dsn="host=db user=u password=p dbname=d",
            now=NOW,
            observation_fetcher=fetcher,
        )

        self.assertEqual(result.as_of, AS_OF)
        self.assertEqual(len(result.points), 25)
        fetcher.assert_awaited_once_with(
            "host=db user=u password=p dbname=d",
            group_id=3,
            start_at=AS_OF - timedelta(days=56),
            end_at=AS_OF,
        )
        stored = collection.replace_one.await_args.args[1]
        self.assertEqual(stored["site_id"], "api-5001")
        self.assertEqual(stored["group_id"], 3)
        self.assertEqual(stored["as_of"], AS_OF)
        self.assertEqual(len(stored["points"]), 25)
        self.assertIn("surge_profiles", stored)

    async def test_reuses_cached_forecast_without_reading_postgres(self) -> None:
        cached = {
            "_id": "api-5001:3:2026-07-20T12:00:00Z",
            "site_id": "api-5001",
            "group_id": 3,
            "model": "robust_seasonal_analog",
            "version": MODEL_VERSION,
            "as_of": AS_OF.replace(tzinfo=None),
            "readiness": "provisional",
            "history_hours": 21 * 24,
            "completeness_ratio": 1.0,
            "points": [
                {
                    "horizon": index + 1,
                    "target_at": (AS_OF + timedelta(hours=index)).replace(tzinfo=None),
                    "p50": 10,
                    "p90": 20,
                    "candidate_count": 14,
                    "source": "analog",
                }
                for index in range(25)
            ],
            "surge_profiles": [
                {
                    "stage": "surge",
                    "event_count": 18,
                    "preferred_event_count": 9,
                    "confidence": 1.0,
                    "persistence_ratios": [0.8, 0.6, 0.4],
                    "source": "local_time_and_day_type",
                }
            ],
        }
        collection = SimpleNamespace(find_one=AsyncMock(return_value=cached), replace_one=AsyncMock())
        db = SimpleNamespace(sub2api_hourly_forecasts=collection)
        fetcher = AsyncMock()

        result = await get_or_create_group_hourly_forecast(
            db,
            site_id="api-5001",
            group_id=3,
            sql_dsn="host=db user=u password=p dbname=d",
            now=NOW,
            observation_fetcher=fetcher,
        )

        self.assertEqual(result.readiness, "provisional")
        self.assertEqual(result.points[0].p90, 20)
        self.assertEqual(result.surge_profiles[0].persistence_ratios, (0.8, 0.6, 0.4))
        fetcher.assert_not_awaited()
        collection.replace_one.assert_not_awaited()

    async def test_regenerates_cached_forecast_from_previous_model_version(self) -> None:
        cached = {
            "_id": "api-5001:3:2026-07-20T12:00:00Z",
            "model": "robust_seasonal_analog",
            "version": "legacy",
            "as_of": AS_OF,
            "readiness": "eligible",
            "history_hours": 56 * 24,
            "completeness_ratio": 1.0,
            "points": [
                {
                    "horizon": index + 1,
                    "target_at": AS_OF + timedelta(hours=index),
                    "p50": 10,
                    "p90": 20,
                    "candidate_count": 14,
                    "source": "analog",
                }
                for index in range(25)
            ],
        }
        collection = SimpleNamespace(find_one=AsyncMock(return_value=cached), replace_one=AsyncMock())
        db = SimpleNamespace(sub2api_hourly_forecasts=collection)
        history = [
            HourlyObservation(
                bucket_at=AS_OF - timedelta(hours=56 * 24 - index),
                account_cost=10,
                requests=100,
                total_tokens=1000,
            )
            for index in range(56 * 24)
        ]
        fetcher = AsyncMock(return_value=history)

        result = await get_or_create_group_hourly_forecast(
            db,
            site_id="api-5001",
            group_id=3,
            sql_dsn="host=db user=u password=p dbname=d",
            now=NOW,
            observation_fetcher=fetcher,
        )

        self.assertEqual(result.version, MODEL_VERSION)
        fetcher.assert_awaited_once()
        collection.replace_one.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
