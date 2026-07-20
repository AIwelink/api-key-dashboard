from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.sub2api.hourly_forecast import ForecastInputError
from app.modules.system.sql_dsn import parse_sql_dsn


MINUTE_FORECAST_DATABASE_TIMEOUT_SECONDS = 90

GROUP_MINUTE_FORECAST_QUERY = """
SELECT date_trunc('minute', created_at) AS bucket_at,
       COUNT(id) AS requests,
       COALESCE(SUM(
           COALESCE(input_tokens, 0)
           + COALESCE(output_tokens, 0)
           + COALESCE(cache_creation_tokens, 0)
           + COALESCE(cache_read_tokens, 0)
       ), 0) AS total_tokens,
       COALESCE(SUM(
           COALESCE(account_stats_cost, total_cost)
           * COALESCE(account_rate_multiplier, 1)
       ), 0) AS account_cost
FROM usage_logs
WHERE group_id = :group_id
  AND created_at >= :start_at
  AND created_at < :end_at
GROUP BY date_trunc('minute', created_at)
ORDER BY bucket_at ASC
"""


@dataclass(frozen=True, slots=True)
class MinuteObservation:
    bucket_at: datetime
    account_cost: float
    requests: float
    total_tokens: float


async def fetch_group_minute_observations(
    sql_dsn: str,
    *,
    group_id: int,
    start_at: datetime,
    end_at: datetime,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> list[MinuteObservation]:
    normalized_start = _floor_utc_minute(start_at)
    normalized_end = _floor_utc_minute(end_at)
    if normalized_end <= normalized_start:
        raise ForecastInputError("minute forecast end_at must be after start_at")
    parsed_dsn = parse_sql_dsn(sql_dsn, "postgresql")
    engine = None
    try:
        engine = engine_factory(
            parsed_dsn.driver_url(),
            poolclass=NullPool,
            connect_args=parsed_dsn.connect_args(MINUTE_FORECAST_DATABASE_TIMEOUT_SECONDS),
            isolation_level="REPEATABLE READ",
        )
        async with asyncio.timeout(MINUTE_FORECAST_DATABASE_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                result = await connection.execute(
                    text(GROUP_MINUTE_FORECAST_QUERY),
                    {
                        "group_id": int(group_id),
                        "start_at": normalized_start,
                        "end_at": normalized_end,
                    },
                )
                rows = [dict(row) for row in result.mappings().all()]
        return _complete_minute_series(rows, end_at=normalized_end)
    finally:
        if engine is not None:
            await engine.dispose()


def _complete_minute_series(
    rows: list[dict[str, Any]],
    *,
    end_at: datetime,
) -> list[MinuteObservation]:
    by_bucket: dict[datetime, MinuteObservation] = {}
    for row in rows:
        bucket_at = _floor_utc_minute(row.get("bucket_at"))
        if bucket_at >= end_at:
            continue
        by_bucket[bucket_at] = MinuteObservation(
            bucket_at=bucket_at,
            account_cost=_number(row.get("account_cost")),
            requests=_number(row.get("requests")),
            total_tokens=_number(row.get("total_tokens")),
        )
    if not by_bucket:
        return []
    completed = []
    bucket_at = min(by_bucket)
    while bucket_at < end_at:
        completed.append(
            by_bucket.get(
                bucket_at,
                MinuteObservation(
                    bucket_at=bucket_at,
                    account_cost=0.0,
                    requests=0.0,
                    total_tokens=0.0,
                ),
            )
        )
        bucket_at += timedelta(minutes=1)
    return completed


def _floor_utc_minute(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ForecastInputError("minute repository datetimes must be timezone-aware")
    return value.astimezone(UTC).replace(second=0, microsecond=0)


def _number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ForecastInputError("minute forecast source returned a non-numeric aggregate") from exc
    if number < 0:
        raise ForecastInputError("minute forecast source returned a negative aggregate")
    return number
