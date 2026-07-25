from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.modules.growth import analytics_repository as repository
from app.modules.growth.analytics_schemas import (
    TrafficAnalyticsFilters,
    TrafficUsersQuery,
    resolve_traffic_window,
    safe_rate,
)
from app.modules.growth.database import growth_connection


_PUBLIC_SUMMARY_KEYS = (
    "homepage_pv",
    "homepage_uv",
    "link_pv",
    "link_uv",
    "registered_accounts",
    "called_accounts",
    "paid_accounts",
    "second_paid_accounts",
    "continued_accounts",
    "refunded_accounts",
)
_ANALYTICS_STATEMENT_TIMEOUT = "5s"


async def _configure_analytics_connection(connection: Any) -> None:
    await connection.execute(text("SET TRANSACTION READ ONLY"))
    await connection.execute(
        text("SELECT set_config('statement_timeout', :statement_timeout, true)"),
        {"statement_timeout": _ANALYTICS_STATEMENT_TIMEOUT},
    )


def _generated_at(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _mask_account_identifier(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip()
    local, separator, domain = normalized.partition("@")
    if not separator or not local or not domain:
        return normalized
    return f"{local[0]}***@{domain[0]}***"


def _public_user_id(site_id: Any, external_user_id: Any, secret_key: str) -> str:
    message = f"{site_id}\0{external_user_id}".encode("utf-8")
    digest = hmac.new(secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"usr_{digest[:32]}"


def _public_user_item(item: dict[str, Any], secret_key: str) -> dict[str, Any]:
    external_user_id = item.get("external_user_id")
    return {
        **item,
        "public_user_id": _public_user_id(
            item.get("site_id"),
            external_user_id,
            secret_key,
        ),
        "external_user_id": _mask_account_identifier(external_user_id),
        "account_label": _mask_account_identifier(item.get("account_label")),
    }


async def get_traffic_analytics_overview(
    mongo_db: Any,
    filters: TrafficAnalyticsFilters,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = _generated_at(now)
    window = resolve_traffic_window(filters.range_key, now=generated_at)

    async with growth_connection(mongo_db) as connection:
        await _configure_analytics_connection(connection)
        summary_row = await repository.load_traffic_summary(connection, filters, window)
        trends = await repository.load_traffic_trends(connection, filters, window)
        source_breakdown = await repository.load_source_breakdown(connection, filters, window)
        link_performance = await repository.load_link_performance(connection, filters, window)
        amounts = await repository.load_amounts(connection, filters, window)

    summary = {key: int(summary_row.get(key) or 0) for key in _PUBLIC_SUMMARY_KEYS}
    registered = summary["registered_accounts"]
    return {
        "generated_at": generated_at,
        "window": {
            "range": window.range_key,
            "start_at": window.start_at,
            "end_at": window.end_at,
            "bucket": window.bucket,
            "timezone": str(summary_row.get("bucket_timezone") or "UTC"),
        },
        "summary": summary,
        "rates": {
            "homepage_registration_rate": safe_rate(registered, summary["homepage_uv"]),
            "link_registration_rate": safe_rate(
                int(summary_row.get("promotion_registered_accounts") or 0),
                summary["link_uv"],
            ),
            "call_rate": safe_rate(summary["called_accounts"], registered),
            "payment_rate": safe_rate(summary["paid_accounts"], registered),
            "second_payment_rate": safe_rate(summary["second_paid_accounts"], registered),
            "continued_rate": safe_rate(summary["continued_accounts"], registered),
        },
        "amounts": amounts,
        "trends": trends,
        "source_breakdown": source_breakdown,
        "link_performance": link_performance,
    }


async def get_traffic_analytics_users(
    mongo_db: Any,
    query: TrafficUsersQuery,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = _generated_at(now)
    window = resolve_traffic_window(query.range_key, now=generated_at)
    async with growth_connection(mongo_db) as connection:
        await _configure_analytics_connection(connection)
        items, total = await repository.list_milestone_users(connection, query, window)

    secret_key = get_settings().app_secret_key
    return {
        "generated_at": generated_at,
        "items": [
            _public_user_item(item, secret_key)
            for item in items
        ],
        "total": total,
        "limit": query.limit,
        "offset": query.offset,
    }
