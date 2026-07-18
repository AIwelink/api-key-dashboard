from __future__ import annotations

import unittest
from datetime import UTC, datetime

import httpx

from app.modules.client_metrics.adapters.newapi import NewApiMetricAdapter
from app.modules.client_metrics.adapters.sub2api import Sub2ApiMetricAdapter


class NewApiMetricAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_records_reported_rpm_and_tpm_with_admin_headers(self) -> None:
        captured: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["authorization"] = request.headers.get("Authorization")
            captured["admin_user"] = request.headers.get("New-Api-User")
            return httpx.Response(
                200,
                json={
                    "data": {"quota": 164373249, "rpm": 68, "tpm": 7065395},
                    "message": "",
                    "success": True,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await NewApiMetricAdapter(http_client=client).sample(
                site={
                    "_id": "newapi-us01",
                    "base_url": "https://api.example.com/",
                    "api_key": "secret-key",
                    "admin_user_id": "42",
                },
                bucket_at=datetime(2026, 7, 19, 1, 2, tzinfo=UTC),
                cursor={},
            )

        self.assertEqual(result.rpm, 68)
        self.assertEqual(result.tpm, 7065395)
        self.assertEqual(result.quality, "complete")
        self.assertEqual(result.source, "newapi_reported")
        self.assertNotIn("quota", result.cursor)
        self.assertEqual(captured["authorization"], "Bearer secret-key")
        self.assertEqual(captured["admin_user"], "42")
        self.assertIn("/api/log/stat?", str(captured["url"]))
        self.assertIn("p=1", str(captured["url"]))
        self.assertIn("page_size=1", str(captured["url"]))
        self.assertIn("type=0", str(captured["url"]))

    async def test_upstream_rejection_is_a_missing_sample(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": False, "message": "permission denied", "data": None})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await NewApiMetricAdapter(http_client=client).sample(
                site={
                    "_id": "newapi-us01",
                    "base_url": "https://api.example.com",
                    "api_key": "secret-key",
                    "admin_user_id": "42",
                },
                bucket_at=datetime(2026, 7, 19, 1, 2, tzinfo=UTC),
                cursor={},
            )

        self.assertIsNone(result.rpm)
        self.assertIsNone(result.tpm)
        self.assertEqual(result.quality, "missing")
        self.assertEqual(result.error_code, "upstream_rejected")
        self.assertNotIn("permission denied", str(result))

    async def test_invalid_or_negative_metrics_are_missing(self) -> None:
        payloads = [
            {"rpm": -1, "tpm": 10},
            {"rpm": "not-a-number", "tpm": 10},
            {"rpm": 1, "tpm": None},
        ]

        for metrics in payloads:
            with self.subTest(metrics=metrics):
                async def handler(_: httpx.Request, value: dict[str, object] = metrics) -> httpx.Response:
                    return httpx.Response(200, json={"success": True, "message": "", "data": value})

                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    result = await NewApiMetricAdapter(http_client=client).sample(
                        site={
                            "_id": "newapi-us01",
                            "base_url": "https://api.example.com",
                            "api_key": "secret-key",
                            "admin_user_id": "42",
                        },
                        bucket_at=datetime(2026, 7, 19, 1, 2, tzinfo=UTC),
                        cursor={},
                    )

                self.assertEqual(result.quality, "missing")
                self.assertEqual(result.error_code, "invalid_metrics")


class Sub2ApiMetricAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_sample_builds_cursor_without_inventing_a_rate(self) -> None:
        result, request_url = await self._sample(
            bucket_at=datetime(2026, 7, 19, 1, 2, tzinfo=UTC),
            cursor={},
            requests=100,
            tokens=10000,
            source_updated_at="2026-07-19T01:02:03Z",
        )

        self.assertEqual(result.quality, "missing")
        self.assertEqual(result.error_code, "initial_cursor")
        self.assertIsNone(result.rpm)
        self.assertEqual(result.cursor["total_requests"], 100)
        self.assertEqual(result.cursor["total_tokens"], 10000)
        self.assertNotIn("group_id", request_url)

    async def test_same_hour_counters_produce_per_minute_rates(self) -> None:
        result, _ = await self._sample(
            bucket_at=datetime(2026, 7, 19, 1, 2, tzinfo=UTC),
            cursor=self._cursor(
                sampled_at=datetime(2026, 7, 19, 1, 1, tzinfo=UTC),
                source_updated_at=datetime(2026, 7, 19, 1, 1, 3, tzinfo=UTC),
                requests=100,
                tokens=10000,
            ),
            requests=150,
            tokens=13000,
            source_updated_at="2026-07-19T01:02:03Z",
        )

        self.assertEqual(result.quality, "complete")
        self.assertEqual(result.rpm, 50)
        self.assertEqual(result.tpm, 3000)
        self.assertEqual(result.elapsed_seconds, 60)

    async def test_hour_rollover_uses_new_hour_counters_as_delta(self) -> None:
        result, _ = await self._sample(
            bucket_at=datetime(2026, 7, 19, 2, 0, tzinfo=UTC),
            cursor=self._cursor(
                sampled_at=datetime(2026, 7, 19, 1, 59, tzinfo=UTC),
                source_updated_at=datetime(2026, 7, 19, 1, 59, 3, tzinfo=UTC),
                requests=900,
                tokens=90000,
                source_bucket_at=datetime(2026, 7, 19, 1, 0, tzinfo=UTC),
            ),
            requests=20,
            tokens=2000,
            source_updated_at="2026-07-19T02:00:03Z",
        )

        self.assertEqual(result.quality, "complete")
        self.assertEqual(result.rpm, 20)
        self.assertEqual(result.tpm, 2000)

    async def test_unchanged_upstream_is_delayed_and_preserves_cursor(self) -> None:
        cursor = self._cursor(
            sampled_at=datetime(2026, 7, 19, 1, 1, tzinfo=UTC),
            source_updated_at=datetime(2026, 7, 19, 1, 1, 3, tzinfo=UTC),
            requests=100,
            tokens=10000,
        )
        result, _ = await self._sample(
            bucket_at=datetime(2026, 7, 19, 1, 2, tzinfo=UTC),
            cursor=cursor,
            requests=100,
            tokens=10000,
            source_updated_at="2026-07-19T01:01:03Z",
        )

        self.assertEqual(result.quality, "delayed")
        self.assertIsNone(result.rpm)
        self.assertEqual(result.cursor, cursor)

    async def test_updated_upstream_with_unchanged_counters_is_real_zero(self) -> None:
        result, _ = await self._sample(
            bucket_at=datetime(2026, 7, 19, 1, 2, tzinfo=UTC),
            cursor=self._cursor(
                sampled_at=datetime(2026, 7, 19, 1, 1, tzinfo=UTC),
                source_updated_at=datetime(2026, 7, 19, 1, 1, 3, tzinfo=UTC),
                requests=100,
                tokens=10000,
            ),
            requests=100,
            tokens=10000,
            source_updated_at="2026-07-19T01:02:03Z",
        )

        self.assertEqual(result.quality, "complete")
        self.assertEqual(result.rpm, 0)
        self.assertEqual(result.tpm, 0)

    async def test_unexplained_counter_rollback_resets_cursor(self) -> None:
        result, _ = await self._sample(
            bucket_at=datetime(2026, 7, 19, 1, 2, tzinfo=UTC),
            cursor=self._cursor(
                sampled_at=datetime(2026, 7, 19, 1, 1, tzinfo=UTC),
                source_updated_at=datetime(2026, 7, 19, 1, 1, 3, tzinfo=UTC),
                requests=100,
                tokens=10000,
            ),
            requests=20,
            tokens=2000,
            source_updated_at="2026-07-19T01:02:03Z",
        )

        self.assertEqual(result.quality, "counter_reset")
        self.assertIsNone(result.rpm)
        self.assertEqual(result.cursor["total_requests"], 20)
        self.assertEqual(result.cursor["total_tokens"], 2000)

    async def _sample(
        self,
        *,
        bucket_at: datetime,
        cursor: dict[str, object],
        requests: int,
        tokens: int,
        source_updated_at: str,
    ):
        captured_url = ""
        local_hour = bucket_at.astimezone(Sub2ApiMetricAdapter.local_timezone).strftime("%Y-%m-%d %H:00")

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_url
            captured_url = str(request.url)
            self.assertEqual(request.headers.get("x-api-key"), "sub2-secret")
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "message": "success",
                    "data": {
                        "generated_at": source_updated_at,
                        "stats": {"stats_updated_at": source_updated_at},
                        "trend": [
                            {
                                "date": local_hour,
                                "requests": requests,
                                "total_tokens": tokens,
                            }
                        ],
                    },
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await Sub2ApiMetricAdapter(http_client=client).sample(
                site={
                    "_id": "sub2api-us01",
                    "base_url": "https://sub2.example.com/",
                    "api_key": "sub2-secret",
                },
                bucket_at=bucket_at,
                cursor=cursor,
            )
        return result, captured_url

    @staticmethod
    def _cursor(
        *,
        sampled_at: datetime,
        source_updated_at: datetime,
        requests: int,
        tokens: int,
        source_bucket_at: datetime | None = None,
    ) -> dict[str, object]:
        return {
            "source_bucket_at": source_bucket_at or sampled_at.replace(minute=0, second=0, microsecond=0),
            "total_requests": requests,
            "total_tokens": tokens,
            "cursor_sampled_at": sampled_at,
            "source_updated_at": source_updated_at,
        }


if __name__ == "__main__":
    unittest.main()
