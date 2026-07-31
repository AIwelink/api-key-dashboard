from __future__ import annotations

import json
import unittest

import httpx

from app.modules.auto_replenishment.sogouedu import SogouEduClient, SogouEduError


class SogouEduClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_reads_login_balance_and_inventory_only(self) -> None:
        requests: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, request.url.path))
            if request.url.path == "/api/customer/login":
                self.assertEqual(json.loads(request.content), {"username": "buyer", "password": "secret"})
                self.assertNotIn("X-Customer-Token", request.headers)
                return httpx.Response(200, json={"token": "customer-token"})
            self.assertEqual(request.headers["X-Customer-Token"], "customer-token")
            if request.url.path == "/api/customer/balance":
                return httpx.Response(
                    200,
                    json={"balance_fen": 10_000, "held_fen": 2_800, "available_fen": 7_200, "currency": "CNY"},
                )
            if request.url.path == "/api/customer/inventory":
                self.assertEqual(request.url.params["product"], "oauth_7d")
                self.assertEqual(request.url.params["quantity"], "1")
                return httpx.Response(
                    200,
                    json={
                        "available": 18,
                        "missing": 0,
                        "needs_production": False,
                        "estimated_total_fen": 500,
                        "estimated_unit_price_fen": 500,
                        "minimum_remaining_seconds": 1_200,
                        "maximum_remaining_seconds": 1_800,
                    },
                )
            raise AssertionError(f"unexpected request: {request.method} {request.url}")

        client = SogouEduClient(transport=httpx.MockTransport(handler))

        result = await client.test_connection(username="buyer", password="secret", product="oauth_7d")

        self.assertEqual(
            requests,
            [
                ("POST", "/api/customer/login"),
                ("GET", "/api/customer/balance"),
                ("GET", "/api/customer/inventory"),
            ],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["balance"]["available_fen"], 7_200)
        self.assertEqual(result["inventory"]["available"], 18)
        self.assertNotIn("token", str(result).lower())
        self.assertFalse(any("pickup" in path or "orders" in path for _, path in requests))

    async def test_connection_accepts_nested_login_and_payload_shapes(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/login"):
                return httpx.Response(200, json={"data": {"token": "nested-token"}})
            if request.url.path.endswith("/balance"):
                return httpx.Response(200, json={"payload": {"balance_fen": 500, "held_fen": 0, "available_fen": 500}})
            return httpx.Response(200, json={"data": {"available": 2, "missing": 0, "needs_production": False}})

        client = SogouEduClient(transport=httpx.MockTransport(handler))

        result = await client.test_connection(username="buyer", password="secret", product="oauth_7d")

        self.assertEqual(result["balance"]["available_fen"], 500)
        self.assertEqual(result["inventory"]["available"], 2)

    async def test_invalid_credentials_return_a_sanitized_error(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "password secret is invalid", "token": "leaked-token"})

        client = SogouEduClient(transport=httpx.MockTransport(handler))

        with self.assertRaisesRegex(SogouEduError, "credentials are invalid") as raised:
            await client.test_connection(username="buyer", password="secret", product="oauth_7d")

        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("leaked-token", str(raised.exception))
        self.assertEqual(raised.exception.status_code, 401)

    async def test_non_json_and_transport_errors_are_sanitized(self) -> None:
        non_json_client = SogouEduClient(
            transport=httpx.MockTransport(lambda _request: httpx.Response(502, text="upstream leaked body"))
        )
        with self.assertRaisesRegex(SogouEduError, "status 502") as non_json_error:
            await non_json_client.test_connection(username="buyer", password="secret", product="oauth_7d")
        self.assertNotIn("leaked body", str(non_json_error.exception))

        def transport_error(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection details that must not be stored")

        transport_client = SogouEduClient(transport=httpx.MockTransport(transport_error))
        with self.assertRaisesRegex(SogouEduError, "could not be reached") as raised:
            await transport_client.test_connection(username="buyer", password="secret", product="oauth_7d")
        self.assertNotIn("connection details", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
