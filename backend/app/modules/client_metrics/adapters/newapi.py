from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from app.modules.client_metrics.models import (
    AdapterSample,
    QUALITY_COMPLETE,
    missing_sample,
    nonnegative_number,
)


SOURCE = "newapi_reported"


class NewApiMetricAdapter:
    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    async def sample(
        self,
        *,
        site: dict[str, Any],
        bucket_at,
        cursor: dict[str, Any],
    ) -> AdapterSample:
        del cursor
        base_url = str(site.get("base_url") or "").rstrip("/")
        api_key = str(site.get("api_key") or "")
        admin_user_id = str(site.get("admin_user_id") or "")
        if not base_url or not api_key or not admin_user_id:
            return missing_sample(source=SOURCE, error_code="site_not_configured")

        params = {
            "p": 1,
            "page_size": 1,
            "type": 0,
            "start_timestamp": int(bucket_at.timestamp()),
            "end_timestamp": int((bucket_at + timedelta(seconds=59)).timestamp()),
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "New-Api-User": admin_user_id,
        }
        try:
            payload = await self._request(
                f"{base_url}/api/log/stat",
                headers=headers,
                params=params,
            )
        except httpx.TimeoutException:
            return missing_sample(source=SOURCE, error_code="timeout")
        except httpx.HTTPStatusError:
            return missing_sample(source=SOURCE, error_code="http_error")
        except (httpx.RequestError, ValueError):
            return missing_sample(source=SOURCE, error_code="request_failed")

        if payload.get("success") is not True:
            return missing_sample(source=SOURCE, error_code="upstream_rejected")
        data = payload.get("data")
        if not isinstance(data, dict):
            return missing_sample(source=SOURCE, error_code="invalid_response")
        rpm = nonnegative_number(data.get("rpm"))
        tpm = nonnegative_number(data.get("tpm"))
        if rpm is None or tpm is None:
            return missing_sample(source=SOURCE, error_code="invalid_metrics")
        return AdapterSample(
            rpm=rpm,
            tpm=tpm,
            quality=QUALITY_COMPLETE,
            source=SOURCE,
        )

    async def _request(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if self._http_client is not None:
            response = await self._http_client.get(url, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
        else:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, headers=headers, params=params)
                response.raise_for_status()
                payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("NewAPI returned a non-object response")
        return payload
