from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx

from app.modules.client_metrics.models import (
    AdapterSample,
    QUALITY_COMPLETE,
    QUALITY_COUNTER_RESET,
    QUALITY_DELAYED,
    QUALITY_MISSING,
    missing_sample,
    nonnegative_number,
)


SOURCE = "sub2api_hour_delta"


class Sub2ApiMetricAdapter:
    local_timezone = timezone(timedelta(hours=8))

    def __init__(self, *, http_client: httpx.AsyncClient | None = None) -> None:
        self._http_client = http_client

    async def sample(
        self,
        *,
        site: dict[str, Any],
        bucket_at: datetime,
        cursor: dict[str, Any],
    ) -> AdapterSample:
        bucket_at = _as_utc(bucket_at)
        base_url = str(site.get("base_url") or "").rstrip("/")
        api_key = str(site.get("api_key") or "")
        if not base_url or not api_key:
            return missing_sample(source=SOURCE, error_code="site_not_configured")

        local_date = bucket_at.astimezone(self.local_timezone).date().isoformat()
        params = {
            "start_date": local_date,
            "end_date": local_date,
            "granularity": "hour",
            "include_stats": "true",
            "include_trend": "true",
            "include_model_stats": "false",
            "include_group_stats": "false",
            "include_users_trend": "false",
            "timezone": "Asia/Shanghai",
        }
        try:
            payload = await self._request(
                f"{base_url}/api/v1/admin/dashboard/snapshot-v2",
                headers={"x-api-key": api_key},
                params=params,
            )
        except httpx.TimeoutException:
            return missing_sample(source=SOURCE, error_code="timeout")
        except httpx.HTTPStatusError:
            return missing_sample(source=SOURCE, error_code="http_error")
        except (httpx.RequestError, ValueError):
            return missing_sample(source=SOURCE, error_code="request_failed")

        if payload.get("code") not in (None, 0):
            return missing_sample(source=SOURCE, error_code="upstream_rejected")
        snapshot = payload.get("data", payload)
        if not isinstance(snapshot, dict):
            return missing_sample(source=SOURCE, error_code="invalid_response")
        current = _current_hour_counters(snapshot, bucket_at=bucket_at, local_timezone=self.local_timezone)
        if current is None:
            return missing_sample(source=SOURCE, error_code="current_hour_missing")

        source_bucket_at, total_requests, total_tokens = current
        stats = snapshot.get("stats") if isinstance(snapshot.get("stats"), dict) else {}
        source_updated_at = _datetime_value(stats.get("stats_updated_at") or snapshot.get("generated_at"))
        current_cursor = {
            "source_bucket_at": source_bucket_at,
            "total_requests": total_requests,
            "total_tokens": total_tokens,
            "cursor_sampled_at": bucket_at,
            "source_updated_at": source_updated_at,
        }
        previous = _parse_cursor(cursor)
        if previous is None:
            return AdapterSample(
                rpm=None,
                tpm=None,
                quality=QUALITY_MISSING,
                source=SOURCE,
                source_updated_at=source_updated_at,
                total_requests=total_requests,
                total_tokens=total_tokens,
                error_code="initial_cursor",
                cursor=current_cursor,
            )

        previous_bucket, previous_requests, previous_tokens, previous_sampled_at, previous_updated_at = previous
        if (
            source_bucket_at == previous_bucket
            and total_requests == previous_requests
            and total_tokens == previous_tokens
            and source_updated_at == previous_updated_at
        ):
            return AdapterSample(
                rpm=None,
                tpm=None,
                quality=QUALITY_DELAYED,
                source=SOURCE,
                source_updated_at=source_updated_at,
                total_requests=total_requests,
                total_tokens=total_tokens,
                error_code="upstream_not_updated",
                cursor=dict(cursor),
            )

        elapsed_seconds = (bucket_at - previous_sampled_at).total_seconds()
        if elapsed_seconds <= 0 or source_bucket_at < previous_bucket:
            return _counter_reset_sample(
                source_updated_at=source_updated_at,
                total_requests=total_requests,
                total_tokens=total_tokens,
                cursor=current_cursor,
            )

        if source_bucket_at > previous_bucket:
            request_delta = total_requests
            token_delta = total_tokens
        else:
            request_delta = total_requests - previous_requests
            token_delta = total_tokens - previous_tokens
        if request_delta < 0 or token_delta < 0:
            return _counter_reset_sample(
                source_updated_at=source_updated_at,
                total_requests=total_requests,
                total_tokens=total_tokens,
                cursor=current_cursor,
            )

        minutes = elapsed_seconds / 60
        return AdapterSample(
            rpm=request_delta / minutes,
            tpm=token_delta / minutes,
            quality=QUALITY_COMPLETE,
            source=SOURCE,
            source_updated_at=source_updated_at,
            total_requests=total_requests,
            total_tokens=total_tokens,
            elapsed_seconds=elapsed_seconds,
            cursor=current_cursor,
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
            raise ValueError("Sub2API returned a non-object response")
        return payload


def _counter_reset_sample(
    *,
    source_updated_at: datetime | None,
    total_requests: int,
    total_tokens: int,
    cursor: dict[str, Any],
) -> AdapterSample:
    return AdapterSample(
        rpm=None,
        tpm=None,
        quality=QUALITY_COUNTER_RESET,
        source=SOURCE,
        source_updated_at=source_updated_at,
        total_requests=total_requests,
        total_tokens=total_tokens,
        error_code="counter_reset",
        cursor=cursor,
    )


def _current_hour_counters(
    snapshot: dict[str, Any],
    *,
    bucket_at: datetime,
    local_timezone,
) -> tuple[datetime, int, int] | None:
    target_bucket = bucket_at.astimezone(local_timezone).replace(minute=0, second=0, microsecond=0).astimezone(UTC)
    trend = snapshot.get("trend")
    if not isinstance(trend, list):
        return None
    for item in reversed(trend):
        if not isinstance(item, dict):
            continue
        source_bucket = _parse_hour_bucket(item.get("date"), local_timezone=local_timezone)
        if source_bucket != target_bucket:
            continue
        requests = nonnegative_number(item.get("requests"))
        tokens = nonnegative_number(item.get("total_tokens"))
        if requests is None or tokens is None:
            return None
        return source_bucket, int(requests), int(tokens)
    return None


def _parse_hour_bucket(value: Any, *, local_timezone) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%Y-%m-%d %H:%M").replace(tzinfo=local_timezone).astimezone(UTC)
    except ValueError:
        return None


def _parse_cursor(value: dict[str, Any]) -> tuple[datetime, int, int, datetime, datetime | None] | None:
    source_bucket_at = _datetime_value(value.get("source_bucket_at"))
    sampled_at = _datetime_value(value.get("cursor_sampled_at"))
    requests = nonnegative_number(value.get("total_requests"))
    tokens = nonnegative_number(value.get("total_tokens"))
    if source_bucket_at is None or sampled_at is None or requests is None or tokens is None:
        return None
    return (
        source_bucket_at,
        int(requests),
        int(tokens),
        sampled_at,
        _datetime_value(value.get("source_updated_at")),
    )


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not value:
        return None
    try:
        return _as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
