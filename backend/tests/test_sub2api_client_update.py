from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.modules.sub2api.client import Sub2ApiClient


class Sub2ApiClientUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_patch_404_fetches_current_account_and_retries_with_full_put(self) -> None:
        client = Sub2ApiClient(base_url="http://sub2.example.com", token="admin-key")
        patch_response = httpx.Response(
            404,
            text="404 page not found",
            request=httpx.Request("PATCH", "http://sub2.example.com/api/v1/admin/accounts/3418"),
        )
        put_response = httpx.Response(
            200,
            json={"code": 0, "data": {"id": 3418, "group_ids": [7]}},
            request=httpx.Request("PUT", "http://sub2.example.com/api/v1/admin/accounts/3418"),
        )
        request = AsyncMock(side_effect=[patch_response, put_response])
        current_account = {
            "id": 3418,
            "name": "+447901709584---user@example.com",
            "notes": None,
            "proxy_id": None,
            "concurrency": 10,
            "priority": 1,
            "rate_multiplier": 1,
            "status": "error",
            "group_ids": [4],
            "expires_at": None,
            "auto_pause_on_expired": True,
            "credentials": {"chatgpt_account_id": "account-id", "email": "user@example.com"},
            "extra": {"account_id": "account-id", "privacy_mode": "training_off"},
        }

        error: HTTPException | None = None
        with (
            patch.object(client, "_request_admin_response_with_retries", request),
            patch.object(client, "get_account", AsyncMock(return_value=current_account)) as get_account,
        ):
            try:
                result = await client.update_account(3418, {"group_id": 7, "group_ids": [7]})
            except HTTPException as exc:
                error = exc

        self.assertIsNone(error, str(error))
        if error is not None:
            return
        get_account.assert_awaited_once_with(3418)
        self.assertEqual(request.await_count, 2)
        self.assertEqual(request.await_args_list[0].args[:2], ("PATCH", "/accounts/3418"))
        self.assertEqual(request.await_args_list[1].args[:2], ("PUT", "/accounts/3418"))
        self.assertEqual(
            request.await_args_list[1].kwargs["json"],
            {
                "name": "+447901709584---user@example.com",
                "notes": "",
                "proxy_id": 0,
                "concurrency": 10,
                "load_factor": 0,
                "priority": 1,
                "rate_multiplier": 1,
                "status": "error",
                "group_ids": [7],
                "expires_at": 0,
                "auto_pause_on_expired": True,
                "credentials": {"chatgpt_account_id": "account-id", "email": "user@example.com"},
                "extra": {"account_id": "account-id", "privacy_mode": "training_off"},
            },
        )
        self.assertEqual(result, {"id": 3418, "group_ids": [7]})


if __name__ == "__main__":
    unittest.main()
