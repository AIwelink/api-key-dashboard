from __future__ import annotations

import asyncio
import hashlib
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from app.modules.growth.database import growth_connection
from app.modules.operations import repository
from app.modules.operations.cache import operations_response_cache
from app.modules.operations.credit_commands import (
    CreditCapabilityUnavailable,
    create_credit_command_adapter,
)
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
from app.modules.system.client_sites import get_client_site


HISTORICAL_CONVERSION_RATE_START = datetime(1970, 1, 1, tzinfo=UTC)
REDEMPTION_REMOTE_PAGE_SIZE = 1000
REDEMPTION_REMOTE_MAX_PAGES = 10


class OperationsSiteAccessDenied(PermissionError):
    pass


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
    *,
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    window = _window(query)
    key = (
        "overview",
        allowed_site_ids,
        query.segment.value,
        window.start_at.isoformat(),
        window.end_at.isoformat(),
    )

    async def load():
        async with growth_connection(mongo_db) as connection:
            current = await repository.get_operations_summary(
                connection,
                allowed_site_ids=allowed_site_ids,
                segment=query.segment.value,
                start_at=window.start_at,
                end_at=window.end_at,
            )
            previous = await repository.get_operations_summary(
                connection,
                allowed_site_ids=allowed_site_ids,
                segment=query.segment.value,
                start_at=window.previous_start_at,
                end_at=window.previous_end_at,
            )
            site_breakdown = await repository.get_operations_site_breakdown(
                connection,
                allowed_site_ids=allowed_site_ids,
                segment=query.segment.value,
                start_at=window.start_at,
                end_at=window.end_at,
            )
        return {
            "summary": current,
            "previous_summary": previous,
            "site_breakdown": site_breakdown,
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
    *,
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    window = _window(query)
    key = (
        "trends",
        allowed_site_ids,
        query.segment.value,
        window.start_at.isoformat(),
        window.end_at.isoformat(),
    )

    async def load():
        async with growth_connection(mongo_db) as connection:
            items = await repository.get_operations_trends(
                connection,
                allowed_site_ids=allowed_site_ids,
                segment=query.segment.value,
                start_at=window.start_at,
                end_at=window.end_at,
            )
        return {"items": items, "total": len(items), "generated_at": datetime.now(UTC).isoformat()}

    return await operations_response_cache.get_or_load(key, load)


async def get_operations_lifecycle_data(
    mongo_db: Any,
    query: OperationsQuery,
    *,
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    window = _window(query)
    key = (
        "lifecycle",
        allowed_site_ids,
        query.segment.value,
        window.start_at.isoformat(),
        window.end_at.isoformat(),
    )

    async def load():
        async with growth_connection(mongo_db) as connection:
            summary_rows = await repository.get_operations_lifecycle_summary(
                connection,
                allowed_site_ids=allowed_site_ids,
                segment=query.segment.value,
                start_at=window.start_at,
                end_at=window.end_at,
            )
            retention = await repository.get_operations_retention(
                connection,
                allowed_site_ids=allowed_site_ids,
                segment=query.segment.value,
                start_at=window.start_at,
                end_at=window.end_at,
            )
            model_breakdown = await repository.get_operations_model_breakdown(
                connection,
                allowed_site_ids=allowed_site_ids,
                segment=query.segment.value,
                start_at=window.start_at,
                end_at=window.end_at,
            )
            customer_breakdown = await repository.get_operations_customer_breakdown(
                connection,
                allowed_site_ids=allowed_site_ids,
                segment=query.segment.value,
                start_at=window.start_at,
                end_at=window.end_at,
            )
        summary = next((row for row in summary_rows if row.get("scope") == "all"), {})
        site_breakdown = [row for row in summary_rows if row.get("scope") == "site"]
        return {
            "summary": summary,
            "retention": retention,
            "site_breakdown": site_breakdown,
            "model_breakdown": model_breakdown,
            "customer_breakdown": customer_breakdown,
            "window": {
                "start_at": window.start_at.isoformat(),
                "end_at": window.end_at.isoformat(),
            },
            "generated_at": datetime.now(UTC).isoformat(),
        }

    return await operations_response_cache.get_or_load(key, load)


async def get_operations_user_data(
    mongo_db: Any,
    query: OperationsQuery,
    *,
    search: str | None,
    limit: int,
    offset: int,
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    window = _window(query)
    key = (
        "users",
        allowed_site_ids,
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
                allowed_site_ids=allowed_site_ids,
                segment=query.segment.value,
                start_at=window.start_at,
                end_at=window.end_at,
                query=search,
                limit=limit,
                offset=offset,
            )
        return {"items": items, "total": len(items), "generated_at": datetime.now(UTC).isoformat()}

    return await operations_response_cache.get_or_load(key, load)


async def get_operations_sync_status(
    mongo_db: Any,
    *,
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    key = ("sync-status", allowed_site_ids)

    async def load():
        now = datetime.now(UTC)
        async with growth_connection(mongo_db) as connection:
            items = await repository.get_sync_status(connection, allowed_site_ids=allowed_site_ids)
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


async def refresh_operations(
    mongo_db: Any,
    payload: RefreshRequest,
    *,
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    del payload
    site_ids = list(allowed_site_ids)
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
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    del site_id
    async with growth_connection(mongo_db) as connection:
        items = await repository.list_internal_users(
            connection,
            allowed_site_ids=allowed_site_ids,
            query=query,
        )
    return {"items": items, "total": len(items)}


async def create_internal_user_config(
    mongo_db: Any,
    payload: InternalUserCreate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        await repository.acquire_operations_sync_lock(connection, site_id=payload.site_id)
        result = await repository.create_internal_user(connection, payload, actor_id=actor_id)
        if result.get("recognition_status") == "recognized":
            await repository.replace_affected_aggregates(
                connection,
                site_id=payload.site_id,
                start_at=HISTORICAL_CONVERSION_RATE_START,
                end_at=datetime.now(UTC),
            )
    operations_response_cache.invalidate(site_id=payload.site_id)
    return result


async def update_internal_user_config(
    mongo_db: Any,
    internal_user_id: UUID,
    payload: InternalUserUpdate,
    *,
    actor_id: str,
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        site_id = await repository.get_internal_user_site_id(connection, internal_user_id)
        if site_id is None:
            raise repository.OperationsNotFoundError("internal user not found")
        if site_id not in allowed_site_ids:
            raise OperationsSiteAccessDenied("Operations site access denied")
        await repository.acquire_operations_sync_lock(connection, site_id=site_id)
        result = await repository.update_internal_user(
            connection,
            internal_user_id,
            payload,
            actor_id=actor_id,
        )
        await repository.replace_affected_aggregates(
            connection,
            site_id=site_id,
            start_at=HISTORICAL_CONVERSION_RATE_START,
            end_at=datetime.now(UTC),
        )
    operations_response_cache.invalidate(site_id=site_id)
    return result


async def delete_internal_user_config(
    mongo_db: Any,
    internal_user_id: UUID,
    *,
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        site_id = await repository.get_internal_user_site_id(connection, internal_user_id)
        if site_id is None:
            raise repository.OperationsNotFoundError("internal user not found")
        if site_id not in allowed_site_ids:
            raise OperationsSiteAccessDenied("Operations site access denied")
        await repository.acquire_operations_sync_lock(connection, site_id=site_id)
        result = await repository.delete_internal_user(connection, internal_user_id)
        await repository.replace_affected_aggregates(
            connection,
            site_id=site_id,
            start_at=HISTORICAL_CONVERSION_RATE_START,
            end_at=datetime.now(UTC),
        )
    operations_response_cache.invalidate(site_id=site_id)
    return result


async def list_conversion_rate_configs(
    mongo_db: Any,
    *,
    site_id: str | None,
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    del site_id
    async with growth_connection(mongo_db) as connection:
        items = await repository.list_conversion_rates(
            connection,
            allowed_site_ids=allowed_site_ids,
        )
    return {"items": items, "total": len(items)}


async def create_conversion_rate_config(
    mongo_db: Any,
    payload: ConversionRateCreate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        existing_rates = await repository.list_conversion_rates(
            connection,
            allowed_site_ids=(payload.site_id,),
        )
        selected_payload = payload
        if not existing_rates and "effective_from" not in payload.model_fields_set:
            selected_payload = payload.model_copy(
                update={"effective_from": HISTORICAL_CONVERSION_RATE_START}
            )
        result = await repository.create_conversion_rate(
            connection,
            selected_payload,
            actor_id=actor_id,
        )
    operations_response_cache.invalidate(site_id=payload.site_id)
    return result


async def list_classification_task_configs(
    mongo_db: Any,
    *,
    site_id: str | None,
    status: str,
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    del site_id
    async with growth_connection(mongo_db) as connection:
        items = await repository.list_classification_tasks(
            connection,
            allowed_site_ids=allowed_site_ids,
            status=status,
        )
    return {"items": items, "total": len(items)}


async def resolve_classification_task_config(
    mongo_db: Any,
    classification_task_id: UUID,
    payload: ClassificationUpdate,
    *,
    actor_id: str,
    allowed_site_ids: tuple[str, ...],
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        site_id = await repository.get_classification_task_site_id(
            connection,
            classification_task_id,
        )
        if site_id is None:
            raise repository.OperationsNotFoundError("classification task not found")
        if site_id not in allowed_site_ids:
            raise OperationsSiteAccessDenied("Operations site access denied")
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
    site = await get_client_site(mongo_db, payload.site_id, include_api_key=True)
    if site is None:
        raise LookupError("client site not found")
    adapter = create_credit_command_adapter(site)
    async with growth_connection(mongo_db, write=True) as connection:
        batch = await repository.get_redemption_batch_by_idempotency(
            connection,
            site_id=payload.site_id,
            idempotency_key=payload.idempotency_key,
        )
        if batch is None:
            batch = await repository.create_redemption_batch_request(
                connection,
                payload,
                actor_id=actor_id,
            )
        else:
            _validate_redemption_idempotent_replay(batch, payload)
            if batch.get("command_status") == "succeeded":
                return batch | {
                    "codes": [],
                    "codes_available": False,
                    "idempotent_replay": True,
                }

    batch_id = UUID(str(batch["redemption_batch_id"]))
    try:
        generated = await adapter.create_redemption_batch(site=site, payload=payload)
        codes = [str(code) for code in generated.get("codes") or []]
        if len(codes) != payload.code_count or len(set(codes)) != len(codes):
            raise RuntimeError("redemption adapter returned an invalid code batch")
        code_hashes = [hashlib.sha256(code.encode("utf-8")).hexdigest() for code in codes]
        code_masks = [_mask_redemption_code(code) for code in codes]
        async with growth_connection(mongo_db, write=True) as connection:
            completed = await repository.complete_redemption_batch(
                connection,
                redemption_batch_id=batch_id,
                source_batch_id=str(generated.get("source_batch_id") or ""),
                code_hashes=code_hashes,
                code_masks=code_masks,
            )
    except Exception as exc:
        async with growth_connection(mongo_db, write=True) as connection:
            await repository.fail_redemption_batch(
                connection,
                redemption_batch_id=batch_id,
                error_code=exc.__class__.__name__,
                error_message=str(exc),
            )
        raise
    return completed | {
        "codes": codes,
        "codes_available": True,
        "idempotent_replay": False,
    }


async def list_redemption_codes(
    mongo_db: Any,
    *,
    site_id: str,
    page: int,
    page_size: int,
    status_filter: str | None,
    origin: str | None,
    search: str | None,
    actor_id: str,
) -> dict[str, Any]:
    _, adapter = await _redemption_adapter(mongo_db, site_id)
    remote_items: list[dict[str, Any]] = []
    remote_page = 1
    remote_pages = 1
    remote_total = 0
    while remote_page <= min(remote_pages, REDEMPTION_REMOTE_MAX_PAGES):
        response = await adapter.list_redemption_codes(
            page=remote_page,
            page_size=REDEMPTION_REMOTE_PAGE_SIZE,
            status_filter=status_filter,
            search=None,
        )
        items = response.get("items") or []
        remote_items.extend(item for item in items if isinstance(item, dict))
        remote_total = int(response.get("total") or len(remote_items))
        remote_pages = max(1, int(response.get("pages") or math.ceil(remote_total / REDEMPTION_REMOTE_PAGE_SIZE)))
        remote_page += 1

    async with growth_connection(mongo_db) as connection:
        batches = await repository.list_redemption_batch_attributions(
            connection,
            site_id=site_id,
        )
    creator_ids = {
        str(batch.get("requested_by") or "")
        for batch in batches
        if batch.get("requested_by")
    }
    creator_labels = await _redemption_creator_labels(mongo_db, creator_ids)
    attribution_by_id = _redemption_attribution_by_id(batches)
    rows = [
        _redemption_list_row(
            item,
            site_id=site_id,
            attribution=attribution_by_id.get(str(item.get("id") or "")),
            creator_labels=creator_labels,
            actor_id=actor_id,
        )
        for item in remote_items
    ]
    if search:
        normalized_search = search.casefold()
        rows = [
            row
            for item, row in zip(remote_items, rows, strict=True)
            if _redemption_matches_search(item, row, normalized_search)
        ]
    if origin:
        rows = [item for item in rows if item["origin"] == origin]
    rows.sort(key=_redemption_sort_key, reverse=True)
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "items": rows[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, math.ceil(total / page_size)),
        "truncated": remote_pages > REDEMPTION_REMOTE_MAX_PAGES or remote_total > len(remote_items),
    }


async def reveal_redemption_code(
    mongo_db: Any,
    *,
    site_id: str,
    code_id: int,
) -> dict[str, Any]:
    _, adapter = await _redemption_adapter(mongo_db, site_id)
    item = await adapter.get_redemption_code(code_id=code_id)
    code = str(item.get("code") or "")
    if not code:
        raise RuntimeError("API site returned an empty redemption code")
    return {
        "code_id": code_id,
        "code": code,
        "code_mask": _mask_redemption_code(code),
        "fetched_at": datetime.now(UTC).isoformat(),
    }


async def delete_redemption_code(
    mongo_db: Any,
    *,
    site_id: str,
    code_id: int,
) -> dict[str, Any]:
    del mongo_db, site_id, code_id
    raise CreditCapabilityUnavailable(
        "Sub2API does not provide atomic delete-if-unused; redemption deletion is disabled"
    )


async def batch_delete_redemption_codes(
    mongo_db: Any,
    *,
    site_id: str,
    code_ids: list[int],
) -> dict[str, Any]:
    del mongo_db, site_id, code_ids
    raise CreditCapabilityUnavailable(
        "Sub2API does not provide atomic delete-if-unused; redemption deletion is disabled"
    )


async def _redemption_adapter(mongo_db: Any, site_id: str):
    site = await get_client_site(mongo_db, site_id, include_api_key=True)
    if site is None:
        raise LookupError("client site not found")
    return site, create_credit_command_adapter(site)


async def _redemption_creator_labels(
    mongo_db: Any,
    creator_ids: set[str],
) -> dict[str, str]:
    if not creator_ids:
        return {}
    labels: dict[str, str] = {}
    cursor = mongo_db.users.find({}, {"name": 1, "email": 1})
    async for user in cursor:
        user_id = str(user.get("_id") or "")
        if user_id in creator_ids:
            labels[user_id] = str(user.get("name") or user.get("email") or user_id)
    return labels


def _redemption_attribution_by_id(
    batches: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for batch in batches:
        source_ids = [item.strip() for item in str(batch.get("source_batch_id") or "").split(",") if item.strip()]
        masks = batch.get("code_masks") or []
        for index, source_id in enumerate(source_ids):
            if source_id in result:
                continue
            result[source_id] = {
                "requested_by": str(batch.get("requested_by") or ""),
                "created_at": batch.get("created_at"),
                "code_mask": str(masks[index]) if index < len(masks) else "",
            }
    return result


def _redemption_list_row(
    item: dict[str, Any],
    *,
    site_id: str,
    attribution: dict[str, Any] | None,
    creator_labels: dict[str, str],
    actor_id: str,
) -> dict[str, Any]:
    code = str(item.get("code") or "")
    requested_by = str((attribution or {}).get("requested_by") or "")
    user = item.get("user")
    user_email = user.get("email") if isinstance(user, dict) else None
    return {
        "id": item.get("id"),
        "site_id": site_id,
        "code_mask": str((attribution or {}).get("code_mask") or "") or _mask_redemption_code(code),
        "type": item.get("type"),
        "value": item.get("value"),
        "status": item.get("status"),
        "created_at": item.get("created_at"),
        "expires_at": item.get("expires_at"),
        "used_by": item.get("used_by"),
        "used_at": item.get("used_at"),
        "user": {"email": user_email} if user_email else None,
        "origin": "management_panel" if attribution else "api_site",
        "created_by": creator_labels.get(requested_by, requested_by) if requested_by else None,
        "created_by_current_user": bool(requested_by and requested_by == actor_id),
    }


def _redemption_sort_key(item: dict[str, Any]) -> tuple[bool, datetime, int]:
    value = item.get("created_at")
    if isinstance(value, datetime):
        created_at = value
    else:
        try:
            created_at = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            created_at = HISTORICAL_CONVERSION_RATE_START
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return bool(item.get("created_by_current_user")), created_at, int(item.get("id") or 0)


def _redemption_matches_search(
    remote_item: dict[str, Any],
    row: dict[str, Any],
    normalized_search: str,
) -> bool:
    user = remote_item.get("user")
    candidates = [
        remote_item.get("code"),
        row.get("code_mask"),
        remote_item.get("used_by"),
        user.get("email") if isinstance(user, dict) else None,
    ]
    return any(normalized_search in str(value).casefold() for value in candidates if value is not None)


def _validate_redemption_idempotent_replay(
    batch: dict[str, Any],
    payload: RedemptionBatchCreate,
) -> None:
    expected = {
        "site_id": payload.site_id,
        "purpose": payload.purpose.value,
        "code_count": payload.code_count,
        "balance_units_per_code": payload.balance_units_per_code,
        "cash_amount_cny": payload.cash_amount_cny,
        "note": payload.note,
    }
    for field, value in expected.items():
        existing = batch.get(field)
        if field in {"balance_units_per_code", "cash_amount_cny"}:
            if str(existing) == str(value) or Decimal(str(existing)) == value:
                continue
        elif existing == value:
            continue
        raise ValueError("idempotency_key is already used by a different redemption request")


def _mask_redemption_code(code: str) -> str:
    if len(code) <= 8:
        return "*" * len(code)
    return f"{code[:4]}...{code[-4:]}"


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
