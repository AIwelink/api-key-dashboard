from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.routers import client_metrics as routes


class ClientMetricRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_returns_404_for_missing_site(self) -> None:
        db = SimpleNamespace(client_sites=SimpleNamespace(find_one=AsyncMock(return_value=None)))

        with self.assertRaises(HTTPException) as raised:
            await routes.get_site_metric_status("missing", _={}, db=db)

        self.assertEqual(raised.exception.status_code, 404)

    async def test_bad_metric_range_returns_400(self) -> None:
        db = SimpleNamespace(client_sites=SimpleNamespace(find_one=AsyncMock(return_value={"_id": "site"})))
        with patch.object(routes, "list_client_minute_metrics", AsyncMock(side_effect=ValueError("end_at must be after start_at"))):
            with self.assertRaises(HTTPException) as raised:
                await routes.get_site_minute_metrics(
                    "site",
                    start_at=datetime(2026, 7, 19, 1, 0, tzinfo=UTC),
                    end_at=datetime(2026, 7, 19, 1, 1, tzinfo=UTC),
                    limit=60,
                    _={},
                    db=db,
                )

        self.assertEqual(raised.exception.status_code, 400)

    async def test_manual_sample_uses_unified_sampler_and_audits(self) -> None:
        db = SimpleNamespace(client_sites=SimpleNamespace(find_one=AsyncMock(return_value={"_id": "site", "status": "active"})))
        actor = {"_id": "maintainer@example.com", "name": "Maintainer"}
        sample_result = {
            "ok": True,
            "site_id": "site",
            "quality": "complete",
            "rpm": 12,
            "tpm": 3456,
        }
        with (
            patch.object(routes, "sample_client_site", AsyncMock(return_value=sample_result)) as sample_mock,
            patch.object(routes, "write_audit_log", AsyncMock()) as audit_mock,
        ):
            result = await routes.post_site_metric_sample("site", actor=actor, db=db)

        self.assertEqual(result, sample_result)
        sample_mock.assert_awaited_once()
        audit_mock.assert_awaited_once()
        self.assertNotIn("api_key", str(result))


if __name__ == "__main__":
    unittest.main()
