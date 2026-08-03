from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.modules.auto_replenishment.service import test_supplier_connection
from app.modules.auto_replenishment.settings import (
    SETTINGS_ID,
    get_auto_replenishment_settings,
    save_auto_replenishment_settings,
)
from app.modules.system.audit import write_audit_log
from app.modules.system.permissions import require_view_permission
from app.schemas import AutoReplenishmentSettingsUpdate
from app.security import get_current_user


router = APIRouter(
    prefix="/auto-replenishment/settings",
    tags=["auto-replenishment"],
    dependencies=[Depends(require_view_permission("auto-replenishment"))],
)


@router.get("")
async def get_auto_replenishment_settings_route(
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    return await get_auto_replenishment_settings(db)


@router.put("")
async def put_auto_replenishment_settings(
    payload: AutoReplenishmentSettingsUpdate,
    actor: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        result = await save_auto_replenishment_settings(
            db,
            payload=payload.model_dump(),
            actor=actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=actor,
        action="auto_replenishment.settings_update",
        resource_type="auto_replenishment_settings",
        resource_id=SETTINGS_ID,
        after=result,
    )
    return result


@router.post("/test")
async def post_auto_replenishment_test(
    actor: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        result = await test_supplier_connection(db, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=actor,
        action="auto_replenishment.connection_test",
        resource_type="auto_replenishment_settings",
        resource_id=SETTINGS_ID,
        after=result,
    )
    if result.get("ok") is not True:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("error") or "SogouEdu connection test failed",
        )
    return result
