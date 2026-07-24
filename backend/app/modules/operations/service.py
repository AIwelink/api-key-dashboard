from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from app.modules.growth.database import growth_connection
from app.modules.operations import repository
from app.modules.operations.cache import operations_response_cache
from app.modules.operations.domain import resolve_operations_window, sync_health
from app.modules.operations.schemas import (
    BalanceAdjustmentCreate,
    ClassificationUpdate,
    ConversionRateCreate,
    InternalUserCreate,
    InternalUserUpdate,
    OperationsQuery,
    RedemptionBatchCreate,
    RefreshRequest,
)
from app.modules.operations.sync import request_operations_refresh
from app.modules.system.client_sites import get_client_site, list_client_sites


class CreditCapabilityUnavailable(RuntimeError):
    code = "capability_unavailable"


class CreditCommandAdapter(Protocol):
    async def create_redemption_batch(
        self,
        *,
        site: dict[str, Any],
        payload: RedemptionBatchCreate,
    ) -> dict[str, Any]: ...

    async def adjust_balance(
        self,
        *,
        site: dict[str, Any],
        payload: BalanceAdjustmentCreate,
    ) -> dict[str, Any]: ...


def _window(query: OperationsQuery, now: datetime | None = None):
    return resolve_operations_window(
        query.range.value,
        now=now or datetime.now(UTC),
        start_at=query.start_at,
        end_at=query.end_at,
    )


async def get_operations_overview(
    mongo_db: Any,
    query: OperationsQuery,
) -> dict[str, Any]:
    window = _window(query)
    key = (
        "overview",
        query.site_id,
        query.segment.value,
        window.start_at.isoformat(),
        window.end_at.isoformat(),
    )

    async def load():
        async with growth_connection(mongo_db) as connection:
            current = await repository.get_operations_summary(
                connection,
                site_id=query.site_id,
                segment=query.segment.value,
                start_at=window.start_at,
                end_at=window.end_at,
            )
            previous = await repository.get_operations_summary(
                connection,
                site_id=query.site_id,
                segment=query.segment.value,
                start_at=window.previous_start_at,
                end_at=window.previous_end_at,
            )
        return {
            "summary": current,
            "previous_summary": previous,
            "window": {
                "start_at": window.start_at.isoformat(),
                "end_at": window.end_at.isoformat(),
                "previous_start_at": window.previous_start_at.isoformat(),
                "previous_end_at": window.previous_end_at.isoformat(),
            },
            "generated_at": datetime.now(UTC).isoformat(),
        }

    return await operations_response_cache.get_or_load(key, load)


async def get_operations_trend_data(
    mongo_db: Any,
    query: OperationsQuery,
) -> dict[str, Any]:
    window = _window(query)
    key = (
        "trends",
        query.site_id,
        query.segment.value,
        window.start_at.isoformat(),
        window.end_at.isoformat(),
    )

    async def load():
        async with growth_connection(mongo_db) as connection:
            items = await repository.get_operations_trends(
                connection,
                site_id=query.site_id,
                segment=query.segment.value,
                start_at=window.start_at,
                end_at=window.end_at,
            )
        return {"items": items, "total": len(items), "generated_at": datetime.now(UTC).isoformat()}

    return await operations_response_cache.get_or_load(key, load)


async def get_operations_user_data(
    mongo_db: Any,
    query: OperationsQuery,
    *,
    search: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    window = _window(query)
    key = (
        "users",
        query.site_id,
        query.segment.value,
        window.start_at.isoformat(),
        window.end_at.isoformat(),
        search,
        limit,
        offset,
    )

    async def load():
        async with growth_connection(mongo_db) as connection:
            items = await repository.list_operations_users(
                connection,
                site_id=query.site_id,
                segment=query.segment.value,
                start_at=window.start_at,
                end_at=window.end_at,
                query=search,
                limit=limit,
                offset=offset,
            )
        return {"items": items, "total": len(items), "generated_at": datetime.now(UTC).isoformat()}

    return await operations_response_cache.get_or_load(key, load)


async def get_operations_sync_status(mongo_db: Any) -> dict[str, Any]:
    key = ("sync-status",)

    async def load():
        now = datetime.now(UTC)
        async with growth_connection(mongo_db) as connection:
            items = await repository.get_sync_status(connection)
        for item in items:
            last_success_at = item.get("last_success_at")
            if isinstance(last_success_at, str):
                last_success_at = datetime.fromisoformat(last_success_at.replace("Z", "+00:00"))
            item["health"] = sync_health(
                now=now,
                last_success_at=last_success_at,
                running=item.get("status") == "running",
            )
        return {"items": items, "total": len(items), "generated_at": now.isoformat()}

    return await operations_response_cache.get_or_load(key, load)


async def refresh_operations(mongo_db: Any, payload: RefreshRequest) -> dict[str, Any]:
    site_ids = payload.site_ids
    if site_ids is None:
        configured = await list_client_sites(mongo_db)
        site_ids = [item["id"] for item in configured["items"] if item.get("status") == "active"]
    results = await asyncio.gather(
        *(request_operations_refresh(mongo_db, site_id=site_id) for site_id in site_ids),
        return_exceptions=True,
    )
    items = []
    for site_id, result in zip(site_ids, results, strict=True):
        if isinstance(result, BaseException):
            items.append({"site_id": site_id, "status": "failed", "error": str(result)[:500]})
        else:
            items.append(result)
    return {"items": items, "total": len(items)}


async def list_internal_user_configs(
    mongo_db: Any,
    *,
    site_id: str | None,
    query: str | None,
) -> dict[str, Any]:
    async with growth_connection(mongo_db) as connection:
        items = await repository.list_internal_users(connection, site_id=site_id, query=query)
    return {"items": items, "total": len(items)}


async def create_internal_user_config(
    mongo_db: Any,
    payload: InternalUserCreate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        result = await repository.create_internal_user(connection, payload, actor_id=actor_id)
    operations_response_cache.invalidate(site_id=payload.site_id)
    return result


async def update_internal_user_config(
    mongo_db: Any,
    internal_user_id: UUID,
    payload: InternalUserUpdate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        result = await repository.update_internal_user(
            connection,
            internal_user_id,
            payload,
            actor_id=actor_id,
        )
    operations_response_cache.invalidate(site_id=result.get("site_id"))
    return result


async def list_conversion_rate_configs(
    mongo_db: Any,
    *,
    site_id: str | None,
) -> dict[str, Any]:
    async with growth_connection(mongo_db) as connection:
        items = await repository.list_conversion_rates(connection, site_id=site_id)
    return {"items": items, "total": len(items)}


async def create_conversion_rate_config(
    mongo_db: Any,
    payload: ConversionRateCreate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        result = await repository.create_conversion_rate(connection, payload, actor_id=actor_id)
    operations_response_cache.invalidate(site_id=payload.site_id)
    return result


async def list_classification_task_configs(
    mongo_db: Any,
    *,
    site_id: str | None,
    status: str,
) -> dict[str, Any]:
    async with growth_connection(mongo_db) as connection:
        items = await repository.list_classification_tasks(
            connection,
            site_id=site_id,
            status=status,
        )
    return {"items": items, "total": len(items)}


async def resolve_classification_task_config(
    mongo_db: Any,
    classification_task_id: UUID,
    payload: ClassificationUpdate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        result = await repository.resolve_classification_task(
            connection,
            classification_task_id,
            payload,
            actor_id=actor_id,
        )
    operations_response_cache.invalidate(site_id=result.get("site_id"))
    return result


async def create_redemption_batch(
    mongo_db: Any,
    payload: RedemptionBatchCreate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    del actor_id
    site = await get_client_site(mongo_db, payload.site_id, include_api_key=True)
    if site is None:
        raise LookupError("client site not found")
    raise CreditCapabilityUnavailable(
        "No verified redemption write adapter is available for this site version"
    )


async def create_balance_adjustment(
    mongo_db: Any,
    payload: BalanceAdjustmentCreate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    del actor_id
    site = await get_client_site(mongo_db, payload.site_id, include_api_key=True)
    if site is None:
        raise LookupError("client site not found")
    raise CreditCapabilityUnavailable(
        "No verified balance-adjustment write adapter is available for this site version"
    )
