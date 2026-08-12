from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import db_dependency
from app.modules.operations import service
from app.modules.operations.repository import OperationsNotFoundError
from app.modules.operations.schemas import (
    BalanceAdjustmentCreate,
    ClassificationUpdate,
    ConversionRateCreate,
    InternalUserCreate,
    InternalUserUpdate,
    OperationsQuery,
    RedemptionBatchCreate,
    RedemptionCodeBatchDelete,
    RedemptionCodeListQuery,
    RefreshRequest,
)
from app.modules.system.audit import write_audit_log
from app.modules.system.permissions import require_view_permission
from app.modules.operations.site_permissions import normalize_operations_site_ids


router = APIRouter(prefix="/operations", tags=["operations"])
OPERATIONS_PERMISSION = "operations-management"


def _actor_id(actor: dict[str, Any]) -> str:
    return str(actor.get("_id") or actor.get("email") or actor.get("id") or "")


def _require_operations_writer(actor: dict[str, Any]) -> None:
    if actor.get("role") not in {"owner", "admin"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owner or admin can change operations configuration",
        )


def _resolve_operations_site_ids(
    actor: dict[str, Any],
    requested_site_ids: list[str] | tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    allowed_site_ids = tuple(normalize_operations_site_ids(actor.get("operations_site_ids")))
    if not allowed_site_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No operations site access has been assigned",
        )
    if requested_site_ids is None:
        return allowed_site_ids
    requested = tuple(dict.fromkeys(requested_site_ids))
    if any(site_id not in allowed_site_ids for site_id in requested):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operations site access denied",
        )
    return requested


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, service.OperationsSiteAccessDenied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, service.CreditCapabilityUnavailable):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    if isinstance(exc, (OperationsNotFoundError, LookupError)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, IntegrityError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Operations configuration conflicts with an existing record",
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if isinstance(exc, SQLAlchemyError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Operations database is unavailable or not initialized",
        ) from exc
    raise exc


@router.get("/summary")
async def get_operations_summary(
    query: OperationsQuery = Depends(),
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    allowed_site_ids = _resolve_operations_site_ids(
        actor,
        [query.site_id] if query.site_id else None,
    )
    try:
        return await service.get_operations_overview(
            db,
            query,
            allowed_site_ids=allowed_site_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)


@router.get("/trends")
async def get_operations_trends(
    query: OperationsQuery = Depends(),
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    allowed_site_ids = _resolve_operations_site_ids(
        actor,
        [query.site_id] if query.site_id else None,
    )
    try:
        return await service.get_operations_trend_data(
            db,
            query,
            allowed_site_ids=allowed_site_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)


@router.get("/users")
async def get_operations_users(
    query: OperationsQuery = Depends(),
    search: str | None = Query(default=None, max_length=240),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    allowed_site_ids = _resolve_operations_site_ids(
        actor,
        [query.site_id] if query.site_id else None,
    )
    try:
        return await service.get_operations_user_data(
            db,
            query,
            search=search,
            limit=limit,
            offset=offset,
            allowed_site_ids=allowed_site_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)


@router.get("/sync-status")
async def get_operations_sync_status(
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    allowed_site_ids = _resolve_operations_site_ids(actor)
    try:
        return await service.get_operations_sync_status(
            db,
            allowed_site_ids=allowed_site_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)


@router.post("/refresh", status_code=status.HTTP_202_ACCEPTED)
async def post_operations_refresh(
    payload: RefreshRequest,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    allowed_site_ids = _resolve_operations_site_ids(actor, payload.site_ids)
    try:
        result = await service.refresh_operations(
            db,
            payload,
            allowed_site_ids=allowed_site_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="operations.refresh",
        resource_type="operations_sync",
        after={"site_ids": list(allowed_site_ids)},
    )
    return result


@router.get("/internal-users")
async def get_internal_users(
    site_id: str | None = Query(default=None),
    query: str | None = Query(default=None, max_length=240),
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    allowed_site_ids = _resolve_operations_site_ids(actor, [site_id] if site_id else None)
    try:
        return await service.list_internal_user_configs(
            db,
            site_id=site_id,
            query=query,
            allowed_site_ids=allowed_site_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)


@router.post("/internal-users", status_code=status.HTTP_201_CREATED)
async def post_internal_user(
    payload: InternalUserCreate,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _require_operations_writer(actor)
    _resolve_operations_site_ids(actor, [payload.site_id])
    try:
        result = await service.create_internal_user_config(
            db,
            payload,
            actor_id=_actor_id(actor),
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="operations.internal_user.create",
        resource_type="operations_internal_user",
        resource_id=result.get("internal_user_id"),
        after=result,
    )
    return result


@router.patch("/internal-users/{internal_user_id}")
async def patch_internal_user(
    internal_user_id: UUID,
    payload: InternalUserUpdate,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _require_operations_writer(actor)
    allowed_site_ids = _resolve_operations_site_ids(actor)
    try:
        result = await service.update_internal_user_config(
            db,
            internal_user_id,
            payload,
            actor_id=_actor_id(actor),
            allowed_site_ids=allowed_site_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="operations.internal_user.update",
        resource_type="operations_internal_user",
        resource_id=str(internal_user_id),
        after=result,
    )
    return result


@router.delete("/internal-users/{internal_user_id}")
async def delete_internal_user(
    internal_user_id: UUID,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _require_operations_writer(actor)
    allowed_site_ids = _resolve_operations_site_ids(actor)
    try:
        result = await service.delete_internal_user_config(
            db,
            internal_user_id,
            allowed_site_ids=allowed_site_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="operations.internal_user.delete",
        resource_type="operations_internal_user",
        resource_id=str(internal_user_id),
        before=result,
        after={"deleted": True, "site_id": result.get("site_id")},
    )
    return result


@router.get("/conversion-rates")
async def get_conversion_rates(
    site_id: str | None = Query(default=None),
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    allowed_site_ids = _resolve_operations_site_ids(actor, [site_id] if site_id else None)
    try:
        return await service.list_conversion_rate_configs(
            db,
            site_id=site_id,
            allowed_site_ids=allowed_site_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)


@router.post("/conversion-rates", status_code=status.HTTP_201_CREATED)
async def post_conversion_rate(
    payload: ConversionRateCreate,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _require_operations_writer(actor)
    _resolve_operations_site_ids(actor, [payload.site_id])
    try:
        result = await service.create_conversion_rate_config(
            db,
            payload,
            actor_id=_actor_id(actor),
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="operations.conversion_rate.create",
        resource_type="operations_conversion_rate",
        resource_id=result.get("conversion_rate_id"),
        after=result,
    )
    return result


@router.get("/classification-tasks")
async def get_classification_tasks(
    site_id: str | None = Query(default=None),
    task_status: str = Query(default="pending", pattern="^(pending|resolved|ignored)$"),
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    allowed_site_ids = _resolve_operations_site_ids(actor, [site_id] if site_id else None)
    try:
        return await service.list_classification_task_configs(
            db,
            site_id=site_id,
            status=task_status,
            allowed_site_ids=allowed_site_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)


@router.patch("/classification-tasks/{classification_task_id}")
async def patch_classification_task(
    classification_task_id: UUID,
    payload: ClassificationUpdate,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _require_operations_writer(actor)
    allowed_site_ids = _resolve_operations_site_ids(actor)
    try:
        result = await service.resolve_classification_task_config(
            db,
            classification_task_id,
            payload,
            actor_id=_actor_id(actor),
            allowed_site_ids=allowed_site_ids,
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="operations.classification.update",
        resource_type="operations_classification_task",
        resource_id=str(classification_task_id),
        after=result,
    )
    return result


@router.post("/redemption-codes/query")
async def get_redemption_codes(
    query: RedemptionCodeListQuery,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _resolve_operations_site_ids(actor, [query.site_id])
    try:
        return await service.list_redemption_codes(
            db,
            site_id=query.site_id,
            page=query.page,
            page_size=query.page_size,
            status_filter=query.status,
            origin=query.origin,
            search=query.search,
            actor_id=_actor_id(actor),
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)


@router.get("/redemption-codes/{site_id}/{code_id}/reveal")
async def get_redemption_code_reveal(
    site_id: str,
    code_id: int,
    response: Response,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _require_operations_writer(actor)
    _resolve_operations_site_ids(actor, [site_id])
    try:
        result = await service.reveal_redemption_code(db, site_id=site_id, code_id=code_id)
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    response.headers["Cache-Control"] = "no-store"
    await write_audit_log(
        db,
        actor=actor,
        action="operations.redemption_code.reveal",
        resource_type="operations_redemption_code",
        resource_id=f"{site_id}:{code_id}",
        after={
            "site_id": site_id,
            "code_id": code_id,
            "code_mask": result.get("code_mask"),
        },
    )
    return result


@router.delete("/redemption-codes/{site_id}/{code_id}")
async def delete_redemption_code_route(
    site_id: str,
    code_id: int,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _require_operations_writer(actor)
    _resolve_operations_site_ids(actor, [site_id])
    _raise_http_error(
        service.CreditCapabilityUnavailable(
            "Sub2API does not provide atomic delete-if-unused; redemption deletion is disabled"
        )
    )


@router.post("/redemption-codes/batch-delete")
async def post_redemption_code_batch_delete(
    payload: RedemptionCodeBatchDelete,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _require_operations_writer(actor)
    _resolve_operations_site_ids(actor, [payload.site_id])
    _raise_http_error(
        service.CreditCapabilityUnavailable(
            "Sub2API does not provide atomic delete-if-unused; redemption deletion is disabled"
        )
    )


@router.post("/redemption-batches", status_code=status.HTTP_201_CREATED)
async def post_redemption_batch(
    payload: RedemptionBatchCreate,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _require_operations_writer(actor)
    _resolve_operations_site_ids(actor, [payload.site_id])
    try:
        result = await service.create_redemption_batch(
            db,
            payload,
            actor_id=_actor_id(actor),
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="operations.redemption_batch.create",
        resource_type="operations_redemption_batch",
        resource_id=result.get("redemption_batch_id"),
        after={key: value for key, value in result.items() if key != "codes"},
    )
    return result


@router.post("/balance-adjustments", status_code=status.HTTP_201_CREATED)
async def post_balance_adjustment(
    payload: BalanceAdjustmentCreate,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _require_operations_writer(actor)
    _resolve_operations_site_ids(actor, [payload.site_id])
    try:
        result = await service.create_balance_adjustment(
            db,
            payload,
            actor_id=_actor_id(actor),
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="operations.balance_adjustment.create",
        resource_type="operations_balance_adjustment",
        resource_id=result.get("adjustment_request_id"),
        after=result,
    )
    return result
