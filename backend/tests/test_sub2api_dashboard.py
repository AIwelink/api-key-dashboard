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


if __name__ == "__main__":
    unittest.main()
