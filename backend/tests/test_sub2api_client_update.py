from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app.modules.sub2api.client import Sub2ApiClient


class Sub2ApiClientUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_redemption_codes_uses_admin_pagination_contract(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "message": "success",
                    "data": {
                        "items": [
                            {
                                "id": 101,
                                "code": "redeem-alpha",
                                "type": "balance",
                                "value": 100,
                                "status": "unused",
                                "created_at": "2026-08-12T03:00:00Z",
                            }
                        ],
                        "total": 1,
                        "page": 1,
                        "page_size": 1000,
                        "pages": 1,
                    },
                },
                request=request,
            )

        client = Sub2ApiClient(base_url="http://sub2.example.com", token="admin-key")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            result = await client.list_redemption_codes(
                page=1,
                page_size=1000,
                status_filter="unused",
                search="alpha",
                http_client=http_client,
            )

        self.assertEqual(captured["method"], "GET")
        self.assertEqual(
            captured["url"],
            "http://sub2.example.com/api/v1/admin/redeem-codes?page=1&page_size=1000&status=unused&search=alpha&sort_by=created_at&sort_order=desc",
        )
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["code"], "redeem-alpha")

    async def test_get_redemption_code_uses_official_admin_path(self) -> None:
        requests: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, str(request.url)))
            data: object = {
                "id": 101,
                "code": "redeem-alpha",
                "status": "unused",
                "value": 100,
            }
            return httpx.Response(
                200,
                json={"code": 0, "message": "success", "data": data},
                request=request,
            )

        client = Sub2ApiClient(base_url="http://sub2.example.com", token="admin-key")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            item = await client.get_redemption_code(101, http_client=http_client)

        self.assertEqual(item["code"], "redeem-alpha")
        self.assertEqual(
            requests,
            [
                ("GET", "http://sub2.example.com/api/v1/admin/redeem-codes/101"),
            ],
        )

    async def test_missing_redemption_code_preserves_not_found_status(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"code": 404, "message": "Redeem code not found"},
                request=request,
            )

        client = Sub2ApiClient(base_url="http://sub2.example.com", token="admin-key")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            with self.assertRaises(HTTPException) as raised:
                await client.get_redemption_code(101, http_client=http_client)

        self.assertEqual(raised.exception.status_code, 404)

    async def test_generate_redemption_codes_uses_verified_admin_contract(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["url"] = str(request.url)
            captured["api_key"] = request.headers.get("x-api-key")
            captured["idempotency_key"] = request.headers.get("Idempotency-Key")
            captured["payload"] = request.content.decode("utf-8")
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "message": "success",
                    "data": [
                        {"id": 101, "code": "redeem-alpha", "type": "balance", "value": 100},
                        {"id": 102, "code": "redeem-beta", "type": "balance", "value": 100},
                    ],
                },
                request=request,
            )

        client = Sub2ApiClient(base_url="http://sub2.example.com", token="admin-key")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
            result = await client.generate_redemption_codes(
                count=2,
                value=Decimal("100"),
                idempotency_key="batch-1:chunk:1",
                http_client=http_client,
            )

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(
            captured["url"],
            "http://sub2.example.com/api/v1/admin/redeem-codes/generate",
        )
        self.assertEqual(captured["api_key"], "admin-key")
        self.assertEqual(captured["idempotency_key"], "batch-1:chunk:1")
        self.assertEqual(
            captured["payload"],
            '{"count":2,"type":"balance","value":100.0}',
        )
        self.assertEqual([item["code"] for item in result], ["redeem-alpha", "redeem-beta"])

    async def test_bulk_runtime_update_posts_the_admin_api_payload(self) -> None:
        client = Sub2ApiClient(base_url="http://sub2.example.com", token="admin-key")
        response = httpx.Response(
            200,
            json={
                "code": 0,
                "message": "success",
                "data": {
                    "success": 2,
                    "failed": 0,
                    "success_ids": [5864, 5863],
                    "failed_ids": [],
                    "results": [
                        {"account_id": 5864, "success": True},
                        {"account_id": 5863, "success": True},
                    ],
                },
            },
            request=httpx.Request(
                "POST",
                "http://sub2.example.com/api/v1/admin/accounts/bulk-update",
            ),
        )
        request = AsyncMock(return_value=response)

        with patch.object(client, "_request_admin_response_with_retries", request):
            result = await client.bulk_update_accounts_runtime(
                [5864, 5863],
                {"concurrency": 30, "priority": 100, "group_ids": [3]},
            )

        request.assert_awaited_once_with(
            "POST",
            "/accounts/bulk-update",
            json={
                "account_ids": [5864, 5863],
                "concurrency": 30,
                "priority": 100,
                "group_ids": [3],
            },
            timeout=15,
        )
        self.assertEqual(result["success_ids"], [5864, 5863])

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

    async def test_credentials_update_fetches_current_account_and_puts_deep_merged_credentials(self) -> None:
        client = Sub2ApiClient(base_url="http://sub2.example.com", token="admin-key")
        put_response = httpx.Response(
            200,
            json={"code": 0, "data": {"id": 4802, "credentials": {"plan_type": "plus"}}},
            request=httpx.Request("PUT", "http://sub2.example.com/api/v1/admin/accounts/4802"),
        )
        request = AsyncMock(return_value=put_response)
        current_account = {
            "id": 4802,
            "name": "EmilyKnight25610@outlook.com",
            "notes": None,
            "proxy_id": None,
            "concurrency": 30,
            "load_factor": 0,
            "priority": 20,
            "rate_multiplier": 1,
            "status": "active",
            "group_ids": [4],
            "expires_at": None,
            "auto_pause_on_expired": True,
            "credentials": {
                "chatgpt_account_id": "account-id",
                "email": "EmilyKnight25610@outlook.com",
                "expires_at": 1785645865,
                "plan_type": "free",
            },
            "extra": {"privacy_mode": "training_off"},
        }

        with (
            patch.object(client, "_request_admin_response_with_retries", request),
            patch.object(client, "get_account", AsyncMock(return_value=current_account)) as get_account,
        ):
            result = await client.update_account(
                4802,
                {
                    "name": "plus EmilyKnight25610@outlook.com",
                    "group_ids": [6],
                    "credentials": {"plan_type": "plus"},
                },
            )

        get_account.assert_awaited_once_with(4802)
        request.assert_awaited_once()
        self.assertEqual(request.await_args.args[:2], ("PUT", "/accounts/4802"))
        payload = request.await_args.kwargs["json"]
        self.assertEqual(
            payload["credentials"],
            {
                "chatgpt_account_id": "account-id",
                "email": "EmilyKnight25610@outlook.com",
                "expires_at": 1785645865,
                "plan_type": "plus",
            },
        )
        self.assertEqual(payload["group_ids"], [6])
        self.assertEqual(result, {"id": 4802, "credentials": {"plan_type": "plus"}})

    async def test_model_mapping_update_replaces_mapping_and_preserves_other_credentials(self) -> None:
        client = Sub2ApiClient(base_url="http://sub2.example.com", token="admin-key")
        put_response = httpx.Response(
            200,
            json={"code": 0, "data": {"id": 4802, "credentials": {"model_mapping": {}}}},
            request=httpx.Request("PUT", "http://sub2.example.com/api/v1/admin/accounts/4802"),
        )
        request = AsyncMock(return_value=put_response)
        current_account = {
            "id": 4802,
            "name": "account@example.com",
            "status": "active",
            "group_ids": [4],
            "credentials": {
                "chatgpt_account_id": "account-id",
                "email": "account@example.com",
                "model_mapping": {"gpt-5.6-sol": "gpt-5.4"},
                "plan_type": "free",
            },
            "extra": {"privacy_mode": "training_off"},
        }

        with (
            patch.object(client, "_request_admin_response_with_retries", request),
            patch.object(client, "get_account", AsyncMock(return_value=current_account)),
        ):
            await client.update_account(4802, {"credentials": {"model_mapping": {}}})

        self.assertEqual(
            request.await_args.kwargs["json"]["credentials"],
            {
                "chatgpt_account_id": "account-id",
                "email": "account@example.com",
                "model_mapping": {},
                "plan_type": "free",
            },
        )


if __name__ == "__main__":
    unittest.main()
