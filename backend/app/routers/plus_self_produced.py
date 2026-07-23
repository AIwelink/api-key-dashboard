from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.modules.sub2api.plus_self_produced import get_status, list_groups, list_results, run_probe, update_settings
from app.modules.system.audit import write_audit_log
from app.schemas import PlusSelfProducedSettingsUpdate
from app.security import get_current_user
from app.modules.system.permissions import require_view_permission


router = APIRouter(
    prefix="/plus-self-produced",
    tags=["plus-self-produced"],
    dependencies=[Depends(require_view_permission("plus-self-produced"))],
)


@router.get("/status")
async def get_plus_self_produced_status(
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_status(db)


@router.get("/groups")
async def get_plus_self_produced_groups(
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> list[dict]:
    return await list_groups(db)


@router.get("/results")
async def get_plus_self_produced_results(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    classification: str | None = None,
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_results(
        db,
        page=page,
        page_size=page_size,
        classification=classification,
    )


@router.patch("/settings")
async def patch_plus_self_produced_settings(
    payload: PlusSelfProducedSettingsUpdate,
    actor: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await update_settings(db, payload.model_dump(exclude_unset=True), actor)
    await write_audit_log(
        db,
        actor=actor,
        action="plus_self_produced.settings.update",
        resource_type="setting",
        resource_id="plus-self-produced",
        after=updated,
    )
    return updated


@router.post("/run")
async def post_plus_self_produced_run(
    actor: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await run_probe(db, trigger="manual")
    if result.get("conflict") is True:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="plus self-produced probe is already running")
    await write_audit_log(
        db,
        actor=actor,
        action="plus_self_produced.run",
        resource_type="sub2api_site",
        resource_id="US06-5002",
        after=result,
    )
    return result
