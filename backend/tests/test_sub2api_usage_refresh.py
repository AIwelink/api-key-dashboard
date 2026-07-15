from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import ANY, AsyncMock, patch

import httpx

from app.modules.sub2api import cache
from app.modules.sub2api.client import Sub2ApiClient


class Sub2ApiUsageRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_usage_uses_remote_usage_endpoint(self) -> None:
        requests: list[httpx.Request] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, json={"data": {"updated_at": "now"}})

        client = Sub2ApiClient(base_url="http://127.0.0.1:5001", token="secret")
        transport = httpx.MockTransport(handle)
        async with httpx.AsyncClient(transport=transport) as http_client:
            result = await client.get_account_usage(2976, http_client=http_client)

        self.assertEqual(result, {"updated_at": "now"})
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            str(requests[0].url),
            "http://127.0.0.1:5001/api/v1/admin/accounts/2976/usage?timezone=Asia%2FShanghai",
        )
        self.assertEqual(requests[0].headers["x-api-key"], "secret")

    async def test_every_account_with_remote_id_refreshes_usage(self) -> None:
        synced_at = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        accounts = [
            {
                "id": 2976,
                "codex_usage_synced_at": datetime(2026, 7, 15, 11, 59, tzinfo=UTC),
                "codex_5h_used_percent": 99,
            },
            {"id": 2977},
            {"name": "missing remote id"},
        ]

        async def usage_for(account_id: int, **_: object) -> dict[str, object]:
            return {
                "updated_at": f"account-{account_id}",
                "five_hour": {
                    "utilization": 12 if account_id == 2976 else 34,
                    "remaining_seconds": 1800,
                    "window_stats": {"requests": account_id, "cost": 1.25},
                },
                "seven_day": {
                    "utilization": 56,
                    "remaining_seconds": 7200,
                    "window_stats": {"requests": account_id * 2, "cost": 2.5},
                },
            }

        client = AsyncMock()
        client.get_account_usage.side_effect = usage_for

        with patch.object(cache, "_restore_cached_usage_snapshots", AsyncMock()) as restore:
            await cache._apply_account_usage_windows(object(), "api-5001", client, accounts, synced_at)

        restore.assert_awaited_once_with(ANY, "api-5001", accounts)
        requested_ids = [call.args[0] for call in client.get_account_usage.await_args_list]
        self.assertCountEqual(requested_ids, [2976, 2977])
        for call in client.get_account_usage.await_args_list:
            self.assertEqual(call.kwargs["timezone"], "Asia/Shanghai")
            self.assertIsNotNone(call.kwargs["http_client"])

        self.assertEqual(accounts[0]["codex_5h_used_percent"], 12)
        self.assertEqual(accounts[0]["codex_usage_updated_at"], "account-2976")
        self.assertEqual(accounts[1]["codex_5h_used_percent"], 34)
        self.assertEqual(accounts[1]["codex_7d_used_percent"], 56)
        self.assertEqual(accounts[1]["codex_usage_synced_at"], synced_at)


if __name__ == "__main__":
    unittest.main()
