from __future__ import annotations

import unittest
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from app.modules.client_metrics.models import AdapterSample
from app.modules.client_metrics import sampler
from app.modules.system.bootstrap import ensure_client_metric_indexes
from app.logging_config import SuppressSuccessfulUsageHttpxFilter


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class StubAdapter:
    def __init__(self, result: AdapterSample | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def sample(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


class ClientMetricSamplerTests(unittest.IsolatedAsyncioTestCase):
    async def test_sample_persists_deterministic_document_and_success_state(self) -> None:
        bucket_at = datetime(2026, 7, 19, 1, 2, tzinfo=UTC)
        site = self._site(data_retention_days=30)
        adapter = StubAdapter(
            AdapterSample(
                rpm=12,
                tpm=3456,
                quality="complete",
                source="newapi_reported",
            )
        )
        metrics = SimpleNamespace(replace_one=AsyncMock())
        states = SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock())
        db = SimpleNamespace(
            client_sites=SimpleNamespace(find_one=AsyncMock(return_value=site)),
            client_minute_metrics=metrics,
            client_metric_sampler_state=states,
        )

        result = await sampler.sample_client_site(
            db,
            site_id="newapi-us01",
            bucket_at=bucket_at,
            adapter_factory=lambda _: adapter,
        )

        document = metrics.replace_one.await_args.args[1]
        self.assertEqual(document["_id"], "newapi-us01:2026-07-19T01:02Z")
        self.assertEqual(document["rpm"], 12)
        self.assertEqual(document["tpm"], 3456)
        self.assertEqual(document["expires_at"], bucket_at + timedelta(days=30))
        self.assertNotIn("api_key", document)
        state_update = states.update_one.await_args.args[1]["$set"]
        self.assertEqual(state_update["last_quality"], "complete")
        self.assertEqual(state_update["consecutive_failures"], 0)
        self.assertTrue(result["ok"])

    async def test_missing_sample_is_stored_and_failure_count_increments(self) -> None:
        bucket_at = datetime(2026, 7, 19, 1, 2, tzinfo=UTC)
        adapter = StubAdapter(
            AdapterSample(
                rpm=None,
                tpm=None,
                quality="missing",
                source="newapi_reported",
                error_code="timeout",
            )
        )
        metrics = SimpleNamespace(replace_one=AsyncMock())
        states = SimpleNamespace(
            find_one=AsyncMock(return_value={"consecutive_failures": 2}),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(
            client_sites=SimpleNamespace(find_one=AsyncMock(return_value=self._site())),
            client_minute_metrics=metrics,
            client_metric_sampler_state=states,
        )

        result = await sampler.sample_client_site(
            db,
            site_id="newapi-us01",
            bucket_at=bucket_at,
            adapter_factory=lambda _: adapter,
        )

        document = metrics.replace_one.await_args.args[1]
        self.assertIsNone(document["rpm"])
        self.assertIsNone(document["tpm"])
        self.assertEqual(document["quality"], "missing")
        self.assertEqual(document["error_code"], "timeout")
        state_update = states.update_one.await_args.args[1]["$set"]
        self.assertEqual(state_update["consecutive_failures"], 3)
        self.assertEqual(state_update["last_error"], "timeout")
        self.assertFalse(result["ok"])

    async def test_complete_minute_is_not_downgraded_by_same_bucket_retry(self) -> None:
        bucket_at = datetime(2026, 7, 19, 1, 2, tzinfo=UTC)
        existing = {
            "_id": "newapi-us01:2026-07-19T01:02Z",
            "site_id": "newapi-us01",
            "client_type": "newapi",
            "bucket_at": bucket_at,
            "rpm": 12,
            "tpm": 3456,
            "quality": "complete",
        }
        adapter = StubAdapter(
            AdapterSample(None, None, "delayed", "newapi_reported", error_code="upstream_not_updated")
        )
        metrics = SimpleNamespace(find_one=AsyncMock(return_value=existing), replace_one=AsyncMock())
        states = SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock())
        db = SimpleNamespace(
            client_sites=SimpleNamespace(find_one=AsyncMock(return_value=self._site())),
            client_minute_metrics=metrics,
            client_metric_sampler_state=states,
        )

        result = await sampler.sample_client_site(
            db,
            site_id="newapi-us01",
            bucket_at=bucket_at,
            adapter_factory=lambda _: adapter,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "already_complete")
        self.assertEqual(result["rpm"], 12)
        self.assertEqual(adapter.calls, [])
        metrics.replace_one.assert_not_awaited()
        states.update_one.assert_not_awaited()

    async def test_unexpected_adapter_error_is_isolated_between_sites(self) -> None:
        sites = [
            self._site(site_id="newapi-us01", client_type="newapi"),
            self._site(site_id="sub2api-us01", client_type="sub2api"),
        ]
        metrics = SimpleNamespace(replace_one=AsyncMock())
        states = SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock())
        db = SimpleNamespace(
            client_sites=SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor(sites)),
            client_minute_metrics=metrics,
            client_metric_sampler_state=states,
        )
        adapters = {
            "newapi": StubAdapter(AdapterSample(1, 2, "complete", "newapi_reported")),
            "sub2api": StubAdapter(error=RuntimeError("api_key=should-not-leak")),
        }

        result = await sampler.sample_all_client_sites(
            db,
            bucket_at=datetime(2026, 7, 19, 1, 2, tzinfo=UTC),
            adapter_factory=lambda client_type: adapters[client_type],
        )

        self.assertEqual(result["sites"], 2)
        self.assertEqual(result["complete"], 1)
        self.assertEqual(result["missing"], 1)
        failed_document = next(
            item.args[1]
            for item in metrics.replace_one.await_args_list
            if item.args[1]["site_id"] == "sub2api-us01"
        )
        self.assertEqual(failed_document["error_code"], "RuntimeError")
        self.assertNotIn("should-not-leak", str(failed_document))

    async def test_sample_all_queries_only_active_configured_client_sites(self) -> None:
        captured: dict[str, object] = {}
        sites = [self._site()]

        def find(query, projection):
            captured["query"] = query
            captured["projection"] = projection
            return AsyncCursor(sites)

        db = SimpleNamespace(
            client_sites=SimpleNamespace(find=find),
            client_minute_metrics=SimpleNamespace(replace_one=AsyncMock()),
            client_metric_sampler_state=SimpleNamespace(find_one=AsyncMock(return_value=None), update_one=AsyncMock()),
        )
        adapter = StubAdapter(AdapterSample(1, 2, "complete", "newapi_reported"))

        await sampler.sample_all_client_sites(
            db,
            bucket_at=datetime(2026, 7, 19, 1, 2, tzinfo=UTC),
            adapter_factory=lambda _: adapter,
        )

        self.assertEqual(captured["query"]["status"], "active")
        self.assertIn("api_key", captured["query"])
        self.assertEqual(
            set(captured["projection"]),
            {"_id", "client_type", "base_url", "api_key", "admin_user_id", "status", "data_retention_days"},
        )
        self.assertNotIn("sql_dsn", captured["projection"])

    def test_scheduler_aligns_to_wall_clock_second_five(self) -> None:
        self.assertEqual(
            sampler.seconds_until_next_sample(datetime(2026, 7, 19, 1, 2, 4, tzinfo=UTC)),
            1,
        )
        self.assertEqual(
            sampler.seconds_until_next_sample(datetime(2026, 7, 19, 1, 2, 6, tzinfo=UTC)),
            59,
        )
        self.assertEqual(
            sampler.target_bucket_for(datetime(2026, 7, 19, 1, 3, 5, tzinfo=UTC)),
            datetime(2026, 7, 19, 1, 2, tzinfo=UTC),
        )

    async def test_bootstrap_creates_metric_and_ttl_indexes(self) -> None:
        metrics = SimpleNamespace(create_index=AsyncMock())
        states = SimpleNamespace(create_index=AsyncMock())
        db = SimpleNamespace(client_minute_metrics=metrics, client_metric_sampler_state=states)

        await ensure_client_metric_indexes(db)

        self.assertIn(
            call([("site_id", 1), ("bucket_at", 1)], unique=True),
            metrics.create_index.await_args_list,
        )
        self.assertIn(
            call("expires_at", expireAfterSeconds=0),
            metrics.create_index.await_args_list,
        )
        states.create_index.assert_awaited_with("updated_at")

    def test_successful_minute_metric_http_logs_are_suppressed_but_errors_remain(self) -> None:
        log_filter = SuppressSuccessfulUsageHttpxFilter()

        for path in (
            "https://api.example.com/api/log/stat?p=1",
            "https://sub2.example.com/api/v1/admin/dashboard/snapshot-v2",
        ):
            success = logging.LogRecord(
                "httpx",
                logging.INFO,
                __file__,
                1,
                f'HTTP Request: GET {path} "HTTP/1.1 200 OK"',
                (),
                None,
            )
            failure = logging.LogRecord(
                "httpx",
                logging.INFO,
                __file__,
                1,
                f'HTTP Request: GET {path} "HTTP/1.1 500 Internal Server Error"',
                (),
                None,
            )
            self.assertFalse(log_filter.filter(success))
            self.assertTrue(log_filter.filter(failure))

    @staticmethod
    def _site(
        *,
        site_id: str = "newapi-us01",
        client_type: str = "newapi",
        data_retention_days: int = 90,
    ) -> dict[str, object]:
        return {
            "_id": site_id,
            "name": site_id,
            "client_type": client_type,
            "base_url": "https://api.example.com",
            "api_key": "secret-key",
            "admin_user_id": "42" if client_type == "newapi" else "",
            "status": "active",
            "data_retention_days": data_retention_days,
        }


if __name__ == "__main__":
    unittest.main()
