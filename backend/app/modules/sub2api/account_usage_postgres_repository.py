from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.system.sql_dsn import parse_sql_dsn


ACCOUNT_USAGE_DATABASE_TIMEOUT_SECONDS = 30
FIVE_HOUR_WINDOW = timedelta(hours=5)
SEVEN_DAY_WINDOW = timedelta(days=7)

ACCOUNT_USAGE_QUERY = """
WITH account_windows AS (
    SELECT account_id, five_hour_start, seven_day_start
    FROM jsonb_to_recordset(CAST(:windows AS jsonb)) AS item(
        account_id bigint,
        five_hour_start timestamptz,
        seven_day_start timestamptz
    )
)
SELECT windows.account_id,
       COUNT(logs.id) FILTER (WHERE logs.created_at >= windows.five_hour_start) AS five_hour_requests,
       COALESCE(SUM(
           COALESCE(logs.input_tokens, 0)
           + COALESCE(logs.output_tokens, 0)
           + COALESCE(logs.cache_creation_tokens, 0)
           + COALESCE(logs.cache_read_tokens, 0)
       ) FILTER (WHERE logs.created_at >= windows.five_hour_start), 0) AS five_hour_tokens,
       COALESCE(SUM(
           COALESCE(logs.account_stats_cost, logs.total_cost)
           * COALESCE(logs.account_rate_multiplier, 1)
       ) FILTER (WHERE logs.created_at >= windows.five_hour_start), 0) AS five_hour_cost,
       COALESCE(SUM(logs.total_cost) FILTER (WHERE logs.created_at >= windows.five_hour_start), 0) AS five_hour_standard_cost,
       COALESCE(SUM(logs.actual_cost) FILTER (WHERE logs.created_at >= windows.five_hour_start), 0) AS five_hour_user_cost,
       COUNT(logs.id) FILTER (WHERE logs.created_at >= windows.seven_day_start) AS seven_day_requests,
       COALESCE(SUM(
           COALESCE(logs.input_tokens, 0)
           + COALESCE(logs.output_tokens, 0)
           + COALESCE(logs.cache_creation_tokens, 0)
           + COALESCE(logs.cache_read_tokens, 0)
       ) FILTER (WHERE logs.created_at >= windows.seven_day_start), 0) AS seven_day_tokens,
       COALESCE(SUM(
           COALESCE(logs.account_stats_cost, logs.total_cost)
           * COALESCE(logs.account_rate_multiplier, 1)
       ) FILTER (WHERE logs.created_at >= windows.seven_day_start), 0) AS seven_day_cost,
       COALESCE(SUM(logs.total_cost) FILTER (WHERE logs.created_at >= windows.seven_day_start), 0) AS seven_day_standard_cost,
       COALESCE(SUM(logs.actual_cost) FILTER (WHERE logs.created_at >= windows.seven_day_start), 0) AS seven_day_user_cost
FROM account_windows AS windows
LEFT JOIN usage_logs AS logs
  ON logs.account_id = windows.account_id
 AND logs.created_at >= LEAST(windows.five_hour_start, windows.seven_day_start)
GROUP BY windows.account_id
ORDER BY windows.account_id ASC
"""


async def fetch_account_usage_snapshots(
    sql_dsn: str,
    *,
    accounts: list[dict[str, Any]],
    observed_at: datetime,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[int, dict[str, Any]]:
    observed_at = _as_utc(observed_at) or datetime.now(UTC)
    account_windows = [_account_window(account, observed_at) for account in accounts]
    account_windows = [window for window in account_windows if window is not None]
    if not account_windows:
        return {}

    parsed_dsn = parse_sql_dsn(sql_dsn, "postgresql")
    engine = None
    try:
        engine = engine_factory(
            parsed_dsn.driver_url(),
            poolclass=NullPool,
            connect_args=parsed_dsn.connect_args(ACCOUNT_USAGE_DATABASE_TIMEOUT_SECONDS),
            isolation_level="REPEATABLE READ",
        )
        parameters = {"windows": json.dumps([window["query"] for window in account_windows])}
        async with asyncio.timeout(ACCOUNT_USAGE_DATABASE_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                result = await connection.execute(text(ACCOUNT_USAGE_QUERY), parameters)
                rows = {_integer(row["account_id"]): dict(row) for row in result.mappings().all()}
        return {
            window["account_id"]: _usage_snapshot(window, rows.get(window["account_id"], {}))
            for window in account_windows
        }
    finally:
        if engine is not None:
            await engine.dispose()


def _account_window(account: dict[str, Any], observed_at: datetime) -> dict[str, Any] | None:
    account_id = _integer_or_none(account.get("id"))
    if account_id is None:
        return None
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    five_hour_reset_at = _as_utc(account.get("codex_5h_reset_at") or extra.get("codex_5h_reset_at"))
    seven_day_reset_at = _as_utc(account.get("codex_7d_reset_at") or extra.get("codex_7d_reset_at"))
    five_hour_window = _window_duration(account, extra, "codex_5h_window_minutes", FIVE_HOUR_WINDOW)
    seven_day_window = _window_duration(account, extra, "codex_7d_window_minutes", SEVEN_DAY_WINDOW)
    five_hour_start = _window_start(five_hour_reset_at, five_hour_window, observed_at)
    seven_day_start = _window_start(seven_day_reset_at, seven_day_window, observed_at)
    return {
        "account_id": account_id,
        "five_hour": _window_metadata(account, extra, "5h", five_hour_reset_at, observed_at),
        "seven_day": _window_metadata(account, extra, "7d", seven_day_reset_at, observed_at),
        "query": {
            "account_id": account_id,
            "five_hour_start": five_hour_start.isoformat(),
            "seven_day_start": seven_day_start.isoformat(),
        },
    }


def _window_start(reset_at: datetime | None, window: timedelta, observed_at: datetime) -> datetime:
    if reset_at is not None and reset_at > observed_at:
        return reset_at - window
    return observed_at - window


def _window_duration(
    account: dict[str, Any],
    extra: dict[str, Any],
    field: str,
    default: timedelta,
) -> timedelta:
    minutes = _number_or_none(account.get(field))
    if minutes is None:
        minutes = _number_or_none(extra.get(field))
    if minutes is None or minutes <= 0:
        return default
    try:
        return timedelta(minutes=minutes)
    except OverflowError:
        return default


def _window_metadata(
    account: dict[str, Any],
    extra: dict[str, Any],
    window: str,
    reset_at: datetime | None,
    observed_at: datetime,
) -> dict[str, Any]:
    utilization = _number_or_none(account.get(f"codex_{window}_used_percent"))
    if utilization is None:
        utilization = _number_or_none(extra.get(f"codex_{window}_used_percent"))
    remaining_seconds = max(0, int((reset_at - observed_at).total_seconds())) if reset_at is not None else 0
    if reset_at is not None and remaining_seconds == 0:
        utilization = 0
    return {
        "utilization": utilization,
        "resets_at": reset_at,
        "remaining_seconds": remaining_seconds,
    }


def _usage_snapshot(window: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {
        "five_hour": _usage_window_snapshot(window["five_hour"], row, "five_hour"),
        "seven_day": _usage_window_snapshot(window["seven_day"], row, "seven_day"),
    }


def _usage_window_snapshot(metadata: dict[str, Any], row: dict[str, Any], prefix: str) -> dict[str, Any] | None:
    stats = _window_stats(row, prefix)
    has_metadata = metadata.get("utilization") is not None or metadata.get("resets_at") is not None
    if not has_metadata and not any(stats.values()):
        return None
    return {**metadata, "window_stats": stats}


def _window_stats(row: dict[str, Any], prefix: str) -> dict[str, Any]:
    return {
        "requests": _integer(row.get(f"{prefix}_requests")),
        "tokens": _integer(row.get(f"{prefix}_tokens")),
        "cost": _number(row.get(f"{prefix}_cost")),
        "standard_cost": _number(row.get(f"{prefix}_standard_cost")),
        "user_cost": _number(row.get(f"{prefix}_user_cost")),
    }


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


def _integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _integer(value: Any) -> int:
    return _integer_or_none(value) or 0


def _number_or_none(value: Any) -> float | int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float:
    parsed = _number_or_none(value)
    return float(parsed) if parsed is not None else 0.0
