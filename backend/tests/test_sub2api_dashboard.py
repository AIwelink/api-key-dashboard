from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.sub2api import dashboard


class DashboardRefreshConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_site_and_group_ranges_are_requested_in_parallel(self) -> None:
        started: set[tuple[int | None, str]] = set()
        all_started = asyncio.Event()

        async def get_snapshot(**params: object) -> dict[str, object]:
            key = (params.get("group_id"), str(params.get("granularity")))
            started.add(key)
            if len(started) == 4:
                all_started.set()
            await all_started.wait()
            return {"granularity": params.get("granularity")}

        async def store_snapshot(*_: object, group_id: int | None, range_type: str, **__: object) -> dict[str, object]:
            return {"ok": True, "group_id": group_id, "range_type": range_type, "trend_points": 1, "models": 0}

        client = AsyncMock()
        client.get_dashboard_snapshot.side_effect = get_snapshot
        db = SimpleNamespace(sub2api_dashboard_meta=SimpleNamespace(update_one=AsyncMock()))

        with patch.object(dashboard, "store_dashboard_snapshot", AsyncMock(side_effect=store_snapshot)):
            result = await asyncio.wait_for(
                dashboard.refresh_dashboard_snapshots(db, site_id="api-5001", client=client, force=True, group_ids=[3]),
                timeout=1,
            )

        self.assertEqual(started, {(None, "hour"), (None, "day"), (3, "hour"), (3, "day")})
        self.assertEqual(len(result["ranges"]), 4)
        self.assertEqual(result["trend_points"], 4)

    async def test_database_site_trends_replace_http_trends_but_keep_http_models(self) -> None:
        client = AsyncMock()
        client.get_dashboard_snapshot.side_effect = lambda **params: {
            "generated_at": "2026-07-19T00:05:00Z",
            "granularity": params["granularity"],
            "trend": [{"date": "http"}],
            "models": [{"model": "gpt-5"}],
        }
        database_snapshot = {
            "generated_at": "2026-07-19T00:00:00Z",
            "start_date": "2026-07-13",
            "end_date": "2026-07-19",
            "granularity": "hour",
            "trend": [{"date": "database"}],
            "models": [],
        }
        stored: list[tuple[int | None, str, dict[str, object]]] = []

        async def store(*_: object, group_id: int | None, range_type: str, snapshot: dict[str, object], **__: object):
            stored.append((group_id, range_type, snapshot))
            return {"group_id": group_id, "range_type": range_type, "trend_points": 1, "models": len(snapshot.get("models", []))}

        db = SimpleNamespace(sub2api_dashboard_meta=SimpleNamespace(update_one=AsyncMock()))
        with (
            patch.object(dashboard, "fetch_postgres_dashboard_snapshot", AsyncMock(return_value=database_snapshot)) as fetch_database,
            patch.object(dashboard, "store_dashboard_snapshot", AsyncMock(side_effect=store)),
        ):
            result = await dashboard.refresh_dashboard_snapshots(
                db,
                site_id="api-5001",
                client=client,
                force=True,
                group_ids=[3],
                sql_dsn="host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            )

        self.assertEqual(fetch_database.await_count, 2)
        site_snapshots = [snapshot for group_id, _, snapshot in stored if group_id is None]
        group_snapshots = [snapshot for group_id, _, snapshot in stored if group_id == 3]
        self.assertEqual([item["trend"] for item in site_snapshots], [[{"date": "database"}], [{"date": "database"}]])
        self.assertEqual([item["models"] for item in site_snapshots], [[{"model": "gpt-5"}], [{"model": "gpt-5"}]])
        self.assertTrue(all(item["_models_generated_at"] == "2026-07-19T00:05:00Z" for item in site_snapshots))
        self.assertEqual([item["trend"] for item in group_snapshots], [[{"date": "http"}], [{"date": "http"}]])
        self.assertEqual(result["site_trend_source"], "postgresql")

    async def test_database_trend_with_http_model_failure_is_partial_and_remains_due(self) -> None:
        client = AsyncMock()
        client.get_dashboard_snapshot.side_effect = RuntimeError("model endpoint unavailable")
        database_snapshot = {
            "generated_at": "2026-07-19T00:00:00Z",
            "start_date": "2026-07-13",
            "end_date": "2026-07-19",
            "granularity": "hour",
            "trend": [{"date": "database"}],
            "models": [],
        }
        db = SimpleNamespace(sub2api_dashboard_meta=SimpleNamespace(update_one=AsyncMock()))

        async def store(*_: object, group_id: int | None, range_type: str, **__: object):
            return {"group_id": group_id, "range_type": range_type, "trend_points": 1, "models": 0}

        with (
            patch.object(dashboard, "fetch_postgres_dashboard_snapshot", AsyncMock(return_value=database_snapshot)),
            patch.object(dashboard, "store_dashboard_snapshot", AsyncMock(side_effect=store)),
        ):
            result = await dashboard.refresh_dashboard_snapshots(
                db,
                site_id="api-5001",
                client=client,
                force=True,
                sql_dsn="host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["model_refresh_failed_ranges"], 2)
        self.assertIsNone(result["refreshed_at"])
        saved_meta = db.sub2api_dashboard_meta.update_one.await_args.args[1]["$set"]
        self.assertIsNone(saved_meta["refreshed_at"])

    async def test_database_site_trend_failure_falls_back_to_http(self) -> None:
        client = AsyncMock()
        client.get_dashboard_snapshot.return_value = {
            "granularity": "hour",
            "trend": [{"date": "http"}],
            "models": [],
        }
        stored: list[dict[str, object]] = []

        async def store(*_: object, snapshot: dict[str, object], **__: object):
            stored.append(snapshot)
            return {"trend_points": 1, "models": 0}

        db = SimpleNamespace(sub2api_dashboard_meta=SimpleNamespace(update_one=AsyncMock()))
        with (
            patch.object(dashboard, "fetch_postgres_dashboard_snapshot", AsyncMock(side_effect=RuntimeError("database unavailable"))),
            patch.object(dashboard, "store_dashboard_snapshot", AsyncMock(side_effect=store)),
        ):
            result = await dashboard.refresh_dashboard_snapshots(
                db,
                site_id="api-5001",
                client=client,
                force=True,
                sql_dsn="host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            )

        self.assertTrue(all(snapshot["trend"] == [{"date": "http"}] for snapshot in stored))
        self.assertEqual(result["site_trend_source"], "http_fallback")


class DashboardStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalized_dashboard_documents_do_not_repeat_raw_payloads(self) -> None:
        trends = SimpleNamespace(bulk_write=AsyncMock())
        models = SimpleNamespace(bulk_write=AsyncMock())
        snapshots = SimpleNamespace(replace_one=AsyncMock())
        db = SimpleNamespace(
            sub2api_dashboard_trends=trends,
            sub2api_dashboard_models=models,
            sub2api_dashboard_snapshots=snapshots,
        )
        snapshot = {
            "generated_at": "2026-07-18T06:53:00Z",
            "_models_generated_at": "2026-07-18T06:54:00Z",
            "granularity": "hour",
            "start_date": "2026-07-17",
            "end_date": "2026-07-18",
            "trend": [{"date": "2026-07-18 06:00:00", "requests": 12, "cost": 3.5, "unknown": "large"}],
            "models": [{"model": "gpt-5", "requests": 12, "cost": 3.5, "unknown": "large"}],
            "stats": {"requests": 12, "large_nested_payload": ["unused"] * 20},
        }

        await dashboard.store_dashboard_snapshot(
            db,
            site_id="api-5001",
            group_id=3,
            range_type="recent_hours",
            snapshot=snapshot,
        )

        trend_operation = trends.bulk_write.await_args.args[0][0]
        model_operation = models.bulk_write.await_args.args[0][0]
        self.assertNotIn("raw", trend_operation._doc)
        self.assertNotIn("raw", model_operation._doc)
        self.assertEqual(trend_operation._doc["generated_at"].isoformat(), "2026-07-18T06:53:00+00:00")
        self.assertEqual(model_operation._doc["generated_at"].isoformat(), "2026-07-18T06:54:00+00:00")
        snapshot_document = snapshots.replace_one.await_args.args[1]
        self.assertNotIn("raw", snapshot_document)
        self.assertEqual(snapshot_document["trend_points"], 1)


if __name__ == "__main__":
    unittest.main()
