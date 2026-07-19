from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.sub2api.hourly_forecast import (
    ForecastInputError,
    ForecastPoint,
    ForecastResult,
    HourlyObservation,
    forecast_hourly_demand,
)
from app.modules.sub2api.hourly_forecast_repository import fetch_group_hourly_observations


FORECAST_HISTORY_DAYS = 56
FORECAST_HORIZON_POINTS = 25
FORECAST_RETENTION_DAYS = 7

ObservationFetcher = Callable[..., Awaitable[list[HourlyObservation]]]
_forecast_locks: dict[str, asyncio.Lock] = {}


async def get_or_create_group_hourly_forecast(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    sql_dsn: str,
    now: datetime,
    observation_fetcher: ObservationFetcher = fetch_group_hourly_observations,
) -> ForecastResult:
    generated_at = _as_utc(now)
    as_of = generated_at.replace(minute=0, second=0, microsecond=0)
    forecast_id = _forecast_id(site_id, group_id, as_of)
    lock_key = f"{site_id}:{int(group_id)}"
    lock = _forecast_locks.setdefault(lock_key, asyncio.Lock())

    async with lock:
        cached = await db.sub2api_hourly_forecasts.find_one({"_id": forecast_id})
        if isinstance(cached, dict):
            return _forecast_from_document(cached)

        history = await observation_fetcher(
            str(sql_dsn),
            group_id=int(group_id),
            start_at=as_of - timedelta(days=FORECAST_HISTORY_DAYS),
            end_at=as_of,
        )
        result = forecast_hourly_demand(history, as_of=as_of, horizons=FORECAST_HORIZON_POINTS)
        document = {
            "_id": forecast_id,
            "site_id": str(site_id),
            "group_id": int(group_id),
            "generated_at": generated_at,
            "expires_at": as_of + timedelta(days=FORECAST_RETENTION_DAYS),
            **asdict(result),
            "points": [asdict(point) for point in result.points],
        }
        await db.sub2api_hourly_forecasts.replace_one({"_id": forecast_id}, document, upsert=True)
        return result


def _forecast_from_document(document: dict[str, Any]) -> ForecastResult:
    raw_points = document.get("points")
    if not isinstance(raw_points, list) or not raw_points:
        raise ForecastInputError("cached forecast points are missing")
    points = tuple(
        ForecastPoint(
            horizon=int(point["horizon"]),
            target_at=_as_utc(point["target_at"]),
            p50=float(point["p50"]),
            p90=float(point["p90"]),
            candidate_count=int(point.get("candidate_count") or 0),
            source=str(point.get("source") or "cached"),
        )
        for point in raw_points
        if isinstance(point, dict)
    )
    if len(points) != len(raw_points):
        raise ForecastInputError("cached forecast contains invalid points")
    return ForecastResult(
        model=str(document.get("model") or ""),
        version=str(document.get("version") or ""),
        as_of=_as_utc(document.get("as_of")),
        readiness=str(document.get("readiness") or "limited"),
        history_hours=int(document.get("history_hours") or 0),
        completeness_ratio=float(document.get("completeness_ratio") or 0),
        points=points,
    )


def _forecast_id(site_id: str, group_id: int, as_of: datetime) -> str:
    timestamp = as_of.isoformat().replace("+00:00", "Z")
    return f"{site_id}:{int(group_id)}:{timestamp}"


def _as_utc(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise ForecastInputError("forecast datetime is invalid")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
