import json
import time
from typing import Any

import httpx
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.utils import now_utc


class Sub2ApiClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = (settings.sub2api_base_url or "").rstrip("/")
        self.token = settings.sub2api_token

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def headers(self) -> dict[str, str]:
        if not self.token:
            return {}
        return {"x-api-key": self.token}

    def admin_url(self, path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url}/api/v1/admin{normalized_path}"

    async def request_admin(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SUB2API_BASE_URL is not configured",
            )
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.request(
                method,
                self.admin_url(path),
                headers=self.headers(),
                params=params,
                json=json,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"sub2api returned non-JSON response: {response.text[:200]}",
            ) from exc

        if response.status_code >= 400:
            message = payload.get("message") if isinstance(payload, dict) else None
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=message or f"sub2api request failed with status {response.status_code}",
            )
        return payload if isinstance(payload, dict) else {"data": payload}

    async def create_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.request_admin("POST", "/accounts", json=payload)
        return response.get("data", response)

    async def get_account(self, account_id: int | str) -> dict[str, Any]:
        response = await self.request_admin("GET", f"/accounts/{account_id}")
        return response.get("data", response)

    async def delete_account(self, account_id: int | str) -> dict[str, Any]:
        if not self.configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SUB2API_BASE_URL is not configured",
            )
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.delete(
                self.admin_url(f"/accounts/{account_id}"),
                headers=self.headers(),
            )
        if response.status_code >= 400:
            message = response.text[:300]
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    message = str(payload.get("message") or payload.get("detail") or message)
            except ValueError:
                pass
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=message or f"sub2api delete failed with status {response.status_code}",
            )
        if response.status_code == 204 or not response.text.strip():
            return {"ok": True, "status_code": response.status_code}
        try:
            payload = response.json()
        except ValueError:
            return {"ok": True, "status_code": response.status_code, "text": response.text[:300]}
        return payload if isinstance(payload, dict) else {"data": payload}

    async def test_account(
        self,
        account_id: int | str,
        *,
        model_id: str = "gpt-5.4-mini",
        prompt: str = "",
    ) -> dict[str, Any]:
        if not self.configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="SUB2API_BASE_URL is not configured",
            )

        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                self.admin_url(f"/accounts/{account_id}/test"),
                headers=self.headers(),
                json={"model_id": model_id, "prompt": prompt},
            )

        latency_ms = round((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"sub2api account test failed with status {response.status_code}: {response.text[:300]}",
            )

        events = parse_sse_data_lines(response.text)
        content = "".join(str(event.get("text", "")) for event in events if event.get("type") == "content")
        complete_event = next((event for event in reversed(events) if event.get("type") == "test_complete"), None)
        success = bool(complete_event and complete_event.get("success") is True)
        return {
            "success": success,
            "model": model_id,
            "prompt": prompt,
            "latency_ms": latency_ms,
            "response_preview": content[:500],
            "events": events[-20:],
        }

    async def list_groups(self, *, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        payload = await self.request_admin("GET", "/groups", params={"page": page, "page_size": page_size})
        return payload.get("data", payload)

    async def list_accounts(
        self,
        *,
        group_id: int | None = None,
        status_filter: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        if group_id is not None:
            return await self.list_group_accounts(
                group_id=group_id,
                status_filter=status_filter,
                page=page,
                page_size=page_size,
            )
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if status_filter:
            params["status"] = status_filter
        payload = await self.request_admin("GET", "/accounts", params=params)
        return payload.get("data", payload)

    async def list_group_accounts(
        self,
        *,
        group_id: int,
        status_filter: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        fetch_page_size = 200
        remote_page = 1
        matched: list[dict[str, Any]] = []
        remote_total = None
        max_pages = 50

        while remote_page <= max_pages:
            params: dict[str, Any] = {"page": remote_page, "page_size": fetch_page_size}
            if status_filter:
                params["status"] = status_filter
            payload = await self.request_admin("GET", "/accounts", params=params)
            data = payload.get("data", payload)
            items = data.get("items", []) if isinstance(data, dict) else []
            if remote_total is None and isinstance(data, dict):
                remote_total = data.get("total")

            matched.extend([item for item in items if account_in_group(item, group_id)])
            if not items:
                break
            if remote_total is not None and remote_page * fetch_page_size >= int(remote_total):
                break
            remote_page += 1

        start = max(0, (page - 1) * page_size)
        end = start + page_size
        total = len(matched)
        return {
            "items": matched[start:end],
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "remote_total": remote_total,
        }

    async def test_connection(self) -> dict[str, Any]:
        if not self.configured:
            return {"ok": False, "message": "SUB2API_BASE_URL is not configured"}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                self.admin_url("/accounts"),
                headers=self.headers(),
                params={"page": 1, "page_size": 1},
            )

        result: dict[str, Any] = {
            "ok": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "base_url": self.base_url,
            "auth_header": "x-api-key" if self.token else None,
            "endpoint": "/api/v1/admin/accounts",
        }
        try:
            data = response.json()
            if isinstance(data, dict):
                result["response_keys"] = list(data.keys())
                if "total" in data:
                    result["total"] = data["total"]
                elif isinstance(data.get("data"), dict) and "total" in data["data"]:
                    result["total"] = data["data"]["total"]
            else:
                result["response_type"] = type(data).__name__
        except ValueError:
            result["message"] = response.text[:300]
        return result


def account_in_group(account: dict[str, Any], group_id: int) -> bool:
    group_ids = account.get("group_ids")
    if isinstance(group_ids, list) and group_id in group_ids:
        return True

    groups = account.get("groups")
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, dict) and group.get("id") == group_id:
                return True

    account_groups = account.get("account_groups")
    if isinstance(account_groups, list):
        for account_group in account_groups:
            if isinstance(account_group, dict) and account_group.get("group_id") == group_id:
                return True

    return False


def parse_sse_data_lines(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if not data or data == "[DONE]":
            continue
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            events.append({"type": "raw", "text": data})
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


async def refresh_account_observation(
    db: AsyncIOMotorDatabase,
    account: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(account.get("metadata", {}))
    metadata["last_checked_at"] = now_utc()
    metadata.setdefault("account_status", "unknown")
    metadata.setdefault("used_quota", None)
    metadata.setdefault("last_request_at", None)
    metadata["last_error"] = "sub2api status endpoint not configured yet"
    await db.accounts.update_one({"_id": account["_id"]}, {"$set": {"metadata": metadata}})
    updated = await db.accounts.find_one({"_id": account["_id"]})
    return updated
