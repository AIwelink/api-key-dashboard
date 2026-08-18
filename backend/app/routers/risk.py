from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.exc import SQLAlchemyError

from app.database import db_dependency
from app.modules.operations.site_permissions import normalize_operations_site_ids
from app.modules.risk import service
from app.modules.risk.adapters.sub2api import SourceStateConflict
from app.modules.risk.schemas import RiskActionRequest, RiskSettingsUpdate
from app.modules.system.audit import write_audit_log
from app.modules.system.permissions import require_view_permission


router = APIRouter(prefix="/operations/risk", tags=["operations-risk"])
OPERATIONS_PERMISSION = "operations-management"


def _authorize(actor: dict[str, Any], *, write: bool = False) -> None:
    if "aiwelink" not in normalize_operations_site_ids(actor.get("operations_site_ids")):
        raise HTTPException(status_code=403, detail="AIWeLink operations access is required")
    if write and actor.get("role") not in {"owner", "admin", "operator"}:
        raise HTTPException(
            status_code=403,
            detail="Only owner, admin, or operator can change risk controls",
        )


def _actor_id(actor: dict[str, Any]) -> str:
    return str(actor.get("_id") or actor.get("email") or actor.get("id") or "")


def _actor_name(actor: dict[str, Any]) -> str:
    return str(actor.get("name") or actor.get("email") or _actor_id(actor))


def _raise(exc: Exception) -> None:
    if isinstance(exc, SourceStateConflict) or (
        isinstance(exc, RuntimeError) and "risk control is busy" in str(exc)
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if isinstance(exc, SQLAlchemyError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Risk database is unavailable",
        ) from exc
    raise exc


@router.get("/overview")
async def get_risk_overview(
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _authorize(actor)
    try:
        return await service.get_risk_overview(db)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.get("/accounts")
async def get_risk_accounts(
    risk_status: str | None = Query(default=None),
    search: str | None = Query(default=None, max_length=240),
    rule: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _authorize(actor)
    try:
        return await service.list_risk_accounts(
            db,
            status=risk_status,
            query=search,
            rule=rule,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.get("/accounts/{risk_account_id}")
async def get_risk_account_detail(
    risk_account_id: UUID,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _authorize(actor)
    try:
        return await service.get_risk_account_detail(db, risk_account_id=risk_account_id)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.get("/ip-clusters")
async def get_risk_ip_clusters(
    search: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _authorize(actor)
    try:
        return await service.list_risk_ip_clusters(
            db,
            query=search,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.get("/events")
async def get_risk_events(
    event_type: str | None = Query(default=None),
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _authorize(actor)
    try:
        return await service.list_risk_events(
            db,
            event_type=event_type,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.get("/settings")
async def get_risk_settings(
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _authorize(actor)
    try:
        return await service.get_risk_settings(db)
    except Exception as exc:  # noqa: BLE001
        _raise(exc)


@router.patch("/settings")
async def patch_risk_settings(
    payload: RiskSettingsUpdate,
    actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    _authorize(actor, write=True)
    try:
        result = await service.update_risk_settings(
            db,
            detector_enabled=payload.detector_enabled,
            auto_ban_enabled=payload.auto_ban_enabled,
            actor_id=_actor_id(actor),
        )
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="operations.risk.settings.update",
        resource_type="risk_settings",
        after={
            "detector_enabled": result.get("detector_enabled"),
            "auto_ban_enabled": result.get("auto_ban_enabled"),
        },
    )
    return result


async def _account_action(
    *,
    action: str,
    risk_account_id: UUID,
    payload: RiskActionRequest,
    actor: dict[str, Any],
    db: Any,
) -> dict[str, Any]:
    _authorize(actor, write=True)
    handler = {
        "ban": service.manual_ban,
        "release": service.manual_release,
        "false-positive": service.set_false_positive,
        "override-remove": service.remove_manual_override,
    }[action]
    try:
        result = await handler(
            db,
            risk_account_id=risk_account_id,
            actor_id=_actor_id(actor),
            actor_name=_actor_name(actor),
            reason=payload.reason,
        )
    except Exception as exc:  # noqa: BLE001
        _raise(exc)
    await write_audit_log(
        db,
        actor=actor,
        action=f"operations.risk.{action}",
        resource_type="risk_account",
        resource_id=str(risk_account_id),
        after={"risk_account_id": str(risk_account_id), "reason": payload.reason},
    )
    return result


@router.post("/accounts/{risk_account_id}/ban")
async def post_manual_ban(risk_account_id: UUID, payload: RiskActionRequest, actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)), db: AsyncIOMotorDatabase = Depends(db_dependency)) -> dict[str, Any]:
    return await _account_action(action="ban", risk_account_id=risk_account_id, payload=payload, actor=actor, db=db)


@router.post("/accounts/{risk_account_id}/release")
async def post_manual_release(risk_account_id: UUID, payload: RiskActionRequest, actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)), db: AsyncIOMotorDatabase = Depends(db_dependency)) -> dict[str, Any]:
    return await _account_action(action="release", risk_account_id=risk_account_id, payload=payload, actor=actor, db=db)


@router.post("/accounts/{risk_account_id}/false-positive")
async def post_false_positive(risk_account_id: UUID, payload: RiskActionRequest, actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)), db: AsyncIOMotorDatabase = Depends(db_dependency)) -> dict[str, Any]:
    return await _account_action(action="false-positive", risk_account_id=risk_account_id, payload=payload, actor=actor, db=db)


@router.post("/accounts/{risk_account_id}/override/remove")
async def post_remove_override(risk_account_id: UUID, payload: RiskActionRequest, actor: dict = Depends(require_view_permission(OPERATIONS_PERMISSION)), db: AsyncIOMotorDatabase = Depends(db_dependency)) -> dict[str, Any]:
    return await _account_action(action="override-remove", risk_account_id=risk_account_id, payload=payload, actor=actor, db=db)
