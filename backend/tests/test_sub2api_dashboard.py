from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.sub2api import dashboard


class DashboardDatabaseSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_uses_postgres_only_and_does_not_write_remote_datasets_to_mongo(self) -> None:
        client = AsyncMock()
        db = SimpleNamespace(
            sub2api_dashboard_meta=SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock()),
            sub2api_dashboard_trends=SimpleNamespace(bulk_write=AsyncMock()),
            sub2api_dashboard_models=SimpleNamespace(bulk_write=AsyncMock()),
            sub2api_dashboard_snapshots=SimpleNamespace(replace_one=AsyncMock()),
        )
        site_snapshot = {
            "generated_at": datetime(2026, 7, 18, 1, 5, tzinfo=UTC),
            "granularity": "hour",
            "start_date": "2026-07-18",
            "end_date": "2026-07-18",
            "trend": [{"date": "2026-07-18 09:00", "requests": 2, "cost": 1.5}],
            "models": [],
        }
        group_snapshot = {**site_snapshot, "trend": [{"date": "2026-07-18 09:00", "requests": 1, "cost": 1.0}]}
        fetch_site = AsyncMock(return_value=site_snapshot)
        fetch_group = AsyncMock(return_value=group_snapshot)
        fetch_models = AsyncMock(return_value=[{"model": "gpt-5.4", "requests": 2, "cost": 1.5}])

        with (
            patch.object(dashboard, "fetch_postgres_dashboard_snapshot", fetch_site),
            patch.object(dashboard, "fetch_postgres_group_dashboard_snapshot", fetch_group),
            patch.object(dashboard, "fetch_postgres_model_statistics", fetch_models),
        ):
            result = await dashboard.refresh_dashboard_snapshots(
                db,
                site_id="api-5001",
                client=client,
                force=True,
                group_ids=[3],
                sql_dsn="host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["site_trend_source"], "postgresql")
        self.assertEqual(fetch_site.await_count, 2)
        self.assertEqual(fetch_group.await_count, 2)
        self.assertEqual(fetch_models.await_count, 2)
        client.get_dashboard_snapshot.assert_not_awaited()
        db.sub2api_dashboard_trends.bulk_write.assert_not_awaited()
        db.sub2api_dashboard_models.bulk_write.assert_not_awaited()
        db.sub2api_dashboard_snapshots.replace_one.assert_not_awaited()
        db.sub2api_dashboard_meta.update_one.assert_awaited_once()

    async def test_refresh_without_sql_dsn_fails_instead_of_falling_back_to_http(self) -> None:
        client = AsyncMock()
        db = SimpleNamespace(sub2api_dashboard_meta=SimpleNamespace(find_one=AsyncMock(), update_one=AsyncMock()))

        with self.assertRaisesRegex(ValueError, "SQL_DSN"):
            await dashboard.refresh_dashboard_snapshots(
                db,
                site_id="api-5001",
                client=client,
                force=True,
                sql_dsn=None,
            )

        client.get_dashboard_snapshot.assert_not_awaited()


class DashboardReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_read_queries_postgres_instead_of_mongo_mirrors(self) -> None:
        db = SimpleNamespace(
            sub2api_sites=SimpleNamespace(
                find_one=AsyncMock(
                    return_value={
                        "_id": "api-5001",
                        "site_type": "sub2api",
                        "sql_dsn": "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
                    }
                )
            ),
            sub2api_dashboard_meta=SimpleNamespace(find_one=AsyncMock(return_value={"_id": "api-5001"})),
            sub2api_dashboard_trends=SimpleNamespace(find=AsyncMock()),
            sub2api_dashboard_models=SimpleNamespace(find=AsyncMock()),
            sub2api_dashboard_snapshots=SimpleNamespace(find=AsyncMock()),
        )
        hourly = {
            "generated_at": datetime(2026, 7, 18, 1, 5, tzinfo=UTC),
            "granularity": "hour",
            "start_date": "2026-07-12",
            "end_date": "2026-07-18",
            "trend": [{"date": "2026-07-18 09:00", "requests": 12, "total_tokens": 100, "cost": 3.5}],
            "models": [],
        }
        daily = {**hourly, "granularity": "day", "trend": [{"date": "2026-07-18", "requests": 12, "total_tokens": 100, "cost": 3.5}]}
        fetch_site = AsyncMock(side_effect=[hourly, daily])
        fetch_models = AsyncMock(side_effect=[[{"model": "gpt-5.4", "cost": 3.5}], [{"model": "gpt-5.4", "cost": 9.5}]])

        with (
            patch.object(dashboard, "fetch_postgres_dashboard_snapshot", fetch_site),
            patch.object(dashboard, "fetch_postgres_model_statistics", fetch_models),
        ):
            result = await dashboard.get_stored_dashboard_snapshots(db, site_id="api-5001")

        self.assertEqual(result["hourly_trend"][0]["bucket"], "2026-07-18 09:00")
        self.assertEqual(result["daily_trend"][0]["bucket"], "2026-07-18")
        self.assertEqual(result["recent_models"][0]["model"], "gpt-5.4")
        self.assertEqual(result["weekly_models"][0]["cost"], 9.5)
        db.sub2api_dashboard_trends.find.assert_not_called()
        db.sub2api_dashboard_models.find.assert_not_called()
        db.sub2api_dashboard_snapshots.find.assert_not_called()


class DashboardRangeTests(unittest.TestCase):
    def test_ranges_cover_hourly_and_daily_windows(self) -> None:
        ranges = dashboard.dashboard_snapshot_ranges(datetime(2026, 7, 18, 1, 0, tzinfo=UTC))

        self.assertEqual([item["range_type"] for item in ranges], ["recent_hours", "last_7d"])
        self.assertEqual([item["params"]["granularity"] for item in ranges], ["hour", "day"])


if __name__ == "__main__":
    unittest.main()
