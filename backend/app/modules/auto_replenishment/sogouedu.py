from __future__ import annotations

from typing import Any

import httpx

from app.utils import now_utc


DEFAULT_BASE_URL = "https://sogouedu.cc"
BALANCE_FIELDS = ("balance_fen", "held_fen", "available_fen", "currency")
INVENTORY_FIELDS = (
    "available",
    "missing",
    "needs_production",
    "estimated_total_fen",
    "estimated_unit_price_fen",
    "minimum_remaining_seconds",
    "maximum_remaining_seconds",
)


class SogouEduError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SogouEduClient:
    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.transport = transport
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))

    async def test_connection(self, *, username: str, password: str, product: str) -> dict[str, Any]:
        if not str(username).strip() or not password:
            raise SogouEduError("SogouEdu credentials are not configured")
        if not str(product).strip():
            raise SogouEduError("SogouEdu product is not configured")

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            login = await self._request_json(
                client,
                "POST",
                "/api/customer/login",
                json={"username": str(username).strip(), "password": password},
            )
            token = _token(login)
            if not token:
                raise SogouEduError("SogouEdu login response did not contain a customer token")
            headers = {"X-Customer-Token": token}
            balance_raw = await self._request_json(client, "GET", "/api/customer/balance", headers=headers)
            inventory_raw = await self._request_json(
                client,
                "GET",
                "/api/customer/inventory",
                headers=headers,
                params={"product": str(product).strip(), "quantity": 1},
            )

        return {
            "ok": True,
            "tested_at": now_utc(),
            "balance": _public_fields(_payload(balance_raw), BALANCE_FIELDS),
            "inventory": _public_fields(_payload(inventory_raw), INVENTORY_FIELDS),
        }

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            response = await client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise SogouEduError("SogouEdu could not be reached") from exc
        if response.status_code == 401:
            raise SogouEduError("SogouEdu credentials are invalid", status_code=401)
        if not 200 <= response.status_code < 300:
            raise SogouEduError(
                f"SogouEdu request failed with status {response.status_code}",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise SogouEduError("SogouEdu returned an invalid JSON response", status_code=response.status_code) from exc
        if not isinstance(data, dict):
            raise SogouEduError("SogouEdu returned an invalid JSON response", status_code=response.status_code)
        return data


def _payload(value: dict[str, Any]) -> dict[str, Any]:
    current = value
    for key in ("data", "payload"):
        nested = current.get(key)
        if isinstance(nested, dict):
            current = nested
    return current


def _token(value: dict[str, Any]) -> str:
    payload = _payload(value)
    return str(payload.get("token") or payload.get("access_token") or "").strip()


def _public_fields(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: value[field] for field in fields if value.get(field) is not None}
