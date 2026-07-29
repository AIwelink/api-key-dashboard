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
        active_sources = await repository.load_active_source_breakdown(
            connection, filters, window
        )
        classified_sources = await repository.load_classified_source_breakdown(
            connection, filters, window
        )
        link_performance = await repository.load_link_performance(connection, filters, window)
        quality = await repository.load_data_quality(connection, filters, window)

    homepage_recorded = int(summary_row.get("homepage_recorded_visits") or 0)
    homepage_counted = int(summary_row.get("homepage_counted_pv") or 0)
    facts_pending = int(summary_row.get("facts_pending_accounts") or 0)
    return {
        "generated_at": generated_at,
        "window": {
            "range": window.range_key,
            "start_at": window.start_at,
            "end_at": window.end_at,
            "bucket": window.bucket,
            "timezone": str(summary_row.get("bucket_timezone") or "UTC"),
        },
        "capabilities": {
            "homepage_traffic": "available",
            "link_traffic": "available",
            "registration_attribution": "available",
            "downstream_facts": "unavailable",
        },
        "homepage_summary": {
            "recorded_visits": homepage_recorded,
            "counted_pv": homepage_counted,
            "session_uv": int(summary_row.get("homepage_session_uv") or 0),
            "excluded_visits": int(summary_row.get("homepage_excluded_visits") or 0),
            "valid_rate": safe_rate(homepage_counted, homepage_recorded),
            "latest_event_at": summary_row.get("homepage_latest_event_at"),
        },
        "link_summary": {
            "recorded_visits": int(summary_row.get("link_recorded_visits") or 0),
            "counted_pv": int(summary_row.get("link_counted_pv") or 0),
            "session_uv": int(summary_row.get("link_session_uv") or 0),
            "excluded_visits": int(summary_row.get("link_excluded_visits") or 0),
            "attribution_updates": int(summary_row.get("link_attribution_updates") or 0),
            "latest_event_at": summary_row.get("link_latest_event_at"),
        },
        "registration_summary": {
            "attributed_accounts": int(summary_row.get("attributed_accounts") or 0),
            "excluded_accounts": int(summary_row.get("excluded_accounts") or 0),
            "facts_pending_accounts": facts_pending,
        },
        "traffic_trends": trends,
        "active_source_breakdown": active_sources,
        "classified_source_breakdown": classified_sources,
        "link_performance": link_performance,
        "quality": {**quality, "facts_pending_accounts": facts_pending},
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
        items, total = await repository.list_registration_attributions(
            connection, query, window
        )

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
