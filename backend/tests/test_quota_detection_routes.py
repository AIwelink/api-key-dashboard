from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from fastapi import HTTPException

from app.modules.system import bootstrap
from app.routers import api_pools


class QuotaDetectionIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_indexes_include_ttl_and_summary_queries(self) -> None:
        db = SimpleNamespace(
            sub2api_quota_detection_states=SimpleNamespace(create_index=AsyncMock()),
            sub2api_quota_limit_samples=SimpleNamespace(create_index=AsyncMock()),
            sub2api_quota_limit_daily_rollups=SimpleNamespace(create_index=AsyncMock()),
            sub2api_quota_limit_profiles=SimpleNamespace(create_index=AsyncMock()),
            sub2api_account_health_analyses=SimpleNamespace(create_index=AsyncMock()),
        )

        await bootstrap.ensure_quota_detection_indexes(db)

        db.sub2api_quota_detection_states.create_index.assert_any_await("expires_at", expireAfterSeconds=0)
        db.sub2api_quota_limit_samples.create_index.assert_any_await("expires_at", expireAfterSeconds=0)
        db.sub2api_quota_limit_samples.create_index.assert_any_await(
            [("site_id", 1), ("account_type", 1), ("window_type", 1), ("hit_at", -1)]
        )
        db.sub2api_quota_limit_daily_rollups.create_index.assert_any_await(
            [("site_id", 1), ("account_type", 1), ("window_type", 1), ("generation", 1), ("local_date", 1)],
            unique=True,
        )
        db.sub2api_account_health_analyses.create_index.assert_any_await("expires_at", expireAfterSeconds=0)


class QuotaDetectionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_site_returns_404(self) -> None:
        with patch.object(api_pools, "get_site", AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as raised:
                await api_pools.get_quota_detection(site_id="missing", _={}, db=object())
        self.assertEqual(raised.exception.status_code, 404)

    async def test_route_returns_site_summary(self) -> None:
        summary = {"site_id": "api-5001", "items": []}
        health = {"site_id": "api-5001", "periods": {}, "stale": False}
        with (
            patch.object(api_pools, "get_site", AsyncMock(return_value={"id": "api-5001"})),
            patch.object(api_pools, "get_quota_detection_summary", AsyncMock(return_value=summary)) as service,
            patch.object(api_pools, "get_account_health_analysis", AsyncMock(return_value=health)) as health_service,
        ):
            result = await api_pools.get_quota_detection(site_id="api-5001", _={}, db=object())
        self.assertEqual(result, {**summary, "account_health_analysis": health})
        service.assert_awaited_once_with(ANY, "api-5001")
        health_service.assert_awaited_once_with(ANY, "api-5001")

    async def test_health_analysis_failure_does_not_hide_quota_detection(self) -> None:
        summary = {"site_id": "api-5001", "items": []}
        with (
            patch.object(api_pools, "get_site", AsyncMock(return_value={"id": "api-5001"})),
            patch.object(api_pools, "get_quota_detection_summary", AsyncMock(return_value=summary)),
            patch.object(
                api_pools,
                "get_account_health_analysis",
                AsyncMock(side_effect=RuntimeError("analysis unavailable")),
            ),
        ):
            result = await api_pools.get_quota_detection(site_id="api-5001", _={}, db=object())

        self.assertEqual(result["items"], [])
        self.assertEqual(
            result["account_health_analysis"],
            {"site_id": "api-5001", "periods": {}, "stale": True},
        )


if __name__ == "__main__":
    unittest.main()
