from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.modules.sub2api import long_7d_probe
from app.modules.sub2api.client import Sub2ApiClient


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def sort(self, *_args: object) -> "AsyncCursor":
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class LongSevenDayProbeCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    def test_accepts_schedulable_long_seven_day_limit_without_five_hour_usage(self) -> None:
        account = {
            "schedulable": True,
            "codex_5h_used_percent": 0,
            "codex_7d_used_percent": 100,
            "codex_7d_reset_after_seconds": 3 * 24 * 60 * 60,
        }

        self.assertTrue(long_7d_probe.is_long_7d_probe_candidate(account, now=self.now))

    def test_rejects_disabled_short_or_not_exhausted_accounts(self) -> None:
        base = {
            "schedulable": True,
            "codex_7d_used_percent": 100,
            "codex_7d_reset_after_seconds": 3 * 24 * 60 * 60,
        }

        self.assertFalse(long_7d_probe.is_long_7d_probe_candidate(base | {"schedulable": False}, now=self.now))
        self.assertFalse(
            long_7d_probe.is_long_7d_probe_candidate(
                base | {"codex_7d_reset_after_seconds": 24 * 60 * 60},
                now=self.now,
            )
        )
        self.assertFalse(long_7d_probe.is_long_7d_probe_candidate(base | {"codex_7d_used_percent": 99}, now=self.now))

    def test_reset_timestamp_can_be_read_from_nested_account_extra(self) -> None:
        account = {
            "schedulable": True,
            "account": {
                "extra": {
                    "codex_7d_used_percent": 100,
                    "codex_7d_reset_at": (self.now + timedelta(days=2)).isoformat(),
                }
            },
        }

        self.assertTrue(long_7d_probe.is_long_7d_probe_candidate(account, now=self.now))


class LongSevenDayProbeDisableReasonTests(unittest.TestCase):
    def test_recognizes_only_the_three_confirmed_account_errors(self) -> None:
        cases = {
            'API returned 401: {"error":{"message":"Your authentication token has been invalidated.","code":"token_invalidated"}}': "token_invalidated",
            'API returned 402: {"detail":{"code":"deactivated_workspace"}}': "deactivated_workspace",
            'API returned 403: {"error":{"message":"Personal access token owner is inactive.","code":"biscuit_baker_service_auth_credential_error_status"}}': "inactive_token_owner",
        }

        for error, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(long_7d_probe.account_disable_reason(error), expected)

    def test_does_not_disable_an_unrelated_403(self) -> None:
        error = 'API returned 403: {"error":{"message":"Request is not allowed for this model","code":"model_not_allowed"}}'

        self.assertIsNone(long_7d_probe.account_disable_reason(error))


class LongSevenDayProbeRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_due_accounts_are_probed_sequentially_and_recent_accounts_are_skipped(self) -> None:
        now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
        accounts = [
            {
                "site_id": "api-5001",
                "sub2api_account_id": account_id,
                "schedulable": True,
                "codex_7d_used_percent": 100,
                "codex_7d_reset_after_seconds": 3 * 24 * 60 * 60,
            }
            for account_id in (10, 11, 12)
        ]
        histories = [
            {
                "_id": "api-5001:12",
                "site_id": "api-5001",
                "remote_account_id": 12,
                "last_attempt_at": now - timedelta(hours=1),
            }
        ]
        accounts_cache = SimpleNamespace(
            find=MagicMock(return_value=AsyncCursor(accounts)),
            update_one=AsyncMock(),
        )
        probe_records = SimpleNamespace(
            find=MagicMock(return_value=AsyncCursor(histories)),
            update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1, upserted_id=None)),
        )
        db = SimpleNamespace(
            sub2api_accounts_cache=accounts_cache,
            long_7d_account_probes=probe_records,
        )

        active_requests = 0
        max_active_requests = 0
        tested_ids: list[int] = []

        async def test_account(account_id: int, **kwargs: object) -> dict[str, object]:
            nonlocal active_requests, max_active_requests
            active_requests += 1
            max_active_requests = max(max_active_requests, active_requests)
            tested_ids.append(account_id)
            await asyncio.sleep(0)
            active_requests -= 1
            return {
                "success": True,
                "model": kwargs["model_id"],
                "mode": kwargs["mode"],
                "prompt": kwargs["prompt"],
                "latency_ms": 10,
                "response_preview": "ok",
                "error": None,
            }

        client = SimpleNamespace(test_account=AsyncMock(side_effect=test_account))
        site = {
            "_id": "api-5001",
            "base_url": "https://sub2api.example.com",
            "token": "secret",
            "long_7d_probe_model": "gpt-5.5",
        }

        with patch.object(long_7d_probe, "Sub2ApiClient", return_value=client):
            result = await long_7d_probe.probe_site_long_7d_accounts(db, site=site, now=now)

        self.assertEqual(tested_ids, [10, 11])
        self.assertEqual(max_active_requests, 1)
        self.assertEqual(result["eligible"], 3)
        self.assertEqual(result["probed"], 2)
        self.assertEqual(result["passed"], 2)
        for call in client.test_account.await_args_list:
            self.assertEqual(call.kwargs, {"model_id": "gpt-5.5", "prompt": "", "mode": "default"})

    async def test_confirmed_account_error_disables_remote_schedulable_state(self) -> None:
        now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
        account = {
            "site_id": "api-5001",
            "sub2api_account_id": 1680,
            "schedulable": True,
            "codex_7d_used_percent": 100,
            "codex_7d_reset_after_seconds": 3 * 24 * 60 * 60,
        }
        accounts_cache = SimpleNamespace(
            find=MagicMock(return_value=AsyncCursor([account])),
            update_one=AsyncMock(),
        )
        probe_records = SimpleNamespace(
            find=MagicMock(return_value=AsyncCursor([])),
            update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1, upserted_id=None)),
        )
        db = SimpleNamespace(
            sub2api_accounts_cache=accounts_cache,
            long_7d_account_probes=probe_records,
        )
        client = SimpleNamespace(
            test_account=AsyncMock(
                return_value={
                    "success": False,
                    "model": "gpt-5.5",
                    "mode": "default",
                    "prompt": "",
                    "latency_ms": 20,
                    "response_preview": "",
                    "error": 'API returned 401: {"error":{"code":"token_invalidated"}}',
                }
            ),
            set_account_schedulable=AsyncMock(return_value={"id": 1680, "schedulable": False}),
        )
        site = {
            "_id": "api-5001",
            "base_url": "https://sub2api.example.com",
            "token": "secret",
            "long_7d_probe_model": "gpt-5.5",
        }

        with patch.object(long_7d_probe, "Sub2ApiClient", return_value=client):
            result = await long_7d_probe.probe_site_long_7d_accounts(db, site=site, now=now)

        client.set_account_schedulable.assert_awaited_once_with(1680, False)
        self.assertEqual(result["disabled"], 1)
        final_record = probe_records.update_one.await_args_list[-1].args[1]["$set"]
        self.assertEqual(final_record["last_result"]["disable_reason"], "token_invalidated")
        self.assertTrue(final_record["last_result"]["schedulable_disabled"])
        cache_updates = accounts_cache.update_one.await_args_list[-1].args[1]["$set"]
        self.assertFalse(cache_updates["schedulable"])
        self.assertFalse(cache_updates["account.schedulable"])

    async def test_unrelated_403_does_not_disable_remote_account(self) -> None:
        now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
        account = {
            "site_id": "api-5001",
            "sub2api_account_id": 1681,
            "schedulable": True,
            "codex_7d_used_percent": 100,
            "codex_7d_reset_after_seconds": 3 * 24 * 60 * 60,
        }
        accounts_cache = SimpleNamespace(find=MagicMock(return_value=AsyncCursor([account])), update_one=AsyncMock())
        probe_records = SimpleNamespace(
            find=MagicMock(return_value=AsyncCursor([])),
            update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1, upserted_id=None)),
        )
        db = SimpleNamespace(sub2api_accounts_cache=accounts_cache, long_7d_account_probes=probe_records)
        client = SimpleNamespace(
            test_account=AsyncMock(
                return_value={
                    "success": False,
                    "model": "gpt-5.5",
                    "mode": "default",
                    "prompt": "",
                    "latency_ms": 20,
                    "response_preview": "",
                    "error": 'API returned 403: {"error":{"code":"model_not_allowed"}}',
                }
            ),
            set_account_schedulable=AsyncMock(),
        )
        site = {"_id": "api-5001", "base_url": "https://sub2api.example.com", "token": "secret"}

        with patch.object(long_7d_probe, "Sub2ApiClient", return_value=client):
            result = await long_7d_probe.probe_site_long_7d_accounts(db, site=site, now=now)

        client.set_account_schedulable.assert_not_awaited()
        self.assertEqual(result["disabled"], 0)


class Sub2ApiClientAccountTestPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_test_sends_mode(self) -> None:
        response = httpx.Response(
            200,
            text='data: {"type":"test_complete","success":true}\n\n',
        )
        client = Sub2ApiClient(base_url="https://sub2api.example.com", token="secret")
        request = AsyncMock(return_value=response)

        with patch.object(client, "_request_admin_response_with_retries", request):
            result = await client.test_account(1680, model_id="gpt-5.5", prompt="", mode="default")

        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "default")
        self.assertEqual(
            request.await_args.kwargs["json"],
            {"model_id": "gpt-5.5", "prompt": "", "mode": "default"},
        )


if __name__ == "__main__":
    unittest.main()
