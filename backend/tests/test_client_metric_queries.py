from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from app.modules.client_metrics.queries import get_client_metric_status, list_client_minute_metrics


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, value: int):
        self.items = self.items[:value]
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class ClientMetricQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_range_reports_complete_missing_and_gap_minutes(self) -> None:
        start = datetime(2026, 7, 19, 1, 0, tzinfo=UTC)
        end = datetime(2026, 7, 19, 1, 4, tzinfo=UTC)
        captured: dict[str, object] = {}
        documents = [
            {
                "_id": "site:01:00",
                "site_id": "site",
                "bucket_at": datetime(2026, 7, 19, 1, 0),
                "rpm": 10,
                "tpm": 1000,
                "quality": "complete",
            },
            {
                "_id": "site:01:01",
                "site_id": "site",
                "bucket_at": datetime(2026, 7, 19, 1, 1, tzinfo=UTC),
                "rpm": None,
                "tpm": None,
                "quality": "missing",
            },
            {
                "_id": "site:01:03",
                "site_id": "site",
                "bucket_at": datetime(2026, 7, 19, 1, 3, tzinfo=UTC),
                "rpm": 0,
                "tpm": 0,
                "quality": "complete",
            },
        ]

        def find(query, projection):
            captured["query"] = query
            captured["projection"] = projection
            return AsyncCursor(documents)

        db = SimpleNamespace(client_minute_metrics=SimpleNamespace(find=find))

        result = await list_client_minute_metrics(
            db,
            site_id="site",
            start_at=start,
            end_at=end,
            limit=100,
        )

        self.assertEqual(result["total_minutes"], 4)
        self.assertEqual(result["complete_minutes"], 2)
        self.assertEqual(result["missing_minutes"], 2)
        self.assertEqual(result["gap_minutes"], 1)
        self.assertEqual(result["completeness_ratio"], 0.5)
        self.assertEqual(len(result["items"]), 3)
        self.assertIsNone(result["items"][1]["rpm"])
        self.assertEqual(captured["query"]["bucket_at"], {"$gte": start, "$lt": end})
        self.assertEqual(captured["projection"].get("source"), 1)

    async def test_range_rejects_naive_reversed_and_excessive_ranges(self) -> None:
        db = SimpleNamespace(client_minute_metrics=SimpleNamespace())
        valid = datetime(2026, 7, 19, 1, 0, tzinfo=UTC)

        with self.assertRaisesRegex(ValueError, "timezone"):
            await list_client_minute_metrics(
                db,
                site_id="site",
                start_at=datetime(2026, 7, 19, 1, 0),
                end_at=valid,
            )
        with self.assertRaisesRegex(ValueError, "after"):
            await list_client_minute_metrics(db, site_id="site", start_at=valid, end_at=valid)
        with self.assertRaisesRegex(ValueError, "10080"):
            await list_client_minute_metrics(
                db,
                site_id="site",
                start_at=valid,
                end_at=datetime(2026, 8, 19, 1, 0, tzinfo=UTC),
            )

    async def test_empty_status_has_stable_shape(self) -> None:
        async def find_one(*_args, **_kwargs):
            return None

        db = SimpleNamespace(client_metric_sampler_state=SimpleNamespace(find_one=find_one))

        result = await get_client_metric_status(db, site_id="site")

        self.assertEqual(result["site_id"], "site")
        self.assertIsNone(result["last_bucket_at"])
        self.assertIsNone(result["last_rpm"])
        self.assertIsNone(result["last_tpm"])
        self.assertEqual(result["consecutive_failures"], 0)


if __name__ == "__main__":
    unittest.main()
