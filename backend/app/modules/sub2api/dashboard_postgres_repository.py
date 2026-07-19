from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.system.sql_dsn import parse_sql_dsn


DASHBOARD_DATABASE_TIMEOUT_SECONDS = 20
DASHBOARD_LOCAL_TZ = timezone(timedelta(hours=8))

HOURLY_QUERY = """
SELECT bucket_start AS bucket,
       total_requests,
       input_tokens,
       output_tokens,
       cache_creation_tokens,
       cache_read_tokens,
       total_cost,
       actual_cost,
       account_cost,
       computed_at
FROM usage_dashboard_hourly
WHERE bucket_start >= :start_at AND bucket_start < :end_at
ORDER BY bucket_start ASC
"""

DAILY_QUERY = """
SELECT bucket_date AS bucket,
       total_requests,
       input_tokens,
       output_tokens,
       cache_creation_tokens,
       cache_read_tokens,
       total_cost,
       actual_cost,
       account_cost,
       computed_at
FROM usage_dashboard_daily
WHERE bucket_date >= :start_date AND bucket_date <= :end_date
ORDER BY bucket_date ASC
"""

GROUP_HOURLY_QUERY = """
SELECT date_trunc('hour', created_at AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai' AS bucket,
       COUNT(id) AS total_requests,
       COALESCE(SUM(input_tokens), 0) AS input_tokens,
       COALESCE(SUM(output_tokens), 0) AS output_tokens,
       COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
       COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
       COALESCE(SUM(total_cost), 0) AS total_cost,
       COALESCE(SUM(actual_cost), 0) AS actual_cost,
       COALESCE(SUM(COALESCE(account_stats_cost, total_cost) * COALESCE(account_rate_multiplier, 1)), 0) AS account_cost,
       MAX(created_at) AS computed_at
FROM usage_logs
WHERE group_id = :group_id
  AND created_at >= :start_at
  AND created_at < :end_at
GROUP BY bucket
ORDER BY bucket ASC
"""

GROUP_DAILY_QUERY = """
SELECT (created_at AT TIME ZONE 'Asia/Shanghai')::date AS bucket,
       COUNT(id) AS total_requests,
       COALESCE(SUM(input_tokens), 0) AS input_tokens,
       COALESCE(SUM(output_tokens), 0) AS output_tokens,
       COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
       COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
       COALESCE(SUM(total_cost), 0) AS total_cost,
       COALESCE(SUM(actual_cost), 0) AS actual_cost,
       COALESCE(SUM(COALESCE(account_stats_cost, total_cost) * COALESCE(account_rate_multiplier, 1)), 0) AS account_cost,
       MAX(created_at) AS computed_at
FROM usage_logs
WHERE group_id = :group_id
  AND created_at >= :start_at
  AND created_at < :end_at
GROUP BY bucket
ORDER BY bucket ASC
"""

MODEL_STATISTICS_QUERY = """
SELECT COALESCE(NULLIF(model, ''), NULLIF(upstream_model, ''), 'unknown') AS model,
       COUNT(id) AS total_requests,
       COALESCE(SUM(input_tokens), 0) AS input_tokens,
       COALESCE(SUM(output_tokens), 0) AS output_tokens,
       COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
       COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
       COALESCE(SUM(total_cost), 0) AS total_cost,
       COALESCE(SUM(actual_cost), 0) AS actual_cost,
       COALESCE(SUM(COALESCE(account_stats_cost, total_cost) * COALESCE(account_rate_multiplier, 1)), 0) AS account_cost
FROM usage_logs
WHERE created_at >= :start_at
  AND created_at < :end_at
  AND (CAST(:group_id AS bigint) IS NULL OR group_id = CAST(:group_id AS bigint))
GROUP BY COALESCE(NULLIF(model, ''), NULLIF(upstream_model, ''), 'unknown')
ORDER BY total_cost DESC, model ASC
"""

GROUP_HOUR_COUNTERS_QUERY = """
SELECT group_id,
       COUNT(id) AS total_requests,
       COALESCE(SUM(
           COALESCE(input_tokens, 0)
           + COALESCE(output_tokens, 0)
           + COALESCE(cache_creation_tokens, 0)
           + COALESCE(cache_read_tokens, 0)
       ), 0) AS total_tokens,
       MAX(created_at) AS source_updated_at
FROM usage_logs
WHERE group_id = ANY(CAST(:group_ids AS bigint[]))
  AND created_at >= :start_at
  AND created_at <= :sampled_at
GROUP BY group_id
ORDER BY group_id ASC
"""


async def fetch_site_dashboard_snapshot(
    sql_dsn: str,
    *,
    start_date: str,
    end_date: str,
    granularity: str,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    normalized_granularity = str(granularity or "").strip().lower()
    if normalized_granularity not in {"hour", "day"}:
        raise ValueError("dashboard granularity must be hour or day")
    parsed_start = date.fromisoformat(start_date)
    parsed_end = date.fromisoformat(end_date)
    if parsed_end < parsed_start:
        raise ValueError("dashboard end_date must not be before start_date")

    query, parameters = _query_and_parameters(normalized_granularity, parsed_start, parsed_end)
    parsed_dsn = parse_sql_dsn(sql_dsn, "postgresql")
    engine = None
    try:
        engine = engine_factory(
            parsed_dsn.driver_url(),
            poolclass=NullPool,
            connect_args=parsed_dsn.connect_args(DASHBOARD_DATABASE_TIMEOUT_SECONDS),
            isolation_level="REPEATABLE READ",
        )
        async with asyncio.timeout(DASHBOARD_DATABASE_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                result = await connection.execute(text(query), parameters)
                rows = [dict(row) for row in result.mappings().all()]
        return _snapshot_from_rows(
            rows,
            start_date=start_date,
            end_date=end_date,
            granularity=normalized_granularity,
        )
    finally:
        if engine is not None:
            await engine.dispose()


async def fetch_group_dashboard_snapshot(
    sql_dsn: str,
    *,
    group_id: int,
    start_date: str,
    end_date: str,
    granularity: str,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    normalized_granularity = str(granularity or "").strip().lower()
    if normalized_granularity not in {"hour", "day"}:
        raise ValueError("dashboard granularity must be hour or day")
    parsed_start = date.fromisoformat(start_date)
    parsed_end = date.fromisoformat(end_date)
    if parsed_end < parsed_start:
        raise ValueError("dashboard end_date must not be before start_date")
    _, parameters = _query_and_parameters("hour", parsed_start, parsed_end)
    parameters["group_id"] = int(group_id)
    query = GROUP_HOURLY_QUERY if normalized_granularity == "hour" else GROUP_DAILY_QUERY
    rows = await _fetch_rows(sql_dsn, query, parameters, engine_factory=engine_factory)
    return _snapshot_from_rows(
        rows,
        start_date=start_date,
        end_date=end_date,
        granularity=normalized_granularity,
    )


async def fetch_model_statistics(
    sql_dsn: str,
    *,
    start_date: str,
    end_date: str,
    group_id: int | None = None,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> list[dict[str, Any]]:
    parsed_start = date.fromisoformat(start_date)
    parsed_end = date.fromisoformat(end_date)
    if parsed_end < parsed_start:
        raise ValueError("dashboard end_date must not be before start_date")
    _, parameters = _query_and_parameters("hour", parsed_start, parsed_end)
    parameters["group_id"] = int(group_id) if group_id is not None else None
    rows = await _fetch_rows(sql_dsn, MODEL_STATISTICS_QUERY, parameters, engine_factory=engine_factory)
    return [_model_item(row) for row in rows]


async def fetch_group_hour_counters(
    sql_dsn: str,
    *,
    group_ids: list[int],
    sampled_at: datetime,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[int, dict[str, Any]]:
    normalized_group_ids = sorted({int(group_id) for group_id in group_ids})
    if not normalized_group_ids:
        return {}
    sampled_at = _as_utc(sampled_at) or datetime.now(UTC)
    local_hour = sampled_at.astimezone(DASHBOARD_LOCAL_TZ).replace(minute=0, second=0, microsecond=0)
    rows = await _fetch_rows(
        sql_dsn,
        GROUP_HOUR_COUNTERS_QUERY,
        {
            "group_ids": normalized_group_ids,
            "start_at": local_hour.astimezone(UTC),
            "sampled_at": sampled_at,
        },
        engine_factory=engine_factory,
    )
    result = {
        group_id: {
            "total_requests": 0,
            "total_tokens": 0,
            "source_updated_at": None,
        }
        for group_id in normalized_group_ids
    }
    for row in rows:
        group_id = _integer(row.get("group_id"))
        if group_id not in result:
            continue
        result[group_id] = {
            "total_requests": _integer(row.get("total_requests")),
            "total_tokens": _integer(row.get("total_tokens")),
            "source_updated_at": _as_utc(row.get("source_updated_at")),
        }
    return result


async def _fetch_rows(
    sql_dsn: str,
    query: str,
    parameters: dict[str, Any],
    *,
    engine_factory: Callable[..., Any],
) -> list[dict[str, Any]]:
    parsed_dsn = parse_sql_dsn(sql_dsn, "postgresql")
    engine = None
    try:
        engine = engine_factory(
            parsed_dsn.driver_url(),
            poolclass=NullPool,
            connect_args=parsed_dsn.connect_args(DASHBOARD_DATABASE_TIMEOUT_SECONDS),
            isolation_level="REPEATABLE READ",
        )
        async with asyncio.timeout(DASHBOARD_DATABASE_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                result = await connection.execute(text(query), parameters)
                return [dict(row) for row in result.mappings().all()]
    finally:
        if engine is not None:
            await engine.dispose()


def _query_and_parameters(granularity: str, start_date: date, end_date: date) -> tuple[str, dict[str, Any]]:
    if granularity == "day":
        return DAILY_QUERY, {"start_date": start_date, "end_date": end_date}
    start_at = datetime.combine(start_date, time.min, tzinfo=DASHBOARD_LOCAL_TZ).astimezone(UTC)
    end_at = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=DASHBOARD_LOCAL_TZ).astimezone(UTC)
    return HOURLY_QUERY, {"start_at": start_at, "end_at": end_at}


def _snapshot_from_rows(
    rows: list[dict[str, Any]],
    *,
    start_date: str,
    end_date: str,
    granularity: str,
) -> dict[str, Any]:
    trend = [_trend_item(row, granularity) for row in rows]
    computed = [_as_utc(row.get("computed_at")) for row in rows]
    generated_at = max((value for value in computed if value is not None), default=None)
    return {
        "generated_at": generated_at,
        "granularity": granularity,
        "start_date": start_date,
        "end_date": end_date,
        "trend": trend,
        "models": [],
    }


def _trend_item(row: dict[str, Any], granularity: str) -> dict[str, Any]:
    input_tokens = _integer(row.get("input_tokens"))
    output_tokens = _integer(row.get("output_tokens"))
    cache_creation_tokens = _integer(row.get("cache_creation_tokens"))
    cache_read_tokens = _integer(row.get("cache_read_tokens"))
    return {
        "date": _bucket_label(row.get("bucket"), granularity),
        "requests": _integer(row.get("total_requests")),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_tokens": input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens,
        "cost": _number(row.get("total_cost")),
        "actual_cost": _number(row.get("actual_cost")),
        "account_cost": _number(row.get("account_cost")),
    }


def _model_item(row: dict[str, Any]) -> dict[str, Any]:
    input_tokens = _integer(row.get("input_tokens"))
    output_tokens = _integer(row.get("output_tokens"))
    cache_creation_tokens = _integer(row.get("cache_creation_tokens"))
    cache_read_tokens = _integer(row.get("cache_read_tokens"))
    return {
        "model": str(row.get("model") or "unknown"),
        "requests": _integer(row.get("total_requests")),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_tokens": input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens,
        "cost": _number(row.get("total_cost")),
        "actual_cost": _number(row.get("actual_cost")),
        "account_cost": _number(row.get("account_cost")),
    }


def _bucket_label(value: Any, granularity: str) -> str:
    if granularity == "day":
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return date.fromisoformat(str(value)).isoformat()
    parsed = _as_utc(value)
    if parsed is None:
        raise ValueError("hourly dashboard bucket is invalid")
    return parsed.astimezone(DASHBOARD_LOCAL_TZ).strftime("%Y-%m-%d %H:%M")


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _integer(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _number(value: Any) -> float:
    if isinstance(value, bool) or value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
